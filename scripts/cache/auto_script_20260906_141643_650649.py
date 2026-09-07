import win32com.client

try:
    excel = win32com.client.Dispatch("Excel.Application")
    excel.Visible = False  # Headless for automated test
    excel.DisplayAlerts = False

    wb = excel.Workbooks.Add()
    ws = wb.ActiveSheet

    # Write headers
    ws.Cells(1, 1).Value = "Product Name"
    ws.Cells(1, 2).Value = "Unit Price"
    ws.Cells(1, 3).Value = "Units Sold"
    ws.Cells(1, 4).Value = "Total Revenue"

    # Populate rows
    data = [
        ("Aether Desktop Pro", 49.99, 120),
        ("Voice Hardware Hub", 199.00, 35),
        ("Telemetry Sensor", 29.50, 210),
    ]

    for row_idx, (name, price, qty) in enumerate(data, start=2):
        ws.Cells(row_idx, 1).Value = name
        ws.Cells(row_idx, 2).Value = price
        ws.Cells(row_idx, 3).Value = qty
        ws.Cells(row_idx, 4).Formula = f"=B{row_idx}*C{row_idx}"

    # Summary Row
    ws.Cells(5, 1).Value = "Total"
    ws.Cells(5, 4).Formula = "=SUM(D2:D4)"

    total_val = ws.Cells(5, 4).Value
    print(f"Excel Table Created! Calculated Total={total_val}")

    wb.Close(SaveChanges=False)
    excel.Quit()
    print("SUCCESS: Excel automation completed flawlessly.")
except Exception as e:
    print(f"ERROR: {e}")
    try:
        excel.Quit()
    except Exception:
        pass
    raise