"""
Tests for:
1. Auto-Build Game Module automatically starting Aether when stopped so Gemini web lookup executes.
2. Per-game 3rd-Party Companion Apps CRUD, live status, Mute Alerts sync -> suppress_alerts_when_running,
   proactive launch prompt once per game session, `launch_game_companion_app` tool + security guardrails,
   Input Safety coexistence, and module export/import local-path stripping.
"""

import asyncio
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.game_engine import GameEngine
from core.game_manager import GameManager
from core.game_modules import (
    build_telemetry_profile_from_module,
    export_game_module,
    import_game_module,
    validate_game_module,
)


class TestAutoBuildStartsAether(unittest.TestCase):
    def test_auto_build_starts_aether_when_stopped_and_passes_live_client(self):
        from core.gui_bridge import GuiBridge

        bridge = GuiBridge.__new__(GuiBridge)
        fake_client = MagicMock(name="FakeGenAIClient")
        fake_engine = SimpleNamespace(is_running=False, genai_client=None, client=None)

        def _fake_start_assistant():
            fake_engine.is_running = True
            fake_engine.genai_client = fake_client
            return {"success": True, "status": "starting"}

        bridge._engine = fake_engine
        bridge.start_assistant = MagicMock(side_effect=_fake_start_assistant)
        bridge.game_mgr = MagicMock()
        bridge.get_raw_config = MagicMock(return_value={})

        captured = {}

        def _fake_auto_build(game_id, game_manager=None, genai_client=None, model_endpoint=""):
            captured["game_id"] = game_id
            captured["genai_client"] = genai_client
            return {
                "success": True,
                "module_id": game_id,
                "display_name": "Crimson Desert",
                "notes": ["Web lookup: universe 'Pywel'."],
            }

        with patch("core.game_modules.auto_build_game_module", side_effect=_fake_auto_build):
            res = bridge.auto_build_game_module("crimson_desert")

        self.assertTrue(res["success"])
        self.assertTrue(res["started_aether"])
        self.assertTrue(res["used_gemini_lookup"])
        bridge.start_assistant.assert_called_once()
        self.assertIs(captured["genai_client"], fake_client)
        self.assertIn("Started Aether", res["notes"][0])

    def test_auto_build_does_not_restart_when_already_running(self):
        from core.gui_bridge import GuiBridge

        bridge = GuiBridge.__new__(GuiBridge)
        fake_client = MagicMock(name="ExistingGenAIClient")
        fake_engine = SimpleNamespace(is_running=True, genai_client=fake_client, client=fake_client)

        bridge._engine = fake_engine
        bridge.start_assistant = MagicMock()
        bridge.game_mgr = MagicMock()
        bridge.get_raw_config = MagicMock(return_value={})

        with patch(
            "core.game_modules.auto_build_game_module",
            return_value={"success": True, "module_id": "elite_dangerous", "display_name": "Elite Dangerous", "notes": []},
        ):
            res = bridge.auto_build_game_module("elite_dangerous")

        self.assertTrue(res["success"])
        self.assertFalse(res["started_aether"])
        self.assertTrue(res["used_gemini_lookup"])
        bridge.start_assistant.assert_not_called()


