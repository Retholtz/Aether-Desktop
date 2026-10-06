"""
Aether Desktop - UI Subsystem: HUD Window Runner
Manages pywebview window initialization, styling, floating HUD overlay, system tray, and GUI lifecycle.
"""

import os
import sys
import threading
import time
import webview
from typing import Optional

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
    except Exception as e:
        print(f"[HUD SHAPE ERROR] Failed to apply window shape: {e}")


def get_monitor_work_areas() -> list:
    """
    Returns a list of connected monitor work areas (excluding taskbars)
    with per-monitor bounds and primary monitor indicator in logical coordinates.
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

                    monitors.append({
                        "left": int(work[0] / scale),
                        "top": int(work[1] / scale),
                        "right": int(work[2] / scale),
                        "bottom": int(work[3] / scale),
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
        self._normal_size = (440, 180)
        self._max_size = (560, 480)

    def get_agent_name(self) -> dict:
        """Returns configured agent name for the HUD overlay."""
        name = self._bridge.get_raw_config().get("api", {}).get("agent_name", "Aether")
        return {"success": True, "agent_name": name}

    def get_overlay_config(self) -> dict:
        """Returns initial overlay configuration including agent name, hud mode, and engine state."""
        cfg = self._bridge.get_raw_config()
        agent_name = cfg.get("api", {}).get("agent_name", "Aether")
        hud_mode = cfg.get("ui", {}).get("hud_mode", "normal")
        is_running = getattr(self._bridge._engine, "is_running", False) if self._bridge._engine else False
        return {
            "success": True,
            "agent_name": agent_name,
            "hud_mode": hud_mode,
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
            w, h = (210, 56)
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

            # Apply OS-level window shape clipping both immediately and post-resize
            apply_hud_window_shape(self._window, mode)

            def _delayed_shape():
                time.sleep(0.06)
                apply_hud_window_shape(self._window, mode)

            threading.Thread(target=_delayed_shape, daemon=True).start()

        if hasattr(self._bridge, "on_hud_mode_changed"):
            self._bridge.on_hud_mode_changed(mode)
        return {"success": True, "mode": mode}

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

    def get_agent_name(self) -> dict:
        """Relays get_agent_name call from HUD overlay to GuiBridge."""
        if hasattr(self._bridge, "get_agent_name"):
            return self._bridge.get_agent_name()
        return {"agent_name": "Aether"}


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

        # Determine path to static UI assets
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
            init_w, init_h = (210, 56)
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

        # 3. Wire window close, minimize, and restore events
        def on_closing():
            if getattr(self, "_is_closing_permanently", False):
                return True
            cfg = self.bridge.get_raw_config().get("ui", {})
            if cfg.get("minimize_to_tray", True):
                self.window.hide()
                overlay_mode = cfg.get("floating_overlay", "on_minimize")
                if overlay_mode == "on_minimize":
                    self.bridge.show_overlay()
                return False  # Cancels window destruction to keep running in tray
            else:
                self.close_all()
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
        self.tray = SystemTrayManager(
            on_open_main=self.bridge.restore_main_window,
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

    def close_all(self):
        """Cleanly closes all windows and stops tray."""
        self._is_closing_permanently = True
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
        try:
            if self.overlay_window:
                self.overlay_window.destroy()
        except Exception:
            pass
        try:
            if self.window:
                self.window.destroy()
        except Exception:
            pass

    def start(self, debug: bool = False):
        """Starts the PyWebView main GUI event loop and system tray."""
        print(f"[HUD] Launching pywebview window from {self.html_path}...")
        self.tray.start()
        try:
            webview.start(self._on_started, debug=debug)
        finally:
            self.tray.stop()

