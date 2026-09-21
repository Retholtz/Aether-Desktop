import concurrent.futures
import ctypes
from ctypes import wintypes
import io
import time
import urllib.request
from PIL import Image
import win32clipboard
import win32con
import win32gui

# ==============================================================================
# Native Windows SendInput Structures & Constants
# ==============================================================================

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

wintypes.ULONG_PTR = wintypes.WPARAM


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.ULONG_PTR),
    ]


class INPUT(ctypes.Structure):
    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    _anonymous_ = ("_input",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_input", _INPUT_UNION),
    ]


def _send_inputs(inputs: list[INPUT]) -> None:
    n_inputs = len(inputs)
    arr = (INPUT * n_inputs)(*inputs)
    ctypes.windll.user32.SendInput(n_inputs, ctypes.byref(arr), ctypes.sizeof(INPUT))


def send_key(vk: int) -> None:
    """Dispatches key-down and key-up atomically via SendInput."""
    _send_inputs([
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)),
    ])


def send_hotkey(mod: int, key: int) -> None:
    """Dispatches modifier + key press combination atomically via SendInput."""
    _send_inputs([
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=mod, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=key, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=key, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=mod, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)),
    ])


# ==============================================================================
# Window Management & Clipboard Utilities
# ==============================================================================

def focus_window_by_titles(match_titles: tuple[str, ...]) -> int | None:
    """Finds and foregrounds the target window deterministically."""
    target_hwnd = None

    def enum_cb(hwnd: int, _: None) -> bool:
        nonlocal target_hwnd
        if win32gui.IsWindowVisible(hwnd):
            txt = win32gui.GetWindowText(hwnd)
            if any(term in txt for term in match_titles):
                target_hwnd = hwnd
                return False
        return True

    try:
        win32gui.EnumWindows(enum_cb, None)
    except Exception:
        pass  # EnumWindows terminates early when matching window is found

    if target_hwnd:
        win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
        # Bypasses Windows foreground lock by momentarily toggling ALT if necessary
        try:
            win32gui.SetForegroundWindow(target_hwnd)
        except Exception:
            send_key(win32con.VK_MENU)
            win32gui.SetForegroundWindow(target_hwnd)
    return target_hwnd


def set_clipboard_data(format_id: int, data: bytes, fallback_text: str | None = None, retries: int = 5) -> None:
    """Atomically updates clipboard buffer with retry handling against OS locks."""
    for attempt in range(retries):
        try:
            win32clipboard.OpenClipboard(0)
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(format_id, data)
                if fallback_text:
                    win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, fallback_text)
                return
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(0.03)


def format_cf_html(html_body: str) -> bytes:
    """Packages HTML fragment according to standard Windows CF_HTML specification."""
    marker_block = (
        "Version:0.9\r\n"
        "StartHTML:{:08d}\r\n"
        "EndHTML:{:08d}\r\n"
        "StartFragment:{:08d}\r\n"
        "EndFragment:{:08d}\r\n"
    )
    fragment_start_tag = "<!--StartFragment-->"
    fragment_end_tag = "<!--EndFragment-->"

    html = (
        "<html>\r\n"
        "<body>\r\n"
        f"{fragment_start_tag}{html_body}{fragment_end_tag}\r\n"
        "</body>\r\n"
        "</html>"
    )

    dummy = marker_block.format(0, 0, 0, 0)
    start_html = len(dummy)
    end_html = start_html + len(html)
    start_fragment = start_html + html.find(fragment_start_tag) + len(fragment_start_tag)
    end_fragment = start_html + html.find(fragment_end_tag)

    header = marker_block.format(start_html, end_html, start_fragment, end_fragment)
    return (header + html).encode("utf-8")


