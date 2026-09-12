import ctypes
from ctypes import wintypes
import time
import win32clipboard
import win32con
import win32gui
from tools.os_controls import bring_hwnd_to_foreground

# --- Win32 Structures and Constants ---
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_A = 0x41
VK_BACK = 0x08
VK_V = 0x56

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulong),
    ]

class INPUT(ctypes.Structure):
    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUT_UNION),
    ]

# --- Helper Functions ---

def find_target_window(title_substrings=("Google Chrome", "Chrome"), class_name="Chrome_WidgetWin_1"):
    """Find the top-level visible window matching given class or title substrings."""
    target_hwnd = None

    def enum_cb(hwnd, _):
        nonlocal target_hwnd
        if target_hwnd is not None:
            return False
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            cname = win32gui.GetClassName(hwnd)
            if cname == class_name or any(sub in title for sub in title_substrings):
                if title:  # Filter out off-screen/invisible helper windows with empty titles
                    target_hwnd = hwnd
                    return False
        return True

    win32gui.EnumWindows(enum_cb, None)
    return target_hwnd

def wait_for_foreground(hwnd, timeout=1.0, interval=0.02):
    """Deterministically wait until target window gains foreground focus."""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        time.sleep(interval)
    return False

def make_cf_html(fragment: str) -> bytes:
    """Build compliant CF_HTML format byte payload."""
    marker_header = (
        "Version:0.9\r\n"
        "StartHTML:%010d\r\n"
        "EndHTML:%010d\r\n"
        "StartFragment:%010d\r\n"
        "EndFragment:%010d\r\n"
    )
    start_html_tag = "<html><body>\r\n<!--StartFragment-->"
    end_html_tag = "<!--EndFragment-->\r\n</body></html>"
    dummy = marker_header % (0, 0, 0, 0)
    
    start_html = len(dummy)
    start_frag = start_html + len(start_html_tag)
    end_frag = start_frag + len(fragment.encode("utf-8"))
    end_html = end_frag + len(end_html_tag.encode("utf-8"))
    
    header = marker_header % (start_html, end_html, start_frag, end_frag)
    return (header + start_html_tag + fragment + end_html_tag).encode("utf-8")

def set_clipboard_html_and_text(html_content: str, text_content: str, max_retries: int = 5):
    """Set HTML and plain text formats on Windows clipboard with retry logic."""
    cf_html = win32clipboard.RegisterClipboardFormat("HTML Format")
    html_payload = make_cf_html(html_content)

    for attempt in range(max_retries):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(cf_html, html_payload)
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text_content)
                return
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            if attempt == max_retries - 1:
                raise
            time.sleep(0.05)

def send_key_combo(vk_mod: int, vk_key: int):
    """Dispatch combined modifier + key down and up sequence via a single SendInput call."""
    inputs = (INPUT * 4)()
    inputs[0].type = INPUT_KEYBOARD
    inputs[0].union.ki.wVk = vk_mod
    inputs[1].type = INPUT_KEYBOARD
    inputs[1].union.ki.wVk = vk_key
    inputs[2].type = INPUT_KEYBOARD
    inputs[2].union.ki.wVk = vk_key
    inputs[2].union.ki.dwFlags = KEYEVENTF_KEYUP
    inputs[3].type = INPUT_KEYBOARD
    inputs[3].union.ki.wVk = vk_mod
    inputs[3].union.ki.dwFlags = KEYEVENTF_KEYUP
    ctypes.windll.user32.SendInput(4, ctypes.byref(inputs), ctypes.sizeof(INPUT))

def send_single_key(vk_key: int):
    """Dispatch single key down and up sequence."""
    inputs = (INPUT * 2)()
    inputs[0].type = INPUT_KEYBOARD
    inputs[0].union.ki.wVk = vk_key
    inputs[1].type = INPUT_KEYBOARD
    inputs[1].union.ki.wVk = vk_key
    inputs[1].union.ki.dwFlags = KEYEVENTF_KEYUP
    ctypes.windll.user32.SendInput(2, ctypes.byref(inputs), ctypes.sizeof(INPUT))

