"""
Batch 5 Regression Tests: Game Mode, TTRPG Mechanics & Frontend Polish.
"""

import asyncio
import re
import tempfile
import time
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

    def register_tool(self, name, description="", parameters=None, handler=None):
        return self.register(name=name, description=description, parameters=parameters, handler=handler)


class TestGameNavigatorAndTools(unittest.TestCase):
    def test_game_navigator_is_not_shadowed(self):
        from core.game_nav import GameNavigator, UniversalGameNavigator

        self.assertIsNot(GameNavigator, UniversalGameNavigator)
        self.assertTrue(issubclass(GameNavigator, UniversalGameNavigator))
        for method_name in (
            "pan_map",
            "locate_landmark_point",
            "verify_marker_placement",
            "pan_and_place_marker",
            "pan_and_place_marker_verified",
        ):
            self.assertTrue(
                callable(getattr(GameNavigator, method_name, None)),
                f"GameNavigator missing expected method {method_name}",
            )

    def test_game_tools_pan_map_and_route_destination_pass_client(self):
        from tools.game_tools import register_game_tools

        dispatcher = _DummyDispatcher()
        dummy_client = object()
        dummy_engine = MagicMock()
        dummy_engine.genai_client = dummy_client
        dummy_engine.client = dummy_client
        dummy_engine.config = {}

        game_manager = MagicMock()
        game_manager.data = {"active_profile": "skyrim"}
        game_manager.get_profile.return_value = {
            "id": "skyrim",
            "display_name": "Skyrim",
            "keybinds": {},
        }

        register_game_tools(dispatcher, game_manager, engine=dummy_engine)
        self.assertIn("pan_game_map", dispatcher.tools)
        self.assertIn("search_and_route_destination", dispatcher.tools)

        with patch("core.game_nav.GameNavigator") as mock_nav_cls:
            mock_nav = mock_nav_cls.return_value
            mock_nav.pan_map.return_value = {"status": "success", "direction": "left"}
            res = dispatcher.tools["pan_game_map"](direction="left", distance="medium")
            self.assertEqual(res.get("status"), "success")
            mock_nav_cls.assert_called_once()
            self.assertIs(mock_nav_cls.call_args.kwargs.get("client"), dummy_client)

        with patch("core.game_nav.GameNavigator") as mock_nav_cls:
            mock_nav = mock_nav_cls.return_value
            mock_nav.search_and_route_destination.return_value = {"status": "success", "destination": "Sol"}
            res = dispatcher.tools["search_and_route_destination"](destination_name="Sol")
            self.assertEqual(res.get("status"), "success")
            mock_nav_cls.assert_called_once()
            self.assertIs(mock_nav_cls.call_args.kwargs.get("client"), dummy_client)


