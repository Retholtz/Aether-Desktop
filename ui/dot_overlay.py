"""
ui/dot_overlay.py - Native Per-Pixel-Alpha Click-Through Floating Status Dot Overlay.

Renders an unobtrusive, zero-box, anti-aliased glowing status dot on Windows using a
native Win32 layered popup window (`WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
| WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE`) with `UpdateLayeredWindow`.
"""

from __future__ import annotations

import math
import os
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from core.logger import get_logger

logger = get_logger("DotOverlay")

DEFAULT_HUD_STATUS_COLORS: Dict[str, str] = {
    "standby": "#8e9bb0",
    "listening": "#00d2ff",
    "thinking": "#bd00ff",
    "executing": "#ff9f1c",
    "speaking": "#06d6a0",
}

VALID_DOT_POSITIONS = ("top_left", "top_right", "bottom_left", "bottom_right")

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def sanitize_hud_colors(raw: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Returns a complete 5-key HUD status color map with valid #RRGGBB hex strings."""
    colors = dict(DEFAULT_HUD_STATUS_COLORS)
    if isinstance(raw, dict):
        for key in DEFAULT_HUD_STATUS_COLORS:
            val = str(raw.get(key) or "").strip()
            if _HEX_COLOR_RE.match(val):
                colors[key] = val.lower()
    return colors


def normalize_dot_size(size: Any) -> int:
    """Clamps dot diameter in pixels to a safe visible range (12..48)."""
    try:
        val = int(round(float(size)))
    except (TypeError, ValueError):
        val = 22
    return max(12, min(48, val))


def normalize_dot_position(position: Any) -> str:
    """Normalizes corner anchor name into one of VALID_DOT_POSITIONS."""
    pos = str(position or "top_right").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "upper_left": "top_left",
        "left_upper": "top_left",
        "upper_right": "top_right",
        "right_upper": "top_right",
        "lower_left": "bottom_left",
        "left_lower": "bottom_left",
        "lower_right": "bottom_right",
        "right_lower": "bottom_right",
    }
    pos = aliases.get(pos, pos)
    return pos if pos in VALID_DOT_POSITIONS else "top_right"


def resolve_status_bucket(state: Optional[str]) -> str:
    """Maps any engine status string to one of the 5 customizable color buckets (or 'error')."""
    s = str(state or "standby").strip().lower()
    if s in ("standby", "idle", "disconnected", "offline", ""):
        return "standby"
    if s in ("listening", "hearing", "connected"):
        return "listening"
    if s in ("thinking", "transcribing"):
        return "thinking"
    if s in ("executing", "tool", "working"):
        return "executing"
    if s in ("speaking",):
        return "speaking"
    if s in ("error",):
        return "error"
    return "listening"


def resolve_status_color(state: Optional[str], custom_colors: Optional[Dict[str, Any]] = None) -> str:
    """Resolves the #RRGGBB color for a given engine status using user-configured colors."""
    bucket = resolve_status_bucket(state)
    if bucket == "error":
        return "#ef476f"
    palette = sanitize_hud_colors(custom_colors)
    return palette.get(bucket, DEFAULT_HUD_STATUS_COLORS["standby"])


def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    clean = str(hex_color or "#8e9bb0").strip().lstrip("#")
    if len(clean) != 6:
        clean = "8e9bb0"
    try:
        return (int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16))
    except ValueError:
        return (142, 155, 176)


def resolve_dot_position(
    position: str,
    monitor_id: Any,
    canvas_size: int,
    monitors: Optional[List[Dict[str, Any]]] = None,
    active_window_rect: Optional[Tuple[int, int, int, int]] = None,
    margin: int = 16,
) -> Tuple[int, int]:
    """
    Computes physical screen (x, y) coordinates for the dot canvas on the requested
    monitor and corner (`top_left`, `top_right`, `bottom_left`, `bottom_right`).
    """
    pos = normalize_dot_position(position)
    mon_list = list(monitors or [])
    if not mon_list:
        mon_list = [{"left": 0, "top": 0, "right": 1920, "bottom": 1080, "is_primary": True}]

    primary = next((m for m in mon_list if m.get("is_primary")), mon_list[0])
    target_mon = primary

    mon_str = str(monitor_id if monitor_id is not None else "auto").strip().lower()
    if mon_str and mon_str != "auto":
        try:
            idx = int(mon_str) - 1  # 1-based UI monitor index ("1", "2", "3"...)
            if 0 <= idx < len(mon_list):
                target_mon = mon_list[idx]
        except ValueError:
            pass
    elif active_window_rect is not None:
        l, t, r, b = active_window_rect
        if r > l and b > t:
            cx = (l + r) // 2
            cy = (t + b) // 2
            for m in mon_list:
                if m["left"] <= cx < m["right"] and m["top"] <= cy < m["bottom"]:
                    target_mon = m
                    break

    left = int(target_mon.get("left", 0))
    top = int(target_mon.get("top", 0))
    right = int(target_mon.get("right", left + 1920))
    bottom = int(target_mon.get("bottom", top + 1080))
    c_size = max(16, int(canvas_size))

    if pos == "top_left":
        x = left + margin
        y = top + margin
    elif pos == "bottom_left":
        x = left + margin
        y = bottom - c_size - margin
    elif pos == "bottom_right":
        x = right - c_size - margin
        y = bottom - c_size - margin
    else:  # top_right
        x = right - c_size - margin
        y = top + margin

    x = max(left, min(x, max(left, right - c_size)))
    y = max(top, min(y, max(top, bottom - c_size)))
    return (int(x), int(y))


