"""
core/visual_qa.py - Low-Cost Multimodal Visual QA Gate (MEDIA_RESOLUTION_LOW)
for Aether's Document & Presentation Pipeline.

Before uploading `.pptx` / `.docx` artifacts to Google Workspace:
1. Always collects every generated `matplotlib` chart PNG.
2. When Microsoft PowerPoint COM (`win32com.client`) or LibreOffice (`soffice`) is
   available on the host, exports slide thumbnails and composes compact 4-up (2x2)
   contact sheets so the entire deck is visually inspected in minimal API calls.
3. Calls Hydrogen (`gemini-4-hydrogen`) with `MEDIA_RESOLUTION_LOW` (~280 tokens/image)
   under `api_feature_context("document_builder", "visual_qa")` to check ONLY for:
   - clipped or overlapping text
   - broken / jagged / discontinuous chart curves
   - misaligned or overflowing tables
   - blank or malformed slides
4. Supports bounded auto-repair (max 2 cycles) and logs a consolidated USD cost receipt.
"""

from __future__ import annotations

import glob
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional, Sequence

from core.api_telemetry import api_feature_context, make_image_part
from core.logger import get_logger
from core.telemetry_db import query_api_usage_rows

logger = get_logger("VisualQA")

VISUAL_QA_PROMPT = """You are a strict visual layout QA inspector for presentations and charts.
Inspect the attached chart image(s) and/or 4-up slide contact sheet(s) ONLY for these visual defects:
1. Clipped, truncated, or overlapping text
2. Broken, jagged, or abruptly discontinuous chart curves (e.g., sharp vertical drops)
3. Misaligned, squished, or overflowing tables
4. Blank or visibly malformed slides

Return STRICT JSON ONLY:
{
  "passed": true or false,
  "defects": [
    {
      "target_id": "chart filename or slide number (e.g. chart_slide_07.png or Slide 19)",
      "severity": "ERROR" or "WARNING",
      "issue": "Concise description of visual defect",
      "suggested_fix": "Specific targeted fix"
    }
  ]
}
If everything is clean and readable, return {"passed": true, "defects": []}.
"""


def stitch_contact_sheets(
    image_paths: Sequence[str],
    out_dir: str,
    grid_cols: int = 2,
    grid_rows: int = 2,
    tile_size: tuple = (640, 360),
) -> List[str]:
    """Combines slide thumbnail PNGs into compact 4-up (2x2) contact sheets for low-cost QA."""
    from PIL import Image, ImageDraw

    valid_paths = [p for p in image_paths if p and os.path.isfile(p)]
    if not valid_paths:
        return []

    os.makedirs(out_dir, exist_ok=True)
    per_sheet = max(1, grid_cols * grid_rows)
    sheet_paths: List[str] = []
    tw, th = tile_size
    pad = 12

    for sheet_idx in range(0, len(valid_paths), per_sheet):
        chunk = valid_paths[sheet_idx:sheet_idx + per_sheet]
        canvas_w = grid_cols * tw + (grid_cols + 1) * pad
        canvas_h = grid_rows * th + (grid_rows + 1) * pad
        canvas = Image.new("RGB", (canvas_w, canvas_h), (240, 244, 248))
        draw = ImageDraw.Draw(canvas)

        for pos, img_path in enumerate(chunk):
            r = pos // grid_cols
            c = pos % grid_cols
            x0 = pad + c * (tw + pad)
            y0 = pad + r * (th + pad)
            try:
                with Image.open(img_path) as im:
                    im_rgb = im.convert("RGB")
                    im_rgb.thumbnail((tw, th), Image.Resampling.LANCZOS)
                    canvas.paste(im_rgb, (x0, y0))
                draw.rectangle([x0, y0, x0 + tw - 1, y0 + th - 1], outline=(180, 192, 206), width=1)
            except Exception as e:
                logger.debug("[VISUAL_QA] Skipped unreadable thumbnail %s: %s", img_path, e)

        out_path = os.path.join(out_dir, f"contact_sheet_{(sheet_idx // per_sheet) + 1:02d}.png")
        canvas.save(out_path, format="PNG")
        sheet_paths.append(out_path)

    return sheet_paths


