"""
core/api_telemetry.py - Centralized Gemini API Cost Logger, Screenshot Scrubber,
Dynamic Per-Image Media Resolution Policy, and Feature Attribution Engine.

Work Order (v2):
- Zero-retention screenshot scrubbing (`scrub_stale_images`) for active loops (`keep_latest_n=1`)
  and persistent chat history (`keep_latest_n=0`).
- Dynamic per-image `media_resolution` flexing (`low` ~280 tok, `medium` ~560 tok default,
  `high` ~1,120 tok dense OCR/puzzles only) + ROI panel cropping.
- Centralized instrumentation of every `google.genai` `generate_content` / `generate_content_stream`
  (sync & async) call with mandatory `feature_tag` taxonomy, exact non-double-counted cost math,
  SQLite persistence (`api_usage_log`), compact `[API_COST]` log lines, and `--cost-report`.
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime
import io
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.logger import get_logger
from core.telemetry_db import query_api_usage_rows, record_api_usage_row

logger = get_logger("ApiTelemetry")

# Mandatory Feature Taxonomy
VALID_FEATURE_TAGS = frozenset({
    "casual_chat",
    "dossier_creation",
    "document_builder",
    "agnostic_telemetry",
    "game_ptt_copilot",
    "cockpit_control",
    "game_web_inquiry",
    "roleplay_session",
    "script_gen_optimizer",
    "unclassified",
})

SCRUBBED_IMAGE_PLACEHOLDER = "[Previous screenshot omitted to save tokens — superseded by newer capture]"

# Session identity for the running Aether process
_PROCESS_SESSION_ID: str = f"sess_{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

# Async- and thread-safe contextvar stack for feature attribution
_CURRENT_CONTEXT: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "aether_api_feature_ctx", default=None
)

_HOOKS_INSTALLED: bool = False

# Pricing table: (uncached_input_per_1m, cached_input_per_1m, output_per_1m)
# Grounding search cost: $0.014 per search query ($14 / 1,000)
GOOGLE_SEARCH_QUERY_COST_USD = 0.014


def get_process_session_id() -> str:
    return _PROCESS_SESSION_ID


@contextlib.contextmanager
def api_feature_context(
    feature_tag: str,
    sub_action: str = "",
    loop_step: int = 1,
    turn_id: Optional[str] = None,
    keep_latest_images: int = 1,
):
    """Binds feature attribution metadata for all Gemini API calls executed inside this block."""
    tag = str(feature_tag or "unclassified").strip().lower()
    if tag not in VALID_FEATURE_TAGS:
        logger.warning("[API_TELEMETRY] Unknown feature_tag '%s'; defaulting to 'unclassified'.", tag)
        tag = "unclassified"
    prev = _CURRENT_CONTEXT.get()
    ctx = {
        "feature_tag": tag,
        "sub_action": str(sub_action or "").strip(),
        "loop_step": max(1, int(loop_step or 1)),
        "turn_id": str(turn_id or (prev.get("turn_id") if isinstance(prev, dict) else "") or uuid.uuid4().hex[:10]),
        "keep_latest_images": max(0, int(keep_latest_images)),
    }
    token = _CURRENT_CONTEXT.set(ctx)
    try:
        yield ctx
    finally:
        _CURRENT_CONTEXT.reset(token)


# ---------------------------------------------------------------------------
# Pricing & Exact Non-Double-Counted Cost Math
# ---------------------------------------------------------------------------

def get_model_pricing_per_million(
    model_name: str,
    as_of_date: Optional[datetime.date] = None,
) -> Tuple[float, float, float]:
    """
    Returns `(uncached_input_usd_per_1m, cached_input_usd_per_1m, output_usd_per_1m)`.
    Respects the 2026 vs 2027+ pricing transition for `gemini-3.8-flash`.
    """
    m = str(model_name or "").strip().lower()
    if m.startswith("models/"):
        m = m[len("models/"):]
    if ":" in m:
        m = m.split(":", 1)[0]

    today = as_of_date or datetime.datetime.now(datetime.timezone.utc).date()

    if "gemini-3.8-flash" in m:
        if today >= datetime.date(2027, 1, 1):
            return (1.50, 0.15, 7.50)
        return (0.75, 0.075, 3.75)
    if "gemini-3.1-flash-lite" in m:
        return (0.25, 0.025, 1.50)
    if "gemini-3.5-flash-lite" in m:
        return (0.30, 0.03, 2.50)
    if "gemini-3.8-live" in m:
        if today >= datetime.date(2027, 1, 1):
            return (1.50, 0.15, 7.50)
        return (0.75, 0.075, 3.75)
    if "gemini-4-hydrogen" in m:
        return (1.25, 0.125, 5.00)
    if "gemini-4-neon" in m or "gemini-4-carbon" in m:
        return (2.50, 0.25, 15.00)
    if "transcribe" in m:
        return (0.30, 0.03, 1.50)
    # Safe default: gemini-3.8-flash 2026 rate
    return (0.75, 0.075, 3.75)


def calculate_api_cost_usd(
    model_name: str,
    prompt_token_count: int = 0,
    cached_content_token_count: int = 0,
    tool_use_prompt_token_count: int = 0,
    candidates_token_count: int = 0,
    thoughts_token_count: int = 0,
    grounding_queries_count: int = 0,
    as_of_date: Optional[datetime.date] = None,
) -> float:
    """
    Exact non-double-counted Gemini API cost formula:
      uncached_input_tokens = max(0, prompt_token_count - cached_content_token_count) + tool_use_prompt_token_count
      billable_output_tokens = candidates_token_count + thoughts_token_count
      search_cost = grounding_queries_count * $0.014
    """
    in_rate, cached_rate, out_rate = get_model_pricing_per_million(model_name, as_of_date=as_of_date)
    prompt_toks = max(0, int(prompt_token_count or 0))
    cached_toks = max(0, min(prompt_toks, int(cached_content_token_count or 0)))
    tool_toks = max(0, int(tool_use_prompt_token_count or 0))
    cand_toks = max(0, int(candidates_token_count or 0))
    thought_toks = max(0, int(thoughts_token_count or 0))
    search_q = max(0, int(grounding_queries_count or 0))

    uncached_input = max(0, prompt_toks - cached_toks) + tool_toks
    billable_output = cand_toks + thought_toks

    cost = (
        (uncached_input / 1_000_000.0) * in_rate
        + (cached_toks / 1_000_000.0) * cached_rate
        + (billable_output / 1_000_000.0) * out_rate
        + (search_q * GOOGLE_SEARCH_QUERY_COST_USD)
    )
    return round(cost, 6)


# ---------------------------------------------------------------------------
# Task 2 — Dynamic Per-Image media_resolution & ROI Cropping
# ---------------------------------------------------------------------------

def resolve_media_resolution_enum(resolution: str = "medium") -> Tuple[str, Any, Any]:
    """
    Maps 'low' | 'medium' | 'high' to `(normalized_str, PartMediaResolution, MediaResolution)`.
    Never defaults to `ultra_high`.
    """
    norm = str(resolution or "medium").strip().lower()
    if norm not in ("low", "medium", "high"):
        norm = "medium"
    try:
        from google.genai import types
        lvl_map = {
            "low": types.PartMediaResolutionLevel.MEDIA_RESOLUTION_LOW,
            "medium": types.PartMediaResolutionLevel.MEDIA_RESOLUTION_MEDIUM,
            "high": types.PartMediaResolutionLevel.MEDIA_RESOLUTION_HIGH,
        }
        cfg_map = {
            "low": types.MediaResolution.MEDIA_RESOLUTION_LOW,
            "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
            "high": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
        }
        part_res = types.PartMediaResolution(level=lvl_map[norm]) if hasattr(types, "PartMediaResolution") else None
        cfg_res = cfg_map[norm] if hasattr(types, "MediaResolution") else None
        return norm, part_res, cfg_res
    except Exception:
        return norm, None, None


def make_image_part(
    img_bytes: bytes,
    resolution: str = "medium",
    mime_type: str = "image/jpeg",
) -> Any:
    """
    Builds a `google.genai.types.Part` for an image with explicit per-image `media_resolution`:
      - 'low'    (~280 tokens): binary checks / confirmation / menu-vs-gameplay
      - 'medium' (~560 tokens): default in-game HUD / PTT / coarse UI navigation
      - 'high'   (~1,120 tokens): dense OCR / market tables / tooltips / puzzles only
    """
    norm, part_res, _ = resolve_media_resolution_enum(resolution)
    try:
        from google.genai import types
        blob = types.Blob(data=img_bytes, mime_type=mime_type)
        if part_res is not None:
            return types.Part(inline_data=blob, media_resolution=part_res)
        return types.Part.from_bytes(data=img_bytes, mime_type=mime_type)
    except Exception:
        return {
            "inline_data": {"mime_type": mime_type, "data": img_bytes},
            "media_resolution": norm,
        }


def crop_image_roi(
    img_bytes: bytes,
    bbox_norm: Tuple[float, float, float, float],
    quality: int = 80,
) -> bytes:
    """
    Crops a JPEG/PNG image to a normalized `(x, y, w, h)` ROI (0.0-1.0) so `medium` resolution
    stays crisp on target panels without flexing up to `high`.
    """
    if not img_bytes or not bbox_norm or len(bbox_norm) != 4:
        return img_bytes
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes))
        iw, ih = img.size
        x, y, w, h = [float(v) for v in bbox_norm]
        left = max(0, min(iw - 2, int(x * iw)))
        top = max(0, min(ih - 2, int(y * ih)))
        right = max(left + 2, min(iw, int((x + w) * iw)))
        bottom = max(top + 2, min(ih, int((y + h) * ih)))
        cropped = img.crop((left, top, right, bottom))
        out = io.BytesIO()
        cropped.save(out, format="JPEG", quality=quality)
        return out.getvalue()
    except Exception:
        return img_bytes


# ---------------------------------------------------------------------------
# Task 1 — Zero-Retention Screenshot Scrubber
# ---------------------------------------------------------------------------

def _is_image_part(obj: Any) -> bool:
    if obj is None:
        return False
    # PIL Image
    try:
        from PIL import Image as _PILImage
        if isinstance(obj, _PILImage.Image):
            return True
    except Exception:
        pass
    # Dict-style Part
    if isinstance(obj, dict):
        inl = obj.get("inline_data") or obj.get("inlineData")
        if isinstance(inl, dict) and str(inl.get("mime_type") or inl.get("mimeType") or "").lower().startswith("image/"):
            return True
        fd = obj.get("file_data") or obj.get("fileData")
        if isinstance(fd, dict) and str(fd.get("mime_type") or fd.get("mimeType") or "").lower().startswith("image/"):
            return True
        return False
    # google.genai.types.Part or Blob
    inl = getattr(obj, "inline_data", None)
    if inl is not None and str(getattr(inl, "mime_type", "") or "").lower().startswith("image/"):
        return True
    fd = getattr(obj, "file_data", None)
    if fd is not None and str(getattr(fd, "mime_type", "") or "").lower().startswith("image/"):
        return True
    mt = getattr(obj, "mime_type", None)
    if mt is not None and str(mt).lower().startswith("image/"):
        return True
    return False


def _make_placeholder_part(original_part: Any) -> Any:
    if isinstance(original_part, dict):
        return {"text": SCRUBBED_IMAGE_PLACEHOLDER}
    try:
        from google.genai import types
        if isinstance(original_part, types.Part):
            return types.Part.from_text(text=SCRUBBED_IMAGE_PLACEHOLDER)
    except Exception:
        pass
    return SCRUBBED_IMAGE_PLACEHOLDER


def scrub_stale_images(
    contents: Any,
    keep_latest_n: int = 1,
) -> Tuple[Any, int, int]:
    """
    Replaces stale image parts in `contents` or chat history lists in-place, keeping only
    the newest `keep_latest_n` image parts:
      - `keep_latest_n=1`: active multi-step vision/tool loops
      - `keep_latest_n=0`: persistent chat history after a turn completes

    Returns `(contents, images_kept_count, images_scrubbed_count)`.
    """
    keep_n = max(0, int(keep_latest_n))
    if contents is None or isinstance(contents, str):
        return contents, 0, 0

    # Collect ordered references to every image slot: (container, index_or_key, original_part)
    image_slots: List[Tuple[Any, int, Any]] = []

    def _scan_list(seq: list) -> None:
        for idx, item in enumerate(seq):
            if _is_image_part(item):
                image_slots.append((seq, idx, item))
            elif isinstance(item, dict) and isinstance(item.get("parts"), list):
                _scan_list(item["parts"])
            elif hasattr(item, "parts") and isinstance(getattr(item, "parts", None), list):
                _scan_list(item.parts)
            elif isinstance(item, list):
                _scan_list(item)

    if isinstance(contents, list):
        _scan_list(contents)
    elif isinstance(contents, dict) and isinstance(contents.get("parts"), list):
        _scan_list(contents["parts"])
    elif hasattr(contents, "parts") and isinstance(getattr(contents, "parts", None), list):
        _scan_list(contents.parts)
    elif _is_image_part(contents):
        if keep_n == 0:
            return _make_placeholder_part(contents), 0, 1
        return contents, 1, 0

    total_imgs = len(image_slots)
    if total_imgs == 0:
        return contents, 0, 0

    to_scrub = max(0, total_imgs - keep_n)
    for container, idx, orig in image_slots[:to_scrub]:
        try:
            container[idx] = _make_placeholder_part(orig)
        except Exception:
            pass

    return contents, total_imgs - to_scrub, to_scrub


def scrub_chat_history_images(chat_obj: Any, keep_latest_n: int = 0) -> int:
    """Scrubs retained image parts from a `google.genai` Chat object's internal history lists."""
    if chat_obj is None:
        return 0
    total_scrubbed = 0
    for attr in ("_curated_history", "_comprehensive_history", "history"):
        hist = getattr(chat_obj, attr, None)
        if isinstance(hist, list) and hist:
            _, _, scrubbed = scrub_stale_images(hist, keep_latest_n=keep_latest_n)
            total_scrubbed += scrubbed
    return total_scrubbed


