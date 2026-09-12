import ctypes
from ctypes import wintypes
import io
import time
import urllib.request
from PIL import Image
import win32clipboard
import win32con
import win32gui

# --- CTYPES STRUCTURES FOR DETERMINISTIC INPUT DISPATCH ---
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )

class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = (("ki", KEYBDINPUT),)
    _anonymous_ = ("_input",)
    _fields_ = (
        ("type", wintypes.DWORD),
        ("_input", _INPUT),
    )

LPINPUT = ctypes.POINTER(INPUT)
SendInput = ctypes.windll.user32.SendInput
SendInput.argtypes = (wintypes.UINT, LPINPUT, ctypes.c_int)
SendInput.restype = wintypes.UINT


def send_combo(modifiers: list[int], key: int) -> None:
    """Dispatches key combos (e.g., Ctrl+V) atomically in a single SendInput call."""
    inputs = [INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=mod)) for mod in modifiers]
    inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=key)))
    inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=key, dwFlags=KEYEVENTF_KEYUP)))
    inputs.extend(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=mod, dwFlags=KEYEVENTF_KEYUP)) for mod in reversed(modifiers))
    
    count = len(inputs)
    arr = (INPUT * count)(*inputs)
    SendInput(count, arr, ctypes.sizeof(INPUT))


def send_keys(*keys: int) -> None:
    """Dispatches sequential individual keypresses atomically."""
    inputs = []
    for k in keys:
        inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=k)))
        inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=k, dwFlags=KEYEVENTF_KEYUP)))
    count = len(inputs)
    arr = (INPUT * count)(*inputs)
    SendInput(count, arr, ctypes.sizeof(INPUT))


def open_clipboard_with_retry(hwnd: int = 0, max_retries: int = 10, delay: float = 0.02) -> None:
    """Safely acquires clipboard lock against transient shell locks."""
    for _ in range(max_retries):
        try:
            win32clipboard.OpenClipboard(hwnd)
            return
        except Exception:
            time.sleep(delay)
    win32clipboard.OpenClipboard(hwnd)


def set_clipboard_html(html_body: str, plain_text: str) -> None:
    marker_block = (
        "Version:0.9\r\n"
        "StartHTML:{:08d}\r\n"
        "EndHTML:{:08d}\r\n"
        "StartFragment:{:08d}\r\n"
        "EndFragment:{:08d}\r\n"
    )
    fragment_start = "<!--StartFragment-->"
    fragment_end = "<!--EndFragment-->"
    html_content = f"<html>\r\n<body>\r\n{fragment_start}{html_body}{fragment_end}\r\n</body>\r\n</html>"
    
    dummy_len = len(marker_block.format(0, 0, 0, 0))
    start_html = dummy_len
    end_html = start_html + len(html_content)
    start_fragment = start_html + html_content.find(fragment_start) + len(fragment_start)
    end_fragment = start_html + html_content.find(fragment_end)
    
    header = marker_block.format(start_html, end_html, start_fragment, end_fragment)
    payload = (header + html_content).encode("utf-8")
    
    cf_html = win32clipboard.RegisterClipboardFormat("HTML Format")
    open_clipboard_with_retry()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(cf_html, payload)
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, plain_text)
    finally:
        win32clipboard.CloseClipboard()


def set_clipboard_dib(dib_data: bytes) -> None:
    open_clipboard_with_retry()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib_data)
    finally:
        win32clipboard.CloseClipboard()


def find_and_focus_window(title_patterns: tuple[str, ...], timeout: float = 2.0) -> int:
    target_hwnd = None

    def enum_windows_callback(hwnd: int, _) -> bool:
        nonlocal target_hwnd
        if win32gui.IsWindowVisible(hwnd):
            text = win32gui.GetWindowText(hwnd)
            if any(pattern in text for pattern in title_patterns):
                target_hwnd = hwnd
                return False
        return True

    try:
        win32gui.EnumWindows(enum_windows_callback, None)
    except Exception:
        pass

    if not target_hwnd:
        raise RuntimeError(f"Could not find window matching patterns: {title_patterns}")

    win32gui.ShowWindow(target_hwnd, win32con.SW_MAXIMIZE)
    win32gui.SetForegroundWindow(target_hwnd)

    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        if win32gui.GetForegroundWindow() == target_hwnd:
            break
        time.sleep(0.02)

    return target_hwnd


def prepare_image_dib(image_url: str, target_width: int = 700) -> bytes:
    req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw_bytes = resp.read()

    with Image.open(io.BytesIO(raw_bytes)) as img:
        img_rgb = img.convert('RGB')
        width, height = img_rgb.size
        target_height = int(height * (target_width / width))
        resized_img = img_rgb.resize((target_width, target_height), Image.Resampling.LANCZOS)
        
        with io.BytesIO() as bmp_stream:
            resized_img.save(bmp_stream, format='BMP')
            return bmp_stream.getvalue()[14:]  # Extract DIB


def insert_doc_section(
    window_patterns: tuple[str, ...] = ("Google Docs", "Chrome"),
    heading_html: str = (
        '<h2 style="font-family: Arial, sans-serif; color: #202124; font-size: 16pt; margin-top: 24px;">'
        '4. Detailed Surface Scanner (DSS) Planetary Heat Map & Hotspots</h2>'
        '<p style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043;">'
        'When launching mapping probes from orbit in Analysis Mode, the DSS generates a spherical heatmap '
        'overlay across the planetary surface. The cyan/teal shaded zones indicate deposit distributions, '
        'bio/geo biomes, and concentrated outcrop regions ideal for deploying the Rhino SRV and mining rigs:'
        '</p>'
    ),
    plain_heading: str = "4. Detailed Surface Scanner (DSS) Planetary Heat Map & Hotspots",
    image_url: str = (
        "https://preview.redd.it/how-to-use-the-dss-heat-map-v0-9lr89ek4sv9a1.png"
        "?width=1600&format=png&auto=webp&s=1b8ca59a5e9a55ba316e72e870695695e5b1f39f"
    ),
    target_image_width: int = 700
) -> None:
    # 1. Preload external resource before window interaction to minimize desync risk
    dib_payload = prepare_image_dib(image_url, target_width=target_image_width)

    # 2. Acquire and focus target document window
    find_and_focus_window(window_patterns)

    # 3. Navigate to document end
    send_combo([win32con.VK_CONTROL], win32con.VK_END)
    time.sleep(0.08)  # Deterministic tick for Google Docs virtual canvas to update caret

    send_keys(win32con.VK_RETURN, win32con.VK_RETURN)
    time.sleep(0.04)

    # 4. Paste formatted HTML heading and paragraph
    set_clipboard_html(heading_html, plain_heading)
    send_combo([win32con.VK_CONTROL], 0x56)  # Ctrl+V
    time.sleep(0.12)  # Allow browser DOM to absorb the HTML structure

    send_keys(win32con.VK_RETURN)
    time.sleep(0.04)

    # 5. Paste DIB image
    set_clipboard_dib(dib_payload)
    send_combo([win32con.VK_CONTROL], 0x56)  # Ctrl+V

    print("DSS Heat Map heading and image inserted successfully!")


if __name__ == "__main__":
    insert_doc_section()