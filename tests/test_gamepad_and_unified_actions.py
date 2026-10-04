"""
Unit tests for ViGEm Virtual Gamepad extensions, unified action routing (keyboard/mouse/controller),
and dead-reckoning map navigation.
"""

import unittest
from unittest.mock import MagicMock, patch
from tools.os_controls import (
    send_gamepad_button,
    send_gamepad_stick,
    send_gamepad_trigger,
    send_gamepad_combo,
)
from core.game_manager import GameManager
from core.game_nav import UniversalGameNavigator, GameNavigator


class TestGamepadAndUnifiedActions(unittest.TestCase):

    @patch("tools.os_controls.get_virtual_gamepad")
    def test_send_gamepad_button(self, mock_get_pad):
        mock_pad = MagicMock()
        mock_get_pad.return_value = mock_pad

        # Digital button
        res = send_gamepad_button("a", duration_sec=0.01)
        self.assertTrue(res)
        self.assertTrue(mock_pad.press_button.called)
        self.assertTrue(mock_pad.release_button.called)
        self.assertTrue(mock_pad.update.called)

        # Prefix stripping
        res2 = send_gamepad_button("gamepad_x", duration_sec=0.01)
        self.assertTrue(res2)

    @patch("tools.os_controls.get_virtual_gamepad")
    def test_send_gamepad_stick(self, mock_get_pad):
        mock_pad = MagicMock()
        mock_get_pad.return_value = mock_pad

        # Left stick deflection
        res = send_gamepad_stick("left", x=0.75, y=-0.5, duration_sec=0.01)
        self.assertTrue(res)
        self.assertTrue(mock_pad.left_joystick_float.called)

        # Right stick deflection
        res_r = send_gamepad_stick("right", x=-1.0, y=1.0, duration_sec=0.01)
        self.assertTrue(res_r)
        self.assertTrue(mock_pad.right_joystick_float.called)

    @patch("tools.os_controls.get_virtual_gamepad")
    def test_send_gamepad_trigger(self, mock_get_pad):
        mock_pad = MagicMock()
        mock_get_pad.return_value = mock_pad

        # Right trigger
        res = send_gamepad_trigger("rt", value=0.85, duration_sec=0.01)
        self.assertTrue(res)
        self.assertTrue(mock_pad.right_trigger_float.called)

        # Left trigger
        res_l = send_gamepad_trigger("lt", value=1.0, duration_sec=0.01)
        self.assertTrue(res_l)
        self.assertTrue(mock_pad.left_trigger_float.called)

    @patch("tools.os_controls.get_virtual_gamepad")
    def test_send_gamepad_combo(self, mock_get_pad):
        mock_pad = MagicMock()
        mock_get_pad.return_value = mock_pad

        res = send_gamepad_combo(["lb", "a"], duration_sec=0.01)
        self.assertTrue(res)
        self.assertEqual(mock_pad.press_button.call_count, 2)
        self.assertEqual(mock_pad.release_button.call_count, 2)

    @patch("tools.os_controls.click_mouse")
    @patch("tools.os_controls.send_gamepad_button")
    @patch("tools.os_controls.send_gamepad_trigger")
    @patch("tools.os_controls.send_directinput_key")
    def test_trigger_action_unified_dispatch(self, mock_key, mock_trig, mock_gp_btn, mock_mouse):
        gm = GameManager()
        gm.data["active_profile"] = "test_game"
        gm.data["profiles"]["test_game"] = {
            "display_name": "Test Game",
            "keybinds": {
                "primary attack": {"key": "mouse_left", "description": "Attack with weapon"},
                "secondary attack": {"key": "mouse_right", "description": "Heavy swing"},
                "jump": {"key": "space", "description": "Jump"},
                "gamepad interact": {"key": "gamepad_x", "description": "Interact on controller"},
                "gamepad aim": {"key": "lt", "description": "Aim down sights"}
            }
        }

        # 1. Mouse Left Click
        res_atk = gm.trigger_action("primary attack")
        self.assertEqual(res_atk["status"], "executed")
        self.assertEqual(res_atk["type"], "mouse")
        mock_mouse.assert_called_with(button="left")

        # 2. Keyboard Key
        res_jmp = gm.trigger_action("jump")
        self.assertEqual(res_jmp["status"], "executed")
        self.assertEqual(res_jmp["type"], "keyboard")
        mock_key.assert_called_with("space")

        # 3. Gamepad Button
        res_gp = gm.trigger_action("gamepad interact")
        self.assertEqual(res_gp["status"], "executed")
        self.assertEqual(res_gp["type"], "gamepad")
        mock_gp_btn.assert_called_with("gamepad_x")

        # 4. Gamepad Trigger
        res_trig = gm.trigger_action("gamepad aim")
        self.assertEqual(res_trig["status"], "executed")
        self.assertEqual(res_trig["type"], "gamepad")
        mock_trig.assert_called_with("lt", 1.0)

    def test_hud_discovery_pan_button_detection(self):
        nav = UniversalGameNavigator()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"waypoint_button": "right", "pan_button": "left", "center_player_key": "space"}'
        mock_client.models.generate_content.return_value = mock_response
        nav.client = mock_client

        hud_info = nav.discover_hud_controls(b"fake_image")
        self.assertEqual(hud_info.get("waypoint_button"), "right")
        self.assertEqual(hud_info.get("pan_button"), "left")
        self.assertEqual(hud_info.get("center_player_key"), "space")


if __name__ == "__main__":
    unittest.main()

