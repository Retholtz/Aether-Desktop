"""
Aether Desktop - Tier 2: Low-Level GUI Primitives
Provides precision mouse clicks, cursor positioning, Unicode keyboard typing,
hotkey shortcuts, and mouse wheel scrolling using native 64-bit Win32 SendInput
with dual-layer fallback to mouse_event/keybd_event.
"""

import ctypes
from ctypes import wintypes
import sys
import time
from typing import Dict, Optional, Tuple

from core.screen_stream import ScreenCapturePipeline, ensure_thread_desktop
from tools.os_controls import focus_window, find_hwnd_by_query

if sys.platform == "win32":
    import win32gui
else:
    win32gui = None

# Win32 Constants
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0009
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_DELETE = 0x2E
VK_LWIN = 0x5B

KEY_MAP = {
    "backspace": VK_BACK,
    "tab": VK_TAB,
    "enter": VK_RETURN,
    "return": VK_RETURN,
    "shift": VK_SHIFT,
    "ctrl": VK_CONTROL,
    "control": VK_CONTROL,
    "alt": VK_MENU,
    "esc": VK_ESCAPE,
    "escape": VK_ESCAPE,
    "space": VK_SPACE,
    "left": VK_LEFT,
    "up": VK_UP,
    "right": VK_RIGHT,
    "down": VK_DOWN,
    "delete": VK_DELETE,
    "del": VK_DELETE,
    "win": VK_LWIN,
    "windows": VK_LWIN,
}

# 64-bit safe SendInput Structures (ULONG_PTR = 8 bytes on Win64; sizeof(INPUT) == 40)
ULONG_PTR = ctypes.c_size_t

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
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

class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]

class INPUT(ctypes.Structure):
    _anonymous_ = ("_u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_u", _INPUT_UNION),
    ]


