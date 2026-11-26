"""
Aether Desktop - UI Subsystem: HUD Window Runner
Manages pywebview window initialization, styling, floating HUD overlay, system tray, and GUI lifecycle.
"""

import json
import os
import sys
import threading
import time
import webview
from pathlib import Path
from typing import Optional

from core.paths import get_resource_dir, get_webview_cache_dir
from core.screen_stream import ensure_thread_desktop
from ui.tray import SystemTrayManager


_CACHED_USER32 = None


def _get_user32():
    """
    Returns an isolated, 64-bit-typed ctypes.WinDLL('user32') handle.
    Unlike pywin32's win32gui (which holds the Python GIL during cross-thread
    SetWindowLong/SetWindowPos calls and can deadlock against WinForms/WebView2),
    ctypes.WinDLL ALWAYS releases the GIL before invoking Win32 APIs.
    """
    global _CACHED_USER32
    if _CACHED_USER32 is not None:
        return _CACHED_USER32
    import ctypes
    from ctypes import wintypes

    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    u32.FindWindowW.restype = wintypes.HWND
    u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u32.ShowWindow.restype = wintypes.BOOL
    u32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    u32.SetWindowPos.restype = wintypes.BOOL
    u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u32.GetWindowRect.restype = wintypes.BOOL
    u32.IsWindow.argtypes = [wintypes.HWND]
    u32.IsWindow.restype = wintypes.BOOL
    if hasattr(u32, "GetWindowLongPtrW"):
        u32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    else:
        u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u32.GetWindowLongW.restype = ctypes.c_long
        u32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        u32.SetWindowLongW.restype = ctypes.c_long
    _CACHED_USER32 = u32
    return u32


def _get_win_exstyle(u32, hwnd: int, gwl_exstyle: int) -> int:
    if hasattr(u32, "GetWindowLongPtrW"):
        return int(u32.GetWindowLongPtrW(int(hwnd), int(gwl_exstyle)))
    return int(u32.GetWindowLongW(int(hwnd), int(gwl_exstyle)))


def _set_win_exstyle(u32, hwnd: int, gwl_exstyle: int, new_style: int) -> int:
    if hasattr(u32, "SetWindowLongPtrW"):
        return int(u32.SetWindowLongPtrW(int(hwnd), int(gwl_exstyle), int(new_style)))
    return int(u32.SetWindowLongW(int(hwnd), int(gwl_exstyle), int(new_style)))


def force_foreground_window(hwnd: int, maximize: bool = False):
    """
    Brings a window to the foreground and unminimizes/maximizes it reliably on Windows,
    bypassing foreground lock restrictions by attaching input threads.
    """
    if sys.platform != "win32" or not hwnd:
        return
    try:
        import ctypes
        import win32con

        user32 = ctypes.windll.user32
        u32 = _get_user32()
        user32.AllowSetForegroundWindow(-1)

        cur_thread_id = user32.GetCurrentThreadId()
        fore_hwnd = user32.GetForegroundWindow()
        fore_thread_id = user32.GetWindowThreadProcessId(fore_hwnd, 0) if fore_hwnd else 0

        attached = False
        if fore_thread_id and fore_thread_id != cur_thread_id:
            attached = bool(user32.AttachThreadInput(cur_thread_id, fore_thread_id, True))

        try:
            cmd = win32con.SW_MAXIMIZE if maximize else win32con.SW_RESTORE
            u32.ShowWindow(int(hwnd), cmd)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(cur_thread_id, fore_thread_id, False)
    except Exception as e:
        print(f"[HUD] force_foreground_window error: {e}")


def _get_overlay_hwnd(window: Optional[webview.Window]) -> Optional[int]:
    """Safely resolves the native Win32 HWND for the HUD overlay window."""
    if sys.platform != "win32" or not window:
        return None
    try:
        native = getattr(window, "native", None)
        if native is not None and hasattr(native, "Handle"):
            try:
                hwnd = int(native.Handle.ToInt64())
                if hwnd:
                    return hwnd
            except Exception:
                pass
        u32 = _get_user32()
        hwnd = u32.FindWindowW(None, "Aether Overlay")
        return int(hwnd) if hwnd else None
    except Exception:
        return None


def _get_dpi_scale_for_window(window: Optional[webview.Window] = None) -> float:
    """Returns the effective DPI scale factor (e.g. 1.0, 1.25, 1.5, 2.0) for the overlay window or primary display."""
    if sys.platform == "win32":
        try:
            import ctypes
            hwnd = _get_overlay_hwnd(window)
            if hwnd:
                dpi = ctypes.windll.user32.GetDpiForWindow(int(hwnd))
                if dpi and dpi >= 96:
                    return float(dpi) / 96.0
        except Exception:
            pass
    try:
        monitors = get_monitor_work_areas()
        primary = next((m for m in monitors if m.get("is_primary")), monitors[0])
        scale = float(primary.get("scale", 1.0) or 1.0)
        if scale > 0:
            return scale
    except Exception:
        pass
    return 1.0


def _get_overlay_physical_rect(window: Optional[webview.Window]) -> Optional[tuple[int, int, int, int]]:
    """Returns (x, y, width, height) of the overlay window in Win32 physical screen pixels."""
    if sys.platform == "win32" and window is not None:
        hwnd = _get_overlay_hwnd(window)
        if hwnd:
            try:
                import win32gui
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                return (int(left), int(top), max(1, int(right - left)), max(1, int(bottom - top)))
            except Exception:
                pass
    if window is not None:
        try:
            x = getattr(window, "x", None)
            y = getattr(window, "y", None)
            w = getattr(window, "width", None)
            h = getattr(window, "height", None)
            if x is not None and y is not None and w is not None and h is not None:
                scale = _get_dpi_scale_for_window(window)
                return (
                    int(round(x * scale)),
                    int(round(y * scale)),
                    max(1, int(round(w * scale))),
                    max(1, int(round(h * scale))),
                )
        except Exception:
            pass
    return None


def move_overlay_physical(window: Optional[webview.Window], phys_x: int, phys_y: int) -> bool:
    """
    Moves the HUD overlay to exact Win32 physical screen coordinates (phys_x, phys_y)
    without pywebview WinForms double-multiplying by DPI scale.
    Falls back to DPI-adjusted window.move() when no native HWND exists (e.g. unit tests / non-Windows).
    """
    if window is None:
        return False
    if sys.platform == "win32":
        hwnd = _get_overlay_hwnd(window)
        if hwnd:
            try:
                import ctypes
                SWP_NOSIZE = 0x0001
                SWP_NOZORDER = 0x0004
                SWP_NOACTIVATE = 0x0010
                ctypes.windll.user32.SetWindowPos(
                    int(hwnd),
                    0,
                    int(phys_x),
                    int(phys_y),
                    0,
                    0,
                    SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
                )
                return True
            except Exception:
                pass
    try:
        scale = _get_dpi_scale_for_window(window)
        window.move(int(round(phys_x / scale)), int(round(phys_y / scale)))
        return True
    except Exception:
        return False


def apply_hud_window_shape(window: Optional[webview.Window], mode: str):
    """
    Applies OS-level window styling for the HUD overlay.
    Avoids Win32 GDI SetWindowRgn which conflicts with Chromium/WebView2
    DirectComposition GPU rendering and causes the overlay window to render blank.
    On Windows 11+, requests native DWM rounded corners via DwmSetWindowAttribute;
    CSS border-radius handles visual card rounding across all modes.
    """
    if sys.platform != "win32" or not window:
        return
    try:
        import ctypes

        u32 = _get_user32()
        hwnd = None
        if hasattr(window, "native") and window.native and hasattr(window.native, "Handle"):
            try:
                hwnd = int(window.native.Handle.ToInt64())
            except Exception:
                hwnd = None
        if not hwnd:
            hwnd = int(u32.FindWindowW(None, "Aether Overlay") or 0)
        if not hwnd:
            return

        # On Windows 11+ (build >= 22000), enable DWM native rounded corners cleanly for card modes
        norm_mode = (mode or "").lower()
        try:
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_ROUND = 2
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(ctypes.c_int(DWMWCP_ROUND)),
                ctypes.sizeof(ctypes.c_int)
            )
        except Exception:
            pass

        # Apply WS_EX_TOOLWINDOW so the floating HUD overlay doesn't pollute Windows 11 Alt+Tab / taskbar,
        # and WS_EX_NOACTIVATE in mini mode so it never steals game focus.
        # Uses GIL-releasing ctypes User32 calls (NEVER win32gui.SetWindowLong/SetWindowPos, which hold
        # the Python GIL while sending cross-thread WM_STYLECHANGING and deadlock WinForms/WebView2).
        try:
            import win32con
            ex_style = _get_win_exstyle(u32, hwnd, win32con.GWL_EXSTYLE)
            new_style = (ex_style | win32con.WS_EX_TOOLWINDOW) & ~win32con.WS_EX_APPWINDOW
            if norm_mode == "mini":
                new_style |= win32con.WS_EX_NOACTIVATE
            else:
                new_style &= ~win32con.WS_EX_NOACTIVATE
            new_style &= ~win32con.WS_EX_TRANSPARENT
            if new_style != ex_style:
                _set_win_exstyle(u32, hwnd, win32con.GWL_EXSTYLE, new_style)
                u32.SetWindowPos(
                    hwnd,
                    win32con.HWND_TOPMOST,
                    0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | win32con.SWP_FRAMECHANGED
                )
        except Exception:
            pass
    except Exception as e:
        print(f"[HUD SHAPE ERROR] Failed to apply window shape: {e}")


