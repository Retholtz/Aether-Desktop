"""
core/document_viewer.py - Unified Document Hub Backend & Native HTML Renderers

Provides safe, path-traversal-hardened listing, metadata extraction, deletion,
and rich zero-dependency HTML rendering for Aether's 4 document libraries:
  - Dossiers (`data/dossiers/*.md`)
  - DOCX     (`data/docx/*.docx`)
  - PPTX     (`data/pptx/*.pptx`)
  - XLSX     (`data/xlsx/*.xlsx`, `.csv`)

All user/document strings are strictly HTML-escaped before rendering so WebView2
previews and `window.print()` PDF exports remain XSS-safe and print-ready.
"""

from __future__ import annotations

import base64
import csv
import html
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from core.logger import get_logger
from core.paths import user_data_path

logger = get_logger("DocumentViewer")

# Process-wide state tracking whichever document is currently open in the Documents viewer panel
_ACTIVE_VIEWER_DOC: Optional[Dict[str, str]] = None


def find_matching_lecture_spec(filename: str = "", category: str = "pptx") -> Optional[str]:
    """
    Finds the exact `outputs/<job_id>/lecture_spec.json` corresponding to a given compiled `.pptx`/`.docx`
    filename (or the most recently modified live `lecture_spec.json` whose compiled artifact still exists in
    `data/pptx/` or `data/docx/`). Prevents accidentally reviving deleted older presentation builds.
    """
    outputs_root = os.path.abspath(user_data_path("outputs"))
    if not os.path.isdir(outputs_root):
        return None

    target_base = os.path.basename(str(filename or "").strip()).lower()
    live_pptx = set()
    live_docx = set()
    pptx_dir = os.path.abspath(user_data_path("data", "pptx"))
    docx_dir = os.path.abspath(user_data_path("data", "docx"))
    if os.path.isdir(pptx_dir):
        live_pptx = {f.lower() for f in os.listdir(pptx_dir) if f.lower().endswith(".pptx")}
    if os.path.isdir(docx_dir):
        live_docx = {f.lower() for f in os.listdir(docx_dir) if f.lower().endswith(".docx")}

    exact_match: Optional[str] = None
    live_candidates: List[Tuple[float, str]] = []
    all_candidates: List[Tuple[float, str]] = []

    for job_name in os.listdir(outputs_root):
        job_dir = os.path.join(outputs_root, job_name)
        if not os.path.isdir(job_dir):
            continue
        spec_fp = os.path.join(job_dir, "lecture_spec.json")
        if not os.path.isfile(spec_fp):
            continue
        spec_mtime = os.path.getmtime(spec_fp)
        all_candidates.append((spec_mtime, spec_fp))

        job_files = {f.lower() for f in os.listdir(job_dir) if os.path.isfile(os.path.join(job_dir, f))}
        if target_base and target_base in job_files:
            exact_match = spec_fp
            break
        if (job_files & live_pptx) or (job_files & live_docx):
            live_candidates.append((spec_mtime, spec_fp))

    if exact_match:
        return exact_match
    if live_candidates:
        live_candidates.sort(key=lambda x: x[0], reverse=True)
        return live_candidates[0][1]
    if all_candidates and not target_base:
        all_candidates.sort(key=lambda x: x[0], reverse=True)
        return all_candidates[0][1]
    return None


def set_active_viewer_document(
    category: str,
    filename: str,
    file_path: str,
    title: str = "",
) -> Dict[str, str]:
    """Records the document currently open in Aether's Documents viewer panel so Aether knows what 'this file' refers to."""
    global _ACTIVE_VIEWER_DOC
    cat = normalize_category(category)
    linked_spec = find_matching_lecture_spec(filename, cat) if cat in ("pptx", "docx") else None
    _ACTIVE_VIEWER_DOC = {
        "category": cat,
        "filename": os.path.basename(filename),
        "file_path": os.path.abspath(file_path),
        "title": title or _clean_title_from_filename(filename),
        "lecture_spec_path": linked_spec or "",
    }
    return dict(_ACTIVE_VIEWER_DOC)


def clear_active_viewer_document(filename: Optional[str] = None) -> None:
    """Clears the active viewer document (or only if it matches `filename`)."""
    global _ACTIVE_VIEWER_DOC
    if _ACTIVE_VIEWER_DOC is None:
        return
    if filename is None or _ACTIVE_VIEWER_DOC.get("filename") == os.path.basename(filename):
        _ACTIVE_VIEWER_DOC = None


def get_active_viewer_document() -> Optional[Dict[str, str]]:
    """Returns the currently open viewer document if it still exists on disk."""
    global _ACTIVE_VIEWER_DOC
    if not _ACTIVE_VIEWER_DOC:
        return None
    fp = _ACTIVE_VIEWER_DOC.get("file_path") or ""
    if not fp or not os.path.isfile(fp):
        _ACTIVE_VIEWER_DOC = None
        return None
    return dict(_ACTIVE_VIEWER_DOC)


def format_active_viewer_context() -> str:
    """Formats a concise 1-line grounding block for Tier 1 / Tier 2 when a document is open in the viewer."""
    doc = get_active_viewer_document()
    if not doc:
        return ""
    spec_part = f" | linked_lecture_spec: {doc['lecture_spec_path']}" if doc.get("lecture_spec_path") else ""
    return (
        f"[ACTIVE DOCUMENT OPEN IN VIEWER: category={doc['category']} | filename='{doc['filename']}' | "
        f"path='{doc['file_path']}'{spec_part} — If the user asks to edit, revise, fix a slide/section, or sync "
        f"'this file' / 'the presentation' / 'the handout' / 'the spreadsheet', target THIS exact document and its linked_lecture_spec!]"
    )

