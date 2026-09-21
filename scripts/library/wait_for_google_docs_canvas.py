import ctypes
from ctypes import wintypes
import time
import win32clipboard
import win32con
import win32gui
from tools.os_controls import bring_hwnd_to_foreground

# --- Input / Windows Structures ---
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
        ("dwExtraInfo", ctypes.c_ulong)
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulong)
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD)
    ]

class INPUT(ctypes.Structure):
    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT)
        ]
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUT_UNION)
    ]

# --- Helper Functions ---

def send_inputs(input_list):
    n_inputs = len(input_list)
    array_type = INPUT * n_inputs
    ctypes.windll.user32.SendInput(
        n_inputs,
        array_type(*input_list),
        ctypes.sizeof(INPUT)
    )

def send_mouse_click():
    down = INPUT(type=INPUT_MOUSE)
    down.union.mi.dwFlags = MOUSEEVENTF_LEFTDOWN
    up = INPUT(type=INPUT_MOUSE)
    up.union.mi.dwFlags = MOUSEEVENTF_LEFTUP
    send_inputs([down, up])

def send_paste_combo():
    ctrl_down = INPUT(type=INPUT_KEYBOARD)
    ctrl_down.union.ki.wVk = VK_CONTROL

    v_down = INPUT(type=INPUT_KEYBOARD)
    v_down.union.ki.wVk = VK_V

    v_up = INPUT(type=INPUT_KEYBOARD)
    v_up.union.ki.wVk = VK_V
    v_up.union.ki.dwFlags = KEYEVENTF_KEYUP

    ctrl_up = INPUT(type=INPUT_KEYBOARD)
    ctrl_up.union.ki.wVk = VK_CONTROL
    ctrl_up.union.ki.dwFlags = KEYEVENTF_KEYUP

    send_inputs([ctrl_down, v_down, v_up, ctrl_up])

def find_target_window(title_substrings=("Google Docs", "Google Chrome", "Chrome")):
    found_hwnds = []

    def enum_windows_callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            window_text = win32gui.GetWindowText(hwnd)
            if any(sub in window_text for sub in title_substrings):
                found_hwnds.append(hwnd)
        return True

    win32gui.EnumWindows(enum_windows_callback, None)
    return found_hwnds[0] if found_hwnds else None

def wait_for_foreground(hwnd, timeout_sec=2.0):
    deadline = time.perf_counter() + timeout_sec
    while time.perf_counter() < deadline:
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.02)
    return False

def build_cf_html(fragment: str) -> bytes:
    marker_header = (
        "Version:0.9\r\n"
        "StartHTML:%010d\r\n"
        "EndHTML:%010d\r\n"
        "StartFragment:%010d\r\n"
        "EndFragment:%010d\r\n"
    )
    start_tag = "<html><body>\r\n<!--StartFragment-->"
    end_tag = "<!--EndFragment-->\r\n</body></html>"
    
    dummy_header = marker_header % (0, 0, 0, 0)
    start_html = len(dummy_header)
    start_fragment = start_html + len(start_tag)
    encoded_fragment = fragment.encode("utf-8")
    end_fragment = start_fragment + len(encoded_fragment)
    end_html = end_fragment + len(end_tag.encode("utf-8"))
    
    header = marker_header % (start_html, end_html, start_fragment, end_fragment)
    return (header + start_tag).encode("utf-8") + encoded_fragment + end_tag.encode("utf-8")

def set_clipboard_content(html_body: str, plain_text: str, max_retries=10, retry_delay=0.03):
    cf_html_format = win32clipboard.RegisterClipboardFormat("HTML Format")
    payload = build_cf_html(html_body)

    for attempt in range(max_retries):
        try:
            win32clipboard.OpenClipboard(0)
            break
        except Exception:
            if attempt == max_retries - 1:
                raise
            time.sleep(retry_delay)

    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(cf_html_format, payload)
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, plain_text)
    finally:
        win32clipboard.CloseClipboard()

def activate_and_paste(
    html_content: str,
    plain_text: str,
    target_point=(1280, 500),
    title_substrings=("Google Docs", "Google Chrome", "Chrome")
):
    set_clipboard_content(html_content, plain_text)

    hwnd = find_target_window(title_substrings)
    if not hwnd:
        raise RuntimeError("Target window not found.")

    bring_hwnd_to_foreground(hwnd)
    wait_for_foreground(hwnd, timeout_sec=1.5)

    # Click canvas target point to guarantee focus
    ctypes.windll.user32.SetCursorPos(target_point[0], target_point[1])
    send_mouse_click()

    # Small settle interval for browser event loop to focus the canvas element
    time.sleep(0.05)

    send_paste_combo()