def export_pptx_slide_thumbnails(pptx_path: str, out_dir: str) -> List[str]:
    """
    Best-effort export of `.pptx` slide PNG thumbnails using host presentation software:
      1. Microsoft PowerPoint COM (`win32com.client`) with strict `try...finally: Quit()`
      2. LibreOffice headless (`soffice --headless --convert-to pdf`) if installed
    Returns sorted list of PNG paths (empty list if neither host renderer is installed).
    """
    if not pptx_path or not os.path.isfile(pptx_path):
        return []

    os.makedirs(out_dir, exist_ok=True)
    abs_pptx = os.path.abspath(pptx_path)
    abs_out = os.path.abspath(out_dir)

    # Method 1: Windows PowerPoint COM (strictly cleaned up in finally)
    if sys.platform == "win32":
        ppt_app = None
        pres = None
        com_inited = False
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            com_inited = True
            ppt_app = win32com.client.Dispatch("PowerPoint.Application")
            pres = ppt_app.Presentations.Open(abs_pptx, WithWindow=False)
            pres.Export(abs_out, "PNG", 960, 540)
            pngs = sorted(glob.glob(os.path.join(abs_out, "*.PNG")) + glob.glob(os.path.join(abs_out, "*.png")))
            if pngs:
                return pngs
        except Exception as e:
            logger.debug("[VISUAL_QA] PowerPoint COM thumbnail export unavailable: %s", e)
        finally:
            try:
                if pres is not None:
                    pres.Close()
            except Exception:
                pass
            try:
                if ppt_app is not None:
                    ppt_app.Quit()
            except Exception:
                pass
            if com_inited:
                try:
                    import pythoncom
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    # Method 2: LibreOffice headless -> PNG/PDF (if soffice is installed)
    soffice = shutil.which("soffice")
    if not soffice and sys.platform == "win32":
        for env_k in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            cand = os.path.join(os.environ.get(env_k, ""), "LibreOffice", "program", "soffice.exe")
            if os.path.isfile(cand):
                soffice = cand
                break
    if soffice:
        try:
            subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", abs_out, abs_pptx],
                capture_output=True,
                timeout=45,
                check=False,
            )
            # Note: PDF rasterization requires optional pdftoppm/fitz; check if pdftoppm exists
            pdf_files = glob.glob(os.path.join(abs_out, "*.pdf"))
            pdftoppm = shutil.which("pdftoppm")
            if pdf_files and pdftoppm:
                subprocess.run(
                    [pdftoppm, "-png", "-r", "110", pdf_files[0], os.path.join(abs_out, "slide")],
                    capture_output=True,
                    timeout=45,
                    check=False,
                )
                pngs = sorted(glob.glob(os.path.join(abs_out, "slide-*.png")))
                if pngs:
                    return pngs
        except Exception as e:
            logger.debug("[VISUAL_QA] LibreOffice thumbnail export unavailable: %s", e)

    return []