def fetch_and_prepare_dib(url: str, target_width: int = 720) -> bytes:
    """Pre-fetches, scales, and prepares CF_DIB byte payload concurrently."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        image_data = resp.read()

    with Image.open(io.BytesIO(image_data)) as img:
        img = img.convert("RGB")
        w, h = img.size
        target_h = int(h * (target_width / w))
        img = img.resize((target_width, target_h), Image.Resampling.LANCZOS)

        with io.BytesIO() as out:
            img.save(out, format="BMP")
            # Strip the 14-byte BITMAPFILEHEADER to conform to CF_DIB structure
            return out.getvalue()[14:]


# ==============================================================================
# Document Templates & Configuration
# ==============================================================================

RHINO_IMG_URL = "https://image-service.zaonce.net/eyJidWNrZXQiOiJmcm9udGllci1jbXMiLCJrZXkiOiIyMDI2LTA5L3JoaW5vX2dhbGFjdGljXzE5MjB4MTA4MC5qcGciLCJlZGl0cyI6eyJ3ZWJwIjp7InF1YWxpdHkiOjg1fSwidG9Gb3JtYXQiOiJ3ZWJwIiwicmVzaXplIjp7ImZpdCI6ImNvbnRhaW4ifX19"
RIG_IMG_URL = "https://i.ytimg.com/vi/5p-OZ-jXDbU/maxresdefault.jpg"

HTML_HEADER = """
<h1 style="font-family: Arial, sans-serif; color: #1a73e8; font-size: 24pt; margin-bottom: 4px;">Elite Dangerous: Rhino SRV & Surface Mining Operations Manual</h1>
<p style="font-family: Arial, sans-serif; color: #5f6368; font-size: 11pt; margin-top: 0px;">Comprehensive Field Checklist, DSS Hotspot Identification & Extraction Workflow</p>
<hr style="border: 1px solid #dadce0; margin: 16px 0;" />

<h2 style="font-family: Arial, sans-serif; color: #202124; font-size: 16pt;">1. Required Equipment & Ship Loadout</h2>
<ul style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043;">
  <li><strong>Detailed Surface Scanner (DSS):</strong> Updated in the Rhino patch to detect <em>Planetary Mining Locations</em> directly from orbit and supercruise.</li>
  <li><strong>Mk II Large Planetary Vehicle Hangar:</strong> Carries the heavy <strong>Vodel Rhino SRV</strong> (Class 4+ bay; allows multi-crew and drops from ship underbelly).</li>
  <li><strong>Rhino SRV Modules:</strong> Equipped with built-in Planetary Mining Deposit Scanner, deployable automated mining rigs, internal refinery, and dual extraction hardpoints.</li>
  <li><strong>Synthesis Reserves:</strong> Keep Sulphur, Phosphorus, Iron, and Nickel stocked for on-the-fly SRV refuels and field hull repairs.</li>
</ul>

<h2 style="font-family: Arial, sans-serif; color: #202124; font-size: 16pt; margin-top: 20px;">2. Surface Mining Operational Workflow</h2>
<table border="1" cellpadding="8" cellspacing="0" style="font-family: Arial, sans-serif; font-size: 10.5pt; border-collapse: collapse; width: 100%; border-color: #dadce0;">
  <tr style="background-color: #1a73e8; color: #ffffff;">
    <th style="padding: 10px; text-align: left; width: 18%;">Phase</th>
    <th style="padding: 10px; text-align: left; width: 35%;">Action Items</th>
    <th style="padding: 10px; text-align: left; width: 47%;">Scanner & Operational Tips</th>
  </tr>
  <tr style="background-color: #ffffff;">
    <td style="padding: 8px; font-weight: bold; color: #1a73e8;">1. DSS Orbital Scan</td>
    <td style="padding: 8px;">Fire DSS probes to 100% map coverage on landable planet.</td>
    <td style="padding: 8px;">Toggle the new <strong>Planetary Mining Locations</strong> overlay. Scans highlight yellow/amber hotspot zones and register numbered Mining Signals in your Navigation panel.</td>
  </tr>
  <tr style="background-color: #f8f9fa;">
    <td style="padding: 8px; font-weight: bold; color: #1a73e8;">2. Signal Inspection</td>
    <td style="padding: 8px;">Target signal in orbital cruise / supercruise contacts list.</td>
    <td style="padding: 8px;">Your ship displays the specific mineral commodities present (e.g. Tritium, Iridium, Gold, Bertrandite). Select sites with multiple high-value resources.</td>
  </tr>
  <tr style="background-color: #ffffff;">
    <td style="padding: 8px; font-weight: bold; color: #1a73e8;">3. Surface Landing & Drop</td>
    <td style="padding: 8px;">Glide into the POI site and drop Rhino SRV from hover bay.</td>
    <td style="padding: 8px;">The site boundary displays as a distinctive yellow perimeter line (~5-6km radius) inside the Rhino cockpit HUD.</td>
  </tr>
  <tr style="background-color: #f8f9fa;">
    <td style="padding: 8px; font-weight: bold; color: #1a73e8;">4. Deposit Prospecting</td>
    <td style="padding: 8px;">Drive along terrain and ping the Planetary Mining Deposit Scanner.</td>
    <td style="padding: 8px;">Nodes illuminate in purple/colored markers indicating density (Low/Med/High). Large nodes support up to 6 deployable rigs simultaneously.</td>
  </tr>
  <tr style="background-color: #ffffff;">
    <td style="padding: 8px; font-weight: bold; color: #1a73e8;">5. Rig Deployment & Buff</td>
    <td style="padding: 8px;">Deploy Automated Mining Rigs directly over concentrated seams.</td>
    <td style="padding: 8px;"><strong>Hotfix Update:</strong> Each rig now extracts up to <strong>12 chunks</strong> (boosted from 9) before requiring collection, significantly improving profitability.</td>
  </tr>
  <tr style="background-color: #f8f9fa;">
    <td style="padding: 8px; font-weight: bold; color: #1a73e8;">6. Collection & Transfer</td>
    <td style="padding: 8px;">Roll over full rigs with cargo scoop open to extract refined units.</td>
    <td style="padding: 8px;">Transfer cargo directly from Rhino to ship without docking. Recover deployed rigs into SRV inventory before leaving the site.</td>
  </tr>