if __name__ == "__main__":
    html_table = """
<h1 style="font-family: Arial, sans-serif; color: #1a73e8; margin-bottom: 6px;">Competitive Meta Tier System Guide</h1>
<p style="font-family: Arial, sans-serif; color: #5f6368; font-size: 14px; margin-bottom: 18px;">
An overview of competitive tier rankings from S-Tier down to F-Tier, detailing how specs and classes are evaluated in modern gaming metas (such as World of Warcraft Mythic+ and Raiding).
</p>
<table border="1" cellpadding="10" cellspacing="0" style="border-collapse: collapse; width: 100%; font-family: Arial, sans-serif; font-size: 13px; border: 1px solid #dadce0;">
  <thead>
    <tr style="background-color: #202124; color: #ffffff; text-align: left;">
      <th style="padding: 10px; width: 10%; border: 1px solid #444;">Tier</th>
      <th style="padding: 10px; width: 22%; border: 1px solid #444;">Classification</th>
      <th style="padding: 10px; width: 38%; border: 1px solid #444;">Core Characteristics & Performance</th>
      <th style="padding: 10px; width: 30%; border: 1px solid #444;">Group Priority & Representation</th>
    </tr>
  </thead>
  <tbody>
    <tr style="background-color: #fdf2f2;">
      <td style="padding: 10px; font-weight: bold; color: #c5221f; border: 1px solid #e0e0e0; text-align: center; font-size: 15px;">S Tier</td>
      <td style="padding: 10px; font-weight: bold; color: #202124; border: 1px solid #e0e0e0;">Meta-Defining / Dominant</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Peak numerical output across both single-target and multi-target; brings mandatory group buffs or essential dungeon utility (e.g., hard stops, shroud, lust); virtually zero encounter weaknesses. Sets the benchmark for all classes.</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Ubiquitous in World First progression and title-range keys; auto-invite status across competitive groups.</td>
    </tr>
    <tr style="background-color: #fef8f0;">
      <td style="padding: 10px; font-weight: bold; color: #e37400; border: 1px solid #e0e0e0; text-align: center; font-size: 15px;">A Tier</td>
      <td style="padding: 10px; font-weight: bold; color: #202124; border: 1px solid #e0e0e0;">Strong / Highly Competitive</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Exceptionally high damage and dependable defensives; fully capable of clearing the highest content without friction; only marginally behind S-tier in raw burst or specific utility.</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Heavily represented and warmly invited; easily matches S-tier output in the hands of skilled players.</td>
    </tr>
    <tr style="background-color: #f6fbf4;">
      <td style="padding: 10px; font-weight: bold; color: #137333; border: 1px solid #e0e0e0; text-align: center; font-size: 15px;">B Tier</td>
      <td style="padding: 10px; font-weight: bold; color: #202124; border: 1px solid #e0e0e0;">Viable / Balanced</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Solid baseline throughput and straightforward playstyle, but lacks game-changing utility, target-uncapped burst, or unique stops. (Where Fury Warrior commonly sits).</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Very common; completely viable for all standard endgame content, though faces heavy competition in the group finder.</td>
    </tr>
    <tr style="background-color: #f0f7fc;">
      <td style="padding: 10px; font-weight: bold; color: #1a73e8; border: 1px solid #e0e0e0; text-align: center; font-size: 15px;">C Tier</td>
      <td style="padding: 10px; font-weight: bold; color: #202124; border: 1px solid #e0e0e0;">Sub-Optimal / Niche</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Underperforming damage profile, mechanical friction in rotation, squishy defensives, or missing key stops needed for dungeon mechanics.</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Rarely prioritized in group finder; mostly played by dedicated specialists, off-meta fans, or casual groups.</td>
    </tr>
    <tr style="background-color: #f8f0fc;">
      <td style="padding: 10px; font-weight: bold; color: #9334e6; border: 1px solid #e0e0e0; text-align: center; font-size: 15px;">D Tier</td>
      <td style="padding: 10px; font-weight: bold; color: #202124; border: 1px solid #e0e0e0;">Underpowered / Struggling</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Noticeably undertuned numbers, severe stat scaling issues, or survival flaws that make surviving high-damage spikes very difficult.</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Generally avoided in competitive play; requires pre-made groups or guild runs to push content.</td>
    </tr>
    <tr style="background-color: #f1f3f4;">
      <td style="padding: 10px; font-weight: bold; color: #5f6368; border: 1px solid #e0e0e0; text-align: center; font-size: 15px;">F Tier</td>
      <td style="padding: 10px; font-weight: bold; color: #202124; border: 1px solid #e0e0e0;">Unviable / Broken</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Catastrophic damage output, broken talent synergies, or fundamental design flaws that actively handicap a group.</td>
      <td style="padding: 10px; color: #3c4043; border: 1px solid #e0e0e0;">Virtually zero competitive presence; requires a full class rework or major balance overhaul.</td>
    </tr>
  </tbody>
</table>
"""
    plain_summary = "Competitive Meta Tier System Guide\nS Tier down to F Tier"
    activate_and_paste(html_table, plain_summary, target_point=(1280, 500))
    print("Successfully pasted fresh styled tier table!")