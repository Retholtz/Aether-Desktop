"""
core/document_compiler.py - Deterministic Zero-Invention Compiler for `lecture_spec.json`.

Compiles a Neon-authored `lecture_spec.json` directly into:
  1. High-DPI (`dpi=300`, `bbox_inches="tight"`, >= 10pt font) `matplotlib` chart PNGs
     with pre-save curve continuity verification (`check_curve_continuity`).
  2. A 16:9 widescreen `.pptx` presentation with:
     - Programmatic Slide 1 module roadmap derived strictly from `spec["modules"]`
     - Slide-bound tables and charts rendered strictly on their owning `slide["slide_number"]`
     - Verbatim Neon bullets (zero naive string chopping, zero `...` truncation)
     - Structured `cards[]` layouts (up to 3 cards/slide, auto-splitting onto `(Cont.)` slides if > 3)
     - Full `speaker_notes` injected into PowerPoint's Notes pane
  3. A print-ready `.docx` pocket handout derived exclusively from `spec["handout_sections"]`
     and `spec["canonical_facts"]`.
  4. Automatic post-compile validation via `core.doc_validator.validate_compiled_artifacts`.

All outputs are written to writable per-user paths under `get_outputs_dir(job_id)`
(`%LOCALAPPDATA%\\Aether\\outputs\\<job_id>\\` when frozen) and optionally mirrored
to `data/pptx/` and `data/docx/`.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from core.doc_validator import (
    check_curve_continuity,
    validate_compiled_artifacts,
    validate_lecture_spec,
)
from core.logger import get_logger
from core.paths import get_outputs_dir, user_data_path

logger = get_logger("DocumentCompiler")

# Clean clinical / executive color palette
PALETTE_RGB = {
    "navy": (11, 37, 69),
    "blue": (19, 64, 116),
    "teal": (0, 128, 128),
    "light_teal": (224, 242, 241),
    "red": (193, 18, 31),
    "slate_dark": (30, 41, 59),
    "slate_muted": (100, 116, 139),
    "bg_light": (244, 247, 250),
    "white": (255, 255, 255),
    "border": (203, 213, 225),
    "row_alt": (238, 244, 249),
}

CHART_COLORS = ["#134074", "#008080", "#C1121F", "#D97706", "#6D28D9", "#334155"]


def _slugify(name: str, max_len: int = 48) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(name or "document").strip()).strip("_")
    return (cleaned[:max_len].strip("_") or "document")


def render_chart_from_spec(
    chart_spec: Dict[str, Any],
    out_png_path: str,
    *,
    slide_number: int = 0,
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """
    Renders a single `chart_spec` dictionary into a 300-DPI PNG with `bbox_inches='tight'`
    and >= 10pt fonts. Runs `check_curve_continuity` BEFORE saving if `smooth_continuous=True`.
    Returns `(png_path_or_None, issues)`.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    issues: List[Dict[str, Any]] = []
    series_list = chart_spec.get("series") or []
    smooth = bool(chart_spec.get("smooth_continuous", False))
    chart_type = str(chart_spec.get("chart_type") or "line").strip().lower()
    loc = f"slide_{slide_number}.chart_spec" if slide_number else "chart_spec"

    for idx, ser in enumerate(series_list):
        if smooth:
            issues.extend(
                check_curve_continuity(
                    ser.get("y") or [],
                    series_name=str(ser.get("name") or f"series_{idx}"),
                    location=f"{loc}.series[{idx}]",
                )
            )
    if any(i.get("severity") == "ERROR" for i in issues):
        return None, issues

    plt.rcParams["font.sans-serif"] = "DejaVu Sans"
    plt.rcParams["font.size"] = 10.5
    plt.rcParams["axes.edgecolor"] = "#CBD5E1"
    plt.rcParams["axes.linewidth"] = 0.9

    fig, ax = plt.subplots(figsize=(6.2, 4.1), dpi=300)

    if chart_type in ("bar", "grouped_bar") and series_list:
        n_series = len(series_list)
        first_x = series_list[0].get("x") or list(range(len(series_list[0].get("y") or [])))
        x_idx = np.arange(len(first_x))
        width = min(0.65 / max(1, n_series), 0.38)
        for s_i, ser in enumerate(series_list):
            y_vals = [float(v) for v in (ser.get("y") or [])]
            offset = (s_i - (n_series - 1) / 2.0) * width
            color = ser.get("color") or CHART_COLORS[s_i % len(CHART_COLORS)]
            bars = ax.bar(
                x_idx + offset,
                y_vals,
                width=width * 0.92,
                label=str(ser.get("name") or f"Series {s_i + 1}"),
                color=color,
                zorder=3,
            )
            labels = ser.get("value_labels") or []
            for b_i, bar in enumerate(bars):
                h = bar.get_height()
                lbl = str(labels[b_i]) if b_i < len(labels) else f"{h:g}"
                ax.text(
                    bar.get_x() + bar.get_width() / 2.0,
                    h + (max(y_vals) * 0.02 if y_vals else 0.5),
                    lbl,
                    ha="center",
                    va="bottom",
                    fontsize=10,
                    fontweight="bold",
                    color="#0B2545",
                )
        ax.set_xticks(x_idx)
        ax.set_xticklabels([str(lbl) for lbl in first_x], fontsize=10, fontweight="bold", color="#0B2545")
        if n_series > 1:
            ax.legend(frameon=True, fontsize=10, loc="best")
        ax.grid(axis="y", linestyle="--", alpha=0.45, zorder=0)
    else:
        # Line / continuous pharmacokinetic / trajectory chart
        for s_i, ser in enumerate(series_list):
            y_vals = np.asarray(ser.get("y") or [], dtype=float)
            raw_x = ser.get("x")
            if raw_x is not None and len(raw_x) == len(y_vals):
                # Support either numeric x or categorical x labels
                if all(isinstance(v, (int, float)) for v in raw_x):
                    x_vals = np.asarray(raw_x, dtype=float)
                    x_labels = None
                else:
                    x_vals = np.arange(len(raw_x))
                    x_labels = [str(v) for v in raw_x]
            else:
                x_vals = np.arange(len(y_vals), dtype=float)
                x_labels = None

            color = ser.get("color") or CHART_COLORS[s_i % len(CHART_COLORS)]
            ls = ser.get("linestyle") or "-"
            marker = ser.get("marker") or ("o" if len(y_vals) <= 12 else None)
            ax.plot(
                x_vals,
                y_vals,
                linewidth=2.5,
                linestyle=ls,
                marker=marker,
                markersize=6 if marker else 0,
                color=color,
                label=str(ser.get("name") or f"Series {s_i + 1}"),
                zorder=3,
            )
            if x_labels is not None:
                ax.set_xticks(x_vals)
                ax.set_xticklabels(x_labels, fontsize=10, fontweight="bold", color="#0B2545")

        if len(series_list) > 1 or any(ser.get("name") for ser in series_list):
            ax.legend(frameon=True, fontsize=10, loc="best")
        ax.grid(True, linestyle="--", alpha=0.4, zorder=0)

    # Optional horizontal target bands or reference lines
    for ann in (chart_spec.get("annotations") or []):
        if isinstance(ann, dict):
            atype = str(ann.get("type") or "").lower()
            if atype == "hspan" and "ymin" in ann and "ymax" in ann:
                ax.axhspan(
                    float(ann["ymin"]),
                    float(ann["ymax"]),
                    color=ann.get("color", "#E0F2F1"),
                    alpha=float(ann.get("alpha", 0.55)),
                    label=ann.get("label"),
                    zorder=1,
                )
            elif atype == "hline" and "y" in ann:
                ax.axhline(
                    float(ann["y"]),
                    color=ann.get("color", "#D97706"),
                    linestyle="--",
                    linewidth=1.4,
                    label=ann.get("label"),
                    zorder=2,
                )
        elif isinstance(ann, str) and ann.strip():
            ax.text(
                0.02,
                0.96,
                ann.strip(),
                transform=ax.transAxes,
                fontsize=10,
                va="top",
                ha="left",
                color="#334155",
                bbox={"boxstyle": "round,pad=0.3", "facecolor": "#F8FAFC", "edgecolor": "#CBD5E1"},
            )

    if chart_spec.get("title"):
        ax.set_title(str(chart_spec["title"]), fontsize=12, fontweight="bold", color="#0B2545", pad=12)
    if chart_spec.get("x_label"):
        ax.set_xlabel(str(chart_spec["x_label"]), fontsize=10.5, fontweight="bold", color="#0B2545")
    if chart_spec.get("y_label"):
        ax.set_ylabel(str(chart_spec["y_label"]), fontsize=10.5, fontweight="bold", color="#0B2545")

    ax.tick_params(axis="both", labelsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    if ax.get_legend_handles_labels()[1]:
        ax.legend(frameon=True, fontsize=10, loc="best")

    os.makedirs(os.path.dirname(os.path.abspath(out_png_path)), exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_png_path, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_png_path, issues


def compile_pptx_from_spec(
    spec: Dict[str, Any],
    out_pptx_path: str,
    charts_dir: str,
) -> Tuple[str, List[str], List[Dict[str, Any]]]:
    """
    Deterministically compiles `lecture_spec` into a 16:9 widescreen `.pptx` presentation.
    Returns `(out_pptx_path, rendered_chart_pngs, chart_issues)`.
    """
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Inches, Pt

    def _rgb(key: str) -> RGBColor:
        r, g, b = PALETTE_RGB[key]
        return RGBColor(r, g, b)

    NAVY = _rgb("navy")
    BLUE = _rgb("blue")
    TEAL = _rgb("teal")
    LIGHT_TEAL = _rgb("light_teal")
    SLATE_DARK = _rgb("slate_dark")
    SLATE_MUTED = _rgb("slate_muted")
    BG_LIGHT = _rgb("bg_light")
    WHITE = _rgb("white")
    BORDER = _rgb("border")
    ROW_ALT = _rgb("row_alt")

    meta = spec.get("metadata") or {}
    deck_title = str(meta.get("title") or "Presentation").strip()
    deck_subtitle = str(meta.get("subtitle") or "").strip()
    audience = str(meta.get("audience") or "").strip()
    modules = spec.get("modules") or []
    slides_spec = spec.get("slides") or []
    total_slides = len(slides_spec)

    mod_map: Dict[Any, Dict[str, Any]] = {m.get("id"): m for m in modules if isinstance(m, dict)}
    os.makedirs(charts_dir, exist_ok=True)

    # Render charts strictly bound to their owning slide_number
    slide_charts: Dict[int, str] = {}
    rendered_pngs: List[str] = []
    chart_issues: List[Dict[str, Any]] = []
    for sl in slides_spec:
        sn = int(sl.get("slide_number") or 0)
        cspec = sl.get("chart_spec")
        if cspec and sn > 0:
            png_path = os.path.join(charts_dir, f"chart_slide_{sn:02d}.png")
            out_png, c_iss = render_chart_from_spec(cspec, png_path, slide_number=sn)
            chart_issues.extend(c_iss)
            if out_png:
                slide_charts[sn] = out_png
                rendered_pngs.append(out_png)

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank_layout = prs.slide_layouts[6]

    def _add_rect(slide, left, top, width, height, fill, border=None, border_pt=0):
        shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height))
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
        if border and border_pt > 0:
            shp.line.color.rgb = border
            shp.line.width = Pt(border_pt)
        else:
            shp.line.fill.background()
        return shp

    def _add_header_footer(slide, slide_num: int, mod_label: str, title_text: str):
        _add_rect(slide, 0, 0, 13.333, 7.5, BG_LIGHT)
        _add_rect(slide, 0, 0, 13.333, 1.15, NAVY)
        _add_rect(slide, 0, 1.15, 13.333, 0.06, TEAL)

        if mod_label:
            pill = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.5), Inches(0.14), Inches(5.6), Inches(0.28))
            pill.fill.solid()
            pill.fill.fore_color.rgb = TEAL
            pill.line.fill.background()
            tf_p = pill.text_frame
            tf_p.word_wrap = False
            tf_p.margin_left = Inches(0.1)
            tf_p.margin_top = Inches(0.02)
            p = tf_p.paragraphs[0]
            p.text = mod_label.upper()
            p.font.name = "Calibri"
            p.font.size = Pt(10)
            p.font.bold = True
            p.font.color.rgb = WHITE

        tb = slide.shapes.add_textbox(Inches(0.5), Inches(0.45), Inches(12.3), Inches(0.62))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = 0
        tf.margin_top = 0
        pt = tf.paragraphs[0]
        pt.text = f"Slide {slide_num}: {title_text}"
        pt.font.name = "Calibri"
        pt.font.size = Pt(20)
        pt.font.bold = True
        pt.font.color.rgb = WHITE

        _add_rect(slide, 0, 7.12, 13.333, 0.38, WHITE, BORDER, 0.5)
        f_left = slide.shapes.add_textbox(Inches(0.5), Inches(7.16), Inches(9.5), Inches(0.28))
        pf_l = f_left.text_frame.paragraphs[0]
        pf_l.text = f"{deck_title}  |  {audience}" if audience else deck_title
        pf_l.font.name = "Calibri"
        pf_l.font.size = Pt(10)
        pf_l.font.color.rgb = SLATE_MUTED

        f_right = slide.shapes.add_textbox(Inches(11.3), Inches(7.16), Inches(1.5), Inches(0.28))
        pf_r = f_right.text_frame.paragraphs[0]
        pf_r.alignment = PP_ALIGN.RIGHT
        pf_r.text = f"{slide_num} / {total_slides}"
        pf_r.font.name = "Calibri"
        pf_r.font.size = Pt(10)
        pf_r.font.bold = True
        pf_r.font.color.rgb = NAVY

    def _add_bullet_card(slide, left, top, width, height, bullets: List[str], card_title: Optional[str] = None):
        _add_rect(slide, left, top, width, height, WHITE, BORDER, 1.0)
        _add_rect(slide, left, top, 0.08, height, BLUE)
        y_cur = top + 0.14
        if card_title:
            tb_h = slide.shapes.add_textbox(Inches(left + 0.22), Inches(y_cur), Inches(width - 0.35), Inches(0.35))
            ph = tb_h.text_frame.paragraphs[0]
            ph.text = str(card_title)
            ph.font.name = "Calibri"
            ph.font.size = Pt(15)
            ph.font.bold = True
            ph.font.color.rgb = BLUE
            y_cur += 0.38

        tb = slide.shapes.add_textbox(
            Inches(left + 0.22),
            Inches(y_cur),
            Inches(width - 0.38),
            Inches(max(0.5, height - (y_cur - top) - 0.12)),
        )
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = 0
        tf.margin_right = 0
        tf.margin_top = 0

        font_sz = 14.0 if len(bullets) <= 3 else (13.0 if len(bullets) <= 5 else 11.5)
        for idx, b_txt in enumerate(bullets):
            p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
            p.space_after = Pt(7)
            p.line_spacing = 1.15
            # Zero naive truncation: render Neon's pre-condensed bullets verbatim!
            p.text = f"▪  {str(b_txt).strip()}"
            p.font.name = "Calibri"
            p.font.size = Pt(font_sz)
            p.font.color.rgb = SLATE_DARK

    def _add_styled_table(slide, left, top, width, height, headers: List[str], rows: List[List[str]]):
        n_rows = len(rows) + 1
        n_cols = len(headers)
        tbl_shape = slide.shapes.add_table(n_rows, n_cols, Inches(left), Inches(top), Inches(width), Inches(height))
        tbl = tbl_shape.table
        hdr_font = 12 if n_cols <= 4 else 11
        cell_font = 11.5 if len(rows) <= 5 else 10.5

        for c_idx, h_val in enumerate(headers):
            cell = tbl.cell(0, c_idx)
            cell.fill.solid()
            cell.fill.fore_color.rgb = NAVY
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            p = cell.text_frame.paragraphs[0]
            p.text = str(h_val)
            p.font.name = "Calibri"
            p.font.size = Pt(hdr_font)
            p.font.bold = True
            p.font.color.rgb = WHITE

        for r_idx, row in enumerate(rows, start=1):
            bg = WHITE if r_idx % 2 == 1 else ROW_ALT
            for c_idx, val in enumerate(row):
                cell = tbl.cell(r_idx, c_idx)
                cell.fill.solid()
                cell.fill.fore_color.rgb = bg
                cell.vertical_anchor = MSO_ANCHOR.MIDDLE
                p = cell.text_frame.paragraphs[0]
                p.text = str(val)
                p.font.name = "Calibri"
                p.font.size = Pt(cell_font)
                p.font.bold = (c_idx == 0)
                p.font.color.rgb = BLUE if c_idx == 0 else SLATE_DARK

    def _add_cards_row(slide, cards_slice: List[Dict[str, Any]], top: float = 1.4, height: float = 5.5):
        n_cards = len(cards_slice)
        margin_x = 0.5
        gap = 0.28
        usable_w = 13.333 - 2 * margin_x - gap * max(0, n_cards - 1)
        card_w = usable_w / max(1, n_cards)

        for idx, card in enumerate(cards_slice):
            cx = margin_x + idx * (card_w + gap)
            _add_rect(slide, cx, top, card_w, height, WHITE, BORDER, 1.0)
            _add_rect(slide, cx, top, card_w, 0.45, BLUE)
            tb_h = slide.shapes.add_textbox(Inches(cx + 0.15), Inches(top + 0.08), Inches(card_w - 0.3), Inches(0.32))
            ph = tb_h.text_frame.paragraphs[0]
            ph.text = str(card.get("title") or f"Card {idx + 1}")
            ph.font.name = "Calibri"
            ph.font.size = Pt(13.5)
            ph.font.bold = True
            ph.font.color.rgb = WHITE

            tb_b = slide.shapes.add_textbox(Inches(cx + 0.16), Inches(top + 0.56), Inches(card_w - 0.32), Inches(height - 0.68))
            tf_b = tb_b.text_frame
            tf_b.word_wrap = True
            tf_b.margin_left = 0
            tf_b.margin_top = 0

            card_items = list(card.get("items") or [])
            if card.get("body"):
                card_items.insert(0, str(card["body"]))

            c_font = 12.0 if len(card_items) <= 5 else 11.0
            for i_idx, item_txt in enumerate(card_items):
                p = tf_b.paragraphs[0] if i_idx == 0 else tf_b.add_paragraph()
                p.space_after = Pt(6)
                p.line_spacing = 1.14
                p.text = f"▪  {str(item_txt).strip()}"
                p.font.name = "Calibri"
                p.font.size = Pt(c_font)
                p.font.color.rgb = SLATE_DARK

    # Compile slides strictly in 1..N order
    for sl in slides_spec:
        sn = int(sl.get("slide_number") or 0)
        mod_obj = mod_map.get(sl.get("module_id"), {})
        mod_label = (
            f"Module {mod_obj.get('id')} • {mod_obj.get('name')}"
            if mod_obj.get("name")
            else ""
        )
        title_txt = str(sl.get("title") or f"Slide {sn}").strip()
        layout_type = str(sl.get("layout_type") or "bullets").strip()
        bullets = [str(b).strip() for b in (sl.get("bullets") or []) if str(b).strip()]
        table_spec = sl.get("table")
        chart_png = slide_charts.get(sn)
        cards = sl.get("cards") or []
        notes = str(sl.get("speaker_notes") or "").strip()

        slide = prs.slides.add_slide(blank_layout)

        if sn == 1 or layout_type == "title_roadmap":
            _add_rect(slide, 0, 0, 13.333, 7.5, NAVY)
            _add_rect(slide, 0, 1.85, 13.333, 0.07, TEAL)

            if audience:
                tb_a = slide.shapes.add_textbox(Inches(0.6), Inches(0.32), Inches(11.5), Inches(0.35))
                pa = tb_a.text_frame.paragraphs[0]
                pa.text = audience.upper()
                pa.font.name = "Calibri"
                pa.font.size = Pt(12)
                pa.font.bold = True
                pa.font.color.rgb = LIGHT_TEAL

            tb_t = slide.shapes.add_textbox(Inches(0.6), Inches(0.68), Inches(12.1), Inches(1.1))
            tf_t = tb_t.text_frame
            tf_t.word_wrap = True
            pt = tf_t.paragraphs[0]
            pt.text = deck_title
            pt.font.name = "Calibri"
            pt.font.size = Pt(30)
            pt.font.bold = True
            pt.font.color.rgb = WHITE
            psub = tf_t.add_paragraph()
            psub.text = deck_subtitle or title_txt
            psub.font.name = "Calibri"
            psub.font.size = Pt(16)
            psub.font.color.rgb = LIGHT_TEAL

            # Programmatic module roadmap derived strictly from spec["modules"]!
            roadmap_lines = [
                f"Module {m.get('id')} (Slides {m.get('slide_start')}–{m.get('slide_end')}): {m.get('name')}"
                for m in modules
                if isinstance(m, dict)
            ]

            if chart_png and bullets:
                _add_bullet_card(slide, 0.6, 2.1, 6.2, 2.35, bullets, card_title="Key Overview")
                _add_bullet_card(slide, 0.6, 4.6, 6.2, 2.35, roadmap_lines or ["Curriculum Overview"], card_title="Curriculum Roadmap")
                _add_rect(slide, 7.05, 2.1, 5.68, 4.85, WHITE, BORDER, 1.0)
                slide.shapes.add_picture(chart_png, Inches(7.15), Inches(2.4), width=Inches(5.48))
            elif bullets and roadmap_lines:
                _add_bullet_card(slide, 0.6, 2.15, 6.0, 4.75, bullets, card_title="Session Objectives")
                _add_bullet_card(slide, 6.85, 2.15, 5.88, 4.75, roadmap_lines, card_title="Curriculum Roadmap")
            else:
                _add_bullet_card(slide, 0.6, 2.15, 12.13, 4.75, roadmap_lines or bullets, card_title="Curriculum Roadmap")

            f_r = slide.shapes.add_textbox(Inches(11.3), Inches(7.05), Inches(1.4), Inches(0.3))
            pfr = f_r.text_frame.paragraphs[0]
            pfr.alignment = PP_ALIGN.RIGHT
            pfr.text = f"1 / {total_slides}"
            pfr.font.name = "Calibri"
            pfr.font.size = Pt(11)
            pfr.font.color.rgb = LIGHT_TEAL

        elif cards:
            _add_header_footer(slide, sn, mod_label, title_txt)
            if bullets:
                _add_bullet_card(slide, 0.5, 1.38, 12.33, 1.35, bullets[:2])
                _add_cards_row(slide, cards[:3], top=2.88, height=4.05)
            else:
                _add_cards_row(slide, cards[:3], top=1.4, height=5.5)

            # Auto-split > 3 cards onto clean "(Cont.)" continuation slides rather than overflowing!
            if len(cards) > 3:
                for chunk_start in range(3, len(cards), 3):
                    cont_slide = prs.slides.add_slide(blank_layout)
                    _add_header_footer(cont_slide, sn, mod_label, f"{title_txt} (Cont.)")
                    _add_cards_row(cont_slide, cards[chunk_start:chunk_start + 3], top=1.4, height=5.5)
                    if notes:
                        cont_slide.notes_slide.notes_text_frame.text = notes

        elif chart_png and table_spec:
            _add_header_footer(slide, sn, mod_label, title_txt)
            if bullets:
                _add_bullet_card(slide, 0.5, 1.4, 6.4, 2.35, bullets, card_title="Key Points")
                _add_styled_table(slide, 0.5, 3.9, 6.4, 3.0, table_spec["headers"], table_spec["rows"])
            else:
                _add_styled_table(slide, 0.5, 1.4, 6.4, 5.5, table_spec["headers"], table_spec["rows"])
            _add_rect(slide, 7.1, 1.4, 5.73, 5.5, WHITE, BORDER, 1.0)
            slide.shapes.add_picture(chart_png, Inches(7.2), Inches(1.85), width=Inches(5.53))

        elif chart_png:
            _add_header_footer(slide, sn, mod_label, title_txt)
            _add_bullet_card(slide, 0.5, 1.4, 6.35, 5.5, bullets, card_title="Key Takeaways")
            _add_rect(slide, 7.05, 1.4, 5.78, 5.5, WHITE, BORDER, 1.0)
            slide.shapes.add_picture(chart_png, Inches(7.15), Inches(1.9), width=Inches(5.58))

        elif table_spec:
            _add_header_footer(slide, sn, mod_label, title_txt)
            if bullets:
                _add_bullet_card(slide, 0.5, 1.4, 12.33, 2.15, bullets, card_title=table_spec.get("title") or "Clinical Key Points")
                _add_styled_table(slide, 0.5, 3.72, 12.33, 3.2, table_spec["headers"], table_spec["rows"])
            else:
                _add_styled_table(slide, 0.5, 1.4, 12.33, 5.5, table_spec["headers"], table_spec["rows"])

        else:
            _add_header_footer(slide, sn, mod_label, title_txt)
            _add_bullet_card(slide, 0.5, 1.4, 12.33, 5.5, bullets, card_title="Key Takeaways")

        if notes:
            slide.notes_slide.notes_text_frame.text = notes

    os.makedirs(os.path.dirname(os.path.abspath(out_pptx_path)), exist_ok=True)
    prs.save(out_pptx_path)
    return out_pptx_path, rendered_pngs, chart_issues


