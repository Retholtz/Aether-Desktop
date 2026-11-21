"""
Phase 2 Game Companion Overhaul Tests:
- LocalhostHttpTelemetryProvider (SSRF safety, loopback JSON parsing, threshold rules, MSFS module)
- Declarative `.aethermodule.json` validation, Import, Export, and Auto-Build
- Anti-cheat `spotter_only` safety guardrail across GameEngine and game_tools
- Frontend Games tab Module / Safety / Coexistence wiring
"""

import http.server
import json
import socketserver
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent


class _DummyDispatcher:
    def __init__(self):
        self.tools = {}

    def register(self, name, description="", parameters=None, handler=None):
        if handler is not None:
            self.tools[name] = handler
            return handler

        def decorator(fn):
            self.tools[name] = fn
            return fn

        return decorator


class TestLocalhostHttpTelemetryProvider(unittest.TestCase):
    def test_ssrf_blocks_non_localhost_urls(self):
        from core.telemetry.providers.http_provider import (
            LocalhostHttpTelemetryProvider,
            is_safe_localhost_url,
        )
        from core.telemetry.schema import GameTelemetryProfile

        self.assertTrue(is_safe_localhost_url("http://127.0.0.1:8965/api/sim/info"))
        self.assertTrue(is_safe_localhost_url("http://localhost:8888/api/getgamedata"))
        self.assertFalse(is_safe_localhost_url("http://169.254.169.254/latest/meta-data/"))
        self.assertFalse(is_safe_localhost_url("https://evil.example.com/telemetry"))
        self.assertFalse(is_safe_localhost_url("file:///C:/Windows/win.ini"))

        prof = GameTelemetryProfile(
            game_id="msfs",
            provider_type="localhost_http",
            http_endpoint="https://evil.example.com/telemetry",
        )
        provider = LocalhostHttpTelemetryProvider(prof)
        self.assertIsNone(provider.poll())

    def test_msfs_module_polls_real_loopback_http_server(self):
        from core.game_modules import build_telemetry_profile_from_module
        from core.telemetry.providers.http_provider import LocalhostHttpTelemetryProvider

        payload = {
            "fuel_total_percent": 12.0,
            "indicated_altitude": 14500,
            "ground_speed": 215,
            "heading": 270,
            "vertical_speed": -500,
            "on_ground": False,
        }

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            msfs_prof = build_telemetry_profile_from_module("msfs_2024")
            self.assertIsNotNone(msfs_prof)
            self.assertEqual(msfs_prof.provider_type, "localhost_http")
            msfs_prof = msfs_prof.model_copy(
                update={"http_endpoint": f"http://127.0.0.1:{port}/api/sim/info"}
            )
            frame = LocalhostHttpTelemetryProvider(msfs_prof).poll()
            self.assertIsNotNone(frame)
            self.assertAlmostEqual(frame.vitals["fuel_pct"], 0.12, places=2)
            self.assertIn("low_fuel", frame.states)
            self.assertEqual(frame.resources["indicated_altitude_ft"], 14500)
            self.assertEqual(frame.resources["ground_speed_kts"], 215)
        finally:
            server.shutdown()
            server.server_close()


