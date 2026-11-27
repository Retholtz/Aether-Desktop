"""
Aether Desktop - Push-to-Talk Global Hotkey Manager
Provides system-wide keyboard hook (WH_KEYBOARD_LL) on Windows via ctypes,
supporting Push-to-Talk in both Hold mode (active while held) and Toggle mode (turn mic on/off).
"""

import ctypes
from ctypes import wintypes
import logging
import os
import sys
import threading
import time
from typing import Callable, List, Optional, Set

try:
    import psutil
except ImportError:
    psutil = None

from core.screen_stream import ensure_thread_desktop

logger = logging.getLogger("Aether.HotkeyManager")

# Win32 Constants
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_QUIT = 0x0012

VK_MBUTTON = 0x04
VK_XBUTTON1 = 0x05
VK_XBUTTON2 = 0x06
MOUSE_PTT_VKS = {VK_MBUTTON, VK_XBUTTON1, VK_XBUTTON2}

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C

# XInput Gamepad button masks for controller / HOTAS-mapped Push-to-Talk
GAMEPAD_BUTTON_MASKS = {
    "gamepad_dpad_up": 0x0001,
    "gamepad_dpad_down": 0x0002,
    "gamepad_dpad_left": 0x0004,
    "gamepad_dpad_right": 0x0008,
    "gamepad_start": 0x0010,
    "gamepad_back": 0x0020,
    "gamepad_view": 0x0020,
    "gamepad_ls": 0x0040,
    "gamepad_l3": 0x0040,
    "gamepad_rs": 0x0080,
    "gamepad_r3": 0x0080,
    "gamepad_lb": 0x0100,
    "gamepad_rb": 0x0200,
    "gamepad_a": 0x1000,
    "gamepad_b": 0x2000,
    "gamepad_x": 0x4000,
    "gamepad_y": 0x8000,
}

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


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class _XINPUT_STATE(ctypes.Structure):
    _fields_ = [
        ("dwPacketNumber", wintypes.DWORD),
        ("Gamepad", _XINPUT_GAMEPAD),
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
    # Mouse buttons (global WH_MOUSE_LL Push-to-Talk)
    "mouse3": VK_MBUTTON,
    "mbutton": VK_MBUTTON,
    "middle": VK_MBUTTON,
    "middle mouse": VK_MBUTTON,
    "mouse4": VK_XBUTTON1,
    "xbutton1": VK_XBUTTON1,
    "mouse back": VK_XBUTTON1,
    "mouse5": VK_XBUTTON2,
    "xbutton2": VK_XBUTTON2,
    "mouse forward": VK_XBUTTON2,
}


