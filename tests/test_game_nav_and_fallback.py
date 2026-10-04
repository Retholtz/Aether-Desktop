"""
Aether Desktop - Test Suite for Visual Map Drag/Pan & Online Keybind Fallback Ingestion
Verifies low-level mouse dragging, GameNavigator visual grounding and waypoint placement,
and GameManager web reference keybind fallback ingestion.
"""

import os
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from tools.os_controls import drag_mouse_relative, move_mouse_absolute, click_mouse_button
from core.game_nav import GameNavigator
from core.game_manager import GameManager
from tools.dispatcher import ToolDispatcher
from tools.game_tools import register_game_tools
from core.gui_bridge import GUIBridge


class MockClient:
    def __init__(self, response_text: str):
        self.models = MagicMock()
        mock_res = MagicMock()
        mock_res.text = response_text
        self.models.generate_content.return_value = mock_res


class TestGameNavAndFallback(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.test_json = os.path.join(self.temp_dir, "test_profiles_nav.json")
        self.mgr = GameManager(profiles_path=self.test_json)

    def tearDown(self):
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

    # --- 1. Mouse Dragging & OS Controls Tests ---

    @patch("ctypes.windll.user32.SendInput")
    def test_drag_mouse_relative(self, mock_send):
        drag_mouse_relative(100, -200, button="right", steps=5, step_delay=0.001)
        # down (1) + 5 steps + up (1) = 7 SendInput calls
        self.assertEqual(mock_send.call_count, 7)

    @patch("ctypes.windll.user32.SetCursorPos")
    def test_move_mouse_absolute(self, mock_set_pos):
        mock_set_pos.return_value = 1
        res = move_mouse_absolute(500, 300)
        self.assertTrue(res)
        mock_set_pos.assert_called_with(500, 300)

    @patch("ctypes.windll.user32.SendInput")
    def test_click_mouse_button(self, mock_send):
        res = click_mouse_button("right", hold_duration=0.005)
        self.assertTrue(res)
        # down (1) + up (1) = 2 SendInput calls
        self.assertEqual(mock_send.call_count, 2)

    # --- 2. GameNavigator Visual Map Panning & Marker Tests ---

    def test_pan_map_deltas(self):
        nav = GameNavigator()
        with patch("core.game_nav.drag_mouse_relative") as mock_drag:
            # North pan (dx=0, dy=+350)
            nav.pan_map("north", distance=350, button="right")
            mock_drag.assert_called_with(0, 350, button="right", steps=14)

            # East pan (dx=-350, dy=0)
            nav.pan_map("east", distance=350, button="right")
            mock_drag.assert_called_with(-350, 0, button="right", steps=14)

    def test_locate_landmark_point_success(self):
        # Model returns normalized point [y, x] = [450, 600]
        json_resp = '{"found": true, "point": [450, 600]}'
        client = MockClient(json_resp)
        nav = GameNavigator(client=client, model_endpoint="gemini-2.5-flash")

        # Mock screen resolution to 1920x1080
        with patch.object(nav, "capture_screen", return_value=(b"fake_jpeg", 1920, 1080)):
            coords = nav.locate_landmark_point("Legendary Bear Mount")
            self.assertIsNotNone(coords)
            # x = (600/1000)*1920 = 1152, y = (450/1000)*1080 = 486
            self.assertEqual(coords, (1152, 486))

    def test_locate_landmark_point_not_found(self):
        json_resp = '{"found": false}'
        client = MockClient(json_resp)
        nav = GameNavigator(client=client)

        with patch.object(nav, "capture_screen", return_value=(b"fake_jpeg", 1920, 1080)):
            coords = nav.locate_landmark_point("Nonexistent Cave")
            self.assertIsNone(coords)

    def test_pan_and_place_marker_full_flow(self):
        json_resp = '{"found": true, "point": [500, 500]}'
        client = MockClient(json_resp)
        nav = GameNavigator(client=client)

        with patch.object(nav, "capture_screen", return_value=(b"fake_jpeg", 1000, 1000)), \
             patch("core.game_nav.send_gamepad_button", return_value=False) as mock_pad, \
             patch("core.game_nav.send_directinput_key") as mock_key, \
             patch("core.game_nav.move_mouse_absolute") as mock_move, \
             patch("core.game_nav.click_mouse_button") as mock_click, \
             patch.object(nav, "verify_marker_placement", return_value={"accurate": True, "correction_needed": False}):

            res = nav.pan_and_place_marker("Legendary Bear Mount", general_direction="north", use_gamepad=True)
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["placed_at"], [500, 500])
            self.assertTrue(res.get("verified"))
            self.assertFalse(res.get("auto_corrected"))
            # Gamepad view tried, returned False, so fallback 'm' pulsed
            mock_pad.assert_called_with("view", duration_sec=0.1)
            mock_key.assert_called_with("m", duration_sec=0.1)
            # Cursor moved to 500, 500
            mock_move.assert_called_with(500, 500)
            # Right click to place waypoint
            mock_click.assert_called_with("right", hold_duration=0.08)

    def test_pan_and_place_marker_keyboard_default(self):
        json_resp = '{"found": true, "point": [500, 500]}'
        client = MockClient(json_resp)
        nav = GameNavigator(client=client)

        with patch.object(nav, "capture_screen", return_value=(b"fake_jpeg", 1000, 1000)), \
             patch("core.game_nav.send_gamepad_button") as mock_pad, \
             patch("core.game_nav.send_directinput_key") as mock_key, \
             patch("core.game_nav.move_mouse_absolute"), \
             patch("core.game_nav.click_mouse_button"), \
             patch.object(nav, "verify_marker_placement", return_value={"accurate": True, "correction_needed": False}):

            res = nav.pan_and_place_marker("Legendary Bear Mount")
            self.assertEqual(res["status"], "success")
            # Defaults to keyboard 'm' without touching gamepad
            mock_pad.assert_not_called()
            mock_key.assert_called_with("m", duration_sec=0.1)

    def test_pan_and_place_marker_with_gamepad_success(self):
        json_resp = '{"found": true, "point": [400, 300]}'
        client = MockClient(json_resp)
        nav = GameNavigator(client=client)

        with patch.object(nav, "capture_screen", return_value=(b"fake_jpeg", 1000, 1000)), \
             patch("core.game_nav.send_gamepad_button", return_value=True) as mock_pad, \
             patch("core.game_nav.send_directinput_key") as mock_key, \
             patch("core.game_nav.move_mouse_absolute") as mock_move, \
             patch("core.game_nav.click_mouse_button") as mock_click, \
             patch.object(nav, "verify_marker_placement", return_value={"accurate": True, "correction_needed": False}):

            res = nav.pan_and_place_marker_verified("Kweiden Camp", use_gamepad=True)
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["placed_at"], [300, 400])
            mock_pad.assert_called_with("view", duration_sec=0.1)
            # Keyboard 'm' should NOT be called when gamepad succeeded
            mock_key.assert_not_called()

    def test_verify_marker_placement_accurate(self):
        verify_json = '{"accurate": true, "correction_needed": false, "correct_point": null}'
        client = MockClient(verify_json)
        nav = GameNavigator(client=client)

        with patch.object(nav, "capture_screen", return_value=(b"fake_jpeg", 1920, 1080)):
            res = nav.verify_marker_placement("Hidden Cave")
            self.assertTrue(res.get("accurate"))
            self.assertFalse(res.get("correction_needed"))

    def test_pan_and_place_marker_auto_correction(self):
        find_resp = '{"found": true, "point": [500, 500]}'
        verify_resp = {"accurate": False, "correction_needed": True, "correct_point": [550, 620]}
        client = MockClient(find_resp)
        nav = GameNavigator(client=client)

        with patch.object(nav, "capture_screen", return_value=(b"fake_jpeg", 1000, 1000)), \
             patch("core.game_nav.send_gamepad_button", return_value=False), \
             patch("core.game_nav.send_directinput_key"), \
             patch("core.game_nav.move_mouse_absolute") as mock_move, \
             patch("core.game_nav.click_mouse_button") as mock_click, \
             patch.object(nav, "verify_marker_placement", return_value=verify_resp):

            res = nav.pan_and_place_marker("Legendary Bear Mount")
            self.assertEqual(res["status"], "success")
            self.assertTrue(res.get("auto_corrected"))
            self.assertTrue(res.get("verified"))
            self.assertEqual(res["initial_point"], [500, 500])
            # Auto-corrected to (620, 550)
            self.assertEqual(res["placed_at"], [620, 550])
            # Mouse moved to initial, then corrected
            self.assertEqual(mock_move.call_count, 2)
            mock_move.assert_called_with(620, 550)
            # Clicked twice (initial + correction)
            self.assertEqual(mock_click.call_count, 2)

    def test_send_gamepad_button_and_admin_check(self):
        from tools.os_controls import send_gamepad_button, is_running_as_admin
        # Test admin check returns boolean
        admin_status = is_running_as_admin()
        self.assertIsInstance(admin_status, bool)

        # Test send_gamepad_button unknown key
        self.assertFalse(send_gamepad_button("nonexistent_button_xyz"))

    # --- 3. Web Default Keybind Ingestion Tests ---

    def test_fetch_web_default_keybinds(self):
        sample_web_binds = {
            "call mount": {
                "key": "h",
                "modifiers": [],
                "description": "Call or mount active companion horse/bear"
            },
            "crouch": {
                "key": "c",
                "modifiers": [],
                "description": "Crouch or slide while sprinting"
            },
            "open map": {
                "key": "m",
                "modifiers": [],
                "description": "Toggle world map"
            }
        }
        client = MockClient(json.dumps(sample_web_binds))
        binds = self.mgr.fetch_web_default_keybinds("Crimson Desert", client, "gemini-2.5-flash")
        self.assertIn("call mount", binds)
        self.assertEqual(binds["call mount"]["key"], "h")
        self.assertEqual(binds["call mount"]["source"], "web_defaults")

    def test_sync_game_binds_with_fallback(self):
        self.mgr.add_game("Crimson Desert", "CrimsonDesert64.exe")
        self.mgr.set_active_profile("crimson_desert")

        sample_web_binds = {
            "call mount": {"key": "h", "modifiers": [], "description": "Call mount"},
            "open inventory": {"key": "i", "modifiers": [], "description": "Inventory"}
        }
        client = MockClient(json.dumps(sample_web_binds))

        res = self.mgr.sync_game_binds_with_fallback("crimson_desert", client, "gemini-2.5-flash")
        self.assertTrue(res["success"])
        self.assertEqual(res["source"], "web_defaults")
        self.assertEqual(res["count"], 2)

        # Verify persisted into profile
        prof = self.mgr.get_profile("crimson_desert")
        self.assertIn("call mount", prof["keybinds"])
        self.assertEqual(prof["keybinds"]["call mount"]["key"], "h")

    # --- 4. Tool & GuiBridge Exposure Tests ---

    def test_tool_pan_and_mark_map_location(self):
        dispatcher = ToolDispatcher()
        tools = register_game_tools(dispatcher, self.mgr)
        self.assertIn("pan_and_mark_map_location", dispatcher._custom_handlers)

        with patch("core.game_nav.GameNavigator.pan_and_place_marker") as mock_nav_call:
            mock_nav_call.return_value = {"status": "success", "placed_at": [600, 400]}
            res = tools["pan_and_mark_map_location"]("Ancient Crypt", "northeast")
            self.assertEqual(res["status"], "success")
            mock_nav_call.assert_called_with("Ancient Crypt", "northeast")

    def test_gui_bridge_sync_with_fallback(self):
        mock_engine = MagicMock()
        mock_engine.client = MockClient(json.dumps({"call mount": {"key": "h", "modifiers": []}}))
        mock_engine.config = {"primary_model_endpoint": "gemini-2.5-flash"}
        bridge = GUIBridge(mock_engine)
        bridge.game_mgr = self.mgr

        self.mgr.add_game("Crimson Desert")
        sync_res = bridge.sync_game_bindings("crimson_desert")
        self.assertTrue(sync_res["success"])
        self.assertEqual(sync_res["source"], "web_defaults")

    # --- 5. Game Mode & Context Fencing Tests ---

    @patch("tools.os_controls.send_directinput_key")
    def test_game_mode_fencing_and_macro_retention(self, mock_key):
        self.mgr.add_game("Elite Dangerous", process_name="EliteDangerous64.exe", keybinds={
            "deploy landing gear": {"key": "l", "modifiers": [], "description": "Toggle landing gear"}
        })
        self.mgr.set_active_profile("elite_dangerous")

        # 1. Game Mode Enabled: Context is strictly locked to Elite Dangerous
        self.assertTrue(self.mgr.is_game_mode_active())
        ctx = self.mgr.get_active_game_context()
        self.assertIn("=== ACTIVE GAME COMPANION: Elite Dangerous ===", ctx)
        self.assertIn("STRICT GAME SCOPE & SEARCH LOCK: ELITE DANGEROUS", ctx)

        # 2. User says "disable game mode": Cognitive context is cleared (unfenced for open chat)
        res = self.mgr.set_game_mode(False, user_explicit=True)
        self.assertFalse(self.mgr.is_game_mode_active())
        self.assertEqual(self.mgr.get_active_game_context(), "")

        # 3. Macro Dispatcher remains standing by and executes in-game macros seamlessly!
        macro_res = self.mgr.trigger_action("deploy landing gear")
        self.assertEqual(macro_res.get("status"), "executed")
        mock_key.assert_called_with("l")

        # 4. User re-enables game mode
        self.mgr.set_game_mode(True, user_explicit=True)
        self.assertTrue(self.mgr.is_game_mode_active())
        ctx_re = self.mgr.get_active_game_context()
        self.assertIn("=== ACTIVE GAME COMPANION: Elite Dangerous ===", ctx_re)

    def test_gui_bridge_game_mode_controls(self):
        mock_engine = MagicMock()
        bridge = GUIBridge(mock_engine)
        bridge.game_mgr = self.mgr

        self.mgr.add_game("Elite Dangerous", process_name="EliteDangerous64.exe")
        self.mgr.set_active_profile("elite_dangerous")

        status = bridge.get_game_mode()
        self.assertTrue(status["is_active"])
        self.assertEqual(status["active_profile"], "elite_dangerous")

        # Toggle Game Mode via bridge
        tog_res = bridge.toggle_game_mode()
        self.assertFalse(tog_res["game_mode_enabled"])
        self.assertFalse(bridge.get_game_mode()["is_active"])

        # Set explicitly via bridge
        set_res = bridge.set_game_mode(True)
        self.assertTrue(set_res["game_mode_enabled"])
        self.assertTrue(bridge.get_game_mode()["is_active"])

    def test_toggle_game_mode_tool(self):
        dispatcher = ToolDispatcher()
        tools = register_game_tools(dispatcher, self.mgr)
        self.assertIn("toggle_game_mode", dispatcher._custom_handlers)

        self.mgr.add_game("Crimson Desert")
        self.mgr.set_active_profile("crimson_desert")

        # Call toggle tool to disable
        res_off = tools["toggle_game_mode"](False)
        self.assertFalse(res_off["game_mode_enabled"])
        self.assertFalse(self.mgr.is_game_mode_active())

        # Call toggle tool to re-enable
        res_on = tools["toggle_game_mode"](True)
        self.assertTrue(res_on["game_mode_enabled"])
        self.assertTrue(self.mgr.is_game_mode_active())

    def test_game_mode_hotkey_configuration(self):
        from core.hotkey_manager import HotkeyManager
        toggle_called = []
        def on_toggle():
            toggle_called.append(True)

        hotkey_mgr = HotkeyManager(on_game_mode_toggle=on_toggle)
        hotkey_mgr.update_config({
            "game_mode_hotkey": "Ctrl+Shift+G",
            "game_mode_vk": 0x47,
            "game_mode_modifiers": ["Control", "Shift"]
        })
        self.assertEqual(hotkey_mgr.game_mode_vk, 0x47)
        self.assertEqual(hotkey_mgr.game_mode_modifiers, ["Control", "Shift"])
        self.assertEqual(hotkey_mgr.game_mode_display, "Ctrl+Shift+G")

        # Test callback execution
        hotkey_mgr.on_game_mode_toggle()
        self.assertEqual(len(toggle_called), 1)

    def test_game_mode_auto_enable_and_status_booleans(self):
        from ui.hud_window import HUDBridge
        self.mgr.add_game("Crimson Desert")
        
        # When active profile is set, game mode auto-engages and listener is notified
        notifications = []
        def on_changed(enabled, gid, gname):
            notifications.append((enabled, gid, gname))
        self.mgr.on_game_mode_changed = on_changed

        self.mgr.set_active_profile("crimson_desert")
        self.assertTrue(self.mgr.game_mode_enabled)
        self.assertTrue(self.mgr.is_game_mode_active())
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0], (True, "crimson_desert", "Crimson Desert"))

        # Test get_current_game_status tool returns explicit booleans
        dispatcher = ToolDispatcher()
        tools = register_game_tools(dispatcher, self.mgr)
        status = tools["get_current_game_status"]()
        self.assertTrue(status["game_mode_enabled"])
        self.assertTrue(status["is_game_mode_active"])
        self.assertEqual(status["active_profile"], "crimson_desert")
        self.assertEqual(status["game"], "Crimson Desert")

        # Test HUDBridge delegation for overlay
        mock_bridge = MagicMock()
        mock_bridge.get_game_mode.return_value = {
            "game_mode_enabled": True,
            "is_active": True,
            "active_profile": "crimson_desert",
            "display_name": "Crimson Desert"
        }
        mock_bridge.get_agent_name.return_value = {"agent_name": "Aether"}
        hud_bridge = HUDBridge(window=None, bridge=mock_bridge)
        gm_res = hud_bridge.get_game_mode()
        self.assertTrue(gm_res["game_mode_enabled"])
        self.assertTrue(gm_res["is_active"])
        self.assertEqual(hud_bridge.get_agent_name()["agent_name"], "Aether")

    def test_strict_universe_lock_prompt_and_tool_filtering(self):
        from core.engine import Engine
        engine = Engine(config_getter=lambda: {"api": {"system_instruction": "You are Aether desktop assistant."}})
        engine.game_mgr = self.mgr
        self.mgr.add_game("Crimson Desert")
        self.mgr.data["profiles"]["crimson_desert"]["universe_name"] = "Pywel"
        self.mgr.set_active_profile("crimson_desert")
        self.assertTrue(self.mgr.is_game_mode_active())

        # 1. System instruction when Game Mode is active
        sys_inst = engine._build_system_instruction()
        self.assertIn("IN-CHARACTER GAME MODE ENGAGED — CRIMSON DESERT", sys_inst)
        self.assertIn("ZERO TOLERANCE FOR OUT-OF-UNIVERSE DEVIATION", sys_inst)
        self.assertIn("Pywel", sys_inst)
        self.assertIn("disable Game Mode", sys_inst)
        self.assertNotIn("You are Aether desktop assistant.", sys_inst)

        # 2. Tool declarations when Game Mode is active
        cfg = engine._execute_turn_modular("what should I do next?")
        tool_names = [fn.name for t in cfg.tools if t.function_declarations for fn in t.function_declarations]
        self.assertIn("trigger_game_action", tool_names)
        self.assertIn("get_current_game_status", tool_names)
        # Invariant OS tools should be filtered out
        self.assertNotIn("navigate_browser", tool_names)
        self.assertNotIn("launch_application", tool_names)
        self.assertNotIn("run_saved_script", tool_names)

        # 3. When Game Mode is toggled off
        self.mgr.set_game_mode(False, user_explicit=True)
        self.assertFalse(self.mgr.is_game_mode_active())
        sys_inst_off = engine._build_system_instruction()
        self.assertIn("You are Aether desktop assistant.", sys_inst_off)
        self.assertNotIn("IN-CHARACTER GAME MODE ENGAGED", sys_inst_off)
        engine.shutdown()

    def test_game_mode_boot_standby_and_dynamic_process_lifecycle(self):
        """Verifies that boot without running game stays in standby, auto-engages when game runs, and disengages on game close."""
        import tempfile
        import uuid
        tmp_json = os.path.join(tempfile.gettempdir(), f"test_games_{uuid.uuid4().hex[:8]}.json")
        initial_data = {
            "active_profile": "crimson_desert",
            "profiles": {
                "crimson_desert": {
                    "display_name": "Crimson Desert",
                    "process_name": "CrimsonDesert.exe",
                    "universe_name": "Pywel"
                }
            }
        }
        with open(tmp_json, "w", encoding="utf-8") as f:
            json.dump(initial_data, f)

        try:
            # 1. Boot up with no game process running
            with patch.object(GameManager, "detect_foreground_game", return_value=None), \
                 patch.object(GameManager, "detect_running_game", return_value=None):
                mgr = GameManager(profiles_path=tmp_json)
                self.assertFalse(mgr.game_mode_enabled)
                self.assertFalse(mgr.is_game_mode_active())
                self.assertFalse(mgr.is_game_running("crimson_desert"))

            # 2. Game starts running
            with patch.object(mgr, "detect_foreground_game", return_value="crimson_desert"), \
                 patch.object(mgr, "detect_running_game", return_value="crimson_desert"):
                status = mgr.check_and_update_foreground_game()
                self.assertTrue(mgr.game_mode_enabled)
                self.assertTrue(mgr.is_game_mode_active())
                self.assertEqual(status["detected_game"], "crimson_desert")

            # 3. Game closes
            with patch.object(mgr, "detect_foreground_game", return_value=None), \
                 patch.object(mgr, "detect_running_game", return_value=None):
                status_closed = mgr.check_and_update_foreground_game()
                self.assertFalse(mgr.game_mode_enabled)
                self.assertFalse(mgr.is_game_mode_active())
                self.assertIsNone(status_closed["detected_game"])
        finally:
            if os.path.exists(tmp_json):
                os.remove(tmp_json)

    def test_chat_context_invalidation_on_game_mode_toggle(self):
        """Verifies that toggling game mode sets the context reset flag on the engine."""
        from core.engine import Engine
        engine = Engine(config_getter=lambda: {"api": {"system_instruction": "You are Aether."}})
        engine.game_mgr = self.mgr
        self.mgr.add_game("Crimson Desert")
        self.mgr.set_active_profile("crimson_desert")

        # Initial active state
        self.assertTrue(self.mgr.is_game_mode_active())
        engine._reset_context_flag = False

        # 1. Turning off Game Mode triggers context reset flag
        bridge = GUIBridge(engine)
        bridge.game_mgr = self.mgr
        bridge.set_game_mode(False)
        self.assertFalse(self.mgr.is_game_mode_active())
        self.assertTrue(engine._reset_context_flag)

        # 2. Reset flag consumed
        engine._reset_context_flag = False

        # 3. Toggling Game Mode back on triggers context reset flag
        bridge.toggle_game_mode()
        self.assertTrue(self.mgr.is_game_mode_active())
        self.assertTrue(engine._reset_context_flag)

        engine.shutdown()


if __name__ == "__main__":
    unittest.main()
