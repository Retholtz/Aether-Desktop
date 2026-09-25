"""
Aether Desktop - Proactive Ambient Desktop Notification Subsystem
Provides non-blocking Windows toast/balloon notifications and subtle ambient audio chimes.
"""

import os
import subprocess
import sys
import threading
import time
from typing import Optional

# Optional winsound for subtle ambient chime
try:
    import winsound
except ImportError:
    winsound = None


class NotificationDispatcher:
    def __init__(self, app_name: str = "Aether"):
        self.app_name = app_name
        self._lock = threading.Lock()
        self._last_notification_time = 0.0
        self._cooldown_seconds = 3.0  # Prevent notification storms

    def notify(self, title: str, message: str, play_chime: bool = True) -> bool:
        """Asynchronously dispatches a notification balloon/toast and ambient chime."""
        now = time.time()
        with self._lock:
            if now - self._last_notification_time < self._cooldown_seconds:
                return False  # Drop or throttle rapid-fire alerts
            self._last_notification_time = now

        threading.Thread(
            target=self._dispatch_background,
            args=(title, message, play_chime),
            daemon=True,
            name="AetherNotificationThread"
        ).start()
        return True

    def _dispatch_background(self, title: str, message: str, play_chime: bool):
        # 1. Play subtle audio chime
        if play_chime and winsound:
            try:
                # Play standard Windows Asterisk/Information sound non-blockingly
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                pass

        # 2. Display Windows Notification via ctypes / Shell
        self._show_windows_toast(title, message)

    def _show_windows_toast(self, title: str, message: str):
        """
        Uses PowerShell to dispatch a clean Windows toast without external heavy dependencies.
        Falls back to console log if execution fails.
        """
        try:
            # Clean single-quotes to prevent script injection in powershell
            safe_title = str(title).replace("'", "''")
            safe_message = str(message).replace("'", "''")
            safe_app = str(self.app_name).replace("'", "''")

            ps_script = f"""
            [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
            [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
            $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
            $textNodes = $template.GetElementsByTagName("text")
            $textNodes.Item(0).AppendChild($template.CreateTextNode('{safe_title}')) > $null
            $textNodes.Item(1).AppendChild($template.CreateTextNode('{safe_message}')) > $null
            $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
            try {{
                $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{safe_app}')
                $notifier.Show($toast)
            }} catch {{
                $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe')
                $notifier.Show($toast)
            }}
            """

            subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
                creationflags=0x08000000,  # CREATE_NO_WINDOW
                timeout=5
            )
        except Exception as e:
            # Non-fatal fallback
            print(f"[INFO] [NOTIFICATION] [{title}] {message} (Native toast fallback: {e})")