_GEOMETRY_CACHE: Dict[int, Tuple[Any, Any, Any, int]] = {}


def _get_dot_geometry(diameter: int):
    """Caches precomputed radial distance grids per dot diameter so pulse frames cost ~0% CPU."""
    import numpy as np

    cached = _GEOMETRY_CACHE.get(diameter)
    if cached is not None:
        return cached
    radius = diameter / 2.0
    glow_pad = max(8, int(round(diameter * 0.55)))
    canvas = int(diameter + glow_pad * 2)
    center = (canvas - 1) / 2.0
    yy, xx = np.mgrid[0:canvas, 0:canvas].astype(np.float32)
    dist = np.sqrt((xx - center) ** 2 + (yy - center) ** 2)
    hl_dist = np.sqrt((xx - (center - radius * 0.28)) ** 2 + (yy - (center - radius * 0.28)) ** 2)
    core_alpha = np.clip((radius - dist) / 1.25 + 0.5, 0.0, 1.0)
    rim_mask = np.clip(1.0 - np.abs(dist - (radius + 0.5)) / 1.2, 0.0, 1.0) * 0.45
    hl_boost = (np.clip(1.0 - hl_dist / max(1.0, radius * 0.65), 0.0, 1.0) ** 2) * 0.35 * core_alpha
    res = (dist, core_alpha, rim_mask, hl_boost, radius, glow_pad, canvas)
    if len(_GEOMETRY_CACHE) > 32:
        _GEOMETRY_CACHE.clear()
    _GEOMETRY_CACHE[diameter] = res
    return res


def render_dot_bgra(
    dot_size: int,
    hex_color: str,
    is_standby: bool = False,
    pulse_phase: float = 0.0,
) -> Tuple[bytes, int]:
    """
    Renders a smooth anti-aliased glowing dot into a premultiplied 32-bit BGRA buffer
    suitable for Win32 `UpdateLayeredWindow` (AC_SRC_ALPHA).
    Returns `(bgra_bytes, canvas_size)`.
    """
    import numpy as np

    diameter = normalize_dot_size(dot_size)
    dist, core_alpha, rim_mask, hl_boost, radius, glow_pad, canvas = _get_dot_geometry(diameter)

    r_col, g_col, b_col = hex_to_rgb(hex_color)
    pulse = 0.5 + 0.5 * math.sin(pulse_phase) if not is_standby else 0.0

    # Subtle outer glow halo (stronger & pulsing when active, faint when standby)
    glow_radius = radius + glow_pad * (0.65 + 0.25 * pulse)
    glow_strength = 0.16 if is_standby else (0.45 + 0.25 * pulse)
    glow_norm = np.clip(1.0 - np.maximum(0.0, dist - radius * 0.85) / max(1.0, (glow_radius - radius * 0.85)), 0.0, 1.0)
    glow_alpha = (glow_norm ** 2.2) * glow_strength

    total_alpha = np.clip(np.maximum(core_alpha, glow_alpha), 0.0, 1.0)
    total_alpha = np.clip(np.maximum(total_alpha, rim_mask), 0.0, 1.0)
    if is_standby:
        total_alpha = total_alpha * 0.82

    # Blend RGB (dark rim outside core, pure color + specular highlight inside core)
    color_weight = np.where(core_alpha > 0.01, 1.0, np.clip(glow_alpha / np.maximum(total_alpha, 1e-5), 0.0, 1.0))
    r_ch = np.clip(r_col * color_weight + 255.0 * hl_boost, 0.0, 255.0)
    g_ch = np.clip(g_col * color_weight + 255.0 * hl_boost, 0.0, 255.0)
    b_ch = np.clip(b_col * color_weight + 255.0 * hl_boost, 0.0, 255.0)

    # Win32 UpdateLayeredWindow expects premultiplied BGRA
    a_u8 = np.clip(np.round(total_alpha * 255.0), 0, 255).astype(np.uint8)
    alpha_f = a_u8.astype(np.float32) / 255.0
    b_u8 = np.clip(np.round(b_ch * alpha_f), 0, 255).astype(np.uint8)
    g_u8 = np.clip(np.round(g_ch * alpha_f), 0, 255).astype(np.uint8)
    r_u8 = np.clip(np.round(r_ch * alpha_f), 0, 255).astype(np.uint8)

    bgra = np.stack([b_u8, g_u8, r_u8, a_u8], axis=-1)
    return bgra.tobytes(), canvas


