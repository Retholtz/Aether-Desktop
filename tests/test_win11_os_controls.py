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

    def test_real_pywin32_is_window_maximized_and_tile_error_no_keyerror(self):
        """Real pywin32 win32gui lacks IsZoomed; _is_window_maximized must use user32.IsZoomed without AttributeError."""
        import win32gui as real_win32gui
        from tools import os_controls

        self.assertFalse(hasattr(real_win32gui, "IsZoomed"))
        fore = real_win32gui.GetForegroundWindow()
        # Must return bool cleanly on real Win32 HWND without raising AttributeError
        res_max = os_controls._is_window_maximized(fore)
        self.assertIsInstance(res_max, bool)

        # Even when _place_window_verified returns an early error, actual_rect is present (no KeyError in tile_windows)
        with patch.object(os_controls, "find_hwnd_by_query", side_effect=[101, 202]), \
             patch.object(os_controls, "_resolve_target_monitor_info", return_value=({"index": 1, "work_x": 0, "work_y": 0, "work_width": 1920, "work_height": 1040}, None)), \
             patch.object(os_controls, "_get_window_class_safe", return_value="Progman"), \
             patch("win32gui.GetWindowText", return_value="Test"):
            res_tile = os_controls.tile_windows("chrome", "explorer")
            self.assertEqual(res_tile["status"], "error")
            self.assertIn("left_rect", res_tile)
            self.assertIsNone(res_tile["left_rect"])

    def test_play_music_resolves_direct_watch_url_and_verifies_playback(self):
        """play_music resolves direct YouTube Music watch URL in 1 step and verifies via SMTC."""
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from tools.dispatcher import ToolDispatcher
        from tools.media_tools import register_media_tools

        disp = ToolDispatcher.__new__(ToolDispatcher)
        disp._custom_declarations = {}
        disp._custom_handlers = {}
        disp._active_scopes = []

        fake_media = MagicMock()
        track_info = SimpleNamespace(
            title="Closer Than Fear",
            artist="Nyra Noir",
            playback_status="playing",
            to_dict=lambda: {"title": "Closer Than Fear", "artist": "Nyra Noir", "playback_status": "playing"},
        )
        fake_media.get_current_media_info = AsyncMock(return_value=track_info)
        fake_media.execute_transport_command = AsyncMock(return_value=(True, "ok"))

        register_media_tools(disp, fake_media)
        self.assertIn("play_music", disp._custom_handlers)

        with patch("tools.media_tools._resolve_youtube_video_id", return_value="V7ywftWI3iI"), \
             patch("tools.media_tools.navigate_browser", return_value={"status": "success"}) as mock_nav, \
             patch("tools.media_tools.asyncio.sleep", new=AsyncMock()):
            res = asyncio.run(disp._custom_handlers["play_music"](query="Nyra Noir", service_name="youtube_music"))

        self.assertEqual(res["status"], "success")
        self.assertTrue(res["playback_verified"])
        self.assertEqual(res["url"], "https://music.youtube.com/watch?v=V7ywftWI3iI")
        mock_nav.assert_called_once()

    def test_find_and_click_element_uses_low_latency_config_and_captured_monitor(self):
        """find_and_click_element passes low-latency GenerateContentConfig and captured monitor index to click()."""
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from tools.dispatcher import ToolDispatcher

        disp = ToolDispatcher.__new__(ToolDispatcher)
        disp.on_event = None
        disp.config_getter = lambda: {"tier2_moderate_model": "gemini-4-neon:high"}
        disp.screen_pipeline = MagicMock()
        disp.screen_pipeline.capture_frame = AsyncMock(return_value=(b"\xff\xd8\xff", {"monitor_index": 2, "monitor_rect": {"left": 2560, "top": 0, "width": 1920, "height": 1080}}))
        disp.gui_controller = MagicMock()
        disp.gui_controller.click.return_value = {"status": "success", "coords": (3520, 540)}
        disp.genai_client = MagicMock()
        disp.genai_client.models.generate_content.return_value = SimpleNamespace(
            text='[{"box_2d": [400, 400, 600, 600], "label": "Play"}]'
        )

        with patch("tools.dispatcher.focus_window"):
            res = asyncio.run(disp.find_and_click_element("Play button", app_name="chrome"))

        self.assertEqual(res["status"], "success")
        # Passed low-latency config (temperature=0.0, thinking_level='low')
        gen_kwargs = disp.genai_client.models.generate_content.call_args.kwargs
        self.assertIn("config", gen_kwargs)
        self.assertEqual(gen_kwargs["config"].temperature, 0.0)
        # Clicked on exact captured monitor #2
        self.assertEqual(disp.gui_controller.click.call_args.kwargs["monitor"], 2)

    def test_live_tool_circuit_breaker_and_no_premature_listening_during_tool_loop(self):
        """Live loop must stop retrying identical failing tools and NOT emit 'listening' on empty mid-tool turn_complete."""
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from core.engine import AetherEngine

        eng = AetherEngine.__new__(AetherEngine)
        eng.is_running = True
        eng._stop_requested = False
        eng._kill_playback_flag = False
        eng._is_transcribing = False
        eng._is_audio_streaming = False
        eng._in_active_turn = False
        eng.is_tool_executing = False
        eng._pending_tool_response_until = 0.0
        eng._macro_fired_in_current_turn = False
        eng._last_fast_path_macro_ts = 0.0
        eng._session_resumption_handle = None
        eng._reset_context_flag = False
        eng.audio = None
        eng.conn_mgr = MagicMock()
        eng.session_lifecycle = MagicMock()
        eng.session_lifecycle.needs_rotation.return_value = False
        eng.session_lifecycle.rotation_in_progress = False
        eng.dispatcher = MagicMock()
        eng.dispatcher.dispatch = AsyncMock(return_value={"status": "error", "message": "Boom"})

        status_events = []
        eng.notify = lambda et, data: status_events.append((et, data)) if et == "status" else None

        sent_responses = []

        class FakeSession:
            async def send_tool_response(self, function_responses):
                sent_responses.extend(function_responses)

            async def receive(self):
                # 3 identical failing tool calls + empty mid-tool turn_complete between them, then final spoken turn_complete
                for i in range(3):
                    yield SimpleNamespace(
                        tool_call=SimpleNamespace(
                            function_calls=[SimpleNamespace(id=f"fc_{i}", name="snap_window", args={"app_name": "chrome", "position": "left"})]
                        ),
                        session_resumption_update=None,
                        go_away=None,
                        server_content=None,
                    )
                    # Empty turn_complete right after tool response (must NOT emit 'listening'!)
                    yield SimpleNamespace(
                        tool_call=None,
                        session_resumption_update=None,
                        go_away=None,
                        server_content=SimpleNamespace(
                            input_transcription=None,
                            model_turn=None,
                            output_transcription=None,
                            interrupted=False,
                            turn_complete=True,
                        ),
                    )
                eng.is_running = False

        sess = FakeSession()
        eng.session = sess
        asyncio.run(eng._async_receive_loop(sess, agent_name="Aether", kill_phrase="Aether stop"))

        # Circuit breaker must have dispatched only TWICE and short-circuited the 3rd call!
        self.assertEqual(eng.dispatcher.dispatch.call_count, 2)
        self.assertEqual(len(sent_responses), 3)
        self.assertTrue(sent_responses[2].response.get("circuit_breaker"))
        # Must NEVER have emitted premature 'listening' during the active tool loop!
        listening_events = [d for _, d in status_events if d.get("state") == "listening"]
        self.assertEqual(listening_events, [])

    def test_hud_hidpi_overlay_never_double_scales_offscreen_and_uses_native_drag(self):
        """
        On a 2560x1600 @ 150% DPI display, move_overlay_physical & reset_overlay_position
        must keep the overlay within physical bounds (never multiplying 1984 * 1.5 = 2976 offscreen),
        and overlay.html must delegate drag to native start_drag().
        """
        from ui import hud_window

        hidpi_monitors = [{
            "left": 0, "top": 0, "right": 2560, "bottom": 1528,
            "scale": 1.5, "is_primary": True,
        }]

        with patch.object(hud_window, "get_monitor_work_areas", return_value=hidpi_monitors):
            # 1. Default top-right for 176x56 logical (264x84 physical) at 150% DPI
            phys_w, phys_h = int(round(176 * 1.5)), int(round(56 * 1.5))
            cx, cy = hud_window.clamp_window_position(None, None, width=phys_w, height=phys_h)
            self.assertEqual(cx, 2560 - 264 - 24)  # 2272 physical
            self.assertLessEqual(cx + phys_w, 2560)

            # 2. Offscreen coordinates (e.g. 4000, -500) clamp safely back inside 2560x1528
            rx, ry = hud_window.clamp_window_position(4000, -500, width=phys_w, height=phys_h)
            self.assertLessEqual(rx + phys_w, 2560)
            self.assertGreaterEqual(ry, 16)

            # 3. move_overlay_physical uses direct Win32 SetWindowPos with exact physical coords (no 1.5x double-scale)
            fake_win = MagicMock()
            fake_win.native = MagicMock()
            fake_win.native.Handle.ToInt64.return_value = 987654
            with patch("ctypes.windll.user32.SetWindowPos", return_value=1) as mock_swp:
                ok = hud_window.move_overlay_physical(fake_win, cx, cy)
                self.assertTrue(ok)
                args = mock_swp.call_args.args
                self.assertEqual(args[0], 987654)
                self.assertEqual(args[2], cx)
                self.assertEqual(args[3], cy)
                # Must NOT have called pywebview's double-scaling window.move()!
                fake_win.move.assert_not_called()

            # 4. Fallback when no HWND exists converts physical -> logical for pywebview window.move()
            mock_no_hwnd_win = MagicMock()
            mock_no_hwnd_win.native = None
            with patch.object(hud_window, "_get_overlay_hwnd", return_value=None):
                ok2 = hud_window.move_overlay_physical(mock_no_hwnd_win, 2272, 24)
                self.assertTrue(ok2)
                mock_no_hwnd_win.move.assert_called_once_with(int(round(2272 / 1.5)), int(round(24 / 1.5)))

        # 5. Verify overlay.html delegates drag to native start_drag()
        overlay_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "static", "overlay.html")
        with open(overlay_path, "r", encoding="utf-8") as f:
            overlay_src = f.read()
        self.assertIn("window.pywebview.api.start_drag()", overlay_src)


if __name__ == "__main__":
    unittest.main()
