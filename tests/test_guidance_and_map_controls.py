import unittest
from unittest.mock import patch, MagicMock
import os
import json
import tempfile
import shutil

from core.game_manager import GameManager
from core.game_engine import GameEngine
from core.game_nav import GameNavigator, UniversalGameNavigator
from tools.dispatcher import ToolDispatcher
from tools.game_tools import register_game_tools


class TestGuidanceAndMapControls(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.profiles_path = os.path.join(self.test_dir, "test_profiles.json")
        initial_data = {
            "active_profile": "crimson_desert",
            "profiles": {
                "crimson_desert": {
                    "display_name": "Crimson Desert",
                    "universe_name": "Pywel",
                    "process_name": "CrimsonDesert.exe",
                    "telemetry_type": "scratchpad_only",
                    "guidance_level": "tactical",
                    "map_controls": {
                        "preferred_input": "mouse_keyboard",
                        "pan_method": "left_drag",
                        "marker_method": "right_click",
                        "zoom_method": "mouse_wheel"
                    },
                    "scratchpad": {
                        "active_quests": ["Bear Saddle"],
                        "general_notes": []
                    },
                    "scratchpad_raw": "",
                    "copilot_log": [],
                    "keybinds": {
                        "open map": {"key": "m", "description": "Open map"}
                    }
                }
            }
        }
        with open(self.profiles_path, "w", encoding="utf-8") as f:
            json.dump(initial_data, f)
        self.manager = GameManager(profiles_path=self.profiles_path)
        self.engine = GameEngine(profiles_path=self.profiles_path)

    def tearDown(self):
        try:
            shutil.rmtree(self.test_dir)
        except Exception:
            pass

    # --- 1. GameManager Guidance Settings Tests ---

    def test_get_and_set_guidance_level(self):
        # Default should be tactical
        self.assertEqual(self.manager.get_guidance_level("crimson_desert"), "tactical")

        # Update to companion
        res = self.manager.set_guidance_level("crimson_desert", "companion")
        self.assertTrue(res["success"])
        self.assertEqual(res["guidance_level"], "companion")
        self.assertEqual(self.manager.get_guidance_level("crimson_desert"), "companion")

        # Update to walkthrough
        res = self.manager.set_guidance_level("crimson_desert", "walkthrough")
        self.assertTrue(res["success"])
        self.assertEqual(res["guidance_level"], "walkthrough")
        self.assertEqual(self.manager.get_guidance_level("crimson_desert"), "walkthrough")

        # Invalid guidance level rejected
        res = self.manager.set_guidance_level("crimson_desert", "invalid_level")
        self.assertFalse(res["success"])
        self.assertIn("error", res)
        # Should stay walkthrough
        self.assertEqual(self.manager.get_guidance_level("crimson_desert"), "walkthrough")

    def test_add_game_default_guidance_and_map_controls(self):
        res = self.manager.add_game(display_name="Elden Ring", process_name="eldenring.exe")
        self.assertTrue(res["success"])
        gid = res["game_id"]
        prof = self.manager.get_profile(gid)
        self.assertEqual(prof.get("guidance_level"), "tactical")
        self.assertIn("map_controls", prof)
        self.assertEqual(prof["map_controls"].get("pan_method"), "left_drag")
        self.assertEqual(prof["map_controls"].get("marker_method"), "right_click")

    def test_guidance_level_prompt_generation(self):
        self.manager.set_game_mode(True, user_explicit=True)

        # Tactical prompt test
        self.manager.set_guidance_level("crimson_desert", "tactical")
        ctx_tactical = self.manager.get_active_game_context()
        self.assertIn("=== GUIDANCE LEVEL: TACTICAL ===", ctx_tactical)
        self.assertIn("DYNAMIC BREAKTHROUGH OVERRIDE:", ctx_tactical)
        self.assertIn("TEMPORARILY ESCALATE YOUR ASSISTANCE TO WALKTHROUGH MODE", ctx_tactical)

        # Companion prompt test
        self.manager.set_guidance_level("crimson_desert", "companion")
        ctx_companion = self.manager.get_active_game_context()
        self.assertIn("=== GUIDANCE LEVEL: COMPANION ===", ctx_companion)
        self.assertIn("IMMERSIVE HINTS ONLY", ctx_companion)
        self.assertIn("DYNAMIC BREAKTHROUGH OVERRIDE:", ctx_companion)

        # Walkthrough prompt test
        self.manager.set_guidance_level("crimson_desert", "walkthrough")
        ctx_walkthrough = self.manager.get_active_game_context()
        self.assertIn("=== GUIDANCE LEVEL: WALKTHROUGH ===", ctx_walkthrough)
        self.assertIn("DIRECT STEP-BY-STEP SOLUTIONS", ctx_walkthrough)
        self.assertIn("CRITICAL NON-PROACTIVE RULE FOR PUZZLES & WALKTHROUGHS:", ctx_walkthrough)
        self.assertIn("DO NOT BE OVERZEALOUS OR PROACTIVE:", ctx_walkthrough)
        self.assertIn("ONLY WHEN THE PLAYER ASKS FOR HELP", ctx_walkthrough)

    # --- 2. Voice Pipeline Interception for Guidance Levels ---

    def test_voice_interception_guidance_commands(self):
        # 1. "set guidance level to walkthrough"
        res = self.engine.intercept_voice_turn("set guidance level to walkthrough")
        self.assertTrue(res.handled)
        self.assertEqual(res.action_type, "guidance_level_change")
        self.assertTrue(res.requires_context_reset)
        self.assertTrue(res.skip_llm)
        self.assertIn("Walkthrough", res.user_message)
        self.assertEqual(self.engine.manager.get_guidance_level("crimson_desert"), "walkthrough")

        # 2. "switch guidance to companion"
        res = self.engine.intercept_voice_turn("Aether, switch guidance to companion")
        self.assertTrue(res.handled)
        self.assertEqual(res.action_type, "guidance_level_change")
        self.assertEqual(self.engine.manager.get_guidance_level("crimson_desert"), "companion")

        # 3. "guidance level tactical"
        res = self.engine.intercept_voice_turn("guidance level tactical")
        self.assertTrue(res.handled)
        self.assertEqual(res.action_type, "guidance_level_change")
        self.assertEqual(self.engine.manager.get_guidance_level("crimson_desert"), "tactical")

    # --- 3. Core Tool Names and Registration ---

    def test_core_game_tool_names_contain_granular_tools(self):
        names = self.engine.CORE_GAME_TOOL_NAMES
        self.assertIn("pan_game_map", names)
        self.assertIn("place_map_marker", names)
        self.assertIn("center_map_on_player", names)
        self.assertIn("pan_and_mark_map_location", names)
        self.assertIn("assist_game_navigation", names)

    def test_register_game_tools_includes_all_navigation_tools(self):
        dispatcher = ToolDispatcher()
        tools = register_game_tools(dispatcher, self.manager)
        self.assertIn("pan_game_map", tools)
        self.assertIn("place_map_marker", tools)
        self.assertIn("center_map_on_player", tools)
        self.assertIn("pan_and_mark_map_location", tools)
        self.assertIn("assist_game_navigation", tools)

    # --- 4. GameNavigator Map Controls & Left-Click Drag Tests ---

    @patch("core.game_nav.drag_mouse_relative")
    def test_pan_map_with_left_drag_control(self, mock_drag):
        map_controls = {
            "preferred_input": "mouse_keyboard",
            "pan_method": "left_drag"
        }
        nav = GameNavigator(map_controls=map_controls)
        # Pan north should drag with button="left"
        nav.pan_map("north", distance=300)
        mock_drag.assert_called_with(0, 300, button="left", steps=14)

        # Pan east should drag with button="left"
        nav.pan_map("east", distance=250)
        mock_drag.assert_called_with(-250, 0, button="left", steps=14)

    @patch("tools.os_controls.send_gamepad_stick")
    def test_pan_map_with_gamepad_stick(self, mock_stick):
        mock_stick.return_value = True
        map_controls = {
            "preferred_input": "gamepad"
        }
        nav = GameNavigator(map_controls=map_controls)
        nav.pan_map("north")
        mock_stick.assert_called_with("right", 0.0, 1.0, duration_sec=0.35)

        nav.pan_map("east")
        mock_stick.assert_called_with("right", 1.0, 0.0, duration_sec=0.35)

    @patch("core.game_nav.click_mouse_button")
    @patch("core.game_nav.move_mouse_absolute")
    @patch.object(GameNavigator, "verify_marker_placement", return_value={"accurate": True, "correction_needed": False})
    @patch.object(GameNavigator, "capture_screen", return_value=(b"fake_jpeg", 1920, 1080))
    @patch.object(GameNavigator, "check_is_map_explicit", return_value=True)
    def test_pan_and_place_marker_uses_map_controls(self, mock_is_map, mock_cap, mock_verify, mock_move, mock_click):
        map_controls = {
            "preferred_input": "mouse_keyboard",
            "pan_method": "left_drag",
            "marker_method": "right_click"
        }
        nav = GameNavigator(map_controls=map_controls)
        with patch.object(nav, "locate_landmark_point", return_value=(800, 600)):
            res = nav.pan_and_place_marker("Wolf Mountain Ruins")
            self.assertEqual(res["status"], "success")
            mock_click.assert_called_with("right", hold_duration=0.08)

    # --- 5. Granular Tools Execution Tests ---

    @patch("core.game_nav.drag_mouse_relative")
    def test_pan_game_map_tool_execution(self, mock_drag):
        dispatcher = ToolDispatcher()
        tools = register_game_tools(dispatcher, self.manager)
        res = tools["pan_game_map"](direction="south", distance=200)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["direction"], "south")
        mock_drag.assert_called_with(0, -200, button="left", steps=14)

    @patch("tools.os_controls.click_mouse_button")
    @patch("tools.os_controls.move_mouse_absolute")
    def test_place_map_marker_tool_execution(self, mock_move, mock_click):
        dispatcher = ToolDispatcher()
        tools = register_game_tools(dispatcher, self.manager)
        res = tools["place_map_marker"](x=500, y=400)
        self.assertEqual(res["status"], "success")
        mock_move.assert_called_with(500, 400)
        mock_click.assert_called_with("right", hold_duration=0.08)

    @patch("tools.os_controls.pulse_key")
    def test_center_map_on_player_tool_execution(self, mock_pulse):
        dispatcher = ToolDispatcher()
        tools = register_game_tools(dispatcher, self.manager)
        res = tools["center_map_on_player"]()
        self.assertEqual(res["status"], "success")
        mock_pulse.assert_called_with("r")

    # --- 6. Voice Accent Preservation in Game Mode ---

    def test_voice_accent_preserved_in_game_mode(self):
        from core.engine import AetherEngine
        fake_cfg = {
            "api": {
                "voice_accent": "British",
                "voice_name": "Sulafat",
                "system_instruction": "You are Aether."
            }
        }
        engine_instance = AetherEngine(config_getter=lambda: fake_cfg)
        engine_instance.game_engine = self.engine
        self.engine.manager.set_game_mode(True, user_explicit=True)

        system_instruction = engine_instance._build_system_instruction()
        self.assertIn("SPOKEN VOICE ACCENT & DIALECT DIRECTIVE:", system_instruction)
        self.assertIn("British accent", system_instruction)
        self.assertIn("MAXIMUM OVERRIDE: IN-CHARACTER GAME MODE ENGAGED", system_instruction)

    # --- 7. Map Navigation Mode Tests ---

    def test_navigation_mode_get_and_set(self):
        # Default should fall back to controller
        self.assertEqual(self.manager.get_navigation_mode("crimson_desert"), "controller")

        # Set to co_pilot_spotter
        res = self.manager.set_navigation_mode("crimson_desert", "co_pilot_spotter")
        self.assertTrue(res["success"])
        self.assertEqual(res["navigation_mode"], "co_pilot_spotter")
        self.assertEqual(self.manager.get_navigation_mode("crimson_desert"), "co_pilot_spotter")

        # Set to search_bar
        res = self.manager.set_navigation_mode("crimson_desert", "search_bar")
        self.assertTrue(res["success"])
        self.assertEqual(res["navigation_mode"], "search_bar")

        # Set to mouse_keyboard
        res = self.manager.set_navigation_mode("crimson_desert", "mouse_keyboard")
        self.assertTrue(res["success"])
        self.assertEqual(res["navigation_mode"], "mouse_keyboard")
        prof = self.manager.get_profile("crimson_desert")
        self.assertEqual(prof["map_controls"]["preferred_input"], "mouse_keyboard")

        # Set to controller
        res = self.manager.set_navigation_mode("crimson_desert", "controller")
        self.assertTrue(res["success"])
        self.assertEqual(res["navigation_mode"], "controller")
        prof = self.manager.get_profile("crimson_desert")
        self.assertEqual(prof["map_controls"]["preferred_input"], "gamepad")

        # Invalid mode rejected
        res = self.manager.set_navigation_mode("crimson_desert", "teleport")
        self.assertFalse(res["success"])
        self.assertIn("error", res)

    def test_navigation_mode_prompt_generation(self):
        self.manager.set_game_mode(True, user_explicit=True)

        # Controller mode prompt
        self.manager.set_navigation_mode("crimson_desert", "controller")
        ctx = self.manager.get_active_game_context()
        self.assertIn("=== MAP NAVIGATION PROTOCOL: CONTROLLER ===", ctx)
        self.assertIn("VIRTUAL CONTROLLER (ViGEmBus)", ctx)

        # Spotter mode prompt
        self.manager.set_navigation_mode("crimson_desert", "co_pilot_spotter")
        ctx = self.manager.get_active_game_context()
        self.assertIn("=== MAP NAVIGATION PROTOCOL: CO_PILOT_SPOTTER ===", ctx)
        self.assertIn("CO-PILOT GUIDED SPOTTER (VOICE & VISION)", ctx)

        # Search bar mode prompt
        self.manager.set_navigation_mode("crimson_desert", "search_bar")
        ctx = self.manager.get_active_game_context()
        self.assertIn("=== MAP NAVIGATION PROTOCOL: SEARCH_BAR ===", ctx)
        self.assertIn("SEARCH BAR & KEYBOARD ROUTING", ctx)

    def test_voice_interception_map_navigation_mode(self):
        # 1. Spotter
        res = self.engine.intercept_voice_turn("set map navigation to spotter")
        self.assertTrue(res.handled)
        self.assertEqual(res.action_type, "navigation_mode_change")
        self.assertEqual(res.details["navigation_mode"], "co_pilot_spotter")
        self.assertIn("Co-Pilot Guided Spotter", res.user_message)

        # 2. Search bar
        res = self.engine.intercept_voice_turn("switch navigation mode to search bar")
        self.assertTrue(res.handled)
        self.assertEqual(res.action_type, "navigation_mode_change")
        self.assertEqual(res.details["navigation_mode"], "search_bar")

        # 3. Mouse & Keyboard
        res = self.engine.intercept_voice_turn("change map navigation to mouse and keyboard")
        self.assertTrue(res.handled)
        self.assertEqual(res.action_type, "navigation_mode_change")
        self.assertEqual(res.details["navigation_mode"], "mouse_keyboard")

        # 4. Controller
        res = self.engine.intercept_voice_turn("map navigation mode controller")
        self.assertTrue(res.handled)
        self.assertEqual(res.action_type, "navigation_mode_change")
        self.assertEqual(res.details["navigation_mode"], "controller")

    def test_spot_and_guide_map_landmark_tool(self):
        dispatcher = ToolDispatcher()
        tools = register_game_tools(dispatcher, self.manager)
        self.assertIn("spot_and_guide_map_landmark", tools)

        with patch("core.game_nav.UniversalGameNavigator.spot_and_guide_map_location") as mock_spot:
            mock_spot.return_value = {
                "status": "success",
                "found": True,
                "relative_location": "northeast ridge",
                "spoken_guidance": "Pan your map northeast toward Wolf Mountain."
            }
            res = tools["spot_and_guide_map_landmark"](landmark="Wolf Mountain")
            self.assertEqual(res["status"], "success")
            self.assertIn("Wolf Mountain", res["spoken_guidance"])
            mock_spot.assert_called_with(landmark_description="Wolf Mountain")

    def test_search_and_route_destination_tool(self):
        dispatcher = ToolDispatcher()
        tools = register_game_tools(dispatcher, self.manager)
        self.assertIn("search_and_route_destination", tools)

        with patch("core.game_nav.UniversalGameNavigator.search_and_route_destination") as mock_route:
            mock_route.return_value = {
                "status": "success",
                "destination": "Sol",
                "message": "Entered destination 'Sol' into navigation search and plotted route."
            }
            res = tools["search_and_route_destination"](destination_name="Sol")
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["destination"], "Sol")
            mock_route.assert_called_with(destination_name="Sol")

    def test_pan_and_mark_delegates_by_navigation_mode(self):
        dispatcher = ToolDispatcher()
        tools = register_game_tools(dispatcher, self.manager)

        # 1. In co_pilot_spotter mode
        self.manager.set_navigation_mode("crimson_desert", "co_pilot_spotter")
        with patch("core.game_nav.GameNavigator.spot_and_guide_map_location") as mock_spot:
            mock_spot.return_value = {"status": "success", "spoken_guidance": "Look east."}
            res = tools["pan_and_mark_map_location"](landmark="Oasis")
            self.assertEqual(res["status"], "success")
            mock_spot.assert_called_once()

        # 2. In search_bar mode
        self.manager.set_navigation_mode("crimson_desert", "search_bar")
        with patch("core.game_nav.GameNavigator.search_and_route_destination") as mock_search:
            mock_search.return_value = {"status": "success", "destination": "Shinrarta Dezhra"}
            res = tools["pan_and_mark_map_location"](landmark="Shinrarta Dezhra")
            self.assertEqual(res["status"], "success")
            mock_search.assert_called_once()

    def test_gui_bridge_navigation_mode(self):
        from core.gui_bridge import GUIBridge
        bridge = GUIBridge(engine=None)
        bridge.game_engine = self.engine

        get_res = bridge.get_navigation_mode("crimson_desert")
        self.assertTrue(get_res["success"])
        self.assertIn("navigation_mode", get_res)

        set_res = bridge.set_navigation_mode("crimson_desert", "co_pilot_spotter")
        self.assertTrue(set_res["success"])
        self.assertEqual(set_res["navigation_mode"], "co_pilot_spotter")
        self.assertEqual(bridge.get_navigation_mode("crimson_desert")["navigation_mode"], "co_pilot_spotter")


if __name__ == "__main__":
    unittest.main()