class DotOverlayWindow:
    """
    Manages the floating, click-through, per-pixel-alpha status dot window on Windows.
    Thread-safe: callers from any thread can invoke show(), hide(), set_state(),
    configure(), and destroy().
    """

    def __init__(
        self,
        size: int = 22,
        position: str = "top_right",
        monitor: str = "auto",
        colors: Optional[Dict[str, str]] = None,
    ):
        self._lock = threading.Lock()
        self._size = normalize_dot_size(size)
        self._position = normalize_dot_position(position)
        self._monitor = str(monitor or "auto")
        self._colors = sanitize_hud_colors(colors)
        self._state = "standby"
        self._visible = False
        self._active_game_process: str = ""
        self._destroyed = False
        self._dirty = True

        self._thread: Optional[threading.Thread] = None
        self._hwnd: Optional[int] = None
        self._ready_event = threading.Event()
        self._stop_event = threading.Event()

    @property
    def is_visible(self) -> bool:
        with self._lock:
            return self._visible

    def get_settings(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "dot_size": self._size,
                "dot_position": self._position,
                "dot_monitor": self._monitor,
                "hud_colors": dict(self._colors),
                "state": self._state,
                "visible": self._visible,
            }

    def configure(
        self,
        size: Optional[int] = None,
        position: Optional[str] = None,
        monitor: Optional[str] = None,
        colors: Optional[Dict[str, str]] = None,
        active_game_process: Optional[str] = None,
    ) -> None:
        with self._lock:
            if size is not None:
                self._size = normalize_dot_size(size)
            if position is not None:
                self._position = normalize_dot_position(position)
            if monitor is not None:
                self._monitor = str(monitor or "auto")
            if colors is not None:
                self._colors = sanitize_hud_colors(colors)
            if active_game_process is not None:
                self._active_game_process = str(active_game_process or "").strip()
            self._dirty = True

    def set_state(self, state: str) -> None:
        clean = str(state or "standby").strip().lower()
        with self._lock:
            if self._state != clean:
                self._state = clean
                self._dirty = True

    def show(self) -> None:
        with self._lock:
            if self._destroyed:
                return
            self._visible = True
            self._dirty = True
        if sys.platform == "win32" and os.environ.get("AETHER_TESTING") != "1":
            self._ensure_thread_started()

    def hide(self) -> None:
        with self._lock:
            self._visible = False
            self._dirty = True

    def destroy(self) -> None:
        with self._lock:
            self._visible = False
            self._destroyed = True
            self._dirty = True
        self._stop_event.set()
        t = self._thread
        if t is not None and t.is_alive() and threading.current_thread() is not t:
            try:
                t.join(timeout=1.0)
            except Exception:
                pass

    def _ensure_thread_started(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._win32_loop,
            daemon=True,
            name="AetherDotOverlay",
        )
        self._thread.start()

    def _get_active_game_rect(self, proc_name: str) -> Optional[Tuple[int, int, int, int]]:
        if not proc_name or sys.platform != "win32":
            return None
        try:
            import win32gui
            from tools.os_controls import find_window_hwnd

            hwnd = find_window_hwnd(proc_name)
            if hwnd and win32gui.IsWindow(hwnd):
                l, t, r, b = win32gui.GetWindowRect(hwnd)
                if r > l and b > t:
                    return (int(l), int(t), int(r), int(b))
        except Exception:
            pass
        return None

    def _win32_loop(self) -> None:
        """Runs the lightweight Win32 layered popup window pump + pulse animator."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes
            from ui.hud_window import get_monitor_work_areas

            # Use isolated WinDLL handles with explicit 64-bit argtypes/restype so ASLR
            # 64-bit pointers (HINSTANCE, HWND, HDC, HBITMAP) never overflow c_int.
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

            LRESULT = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", wintypes.DWORD),
                    ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD),
                ]

            class BITMAPINFO(ctypes.Structure):
                _fields_ = [
                    ("bmiHeader", BITMAPINFOHEADER),
                    ("bmiColors", wintypes.DWORD * 3),
                ]

            class BLENDFUNCTION(ctypes.Structure):
                _fields_ = [
                    ("BlendOp", ctypes.c_ubyte),
                    ("BlendFlags", ctypes.c_ubyte),
                    ("SourceConstantAlpha", ctypes.c_ubyte),
                    ("AlphaFormat", ctypes.c_ubyte),
                ]

            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE

            user32.CreateWindowExW.argtypes = [
                wintypes.DWORD,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.HWND,
                wintypes.HMENU,
                wintypes.HINSTANCE,
                wintypes.LPVOID,
            ]
            user32.CreateWindowExW.restype = wintypes.HWND

            user32.GetDC.argtypes = [wintypes.HWND]
            user32.GetDC.restype = wintypes.HDC
            user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
            user32.ReleaseDC.restype = ctypes.c_int

            user32.UpdateLayeredWindow.argtypes = [
                wintypes.HWND,
                wintypes.HDC,
                ctypes.POINTER(wintypes.POINT),
                ctypes.POINTER(wintypes.SIZE),
                wintypes.HDC,
                ctypes.POINTER(wintypes.POINT),
                wintypes.COLORREF,
                ctypes.POINTER(BLENDFUNCTION),
                wintypes.DWORD,
            ]
            user32.UpdateLayeredWindow.restype = wintypes.BOOL

            user32.SetWindowPos.argtypes = [
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            user32.SetWindowPos.restype = wintypes.BOOL

            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.ShowWindow.restype = wintypes.BOOL
            user32.DestroyWindow.argtypes = [wintypes.HWND]
            user32.DestroyWindow.restype = wintypes.BOOL

            user32.PeekMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
            ]
            user32.PeekMessageW.restype = wintypes.BOOL
            user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.TranslateMessage.restype = wintypes.BOOL
            user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.DispatchMessageW.restype = LRESULT

            user32.MsgWaitForMultipleObjects.argtypes = [
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.BOOL,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            user32.MsgWaitForMultipleObjects.restype = wintypes.DWORD

            gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
            gdi32.CreateCompatibleDC.restype = wintypes.HDC
            gdi32.DeleteDC.argtypes = [wintypes.HDC]
            gdi32.DeleteDC.restype = wintypes.BOOL
            gdi32.CreateDIBSection.argtypes = [
                wintypes.HDC,
                ctypes.POINTER(BITMAPINFO),
                wintypes.UINT,
                ctypes.POINTER(ctypes.c_void_p),
                wintypes.HANDLE,
                wintypes.DWORD,
            ]
            gdi32.CreateDIBSection.restype = wintypes.HBITMAP
            gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
            gdi32.SelectObject.restype = wintypes.HGDIOBJ
            gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
            gdi32.DeleteObject.restype = wintypes.BOOL

            # Use Windows' built-in native C++ "STATIC" class (zero Python WNDPROC callback)
            # so synchronous Win32 SendMessage/broadcast calls from WinForms/WebView2 never
            # require acquiring Python's GIL and can never deadlock the main UI thread.
            hinstance = kernel32.GetModuleHandleW(None)
            WS_POPUP = 0x80000000
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_TOPMOST = 0x00000008
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_NOACTIVATE = 0x08000000
            ex_style = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE

            hwnd = user32.CreateWindowExW(
                ex_style,
                "STATIC",
                "Aether Status Dot",
                WS_POPUP,
                0,
                0,
                64,
                64,
                None,
                None,
                hinstance,
                None,
            )
            if not hwnd:
                logger.warning("Failed to create Win32 layered Dot Overlay window (err=%s).", ctypes.get_last_error())
                return

            self._hwnd = int(hwnd)
            self._ready_event.set()

            SW_HIDE = 0
            SW_SHOWNOACTIVATE = 4
            HWND_TOPMOST = wintypes.HWND(-1)
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOACTIVATE = 0x0010
            ULW_ALPHA = 0x00000002
            AC_SRC_OVER = 0x00
            AC_SRC_ALPHA = 0x01
            DIB_RGB_COLORS = 0
            PM_REMOVE = 0x0001
            QS_ALLINPUT = 0x04FF

            msg = wintypes.MSG()
            currently_shown = False
            pulse_phase = 0.0
            last_frame_t = time.monotonic()
            last_pos_key = None
            last_pos_check_t = 0.0
            last_topmost_t = 0.0
            cached_pos = (0, 0)

            def _pump_messages():
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))

            def _paint_layered(dot_size: int, hex_col: str, is_standby: bool, phase: float, pos: str, mon_id: str, game_proc: str, force_topmost: bool = False):
                nonlocal last_pos_key, last_pos_check_t, last_topmost_t, cached_pos
                bgra_bytes, canvas = render_dot_bgra(dot_size, hex_col, is_standby=is_standby, pulse_phase=phase)

                now_t = time.monotonic()
                pos_key = (pos, mon_id, canvas, game_proc)
                # Only re-query OS monitors / active game HWND when config changes (or every 2.5s in auto-game mode)
                if pos_key != last_pos_key or (str(mon_id).lower() == "auto" and game_proc and (now_t - last_pos_check_t) >= 2.5):
                    monitors = get_monitor_work_areas()
                    game_rect = self._get_active_game_rect(game_proc) if (str(mon_id).lower() == "auto" and game_proc) else None
                    cached_pos = resolve_dot_position(pos, mon_id, canvas, monitors=monitors, active_window_rect=game_rect)
                    last_pos_key = pos_key
                    last_pos_check_t = now_t

                px, py = cached_pos

                hdc_screen = user32.GetDC(None)
                hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
                bmi = BITMAPINFO()
                bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi.bmiHeader.biWidth = canvas
                bmi.bmiHeader.biHeight = -canvas  # top-down DIB
                bmi.bmiHeader.biPlanes = 1
                bmi.bmiHeader.biBitCount = 32
                bmi.bmiHeader.biCompression = 0  # BI_RGB

                bits_ptr = ctypes.c_void_p()
                hbmp = gdi32.CreateDIBSection(hdc_mem, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits_ptr), None, 0)
                if hbmp and bits_ptr.value:
                    old_bmp = gdi32.SelectObject(hdc_mem, hbmp)
                    ctypes.memmove(bits_ptr.value, bgra_bytes, len(bgra_bytes))

                    pt_dst = wintypes.POINT(px, py)
                    pt_src = wintypes.POINT(0, 0)
                    sz = wintypes.SIZE(canvas, canvas)
                    blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)

                    user32.UpdateLayeredWindow(
                        hwnd,
                        hdc_screen,
                        ctypes.byref(pt_dst),
                        ctypes.byref(sz),
                        hdc_mem,
                        ctypes.byref(pt_src),
                        0,
                        ctypes.byref(blend),
                        ULW_ALPHA,
                    )
                    if force_topmost or (now_t - last_topmost_t) >= 3.0:
                        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                        last_topmost_t = now_t
                    gdi32.SelectObject(hdc_mem, old_bmp)
                    gdi32.DeleteObject(hbmp)
                gdi32.DeleteDC(hdc_mem)
                user32.ReleaseDC(None, hdc_screen)

            while not self._stop_event.is_set():
                _pump_messages()

                with self._lock:
                    visible = self._visible
                    dirty = self._dirty
                    size = self._size
                    pos = self._position
                    mon = self._monitor
                    colors = dict(self._colors)
                    state = self._state
                    game_proc = self._active_game_process
                    self._dirty = False

                if not visible:
                    if currently_shown:
                        user32.ShowWindow(hwnd, SW_HIDE)
                        currently_shown = False
                    # Wake immediately on any Win32 message (0ms delay), or after 80ms
                    user32.MsgWaitForMultipleObjects(0, None, False, 80, QS_ALLINPUT)
                    continue

                bucket = resolve_status_bucket(state)
                is_standby = (bucket == "standby")
                hex_col = resolve_status_color(state, colors)
                now = time.monotonic()
                just_shown = False

                if not currently_shown:
                    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                    currently_shown = True
                    just_shown = True
                    dirty = True

                # Gentle ~12 FPS breathing pulse while active; 0% CPU while in standby
                if not is_standby and (now - last_frame_t >= 0.085):
                    pulse_phase = (pulse_phase + 0.24) % (2.0 * math.pi)
                    dirty = True

                if dirty:
                    _paint_layered(size, hex_col, is_standby, pulse_phase, pos, mon, game_proc, force_topmost=just_shown)
                    last_frame_t = now

                wait_ms = 60 if not is_standby else 120
                user32.MsgWaitForMultipleObjects(0, None, False, wait_ms, QS_ALLINPUT)

            try:
                user32.ShowWindow(hwnd, SW_HIDE)
                user32.DestroyWindow(hwnd)
            except Exception:
                pass
            self._hwnd = None
        except Exception as e:
            logger.warning("DotOverlay Win32 loop error: %s", e)
