import ctypes
from ctypes import wintypes
import io
import time
import urllib.request
from typing import Sequence
from PIL import Image
import win32clipboard
import win32con
import win32gui

# ================= Win32 Input Structures =================
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _INPUT_UNION),
    ]


def send_keystroke_sequence(inputs: list[tuple[int, int]]) -> None:
    """Dispatches virtual-key actions atomically via SendInput."""
    count = len(inputs)
    input_array = (INPUT * count)()
    for idx, (vk, flags) in enumerate(inputs):
        input_array[idx].type = INPUT_KEYBOARD
        input_array[idx].u.ki = KEYBDINPUT(
            wVk=vk,
            wScan=0,
            dwFlags=flags,
            time=0,
            dwExtraInfo=None,
        )
    ctypes.windll.user32.SendInput(
        count, ctypes.byref(input_array), ctypes.sizeof(INPUT)
    )


def fetch_and_prepare_dib(url: str, target_width: int = 700, timeout: float = 10.0) -> bytes:
    """Downloads an image, scales proportionally, and exports as raw DIB bytes."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        img_data = resp.read()

    with Image.open(io.BytesIO(img_data)) as img:
        img = img.convert("RGB")
        w, h = img.size
        target_height = max(1, int(h * (target_width / w)))
        resized = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
        
        with io.BytesIO() as out_buf:
            resized.save(out_buf, format="BMP")
            # Exclude standard 14-byte BITMAPFILEHEADER to obtain device-independent bitmap
            return out_buf.getvalue()[14:]


def copy_dib_to_clipboard(dib_data: bytes, retries: int = 5, retry_delay: float = 0.05) -> None:
    """Sets CF_DIB data onto Windows clipboard with retry logic against access contention."""
    for _ in range(retries):
        try:
            win32clipboard.OpenClipboard(0)
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib_data)
                return
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            time.sleep(retry_delay)
    raise RuntimeError("Failed to acquire and set clipboard data after multiple attempts.")


def focus_window_by_keywords(keywords: Sequence[str], wait_timeout: float = 1.0) -> int:
    """Finds and foregrounds matching window deterministically without blind sleeps."""
    found_hwnd = None

    def enum_cb(hwnd: int, _) -> bool:
        nonlocal found_hwnd
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).lower()
            if any(kw.lower() in title for kw in keywords):
                found_hwnd = hwnd
                return False  # Terminate enumeration immediately
        return True

    try:
        win32gui.EnumWindows(enum_cb, None)
    except Exception:
        pass  # Win32 callback returns False to abort, which throws an exception in pywin32

    if not found_hwnd:
        raise RuntimeError(f"Target window matching {keywords} not found.")

    win32gui.ShowWindow(found_hwnd, win32con.SW_RESTORE)
    ctypes.windll.user32.SetForegroundWindow(found_hwnd)

    start = time.perf_counter()
    while win32gui.GetForegroundWindow() != found_hwnd:
        if time.perf_counter() - start > wait_timeout:
            break
        time.sleep(0.01)

    return found_hwnd


def paste_image_into_doc(
    image_url: str,
    target_width: int = 700,
    window_matchers: Sequence[str] = ("Docs", "Chrome"),
) -> None:
    # 1. Pipeline download & DIB preparation
    dib_bytes = fetch_and_prepare_dib(image_url, target_width=target_width)
    
    # 2. Safely populate clipboard
    copy_dib_to_clipboard(dib_bytes)
    
    # 3. Deterministically focus the application window
    focus_window_by_keywords(window_matchers)

    # 4. Atomic Enter keystroke to advance line
    send_keystroke_sequence([
        (win32con.VK_RETURN, 0),
        (win32con.VK_RETURN, KEYEVENTF_KEYUP),
    ])

    # Allow web document message loop to process caret displacement
    time.sleep(0.05)

    # 5. Atomic Ctrl+V keystroke to insert clipboard DIB
    send_keystroke_sequence([
        (win32con.VK_CONTROL, 0),
        (win32con.VK_V, 0),
        (win32con.VK_V, KEYEVENTF_KEYUP),
        (win32con.VK_CONTROL, KEYEVENTF_KEYUP),
    ])


if __name__ == "__main__":
    RHINO_SRV_URL = (
        "https://image-service.zaonce.net/eyJidWNrZXQiOiJmcm9udGllci1jbXMiLCJrZXkiOiIyMDI2LTA5L"
        "3JoaW5vX2dhbGFjdGljXzE5MjB4MTA4MC5qcGciLCJlZGl0cyI6eyJ3ZWJwIjp7InF1YWxpdHkiOjg1fSwid"
        "G9Gb3JtYXQiOiJ3ZWJwIiwicmVzaXplIjp7ImZpdCI6ImNvbnRhaW4ifX19"
    )
    paste_image_into_doc(image_url=RHINO_SRV_URL, target_width=700)
    print("Pasted Rhino image into Google Docs!")