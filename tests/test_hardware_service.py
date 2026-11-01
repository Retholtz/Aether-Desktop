import asyncio
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
from core.hardware_service import (
    HardwareService,
    HardwareSnapshot,
    AmdAdlBackend,
    WindowsGpuFallback,
    CpuMonitorBackend
)
from tools.hardware_tools import register_hardware_tools
from core.game_engine import GameEngine, GameInterceptionResult


class TestHardwareService(unittest.TestCase):
    def setUp(self):
        self.service = HardwareService(presentmon_path="assets/bin/PresentMon.exe")

    def tearDown(self):
        self.service.close()

    @patch("core.hardware_service.PYNVML_AVAILABLE", True)
    @patch("core.hardware_service.pynvml")
    @patch("core.hardware_service.psutil")
    def test_get_snapshot_calculations(self, mock_psutil, mock_nvml):
        # Mock CPU & RAM
        mock_psutil.cpu_percent.return_value = 25.5
        mock_ram = MagicMock()
        mock_ram.total = 32 * (1024 ** 3)
        mock_ram.available = 16 * (1024 ** 3)
        mock_psutil.virtual_memory.return_value = mock_ram

        # Mock GPU & VRAM
        self.service._nvml_initialized = True
        self.service._gpu_backend = "nvml"
        self.service._gpu_handle = MagicMock()
        mock_nvml.nvmlDeviceGetTemperature.return_value = 68
        mock_util = MagicMock()
        mock_util.gpu = 88
        mock_nvml.nvmlDeviceGetUtilizationRates.return_value = mock_util
        mock_mem = MagicMock()
        mock_mem.used = 12 * (1024 ** 3)
        mock_mem.total = 16 * (1024 ** 3)
        mock_nvml.nvmlDeviceGetMemoryInfo.return_value = mock_mem

        # Mock CPU Backend
        with patch.object(self.service._cpu_backend, "query", return_value={
            "cpu_name": "Intel Core Ultra 9 275HX",
            "temp_c": 72,
            "load_pct": 25.5,
            "clock_ghz": 4.8,
            "power_w": 28.5
        }):
            # Mock Rolling Frametimes (average 16.66ms -> 60 FPS)
            self.service._frame_times.extend([16.66] * 60)
            self.service._monitored_process_name = "CrimsonDesert.exe"

            snap = self.service.get_snapshot()

            self.assertEqual(snap.cpu_name, "Intel Core Ultra 9 275HX")
            self.assertEqual(snap.cpu_temp_c, 72)
            self.assertEqual(snap.cpu_clock_ghz, 4.8)
            self.assertEqual(snap.cpu_power_w, 28.5)
            self.assertEqual(snap.gpu_temp_c, 68)
            self.assertEqual(snap.gpu_utilization_pct, 88)
            self.assertAlmostEqual(snap.vram_used_gb, 12.0, places=1)
            self.assertAlmostEqual(snap.vram_pct, 75.0, places=1)
            self.assertEqual(snap.fps_avg, 60)
            self.assertEqual(snap.active_game_process, "CrimsonDesert.exe")

    @patch("asyncio.create_subprocess_exec")
    @patch("os.path.exists", return_value=True)
    def test_start_and_stop_presentmon(self, mock_exists, mock_exec):
        mock_proc = AsyncMock()
        mock_proc.pid = 4321
        mock_exec.return_value = mock_proc

        # Start
        asyncio.run(self.service.start_fps_monitoring("EliteDangerous64.exe"))
        self.assertIsNotNone(self.service._presentmon_proc)
        self.assertEqual(self.service._monitored_process_name, "EliteDangerous64.exe")

        # Stop
        asyncio.run(self.service.stop_fps_monitoring())
        self.assertIsNone(self.service._presentmon_proc)
        self.assertIsNone(self.service._monitored_process_name)

    def test_check_alerts(self):
        with patch.object(self.service, "get_snapshot") as mock_snap:
            mock_snap.return_value = HardwareSnapshot(
                cpu_temp_c=94,
                gpu_temp_c=90,
                vram_pct=96.0
            )
            alerts = self.service.check_alerts(vram_threshold=95.0, temp_threshold=85.0, cpu_temp_threshold=90.0)
            self.assertEqual(len(alerts), 3)
            self.assertTrue(any("cpu temperature" in a.lower() for a in alerts))
            self.assertTrue(any("gpu temperature" in a.lower() for a in alerts))
            self.assertTrue(any("vram" in a.lower() for a in alerts))

    def test_tool_registration_and_execution(self):
        dispatcher = MagicMock()
        registered_handlers = {}

        def mock_reg(name, *args, **kwargs):
            if "handler" in kwargs:
                registered_handlers[name] = kwargs["handler"]
            elif len(args) >= 2:
                registered_handlers[name] = args[1]

        dispatcher.register_tool.side_effect = mock_reg
        register_hardware_tools(dispatcher, self.service)

        self.assertIn("get_hardware_telemetry", registered_handlers)
        handler = registered_handlers["get_hardware_telemetry"]

        with patch.object(self.service, "get_snapshot") as mock_snap:
            mock_snap.return_value = HardwareSnapshot(
                cpu_name="Intel Core Ultra 9 275HX",
                cpu_temp_c=75,
                cpu_load_pct=15.0,
                cpu_clock_ghz=5.0,
                gpu_name="NVIDIA GeForce RTX 5080 Laptop GPU",
                gpu_temp_c=70,
                gpu_utilization_pct=95,
                vram_used_gb=8.0,
                vram_total_gb=16.0,
                vram_pct=50.0,
                fps_avg=120,
                fps_1pct_low=95
            )
            res = asyncio.run(handler())
            self.assertEqual(res["status"], "success")
            self.assertIn("CPU (Intel Core Ultra 9 275HX): 75°C", res["spoken_summary"])
            self.assertIn("GPU (NVIDIA GeForce RTX 5080 Laptop GPU): 70°C", res["spoken_summary"])
            self.assertIn("Framerate: 120 FPS", res["spoken_summary"])
            self.assertEqual(res["telemetry"]["cpu_name"], "Intel Core Ultra 9 275HX")
            self.assertEqual(res["telemetry"]["cpu_temp_c"], 75)
            self.assertEqual(res["telemetry"]["gpu_name"], "NVIDIA GeForce RTX 5080 Laptop GPU")

    def test_game_engine_voice_fast_path(self):
        ge = GameEngine()
        self.assertIn("get_hardware_telemetry", GameEngine.CORE_GAME_TOOL_NAMES)

        queries = [
            "how are my temps",
            "what's my fps",
            "how's my gpu",
            "what is my fps",
            "check fps",
            "hardware status",
            "how is my cpu",
            "cpu thermals",
            "what is the cpu sitting at right now",
            "what is a thermals in",
            "what's my frame per second",
            "what are our thermals",
            "check framerate"
        ]
        for q in queries:
            result = ge.intercept_voice_turn(q)
            self.assertTrue(result.handled, f"Query '{q}' should be handled")
            self.assertEqual(result.action_type, "hardware_telemetry")
            self.assertTrue(result.skip_llm)

    def test_gui_bridge_toggle_fps(self):
        from core.gui_bridge import GuiBridge
        with patch.object(GuiBridge, "_load_config", return_value={}), \
             patch("core.gui_bridge.AetherEngine") as mock_engine_cls:
            mock_engine = MagicMock()
            bridge = GuiBridge(config_path="config.json", engine=mock_engine)

            mock_profiles = {
                "crimson_desert": {
                    "display_name": "Crimson Desert",
                    "executable": "CrimsonDesert.exe",
                    "fps_monitoring": True
                },
                "elite_dangerous_64": {
                    "display_name": "Elite Dangerous 64",
                    "executable": "EliteDangerous64.exe",
                    "fps_monitoring": False
                }
            }
            bridge.game_mgr.get_profile = MagicMock(side_effect=lambda gid: mock_profiles.get(gid))
            bridge.game_mgr.save_profiles = MagicMock()
            bridge.game_mgr.save_profile = MagicMock()

            # Toggle Crimson Desert to False
            res1 = bridge.toggle_game_fps_monitoring("crimson_desert", False)
            self.assertEqual(res1["status"], "success")
            self.assertFalse(res1["fps_monitoring"])

            # Toggle Elite Dangerous 64 to True
            res2 = bridge.toggle_game_fps_monitoring("elite_dangerous_64", True)
            self.assertEqual(res2["status"], "success")
            self.assertTrue(res2["fps_monitoring"])

            # Toggle using string "false"
            res3 = bridge.toggle_game_fps_monitoring("elite_dangerous_64", "false")
            self.assertEqual(res3["status"], "success")
            self.assertFalse(res3["fps_monitoring"])

            # Fallback alias lookup (elite_dangerous -> elite_dangerous_64)
            res4 = bridge.toggle_game_fps_monitoring("elite_dangerous", True)
            self.assertEqual(res4["status"], "success")
            self.assertTrue(res4["fps_monitoring"])

    def test_engine_on_game_mode_changed_sync(self):
        from core.engine import AetherEngine
        with patch.object(AetherEngine, "__init__", return_value=None):
            engine = AetherEngine()
            engine.hardware_service = MagicMock()
            engine.hardware_service.start_fps_monitoring = AsyncMock()
            engine.hardware_service.stop_fps_monitoring = AsyncMock()
            engine.game_engine = MagicMock()
            engine.game_engine.manager.get_profile.return_value = {
                "display_name": "Crimson Desert",
                "executable": "CrimsonDesert.exe",
                "fps_monitoring": True
            }

            async def _run_test():
                # Test enable with running game
                engine._on_game_mode_changed(enabled=True, game_id="crimson_desert", game_name="Crimson Desert", is_running=True)
                await asyncio.sleep(0.01)
                engine.hardware_service.start_fps_monitoring.assert_called_with("CrimsonDesert.exe")

                # Test disable
                engine._on_game_mode_changed(enabled=False, game_id="crimson_desert", game_name="Crimson Desert", is_running=False)
                await asyncio.sleep(0.01)
                engine.hardware_service.stop_fps_monitoring.assert_called()

            asyncio.run(_run_test())

    def test_amd_adl_and_windows_gpu_fallback(self):
        # Test AmdAdlBackend mock
        adl = AmdAdlBackend()
        adl.initialized = True
        adl.gpu_name = "AMD Radeon RX 7900 XTX"
        mock_adl_data = {"temp_c": 62, "util_pct": 75, "vram_used_gb": 16.0, "vram_total_gb": 24.0}
        with patch.object(adl, "query", return_value=mock_adl_data):
            res = adl.query()
            self.assertEqual(res["temp_c"], 62)
            self.assertEqual(res["util_pct"], 75)
            self.assertEqual(res["vram_used_gb"], 16.0)
            self.assertEqual(res["vram_total_gb"], 24.0)

        # Test WindowsGpuFallback mock
        fallback = WindowsGpuFallback()
        fallback.initialized = True
        fallback.gpu_name = "Intel Arc A770 Graphics"
        fallback.total_vram_gb = 16.0
        mock_fallback_data = {"temp_c": 0, "util_pct": 60, "vram_used_gb": 8.5, "vram_total_gb": 16.0}
        with patch.object(fallback, "query", return_value=mock_fallback_data):
            res = fallback.query()
            self.assertEqual(res["util_pct"], 60)
            self.assertEqual(res["vram_used_gb"], 8.5)
            self.assertEqual(res["vram_total_gb"], 16.0)

    def test_hardware_service_fallback_chain(self):
        # When NVML is disabled, service should fall back to AMD or Windows fallback
        svc = HardwareService(presentmon_path="assets/bin/PresentMon.exe")
        svc._nvml_initialized = False
        svc._gpu_backend = "amd_adl"
        svc._amd_backend.initialized = True
        svc._amd_backend.gpu_name = "AMD Radeon RX 6800 XT"
        mock_amd_data = {"temp_c": 65, "util_pct": 80, "vram_used_gb": 10.0, "vram_total_gb": 16.0}
        with patch.object(svc._amd_backend, "query", return_value=mock_amd_data):
            snap = svc.get_snapshot()
            self.assertEqual(snap.gpu_name, "AMD Radeon RX 6800 XT")
            self.assertEqual(snap.gpu_temp_c, 65)
            self.assertEqual(snap.gpu_utilization_pct, 80)
            self.assertEqual(snap.vram_used_gb, 10.0)
            self.assertEqual(snap.vram_total_gb, 16.0)
        svc.close()

    def test_cpu_monitor_backend_query(self):
        cpu = CpuMonitorBackend()
        data = cpu.query()
        self.assertIn("cpu_name", data)
        self.assertIn("load_pct", data)
        self.assertIsInstance(data["load_pct"], float)
        cpu.close()

    @patch("subprocess.Popen")
    @patch("os.path.exists", return_value=True)
    def test_sync_presentmon_and_hud_telemetry(self, mock_exists, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 9999
        mock_proc.poll.return_value = None
        mock_proc.stdout.readline.side_effect = [
            "Application,ProcessID,SwapChainAddress,PresentRuntime,SyncInterval,PresentFlags,AllowsTearing,PresentMode,TimeInSeconds,MsBetweenDisplayChange,MsBetweenPresents\n",
            "CrimsonDesert.exe,1234,0x1,DXGI,1,0,0,Hardware: Independent Flip,1.0,8.33,8.333\n",
            "CrimsonDesert.exe,1234,0x1,DXGI,1,0,0,Hardware: Independent Flip,1.01,8.33,8.333\n",
            ""
        ]
        mock_popen.return_value = mock_proc

        self.service.start_fps_monitoring_sync("CrimsonDesert.exe")
        if self.service._presentmon_thread:
            self.service._presentmon_thread.join(timeout=1.0)

        metrics = self.service.get_fps_metrics()
        self.assertEqual(metrics["active_game_process"], "CrimsonDesert.exe")
        self.assertEqual(metrics["fps_avg"], 120)

        self.service.stop_fps_monitoring_sync()
        self.assertIsNone(self.service._monitored_process_name)

    def test_hud_bridge_dynamic_mini_pill_width(self):
        from ui.hud_window import HUDBridge
        hud = HUDBridge(window=None, bridge=MagicMock())
        self.assertEqual(hud._mini_size, (176, 56))
        hud.set_mini_fps_visible(True)
        self.assertEqual(hud._mini_size, (245, 56))
        hud.set_mini_fps_visible(False)
        self.assertEqual(hud._mini_size, (176, 56))

    def test_update_alert_settings_and_transient_spike_filtering(self):
        received = []
        self.service.alert_callback = lambda msg: received.append(msg)
        self.service.update_alert_settings(
            alerts_enabled=True,
            cpu_temp_threshold_c=95,
            gpu_temp_threshold_c=88,
            vram_threshold_pct=96
        )
        self.assertTrue(self.service.alerts_enabled)
        self.assertEqual(self.service.cpu_temp_alert_threshold_c, 95.0)
        self.assertEqual(self.service.gpu_temp_alert_threshold_c, 88.0)
        self.assertEqual(self.service.vram_alert_threshold_pct, 96.0)

        # Simulate transient 96C spike (2 polls) followed by 80C recovery -> should NOT fire alert
        snaps = [
            HardwareSnapshot(cpu_temp_c=96, gpu_temp_c=70, vram_pct=50.0),
            HardwareSnapshot(cpu_temp_c=96, gpu_temp_c=70, vram_pct=50.0),
            HardwareSnapshot(cpu_temp_c=80, gpu_temp_c=70, vram_pct=50.0),
            # Then 3 consecutive sustained polls at 96C -> SHOULD fire once
            HardwareSnapshot(cpu_temp_c=96, gpu_temp_c=70, vram_pct=50.0),
            HardwareSnapshot(cpu_temp_c=97, gpu_temp_c=70, vram_pct=50.0),
            HardwareSnapshot(cpu_temp_c=96, gpu_temp_c=70, vram_pct=50.0),
        ]
        idx = {"i": 0}

        def next_snap():
            i = idx["i"]
            idx["i"] += 1
            if i >= len(snaps):
                self.service._alert_running = False
                return snaps[-1]
            if i == len(snaps) - 1:
                self.service._alert_running = False
            return snaps[i]

        self.service._alert_running = True
        with patch.object(self.service, "get_snapshot", side_effect=next_snap):
            asyncio.run(self.service._poll_hardware_alerts(0.001))

        self.assertEqual(len(received), 1)
        self.assertIn("CPU temperature is high at 96°C (Threshold: 95°C)", received[0])


if __name__ == "__main__":
    unittest.main()