class TestModuleImportExportAndAutoBuild(unittest.TestCase):
    def test_validate_rejects_external_http_endpoint(self):
        from core.game_modules import validate_game_module

        ok, _, err = validate_game_module({
            "module_id": "bad_mod",
            "display_name": "Bad Mod",
            "telemetry": {
                "provider_type": "localhost_http",
                "http_endpoint": "http://attacker.invalid/steal",
            },
        })
        self.assertFalse(ok)
        self.assertIn("localhost", err.lower())

    def test_import_and_export_roundtrip_strips_private_notes(self):
        from core.game_manager import GameManager
        from core.game_modules import export_game_module, import_game_module

        with tempfile.TemporaryDirectory() as tmp:
            pfile = Path(tmp) / "profiles.json"
            mod_dir = Path(tmp) / "user_modules"
            mgr = GameManager(profiles_path=str(pfile))
            mgr.add_game("Starfield", "Starfield.exe")
            mgr.get_profile("starfield")["scratchpad_raw"] = "SECRET PERSONAL NOTES"

            incoming = {
                "module_id": "starfield",
                "display_name": "Starfield",
                "executables": ["Starfield.exe"],
                "universe_name": "Settled Systems",
                "input_mode": "autonomous",
                "suppress_alerts_when_running": ["CustomPilot.exe"],
                "companion_resources": {"lore": {"name": "Inara", "domain": "inara.cz"}},
                "telemetry": {"provider_type": "vision_hud", "poll_interval_sec": 0.5},
            }
            res = import_game_module(incoming, game_manager=mgr, target_dir=str(mod_dir))
            self.assertTrue(res["success"])
            self.assertTrue((mod_dir / "starfield.aethermodule.json").is_file())

            # Personal scratchpad preserved after assimilation
            prof = mgr.get_profile("starfield")
            self.assertEqual(prof.get("scratchpad_raw"), "SECRET PERSONAL NOTES")
            self.assertEqual(prof.get("universe_name"), "Settled Systems")
            self.assertEqual(prof.get("suppress_alerts_when_running"), ["CustomPilot.exe"])

            # Exported shareable module must NOT leak personal scratchpad
            exp = export_game_module("starfield", game_manager=mgr)
            self.assertTrue(exp["success"])
            self.assertNotIn("scratchpad_raw", exp["module"])
            self.assertNotIn("copilot_log", exp["module"])
            self.assertEqual(exp["module"]["universe_name"], "Settled Systems")
            mgr.close()

    def test_auto_build_detects_anticheat_and_companion_bridges(self):
        from core.game_manager import GameManager
        from core.game_modules import auto_build_game_module

        with tempfile.TemporaryDirectory() as tmp:
            pfile = Path(tmp) / "profiles.json"
            mod_dir = Path(tmp) / "user_modules"
            mgr = GameManager(profiles_path=str(pfile))
            mgr.add_game("Apex Legends", "r5apex.exe")

            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.text = json.dumps({
                "universe_name": "Outlands",
                "wiki_domain": "apexlegends.wiki.gg",
                "uses_kernel_anticheat": True,
                "anticheat_name": "Easy Anti-Cheat",
            })
            mock_client.models.generate_content.return_value = mock_resp

            res = auto_build_game_module(
                "apex_legends",
                game_manager=mgr,
                genai_client=mock_client,
                target_dir=str(mod_dir),
            )
            self.assertTrue(res["success"])
            self.assertEqual(res["input_mode"], "spotter_only")
            self.assertEqual(mgr.get_input_mode("apex_legends"), "spotter_only")
            self.assertEqual(mgr.get_profile("apex_legends")["universe_name"], "Outlands")
            mgr.close()


class TestSpotterOnlyAntiCheatGuardrail(unittest.TestCase):
    def test_spotter_only_blocks_synthetic_inputs_and_routes_nav_to_spotter(self):
        from core.game_engine import GameEngine
        from core.game_manager import GameManager
        from tools.game_tools import register_game_tools

        with tempfile.TemporaryDirectory() as tmp:
            pfile = Path(tmp) / "profiles.json"
            mgr = GameManager(profiles_path=str(pfile))
            mgr.add_game("Valorant", "VALORANT-Win64-Shipping.exe")
            mgr.set_keybind("valorant", "open map", "m")
            mgr.set_active_profile("valorant")
            mgr.set_input_mode("valorant", "spotter_only")

            # 1. Fast-path voice macro must NOT fire synthetic keypresses
            ge = GameEngine(profiles_path=str(pfile))
            ge.manager = mgr
            res_voice = ge.intercept_voice_turn("open map")
            self.assertFalse(res_voice.handled)

            # 2. Direct game tools must block synthetic inputs & route navigation to spotter
            dispatcher = _DummyDispatcher()
            dummy_engine = MagicMock()
            dummy_engine.genai_client = MagicMock()
            dummy_engine.config = {}
            register_game_tools(dispatcher, mgr, engine=dummy_engine)

            self.assertEqual(
                dispatcher.tools["trigger_game_action"]("open map")["status"],
                "blocked_by_spotter_only",
            )
            self.assertEqual(
                dispatcher.tools["pan_game_map"]("north")["status"],
                "blocked_by_spotter_only",
            )
            self.assertEqual(
                dispatcher.tools["place_map_marker"](100, 100)["status"],
                "blocked_by_spotter_only",
            )
            self.assertEqual(
                dispatcher.tools["center_map_on_player"]()["status"],
                "blocked_by_spotter_only",
            )
            self.assertEqual(
                dispatcher.tools["search_and_route_destination"]("Sol")["status"],
                "blocked_by_spotter_only",
            )

            with patch("core.game_nav.GameNavigator.spot_and_guide_map_location") as mock_spot:
                mock_spot.return_value = {"status": "success", "spoken_guidance": "Look northeast."}
                nav_res = dispatcher.tools["pan_and_mark_map_location"]("Site A")
                self.assertEqual(nav_res["status"], "success")
                mock_spot.assert_called_once()

            ge.stop()
            mgr.close()


class TestPhase2FrontendControls(unittest.TestCase):
    def test_games_tab_ui_has_module_and_safety_controls(self):
        html = (REPO_ROOT / "ui" / "static" / "index.html").read_text(encoding="utf-8")
        js = (REPO_ROOT / "ui" / "static" / "games_tab.js").read_text(encoding="utf-8")
        for el_id in (
            "select-game-input-mode",
            "input-game-suppress-alerts",
            "btn-autobuild-game-module",
            "btn-import-game-module",
            "btn-export-game-module",
        ):
            self.assertIn(el_id, html)
            self.assertIn(el_id, js)


if __name__ == "__main__":
    unittest.main()
