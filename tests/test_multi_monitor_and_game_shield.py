"""
tests/test_multi_monitor_and_game_shield.py - Unit tests for Multi-Monitor Integration & Game Shield
Validates:
1. Enumeration of 1 to N Windows displays matching Windows Display Settings numbers.
2. Window movement to target monitors with maximize and no_activate options.
3. Companion web shortcuts in navigate_browser (inara, eddb, coriolis, mapgenie, etc.).
4. Game Shield protection: blocking close_window / close_application on the active game in Game Mode.
5. Permitting non-game apps (File Explorer, Chrome, Discord) to close while in Game Mode.
6. Permitting all apps to close when Game Mode is inactive.
7. Game focus preservation across auxiliary window launches.
"""

import unittest
from unittest.mock import MagicMock, patch
import sys

from tools.os_controls import (
    get_monitors_info,
    move_window_to_monitor,
    navigate_browser,
    GAME_WEB_SHORTCUTS,
)
from tools.dispatcher import ToolDispatcher
from core.game_engine import GameEngine
from core.game_manager import GameManager


class TestMultiMonitorAndGameShield(unittest.IsolatedAsyncioTestCase):

    def test_game_web_shortcuts_resolution(self):
        """Verifies that companion shortcuts are properly resolved."""
        self.assertIn("inara", GAME_WEB_SHORTCUTS)
        self.assertEqual(GAME_WEB_SHORTCUTS["inara"], "https://inara.cz")
        self.assertEqual(GAME_WEB_SHORTCUTS["coriolis"], "https://coriolis.io")
        self.assertEqual(GAME_WEB_SHORTCUTS["mapgenie"], "https://mapgenie.io")

    @patch("tools.os_controls.win32api")
    def test_get_monitors_info_multi_display(self, mock_win32api):
        """Tests enumeration of 1 to 5 displays sorted by Windows Display number."""
        # Mock 3 display monitors
        mock_win32api.EnumDisplayMonitors.return_value = [
            (101, 0, (1920, 0, 3840, 1080)),
            (102, 0, (0, 0, 1920, 1080)),
            (103, 0, (3840, 0, 5760, 1080)),
        ]
        mock_win32api.GetMonitorInfo.side_effect = lambda hmon: {
            101: {"Device": "\\\\.\\DISPLAY2", "Monitor": (1920, 0, 3840, 1080), "Work": (1920, 0, 3840, 1040), "Flags": 0},
            102: {"Device": "\\\\.\\DISPLAY1", "Monitor": (0, 0, 1920, 1080), "Work": (0, 0, 1920, 1040), "Flags": 1},
            103: {"Device": "\\\\.\\DISPLAY3", "Monitor": (3840, 0, 5760, 1080), "Work": (3840, 0, 5760, 1040), "Flags": 0},
        }[hmon]

        monitors = get_monitors_info()
        self.assertEqual(len(monitors), 3)
        # Verify sorted 1, 2, 3
        self.assertEqual([m["index"] for m in monitors], [1, 2, 3])
        self.assertTrue(monitors[0]["is_primary"])
        self.assertEqual(monitors[0]["width"], 1920)
        self.assertEqual(monitors[0]["work_height"], 1040)
        self.assertEqual(monitors[1]["index"], 2)
        self.assertEqual(monitors[1]["x"], 1920)

    @patch("tools.os_controls.find_hwnd_by_query")
    @patch("tools.os_controls.get_monitors_info")
    @patch("tools.os_controls.win32gui")
    def test_move_window_to_monitor_success(self, mock_win32gui, mock_get_monitors, mock_find_hwnd):
        """Tests moving an application to secondary monitor with maximize."""
        mock_get_monitors.return_value = [
            {"index": 1, "is_primary": True, "work_x": 0, "work_y": 0, "work_width": 1920, "work_height": 1040},
            {"index": 2, "is_primary": False, "work_x": 1920, "work_y": 0, "work_width": 2560, "work_height": 1400},
        ]
        mock_find_hwnd.return_value = 9999
        mock_win32gui.GetWindowText.return_value = "Chrome - Inara"
        mock_win32gui.IsZoomed.return_value = False

        res = move_window_to_monitor("chrome", monitor=2, maximize=True, no_activate=True)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["monitor"], 2)
        self.assertTrue(mock_win32gui.SetWindowPos.called)
        self.assertTrue(mock_win32gui.ShowWindow.called)

    @patch("tools.os_controls.get_monitors_info")
    def test_move_window_to_invalid_monitor(self, mock_get_monitors):
        """Tests requesting a monitor that exceeds the number of connected displays."""
        mock_get_monitors.return_value = [
            {"index": 1, "is_primary": True, "work_x": 0, "work_y": 0, "work_width": 1920, "work_height": 1040},
        ]
        res = move_window_to_monitor("chrome", monitor=4)
        self.assertEqual(res["status"], "error")
        self.assertIn("Monitor #4 not found", res["message"])
        self.assertIn("1 monitor(s) configured", res["message"])

    @patch("tools.os_controls.find_hwnd_by_query")
    @patch("tools.os_controls.focus_window")
    @patch("tools.os_controls.move_window_to_monitor")
    @patch("tools.gui_primitives.GuiPrimitivesController")
    def test_navigate_browser_shortcut_and_monitor(self, mock_gui_cls, mock_move, mock_focus, mock_find_hwnd):
        """Tests navigating browser with inara shortcut and monitor placement."""
        mock_find_hwnd.return_value = 8888
        mock_gui = MagicMock()
        mock_gui_cls.return_value = mock_gui

        res = navigate_browser("inara", app_name="chrome", monitor=2)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["target"], "https://inara.cz")
        self.assertEqual(res["monitor"], 2)
        mock_gui.press_key.assert_called_with("ctrl+l")
        mock_gui.type_text.assert_called_with("https://inara.cz", press_enter=True)
        mock_move.assert_called_with("chrome", monitor=2, maximize=True)

    async def test_game_shield_blocks_closing_active_game_in_game_mode(self):
        """Verifies Game Shield strictly prevents closing the active game during Game Mode."""
        mock_engine = MagicMock()
        mock_ge = MagicMock()
        mock_gm = MagicMock()
        mock_ge.is_game_mode_active.return_value = True
        mock_gm.is_game_mode_active.return_value = True
        mock_gm.data = {"active_profile": "crimson_desert"}
        mock_gm.get_profile.return_value = {
            "display_name": "Crimson Desert",
            "process_name": "CrimsonDesert.exe"
        }
        mock_engine.game_engine = mock_ge
        mock_engine.game_mgr = mock_gm

        dispatcher = ToolDispatcher(whitelist_getter=lambda: ["crimsondesert.exe", "crimson desert", "explorer.exe"])
        dispatcher.engine = mock_engine

        # 1. Attempt close_window on Crimson Desert
        res1 = await dispatcher.dispatch("close_window", {"app_name": "Crimson Desert"})
        self.assertEqual(res1.get("status"), "blocked")
        self.assertIn("Game Shield active", res1.get("message", ""))

        # 2. Attempt close_application on executable process name
        res2 = await dispatcher.dispatch("close_application", {"app_name": "CrimsonDesert.exe"})
        self.assertEqual(res2.get("status"), "blocked")
        self.assertIn("Game Shield active", res2.get("message", ""))

    async def test_game_shield_allows_closing_auxiliary_app_in_game_mode(self):
        """Verifies Game Shield allows closing companion or desktop apps (File Explorer, etc.) while in Game Mode."""
        mock_engine = MagicMock()
        mock_ge = MagicMock()
        mock_gm = MagicMock()
        mock_ge.is_game_mode_active.return_value = True
        mock_gm.is_game_mode_active.return_value = True
        mock_gm.data = {"active_profile": "crimson_desert"}
        mock_gm.get_profile.return_value = {
            "display_name": "Crimson Desert",
            "process_name": "CrimsonDesert.exe"
        }
        mock_engine.game_engine = mock_ge
        mock_engine.game_mgr = mock_gm

        dispatcher = ToolDispatcher(whitelist_getter=lambda: ["explorer.exe", "file explorer"])
        dispatcher.engine = mock_engine

        with patch("tools.dispatcher.close_window") as mock_close:
            mock_close.return_value = {"status": "success", "title": "File Explorer"}
            res = await dispatcher.dispatch("close_window", {"app_name": "File Explorer"})
            self.assertEqual(res.get("status"), "success")
            mock_close.assert_called_with("File Explorer")

    async def test_game_shield_inactive_when_game_mode_off(self):
        """Verifies that when Game Mode is disabled, Game Shield is inactive and normal close functions work."""
        mock_engine = MagicMock()
        mock_ge = MagicMock()
        mock_gm = MagicMock()
        mock_ge.is_game_mode_active.return_value = False
        mock_gm.is_game_mode_active.return_value = False
        mock_engine.game_engine = mock_ge
        mock_engine.game_mgr = mock_gm

        dispatcher = ToolDispatcher(whitelist_getter=lambda: ["crimsondesert.exe", "crimson desert"])
        dispatcher.engine = mock_engine

        with patch("tools.dispatcher.close_window") as mock_close:
            mock_close.return_value = {"status": "success", "title": "Crimson Desert"}
            res = await dispatcher.dispatch("close_window", {"app_name": "Crimson Desert"})
            self.assertEqual(res.get("status"), "success")
            mock_close.assert_called_with("Crimson Desert")

    async def test_launch_application_with_monitor_and_focus_preservation(self):
        """Verifies launch_application positions window on monitor and restores game focus."""
        mock_engine = MagicMock()
        mock_ge = MagicMock()
        mock_ge.is_game_mode_active.return_value = True
        mock_engine.game_engine = mock_ge
        mock_engine.game_mgr = mock_ge

        dispatcher = ToolDispatcher(whitelist_getter=lambda: ["chrome.exe", "chrome"])
        dispatcher.engine = mock_engine

        with patch("security.whitelist.WhitelistValidator.validate_and_launch") as mock_launch, \
             patch("tools.dispatcher.find_hwnd_by_query", return_value=5555), \
             patch("tools.dispatcher.move_window_to_monitor") as mock_move, \
             patch("tools.dispatcher.bring_hwnd_to_foreground") as mock_bring, \
             patch("tools.dispatcher.win32gui") as mock_win32gui:

            mock_win32gui.GetForegroundWindow.return_value = 1234  # Game window
            mock_launch.return_value = {"status": "success", "executable": "chrome.exe"}

            res = await dispatcher.dispatch("launch_application", {"app_name": "chrome", "monitor": 2})
            self.assertEqual(res.get("status"), "success")
            mock_move.assert_called_with("chrome", monitor=2, maximize=False, no_activate=True)
            mock_bring.assert_called_with(1234)

    def test_immersion_directives_include_auxiliary_consoles(self):
        """Verifies that GameEngine system prompt includes directives for auxiliary consoles and companion MFDs."""
        gm = GameManager()
        gm.set_game_mode(True, user_explicit=True)
        ge = GameEngine()
        ge.manager = gm

        prompt = ge.build_system_instruction()
        self.assertIsNotNone(prompt)
        self.assertIn("AUXILIARY CONSOLES & COMPANION FLIGHT-DECK TOOLS", prompt)
        self.assertIn("move_window_to_monitor", prompt)
        self.assertIn("Game Shield", prompt)


if __name__ == "__main__":
    unittest.main()