def compile_docx_handout_from_spec(
    spec: Dict[str, Any],
    out_docx_path: str,
) -> str:
    """
    Deterministically compiles a concise, print-ready `.docx` audience handout derived
    EXCLUSIVELY from `spec["handout_sections"]` and `spec["canonical_facts"]`.
    Zero invention from model parametric memory.
    """
    import docx
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml import OxmlElement, parse_xml
    from docx.oxml.ns import nsdecls, qn
    from docx.shared import Inches, Pt, RGBColor

    meta = spec.get("metadata") or {}
    deck_title = str(meta.get("title") or "Quick-Reference Handout").strip()
    audience = str(meta.get("audience") or "").strip()
    guidelines = str(meta.get("guidelines_version") or "").strip()
    handout_sections = spec.get("handout_sections") or []
    canonical_facts = spec.get("canonical_facts") or {}

    doc = docx.Document()
    for section in doc.sections:
        section.top_margin = Inches(0.55)
        section.bottom_margin = Inches(0.55)
        section.left_margin = Inches(0.6)
        section.right_margin = Inches(0.6)

    normal_style = doc.styles["Normal"]
    normal_style.font.name = "Calibri"
    normal_style.font.size = Pt(9.5)

    # Banner Title
    p_title = doc.add_paragraph()
    p_title.paragraph_format.space_after = Pt(2)
    r_title = p_title.add_run(f"{deck_title} — Quick-Reference Handout")
    r_title.bold = True
    r_title.font.size = Pt(15)
    r_title.font.color.rgb = RGBColor(11, 37, 69)

    sub_parts = [p for p in (audience, guidelines) if p]
    if sub_parts:
        p_sub = doc.add_paragraph()
        p_sub.paragraph_format.space_after = Pt(6)
        r_sub = p_sub.add_run("  |  ".join(sub_parts))
        r_sub.font.size = Pt(9.5)
        r_sub.font.color.rgb = RGBColor(0, 128, 128)
        r_sub.bold = True

    def _shade_cell(cell, hex_color: str):
        shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}"/>')
        cell._tc.get_or_add_tcPr().append(shading)

    def _add_doc_table(headers: List[str], rows: List[List[str]]):
        tbl = doc.add_table(rows=len(rows) + 1, cols=len(headers))
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
        tbl.style = "Table Grid"
        for c_i, h in enumerate(headers):
            cell = tbl.cell(0, c_i)
            _shade_cell(cell, "0B2545")
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            run = p.add_run(str(h))
            run.bold = True
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(255, 255, 255)
        for r_i, row in enumerate(rows, start=1):
            bg_hex = "FFFFFF" if r_i % 2 == 1 else "EEF4F9"
            for c_i, val in enumerate(row):
                cell = tbl.cell(r_i, c_i)
                _shade_cell(cell, bg_hex)
                p = cell.paragraphs[0]
                p.paragraph_format.space_before = Pt(2)
                p.paragraph_format.space_after = Pt(2)
                run = p.add_run(str(val))
                run.font.size = Pt(8.8)
                if c_i == 0:
                    run.bold = True
                    run.font.color.rgb = RGBColor(19, 64, 116)
        spacer = doc.add_paragraph()
        spacer.paragraph_format.space_before = Pt(0)
        spacer.paragraph_format.space_after = Pt(4)

    for sec in handout_sections:
        if not isinstance(sec, dict):
            continue
        p_h = doc.add_paragraph()
        p_h.paragraph_format.space_before = Pt(6)
        p_h.paragraph_format.space_after = Pt(2)
        r_h = p_h.add_run(str(sec.get("title") or "Section"))
        r_h.bold = True
        r_h.font.size = Pt(11.5)
        r_h.font.color.rgb = RGBColor(19, 64, 116)

        if sec.get("summary"):
            p_s = doc.add_paragraph(str(sec["summary"]).strip())
            p_s.paragraph_format.space_after = Pt(3)

        for rule in (sec.get("rules") or []):
            pr = doc.add_paragraph(style="List Bullet")
            pr.paragraph_format.space_before = Pt(0)
            pr.paragraph_format.space_after = Pt(2)
            rr = pr.add_run(str(rule).strip())
            rr.font.size = Pt(9.2)

        for tbl_obj in (sec.get("tables") or []):
            if isinstance(tbl_obj, dict) and tbl_obj.get("headers") and tbl_obj.get("rows"):
                if tbl_obj.get("title"):
                    pt = doc.add_paragraph()
                    pt.paragraph_format.space_before = Pt(3)
                    pt.paragraph_format.space_after = Pt(2)
                    rt = pt.add_run(str(tbl_obj["title"]))
                    rt.bold = True
                    rt.font.size = Pt(9.5)
                    rt.font.color.rgb = RGBColor(0, 128, 128)
                _add_doc_table(tbl_obj["headers"], tbl_obj["rows"])

    # If handout_sections did not explicitly list a canonical thresholds table, append one compactly
    if canonical_facts and not handout_sections:
        p_h = doc.add_paragraph()
        r_h = p_h.add_run("Canonical Reference Thresholds")
        r_h.bold = True
        r_h.font.size = Pt(11.5)
        _add_doc_table(
            ["Parameter", "Canonical Value"],
            [[k.replace("_", " ").title(), str(v)] for k, v in canonical_facts.items()],
        )

    os.makedirs(os.path.dirname(os.path.abspath(out_docx_path)), exist_ok=True)
    doc.save(out_docx_path)
    return out_docx_path


