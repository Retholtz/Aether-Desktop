import os
import sys
import time
import subprocess
import win32gui
import win32process
import psutil

# Launch notepad
subprocess.Popen(["notepad.exe"])
time.sleep(1.5)

print("Listing all visible windows:")
def cb(hwnd, _):
    if win32gui.IsWindowVisible(hwnd):
        title = win32gui.GetWindowText(hwnd)
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            p = psutil.Process(pid)
            pname = p.name()
        except Exception as e:
            pname = f"err: {e}"
        if title.strip() or "notepad" in pname.lower():
            print(f"HWND: {hwnd} (0x{hwnd:X}) | Title: {repr(title)} | Proc: {pname}")

win32gui.EnumWindows(cb, None)

