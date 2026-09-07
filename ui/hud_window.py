"""
Aether Desktop - UI Subsystem: HUD Window Runner
Manages pywebview window initialization, styling, floating HUD overlay, system tray, and GUI lifecycle.
"""

import os
import sys
import webview
from typing import Optional

from core.screen_stream import ensure_thread_desktop
from ui.tray import SystemTrayManager


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

        # 2. Create Floating HUD Overlay Window (frameless, always on top, draggable)
        self.overlay_window = webview.create_window(
            title="Aether Overlay",
            url=self.overlay_html_path,
            js_api=self.bridge,
            width=430,
            height=110,
            frameless=True,
            on_top=True,
            easy_drag=True,
            hidden=True,
            background_color="#121620"
        )
        self.bridge.set_overlay_window(self.overlay_window)

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

