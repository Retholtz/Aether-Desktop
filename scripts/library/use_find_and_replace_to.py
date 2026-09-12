import ctypes
from ctypes import wintypes
import time
from typing import Iterable, Optional
import win32con
import win32gui

user32 = ctypes.windll.user32

# Win32 SendInput Structures
PUL = ctypes.POINTER(ctypes.c_ulong)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", PUL),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", PUL),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", INPUT_UNION),
    ]


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002


def find_window_by_titles(keywords: Iterable[str]) -> Optional[int]:
    """Locate the top-level visible window containing any of the given title keywords."""
    found_hwnd = None
    lower_keywords = [kw.lower() for kw in keywords]

    def enum_cb(hwnd: int, _) -> bool:
        nonlocal found_hwnd
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).lower()
            if any(kw in title for kw in lower_keywords):
                found_hwnd = hwnd
                return False
        return True

    try:
        win32gui.EnumWindows(enum_cb, None)
    except Exception:
        # EnumWindows throws when callback returns False (match found)
        pass

    return found_hwnd


def activate_window(hwnd: int, timeout_sec: float = 0.5) -> bool:
    """Deterministically bring the target window to foreground using thread attachment."""
    if user32.GetForegroundWindow() == hwnd:
        return True

    user32.ShowWindow(hwnd, win32con.SW_RESTORE)

    cur_fg = user32.GetForegroundWindow()
    cur_thread = user32.GetWindowThreadProcessId(cur_fg, None)
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)

    attached = False
    if cur_thread != target_thread and cur_thread != 0:
        attached = bool(user32.AttachThreadInput(cur_thread, target_thread, True))

    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(cur_thread, target_thread, False)

    deadline = time.perf_counter() + timeout_sec
    while time.perf_counter() < deadline:
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.01)

    return user32.GetForegroundWindow() == hwnd


def send_hotkey(mod_vk: int, key_vk: int) -> None:
    """Send an atomic key combination (Mod + Key) using native SendInput."""
    inputs = (INPUT * 4)(
        INPUT(type=INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=mod_vk, wScan=0, dwFlags=0, time=0, dwExtraInfo=None))),
        INPUT(type=INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=key_vk, wScan=0, dwFlags=0, time=0, dwExtraInfo=None))),
        INPUT(type=INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=key_vk, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None))),
        INPUT(type=INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=mod_vk, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None))),
    )
    user32.SendInput(len(inputs), ctypes.byref(inputs), ctypes.sizeof(INPUT))


def trigger_find_replace(
    title_keywords: Iterable[str] = ("Google Docs", "Docs"),
    mod_vk: int = win32con.VK_CONTROL,
    key_vk: int = 0x48,  # 'H' key
) -> bool:
    """Focus target Docs window and immediately trigger the Find and Replace dialog."""
    hwnd = find_window_by_titles(title_keywords)
    if not hwnd:
        return False

    if not activate_window(hwnd):
        return False

    send_hotkey(mod_vk, key_vk)
    return True


if __name__ == "__main__":
    trigger_find_replace()