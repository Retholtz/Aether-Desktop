import ctypes
from ctypes import wintypes
import time
import win32clipboard
import win32con
import win32gui
import win32process

# Win32 INPUT structures for SendInput
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]

class INPUT(ctypes.Structure):
    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    _anonymous_ = ("_u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_u", _INPUT_UNION),
    ]

def send_paste_input():
    """Sends Ctrl+V atomically via a single SendInput call."""
    inputs = (INPUT * 4)(
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=0, time=0, dwExtraInfo=None)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_V, wScan=0, dwFlags=0, time=0, dwExtraInfo=None)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_V, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)),
    )
    ctypes.windll.user32.SendInput(4, ctypes.byref(inputs), ctypes.sizeof(INPUT))

def set_clipboard_html(html_body: str, plain_text_fallback: str = "") -> None:
    """Encodes and sets CF_HTML and Unicode text to clipboard with strict byte calculations."""
    marker_block = (
        "Version:0.9\r\n"
        "StartHTML:{:08d}\r\n"
        "EndHTML:{:08d}\r\n"
        "StartFragment:{:08d}\r\n"
        "EndFragment:{:08d}\r\n"
    )
    fragment_start_tag = "<!--StartFragment-->"
    fragment_end_tag = "<!--EndFragment-->"
    
    html_doc = (
        "<html>\r\n"
        "<body>\r\n"
        f"{fragment_start_tag}{html_body}{fragment_end_tag}\r\n"
        "</body>\r\n"
        "</html>"
    )
    
    # Calculate byte positions accurately for UTF-8 encoded payloads
    dummy_header_len = len(marker_block.format(0, 0, 0, 0).encode("utf-8"))
    doc_prefix = "<html>\r\n<body>\r\n"
    prefix_bytes = len(doc_prefix.encode("utf-8"))
    frag_tag_bytes = len(fragment_start_tag.encode("utf-8"))
    frag_bytes = len(html_body.encode("utf-8"))
    end_tag_bytes = len(fragment_end_tag.encode("utf-8"))
    doc_suffix_bytes = len("\r\n</body>\r\n</html>".encode("utf-8"))
    
    start_html = dummy_header_len
    start_fragment = start_html + prefix_bytes + frag_tag_bytes
    end_fragment = start_fragment + frag_bytes
    end_html = end_fragment + end_tag_bytes + doc_suffix_bytes
    
    header = marker_block.format(start_html, end_html, start_fragment, end_fragment)
    clipboard_data = (header + html_doc).encode("utf-8")
    
    cf_html = win32clipboard.RegisterClipboardFormat("HTML Format")
    
    # Deterministic clipboard opening with transient lock recovery
    for attempt in range(10):
        try:
            win32clipboard.OpenClipboard(0)
            break
        except Exception:
            if attempt == 9:
                raise
            time.sleep(0.01)
            
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(cf_html, clipboard_data)
        if plain_text_fallback:
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, plain_text_fallback)
    finally:
        win32clipboard.CloseClipboard()

def focus_target_window(title_substrings=("Chrome", "Docs"), timeout: float = 1.0) -> int:
    """Finds and activates the target window without arbitrary high-latency sleeps."""
    target_hwnd = None

    def enum_cb(hwnd, _):
        nonlocal target_hwnd
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if any(sub.lower() in title.lower() for sub in title_substrings):
                target_hwnd = hwnd
                return False
        return True

    try:
        win32gui.EnumWindows(enum_cb, None)
    except Exception:
        pass  # EnumWindows terminates early once false is returned

    if not target_hwnd:
        return 0

    cur_fg = win32gui.GetForegroundWindow()
    if cur_fg != target_hwnd:
        cur_thread = win32process.GetWindowThreadProcessId(cur_fg)[0]
        target_thread = win32process.GetWindowThreadProcessId(target_hwnd)[0]
        
        # Attach threads if needed to bypass foreground lock limits
        if cur_thread != target_thread:
            ctypes.windll.user32.AttachThreadInput(cur_thread, target_thread, True)
            win32gui.ShowWindow(target_hwnd, win32con.SW_SHOWMAXIMIZED)
            win32gui.SetForegroundWindow(target_hwnd)
            ctypes.windll.user32.AttachThreadInput(cur_thread, target_thread, False)
        else:
            win32gui.ShowWindow(target_hwnd, win32con.SW_SHOWMAXIMIZED)
            win32gui.SetForegroundWindow(target_hwnd)

        # Deterministic foreground sync
        end_time = time.perf_counter() + timeout
        while time.perf_counter() < end_time:
            if win32gui.GetForegroundWindow() == target_hwnd:
                break
            time.sleep(0.01)

    return target_hwnd

def paste_html_document(html_content: str, title_text: str = "", target_titles=("Chrome", "Docs")):
    set_clipboard_html(html_content, plain_text_fallback=title_text)
    hwnd = focus_target_window(title_substrings=target_titles)
    if hwnd:
        send_paste_input()
        return True
    return False