# ---------------------------------------------------------------------------
# Pre-Call Diagnostic Inspection & Post-Call Usage Extraction
# ---------------------------------------------------------------------------

def _inspect_request_diagnostics(contents: Any, config: Any) -> Dict[str, Any]:
    """Extracts pre-call leak & bloat diagnostics without mutating payloads."""
    sys_chars = 0
    tool_count = 0
    thinking_str = ""
    media_res_str = ""

    if config is not None:
        si = getattr(config, "system_instruction", None) if not isinstance(config, dict) else config.get("system_instruction")
        if isinstance(si, str):
            sys_chars = len(si)
        elif si is not None:
            sys_chars = len(str(si))

        tools = getattr(config, "tools", None) if not isinstance(config, dict) else config.get("tools")
        if isinstance(tools, list):
            for t in tools:
                fdecls = getattr(t, "function_declarations", None) if not isinstance(t, dict) else t.get("function_declarations")
                if isinstance(fdecls, list):
                    tool_count += len(fdecls)
                elif getattr(t, "google_search", None) or (isinstance(t, dict) and t.get("google_search")):
                    tool_count += 1

        tc = getattr(config, "thinking_config", None) if not isinstance(config, dict) else config.get("thinking_config")
        if tc is not None:
            tl = getattr(tc, "thinking_level", None) if not isinstance(tc, dict) else tc.get("thinking_level")
            tb = getattr(tc, "thinking_budget", None) if not isinstance(tc, dict) else tc.get("thinking_budget")
            if tl is not None:
                thinking_str = f"level={tl}"
            elif tb is not None:
                thinking_str = f"budget={tb}"
            else:
                thinking_str = str(tc)[:48]

        mr = getattr(config, "media_resolution", None) if not isinstance(config, dict) else config.get("media_resolution")
        if mr is not None:
            media_res_str = str(mr).split(".")[-1].replace("MEDIA_RESOLUTION_", "").lower()

    # Check per-part media_resolution if not set globally & count history messages
    hist_count = 0
    if isinstance(contents, list):
        hist_count = len(contents)
        if not media_res_str:
            def _find_part_res(seq: list) -> str:
                for it in seq:
                    pmr = getattr(it, "media_resolution", None) if not isinstance(it, dict) else it.get("media_resolution")
                    if pmr is not None:
                        lvl = getattr(pmr, "level", pmr)
                        return str(lvl).split(".")[-1].replace("MEDIA_RESOLUTION_", "").lower()
                    parts = getattr(it, "parts", None) if not isinstance(it, dict) else it.get("parts")
                    if isinstance(parts, list):
                        found = _find_part_res(parts)
                        if found:
                            return found
                return ""
            media_res_str = _find_part_res(contents)
    elif contents is not None:
        hist_count = 1

    return {
        "system_prompt_chars": sys_chars,
        "tool_declarations_count": tool_count,
        "history_message_count": hist_count,
        "thinking_config": thinking_str or "default",
        "media_resolution": media_res_str,
    }