# --- Document Content ---
HTML_TABLE = """
<h1 style="font-family: Arial, sans-serif; color: #1a73e8; margin-bottom: 4px;">Competitive Meta Tier System Guide</h1>
<p style="font-family: Arial, sans-serif; color: #5f6368; font-size: 13px; margin-bottom: 16px;">
A comprehensive breakdown of competitive rankings from S-Tier down to F-Tier, detailing how specs and classes are evaluated in modern gaming metas (such as World of Warcraft Mythic+ and Raiding).
</p>

<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; font-family: Arial, sans-serif; font-size: 13px; border: 1px solid #dcdcdc;">
  <thead>
    <tr style="background-color: #202124; color: #ffffff; text-align: left;">
      <th style="padding: 10px; width: 10%; border: 1px solid #444;">Tier</th>
      <th style="padding: 10px; width: 20%; border: 1px solid #444;">Classification</th>
      <th style="padding: 10px; width: 35%; border: 1px solid #444;">Core Characteristics & Performance</th>
      <th style="padding: 10px; width: 20%; border: 1px solid #444;">Group Priority & Representation</th>
      <th style="padding: 10px; width: 15%; border: 1px solid #444;">Tuning State</th>
    </tr>
  </thead>
  <tbody>
    <tr style="background-color: #fdf2f2;">
      <td style="padding: 10px; font-weight: bold; color: #c5221f; border: 1px solid #e0e0e0; text-align: center; font-size: 15px;">S Tier</td>
      <td style="padding: 10px; font-weight: bold; color: #202124; border: 1px solid #e0e0e0;">Meta-Defining / Dominant</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Peak numerical output across single-target and multi-target; mandatory raid buffs or dungeon utility (e.g., hard stops, shroud, lust); virtually zero encounter weaknesses.</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Ubiquitous in World First race and title-range keys. Mandatory for bleeding-edge push groups.</td>
      <td style="padding: 10px; color: #c5221f; font-weight: bold; border: 1px solid #e0e0e0;">Overtuned / Top Priority Nerf Target</td>
    </tr>
    <tr style="background-color: #fef8f0;">
      <td style="padding: 10px; font-weight: bold; color: #e37400; border: 1px solid #e0e0e0; text-align: center; font-size: 15px;">A Tier</td>
      <td style="padding: 10px; font-weight: bold; color: #202124; border: 1px solid #e0e0e0;">Strong / Highly Competitive</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Exceptionally high damage and reliable survivability; capable of clearing the hardest content in the game without friction; only marginally behind S-tier in raw burst or utility.</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Heavily represented and warmly invited; easily matches S-tier output in capable hands.</td>
      <td style="padding: 10px; color: #e37400; font-weight: bold; border: 1px solid #e0e0e0;">Optimal / Healthy Benchmark</td>
    </tr>
    <tr style="background-color: #f6fbf4;">
      <td style="padding: 10px; font-weight: bold; color: #137333; border: 1px solid #e0e0e0; text-align: center; font-size: 15px;">B Tier</td>
      <td style="padding: 10px; font-weight: bold; color: #202124; border: 1px solid #e0e0e0;">Viable / Balanced</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Solid baseline throughput and straightforward playstyle, but lacks game-changing utility, target-uncapped burst, or exceptional self-sustain. (Where Fury Warrior commonly sits).</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Very common in standard groups, heroic/early mythic raids, and mid-range keys; clears all content fine.</td>
      <td style="padding: 10px; color: #137333; font-weight: bold; border: 1px solid #e0e0e0;">Balanced / Solid Baseline</td>
    </tr>
    <tr style="background-color: #f0f7fc;">
      <td style="padding: 10px; font-weight: bold; color: #1a73e8; border: 1px solid #e0e0e0; text-align: center; font-size: 15px;">C Tier</td>
      <td style="padding: 10px; font-weight: bold; color: #202124; border: 1px solid #e0e0e0;">Sub-Optimal / Niche</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Underperforming damage profile, mechanical friction in rotation, squishy defenses, or missing key stops needed for dungeon mechanics.</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Rarely prioritized in group finder; mostly played by dedicated spec specialists or casual groups.</td>
      <td style="padding: 10px; color: #1a73e8; font-weight: bold; border: 1px solid #e0e0e0;">Undertuned / Needs Number Buffs</td>
    </tr>
    <tr style="background-color: #f8f0fc;">
      <td style="padding: 10px; font-weight: bold; color: #9334e6; border: 1px solid #e0e0e0; text-align: center; font-size: 15px;">D Tier</td>
      <td style="padding: 10px; font-weight: bold; color: #202124; border: 1px solid #e0e0e0;">Underpowered / Struggling</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Substantially undertuned output, severe stat scaling issues, or survival flaws that make surviving high tyrannical/fortified spikes very difficult.</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Generally avoided; invites are rare without a pre-made guild group or high personal IO score.</td>
      <td style="padding: 10px; color: #9334e6; font-weight: bold; border: 1px solid #e0e0e0;">Poor Synergy / Needs Talent Changes</td>
    </tr>
    <tr style="background-color: #f1f3f4;">
      <td style="padding: 10px; font-weight: bold; color: #5f6368; border: 1px solid #e0e0e0; text-align: center; font-size: 15px;">F Tier</td>
      <td style="padding: 10px; font-weight: bold; color: #202124; border: 1px solid #e0e0e0;">Unviable / Broken</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Fundamentally broken talent interactions, dead abilities, or catastrophic damage output that puts the group at a severe mathematical disadvantage.</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Virtually zero competitive presence; viewed as an intentional handicap in competitive content.</td>
      <td style="padding: 10px; color: #5f6368; font-weight: bold; border: 1px solid #e0e0e0;">Rework Required / Bottom of Barrel</td>
    </tr>
  </tbody>
</table>
"""
FALLBACK_TEXT = "Competitive Meta Tier System Guide\nS Tier to F Tier"

def main():
    # 1. Preload clipboard before window activation to eliminate latency during key strokes
    set_clipboard_html_and_text(HTML_TABLE, FALLBACK_TEXT)

    # 2. Acquire target window
    hwnd = find_target_window()
    if not hwnd:
        raise RuntimeError("Target Chrome window not found.")

    # 3. Bring to foreground and await active focus deterministically
    bring_hwnd_to_foreground(hwnd)
    if not wait_for_foreground(hwnd):
        raise TimeoutError("Timed out waiting for Chrome to become the foreground window.")

    # 4. Select all, clear existing content, and paste formatted payload
    send_key_combo(VK_CONTROL, VK_A)
    time.sleep(0.04)
    send_single_key(VK_BACK)
    time.sleep(0.04)
    send_key_combo(VK_CONTROL, VK_V)

if __name__ == "__main__":
    main()