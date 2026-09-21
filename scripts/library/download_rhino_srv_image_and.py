import ctypes
from ctypes import wintypes
import time
import win32clipboard
import win32con
import win32gui

MANUAL_TITLE = "Vodel Rhino Heavy SRV: Complete Operations & Field Instruction Manual"
TARGET_WINDOW_KEYWORDS = ("Chrome", "Docs")

MANUAL_HTML = """
<h1 style="font-family: Arial, sans-serif; color: #1a73e8; font-size: 26pt; margin-bottom: 4px;">Vodel Rhino Heavy SRV: Complete Operations & Field Instruction Manual</h1>
<p style="font-family: Arial, sans-serif; color: #5f6368; font-size: 11pt; margin-top: 0px; font-weight: bold;">
  STANDARD OPERATING PROCEDURES (SOP) | SURFACE MINING, TELEMETRY & RIG EXTRACTION
</p>
<hr style="border: 1px solid #dadce0; margin: 18px 0;" />

<h2 style="font-family: Arial, sans-serif; color: #202124; font-size: 16pt;">1. Vehicle Architecture & Mothership Outfitting</h2>
<p style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043;">
  The <strong>Vodel Rhino</strong> is a heavy industrial 6-wheel planetary surface vehicle designed specifically for automated sub-surface and outcrop mining. Due to its reinforced chassis and industrial payload, it operates under distinct outfitting parameters compared to the lighter Scarab and Scorpion reconnaissance vehicles.
</p>
<ul style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043;">
  <li><strong>Hangar Module Requirement:</strong> Requires the new <strong>Mk II Large Planetary Vehicle Hangar (LPVH)</strong> (Class 4 minimum; Class 6 recommended for multi-bay loadouts). Standard Mk I hangars cannot accommodate the Rhino's expanded footprint.</li>
  <li><strong>Hangar Bay Co-Existence:</strong> Mk II hangars support full multi-bay interoperability. A Class 6 Mk II hangar can simultaneously host a <em>Rhino</em> alongside a combat-spec <em>Scorpion</em> or survey <em>Scarab</em> without restriction.</li>
  <li><strong>Cockpit & Multi-Crew Configuration:</strong> Features dual tandem seating. A second commander or telepresence crew member can operate turret targeting, scanner pings, and rig diagnostics while the driver maneuvers.</li>
  <li><strong>Payload Systems:</strong> Outfitted with dual Automated Mining Rig deployment racks, high-output mining laser emitters, an integrated Planetary Mining Deposit Scanner, and a high-capacity magnetized cargo scoop.</li>
</ul>

<h2 style="font-family: Arial, sans-serif; color: #202124; font-size: 16pt; margin-top: 24px;">2. Comprehensive Phase-by-Phase Operational Workflow</h2>

<h3 style="font-family: Arial, sans-serif; color: #1a73e8; font-size: 13pt;">Phase 1: Orbital Reconnaissance & DSS Probing</h3>
<p style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043;">
  1. Approach the target landable planet in Supercruise and switch cockpit mode to <strong>Analysis Mode</strong> (HUD turns blue).<br />
  2. Throttle to zero and fire Detailed Surface Scanner (DSS) probes to achieve 100% planetary surface mapping efficiency.<br />
  3. Toggle the scanner mode to <strong>Planetary Mining Locations</strong>. High-density resource fields appear as distinctive <strong>yellow/amber heatmap zones</strong> on the planetary globe.<br />
  4. Inspect the Navigation panel: new numbered entries tagged <em>"Planetary Mining Location Signal [X]"</em> will appear. Target signals that list high-demand commodities (e.g., Tritium, Palladium, Gold, Silver, Bertrandite).
</p>

<h3 style="font-family: Arial, sans-serif; color: #1a73e8; font-size: 13pt;">Phase 2: Orbital Descent & Mothership Positioning</h3>
<p style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043;">
  1. Lock onto the designated Mining Signal and align your entry trajectory for Orbital Cruise (entry pitch between -5° and -30°).<br />
  2. Complete the Glide phase directly above the POI coordinates.<br />
  3. <strong>Strategic Mothership Landing:</strong> Set your ship down approximately <strong>300 to 500 meters</strong> from the edge of the resource cluster. Parking too far creates excessive driving transit times during cargo transfers; parking directly on deposits can obstruct deposit scanner lines.<br />
  4. Access the lower crotch interface (Role Panel - Key 3) and deploy the Rhino SRV via the ship's ventral drop bay.
</p>

<h3 style="font-family: Arial, sans-serif; color: #1a73e8; font-size: 13pt;">Phase 3: Surface Prospection & Deposit Classification</h3>
<p style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043;">
  1. Upon SRV touchdown, observe the HUD boundary: the site is enclosed within a <strong>yellow boundary line</strong> spanning a 5–6 km radius.<br />
  2. Activate the <strong>Planetary Mining Deposit Scanner</strong> (2 km active pulse ping).<br />
  3. Observe terrain telemetry: viable mineral seams illuminate with vibrant <strong>purple/magenta circular outline rings</strong>.<br />
  4. Target the seam node with the turret or forward reticle to read deposit density (<em>Low</em>, <em>Medium</em>, <em>High</em>). High-yield nodes provide deeper reserves and can accommodate up to 6 simultaneous mining rigs.
</p>

<h3 style="font-family: Arial, sans-serif; color: #1a73e8; font-size: 13pt;">Phase 4: Automated Rig Deployment & Extraction Cycling</h3>
<p style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043;">
  1. Drive the Rhino directly into the purple target circle and bring the chassis to a complete stop on level terrain.<br />
  2. Trigger the rig deployment sequence from the cockpit weapon group or auxiliary module menu.<br />
  3. The automated rig extends stabilizing hydraulic outriggers, locks into the bedrock, and initiates deep sub-surface sonic drilling.<br />
  4. <strong>Hotfix Extraction Yield:</strong> Each automated rig now extracts up to <strong>12 mineral chunks</strong> per cycle (increased from 9 in the latest balance update).<br />
  5. The rig HUD indicates drilling percentage, chunk reservoir status, and power stability. Remain within telemetry range (within 3 km) while drilling progresses.
</p>

<h3 style="font-family: Arial, sans-serif; color: #1a73e8; font-size: 13pt;">Phase 5: The "Circuit Staggering" Multi-Rig Optimization Meta</h3>
<p style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043;">
  To achieve maximum metric tons per hour, never wait beside a single drilling rig. Deploy in a rolling circuit:
</p>
<ul style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043;">
  <li><strong>Node A (Rig 1):</strong> Deploy Rig 1 on high-density seam &rarr; immediately drive to Node B.</li>
  <li><strong>Node B (Rig 2):</strong> Deploy Rig 2 &rarr; drive to Node C.</li>
  <li><strong>Node C (Rig 3):</strong> Deploy Rig 3 &rarr; drive to Node D.</li>
  <li><strong>Loop Harvest:</strong> By the time Rig 3 or 4 is operational, Rig 1 will have completed its full 12-chunk extraction. Return to Rig 1, harvest, pack up, and leapfrog to the next node. This creates zero operational downtime.</li>
</ul>

<h3 style="font-family: Arial, sans-serif; color: #1a73e8; font-size: 13pt;">Phase 6: Cargo Collection, Mothership Transfer, & Rig Recovery</h3>
<p style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043;">
  1. Approach the completed rig, lower the Rhino's magnetized cargo scoop (Home key by default), and scoop the ejected high-purity ore chunks.<br />
  2. <strong>Low-Gravity Thruster Discipline:</strong> On planets with gravity below 0.2G, utilize the Rhino's downward vertical thrusters to prevent floating and bouncing over loose chunks.<br />
  3. Once collection is complete, target the rig and execute <em>Module Recall</em> to stow the reusable rig back into the Rhino's deployer bay.<br />
  4. <strong>Remote Mothership Transfer:</strong> When the Rhino's internal cargo hold reaches capacity, drive directly beneath the mothership's cargo hatch. Use the inventory transfer UI to offload ore straight into the ship's main cargo racks without docking the SRV!
</p>

<h2 style="font-family: Arial, sans-serif; color: #202124; font-size: 16pt; margin-top: 24px;">3. Quick-Action Operational Reference Matrix</h2>
<table border="1" cellpadding="8" cellspacing="0" style="font-family: Arial, sans-serif; font-size: 10pt; border-collapse: collapse; width: 100%; border-color: #dadce0;">
  <tr style="background-color: #1a73e8; color: #ffffff;">
    <th style="padding: 10px; text-align: left; width: 15%;">Operational Phase</th>
    <th style="padding: 10px; text-align: left; width: 25%;">Primary Action Items</th>
    <th style="padding: 10px; text-align: left; width: 30%;">Scanner & HUD Telemetry</th>
    <th style="padding: 10px; text-align: left; width: 30%;">Failure Safeguards & Pro Tips</th>
  </tr>
  <tr style="background-color: #ffffff;">
    <td style="padding: 8px; font-weight: bold; color: #1a73e8;">1. Orbital DSS</td>
    <td style="padding: 8px;">Map planet with probes at zero throttle; toggle Mining overlay.</td>
    <td style="padding: 8px;"><strong>Yellow/Amber</strong> spherical heatzones; numbered Mining Signals in Nav panel.</td>
    <td style="padding: 8px;">Inspect contacts list to confirm commodity types before initiating orbital drop.</td>
  </tr>
  <tr style="background-color: #f8f9fa;">
    <td style="padding: 8px; font-weight: bold; color: #1a73e8;">2. Glide & Landing</td>
    <td style="padding: 8px;">Glide into signal; set mothership down 300-500m from cluster.</td>
    <td style="padding: 8px;">Glide reticle & pitch ladder; terrain elevation contour scanner.</td>
    <td style="padding: 8px;">Avoid landing on active seams. Deploy Rhino directly from ventral bay.</td>
  </tr>
  <tr style="background-color: #ffffff;">
    <td style="padding: 8px; font-weight: bold; color: #1a73e8;">3. Surface Prospection</td>
    <td style="padding: 8px;">Trigger Deposit Scanner pulse; target nodes for density analysis.</td>
    <td style="padding: 8px;">Yellow 5-6km outer site boundary; <strong>Purple/Magenta</strong> seam outline rings.</td>
    <td style="padding: 8px;">Focus on High-density seams first to maximize rig saturation (up to 6 rigs).</td>
  </tr>
  <tr style="background-color: #f8f9fa;">
    <td style="padding: 8px; font-weight: bold; color: #1a73e8;">4. Rig Deployment</td>
    <td style="padding: 8px;">Stop inside purple ring; fire rig deployer; engage drilling cycle.</td>
    <td style="padding: 8px;">Rig deployment icon; audio lock confirmation; drilling progress % gauge.</td>
    <td style="padding: 8px;">Ensure chassis is stabilized. Do not deploy rigs on severe inclines (&gt;45°).</td>
  </tr>
  <tr style="background-color: #ffffff;">
    <td style="padding: 8px; font-weight: bold; color: #1a73e8;">5. Extraction Circuit</td>
    <td style="padding: 8px;">Maintain rolling 3-4 rig circuit; harvest full rigs (12 chunks each).</td>
    <td style="padding: 8px;">Rig status alerts (Active, Full, Depleted); chunk count readout (X/12).</td>
    <td style="padding: 8px;">Never wait idle at one rig; cycle between deployed rigs to maximize yields.</td>
  </tr>
  <tr style="background-color: #f8f9fa;">
    <td style="padding: 8px; font-weight: bold; color: #1a73e8;">6. Hauling & Stowage</td>
    <td style="padding: 8px;">Collect chunks with cargo scoop; pack up rigs; offload to ship.</td>
    <td style="padding: 8px;">Cargo scoop crosshair; inventory capacity gauge; ship proximity beacon.</td>
    <td style="padding: 8px;">Use downward thrusters in &lt;0.2G. Never depart surface without stowing reusable rigs.</td>
  </tr>
</table>

<h2 style="font-family: Arial, sans-serif; color: #202124; font-size: 16pt; margin-top: 24px;">4. Field Synthesis, Maintenance, & Anarchy Threat Protocols</h2>
<ul style="font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #3c4043;">
  <li><strong>Field Refueling:</strong> Synthesize SRV fuel on the move using 1x Sulphur + 1x Phosphorus via the Inventory &rarr; Synthesis panel to prevent engine power failure in deep field expeditions.</li>
  <li><strong>Chassis Hull Repairs:</strong> Synthesize SRV structural repairs using 2x Iron + 1x Nickel when traversing sharp geological ridges or high-G drop-offs.</li>
  <li><strong>Hostile & Anarchy System Security:</strong> In lawless systems or during contested Community Goals, deploy with a combat-fitted <strong>Scorpion SRV</strong> in your secondary Mk II hangar bay. In multi-crew, keep one player in the turreted Rhino or Scorpion to deter hostile pirate NPC spawns and ground raiders.</li>
  <li><strong>Economic Maximization:</strong> Track ongoing trade initiatives and Community Goals (such as current Wreaken testing operations) to take advantage of boosted payout multipliers (e.g., 2x on Silver, 3x on Bertrandite, Indite, and Gallite).</li>
</ul>
"""


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class INPUT(ctypes.Structure):
    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    _anonymous_ = ("_union",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_union", _INPUT_UNION),
    ]


