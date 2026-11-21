import win32gui
import win32con
import win32api

def run():
    monitor = win32api.MonitorFromPoint((0, 0), win32con.MONITOR_DEFAULTTOPRIMARY)
    info = win32api.GetMonitorInfo(monitor)
    work_left, work_top, work_right, work_bottom = info['Work']
    total_width = work_right - work_left
    total_height = work_bottom - work_top

    def enum_windows_callback(hwnd, windows):
        if win32gui.IsWindowVisible(hwnd) and not win32gui.IsIconic(hwnd):
            title = win32gui.GetWindowText(hwnd).strip()
            rect = win32gui.GetWindowRect(hwnd)
            width = rect[2] - rect[0]
            height = rect[3] - rect[1]
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            if title and width > 100 and height > 100 and not (ex_style & win32con.WS_EX_TOOLWINDOW):
                class_name = win32gui.GetClassName(hwnd)
                if class_name not in ['Progman', 'WorkerW', 'Shell_TrayWnd', 'Windows.UI.Core.CoreWindow']:
                    if 'Aether Desktop' not in title or 'Visual Studio' in title:
                        windows.append((hwnd, title, class_name))
        return True

    windows = []
    win32gui.EnumWindows(enum_windows_callback, windows)

    if windows:
        n = len(windows)
        col_width = total_width // n
        for i, (hwnd, _, _) in enumerate(windows):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_NOTOPMOST,
                work_left + i * col_width,
                work_top,
                col_width if i < n - 1 else (total_width - i * col_width),
                total_height,
                win32con.SWP_SHOWWINDOW
            )

if __name__ == '__main__':
    run()