import win32com.client

try:
    # Launch Excel
    excel = win32com.client.Dispatch("Excel.Application")
    excel.Visible = True

    # Create a new workbook
    workbook = excel.Workbooks.Add()
    sheet = workbook.ActiveSheet

    # Define headers
    headers = ["Product", "Quantity", "Price"]

    # Populate headers in the first row (A1, B1, C1)
    for i, header in enumerate(headers):
        sheet.Cells(1, i + 1).Value = header

    # Auto-fit columns for better visibility
    sheet.Columns("A:C").AutoFit()

except Exception as e:
    print(f"An error occurred: {e}")