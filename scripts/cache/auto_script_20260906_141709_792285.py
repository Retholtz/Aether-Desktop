import win32com.client

try:
    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False  # Headless for test
    word.DisplayAlerts = False

    doc = word.Documents.Add()
    # Add title paragraph
    p = doc.Paragraphs.Add()
    p.Range.Text = "Project Status Report"
    p.Range.InsertParagraphAfter()

    # Add 3x3 table
    table_range = doc.Paragraphs.Add().Range
    table = doc.Tables.Add(table_range, NumRows=3, NumColumns=3)
    table.Borders.Enable = True

    headers = ["Task", "Owner", "Status"]
    for col_idx, h in enumerate(headers, start=1):
        table.Cell(1, col_idx).Range.Text = h

    rows = [
        ("Vision Stream", "Aether", "Completed"),
        ("Script Engine", "Aether", "Active")
    ]
    for row_idx, r in enumerate(rows, start=2):
        for col_idx, val in enumerate(r, start=1):
            table.Cell(row_idx, col_idx).Range.Text = val

    print(f"Word Document Created! Table rows={table.Rows.Count}, cols={table.Columns.Count}")

    doc.Close(SaveChanges=False)
    word.Quit()
    print("SUCCESS: Word table automation completed flawlessly.")
except Exception as e:
    print(f"ERROR: {e}")
    try:
        word.Quit()
    except Exception:
        pass
    raise