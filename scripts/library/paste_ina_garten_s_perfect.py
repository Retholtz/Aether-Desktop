import ctypes
from ctypes import wintypes
import time
from typing import List, Optional
import win32clipboard
import win32con
import win32gui

# Native Win32 SendInput Structures
PUL = ctypes.POINTER(ctypes.c_ulong)


class KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", PUL),
    ]


class HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", PUL),
    ]


class InputUnion(ctypes.Union):
    _fields_ = [
        ("ki", KeyBdInput),
        ("mi", MouseInput),
        ("hi", HardwareInput),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("union", InputUnion),
    ]


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56


def format_cf_html(html_str: str) -> bytes:
    """Formats an HTML snippet into the Windows CF_HTML clipboard protocol standard."""
    header_template = (
        "Version:0.9\r\n"
        "StartHTML:{:08d}\r\n"
        "EndHTML:{:08d}\r\n"
        "StartFragment:{:08d}\r\n"
        "EndFragment:{:08d}\r\n"
    )
    fragment_start = "<!--StartFragment-->"
    fragment_end = "<!--EndFragment-->"
    full_html = f"<html><body>{fragment_start}{html_str}{fragment_end}</body></html>"

    dummy_header = header_template.format(0, 0, 0, 0)
    start_html = len(dummy_header.encode("utf-8"))
    start_fragment = start_html + len(f"<html><body>{fragment_start}".encode("utf-8"))
    end_fragment = start_fragment + len(html_str.encode("utf-8"))
    end_html = start_fragment + len(f"{html_str}{fragment_end}</body></html>".encode("utf-8"))

    header = header_template.format(start_html, end_html, start_fragment, end_fragment)
    return (header + full_html).encode("utf-8")


def set_clipboard_data(
    html_content: str,
    plain_text: str,
    max_retries: int = 5,
    retry_interval: float = 0.05,
) -> None:
    """Sets dual-format (HTML and UTF-16 text) clipboard data with retry logic and strict cleanup."""
    cf_html_format = win32clipboard.RegisterClipboardFormat("HTML Format")
    formatted_html = format_cf_html(html_content)

    for attempt in range(max_retries):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(cf_html_format, formatted_html)
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, plain_text)
                return
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            if attempt == max_retries - 1:
                raise
            time.sleep(retry_interval)


def find_target_window(title_keywords: List[str]) -> Optional[int]:
    """Finds the best matching visible top-level window based on priority keywords."""
    matching_hwnds = []

    def enum_windows_callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            for priority, keyword in enumerate(title_keywords):
                if keyword.lower() in title.lower():
                    matching_hwnds.append((priority, hwnd))
                    break

    win32gui.EnumWindows(enum_windows_callback, None)
    if not matching_hwnds:
        return None

    # Sort by priority rank (earlier elements in title_keywords have higher precedence)
    matching_hwnds.sort(key=lambda item: item[0])
    return matching_hwnds[0][1]


def activate_window(hwnd: int, timeout_sec: float = 1.0) -> bool:
    """Deterministically restores and brings the specified window handle to the foreground."""
    if win32gui.GetForegroundWindow() == hwnd:
        return True

    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    # Attach thread input to bypass Windows foreground lock restrictions
    cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    fg_hwnd = win32gui.GetForegroundWindow()
    fg_thread = ctypes.windll.user32.GetWindowThreadProcessId(fg_hwnd, None)

    attached = False
    if cur_thread != fg_thread:
        attached = bool(ctypes.windll.user32.AttachThreadInput(cur_thread, fg_thread, True))

    try:
        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)
    finally:
        if attached:
            ctypes.windll.user32.AttachThreadInput(cur_thread, fg_thread, False)

    # Deterministic check until window assumes foreground status
    deadline = time.perf_counter() + timeout_sec
    while time.perf_counter() < deadline:
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.01)

    return win32gui.GetForegroundWindow() == hwnd


def dispatch_paste() -> None:
    """Dispatches an atomic Ctrl+V input sequence via native Win32 SendInput."""
    inputs = (INPUT * 4)(
        INPUT(
            type=INPUT_KEYBOARD,
            union=InputUnion(ki=KeyBdInput(wVk=VK_CONTROL, wScan=0, dwFlags=0, time=0, dwExtraInfo=None)),
        ),
        INPUT(
            type=INPUT_KEYBOARD,
            union=InputUnion(ki=KeyBdInput(wVk=VK_V, wScan=0, dwFlags=0, time=0, dwExtraInfo=None)),
        ),
        INPUT(
            type=INPUT_KEYBOARD,
            union=InputUnion(ki=KeyBdInput(wVk=VK_V, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)),
        ),
        INPUT(
            type=INPUT_KEYBOARD,
            union=InputUnion(ki=KeyBdInput(wVk=VK_CONTROL, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)),
        ),
    )
    ctypes.windll.user32.SendInput(4, ctypes.byref(inputs), ctypes.sizeof(INPUT))


