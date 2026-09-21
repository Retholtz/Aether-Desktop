"""
Aether Skill: tile_windows
Arranges two target application windows side-by-side (50% left, 50% right)
on the primary desktop monitor using native Win32 window positioning.
"""

import json
import os
import sys
import time

if sys.platform == "win32":
    import win32api
    import win32con
    import win32gui
else:
    win32api = None
    win32con = None
    win32gui = None

from tools.os_controls import find_hwnd_by_query, bring_hwnd_to_foreground


def main():
    if not win32gui:
        print("Error: tile_windows requires Windows Win32 API.")
        sys.exit(1)

    args_str = os.environ.get("SKILL_ARGS", "")
    if not args_str and len(sys.argv) > 1:
        args_str = sys.argv[1]

    try:
        args = json.loads(args_str) if args_str else {}
    except Exception:
        args = {}

    left_app = args.get("left_app", "").strip()
    right_app = args.get("right_app", "").strip()

    if not left_app or not right_app:
        print("Error: left_app and right_app arguments are required.")
        sys.exit(1)

    hwnd_left = find_hwnd_by_query(left_app)
    hwnd_right = find_hwnd_by_query(right_app)

    if not hwnd_left:
        print(f"Error: Window for '{left_app}' not found.")
        sys.exit(1)
    if not hwnd_right:
        print(f"Error: Window for '{right_app}' not found.")
        sys.exit(1)

    # Get work area of primary monitor (excluding taskbar)
    work_area = win32api.GetMonitorInfo(win32api.MonitorFromPoint((0, 0)))["Work"]
    work_left, work_top, work_right, work_bottom = work_area
    total_w = work_right - work_left
    total_h = work_bottom - work_top
    half_w = total_w // 2

    # Restore windows if minimized
    win32gui.ShowWindow(hwnd_left, win32con.SW_RESTORE)
    win32gui.ShowWindow(hwnd_right, win32con.SW_RESTORE)

    # Move left window
    win32gui.MoveWindow(hwnd_left, work_left, work_top, half_w, total_h, True)
    bring_hwnd_to_foreground(hwnd_left)
    time.sleep(0.05)

    # Move right window
    win32gui.MoveWindow(hwnd_right, work_left + half_w, work_top, half_w, total_h, True)
    bring_hwnd_to_foreground(hwnd_right)

    print(f"Tiled '{left_app}' on the left and '{right_app}' on the right successfully.")


if __name__ == "__main__":
    main()

