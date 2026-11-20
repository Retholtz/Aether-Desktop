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
from typing import Optional

from core.paths import get_bundle_dir
from core.screen_stream import ensure_thread_desktop
from ui.tray import SystemTrayManager


def force_foreground_window(hwnd: int, maximize: bool = False):
    """
    Brings a window to the foreground and unminimizes/maximizes it reliably on Windows,
    bypassing foreground lock restrictions by attaching input threads.
    """
    if sys.platform != "win32" or not hwnd:
        return
    try:
        import ctypes
        import win32gui
        import win32con

        user32 = ctypes.windll.user32
        user32.AllowSetForegroundWindow(-1)

        cur_thread_id = user32.GetCurrentThreadId()
        fore_hwnd = user32.GetForegroundWindow()
        fore_thread_id = user32.GetWindowThreadProcessId(fore_hwnd, 0) if fore_hwnd else 0

        attached = False
        if fore_thread_id and fore_thread_id != cur_thread_id:
            attached = bool(user32.AttachThreadInput(cur_thread_id, fore_thread_id, True))

        try:
            cmd = win32con.SW_MAXIMIZE if maximize else win32con.SW_RESTORE
            win32gui.ShowWindow(hwnd, cmd)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(cur_thread_id, fore_thread_id, False)
    except Exception as e:
        print(f"[HUD] force_foreground_window error: {e}")


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
        import win32gui

        hwnd = None
        if hasattr(window, "native") and window.native and hasattr(window.native, "Handle"):
            try:
                hwnd = window.native.Handle.ToInt64()
            except Exception:
                hwnd = None
        if not hwnd:
            hwnd = win32gui.FindWindow(None, "Aether Overlay")
        if not hwnd:
            return

        # On Windows 11+ (build >= 22000), enable DWM native rounded corners cleanly
        try:
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_ROUND = 2
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(ctypes.c_int(DWMWCP_ROUND)),
                ctypes.sizeof(ctypes.c_int)
            )
        except Exception:
            pass

        # Apply WS_EX_TOOLWINDOW so the floating HUD overlay doesn't pollute Windows 11 Alt+Tab / taskbar,
        # and WS_EX_NOACTIVATE in mini pill mode so clicking/hovering during games won't steal focus.
        try:
            import win32con
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            new_style = (ex_style | win32con.WS_EX_TOOLWINDOW) & ~win32con.WS_EX_APPWINDOW
            if (mode or "").lower() == "mini":
                new_style |= win32con.WS_EX_NOACTIVATE
            else:
                new_style &= ~win32con.WS_EX_NOACTIVATE
            if new_style != ex_style:
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, new_style)
                win32gui.SetWindowPos(
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
        self._normal_size = (440, 180)
        self._max_size = (560, 480)

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
        """Returns initial overlay configuration including agent name, hud mode, and engine state."""
        cfg = self._bridge.get_raw_config()
        agent_name = cfg.get("api", {}).get("agent_name", "Aether")
        hud_mode = cfg.get("ui", {}).get("hud_mode", "normal")
        ui_theme = cfg.get("ui", {}).get("theme", "system")
        is_running = getattr(self._bridge._engine, "is_running", False) if self._bridge._engine else False
        return {
            "success": True,
            "agent_name": agent_name,
            "hud_mode": hud_mode,
            "theme": ui_theme,
            "is_running": is_running,
            "state": "listening" if is_running else "standby",
        }

    def save_hud_position(self, x: Optional[int] = None, y: Optional[int] = None) -> dict:
        """Saves current HUD overlay window position to config and disk."""
        try:
            if x is None or y is None:
                if self._window:
                    x = getattr(self._window, "x", None)
                    y = getattr(self._window, "y", None)
            if x is not None and y is not None:
                return self._bridge.save_overlay_position(int(x), int(y))
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Invalid coordinates"}

    def set_mode(self, mode: str) -> dict:
        """Resizes the HUD overlay window according to requested tier (mini, normal, max)."""
        mode = mode.lower() if mode else "normal"
        if mode not in ("mini", "normal", "max"):
            mode = "normal"
        if self._window:
            with self._mode_lock:
                w, h = self._mini_wide_size if self._mini_fps_visible else self._mini_narrow_size
                if mode == "normal":
                    w, h = self._normal_size[0], self._normal_size[1]
                elif mode == "max":
                    w, h = self._max_size[0], self._max_size[1]

                # Re-clamp position so expanding width/height does not push window off screen
                cur_x = getattr(self._window, "x", None)
                cur_y = getattr(self._window, "y", None)
                if cur_x is not None and cur_y is not None:
                    cx, cy = clamp_window_position(cur_x, cur_y, width=w, height=h)
                    if cx != cur_x or cy != cur_y:
                        self._window.move(cx, cy)
                        self._bridge.save_overlay_position(cx, cy)

                self._window.resize(w, h)
                apply_hud_window_shape(self._window, mode)

                # Ensure overlay JavaScript switches CSS classes and DOM layout immediately
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
                    cur_x = getattr(self._window, "x", None)
                    cur_y = getattr(self._window, "y", None)
                    if cur_x is not None and cur_y is not None:
                        cx, cy = clamp_window_position(cur_x, cur_y, width=w, height=h)
                        if cx != cur_x or cy != cur_y:
                            self._window.move(cx, cy)
                    self._window.resize(w, h)
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": True, "visible": new_vis}

    def resize_overlay(self, width: int, height: int, x: Optional[int] = None, y: Optional[int] = None) -> dict:
        """Dynamically resizes (and optionally repositions) the HUD overlay window."""
        try:
            if self._window:
                w = max(100, int(width))
                h = max(40, int(height))
                self._window.resize(w, h)
                if x is not None and y is not None:
                    self._window.move(int(x), int(y))
                    self._bridge.save_overlay_position(int(x), int(y))
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
        """Triggers native OS-level window drag via Win32."""
        if sys.platform == "win32" and self._window:
            try:
                import win32gui
                import win32con
                hwnd = None
                if hasattr(self._window, "native") and self._window.native and hasattr(self._window.native, "Handle"):
                    try:
                        hwnd = self._window.native.Handle.ToInt64()
                    except Exception:
                        hwnd = None
                if not hwnd:
                    hwnd = win32gui.FindWindow(None, "Aether Overlay")
                if hwnd:
                    win32gui.ReleaseCapture()
                    win32gui.PostMessage(hwnd, win32con.WM_NCLBUTTONDOWN, win32con.HTCAPTION, 0)
                return {"success": True}
            except Exception as e:
                return {"success": False, "error": str(e)}
        return {"success": False, "error": "Not supported on this platform"}

    def move_overlay(self, x: int, y: int) -> dict:
        """Moves the HUD overlay window to screen coordinates (x, y) with boundary clamping."""
        try:
            if self._window:
                w = getattr(self._window, "width", None) or 440
                h = getattr(self._window, "height", None) or 180
                cx, cy = clamp_window_position(x, y, width=w, height=h)
                self._window.move(cx, cy)
                return {"success": True}
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

        # Determine path to static UI assets
        base_dir = get_bundle_dir()
        self.html_path = os.path.join(base_dir, "ui", "static", "index.html")
        self.overlay_html_path = os.path.join(base_dir, "ui", "static", "overlay.html")

        # 1. Create Main Application Window
        self.window = webview.create_window(
            title=self.title,
            url=self.html_path,
            js_api=self.bridge,
            width=self.width,
            height=self.height,
            min_size=(self.min_width, self.min_height),
            background_color="#202020"
        )
        self.bridge.set_window(self.window)

        # 2. Create Floating HUD Overlay Window with dedicated HUDBridge
        self.hud_bridge = HUDBridge(window=None, bridge=self.bridge)
        cfg_ui = self.bridge.get_raw_config().get("ui", {})
        cfg_mode = cfg_ui.get("hud_mode", "normal")
        init_w, init_h = (440, 180)
        if cfg_mode == "mini":
            init_w, init_h = (176, 56)
        elif cfg_mode == "max":
            init_w, init_h = (560, 480)

        init_x = cfg_ui.get("overlay_x")
        init_y = cfg_ui.get("overlay_y")
        cx, cy = clamp_window_position(init_x, init_y, width=init_w, height=init_h)

        overlay_kwargs = {
            "title": "Aether Overlay",
            "url": self.overlay_html_path,
            "js_api": self.hud_bridge,
            "width": init_w,
            "height": init_h,
            "x": cx,
            "y": cy,
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
        self.bridge.set_overlay_window(self.overlay_window)
        self.bridge.set_hud_bridge(self.hud_bridge)

        # If clamped position differed from config, persist clamped coordinates
        if init_x != cx or init_y != cy:
            self.bridge.save_overlay_position(cx, cy)

        # Wire window moved event for debounced position persistence
        self._pos_save_timer = None
        self._last_moved_pos = None

        def _save_debounced_pos():
            if self._last_moved_pos and self.overlay_window:
                mx, my = self._last_moved_pos
                cur_x = getattr(self.overlay_window, "x", None)
                cur_y = getattr(self.overlay_window, "y", None)
                if cur_x is not None and cur_y is not None and (cur_x != mx or cur_y != my):
                    self.overlay_window.move(mx, my)
                self.bridge.save_overlay_position(mx, my)

        def _on_overlay_moved(x, y):
            try:
                w = getattr(self.overlay_window, "width", None) or 440
                h = getattr(self.overlay_window, "height", None) or 180
                cx, cy = clamp_window_position(x, y, width=w, height=h)
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
        if overlay_mode == "always" or (raw_cfg.get("start_minimized", False) and overlay_mode == "on_minimize"):
            self.bridge.show_overlay()

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
            self.tray.stop()
        except Exception:
            pass

        def _invoke_on_ui_or_direct(win_obj, fn):
            native = getattr(win_obj, "native", None) if win_obj is not None else None
            if native is not None and getattr(native, "InvokeRequired", False) and hasattr(native, "BeginInvoke"):
                try:
                    from System import Action
                    native.BeginInvoke(Action(fn))
                    return
                except Exception:
                    pass
            fn()

        try:
            if self.overlay_window:
                _invoke_on_ui_or_direct(self.overlay_window, self.overlay_window.destroy)
        except Exception:
            pass
        if destroy_main:
            try:
                if self.window:
                    _invoke_on_ui_or_direct(self.window, self.window.destroy)
            except Exception:
                pass
            if sys.platform == "win32" and has_native_ui:
                try:
                    from System.Windows.Forms import Application
                    _invoke_on_ui_or_direct(self.window, Application.Exit)
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

    def start(self, debug: bool = False):
        """Starts the PyWebView main GUI event loop and system tray."""
        import logging
        import traceback
        from core.paths import app_path

        logging.getLogger("pywebview").setLevel(logging.DEBUG)
        storage_dir = app_path("data", "webview_profile")
        self._ensure_webview_pdf_default_preference(storage_dir)
        print(f"[HUD] Launching pywebview window from {self.html_path}...")
        self.tray.start()
        try:
            webview.start(
                self._on_started,
                debug=debug,
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

