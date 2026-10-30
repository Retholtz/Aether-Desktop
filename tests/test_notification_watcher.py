import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from core.notification_watcher import DesktopNotificationWatcher, DesktopNotification
from tools.notification_tools import register_notification_tools
from core.gui_bridge import GuiBridge
import core.config


class TestNotificationWatcher(unittest.TestCase):
    def setUp(self):
        self.watcher = DesktopNotificationWatcher(
            poll_interval=0.1,
            target_apps=["Discord", "Slack"]
        )

    def test_record_seen_and_deduplication(self):
        self.watcher._record_seen(101)
        self.assertIn(101, self.watcher._seen_ids)
        self.assertEqual(len(self.watcher._seen_queue), 1)

        # Duplicate must not increase queue length
        self.watcher._record_seen(101)
        self.assertEqual(len(self.watcher._seen_queue), 1)

    def test_filter_and_recent_storage(self):
        n1 = DesktopNotification(notification_id=1, app_name="Discord", title="Alice", body="Hey there")
        n2 = DesktopNotification(notification_id=2, app_name="Spotify", title="Playing", body="Song")

        self.watcher._recent_notifications.appendleft(n1)
        self.watcher._recent_notifications.appendleft(n2)

        discord_only = self.watcher.get_recent(app_filter="Discord")
        self.assertEqual(len(discord_only), 1)
        self.assertEqual(discord_only[0]["title"], "Alice")

        all_notifs = self.watcher.get_recent()
        self.assertEqual(len(all_notifs), 2)

    def test_clear_history(self):
        self.watcher._recent_notifications.append(
            DesktopNotification(notification_id=1, app_name="Discord", title="A", body="B")
        )
        self.assertEqual(len(self.watcher.get_recent()), 1)
        self.watcher.clear_history()
        self.assertEqual(len(self.watcher.get_recent()), 0)

    @patch("core.notification_watcher.WINRT_AVAILABLE", True)
    def test_text_extraction_helper(self):
        mock_notif = MagicMock()
        mock_binding = MagicMock()

        el1 = MagicMock()
        el1.text = "Commander Jameson"
        el2 = MagicMock()
        el2.text = "Under attack in Deciat!"
        mock_binding.get_text_elements.return_value = [el1, el2]

        mock_notif.notification.visual.get_binding.return_value = mock_binding

        title, body = self.watcher._extract_text(mock_notif)
        self.assertEqual(title, "Commander Jameson")
        self.assertEqual(body, "Under attack in Deciat!")

    def test_dynamic_whitelist_integration_and_filtering(self):
        # Starts with Discord and Slack
        dynamic_whitelist = ["spotify.exe", "Spotify"]
        watcher = DesktopNotificationWatcher(
            poll_interval=0.1,
            target_apps=["Discord", "Slack"],
            whitelist_getter=lambda: dynamic_whitelist
        )

        effective = watcher.get_effective_targets()
        self.assertIn("discord", effective)
        self.assertIn("slack", effective)
        self.assertIn("spotify", effective)

        # Dynamic update to whitelist without restarting watcher
        dynamic_whitelist.append("Signal")
        dynamic_whitelist.append("*")  # Wildcard must be ignored

        updated_targets = watcher.get_effective_targets()
        self.assertIn("signal", updated_targets)
        self.assertNotIn("*", updated_targets)

    def test_notification_tools_registration_and_execution(self):
        self.watcher.is_supported = MagicMock(return_value=True)
        dispatcher = MagicMock()
        registered_tools = {}

        def mock_register(name, description=None, parameters=None, handler=None, **kwargs):
            if isinstance(description, dict) and handler is None:
                # signature (name, decl, handler)
                registered_tools[name] = kwargs.get("handler") or description
            else:
                registered_tools[name] = handler

        dispatcher.register_tool.side_effect = mock_register
        register_notification_tools(dispatcher, self.watcher)

        self.assertIn("get_recent_desktop_notifications", registered_tools)
        self.assertIn("clear_recent_notifications", registered_tools)

        # Test tool calls
        self.watcher._recent_notifications.append(
            DesktopNotification(notification_id=99, app_name="Discord", title="Bob", body="Meeting now")
        )

        loop = asyncio.new_event_loop()
        try:
            get_fn = registered_tools["get_recent_desktop_notifications"]
            res = loop.run_until_complete(get_fn(app_name="Discord", limit=5))
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["count"], 1)

            clear_fn = registered_tools["clear_recent_notifications"]
            res_clear = loop.run_until_complete(clear_fn())
            self.assertEqual(res_clear["status"], "success")
            self.assertEqual(len(self.watcher.get_recent()), 0)
        finally:
            loop.close()