def _extract_usage_and_grounding(response: Any) -> Dict[str, int]:
    """Extracts token counts, modality breakdown, and Google Search grounding query count."""
    out = {
        "prompt_token_count": 0,
        "cached_content_token_count": 0,
        "tool_use_prompt_token_count": 0,
        "candidates_token_count": 0,
        "thoughts_token_count": 0,
        "total_token_count": 0,
        "prompt_image_tokens": 0,
        "prompt_audio_tokens": 0,
        "grounding_queries_count": 0,
    }
    if response is None:
        return out

    um = getattr(response, "usage_metadata", None)
    if um is not None:
        for k in (
            "prompt_token_count",
            "cached_content_token_count",
            "tool_use_prompt_token_count",
            "candidates_token_count",
            "thoughts_token_count",
            "total_token_count",
        ):
            val = getattr(um, k, None)
            if val is not None:
                try:
                    out[k] = int(val)
                except (TypeError, ValueError):
                    pass

        # Gemini Live WebSocket UsageMetadata uses `response_token_count` instead of `candidates_token_count`
        if out["candidates_token_count"] == 0:
            resp_tok = getattr(um, "response_token_count", None)
            if resp_tok is not None:
                try:
                    out["candidates_token_count"] = int(resp_tok)
                except (TypeError, ValueError):
                    pass

        details = getattr(um, "prompt_tokens_details", None) or []
        for det in details:
            mod = str(getattr(det, "modality", "") or "").upper()
            cnt = int(getattr(det, "token_count", 0) or 0)
            if "IMAGE" in mod:
                out["prompt_image_tokens"] += cnt
            elif "AUDIO" in mod:
                out["prompt_audio_tokens"] += cnt

    # Count grounding web_search_queries across candidates
    cands = getattr(response, "candidates", None) or []
    for c in cands:
        gm = getattr(c, "grounding_metadata", None)
        if gm is not None:
            wsq = getattr(gm, "web_search_queries", None) or []
            if isinstance(wsq, (list, tuple)):
                out["grounding_queries_count"] += len(wsq)

    return out


