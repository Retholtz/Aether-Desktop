import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np

from core.telemetry.schema import TelemetryFrame, GameTelemetryProfile, HudBarRegion
from core.telemetry.providers.file_provider import NativeFileTelemetryProvider
from core.telemetry.providers.vision_hud_provider import VisionHudTelemetryProvider
from core.telemetry.copilot_gate import CopilotEventGate
from core.telemetry.telemetry_service import TelemetryService


class TestAgnosticTelemetryProvider(unittest.TestCase):
    def test_elite_dangerous_status_parsing(self):
        """Verify native Status.json bitmasks decode shields_offline, overheating, and in_combat."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_file = Path(tmpdir) / "Status.json"
            # Flags: Overheating (1<<20) + InDanger (1<<30), ShieldsUp (1<<16) is 0
            flags_val = (1 << 20) | (1 << 30)
            status_file.write_text(json.dumps({
                "Flags": flags_val,
                "Fuel": {"FuelMain": 14.5},
                "Pips": [2, 4, 6]
            }), encoding="utf-8")

            profile = GameTelemetryProfile(
                game_id="elite_dangerous_64",
                provider_type="native_file",
                native_file_path=str(status_file)
            )
            provider = NativeFileTelemetryProvider(profile)
            frame = provider.poll()

            self.assertIsNotNone(frame)
            self.assertEqual(frame.vitals["shield_pct"], 0.0)
            self.assertIn("shields_offline", frame.states)
            self.assertIn("overheating", frame.states)
            self.assertIn("in_combat", frame.states)
            self.assertEqual(frame.resources["fuel_tons"], 14.5)
            self.assertEqual(frame.resources["pips"], [2, 4, 6])

    def test_vision_hud_bar_fill_sampling(self):
        """Verify VisionHudTelemetryProvider measures partial and full bar ratios from BGRA pixels."""
        profile = GameTelemetryProfile(
            game_id="crimson_desert",
            provider_type="vision_hud",
            hud_bars=[
                HudBarRegion(name="health", bbox_pct=(0.04, 0.05, 0.18, 0.015), color_profile="red"),
                HudBarRegion(name="stamina", bbox_pct=(0.04, 0.072, 0.15, 0.012), color_profile="green"),
            ]
        )
        provider = VisionHudTelemetryProvider(profile)

        # Synthetic 10x100 BGRA frames:
        # Health: left 40 columns bright red (BGR: 10, 10, 220), right 60 columns dark gray
        hp_img = np.zeros((10, 100, 4), dtype=np.uint8)
        hp_img[:, :40, :3] = [10, 10, 220]
        hp_img[:, 40:, :3] = [30, 30, 30]

        # Stamina: left 75 columns bright green (BGR: 10, 210, 10), right 25 columns dark gray
        stam_img = np.zeros((10, 100, 4), dtype=np.uint8)
        stam_img[:, :75, :3] = [10, 210, 10]
        stam_img[:, 75:, :3] = [30, 30, 30]

        mock_sct = MagicMock()
        mock_sct.monitors = [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
        ]
        mock_sct.grab.side_effect = [hp_img, stam_img]
        provider._sct = mock_sct

        frame = provider.poll()
        self.assertIsNotNone(frame)
        self.assertAlmostEqual(frame.vitals["health_pct"], 0.4, places=2)
        self.assertAlmostEqual(frame.vitals["stamina_pct"], 0.75, places=2)

        # Title screen / Map screen with 0 matching bar pixels returns None instead of 0.0% health
        empty_img = np.zeros((10, 100, 4), dtype=np.uint8)
        mock_sct.grab.side_effect = [empty_img, empty_img]
        self.assertIsNone(provider.poll())

    def test_title_screen_and_map_occlusion_do_not_false_trigger(self):
        """Verify 0% or uncalibrated readings before baseline (or instant map occlusion) do not false-fire."""
        gate = CopilotEventGate(global_cooldown_sec=10.0)

        # Turning telemetry ON at Title Screen (0% or 25% noise before any healthy baseline seen) -> Silent
        self.assertIsNone(gate.evaluate(TelemetryFrame(game_id="crimson_desert", timestamp=10.0, vitals={"health_pct": 0.0})))
        self.assertIsNone(gate.evaluate(TelemetryFrame(game_id="crimson_desert", timestamp=11.0, vitals={"health_pct": 0.25})))

        # Healthy baseline established (85%) -> Instant drop to 0.02 when opening Map -> Suppressed as UI occlusion
        self.assertIsNone(gate.evaluate(TelemetryFrame(game_id="crimson_desert", timestamp=20.0, vitals={"health_pct": 0.85})))
        self.assertIsNone(gate.evaluate(TelemetryFrame(game_id="crimson_desert", timestamp=21.0, vitals={"health_pct": 0.02})))

    def test_per_game_callsign_and_silent_macro(self):
        """Verify per-game ';' callsign rotation, blank callsign directive, and silent fast-path macro."""
        from core.game_manager import GameManager
        from core.game_engine import GameEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            pfile = Path(tmpdir) / "profiles.json"
            mgr = GameManager(profiles_path=str(pfile))
            mgr.add_game("Crimson Desert", "CrimsonDesert.exe")
            mgr.set_keybind("crimson_desert", "open map", "m", description="Open world map")
            mgr.set_active_profile("crimson_desert")

            # Blank callsign -> explicitly forbids calling out 'Commander' or any title
            mgr.set_callsign("crimson_desert", "")
            blank_dir = mgr.build_game_callsign_directive(mgr.get_profile("crimson_desert"), "crimson_desert")
            self.assertIn("BLANK", blank_dir)
            self.assertIn("NEVER call them 'Commander'", blank_dir)

            # Multiple ';' callsigns with toggle OFF -> random alternation
            mgr.set_callsign("crimson_desert", "Michael; Mike; Commander; Chief")
            mgr.set_multiple_main_characters("crimson_desert", False)
            rand_dir = mgr.build_game_callsign_directive(mgr.get_profile("crimson_desert"), "crimson_desert")
            self.assertIn("Randomly alternate between", rand_dir)
            self.assertIn("'Michael', 'Mike', 'Commander', 'Chief'", rand_dir)

            # Multiple ';' callsigns with Multiple Main Characters toggle ON -> vision-matched
            mgr.set_callsign("crimson_desert", "Kliff; Oongka; Damiane")
            mgr.set_multiple_main_characters("crimson_desert", True)
            multi_dir = mgr.build_game_callsign_directive(mgr.get_profile("crimson_desert"), "crimson_desert")
            self.assertIn("VISION MATCHED", multi_dir)
            self.assertIn("'Kliff', 'Oongka', 'Damiane'", multi_dir)
            self.assertIn("Do NOT randomly pick a different protagonist", multi_dir)

            # Fast-path macro is silent (tts_message is None)
            ge = GameEngine(profiles_path=str(pfile))
            with patch("core.game_manager.send_directinput_key", create=True), \
                 patch("core.game_macro_runner.send_directinput_key", create=True):
                res = ge.intercept_voice_turn("Open map")
                self.assertTrue(res.handled)
                self.assertEqual(res.action_type, "fast_path_macro")
                self.assertIsNone(res.tts_message)
            ge.stop()
            mgr.close()

    def test_copilot_gate_hysteresis_and_cooldown(self):
        """Verify health threshold fires once below 30% and resets only above 50%."""
        gate = CopilotEventGate(global_cooldown_sec=10.0)

        # Frame 1: Healthy (80%) -> Silent
        res1 = gate.evaluate(TelemetryFrame(game_id="crimson_desert", timestamp=100.0, vitals={"health_pct": 0.80}))
        self.assertIsNone(res1)

        # Frame 2: Drops to 25% -> Fires critical alert
        res2 = gate.evaluate(TelemetryFrame(game_id="crimson_desert", timestamp=101.0, vitals={"health_pct": 0.25}))
        self.assertIsNotNone(res2)
        self.assertEqual(res2["key"], "low_health")

        # Frame 3: Still at 22% 30 seconds later -> Suppressed by hysteresis
        res3 = gate.evaluate(TelemetryFrame(game_id="crimson_desert", timestamp=135.0, vitals={"health_pct": 0.22}))
        self.assertIsNone(res3)

        # Frame 4: Heals to 60% (resets hysteresis), then drops to 20% -> Fires again
        gate.evaluate(TelemetryFrame(game_id="crimson_desert", timestamp=140.0, vitals={"health_pct": 0.60}))
        res4 = gate.evaluate(TelemetryFrame(game_id="crimson_desert", timestamp=170.0, vitals={"health_pct": 0.20}))
        self.assertIsNotNone(res4)

    def test_copilot_gate_state_transitions_and_critical_bypass(self):
        """Verify state onset triggers and critical alerts bypass standard 12s cooldown after 4s."""
        gate = CopilotEventGate(global_cooldown_sec=12.0)

        # Non-critical low_fuel triggers at t=100
        res1 = gate.evaluate(TelemetryFrame(game_id="elite_dangerous_64", timestamp=100.0, states=["low_fuel"]))
        self.assertIsNotNone(res1)
        self.assertEqual(res1["key"], "state_low_fuel")

        # Critical shields_offline at t=102 (< 4s) is suppressed
        res2 = gate.evaluate(TelemetryFrame(game_id="elite_dangerous_64", timestamp=102.0, states=["low_fuel", "shields_offline"]))
        self.assertIsNone(res2)

        # Critical overheating at t=105 (> 4s, < 12s) bypasses standard cooldown
        res3 = gate.evaluate(TelemetryFrame(game_id="elite_dangerous_64", timestamp=105.0, states=["low_fuel", "shields_offline", "overheating"]))
        self.assertIsNotNone(res3)
        self.assertEqual(res3["key"], "state_overheating")

    def test_gui_bridge_toggle_and_engine_live_injection(self):
        """Verify GuiBridge.toggle_game_telemetry toggles service and Engine injects into Flash Live."""
        from core.gui_bridge import GuiBridge
        from core.engine import AetherEngine

        with patch.object(AetherEngine, "__init__", return_value=None):
            engine = AetherEngine()
            engine.audio = None
            engine.is_running = True
            engine.is_speaking = False
            engine._is_speaking = False
            engine.session = MagicMock()
            engine.session.send_client_content = AsyncMock()
            engine.telemetry_service = TelemetryService(on_copilot_trigger=engine.inject_tactical_alert)

            async def _run():
                await engine.inject_tactical_alert("Co-pilot alert: Health critical at 20%.")
                engine.session.send_client_content.assert_awaited_once()

            asyncio.run(_run())

        with patch("core.gui_bridge.AetherEngine", side_effect=lambda *a, **kw: MagicMock()):
            bridge = GuiBridge(config_path="config.json")
            bridge.game_mgr = MagicMock()
            profile_dict = {"display_name": "Crimson Desert", "telemetry_enabled": False}
            bridge.game_mgr.get_profile.return_value = profile_dict
            bridge._engine = MagicMock()
            bridge._engine._loop = None
            bridge._engine.telemetry_service = TelemetryService()

            res_on = bridge.toggle_game_telemetry("crimson_desert", True)
            self.assertTrue(res_on["success"])
            self.assertTrue(res_on["telemetry_enabled"])
            self.assertTrue(profile_dict["telemetry_enabled"])
            self.assertTrue(bridge._engine.telemetry_service.enabled)

            res_off = bridge.toggle_game_telemetry("crimson_desert", False)
            self.assertTrue(res_off["success"])
            self.assertFalse(res_off["telemetry_enabled"])
            self.assertFalse(profile_dict["telemetry_enabled"])
            self.assertFalse(bridge._engine.telemetry_service.enabled)


if __name__ == "__main__":
    unittest.main()