def paste_recipe_to_docs(
    html_content: str,
    plain_text_content: str,
    window_queries: Optional[List[str]] = None,
) -> None:
    """Coordinates clipboard preparation, target window focus, and atomic paste execution."""
    if window_queries is None:
        window_queries = ["Google Docs", "Untitled document", "Chrome"]

    set_clipboard_data(html_content, plain_text_content)

    hwnd = find_target_window(window_queries)
    if not hwnd:
        raise RuntimeError(f"Target window matching {window_queries} could not be located.")

    if not activate_window(hwnd):
        raise RuntimeError("Failed to transition target window to foreground.")

    dispatch_paste()


if __name__ == "__main__":
    RECIPE_HTML = """
    <h1>Ina Garten's Perfect Roast Chicken</h1>
    <p><em>The Barefoot Contessa's most iconic, foolproof roast chicken with tender aromatic vegetables.</em></p>
    <p><strong>Prep Time:</strong> 20 mins &nbsp;|&nbsp; <strong>Cook Time:</strong> 1 hr 30 mins &nbsp;|&nbsp; <strong>Total Time:</strong> 2 hrs &nbsp;|&nbsp; <strong>Servings:</strong> 4 to 6</p>
    <hr/>
    <h2>Ingredients</h2>
    <ul>
        <li>1 (5 to 6-pound) roasting chicken</li>
        <li>Kosher salt and freshly ground black pepper</li>
        <li>1 large bunch fresh thyme, plus 20 sprigs fresh thyme</li>
        <li>1 lemon, halved</li>
        <li>1 head garlic, cut in half crosswise</li>
        <li>2 tablespoons (1/4 stick) unsalted butter, melted</li>
        <li>1 large yellow onion, thickly sliced</li>
        <li>2 carrots, peeled and cut into 2-inch chunks</li>
        <li>1 bulb fennel, tops removed and cut into wedges</li>
        <li>Olive oil</li>
    </ul>
    <hr/>
    <h2>Directions</h2>
    <ol>
        <li><strong>Preheat Oven:</strong> Preheat the oven to 425°F (220°C).</li>
        <li><strong>Prep Chicken:</strong> Remove chicken giblets. Rinse the chicken inside and out, remove excess fat, and pat the outside completely dry with paper towels.</li>
        <li><strong>Season Cavity:</strong> Liberally season the cavity with kosher salt and black pepper. Stuff with the bunch of fresh thyme, both lemon halves, and both garlic halves.</li>
        <li><strong>Truss & Brush:</strong> Brush the outside of the chicken with the melted butter and sprinkle generously with salt and pepper. Tie the legs together with kitchen string and tuck the wing tips underneath.</li>
        <li><strong>Prepare Vegetables:</strong> In a roasting pan, toss the sliced onion, carrots, fennel, remaining 20 sprigs of thyme, and 2 tablespoons of olive oil with salt and pepper. Spread evenly across the bottom and set the chicken breast-side up on top.</li>
        <li><strong>Roast:</strong> Roast for 1 hour 15 minutes to 1 1/2 hours, until the juices run clear when cutting between leg and thigh (or instant-read thermometer reads 165°F in thickest part of thigh).</li>
        <li><strong>Rest & Serve:</strong> Transfer chicken and vegetables to a warm platter, tent loosely with aluminum foil, and let rest for 15 to 20 minutes before carving. Serve with the pan juices and caramelized vegetables.</li>
    </ol>
    """

    RECIPE_PLAIN_TEXT = (
        "Ina Garten's Perfect Roast Chicken\n\n"
        "Prep Time: 20 mins | Cook Time: 1 hr 30 mins | Servings: 4-6\n\n"
        "Ingredients:\n"
        "- 1 (5 to 6-pound) roasting chicken\n"
        "- Kosher salt and freshly ground black pepper\n"
        "- 1 large bunch fresh thyme, plus 20 sprigs fresh thyme\n"
        "- 1 lemon, halved\n"
        "- 1 head garlic, cut in half crosswise\n"
        "- 2 tablespoons unsalted butter, melted\n"
        "- 1 large yellow onion, thickly sliced\n"
        "- 2 carrots, peeled and cut into 2-inch chunks\n"
        "- 1 bulb fennel, tops removed and cut into wedges\n"
        "- Olive oil\n\n"
        "Directions:\n"
        "1. Preheat oven to 425°F (220°C).\n"
        "2. Remove giblets, rinse chicken inside and out, and pat thoroughly dry.\n"
        "3. Season inside cavity with salt and pepper; stuff with thyme bunch, lemon halves, and garlic.\n"
        "4. Brush skin with melted butter and season well. Tie legs with kitchen twine and tuck wings under.\n"
        "5. In a roasting pan, toss onions, carrots, fennel, remaining thyme sprigs, and olive oil with salt and pepper. Place chicken on top.\n"
        "6. Roast for 1 hour 15 minutes to 1 1/2 hours until internal temperature reaches 165°F.\n"
        "7. Rest covered in foil for 15-20 minutes before carving and serving with roasted vegetables.\n"
    )

    paste_recipe_to_docs(
        html_content=RECIPE_HTML,
        plain_text_content=RECIPE_PLAIN_TEXT,
    )