def _finalize_and_log_call(
    *,
    ctx: Optional[Dict[str, Any]],
    model_name: str,
    diag: Dict[str, Any],
    imgs_kept: int,
    imgs_scrubbed: int,
    response: Any,
    latency_ms: float,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    feat = str((ctx or {}).get("feature_tag") or "unclassified")
    sub = str((ctx or {}).get("sub_action") or "generate_content")
    step = int((ctx or {}).get("loop_step") or 1)
    turn_id = str((ctx or {}).get("turn_id") or "")

    if feat == "unclassified":
        logger.warning("[API_TELEMETRY] Unclassified Gemini API call to model='%s' (sub='%s') — add api_feature_context()!", model_name, sub)

    usage = _extract_usage_and_grounding(response)
    cost = calculate_api_cost_usd(
        model_name=model_name,
        prompt_token_count=usage["prompt_token_count"],
        cached_content_token_count=usage["cached_content_token_count"],
        tool_use_prompt_token_count=usage["tool_use_prompt_token_count"],
        candidates_token_count=usage["candidates_token_count"],
        thoughts_token_count=usage["thoughts_token_count"],
        grounding_queries_count=usage["grounding_queries_count"],
    )

    rec: Dict[str, Any] = {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "session_id": _PROCESS_SESSION_ID,
        "turn_id": turn_id,
        "loop_step": step,
        "feature_tag": feat,
        "sub_action": sub,
        "model_name": str(model_name or ""),
        "images_attached_count": imgs_kept,
        "images_scrubbed_count": imgs_scrubbed,
        "media_resolution": diag.get("media_resolution", ""),
        "system_prompt_chars": diag.get("system_prompt_chars", 0),
        "tool_declarations_count": diag.get("tool_declarations_count", 0),
        "history_message_count": diag.get("history_message_count", 0),
        "thinking_config": diag.get("thinking_config", ""),
        **usage,
        "latency_ms": round(latency_ms, 1),
        "estimated_cost_usd": cost,
    }

    # Compact [API_COST] log line
    logger.info(
        "[API_COST] feat=%s sub=%s step=%d model=%s tools=%d hist=%d imgs=%d(scrub=%d) "
        "in=%d(cached=%d,img=%d) out=%d(thoughts=%d) search=%d cost=$%.5f",
        feat,
        sub,
        step,
        rec["model_name"],
        rec["tool_declarations_count"],
        rec["history_message_count"],
        imgs_kept,
        imgs_scrubbed,
        usage["prompt_token_count"],
        usage["cached_content_token_count"],
        usage["prompt_image_tokens"],
        usage["candidates_token_count"],
        usage["thoughts_token_count"],
        usage["grounding_queries_count"],
        cost,
    )

    record_api_usage_row(rec, db_path=db_path)
    return rec


# ---------------------------------------------------------------------------
# Centralized Global google.genai Instrumentation Hooks
# ---------------------------------------------------------------------------

def install_genai_telemetry_hooks() -> bool:
    """
    Idempotently wraps `google.genai.models.Models` and `AsyncModels` (`generate_content` and
    `generate_content_stream`) so EVERY Gemini API call in Aether automatically:
      1. Scrubs stale screenshots (`keep_latest_n=1` by default)
      2. Logs feature-attributed `[API_COST]` lines & SQLite `api_usage_log` rows
    """
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return True
    try:
        from google.genai import models as _genai_models
    except Exception as exc:
        logger.debug("[API_TELEMETRY] google.genai not available for hooks: %s", exc)
        return False

    orig_sync_gen = _genai_models.Models.generate_content
    orig_sync_stream = _genai_models.Models.generate_content_stream
    orig_async_gen = _genai_models.AsyncModels.generate_content
    orig_async_stream = _genai_models.AsyncModels.generate_content_stream

    def _wrapped_sync_gen(self, *, model: str, contents: Any, config: Any = None, **kwargs):
        ctx = _CURRENT_CONTEXT.get()
        keep_n = (ctx or {}).get("keep_latest_images", 1)
        contents, imgs_kept, imgs_scrubbed = scrub_stale_images(contents, keep_latest_n=keep_n)
        diag = _inspect_request_diagnostics(contents, config)
        t0 = time.perf_counter()
        resp = orig_sync_gen(self, model=model, contents=contents, config=config, **kwargs)
        _finalize_and_log_call(
            ctx=ctx,
            model_name=model,
            diag=diag,
            imgs_kept=imgs_kept,
            imgs_scrubbed=imgs_scrubbed,
            response=resp,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )
        return resp

    def _wrapped_sync_stream(self, *, model: str, contents: Any, config: Any = None, **kwargs):
        ctx = _CURRENT_CONTEXT.get()
        keep_n = (ctx or {}).get("keep_latest_images", 1)
        contents, imgs_kept, imgs_scrubbed = scrub_stale_images(contents, keep_latest_n=keep_n)
        diag = _inspect_request_diagnostics(contents, config)
        t0 = time.perf_counter()
        stream = orig_sync_stream(self, model=model, contents=contents, config=config, **kwargs)

        def _gen():
            last_chunk = None
            try:
                for chunk in stream:
                    last_chunk = chunk
                    yield chunk
            finally:
                _finalize_and_log_call(
                    ctx=ctx,
                    model_name=model,
                    diag=diag,
                    imgs_kept=imgs_kept,
                    imgs_scrubbed=imgs_scrubbed,
                    response=last_chunk,
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                )

        return _gen()

    async def _wrapped_async_gen(self, *, model: str, contents: Any, config: Any = None, **kwargs):
        ctx = _CURRENT_CONTEXT.get()
        keep_n = (ctx or {}).get("keep_latest_images", 1)
        contents, imgs_kept, imgs_scrubbed = scrub_stale_images(contents, keep_latest_n=keep_n)
        diag = _inspect_request_diagnostics(contents, config)
        t0 = time.perf_counter()
        resp = await orig_async_gen(self, model=model, contents=contents, config=config, **kwargs)
        _finalize_and_log_call(
            ctx=ctx,
            model_name=model,
            diag=diag,
            imgs_kept=imgs_kept,
            imgs_scrubbed=imgs_scrubbed,
            response=resp,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )
        return resp

    async def _wrapped_async_stream(self, *, model: str, contents: Any, config: Any = None, **kwargs):
        ctx = _CURRENT_CONTEXT.get()
        keep_n = (ctx or {}).get("keep_latest_images", 1)
        contents, imgs_kept, imgs_scrubbed = scrub_stale_images(contents, keep_latest_n=keep_n)
        diag = _inspect_request_diagnostics(contents, config)
        t0 = time.perf_counter()
        stream = await orig_async_stream(self, model=model, contents=contents, config=config, **kwargs)

        async def _agen():
            last_chunk = None
            try:
                async for chunk in stream:
                    last_chunk = chunk
                    yield chunk
            finally:
                _finalize_and_log_call(
                    ctx=ctx,
                    model_name=model,
                    diag=diag,
                    imgs_kept=imgs_kept,
                    imgs_scrubbed=imgs_scrubbed,
                    response=last_chunk,
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                )

        return _agen()

    _genai_models.Models.generate_content = _wrapped_sync_gen
    _genai_models.Models.generate_content_stream = _wrapped_sync_stream
    _genai_models.AsyncModels.generate_content = _wrapped_async_gen
    _genai_models.AsyncModels.generate_content_stream = _wrapped_async_stream
    _HOOKS_INSTALLED = True
    logger.info("[API_TELEMETRY] Global google.genai telemetry & screenshot scrubber hooks installed.")
    return True


# ---------------------------------------------------------------------------
# Feature Breakdown & Top-5 Cost Report (--cost-report & Shutdown Summary)
# ---------------------------------------------------------------------------

def format_cost_report(
    session_id: Optional[str] = None,
    since_utc: Optional[str] = None,
    db_path: Optional[str] = None,
) -> str:
    """
    Formats a human-readable Feature Breakdown Table and Top 5 Most Expensive Calls
    from SQLite `api_usage_log`.
    """
    rows = query_api_usage_rows(session_id=session_id, since_utc=since_utc, db_path=db_path)
    scope = f"Session {session_id}" if session_id else (f"Since {since_utc}" if since_utc else "All Recorded Calls")
    if not rows:
        return f"=== AETHER API COST REPORT ({scope}) ===\nNo API calls recorded yet."

    by_feat: Dict[str, Dict[str, Any]] = {}
    total_usd = 0.0
    for r in rows:
        feat = str(r.get("feature_tag") or "unclassified")
        agg = by_feat.setdefault(feat, {
            "calls": 0,
            "imgs": 0,
            "uncached_in": 0,
            "cached_in": 0,
            "prompt_in": 0,
            "out": 0,
            "thoughts": 0,
            "search": 0,
            "usd": 0.0,
        })
        p_in = int(r.get("prompt_token_count") or 0)
        c_in = int(r.get("cached_content_token_count") or 0)
        t_in = int(r.get("tool_use_prompt_token_count") or 0)
        agg["calls"] += 1
        agg["imgs"] += int(r.get("images_attached_count") or 0)
        agg["prompt_in"] += p_in
        agg["cached_in"] += c_in
        agg["uncached_in"] += max(0, p_in - c_in) + t_in
        agg["out"] += int(r.get("candidates_token_count") or 0)
        agg["thoughts"] += int(r.get("thoughts_token_count") or 0)
        agg["search"] += int(r.get("grounding_queries_count") or 0)
        cost = float(r.get("estimated_cost_usd") or 0.0)
        agg["usd"] += cost
        total_usd += cost

    lines = [
        f"=== AETHER API COST REPORT ({scope}) ===",
        f"Total Calls: {len(rows)} | Total Estimated Spend: ${total_usd:.5f}",
        "",
        "Feature Breakdown:",
        f"{'Feature':<22} {'Calls':>5} {'Imgs':>5} {'UncachedIn':>11} {'CachedIn (Hit%)':>18} {'Output':>9} {'Thoughts':>9} {'Search':>6} {'Subtotal ($ / %)':>19}",
        "-" * 112,
    ]
    for feat, agg in sorted(by_feat.items(), key=lambda kv: kv[1]["usd"], reverse=True):
        hit_pct = (agg["cached_in"] / agg["prompt_in"] * 100.0) if agg["prompt_in"] > 0 else 0.0
        share_pct = (agg["usd"] / total_usd * 100.0) if total_usd > 0 else 0.0
        cached_col = f"{agg['cached_in']:,} ({hit_pct:.0f}%)"
        sub_col = f"${agg['usd']:.5f} ({share_pct:.1f}%)"
        lines.append(
            f"{feat:<22} {agg['calls']:>5} {agg['imgs']:>5} {agg['uncached_in']:>11,} "
            f"{cached_col:>18} {agg['out']:>9,} {agg['thoughts']:>9,} {agg['search']:>6} {sub_col:>19}"
        )

    lines.append("")
    lines.append("Top 5 Most Expensive Calls:")
    top5 = sorted(rows, key=lambda r: float(r.get("estimated_cost_usd") or 0.0), reverse=True)[:5]
    for idx, r in enumerate(top5, 1):
        lines.append(
            f"  {idx}. [{r.get('feature_tag')}/{r.get('sub_action') or '-'}] model={r.get('model_name')} "
            f"tools={r.get('tool_declarations_count', 0)} imgs={r.get('images_attached_count', 0)} "
            f"prompt={r.get('prompt_token_count', 0):,} thoughts={r.get('thoughts_token_count', 0):,} "
            f"search={r.get('grounding_queries_count', 0)} cost=${float(r.get('estimated_cost_usd') or 0.0):.5f}"
        )

    return "\n".join(lines)


def record_live_session_usage(
    response: Any,
    *,
    model_name: str = "gemini-3.8-live",
    feature_tag: str = "casual_chat",
    sub_action: str = "live_websocket_turn",
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Meters token usage & USD cost emitted by Gemini Multimodal Live WebSocket frames
    (`response.usage_metadata`) into `api_usage_log` and `[API_COST]` logs so `--cost-report`
    has zero blind spots for Live voice/video/text sessions.
    """
    um = getattr(response, "usage_metadata", None)
    if um is None:
        return None
    usage = _extract_usage_and_grounding(response)
    if usage["prompt_token_count"] <= 0 and usage["candidates_token_count"] <= 0 and usage["total_token_count"] <= 0:
        return None
    tag = str(feature_tag or "casual_chat").strip().lower()
    if tag not in VALID_FEATURE_TAGS:
        tag = "casual_chat"
    return _finalize_and_log_call(
        ctx={
            "feature_tag": tag,
            "sub_action": sub_action,
            "loop_step": 1,
            "turn_id": uuid.uuid4().hex[:10],
        },
        model_name=model_name,
        diag={
            "system_prompt_chars": 0,
            "tool_declarations_count": 0,
            "history_message_count": 1,
            "thinking_config": "live",
            "media_resolution": "",
        },
        imgs_kept=1 if usage["prompt_image_tokens"] > 0 else 0,
        imgs_scrubbed=0,
        response=response,
        latency_ms=0.0,
        db_path=db_path,
    )
