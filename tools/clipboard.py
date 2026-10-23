"""
Aether Clipboard Utility: tools/clipboard.py
Provides native Windows clipboard access for plain text and HTML payloads,
enabling direct copy-pasting of formatted rich text and tables into Google Docs, Word, and browsers.
"""

import sys
import os
import time
import re
import html
from collections import deque

try:
    import win32clipboard
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

# In-memory ring buffer preserving the last 15 clipboard text items before overwrites
_CLIPBOARD_HISTORY: deque = deque(maxlen=15)


def _snapshot_current_clipboard():
    """Saves the current plain-text clipboard value to the ring buffer before overwriting."""
    try:
        current = get_clipboard_text()
        if current and (not _CLIPBOARD_HISTORY or _CLIPBOARD_HISTORY[-1]["text"] != current):
            _CLIPBOARD_HISTORY.append({"text": current, "timestamp": time.time()})
    except Exception:
        pass


def get_clipboard_history() -> list:
    """Returns the in-memory clipboard history ring (oldest to newest)."""
    return list(_CLIPBOARD_HISTORY)


def restore_previous_clipboard() -> bool:
    """Restores the most recent saved clipboard entry from the history ring."""
    if not _CLIPBOARD_HISTORY:
        return False
    entry = _CLIPBOARD_HISTORY.pop()
    return set_clipboard_text(entry["text"], record_history=False)


def format_windows_html_clipboard(html_fragment: str) -> bytes:
    """Builds the standard Windows CF_HTML clipboard structure with correct byte offsets."""
    header_template = (
        "Version:0.9\r\n"
        "StartHTML:{start_html:010d}\r\n"
        "EndHTML:{end_html:010d}\r\n"
        "StartFragment:{start_frag:010d}\r\n"
        "EndFragment:{end_frag:010d}\r\n"
    )
    prefix = "<html><body>\r\n<!--StartFragment-->"
    suffix = "<!--EndFragment-->\r\n</body></html>"

    dummy = header_template.format(start_html=0, end_html=0, start_frag=0, end_frag=0)
    header_len = len(dummy.encode("utf-8"))
    start_html = header_len
    start_frag = header_len + len(prefix.encode("utf-8"))
    end_frag = start_frag + len(html_fragment.encode("utf-8"))
    end_html = end_frag + len(suffix.encode("utf-8"))

    header = header_template.format(
        start_html=start_html,
        end_html=end_html,
        start_frag=start_frag,
        end_frag=end_frag
    )
    return (header + prefix + html_fragment + suffix).encode("utf-8")


def set_clipboard_html(html_fragment: str, plain_text: str = "", record_history: bool = True) -> bool:
    """Sets the Windows clipboard to HTML format (with optional plain text fallback)."""
    if not HAS_WIN32:
        return False
    if record_history:
        _snapshot_current_clipboard()
    try:
        cf_html = win32clipboard.RegisterClipboardFormat("HTML Format")
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(cf_html, format_windows_html_clipboard(html_fragment))
            if plain_text:
                win32clipboard.SetClipboardText(plain_text, win32con.CF_UNICODETEXT)
            elif html_fragment:
                # Basic strip for fallback
                import re
                fallback = re.sub(r'<[^>]+>', ' ', html_fragment).strip()
                win32clipboard.SetClipboardText(fallback, win32con.CF_UNICODETEXT)
            return True
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return False


def set_clipboard_text(text: str, record_history: bool = True) -> bool:
    """Sets plain text to the Windows clipboard."""
    if not HAS_WIN32:
        return False
    if record_history:
        _snapshot_current_clipboard()
    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
            return True
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return False


def get_clipboard_text() -> str:
    """Retrieves plain text from the Windows clipboard."""
    if not HAS_WIN32:
        return ""
    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                return str(data or "")
            return ""
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return ""


