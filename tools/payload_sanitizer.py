"""
Aether Desktop - Client-Side Tool Payload Truncator (Layer A)
Intercepts and compacts tool execution responses (scripts, logs, tracebacks)
before sending them up the WebSocket to prevent context-window token bloat.
"""

import json
import os
import re
from typing import Any, Dict, Optional

# Default scratchpad cache path
DEFAULT_CACHE_REL_PATH = os.path.join("data", "scratchpad", "last_tool_output.txt")
MAX_PAYLOAD_CHAR_LIMIT = 800
SUMMARY_CHAR_LIMIT = 500

# Runner frames to strip from Python tracebacks
RUNNER_FRAME_PATTERNS = [
    r"runners\.py",
    r"base_events\.py",
    r"contextlib\.py",
    r"asyncio[\\/]events\.py",
    r"asyncio[\\/]base_events\.py",
    r"asyncio[\\/]runners\.py",
]


def clean_traceback(tb_str: str) -> str:
    """
    Strips internal Python runner frames (runners.py, base_events.py, contextlib.py)
    from traceback strings.
    Extracts and returns only:
      - The failing code line and module name.
      - The final 2 lines containing the exact ExceptionType and message.
    """
    if not tb_str or not isinstance(tb_str, str):
        return ""

    lines = tb_str.strip().splitlines()
    if not lines:
        return ""

    # If it's a short error message with no stack frames, return as is
    if "Traceback (most recent call last):" not in tb_str and not any("File " in l for l in lines):
        return "\n".join(lines[-2:] if len(lines) >= 2 else lines)

    # Parse stack frames into (header_line, [code_lines])
    frames = []
    current_header = None
    current_code = []
    trailer_lines = []

    in_traceback = False
    for line in lines:
        stripped = line.strip()
        if "Traceback (most recent call last):" in line:
            in_traceback = True
            continue

        if stripped.startswith("File "):
            if current_header is not None:
                frames.append((current_header, current_code))
            current_header = line
            current_code = []
        elif current_header is not None:
            # Check if this line is an exception line (starts with an identifier like ValueError:)
            if re.match(r"^[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Warning|Interrupt|Exit|Fault):", stripped) or (
                len(current_code) > 0 and not line.startswith("    ") and not line.startswith("  ")
            ):
                frames.append((current_header, current_code))
                current_header = None
                current_code = []
                trailer_lines.append(line)
            else:
                current_code.append(line)
        else:
            trailer_lines.append(line)

    if current_header is not None:
        frames.append((current_header, current_code))

    # Filter out internal runner frames
    filtered_frames = []
    for header, code in frames:
        is_runner = any(re.search(pat, header, re.IGNORECASE) for pat in RUNNER_FRAME_PATTERNS)
        if not is_runner:
            filtered_frames.append((header, code))

    # Determine failing frame: prefer the last non-runner frame; if none left, use the last original frame
    target_frame = filtered_frames[-1] if filtered_frames else (frames[-1] if frames else None)

    result_parts = []
    if target_frame:
        header, code_lines = target_frame
        result_parts.append(header.strip())
        for c in code_lines:
            result_parts.append(c.strip())

    # Extract final 2 lines containing ExceptionType and message
    exception_lines = [l.strip() for l in trailer_lines if l.strip()]
    if not exception_lines and lines:
        # Fallback to the last line of the original traceback
        exception_lines = [lines[-1].strip()]

    last_two = exception_lines[-2:] if len(exception_lines) >= 2 else exception_lines
    result_parts.extend(last_two)

    return "\n".join(result_parts) if result_parts else tb_str[:SUMMARY_CHAR_LIMIT]


def _extract_primary_text_payload(result: Dict[str, Any]) -> tuple[str, bool]:
    """
    Inspects result dict to locate the primary text/collection payload.
    Returns (raw_text, exceeds_limit).
    """
    # 1. Direct stdout/output string
    for key in ("stdout", "output", "text", "data", "message"):
        val = result.get(key)
        if isinstance(val, str) and len(val) > MAX_PAYLOAD_CHAR_LIMIT:
            return val, True

    # 2. Large collections / tables (e.g. facts, skills, results lists)
    for key in ("facts", "skills", "results", "items", "table"):
        val = result.get(key)
        if isinstance(val, (list, dict)):
            dumped = json.dumps(val, indent=2, default=str)
            if len(dumped) > MAX_PAYLOAD_CHAR_LIMIT:
                return dumped, True

    # 3. Overall serialized dict if large
    # Exclude binary data (_jpeg_bytes) when calculating size
    clean_dict = {k: v for k, v in result.items() if not k.startswith("_")}
    serialized = json.dumps(clean_dict, indent=2, default=str)
    if len(serialized) > MAX_PAYLOAD_CHAR_LIMIT:
        return serialized, True

    return "", False


def sanitize_tool_result(
    result: Any,
    cache_path: Optional[str] = None,
    base_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Sanitizes and caps tool execution outputs before sending them to Gemini.
    - If output string exceeds 800 characters, writes raw payload to local cache
      and returns an executive summary dictionary.
    - If an exception or traceback is present, cleans internal runner frames
      and preserves only the failing line, module name, and final exception lines.
    - Preserves private metadata keys (e.g. _jpeg_bytes).
    """
    if not isinstance(result, dict):
        result_dict = {"status": "success", "result": result}
    else:
        result_dict = dict(result)

    # 1. Clean tracebacks / stderr if present
    for tb_key in ("traceback", "stderr", "error"):
        if tb_key in result_dict and isinstance(result_dict[tb_key], str) and result_dict[tb_key]:
            cleaned_tb = clean_traceback(result_dict[tb_key])
            result_dict[tb_key] = cleaned_tb

    if "traceback_history" in result_dict and isinstance(result_dict["traceback_history"], list):
        result_dict["traceback_history"] = [
            clean_traceback(t) if isinstance(t, str) else t
            for t in result_dict["traceback_history"]
        ]

    # 2. Check for payload size capping
    raw_text, exceeds = _extract_primary_text_payload(result_dict)

    if exceeds:
        if base_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        target_cache_path = cache_path or os.path.join(base_dir, DEFAULT_CACHE_REL_PATH)
        os.makedirs(os.path.dirname(os.path.abspath(target_cache_path)), exist_ok=True)

        try:
            with open(target_cache_path, "w", encoding="utf-8") as f:
                f.write(raw_text)
        except Exception as write_err:
            # Fallback if writing fails
            pass

        # Use relative path for presentation to Gemini
        rel_cache_path = os.path.relpath(target_cache_path, base_dir).replace("\\", "/")

        sanitized = {
            "status": result_dict.get("status", "success"),
            "summary": raw_text[:SUMMARY_CHAR_LIMIT] + "... [TRUNCATED - full output cached locally]",
            "cache_path": rel_cache_path,
            "total_characters": len(raw_text)
        }

        # Preserve private pass-through keys (such as _jpeg_bytes for screen snapshots)
        for k, v in result_dict.items():
            if k.startswith("_"):
                sanitized[k] = v

        return sanitized

    return result_dict

