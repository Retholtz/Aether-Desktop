"""
Aether Skill: create_table_word
Connects to Microsoft Word via native COM automation (win32com.client),
inserts a formatted table with headers and data, and applies clean styling.
"""

import json
import os
import sys
import time
import win32com.client
from tools.os_controls import find_hwnd_by_query, bring_hwnd_to_foreground


def main():
    args_str = os.environ.get("SKILL_ARGS", "")
    if not args_str and len(sys.argv) > 1:
        args_str = sys.argv[1]

    try:
        args = json.loads(args_str) if args_str else {}
    except Exception:
        args = {}

    headers = args.get("headers", ["Header 1", "Header 2", "Header 3"])
    rows = args.get("rows", [["Data 1", "Data 2", "Data 3"]])
    title = args.get("title", "")

    # Bring Word to foreground if running
    hwnd = find_hwnd_by_query("word")
    if hwnd:
        bring_hwnd_to_foreground(hwnd)
        time.sleep(0.1)

    # Connect to Word via COM
    try:
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = True
        if word.Documents.Count == 0:
            doc = word.Documents.Add()
        else:
            doc = word.ActiveDocument
    except Exception as e:
        print(f"Failed to connect to Microsoft Word: {e}")
        sys.exit(1)

    selection = word.Selection

    if title:
        selection.TypeText(f"{title}\n")

    num_rows = len(rows) + (1 if headers else 0)
    num_cols = len(headers) if headers else (len(rows[0]) if rows else 1)

    table = doc.Tables.Add(Range=selection.Range, NumRows=num_rows, NumColumns=num_cols)
    try:
        table.Style = "Table Grid"
    except Exception:
        pass

    # Populate Headers
    row_offset = 1
    if headers:
        for c_idx, h in enumerate(headers, start=1):
            cell = table.Cell(1, c_idx)
            cell.Range.Text = str(h)
            cell.Range.Font.Bold = True
        row_offset = 2

    # Populate Rows
    for r_idx, row in enumerate(rows):
        current_row = r_idx + row_offset
        for c_idx, val in enumerate(row, start=1):
            if c_idx <= num_cols:
                table.Cell(current_row, c_idx).Range.Text = str(val)

    print(f"Successfully created Word table ({num_cols} cols, {num_rows} rows).")


if __name__ == "__main__":
    main()

