import time
import ctypes
import win32gui
import win32clipboard
import win32con
from tools.os_controls import bring_hwnd_to_foreground

# --- Constants ---
VK_CONTROL = 0x11
VK_BACK = 0x08
VK_A = 0x41
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002

USER32 = ctypes.windll.user32

DEFAULT_HTML_CONTENT = """
<div style="font-family: Arial, sans-serif; line-height: 1.6; color: #202124;">
  <h1 style="color: #1a73e8; font-size: 22pt; margin-bottom: 8px;">Comprehensive Clinical Comparison: Xarelto (Rivaroxaban) vs. Eliquis (Apixaban)</h1>
  <p style="font-size: 11pt; color: #5f6368; margin-top: 0;">An in-depth evaluation of pharmacodynamics, clinical efficacy, bleeding risks, and practical pros &amp; cons of Direct Oral Anticoagulants (DOACs).</p>
  <hr style="border: none; border-top: 1px solid #dadce0; margin: 16px 0;" />
  <h2 style="color: #202124; font-size: 15pt; margin-top: 16px;">1. Head-to-Head Comparison Summary</h2>
  <table style="width: 100%; border-collapse: collapse; margin-top: 10px; margin-bottom: 20px; font-size: 10pt;">
    <thead>
      <tr style="background-color: #f1f3f4; text-align: left;">
        <th style="padding: 10px 12px; border: 1px solid #dadce0; width: 24%;">Feature / Clinical Parameter</th>
        <th style="padding: 10px 12px; border: 1px solid #dadce0; width: 38%; color: #174ea6;">Xarelto (Rivaroxaban)</th>
        <th style="padding: 10px 12px; border: 1px solid #dadce0; width: 38%; color: #174ea6;">Eliquis (Apixaban)</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td style="padding: 8px 12px; border: 1px solid #dadce0; font-weight: bold;">Drug Class &amp; Target</td>
        <td style="padding: 8px 12px; border: 1px solid #dadce0;">Direct Factor Xa Inhibitor</td>
        <td style="padding: 8px 12px; border: 1px solid #dadce0;">Direct Factor Xa Inhibitor</td>
      </tr>
      <tr style="background-color: #fafafa;">
        <td style="padding: 8px 12px; border: 1px solid #dadce0; font-weight: bold;">Standard Dosing Frequency</td>
        <td style="padding: 8px 12px; border: 1px solid #dadce0;"><strong>Once daily (QD)</strong></td>
        <td style="padding: 8px 12px; border: 1px solid #dadce0;"><strong>Twice daily (BID)</strong></td>
      </tr>
    </tbody>
  </table>
  <h2 style="color: #202124; font-size: 15pt; margin-top: 20px;">4. Summary &amp; Clinical Recommendation</h2>
  <p style="font-size: 10.5pt; line-height: 1.6;">For the majority of patients with nonvalvular atrial fibrillation or venous thromboembolism, <strong>Eliquis (apixaban)</strong> is widely considered the preferred first-line agent due to its superior safety margin and lower bleeding incidence. <strong>Xarelto (rivaroxaban)</strong> remains an outstanding alternative when once-daily simplicity is critical for patient adherence or when treatment involves chronic CAD or PAD vascular protection.</p>
</div>
"""

# --- Helper Functions ---
def send_key(vk_code, down=True):
    """Sends a low-level keyboard event."""
    flags = 0 if down else KEYEVENTF_KEYUP
    USER32.keybd_event(vk_code, 0, flags, 0)

def send_hotkey(vk_modifier, vk_key, delay=0.02):
    """Executes a modifier+key hotkey with minimal deterministic delays."""
    send_key(vk_modifier, True)
    time.sleep(delay)
    send_key(vk_key, True)
    time.sleep(delay)
    send_key(vk_key, False)
    send_key(vk_modifier, False)
    time.sleep(delay)

def set_clipboard_html(html_text, fallback_text="HTML Content"):
    """Formats and sets HTML content to the Windows clipboard safely."""
    cf_html = win32clipboard.RegisterClipboardFormat("HTML Format")
    
    header_template = (
        "Version:0.9\r\n"
        "StartHTML:{:08d}\r\n"
        "EndHTML:{:08d}\r\n"
        "StartFragment:{:08d}\r\n"
        "EndFragment:{:08d}\r\n"
    )
    
    dummy_header = header_template.format(0, 0, 0, 0)
    start_html = len(dummy_header)
    start_frag = start_html + len("<html><body><!--StartFragment-->")
    
    encoded_html = html_text.encode('utf-8')
    end_frag = start_frag + len(encoded_html)
    end_html = end_frag + len("<!--EndFragment--></body></html>")
    
    full_header = header_template.format(start_html, end_html, start_frag, end_frag)
    full_data = f"{full_header}<html><body><!--StartFragment-->{html_text}<!--EndFragment--></body></html>"
    
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(cf_html, full_data.encode('utf-8'))
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, fallback_text)
    finally:
        win32clipboard.CloseClipboard()

def get_target_hwnd(target_titles):
    """Finds the first visible window matching any of the target titles."""
    found_hwnd = None
    def enum_cb(hwnd, _):
        nonlocal found_hwnd
        if found_hwnd:
            return
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if any(t in title for t in target_titles):
                found_hwnd = hwnd
                
    win32gui.EnumWindows(enum_cb, None)
    return found_hwnd

# --- Main Execution ---
def update_google_docs(html_content=DEFAULT_HTML_CONTENT, target_titles=("Google Docs", "Chrome")):
    """Clears the target document and pastes the provided HTML content."""
    hwnd = get_target_hwnd(target_titles)
    if not hwnd:
        print("Target window not found.")
        return False

    # 1. Activate Window
    bring_hwnd_to_foreground(hwnd)
    time.sleep(0.15)  # Deterministic wait for OS window activation

    # 2. Clear existing content (Ctrl+A, Backspace)
    send_hotkey(VK_CONTROL, VK_A)
    send_key(VK_BACK, True)
    send_key(VK_BACK, False)
    time.sleep(0.05)  # Brief wait for UI to process deletion

    # 3. Prepare Clipboard
    set_clipboard_html(html_content, fallback_text="Comprehensive Clinical Comparison: Xarelto vs Eliquis")
    
    # 4. Paste Content (Ctrl+V)
    send_hotkey(VK_CONTROL, VK_V)
    time.sleep(0.1)  # Brief wait to ensure paste command registers before script exit
    
    print("Successfully pasted rich HTML comparison into Google Docs.")
    return True

if __name__ == "__main__":
    update_google_docs()