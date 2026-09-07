"""
Aether Desktop - UI Subsystem: System Tray Manager
Provides system tray integration via pystray with quick actions:
- Restore/Open Aether Desktop
- Toggle Floating HUD Overlay
- Mute/Unmute Microphone
- Exit Application
"""

import threading
from typing import Callable, Optional
from PIL import Image, ImageDraw
import pystray


def create_tray_icon_image(size: int = 64) -> Image.Image:
    """Generates a sleek glowing circular icon for Aether Desktop tray."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Outer circle background (dark glass)
    draw.ellipse((2, 2, size - 3, size - 3), fill=(18, 22, 32, 240), outline=(0, 210, 255, 200), width=2)

    # Inner glowing cyan orb
    pad = size // 4
    draw.ellipse((pad, pad, size - pad, size - pad), fill=(0, 220, 255, 255))

    # Core white specular center
    core_pad = size // 3
    draw.ellipse((core_pad, core_pad, size - core_pad, size - core_pad), fill=(255, 255, 255, 220))

    return img


class SystemTrayManager:
    """Manages the Windows taskbar notification tray icon and menu."""

    def __init__(
        self,
        on_open_main: Callable[[], None],
        on_toggle_overlay: Callable[[], None],
        on_toggle_mute: Callable[[], None],
        on_exit: Callable[[], None],
        title: str = "Aether Desktop"
    ):
        self.on_open_main = on_open_main
        self.on_toggle_overlay = on_toggle_overlay
        self.on_toggle_mute = on_toggle_mute
        self.on_exit = on_exit
        self.title = title
        self._icon: Optional[pystray.Icon] = None
        self._is_running = False

    def start(self):
        """Initializes and runs the tray icon detached in a background thread."""
        if self._is_running:
            return

        icon_image = create_tray_icon_image()

        def _action_open(icon, item):
            self.on_open_main()

        def _action_toggle_overlay(icon, item):
            self.on_toggle_overlay()

        def _action_mute(icon, item):
            self.on_toggle_mute()

        def _action_exit(icon, item):
            self.stop()
            self.on_exit()

        menu = pystray.Menu(
            pystray.MenuItem("Open Aether Desktop", _action_open, default=True),
            pystray.MenuItem("Toggle Floating Overlay", _action_toggle_overlay),
            pystray.MenuItem("Mute Microphone", _action_mute),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", _action_exit)
        )

        self._icon = pystray.Icon(
            name="aether_desktop",
            icon=icon_image,
            title=self.title,
            menu=menu
        )

        self._is_running = True
        self._icon.run_detached()

    def stop(self):
        """Removes the icon from the system tray safely without blocking shutdown."""
        if self._icon and self._is_running:
            self._is_running = False
            try:
                def _do_stop():
                    try:
                        self._icon.stop()
                    except Exception:
                        pass

                t = threading.Thread(target=_do_stop, daemon=True)
                t.start()
                t.join(timeout=1.0)
            except Exception:
                pass

