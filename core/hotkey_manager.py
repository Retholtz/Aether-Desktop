"""
Aether Desktop - Push-to-Talk Global Hotkey Manager
Provides system-wide keyboard hook (WH_KEYBOARD_LL) on Windows via ctypes,
supporting Push-to-Talk in both Hold mode (active while held) and Toggle mode (turn mic on/off).
"""

import ctypes
from ctypes import wintypes
import logging
import sys
import threading
from typing import Callable, List, Optional, Set

from core.screen_stream import ensure_thread_desktop

logger = logging.getLogger("Aether.HotkeyManager")

# Win32 Constants
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C

# 64-bit safe Win32 type definitions
LRESULT = ctypes.c_longlong
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


# Comprehensive mapping of standard web / friendly key names to Windows Virtual Key codes
KEY_NAME_TO_VK = {
    "space": 0x20,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "escape": 0x1B,
    "esc": 0x1B,
    "backspace": 0x08,
    "capslock": 0x14,
    "caps lock": 0x14,
    "scrolllock": 0x91,
    "pause": 0x13,
    "insert": 0x2D,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "printscreen": 0x2C,
    # Function keys F1 - F24
    **{f"f{i}": 0x70 + (i - 1) for i in range(1, 25)},
    # Alphanumeric
    **{chr(c).lower(): c for c in range(0x41, 0x5B)},  # a-z
    **{str(i): 0x30 + i for i in range(10)},  # 0-9
    **{f"digit{i}": 0x30 + i for i in range(10)},
    **{f"key{chr(c).lower()}": c for c in range(0x41, 0x5B)},
    # Numpad
    **{f"numpad{i}": 0x60 + i for i in range(10)},
    "numpadmultiply": 0x6A,
    "numpadadd": 0x6B,
    "numpadsubtract": 0x6D,
    "numpaddecimal": 0x6E,
    "numpaddivide": 0x6F,
    # Common symbols & punctuation
    "backquote": 0xC0,
    "tilde": 0xC0,
    "`": 0xC0,
    "~": 0xC0,
    "minus": 0xBD,
    "-": 0xBD,
    "equal": 0xBB,
    "=": 0xBB,
    "bracketleft": 0xDB,
    "[": 0xDB,
    "bracketright": 0xDD,
    "]": 0xDD,
    "backslash": 0xDC,
    "\\": 0xDC,
    "semicolon": 0xBA,
    ";": 0xBA,
    "quote": 0xDE,
    "'": 0xDE,
    "comma": 0xBC,
    ",": 0xBC,
    "period": 0xBE,
    ".": 0xBE,
    "slash": 0xBF,
    "/": 0xBF,
    # Modifiers
    "control": VK_CONTROL,
    "ctrl": VK_CONTROL,
    "shift": VK_SHIFT,
    "alt": VK_MENU,
}


def parse_keybind_string(key_str: str) -> tuple[int, List[str]]:
    """
    Parses key strings such as 'Ctrl+Space', 'Alt+V', 'F4', 'Space', 'Caps Lock'.
    Returns (vk_code, required_modifiers).
    """
    cleaned = key_str.strip().lower()
    # Check if the entire string directly matches a known key (e.g. 'caps lock', 'page up', 'space')
    if cleaned in KEY_NAME_TO_VK:
        return KEY_NAME_TO_VK[cleaned], []

    # If it contains '+' or multiple tokens separated by '+', split by '+'
    if "+" in key_str:
        raw_parts = [p.strip() for p in key_str.split("+") if p.strip()]
    else:
        raw_parts = [p.strip() for p in key_str.split() if p.strip()]

    modifiers = []
    main_parts = []

    for part in raw_parts:
        p_lower = part.lower()
        if p_lower in ("ctrl", "control", "controlleft", "controlright"):
            if "Control" not in modifiers:
                modifiers.append("Control")
        elif p_lower in ("alt", "altleft", "altright", "menu"):
            if "Alt" not in modifiers:
                modifiers.append("Alt")
        elif p_lower in ("shift", "shiftleft", "shiftright"):
            if "Shift" not in modifiers:
                modifiers.append("Shift")
        else:
            main_parts.append(p_lower)

    main_key = " ".join(main_parts)
    if not main_key and modifiers:
        last_mod = modifiers.pop()
        main_key = last_mod.lower()

    vk = KEY_NAME_TO_VK.get(main_key, 0x20)  # Default to Space if unknown
    return vk, modifiers


