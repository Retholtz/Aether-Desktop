import ctypes
from ctypes import wintypes
import time
import win32clipboard
import win32gui
from tools.os_controls import bring_hwnd_to_foreground

# --- CTypes Structures for SendInput ---
user32 = ctypes.WinDLL('user32', use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56

class KEYBDINPUT(ctypes.Structure):
    _fields_ = (("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)))

class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = (("ki", KEYBDINPUT),
                    ("mi", ctypes.c_byte * 28),
                    ("hi", ctypes.c_byte * 32))
    _anonymous_ = ("_input",)
    _fields_ = (("type", wintypes.DWORD),
                ("_input", _INPUT))

def send_ctrl_v():
    """Sends Ctrl+V using the native SendInput API for deterministic execution."""
    inputs = (INPUT * 4)()
    
    inputs[0].type = INPUT_KEYBOARD
    inputs[0].ki.wVk = VK_CONTROL
    
    inputs[1].type = INPUT_KEYBOARD
    inputs[1].ki.wVk = VK_V
    
    inputs[2].type = INPUT_KEYBOARD
    inputs[2].ki.wVk = VK_V
    inputs[2].ki.dwFlags = KEYEVENTF_KEYUP
    
    inputs[3].type = INPUT_KEYBOARD
    inputs[3].ki.wVk = VK_CONTROL
    inputs[3].ki.dwFlags = KEYEVENTF_KEYUP
    
    user32.SendInput(4, ctypes.byref(inputs), ctypes.sizeof(INPUT))

def make_cf_html(fragment: str) -> bytes:
    MARKER_START = "<!--StartFragment-->"
    MARKER_END = "<!--EndFragment-->"
    html_page = (
        "<!DOCTYPE html><html><body>"
        f"{MARKER_START}{fragment}{MARKER_END}"
        "</body></html>"
    )
    header_len = 105 # Fixed length of the dummy header
    page_bytes = html_page.encode("utf-8")
    
    start_html = header_len
    end_html = header_len + len(page_bytes)
    start_frag = header_len + page_bytes.find(MARKER_START.encode("utf-8")) + len(MARKER_START.encode("utf-8"))
    end_frag = header_len + page_bytes.find(MARKER_END.encode("utf-8"))
    
    real_header = (
        "Version:0.9\r\n"
        f"StartHTML:{start_html:010d}\r\n"
        f"EndHTML:{end_html:010d}\r\n"
        f"StartFragment:{start_frag:010d}\r\n"
        f"EndFragment:{end_frag:010d}\r\n"
    )
    return (real_header + html_page).encode("utf-8")

def find_target_window(target_titles: tuple) -> int:
    target_hwnd = 0
    def enum_cb(hwnd, _):
        nonlocal target_hwnd
        if target_hwnd == 0 and win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if any(t in title for t in target_titles):
                target_hwnd = hwnd
    win32gui.EnumWindows(enum_cb, None)
    return target_hwnd

def paste_content_to_window(html_content: str, plain_text: str, target_titles: tuple = ("Google Docs", "Chrome")):
    cf_html_bytes = make_cf_html(html_content)
    CF_HTML = win32clipboard.RegisterClipboardFormat("HTML Format")
    
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(CF_HTML, cf_html_bytes)
        win32clipboard.SetClipboardText(plain_text, win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()

    target_hwnd = find_target_window(target_titles)
    if target_hwnd:
        bring_hwnd_to_foreground(target_hwnd)
        # Minimal deterministic wait for OS window focus transition
        time.sleep(0.1)
        send_ctrl_v()
        print("Pasted coagulation cascade content successfully!")
    else:
        print("Target window not found.")

# --- Parameterized Inputs ---
HTML_CONTENT = """
<div style="font-family: Arial, sans-serif; line-height: 1.6; color: #202124;">
  <h1 style="color: #1a73e8; border-bottom: 2px solid #1a73e8; padding-bottom: 8px;">The Coagulation Cascade: Molecular Pathways, Cell-Based Models & Clinical Pharmacology</h1>
  <p><strong>Secondary hemostasis</strong>—commonly referred to as the <strong>coagulation cascade</strong>—is a tightly regulated biochemical amplification cascade of serine proteases and cofactors designed to convert soluble plasma fibrinogen into an insoluble, cross-linked fibrin mesh. This mesh stabilizes the initial platelet plug formed during primary hemostasis and halts extravascular hemorrhage.</p>
  <hr style="border: 0; border-top: 1px solid #dadce0; margin: 20px 0;">
  <h2 style="color: #2c3e50;">1. Overview: The Classical Cascade Model</h2>
  <p>Historically described by Macfarlane and Davie & Ratnoff (1964), the classical model categorizes secondary hemostasis into two triggering branches—the <strong>Extrinsic Pathway</strong> and the <strong>Intrinsic Pathway</strong>—which converge at the activation of <strong>Factor X</strong> to form the <strong>Common Pathway</strong>.</p>
  <div style="background-color: #f8f9fa; border: 1px solid #dadce0; border-radius: 8px; padding: 16px; margin: 20px 0; font-family: monospace; white-space: pre; line-height: 1.4; color: #1a202c; font-size: 13px;">
========================================================================================
                      THE CLASSICAL COAGULATION CASCADE DIAGRAM
========================================================================================
    INTRINSIC PATHWAY (Contact Activation)            EXTRINSIC PATHWAY (Tissue Injury)
      [Tested via aPTT: 25-35 sec]                      [Tested via PT / INR: 11-13 sec]
             Factor XII ──> XIIa                               Tissue Factor (TF / III)
                             ↓                                         ↓
                        Factor XI ──> XIa                   TF + Factor VII ──> VIIa
                                       ↓                               │
                                  Factor IX ──> IXa                    │
                    + Factor VIIIa, Ca²⁺, PL ───┘                     │
                                  ↓                                    ↓
                       [Intrinsic Tenase Complex]           [Extrinsic Tenase Complex]
                                  │                                    │
                                  └──────────────────┬─────────────────┘
                                                     ↓
                                             FACTOR X ──> Xa
                                                           │ + Factor Va, Ca²⁺, Platelet PL
                                                           ↓
                                              [Prothrombinase Complex]
                                                           ↓
                                           Prothrombin (II) ──> THROMBIN (IIa)
                                                                      │
                                                ┌─────────────────────┴─────────────────────┐
                                                ↓                                           ↓
                                        Fibrinogen (I) ──> Fibrin Monomer (Ia)        Factor XIII ──> XIIIa
                                                                 │                                  │
                                                                 ↓                                  │
                                                        Fibrin Polymers (Soft)                      │
                                                                 │                                  │
                                                                 └─────────────────┬────────────────┘
                                                                                   ↓
                                                                       CROSS-LINKED FIBRIN CLOT
========================================================================================
  </div>
</div>
"""

PLAIN_TEXT = """The Coagulation Cascade: Molecular Pathways, Cell-Based Models & Clinical Pharmacology

Secondary hemostasis—commonly referred to as the coagulation cascade—is a tightly regulated biochemical amplification cascade of serine proteases and cofactors designed to convert soluble plasma fibrinogen into an insoluble, cross-linked fibrin mesh.

THE CLASSICAL COAGULATION CASCADE DIAGRAM
INTRINSIC PATHWAY (aPTT): XII -> XIIa -> XI -> XIa -> IX -> IXa (+ VIIIa, Ca2+, PL)
EXTRINSIC PATHWAY (PT/INR): Tissue Factor (III) + VII -> VIIa
COMMON PATHWAY: X -> Xa (+ Va, Ca2+, PL) -> Prothrombin (II) -> Thrombin (IIa) -> Fibrinogen (I) -> Fibrin (Ia) -> XIIIa -> Cross-linked Fibrin Clot.
"""

if __name__ == "__main__":
    paste_content_to_window(HTML_CONTENT, PLAIN_TEXT, ("Google Docs", "Chrome"))