if __name__ == "__main__":
    content_html = """
<h1 style="font-family: Arial, sans-serif; color: #1a73e8; font-size: 24pt; margin-bottom: 4px;">Elite Dangerous: Rhino SRV Surface Mining Guide & Operations Sheet</h1>
<p style="font-family: Arial, sans-serif; color: #5f6368; font-size: 11pt; margin-top: 0px;">Operational Reference & Field Checklist | Rhino SRV Planetary Mining Update</p>
<hr style="border: 1px solid #dadce0; margin: 16px 0;" />

<h2 style="font-family: Arial, sans-serif; color: #202124; font-size: 16pt;">1. Required Equipment & Ship Loadout</h2>
<ul style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043;">
  <li><strong>Detailed Surface Scanner (DSS):</strong> Essential for mapping planetary bodies to locate high-density geological hotspots and mineral reserves.</li>
  <li><strong>Planetary Vehicle Hangar (Class 4+ recommended):</strong> Equipped with the new <strong>Rhino SRV</strong>. Class 4G/4H allows carrying a backup SRV.</li>
  <li><strong>Rhino SRV Configuration:</strong> Dual automated mining rig deployment bays, heavy extraction lasers, and reinforced cargo hold.</li>
  <li><strong>Synthesis Materials:</strong> Stock basic elements (Sulphur, Phosphorus, Iron, Nickel) for quick SRV fuel synthesis and hull repairs in deep field operations.</li>
</ul>

<h2 style="font-family: Arial, sans-serif; color: #202124; font-size: 16pt; margin-top: 20px;">2. Surface Mining Operational Workflow</h2>
<table border="1" cellpadding="8" cellspacing="0" style="font-family: Arial, sans-serif; font-size: 10.5pt; border-collapse: collapse; width: 100%; border-color: #dadce0;">
  <tr style="background-color: #1a73e8; color: #ffffff;">
    <th style="padding: 10px; text-align: left; width: 15%;">Phase</th>
    <th style="padding: 10px; text-align: left; width: 35%;">Action Items</th>
    <th style="padding: 10px; text-align: left; width: 50%;">Best Practices & Pro Tips</th>
  </tr>
  <tr style="background-color: #ffffff;">
    <td style="padding: 8px; font-weight: bold; color: #1a73e8;">Phase 1: Prospecting</td>
    <td style="padding: 8px;">Map target body with DSS & land near geological / mineral hotspots.</td>
    <td style="padding: 8px;">Target high-metal content or rocky bodies. Look for Tritium, Gold, Silver, Palladium, or active Community Goal minerals.</td>
  </tr>
  <tr style="background-color: #f8f9fa;">
    <td style="padding: 8px; font-weight: bold; color: #1a73e8;">Phase 2: Scouting</td>
    <td style="padding: 8px;">Deploy Rhino SRV and follow wave scanner signatures.</td>
    <td style="padding: 8px;">Higher audio pitch and upper-band radar signals point toward rich surface nodes and outcrop clusters.</td>
  </tr>
  <tr style="background-color: #ffffff;">
    <td style="padding: 8px; font-weight: bold; color: #1a73e8;">Phase 3: Rig Deployment</td>
    <td style="padding: 8px;">Position Rhino adjacent to node and deploy automated mining rig.</td>
    <td style="padding: 8px;">Ensure relatively flat terrain so rig secures properly. You can deploy multiple rigs simultaneously across nearby nodes.</td>
  </tr>
  <tr style="background-color: #f8f9fa;">
    <td style="padding: 8px; font-weight: bold; color: #1a73e8;">Phase 4: Extraction</td>
    <td style="padding: 8px;">Activate rig drilling cycle and monitor output.</td>
    <td style="padding: 8px;"><strong>Recent Buff:</strong> Rig extraction capacity is now boosted to <strong>12 chunks</strong> (up from 9). Stay within telemetry range.</td>
  </tr>
  <tr style="background-color: #ffffff;">
    <td style="padding: 8px; font-weight: bold; color: #1a73e8;">Phase 5: Collection</td>
    <td style="padding: 8px;">Open cargo scoop and collect dislodged chunks; stow rig.</td>
    <td style="padding: 8px;">Ferry full cargo back to the mothership cargo hold. Always pack up the reusable rig module before departing.</td>
  </tr>
</table>

<h2 style="font-family: Arial, sans-serif; color: #202124; font-size: 16pt; margin-top: 20px;">3. Key Tips, Best Practices & Hotfix Changes</h2>
<ul style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043;">
  <li><strong>Stagger Multiple Rigs:</strong> Deploy 3-4 rigs across adjacent nodes in a circuit. By the time you place the last rig, the first will have completed its 12-chunk cycle.</li>
  <li><strong>Low-G Thruster Control:</strong> In low-gravity environments (&lt; 0.2G), use downward vertical thrusters to keep your wheels planted when scooping chunks.</li>
  <li><strong>Community Goal Payout Boosts:</strong> Current CG (e.g. Metz Enterprise in Ega) offers up to 3x payout multipliers on specific surface-mined minerals (Bertrandite, Gallite, Indite).</li>
  <li><strong>Inventory Cycling:</strong> Keep your ship landed close to the cluster to minimize round-trip travel time when dumping cargo.</li>
</ul>
"""
    fallback_text = "Elite Dangerous: Rhino SRV Surface Mining Guide"
    paste_html_document(content_html, title_text=fallback_text)