def clean_markdown_typography(text: str) -> str:
    """
    Cleans raw LaTeX math expressions and scientific notation into clean Unicode
    for native rendering in Google Docs, Word, and rich HTML viewports.
    """
    if not text:
        return ""

    s = text

    # Common temperature expressions: $<19^\circ\text{C}$, $25^\circ\text{C}$, $25^\circ C$, 25^\circ\text{C}
    s = re.sub(r'\$?([<>=~±≤≥]?\s*\d+(?:\.\d+)?)\s*\^?\\circ\\text\{([CFcf])\}\$?', r'\1°\2', s)
    s = re.sub(r'\$?([<>=~±≤≥]?\s*\d+(?:\.\d+)?)\s*\^?\\circ\s*([CFcf])\$?', r'\1°\2', s)
    s = re.sub(r'\\circ\\text\{([CFcf])\}', r'°\1', s)
    s = re.sub(r'\^?\\circ', '°', s)

    # Degrees and ranges: $30^\circ\text{C}\text{–}35^\circ\text{C}$
    s = re.sub(r'\\text\{[–\-]{1,2}\}', '–', s)
    s = re.sub(r'\\text\{\s*to\s*\}', ' to ', s)

    # Comparison and math operators
    s = re.sub(r'\$?\\le(?:q)?\$?', '≤', s)
    s = re.sub(r'\$?\\ge(?:q)?\$?', '≥', s)
    s = re.sub(r'\$?\\pm\s*\$?(\d)', r'±\1', s)
    s = re.sub(r'\$?\\pm\$?', '±', s)
    s = re.sub(r'\$?\\approx\$?', '≈', s)
    s = re.sub(r'\$?\\times\$?', '×', s)
    s = re.sub(r'\$?\\rightarrow\$?', '→', s)
    s = re.sub(r'\$?\\to\$?', '→', s)

    # Chemical formulas and ions
    s = re.sub(r'\$?(?:\\text\{)?CO_?2\}?\$?', 'CO₂', s)
    s = re.sub(r'\$?(?:\\text\{)?H_?2O\}?\$?', 'H₂O', s)
    s = re.sub(r'\$?(?:\\text\{)?Ca\^?\{?2\+\}?\}?\$?', 'Ca²⁺', s)
    s = re.sub(r'\$?(?:\\text\{)?Mg\^?\{?2\+\}?\}?\$?', 'Mg²⁺', s)

    # Generic \text{...} extraction
    s = re.sub(r'\\text\{([^}]+)\}', r'\1', s)

    # Clean solitary math wrappers e.g. $25°C$, $<19°C$, $>27°C$, $1:1:1$, $66°F$
    s = re.sub(r'\$([<>=~±≤≥]?\s*[^$\n]+?)\$', r'\1', s)

    return s


