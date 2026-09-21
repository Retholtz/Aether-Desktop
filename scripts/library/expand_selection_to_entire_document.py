import ctypes
from ctypes import wintypes
import time
import win32clipboard
import win32gui
from tools.os_controls import bring_hwnd_to_foreground

# --- Win32 Structures (64-bit aligned) ---
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_A = 0x41
VK_BACK = 0x08
VK_V = 0x56

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]

class INPUT(ctypes.Structure):
    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("ki", KEYBDINPUT),
            ("mi", MOUSEINPUT),
            ("hi", HARDWAREINPUT),
        ]
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUT_UNION),
    ]


def send_inputs(input_list: list[INPUT]) -> None:
    n_inputs = len(input_list)
    array_type = INPUT * n_inputs
    input_array = array_type(*input_list)
    ctypes.windll.user32.SendInput(n_inputs, ctypes.byref(input_array), ctypes.sizeof(INPUT))


def make_key_input(vk: int, flags: int = 0) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = vk
    inp.union.ki.wScan = 0
    inp.union.ki.dwFlags = flags
    inp.union.ki.time = 0
    inp.union.ki.dwExtraInfo = 0
    return inp


def send_key_combo(vk_mod: int, vk_key: int) -> None:
    send_inputs([
        make_key_input(vk_mod),
        make_key_input(vk_key),
        make_key_input(vk_key, KEYEVENTF_KEYUP),
        make_key_input(vk_mod, KEYEVENTF_KEYUP),
    ])


def send_single_key(vk_key: int) -> None:
    send_inputs([
        make_key_input(vk_key),
        make_key_input(vk_key, KEYEVENTF_KEYUP),
    ])


def make_cf_html(fragment: str) -> bytes:
    marker_header = (
        "Version:0.9\r\n"
        "StartHTML:%010d\r\n"
        "EndHTML:%010d\r\n"
        "StartFragment:%010d\r\n"
        "EndFragment:%010d\r\n"
    )
    start_tag = "<html><body>\r\n<!--StartFragment-->"
    end_tag = "<!--EndFragment-->\r\n</body></html>"

    dummy = marker_header % (0, 0, 0, 0)
    start_html = len(dummy)
    start_frag = start_html + len(start_tag)
    end_frag = start_frag + len(fragment.encode("utf-8"))
    end_html = end_frag + len(end_tag.encode("utf-8"))

    header = marker_header % (start_html, end_html, start_frag, end_frag)
    return (header + start_tag + fragment + end_tag).encode("utf-8")


def set_clipboard_html(html_fragment: str, retries: int = 5, retry_delay: float = 0.02) -> None:
    cf_html = win32clipboard.RegisterClipboardFormat("HTML Format")
    payload = make_cf_html(html_fragment)

    for attempt in range(retries):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(cf_html, payload)
                return
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(retry_delay)


def find_target_window(title_substring: str = "Chrome") -> int | None:
    found_hwnd = None

    def enum_cb(hwnd, _):
        nonlocal found_hwnd
        if win32gui.IsWindowVisible(hwnd):
            if title_substring in win32gui.GetWindowText(hwnd):
                found_hwnd = hwnd
                return False
        return True

    try:
        win32gui.EnumWindows(enum_cb, None)
    except Exception:
        pass
    return found_hwnd


def wait_for_foreground(hwnd: int, timeout_sec: float = 0.5) -> bool:
    start = time.perf_counter()
    while (time.perf_counter() - start) < timeout_sec:
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.01)
    return False


def replace_content_with_html(
    html_content: str,
    target_title_substring: str = "Chrome",
    select_all_count: int = 3,
    inter_action_delay: float = 0.04,
) -> None:
    # Preload clipboard before issuing window commands
    set_clipboard_html(html_content)

    # Focus target application
    hwnd = find_target_window(target_title_substring)
    if hwnd:
        bring_hwnd_to_foreground(hwnd)
        wait_for_foreground(hwnd)

    # Expand selection across container bounds (e.g. cell -> table -> document)
    for _ in range(select_all_count):
        send_key_combo(VK_CONTROL, VK_A)
        time.sleep(inter_action_delay)

    # Remove selected content and paste new payload
    send_single_key(VK_BACK)
    time.sleep(inter_action_delay)
    send_key_combo(VK_CONTROL, VK_V)


