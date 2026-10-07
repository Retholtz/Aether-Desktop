"""
Skill: click_document_canvas_copy_letter
Finds Google Docs/Chrome window, clicks document canvas to guarantee focus,
stages letter content in clipboard, and pastes via safe GUI primitives (zero ctypes).
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
from tools.gui_primitives import GuiPrimitivesController
from tools.os_controls import bring_hwnd_to_foreground, find_hwnd_by_query
from tools.screen_vision import capture_screen_image


def set_clipboard_text(text: str, max_retries: int = 5, retry_delay: float = 0.02) -> bool:
    """Atomically sets Unicode text to the system clipboard with automatic retries."""
    if not text:
        return False
    for _ in range(max_retries):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                return True
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            time.sleep(retry_delay)
    return False


def find_target_window(keywords: tuple = ("Google Docs", "Chrome")) -> int:
    """Finds first top-level visible window matching priority keywords."""
    for kw in keywords:
        hwnd = find_hwnd_by_query(kw)
        if hwnd:
            return hwnd
    return 0


def paste_text_to_canvas(
    text: str,
    target_keywords: tuple = ("Google Docs", "Chrome"),
    focus_timeout: float = 0.5,
) -> bool:
    ensure_thread_desktop()

    if not set_clipboard_text(text):
        return False

    target_hwnd = find_target_window(target_keywords)
    canvas_x, canvas_y = 960, 360
    if target_hwnd:
        bring_hwnd_to_foreground(target_hwnd)
        start_time = time.perf_counter()
        while (
            win32gui.GetForegroundWindow() != target_hwnd
            and (time.perf_counter() - start_time) < focus_timeout
        ):
            time.sleep(0.01)

        rect = win32gui.GetWindowRect(target_hwnd)
        left, top, right, bottom = rect
        win_w = max(100, right - left)
        win_h = max(100, bottom - top)
        canvas_x = left + int(win_w * 0.5)
        canvas_y = top + int(win_h * 0.35)

    gui = GuiPrimitivesController()
    gui.click(x=canvas_x, y=canvas_y, normalized=False, app_name="chrome")
    time.sleep(0.2)
    gui.press_key("ctrl+v")
    time.sleep(0.5)

    try:
        capture_screen_image("active_window")
    except Exception:
        pass

    return True


DEFAULT_LETTER_TEXT = """September 22, 2026

Dr. Stanley Orlop
[Organization / Practice Name]

Dear Stanley,

It is with a genuinely heavy heart that I am writing to submit my formal resignation from my position, effective two weeks from today.

Having known each other and worked together for so many years, this was an exceptionally difficult decision to reach. The journey we have shared and the deep personal friendship we have built mean more to me than words can fully express. You have been far more than an esteemed colleague and leader—you have been a trusted confidant, an inspiration, and a true friend whose guidance, warmth, and camaraderie I will always cherish.

Please know that this step comes only after extensive thought and personal reflection, and it in no way diminishes my profound respect and affection for you and our work together.

Over the coming weeks, my absolute priority is to ensure that this transition is as seamless and supportive as possible. I am completely dedicated to assisting in handing over my responsibilities, wrapping up active matters, and helping the team in any way needed so that no momentum is lost.

Above all, Stanley, thank you from the bottom of my heart for your unwavering support, your trust, and your friendship across all these years. While my professional chapter here is drawing to a close, our friendship is something I treasure deeply and look forward to continuing for many years to come.

With warmth, gratitude, and highest regard,

Sincerely,"""


def main():
    args_str = os.environ.get("SKILL_ARGS", "")
    if not args_str and len(sys.argv) > 1:
        args_str = sys.argv[1]

    text = DEFAULT_LETTER_TEXT
    if args_str:
        try:
            parsed = json.loads(args_str)
            if isinstance(parsed, dict):
                text = parsed.get("text") or parsed.get("letter") or parsed.get("content") or DEFAULT_LETTER_TEXT
            elif isinstance(parsed, str):
                text = parsed
        except Exception:
            text = args_str

    success = paste_text_to_canvas(text)
    if success:
        print("Pasted into document canvas.")


if __name__ == "__main__":
    main()