def build_cf_html_payload(html_fragment: str) -> bytes:
    """Encodes an HTML fragment with standard CF_HTML descriptor offsets."""
    marker_block = (
        "Version:0.9\r\n"
        "StartHTML:{:08d}\r\n"
        "EndHTML:{:08d}\r\n"
        "StartFragment:{:08d}\r\n"
        "EndFragment:{:08d}\r\n"
    )
    fragment_start_tag = "<!--StartFragment-->"
    fragment_end_tag = "<!--EndFragment-->"
    full_html = (
        f"<html>\r\n<body>\r\n{fragment_start_tag}{html_fragment}{fragment_end_tag}\r\n</body>\r\n</html>"
    )

    dummy_header = marker_block.format(0, 0, 0, 0)
    start_html = len(dummy_header)
    end_html = start_html + len(full_html)
    start_fragment = start_html + full_html.find(fragment_start_tag) + len(fragment_start_tag)
    end_fragment = start_html + full_html.find(fragment_end_tag)

    header = marker_block.format(start_html, end_html, start_fragment, end_fragment)
    return (header + full_html).encode("utf-8")


def set_clipboard_data(html_fragment: str, plain_text: str, max_retries: int = 5) -> None:
    """Sets HTML and Unicode formats into the Windows clipboard with retry logic."""
    cf_html_format = win32clipboard.RegisterClipboardFormat("HTML Format")
    payload = build_cf_html_payload(html_fragment)

    for attempt in range(max_retries):
        try:
            win32clipboard.OpenClipboard(0)
            break
        except Exception:
            if attempt == max_retries - 1:
                raise
            time.sleep(0.05)

    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(cf_html_format, payload)
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, plain_text)
    finally:
        win32clipboard.CloseClipboard()