</table>

<h2 style="font-family: Arial, sans-serif; color: #202124; font-size: 16pt; margin-top: 24px;">3. Field Visual References: The Rhino SRV & Active Mining Rig Site</h2>
<p style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043;">
  <strong>Vehicle Profile:</strong> Vodel's heavy 6-wheel Rhino SRV deployed on planetary terrain:
</p>
"""

HTML_RIG_SECTION = """
<p style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043; margin-top: 16px;">
  <strong>Active Surface Mining Site:</strong> The new automated mining rig deployed on a designated mineral deposit seam, showing the extraction drilling loop and collection interface:
</p>
"""


# ==============================================================================
# Execution Pipeline
# ==============================================================================

def main() -> None:
    # 1. Concurrently pre-fetch and encode image assets ahead of UI manipulation
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_rhino = executor.submit(fetch_and_prepare_dib, RHINO_IMG_URL)
        f_rig = executor.submit(fetch_and_prepare_dib, RIG_IMG_URL)
        rhino_dib = f_rhino.result()
        rig_dib = f_rig.result()

    # 2. Acquire and focus target Docs / Chrome window
    hwnd = focus_window_by_titles(("Docs", "Chrome"))
    if not hwnd:
        raise RuntimeError("Google Docs / Chrome window was not found.")
    time.sleep(0.15)

    cf_html_format = win32clipboard.RegisterClipboardFormat("HTML Format")

    # 3. Select all and replace with Header HTML
    send_hotkey(win32con.VK_CONTROL, 0x41)  # Ctrl+A
    time.sleep(0.05)

    set_clipboard_data(
        cf_html_format,
        format_cf_html(HTML_HEADER),
        fallback_text="Elite Dangerous: Rhino SRV & Surface Mining Operations Manual",
    )
    send_hotkey(win32con.VK_CONTROL, 0x56)  # Ctrl+V
    time.sleep(0.25)

    # 4. Append Rhino SRV visual
    send_hotkey(win32con.VK_CONTROL, win32con.VK_END)
    send_key(win32con.VK_RETURN)
    set_clipboard_data(win32clipboard.CF_DIB, rhino_dib)
    send_hotkey(win32con.VK_CONTROL, 0x56)  # Ctrl+V
    time.sleep(0.35)

    # 5. Append intermediate section text
    send_hotkey(win32con.VK_CONTROL, win32con.VK_END)
    send_key(win32con.VK_RETURN)
    set_clipboard_data(
        cf_html_format,
        format_cf_html(HTML_RIG_SECTION),
        fallback_text="Active Surface Mining Site visual",
    )
    send_hotkey(win32con.VK_CONTROL, 0x56)  # Ctrl+V
    time.sleep(0.25)

    # 6. Append Mining Rig visual
    send_hotkey(win32con.VK_CONTROL, win32con.VK_END)
    send_key(win32con.VK_RETURN)
    set_clipboard_data(win32clipboard.CF_DIB, rig_dib)
    send_hotkey(win32con.VK_CONTROL, 0x56)  # Ctrl+V
    time.sleep(0.35)

    print("Document refreshed and populated with updated surface mining visuals!")


if __name__ == "__main__":
    main()