if __name__ == "__main__":
    HTML_TABLE_CONTENT = """
<h2>Competitive Meta Tier System Guide</h2>
<p>Understanding how tiers (from S-Tier down to F-Tier) represent performance, balance, and group priority in competitive gaming and World of Warcraft:</p>
<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; font-family: Arial, sans-serif;">
  <thead>
    <tr style="background-color: #e8eaed; font-weight: bold; text-align: left;">
      <th style="padding: 8px;">Tier</th>
      <th style="padding: 8px;">Classification</th>
      <th style="padding: 8px;">Core Characteristics & Performance</th>
      <th style="padding: 8px;">Group Priority & Representation</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td style="padding: 8px; font-weight: bold; color: #d93025;">S Tier</td>
      <td style="padding: 8px; font-weight: bold;">Meta-Defining / Dominant</td>
      <td style="padding: 8px;">Peak numerical output, mandatory utility (shroud, lust, mass stops), zero encounter weaknesses. Sets the benchmark for all high-end content.</td>
      <td style="padding: 8px;">Mandatory in bleeding-edge World First and title-range keys; auto-invite status.</td>
    </tr>
    <tr>
      <td style="padding: 8px; font-weight: bold; color: #e37400;">A Tier</td>
      <td style="padding: 8px; font-weight: bold;">Strong / Competitive</td>
      <td style="padding: 8px;">Very high damage output and strong survivability; fully capable of clearing the highest content; only marginally behind S-tier in raw burst or specific utility.</td>
      <td style="padding: 8px;">Heavily represented and warmly invited; easily matches S-tier output in skilled hands.</td>
    </tr>
    <tr>
      <td style="padding: 8px; font-weight: bold; color: #188038;">B Tier</td>
      <td style="padding: 8px; font-weight: bold;">Viable / Balanced</td>
      <td style="padding: 8px;">Solid baseline throughput and dependable playstyle, but lacks game-changing utility, target-uncapped burst, or unique stops. (Where Fury Warrior typically sits).</td>
      <td style="padding: 8px;">Very common; completely viable for all standard endgame content, though faces heavy competition in group finder.</td>
    </tr>
    <tr>
      <td style="padding: 8px; font-weight: bold; color: #1a73e8;">C Tier</td>
      <td style="padding: 8px; font-weight: bold;">Sub-Optimal / Niche</td>
      <td style="padding: 8px;">Underperforming damage profile, mechanical friction in rotation, or squishy defensive toolkit compared to higher tiers.</td>
      <td style="padding: 8px;">Rarely prioritized; primarily played by dedicated specialists, off-meta fans, or casual groups.</td>
    </tr>
    <tr>
      <td style="padding: 8px; font-weight: bold; color: #9334e6;">D Tier</td>
      <td style="padding: 8px; font-weight: bold;">Underpowered / Struggling</td>
      <td style="padding: 8px;">Noticeably undertuned numbers, severe stat scaling issues, or survival flaws that make surviving high-damage spikes very difficult.</td>
      <td style="padding: 8px;">Generally avoided in competitive play; requires pre-made groups or guild runs to push content.</td>
    </tr>
    <tr>
      <td style="padding: 8px; font-weight: bold; color: #5f6368;">F Tier</td>
      <td style="padding: 8px; font-weight: bold;">Unviable / Broken</td>
      <td style="padding: 8px;">Catastrophic damage output, broken talent synergies, or fundamental design flaws that actively handicap a group.</td>
      <td style="padding: 8px;">Virtually zero competitive presence; requires a full class rework or major balance overhaul.</td>
    </tr>
  </tbody>
</table>
"""
    replace_content_with_html(HTML_TABLE_CONTENT)
    print("Pasted fresh tier table successfully.")