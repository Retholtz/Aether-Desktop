import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from core.game_manager import GameManager, DEFAULT_PROFILES
from tools.os_controls import SCANCODE_MAP, send_directinput_key, send_directinput_combo


class TestGameEnginePhase1(unittest.TestCase):
    def setUp(self):
        self.test_json = "data/test_games_profiles.json"
        if os.path.exists(self.test_json):
            try:
                os.remove(self.test_json)
            except OSError:
                pass
        self.mgr = GameManager(profiles_path=self.test_json)

    def tearDown(self):
        if os.path.exists(self.test_json):
            try:
                os.remove(self.test_json)
            except OSError:
                pass

    def test_scancode_dictionary_coverage(self):
        # Verify critical gaming keys exist in mapping
        self.assertIn("w", SCANCODE_MAP)
        self.assertIn("i", SCANCODE_MAP)
        self.assertIn("h", SCANCODE_MAP)
        self.assertIn("delete", SCANCODE_MAP)
        self.assertIn("shift", SCANCODE_MAP)

    def test_profile_initialization_defaults_empty(self):
        # Verify default profile is now an empty game list
        self.assertEqual(self.mgr.data.get("profiles"), {})
        self.assertEqual(self.mgr.data.get("active_profile"), "")
        self.assertIsNone(self.mgr.get_active_profile())

    def test_add_and_delete_game(self):
        # Add a game
        res = self.mgr.add_game(
            "Star Citizen",
            process_name="StarCitizen.exe",
            keybinds={"quantum drive": {"key": "b", "modifiers": [], "description": "Engage QT"}}
        )
        self.assertTrue(res["success"])
        gid = res["game_id"]
        self.assertEqual(gid, "star_citizen")
        self.assertEqual(self.mgr.data["active_profile"], "star_citizen")
        self.assertIn("star_citizen", self.mgr.data["profiles"])

        # Add a second game
        res2 = self.mgr.add_game("Cyberpunk 2077", process_name="Cyberpunk2077.exe")
        self.assertTrue(res2["success"])
        self.assertIn("cyberpunk_2077", self.mgr.data["profiles"])
        # First added game remains active
        self.assertEqual(self.mgr.data["active_profile"], "star_citizen")

        # Delete active game -> switches to remaining game
        del_res = self.mgr.delete_game("star_citizen")
        self.assertTrue(del_res["success"])
        self.assertEqual(self.mgr.data["active_profile"], "cyberpunk_2077")
        self.assertNotIn("star_citizen", self.mgr.data["profiles"])

        # Delete last remaining game -> active becomes empty
        del_res2 = self.mgr.delete_game("cyberpunk_2077")
        self.assertTrue(del_res2["success"])
        self.assertEqual(self.mgr.data["active_profile"], "")
        self.assertEqual(len(self.mgr.data["profiles"]), 0)

    def test_active_profile_switching(self):
        self.mgr.add_game("Game One")
        self.mgr.add_game("Game Two")
        self.assertEqual(self.mgr.data.get("active_profile"), "game_one")

        res = self.mgr.set_active_profile("game_two")
        self.assertTrue(res)
        self.assertEqual(self.mgr.data.get("active_profile"), "game_two")

        active_prof = self.mgr.get_active_profile()
        self.assertIsNotNone(active_prof)
        self.assertEqual(active_prof["display_name"], "Game Two")

        # Invalid profile id
        self.assertFalse(self.mgr.set_active_profile("non_existent_game"))

    def test_trigger_action_dispatch(self):
        self.mgr.add_game(
            "Game One",
            keybinds={
                "open inventory": {"key": "i", "modifiers": [], "description": "Open Inventory"},
                "silent running": {"key": "delete", "modifiers": ["shift"], "description": "Toggle Silent Running"}
            }
        )

        with patch("tools.os_controls._send_scancode_event") as mock_send:
            res = self.mgr.trigger_action("open inventory")
            self.assertEqual(res["status"], "executed")
            self.assertEqual(res["game"], "Game One")
            self.assertEqual(res["action"], "Open Inventory")
            self.assertTrue(mock_send.called)

        # Test unknown phrase
        res_ignored = self.mgr.trigger_action("unknown gesture")
        self.assertEqual(res_ignored["status"], "ignored")

        # Test combo trigger
        with patch("tools.os_controls._send_scancode_event") as mock_send:
            res_combo = self.mgr.trigger_action("silent running")
            self.assertEqual(res_combo["status"], "executed")
            self.assertEqual(res_combo["game"], "Game One")
            self.assertTrue(mock_send.called)

    def test_custom_keybind_and_scratchpad(self):
        self.mgr.add_game("Game One")
        added = self.mgr.set_keybind(
            "game_one",
            phrase="dodge roll",
            key="space",
            modifiers=["alt"],
            description="Quick evasive roll"
        )
        self.assertTrue(added)
        g1 = self.mgr.get_profile("game_one")
        self.assertIn("dodge roll", g1["keybinds"])
        self.assertEqual(g1["keybinds"]["dodge roll"]["key"], "space")
        self.assertEqual(g1["keybinds"]["dodge roll"]["modifiers"], ["alt"])

        # Test scratchpad update
        sc_res = self.mgr.update_scratchpad("game_one", "active_quests", "Defeat the Mountain Beast")
        self.assertTrue(sc_res)
        self.assertIn("Defeat the Mountain Beast", g1["scratchpad"]["active_quests"])

    def test_elite_dangerous_binds_xml_parser(self):
        temp_dir = tempfile.mkdtemp(prefix="ed_binds_test_")
        try:
            binds_file = os.path.join(temp_dir, "Custom.4.0.binds")
            xml_sample = """<?xml version="1.0" encoding="UTF-8"?>
<Root PresetName="Custom">
    <DeployHeatSink>
        <Primary Device="Keyboard" Key="Key_V" />
        <Secondary Device="{NoDevice}" Key="" />
    </DeployHeatSink>
    <ToggleFlightAssist>
        <Primary Device="Keyboard" Key="Key_Z" />
        <Secondary Device="{NoDevice}" Key="" />
    </ToggleFlightAssist>
    <ChargeECM>
        <Primary Device="Keyboard" Key="Key_C">
            <Modifier Device="Keyboard" Key="Key_LeftShift" />
        </Primary>
        <Secondary Device="{NoDevice}" Key="" />
    </ChargeECM>
    <IncreaseEnginesPower>
        <Primary Device="Keyboard" Key="Key_Up" />
        <Secondary Device="{NoDevice}" Key="" />
    </IncreaseEnginesPower>
</Root>
"""
            with open(binds_file, "w", encoding="utf-8") as f:
                f.write(xml_sample)

            self.mgr.add_game("Elite Dangerous", bindings_dir=temp_dir)
            discovered = self.mgr.scan_elite_dangerous_binds()

            self.assertIn("deploy heat sink", discovered)
            self.assertEqual(discovered["deploy heat sink"]["key"], "v")

            self.assertIn("charge ecm", discovered)
            self.assertEqual(discovered["charge ecm"]["key"], "c")
            self.assertEqual(discovered["charge ecm"]["modifiers"], ["leftshift"])

            self.assertIn("divert power to engines", discovered)
            self.assertEqual(discovered["divert power to engines"]["key"], "up")

            # Test sync_game_binds
            sync_res = self.mgr.sync_game_binds("elite_dangerous")
            self.assertTrue(sync_res["success"])
            self.assertGreaterEqual(sync_res["count"], 4)

            # Check that the keybinds in profile contain auto_detected entries
            ed_prof = self.mgr.get_profile("elite_dangerous")
            self.assertEqual(ed_prof["keybinds"]["charge ecm"]["source"], "auto_detected")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_directinput_key_validation(self):
        # Unknown key returns False
        res = send_directinput_key("nonexistent_key_123")
        self.assertFalse(res)

        # Valid keys return True
        with patch("tools.os_controls._send_scancode_event") as mock_send:
            res_key = send_directinput_key("w", duration_sec=0.001)
            self.assertTrue(res_key)
            self.assertTrue(mock_send.called)

            mock_send.reset_mock()
            res_combo = send_directinput_combo(["shift"], "delete", hold_duration=0.001)
            self.assertTrue(res_combo)
            self.assertTrue(mock_send.called)


if __name__ == "__main__":
    unittest.main()