def get_monitor_work_areas() -> list:
    """
    Returns a list of connected monitor work areas (excluding taskbars)
    in the same physical screen coordinate space used by DPI-aware WinForms/PyWebView.
    """
    monitors = []
    if sys.platform == "win32":
        try:
            import win32api
            import ctypes
            for hmon, _, _ in win32api.EnumDisplayMonitors():
                info = win32api.GetMonitorInfo(hmon)
                work = info.get("Work") or info.get("rcWork") or info.get("Monitor")
                is_primary = bool(info.get("Flags", 0) & 1)
                if work:
                    scale = 1.0
                    try:
                        dpi_x = ctypes.c_uint()
                        dpi_y = ctypes.c_uint()
                        ctypes.windll.shcore.GetDpiForMonitor(int(hmon), 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
                        if dpi_x.value > 0:
                            scale = dpi_x.value / 96.0
                    except Exception:
                        pass

                    # Process is Per-Monitor DPI v2 aware: keep work area coordinates in physical
                    # screen pixels so mixed-DPI origins and WinForms window.x/window.y match 1:1.
                    monitors.append({
                        "left": int(work[0]),
                        "top": int(work[1]),
                        "right": int(work[2]),
                        "bottom": int(work[3]),
                        "scale": scale,
                        "is_primary": is_primary
                    })
        except Exception:
            pass

    if not monitors:
        try:
            for s in webview.screens:
                monitors.append({
                    "left": s.x,
                    "top": s.y,
                    "right": s.x + s.width,
                    "bottom": s.y + s.height,
                    "is_primary": (s.x == 0 and s.y == 0)
                })
        except Exception:
            pass

    if not monitors:
        monitors.append({"left": 0, "top": 0, "right": 1920, "bottom": 1080, "is_primary": True})
    return monitors


def clamp_window_position(
    x: Optional[int],
    y: Optional[int],
    width: int = 210,
    height: int = 56,
    margin: int = 16
) -> tuple[int, int]:
    """
    Ensures that a window of given width and height is completely visible on
    the most appropriate active monitor's work area, with a safe margin.
    If coordinates are missing, off-screen, or invalid, defaults to the
    top-right corner of the primary display.
    """
    monitors = get_monitor_work_areas()
    primary = next((m for m in monitors if m["is_primary"]), monitors[0])

    if x is None or y is None:
        def_x = primary["right"] - width - 24
        def_y = primary["top"] + 24
        return (int(def_x), int(def_y))

    x, y = int(x), int(y)

    # Find monitor that has the largest overlap with the window rectangle
    best_mon = None
    best_area = 0
    for m in monitors:
        iw = max(0, min(x + width, m["right"]) - max(x, m["left"]))
        ih = max(0, min(y + height, m["bottom"]) - max(y, m["top"]))
        area = iw * ih
        if area > best_area:
            best_area = area
            best_mon = m

    # If zero overlap with any monitor, find the closest monitor
    if best_area <= 0 or not best_mon:
        cx, cy = x + width / 2, y + height / 2
        closest_mon = None
        min_dist = float("inf")
        for m in monitors:
            mcx = (m["left"] + m["right"]) / 2
            mcy = (m["top"] + m["bottom"]) / 2
            dist = (cx - mcx) ** 2 + (cy - mcy) ** 2
            if dist < min_dist:
                min_dist = dist
                closest_mon = m
        best_mon = closest_mon or primary

    min_x = best_mon["left"] + margin
    max_x = max(min_x, best_mon["right"] - width - margin)
    clamped_x = max(min_x, min(x, max_x))

    min_y = best_mon["top"] + margin
    max_y = max(min_y, best_mon["bottom"] - height - margin)
    clamped_y = max(min_y, min(y, max_y))

    return (int(clamped_x), int(clamped_y))


class HUDBridge:
    """
    Exposes HUD-specific view mode and window resizing APIs to PyWebView.
    All internal non-RPC fields are prefixed with '_' to prevent pywebview reflection loops.
    """

    def __init__(self, window: Optional[webview.Window], bridge):
        self._window = window
        self._bridge = bridge
        self._mini_narrow_size = (176, 56)
        self._mini_wide_size = (245, 56)
        self._mini_fps_visible = False
        self._mode_lock = threading.Lock()
        self._drag_lock = threading.Lock()
        self._is_native_dragging = False
        self._normal_size = (440, 180)
        self._max_size = (560, 480)
        self._dot_state = "standby"
        self._dot_colors = None
        self._dot_form = None
        self._dot_bitmap = None
        self._dot_visible = False
        self._dot_last_phys_rect = None
        self._vr_manager = None

    @property
    def _mini_size(self) -> tuple[int, int]:
        return self._mini_wide_size if self._mini_fps_visible else self._mini_narrow_size

    def get_agent_name(self) -> dict:
        """Returns configured agent name for the HUD overlay."""
        if hasattr(self._bridge, "get_agent_name"):
            try:
                res = self._bridge.get_agent_name()
                if isinstance(res, dict) and isinstance(res.get("agent_name"), str):
                    return {"success": True, "agent_name": res["agent_name"]}
            except Exception:
                pass
        name = self._bridge.get_raw_config().get("api", {}).get("agent_name", "Aether")
        return {"success": True, "agent_name": name if isinstance(name, str) else "Aether"}

    def get_overlay_config(self) -> dict:
        """Returns initial overlay configuration including agent name, hud mode, custom colors, and engine state."""
        from ui.dot_overlay import DEFAULT_HUD_STATUS_COLORS, sanitize_hud_colors

        cfg = self._bridge.get_raw_config()
        ui_cfg = cfg.get("ui", {}) or {}
        agent_name = cfg.get("api", {}).get("agent_name", "Aether")
        hud_mode = ui_cfg.get("hud_mode", "normal")
        ui_theme = ui_cfg.get("theme", "system")
        hud_colors = sanitize_hud_colors(ui_cfg.get("hud_colors") or DEFAULT_HUD_STATUS_COLORS)
        is_running = getattr(self._bridge._engine, "is_running", False) if self._bridge._engine else False
        return {
            "success": True,
            "agent_name": agent_name,
            "hud_mode": hud_mode,
            "theme": ui_theme,
            "hud_colors": hud_colors,
            "dot_size": ui_cfg.get("dot_size", 22),
            "dot_position": ui_cfg.get("dot_position", "top_right"),
            "dot_monitor": ui_cfg.get("dot_monitor", "auto"),
            "vr_dot_enabled": bool(ui_cfg.get("vr_dot_enabled", True)),
            "vr_dot_anchor": ui_cfg.get("vr_dot_anchor", "hmd"),
            "vr_dot_inset": ui_cfg.get("vr_dot_inset", 0.62),
            "vr_dot_x": ui_cfg.get("vr_dot_x", 0.25),
            "vr_dot_y": ui_cfg.get("vr_dot_y", 0.18),
            "vr_dot_depth": ui_cfg.get("vr_dot_depth", 1.20),
            "vr_dot_scale": ui_cfg.get("vr_dot_scale", 1.00),
            "vr_hud_enabled": bool(ui_cfg.get("vr_hud_enabled", True)),
            "vr_hud_anchor": ui_cfg.get("vr_hud_anchor", "cockpit"),
            "vr_hud_depth": ui_cfg.get("vr_hud_depth", 1.35),
            "vr_hud_scale": ui_cfg.get("vr_hud_scale", 1.00),
            "vr_hud_offset_x": ui_cfg.get("vr_hud_offset_x", 0.0),
            "vr_hud_offset_y": ui_cfg.get("vr_hud_offset_y", -0.12),
            "vr_hud_curvature": ui_cfg.get("vr_hud_curvature", 0.12),
            "vr_hud_yaw": ui_cfg.get("vr_hud_yaw", 0.0),
            "vr_hud_pitch": ui_cfg.get("vr_hud_pitch", 0.0),
            "vr_hud_roll": ui_cfg.get("vr_hud_roll", 0.0),
            "vr_wrist_auto_hide": bool(ui_cfg.get("vr_wrist_auto_hide", True)),
            "vr_wrist_smoothing": ui_cfg.get("vr_wrist_smoothing", 0.60),
            "vr_runtime": ui_cfg.get("vr_runtime", "auto"),
            "is_running": is_running,
            "state": "listening" if is_running else "standby",
        }

    def save_hud_position(self, x: Optional[int] = None, y: Optional[int] = None) -> dict:
        """Saves current HUD overlay window position (physical screen coordinates) to config and disk."""
        try:
            if x is None or y is None:
                rect = _get_overlay_physical_rect(self._window)
                if rect:
                    x, y = rect[0], rect[1]
                elif self._window:
                    x = getattr(self._window, "x", None)
                    y = getattr(self._window, "y", None)
            if x is not None and y is not None:
                return self._bridge.save_overlay_position(int(x), int(y))
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Invalid coordinates"}

    def _get_ui_dispatcher_form(self):
        """Returns a live WinForms Form on the pywebview UI thread for safe BeginInvoke calls."""
        for candidate in (getattr(self._bridge, "_window", None), self._window):
            native = getattr(candidate, "native", None) if candidate is not None else None
            if native is not None:
                try:
                    if not getattr(native, "IsDisposed", False) and not getattr(native, "Disposing", False):
                        if getattr(native, "IsHandleCreated", True):
                            return native
                except Exception:
                    continue
        return None

    def _run_on_ui_thread(self, fn) -> None:
        """Schedules fn() asynchronously on the pywebview WinForms UI thread without blocking."""
        if sys.platform != "win32":
            return
        host = self._get_ui_dispatcher_form()
        if host is None:
            return

        def _safe():
            try:
                fn()
            except Exception as err:
                print(f"[HUD DOT UI ERROR] {err}")

        try:
            if getattr(host, "InvokeRequired", False) and hasattr(host, "BeginInvoke"):
                from System import Action
                host.BeginInvoke(Action(_safe))
            else:
                _safe()
        except Exception as err:
            print(f"[HUD DOT DISPATCH ERROR] {err}")

    @staticmethod
    def _build_dot_bitmap(phys_d: int, hex_color: str, sd_mod=None, sd2d_mod=None):
        """
        Pre-renders a crisp, anti-aliased circular LED Bitmap on a TransparencyKey (#010203)
        canvas. Because WinForms paints BackgroundImage natively in .NET/GDI+, zero Python
        Paint callbacks ever run on the WinForms UI thread (preventing GIL deadlocks).
        """
        if sd_mod is None:
            import System.Drawing as sd_mod
        if sd2d_mod is None:
            import System.Drawing.Drawing2D as sd2d_mod

        Bitmap = sd_mod.Bitmap
        Color = sd_mod.Color
        Graphics = sd_mod.Graphics
        Pen = sd_mod.Pen
        SolidBrush = sd_mod.SolidBrush
        PixelOffsetMode = sd2d_mod.PixelOffsetMode
        SmoothingMode = sd2d_mod.SmoothingMode

        size = max(12, int(phys_d))
        bmp = Bitmap(size, size)
        gfx = Graphics.FromImage(bmp)
        brush = None
        highlight_brush = None
        rim_pen = None
        try:
            r = int(hex_color[1:3], 16)
            g = int(hex_color[3:5], 16)
            b = int(hex_color[5:7], 16)
            gfx.Clear(Color.FromArgb(1, 2, 3))
            gfx.SmoothingMode = SmoothingMode.AntiAlias
            gfx.PixelOffsetMode = PixelOffsetMode.HighQuality

            diameter = float(size) - 2.0
            if diameter < 4.0:
                diameter = float(size)
            ox = (float(size) - diameter) / 2.0
            oy = (float(size) - diameter) / 2.0

            brush = SolidBrush(Color.FromArgb(255, r, g, b))
            gfx.FillEllipse(brush, ox, oy, diameter, diameter)

            # Subtle top-left specular glass highlight
            hl_d = max(3.0, diameter * 0.36)
            hl_x = ox + diameter * 0.18
            hl_y = oy + diameter * 0.14
            highlight_brush = SolidBrush(Color.FromArgb(105, 255, 255, 255))
            gfx.FillEllipse(highlight_brush, hl_x, hl_y, hl_d, hl_d * 0.78)

            # Crisp 1px dark rim keeps the circle distinct on bright & dark games
            rim_pen = Pen(Color.FromArgb(220, 14, 18, 26), 1.25)
            gfx.DrawEllipse(rim_pen, ox, oy, diameter, diameter)
        finally:
            for obj in (rim_pen, highlight_brush, brush, gfx):
                if obj is not None:
                    try:
                        obj.Dispose()
                    except Exception:
                        pass
        return bmp

    def _update_dot_form_on_ui(self, px: int, py: int, phys_d: int, show: bool) -> None:
        """Creates/updates the tiny circular WinForms Dot Form on the UI thread (zero Python Paint hooks or UI-thread imports)."""
        if sys.platform != "win32":
            return
        from ui.dot_overlay import resolve_status_color

        ui_cfg = self._bridge.get_raw_config().get("ui", {}) or {}
        colors = self._dot_colors if isinstance(self._dot_colors, dict) else ui_cfg.get("hud_colors")
        hex_color = resolve_status_color(self._dot_state, colors)

        # Resolve ALL Python / .NET imports on the caller thread BEFORE BeginInvoke
        # so the WinForms STA UI thread never contends for Python's import lock during startup.
        try:
            import ctypes
            import win32con
            import System.Windows.Forms as WF
            import System.Drawing as SD
            import System.Drawing.Drawing2D as SD2D
            u32 = _get_user32()
        except Exception as err:
            print(f"[HUD DOT IMPORT ERROR] {err}")
            return

        Color = SD.Color
        Point = SD.Point
        Size = SD.Size
        dim = max(12, int(phys_d))
        bmp = self._build_dot_bitmap(dim, hex_color, sd_mod=SD, sd2d_mod=SD2D)

        def _apply():
            form = self._dot_form
            if form is None or getattr(form, "IsDisposed", False):
                form = WF.Form()
                form.Text = "Aether Dot"
                form.FormBorderStyle = getattr(WF.FormBorderStyle, "None")
                form.ShowInTaskbar = False
                form.TopMost = True
                form.StartPosition = WF.FormStartPosition.Manual
                form.MinimumSize = Size(1, 1)
                form.BackgroundImageLayout = getattr(WF.ImageLayout, "None")
                key_color = Color.FromArgb(1, 2, 3)
                form.BackColor = key_color
                form.TransparencyKey = key_color
                self._dot_form = form

            old_bmp = self._dot_bitmap
            form.BackgroundImage = bmp
            self._dot_bitmap = bmp
            if old_bmp is not None:
                try:
                    old_bmp.Dispose()
                except Exception:
                    pass

            form.Location = Point(int(px), int(py))
            form.Size = Size(dim, dim)
            hwnd = int(form.Handle.ToInt64())

            # Disable Windows 11 DWM rounded border / shadow behind the circular TransparencyKey dot
            try:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    ctypes.c_void_p(hwnd), 2, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
                )  # DWMWA_NCRENDERING_POLICY = DWMNCRP_DISABLED
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    ctypes.c_void_p(hwnd), 33, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
                )  # DWMWA_WINDOW_CORNER_PREFERENCE = DWMWCP_DONOTROUND
            except Exception:
                pass

            # Keep WS_EX_LAYERED (from TransparencyKey) + add TOOLWINDOW | NOACTIVATE | TRANSPARENT
            ex_style = _get_win_exstyle(u32, hwnd, win32con.GWL_EXSTYLE)
            new_style = (
                (ex_style | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TRANSPARENT)
                & ~win32con.WS_EX_APPWINDOW
            )
            if new_style != ex_style:
                _set_win_exstyle(u32, hwnd, win32con.GWL_EXSTYLE, new_style)

            flags = win32con.SWP_NOACTIVATE | (win32con.SWP_SHOWWINDOW if show else win32con.SWP_HIDEWINDOW)
            u32.SetWindowPos(
                hwnd,
                win32con.HWND_TOPMOST,
                int(px),
                int(py),
                dim,
                dim,
                flags,
            )
            if show:
                u32.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
                form.Invalidate()
            else:
                u32.ShowWindow(hwnd, win32con.SW_HIDE)
            self._dot_visible = bool(show)

        self._dot_last_phys_rect = (int(px), int(py), dim)
        self._run_on_ui_thread(_apply)

    def _get_vr_manager(self):
        """Lazily creates the unified VR Overlay Manager (Dot + Mini/Normal/Max HUD)."""
        if self._vr_manager is None:
            from ui.vr_overlay_manager import VROverlayManager, capture_hwnd_rgba
            ui_cfg = self._bridge.get_raw_config().get("ui", {}) or {}
            mgr = VROverlayManager(
                hud_capture_fn=lambda: capture_hwnd_rgba(_get_overlay_hwnd(self._window)),
                vr_runtime=ui_cfg.get("vr_runtime", "auto"),
            )
            mgr.configure_dot(
                enabled=bool(ui_cfg.get("vr_dot_enabled", True)),
                size=ui_cfg.get("dot_size", 22),
                position=ui_cfg.get("dot_position", "top_right"),
                anchor=ui_cfg.get("vr_dot_anchor", "hmd"),
                inset=ui_cfg.get("vr_dot_inset", 0.62),
                offset_x=ui_cfg.get("vr_dot_x", 0.25),
                offset_y=ui_cfg.get("vr_dot_y", 0.18),
                depth_m=ui_cfg.get("vr_dot_depth", 1.20),
                scale=ui_cfg.get("vr_dot_scale", 1.00),
                colors=ui_cfg.get("hud_colors"),
            )
            mgr.configure_hud(
                enabled=bool(ui_cfg.get("vr_hud_enabled", True)),
                mode=ui_cfg.get("hud_mode", "normal"),
                anchor=ui_cfg.get("vr_hud_anchor", "cockpit"),
                depth_m=ui_cfg.get("vr_hud_depth", 1.25),
                scale=ui_cfg.get("vr_hud_scale", 1.00),
                offset_x=ui_cfg.get("vr_hud_offset_x", 0.0),
                offset_y=ui_cfg.get("vr_hud_offset_y", -0.12),
                curvature=ui_cfg.get("vr_hud_curvature", 0.12),
                yaw_deg=ui_cfg.get("vr_hud_yaw", 0.0),
                pitch_deg=ui_cfg.get("vr_hud_pitch", 0.0),
                roll_deg=ui_cfg.get("vr_hud_roll", 0.0),
                wrist_auto_hide=bool(ui_cfg.get("vr_wrist_auto_hide", True)),
                wrist_smoothing=ui_cfg.get("vr_wrist_smoothing", 0.60),
            )
            mgr.set_dot_state(self._dot_state)
            self._vr_manager = mgr
        return self._vr_manager

    def get_vr_status(self) -> dict:
        """Returns VR backend availability, HMD presence, and Dot/HUD overlay states."""
        mgr = self._get_vr_manager()
        return {"success": True, **mgr.get_status()}

    def get_vr_dot_status(self) -> dict:
        """Backwards-compatible alias for get_vr_status()."""
        return self.get_vr_status()

    def recenter_vr_hud(self) -> dict:
        """Recenters the cockpit-anchored VR HUD & Dot to the user's current HMD gaze."""
        mgr = self._get_vr_manager()
        return mgr.recenter_hud()

    def reset_vr_settings(self) -> dict:
        """Resets all VR overlay settings (Dot + Mini/Normal/Max) to safe defaults and recenters."""
        mgr = self._get_vr_manager()
        return mgr.reset_defaults()

    def apply_vr_hud_layout(
        self,
        mode: Optional[str] = None,
        vr_hud_enabled: Optional[bool] = None,
        vr_hud_anchor: Optional[str] = None,
        vr_hud_depth: Optional[float] = None,
        vr_hud_scale: Optional[float] = None,
        vr_hud_offset_x: Optional[float] = None,
        vr_hud_offset_y: Optional[float] = None,
        vr_hud_curvature: Optional[float] = None,
        vr_hud_yaw: Optional[float] = None,
        vr_hud_pitch: Optional[float] = None,
        vr_hud_roll: Optional[float] = None,
        vr_wrist_auto_hide: Optional[bool] = None,
        vr_wrist_smoothing: Optional[float] = None,
        vr_runtime: Optional[str] = None,
        show: bool = True,
    ) -> dict:
        """Syncs VR HUD panel settings and shows/hides the 3D HUD quad in VR."""
        ui_cfg = self._bridge.get_raw_config().get("ui", {}) or {}
        eff_mode = str(mode or getattr(self, "_last_applied_mode", None) or ui_cfg.get("hud_mode", "normal")).lower()
        enabled = bool(vr_hud_enabled if vr_hud_enabled is not None else ui_cfg.get("vr_hud_enabled", True))
        try:
            mgr = self._get_vr_manager()
            mgr.configure_hud(
                enabled=enabled,
                mode=eff_mode if eff_mode in ("mini", "normal", "max") else None,
                anchor=vr_hud_anchor if vr_hud_anchor is not None else ui_cfg.get("vr_hud_anchor", "cockpit"),
                depth_m=vr_hud_depth if vr_hud_depth is not None else ui_cfg.get("vr_hud_depth", 1.25),
                scale=vr_hud_scale if vr_hud_scale is not None else ui_cfg.get("vr_hud_scale", 1.00),
                offset_x=vr_hud_offset_x if vr_hud_offset_x is not None else ui_cfg.get("vr_hud_offset_x", 0.0),
                offset_y=vr_hud_offset_y if vr_hud_offset_y is not None else ui_cfg.get("vr_hud_offset_y", -0.12),
                curvature=vr_hud_curvature if vr_hud_curvature is not None else ui_cfg.get("vr_hud_curvature", 0.12),
                yaw_deg=vr_hud_yaw if vr_hud_yaw is not None else ui_cfg.get("vr_hud_yaw", 0.0),
                pitch_deg=vr_hud_pitch if vr_hud_pitch is not None else ui_cfg.get("vr_hud_pitch", 0.0),
                roll_deg=vr_hud_roll if vr_hud_roll is not None else ui_cfg.get("vr_hud_roll", 0.0),
                wrist_auto_hide=bool(vr_wrist_auto_hide if vr_wrist_auto_hide is not None else ui_cfg.get("vr_wrist_auto_hide", True)),
                wrist_smoothing=vr_wrist_smoothing if vr_wrist_smoothing is not None else ui_cfg.get("vr_wrist_smoothing", 0.60),
                vr_runtime=vr_runtime if vr_runtime is not None else ui_cfg.get("vr_runtime", "auto"),
            )
            if show and enabled and eff_mode in ("mini", "normal", "max"):
                mgr.show_hud(mode=eff_mode)
            else:
                mgr.hide_hud()
        except Exception:
            pass
        return {"success": True, "mode": eff_mode, "enabled": enabled}

    def hide_vr_hud(self) -> None:
        """Hides the 3D VR HUD overlay without affecting the desktop window."""
        if self._vr_manager is not None:
            try:
                self._vr_manager.hide_hud()
            except Exception:
                pass

    def hide_dot(self) -> None:
        """Hides the circular Dot Form (and VR Dot overlay if active) without affecting the WebView2 overlay window."""
        self._dot_visible = False
        if self._vr_manager is not None:
            try:
                self._vr_manager.hide_dot()
            except Exception:
                pass
        if sys.platform != "win32":
            return
        try:
            import win32con
            u32 = _get_user32()
        except Exception:
            return

        def _hide():
            form = self._dot_form
            if form is not None and not getattr(form, "IsDisposed", False):
                if getattr(form, "IsHandleCreated", False):
                    u32.ShowWindow(int(form.Handle.ToInt64()), win32con.SW_HIDE)

        self._run_on_ui_thread(_hide)

    def destroy_dot(self) -> None:
        """Disposes the circular Dot Form, its Bitmap, and the VR Overlay Manager cleanly during shutdown."""
        self._dot_visible = False
        if self._vr_manager is not None:
            try:
                self._vr_manager.destroy()
            except Exception:
                pass
            self._vr_manager = None

        def _dispose():
            form = self._dot_form
            self._dot_form = None
            bmp = self._dot_bitmap
            self._dot_bitmap = None
            if form is not None:
                try:
                    if not getattr(form, "IsDisposed", False):
                        form.BackgroundImage = None
                        form.Close()
                        form.Dispose()
                except Exception:
                    pass
            if bmp is not None:
                try:
                    bmp.Dispose()
                except Exception:
                    pass

        self._run_on_ui_thread(_dispose)

    def set_dot_status(self, state: Optional[str] = None, colors: Optional[dict] = None) -> None:
        """Updates active status state and/or custom color palette for the circular Dot Form and VR Dot."""
        if state is not None:
            self._dot_state = str(state or "standby").lower().strip()
        if isinstance(colors, dict):
            from ui.dot_overlay import sanitize_hud_colors
            self._dot_colors = sanitize_hud_colors(colors)
        if self._vr_manager is not None:
            try:
                if state is not None:
                    self._vr_manager.set_dot_state(self._dot_state)
                if self._dot_colors is not None:
                    self._vr_manager.configure_dot(colors=self._dot_colors)
            except Exception:
                pass
        if not self._dot_visible or getattr(self, "_last_applied_mode", None) != "dot":
            return
        if self._dot_last_phys_rect is not None:
            px, py, phys_d = self._dot_last_phys_rect
            self._update_dot_form_on_ui(px, py, phys_d, show=True)

    def apply_dot_layout(
        self,
        dot_size: Optional[int] = None,
        dot_position: Optional[str] = None,
        dot_monitor: Optional[str] = None,
        active_game_process: Optional[str] = None,
        vr_dot_enabled: Optional[bool] = None,
        vr_dot_anchor: Optional[str] = None,
        vr_dot_inset: Optional[float] = None,
        vr_dot_x: Optional[float] = None,
        vr_dot_y: Optional[float] = None,
        vr_dot_depth: Optional[float] = None,
        vr_dot_scale: Optional[float] = None,
    ) -> dict:
        """Positions, sizes, and displays the circular WinForms Dot indicator and mirrors it to OpenVR when active."""
        from ui.dot_overlay import (
            normalize_dot_position,
            normalize_dot_size,
            resolve_dot_position,
        )
        from ui.vr_dot_overlay import (
            normalize_vr_dot_anchor,
            normalize_vr_dot_depth,
            normalize_vr_dot_inset,
            normalize_vr_dot_scale,
            normalize_vr_dot_x,
            normalize_vr_dot_y,
        )

        ui_cfg = self._bridge.get_raw_config().get("ui", {}) or {}
        d_size = normalize_dot_size(dot_size if dot_size is not None else ui_cfg.get("dot_size", 22))
        d_pos = normalize_dot_position(dot_position if dot_position is not None else ui_cfg.get("dot_position", "top_right"))
        d_mon = str(dot_monitor if dot_monitor is not None else ui_cfg.get("dot_monitor", "auto"))

        v_enabled = bool(vr_dot_enabled if vr_dot_enabled is not None else ui_cfg.get("vr_dot_enabled", True))
        v_anchor = normalize_vr_dot_anchor(vr_dot_anchor if vr_dot_anchor is not None else ui_cfg.get("vr_dot_anchor", "hmd"))
        v_inset = normalize_vr_dot_inset(vr_dot_inset if vr_dot_inset is not None else ui_cfg.get("vr_dot_inset", 0.62))
        v_x = normalize_vr_dot_x(vr_dot_x if vr_dot_x is not None else ui_cfg.get("vr_dot_x", 0.25))
        v_y = normalize_vr_dot_y(vr_dot_y if vr_dot_y is not None else ui_cfg.get("vr_dot_y", 0.18))
        v_depth = normalize_vr_dot_depth(vr_dot_depth if vr_dot_depth is not None else ui_cfg.get("vr_dot_depth", 1.20))
        v_scale = normalize_vr_dot_scale(vr_dot_scale if vr_dot_scale is not None else ui_cfg.get("vr_dot_scale", 1.00))

        try:
            mgr = self._get_vr_manager()
            mgr.configure_dot(
                enabled=v_enabled,
                size=d_size,
                position=d_pos,
                anchor=v_anchor,
                inset=v_inset,
                offset_x=v_x,
                offset_y=v_y,
                depth_m=v_depth,
                scale=v_scale,
                colors=self._dot_colors if isinstance(self._dot_colors, dict) else ui_cfg.get("hud_colors"),
            )
            mgr.set_dot_state(self._dot_state)
            mgr.hide_hud()
            if v_enabled:
                mgr.show_dot()
            else:
                mgr.hide_dot()
        except Exception:
            pass

        game_rect = None
        if d_mon.lower() == "auto" and active_game_process and sys.platform == "win32":
            try:
                import win32gui
                from tools.os_controls import find_window_hwnd
                ghwnd = find_window_hwnd(active_game_process)
                if ghwnd and win32gui.IsWindow(ghwnd):
                    l, t, r, b = win32gui.GetWindowRect(ghwnd)
                    if r > l and b > t:
                        game_rect = (int(l), int(t), int(r), int(b))
            except Exception:
                game_rect = None

        scale = _get_dpi_scale_for_window(self._window)
        phys_d = max(12, int(round(d_size * scale)))
        monitors = get_monitor_work_areas()
        px, py = resolve_dot_position(
            d_pos,
            d_mon,
            phys_d,
            monitors=monitors,
            active_window_rect=game_rect,
        )
        self._update_dot_form_on_ui(px, py, phys_d, show=True)
        return {"success": True, "mode": "dot", "size": d_size, "x": px, "y": py}

    def set_mode(self, mode: str) -> dict:
        """Resizes the HUD overlay window according to requested tier (dot, mini, normal, max)."""
        mode = mode.lower() if mode else "normal"
        if mode not in ("dot", "mini", "normal", "max"):
            mode = "normal"
        if self._window:
            with self._mode_lock:
                self._last_applied_mode = mode
                if mode == "dot":
                    if hasattr(self._bridge, "_safe_hide_overlay_window"):
                        try:
                            self._bridge._safe_hide_overlay_window()
                        except Exception:
                            pass
                    self.apply_dot_layout()
                else:
                    self.hide_dot()
                    w, h = self._mini_wide_size if self._mini_fps_visible else self._mini_narrow_size
                    if mode == "normal":
                        w, h = self._normal_size[0], self._normal_size[1]
                    elif mode == "max":
                        w, h = self._max_size[0], self._max_size[1]

                    scale = _get_dpi_scale_for_window(self._window)
                    phys_w = int(round(w * scale))
                    phys_h = int(round(h * scale))

                    rect = _get_overlay_physical_rect(self._window)
                    if rect:
                        cur_x, cur_y = rect[0], rect[1]
                        cx, cy = clamp_window_position(cur_x, cur_y, width=phys_w, height=phys_h)
                        if cx != cur_x or cy != cur_y:
                            move_overlay_physical(self._window, cx, cy)
                            self._bridge.save_overlay_position(cx, cy)

                    self._window.resize(w, h)
                    apply_hud_window_shape(self._window, mode)
                    self.apply_vr_hud_layout(mode=mode, show=True)

                    # Ensure overlay JavaScript switches CSS classes and DOM layout immediately (only if loaded/shown)
                    events = getattr(self._window, "events", None)
                    loaded_ok = (events is None or not hasattr(events, "loaded") or not hasattr(events.loaded, "is_set") or events.loaded.is_set())
                    shown_ok = (events is None or not hasattr(events, "shown") or not hasattr(events.shown, "is_set") or events.shown.is_set())
                    if loaded_ok and shown_ok:
                        try:
                            self._window.evaluate_js(
                                f"if (window.aetherOverlay && window.aetherOverlay.setMode) {{ window.aetherOverlay.setMode('{mode}'); }}"
                            )
                        except Exception:
                            pass

        if hasattr(self._bridge, "on_hud_mode_changed"):
            self._bridge.on_hud_mode_changed(mode)
        return {"success": True, "mode": mode}

    def set_mini_fps_visible(self, visible: bool) -> dict:
        """Dynamically adjusts Mini Pill width: narrow (176px) when FPS is off, wider (245px) when FPS is on."""
        new_vis = bool(visible)
        if new_vis == self._mini_fps_visible:
            return {"success": True, "visible": new_vis}
        self._mini_fps_visible = new_vis
        try:
            cur_mode = self.get_hud_mode().get("mode", "normal")
            if cur_mode == "mini" and self._window:
                with self._mode_lock:
                    w, h = self._mini_wide_size if new_vis else self._mini_narrow_size
                    scale = _get_dpi_scale_for_window(self._window)
                    rect = _get_overlay_physical_rect(self._window)
                    if rect:
                        cur_x, cur_y = rect[0], rect[1]
                        cx, cy = clamp_window_position(
                            cur_x, cur_y,
                            width=int(round(w * scale)),
                            height=int(round(h * scale)),
                        )
                        if cx != cur_x or cy != cur_y:
                            move_overlay_physical(self._window, cx, cy)
                    self._window.resize(w, h)
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": True, "visible": new_vis}

    def resize_overlay(self, width: int, height: int, x: Optional[int] = None, y: Optional[int] = None) -> dict:
        """Dynamically resizes (and optionally repositions in logical px) the HUD overlay window."""
        try:
            if self._window:
                w = max(100, int(width))
                h = max(40, int(height))
                scale = _get_dpi_scale_for_window(self._window)
                self._window.resize(w, h)
                if x is not None and y is not None:
                    phys_x = int(round(int(x) * scale))
                    phys_y = int(round(int(y) * scale))
                    cx, cy = clamp_window_position(
                        phys_x, phys_y,
                        width=int(round(w * scale)),
                        height=int(round(h * scale)),
                    )
                    move_overlay_physical(self._window, cx, cy)
                    self._bridge.save_overlay_position(cx, cy)
                cur_mode = self.get_hud_mode().get("mode", "normal")
                apply_hud_window_shape(self._window, cur_mode)
                return {"success": True, "width": w, "height": h}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Window not initialized"}

    def save_hud_size(self, mode: str, width: int, height: int) -> dict:
        """Saves user-customized dimensions for Normal or Max mode."""
        mode = mode.lower() if mode else "normal"
        try:
            w, h = max(100, int(width)), max(40, int(height))
            if mode == "normal":
                self._normal_size = (w, h)
            elif mode == "max":
                self._max_size = (w, h)
            apply_hud_window_shape(self._window, mode)
            return {"success": True, "mode": mode, "width": w, "height": h}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_window_bounds(self) -> dict:
        """Returns current overlay window bounds and user preferred sizes."""
        bounds = {"x": 0, "y": 0, "width": 440, "height": 180}
        try:
            if self._window:
                bounds["x"] = getattr(self._window, "x", 0)
                bounds["y"] = getattr(self._window, "y", 0)
                bounds["width"] = getattr(self._window, "width", 440)
                bounds["height"] = getattr(self._window, "height", 180)
        except Exception:
            pass
        bounds["normal_size"] = list(self._normal_size)
        bounds["max_size"] = list(self._max_size)
        return {"success": True, "bounds": bounds}

    def apply_shape(self, mode: Optional[str] = None) -> dict:
        """Explicitly re-applies the OS window clipping region for the active HUD mode."""
        cur_mode = mode or (self.get_hud_mode().get("mode", "normal"))
        apply_hud_window_shape(self._window, cur_mode)
        return {"success": True}

    def start_drag(self) -> dict:
        """
        Starts a smooth, 120Hz native Win32 physical-coordinate drag loop using
        GetCursorPos + SetWindowPos while the left mouse button is held.
        Eliminates WebView2 IPC choppiness, DPI double-scaling, and mouse-capture loss
        on frameless WS_EX_NOACTIVATE windows across multi-monitor HiDPI setups.
        """
        if sys.platform != "win32" or not self._window:
            return {"success": False, "error": "Not supported on this platform"}

        hwnd = _get_overlay_hwnd(self._window)
        if not hwnd:
            return {"success": False, "error": "Overlay HWND not found"}

        with self._drag_lock:
            if self._is_native_dragging:
                return {"success": True, "native": True}
            self._is_native_dragging = True

        def _drag_loop():
            import ctypes
            from ctypes import wintypes
            import win32gui

            user32 = ctypes.windll.user32
            VK_LBUTTON = 0x01
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            SWP_FLAGS = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE

            pt = wintypes.POINT()
            try:
                if not user32.GetCursorPos(ctypes.byref(pt)):
                    return
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                offset_x = int(pt.x) - int(left)
                offset_y = int(pt.y) - int(top)
                win_w = max(1, int(right - left))
                win_h = max(1, int(bottom - top))
                last_x, last_y = int(left), int(top)

                # Follow physical cursor at ~120Hz while left mouse button remains pressed
                while user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000:
                    if user32.GetCursorPos(ctypes.byref(pt)):
                        nx = int(pt.x) - offset_x
                        ny = int(pt.y) - offset_y
                        if nx != last_x or ny != last_y:
                            user32.SetWindowPos(int(hwnd), 0, nx, ny, 0, 0, SWP_FLAGS)
                            last_x, last_y = nx, ny
                    time.sleep(0.008)

                # Re-read actual physical size (in case DPI changed across monitors) and clamp cleanly on release
                try:
                    fl, ft, fr, fb = win32gui.GetWindowRect(hwnd)
                    last_x, last_y = int(fl), int(ft)
                    win_w = max(1, int(fr - fl))
                    win_h = max(1, int(fb - ft))
                except Exception:
                    pass

                cx, cy = clamp_window_position(last_x, last_y, width=win_w, height=win_h)
                if cx != last_x or cy != last_y:
                    user32.SetWindowPos(int(hwnd), 0, cx, cy, 0, 0, SWP_FLAGS)
                self._bridge.save_overlay_position(cx, cy)
            except Exception as e:
                print(f"[HUD DRAG ERROR] {e}")
            finally:
                with self._drag_lock:
                    self._is_native_dragging = False

        t = threading.Thread(target=_drag_loop, daemon=True, name="HudNativeDrag")
        t.start()
        return {"success": True, "native": True}

    def move_overlay(self, x: int, y: int) -> dict:
        """Moves the HUD overlay window to physical screen coordinates (x, y) with boundary clamping."""
        try:
            if self._window:
                rect = _get_overlay_physical_rect(self._window)
                if rect:
                    w, h = rect[2], rect[3]
                else:
                    scale = _get_dpi_scale_for_window(self._window)
                    w = int(round((getattr(self._window, "width", None) or 440) * scale))
                    h = int(round((getattr(self._window, "height", None) or 180) * scale))
                cx, cy = clamp_window_position(int(x), int(y), width=w, height=h)
                move_overlay_physical(self._window, cx, cy)
                return {"success": True, "x": cx, "y": cy}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Window not initialized"}

    def reset_overlay_position(self) -> dict:
        """Snaps the HUD overlay back to the top-right of the primary display."""
        return self._bridge.reset_overlay_position()

    def restore_main_window(self) -> dict:
        return self._bridge.restore_main_window()

    def hide_overlay(self) -> dict:
        return self._bridge.hide_overlay()

    def toggle_overlay(self) -> dict:
        return self._bridge.toggle_overlay()

    def toggle_mic_mute(self) -> dict:
        return self._bridge.toggle_mic_mute()

    def get_windows_theme(self) -> dict:
        return self._bridge.get_windows_theme()

    def get_hud_mode(self) -> dict:
        if hasattr(self._bridge, "get_hud_mode"):
            return self._bridge.get_hud_mode()
        return {"success": True, "mode": "normal"}

    def update_vad_silence(self, silence_ms: int) -> bool:
        """Relays dynamic silence threshold changes from HUD window to GuiBridge."""
        if hasattr(self._bridge, "update_vad_silence"):
            return self._bridge.update_vad_silence(silence_ms)
        return False

    def get_config(self) -> dict:
        """Relays get_config call from HUD window to GuiBridge."""
        if hasattr(self._bridge, "get_config"):
            return self._bridge.get_config()
        return {}

    def get_game_mode(self) -> dict:
        """Relays get_game_mode call from HUD overlay to GuiBridge."""
        if hasattr(self._bridge, "get_game_mode"):
            return self._bridge.get_game_mode()
        return {"game_mode_enabled": False, "is_active": False, "is_running": False, "active_profile": "", "display_name": ""}

    def get_hud_telemetry(self) -> dict:
        """Relays get_hud_telemetry call from HUD overlay to GuiBridge."""
        if hasattr(self._bridge, "get_hud_telemetry"):
            return self._bridge.get_hud_telemetry()
        return {"game_mode_enabled": False, "is_running": False, "fps_monitoring": False, "fps": None}

    def set_game_mode(self, enabled: bool) -> dict:
        """Relays set_game_mode call from HUD overlay to GuiBridge."""
        if hasattr(self._bridge, "set_game_mode"):
            return self._bridge.set_game_mode(enabled)
        return {"success": False}

    def toggle_game_mode(self) -> dict:
        """Relays toggle_game_mode call from HUD overlay to GuiBridge."""
        if hasattr(self._bridge, "toggle_game_mode"):
            return self._bridge.toggle_game_mode()
        return {"success": False}

    def send_text_message(self, text: str) -> dict:
        """Relays typed chat message from HUD overlay to GuiBridge."""
        if hasattr(self._bridge, "send_text_message"):
            return self._bridge.send_text_message(text)
        return {"success": False, "error": "Bridge not available"}

    def set_input_focused(self, focused: bool) -> dict:
        """Relays text input focus state from HUD overlay to GuiBridge."""
        if hasattr(self._bridge, "set_input_focused"):
            return self._bridge.set_input_focused(focused)
        return {"success": False}