def markdown_to_html_for_clipboard(md_text: str) -> str:
    """
    Converts Markdown content (headers, tables, bold, lists) into clean,
    styled HTML designed for direct paste into Google Docs, Microsoft Word, or web editors.
    """
    import html
    import re

    lines = md_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    html_parts = []
    in_table = False
    table_rows = []
    in_ul = False
    in_ol = False

    def _flush_table():
        nonlocal in_table, table_rows
        if not in_table or not table_rows:
            in_table = False
            table_rows = []
            return ""
        
        # Determine headers vs body
        headers = []
        body_rows = []
        for r_idx, r in enumerate(table_rows):
            # Check if this is a separator row e.g. |---|---|
            if all(c.replace("-", "").replace(":", "").strip() == "" for c in r):
                continue
            if r_idx == 0:
                headers = r
            else:
                body_rows.append(r)

        t_parts = [
            '<table border="1" style="border-collapse: collapse; width: 100%; font-family: Arial, sans-serif; font-size: 10pt; margin: 14px 0;">'
        ]
        if headers:
            t_parts.append('<thead><tr style="background-color: #f3f4f6;">')
            for h in headers:
                t_parts.append(f'<th style="border: 1px solid #d1d5db; padding: 8px 12px; text-align: left; font-weight: bold; color: #111827; white-space: nowrap; font-size: 10pt;">{_format_inline(h)}</th>')
            t_parts.append('</tr></thead>')
        
        t_parts.append('<tbody>')
        for idx, row in enumerate(body_rows):
            bg = "#ffffff" if idx % 2 == 0 else "#fafafa"
            t_parts.append(f'<tr style="background-color: {bg};">')
            for cell in row:
                t_parts.append(f'<td style="border: 1px solid #d1d5db; padding: 8px 12px; color: #374151; font-size: 10pt; vertical-align: top;">{_format_inline(cell)}</td>')
            t_parts.append('</tr>')
        t_parts.append('</tbody></table>')

        in_table = False
        table_rows = []
        return "".join(t_parts)

    def _format_inline(text: str) -> str:
        cleaned = clean_markdown_typography(text.strip())
        s = html.escape(cleaned)
        # Restore <br> tags in table cells or lists for clean multi-line display
        s = re.sub(r'&lt;br\s*/?&gt;', '<br/>', s, flags=re.IGNORECASE)
        # Bold
        s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
        # Italic
        s = re.sub(r'\*(.+?)\*', r'<em>\1</em>', s)
        # Inline code
        s = re.sub(r'`(.+?)`', r'<code style="background-color: #f3f4f6; padding: 2px 4px; border-radius: 3px; font-family: Consolas, monospace;">\1</code>', s)
        return s

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Fenced code block or Mermaid block
        if stripped.startswith("```") or stripped.startswith("`mermaid"):
            if in_table:
                html_parts.append(_flush_table())
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if in_ol:
                html_parts.append("</ol>")
                in_ol = False

            is_mermaid = "mermaid" in stripped.lower()
            code_lines = []
            i += 1
            while i < len(lines):
                c_line = lines[i]
                if c_line.strip() in ("```", "`") or c_line.strip().startswith("```"):
                    i += 1
                    break
                code_lines.append(c_line)
                i += 1
            raw_code = "\n".join(code_lines)
            if is_mermaid:
                html_parts.append(
                    f'<div style="background-color: #f8fafc; border: 1px solid #cbd5e1; border-left: 4px solid #3b82f6; '
                    f'padding: 12px 16px; margin: 14px 0; border-radius: 6px; font-family: Arial, sans-serif;">'
                    f'<strong style="color: #1e40af; font-size: 10pt; text-transform: uppercase; letter-spacing: 0.5px;">'
                    f'📊 Process Map & Sequence</strong>'
                    f'<pre style="margin: 8px 0 0 0; font-family: Consolas, monospace; font-size: 9.5pt; color: #334155; white-space: pre-wrap;">'
                    f'{html.escape(raw_code)}</pre></div>'
                )
            else:
                html_parts.append(
                    f'<pre style="background-color: #f8fafc; border: 1px solid #e2e8f0; padding: 12px 16px; '
                    f'border-radius: 6px; font-family: Consolas, monospace; font-size: 10pt; line-height: 1.4; '
                    f'color: #1e293b; overflow-x: auto; margin: 12px 0;"><code>{html.escape(raw_code)}</code></pre>'
                )
            continue

        # Check Table line
        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2:
            if not in_table:
                if in_ul:
                    html_parts.append("</ul>")
                    in_ul = False
                if in_ol:
                    html_parts.append("</ol>")
                    in_ol = False
                in_table = True
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            table_rows.append(cells)
            i += 1
            continue
        elif in_table:
            html_parts.append(_flush_table())

        if not stripped:
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if in_ol:
                html_parts.append("</ol>")
                in_ol = False
            i += 1
            continue

        # Headers
        if stripped.startswith("#### "):
            html_parts.append(f'<h4 style="font-family: Arial, sans-serif; font-size: 11pt; color: #374151; margin: 10px 0 4px 0;">{_format_inline(stripped[5:])}</h4>')
        elif stripped.startswith("### "):
            html_parts.append(f'<h3 style="font-family: Arial, sans-serif; font-size: 13pt; color: #1f2937; margin: 12px 0 6px 0;">{_format_inline(stripped[4:])}</h3>')
        elif stripped.startswith("## "):
            html_parts.append(f'<h2 style="font-family: Arial, sans-serif; font-size: 16pt; color: #111827; margin: 14px 0 8px 0; border-bottom: 1px solid #e5e7eb; padding-bottom: 4px;">{_format_inline(stripped[3:])}</h2>')
        elif stripped.startswith("# "):
            html_parts.append(f'<h1 style="font-family: Arial, sans-serif; font-size: 20pt; color: #111827; margin: 16px 0 10px 0;">{_format_inline(stripped[2:])}</h1>')
        # Lists
        elif stripped.startswith(("- ", "* ")):
            if in_ol:
                html_parts.append("</ol>")
                in_ol = False
            if not in_ul:
                html_parts.append('<ul style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.5; margin: 6px 0;">')
                in_ul = True
            html_parts.append(f'<li style="margin-bottom: 4px; color: #1f2937;">{_format_inline(stripped[2:])}</li>')
        elif re.match(r'^\d+\.\s', stripped):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if not in_ol:
                html_parts.append('<ol style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.5; margin: 6px 0;">')
                in_ol = True
            item_text = re.sub(r'^\d+\.\s', '', stripped)
            html_parts.append(f'<li style="margin-bottom: 4px; color: #1f2937;">{_format_inline(item_text)}</li>')
        # Horizontal Rule
        elif stripped in ("---", "***", "___"):
            html_parts.append('<hr style="border: 0; border-top: 1px solid #d1d5db; margin: 16px 0;" />')
        # Paragraph
        else:
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if in_ol:
                html_parts.append("</ol>")
                in_ol = False
            html_parts.append(f'<p style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.5; margin: 6px 0 8px 0; color: #1f2937;">{_format_inline(stripped)}</p>')

        i += 1

    if in_table:
        html_parts.append(_flush_table())
    if in_ul:
        html_parts.append("</ul>")
    if in_ol:
        html_parts.append("</ol>")

    return "".join(html_parts)


