import time
import ctypes
import re
import win32clipboard
import win32con
import win32gui
from tools.os_controls import bring_hwnd_to_foreground

def put_html_on_clipboard(html_fragment: str) -> None:
    """Formats and places HTML and plain text fallback onto the Windows clipboard."""
    CF_HTML = win32clipboard.RegisterClipboardFormat("HTML Format")
    
    header_template = (
        "Version:0.9\r\n"
        "StartHTML:{start_html:010d}\r\n"
        "EndHTML:{end_html:010d}\r\n"
        "StartFragment:{start_fragment:010d}\r\n"
        "EndFragment:{end_fragment:010d}\r\n"
    )
    
    prefix = "<html>\r\n<body>\r\n<!--StartFragment-->"
    suffix = "<!--EndFragment-->\r\n</body>\r\n</html>"
    
    # Calculate byte lengths for accurate clipboard offsets
    prefix_bytes = prefix.encode('utf-8')
    suffix_bytes = suffix.encode('utf-8')
    fragment_bytes = html_fragment.encode('utf-8')
    
    dummy_header = header_template.format(start_html=0, end_html=0, start_fragment=0, end_fragment=0)
    header_bytes_len = len(dummy_header.encode('utf-8'))
    
    start_html = header_bytes_len
    start_fragment = start_html + len(prefix_bytes)
    end_fragment = start_fragment + len(fragment_bytes)
    end_html = end_fragment + len(suffix_bytes)
    
    final_header = header_template.format(
        start_html=start_html,
        end_html=end_html,
        start_fragment=start_fragment,
        end_fragment=end_fragment
    )
    
    payload = final_header.encode('utf-8') + prefix_bytes + fragment_bytes + suffix_bytes
    plain_text = re.sub(r'<[^>]+>', '', html_fragment)
    
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(CF_HTML, payload)
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, plain_text)
    finally:
        win32clipboard.CloseClipboard()

def find_window_by_titles(title_substrings: list) -> int:
    """Finds the first visible window handle matching any of the given substrings."""
    found_hwnd = 0
    def enum_cb(hwnd, _):
        nonlocal found_hwnd
        if found_hwnd == 0 and win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if any(sub in title for sub in title_substrings):
                found_hwnd = hwnd
    win32gui.EnumWindows(enum_cb, None)
    return found_hwnd

def paste_to_window(hwnd: int, click_x: int, click_y: int, timeout: float = 2.0) -> bool:
    """Brings window to foreground, clicks a specific coordinate, and sends Ctrl+V."""
    if not hwnd:
        return False

    bring_hwnd_to_foreground(hwnd)
    
    # Deterministic wait for the window to become the active foreground window
    start_time = time.time()
    while win32gui.GetForegroundWindow() != hwnd:
        if time.time() - start_time > timeout:
            print("Timeout waiting for window to come to foreground.")
            return False
        time.sleep(0.05)

    # Mouse click
    ctypes.windll.user32.SetCursorPos(click_x, click_y)
    ctypes.windll.user32.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    ctypes.windll.user32.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    
    # Minimal deterministic wait for UI focus processing
    time.sleep(0.05)

    # Send Ctrl+V
    VK_CONTROL = 0x11
    VK_V = 0x56
    KEYEVENTF_KEYUP = 0x0002

    ctypes.windll.user32.keybd_event(VK_CONTROL, 0, 0, 0)
    ctypes.windll.user32.keybd_event(VK_V, 0, 0, 0)
    ctypes.windll.user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    ctypes.windll.user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    
    return True

def automate_html_paste(html_content: str, target_titles: list, click_x: int, click_y: int) -> None:
    """Main automation routine."""
    put_html_on_clipboard(html_content)
    print("Copied HTML content to clipboard.")
    
    hwnd = find_window_by_titles(target_titles)
    if hwnd:
        success = paste_to_window(hwnd, click_x, click_y)
        if success:
            print("Successfully dispatched Ctrl+V to target window.")
    else:
        print("Target window not found.")

if __name__ == "__main__":
    # Parameterized inputs
    TARGET_TITLES = ["Google Docs", "Chrome"]
    CLICK_X = 900
    CLICK_Y = 500
    
    HTML_CONTENT = """
    <h1>How to Create a Sourdough Starter from Scratch</h1>
    <p>A sourdough starter is a living culture of wild yeasts and beneficial lactic acid bacteria created naturally by fermenting flour and water. With a simple daily feeding routine, you can build a robust, active starter in 7 days.</p>
    <hr/>
    <h2>1. Essential Equipment & Ingredients</h2>
    <ul>
      <li><b>Flour:</b> Unbleached all-purpose, bread flour, or whole wheat / rye flour.</li>
      <li><b>Water:</b> Filtered, unchlorinated water at room temperature (~75°F / 24°C).</li>
      <li><b>Container:</b> 1-quart (32 oz) wide-mouth glass jar.</li>
      <li><b>Utensils:</b> A digital kitchen scale and a silicone spatula.</li>
    </ul>
    """
    
    automate_html_paste(HTML_CONTENT, TARGET_TITLES, CLICK_X, CLICK_Y)