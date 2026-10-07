"""
Skill: use_find_and_replace_to
Focuses Google Docs window and triggers Find and Replace dialog (Ctrl+H) safely without ctypes.
"""

import time
from typing import Iterable, Optional
import win32api
import win32con
import win32gui
from tools.os_controls import bring_hwnd_to_foreground


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
        pass

    return found_hwnd


def activate_window(hwnd: int, timeout_sec: float = 0.5) -> bool:
    """Deterministically bring the target window to foreground."""
    bring_hwnd_to_foreground(hwnd)
    deadline = time.perf_counter() + timeout_sec
    while time.perf_counter() < deadline:
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.01)
    return win32gui.GetForegroundWindow() == hwnd


def send_hotkey(mod_vk: int, key_vk: int) -> None:
    """Send an atomic key combination (Mod + Key) using win32api."""
    win32api.keybd_event(mod_vk, 0, 0, 0)
    win32api.keybd_event(key_vk, 0, 0, 0)
    win32api.keybd_event(key_vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(mod_vk, 0, win32con.KEYEVENTF_KEYUP, 0)


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