class TestGameEngineVoiceInterceptAndTools(unittest.TestCase):
    def _make_game_engine(self, initiative_order=None):
        from core.game_engine import GameEngine

        mock_engine = MagicMock()
        mock_engine.ttrpg_service = MagicMock()
        mock_engine.ttrpg_service.active_state = {
            "initiative_order": list(initiative_order or [])
        }
        mock_engine.dispatcher = MagicMock()

        ge = GameEngine.__new__(GameEngine)
        ge.engine = mock_engine
        ge.manager = MagicMock()
        ge.manager.data = {"active_profile": ""}
        ge.manager.get_profile.return_value = None
        ge.manager.game_mode_enabled = False
        ge.manager.is_game_mode_active.return_value = False
        ge.manager.trigger_action.return_value = {"status": "not_found"}
        ge.game_service = ge.manager
        return ge, mock_engine

    def test_core_game_tools_include_window_and_music_tools(self):
        from core.game_engine import GameEngine

        for tool_name in ("tile_windows", "snap_window", "play_music"):
            self.assertIn(tool_name, GameEngine.CORE_GAME_TOOL_NAMES)

    def test_media_pause_and_skip_do_not_hijack_gaming_sentences(self):
        ge, _ = self._make_game_engine()
        self.assertFalse(ge.intercept_voice_turn("pause the game").handled)
        self.assertFalse(ge.intercept_voice_turn("let's skip this quest").handled)
        self.assertFalse(ge.intercept_voice_turn("can you pause recording").handled)

        res_pause = ge.intercept_voice_turn("pause")
        self.assertTrue(res_pause.handled)
        self.assertEqual(res_pause.action_type, "media_pause")

        res_pause_music = ge.intercept_voice_turn("pause the music")
        self.assertTrue(res_pause_music.handled)
        self.assertEqual(res_pause_music.action_type, "media_pause")

        res_skip = ge.intercept_voice_turn("skip")
        self.assertTrue(res_skip.handled)
        self.assertEqual(res_skip.action_type, "media_next")

        res_skip_song = ge.intercept_voice_turn("skip this song")
        self.assertTrue(res_skip_song.handled)
        self.assertEqual(res_skip_song.action_type, "media_next")

    def test_next_turn_gated_on_active_ttrpg_combat(self):
        ge_inactive, _ = self._make_game_engine(initiative_order=[])
        res_inactive = ge_inactive.intercept_voice_turn("next turn")
        self.assertFalse(res_inactive.handled)

        # Explicit "advance initiative" still works regardless
        res_explicit = ge_inactive.intercept_voice_turn("advance initiative")
        self.assertTrue(res_explicit.handled)
        self.assertEqual(res_explicit.action_type, "ttrpg_advance_turn")

        ge_active, _ = self._make_game_engine(
            initiative_order=[{"name": "Lyra", "initiative": 15, "is_player": True}]
        )
        res_active = ge_active.intercept_voice_turn("next turn")
        self.assertTrue(res_active.handled)
        self.assertEqual(res_active.action_type, "ttrpg_advance_turn")


class TestGameMacroRunner(unittest.TestCase):
    def test_evaluate_condition_handles_missing_boolean_flags(self):
        from core.game_macro_runner import evaluate_condition

        self.assertTrue(evaluate_condition("state.shields_up == false", {}))
        self.assertTrue(evaluate_condition("state.shields_up != true", {}))
        self.assertFalse(evaluate_condition("state.shields_up == true", {}))
        self.assertTrue(
            evaluate_condition("state.shields_up == true", {"shields_up": True})
        )

    def test_case_insensitive_sequence_lookup_and_instant_abort(self):
        from core.game_macro_runner import MacroRunner

        runner = MacroRunner()
        profile = {
            "id": "elite_dangerous",
            "display_name": "Elite Dangerous",
            "keybinds": {"DeployHardpoints": "U"},
            "sequences": {
                "combat_prep": {
                    "steps": [{"action": "wait", "delay_ms": 4000}],
                },
                "quick_toggle": {
                    "steps": [{"action": "wait", "delay_ms": 10}],
                },
            },
        }

        res = runner.execute_action("COMBAT_PREP", profile)
        self.assertEqual(res.get("status"), "executed")
        self.assertIsNotNone(runner._active_sequence_thread)
        self.assertTrue(runner._active_sequence_thread.is_alive())

        t0 = time.monotonic()
        runner.abort_running_sequence()
        runner._active_sequence_thread.join(timeout=1.0)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 0.5, "abort_running_sequence() should wake _abort_event immediately")
        self.assertFalse(runner._active_sequence_thread.is_alive())

        # Starting a new sequence while another is running aborts & joins prior thread cleanly
        runner.execute_action("combat_prep", profile)
        prev_thread = runner._active_sequence_thread
        t1 = time.monotonic()
        res2 = runner.execute_action("Quick_Toggle", profile)
        elapsed2 = time.monotonic() - t1
        self.assertEqual(res2.get("status"), "executed")
        self.assertLess(elapsed2, 0.5)
        self.assertFalse(prev_thread.is_alive())
        if runner._active_sequence_thread:
            runner._active_sequence_thread.join(timeout=1.0)


