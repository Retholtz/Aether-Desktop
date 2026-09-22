import ctypes
from ctypes import wintypes
import time
import win32clipboard
import win32con
import win32gui
from tools.os_controls import bring_hwnd_to_foreground

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56

DEFAULT_LETTER_TEMPLATE = """September 22, 2026

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


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulong),
    ]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    _anonymous_ = ("_u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_u", _U),
    ]


def set_clipboard_text(text: str, retries: int = 5, delay: float = 0.05) -> None:
    """Sets text to Windows clipboard reliably with retry logic."""
    for attempt in range(retries):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                return
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(delay)


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


def paste_letter(letter_content: str = DEFAULT_LETTER_TEMPLATE) -> None:
    # Set clipboard content first to avoid foreground delays
    set_clipboard_text(letter_content)

    # Locate target window and bring to focus
    target_hwnd = locate_docs_window()
    bring_hwnd_to_foreground(target_hwnd)
    wait_for_foreground(target_hwnd)

    # Execute atomic paste sequence
    send_paste_input()
    print("Letter pasted successfully.")


if __name__ == "__main__":
    paste_letter()