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

    def test_profile_initialization_defaults(self):
        ed = self.mgr.get_profile("elite_dangerous")
        cd = self.mgr.get_profile("crimson_desert")
        self.assertIsNotNone(ed)
        self.assertIsNotNone(cd)
        self.assertIn("open inventory", cd["keybinds"])
        self.assertEqual(cd["keybinds"]["open inventory"]["key"], "i")

    def test_active_profile_switching(self):
        self.assertEqual(self.mgr.data.get("active_profile"), "crimson_desert")
        res = self.mgr.set_active_profile("elite_dangerous")
        self.assertTrue(res)
        self.assertEqual(self.mgr.data.get("active_profile"), "elite_dangerous")

        active_prof = self.mgr.get_active_profile()
        self.assertIsNotNone(active_prof)
        self.assertEqual(active_prof["display_name"], "Elite Dangerous")

        # Invalid profile id
        self.assertFalse(self.mgr.set_active_profile("non_existent_game"))

    def test_trigger_action_dispatch(self):
        # Crimson desert active: test open inventory
        with patch("tools.os_controls._send_scancode_event") as mock_send:
            res = self.mgr.trigger_action("open inventory")
            self.assertEqual(res["status"], "executed")
            self.assertEqual(res["game"], "Crimson Desert")
            self.assertEqual(res["action"], "Open Inventory")
            self.assertTrue(mock_send.called)

        # Crimson desert active: test unknown phrase
        res_ignored = self.mgr.trigger_action("unknown gesture")
        self.assertEqual(res_ignored["status"], "ignored")

        # Switch to elite dangerous and test combo trigger
        self.mgr.set_active_profile("elite_dangerous")
        with patch("tools.os_controls._send_scancode_event") as mock_send:
            res_combo = self.mgr.trigger_action("silent running")
            self.assertEqual(res_combo["status"], "executed")
            self.assertEqual(res_combo["game"], "Elite Dangerous")
            self.assertTrue(mock_send.called)

    def test_custom_keybind_and_scratchpad(self):
        added = self.mgr.set_keybind(
            "crimson_desert",
            phrase="dodge roll",
            key="space",
            modifiers=["alt"],
            description="Quick evasive roll"
        )
        self.assertTrue(added)
        cd = self.mgr.get_profile("crimson_desert")
        self.assertIn("dodge roll", cd["keybinds"])
        self.assertEqual(cd["keybinds"]["dodge roll"]["key"], "space")
        self.assertEqual(cd["keybinds"]["dodge roll"]["modifiers"], ["alt"])

        # Test scratchpad update
        sc_res = self.mgr.update_scratchpad("crimson_desert", "active_quests", "Defeat the Mountain Beast")
        self.assertTrue(sc_res)
        self.assertIn("Defeat the Mountain Beast", cd["scratchpad"]["active_quests"])

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

            self.mgr.data["profiles"]["elite_dangerous"]["bindings_dir"] = temp_dir
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
