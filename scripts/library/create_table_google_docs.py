"""
Aether Skill: create_table_google_docs
Formats a structured table (headers & rows) as Windows Clipboard HTML and TSV,
brings Google Docs (or active browser) to the foreground, and pastes it via Ctrl+V.
"""

import html
import json
import os
import sys
import time
import win32clipboard
import win32con

from core.screen_stream import ensure_thread_desktop
ensure_thread_desktop()

from tools.os_controls import find_hwnd_by_query, bring_hwnd_to_foreground
from tools.gui_primitives import GuiPrimitivesController


def format_windows_html_clipboard(html_fragment: str) -> bytes:
    """Builds Windows standard CF_HTML format payload with byte offsets."""
    header_template = (
        "Version:0.9\r\n"
        "StartHTML:{start_html:010d}\r\n"
        "EndHTML:{end_html:010d}\r\n"
        "StartFragment:{start_frag:010d}\r\n"
        "EndFragment:{end_frag:010d}\r\n"
    )
    prefix = "<html><body>\r\n<!--StartFragment-->"
    suffix = "<!--EndFragment-->\r\n</body></html>"
    dummy_header = header_template.format(start_html=0, end_html=0, start_frag=0, end_frag=0)
    header_len = len(dummy_header.encode("utf-8"))
    start_html = header_len
    start_frag = header_len + len(prefix.encode("utf-8"))
    end_frag = start_frag + len(html_fragment.encode("utf-8"))
    end_html = end_frag + len(suffix.encode("utf-8"))
    header = header_template.format(
        start_html=start_html, end_html=end_html,
        start_frag=start_frag, end_frag=end_frag
    )
    return (header + prefix + html_fragment + suffix).encode("utf-8")


def main():
    # 1. Parse arguments from SKILL_ARGS or argv
    args_str = os.environ.get("SKILL_ARGS", "")
    if not args_str and len(sys.argv) > 1:
        args_str = sys.argv[1]

    try:
        args = json.loads(args_str) if args_str else {}
    except Exception:
        args = {}

    headers = args.get("headers", ["Header 1", "Header 2", "Header 3"])
    rows = args.get("rows", [["Row 1 Col 1", "Row 1 Col 2", "Row 1 Col 3"]])
    title = args.get("title", "")
    target_app = args.get("app_name", "Google Docs")

    # 2. Build HTML Table
    html_parts = []
    if title:
        html_parts.append(f"<h3 style='font-family: Arial, sans-serif; color: #1f2937;'>{html.escape(title)}</h3>")

    html_parts.append(
        "<table border='1' style='border-collapse: collapse; width: 100%; font-family: Arial, sans-serif; font-size: 11pt;'>"
    )
    if headers:
        html_parts.append("<thead><tr style='background-color: #f3f4f6;'>")
        for h in headers:
            html_parts.append(f"<th style='border: 1px solid #d1d5db; padding: 8px 12px; text-align: left; font-weight: bold;'>{html.escape(str(h))}</th>")
        html_parts.append("</tr></thead>")

    html_parts.append("<tbody>")
    for r_idx, row in enumerate(rows):
        bg = "#ffffff" if r_idx % 2 == 0 else "#fafafa"
        html_parts.append(f"<tr style='background-color: {bg};'>")
        for cell in row:
            html_parts.append(f"<td style='border: 1px solid #d1d5db; padding: 8px 12px;'>{html.escape(str(cell))}</td>")
        html_parts.append("</tr>")
    html_parts.append("</tbody></table>")
    html_table = "".join(html_parts)

    # 3. Build TSV Fallback
    tsv_lines = []
    if title:
        tsv_lines.append(f"{title}\n")
    if headers:
        tsv_lines.append("\t".join(str(h) for h in headers))
    for row in rows:
        tsv_lines.append("\t".join(str(c) for c in row))
    tsv_table = "\n".join(tsv_lines)

    # 4. Bring target window to foreground
    hwnd = find_hwnd_by_query(target_app) or find_hwnd_by_query("chrome")
    if hwnd:
        bring_hwnd_to_foreground(hwnd)
        time.sleep(0.15)

    # 5. Load Clipboard
    cf_html = win32clipboard.RegisterClipboardFormat("HTML Format")
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(cf_html, format_windows_html_clipboard(html_table))
        win32clipboard.SetClipboardText(tsv_table, win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()

    # 6. Paste via Ctrl+V
    gui = GuiPrimitivesController()
    gui.press_key("ctrl+v")
    time.sleep(0.1)

    print(f"Successfully created and pasted table ({len(headers)} cols, {len(rows)} rows) into {target_app}.")


if __name__ == "__main__":
    main()

