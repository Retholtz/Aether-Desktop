"""
tests/test_win11_os_controls.py - Unit tests for Batch 1 Windows 11 OS Control & Multi-Monitor Fixes.
"""

import asyncio
import os
import sys
import tempfile
import shutil
import unittest
from unittest.mock import MagicMock, patch

from tools.os_controls import (
    find_hwnd_by_query,
    close_window,
    tile_windows,
    snap_window,
    _place_window_verified,
    SHELL_EXCLUDED_CLASSES,
)
from tools.dispatcher import ToolDispatcher
from tools.gui_primitives import GuiPrimitivesController, KEYEVENTF_EXTENDEDKEY, VK_LWIN, VK_LEFT
from core.screen_stream import ScreenCapturePipeline
from core.media_controller_service import MediaControllerService
from core.program_manager import ProgramManager
from security.whitelist import WhitelistValidator
from ui.hud_window import get_monitor_work_areas
from tools.script_runner import ScriptRunner


class TestWin11WindowDiscoveryAndTiling(unittest.IsolatedAsyncioTestCase):

    @patch("tools.os_controls._is_window_cloaked", return_value=False)
    @patch("tools.os_controls.psutil")
    @patch("tools.os_controls.win32process")
    @patch("tools.os_controls.win32gui")
    def test_file_explorer_ignores_progman_and_shell_windows(
        self, mock_gui, mock_proc_api, mock_psutil, mock_cloaked
    ):
        """find_hwnd_by_query('file explorer') must ignore Progman/Shell_TrayWnd and only match CabinetWClass."""
        windows = [
            (100, "Program Manager", "Progman", "explorer.exe", (0, 0, 2560, 1440), 0),
            (101, "", "Shell_TrayWnd", "explorer.exe", (0, 1400, 2560, 1440), 0),
            (102, "File Explorer - Google Chrome", "Chrome_WidgetWin_1", "chrome.exe", (10, 10, 1200, 900), 0),
            (103, "Downloads - File Explorer", "CabinetWClass", "explorer.exe", (50, 50, 1000, 700), 0),
        ]
        by_hwnd = {w[0]: w for w in windows}

        mock_gui.EnumWindows.side_effect = lambda cb, extra: [cb(w[0], extra) for w in windows]
        mock_gui.IsWindowVisible.return_value = True
        mock_gui.IsIconic.return_value = False
        mock_gui.GetWindowText.side_effect = lambda h: by_hwnd[h][1]
        mock_gui.GetClassName.side_effect = lambda h: by_hwnd[h][2]
        mock_gui.GetWindowRect.side_effect = lambda h: by_hwnd[h][4]
        mock_gui.GetWindowLong.side_effect = lambda h, idx: by_hwnd[h][5]
        mock_proc_api.GetWindowThreadProcessId.side_effect = lambda h: (1, h)
        mock_psutil.Process.side_effect = lambda pid: MagicMock(name=lambda: by_hwnd[pid][3])
        for w in windows:
            pass
        mock_psutil.Process.side_effect = lambda pid: type("P", (), {"name": lambda self, n=by_hwnd[pid][3]: n})()

        self.assertEqual(find_hwnd_by_query("file explorer"), 103)
        self.assertEqual(find_hwnd_by_query("explorer"), 103)

        # When no CabinetWClass window is open, must return None (NOT Progman 100 or Chrome tab 102!)
        windows_no_explorer = windows[:3]
        mock_gui.EnumWindows.side_effect = lambda cb, extra: [cb(w[0], extra) for w in windows_no_explorer]
        self.assertIsNone(find_hwnd_by_query("file explorer"))

    @patch("tools.os_controls.psutil")
    @patch("tools.os_controls.win32process")
    @patch("tools.os_controls.win32gui")
    def test_cloaked_and_corewindow_uwp_ghosts_excluded(self, mock_gui, mock_proc_api, mock_psutil):
        """DWM-cloaked UWP windows and Windows.UI.Core.CoreWindow ghosts must never be returned."""
        windows = [
            (200, "Settings", "Windows.UI.Core.CoreWindow", "SystemSettings.exe", (0, 0, 1200, 800)),
            (201, "Calculator", "ApplicationFrameWindow", "ApplicationFrameHost.exe", (0, 0, 400, 600)),
        ]
        by_hwnd = {w[0]: w for w in windows}
        mock_gui.EnumWindows.side_effect = lambda cb, extra: [cb(w[0], extra) for w in windows]
        mock_gui.IsWindowVisible.return_value = True
        mock_gui.IsIconic.return_value = False
        mock_gui.GetWindowText.side_effect = lambda h: by_hwnd[h][1]
        mock_gui.GetClassName.side_effect = lambda h: by_hwnd[h][2]
        mock_gui.GetWindowRect.side_effect = lambda h: by_hwnd[h][4]
        mock_gui.GetWindowLong.return_value = 0
        mock_proc_api.GetWindowThreadProcessId.side_effect = lambda h: (1, h)
        mock_psutil.Process.side_effect = lambda pid: type("P", (), {"name": lambda self, n=by_hwnd[pid][3]: n})()

        with patch("tools.os_controls._is_window_cloaked", side_effect=lambda h: h == 201):
            self.assertIsNone(find_hwnd_by_query("settings"))
            self.assertIsNone(find_hwnd_by_query("calculator"))

        with patch("tools.os_controls._is_window_cloaked", return_value=False):
            self.assertEqual(find_hwnd_by_query("calculator"), 201)

    @patch("tools.os_controls._is_window_cloaked", return_value=False)
    @patch("tools.os_controls.psutil")
    @patch("tools.os_controls.win32process")
    @patch("tools.os_controls.win32gui")
    def test_exact_process_match_outranks_incidental_title_match(self, mock_gui, mock_proc_api, mock_psutil, _):
        """Querying 'chrome' should match chrome.exe over VS Code editing 'chrome_test.py'."""
        windows = [
            (300, "chrome_test.py - Visual Studio Code", "Chrome_WidgetWin_1", "Code.exe", (0, 0, 1600, 900)),
            (301, "Google - Google Chrome", "Chrome_WidgetWin_1", "chrome.exe", (0, 0, 1600, 900)),
        ]
        by_hwnd = {w[0]: w for w in windows}
        mock_gui.EnumWindows.side_effect = lambda cb, extra: [cb(w[0], extra) for w in windows]
        mock_gui.IsWindowVisible.return_value = True
        mock_gui.IsIconic.return_value = False
        mock_gui.GetWindowText.side_effect = lambda h: by_hwnd[h][1]
        mock_gui.GetClassName.side_effect = lambda h: by_hwnd[h][2]
        mock_gui.GetWindowRect.side_effect = lambda h: by_hwnd[h][4]
        mock_gui.GetWindowLong.return_value = 0
        mock_proc_api.GetWindowThreadProcessId.side_effect = lambda h: (1, h)
        mock_psutil.Process.side_effect = lambda pid: type("P", (), {"name": lambda self, n=by_hwnd[pid][3]: n})()

        self.assertEqual(find_hwnd_by_query("chrome"), 301)

    @patch("tools.os_controls.bring_hwnd_to_foreground", return_value=True)
    @patch("tools.os_controls._get_dwm_border_insets", return_value=(7, 0, 7, 7))
    @patch("tools.os_controls._get_window_class_safe", return_value="CabinetWClass")
    @patch("tools.os_controls._resolve_target_monitor_info")
    @patch("tools.os_controls.find_hwnd_by_query")
    @patch("tools.os_controls.win32gui")
    def test_tile_windows_verifies_placement_and_fails_when_unmoved(
        self, mock_gui, mock_find, mock_mon, mock_cls, mock_insets, mock_fore
    ):
        """tile_windows must succeed when both windows verify in place and error if a window refuses to move."""
        mock_mon.return_value = (
            {"index": 1, "is_primary": True, "work_x": 0, "work_y": 0, "work_width": 2000, "work_height": 1000},
            None,
        )
        mock_find.side_effect = lambda q: 1111 if "explorer" in q.lower() else 2222
        mock_gui.GetWindowText.side_effect = lambda h: "Downloads" if h == 1111 else "Google Chrome"
        mock_gui.IsIconic.return_value = False
        mock_gui.IsZoomed.return_value = False

        # Case 1: Both windows land in target rects -> success
        with patch(
            "tools.os_controls._get_visible_window_rect",
            side_effect=lambda h: (0, 0, 1000, 1000) if h == 1111 else (1000, 0, 2000, 1000),
        ):
            res = tile_windows("file explorer", "chrome")
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["monitor"], 1)
            # Verify DWM border compensation was applied: left window shifted by -7, width +14
            first_call = mock_gui.SetWindowPos.call_args_list[0][0]
            self.assertEqual(first_call[2], -7)
            self.assertEqual(first_call[4], 1014)

        # Case 2: Left window refuses to move (stays at wrong rect) -> MUST return error!
        with patch(
            "tools.os_controls._get_visible_window_rect",
            side_effect=lambda h: (500, 500, 900, 800) if h == 1111 else (1000, 0, 2000, 1000),
        ):
            res_fail = tile_windows("file explorer", "chrome")
            self.assertEqual(res_fail["status"], "error")
            self.assertIn("Failed to split windows", res_fail["message"])

        # Case 3: Both queries resolve to the same HWND -> error
        mock_find.side_effect = lambda q: 2222
        res_same = tile_windows("file explorer", "chrome")
        self.assertEqual(res_same["status"], "error")
        self.assertIn("same window", res_same["message"])

    @patch("tools.os_controls.find_hwnd_by_query", return_value=65870)
    @patch("tools.os_controls._get_window_class_safe", return_value="Progman")
    @patch("tools.os_controls.win32gui")
    def test_close_window_refuses_protected_shell_surface(self, mock_gui, mock_cls, mock_find):
        """close_window must never post WM_CLOSE to Progman or other shell classes."""
        res = close_window("file explorer")
        self.assertEqual(res["status"], "error")
        mock_gui.PostMessage.assert_not_called()

    @patch("subprocess.run")
    @patch("core.program_manager.ProgramManager.close_program", return_value={"status": "success", "message": "Closed 1 File Explorer window(s)."})
    def test_validate_and_close_never_taskkills_explorer(self, mock_pm_close, mock_subproc):
        """WhitelistValidator.validate_and_close('file explorer') must close folder windows instead of taskkilling explorer.exe."""
        res = WhitelistValidator.validate_and_close("file explorer", ["explorer.exe"])
        self.assertEqual(res["status"], "success")
        mock_pm_close.assert_called_once_with("file_explorer")
        mock_subproc.assert_not_called()

    async def test_dispatcher_exposes_and_routes_tile_and_snap(self):
        """ToolDispatcher exposes tile_windows and snap_window as native Tier 1 core tools."""
        dispatcher = ToolDispatcher()
        core_names = {d["name"] for d in dispatcher.get_core_declarations()}
        self.assertIn("tile_windows", core_names)
        self.assertIn("snap_window", core_names)

        with patch("tools.dispatcher.tile_windows", return_value={"status": "success", "message": "Split OK"}) as mock_tile:
            res = await dispatcher.dispatch("tile_windows", {"left_app": "file explorer", "right_app": "chrome", "monitor": 2})
            self.assertEqual(res["status"], "success")
            mock_tile.assert_called_once_with("file explorer", "chrome", monitor=2)

        with patch("tools.dispatcher.snap_window", return_value={"status": "success", "message": "Snapped OK"}) as mock_snap:
            res2 = await dispatcher.dispatch("snap_window", {"app_name": "chrome", "position": "right"})
            self.assertEqual(res2["status"], "success")
            mock_snap.assert_called_once_with("chrome", position="right", monitor=None)


