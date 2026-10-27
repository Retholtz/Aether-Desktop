import unittest
import os
import tempfile
import shutil
from unittest.mock import MagicMock, patch
from core.gui_bridge import GuiBridge
import core.config


class TestSteamGuiBridge(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.settings_path = os.path.join(self.tmp_dir, "settings.json")
        self.orig_settings_path = core.config.SETTINGS_PATH
        core.config.SETTINGS_PATH = self.settings_path

        self.bridge = GuiBridge(config_path="config.json")
        self.bridge._engine = MagicMock()
        self.bridge._engine.steam_service = MagicMock()

    def tearDown(self):
        core.config.SETTINGS_PATH = self.orig_settings_path
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_get_and_save_steam_status(self):
        # Initial state -> disconnected
        status = self.bridge.get_steam_status()
        self.assertTrue(status["success"])
        self.assertFalse(status["connected"])

        # Save credentials
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = b'{"response": {"players": [{"personaname": "ViperPlayer"}]}}'
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            res = self.bridge.save_steam_config(
                api_key="TEST_API_KEY_123456",
                steam_id64="76561198000000001",
                auto_detect=True
            )
            self.assertTrue(res["connected"])
            self.assertEqual(res["persona_name"], "ViperPlayer")
            self.bridge._engine.steam_service.configure.assert_called_with(
                api_key="TEST_API_KEY_123456",
                steam_id64="76561198000000001"
            )

        # get_oauth_status includes steam
        oauth_status = self.bridge.get_oauth_status()
        self.assertIn("steam", oauth_status)
        self.assertTrue(oauth_status["steam"]["connected"])

        # Set friend polling
        poll_res = self.bridge.set_steam_friend_polling(enabled=True, interval_minutes=15)
        self.assertTrue(poll_res["poll_friends_status"])
        self.assertEqual(poll_res["poll_interval_minutes"], 15)

        # Disconnect
        disc_res = self.bridge.disconnect_steam()
        self.assertFalse(disc_res["connected"])
        self.assertFalse(disc_res["poll_friends_status"])
        self.bridge._engine.steam_service.configure.assert_called_with(api_key="", steam_id64="")


if __name__ == "__main__":
    unittest.main()

