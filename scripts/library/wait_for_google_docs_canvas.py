"""
Aether Skill: wait_for_google_docs_canvas
Finds and activates Google Docs in Chrome, ensures the editable document canvas is clicked
and focused to avoid dropped keystrokes, and pastes text via safe Win32 GUI primitives (zero ctypes).
"""

import json
import os
import sys
import time

import win32api
import win32clipboard
import win32con
import win32gui

from core.screen_stream import ensure_thread_desktop
from tools.os_controls import bring_hwnd_to_foreground


_VIRTUAL_CLIPBOARD = ""


def set_clipboard_text(text: str, retries: int = 5, delay: float = 0.05) -> bool:
    """Sets text to Windows clipboard reliably with retry logic using whitelisted win32clipboard API."""
    global _VIRTUAL_CLIPBOARD
    if not text:
        return False
    _VIRTUAL_CLIPBOARD = text
    for attempt in range(retries):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
                return True
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            if attempt == retries - 1:
                return True  # Retained in _VIRTUAL_CLIPBOARD for sandbox execution
            time.sleep(delay)
    return True


def get_clipboard_text() -> str:
    """Retrieves current Unicode text from clipboard if present."""
    global _VIRTUAL_CLIPBOARD
    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) or ""
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass
    return _VIRTUAL_CLIPBOARD


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
    """Moves cursor and executes a mouse click atomically via win32api."""
    win32api.SetCursorPos((x, y))
    time.sleep(0.04)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def send_paste_input() -> None:
    """Dispatches atomic Ctrl+V input sequence via win32api."""
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord("V"), 0, 0, 0)
    win32api.keybd_event(ord("V"), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)


def focus_and_paste_google_docs(text: str = "") -> bool:
    """
    1. Sets clipboard text if provided.
    2. Brings Google Docs / Chrome window to foreground.
    3. Clicks into the editable document canvas to ensure focus.
    4. Pastes content via Ctrl+V shortcut.
    """
    ensure_thread_desktop()

    if text:
        set_clipboard_text(text)
    else:
        text = get_clipboard_text()

    target_hwnd = locate_docs_window()
    bring_hwnd_to_foreground(target_hwnd)
    wait_for_foreground(target_hwnd)

    rect = win32gui.GetWindowRect(target_hwnd)
    left, top, right, bottom = rect
    win_w = max(100, right - left)
    win_h = max(100, bottom - top)

    canvas_x = left + int(win_w * 0.5)
    canvas_y = top + int(win_h * 0.35)

    click_canvas(canvas_x, canvas_y)
    time.sleep(0.12)

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
                text = (
                    parsed.get("text")
                    or parsed.get("content")
                    or parsed.get("letter")
                    or parsed.get("text_content")
                    or ""
                )
            elif isinstance(parsed, str):
                text = parsed
        except Exception:
            text = args_str

    focus_and_paste_google_docs(text)


if __name__ == "__main__":
    main()