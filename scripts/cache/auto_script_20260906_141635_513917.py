try:
    import win32com.client
    print('win32com available')
except Exception as e:
    print('win32com error:', e)