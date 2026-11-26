"""
core/doc_validator.py - Zero-Token Deterministic Validator & Schema Contract
for Aether's Multi-Model Document & Presentation Pipeline (Neon -> Hydrogen -> Workspace).

Validates:
1. `lecture_spec.json` schema, module-to-slide range integrity, bullet word/count bounds,
   table/chart slide bindings, and matplotlib curve continuity.
2. Compiled `.pptx` XML & `.docx` XML text hygiene:
   - Unbalanced delimiters: (), [], quotes
   - Truncated punctuation / dangling sentence fragments / leaked Markdown (`, **, ###, <br>)
   - Naked leading decimals (clinical/numeric safety: flags `.1 U/kg`, requires `0.1 U/kg`)
   - Dropped content check (every `bullets[]` and `cards[]` item in `lecture_spec.json` present in full)
   - Slide-bound table and chart presence + programmatic Slide 1 module roadmap accuracy
   - Presenter-cue leakage check (e.g., Attending Clinical Pearl must stay in Speaker Notes, not visible slides)
3. Cross-artifact factual consistency across `canonical_facts`, dossier, `.pptx`, and `.docx`.
4. `matplotlib` curve continuity (`np.abs(np.diff(y))`) and chart PNG quality (`dpi >= 300`).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

VALID_LAYOUT_TYPES = frozenset({
    "title_roadmap",
    "bullets",
    "bullets_plus_chart",
    "table_slide",
    "case_cards",
    "summary_rules",
})

LECTURE_SPEC_START_MARKER = "<!-- LECTURE_SPEC_JSON_START -->"
LECTURE_SPEC_END_MARKER = "<!-- LECTURE_SPEC_JSON_END -->"

# Presenter-only markers that must never appear on audience-facing visible slide shapes
FORBIDDEN_VISIBLE_SLIDE_PHRASES = (
    "attending clinical pearl",
    "slide visual blueprint",
    "speaker notes:",
    "instructor note:",
    "ask pgy-",
    "ask residents",
    "stress to pgy-",
    "remind residents",
    "remind learners",
)

# Regex patterns for text hygiene & clinical/numeric safety
_NAKED_DECIMAL_RE = re.compile(r"(?<![\d\w])\.\d+(?!\w*[a-zA-Z]{2,}\b)")
_TRAILING_ELLIPSIS_RE = re.compile(r"(?:\.\.\.|…)\s*$")
_DANGLING_FRAGMENT_RE = re.compile(
    r"(?:"
    r":\."
    r"|,\."
    r"|;\."
    r"|\(\."
    r"|[<>≥≤]\."
    r"|\b(?:vs|with a|with an|and|or|the|of|for|to|in|on|at|by|from|than|into)\."
    r"|[–—-]\d{1,2}\."
    r")\s*$",
    re.IGNORECASE,
)
_MARKDOWN_LEAK_RE = re.compile(r"(?:`|\*\*|^\s*#{1,6}\s|<br\s*/?>)", re.IGNORECASE | re.MULTILINE)


def format_lecture_spec_schema_prompt() -> str:
    """Canonical schema contract injected into Neon's research_dossier prompt."""
    return (
        "MACHINE-READABLE SINGLE-SOURCE-OF-TRUTH CONTRACT (`lecture_spec.json`):\n"
        f"After the complete Markdown dossier, append a strict JSON block wrapped EXACTLY between "
        f"`{LECTURE_SPEC_START_MARKER}` and `{LECTURE_SPEC_END_MARKER}` containing:\n"
        "{\n"
        '  "metadata": {\n'
        '    "title": "Deck Title",\n'
        '    "subtitle": "Subtitle",\n'
        '    "audience": "Target Audience (e.g. PGY 1-3 Internal Medicine Residents)",\n'
        '    "total_slides": 26,\n'
        '    "guidelines_version": "Exact guidelines year/source"\n'
        "  },\n"
        '  "canonical_facts": {\n'
        '    "fact_key": "Exact canonical threshold/rule (e.g. >= 200 mg/dL, < 3.5 mEq/L)"\n'
        "  },\n"
        '  "modules": [\n'
        '    {"id": 1, "name": "Module Name", "slide_start": 1, "slide_end": 4}\n'
        "  ],\n"
        '  "slides": [\n'
        "    {\n"
        '      "slide_number": 1,\n'
        '      "module_id": 1,\n'
        '      "title": "Plain text slide title (no Markdown)",\n'
        '      "layout_type": "title_roadmap | bullets | bullets_plus_chart | table_slide | case_cards | summary_rules",\n'
        '      "bullets": ["Concise, complete audience-facing phrase (MAX 5 bullets, <= 22 words each, ZERO Markdown/backticks, explicit leading zeros like 0.1 U/kg)"],\n'
        '      "table": {"title": "Optional table title", "headers": ["Col 1", "Col 2"], "rows": [["v1", "v2"]]},\n'
        '      "chart_spec": {"title": "Chart Title", "chart_type": "line | bar | grouped_bar", "x_label": "X", "y_label": "Y", "series": [{"name": "S1", "x": [0, 1], "y": [10, 20]}], "annotations": [], "smooth_continuous": true},\n'
        '      "cards": [{"title": "Case 1 (PGY-1)", "items": ["Concise complete line 1", "Concise complete line 2"]}],\n'
        '      "speaker_notes": "Full attending script, Socratic questions, clinical pearls, and citations (NEVER put attending prompts in bullets[])"\n'
        "    }\n"
        "  ],\n"
        '  "handout_sections": [\n'
        "    {\n"
        '      "title": "Section Title",\n'
        '      "target_pages": 2,\n'
        '      "rules": ["Audience-facing quick-reference rule derived strictly from canonical_facts"],\n'
        '      "tables": [{"title": "Table Title", "headers": ["Col 1", "Col 2"], "rows": [["v1", "v2"]]}]\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "STRICT RULES FOR `lecture_spec.json`:\n"
        "- `len(slides)` MUST equal `metadata.total_slides`, numbered sequentially `1..N`.\n"
        "- Every slide's `module_id` MUST match a module in `modules[]` whose `slide_start <= slide_number <= slide_end`.\n"
        "- Every bullet in `bullets[]` MUST be a complete, standalone audience-facing phrase (MAX 5 bullets/slide, <= 22 words each, NO backticks, NO `**`, NO `...` truncation, ALWAYS include leading zero on decimals like `0.1 U/kg`).\n"
        "- Multi-case slides (e.g. PGY-1/2/3 vignettes) MUST use `layout_type: 'case_cards'` with structured `cards[]` items so nothing overflows or drops.\n"
        "- Every `chart_spec` with `smooth_continuous: true` MUST have smooth numeric `y` arrays with no abrupt piecewise jumps.\n"
        "- `handout_sections[]` MUST use the EXACT same thresholds/numbers as `canonical_facts` and `slides[]` (never outdated guidelines).\n"
    )