class TestTTRPGServiceAndTools(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        from core.ttrpg_service import TTRPGService

        self.svc = TTRPGService(data_dir=self.tmp_dir.name)
        self.svc.create_campaign(
            title="Batch 5 Test Campaign",
            system_preset="dnd_5e",
        )
        self.svc.add_character(
            {
                "name": "Lyra",
                "role": "player",
                "character_class": "Warlock",
                "level": 3,
                "vitals": {
                    "health": {"current": 20, "max": 20},
                    "guard": {"current": 15, "max": 30},
                },
                "conditions": ["Poisoned", "Cursed", "Attuned: Ring of Protection"],
                "spell_slots": {
                    "pact_current": 0,
                    "pact_max": 2,
                    "pact_level": 2,
                    "1": {"current": 0, "max": 2},
                },
                "custom_meters": {"ki_points": {"current": 3, "max": 3}},
                "attributes": {
                    "STR": 8,
                    "DEX": 14,
                    "CON": 1,  # -5 CON modifier for level-up clamping test
                    "INT": 10,
                    "WIS": 12,
                    "CHA": 16,
                },
            }
        )
        self.svc.add_character(
            {
                "name": "Borin",
                "role": "player",
                "character_class": "Fighter",
                "level": 3,
                "vitals": {
                    "health": {"current": 18, "max": 18},
                    "guard": {"current": 16, "max": 30},
                },
            }
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_find_helpers_reject_empty_string(self):
        self.svc.start_combat([{"name": "Lyra", "initiative": 15, "is_player": True}])
        self.assertEqual(self.svc._find_character(""), (None, None))
        self.assertEqual(self.svc._find_character("   "), (None, None))
        self.assertIsNone(self.svc._find_combatant(""))
        self.assertIsNone(self.svc._find_combatant("   "))

    def test_custom_meter_never_corrupts_initiative_hp_and_aliases_work(self):
        self.svc.start_combat([{"name": "Lyra", "initiative": 14, "is_player": True}])

        res_meter = self.svc.modify_character_stat(
            "Lyra", "ki_points", -1, is_custom_meter=True
        )
        self.assertEqual(res_meter.get("status"), "success")

        lyra_init = self.svc._find_combatant("Lyra")
        self.assertEqual(lyra_init["current_hp"], 20)
        self.assertEqual(lyra_init["status"], "Healthy")

        # Alias "hp" -> "health" updates both character and initiative tracker
        res_hp = self.svc.modify_character_stat("Lyra", "hp", -5)
        self.assertEqual(res_hp.get("status"), "success")
        _, lyra = self.svc._find_character("Lyra")
        self.assertEqual(lyra["vitals"]["health"]["current"], 15)
        self.assertEqual(self.svc._find_combatant("Lyra")["current_hp"], 15)

        # Alias "ac" -> "guard" updates both character and initiative AC
        res_ac = self.svc.modify_character_stat("Lyra", "ac", 2)
        self.assertEqual(res_ac.get("status"), "success")
        _, lyra = self.svc._find_character("Lyra")
        self.assertEqual(lyra["vitals"]["guard"]["current"], 17)
        self.assertEqual(self.svc._find_combatant("Lyra")["armor_class"], 17)

    def test_start_combat_marks_zero_hp_player_dying_not_defeated(self):
        self.svc.modify_character_stat("Borin", "health", -18)
        self.svc.start_combat([{"name": "Borin", "initiative": 10, "is_player": True}])
        borin_init = self.svc._find_combatant("Borin")
        self.assertEqual(borin_init["status"], "Dying (0 HP)")

    def test_long_rest_skips_dead_restores_pact_and_preserves_persistent_conditions(self):
        _, borin = self.svc._find_character("Borin")
        borin["vitals"]["health"]["current"] = 0
        borin["death_saves"] = {"successes": 0, "failures": 3, "status": "dead"}
        borin["conditions"] = ["Dead"]
        self.svc.save_active_campaign_state()

        res = self.svc.commit_interactive_rest({"rest_type": "long", "allocations": {}})
        self.assertEqual(res.get("status"), "success")

        _, borin_after = self.svc._find_character("Borin")
        self.assertEqual(
            borin_after["vitals"]["health"]["current"],
            0,
            "Long rest must not revive a Dead hero",
        )

        _, lyra_after = self.svc._find_character("Lyra")
        self.assertEqual(lyra_after["spell_slots"]["pact_current"], 2, "Pact slots must recharge")
        self.assertNotIn("Poisoned", lyra_after["conditions"])
        self.assertIn("Cursed", lyra_after["conditions"])
        self.assertIn("Attuned: Ring of Protection", lyra_after["conditions"])

    def test_charmed_condition_scoped_to_charmer(self):
        _, lyra = self.svc._find_character("Lyra")
        lyra["conditions"] = ["Charmed (Strahd)"]
        lyra["spell_slots"]["level_1"] = {"current": 2, "max": 2}
        lyra["spells"] = [{"name": "Hex", "level": "1st Level", "prepared": True}]
        self.svc.save_active_campaign_state()

        blocked = self.svc.cast_character_spell("Lyra", "Hex", spell_level=1, target_name="Strahd")
        self.assertEqual(blocked.get("status"), "error")
        self.assertIn("Charmed", blocked.get("message", ""))

        allowed = self.svc.cast_character_spell("Lyra", "Hex", spell_level=1, target_name="Goblin")
        self.assertEqual(allowed.get("status"), "success", allowed.get("message"))

    def test_level_up_clamps_hp_gain_to_at_least_one(self):
        from core.ttrpg_service import level_up_dnd_character

        _, lyra = self.svc._find_character("Lyra")
        # d6 Wizard with CON=1 (-5 mod): (6//2)+1+(-5) = -1 -> must clamp to >= 1
        wizard_copy = {
            "name": "Frail Wizard",
            "class_name": "Wizard",
            "level": 1,
            "attributes": {"CON": 1},
            "vitals": {"health": {"current": 6, "max": 6}},
        }
        res = level_up_dnd_character(wizard_copy, target_level=2)
        self.assertEqual(res.get("status"), "success")
        self.assertGreaterEqual(wizard_copy["vitals"]["health"]["max"], 7)

    def test_death_save_reads_kept_die_for_nat20_and_nat1(self):
        from tools.ttrpg_tools import register_ttrpg_tools

        dispatcher = _DummyDispatcher()
        emitted = []
        register_ttrpg_tools(dispatcher, self.svc, notify_ui_callback=lambda t, d: emitted.append((t, d)))
        self.assertIn("roll_dice", dispatcher.tools)

        # Put Borin at 0 HP Dying
        self.svc.modify_character_stat("Borin", "health", -18)

        # Mock parse_and_roll returning kept_die=20 (no legacy "rolls" key!)
        with patch.object(
            self.svc,
            "parse_and_roll",
            return_value={
                "status": "success",
                "kept_die": 20,
                "dice_rolled": [20],
                "total": 20,
            },
        ):
            res = asyncio.run(
                dispatcher.tools["roll_dice"](
                    formula="1d20", purpose="Death Saving Throw", character_name="Borin"
                )
            )
            self.assertEqual(res.get("status"), "success")
            self.assertIn("NATURAL 20", res.get("death_save_result", "").upper())
            _, borin = self.svc._find_character("Borin")
            self.assertEqual(borin["vitals"]["health"]["current"], 1)

        # Drop Borin back to 0 HP and roll a Nat 1 with a +3 bonus (total=4, raw=1)
        self.svc.modify_character_stat("Borin", "health", -1)
        with patch.object(
            self.svc,
            "parse_and_roll",
            return_value={
                "status": "success",
                "kept_die": 1,
                "dice_rolled": [1],
                "total": 4,
            },
        ):
            res = asyncio.run(
                dispatcher.tools["roll_dice"](
                    formula="1d20+3", purpose="Death Saving Throw", character_name="Borin"
                )
            )
            self.assertEqual(res.get("status"), "success")
            self.assertIn("NATURAL 1", res.get("death_save_result", "").upper())
            _, borin = self.svc._find_character("Borin")
            self.assertEqual(borin["death_saves"]["failures"], 2)

    def test_modular_system_tools_emit_ui_events(self):
        from tools.ttrpg_tools import register_modular_system_tools

        dispatcher = _DummyDispatcher()
        emitted = []

        # Set up a character with Pathfinder, CoC, and Cyberpunk structures so all 3 tools succeed
        self.svc.add_character(
            {
                "name": "OmniHero",
                "role": "player",
                "vitals": {
                    "health": {"current": 30, "max": 30},
                    "guard": {"current": 14, "max": 30},
                    "sanity": {"current": 50, "max": 50},
                    "magic_points": {"current": 10, "max": 10},
                },
                "native_system_data": {
                    "spellcasting": {
                        "spell_slots": {"rank_3": {"current": 2, "max": 2}},
                        "focus_magic": {"focus_points": {"current": 1, "max": 1}},
                    },
                    "armor": {"head_sp": 11, "body_sp": 11},
                },
            }
        )

        register_modular_system_tools(
            dispatcher, self.svc, notify_ui_callback=lambda t, d: emitted.append((t, d))
        )

        res_pf = asyncio.run(
            dispatcher.tools["cast_pathfinder_spell"](
                character_name="OmniHero", spell_name="Fireball", spell_rank=3
            )
        )
        res_coc = asyncio.run(
            dispatcher.tools["cast_coc_spell"](
                character_name="OmniHero", spell_name="Wither", mp_cost=2, san_loss_formula="1d4"
            )
        )
        res_cp = asyncio.run(
            dispatcher.tools["apply_cyberpunk_damage"](
                character_name="OmniHero", raw_damage=15, hit_location="body"
            )
        )

        self.assertEqual(res_pf.get("status"), "success")
        self.assertEqual(res_coc.get("status"), "success")
        self.assertIn(res_cp.get("status"), ("success", "penetrated"))

        emitted_types = [t for t, _ in emitted]
        self.assertGreaterEqual(emitted_types.count("ttrpg_state_updated"), 3)
        self.assertGreaterEqual(emitted_types.count("roster_updated"), 3)


class TestFrontendStaticPolish(unittest.TestCase):
    def _read_static(self, rel_path: str) -> str:
        return (REPO_ROOT / "ui" / "static" / rel_path).read_text(encoding="utf-8")

    def test_settings_js_oauth_guards_model_merge_and_custom_endpoints(self):
        src = self._read_static("settings.js")
        self.assertIn("const hasKey = (k) => Object.prototype.hasOwnProperty.call(statusMap, k)", src)
        for key in ("'google'", "'microsoft'", "'imap'", "'steam'", "'desktop_notifications'", "'streaming'"):
            self.assertIn(f"hasKey({key})", src)
        self.assertIn("function mergeDiscoveredModelOptions(", src)
        self.assertIn("mergeDiscoveredModelOptions(DEFAULT_CHAT_MODELS", src)
        self.assertIn("function formatTierEndpoint(", src)

    def test_app_js_handles_roster_updated_and_deduplicates_collect_settings(self):
        src = self._read_static("app.js")
        self.assertIn('type === "roster_updated"', src)
        self.assertNotIn("\nfunction collectSettingsPayload() {", src)
        self.assertIn('typeof window.collectSettingsPayload !== "function"', src)

    def test_projects_js_syncs_window_active_project_id(self):
        src = self._read_static("projects.js")
        self.assertIn("window.activeProjectId = activeProjectId;", src)
        self.assertIn("function setActiveProjectId(pid)", src)

    def test_roleplay_js_cast_spell_targets_real_story_chat(self):
        src = self._read_static("roleplay.js")
        self.assertNotIn("input-rp-dm-prompt", src)
        self.assertNotIn("btn-send-rp-dm-prompt", src)
        m = re.search(r"async function handleCardCastSpell\b[\s\S]{0,1800}", src)
        self.assertIsNotNone(m)
        block = m.group(0)
        self.assertIn("rpStoryChatInput", block)
        self.assertIn("btnSendRpStory", block)
        self.assertNotIn("send_chat_message", block)

    def test_games_tab_js_pane_id_and_debounced_target_capture(self):
        src = self._read_static("games_tab.js")
        self.assertNotIn("tab-pane-games", src)
        self.assertIn("document.getElementById('tab-games')", src)
        self.assertGreaterEqual(src.count("const targetGameId = activeGameId;"), 2)

    def test_programs_tab_js_has_no_aetherapp_references(self):
        src = self._read_static("programs_tab.js")
        self.assertNotIn("window.AetherApp", src)
        self.assertGreaterEqual(src.count("window.aetherUI.showToast"), 4)


if __name__ == "__main__":
    unittest.main()
