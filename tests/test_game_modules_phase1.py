"""
Phase 1 Game Companion Overhaul Tests:
- Pure-JSON Game Modules (`core/game_modules.py` + `data/game_modules/*.aethermodule.json`)
- Schema-driven NativeFileTelemetryProvider (fixed Frontier bitmasks, Flags2, NavRoute/Cargo, custom games)
- EliteTelemetryAdapter bitmask & companion file fixes
- Generic CopilotEventGate & TelemetryService 3rd-party alert suppression (e.g. EDCoPilot.exe)
- UniversalGameNavigator / GameNavigator multi-monitor coordinates, downscaling, low-latency config & web-context dead-reckoning
- De-hardcoded game_tools.py and games_tab.js
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestDeclarativeGameModules(unittest.TestCase):
    def test_bundled_modules_load_without_python_hardcoding(self):
        from core.game_modules import (
            build_telemetry_profile_from_module,
            get_module_for_game,
            load_all_game_modules,
        )

        modules = load_all_game_modules()
        self.assertIn("elite_dangerous", modules)
        self.assertIn("elite_dangerous_64", modules)
        self.assertIn("crimson_desert", modules)

        ed_prof = build_telemetry_profile_from_module("elite_dangerous_64")
        self.assertIsNotNone(ed_prof)
        self.assertEqual(ed_prof.provider_type, "native_file")
        self.assertIn("EDCoPilot.exe", ed_prof.suppress_alerts_when_running)
        self.assertEqual(ed_prof.bitmask_mappings["Flags"]["shields_up"], 1 << 3)
        self.assertEqual(ed_prof.bitmask_mappings["Flags"]["scooping_fuel"], 1 << 11)
        self.assertEqual(ed_prof.bitmask_mappings["Flags"]["fsd_mass_locked"], 1 << 16)
        self.assertEqual(ed_prof.bitmask_mappings["Flags"]["low_fuel"], 1 << 19)
        self.assertEqual(ed_prof.bitmask_mappings["Flags"]["is_in_danger"], 1 << 22)

        cd_prof = build_telemetry_profile_from_module("crimson_desert")
        self.assertIsNotNone(cd_prof)
        self.assertEqual(cd_prof.provider_type, "vision_hud")
        self.assertEqual([b.name for b in cd_prof.hud_bars], ["health", "stamina", "spirit"])

        # Match by executable name when game_id differs
        matched = get_module_for_game("my_custom_alias", profile={"process_name": "CrimsonDesert.exe"})
        self.assertIsNotNone(matched)
        self.assertEqual(matched["module_id"], "crimson_desert")

    def test_custom_community_module_works_with_zero_python_changes(self):
        from core.game_modules import build_telemetry_profile_from_module
        from core.telemetry.copilot_gate import CopilotEventGate
        from core.telemetry.providers.file_provider import NativeFileTelemetryProvider

        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = Path(tmp) / "modules"
            mod_dir.mkdir()
            status_file = Path(tmp) / "star_sim_status.json"
            status_file.write_text(
                json.dumps({
                    "StateBits": (1 << 2),
                    "Power": {"ReactorPct": 85},
                    "Ship": {"Hull": 40},
                    "JumpTarget": "Pyro",
                }),
                encoding="utf-8",
            )

            custom_mod = {
                "schema_version": 1,
                "module_id": "custom_space_sim",
                "display_name": "Custom Space Sim",
                "game_ids": ["custom_space_sim"],
                "telemetry": {
                    "provider_type": "native_file",
                    "native_file_path": str(status_file),
                    "bitmask_mappings": {"StateBits": {"reactor_overheat": 4}},
                    "state_rules": [
                        {"flag": "reactor_overheat", "when": True, "state": "reactor_critical"}
                    ],
                    "vital_mappings": {
                        "reactor_pct": {"path": "Power.ReactorPct", "scale_if_gt_1": 100.0},
                        "hull_pct": {"path": "Ship.Hull", "scale_if_gt_1": 100.0},
                    },
                    "resource_mappings": {"jump_target": "JumpTarget"},
                    "alert_rules": {
                        "state_reactor_critical": {
                            "priority": 92,
                            "critical": True,
                            "prompt": "Co-pilot alert: Reactor overheating!",
                        }
                    },
                },
            }
            (mod_dir / "custom_space_sim.aethermodule.json").write_text(
                json.dumps(custom_mod), encoding="utf-8"
            )

            prof = build_telemetry_profile_from_module("custom_space_sim", extra_dirs=[str(mod_dir)])
            self.assertIsNotNone(prof)
            provider = NativeFileTelemetryProvider(prof)
            frame = provider.poll()
            self.assertIsNotNone(frame)
            self.assertIn("reactor_critical", frame.states)
            self.assertAlmostEqual(frame.vitals["reactor_pct"], 0.85, places=2)
            self.assertAlmostEqual(frame.vitals["hull_pct"], 0.40, places=2)
            self.assertEqual(frame.resources["jump_target"], "Pyro")

            gate = CopilotEventGate(global_cooldown_sec=10.0)
            alert = gate.evaluate(frame, alert_rules=prof.alert_rules)
            self.assertIsNotNone(alert)
            self.assertEqual(alert["key"], "state_reactor_critical")


class TestEliteFixesAndCompanionFiles(unittest.TestCase):
    def test_file_provider_decodes_real_frontier_bitmasks_flags2_and_companions(self):
        from core.telemetry.providers.file_provider import NativeFileTelemetryProvider
        from core.telemetry.schema import GameTelemetryProfile

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            # ShieldsUp=bit3(8), ScoopingFuel=bit11(2048), LowFuel=bit19(524288), Flags2: LowOxygen=bit6(64)
            (p / "Status.json").write_text(
                json.dumps({
                    "Flags": (1 << 3) | (1 << 11) | (1 << 19),
                    "Flags2": (1 << 6),
                    "Fuel": {"FuelMain": 4.2},
                    "Pips": [4, 4, 4],
                }),
                encoding="utf-8",
            )
            (p / "NavRoute.json").write_text(
                json.dumps({
                    "Route": [
                        {"StarSystem": "Sol"},
                        {"StarSystem": "Alpha Centauri"},
                        {"StarSystem": "Sirius"},
                    ]
                }),
                encoding="utf-8",
            )
            (p / "Cargo.json").write_text(
                json.dumps({
                    "Count": 12,
                    "Inventory": [{"Name_Localised": "Tritium", "Count": 12}],
                }),
                encoding="utf-8",
            )

            profile = GameTelemetryProfile(
                game_id="elite_dangerous",
                provider_type="native_file",
                native_file_path=str(p / "Status.json"),
            )
            frame = NativeFileTelemetryProvider(profile).poll()
            self.assertIsNotNone(frame)
            self.assertEqual(frame.vitals["shield_pct"], 1.0)
            self.assertNotIn("shields_offline", frame.states)
            self.assertIn("low_fuel", frame.states)
            self.assertIn("low_oxygen", frame.states)
            self.assertEqual(frame.resources["route_destination"], "Sirius")
            self.assertEqual(frame.resources["route_jumps"], 2)
            self.assertEqual(frame.resources["cargo_count"], 12)

    def test_elite_adapter_bitmask_and_companion_summary(self):
        from core.telemetry.declarative import DeclarativeFileTelemetryAdapter
        from core.game_modules import get_module_for_game

        mod = get_module_for_game("elite_dangerous")
        flags_map = mod["telemetry"]["bitmask_mappings"]["Flags"]
        self.assertEqual(flags_map["scooping_fuel"], 0x00000800)
        self.assertEqual(flags_map["low_fuel"], 0x00080000)
        self.assertNotEqual(flags_map["scooping_fuel"], flags_map["low_fuel"])

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "Status.json").write_text(
                json.dumps({"Flags": 0x00000808, "Flags2": 0x00000001, "Pips": [4, 4, 4]}),
                encoding="utf-8",
            )
            (p / "NavRoute.json").write_text(
                json.dumps({"Route": [{"StarSystem": "Sol"}, {"StarSystem": "Colonia"}]}),
                encoding="utf-8",
            )
            adapter = DeclarativeFileTelemetryAdapter(game_id="elite_dangerous", journal_dir=str(p))
            adapter.state["star_system"] = "Sol"
            adapter._poll_status_file()

            self.assertTrue(adapter.flags["scooping_fuel"])
            self.assertFalse(adapter.flags["low_fuel"])
            self.assertTrue(adapter.flags["on_foot"])
            self.assertEqual(adapter.state["route_destination"], "Colonia")
            self.assertEqual(adapter.state["route_jumps"], 1)
            self.assertIn("Colonia", adapter.get_summary_prompt_context())
            self.assertIn("Sol", adapter.get_status_summary())


class TestTelemetryCoexistenceAndGenericGate(unittest.TestCase):
    def test_custom_vital_rule_and_edcopilot_suppression(self):
        from core.telemetry.copilot_gate import CopilotEventGate
        from core.telemetry.schema import GameTelemetryProfile, TelemetryFrame
        from core.telemetry.telemetry_service import TelemetryService

        gate = CopilotEventGate(global_cooldown_sec=10.0)
        mana_rules = {
            "mana_pct": {
                "key": "mana_empty",
                "warn_below": 0.10,
                "min_visible": 0.03,
                "reset_above": 0.40,
                "priority": 65,
                "critical": False,
                "prompt": "Co-pilot alert: Mana depleted!",
            }
        }
        res = gate.evaluate(
            TelemetryFrame(game_id="fantasy_rpg", timestamp=100.0, vitals={"mana_pct": 0.05}),
            alert_rules=mana_rules,
        )
        self.assertIsNotNone(res)
        self.assertEqual(res["key"], "mana_empty")

        # Verify TelemetryService suppresses spoken alert when companion process is running
        triggered = []

        async def _on_trigger(msg):
            triggered.append(msg)

        svc = TelemetryService(on_copilot_trigger=_on_trigger)
        svc.enabled = True
        svc.active_game_id = "elite_dangerous"

        fake_prof = GameTelemetryProfile(
            game_id="elite_dangerous",
            provider_type="native_file",
            poll_interval_sec=0.01,
            suppress_alerts_when_running=["EDCoPilot.exe"],
        )

        class _OneShotProvider:
            def __init__(self, _p):
                self.count = 0

            def poll(self):
                self.count += 1
                if self.count > 1:
                    svc.enabled = False
                    return None
                return TelemetryFrame(game_id="elite_dangerous", timestamp=200.0, states=["low_fuel"])

        with patch.object(svc, "_resolve_active_profile", return_value=fake_prof), \
             patch("core.telemetry.telemetry_service.NativeFileTelemetryProvider", _OneShotProvider), \
             patch("core.telemetry.telemetry_service._is_any_process_running", return_value=True):
            asyncio.run(svc._poll_loop())

        self.assertEqual(triggered, [], "Alerts must be suppressed while EDCoPilot.exe is running")


class TestUniversalGameNavigatorUpgrades(unittest.TestCase):
    def test_multi_monitor_offset_and_downscaling(self):
        from core.game_nav import GameNavigator

        nav = GameNavigator(client=MagicMock(), target_process="Game.exe", game_name="Crimson Desert")
        # Simulate secondary monitor at left=2560, top=0, 2560x1440
        nav._last_capture_origin = (2560, 0)
        px, py = nav._norm_to_screen_px(500, 500, 2560, 1440)
        self.assertEqual((px, py), (2560 + 1280, 720))

        # Verify capture_viewport downscales >1280px images while returning true viewport resolution
        fake_img = Image.new("RGB", (2560, 1440), color=(20, 40, 60))
        fake_bgra = fake_img.convert("RGBA").tobytes("raw", "BGRA")

        class _FakeShot:
            size = (2560, 1440)
            bgra = fake_bgra

        mock_sct = MagicMock()
        mock_sct.__enter__.return_value = mock_sct
        mock_sct.monitors = [
            {"left": 0, "top": 0, "width": 5120, "height": 1440},
            {"left": 0, "top": 0, "width": 2560, "height": 1440},
            {"left": 2560, "top": 0, "width": 2560, "height": 1440},
        ]
        mock_sct.grab.return_value = _FakeShot()

        with patch("core.game_nav.mss.mss", return_value=mock_sct), \
             patch.object(nav, "_resolve_target_monitor", return_value=mock_sct.monitors[2]):
            jpg_bytes, w, h = nav.capture_viewport(max_dim=1280)
            self.assertEqual((w, h), (2560, 1440))
            self.assertEqual(nav._last_capture_origin, (2560, 0))
            import io
            decoded = Image.open(io.BytesIO(jpg_bytes))
            self.assertLessEqual(max(decoded.size), 1280)

    def test_low_latency_vision_config_and_auto_web_context(self):
        from core.game_nav import GameNavigator

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = '{"found": true, "point": [500, 500]}'
        mock_client.models.generate_content.return_value = mock_resp

        nav = GameNavigator(client=mock_client, target_process="Game.exe", game_name="Red Dead Redemption 2")
        with patch.object(nav, "capture_screen", return_value=(b"fake_jpg", 1920, 1080)):
            pt = nav.locate_landmark_point("Legendary Bear", web_context="North of O'Creagh's Run in Grizzlies East")
            self.assertEqual(pt, (960, 540))
            call_kwargs = mock_client.models.generate_content.call_args.kwargs
            cfg = call_kwargs.get("config")
            # Either GenerateContentConfig with temperature=0.0 or fallback dict
            temp = getattr(cfg, "temperature", None) if not isinstance(cfg, dict) else cfg.get("temperature")
            self.assertEqual(temp, 0.0)


class TestDeHardcodedGameToolsAndFrontend(unittest.TestCase):
    def test_games_tab_js_has_no_hardcoded_game_branches(self):
        src = (REPO_ROOT / "ui" / "static" / "games_tab.js").read_text(encoding="utf-8")
        self.assertNotIn('return "crimson_desert";', src)
        self.assertNotIn("if (activeGameId === 'elite_dangerous')", src)
        self.assertIn("data.status_summary", src)

    def test_game_tools_telemetry_is_adapter_agnostic(self):
        src = (REPO_ROOT / "tools" / "game_tools.py").read_text(encoding="utf-8")
        self.assertNotIn('if active_id == "elite_dangerous"', src)
        self.assertIn("status_summary", src)


if __name__ == "__main__":
    unittest.main()
