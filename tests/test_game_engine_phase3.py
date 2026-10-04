import os
import json
import time
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from core.game_telemetry import EliteTelemetryWatcher
from core.game_manager import GameManager
from tools.dispatcher import ToolDispatcher
from tools.game_tools import register_game_tools
from core.gui_bridge import GUIBridge


class MockEngine:
    def __init__(self):
        self.dispatcher = ToolDispatcher()
        self.game_mgr = None


class TestGameEnginePhase3(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.watcher = EliteTelemetryWatcher(journal_dir=self.temp_dir)
        self.test_json = os.path.join(self.temp_dir, "test_profiles.json")
        self.mgr = GameManager(profiles_path=self.test_json)

    def tearDown(self):
        self.watcher.stop()
        self.mgr.close()
        for f in os.listdir(self.temp_dir):
            try:
                os.remove(os.path.join(self.temp_dir, f))
            except Exception:
                pass
        try:
            os.rmdir(self.temp_dir)
        except Exception:
            pass

    def test_parse_journal_events(self):
        # 1. Simulate FSDJump Event
        jump_ev = {
            "timestamp": "2026-10-03T21:00:00Z",
            "event": "FSDJump",
            "StarSystem": "Sol",
            "Body": "Earth",
            "Docked": False
        }
        self.watcher._parse_event_line(json.dumps(jump_ev))

        self.assertEqual(self.watcher.state["star_system"], "Sol")
        self.assertEqual(self.watcher.state["body"], "Earth")
        self.assertFalse(self.watcher.state["docked"])

        # 2. Simulate Docked Event
        dock_ev = {
            "timestamp": "2026-10-03T21:05:00Z",
            "event": "Docked",
            "StationName": "Li Qing Jao",
            "StarSystem": "Sol"
        }
        self.watcher._parse_event_line(json.dumps(dock_ev))

        self.assertTrue(self.watcher.state["docked"])
        self.assertEqual(self.watcher.state["station"], "Li Qing Jao")

        # 3. Verify Context String Output
        summary = self.watcher.get_summary_prompt_context()
        self.assertIn("Sol", summary)
        self.assertIn("Li Qing Jao", summary)

    def test_supercruise_and_ship_loadout_events(self):
        # Supercruise entry
        sc_entry = {"event": "SupercruiseEntry", "StarSystem": "Sol"}
        self.watcher._parse_event_line(json.dumps(sc_entry))
        self.assertTrue(self.watcher.state["supercruise"])

        # Supercruise exit near Moon
        sc_exit = {"event": "SupercruiseExit", "Body": "Moon"}
        self.watcher._parse_event_line(json.dumps(sc_exit))
        self.assertFalse(self.watcher.state["supercruise"])
        self.assertEqual(self.watcher.state["body"], "Moon")

        # Ship loadout and shields
        loadout = {"event": "Loadout", "Ship": "anaconda", "ShipName": "Nebula Wanderer", "HullHealth": 0.95}
        self.watcher._parse_event_line(json.dumps(loadout))
        self.assertEqual(self.watcher.state["ship_type"], "anaconda")
        self.assertEqual(self.watcher.state["ship_name"], "Nebula Wanderer")
        self.assertEqual(self.watcher.state["hull_health"], 0.95)

        shield = {"event": "ShieldState", "ShieldsUp": False}
        self.watcher._parse_event_line(json.dumps(shield))
        self.assertFalse(self.watcher.state["shields_up"])

        summary = self.watcher.get_summary_prompt_context()
        self.assertIn("Nebula Wanderer", summary)
        self.assertIn("OFFLINE", summary)
        self.assertIn("95%", summary)

    def test_journal_file_tail_and_rehydrate(self):
        # Create a journal file with initial events
        journal_file = os.path.join(self.temp_dir, "Journal.2026-10-04T080000.01.log")
        with open(journal_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"event": "FSDJump", "StarSystem": "Achenar", "Body": "Achenar 3", "Docked": False}) + "\n")
            f.write(json.dumps({"event": "Loadout", "Ship": "cutter", "ShipName": "Imperial Glory", "HullHealth": 1.0}) + "\n")

        # Start watcher thread
        self.watcher.start()
        time.sleep(0.3)

        self.assertEqual(self.watcher.state["star_system"], "Achenar")
        self.assertEqual(self.watcher.state["ship_type"], "cutter")

        # Append new real-time event to the file
        with open(journal_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "Docked", "StationName": "Dawes Hub", "StarSystem": "Achenar"}) + "\n")

        # Allow tail loop to read appended line
        time.sleep(0.8)
        self.assertTrue(self.watcher.state["docked"])
        self.assertEqual(self.watcher.state["station"], "Dawes Hub")

    def test_game_manager_context_injection(self):
        # Empty when no active profile
        self.assertEqual(self.mgr.get_active_game_context(), "")

        # Add profile
        self.mgr.add_game(
            "Elite Dangerous",
            process_name="EliteDangerous64.exe",
            keybinds={
                "deploy heat sink": {"key": "v", "modifiers": [], "description": "Deploy Heat Sink"},
                "toggle landing gear": {"key": "l", "modifiers": [], "description": "Toggle Landing Gear"}
            }
        )
        self.mgr.set_active_profile("elite_dangerous")
        self.mgr.update_game_scratchpad = lambda gid, text: self.mgr.get_profile(gid).update({"scratchpad_raw": text})
        self.mgr.get_profile("elite_dangerous")["scratchpad_raw"] = "Farm 50 Polonium in Shinrarta"

        # Wire mock watcher state
        mock_watcher = MagicMock()
        mock_watcher.get_summary_prompt_context.return_value = "[LIVE GAME TELEMETRY: Elite Dangerous]\n- Location: Shinrarta Dezhra | Status: Normal Space"
        self.mgr.ed_watcher = mock_watcher

        context = self.mgr.get_active_game_context()
        self.assertIn("=== ACTIVE GAME COMPANION: Elite Dangerous ===", context)
        self.assertIn("Farm 50 Polonium in Shinrarta", context)
        self.assertIn('deploy heat sink', context)
        self.assertIn('Shinrarta Dezhra', context)

    def test_game_tools_registration_and_execution(self):
        dispatcher = ToolDispatcher()
        self.mgr.add_game(
            "Elite Dangerous",
            keybinds={
                "deploy heat sink": {"key": "v", "modifiers": [], "description": "Deploy Heat Sink"}
            }
        )
        self.mgr.set_active_profile("elite_dangerous")

        tools = register_game_tools(dispatcher, self.mgr)
        self.assertIn("get_current_game_telemetry", dispatcher._custom_handlers)
        self.assertIn("trigger_game_action", dispatcher._custom_handlers)
        self.assertIn("lookup_inara_market", dispatcher._custom_handlers)

        # 1. Test telemetry tool
        telemetry = tools["get_current_game_telemetry"]()
        self.assertIsInstance(telemetry, dict)

        # 2. Test trigger action tool with mock scancode
        with patch("tools.os_controls._send_scancode_event") as mock_send:
            res = tools["trigger_game_action"]("deploy heat sink")
            self.assertEqual(res["status"], "executed")

        # 3. Test market lookup tool
        m_res = tools["lookup_inara_market"]("Tritium", "Sol")
        self.assertEqual(m_res["status"], "success")
        self.assertEqual(m_res["commodity"], "Tritium")
        self.assertEqual(m_res["reference_system"], "Sol")

    def test_gui_bridge_telemetry_method(self):
        bridge = GUIBridge(MockEngine())
        bridge.game_mgr = self.mgr
        self.mgr.add_game("Elite Dangerous")
        self.mgr.set_active_profile("elite_dangerous")

        # Without active telemetry events
        t_data = bridge.get_game_telemetry()
        self.assertIn("active", t_data)
        self.assertFalse(t_data["active"])

        # With mock watcher state
        mock_watcher = MagicMock()
        mock_watcher.state = {
            "game": "Elite Dangerous",
            "active": True,
            "star_system": "Sol",
            "station": "Li Qing Jao",
            "docked": True,
            "supercruise": False
        }
        self.mgr.ed_watcher = mock_watcher
        t_live = bridge.get_game_telemetry()
        self.assertTrue(t_live["active"])
        self.assertEqual(t_live["star_system"], "Sol")
        self.assertEqual(t_live["station"], "Li Qing Jao")


if __name__ == "__main__":
    unittest.main()