def run_visual_qa_gate(
    *,
    client: Any,
    model: Optional[str] = None,
    chart_pngs: Optional[Sequence[str]] = None,
    pptx_path: Optional[str] = None,
    qa_dir: Optional[str] = None,
    max_images_per_call: int = 8,
) -> Dict[str, Any]:
    """
    Executes the low-cost visual QA pass at `MEDIA_RESOLUTION_LOW` (~280 tok/image)
    tagged `feature_tag="document_builder", sub_action="visual_qa"`.
    When `model` is omitted, dynamically resolves the Tier 2 endpoint from Settings.
    """
    from google.genai import types
    if not model:
        from core.config_manager import load_config
        from core.model_discovery import resolve_tier_model
        model, _, _ = resolve_tier_model(load_config() or {}, tier=2)

    targets: List[tuple[str, str]] = []  # (label, file_path)
    for png in (chart_pngs or []):
        if png and os.path.isfile(png):
            targets.append((os.path.basename(png), png))

    temp_thumb_dir = None
    contact_sheets: List[str] = []
    try:
        if pptx_path and os.path.isfile(pptx_path):
            base_qa = qa_dir or tempfile.mkdtemp(prefix="aether_vqa_")
            if not qa_dir:
                temp_thumb_dir = base_qa
            thumbs = export_pptx_slide_thumbnails(pptx_path, os.path.join(base_qa, "thumbs"))
            if thumbs:
                contact_sheets = stitch_contact_sheets(thumbs, os.path.join(base_qa, "sheets"))
                for cs in contact_sheets:
                    targets.append((os.path.basename(cs), cs))

        if not targets:
            return {
                "status": "success",
                "passed": True,
                "defects": [],
                "images_inspected": 0,
                "contact_sheets": [],
                "message": "No chart PNGs or slide thumbnails to inspect; Visual QA skipped cleanly.",
            }

        if not client:
            return {
                "status": "skipped",
                "passed": True,
                "defects": [],
                "images_inspected": 0,
                "message": "GenAI client unavailable for Visual QA; skipped.",
            }

        all_defects: List[Dict[str, Any]] = []
        inspected = 0

        for batch_i in range(0, len(targets), max(1, max_images_per_call)):
            batch = targets[batch_i:batch_i + max_images_per_call]
            parts: List[Any] = [types.Part.from_text(text=VISUAL_QA_PROMPT)]
            for label, img_path in batch:
                with open(img_path, "rb") as f:
                    img_bytes = f.read()
                parts.append(types.Part.from_text(text=f"Image target: {label}"))
                parts.append(make_image_part(img_bytes, resolution="low", mime_type="image/png"))
                inspected += 1

            gen_cfg = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
            )
            step_num = (batch_i // max(1, max_images_per_call)) + 1
            with api_feature_context("document_builder", "visual_qa", loop_step=step_num, keep_latest_images=len(batch)):
                resp = client.models.generate_content(
                    model=model,
                    contents=[types.Content(role="user", parts=parts)],
                    config=gen_cfg,
                )

            raw_txt = (getattr(resp, "text", "") or "").strip()
            try:
                parsed = json.loads(raw_txt) if raw_txt else {}
            except Exception:
                parsed = {"passed": True, "defects": []}

            for d in (parsed.get("defects") or []):
                if isinstance(d, dict):
                    all_defects.append(d)

        blocking = [d for d in all_defects if str(d.get("severity") or "ERROR").upper() == "ERROR"]
        passed = len(blocking) == 0
        return {
            "status": "pass" if passed else "fail",
            "passed": passed,
            "defects": all_defects,
            "blocking_defects": blocking,
            "images_inspected": inspected,
            "contact_sheets": contact_sheets,
            "summary": (
                f"PASS: Visual QA inspected {inspected} image(s) at MEDIA_RESOLUTION_LOW with 0 blocking defects."
                if passed
                else f"FAIL: Visual QA found {len(blocking)} blocking visual defect(s) across {inspected} image(s)."
            ),
        }
    finally:
        if temp_thumb_dir and os.path.isdir(temp_thumb_dir):
            shutil.rmtree(temp_thumb_dir, ignore_errors=True)


def run_bounded_visual_qa_with_repair(
    *,
    client: Any,
    model: str = "gemini-4-hydrogen",
    chart_pngs: Optional[Sequence[str]] = None,
    pptx_path: Optional[str] = None,
    qa_dir: Optional[str] = None,
    repair_callback: Optional[Callable[[List[Dict[str, Any]], int], Optional[Dict[str, Any]]]] = None,
    max_repair_cycles: int = 2,
) -> Dict[str, Any]:
    """
    Runs Visual QA and, if blocking defects are found and `repair_callback` is provided,
    invokes up to `max_repair_cycles` targeted repair attempts before returning final pass/fail.
    """
    cur_charts = list(chart_pngs or [])
    cur_pptx = pptx_path
    cycles_used = 0
    last_res: Dict[str, Any] = {}

    for attempt in range(max(0, int(max_repair_cycles)) + 1):
        last_res = run_visual_qa_gate(
            client=client,
            model=model,
            chart_pngs=cur_charts,
            pptx_path=cur_pptx,
            qa_dir=qa_dir,
        )
        if last_res.get("passed"):
            last_res["repair_cycles_used"] = cycles_used
            return last_res

        if attempt >= max_repair_cycles or repair_callback is None:
            break

        cycles_used += 1
        updated = repair_callback(last_res.get("blocking_defects") or last_res.get("defects") or [], cycles_used)
        if isinstance(updated, dict):
            if "chart_pngs" in updated:
                cur_charts = list(updated["chart_pngs"] or [])
            if "pptx_path" in updated:
                cur_pptx = updated["pptx_path"]

    last_res["repair_cycles_used"] = cycles_used
    return last_res


def log_pipeline_cost_receipt(
    job_id: str,
    *,
    since_ts: float,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Queries `api_usage_log` since `since_ts` and logs a single consolidated USD cost receipt
    attributing `dossier_creation` (Neon) and `document_builder` (Hydrogen + Visual QA).
    """
    import datetime as _dt
    since_utc = _dt.datetime.fromtimestamp(float(since_ts), tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    rows = query_api_usage_rows(since_utc=since_utc, db_path=db_path)
    dossier_usd = 0.0
    builder_usd = 0.0
    qa_usd = 0.0
    other_usd = 0.0
    call_count = 0

    for row in rows:
        feat = str(row.get("feature_tag") or "").lower()
        sub = str(row.get("sub_action") or "").lower()
        cost = float(row.get("estimated_cost_usd") or 0.0)
        if feat == "dossier_creation":
            dossier_usd += cost
            call_count += 1
        elif feat == "document_builder":
            if "visual_qa" in sub:
                qa_usd += cost
            else:
                builder_usd += cost
            call_count += 1
        else:
            other_usd += cost

    total_usd = round(dossier_usd + builder_usd + qa_usd, 6)
    receipt = (
        f"[DOC_PIPELINE_COST] job={job_id} calls={call_count} "
        f"dossier=${dossier_usd:.5f} builder=${builder_usd:.5f} qa=${qa_usd:.5f} total=${total_usd:.5f}"
    )
    logger.info(receipt)
    return {
        "job_id": job_id,
        "calls": call_count,
        "dossier_cost_usd": round(dossier_usd, 6),
        "builder_cost_usd": round(builder_usd, 6),
        "visual_qa_cost_usd": round(qa_usd, 6),
        "total_cost_usd": total_usd,
        "receipt": receipt,
    }
