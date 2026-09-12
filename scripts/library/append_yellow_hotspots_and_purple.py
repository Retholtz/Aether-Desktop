import ctypes
from ctypes import wintypes
import io
import time
import urllib.request
import win32clipboard
import win32con
import win32gui
from PIL import Image

# Setup Win32 SendInput Structures
user32 = ctypes.windll.user32

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

VK_CONTROL = 0x11
VK_END = 0x23
VK_RETURN = 0x0D
VK_V = 0x56


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    _anonymous_ = ("_input",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_input", _INPUT),
    ]


def _make_key_input(vk: int, flags: int = 0) -> INPUT:
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=None)
    return inp


def send_combo(modifiers: list[int], key: int) -> None:
    events = [_make_key_input(mod, 0) for mod in modifiers]
    events.append(_make_key_input(key, 0))
    events.append(_make_key_input(key, KEYEVENTF_KEYUP))
    events.extend([_make_key_input(mod, KEYEVENTF_KEYUP) for mod in reversed(modifiers)])

    arr = (INPUT * len(events))(*events)
    user32.SendInput(len(events), ctypes.byref(arr), ctypes.sizeof(INPUT))


def send_keys(*vks: int) -> None:
    events = []
    for vk in vks:
        events.append(_make_key_input(vk, 0))
        events.append(_make_key_input(vk, KEYEVENTF_KEYUP))

    arr = (INPUT * len(events))(*events)
    user32.SendInput(len(events), ctypes.byref(arr), ctypes.sizeof(INPUT))


def focus_window_by_titles(titles: tuple[str, ...]) -> bool:
    target_hwnd = None

    def enum_cb(hwnd, _):
        nonlocal target_hwnd
        if win32gui.IsWindowVisible(hwnd):
            text = win32gui.GetWindowText(hwnd)
            if any(title in text for title in titles):
                target_hwnd = hwnd

    win32gui.EnumWindows(enum_cb, None)
    if not target_hwnd:
        return False

    if win32gui.IsIconic(target_hwnd):
        win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(target_hwnd)

    for _ in range(20):
        if win32gui.GetForegroundWindow() == target_hwnd:
            return True
        time.sleep(0.02)
    return True


def safe_open_clipboard(max_retries: int = 5, retry_delay: float = 0.05) -> None:
    for i in range(max_retries):
        try:
            win32clipboard.OpenClipboard(0)
            return
        except Exception:
            if i == max_retries - 1:
                raise
            time.sleep(retry_delay)


def set_clipboard_html(html_body: str, plain_text: str) -> None:
    header_fmt = (
        "Version:0.9\r\n"
        "StartHTML:{:010d}\r\n"
        "EndHTML:{:010d}\r\n"
        "StartFragment:{:010d}\r\n"
        "EndFragment:{:010d}\r\n"
    )
    dummy_header = header_fmt.format(0, 0, 0, 0).encode("ascii")
    header_len = len(dummy_header)

    prefix = "<html>\r\n<body>\r\n<!--StartFragment-->".encode("utf-8")
    body_bytes = html_body.encode("utf-8")
    suffix = "<!--EndFragment-->\r\n</body>\r\n</html>".encode("utf-8")

    start_html = header_len
    start_frag = header_len + len(prefix)
    end_frag = start_frag + len(body_bytes)
    end_html = end_frag + len(suffix)

    header = header_fmt.format(start_html, end_html, start_frag, end_frag).encode("ascii")
    payload = header + prefix + body_bytes + suffix

    cf_html = win32clipboard.RegisterClipboardFormat("HTML Format")

    safe_open_clipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(cf_html, payload)
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, plain_text)
    finally:
        win32clipboard.CloseClipboard()


def set_clipboard_image_from_url(url: str, target_width: int = 720) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = resp.read()

    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        w, h = img.size
        target_h = int(h * (target_width / w))
        img = img.resize((target_width, target_h), Image.Resampling.LANCZOS)

        output = io.BytesIO()
        img.save(output, format="BMP")
        # Strip 14-byte BITMAPFILEHEADER to format raw DIB for CF_DIB
        dib_data = output.getvalue()[14:]

    safe_open_clipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib_data)
    finally:
        win32clipboard.CloseClipboard()


def append_document_section(
    section_title: str,
    section_html: str,
    image_url: str,
    target_width: int = 720,
    editor_settle_delay: float = 0.25,
) -> None:
    if not focus_window_by_titles(("Chrome", "Docs")):
        raise RuntimeError("Target document window (Chrome/Docs) not found.")

    # Navigate to end of document
    send_combo([VK_CONTROL], VK_END)
    time.sleep(editor_settle_delay)
    send_keys(VK_RETURN, VK_RETURN)

    # Paste formatted HTML section
    set_clipboard_html(section_html, section_title)
    send_combo([VK_CONTROL], VK_V)
    time.sleep(editor_settle_delay)

    # Position cursor for image insertion
    send_combo([VK_CONTROL], VK_END)
    time.sleep(editor_settle_delay)
    send_keys(VK_RETURN)

    # Download, format, and paste image
    set_clipboard_image_from_url(image_url, target_width=target_width)
    send_combo([VK_CONTROL], VK_V)
    time.sleep(editor_settle_delay)


if __name__ == "__main__":
    SECTION_TITLE = "4. Scanner Telemetry: Orbital Yellow Hotspots vs. Surface Purple Seams"
    SECTION_HTML = """
<h2 style="font-family: Arial, sans-serif; color: #202124; font-size: 16pt; margin-top: 24px;">4. Scanner Telemetry: Orbital Yellow Hotspots vs. Surface Purple Seams</h2>
<p style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043;">
  A key distinction in the new surface mining loop is understanding the two-tiered scanner feedback:
</p>
<ul style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043;">
  <li><strong>Orbital Yellow Hotspots (DSS Probes):</strong> Mapping a body with the Detailed Surface Scanner highlights macro-level <em>Planetary Mining Locations</em> in bright yellow/amber patches on the globe. Each yellow zone spans a roughly 5 to 6 km radius and creates numbered signal entries in your ship's Navigation panel.</li>
  <li><strong>Surface Purple Seams (Rhino Mineral Scanner):</strong> Once on the ground within the yellow boundary, driving the Rhino and pinging its dedicated Planetary Mining Deposit Scanner (2 km pulse range) illuminates high-grade ore seams with a vibrant <strong>purple/magenta outline ring</strong> directly on the terrain and radar HUD. These purple circles define the exact boundary where your automated mining rigs can be deployed.</li>
</ul>
<p style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043;">
  <strong>Visual Telemetry Reference:</strong> Surface scanning dynamics and automated rig management:
</p>
"""
    IMAGE_URL = "https://i.ytimg.com/vi/BcDSHcrvlRM/maxresdefault.jpg"

    append_document_section(SECTION_TITLE, SECTION_HTML, IMAGE_URL)
    print("Appended Yellow/Purple Scanner section and image successfully!")