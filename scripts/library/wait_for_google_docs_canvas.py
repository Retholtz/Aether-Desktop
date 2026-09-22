"""
Aether Skill: wait_for_google_docs_canvas
Finds and activates Google Docs in Chrome, ensures the editable document canvas is clicked
and focused to avoid dropped keystrokes, and pastes text via SendInput.
"""

import ctypes
from ctypes import wintypes
import json
import os
import sys
import time
import win32clipboard
import win32con
import win32gui
from tools.os_controls import bring_hwnd_to_foreground

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulong),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulong),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("_u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_u", _INPUT_UNION),
    ]


def set_clipboard_text(text: str, retries: int = 5, delay: float = 0.05) -> bool:
    """Sets text to Windows clipboard reliably with retry logic."""
    for attempt in range(retries):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                return True
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            if attempt == retries - 1:
                return False
            time.sleep(delay)
    return False


def get_clipboard_text() -> str:
    """Retrieves current Unicode text from clipboard if present."""
    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) or ""
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass
    return ""


def locate_docs_window(timeout: float = 5.0, poll_interval: float = 0.1) -> int:
    """Finds Google Docs or Chrome window handle deterministically."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        target_hwnd = None
        fallback_hwnd = None

        def enum_cb(hwnd, _):
            nonlocal target_hwnd, fallback_hwnd
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if "Google Docs" in title:
                    target_hwnd = hwnd
                    return False
                elif "Chrome" in title and fallback_hwnd is None:
                    fallback_hwnd = hwnd
            return True

        win32gui.EnumWindows(enum_cb, None)
        chosen = target_hwnd or fallback_hwnd
        if chosen:
            return chosen

        time.sleep(poll_interval)
    raise TimeoutError("Timed out waiting for Google Docs / Chrome window.")


def wait_for_foreground(hwnd: int, timeout: float = 2.0, poll_interval: float = 0.05) -> bool:
    """Waits until the specified window is the foreground window."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        time.sleep(poll_interval)
    return False


def click_canvas(x: int, y: int) -> None:
    """Moves cursor and executes a mouse click atomically via SendInput."""
    ctypes.windll.user32.SetCursorPos(x, y)
    mouse_inputs = (INPUT * 2)(
        INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0)),
        INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0)),
    )
    ctypes.windll.user32.SendInput(2, mouse_inputs, ctypes.sizeof(INPUT))


def send_paste_input() -> None:
    """Dispatches atomic Ctrl+V input array via SendInput."""
    inputs = (INPUT * 4)(
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_V, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_V, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)),
    )
    sent = ctypes.windll.user32.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise ctypes.WinError(ctypes.get_last_error())


def focus_and_paste_google_docs(text: str = "") -> bool:
    """
    1. Sets clipboard text if provided.
    2. Brings Google Docs / Chrome window to foreground.
    3. Clicks into the editable document canvas to ensure focus.
    4. Pastes content via Ctrl+V.
    """
    if text:
        set_clipboard_text(text)
    else:
        text = get_clipboard_text()

    # Locate target window and bring to foreground
    target_hwnd = locate_docs_window()
    bring_hwnd_to_foreground(target_hwnd)
    wait_for_foreground(target_hwnd)

    # Calculate center of upper canvas area to guarantee focus within document body
    rect = win32gui.GetWindowRect(target_hwnd)
    left, top, right, bottom = rect
    win_w = max(100, right - left)
    win_h = max(100, bottom - top)

    # Google Docs editing canvas is horizontally centered, below top toolbars (~35% down)
    canvas_x = left + int(win_w * 0.5)
    canvas_y = top + int(win_h * 0.35)

    click_canvas(canvas_x, canvas_y)
    # Yield briefly so the document canvas registers focus
    time.sleep(0.12)

    # Execute atomic paste sequence
    send_paste_input()
    print(f"Successfully focused Google Docs canvas and dispatched paste ({len(text)} chars). Note: Remember to call capture_screen_snapshot to verify the visual outcome.")
    return True


def main():
    args_str = os.environ.get("SKILL_ARGS", "")
    if not args_str and len(sys.argv) > 1:
        args_str = sys.argv[1]

    text = ""
    if args_str:
        try:
            parsed = json.loads(args_str)
            if isinstance(parsed, dict):
                text = parsed.get("text") or parsed.get("content") or parsed.get("letter") or ""
            elif isinstance(parsed, str):
                text = parsed
        except Exception:
            text = args_str

    focus_and_paste_google_docs(text)


if __name__ == "__main__":
    main()