def compile_presentation_bundle(
    lecture_spec_path: str,
    *,
    job_id: str = "",
    build_pptx: bool = True,
    build_docx: bool = True,
    mirror_to_data_dirs: bool = True,
    dossier_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    End-to-end deterministic compilation + zero-token validation from `lecture_spec.json`.
    Writes all artifacts under `get_outputs_dir(job_id)` and validates before returning.
    """
    if not lecture_spec_path or not os.path.isfile(lecture_spec_path):
        return {
            "status": "error",
            "passed": False,
            "error": f"lecture_spec.json not found: {lecture_spec_path}",
        }

    with open(lecture_spec_path, "r", encoding="utf-8") as f:
        spec = json.load(f)

    pre_val = validate_lecture_spec(spec)
    if not pre_val["passed"]:
        return {
            "status": "validation_failed",
            "passed": False,
            "phase": "lecture_spec",
            "validation": pre_val,
            "error": pre_val["summary"],
        }

    meta = spec.get("metadata") or {}
    slug = _slugify(meta.get("title") or "presentation")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    resolved_job_id = job_id.strip() or f"{ts}_{slug[:28]}"
    job_dir = get_outputs_dir(resolved_job_id)
    charts_dir = os.path.join(job_dir, "charts")
    os.makedirs(charts_dir, exist_ok=True)

    # Ensure a copy of lecture_spec.json lives inside outputs/<job_id>/
    canonical_spec_path = os.path.join(job_dir, "lecture_spec.json")
    if os.path.abspath(lecture_spec_path) != os.path.abspath(canonical_spec_path):
        shutil.copy2(lecture_spec_path, canonical_spec_path)

    pptx_path = None
    docx_path = None
    chart_pngs: List[str] = []
    compile_issues: List[Dict[str, Any]] = []

    if build_pptx:
        target_pptx = os.path.join(job_dir, f"{ts}_{slug}.pptx")
        pptx_path, chart_pngs, chart_iss = compile_pptx_from_spec(spec, target_pptx, charts_dir)
        compile_issues.extend(chart_iss)

    if build_docx and (spec.get("handout_sections") or spec.get("canonical_facts")):
        target_docx = os.path.join(job_dir, f"{ts}_{slug}_Handout.docx")
        docx_path = compile_docx_handout_from_spec(spec, target_docx)

    post_val = validate_compiled_artifacts(
        spec,
        pptx_path=pptx_path,
        docx_path=docx_path,
        chart_pngs=chart_pngs,
        dossier_path=dossier_path,
    )
    if compile_issues:
        post_val["errors"] = list(compile_issues) + list(post_val.get("errors") or [])
        post_val["passed"] = len(post_val["errors"]) == 0
        post_val["status"] = "pass" if post_val["passed"] else "fail"

    mirrored_pptx = None
    mirrored_docx = None
    if post_val["passed"] and mirror_to_data_dirs:
        if pptx_path and os.path.isfile(pptx_path):
            pptx_dir = user_data_path("data", "pptx")
            os.makedirs(pptx_dir, exist_ok=True)
            mirrored_pptx = os.path.join(pptx_dir, os.path.basename(pptx_path))
            shutil.copy2(pptx_path, mirrored_pptx)
        if docx_path and os.path.isfile(docx_path):
            docx_dir = user_data_path("data", "docx")
            os.makedirs(docx_dir, exist_ok=True)
            mirrored_docx = os.path.join(docx_dir, os.path.basename(docx_path))
            shutil.copy2(docx_path, mirrored_docx)

    logger.info(
        "[DOC_COMPILER] job=%s passed=%s pptx=%s docx=%s charts=%d errors=%d",
        resolved_job_id,
        post_val["passed"],
        os.path.basename(pptx_path) if pptx_path else "none",
        os.path.basename(docx_path) if docx_path else "none",
        len(chart_pngs),
        len(post_val.get("errors") or []),
    )
    return {
        "status": "success" if post_val["passed"] else "validation_failed",
        "passed": post_val["passed"],
        "job_id": resolved_job_id,
        "job_dir": job_dir,
        "lecture_spec_path": canonical_spec_path,
        "pptx_path": mirrored_pptx or pptx_path,
        "output_pptx_path": pptx_path,
        "docx_path": mirrored_docx or docx_path,
        "output_docx_path": docx_path,
        "chart_pngs": chart_pngs,
        "validation": post_val,
    }