class HudWindow:
    """Encapsulates the PyWebView HUD interface, floating overlay, and system tray."""

    def __init__(
        self,
        bridge,
        title: str = "Aether Desktop",
        width: int = 1040,
        height: int = 760,
        min_width: int = 880,
        min_height: int = 620,
        on_init: Optional[object] = None,
    ):
        ensure_thread_desktop()
        self.bridge = bridge
        self.title = title
        self.width = width
        self.height = height
        self.min_width = min_width
        self.min_height = min_height
        self.on_init = on_init
        self._is_closing_permanently = False
        self._close_all_lock = threading.Lock()
        self._close_all_done = False
        if hasattr(self.bridge, "set_hud_window"):
            self.bridge.set_hud_window(self)

        # Ensure WebView2 user data folder is directed to writable %LOCALAPPDATA%\Aether\WebView2
        # BEFORE calling webview.create_window() or webview.start().
        webview_cache_dir = get_webview_cache_dir()
        os.environ["WEBVIEW2_USER_DATA_FOLDER"] = webview_cache_dir

        # Determine path to static UI assets
        base_dir = get_resource_dir()
        self.html_path = os.path.join(base_dir, "ui", "static", "index.html")
        self.overlay_html_path = os.path.join(base_dir, "ui", "static", "overlay.html")
        self.html_url = Path(self.html_path).resolve().as_uri()
        self.overlay_html_url = Path(self.overlay_html_path).resolve().as_uri()

        # 1. Create Main Application Window
        self.window = webview.create_window(
            title=self.title,
            url=self.html_url,
            js_api=self.bridge,
            width=self.width,
            height=self.height,
            min_size=(self.min_width, self.min_height),
            background_color="#202020"
        )
        self.bridge.set_window(self.window)

        # 2. Create Floating HUD Overlay Window with dedicated HUDBridge
        # Keep overlay_window at normal/mini/max card dimensions even when cfg_mode == "dot"
        # (Dot Mode is rendered by HUDBridge's lightweight WinForms circular Form on the same UI thread).
        self.hud_bridge = HUDBridge(window=None, bridge=self.bridge)
        cfg_ui = self.bridge.get_raw_config().get("ui", {})
        cfg_mode = cfg_ui.get("hud_mode", "normal")
        init_scale = _get_dpi_scale_for_window(None)
        init_x = cfg_ui.get("overlay_x")
        init_y = cfg_ui.get("overlay_y")
        init_w, init_h = (440, 180)
        if cfg_mode == "mini":
            init_w, init_h = (176, 56)
        elif cfg_mode == "max":
            init_w, init_h = (560, 480)
        phys_w = int(round(init_w * init_scale))
        phys_h = int(round(init_h * init_scale))
        cx, cy = clamp_window_position(init_x, init_y, width=phys_w, height=phys_h)

        # pywebview WinForms multiplies initial x/y by DPI scale internally, so pass logical coords
        logical_x = int(round(cx / init_scale))
        logical_y = int(round(cy / init_scale))

        overlay_kwargs = {
            "title": "Aether Overlay",
            "url": self.overlay_html_url,
            "js_api": self.hud_bridge,
            "width": init_w,
            "height": init_h,
            "x": logical_x,
            "y": logical_y,
            "min_size": (10, 10),
            "frameless": True,
            "on_top": True,
            "easy_drag": False,
            "hidden": True,
            "transparent": False,
            "background_color": "#121620"
        }

        self.overlay_window = webview.create_window(**overlay_kwargs)
        self.hud_bridge._window = self.overlay_window
        self.hud_bridge._last_applied_mode = cfg_mode
        self.bridge.set_overlay_window(self.overlay_window)
        self.bridge.set_hud_bridge(self.hud_bridge)
        self.dot_overlay = None

        # If clamped position differed from config (for draggable modes only), persist clamped physical coordinates
        if cfg_mode != "dot" and (init_x != cx or init_y != cy):
            self.bridge.save_overlay_position(cx, cy)

        # Wire window moved event for debounced position persistence (physical pixels, never mid-drag)
        self._pos_save_timer = None
        self._last_moved_pos = None

        def _save_debounced_pos():
            if getattr(self.hud_bridge, "_is_native_dragging", False):
                return
            if getattr(self.hud_bridge, "_last_applied_mode", None) == "dot":
                return
            if sys.platform == "win32":
                try:
                    import ctypes
                    # Do not snap/save while left mouse button is still held down
                    if ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000:
                        return
                except Exception:
                    pass
            if self._last_moved_pos and self.overlay_window:
                mx, my = self._last_moved_pos
                rect = _get_overlay_physical_rect(self.overlay_window)
                if rect and (rect[0] != mx or rect[1] != my):
                    move_overlay_physical(self.overlay_window, mx, my)
                self.bridge.save_overlay_position(mx, my)

        def _on_overlay_moved(x, y):
            if getattr(self.hud_bridge, "_is_native_dragging", False):
                return
            if getattr(self.hud_bridge, "_last_applied_mode", None) == "dot":
                return
            try:
                rect = _get_overlay_physical_rect(self.overlay_window)
                if rect:
                    px, py, pw, ph = rect
                else:
                    scale = _get_dpi_scale_for_window(self.overlay_window)
                    px = int(round(x * scale))
                    py = int(round(y * scale))
                    pw = int(round((getattr(self.overlay_window, "width", None) or 440) * scale))
                    ph = int(round((getattr(self.overlay_window, "height", None) or 180) * scale))
                cx, cy = clamp_window_position(px, py, width=pw, height=ph)
                self._last_moved_pos = (cx, cy)
                if self._pos_save_timer:
                    self._pos_save_timer.cancel()
                self._pos_save_timer = threading.Timer(0.5, _save_debounced_pos)
                self._pos_save_timer.daemon = True
                self._pos_save_timer.start()
            except Exception:
                pass

        self.overlay_window.events.moved += _on_overlay_moved

        def _is_windows_shutting_down() -> bool:
            if sys.platform != "win32":
                return False
            try:
                import ctypes
                # SM_SHUTTINGDOWN = 0x2000
                return bool(ctypes.windll.user32.GetSystemMetrics(0x2000))
            except Exception:
                return False

        # 3. Wire window close, minimize, and restore events
        def on_closing():
            if getattr(self, "_is_closing_permanently", False) or _is_windows_shutting_down():
                self._cleanup_before_window_close(destroy_main=False)
                return True
            cfg = self.bridge.get_raw_config().get("ui", {})
            if cfg.get("minimize_to_tray", True):
                self.window.hide()
                overlay_mode = cfg.get("floating_overlay", "on_minimize")
                if overlay_mode == "on_minimize":
                    self.bridge.show_overlay()
                return False  # Cancels window destruction to keep running in tray
            else:
                self._cleanup_before_window_close(destroy_main=False)
                return True

        def on_minimized():
            cfg = self.bridge.get_raw_config().get("ui", {})
            if cfg.get("minimize_to_tray", True):
                self.window.hide()
            overlay_mode = cfg.get("floating_overlay", "on_minimize")
            if overlay_mode == "on_minimize":
                self.bridge.show_overlay()

        def on_restored():
            cfg = self.bridge.get_raw_config().get("ui", {})
            overlay_mode = cfg.get("floating_overlay", "on_minimize")
            if overlay_mode == "on_minimize":
                self.bridge.hide_overlay()

        self.window.events.closing += on_closing
        self.window.events.minimized += on_minimized
        self.window.events.restored += on_restored

        # 4. Initialize System Tray Manager
        def _maximize_main_window():
            try:
                self.bridge.restore_main_window()
                hwnd = None
                native = getattr(self.window, "native", None)
                if native is not None and hasattr(native, "Handle"):
                    try:
                        hwnd = native.Handle.ToInt64()
                    except Exception:
                        hwnd = None
                if not hwnd and sys.platform == "win32":
                    import win32gui
                    hwnd = win32gui.FindWindow(None, self.title)
                if hwnd:
                    force_foreground_window(hwnd, maximize=True)
            except Exception as e:
                print(f"[HUD] Tray maximize error: {e}")

        self.tray = SystemTrayManager(
            on_open_main=self.bridge.restore_main_window,
            on_maximize_main=_maximize_main_window,
            on_toggle_overlay=self.bridge.toggle_overlay,
            on_toggle_mute=self.bridge.toggle_mic_mute,
            on_set_hud_mode=self.bridge.set_mode,
            on_cycle_hud_mode=self.bridge.cycle_hud_mode,
            on_reset_overlay=self.bridge.reset_overlay_position,
            on_exit=self.close_all,
            title="Aether Desktop"
        )

    def _on_started(self):
        """Called once pywebview event loop is ready."""
        if self.on_init and callable(self.on_init):
            try:
                self.on_init(self.window)
            except Exception as e:
                print(f"[HUD] Error during window initialization hook: {e}")
        else:
            try:
                from main import initialize_window
                initialize_window(self.window)
            except Exception:
                pass

        cfg = self.bridge.get_raw_config().get("ui", {})
        overlay_mode = cfg.get("floating_overlay", "on_minimize")
        raw_cfg = self.bridge.get_raw_config()
        should_show_overlay = overlay_mode == "always" or (
            raw_cfg.get("start_minimized", False) and overlay_mode == "on_minimize"
        )
        if should_show_overlay:
            cur_mode = str(cfg.get("hud_mode", "normal") or "normal").lower()
            if cur_mode == "dot":
                for win_obj in (self.window, self.overlay_window):
                    ev = getattr(getattr(win_obj, "events", None), "loaded", None)
                    if isinstance(ev, threading.Event) or (ev is not None and hasattr(ev, "wait") and hasattr(ev, "is_set")):
                        try:
                            ev.wait(8.0)
                        except Exception:
                            pass
                time.sleep(0.25)
            self.bridge.show_overlay()
        elif bool(cfg.get("vr_hud_enabled", True)) or bool(cfg.get("vr_dot_enabled", True)):
            # Even when desktop overlay is set to "on_minimize", allow VR overlay to
            # auto-connect and show the configured tier as soon as SteamVR boots.
            cur_mode = str(cfg.get("hud_mode", "normal") or "normal").lower()
            try:
                mgr = self.hud_bridge._get_vr_manager()
                if cur_mode == "dot":
                    mgr.show_dot()
                elif cur_mode in ("mini", "normal", "max"):
                    mgr.show_hud(mode=cur_mode)
            except Exception:
                pass

        # Hook Windows session/shutdown events so minimize_to_tray never blocks OS shutdown/logoff
        if sys.platform == "win32":
            self._register_windows_shutdown_hooks()

    def _register_windows_shutdown_hooks(self):
        """Registers WinForms FormClosing & SystemEvents.SessionEnding hooks to allow clean OS shutdown."""
        try:
            native_form = getattr(self.window, "native", None)
            if native_form is not None and hasattr(native_form, "FormClosing"):
                def _on_native_form_closing(sender, e):
                    try:
                        reason = str(getattr(e, "CloseReason", ""))
                        if reason in ("WindowsShutDown", "ApplicationExitCall", "TaskManagerClosing"):
                            self._is_closing_permanently = True
                            e.Cancel = False
                            threading.Thread(target=self.close_all, daemon=True).start()
                    except Exception:
                        pass
                native_form.FormClosing += _on_native_form_closing
        except Exception as e:
            print(f"[HUD] Could not attach native FormClosing hook: {e}")

        try:
            from Microsoft.Win32 import SystemEvents
            def _on_session_ending(sender, e):
                try:
                    self._is_closing_permanently = True
                    e.Cancel = False
                    threading.Thread(target=self.close_all, daemon=True).start()
                except Exception:
                    pass
            self._session_ending_handler = _on_session_ending
            SystemEvents.SessionEnding += self._session_ending_handler
        except Exception as e:
            print(f"[HUD] Could not attach SystemEvents.SessionEnding hook: {e}")

    def _cleanup_before_window_close(self, destroy_main: bool = True):
        """Idempotently tears down tray, overlay, timers, and optionally destroys the main window."""
        with self._close_all_lock:
            if self._close_all_done:
                return
            self._close_all_done = True

        self._is_closing_permanently = True
        if hasattr(self.bridge, "_is_shutting_down"):
            self.bridge._is_shutting_down = True

        # Guarantee process termination if a live WinForms/WebView2 loop blocks Application.Exit
        # (only arm when a real native WinForms handle exists so test mocks don't terminate the runner)
        has_native_ui = getattr(self.window, "native", None) is not None or getattr(self.overlay_window, "native", None) is not None
        if has_native_ui:
            watchdog = threading.Timer(2.5, os._exit, args=(0,))
            watchdog.daemon = True
            watchdog.start()
            self._shutdown_watchdog = watchdog

        if getattr(self, "_session_ending_handler", None) is not None:
            try:
                from Microsoft.Win32 import SystemEvents
                SystemEvents.SessionEnding -= self._session_ending_handler
                self._session_ending_handler = None
            except Exception:
                pass
        if hasattr(self, "_pos_save_timer") and self._pos_save_timer:
            try:
                self._pos_save_timer.cancel()
            except Exception:
                pass
        if hasattr(self, "_last_moved_pos") and self._last_moved_pos:
            try:
                mx, my = self._last_moved_pos
                self.bridge.save_overlay_position(mx, my)
            except Exception:
                pass
        try:
            if getattr(self, "hud_bridge", None) and hasattr(self.hud_bridge, "destroy_dot"):
                self.hud_bridge.destroy_dot()
        except Exception:
            pass
        try:
            if getattr(self, "dot_overlay", None):
                self.dot_overlay.destroy()
        except Exception:
            pass
        try:
            self.tray.stop()
        except Exception:
            pass

        def _invoke_on_ui_or_direct(win_obj, fn):
            def _safe_run():
                try:
                    fn()
                except Exception:
                    pass

            native = getattr(win_obj, "native", None) if win_obj is not None else None
            if native is not None:
                try:
                    if getattr(native, "IsDisposed", False) or getattr(native, "Disposing", False):
                        return
                    if getattr(native, "InvokeRequired", False) and hasattr(native, "BeginInvoke"):
                        if getattr(native, "IsHandleCreated", True):
                            from System import Action
                            native.BeginInvoke(Action(_safe_run))
                            return
                except Exception:
                    return
            _safe_run()

        try:
            if self.overlay_window:
                _invoke_on_ui_or_direct(self.overlay_window, self.overlay_window.destroy)
        except Exception:
            pass
        if destroy_main:
            if sys.platform == "win32" and has_native_ui:
                try:
                    from System.Windows.Forms import Application

                    def _exit_winforms():
                        try:
                            if self.window:
                                self.window.destroy()
                        except Exception:
                            pass
                        try:
                            Application.Exit()
                        except Exception:
                            pass

                    _invoke_on_ui_or_direct(self.window, _exit_winforms)
                except Exception:
                    pass
            else:
                try:
                    if self.window:
                        _invoke_on_ui_or_direct(self.window, self.window.destroy)
                except Exception:
                    pass

    def close_all(self):
        """Cleanly closes all windows and stops tray."""
        self._cleanup_before_window_close(destroy_main=True)

    @staticmethod
    def _ensure_webview_pdf_default_preference(storage_dir: str) -> None:
        """
        Pre-configures WebView2's Chromium Preferences so Print Preview defaults
        to Chromium's built-in 'Save as PDF' destination instead of 'Microsoft Print to PDF'
        or slow WSD network printers.
        """
        try:
            pref_dir = os.path.join(storage_dir, "EBWebView", "Default")
            os.makedirs(pref_dir, exist_ok=True)
            pref_path = os.path.join(pref_dir, "Preferences")
            prefs = {}
            if os.path.isfile(pref_path):
                try:
                    with open(pref_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                        if isinstance(loaded, dict):
                            prefs = loaded
                except Exception:
                    prefs = {}

            save_as_pdf_dest = {
                "id": "Save as PDF",
                "origin": "local",
                "account": "",
                "capabilities": None,
                "displayName": "Save as PDF",
                "extensionId": "",
                "extensionName": "",
            }
            app_state = {
                "version": 2,
                "isGcpPromoDismissed": False,
                "recentDestinations": [save_as_pdf_dest],
            }
            printing_cfg = prefs.setdefault("printing", {})
            if not isinstance(printing_cfg, dict):
                printing_cfg = {}
                prefs["printing"] = printing_cfg
            sticky_cfg = printing_cfg.setdefault("print_preview_sticky_settings", {})
            if not isinstance(sticky_cfg, dict):
                sticky_cfg = {}
                printing_cfg["print_preview_sticky_settings"] = sticky_cfg
            sticky_cfg["appState"] = json.dumps(app_state, separators=(",", ":"))

            with open(pref_path, "w", encoding="utf-8") as f:
                json.dump(prefs, f, separators=(",", ":"))
        except Exception as e:
            print(f"[WARN] [HUD] Could not pre-seed WebView2 Save as PDF preference: {e}")

    def _clear_stale_webview_cache(self, storage_dir: str):
        """
        Clears WebView2 HTTP and V8 JS bytecode caches whenever any static UI asset
        (HTML/JS/CSS) changes on disk, preventing stale cached scripts from running
        across upgrades or local edits while preserving Preferences and Local Storage.
        """
        import shutil

        try:
            static_dir = os.path.dirname(self.html_path)
            sig_parts = []
            if os.path.isdir(static_dir):
                for fname in sorted(os.listdir(static_dir)):
                    if fname.endswith((".html", ".js", ".css")):
                        fpath = os.path.join(static_dir, fname)
                        try:
                            st = os.stat(fpath)
                            sig_parts.append(f"{fname}:{st.st_mtime_ns}:{st.st_size}")
                        except OSError:
                            pass
            current_sig = "|".join(sig_parts)
            stamp_path = os.path.join(storage_dir, ".ui_asset_stamp")
            prev_sig = ""
            if os.path.isfile(stamp_path):
                try:
                    with open(stamp_path, "r", encoding="utf-8") as f:
                        prev_sig = f.read().strip()
                except OSError:
                    prev_sig = ""

            if current_sig and current_sig != prev_sig:
                default_dir = os.path.join(storage_dir, "EBWebView", "Default")
                for cache_sub in ("Cache", "Code Cache"):
                    cache_path = os.path.join(default_dir, cache_sub)
                    if os.path.isdir(cache_path):
                        shutil.rmtree(cache_path, ignore_errors=True)
                os.makedirs(storage_dir, exist_ok=True)
                with open(stamp_path, "w", encoding="utf-8") as f:
                    f.write(current_sig)
                print("[HUD] Cleared stale WebView2 HTTP/JS cache after UI asset update.")
        except Exception as e:
            print(f"[WARN] [HUD] Could not refresh WebView2 static cache: {e}")

    def start(self, debug: bool = False):
        """Starts the PyWebView main GUI event loop and system tray."""
        import logging
        import traceback

        logging.getLogger("pywebview").setLevel(logging.DEBUG)
        storage_dir = get_webview_cache_dir()
        os.environ["WEBVIEW2_USER_DATA_FOLDER"] = storage_dir
        self._clear_stale_webview_cache(storage_dir)
        self._ensure_webview_pdf_default_preference(storage_dir)
        print(f"[HUD] Launching pywebview window from {self.html_url}...")
        self.tray.start()
        try:
            webview.start(
                self._on_started,
                debug=debug,
                gui="edgechromium",
                private_mode=False,
                storage_path=storage_dir,
            )
        except Exception as exc:
            err_trace = traceback.format_exc()
            logging.getLogger("Aether").critical("PyWebView failed to initialize:\n%s", err_trace)
            if sys.platform == "win32":
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(
                        0,
                        f"Aether UI failed to start. Please ensure Microsoft Edge WebView2 Runtime is installed.\n\nError: {exc}",
                        "Aether Desktop Startup Error",
                        0x10,
                    )
                except Exception:
                    pass
            raise
        finally:
            self._cleanup_before_window_close(destroy_main=False)

