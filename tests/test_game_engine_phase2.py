import os
import unittest
from unittest.mock import patch

from core.game_manager import GameManager
from core.gui_bridge import GUIBridge


class MockEngine:
    pass


class TestGameEnginePhase2(unittest.TestCase):
    def setUp(self):
        self.test_json = "data/test_games_profiles_phase2.json"
        if os.path.exists(self.test_json):
            try:
                os.remove(self.test_json)
            except OSError:
                pass
        self.bridge = GUIBridge(MockEngine())
        self.bridge.game_mgr = GameManager(profiles_path=self.test_json)

    def tearDown(self):
        if os.path.exists(self.test_json):
            try:
                os.remove(self.test_json)
            except OSError:
                pass

    def test_add_and_delete_game_via_bridge(self):
        # Starts empty
        initial = self.bridge.get_game_profiles()
        self.assertEqual(initial["profiles"], {})
        self.assertEqual(initial["active_profile"], "")

        # Add game
        add_res = self.bridge.add_game_profile("Star Citizen", "StarCitizen.exe")
        self.assertTrue(add_res["success"])
        self.assertEqual(add_res["game_id"], "star_citizen")

        profiles_data = self.bridge.get_game_profiles()
        self.assertIn("star_citizen", profiles_data["profiles"])
        self.assertEqual(profiles_data["active_profile"], "star_citizen")

        # Delete game
        del_res = self.bridge.delete_game_profile("star_citizen")
        self.assertTrue(del_res["success"])
        after_del = self.bridge.get_game_profiles()
        self.assertEqual(after_del["profiles"], {})
        self.assertEqual(after_del["active_profile"], "")

    def test_switch_profile_and_scratchpad(self):
        self.bridge.add_game_profile("Game Alpha")
        self.bridge.add_game_profile("Game Beta")

        # 1. Switch profile
        res = self.bridge.set_active_game_profile("game_beta")
        self.assertTrue(res["success"])
        self.assertEqual(self.bridge.game_mgr.data["active_profile"], "game_beta")

        # 2. Update scratchpad
        note = "Current Goal: Collect 25 Encoded Data Firmware"
        res_note = self.bridge.update_game_scratchpad("game_beta", note)
        self.assertTrue(res_note["success"])
        self.assertEqual(self.bridge.game_mgr.get_profile("game_beta")["scratchpad_raw"], note)

    def test_add_and_delete_keybind(self):
        self.bridge.add_game_profile("Action RPG")

        # Add custom keybind
        res = self.bridge.save_game_keybind("action_rpg", "cast fireball", "f", ["shift"], "Fireball spell")
        self.assertTrue(res["success"])
        self.assertIn("cast fireball", res["keybinds"])
        self.assertEqual(res["keybinds"]["cast fireball"]["key"], "f")

        # Delete keybind
        del_res = self.bridge.delete_game_keybind("action_rpg", "cast fireball")
        self.assertTrue(del_res["success"])
        self.assertNotIn("cast fireball", del_res["keybinds"])

    def test_get_game_profiles_structure(self):
        self.bridge.add_game_profile("Sim Space", "SimSpace.exe")
        profiles_info = self.bridge.get_game_profiles()
        self.assertIn("active_profile", profiles_info)
        self.assertIn("profiles", profiles_info)
        self.assertIn("running_status", profiles_info)
        self.assertIn("sim_space", profiles_info["profiles"])
        self.assertIsInstance(profiles_info["running_status"], dict)

    def test_test_game_macro(self):
        self.bridge.add_game_profile(
            "Sandbox Game",
            ""
        )
        self.bridge.save_game_keybind("sandbox_game", "open inventory", "i", [], "Open Inventory")

        with patch("tools.os_controls._send_scancode_event") as mock_send:
            res = self.bridge.test_game_macro("open inventory")
            self.assertEqual(res["status"], "executed")
            self.assertEqual(res["game"], "Sandbox Game")
            self.assertTrue(mock_send.called)

    def test_invalid_profile_handling(self):
        res_switch = self.bridge.set_active_game_profile("invalid_game_xyz")
        self.assertFalse(res_switch["success"])
        self.assertIn("error", res_switch)

        res_save = self.bridge.save_game_keybind("invalid_game_xyz", "action", "x", [], "")
        self.assertFalse(res_save["success"])
        self.assertIn("error", res_save)

        res_del = self.bridge.delete_game_keybind("invalid_game_xyz", "action")
        self.assertFalse(res_del["success"])

        res_pad = self.bridge.update_game_scratchpad("invalid_game_xyz", "test note")
        self.assertFalse(res_pad["success"])

        res_add_empty = self.bridge.add_game_profile("   ")
        self.assertFalse(res_add_empty["success"])


if __name__ == "__main__":
    unittest.main()