class HotkeyManager:
    """
    Global low-level keyboard listener managing Push-to-Talk keybinds.
    Supports both Push-to-Talk Hold mode and Toggle mode system-wide.
    """

    def __init__(
        self,
        on_ptt_change: Optional[Callable[[bool], None]] = None,
        on_ptt_toggle: Optional[Callable[[], None]] = None,
        config_getter: Optional[Callable[[], dict]] = None,
    ):
        self.on_ptt_change = on_ptt_change
        self.on_ptt_toggle = on_ptt_toggle
        self.config_getter = config_getter or (lambda: {})

        self.enabled = False
        self.audio_mode = "always_on"  # "always_on" or "ptt"
        self.ptt_type = "hold"          # "hold" or "toggle"
        self.target_vk = 0x20           # Default: VK_SPACE
        self.target_modifiers: List[str] = []
        self.key_display = "Space"

        self._is_key_down = False
        self._input_focused = False
        self._hook_handle = None
        self._hook_thread_id = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._ready_event = threading.Event()

        # Prevent GC of ctypes callback function
        self._hook_fn = HOOKPROC(self._low_level_keyboard_proc)

        # Initialize settings from config
        self._load_from_config()

    def _load_from_config(self):
        """Loads PTT configuration from the application config getter."""
        try:
            cfg = self.config_getter()
            audio_cfg = cfg.get("audio", {})
            self.update_config(audio_cfg)
        except Exception as e:
            logger.warning(f"Could not load initial hotkey config: {e}")

    def update_config(self, audio_cfg: dict):
        """
        Dynamically updates PTT keybind settings without restarting the background hook.
        """
        self.audio_mode = audio_cfg.get("mode", "always_on")
        self.ptt_type = audio_cfg.get("ptt_type", "hold")
        self.key_display = audio_cfg.get("ptt_key_display", "Space")

        explicit_vk = audio_cfg.get("ptt_vk")
        explicit_mods = audio_cfg.get("ptt_modifiers")

        if explicit_vk is not None:
            self.target_vk = int(explicit_vk)
            self.target_modifiers = list(explicit_mods or [])
        else:
            key_str = audio_cfg.get("ptt_key", "Space")
            self.target_vk, self.target_modifiers = parse_keybind_string(key_str)

        self.enabled = (self.audio_mode == "ptt")
        self._is_key_down = False

        logger.info(
            f"[HOTKEY] Config updated: mode={self.audio_mode}, ptt_type={self.ptt_type}, "
            f"key={self.key_display} (VK={self.target_vk:#04x}, mods={self.target_modifiers}), "
            f"enabled={self.enabled}"
        )

    def set_input_focused(self, focused: bool):
        """
        Informs the hotkey manager when the user is actively typing in a text field
        inside the Aether Desktop window to prevent single-key hotkeys (like Space) from firing.
        """
        self._input_focused = focused
        if focused and self._is_key_down and self.ptt_type == "hold":
            self._is_key_down = False
            if self.on_ptt_change:
                self.on_ptt_change(False)

    def _check_modifiers(self) -> bool:
        """Verifies if the current physical modifier state matches target modifiers."""
        if sys.platform != "win32":
            return True

        user32 = ctypes.windll.user32
        ctrl_down = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
        alt_down = bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
        shift_down = bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)

        req_ctrl = "Control" in self.target_modifiers
        req_alt = "Alt" in self.target_modifiers
        req_shift = "Shift" in self.target_modifiers

        if req_ctrl != ctrl_down:
            return False
        if req_alt != alt_down:
            return False
        if req_shift != shift_down:
            return False

        return True

    def _low_level_keyboard_proc(self, nCode: int, wParam: int, lParam: int) -> int:
        """Low-level Windows keyboard callback executed for every keystroke system-wide."""
        if nCode >= 0 and self.enabled and self._running:
            try:
                kb = KBDLLHOOKSTRUCT.from_address(lParam)
                vk = kb.vkCode

                is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                is_up = wParam in (WM_KEYUP, WM_SYSKEYUP)

                # Check if this event targets our configured PTT key
                if vk == self.target_vk:
                    # Smart typing safeguard: suppress single-key hotkeys if user is typing text in Aether
                    if self._input_focused and not self.target_modifiers:
                        pass
                    elif is_down:
                        if self._check_modifiers():
                            if not self._is_key_down:
                                self._is_key_down = True
                                if self.ptt_type == "hold":
                                    logger.debug("[PTT] Key down -> Hold Speak ON")
                                    if self.on_ptt_change:
                                        self.on_ptt_change(True)
                                elif self.ptt_type == "toggle":
                                    logger.debug("[PTT] Key down -> Toggle Mic")
                                    if self.on_ptt_toggle:
                                        self.on_ptt_toggle()
                    elif is_up:
                        if self._is_key_down:
                            self._is_key_down = False
                            if self.ptt_type == "hold":
                                logger.debug("[PTT] Key up -> Hold Speak OFF")
                                if self.on_ptt_change:
                                    self.on_ptt_change(False)
            except Exception as ex:
                logger.error(f"[HOTKEY PROC ERROR] {ex}")

        # Always pass the hook event to the next hook in chain (never swallow keystrokes)
        user32 = ctypes.windll.user32
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _run_hook_loop(self):
        """Background thread worker hosting the Windows message pump for the LL hook."""
        ensure_thread_desktop()

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # Configure ctypes prototypes for 64-bit safe execution
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK

        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = LRESULT

        self._hook_thread_id = kernel32.GetCurrentThreadId()
        h_mod = kernel32.GetModuleHandleW(None)

        self._hook_handle = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            self._hook_fn,
            h_mod,
            0
        )

        if not self._hook_handle:
            err = kernel32.GetLastError()
            logger.error(f"[HOTKEY] Failed to install SetWindowsHookExW (Error {err})")
            self._ready_event.set()
            return

        logger.info(f"[HOTKEY] Global keyboard hook active (Handle: {self._hook_handle})")
        self._ready_event.set()

        msg = wintypes.MSG()
        # GetMessage retrieves messages posted to this thread; returns 0 on WM_QUIT
        while self._running and user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._hook_handle:
            user32.UnhookWindowsHookEx(self._hook_handle)
            self._hook_handle = None
            logger.info("[HOTKEY] Global keyboard hook removed cleanly.")

    def start(self):
        """Starts the global keyboard listener in a dedicated background worker thread."""
        if self._running or sys.platform != "win32":
            return

        self._running = True
        self._ready_event.clear()
        self._thread = threading.Thread(target=self._run_hook_loop, name="PTT-HotkeyHook", daemon=True)
        self._thread.start()
        self._ready_event.wait(timeout=2.0)

    def stop(self):
        """Stops the global keyboard listener and cleanly tears down the Windows hook."""
        if not self._running:
            return

        self._running = False
        self.enabled = False
        self._is_key_down = False

        if sys.platform == "win32" and self._hook_thread_id:
            try:
                user32 = ctypes.windll.user32
                user32.PostThreadMessageW(self._hook_thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            self._thread = None
