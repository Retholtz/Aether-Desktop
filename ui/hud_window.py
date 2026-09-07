"""
Aether Desktop - UI Subsystem: HUD Window Runner
Manages pywebview window initialization, styling, and GUI lifecycle.
"""

import os
import sys
import webview
from typing import Optional

from core.screen_stream import ensure_thread_desktop


class HudWindow:
    """Encapsulates the PyWebView HUD interface."""

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
            # Fallback to gui/ if ui/static is missing
            self.html_path = os.path.join(base_dir, "gui", "index.html")

        # Create pywebview window
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

    def start(self, debug: bool = False):
        """Starts the PyWebView main GUI event loop (blocking until closed)."""
        print(f"[HUD] Launching pywebview window from {self.html_path}...")
        webview.start(debug=debug)