DOC_CATEGORIES: Dict[str, Dict[str, Any]] = {
    "dossiers": {
        "subdir": ("data", "dossiers"),
        "extensions": (".md",),
        "label": "Dossiers",
        "singular": "Dossier",
        "plural": "Dossiers",
    },
    "docx": {
        "subdir": ("data", "docx"),
        "extensions": (".docx",),
        "label": "DOCX",
        "singular": "Word Doc",
        "plural": "Word Docs",
    },
    "pptx": {
        "subdir": ("data", "pptx"),
        "extensions": (".pptx",),
        "label": "PPTX",
        "singular": "Presentation",
        "plural": "Presentations",
    },
    "xlsx": {
        "subdir": ("data", "xlsx"),
        "extensions": (".xlsx", ".xlsm", ".csv"),
        "label": "XLSX",
        "singular": "Spreadsheet",
        "plural": "Spreadsheets",
    },
}


def normalize_category(category: str) -> str:
    cat = str(category or "dossiers").strip().lower()
    if cat in ("doc", "docs", "word"):
        return "docx"
    if cat in ("ppt", "slides", "presentation", "presentations"):
        return "pptx"
    if cat in ("xls", "excel", "sheets", "spreadsheet", "spreadsheets", "csv"):
        return "xlsx"
    if cat in DOC_CATEGORIES:
        return cat
    return "dossiers"


def get_category_dir(category: str, ensure_exists: bool = True) -> str:
    cat = normalize_category(category)
    target = os.path.abspath(user_data_path(*DOC_CATEGORIES[cat]["subdir"]))
    if ensure_exists:
        os.makedirs(target, exist_ok=True)
    return target


def safe_document_path(category: str, filename: str) -> Optional[str]:
    """Resolves a filename strictly inside `data/<category>/` with path-traversal protection."""
    cat = normalize_category(category)
    raw = str(filename or "").strip()
    if not raw or "/" in raw or "\\" in raw or ".." in raw:
        return None
    base_dir = get_category_dir(cat, ensure_exists=True)
    candidate = os.path.abspath(os.path.join(base_dir, os.path.basename(raw)))
    try:
        if os.path.commonpath([base_dir, candidate]) != base_dir:
            return None
    except ValueError:
        return None
    allowed_exts = DOC_CATEGORIES[cat]["extensions"]
    if not any(candidate.lower().endswith(ext) for ext in allowed_exts):
        return None
    return candidate


def _format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.2f} MB"


def _clean_title_from_filename(filename: str) -> str:
    stem = os.path.splitext(filename)[0]
    stem = re.sub(r"^\d{8}_\d{6}_", "", stem)
    return stem.replace("_", " ").strip() or filename


def _extract_datetime_from_file(filepath: str, filename: str) -> Tuple[str, str, float]:
    mtime = os.path.getmtime(filepath)
    m = re.match(r"^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})_", filename)
    if m:
        date_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        time_str = f"{m.group(4)}:{m.group(5)}:{m.group(6)}"
    else:
        lt = time.localtime(mtime)
        date_str = time.strftime("%Y-%m-%d", lt)
        time_str = time.strftime("%H:%M:%S", lt)
    return date_str, time_str, mtime


def _quick_office_summary(category: str, filepath: str) -> Tuple[str, str]:
    """Returns `(preview_text, badge_meta)` quickly for sidebar cards."""
    size_str = _format_size(os.path.getsize(filepath))
    try:
        if category == "docx":
            import docx
            d = docx.Document(filepath)
            first_line = ""
            for p in d.paragraphs:
                t = (p.text or "").strip()
                if t:
                    first_line = t[:120]
                    break
            meta = f"{len(d.paragraphs)} paras · {len(d.tables)} tables · {size_str}"
            return first_line or "Microsoft Word Document (.docx)", meta

        if category == "pptx":
            from pptx import Presentation
            prs = Presentation(filepath)
            first_title = ""
            if len(prs.slides) > 0:
                for sh in prs.slides[0].shapes:
                    if getattr(sh, "has_text_frame", False):
                        t = (sh.text_frame.text or "").strip().splitlines()
                        if t:
                            first_title = t[0][:120]
                            break
            meta = f"{len(prs.slides)} slides · {size_str}"
            return first_title or "PowerPoint Presentation (.pptx)", meta

        if category == "xlsx":
            if filepath.lower().endswith(".csv"):
                with open(filepath, "r", encoding="utf-8", errors="replace") as cf:
                    row_cnt = sum(1 for _ in cf)
                return "Comma-Separated Values (.csv)", f"{row_cnt} rows · {size_str}"
            import openpyxl
            wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
            sheets = list(wb.sheetnames)
            wb.close()
            preview = f"Sheets: {', '.join(sheets[:4])}" + ("…" if len(sheets) > 4 else "")
            meta = f"{len(sheets)} sheet{'s' if len(sheets) != 1 else ''} · {size_str}"
            return preview or "Excel Workbook (.xlsx)", meta
    except Exception as e:
        logger.debug(f"[DOC_VIEWER] Quick summary fallback for {filepath}: {e}")
    return f"{category.upper()} document", size_str


