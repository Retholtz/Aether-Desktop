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


def apply_hud_window_shape(window: Optional[webview.Window], mode: str):
    """
    Applies an OS-level window clipping region using Win32 GDI SetWindowRgn.
    Clips out any window borders/corners so pixels outside the rounded pill or card
    are 100% transparent and pass-through to the desktop on Windows.
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

        rect = win32gui.GetWindowRect(hwnd)
        w = max(1, rect[2] - rect[0])
        h = max(1, rect[3] - rect[1])

        dpi = ctypes.windll.user32.GetDpiForWindow(hwnd) if hasattr(ctypes.windll.user32, "GetDpiForWindow") else 96
        scale = dpi / 96.0
        # Unified sleek 14px rounded corner styling across all modes (mini, normal, max)
        corner_diam = max(8, int(28 * scale))
        rgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, corner_diam, corner_diam)
        ctypes.windll.user32.SetWindowRgn(hwnd, rgn, True)
    except Exception as e:
        print(f"[HUD SHAPE ERROR] Failed to apply window shape: {e}")


def is_position_visible_on_screens(x: int, y: int, width: int = 180, height: int = 52) -> bool:
    """
    Verifies that at least a 30x30 portion of the window intersects
    a currently connected, active monitor to prevent off-screen windows.
    """
    try:
        screens = webview.screens
        if not screens:
            return True
        for s in screens:
            inter_left = max(s.x, x)
            inter_top = max(s.y, y)
            inter_right = min(s.x + s.width, x + width)
            inter_bottom = min(s.y + s.height, y + height)
            if (inter_right - inter_left) >= 30 and (inter_bottom - inter_top) >= 30:
                return True
        return False
    except Exception:
        return True


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
            if mode == "mini":
                self._window.resize(180, 52)
            elif mode == "normal":
                self._window.resize(self._normal_size[0], self._normal_size[1])
            elif mode == "max":
                self._window.resize(self._max_size[0], self._max_size[1])

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

    def move_overlay(self, x: int, y: int) -> dict:
        """Moves the HUD overlay window to screen coordinates (x, y)."""
        try:
            if self._window:
                self._window.move(int(x), int(y))
                self._bridge.save_overlay_position(int(x), int(y))
                return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Window not initialized"}

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
    ):
        ensure_thread_desktop()
        self.bridge = bridge
        self.title = title
        self.width = width
        self.height = height
        self.min_width = min_width
        self.min_height = min_height

        # Determine path to static UI assets
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.html_path = os.path.join(base_dir, "ui", "static", "index.html")
        if not os.path.exists(self.html_path):
            self.html_path = os.path.join(base_dir, "gui", "index.html")

        self.overlay_html_path = os.path.join(base_dir, "ui", "static", "overlay.html")
        if not os.path.exists(self.overlay_html_path):
            self.overlay_html_path = os.path.join(base_dir, "gui", "overlay.html")

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
            init_w, init_h = (180, 52)
        elif cfg_mode == "max":
            init_w, init_h = (560, 480)

        init_x = cfg_ui.get("overlay_x")
        init_y = cfg_ui.get("overlay_y")
        overlay_kwargs = {
            "title": "Aether Overlay",
            "url": self.overlay_html_path,
            "js_api": self.hud_bridge,
            "width": init_w,
            "height": init_h,
            "min_size": (10, 10),
            "frameless": True,
            "on_top": True,
            "easy_drag": True,
            "hidden": True,
            "transparent": False,
            "background_color": "#121620"
        }
        if init_x is not None and init_y is not None:
            try:
                ix, iy = int(init_x), int(init_y)
                if is_position_visible_on_screens(ix, iy, init_w, init_h):
                    overlay_kwargs["x"] = ix
                    overlay_kwargs["y"] = iy
            except (ValueError, TypeError):
                pass

        self.overlay_window = webview.create_window(**overlay_kwargs)
        self.hud_bridge._window = self.overlay_window
        self.bridge.set_overlay_window(self.overlay_window)
        self.bridge.set_hud_bridge(self.hud_bridge)

        # Wire window moved event for debounced position persistence
        self._pos_save_timer = None
        self._last_moved_pos = None

        def _save_debounced_pos():
            if self._last_moved_pos:
                mx, my = self._last_moved_pos
                self.bridge.save_overlay_position(mx, my)

        def _on_overlay_moved(x, y):
            try:
                self._last_moved_pos = (int(x), int(y))
                if self._pos_save_timer:
                    self._pos_save_timer.cancel()
                self._pos_save_timer = threading.Timer(0.5, _save_debounced_pos)
                self._pos_save_timer.daemon = True
                self._pos_save_timer.start()
            except Exception:
                pass

        self.overlay_window.events.moved += _on_overlay_moved

        # 3. Wire window minimize and restore events for overlay mode
        def on_minimized():
            cfg = self.bridge.get_raw_config().get("ui", {})
            overlay_mode = cfg.get("floating_overlay", "on_minimize")
            if overlay_mode == "on_minimize":
                self.bridge.show_overlay()

        def on_restored():
            cfg = self.bridge.get_raw_config().get("ui", {})
            overlay_mode = cfg.get("floating_overlay", "on_minimize")
            if overlay_mode == "on_minimize":
                self.bridge.hide_overlay()

        self.window.events.minimized += on_minimized
        self.window.events.restored += on_restored

        # 4. Initialize System Tray Manager
        self.tray = SystemTrayManager(
            on_open_main=self.bridge.restore_main_window,
            on_toggle_overlay=self.bridge.toggle_overlay,
            on_toggle_mute=self.bridge.toggle_mic_mute,
            on_set_hud_mode=self.bridge.set_mode,
            on_cycle_hud_mode=self.bridge.cycle_hud_mode,
            on_exit=self.close_all,
            title="Aether Desktop"
        )

    def _on_started(self):
        """Called once pywebview event loop is ready."""
        cfg = self.bridge.get_raw_config().get("ui", {})
        overlay_mode = cfg.get("floating_overlay", "on_minimize")
        if overlay_mode == "always":
            self.bridge.show_overlay()

    def close_all(self):
        """Cleanly closes all windows and stops tray."""
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

