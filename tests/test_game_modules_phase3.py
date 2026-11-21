"""
Phase 3 Game Companion Ergonomics Tests:
- One-Click Vision HUD Bar Calibration (`calibrate_hud_bars_from_frame`)
- Mouse Side-Button (`Mouse4`/`Mouse5`/`Middle`) & XInput Gamepad Push-to-Talk (`HotkeyManager`)
- Elevated-Game (Admin / UIPI) Detection Warning (`GameManager.check_game_elevation`)
- Frontend UI controls for HUD calibration, UIPI warning banner, and Mouse/Gamepad PTT
"""

import ctypes
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestVisionHudCalibration(unittest.TestCase):
    def test_calibrate_hud_bars_parses_and_clamps_regions(self):
        from core.telemetry.providers.vision_hud_provider import calibrate_hud_bars_from_frame

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = json.dumps({
            "bars": [
                {"name": "Health", "bbox_1000": [40, 50, 180, 15], "color_profile": "red"},
                {"name": "Stamina", "bbox_1000": [40, 72, 150, 12], "color_profile": "green"},
                {"name": "Spirit", "bbox_1000": [40, 92, 140, 12], "color_profile": "invalid_color"},
                {"name": "Health", "bbox_1000": [10, 10, 100, 10], "color_profile": "red"},  # duplicate ignored
            ]
        })
        mock_client.models.generate_content.return_value = mock_resp

        bars = calibrate_hud_bars_from_frame(b"fake_jpg", mock_client, game_name="Crimson Desert")
        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[0].name, "health")
        self.assertEqual(bars[0].bbox_pct, (0.04, 0.05, 0.18, 0.015))
        self.assertEqual(bars[0].color_profile, "red")
        self.assertEqual(bars[1].name, "stamina")
        self.assertEqual(bars[2].name, "spirit")
        self.assertEqual(bars[2].color_profile, "red")  # invalid color falls back safely

    def test_calibrate_hud_bars_returns_empty_on_menu_screen(self):
        from core.telemetry.providers.vision_hud_provider import calibrate_hud_bars_from_frame

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = '{"bars": []}'
        mock_client.models.generate_content.return_value = mock_resp

        self.assertEqual(calibrate_hud_bars_from_frame(b"fake_jpg", mock_client), [])