def extract_lecture_spec_from_text(raw_text: str) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
    """
    Splits a Neon response containing a Markdown dossier + fenced `lecture_spec.json` block.
    Returns `(clean_markdown, parsed_spec_or_None, parse_error_or_None)`.
    """
    text = raw_text or ""
    if LECTURE_SPEC_START_MARKER in text and LECTURE_SPEC_END_MARKER in text:
        pattern = re.compile(
            re.escape(LECTURE_SPEC_START_MARKER) + r"([\s\S]*?)" + re.escape(LECTURE_SPEC_END_MARKER)
        )
        m = pattern.search(text)
        if m:
            raw_json_block = m.group(1).strip()
            # Strip optional ```json ... ``` fences inside markers
            fence_m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw_json_block, re.IGNORECASE)
            json_str = fence_m.group(1).strip() if fence_m else raw_json_block
            clean_md = (text[:m.start()] + text[m.end():]).strip()
            try:
                spec = json.loads(json_str)
                if isinstance(spec, dict):
                    return clean_md, spec, None
                return clean_md, None, "Extracted lecture_spec JSON is not a JSON object."
            except Exception as e:
                return clean_md, None, f"Failed to parse lecture_spec JSON block: {e}"
    return text.strip(), None, None


def normalize_for_comparison(text: str) -> str:
    """Normalizes Unicode operators, whitespace, and punctuation for cross-artifact comparison."""
    s = str(text or "")
    replacements = {
        "≥": ">=",
        "≤": "<=",
        "–": "-",
        "—": "-",
        "−": "-",
        "⁺": "+",
        "⁻": "-",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "\u00a0": " ",
        "▪": "",
        "•": "",
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    s = re.sub(r"\s+", " ", s).strip().lower()
    # Normalize spacing around comparison operators: '>= 200' -> '>=200'
    s = re.sub(r"\s*(>=|<=|>|<)\s*", r" \1 ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _count_words(text: str) -> int:
    return len([w for w in re.split(r"\s+", str(text or "").strip()) if w])


def has_unbalanced_delimiters(text: str) -> Optional[str]:
    """Detects unmatched parentheses, brackets, or double quotes in a text item."""
    s = str(text or "")
    # Parentheses & square brackets via stack
    pairs = {"(": ")", "[": "]"}
    closing = {")": "(", "]": "["}
    stack: List[str] = []
    for ch in s:
        if ch in pairs:
            stack.append(ch)
        elif ch in closing:
            if not stack or stack[-1] != closing[ch]:
                return f"Unmatched closing delimiter '{ch}'"
            stack.pop()
    if stack:
        return f"Unclosed delimiter '{stack[-1]}'"

    # Straight double quotes (must be even count)
    if s.count('"') % 2 != 0:
        return 'Unbalanced straight double quote (")'

    # Curly double quotes
    if s.count("“") != s.count("”"):
        return "Unbalanced curly quotes (“ / ”)"

    return None


def lint_text_item(
    text: str,
    location: str,
    *,
    check_presenter_leak: bool = False,
    max_words: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Runs deterministic text hygiene and clinical/numeric safety checks on a single string."""
    issues: List[Dict[str, Any]] = []
    s = str(text or "").strip()
    if not s:
        return issues

    delim_err = has_unbalanced_delimiters(s)
    if delim_err:
        issues.append({
            "severity": "ERROR",
            "code": "UNBALANCED_DELIMITER",
            "location": location,
            "message": f"{delim_err} in: {s[:120]}",
        })

    if _TRAILING_ELLIPSIS_RE.search(s):
        issues.append({
            "severity": "ERROR",
            "code": "TRUNCATED_ELLIPSIS",
            "location": location,
            "message": f"Trailing ellipsis truncation detected: {s[:120]}",
        })

    if _DANGLING_FRAGMENT_RE.search(s):
        issues.append({
            "severity": "ERROR",
            "code": "TRUNCATED_FRAGMENT",
            "location": location,
            "message": f"Dangling/truncated punctuation artifact at end of text: {s[:120]}",
        })

    if _MARKDOWN_LEAK_RE.search(s):
        issues.append({
            "severity": "ERROR",
            "code": "MARKDOWN_LEAK",
            "location": location,
            "message": f"Raw Markdown/HTML token leaked into rendered text: {s[:120]}",
        })

    # Naked leading decimal check (ignore URLs / file extensions)
    scrubbed_for_dec = re.sub(r"https?://\S+", "", s)
    scrubbed_for_dec = re.sub(r"\b\w+\.(?:pptx|docx|pdf|png|jpg|md|json|py)\b", "", scrubbed_for_dec, flags=re.IGNORECASE)
    dec_match = _NAKED_DECIMAL_RE.search(scrubbed_for_dec)
    if dec_match:
        issues.append({
            "severity": "ERROR",
            "code": "NAKED_LEADING_DECIMAL",
            "location": location,
            "message": f"Naked leading decimal '{dec_match.group(0)}' (must include leading zero, e.g. '0{dec_match.group(0)}') in: {s[:120]}",
        })

    if check_presenter_leak:
        s_low = s.lower()
        for phrase in FORBIDDEN_VISIBLE_SLIDE_PHRASES:
            if phrase in s_low:
                issues.append({
                    "severity": "ERROR",
                    "code": "PRESENTER_CUE_ON_SLIDE",
                    "location": location,
                    "message": f"Presenter-only cue ('{phrase}') leaked onto visible slide; move to speaker_notes: {s[:120]}",
                })
                break

    if max_words is not None:
        wc = _count_words(s)
        if wc > max_words:
            issues.append({
                "severity": "ERROR",
                "code": "BULLET_WORD_LIMIT",
                "location": location,
                "message": f"Bullet has {wc} words (limit is <= {max_words}): {s[:120]}",
            })

    return issues


def check_curve_continuity(
    y_values: Sequence[float],
    series_name: str = "series",
    location: str = "chart",
    max_step_fraction: float = 0.25,
    spike_ratio_threshold: float = 8.0,
) -> List[Dict[str, Any]]:
    """
    Inspects a continuous plotted `y` array via `np.abs(np.diff(y))` and flags sharp
    piecewise discontinuities (e.g., a sudden vertical drop/step like a broken piecewise join).
    """
    issues: List[Dict[str, Any]] = []
    try:
        arr = np.asarray(y_values, dtype=float)
    except Exception as e:
        return [{
            "severity": "ERROR",
            "code": "CHART_INVALID_DATA",
            "location": location,
            "message": f"Series '{series_name}' has non-numeric y values: {e}",
        }]

    if arr.size < 3:
        return issues
    if not np.all(np.isfinite(arr)):
        return [{
            "severity": "ERROR",
            "code": "CHART_NON_FINITE",
            "location": location,
            "message": f"Series '{series_name}' contains NaN or Inf values.",
        }]

    y_span = float(np.max(arr) - np.min(arr))
    if y_span <= 1e-9:
        return issues

    diffs = np.abs(np.diff(arr))
    max_idx = int(np.argmax(diffs))
    max_jump = float(diffs[max_idx])
    jump_frac = max_jump / y_span

    # Dense curve discontinuity detection:
    # 1) Any single step jumping > max_step_fraction of total span on a sampled curve (len >= 8)
    # 2) Or an isolated jump > spike_ratio_threshold * local neighboring step median (catches vertical steps!)
    if arr.size >= 8 and jump_frac > max_step_fraction:
        issues.append({
            "severity": "ERROR",
            "code": "CHART_DISCONTINUITY",
            "location": location,
            "message": (
                f"Series '{series_name}' has a sharp discontinuity at index {max_idx}->{max_idx + 1}: "
                f"step {max_jump:.4g} is {jump_frac * 100:.1f}% of y-range ({y_span:.4g})."
            ),
        })
        return issues

    if arr.size >= 15:
        # Compare max_jump against the 75th-percentile non-zero step (ignores flat tails)
        nonzero_diffs = diffs[diffs > (y_span * 1e-4)]
        if nonzero_diffs.size >= 5:
            p75 = float(np.percentile(nonzero_diffs, 75))
            if p75 > 0 and max_jump > (y_span * 0.12) and (max_jump / p75) > spike_ratio_threshold:
                issues.append({
                    "severity": "ERROR",
                    "code": "CHART_DISCONTINUITY",
                    "location": location,
                    "message": (
                        f"Series '{series_name}' has an abrupt piecewise jump at index {max_idx}->{max_idx + 1}: "
                        f"step {max_jump:.4g} is {max_jump / p75:.1f}x the typical curve step ({p75:.4g})."
                    ),
                })

    return issues


def check_chart_png(png_path: str, min_width_px: int = 900, min_dpi: int = 200) -> List[Dict[str, Any]]:
    """Checks a saved chart PNG exists, is non-blank, and meets resolution/DPI standards."""
    issues: List[Dict[str, Any]] = []
    if not png_path or not os.path.isfile(png_path):
        return [{
            "severity": "ERROR",
            "code": "CHART_MISSING",
            "location": str(png_path),
            "message": f"Expected chart PNG not found: {png_path}",
        }]
    try:
        from PIL import Image
        with Image.open(png_path) as img:
            w, h = img.size
            dpi_info = img.info.get("dpi")
            if w < min_width_px or h < 450:
                issues.append({
                    "severity": "ERROR",
                    "code": "CHART_LOW_RESOLUTION",
                    "location": png_path,
                    "message": f"Chart PNG resolution {w}x{h}px is too low (expected >= {min_width_px}px wide; save at dpi=300).",
                })
            if dpi_info and isinstance(dpi_info, (tuple, list)) and len(dpi_info) >= 1:
                if float(dpi_info[0]) < min_dpi:
                    issues.append({
                        "severity": "WARNING",
                        "code": "CHART_LOW_DPI",
                        "location": png_path,
                        "message": f"Chart PNG DPI metadata is {dpi_info[0]:.0f} (recommended dpi=300).",
                    })
            arr = np.asarray(img.convert("RGB"), dtype=float)
            if float(np.std(arr)) < 2.0:
                issues.append({
                    "severity": "ERROR",
                    "code": "CHART_BLANK",
                    "location": png_path,
                    "message": "Chart PNG appears blank/solid color.",
                })
    except Exception as e:
        issues.append({
            "severity": "ERROR",
            "code": "CHART_UNREADABLE",
            "location": png_path,
            "message": f"Could not inspect chart PNG '{png_path}': {e}",
        })
    return issues


def validate_lecture_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validates `lecture_spec.json` against the single-source-of-truth contract.
    Returns `{passed: bool, errors: List[dict], warnings: List[dict], summary: str}`.
    """
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    def _add(issue: Dict[str, Any]):
        if issue.get("severity") == "WARNING":
            warnings.append(issue)
        else:
            errors.append(issue)

    if not isinstance(spec, dict):
        return {
            "passed": False,
            "errors": [{"severity": "ERROR", "code": "SPEC_NOT_DICT", "location": "root", "message": "lecture_spec must be a JSON object."}],
            "warnings": [],
            "summary": "FAIL: lecture_spec is not a JSON object.",
        }

    # 1. Top-level keys
    for req_key in ("metadata", "canonical_facts", "modules", "slides"):
        if req_key not in spec:
            _add({"severity": "ERROR", "code": "MISSING_KEY", "location": "root", "message": f"Missing required top-level key '{req_key}'."})

    meta = spec.get("metadata") if isinstance(spec.get("metadata"), dict) else {}
    modules = spec.get("modules") if isinstance(spec.get("modules"), list) else []
    slides = spec.get("slides") if isinstance(spec.get("slides"), list) else []
    canonical_facts = spec.get("canonical_facts") if isinstance(spec.get("canonical_facts"), dict) else {}
    handout_sections = spec.get("handout_sections") if isinstance(spec.get("handout_sections"), list) else []

    for mk in ("title", "audience", "total_slides"):
        if not meta.get(mk):
            _add({"severity": "ERROR", "code": "MISSING_METADATA", "location": f"metadata.{mk}", "message": f"Missing required metadata.{mk}."})

    try:
        declared_total = int(meta.get("total_slides") or 0)
    except (TypeError, ValueError):
        declared_total = 0

    if declared_total <= 0:
        _add({"severity": "ERROR", "code": "INVALID_TOTAL_SLIDES", "location": "metadata.total_slides", "message": "metadata.total_slides must be a positive integer."})
    elif len(slides) != declared_total:
        _add({
            "severity": "ERROR",
            "code": "SLIDE_COUNT_MISMATCH",
            "location": "slides",
            "message": f"len(slides)={len(slides)} does not match metadata.total_slides={declared_total}.",
        })

    if not canonical_facts:
        _add({"severity": "WARNING", "code": "EMPTY_CANONICAL_FACTS", "location": "canonical_facts", "message": "canonical_facts is empty; cross-artifact fact checking will be limited."})

    # 2. Module range map & overlap/gap check
    mod_by_id: Dict[Any, Dict[str, Any]] = {}
    covered_slides: Dict[int, Any] = {}
    for m_idx, mod in enumerate(modules):
        loc = f"modules[{m_idx}]"
        if not isinstance(mod, dict):
            _add({"severity": "ERROR", "code": "INVALID_MODULE", "location": loc, "message": "Module entry must be an object."})
            continue
        mid = mod.get("id")
        mname = str(mod.get("name") or "").strip()
        try:
            s_start = int(mod.get("slide_start"))
            s_end = int(mod.get("slide_end"))
        except (TypeError, ValueError):
            _add({"severity": "ERROR", "code": "INVALID_MODULE_RANGE", "location": loc, "message": f"Module '{mname or mid}' must have integer slide_start and slide_end."})
            continue
        if s_start < 1 or s_end < s_start:
            _add({"severity": "ERROR", "code": "INVALID_MODULE_RANGE", "location": loc, "message": f"Module '{mname}' has invalid range {s_start}..{s_end}."})
            continue
        mod_by_id[mid] = mod
        for sn in range(s_start, s_end + 1):
            if sn in covered_slides:
                _add({
                    "severity": "ERROR",
                    "code": "MODULE_OVERLAP",
                    "location": loc,
                    "message": f"Slide {sn} is claimed by both module {covered_slides[sn]} and module {mid}.",
                })
            covered_slides[sn] = mid

    # 3. Validate each slide
    for idx, sl in enumerate(slides, start=1):
        loc = f"slides[{idx - 1}]"
        if not isinstance(sl, dict):
            _add({"severity": "ERROR", "code": "INVALID_SLIDE", "location": loc, "message": "Slide entry must be an object."})
            continue
        snum = sl.get("slide_number")
        if snum != idx:
            _add({
                "severity": "ERROR",
                "code": "SLIDE_NUMBER_SEQUENCE",
                "location": loc,
                "message": f"Expected sequential slide_number={idx}, got {snum}.",
            })
        s_loc = f"slide_{snum or idx}"

        # Module alignment
        mod_id = sl.get("module_id")
        if modules:
            if mod_id not in mod_by_id:
                _add({"severity": "ERROR", "code": "UNKNOWN_MODULE_ID", "location": s_loc, "message": f"Slide {snum} references unknown module_id={mod_id}."})
            else:
                m_obj = mod_by_id[mod_id]
                if not (int(m_obj["slide_start"]) <= int(snum or idx) <= int(m_obj["slide_end"])):
                    _add({
                        "severity": "ERROR",
                        "code": "SLIDE_MODULE_RANGE_MISMATCH",
                        "location": s_loc,
                        "message": f"Slide {snum} has module_id={mod_id} ('{m_obj.get('name')}'), which spans slides {m_obj['slide_start']}..{m_obj['slide_end']}.",
                    })

        # Layout type
        layout = str(sl.get("layout_type") or "").strip()
        if layout not in VALID_LAYOUT_TYPES:
            _add({
                "severity": "ERROR",
                "code": "INVALID_LAYOUT_TYPE",
                "location": s_loc,
                "message": f"Invalid layout_type '{layout}'. Must be one of {sorted(VALID_LAYOUT_TYPES)}.",
            })

        # Title
        title = str(sl.get("title") or "").strip()
        if not title:
            _add({"severity": "ERROR", "code": "MISSING_TITLE", "location": s_loc, "message": "Slide title is empty."})
        else:
            for iss in lint_text_item(title, f"{s_loc}.title", check_presenter_leak=True):
                _add(iss)

        # Bullets
        bullets = sl.get("bullets") or []
        if not isinstance(bullets, list):
            _add({"severity": "ERROR", "code": "INVALID_BULLETS", "location": f"{s_loc}.bullets", "message": "bullets must be a list."})
        else:
            if len(bullets) > 5:
                _add({
                    "severity": "ERROR",
                    "code": "TOO_MANY_BULLETS",
                    "location": f"{s_loc}.bullets",
                    "message": f"Slide {snum} has {len(bullets)} bullets (max 5 allowed).",
                })
            for b_i, b_txt in enumerate(bullets):
                for iss in lint_text_item(str(b_txt), f"{s_loc}.bullets[{b_i}]", check_presenter_leak=True, max_words=22):
                    _add(iss)

        # Table validation
        tbl = sl.get("table")
        if tbl is not None:
            if not isinstance(tbl, dict) or not tbl.get("headers") or not tbl.get("rows"):
                _add({"severity": "ERROR", "code": "INVALID_TABLE", "location": f"{s_loc}.table", "message": "Table must contain non-empty 'headers' and 'rows'."})
            else:
                n_cols = len(tbl["headers"])
                for h_i, h_val in enumerate(tbl["headers"]):
                    for iss in lint_text_item(str(h_val), f"{s_loc}.table.headers[{h_i}]", check_presenter_leak=True):
                        _add(iss)
                for r_i, row in enumerate(tbl["rows"]):
                    if not isinstance(row, list) or len(row) != n_cols:
                        _add({
                            "severity": "ERROR",
                            "code": "TABLE_COL_MISMATCH",
                            "location": f"{s_loc}.table.rows[{r_i}]",
                            "message": f"Row {r_i} has {len(row) if isinstance(row, list) else 'non-list'} cells; expected {n_cols}.",
                        })
                        continue
                    for c_i, cell in enumerate(row):
                        for iss in lint_text_item(str(cell), f"{s_loc}.table.rows[{r_i}][{c_i}]", check_presenter_leak=True):
                            _add(iss)

        # Chart spec validation
        cspec = sl.get("chart_spec")
        if cspec is not None:
            if not isinstance(cspec, dict) or not cspec.get("series"):
                _add({"severity": "ERROR", "code": "INVALID_CHART_SPEC", "location": f"{s_loc}.chart_spec", "message": "chart_spec must be an object with non-empty 'series'."})
            else:
                smooth = bool(cspec.get("smooth_continuous", False))
                for s_i, ser in enumerate(cspec.get("series") or []):
                    if not isinstance(ser, dict) or "y" not in ser:
                        _add({"severity": "ERROR", "code": "INVALID_CHART_SERIES", "location": f"{s_loc}.chart_spec.series[{s_i}]", "message": "Chart series must contain numeric 'y' array."})
                        continue
                    if smooth:
                        for iss in check_curve_continuity(
                            ser.get("y") or [],
                            series_name=str(ser.get("name") or f"series_{s_i}"),
                            location=f"{s_loc}.chart_spec.series[{s_i}]",
                        ):
                            _add(iss)

        # Cards validation (multi-vignette / multi-case slides)
        cards = sl.get("cards")
        if cards is not None:
            if not isinstance(cards, list) or not cards:
                _add({"severity": "ERROR", "code": "INVALID_CARDS", "location": f"{s_loc}.cards", "message": "cards must be a non-empty list when present."})
            else:
                for c_i, card in enumerate(cards):
                    if not isinstance(card, dict) or not card.get("title"):
                        _add({"severity": "ERROR", "code": "INVALID_CARD", "location": f"{s_loc}.cards[{c_i}]", "message": "Each card must have a 'title'."})
                        continue
                    for iss in lint_text_item(str(card.get("title")), f"{s_loc}.cards[{c_i}].title", check_presenter_leak=True):
                        _add(iss)
                    for item_i, item_txt in enumerate(card.get("items") or []):
                        for iss in lint_text_item(str(item_txt), f"{s_loc}.cards[{c_i}].items[{item_i}]", check_presenter_leak=True):
                            _add(iss)
                    if card.get("body"):
                        for iss in lint_text_item(str(card.get("body")), f"{s_loc}.cards[{c_i}].body", check_presenter_leak=True):
                            _add(iss)

    # 4. Validate handout sections
    for h_idx, sec in enumerate(handout_sections):
        h_loc = f"handout_sections[{h_idx}]"
        if not isinstance(sec, dict) or not sec.get("title"):
            _add({"severity": "ERROR", "code": "INVALID_HANDOUT_SECTION", "location": h_loc, "message": "Each handout section must have a 'title'."})
            continue
        for r_i, rule in enumerate(sec.get("rules") or []):
            for iss in lint_text_item(str(rule), f"{h_loc}.rules[{r_i}]", check_presenter_leak=True):
                _add(iss)
        for t_i, tbl in enumerate(sec.get("tables") or []):
            for r_i, row in enumerate((tbl or {}).get("rows") or []):
                for c_i, cell in enumerate(row or []):
                    for iss in lint_text_item(str(cell), f"{h_loc}.tables[{t_i}].rows[{r_i}][{c_i}]", check_presenter_leak=True):
                        _add(iss)

    passed = len(errors) == 0
    summary = (
        f"PASS: lecture_spec valid ({len(slides)} slides, {len(modules)} modules, {len(canonical_facts)} canonical facts, {len(warnings)} warnings)."
        if passed
        else f"FAIL: lecture_spec has {len(errors)} error(s) and {len(warnings)} warning(s)."
    )
    return {"passed": passed, "errors": errors, "warnings": warnings, "summary": summary}


def _extract_pptx_slide_contents(pptx_path: str) -> List[Dict[str, Any]]:
    """Extracts structured text, tables, pictures, and speaker notes from every slide of a .pptx file."""
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(pptx_path)
    out: List[Dict[str, Any]] = []
    for idx, slide in enumerate(prs.slides, start=1):
        texts: List[str] = []
        tables: List[List[List[str]]] = []
        picture_count = 0

        for shape in slide.shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                picture_count += 1
            if shape.has_text_frame:
                for p in shape.text_frame.paragraphs:
                    t = (p.text or "").strip()
                    if t:
                        texts.append(t)
            if shape.has_table:
                tbl_rows: List[List[str]] = []
                for row in shape.table.rows:
                    row_vals = [(c.text or "").strip() for c in row.cells]
                    tbl_rows.append(row_vals)
                    for rv in row_vals:
                        if rv:
                            texts.append(rv)
                tables.append(tbl_rows)

        notes_text = ""
        try:
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                notes_text = (slide.notes_slide.notes_text_frame.text or "").strip()
        except Exception:
            notes_text = ""

        out.append({
            "slide_index": idx,
            "texts": texts,
            "full_text": "\n".join(texts),
            "tables": tables,
            "picture_count": picture_count,
            "notes": notes_text,
        })
    return out


def _extract_docx_contents(docx_path: str) -> Dict[str, Any]:
    """Extracts all paragraphs and table cell texts from a .docx file."""
    import docx

    doc = docx.Document(docx_path)
    items: List[Tuple[str, str]] = []
    for p_i, p in enumerate(doc.paragraphs):
        t = (p.text or "").strip()
        if t:
            items.append((f"docx.paragraph[{p_i}]", t))
    for t_i, tbl in enumerate(doc.tables):
        for r_i, row in enumerate(tbl.rows):
            for c_i, cell in enumerate(row.cells):
                t = (cell.text or "").strip()
                if t:
                    items.append((f"docx.table[{t_i}].row[{r_i}].col[{c_i}]", t))
    full_text = "\n".join(t for _, t in items)
    return {"items": items, "full_text": full_text, "table_count": len(doc.tables)}


def check_cross_artifact_consistency(
    spec: Dict[str, Any],
    *,
    dossier_text: str = "",
    pptx_text: str = "",
    docx_text: str = "",
) -> List[Dict[str, Any]]:
    """
    Verifies every value in `spec['canonical_facts']` (normalized for spacing/Unicode) is
    consistent across the dossier, `.pptx`, and `.docx` handout, and checks optional
    `spec['forbidden_legacy_values']` contradictions.
    """
    issues: List[Dict[str, Any]] = []
    canonical_facts = spec.get("canonical_facts") if isinstance(spec.get("canonical_facts"), dict) else {}
    if not canonical_facts:
        return issues

    norm_pptx = normalize_for_comparison(pptx_text)
    norm_docx = normalize_for_comparison(docx_text)
    norm_dossier = normalize_for_comparison(dossier_text)

    # Collect which canonical fact values are explicitly referenced in slides[] and handout_sections[]
    slides_blob = normalize_for_comparison(json.dumps(spec.get("slides") or [], ensure_ascii=False))
    handout_blob = normalize_for_comparison(json.dumps(spec.get("handout_sections") or [], ensure_ascii=False))

    for fact_key, fact_val in canonical_facts.items():
        if not fact_val:
            continue
        norm_val = normalize_for_comparison(str(fact_val))
        if not norm_val:
            continue

        # Extract core numeric/comparison core (e.g. ">= 200", "< 3.5", "< 250", "> 300", "< 7.0%")
        num_core_m = re.search(r"(?:>=|<=|>|<)?\s*\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?\s*%?", norm_val)
        core_token = normalize_for_comparison(num_core_m.group(0)) if num_core_m else norm_val

        def _present(haystack: str) -> bool:
            if not haystack:
                return True
            if norm_val in haystack:
                return True
            if core_token and core_token in haystack:
                return True
            return False

        if pptx_text and (norm_val in slides_blob or core_token in slides_blob):
            if not _present(norm_pptx):
                issues.append({
                    "severity": "ERROR",
                    "code": "CANONICAL_FACT_MISSING_PPTX",
                    "location": f"canonical_facts.{fact_key}",
                    "message": f"Canonical fact '{fact_key}': '{fact_val}' is declared for slides but missing/drifted in compiled .pptx.",
                })

        if docx_text and (norm_val in handout_blob or core_token in handout_blob or not handout_blob):
            if not _present(norm_docx):
                issues.append({
                    "severity": "ERROR",
                    "code": "CANONICAL_FACT_MISSING_DOCX",
                    "location": f"canonical_facts.{fact_key}",
                    "message": f"Canonical fact '{fact_key}': '{fact_val}' is missing/drifted in compiled .docx handout.",
                })

        if dossier_text and not _present(norm_dossier):
            issues.append({
                "severity": "WARNING",
                "code": "CANONICAL_FACT_MISSING_DOSSIER",
                "location": f"canonical_facts.{fact_key}",
                "message": f"Canonical fact '{fact_key}': '{fact_val}' was not found verbatim in dossier text.",
            })

    # Optional explicit legacy-contradiction list in spec (e.g., {"legacy_dka_2009": "> 250 mg/dL"})
    forbidden_legacy = spec.get("forbidden_legacy_values")
    if isinstance(forbidden_legacy, dict):
        for label, bad_val in forbidden_legacy.items():
            norm_bad = normalize_for_comparison(str(bad_val))
            if not norm_bad:
                continue
            for art_name, art_norm in (("pptx", norm_pptx), ("docx", norm_docx)):
                if art_norm and norm_bad in art_norm:
                    issues.append({
                        "severity": "ERROR",
                        "code": "LEGACY_FACT_CONTRADICTION",
                        "location": f"{art_name}:{label}",
                        "message": f"Forbidden outdated/legacy value '{bad_val}' ({label}) appeared in compiled .{art_name}.",
                    })

    return issues


def validate_compiled_artifacts(
    spec_or_path: Any,
    *,
    pptx_path: Optional[str] = None,
    docx_path: Optional[str] = None,
    chart_pngs: Optional[Sequence[str]] = None,
    dossier_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Runs the complete zero-token deterministic validation suite across:
      1. `lecture_spec.json` schema & index integrity
      2. `.pptx` XML text hygiene, dropped-content check, slide-bound tables/charts, Slide 1 roadmap, & speaker notes
      3. `.docx` XML text hygiene & dropped-content check
      4. Cross-artifact `canonical_facts` consistency
      5. Saved chart PNG quality check

    Any ERROR causes `passed=False` and blocks Google Workspace upload until resolved.
    """
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    def _absorb(res_issues: Sequence[Dict[str, Any]]):
        for iss in res_issues:
            if iss.get("severity") == "WARNING":
                warnings.append(iss)
            else:
                errors.append(iss)

    # Load lecture_spec
    if isinstance(spec_or_path, str):
        if not os.path.isfile(spec_or_path):
            return {
                "passed": False,
                "errors": [{"severity": "ERROR", "code": "SPEC_NOT_FOUND", "location": spec_or_path, "message": f"lecture_spec.json not found: {spec_or_path}"}],
                "warnings": [],
                "summary": f"FAIL: lecture_spec.json not found at {spec_or_path}",
            }
        try:
            with open(spec_or_path, "r", encoding="utf-8") as f:
                spec = json.load(f)
        except Exception as e:
            return {
                "passed": False,
                "errors": [{"severity": "ERROR", "code": "SPEC_PARSE_ERROR", "location": spec_or_path, "message": f"Invalid JSON in {spec_or_path}: {e}"}],
                "warnings": [],
                "summary": f"FAIL: Invalid JSON in {spec_or_path}",
            }
    elif isinstance(spec_or_path, dict):
        spec = spec_or_path
    else:
        return {
            "passed": False,
            "errors": [{"severity": "ERROR", "code": "SPEC_INVALID", "location": "spec", "message": "spec_or_path must be a path or dict."}],
            "warnings": [],
            "summary": "FAIL: Invalid lecture_spec input.",
        }

    # 1. Spec schema & curve continuity check
    spec_res = validate_lecture_spec(spec)
    _absorb(spec_res["errors"])
    _absorb(spec_res["warnings"])

    pptx_full_text = ""
    docx_full_text = ""
    dossier_text = ""

    if dossier_path and os.path.isfile(dossier_path):
        try:
            with open(dossier_path, "r", encoding="utf-8") as f:
                dossier_text = f.read()
        except Exception:
            dossier_text = ""

    # 2. Validate compiled .pptx
    if pptx_path:
        if not os.path.isfile(pptx_path):
            errors.append({"severity": "ERROR", "code": "PPTX_NOT_FOUND", "location": pptx_path, "message": f".pptx file not found: {pptx_path}"})
        else:
            try:
                pptx_slides = _extract_pptx_slide_contents(pptx_path)
            except Exception as e:
                pptx_slides = []
                errors.append({"severity": "ERROR", "code": "PPTX_READ_ERROR", "location": pptx_path, "message": f"Failed reading .pptx: {e}"})

            if pptx_slides:
                pptx_full_text = "\n".join(s["full_text"] for s in pptx_slides)
                norm_pptx_all = normalize_for_comparison(pptx_full_text)
                spec_slides = spec.get("slides") or []

                # Allow >= len(spec_slides) in case a dense slide cleanly auto-split into a (Cont.) slide
                if len(pptx_slides) < len(spec_slides):
                    errors.append({
                        "severity": "ERROR",
                        "code": "PPTX_SLIDE_COUNT_SHORT",
                        "location": pptx_path,
                        "message": f"Compiled .pptx has {len(pptx_slides)} slides, fewer than lecture_spec ({len(spec_slides)}).",
                    })

                # Text hygiene on EVERY visible text item in .pptx
                for sl_data in pptx_slides:
                    s_idx = sl_data["slide_index"]
                    for t_i, txt in enumerate(sl_data["texts"]):
                        _absorb(lint_text_item(txt, f"pptx.slide_{s_idx}.text[{t_i}]", check_presenter_leak=True))

                # Slide 1 roadmap range verification
                if spec.get("modules") and pptx_slides:
                    s1_norm = normalize_for_comparison(pptx_slides[0]["full_text"])
                    for mod in spec.get("modules") or []:
                        ms = int(mod.get("slide_start", 0) or 0)
                        me = int(mod.get("slide_end", 0) or 0)
                        expected_range = f"{ms}-{me}" if ms != me else f"{ms}"
                        if ms > 0 and expected_range not in s1_norm and f"{ms} - {me}" not in s1_norm:
                            errors.append({
                                "severity": "ERROR",
                                "code": "ROADMAP_RANGE_MISMATCH",
                                "location": "pptx.slide_1",
                                "message": f"Slide 1 roadmap is missing module {mod.get('id')} ('{mod.get('name')}') slide range '{ms}–{me}'.",
                            })

                # Map each spec slide to its matching compiled slide(s)
                for sp_sl in spec_slides:
                    sn = int(sp_sl.get("slide_number") or 0)
                    if sn < 1 or sn > len(pptx_slides):
                        continue
                    # Primary compiled slide at exact 1-based index sn (plus any immediately following "(Cont.)" slide)
                    primary_slide = pptx_slides[sn - 1]
                    combined_norm = normalize_for_comparison(primary_slide["full_text"])
                    if sn < len(pptx_slides) and "(cont" in pptx_slides[sn]["full_text"].lower():
                        combined_norm += " " + normalize_for_comparison(pptx_slides[sn]["full_text"])

                    # Verify every bullet in full
                    for b_i, bullet in enumerate(sp_sl.get("bullets") or []):
                        nb = normalize_for_comparison(str(bullet))
                        if nb and nb not in combined_norm and nb not in norm_pptx_all:
                            errors.append({
                                "severity": "ERROR",
                                "code": "DROPPED_BULLET",
                                "location": f"pptx.slide_{sn}.bullets[{b_i}]",
                                "message": f"Spec bullet missing or altered in .pptx Slide {sn}: {str(bullet)[:120]}",
                            })

                    # Verify every card item in full
                    for c_i, card in enumerate(sp_sl.get("cards") or []):
                        ctitle = normalize_for_comparison(str(card.get("title") or ""))
                        if ctitle and ctitle not in combined_norm and ctitle not in norm_pptx_all:
                            errors.append({
                                "severity": "ERROR",
                                "code": "DROPPED_CARD",
                                "location": f"pptx.slide_{sn}.cards[{c_i}]",
                                "message": f"Spec card '{card.get('title')}' missing from .pptx Slide {sn} (or continuation slide).",
                            })
                        for it_i, it_txt in enumerate(card.get("items") or []):
                            nit = normalize_for_comparison(str(it_txt))
                            if nit and nit not in combined_norm and nit not in norm_pptx_all:
                                errors.append({
                                    "severity": "ERROR",
                                    "code": "DROPPED_CARD_ITEM",
                                    "location": f"pptx.slide_{sn}.cards[{c_i}].items[{it_i}]",
                                    "message": f"Spec card item missing from .pptx Slide {sn}: {str(it_txt)[:120]}",
                                })

                    # Verify table pinned to exact slide_number
                    if sp_sl.get("table"):
                        if not primary_slide["tables"]:
                            errors.append({
                                "severity": "ERROR",
                                "code": "TABLE_PLACEMENT_DRIFT",
                                "location": f"pptx.slide_{sn}",
                                "message": f"Expected table '{sp_sl['table'].get('title', '')}' on Slide {sn}, but Slide {sn} has no table.",
                            })
                        else:
                            # Confirm first header appears in Slide sn's table
                            first_hdr = normalize_for_comparison(str((sp_sl["table"].get("headers") or [""])[0]))
                            tbl_blob = normalize_for_comparison(json.dumps(primary_slide["tables"], ensure_ascii=False))
                            if first_hdr and first_hdr not in tbl_blob:
                                errors.append({
                                    "severity": "ERROR",
                                    "code": "TABLE_CONTENT_DRIFT",
                                    "location": f"pptx.slide_{sn}",
                                    "message": f"Table on Slide {sn} does not match expected header '{sp_sl['table']['headers'][0]}'.",
                                })

                    # Verify chart pinned to exact slide_number
                    if sp_sl.get("chart_spec"):
                        if primary_slide["picture_count"] < 1:
                            errors.append({
                                "severity": "ERROR",
                                "code": "CHART_PLACEMENT_DRIFT",
                                "location": f"pptx.slide_{sn}",
                                "message": f"Expected chart '{sp_sl['chart_spec'].get('title', '')}' on Slide {sn}, but Slide {sn} has no embedded chart image.",
                            })

                    # Verify speaker notes injected
                    if (sp_sl.get("speaker_notes") or "").strip() and not primary_slide["notes"]:
                        warnings.append({
                            "severity": "WARNING",
                            "code": "MISSING_SPEAKER_NOTES",
                            "location": f"pptx.slide_{sn}",
                            "message": f"Slide {sn} has speaker_notes in spec but PowerPoint Notes pane is empty.",
                        })

    # 3. Validate compiled .docx handout
    if docx_path:
        if not os.path.isfile(docx_path):
            errors.append({"severity": "ERROR", "code": "DOCX_NOT_FOUND", "location": docx_path, "message": f".docx file not found: {docx_path}"})
        else:
            try:
                docx_data = _extract_docx_contents(docx_path)
            except Exception as e:
                docx_data = {"items": [], "full_text": "", "table_count": 0}
                errors.append({"severity": "ERROR", "code": "DOCX_READ_ERROR", "location": docx_path, "message": f"Failed reading .docx: {e}"})

            docx_full_text = docx_data["full_text"]
            for loc, txt in docx_data["items"]:
                _absorb(lint_text_item(txt, loc, check_presenter_leak=True))

            # Confirm handout_sections rules/tables appear in .docx
            norm_docx_all = normalize_for_comparison(docx_full_text)
            for h_i, sec in enumerate(spec.get("handout_sections") or []):
                stitle = normalize_for_comparison(str(sec.get("title") or ""))
                if stitle and stitle not in norm_docx_all:
                    errors.append({
                        "severity": "ERROR",
                        "code": "DROPPED_HANDOUT_SECTION",
                        "location": f"docx.handout_sections[{h_i}]",
                        "message": f"Handout section '{sec.get('title')}' is missing from compiled .docx.",
                    })

    # 4. Cross-artifact factual consistency check
    _absorb(
        check_cross_artifact_consistency(
            spec,
            dossier_text=dossier_text,
            pptx_text=pptx_full_text,
            docx_text=docx_full_text,
        )
    )

    # 5. Saved chart PNG inspection
    for png in (chart_pngs or []):
        _absorb(check_chart_png(png))

    passed = len(errors) == 0
    summary = (
        f"PASS: All deterministic validations succeeded ({len(warnings)} warning(s))."
        if passed
        else f"FAIL: Deterministic validation blocked upload with {len(errors)} error(s) and {len(warnings)} warning(s)."
    )
    return {
        "passed": passed,
        "status": "pass" if passed else "fail",
        "errors": errors,
        "warnings": warnings,
        "summary": summary,
    }