def focus_window_by_keywords(keywords: tuple[str, ...], timeout: float = 1.0) -> int:
    """Locates and forces the target window into the foreground deterministically."""
    target_hwnd = None

    def enum_windows_callback(hwnd: int, _: None) -> None:
        nonlocal target_hwnd
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if any(k.lower() in title.lower() for k in keywords):
                target_hwnd = hwnd

    win32gui.EnumWindows(enum_windows_callback, None)
    if not target_hwnd:
        raise RuntimeError(f"No active window matched criteria: {keywords}")

    current_foreground = win32gui.GetForegroundWindow()
    if current_foreground == target_hwnd:
        return target_hwnd

    foreground_thread = ctypes.windll.user32.GetWindowThreadProcessId(current_foreground, None)
    target_thread = ctypes.windll.kernel32.GetCurrentThreadId()

    attached = False
    if foreground_thread and foreground_thread != target_thread:
        attached = bool(ctypes.windll.user32.AttachThreadInput(target_thread, foreground_thread, True))

    try:
        if win32gui.IsIconic(target_hwnd):
            win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(target_hwnd, win32con.SW_SHOW)

        win32gui.BringWindowToTop(target_hwnd)
        win32gui.SetForegroundWindow(target_hwnd)
    finally:
        if attached:
            ctypes.windll.user32.AttachThreadInput(target_thread, foreground_thread, False)

    start_time = time.perf_counter()
    while time.perf_counter() - start_time < timeout:
        if win32gui.GetForegroundWindow() == target_hwnd:
            return target_hwnd
        time.sleep(0.01)

    return target_hwnd


