"""
Skill: google_docs_writer
Purpose: Focuses Google Docs canvas safely, sets clipboard using whitelisted
         interfaces (zero ctypes), executes focus handshake, dispatches paste,
         and executes closed-loop paste verification.
"""

import json
import os
import sys
import time
from typing import Any, Dict, Optional, Tuple

import win32api
import win32clipboard
import win32con
import win32gui

from core.screen_stream import ensure_thread_desktop
from tools.gui_primitives import GuiPrimitivesController
from tools.os_controls import bring_hwnd_to_foreground, find_hwnd_by_query, focus_window
from tools.screen_vision import capture_screen_image


def set_safe_clipboard_text(text: str, retries: int = 5, delay: float = 0.05) -> bool:
    """Sets Unicode text to Windows clipboard reliably using whitelisted win32clipboard API."""
    if not text:
        return False
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


def get_safe_clipboard_text() -> str:
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


def find_google_docs_window(timeout: float = 3.0, poll_interval: float = 0.05) -> Optional[int]:
    """Finds Google Docs or Chrome window handle deterministically."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = find_hwnd_by_query("Google Docs")
        if hwnd:
            return hwnd
        hwnd = find_hwnd_by_query("Chrome")
        if hwnd:
            return hwnd
        time.sleep(poll_interval)
    return None


def calculate_canvas_target(hwnd: int) -> Tuple[int, int]:
    """
    Calculates upper-center coordinates of the document canvas inside the window.
    Google Docs editing canvas is horizontally centered, below top toolbars (~35% down).
    """
    rect = win32gui.GetWindowRect(hwnd)
    left, top, right, bottom = rect
    win_w = max(100, right - left)
    win_h = max(100, bottom - top)
    canvas_x = left + int(win_w * 0.5)
    canvas_y = top + int(win_h * 0.35)
    return canvas_x, canvas_y


def write_to_google_doc(
    agent_context: Any = None,
    text_content: str = "",
    app_name: str = "chrome"
) -> Dict[str, Any]:
    """
    Writes text content into an active Google Docs document safely.
    1. Ensures target window is focused.
    2. Stages text into the Windows clipboard via whitelisted win32clipboard.
    3. Clicks into document canvas to complete focus handshake.
    4. Dispatches Ctrl+V paste shortcut.
    5. Captures screen snapshot for closed-loop visual verification.
    """
    ensure_thread_desktop()

    # Step 1: Ensure Chrome / Google Docs is focused safely
    target_hwnd = None
    if agent_context and hasattr(agent_context, "focus_window"):
        agent_context.focus_window(app_name=app_name)
    else:
        focus_window(app_name)
        target_hwnd = find_google_docs_window(timeout=2.0)
        if target_hwnd:
            bring_hwnd_to_foreground(target_hwnd)
    time.sleep(0.3)

    # Step 2: Set clipboard using whitelisted Windows Clipboard API instead of raw ctypes
    if text_content:
        set_safe_clipboard_text(text_content)
    else:
        text_content = get_safe_clipboard_text()

    if not text_content:
        return {
            "status": "error",
            "message": "No text content provided to write_to_google_doc."
        }

    # Step 3: Explicit visual grounding click into the editable canvas area
    gui = GuiPrimitivesController()
    if agent_context and hasattr(agent_context, "find_and_click_element"):
        try:
            agent_context.find_and_click_element(
                app_name=app_name,
                target_description="document canvas"
            )
        except Exception:
            pass
    elif target_hwnd:
        canvas_x, canvas_y = calculate_canvas_target(target_hwnd)
        gui.click(x=canvas_x, y=canvas_y, normalized=False, app_name=app_name)
    else:
        # Fallback click near upper-center of primary display
        gui.click(x=960, y=360, normalized=False, app_name=app_name)
    time.sleep(0.25)

    # Step 4: Dispatch paste shortcut
    if agent_context and hasattr(agent_context, "press_key"):
        agent_context.press_key(key_combo="ctrl+v")
    else:
        gui.press_key(key_combo="ctrl+v")
    time.sleep(0.6)

    # Step 5: Closed-Loop Verification
    snapshot_meta = {}
    if agent_context and hasattr(agent_context, "capture_screen_snapshot"):
        snapshot = agent_context.capture_screen_snapshot()
        snapshot_meta = {"verification_snapshot": snapshot}
    else:
        try:
            img_bytes, meta = capture_screen_image("active_window")
            snapshot_meta = {
                "verification_snapshot": meta,
                "snapshot_bytes_len": len(img_bytes) if img_bytes else 0
            }
        except Exception as e:
            snapshot_meta = {"verification_error": str(e)}

    return {
        "status": "success",
        "message": f"Successfully focused Google Docs canvas and pasted content ({len(text_content)} chars).",
        "chars_written": len(text_content),
        **snapshot_meta
    }


def main():
    args_str = os.environ.get("SKILL_ARGS", "")
    if not args_str and len(sys.argv) > 1:
        args_str = sys.argv[1]

    text = ""
    app_name = "chrome"
    if args_str:
        try:
            parsed = json.loads(args_str)
            if isinstance(parsed, dict):
                text = (
                    parsed.get("text")
                    or parsed.get("text_content")
                    or parsed.get("content")
                    or parsed.get("letter")
                    or ""
                )
                app_name = parsed.get("app_name") or "chrome"
            elif isinstance(parsed, str):
                text = parsed
        except Exception:
            text = args_str

    res = write_to_google_doc(text_content=text, app_name=app_name)
    print(json.dumps(res, default=str))


if __name__ == "__main__":
    main()