class TestMouseAndGamepadPTT(unittest.TestCase):
    def test_parse_mouse_and_gamepad_bindings(self):
        from core.hotkey_manager import (
            VK_MBUTTON,
            VK_XBUTTON1,
            VK_XBUTTON2,
            parse_gamepad_button_mask,
            parse_keybind_string,
        )

        self.assertEqual(parse_keybind_string("Mouse4"), (VK_XBUTTON1, []))
        self.assertEqual(parse_keybind_string("Mouse5"), (VK_XBUTTON2, []))
        self.assertEqual(parse_keybind_string("Mouse3"), (VK_MBUTTON, []))
        self.assertEqual(parse_gamepad_button_mask("Gamepad_LB"), 0x0100)
        self.assertEqual(parse_gamepad_button_mask("Gamepad RB"), 0x0200)
        self.assertEqual(parse_gamepad_button_mask("Gamepad LS"), 0x0040)
        self.assertEqual(parse_gamepad_button_mask("Space"), 0)

    def test_mouse_side_button_ptt_hold_and_toggle(self):
        from core.hotkey_manager import (
            HotkeyManager,
            MSLLHOOKSTRUCT,
            VK_XBUTTON1,
            WM_XBUTTONDOWN,
            WM_XBUTTONUP,
        )

        events = []
        hm = HotkeyManager(
            on_ptt_change=lambda active: events.append(("hold", active)),
            on_ptt_toggle=lambda: events.append(("toggle", True)),
            config_getter=lambda: {
                "audio": {
                    "mode": "ptt",
                    "ptt_type": "hold",
                    "ptt_key": "Mouse4",
                    "ptt_key_display": "Mouse4",
                    "ptt_vk": VK_XBUTTON1,
                    "ptt_modifiers": [],
                }
            },
        )
        hm._running = True
        hm._dispatch_callback = lambda fn, *args, **kw: fn(*args) if fn else None

        ms = MSLLHOOKSTRUCT()
        ms.mouseData = (1 << 16)  # XBUTTON1 (Mouse4)

        with patch.object(hm, "_check_modifiers", return_value=True), \
             patch("ctypes.windll.user32.CallNextHookEx", return_value=0):
            hm._low_level_mouse_proc(0, WM_XBUTTONDOWN, ctypes.addressof(ms))
            hm._low_level_mouse_proc(0, WM_XBUTTONUP, ctypes.addressof(ms))

        self.assertEqual(events, [("hold", True), ("hold", False)])

        # Toggle mode
        events.clear()
        hm.ptt_type = "toggle"
        with patch.object(hm, "_check_modifiers", return_value=True), \
             patch("ctypes.windll.user32.CallNextHookEx", return_value=0):
            hm._low_level_mouse_proc(0, WM_XBUTTONDOWN, ctypes.addressof(ms))
            hm._low_level_mouse_proc(0, WM_XBUTTONUP, ctypes.addressof(ms))
        self.assertEqual(events, [("toggle", True)])

    def test_xinput_gamepad_poll_detects_bound_button(self):
        from core.hotkey_manager import HotkeyManager

        hm = HotkeyManager(
            config_getter=lambda: {
                "audio": {
                    "mode": "ptt",
                    "ptt_type": "hold",
                    "ptt_key": "Gamepad_LB",
                    "ptt_key_display": "Gamepad LB",
                }
            }
        )
        self.assertEqual(hm.ptt_gamepad_mask, 0x0100)

        class _FakeXInput:
            def __init__(self, buttons):
                self.buttons = buttons

            def XInputGetState(self, idx, state_ptr):
                if idx == 0:
                    state_ptr._obj.Gamepad.wButtons = self.buttons
                    return 0
                return 1167  # ERROR_DEVICE_NOT_CONNECTED

        self.assertTrue(hm._poll_gamepad_once(_FakeXInput(0x0100)))
        self.assertFalse(hm._poll_gamepad_once(_FakeXInput(0x0200)))


class TestElevatedGameUIPIDetection(unittest.TestCase):
    def test_detects_elevated_game_when_aether_is_standard_user(self):
        from core.game_manager import GameManager

        with tempfile.TemporaryDirectory() as tmp:
            pfile = Path(tmp) / "profiles.json"
            mgr = GameManager(profiles_path=str(pfile))
            mgr.add_game("Admin Game", "AdminGame.exe")

            fake_proc = MagicMock()
            fake_proc.info = {"pid": 4321, "name": "AdminGame.exe"}

            with patch("core.game_manager.is_current_process_elevated", return_value=False), \
                 patch("core.game_manager.is_pid_elevated", return_value=True), \
                 patch("psutil.process_iter", return_value=[fake_proc]):
                res = mgr.check_game_elevation("admin_game")
                self.assertTrue(res["uipi_blocked"])
                self.assertTrue(res["game_elevated"])
                self.assertIn("Administrator", res["warning"])

            # If Aether is also elevated, UIPI is NOT blocked
            with patch("core.game_manager.is_current_process_elevated", return_value=True):
                res_ok = mgr.check_game_elevation("admin_game")
                self.assertFalse(res_ok["uipi_blocked"])

            mgr.close()


class TestPhase3FrontendWiring(unittest.TestCase):
    def test_ui_controls_present_for_hud_calibration_uipi_and_mouse_gamepad_ptt(self):
        html = (REPO_ROOT / "ui" / "static" / "index.html").read_text(encoding="utf-8")
        games_js = (REPO_ROOT / "ui" / "static" / "games_tab.js").read_text(encoding="utf-8")
        app_js = (REPO_ROOT / "ui" / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("btn-calibrate-hud-bars", html)
        self.assertIn("btn-calibrate-hud-bars", games_js)
        self.assertIn("game-uipi-warning-banner", html)
        self.assertIn("game-uipi-warning-banner", games_js)
        self.assertIn("ptt-quick-bind-btn", html)
        self.assertIn("Gamepad_LB", html)
        self.assertIn("ptt-quick-bind-btn", app_js)
        self.assertIn("Mouse4 (Side Back)", app_js)


if __name__ == "__main__":
    unittest.main()