def copy_markdown_as_rich_html(markdown_content: str) -> bool:
    """
    Renders Markdown into styled HTML and loads both CF_HTML and plain-text
    fallback into the Windows clipboard without changing window focus.
    """
    if not HAS_WIN32:
        return False
    html_payload = markdown_to_html_for_clipboard(markdown_content)
    return set_clipboard_html(html_payload, plain_text=markdown_content)


def paste_markdown_to_target_app(markdown_content: str, app_name: str = "chrome") -> dict:
    """
    Renders markdown as styled HTML, loads it into the Windows clipboard,
    brings the target app (e.g., Google Docs in Chrome) to the foreground,
    and pastes it via Ctrl+V.
    """
    if not HAS_WIN32:
        return {"status": "error", "message": "Windows clipboard APIs not available on this platform."}

    html_payload = markdown_to_html_for_clipboard(markdown_content)
    success = set_clipboard_html(html_payload, plain_text=markdown_content)
    if not success:
        return {"status": "error", "message": "Failed to set clipboard data."}

    try:
        from tools.os_controls import find_hwnd_by_query, focus_window
        hwnd = find_hwnd_by_query(app_name)
        if not hwnd and app_name.lower() in ("chrome", "google docs", "docs"):
            hwnd = find_hwnd_by_query("google docs") or find_hwnd_by_query("chrome")

        if not hwnd:
            return {
                "status": "error",
                "message": f"Window for '{app_name}' was not found. Please ensure Google Docs or your target app is open before pasting."
            }

        focus_window(app_name)
        time.sleep(0.2)

        from tools.gui_primitives import GuiPrimitivesController
        gui = GuiPrimitivesController()
        gui.press_key("ctrl+v")
        time.sleep(0.15)

        return {
            "status": "success",
            "app_name": app_name,
            "char_count": len(markdown_content),
            "message": f"Successfully pasted rich formatted document ({len(markdown_content)} chars) into {app_name}."
        }
    except Exception as e:
        return {"status": "error", "message": f"Pasting to {app_name} failed: {e}"}


