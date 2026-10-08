import os
import unittest
from unittest.mock import MagicMock, patch

from core.game_engine import GameEngine, GameInterceptionResult


class TestGameEngine(unittest.TestCase):
    def setUp(self):
        self.test_profiles_path = "data/test_game_engine_profiles.json"
        if os.path.exists(self.test_profiles_path):
            try:
                os.remove(self.test_profiles_path)
            except OSError:
                pass
        self.engine = GameEngine(profiles_path=self.test_profiles_path)

    def tearDown(self):
        self.engine.stop()
        if os.path.exists(self.test_profiles_path):
            try:
                os.remove(self.test_profiles_path)
            except OSError:
                pass

    def test_mode_toggle_voice_interception(self):
        # Enable command
        res = self.engine.intercept_voice_turn("Aether, enable game mode")
        self.assertTrue(res.handled)
        self.assertEqual(res.action_type, "mode_toggle")
        self.assertTrue(self.engine.game_mode_enabled)
        self.assertTrue(res.skip_llm)
        self.assertIsNotNone(res.tts_message)

        # Disable command
        res2 = self.engine.intercept_voice_turn("turn off game mode")
        self.assertTrue(res2.handled)
        self.assertEqual(res2.action_type, "mode_toggle")
        self.assertFalse(self.engine.game_mode_enabled)

    def test_tool_filtering_universal_fencing(self):
        declarations = [
            {"name": "trigger_game_action"},
            {"name": "inspect_screen_context"},
            {"name": "search_past_sessions"},
            {"name": "google_search"}
        ]
        filtered = self.engine.filter_tools(declarations)
        names = {d["name"] for d in filtered}
        self.assertIn("trigger_game_action", names)
        self.assertIn("inspect_screen_context", names)
        self.assertNotIn("search_past_sessions", names)
        self.assertNotIn("google_search", names)

    def test_tool_filtering_with_extra_tools(self):
        # Add profile with extra tools
        self.engine.manager.add_game(
            "Custom Sim",
            process_name="CustomSim.exe"
        )
        active_id = self.engine.manager.data.get("active_profile")
        prof = self.engine.manager.get_profile(active_id)
        prof["extra_tools"] = ["custom_sim_tool"]

        declarations = [
            {"name": "trigger_game_action"},
            {"name": "custom_sim_tool"},
            {"name": "unrelated_tool"}
        ]
        filtered = self.engine.filter_tools(declarations)
        names = {d["name"] for d in filtered}
        self.assertIn("trigger_game_action", names)
        self.assertIn("custom_sim_tool", names)
        self.assertNotIn("unrelated_tool", names)

    def test_immersion_prompt_generation(self):
        self.engine.set_game_mode(True, user_explicit=True)
        self.engine.manager.add_game("Starfield", process_name="Starfield.exe")
        self.engine.manager.is_game_mode_active = MagicMock(return_value=True)

        instruction = self.engine.build_system_instruction(hotkey_display="Ctrl+Shift+G")
        self.assertIsNotNone(instruction)
        self.assertIn("IN-CHARACTER GAME MODE ENGAGED", instruction)
        self.assertIn("STARFIELD", instruction)
        self.assertIn("Ctrl+Shift+G", instruction)

    def test_immersion_dynamic_fallback_for_generic_games(self):
        self.engine.set_game_mode(True, user_explicit=True)
        # Profile without explicit display_name or universe_name
        self.engine.manager.data["profiles"]["indie_game"] = {
            "display_name": "",
            "process_name": "indie_dungeon.exe",
            "keybinds": {}
        }
        self.engine.manager.data["active_profile"] = "indie_game"
        self.engine.manager.is_game_mode_active = MagicMock(return_value=True)

        instruction = self.engine.build_system_instruction()
        self.assertIsNotNone(instruction)
        self.assertIn("Indie Dungeon", instruction)
        self.assertIn("DYNAMIC GROUNDING", instruction)

    def test_fast_path_macro_voice_interception(self):
        self.engine.manager.add_game(
            "Flight Sim",
            keybinds={"gear toggle": {"key": "g", "modifiers": [], "description": "Landing Gear"}}
        )
        with patch.object(self.engine.manager, "trigger_action", return_value={"status": "executed", "action": "gear toggle"}):
            res = self.engine.intercept_voice_turn("gear toggle")
            self.assertTrue(res.handled)
            self.assertEqual(res.action_type, "fast_path_macro")
            self.assertTrue(res.skip_llm)
            self.assertIn("Executed: gear toggle", res.user_message)

    def test_hotkey_toggle(self):
        self.assertFalse(self.engine.game_mode_enabled)
        new_state, msg = self.engine.handle_hotkey_toggle()
        self.assertTrue(new_state)
        self.assertTrue(self.engine.game_mode_enabled)
        self.assertIn("Game Mode active", msg)

        new_state2, msg2 = self.engine.handle_hotkey_toggle()
        self.assertFalse(new_state2)
        self.assertFalse(self.engine.game_mode_enabled)
        self.assertIn("Switched to open conversation", msg2)

    def test_backward_compatibility_alias(self):
        self.assertIs(self.engine.game_mgr, self.engine.manager)
        self.assertEqual(self.engine.data, self.engine.manager.data)


if __name__ == "__main__":
    unittest.main()