def send_paste_input() -> None:
    """Dispatches atomic Ctrl+V key combination via SendInput."""
    vk_control = 0x11
    vk_v = 0x56
    input_type_keyboard = 1
    flag_keyup = 0x0002

    inputs = (INPUT * 4)(
        INPUT(type=input_type_keyboard, ki=KEYBDINPUT(wVk=vk_control)),
        INPUT(type=input_type_keyboard, ki=KEYBDINPUT(wVk=vk_v)),
        INPUT(type=input_type_keyboard, ki=KEYBDINPUT(wVk=vk_v, dwFlags=flag_keyup)),
        INPUT(type=input_type_keyboard, ki=KEYBDINPUT(wVk=vk_control, dwFlags=flag_keyup)),
    )
    sent = ctypes.windll.user32.SendInput(4, ctypes.byref(inputs), ctypes.sizeof(INPUT))
    if sent != 4:
        raise RuntimeError(f"SendInput failed: sent {sent} of 4 events.")


def main() -> None:
    set_clipboard_data(MANUAL_HTML, MANUAL_TITLE)
    focus_window_by_keywords(TARGET_WINDOW_KEYWORDS)
    send_paste_input()
    print("Pasted comprehensive Rhino manual into new Google Doc!")


if __name__ == "__main__":
    main()