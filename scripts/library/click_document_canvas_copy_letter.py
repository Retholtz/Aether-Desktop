import ctypes
from ctypes import wintypes
import time
import win32clipboard
import win32gui
from tools.os_controls import bring_hwnd_to_foreground

# --- Input Structure Definitions ---
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


def set_clipboard_text(text: str, max_retries: int = 5, retry_delay: float = 0.02) -> bool:
    """Atomically sets Unicode text to the system clipboard with automatic retries."""
    for _ in range(max_retries):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
                return True
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            time.sleep(retry_delay)
    return False


def find_target_window(keywords: tuple[str, ...]) -> int:
    """Finds first top-level visible window matching priority keywords."""
    matches = {}

    def enum_proc(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            for kw in keywords:
                if kw in title and kw not in matches:
                    matches[kw] = hwnd
        return True

    win32gui.EnumWindows(enum_proc, None)
    for kw in keywords:
        if kw in matches:
            return matches[kw]
    return 0


def click_canvas(x: int, y: int) -> None:
    """Moves cursor and executes a mouse click atomically via SendInput."""
    ctypes.windll.user32.SetCursorPos(x, y)
    mouse_inputs = (INPUT * 2)(
        INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0)),
        INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0)),
    )
    ctypes.windll.user32.SendInput(2, mouse_inputs, ctypes.sizeof(INPUT))


def send_paste_keystroke() -> None:
    """Sends Ctrl+V sequence atomically in a single SendInput call."""
    key_inputs = (INPUT * 4)(
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(VK_CONTROL, 0, 0, 0, 0)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(VK_V, 0, 0, 0, 0)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(VK_V, 0, KEYEVENTF_KEYUP, 0, 0)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0, 0)),
    )
    ctypes.windll.user32.SendInput(4, key_inputs, ctypes.sizeof(INPUT))


def paste_text_to_canvas(
    text: str,
    target_keywords: tuple[str, ...] = ("Google Docs", "Chrome"),
    click_coords: tuple[int, int] = (1800, 450),
    focus_timeout: float = 0.5,
) -> bool:
    if not set_clipboard_text(text):
        return False

    target_hwnd = find_target_window(target_keywords)
    if target_hwnd:
        bring_hwnd_to_foreground(target_hwnd)
        start_time = time.perf_counter()
        while (
            win32gui.GetForegroundWindow() != target_hwnd
            and (time.perf_counter() - start_time) < focus_timeout
        ):
            time.sleep(0.01)

    click_canvas(*click_coords)
    # Brief yield allowing document surface to register active focus
    time.sleep(0.05)
    send_paste_keystroke()
    return True


if __name__ == "__main__":
    LETTER_TEXT = """September 22, 2026

Dr. Stanley Orlop
[Organization / Practice Name]

Dear Stanley,

It is with a genuinely heavy heart that I am writing to submit my formal resignation from my position, effective [Last Working Day, e.g., October 6, 2026].

Having known each other and worked together for so many years, this was an exceptionally difficult decision to reach. The journey we have shared and the deep personal friendship we have built mean more to me than words can fully express. You have been far more than an esteemed colleague and leader—you have been a trusted confidant, an inspiration, and a true friend whose guidance, warmth, and camaraderie I will always cherish.

Please know that this step comes only after extensive thought and personal reflection, and it in no way diminishes my profound respect and affection for you and our work together.

Over the coming weeks, my absolute priority is to ensure that this transition is as seamless and supportive as possible. I am completely dedicated to assisting in handing over my responsibilities, wrapping up active matters, and helping the team in any way needed so that no momentum is lost.

Above all, Stanley, thank you from the bottom of my heart for your unwavering support, your trust, and your friendship across all these years. While my professional chapter here is drawing to a close, our friendship is something I treasure deeply and look forward to continuing for many years to come.

With warmth, gratitude, and highest regard,

Mike"""

    success = paste_text_to_canvas(LETTER_TEXT, click_coords=(1800, 450))
    if success:
        print("Pasted into document canvas.")