class GuiPrimitivesController:
    """Executes low-level mouse, keyboard, and scrolling actions on Windows."""

    def __init__(self, capture_pipeline: Optional[ScreenCapturePipeline] = None):
        ensure_thread_desktop()
        self.capture = capture_pipeline or ScreenCapturePipeline()

    def translate_coords(
        self,
        x: float,
        y: float,
        monitor: str | int = "auto",
        normalized: bool = True,
        app_name: Optional[str] = None
    ) -> Tuple[int, int, int]:
        """
        Translates coordinates into Windows desktop pixels.
        If app_name is provided and the window exists, coordinates (0..1000) are mapped
        directly relative to the target window's physical bounds, guaranteeing clicks stay inside the window.
        Otherwise, coordinates are mapped relative to the target/streamed monitor.
        """
        if app_name and win32gui:
            hwnd = find_hwnd_by_query(app_name)
            if hwnd:
                try:
                    rect = win32gui.GetWindowRect(hwnd)
                    w = max(10, rect[2] - rect[0])
                    h = max(10, rect[3] - rect[1])
                    if normalized:
                        norm_x = max(0.0, min(1000.0, float(x))) / 1000.0
                        norm_y = max(0.0, min(1000.0, float(y))) / 1000.0
                        act_x = rect[0] + int(norm_x * w)
                        act_y = rect[1] + int(norm_y * h)
                    else:
                        act_x = rect[0] + int(x)
                        act_y = rect[1] + int(y)
                    return act_x, act_y, -1
                except Exception:
                    pass

        mon_idx, mon_rect = self.capture.resolve_target_monitor(monitor)

        if normalized:
            norm_x = max(0.0, min(1000.0, float(x))) / 1000.0
            norm_y = max(0.0, min(1000.0, float(y))) / 1000.0
            actual_x = mon_rect["left"] + int(norm_x * mon_rect["width"])
            actual_y = mon_rect["top"] + int(norm_y * mon_rect["height"])
        else:
            actual_x = int(x)
            actual_y = int(y)

        return actual_x, actual_y, mon_idx

    def move_cursor(
        self,
        x: float,
        y: float,
        monitor: str | int = "auto",
        normalized: bool = True,
        app_name: Optional[str] = None
    ) -> Dict:
        """Positions cursor at target coordinates."""
        ensure_thread_desktop()
        act_x, act_y, mon_idx = self.translate_coords(x, y, monitor, normalized, app_name=app_name)
        ctypes.windll.user32.SetCursorPos(act_x, act_y)
        return {
            "status": "success",
            "action": "move_cursor",
            "coords": (act_x, act_y),
            "monitor": mon_idx,
            "target_app": app_name
        }

    def click(
        self,
        x: float,
        y: float,
        button: str = "left",
        clicks: int = 1,
        monitor: str | int = "auto",
        normalized: bool = True,
        app_name: Optional[str] = None
    ) -> Dict:
        """
        Moves the cursor and performs mouse click(s) at target coordinates.
        Brings target window to the foreground first so clicks are not swallowed by WM_MOUSEACTIVATE.
        Uses 60ms hover settle time and 40ms button-down dwell time for reliable UI registration.
        """
        ensure_thread_desktop()

        # If targeting an application window, bring to foreground first
        if app_name:
            focus_window(app_name)
            time.sleep(0.08)

        act_x, act_y, mon_idx = self.translate_coords(x, y, monitor, normalized, app_name=app_name)

        ctypes.windll.user32.SetCursorPos(act_x, act_y)
        time.sleep(0.06)  # 60ms hover settle time

        btn_lower = button.lower().strip()
        if btn_lower == "right":
            down_flag, up_flag = MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
        elif btn_lower == "middle":
            down_flag, up_flag = MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP
        else:
            down_flag, up_flag = MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP

        for i in range(max(1, clicks)):
            # Down
            down_input = INPUT(type=INPUT_MOUSE)
            down_input.mi = MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=down_flag, time=0, dwExtraInfo=0)
            res_down = ctypes.windll.user32.SendInput(1, ctypes.byref(down_input), ctypes.sizeof(INPUT))
            if res_down == 0:
                ctypes.windll.user32.mouse_event(down_flag, 0, 0, 0, 0)
            time.sleep(0.04)  # 40ms button-down dwell time

            # Up
            up_input = INPUT(type=INPUT_MOUSE)
            up_input.mi = MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=up_flag, time=0, dwExtraInfo=0)
            res_up = ctypes.windll.user32.SendInput(1, ctypes.byref(up_input), ctypes.sizeof(INPUT))
            if res_up == 0:
                ctypes.windll.user32.mouse_event(up_flag, 0, 0, 0, 0)

            if i < clicks - 1:
                time.sleep(0.06)

        target_desc = f"window '{app_name}'" if app_name else f"Monitor {mon_idx}"
        return {
            "status": "success",
            "action": f"{btn_lower}_click",
            "clicks": clicks,
            "coords": (act_x, act_y),
            "monitor": mon_idx,
            "target_app": app_name,
            "message": f"Clicked at ({act_x}, {act_y}) on {target_desc}."
        }

    def type_text(
        self,
        text: str,
        press_enter: bool = False,
        app_name: Optional[str] = None
    ) -> Dict:
        """
        Types Unicode characters into the active field, optionally focusing the target window first.
        """
        ensure_thread_desktop()
        if not text:
            return {"status": "success", "action": "type_text", "length": 0}

        focus_res = None
        if app_name:
            focus_res = focus_window(app_name)
            time.sleep(0.08)

        inputs = []
        for char in text:
            code = ord(char)
            ki_down = INPUT(type=INPUT_KEYBOARD)
            ki_down.ki = KEYBDINPUT(wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=0)
            inputs.append(ki_down)

            ki_up = INPUT(type=INPUT_KEYBOARD)
            ki_up.ki = KEYBDINPUT(wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
            inputs.append(ki_up)

        if press_enter:
            enter_down = INPUT(type=INPUT_KEYBOARD)
            enter_down.ki = KEYBDINPUT(wVk=VK_RETURN, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)
            inputs.append(enter_down)

            enter_up = INPUT(type=INPUT_KEYBOARD)
            enter_up.ki = KEYBDINPUT(wVk=VK_RETURN, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
            inputs.append(enter_up)

        n_inputs = len(inputs)
        input_array = (INPUT * n_inputs)(*inputs)
        res = ctypes.windll.user32.SendInput(n_inputs, input_array, ctypes.sizeof(INPUT))

        # Dual-layer fallback to keybd_event if SendInput missed any inputs
        if res < n_inputs:
            for char in text:
                code = ord(char)
                ctypes.windll.user32.keybd_event(0, code, KEYEVENTF_UNICODE, 0)
                ctypes.windll.user32.keybd_event(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0)
            if press_enter:
                ctypes.windll.user32.keybd_event(VK_RETURN, 0, 0, 0)
                ctypes.windll.user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)

        return {
            "status": "success",
            "action": "type_text",
            "typed_chars": len(text),
            "pressed_enter": press_enter,
            "target_app": app_name,
            "focus": focus_res,
            "message": f"Typed '{text}'" + (" and pressed Enter." if press_enter else ".")
        }

    def press_key(self, key_combo: str) -> Dict:
        """Presses a single key or key shortcut combination (e.g. ctrl+t, enter, escape)."""
        ensure_thread_desktop()
        tokens = [t.strip().lower() for t in key_combo.split("+") if t.strip()]
        if not tokens:
            return {"status": "error", "message": "Empty key combo"}

        vk_codes = []
        for t in tokens:
            if t in KEY_MAP:
                vk_codes.append(KEY_MAP[t])
            elif len(t) == 1:
                vk_codes.append(ord(t.upper()))
            elif t.startswith("f") and t[1:].isdigit():
                num = int(t[1:])
                if 1 <= num <= 12:
                    vk_codes.append(0x70 + num - 1)
            else:
                return {"status": "error", "message": f"Unrecognized key: {t}"}

        # Press forward
        for vk in vk_codes:
            inp = INPUT(type=INPUT_KEYBOARD)
            inp.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)
            ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            time.sleep(0.01)

        # Release reverse
        for vk in reversed(vk_codes):
            inp = INPUT(type=INPUT_KEYBOARD)
            inp.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
            ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            time.sleep(0.01)

        return {
            "status": "success",
            "action": "press_key",
            "combo": key_combo,
            "message": f"Pressed key combo: {key_combo}"
        }

    def scroll(self, amount: int = 3, direction: str = "down") -> Dict:
        """Scrolls mouse wheel up or down."""
        ensure_thread_desktop()
        dir_clean = direction.lower().strip()
        delta = -120 * max(1, amount) if dir_clean == "down" else 120 * max(1, amount)

        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(dx=0, dy=0, mouseData=delta, dwFlags=MOUSEEVENTF_WHEEL, time=0, dwExtraInfo=0)
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

        return {
            "status": "success",
            "action": "scroll",
            "direction": dir_clean,
            "amount": amount,
            "message": f"Scrolled {dir_clean} by {amount} steps."
        }