class TestNotificationGuiBridge(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.settings_path = os.path.join(self.tmp_dir, "settings.json")
        self.orig_settings_path = core.config.SETTINGS_PATH
        core.config.SETTINGS_PATH = self.settings_path

        self.bridge = GuiBridge(config_path="config.json")
        self.bridge._engine = MagicMock()
        self.bridge._engine.notification_watcher = DesktopNotificationWatcher(
            poll_interval=1.5,
            target_apps=["Discord", "Slack"]
        )

    def tearDown(self):
        core.config.SETTINGS_PATH = self.orig_settings_path
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_get_and_save_desktop_notifications_status(self):
        status = self.bridge.get_desktop_notifications_status()
        self.assertTrue(status["success"])
        self.assertTrue(status["enabled"])

        # Save config
        res = self.bridge.save_desktop_notifications_config(
            enabled=True,
            verbal_announcement=True,
            poll_interval_seconds=2.0,
            target_apps=["Discord", "Slack", "Microsoft Teams"]
        )
        self.assertTrue(res["enabled"])
        self.assertTrue(res["verbal_announcement"])
        self.assertEqual(res["poll_interval_seconds"], 2.0)
        self.assertIn("Discord", res["target_apps"])

        # Check OAuth status aggregation
        oauth_status = self.bridge.get_oauth_status()
        self.assertIn("desktop_notifications", oauth_status)
        self.assertTrue(oauth_status["desktop_notifications"]["verbal_announcement"])

        # Test querying recent and clear
        self.bridge._engine.notification_watcher._recent_notifications.append(
            DesktopNotification(notification_id=1, app_name="Discord", title="DM", body="Hello")
        )
        recent = self.bridge.get_recent_desktop_notifications()
        self.assertEqual(recent["count"], 1)

        clear_res = self.bridge.clear_desktop_notifications()
        self.assertTrue(clear_res["success"])
        self.assertEqual(len(self.bridge._engine.notification_watcher.get_recent()), 0)


class TestEngineNotificationIntegration(unittest.TestCase):
    def test_handle_desktop_notification_and_verbal_announcement(self):
        from core.engine import AetherEngine
        with patch.object(AetherEngine, "__init__", lambda self, *args, **kwargs: None):
            engine = AetherEngine()
            engine.settings = {
                "integrations": {
                    "desktop_notifications": {
                        "enabled": True,
                        "verbal_announcement": True
                    }
                }
            }
            engine.notify = MagicMock()
            engine.audio = None
            engine.is_speaking = False
            engine.queue_whisper = MagicMock()

            notif = DesktopNotification(
                notification_id=123,
                app_name="Discord",
                title="GhostRider",
                body="Raid starting in 5 minutes"
            )

            # Invoke notification dispatch handler
            engine._handle_desktop_notification = AetherEngine._handle_desktop_notification.__get__(engine)
            engine._handle_desktop_notification(notif)

            # Verified notification was emitted to UI HUD
            engine.notify.assert_called_with("desktop_notification", notif.to_dict())

            # Verified verbal whisper was triggered because announce_verbally=True and not is_speaking
            engine.queue_whisper.assert_called_with("New Discord message from GhostRider.")

            # If currently speaking, queue_whisper must not be called
            engine.queue_whisper.reset_mock()
            engine.is_speaking = True
            engine._handle_desktop_notification(notif)
            engine.queue_whisper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