class TestWin11MultiMonitorInputAndShellFixes(unittest.TestCase):

    @patch("core.screen_stream.win32api")
    @patch("core.screen_stream.win32gui")
    def test_screen_stream_tracks_monitor_key_and_negative_coords(self, mock_gui, mock_api):
        """ScreenCapturePipeline.get_active_monitor_index must read 'Monitor' (not 'rcMonitor')."""
        pipe = ScreenCapturePipeline.__new__(ScreenCapturePipeline)
        pipe.monitors = [
            {"left": -1920, "top": 0, "width": 4480, "height": 1440},
            {"left": 0, "top": 0, "width": 2560, "height": 1440},
            {"left": -1920, "top": 0, "width": 1920, "height": 1080},
        ]
        mock_gui.GetForegroundWindow.return_value = 999
        mock_api.MonitorFromWindow.return_value = 22
        mock_api.GetMonitorInfo.return_value = {"Monitor": (-1920, 0, 0, 1080), "Work": (-1920, 0, 0, 1040)}

        self.assertEqual(pipe.get_active_monitor_index(), 2)

    @patch("tools.gui_primitives.ScreenCapturePipeline")
    def test_gui_primitives_lazy_capture_and_extended_keys(self, mock_pipe_cls):
        """GuiPrimitivesController must not scan MSS on init, and press_key must set EXTENDEDKEY + VkKeyScanW."""
        ctrl = GuiPrimitivesController()
        mock_pipe_cls.assert_not_called()

        sent_inputs = []

        def fake_send_input(n, ptr, size):
            # Capture the KEYBDINPUT fields directly from the ctypes pointer
            inp = ptr._obj
            sent_inputs.append((inp.ki.wVk, inp.ki.wScan, inp.ki.dwFlags))
            return 1

        with patch("ctypes.windll.user32.SendInput", side_effect=fake_send_input), \
             patch("ctypes.windll.user32.MapVirtualKeyW", return_value=0x4B), \
             patch("ctypes.windll.user32.VkKeyScanW", return_value=0x00BD):  # VK_OEM_MINUS
            res_snap = ctrl.press_key("win+left")
            self.assertEqual(res_snap["status"], "success")
            # Down events for VK_LWIN and VK_LEFT must both include KEYEVENTF_EXTENDEDKEY
            self.assertEqual(sent_inputs[0][0], VK_LWIN)
            self.assertTrue(sent_inputs[0][2] & KEYEVENTF_EXTENDEDKEY)
            self.assertEqual(sent_inputs[1][0], VK_LEFT)
            self.assertTrue(sent_inputs[1][2] & KEYEVENTF_EXTENDEDKEY)

            sent_inputs.clear()
            res_minus = ctrl.press_key("ctrl+-")
            self.assertEqual(res_minus["status"], "success")
            # Must use VK_OEM_MINUS (0xBD), NOT ord('-') == 0x2D (VK_INSERT)
            self.assertEqual(sent_inputs[1][0], 0xBD)

    @patch("win32gui.PostMessage")
    def test_media_command_no_double_toggle_when_hwnd_succeeds(self, mock_post):
        """_send_media_command must NOT also fire global VK_MEDIA_* when WM_APPCOMMAND already succeeded."""
        svc = MediaControllerService()
        with patch.object(svc, "_send_media_key", return_value=True) as mock_key:
            ok = svc._send_media_command("toggle", target_hwnd=12345)
            self.assertTrue(ok)
            mock_post.assert_called_once()
            mock_key.assert_not_called()

    @patch("subprocess.Popen")
    def test_program_manager_preserves_backslashes_and_maps_chrome_profile(self, mock_popen):
        """ProgramManager.launch_program preserves Windows paths in args and resolves Chrome profile aliases."""
        tmp_dir = tempfile.mkdtemp()
        try:
            pm = ProgramManager(programs_path=os.path.join(tmp_dir, "progs.json"))
            pm.add_program("Path Tool", path=r"C:\Windows\System32\notepad.exe", arguments=r'--file "C:\Users\Test\my doc.txt"')
            res = pm.launch_program("Path Tool")
            self.assertEqual(res["status"], "success")
            launched_cmd = mock_popen.call_args[0][0]
            self.assertIn(r"C:\Users\Test\my doc.txt", launched_cmd)

            mock_popen.reset_mock()
            with patch("os.path.exists", return_value=True), \
                 patch.object(WhitelistValidator, "get_chrome_profiles", return_value={"michael": "Profile 1"}):
                pm.add_program("Google Chrome", path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
                res_c = pm.launch_program("Google Chrome", profile="Michael")
                self.assertEqual(res_c["status"], "success")
                chrome_cmd = mock_popen.call_args[0][0]
                self.assertIn("--profile-directory=Profile 1", chrome_cmd)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @patch("win32api.EnumDisplayMonitors")
    @patch("win32api.GetMonitorInfo")
    def test_hud_monitor_work_areas_preserves_physical_mixed_dpi_origins(self, mock_info, mock_enum):
        """get_monitor_work_areas must NOT divide physical work origins by per-monitor DPI scale."""
        mock_enum.return_value = [(1, 0, (0, 0, 2560, 1440)), (2, 0, (2560, 0, 6400, 2160))]
        mock_info.side_effect = lambda h: {
            1: {"Work": (0, 0, 2560, 1400), "Flags": 1},
            2: {"Work": (2560, 0, 6400, 2100), "Flags": 0},
        }[h]
        areas = get_monitor_work_areas()
        self.assertEqual(len(areas), 2)
        # Monitor 2's left origin must remain 2560 (not divided by 1.5 -> 1706!)
        self.assertEqual(areas[1]["left"], 2560)
        self.assertEqual(areas[1]["right"], 6400)

    def test_script_runner_uses_run_script_flag_when_frozen(self):
        """ScriptRunner must invoke [AetherDesktop.exe, '--run-script', script] in frozen builds."""
        runner = ScriptRunner.__new__(ScriptRunner)
        runner.python_exe = r"C:\Aether\AetherDesktop.exe"
        runner._use_frozen_script_flag = True
        cmd = runner._build_python_cmd("tile_windows.py", {"left_app": "explorer"})
        self.assertEqual(cmd[:3], [r"C:\Aether\AetherDesktop.exe", "--run-script", "tile_windows.py"])


if __name__ == "__main__":
    unittest.main()