class TestCompanionAppsLifecycle(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.profiles_path = os.path.join(self._tmp.name, "games_profiles.json")
        self.gm = GameManager(profiles_path=self.profiles_path)
        self.gm.add_game("Elite Dangerous", "EliteDangerous64.exe")
        self.gm.set_active_profile("elite_dangerous")
        self.gm.game_mode_enabled = True

    def tearDown(self):
        self._tmp.cleanup()

    def test_add_update_remove_and_mute_sync(self):
        # Add EDMC (prompt on launch, do not mute)
        r1 = self.gm.add_companion_app(
            game_id="elite_dangerous",
            name="EDMC",
            executable_or_path=r"C:\Apps\EDMC\EDMarketConnector.exe",
            purpose="Market & Inara sync",
            mute_aether_alerts=False,
            prompt_to_launch=True,
        )
        self.assertTrue(r1["success"])
        self.assertEqual(r1["app"]["executable"], "EDMarketConnector.exe")
        self.assertNotIn("EDMarketConnector.exe", r1["suppress_alerts_when_running"])

        # Add EDCoPilot with mute_aether_alerts=True -> auto-syncs into suppress_alerts_when_running!
        r2 = self.gm.add_companion_app(
            game_id="elite_dangerous",
            name="EDCoPilot",
            executable_or_path=r"C:\Apps\EDCoPilot\EDCoPilot.exe",
            purpose="Voice co-pilot",
            mute_aether_alerts=True,
            prompt_to_launch=True,
        )
        self.assertTrue(r2["success"])
        self.assertIn("EDCoPilot.exe", r2["suppress_alerts_when_running"])

        # Toggle EDCoPilot mute OFF -> removed from suppress_alerts_when_running
        r3 = self.gm.update_companion_app("elite_dangerous", "edcopilot", {"mute_aether_alerts": False})
        self.assertTrue(r3["success"])
        self.assertNotIn("EDCoPilot.exe", r3["suppress_alerts_when_running"])

        # Toggle EDCoPilot quiet_when_running ON -> also suppresses duplicate alerts & activates quiet mode
        r_q = self.gm.update_companion_app("elite_dangerous", "edcopilot", {"quiet_when_running": True})
        self.assertTrue(r_q["success"])
        self.assertIn("EDCoPilot.exe", r_q["suppress_alerts_when_running"])
        self.assertEqual(
            self.gm.is_quiet_companion_running("elite_dangerous", running_procs={"edcopilot.exe"}),
            "EDCoPilot",
        )

        # Remove EDCoPilot -> cleanly removed from suppress list
        r4 = self.gm.remove_companion_app("elite_dangerous", "edcopilot")
        self.assertTrue(r4["success"])
        self.assertNotIn("EDCoPilot.exe", r4["suppress_alerts_when_running"])

    def test_context_shows_live_status_and_spotter_only_safety(self):
        self.gm.add_companion_app(
            game_id="elite_dangerous",
            name="EDMC",
            executable_or_path=r"C:\Apps\EDMC\EDMarketConnector.exe",
            purpose="Market sync",
            mute_aether_alerts=False,
            prompt_to_launch=True,
        )
        self.gm.add_companion_app(
            game_id="elite_dangerous",
            name="SRV Survey",
            executable_or_path=r"C:\Apps\SRVSurvey\SRVSurvey.exe",
            purpose="Exobiology tracker",
            mute_aether_alerts=False,
            prompt_to_launch=True,
        )
        self.gm.set_input_mode("elite_dangerous", "spotter_only")

        with patch.object(
            GameManager,
            "_get_running_process_names_lower",
            return_value={"edmarketconnector.exe"},
        ):
            ctx = self.gm.get_active_game_context()

        self.assertIn("LINKED 3RD-PARTY COMPANION APPS", ctx)
        self.assertIn('"EDMC" (EDMarketConnector.exe) [RUNNING] — Market sync', ctx)
        self.assertIn('"SRV Survey" (SRVSurvey.exe) [NOT RUNNING] — Exobiology tracker', ctx)
        self.assertIn("launch_game_companion_app", ctx)
        self.assertIn("SPOTTER-ONLY (ANTI-CHEAT SAFE)", ctx)

    def test_missing_promptable_and_secure_launch(self):
        fake_exe = os.path.join(self._tmp.name, "EDMarketConnector.exe")
        with open(fake_exe, "wb") as f:
            f.write(b"MZ")

        self.gm.add_companion_app(
            game_id="elite_dangerous",
            name="EDMC",
            executable_or_path=fake_exe,
            purpose="Market sync",
            mute_aether_alerts=False,
            prompt_to_launch=True,
        )
        self.gm.add_companion_app(
            game_id="elite_dangerous",
            name="EDD",
            executable_or_path="EDDiscovery.exe",
            purpose="Journal history",
            mute_aether_alerts=False,
            prompt_to_launch=False,
        )

        missing = self.gm.get_missing_promptable_companion_apps("elite_dangerous", running_procs=set())
        self.assertEqual([a["name"] for a in missing], ["EDMC"])

        # Reject launching arbitrary unconfigured executable
        denied = self.gm.launch_companion_app("cmd.exe", game_id="elite_dangerous")
        self.assertFalse(denied["success"])
        self.assertIn("not linked", denied["error"])

        # Launch configured EDMC safely via Popen
        with patch("subprocess.Popen") as mock_popen, patch.object(
            GameManager, "_get_running_process_names_lower", return_value=set()
        ):
            launched = self.gm.launch_companion_app("EDMC", game_id="elite_dangerous")
        self.assertTrue(launched["success"])
        self.assertTrue(launched["launched"])
        mock_popen.assert_called_once()
        self.assertEqual(mock_popen.call_args[0][0], [os.path.normpath(fake_exe)])

    def test_proactive_prompt_fires_once_per_session_and_resets_on_exit(self):
        from core.engine import AetherEngine

        self.gm.add_companion_app(
            game_id="elite_dangerous",
            name="EDMC",
            executable_or_path=r"C:\Apps\EDMC\EDMarketConnector.exe",
            prompt_to_launch=True,
        )

        eng = AetherEngine.__new__(AetherEngine)
        eng.is_running = True
        eng.game_engine = SimpleNamespace(manager=self.gm)
        eng._get_monitor_user_name = lambda: "Michael"
        injected = []

        async def _fake_inject(prompt):
            injected.append(prompt)

        eng.inject_tactical_alert = _fake_inject

        async def _run():
            with patch.object(GameManager, "_get_running_process_names_lower", return_value=set()):
                # 1st activation -> prompts once
                fired1 = eng._check_proactive_companion_launch(True, "elite_dangerous", "Elite Dangerous", True)
                await asyncio.sleep(0)
                # 2nd activation during same session -> suppressed
                fired2 = eng._check_proactive_companion_launch(True, "elite_dangerous", "Elite Dangerous", True)
                await asyncio.sleep(0)
                # Game closes -> resets session latch
                eng._check_proactive_companion_launch(False, "elite_dangerous", "Elite Dangerous", False)
                # Next session -> prompts again!
                fired3 = eng._check_proactive_companion_launch(True, "elite_dangerous", "Elite Dangerous", True)
                await asyncio.sleep(0)
            return fired1, fired2, fired3

        f1, f2, f3 = asyncio.run(_run())
        self.assertTrue(f1)
        self.assertFalse(f2)
        self.assertTrue(f3)
        self.assertEqual(len(injected), 2)
        self.assertIn("Hey Michael, EDMC is not active. Would you like me to start it?", injected[0])
        self.assertIn("launch_game_companion_app", injected[0])

    def test_module_export_strips_local_path_and_import_preserves_local_path(self):
        local_path = r"D:\GamesTools\EDMC\EDMarketConnector.exe"
        self.gm.add_companion_app(
            game_id="elite_dangerous",
            name="EDMC",
            executable_or_path=local_path,
            purpose="Market sync",
            mute_aether_alerts=True,
            prompt_to_launch=True,
        )

        exp = export_game_module("elite_dangerous", game_manager=self.gm)
        self.assertTrue(exp["success"])
        exported_apps = exp["module"]["companion_apps"]
        self.assertEqual(len(exported_apps), 1)
        self.assertEqual(exported_apps[0]["executable"], "EDMarketConnector.exe")
        self.assertNotIn("path", exported_apps[0])  # Local filesystem path stripped for privacy/security!
        self.assertIn("EDMarketConnector.exe", exp["module"]["suppress_alerts_when_running"])

        # Re-importing the exported module preserves the user's saved local path
        imp = import_game_module(exp["module"], game_manager=self.gm, target_dir=self._tmp.name)
        self.assertTrue(imp["success"])
        apps_after = self.gm.get_companion_apps("elite_dangerous")
        self.assertEqual(apps_after[0]["path"], os.path.normpath(local_path))

        # Telemetry profile also picks up muted companion app executable
        tp = build_telemetry_profile_from_module(
            "elite_dangerous",
            profile=self.gm.get_profile("elite_dangerous"),
            extra_dirs=[self._tmp.name],
        )
        self.assertIn("EDMarketConnector.exe", tp.suppress_alerts_when_running)

    def test_launch_game_companion_app_in_core_tools_and_ui_hooks(self):
        self.assertIn("launch_game_companion_app", GameEngine.CORE_GAME_TOOL_NAMES)

        with open("ui/static/index.html", "r", encoding="utf-8") as f:
            html = f.read()
        self.assertIn('id="modal-add-companion-app"', html)
        self.assertIn('id="game-companion-apps-list"', html)
        self.assertIn('id="btn-add-companion-app"', html)
        self.assertNotIn('id="btn-add-companion-app-banner"', html)
        self.assertNotIn('id="input-game-suppress-alerts"', html)
        self.assertIn('id="chk-companion-mute-alerts"', html)
        self.assertIn('id="chk-companion-quiet-running"', html)
        self.assertIn('id="chk-companion-prompt-launch"', html)
        self.assertIn('id="select-game-input-mode" class="hud-select" style="width: auto; min-width: 210px;"', html)

        with open("ui/static/games_tab.js", "r", encoding="utf-8") as f:
            js = f.read()
        self.assertIn("add_game_companion_app", js)
        self.assertIn("update_game_companion_app", js)
        self.assertIn("remove_game_companion_app", js)
        self.assertIn("launch_game_companion_app", js)
        self.assertIn("renderCompanionAppsList", js)
        self.assertIn("Quiet When Running", js)
        self.assertIn("calibrate_hud_bars", GameEngine.CORE_GAME_TOOL_NAMES)


class TestHudCalibrationFocusAndVoice(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.profiles_path = os.path.join(self._tmp.name, "games_profiles.json")
        self.gm = GameManager(profiles_path=self.profiles_path)
        self.gm.add_game("Crimson Desert", "CrimsonDesert.exe")
        self.gm.set_active_profile("crimson_desert")
        self.gm.set_input_mode("crimson_desert", "spotter_only")

    def tearDown(self):
        self._tmp.cleanup()

    def test_ui_button_auto_starts_focuses_game_and_waits_countdown(self):
        from core.gui_bridge import GuiBridge
        from core.telemetry.schema import HudBarRegion

        bridge = GuiBridge.__new__(GuiBridge)
        fake_client = MagicMock(name="FakeGenAIClient")
        fake_engine = SimpleNamespace(is_running=False, genai_client=None, client=None, telemetry_service=None)

        def _fake_start():
            fake_engine.is_running = True
            fake_engine.genai_client = fake_client
            return {"success": True, "status": "starting"}

        bridge._engine = fake_engine
        bridge.start_assistant = MagicMock(side_effect=_fake_start)
        bridge.game_mgr = self.gm
        bridge.get_raw_config = MagicMock(return_value={})

        fake_bars = [HudBarRegion(name="health", bbox_pct=(0.05, 0.90, 0.20, 0.02), color_profile="red")]

        with patch("tools.os_controls.focus_window", return_value={"status": "success"}) as mock_focus, \
             patch("time.sleep") as mock_sleep, \
             patch("core.game_nav.UniversalGameNavigator.capture_viewport", return_value=(b"\xff\xd8\xff", 1280, 720)), \
             patch("core.telemetry.providers.vision_hud_provider.calibrate_hud_bars_from_frame", return_value=fake_bars), \
             patch("core.game_modules.get_user_modules_dir", return_value=self._tmp.name):
            res = bridge.calibrate_game_hud_bars("crimson_desert", focus_game=True, countdown_sec=3.0)

        self.assertTrue(res["success"])
        self.assertTrue(res["started_aether"])
        self.assertTrue(res["focused_game"])
        bridge.start_assistant.assert_called_once()
        mock_focus.assert_called_once()
        mock_sleep.assert_called_once_with(3.0)
        self.assertEqual(res["bars"][0]["name"], "health")

    def test_voice_tool_calibrates_instantly_in_spotter_only_without_focus_swap(self):
        from tools.game_tools import register_game_tools
        from core.telemetry.schema import HudBarRegion

        registered = {}

        class _FakeDispatcher:
            genai_client = MagicMock(name="VoiceGenAIClient")

            def register(self, name, description="", parameters=None):
                def _wrap(fn):
                    registered[name] = fn
                    return fn
                return _wrap

        fake_engine = SimpleNamespace(genai_client=_FakeDispatcher.genai_client, config={}, telemetry_service=None)
        register_game_tools(_FakeDispatcher(), self.gm, engine=fake_engine)
        self.assertIn("calibrate_hud_bars", registered)

        fake_bars = [
            HudBarRegion(name="health", bbox_pct=(0.04, 0.90, 0.18, 0.015), color_profile="red"),
            HudBarRegion(name="stamina", bbox_pct=(0.04, 0.93, 0.16, 0.012), color_profile="green"),
        ]
        with patch("tools.os_controls.focus_window") as mock_focus, \
             patch("time.sleep") as mock_sleep, \
             patch("core.game_nav.UniversalGameNavigator.capture_viewport", return_value=(b"\xff\xd8\xff", 1280, 720)), \
             patch("core.telemetry.providers.vision_hud_provider.calibrate_hud_bars_from_frame", return_value=fake_bars), \
             patch("core.game_modules.get_user_modules_dir", return_value=self._tmp.name):
            res = registered["calibrate_hud_bars"]()

        self.assertTrue(res["success"])
        self.assertEqual([b["name"] for b in res["bars"]], ["health", "stamina"])
        mock_focus.assert_not_called()
        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