def list_documents(category: str = "dossiers", query: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lists all documents in the selected category folder, newest first."""
    cat = normalize_category(category)
    if cat == "dossiers":
        from tools.dossier_exporter import list_dossiers
        items = list_dossiers(query=query)
        for it in items:
            it["doc_type"] = "dossiers"
            it["meta_summary"] = f"{it.get('word_count', 0)} words · {str(it.get('category', 'general')).upper()}"
        return items

    folder = get_category_dir(cat, ensure_exists=True)
    allowed_exts = DOC_CATEGORIES[cat]["extensions"]
    q = str(query or "").strip().lower()
    results: List[Dict[str, Any]] = []

    for fname in os.listdir(folder):
        if not any(fname.lower().endswith(ext) for ext in allowed_exts):
            continue
        fpath = os.path.join(folder, fname)
        if not os.path.isfile(fpath):
            continue
        title = _clean_title_from_filename(fname)
        date_str, time_str, mtime = _extract_datetime_from_file(fpath, fname)
        preview, meta_summary = _quick_office_summary(cat, fpath)
        if q and not (q in title.lower() or q in fname.lower() or q in preview.lower()):
            continue
        results.append({
            "doc_type": cat,
            "filename": fname,
            "file_path": fpath,
            "title": title,
            "preview": preview,
            "date": date_str,
            "time": time_str,
            "mtime": mtime,
            "size_bytes": os.path.getsize(fpath),
            "meta_summary": meta_summary,
            "category": cat.upper(),
        })

    results.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    return results


def _render_docx_to_html(filepath: str) -> Tuple[str, str, str]:
    """Renders a `.docx` file to clean, print-ready HTML + plain text copy + meta summary."""
    import docx
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = docx.Document(filepath)
    parts: List[str] = ['<div class="doc-word-root">']
    plain_lines: List[str] = []
    word_count = 0
    in_list = False

    def _close_list():
        nonlocal in_list
        if in_list:
            parts.append("</ul>")
            in_list = False

    def _render_runs(paragraph) -> str:
        run_html: List[str] = []
        for run in paragraph.runs:
            txt = html.escape(run.text or "")
            if not txt:
                continue
            styles: List[str] = []
            if run.font and run.font.color and run.font.color.rgb:
                styles.append(f"color:#{run.font.color.rgb}")
            if run.bold:
                txt = f"<strong>{txt}</strong>"
            if run.italic:
                txt = f"<em>{txt}</em>"
            if run.underline:
                txt = f"<u>{txt}</u>"
            if styles:
                txt = f'<span style="{";".join(styles)}">{txt}</span>'
            run_html.append(txt)
        return "".join(run_html) if run_html else html.escape(paragraph.text or "")

    # Extract embedded images from paragraph XML if present
    def _extract_para_images(paragraph) -> List[str]:
        imgs: List[str] = []
        try:
            blips = paragraph._element.xpath(".//*[local-name()='blip']")
            for blip in blips:
                r_id = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
                if r_id and r_id in doc.part.related_parts:
                    img_part = doc.part.related_parts[r_id]
                    ctype = getattr(img_part, "content_type", "image/png") or "image/png"
                    if ctype.startswith("image/"):
                        b64 = base64.b64encode(img_part.blob).decode("ascii")
                        imgs.append(f'<div class="doc-img-wrap"><img src="data:{ctype};base64,{b64}" alt="Embedded image" /></div>')
        except Exception:
            pass
        return imgs

    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            p = Paragraph(child, doc)
            raw_txt = (p.text or "").strip()
            img_tags = _extract_para_images(p)
            if not raw_txt and not img_tags:
                continue
            word_count += len(raw_txt.split())
            if raw_txt:
                plain_lines.append(raw_txt)
            style_name = (p.style.name if p.style else "") or ""
            low_style = style_name.lower()
            inner = _render_runs(p)

            if "list" in low_style or low_style.startswith("bullet"):
                if not in_list:
                    parts.append("<ul>")
                    in_list = True
                parts.append(f"<li>{inner}</li>")
            else:
                _close_list()
                if "heading 1" in low_style or low_style == "title":
                    parts.append(f"<h1>{inner}</h1>")
                elif "heading 2" in low_style or low_style == "subtitle":
                    parts.append(f"<h2>{inner}</h2>")
                elif "heading 3" in low_style:
                    parts.append(f"<h3>{inner}</h3>")
                elif "heading" in low_style:
                    parts.append(f"<h4>{inner}</h4>")
                elif inner.strip():
                    parts.append(f"<p>{inner}</p>")
            for im in img_tags:
                _close_list()
                parts.append(im)

        elif isinstance(child, CT_Tbl):
            _close_list()
            tbl = Table(child, doc)
            if not tbl.rows:
                continue
            parts.append('<div class="dossier-table-wrap"><table class="doc-word-table">')
            for r_i, row in enumerate(tbl.rows):
                tag = "th" if r_i == 0 else "td"
                if r_i == 0:
                    parts.append("<thead><tr>")
                elif r_i == 1:
                    parts.append("<tbody><tr>")
                else:
                    parts.append("<tr>")
                row_vals = []
                for cell in row.cells:
                    c_txt = (cell.text or "").strip()
                    word_count += len(c_txt.split())
                    row_vals.append(c_txt)
                    cell_html = "<br>".join(html.escape(line) for line in c_txt.splitlines()) if c_txt else "&nbsp;"
                    parts.append(f"<{tag}>{cell_html}</{tag}>")
                plain_lines.append(" | ".join(row_vals))
                parts.append("</tr>")
                if r_i == 0:
                    parts.append("</thead>")
            if len(tbl.rows) > 1:
                parts.append("</tbody>")
            parts.append("</table></div>")

    _close_list()
    parts.append("</div>")
    meta = f"{word_count} words · {len(doc.tables)} tables · {_format_size(os.path.getsize(filepath))}"
    return "\n".join(parts), "\n".join(plain_lines), meta


def _render_pptx_to_html(filepath: str) -> Tuple[str, str, str]:
    """
    Renders a `.pptx` deck as true 16:9 proportional visual canvases matching PowerPoint / Google Slides:
    every rectangle, card header, textbox, native table, and embedded chart PNG is positioned at its
    exact `(left%, top%, width%, height%)` coordinates in slide z-order with real `.pptx` fills, borders,
    alignment, and proportional `cqw` typography, plus toggleable Speaker Notes underneath each slide.
    """
    from pptx import Presentation
    from pptx.enum.dml import MSO_FILL
    from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE, MSO_SHAPE_TYPE
    from pptx.enum.text import PP_ALIGN

    prs = Presentation(filepath)
    total_slides = len(prs.slides)
    slide_w = float(prs.slide_width or 12192000)   # default 13.333 in (960 pt)
    slide_h = float(prs.slide_height or 6858000)   # default 7.5 in
    slide_w_pt = max(360.0, slide_w / 12700.0)
    aspect_ratio = slide_w / max(1.0, slide_h)

    def _safe_rgb_hex(color_format) -> Optional[str]:
        try:
            if color_format is not None and getattr(color_format, "rgb", None) is not None:
                return f"#{color_format.rgb}"
        except Exception:
            pass
        return None

    def _shape_fill_hex(shape) -> Optional[str]:
        try:
            fill = shape.fill
            if fill is not None and fill.type == MSO_FILL.SOLID:
                return _safe_rgb_hex(fill.fore_color)
        except Exception:
            pass
        return None

    def _shape_border_css(shape) -> str:
        try:
            line = shape.line
            if line is not None and line.fill is not None and line.fill.type == MSO_FILL.SOLID:
                col = _safe_rgb_hex(line.color)
                if col:
                    w_pt = (line.width / 12700.0) if getattr(line, "width", None) else 1.0
                    px = max(1.0, min(3.0, w_pt))
                    return f"border:{px:.1f}px solid {col};"
        except Exception:
            pass
        return ""

    def _font_cqw(font_obj, fallback_pt: float = 12.0) -> float:
        pt = fallback_pt
        try:
            if font_obj is not None and font_obj.size is not None:
                pt = float(font_obj.size.pt)
        except Exception:
            pass
        return max(0.75, min(6.0, (pt / slide_w_pt) * 100.0))

    def _align_css(align_val) -> str:
        if align_val == PP_ALIGN.CENTER:
            return "center"
        if align_val == PP_ALIGN.RIGHT:
            return "right"
        if align_val == PP_ALIGN.JUSTIFY:
            return "justify"
        return "left"

    def _render_text_frame_html(tf, default_pt: float = 12.0, default_color: str = "#1e293b") -> Tuple[str, List[str]]:
        p_html: List[str] = []
        raw_lines: List[str] = []
        for p in tf.paragraphs:
            txt_plain = (p.text or "").strip()
            if not txt_plain:
                continue
            raw_lines.append(txt_plain)
            p_align = _align_css(p.alignment)
            p_cqw = _font_cqw(p.font, fallback_pt=default_pt)
            p_color = _safe_rgb_hex(p.font.color) if p.font else None
            p_bold = bool(p.font.bold) if p.font and p.font.bold is not None else False
            p_italic = bool(p.font.italic) if p.font and p.font.italic is not None else False
            sp_after_cqw = 0.35
            try:
                if p.space_after is not None:
                    sp_after_cqw = max(0.0, min(1.8, (float(p.space_after.pt) / slide_w_pt) * 100.0))
            except Exception:
                pass
            ln_sp = 1.18
            try:
                if isinstance(p.line_spacing, (int, float)) and 0.9 <= float(p.line_spacing) <= 2.2:
                    ln_sp = float(p.line_spacing)
            except Exception:
                pass

            runs_out: List[str] = []
            if p.runs:
                for run in p.runs:
                    r_txt = html.escape(run.text or "")
                    if not r_txt:
                        continue
                    r_styles: List[str] = []
                    r_col = _safe_rgb_hex(run.font.color) if run.font else None
                    if r_col:
                        r_styles.append(f"color:{r_col}")
                    if run.font and run.font.size is not None:
                        r_styles.append(f"font-size:{_font_cqw(run.font, default_pt):.3f}cqw")
                    if run.font and run.font.bold:
                        r_styles.append("font-weight:700")
                    if run.font and run.font.italic:
                        r_styles.append("font-style:italic")
                    if run.font and run.font.underline:
                        r_styles.append("text-decoration:underline")
                    if r_styles:
                        runs_out.append(f'<span style="{";".join(r_styles)}">{r_txt}</span>')
                    else:
                        runs_out.append(r_txt)
            inner = "".join(runs_out) if runs_out else html.escape(p.text or "")
            p_styles = [
                f"text-align:{p_align}",
                f"font-size:{p_cqw:.3f}cqw",
                f"line-height:{ln_sp:.2f}",
                f"margin:0 0 {sp_after_cqw:.3f}cqw 0",
                f"color:{p_color or default_color}",
            ]
            if p_bold:
                p_styles.append("font-weight:700")
            if p_italic:
                p_styles.append("font-style:italic")
            p_html.append(f'<div class="doc-pptx-p" style="{";".join(p_styles)}">{inner}</div>')
        return "".join(p_html), raw_lines

    parts: List[str] = [
        '<div class="doc-pptx-deck">',
        '<div class="doc-pptx-controls no-print">'
        f'<span class="doc-pptx-summary">📽️ {total_slides} Slides · True 16:9 Slide Canvas</span>'
        '<label class="doc-notes-toggle"><input type="checkbox" id="togglePptxNotesChk" checked /> Show Speaker Notes</label>'
        '</div>',
    ]
    plain_lines: List[str] = []
    img_count = 0
    tbl_count = 0

    for idx, slide in enumerate(prs.slides, start=1):
        slide_bg = "#ffffff"
        try:
            if slide.background and slide.background.fill and slide.background.fill.type == MSO_FILL.SOLID:
                bg_hex = _safe_rgb_hex(slide.background.fill.fore_color)
                if bg_hex:
                    slide_bg = bg_hex
        except Exception:
            pass

        shape_layers: List[str] = []
        slide_plain: List[str] = []

        for z_idx, sh in enumerate(slide.shapes, start=1):
            try:
                left_pct = (float(sh.left or 0) / slide_w) * 100.0
                top_pct = (float(sh.top or 0) / slide_h) * 100.0
                w_pct = (float(sh.width or 0) / slide_w) * 100.0
                h_pct = (float(sh.height or 0) / slide_h) * 100.0
            except Exception:
                continue
            if w_pct <= 0.05 or h_pct <= 0.05:
                continue

            pos_css = (
                f"left:{left_pct:.3f}%;top:{top_pct:.3f}%;"
                f"width:{w_pct:.3f}%;height:{h_pct:.3f}%;z-index:{z_idx};"
            )

            # 1. Embedded Picture / Chart PNG
            if sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
                try:
                    blob = sh.image.blob
                    ctype = sh.image.content_type or "image/png"
                    if ctype.startswith("image/"):
                        b64 = base64.b64encode(blob).decode("ascii")
                        shape_layers.append(
                            f'<div class="doc-pptx-layer doc-pptx-layer-img" style="{pos_css}">'
                            f'<img src="data:{ctype};base64,{b64}" alt="Slide {idx} visual" />'
                            f'</div>'
                        )
                        img_count += 1
                except Exception:
                    pass
                continue

            # 2. Native PowerPoint Table
            if getattr(sh, "has_table", False):
                tbl = sh.table
                tbl_count += 1
                t_parts = [f'<div class="doc-pptx-layer doc-pptx-layer-tbl" style="{pos_css}"><table class="doc-pptx-canvas-table">']
                for r_i, row in enumerate(tbl.rows):
                    t_parts.append("<tr>")
                    row_plain = []
                    for cell in row.cells:
                        c_bg = _shape_fill_hex(cell)
                        c_styles = []
                        if c_bg:
                            c_styles.append(f"background:{c_bg}")
                        default_col = "#ffffff" if (r_i == 0 and c_bg) else "#1e293b"
                        c_html, c_lines = _render_text_frame_html(cell.text_frame, default_pt=10.5, default_color=default_col)
                        row_plain.append(" ".join(c_lines))
                        tag = "th" if r_i == 0 else "td"
                        st_attr = f' style="{";".join(c_styles)}"' if c_styles else ""
                        t_parts.append(f"<{tag}{st_attr}>{c_html or '&nbsp;'}</{tag}>")
                    if any(row_plain):
                        slide_plain.append(" | ".join(row_plain))
                    t_parts.append("</tr>")
                t_parts.append("</table></div>")
                shape_layers.append("".join(t_parts))
                continue

            # 3. Rectangle / Card / TextBox / AutoShape
            bg_hex = _shape_fill_hex(sh)
            border_css = _shape_border_css(sh)
            radius_css = ""
            try:
                if sh.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE and sh.auto_shape_type == MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE:
                    radius_css = "border-radius:0.55cqw;"
            except Exception:
                pass

            inner_html = ""
            if getattr(sh, "has_text_frame", False):
                default_col = "#ffffff" if (bg_hex and bg_hex.lower() in ("#0b2545", "#134074", "#008080")) else "#1e293b"
                inner_html, lines_txt = _render_text_frame_html(sh.text_frame, default_pt=12.0, default_color=default_col)
                slide_plain.extend(lines_txt)

            if not bg_hex and not border_css and not inner_html:
                continue

            extra_css = (f"background:{bg_hex};" if bg_hex else "") + border_css + radius_css
            pad_css = "padding:0.25cqw 0.35cqw;" if inner_html else ""
            shape_layers.append(
                f'<div class="doc-pptx-layer doc-pptx-layer-box" style="{pos_css}{extra_css}{pad_css}">'
                f'{inner_html}'
                f'</div>'
            )

        slide_label = slide_plain[0] if slide_plain else f"Slide {idx}"
        plain_lines.append(f"--- Slide {idx}: {slide_label} ---")
        for ln in slide_plain[1:]:
            plain_lines.append(f"  {ln}")

        parts.append('<section class="doc-pptx-slide-frame">')
        parts.append(
            f'<div class="doc-pptx-frame-bar no-print">'
            f'<span>Slide {idx} of {total_slides}</span>'
            f'</div>'
        )
        parts.append(
            f'<div class="doc-pptx-canvas" style="aspect-ratio:{aspect_ratio:.4f};background:{slide_bg};">'
            + "".join(shape_layers)
            + "</div>"
        )

        # Speaker Notes panel underneath the 16:9 canvas
        notes_txt = ""
        try:
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                notes_txt = (slide.notes_slide.notes_text_frame.text or "").strip()
        except Exception:
            notes_txt = ""
        if notes_txt:
            plain_lines.append(f"  [Notes] {notes_txt}")
            parts.append(
                f'<div class="doc-pptx-notes">'
                f'<div class="doc-pptx-notes-label">Speaker Notes</div>'
                f'<div class="doc-pptx-notes-body">{html.escape(notes_txt).replace(chr(10), "<br>")}</div>'
                f'</div>'
            )
        parts.append("</section>")

    parts.append("</div>")
    meta = f"{total_slides} slides · {img_count} visuals · {tbl_count} tables · {_format_size(os.path.getsize(filepath))}"
    return "\n".join(parts), "\n".join(plain_lines), meta


def _render_xlsx_to_html(filepath: str, max_rows: int = 500, max_cols: int = 40) -> Tuple[str, str, str]:
    """Renders an `.xlsx` (or `.csv`) spreadsheet as an interactive multi-sheet grid with formula tooltips."""
    plain_lines: List[str] = []

    if filepath.lower().endswith(".csv"):
        with open(filepath, "r", encoding="utf-8", errors="replace", newline="") as cf:
            rows = list(csv.reader(cf))
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        parts = ['<div class="doc-xlsx-root">', '<div class="doc-xlsx-sheet active" data-sheet-idx="0">']
        parts.append('<div class="doc-xlsx-sheet-print-title">CSV Data</div>')
        parts.append('<div class="dossier-table-wrap"><table class="doc-xlsx-grid">')
        for r_i, row in enumerate(rows):
            tag = "th" if r_i == 0 else "td"
            if r_i == 0:
                parts.append("<thead><tr><th class=\"doc-xlsx-rowhdr\">#</th>")
            elif r_i == 1:
                parts.append("<tbody><tr><td class=\"doc-xlsx-rowhdr\">1</td>")
            else:
                parts.append(f"<tr><td class=\"doc-xlsx-rowhdr\">{r_i}</td>")
            for cell_v in row[:max_cols]:
                parts.append(f"<{tag}>{html.escape(str(cell_v))}</{tag}>")
            plain_lines.append("\t".join(row[:max_cols]))
            parts.append("</tr>")
            if r_i == 0:
                parts.append("</thead>")
        if len(rows) > 1:
            parts.append("</tbody>")
        parts.append("</table></div>")
        if truncated:
            parts.append(f'<div class="doc-xlsx-trunc-note">Showing first {max_rows} rows. Open in Excel for full file.</div>')
        parts.append("</div></div>")
        meta = f"CSV · {len(rows)} rows · {_format_size(os.path.getsize(filepath))}"
        return "\n".join(parts), "\n".join(plain_lines), meta

    import openpyxl
    from openpyxl.utils import get_column_letter

    wb_vals = openpyxl.load_workbook(filepath, data_only=True)
    try:
        wb_forms = openpyxl.load_workbook(filepath, data_only=False)
    except Exception:
        wb_forms = wb_vals

    sheet_names = list(wb_vals.sheetnames)
    parts: List[str] = ['<div class="doc-xlsx-root">']

    # Interactive sheet tabs bar
    parts.append('<div class="doc-xlsx-tabs no-print" role="tablist">')
    for s_i, sname in enumerate(sheet_names):
        act = " active" if s_i == 0 else ""
        parts.append(
            f'<button type="button" class="doc-xlsx-tab-btn{act}" data-xlsx-sheet="{s_i}">'
            f'{html.escape(sname)}</button>'
        )
    parts.append("</div>")

    total_cells_rows = 0
    for s_i, sname in enumerate(sheet_names):
        ws_v = wb_vals[sname]
        ws_f = wb_forms[sname] if sname in wb_forms.sheetnames else ws_v
        n_rows = min(ws_v.max_row or 0, max_rows)
        n_cols = min(ws_v.max_column or 0, max_cols)
        total_cells_rows += ws_v.max_row or 0
        plain_lines.append(f"=== Sheet: {sname} ===")

        # Build merged-cell span maps
        merge_top_left: Dict[Tuple[int, int], Tuple[int, int]] = {}
        merge_covered: set = set()
        for rng in ws_v.merged_cells.ranges:
            if rng.min_row <= n_rows and rng.min_col <= n_cols:
                rs = min(rng.max_row, n_rows) - rng.min_row + 1
                cs = min(rng.max_col, n_cols) - rng.min_col + 1
                merge_top_left[(rng.min_row, rng.min_col)] = (rs, cs)
                for rr in range(rng.min_row, min(rng.max_row, n_rows) + 1):
                    for cc in range(rng.min_col, min(rng.max_col, n_cols) + 1):
                        if (rr, cc) != (rng.min_row, rng.min_col):
                            merge_covered.add((rr, cc))

        act = " active" if s_i == 0 else ""
        parts.append(f'<div class="doc-xlsx-sheet{act}" data-sheet-idx="{s_i}">')
        parts.append(f'<div class="doc-xlsx-sheet-print-title">Sheet: {html.escape(sname)}</div>')

        if n_rows == 0 or n_cols == 0:
            parts.append('<div class="doc-xlsx-empty">Empty worksheet.</div></div>')
            continue

        parts.append('<div class="dossier-table-wrap"><table class="doc-xlsx-grid"><thead><tr><th class="doc-xlsx-rowhdr"></th>')
        for c in range(1, n_cols + 1):
            parts.append(f'<th class="doc-xlsx-colhdr">{get_column_letter(c)}</th>')
        parts.append("</tr></thead><tbody>")

        for r in range(1, n_rows + 1):
            parts.append(f'<tr><td class="doc-xlsx-rowhdr">{r}</td>')
            row_txt_vals: List[str] = []
            for c in range(1, n_cols + 1):
                if (r, c) in merge_covered:
                    continue
                cell_v = ws_v.cell(row=r, column=c)
                cell_f = ws_f.cell(row=r, column=c)
                val = cell_v.value
                raw_f = cell_f.value
                formula_tip = ""
                if isinstance(raw_f, str) and raw_f.startswith("="):
                    formula_tip = f' title="Formula: {html.escape(raw_f)}"'
                    if val is None:
                        val = raw_f

                disp = ""
                is_num = False
                if val is None:
                    disp = ""
                elif isinstance(val, bool):
                    disp = "TRUE" if val else "FALSE"
                elif isinstance(val, (int, float)):
                    is_num = True
                    disp = f"{val:,.2f}".rstrip("0").rstrip(".") if isinstance(val, float) else f"{val:,}"
                else:
                    disp = str(val)
                row_txt_vals.append(disp)

                span_attrs = ""
                if (r, c) in merge_top_left:
                    rs, cs = merge_top_left[(r, c)]
                    if rs > 1:
                        span_attrs += f' rowspan="{rs}"'
                    if cs > 1:
                        span_attrs += f' colspan="{cs}"'

                styles: List[str] = []
                if is_num:
                    styles.append("text-align:right")
                if cell_v.font and cell_v.font.bold:
                    styles.append("font-weight:700")
                if r == 1 and not styles:
                    styles.append("font-weight:600")
                try:
                    fill = cell_v.fill
                    if fill and fill.fill_type == "solid" and fill.start_color and fill.start_color.type == "rgb":
                        rgb_hex = str(fill.start_color.rgb or "")
                        if len(rgb_hex) == 8 and rgb_hex.upper() not in ("00000000", "FFFFFFFF"):
                            styles.append(f"background-color:#{rgb_hex[2:]}")
                except Exception:
                    pass

                style_attr = f' style="{";".join(styles)}"' if styles else ""
                parts.append(f'<td{span_attrs}{style_attr}{formula_tip}>{html.escape(disp) if disp else "&nbsp;"}</td>')
            if any(row_txt_vals):
                plain_lines.append("\t".join(row_txt_vals))
            parts.append("</tr>")

        parts.append("</tbody></table></div>")
        if (ws_v.max_row or 0) > max_rows or (ws_v.max_column or 0) > max_cols:
            parts.append(
                f'<div class="doc-xlsx-trunc-note">Showing first {n_rows} rows × {n_cols} columns '
                f'(full sheet: {ws_v.max_row} × {ws_v.max_column}). Click Open to view in Excel.</div>'
            )
        parts.append("</div>")

    wb_vals.close()
    if wb_forms is not wb_vals:
        try:
            wb_forms.close()
        except Exception:
            pass

    parts.append("</div>")
    meta = f"{len(sheet_names)} sheet{'s' if len(sheet_names) != 1 else ''} · {total_cells_rows} rows · {_format_size(os.path.getsize(filepath))}"
    return "\n".join(parts), "\n".join(plain_lines), meta


def get_document_details(category: str, filename: str) -> Dict[str, Any]:
    """Loads metadata, plain-text copy, and rich HTML preview for any supported document."""
    cat = normalize_category(category)
    if cat == "dossiers":
        from tools.dossier_exporter import get_dossier_details
        res = get_dossier_details(filename)
        if res.get("status") == "success":
            d = res["dossier"]
            d["doc_type"] = "dossiers"
            d["meta_summary"] = f"{d.get('date', '')} {d.get('time', '')} · {str(d.get('category', 'general')).upper()} · {d.get('word_count', 0)} words"
            return {"status": "success", "document": d}
        return {"status": "error", "message": res.get("message", "Dossier not found.")}

    fpath = safe_document_path(cat, filename)
    if not fpath or not os.path.isfile(fpath):
        return {"status": "error", "message": f"Document not found: {filename}"}

    title = _clean_title_from_filename(filename)
    date_str, time_str, mtime = _extract_datetime_from_file(fpath, filename)

    try:
        if cat == "docx":
            rendered_html, plain_text, meta_info = _render_docx_to_html(fpath)
        elif cat == "pptx":
            rendered_html, plain_text, meta_info = _render_pptx_to_html(fpath)
        elif cat == "xlsx":
            rendered_html, plain_text, meta_info = _render_xlsx_to_html(fpath)
        else:
            return {"status": "error", "message": f"Unsupported document category: {cat}"}
    except Exception as e:
        logger.error(f"[DOC_VIEWER] Failed rendering {cat}/{filename}: {e}")
        return {"status": "error", "message": f"Could not render {filename}: {e}"}

    return {
        "status": "success",
        "document": {
            "doc_type": cat,
            "filename": os.path.basename(fpath),
            "file_path": fpath,
            "title": title,
            "date": date_str,
            "time": time_str,
            "mtime": mtime,
            "category": cat.upper(),
            "meta_summary": f"{date_str} {time_str} · {cat.upper()} · {meta_info}",
            "rendered_html": rendered_html,
            "plain_text": plain_text,
            "content_markdown": plain_text,
        },
    }


def delete_document_file(category: str, filename: str) -> Dict[str, Any]:
    """Deletes a document strictly within `data/<category>/` and cleans up orphaned `outputs/<job_id>/` specs."""
    cat = normalize_category(category)
    clear_active_viewer_document(filename)
    if cat == "dossiers":
        from tools.dossier_exporter import delete_dossier_file
        return delete_dossier_file(filename)

    fpath = safe_document_path(cat, filename)
    if not fpath or not os.path.isfile(fpath):
        return {"status": "error", "message": f"Document not found: {filename}"}
    try:
        base_name = os.path.basename(fpath)
        os.remove(fpath)
        logger.info(f"[DOC_VIEWER] Deleted {cat} file: {fpath}")

        # Prevent stale lecture_spec revival: if this deleted .pptx/.docx belonged to an outputs/<job_id>/
        # folder that now has no remaining mirrored .pptx/.docx in data/, clean up that stale copy/folder.
        if cat in ("pptx", "docx"):
            try:
                import shutil
                outputs_root = os.path.abspath(user_data_path("outputs"))
                pptx_dir = os.path.abspath(user_data_path("data", "pptx"))
                docx_dir = os.path.abspath(user_data_path("data", "docx"))
                live_pptx = {f.lower() for f in os.listdir(pptx_dir)} if os.path.isdir(pptx_dir) else set()
                live_docx = {f.lower() for f in os.listdir(docx_dir)} if os.path.isdir(docx_dir) else set()
                if os.path.isdir(outputs_root):
                    for job_name in os.listdir(outputs_root):
                        job_dir = os.path.join(outputs_root, job_name)
                        if not os.path.isdir(job_dir):
                            continue
                        job_copy = os.path.join(job_dir, base_name)
                        if os.path.isfile(job_copy):
                            os.remove(job_copy)
                            remaining = {f.lower() for f in os.listdir(job_dir) if os.path.isfile(os.path.join(job_dir, f))}
                            if not (remaining & live_pptx) and not (remaining & live_docx):
                                shutil.rmtree(job_dir, ignore_errors=True)
                                logger.info(f"[DOC_VIEWER] Cleaned orphaned build folder: {job_dir}")
            except Exception as clean_err:
                logger.debug(f"[DOC_VIEWER] Orphan cleanup skipped: {clean_err}")

        return {"status": "success", "deleted": base_name, "category": cat}
    except Exception as e:
        logger.error(f"[DOC_VIEWER] Delete failed for {fpath}: {e}")
        return {"status": "error", "message": str(e)}


def rename_document_file(category: str, filename: str, new_title: str) -> Dict[str, Any]:
    """
    Safely renames a document in `data/<category>/` to match `new_title`:
    - Preserves any leading `YYYYMMDD_HHMMSS_` timestamp prefix on the filename for stable chronological sorting.
    - For Markdown dossiers (`.md`), also updates the top `# Heading` line so the rendered page and sidebar
      reflect the clean human-readable title immediately (e.g. removing underscores).
    - For `.pptx`/`.docx`, also renames any matching mirrored file in `outputs/<job_id>/` so `lecture_spec.json`
      linking remains intact.
    """
    cat = normalize_category(category)
    clean_title = re.sub(r"[\r\n\t]+", " ", str(new_title or "")).strip()
    if not clean_title:
        return {"status": "error", "message": "New title cannot be empty."}
    if len(clean_title) > 140:
        clean_title = clean_title[:140].strip()

    fpath = safe_document_path(cat, filename)
    if not fpath or not os.path.isfile(fpath):
        return {"status": "error", "message": f"Document not found: {filename}"}

    old_base = os.path.basename(fpath)
    ext = os.path.splitext(old_base)[1]
    ts_m = re.match(r"^(\d{8}_\d{6}_)", old_base)
    ts_prefix = ts_m.group(1) if ts_m else ""

    # Build a clean, safe Windows filename stem from new_title
    safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", clean_title)
    safe_stem = re.sub(r"\s+", "_", safe_stem).strip("._ ")
    if not safe_stem:
        return {"status": "error", "message": "New title contains no valid filename characters."}

    new_base = f"{ts_prefix}{safe_stem}{ext}"
    new_path = safe_document_path(cat, new_base)
    if not new_path:
        return {"status": "error", "message": "Invalid target filename."}

    if os.path.abspath(new_path).lower() != os.path.abspath(fpath).lower() and os.path.exists(new_path):
        return {"status": "error", "message": f"A file named '{new_base}' already exists."}

    try:
        # For Markdown dossiers, update the first `# Heading` line so the displayed title & page header match
        if cat == "dossiers":
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            lines = content.splitlines()
            updated_heading = False
            for i, ln in enumerate(lines[:10]):
                if re.match(r"^\s*#\s+.+", ln):
                    lines[i] = f"# {clean_title}"
                    updated_heading = True
                    break
            if updated_heading:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + ("\n" if content.endswith("\n") else ""))

        if os.path.abspath(new_path) != os.path.abspath(fpath):
            os.rename(fpath, new_path)

        # Keep outputs/<job_id>/ mirrored filename in sync for .pptx / .docx
        if cat in ("pptx", "docx") and new_base != old_base:
            try:
                outputs_root = os.path.abspath(user_data_path("outputs"))
                if os.path.isdir(outputs_root):
                    for job_name in os.listdir(outputs_root):
                        job_dir = os.path.join(outputs_root, job_name)
                        if not os.path.isdir(job_dir):
                            continue
                        old_job_copy = os.path.join(job_dir, old_base)
                        if os.path.isfile(old_job_copy):
                            new_job_copy = os.path.join(job_dir, new_base)
                            if not os.path.exists(new_job_copy):
                                os.rename(old_job_copy, new_job_copy)
            except Exception as sync_err:
                logger.debug(f"[DOC_VIEWER] Output mirror rename skipped: {sync_err}")

        # Refresh active viewer document state if this file is currently open
        active = get_active_viewer_document()
        if active and active.get("filename") == old_base:
            set_active_viewer_document(cat, new_base, new_path, title=clean_title)

        logger.info(f"[DOC_VIEWER] Renamed {cat} '{old_base}' -> '{new_base}' (title='{clean_title}')")
        return {
            "status": "success",
            "category": cat,
            "old_filename": old_base,
            "new_filename": new_base,
            "title": clean_title,
            "file_path": new_path,
        }
    except Exception as e:
        logger.error(f"[DOC_VIEWER] Rename failed for {fpath}: {e}")
        return {"status": "error", "message": str(e)}