def parse_gamepad_button_mask(key_str: str) -> int:
    """Returns the XInput bitmask if `key_str` refers to a gamepad button, else 0."""
    cleaned = (key_str or "").strip().lower().replace(" ", "_").replace("-", "_")
    return int(GAMEPAD_BUTTON_MASKS.get(cleaned, 0))


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
        on_hud_mode_cycle: Optional[Callable[[], None]] = None,
        on_game_mode_toggle: Optional[Callable[[], None]] = None,
        config_getter: Optional[Callable[[], dict]] = None,
    ):
        self.on_ptt_change = on_ptt_change
        self.on_ptt_toggle = on_ptt_toggle
        self.on_hud_mode_cycle = on_hud_mode_cycle
        self.on_game_mode_toggle = on_game_mode_toggle
        self.config_getter = config_getter or (lambda: {})

        self.enabled = False
        self.audio_mode = "always_on"  # "always_on" or "ptt"
        self.ptt_type = "hold"          # "hold" or "toggle"
        self.target_vk = 0x20           # Default: VK_SPACE
        self.target_modifiers: List[str] = []
        self.key_display = "Space"
        self.ptt_gamepad_mask: int = 0

        # HUD Overlay Mode Switch Hotkey (default Ctrl+Space)
        self.hud_mode_vk: Optional[int] = 0x20
        self.hud_mode_modifiers: List[str] = ["Control"]
        self.hud_mode_display: str = "Ctrl+Space"
        self._is_hud_key_down = False

        # Game Mode Focus Toggle Hotkey (default Ctrl+Shift+G)
        self.game_mode_vk: Optional[int] = 0x47  # VK for 'G'
        self.game_mode_modifiers: List[str] = ["Control", "Shift"]
        self.game_mode_display: str = "Ctrl+Shift+G"
        self._is_game_mode_key_down = False

        self._is_key_down = False
        self._is_gamepad_down = False
        self._input_focused = False
        self._cached_child_pids: Set[int] = set()
        self._last_pid_cache_time: float = 0.0
        self._pid_refresh_in_progress: bool = False
        self._hook_handle = None
        self._mouse_hook_handle = None
        self._hook_thread_id = None
        self._thread: Optional[threading.Thread] = None
        self._gamepad_thread: Optional[threading.Thread] = None
        self._mouse_poll_thread: Optional[threading.Thread] = None
        self._running = False
        self._ready_event = threading.Event()

        # Prevent GC of ctypes callback functions
        self._hook_fn = HOOKPROC(self._low_level_keyboard_proc)
        self._mouse_hook_fn = HOOKPROC(self._low_level_mouse_proc)

        # Initialize settings from config
        self._load_from_config()

    def _load_from_config(self):
        """Loads PTT and HUD mode configuration from the application config getter."""
        try:
            cfg = self.config_getter()
            audio_cfg = cfg.get("audio", {})
            ui_cfg = cfg.get("ui", {})
            self.update_config(audio_cfg, ui_cfg)
        except Exception as e:
            logger.warning(f"Could not load initial hotkey config: {e}")

    def update_config(self, audio_cfg: Optional[dict] = None, ui_cfg: Optional[dict] = None):
        """
        Dynamically updates PTT and HUD mode keybind settings without restarting the background hook.
        """
        if audio_cfg is None:
            audio_cfg = self.config_getter().get("audio", {})
        if ui_cfg is None:
            ui_cfg = self.config_getter().get("ui", {})

        self.audio_mode = audio_cfg.get("mode", "always_on")
        self.ptt_type = audio_cfg.get("ptt_type", "hold")
        self.key_display = audio_cfg.get("ptt_key_display", "Space")

        explicit_vk = audio_cfg.get("ptt_vk")
        explicit_mods = audio_cfg.get("ptt_modifiers")

        key_str = str(audio_cfg.get("ptt_key") or self.key_display or "Space")
        self.ptt_gamepad_mask = parse_gamepad_button_mask(key_str) or parse_gamepad_button_mask(self.key_display)

        if self.ptt_gamepad_mask:
            self.target_vk = 0
            self.target_modifiers = []
        elif explicit_vk is not None:
            self.target_vk = int(explicit_vk)
            self.target_modifiers = list(explicit_mods or [])
        else:
            self.target_vk, self.target_modifiers = parse_keybind_string(key_str)

        # HUD mode hotkey settings
        self.hud_mode_display = ui_cfg.get("hud_mode_key_display", "Ctrl+Space")
        explicit_hud_vk = ui_cfg.get("hud_mode_vk")
        explicit_hud_mods = ui_cfg.get("hud_mode_modifiers")

        if explicit_hud_vk is not None:
            self.hud_mode_vk = int(explicit_hud_vk)
            self.hud_mode_modifiers = list(explicit_hud_mods or [])
        else:
            hud_key_str = ui_cfg.get("hud_mode_hotkey", "Ctrl+Space")
            self.hud_mode_vk, self.hud_mode_modifiers = parse_keybind_string(hud_key_str)

        # Game Mode hotkey settings
        self.game_mode_display = ui_cfg.get("game_mode_key_display", "Ctrl+Shift+G")
        explicit_gm_vk = ui_cfg.get("game_mode_vk")
        explicit_gm_mods = ui_cfg.get("game_mode_modifiers")

        if explicit_gm_vk is not None:
            self.game_mode_vk = int(explicit_gm_vk)
            self.game_mode_modifiers = list(explicit_gm_mods or [])
        else:
            gm_key_str = ui_cfg.get("game_mode_hotkey", "Ctrl+Shift+G")
            self.game_mode_vk, self.game_mode_modifiers = parse_keybind_string(gm_key_str)

        self.enabled = (self.audio_mode == "ptt")
        self._is_key_down = False
        self._is_gamepad_down = False
        self._is_hud_key_down = False
        self._is_game_mode_key_down = False
        if self._running and self.enabled and self.ptt_gamepad_mask:
            self._ensure_gamepad_thread()
        if self._running and self.enabled and self.target_vk in MOUSE_PTT_VKS:
            self._ensure_mouse_poll_thread()

        logger.info(
            f"[HOTKEY] Config updated: PTT(mode={self.audio_mode}, ptt_type={self.ptt_type}, "
            f"key={self.key_display}, VK={self.target_vk:#04x}, mods={self.target_modifiers}) | "
            f"HUD(key={self.hud_mode_display}, VK={self.hud_mode_vk:#04x}, mods={self.hud_mode_modifiers}) | "
            f"GameMode(key={self.game_mode_display}, VK={self.game_mode_vk:#04x}, mods={self.game_mode_modifiers})"
        )

    def set_input_focused(self, focused: bool):
        """
        Informs the hotkey manager when the user is actively typing in a text field
        inside the Aether Desktop window to prevent single-key hotkeys (like Space) from firing.
        """
        self._input_focused = focused
        logger.debug(f"[HOTKEY] set_input_focused: {focused}")
        if focused and self._is_key_down and self.ptt_type == "hold":
            self._is_key_down = False
            if self.on_ptt_change:
                self._dispatch_callback(self.on_ptt_change, False)

    def _dispatch_callback(self, fn: Optional[Callable], *args, log_msg: Optional[str] = None):
        """Dispatches a hotkey callback off the low-level Win32 hook thread so WH_KEYBOARD_LL returns immediately."""
        def _runner():
            if fn:
                try:
                    fn(*args)
                except Exception as cb_err:
                    logger.error(f"[HOTKEY CALLBACK ERROR] {cb_err}")
            if log_msg:
                logger.info(log_msg)
        threading.Thread(target=_runner, daemon=True).start()

    def _refresh_child_pids_async(self):
        """Refreshes cached child PIDs (e.g. WebView2 renderers) off the WH_KEYBOARD_LL hook thread."""
        if psutil is None or getattr(self, "_pid_refresh_in_progress", False):
            return
        self._pid_refresh_in_progress = True

        def _worker():
            try:
                cached_set: Set[int] = set()
                cur_proc = psutil.Process(os.getpid())
                for child in cur_proc.children(recursive=True):
                    cached_set.add(child.pid)
                self._cached_child_pids = cached_set
                self._last_pid_cache_time = time.monotonic()
            except Exception:
                pass
            finally:
                self._pid_refresh_in_progress = False

        threading.Thread(target=_worker, name="Hotkey-PIDCache", daemon=True).start()

    def _is_aether_foreground(self) -> bool:
        """
        Checks if the currently active foreground window belongs to the Aether Desktop process
        or one of its child renderer processes (e.g. WebView2).
        """
        if sys.platform != "win32":
            return False
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            current_pid = os.getpid()
            if pid.value == current_pid:
                return True
            # Refresh child PID cache asynchronously so WH_KEYBOARD_LL never blocks on process enumeration
            now = time.monotonic()
            if (now - getattr(self, "_last_pid_cache_time", 0.0)) > 5.0:
                self._refresh_child_pids_async()
            return pid.value in getattr(self, "_cached_child_pids", set())
        except Exception:
            return False

    def _check_modifiers(self, target_modifiers: Optional[List[str]] = None) -> bool:
        """Verifies if the current physical modifier state matches target modifiers."""
        if sys.platform != "win32":
            return True

        if target_modifiers is None:
            target_modifiers = self.target_modifiers

        user32 = ctypes.windll.user32
        ctrl_down = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
        alt_down = bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
        shift_down = bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)

        req_ctrl = "Control" in target_modifiers
        req_alt = "Alt" in target_modifiers
        req_shift = "Shift" in target_modifiers

        if req_ctrl != ctrl_down:
            return False
        if req_alt != alt_down:
            return False
        if req_shift != shift_down:
            return False

        return True

    def _low_level_keyboard_proc(self, nCode: int, wParam: int, lParam: int) -> int:
        """Low-level Windows keyboard callback executed for every keystroke system-wide."""
        if nCode >= 0 and self._running:
            try:
                kb = KBDLLHOOKSTRUCT.from_address(lParam)
                vk = kb.vkCode

                is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                is_up = wParam in (WM_KEYUP, WM_SYSKEYUP)

                # 1. Check HUD Mode Cycle Hotkey (system-wide global hotkey)
                if self.hud_mode_vk is not None and vk == self.hud_mode_vk:
                    if is_down:
                        if self._check_modifiers(self.hud_mode_modifiers):
                            if not self._is_hud_key_down:
                                self._is_hud_key_down = True
                                self._dispatch_callback(
                                    self.on_hud_mode_cycle,
                                    log_msg="[HUD HOTKEY] Key down -> Cycle HUD Mode",
                                )
                    elif is_up:
                        self._is_hud_key_down = False

                # 2. Check Game Mode Toggle Hotkey (system-wide global hotkey)
                if self.game_mode_vk is not None and vk == self.game_mode_vk:
                    if is_down:
                        if self._check_modifiers(self.game_mode_modifiers):
                            if not self._is_game_mode_key_down:
                                self._is_game_mode_key_down = True
                                self._dispatch_callback(
                                    self.on_game_mode_toggle,
                                    log_msg="[GAME MODE HOTKEY] Key down -> Toggle Game Mode",
                                )
                    elif is_up:
                        self._is_game_mode_key_down = False

                # 3. Check Push-to-Talk Hotkey (active when audio mode is PTT)
                if self.enabled and vk == self.target_vk:
                    is_foreground_aether = self._is_aether_foreground()
                    # Smart typing safeguard: suppress single-key hotkeys ONLY if user is typing text in Aether
                    # If Aether is not the foreground window, typing safeguard is never applied
                    is_typing_suppressed = self._input_focused and is_foreground_aether and not self.target_modifiers

                    if not is_foreground_aether and self._input_focused:
                        # Auto-clear stale input focus when user switches to other windows
                        self._input_focused = False

                    if is_typing_suppressed:
                        if is_down and not self._is_key_down:
                            self._dispatch_callback(
                                None,
                                log_msg="[PTT] Hotkey ignored: typing safeguard active in Aether text field",
                            )
                    elif is_down:
                        if self._check_modifiers(self.target_modifiers):
                            if not self._is_key_down:
                                self._is_key_down = True
                                if self.ptt_type == "hold":
                                    self._dispatch_callback(
                                        self.on_ptt_change,
                                        True,
                                        log_msg="[PTT] Hotkey pressed -> Hold Speak ON",
                                    )
                                elif self.ptt_type == "toggle":
                                    self._dispatch_callback(
                                        self.on_ptt_toggle,
                                        log_msg="[PTT] Hotkey pressed -> Toggle Mic",
                                    )
                    elif is_up:
                        if self._is_key_down:
                            self._is_key_down = False
                            if self.ptt_type == "hold":
                                self._dispatch_callback(
                                    self.on_ptt_change,
                                    False,
                                    log_msg="[PTT] Hotkey released -> Hold Speak OFF",
                                )
            except Exception as ex:
                logger.error(f"[HOTKEY PROC ERROR] {ex}")

        # Always pass the hook event to the next hook in chain (never swallow keystrokes)
        user32 = ctypes.windll.user32
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _low_level_mouse_proc(self, nCode: int, wParam: int, lParam: int) -> int:
        """Low-level Windows mouse hook enabling Mouse4/Mouse5/Middle Push-to-Talk."""
        if nCode >= 0 and self._running and self.enabled and self.target_vk in MOUSE_PTT_VKS:
            try:
                vk = 0
                is_down = False
                is_up = False
                if wParam == WM_MBUTTONDOWN:
                    vk, is_down = VK_MBUTTON, True
                elif wParam == WM_MBUTTONUP:
                    vk, is_up = VK_MBUTTON, True
                elif wParam in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                    ms = MSLLHOOKSTRUCT.from_address(lParam)
                    xbtn = (ms.mouseData >> 16) & 0xFFFF
                    if xbtn == 1:
                        vk = VK_XBUTTON1
                    elif xbtn == 2:
                        vk = VK_XBUTTON2
                    is_down = (wParam == WM_XBUTTONDOWN)
                    is_up = (wParam == WM_XBUTTONUP)

                if vk and vk == self.target_vk:
                    if is_down and self._check_modifiers(self.target_modifiers):
                        if not self._is_key_down:
                            self._is_key_down = True
                            if self.ptt_type == "hold":
                                self._dispatch_callback(
                                    self.on_ptt_change,
                                    True,
                                    log_msg=f"[PTT] Mouse button ({self.key_display}) pressed -> Hold Speak ON",
                                )
                            elif self.ptt_type == "toggle":
                                self._dispatch_callback(
                                    self.on_ptt_toggle,
                                    log_msg=f"[PTT] Mouse button ({self.key_display}) pressed -> Toggle Mic",
                                )
                    elif is_up and self._is_key_down:
                        self._is_key_down = False
                        if self.ptt_type == "hold":
                            self._dispatch_callback(
                                self.on_ptt_change,
                                False,
                                log_msg=f"[PTT] Mouse button ({self.key_display}) released -> Hold Speak OFF",
                            )
            except Exception as ex:
                logger.error(f"[MOUSE HOTKEY PROC ERROR] {ex}")

        user32 = ctypes.windll.user32
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _load_xinput(self):
        for dll_name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
            try:
                dll = ctypes.WinDLL(dll_name)
                if hasattr(dll, "XInputGetState"):
                    dll.XInputGetState.argtypes = [wintypes.DWORD, ctypes.POINTER(_XINPUT_STATE)]
                    dll.XInputGetState.restype = wintypes.DWORD
                    return dll
            except Exception:
                continue
        return None

    def _poll_gamepad_once(self, xinput_dll) -> bool:
        """Returns True if any connected XInput controller (0-3) is pressing `self.ptt_gamepad_mask`."""
        mask = self.ptt_gamepad_mask
        if not mask or xinput_dll is None:
            return False
        st = _XINPUT_STATE()
        for idx in range(4):
            try:
                if xinput_dll.XInputGetState(idx, ctypes.byref(st)) == 0:
                    if st.Gamepad.wButtons & mask:
                        return True
            except Exception:
                continue
        return False

    def _gamepad_poll_loop(self):
        """Lightweight 50Hz XInput poller active only when PTT is bound to a gamepad button."""
        xinput_dll = self._load_xinput()
        if xinput_dll is None:
            logger.warning("[HOTKEY] XInput DLL not available; gamepad PTT disabled.")
            return
        while self._running:
            if not self.enabled or not self.ptt_gamepad_mask:
                time.sleep(0.2)
                continue
            pressed = self._poll_gamepad_once(xinput_dll)
            if pressed and not self._is_gamepad_down:
                self._is_gamepad_down = True
                if self.ptt_type == "hold":
                    self._dispatch_callback(
                        self.on_ptt_change,
                        True,
                        log_msg=f"[PTT] Gamepad ({self.key_display}) pressed -> Hold Speak ON",
                    )
                elif self.ptt_type == "toggle":
                    self._dispatch_callback(
                        self.on_ptt_toggle,
                        log_msg=f"[PTT] Gamepad ({self.key_display}) pressed -> Toggle Mic",
                    )
            elif not pressed and self._is_gamepad_down:
                self._is_gamepad_down = False
                if self.ptt_type == "hold":
                    self._dispatch_callback(
                        self.on_ptt_change,
                        False,
                        log_msg=f"[PTT] Gamepad ({self.key_display}) released -> Hold Speak OFF",
                    )
            time.sleep(0.02)

    def _ensure_gamepad_thread(self):
        if sys.platform != "win32":
            return
        if self._gamepad_thread is None or not self._gamepad_thread.is_alive():
            self._gamepad_thread = threading.Thread(
                target=self._gamepad_poll_loop,
                name="PTT-GamepadPoller",
                daemon=True,
            )
            self._gamepad_thread.start()

    def _mouse_ptt_poll_loop(self):
        """
        Lightweight 50Hz GetAsyncKeyState poller for Mouse3/Mouse4/Mouse5 Push-to-Talk.
        Avoids installing a global WH_MOUSE_LL hook, which would intercept every high-rate
        WM_MOUSEMOVE event through Python's GIL and cause OS-wide cursor lag.
        """
        user32 = ctypes.windll.user32
        while self._running:
            vk = self.target_vk
            if not self.enabled or vk not in MOUSE_PTT_VKS:
                time.sleep(0.2)
                continue
            try:
                pressed = bool(user32.GetAsyncKeyState(int(vk)) & 0x8000)
                if pressed and not self._is_key_down:
                    if self._check_modifiers(self.target_modifiers):
                        self._is_key_down = True
                        if self.ptt_type == "hold":
                            self._dispatch_callback(
                                self.on_ptt_change,
                                True,
                                log_msg=f"[PTT] Mouse button ({self.key_display}) pressed -> Hold Speak ON",
                            )
                        elif self.ptt_type == "toggle":
                            self._dispatch_callback(
                                self.on_ptt_toggle,
                                log_msg=f"[PTT] Mouse button ({self.key_display}) pressed -> Toggle Mic",
                            )
                elif not pressed and self._is_key_down:
                    self._is_key_down = False
                    if self.ptt_type == "hold":
                        self._dispatch_callback(
                            self.on_ptt_change,
                            False,
                            log_msg=f"[PTT] Mouse button ({self.key_display}) released -> Hold Speak OFF",
                        )
            except Exception as ex:
                logger.debug(f"[MOUSE PTT POLL ERROR] {ex}")
            time.sleep(0.02)

    def _ensure_mouse_poll_thread(self):
        if sys.platform != "win32":
            return
        if self._mouse_poll_thread is None or not self._mouse_poll_thread.is_alive():
            self._mouse_poll_thread = threading.Thread(
                target=self._mouse_ptt_poll_loop,
                name="PTT-MousePoller",
                daemon=True,
            )
            self._mouse_poll_thread.start()

    def _run_hook_loop(self):
        """Background thread worker hosting the Windows message pump for the LL keyboard hook."""
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

        # Do NOT install WH_MOUSE_LL globally (every 1000Hz WM_MOUSEMOVE would stall on Python's GIL
        # and cause severe cursor lag). Mouse3/4/5 PTT is handled by `_mouse_ptt_poll_loop` instead.
        self._mouse_hook_handle = None

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

        # Reload latest config and restore enabled state (stop() sets self.enabled = False)
        try:
            cfg = self.config_getter() if callable(self.config_getter) else {}
            if cfg:
                self._load_from_config()
            else:
                self.enabled = (self.audio_mode == "ptt")
        except Exception:
            self.enabled = (self.audio_mode == "ptt")

        self._is_key_down = False
        self._is_hud_key_down = False
        self._is_game_mode_key_down = False
        self._input_focused = False
        self._refresh_child_pids_async()

        self._running = True
        self._ready_event.clear()
        self._thread = threading.Thread(target=self._run_hook_loop, name="PTT-HotkeyHook", daemon=True)
        self._thread.start()
        self._ready_event.wait(timeout=2.0)
        if self.enabled and self.ptt_gamepad_mask:
            self._ensure_gamepad_thread()
        if self.enabled and self.target_vk in MOUSE_PTT_VKS:
            self._ensure_mouse_poll_thread()

    def reset_key_states(self):
        """Clears transient pressed-key states (e.g. when the voice assistant enters Standby) without removing the global hook."""
        self._is_key_down = False
        self._is_hud_key_down = False
        self._is_game_mode_key_down = False

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
