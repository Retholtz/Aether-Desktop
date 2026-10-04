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

            res = nav.pan_and_place_marker("Legendary Bear Mount", general_direction="north")
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


if __name__ == "__main__":
    unittest.main()
