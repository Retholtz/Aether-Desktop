"""
Tests for Game-Agnostic Declarative Binds Scanner, Declarative REST Intel Engine,
Keyboard/Mouse Search-Bar Routing, Telemetry Module Hydration, and Tier 2 `.py` Game Extensions.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


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


class TestDeclarativeUnlimitedBindsScanner(unittest.TestCase):
    def test_imports_hundreds_of_binds_without_allowlist_cap_and_handles_all_devices(self):
        from core.binds_scanners.declarative import scan_declarative_bindings

        with tempfile.TemporaryDirectory() as tmp:
            # Build an XML binds file with 250 distinct bound actions across Keyboard, Mouse, GamePad, and HOTAS
            nodes = [
                '<DeployHeatSink><Primary Device="Keyboard" Key="Key_V" /><Secondary Device="{NoDevice}" Key="" /></DeployHeatSink>',
                '<GalaxyMapOpen><Primary Device="0B051C92" Key="GamePad_DPadLeft" /><Secondary Device="Keyboard" Key="Key_M" /></GalaxyMapOpen>',
                '<ShipSpotLightToggle><Primary Device="0B051C92" Key="GamePad_DPadLeft" /><Secondary Device="{NoDevice}" Key="" /></ShipSpotLightToggle>',
                '<PrimaryFire><Primary Device="Mouse" Key="Mouse_1" /><Secondary Device="{NoDevice}" Key="" /></PrimaryFire>',
                '<CustomThrottleDetent><Primary Device="ThrustmasterWarthog" Key="Joy_14" /><Secondary Device="{NoDevice}" Key="" /></CustomThrottleDetent>',
                '<OrbitLinesToggle><Primary Device="Keyboard" Key="Key_Equals" /><Secondary Device="{NoDevice}" Key="" /></OrbitLinesToggle>',
                '<NumpadMacro><Primary Device="Keyboard" Key="Key_Numpad_5"><Modifier Device="Keyboard" Key="Key_LeftControl" /></Primary><Secondary Device="{NoDevice}" Key="" /></NumpadMacro>',
            ]
            for i in range(250):
                nodes.append(
                    f'<CustomAction{i:03d}><Primary Device="Keyboard" Key="Key_F1" /><Secondary Device="{{NoDevice}}" Key="" /></CustomAction{i:03d}>'
                )
            xml_body = '<?xml version="1.0" encoding="UTF-8"?><Root PresetName="Custom">' + "".join(nodes) + "</Root>"
            (Path(tmp) / "Custom.4.2.binds").write_text(xml_body, encoding="utf-8")
            (Path(tmp) / "StartPreset.4.start").write_text("ConsoleX360\nCustom\nConsoleX360\nConsoleX360\n", encoding="utf-8")

            spec = {
                "format": "xml_primary_secondary",
                "dir": tmp,
                "file_glob": "*.binds",
                "preset_pointer_file": "StartPreset.4.start",
                "key_prefix": "Key_",
                "device_priority": ["Keyboard", "Mouse", "GamePad", "Controller"],
                "auto_import_all": True,
                "actions": {
                    "DeployHeatSink": {"phrase": "deploy heat sink", "description": "Deploy Heat Sink"},
                    "GalaxyMapOpen": {"phrase": "open galaxy map", "description": "Open Galaxy Map"},
                },
            }

            binds = scan_declarative_bindings({}, spec)
            # Zero cap: all 257 bound actions are imported!
            self.assertGreaterEqual(len(binds), 257)
            # Priority picks Keyboard Secondary over GamePad Primary for GalaxyMapOpen
            self.assertEqual(binds["open galaxy map"]["key"], "m")
            self.assertEqual(binds["open galaxy map"]["device"], "keyboard")
            # GamePad-only bind still imported with normalized gamepad token
            self.assertEqual(binds["ship spot light toggle"]["key"], "gamepad_dpad_left")
            self.assertEqual(binds["ship spot light toggle"]["device"], "gamepad")
            # Mouse bind normalized
            self.assertEqual(binds["primary fire"]["key"], "mouse_left")
            self.assertEqual(binds["primary fire"]["device"], "mouse")
            # Arbitrary HOTAS / controller bind imported cleanly
            self.assertEqual(binds["custom throttle detent"]["device"], "controller")
            self.assertEqual(binds["custom throttle detent"]["raw_device"], "ThrustmasterWarthog")
            # Key normalization matches SCANCODE_MAP
            self.assertEqual(binds["orbit lines toggle"]["key"], "=")
            self.assertEqual(binds["numpad macro"]["key"], "numpad_5")
            self.assertEqual(binds["numpad macro"]["modifiers"], ["leftctrl"])

    def test_game_manager_supports_1000_plus_keybinds_without_prompt_bloat(self):
        from core.game_manager import GameManager

        with tempfile.TemporaryDirectory() as tmp:
            pfile = Path(tmp) / "profiles.json"
            mgr = GameManager(profiles_path=str(pfile))
            many_binds = {
                f"custom action {i}": {"key": "k", "modifiers": [], "description": f"Action {i}", "source": "auto_detected"}
                for i in range(1050)
            }
            many_binds["deploy heat sink"] = {
                "key": "v", "modifiers": [], "description": "Deploy Heat Sink", "source": "manual"
            }
            mgr.add_game("Mega Sim", process_name="MegaSim.exe", keybinds=many_binds)
            mgr.set_active_profile("mega_sim")

            # All 1,051 binds stored intact
            self.assertEqual(len(mgr.get_profile("mega_sim")["keybinds"]), 1051)

            # Prompt summarizes cleanly while prioritizing manual binds & noting overflow
            ctx = mgr.get_active_game_context()
            self.assertIn("1051 total", ctx)
            self.assertIn("deploy heat sink", ctx)
            self.assertIn("additional synced keybinds active", ctx)

            # Triggering bind #1049 still works directly!
            with patch("tools.os_controls._send_scancode_event") as mock_send:
                res = mgr.trigger_action("custom action 1049")
                self.assertEqual(res["status"], "executed")
                self.assertTrue(mock_send.called)
            mgr.close()


class TestDeclarativeRestIntelEngine(unittest.TestCase):
    def test_ssrf_guard_blocks_http_and_private_ips(self):
        from core.game_intel import is_safe_public_https_url

        self.assertTrue(is_safe_public_https_url("https://spansh.co.uk/api/stations/search"))
        self.assertFalse(is_safe_public_https_url("http://spansh.co.uk/api/stations/search"))
        self.assertFalse(is_safe_public_https_url("https://127.0.0.1/secret"))
        self.assertFalse(is_safe_public_https_url("https://169.254.169.254/latest/meta-data"))
        self.assertFalse(is_safe_public_https_url("https://10.0.0.1/internal"))

    def test_declarative_intel_canonicalizes_query_and_extracts_nested_market_match(self):
        from core.game_intel import query_declarative_intel, _VALUE_RESOLVER_CACHE
        from core.game_modules import get_module_for_game

        _VALUE_RESOLVER_CACHE.clear()
        mod = get_module_for_game("elite_dangerous_64")
        self.assertIsNotNone(mod)
        market_ep = mod["intel_endpoints"]["market"]

        fake_vocab = {"values": ["Algae", "Liquid oxygen", "Tritium"]}
        fake_search_resp = {
            "count": 2,
            "results": [
                {
                    "system_name": "Sol",
                    "name": "Galileo",
                    "type": "Orbis Starport",
                    "distance": 0.0,
                    "distance_to_arrival": 507.12,
                    "large_pads": 6,
                    "medium_pads": 14,
                    "market": [
                        {"commodity": "Algae", "buy_price": 100, "supply": 500},
                        {"commodity": "Liquid oxygen", "buy_price": 868, "supply": 158418, "sell_price": 764, "demand": 1},
                    ],
                }
            ],
        }

        captured_payloads = []

        def _fake_http(url, method="GET", payload=None, timeout_sec=10.0, headers=None):
            if "field_values/market" in url:
                return fake_vocab
            captured_payloads.append(payload)
            return fake_search_resp

        with patch("core.game_intel._http_json", side_effect=_fake_http):
            res = query_declarative_intel(
                endpoint_spec=market_ep,
                query="Liquid Oxygen",
                location="Sol",
                telemetry_state={"star_system": "Sol", "needs_large_pad": True},
            )

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["canonical_query"], "Liquid oxygen")
        self.assertEqual(res["count"], 1)
        row = res["results"][0]
        self.assertEqual(row["system"], "Sol")
        self.assertEqual(row["station"], "Galileo")
        self.assertEqual(row["buy_price_cr"], 868)
        self.assertEqual(row["supply"], 158418)
        self.assertIn("Galileo", res["intel"])
        self.assertIn("158418", res["intel"])
        # Verify $if conditional filter included has_large_pad when needs_large_pad=True
        self.assertEqual(captured_payloads[0]["filters"]["has_large_pad"], {"value": True})

    def test_query_game_intel_tool_uses_declarative_rest_before_web_search(self):
        from core.game_manager import GameManager
        from tools.game_tools import register_game_tools

        with tempfile.TemporaryDirectory() as tmp:
            pfile = Path(tmp) / "profiles.json"
            mgr = GameManager(profiles_path=str(pfile))
            mgr.add_game("Elite Dangerous 64", process_name="EliteDangerous64.exe")
            mgr.set_active_profile("elite_dangerous_64")

            mock_adapter = MagicMock()
            mock_adapter.get_state.return_value = {"star_system": "Barnard's Star", "needs_large_pad": False}
            mock_adapter.get_location.return_value = {"system": "Barnard's Star"}
            mgr.active_telemetry_adapter = mock_adapter

            dispatcher = _DummyDispatcher()
            mock_engine = MagicMock()
            mock_engine.genai_client = MagicMock()
            tools = register_game_tools(dispatcher, mgr, engine=mock_engine)

            fake_live = {
                "status": "success",
                "source": "Spansh Live Commodity Market (EDDN)",
                "canonical_query": "Liquid oxygen",
                "reference_location": "Barnard's Star",
                "count": 1,
                "results": [{"system": "Sol", "station": "Galileo", "distance_ly": 5.95, "buy_price_cr": 868, "supply": 158418}],
                "intel": "1. system: Sol | station: Galileo | distance_ly: 5.95 | buy_price_cr: 868 | supply: 158418",
            }
            with patch("core.game_intel.query_declarative_intel", return_value=fake_live) as mock_q:
                out = tools["query_game_intel"](query="Liquid Oxygen", category="market")
                self.assertEqual(out["status"], "success")
                self.assertEqual(out["canonical_query"], "Liquid oxygen")
                self.assertIn("Galileo", out["intel"])
                mock_q.assert_called_once()
                # Google search fallback was NOT needed
                mock_engine.genai_client.models.generate_content.assert_not_called()
            mgr.close()


class TestTelemetryHydrationAndKbmSearchRouting(unittest.TestCase):
    def test_telemetry_factory_hydrates_module_adapter_over_scratchpad_placeholder(self):
        from core.telemetry.factory import create_telemetry_adapter
        from core.telemetry.elite import EliteTelemetryAdapter

        # Legacy profile with scratchpad_only + full path process_name
        prof = {
            "display_name": "Elite Dangerous 64",
            "process_name": r"D:\SteamLibrary\steamapps\common\Elite Dangerous\Products\elite-dangerous-odyssey-64\EliteDangerous64.exe",
            "telemetry_type": "scratchpad_only",
        }
        adapter = create_telemetry_adapter("elite_dangerous_64", profile=prof)
        self.assertIsInstance(adapter, EliteTelemetryAdapter)

    def test_kbm_search_bar_router_uses_synced_bind_clipboard_paste_and_verifies_telemetry(self):
        from core.game_nav import GameNavigator

        mock_adapter = MagicMock()
        mock_adapter.get_state.return_value = {"route_destination": "Sol", "route_jumps": 3}

        nav = GameNavigator(
            client=MagicMock(),
            target_process="EliteDangerous64.exe",
            map_controls={
                "search_routing": {
                    "map_open_actions": ["open galaxy map"],
                    "map_key": "m",
                    "open_wait_sec": 0.01,
                    "search_box_visual_hint": "Search box",
                    "input_method": "clipboard_paste",
                    "submit_key": "enter",
                    "select_result_wait_sec": 0.01,
                    "plot_visual_hint": "Plot Route button",
                    "plot_hold_sec": 0.05,
                    "verify_telemetry_field": "route_destination",
                    "verify_timeout_sec": 0.2,
                }
            },
            keybinds={
                "open galaxy map": {"key": "f9", "modifiers": [], "device": "keyboard", "action_id": "GalaxyMapOpen"}
            },
            telemetry_adapter=mock_adapter,
        )

        with patch("tools.clipboard.set_clipboard_text", return_value=True) as mock_clip, \
             patch("tools.os_controls.focus_window"), \
             patch("core.game_nav.send_directinput_key") as mock_key, \
             patch("tools.os_controls.send_directinput_combo") as mock_combo, \
             patch("core.game_nav.move_mouse_absolute") as mock_move, \
             patch("core.game_nav.click_mouse_button") as mock_click, \
             patch.object(nav, "capture_viewport", return_value=(b"jpg", 1920, 1080)), \
             patch.object(nav, "is_map_screen", return_value=False), \
             patch.object(nav, "locate_ui_element_point", side_effect=[(500, 120), (1650, 620)]):
            res = nav.search_and_route_destination("Sol")

        self.assertEqual(res["status"], "success")
        self.assertTrue(res["verified"])
        self.assertEqual(res["route_jumps"], 3)
        mock_clip.assert_called_once_with("Sol")
        # Opened Galaxy Map with the user's synced F9 key instead of hardcoded 'm'
        mock_key.assert_any_call("f9", duration_sec=0.12)
        mock_combo.assert_any_call(["ctrl"], "v", hold_duration=0.06)
        mock_move.assert_any_call(500, 120)
        mock_move.assert_any_call(1650, 620)
        self.assertGreaterEqual(mock_click.call_count, 2)

    def test_kbm_search_bar_post_input_keys_and_honest_unverified_status(self):
        from core.game_nav import GameNavigator

        mock_adapter = MagicMock()
        mock_adapter.get_state.return_value = {"route_destination": None}

        nav = GameNavigator(
            client=MagicMock(),
            target_process="EliteDangerous64.exe",
            map_controls={
                "search_routing": {
                    "map_key": "m",
                    "open_wait_sec": 0.01,
                    "input_method": "clipboard_paste",
                    "submit_key": "enter",
                    "select_result_wait_sec": 0.01,
                    "post_input_keys": [
                        {"key": "down", "delay_ms": 10},
                        {"key": "enter", "delay_ms": 10},
                    ],
                    "plot_key": "space",
                    "plot_hold_sec": 0.02,
                    "verify_telemetry_field": "route_destination",
                    "verify_timeout_sec": 0.05,
                }
            },
            telemetry_adapter=mock_adapter,
        )

        with patch("tools.clipboard.set_clipboard_text", return_value=True), \
             patch("tools.os_controls.focus_window"), \
             patch("core.game_nav.send_directinput_key") as mock_key, \
             patch("tools.os_controls.send_directinput_combo"), \
             patch.object(nav, "capture_viewport", return_value=(b"jpg", 1920, 1080)), \
             patch.object(nav, "is_map_screen", return_value=True):
            res = nav.search_and_route_destination("HIP 74164")

        self.assertEqual(res["status"], "partial")
        self.assertFalse(res["verified"])
        key_calls = [c.args[0] for c in mock_key.call_args_list]
        self.assertIn("down", key_calls)
        down_idx = key_calls.index("down")
        self.assertEqual(key_calls[down_idx + 1], "enter")


class TestUserMacrosAndVisionClick(unittest.TestCase):
    def test_macro_runner_repeat_and_vision_click_honest_failure(self):
        from core.game_macro_runner import MacroRunner

        runner = MacroRunner()
        profile = {
            "display_name": "Elite Dangerous",
            "keybinds": {
                "divert power to engines": {"key": "up", "modifiers": [], "device": "keyboard"}
            },
            "sequences": {
                "spam engines": {
                    "steps": [{"action": "trigger_bind", "phrase": "divert power to engines", "repeat": 4, "interval_ms": 5}],
                    "sync": True,
                },
                "take us out": {
                    "condition": "state.docked",
                    "steps": [{"action": "vision_click", "target": "Auto Launch", "abort_if_not_found": True}],
                    "sync": True,
                },
            },
        }

        with patch("tools.os_controls.send_directinput_key", return_value=True) as mock_key:
            res = runner.execute_action("spam engines", profile)
        self.assertEqual(res["status"], "executed")
        self.assertEqual(mock_key.call_count, 4)

        mock_adapter = MagicMock()
        mock_adapter.get_state_flags.return_value = {"docked": True}

        # Missing UI element must NOT falsely claim executed
        with patch("core.game_nav.UniversalGameNavigator.locate_ui_element_point", return_value=None):
            res_miss = runner.execute_action("take us out", profile, telemetry_adapter=mock_adapter, vision_client=MagicMock())
        self.assertEqual(res_miss["status"], "element_not_found")

        # Found UI element moves mouse, clicks, and returns executed
        with patch("core.game_nav.UniversalGameNavigator.locate_ui_element_point", return_value=(640, 480)), \
             patch("tools.os_controls.move_mouse_absolute") as mock_move, \
             patch("tools.os_controls.click_mouse_button") as mock_click:
            res_hit = runner.execute_action("take us out", profile, telemetry_adapter=mock_adapter, vision_client=MagicMock())
        self.assertEqual(res_hit["status"], "executed")
        mock_move.assert_called_with(640, 480)
        mock_click.assert_called_once()

    def test_save_list_delete_user_macro_and_click_ui_tool(self):
        from core.game_manager import GameManager
        from tools.game_tools import register_game_tools

        with tempfile.TemporaryDirectory() as tmp:
            mgr = GameManager(profiles_path=str(Path(tmp) / "profiles.json"))
            mgr.add_game("Elite Dangerous", process_name="EliteDangerous64.exe")
            mgr.set_active_profile("elite_dangerous")

            dispatcher = _DummyDispatcher()
            mock_engine = MagicMock()
            mock_engine.genai_client = MagicMock()
            tools = register_game_tools(dispatcher, mgr, engine=mock_engine)

            save_res = tools["save_game_macro"](
                name="full engines",
                steps=[{"action": "press", "key": "x", "repeat": 5, "interval_ms": 20}],
                description="Press X 5 times",
            )
            self.assertEqual(save_res["status"], "success")

            list_res = tools["list_game_macros"]()
            self.assertIn("full engines", list_res["macros"])

            with patch("core.game_nav.UniversalGameNavigator.locate_ui_element_point", return_value=(300, 400)), \
                 patch("tools.os_controls.focus_window"), \
                 patch("tools.os_controls.move_mouse_absolute") as mock_move, \
                 patch("tools.os_controls.click_mouse_button") as mock_click:
                click_res = tools["click_game_ui_element"](target_description="Auto Launch")
            self.assertEqual(click_res["status"], "executed")
            mock_move.assert_called_with(300, 400)
            mock_click.assert_called_once()

            del_res = tools["delete_game_macro"](name="full engines")
            self.assertEqual(del_res["status"], "success")
            mgr.close()

    def test_gui_bridge_macros_crud_ai_generator_and_frontend_ui(self):
        from core.game_manager import GameManager
        from core.gui_bridge import GUIBridge

        with tempfile.TemporaryDirectory() as tmp:
            mgr = GameManager(profiles_path=str(Path(tmp) / "profiles.json"))
            mgr.add_game("Elite Dangerous", process_name="EliteDangerous64.exe")
            mgr.set_active_profile("elite_dangerous")

            mock_eng = MagicMock()
            mock_resp = MagicMock()
            mock_resp.text = json.dumps({
                "name": "combat pips",
                "aliases": ["weapons full"],
                "description": "Balance power then put 4 pips to weapons",
                "condition": "",
                "ack": "Power diverted to weapons.",
                "steps": [{"action": "press", "key": "right", "repeat": 4, "interval_ms": 20}],
            })
            mock_eng.genai_client.models.generate_content.return_value = mock_resp

            bridge = GUIBridge(mock_eng)
            bridge.game_mgr = mgr

            # Module default "take us out" appears automatically
            initial = bridge.get_game_macros("elite_dangerous")
            self.assertTrue(initial["success"])
            self.assertIn("take us out", initial["macros"])

            # Flexible intent directive present in LLM context
            ctx = mgr.get_active_game_context()
            self.assertIn("FLEXIBLE INTENT MATCHING", ctx)
            self.assertIn("take us out", ctx)

            # Plain-English AI macro generation (including optional live game screenshot attachment)
            with patch("core.game_nav.UniversalGameNavigator.capture_viewport", return_value=(b"\xff\xd8fakejpg", 1920, 1080)):
                gen = bridge.generate_game_macro_from_text(
                    "elite_dangerous",
                    "Press right arrow 4 times with 20ms pause",
                    include_screenshot=True,
                )
            self.assertTrue(gen["success"])
            self.assertTrue(gen["used_screenshot"])
            self.assertEqual(gen["macro"]["name"], "combat pips")

            # Save + rename + delete via bridge
            saved = bridge.save_game_macro(
                "elite_dangerous",
                "combat pips",
                gen["macro"]["steps"],
                description=gen["macro"]["description"],
                aliases=gen["macro"]["aliases"],
            )
            self.assertTrue(saved["success"])
            self.assertIn("combat pips", saved["macros"])

            deleted = bridge.delete_game_macro("elite_dangerous", "combat pips")
            self.assertTrue(deleted["success"])
            self.assertNotIn("combat pips", deleted["macros"])
            mgr.close()

        # Verify Games tab HTML & JS wire the Macros section & Add/Edit modal
        repo_root = Path(__file__).resolve().parent.parent
        html = (repo_root / "ui" / "static" / "index.html").read_text(encoding="utf-8")
        js = (repo_root / "ui" / "static" / "games_tab.js").read_text(encoding="utf-8")
        self.assertIn("table-game-macros", html)
        for el_id in (
            "input-filter-keybinds",
            "tbody-game-macros",
            "btn-add-macro",
            "modal-edit-macro",
            "input-macro-ai-prompt",
            "chk-macro-ai-screenshot",
            "btn-generate-macro-steps",
            "textarea-macro-steps",
            "btn-confirm-save-macro",
            "select-game-copilot-vibe",
            "textarea-game-copilot-personality",
        ):
            self.assertIn(el_id, html)
            self.assertIn(el_id, js)

    def test_shorthand_macro_normalization_and_bind_validation(self):
        from core.game_macro_runner import normalize_and_validate_macro_steps

        profile = {
            "keybinds": {
                "left panel": {"key": "1", "modifiers": []},
                "ui next page": {"key": "e", "modifiers": []},
            }
        }
        # Shorthand LLM output normalizes cleanly when binds exist
        steps, errs = normalize_and_validate_macro_steps(
            [
                {"trigger_bind": {"phrase": "left panel"}},
                {"wait": 500},
                {"press": "space"},
            ],
            profile=profile,
        )
        self.assertEqual(errs, [])
        self.assertEqual(steps[0], {"action": "trigger_bind", "phrase": "left panel"})
        self.assertEqual(steps[1], {"action": "wait", "delay_ms": 500})
        self.assertEqual(steps[2], {"action": "press", "key": "space"})

        # Unknown bind phrase is rejected with helpful close-match hint
        _, bad_errs = normalize_and_validate_macro_steps(
            [{"trigger_bind": {"phrase": "cycle next page"}}],
            profile=profile,
        )
        self.assertEqual(len(bad_errs), 1)
        self.assertIn("unknown keybind", bad_errs[0].lower())
        self.assertIn("ui next page", bad_errs[0])

    def test_unified_chat_log_across_silent_rotations_and_copilot_vibe_directive(self):
        from core.session_lifecycle import SessionLifecycleManager
        from core.game_manager import GameManager

        with tempfile.TemporaryDirectory() as tmp:
            chats_dir = Path(tmp) / "chats"
            lc = SessionLifecycleManager(chats_dir=str(chats_dir))
            initial_id = lc.session_id

            lc.record_turn("user", "Open Galaxy Map")
            lc.record_turn("assistant", "Galaxy map up!")
            lc.reset_metrics()  # Silent WebSocket rotation #1

            lc.record_turn("user", "All stop")
            lc.record_turn("assistant", "Killed the engines!")
            lc.reset_metrics()  # Silent WebSocket rotation #2

            # Still ONE unified session file with all 4 turns
            self.assertEqual(lc.session_id, initial_id)
            files = list(chats_dir.glob("session_*.json"))
            self.assertEqual(len(files), 1)
            saved = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(len(saved["turns"]), 4)
            self.assertEqual([t["turn_id"] for t in saved["turns"]], [1, 2, 3, 4])

            # Co-Pilot vibe + unrestricted free-text personality + anti-rote & adaptive pacing
            mgr = GameManager(profiles_path=str(Path(tmp) / "profiles.json"))
            mgr.add_game("Space Sim", process_name="SpaceSim.exe")
            mgr.set_active_profile("space_sim")
            res = mgr.set_copilot_vibe("space_sim", "witty", "Dry humor like TARS from Interstellar.\nLoves banter.")
            self.assertTrue(res["success"])
            ctx = mgr.get_active_game_context()
            self.assertIn("ANTI-ROTE VARIETY RULE", ctx)
            self.assertIn("TARS from Interstellar", ctx)
            self.assertIn("DO NOT cut yourself off at 2", ctx)
            mgr.close()


class TestTier2PythonGameExtensions(unittest.TestCase):
    def test_untrusted_extension_blocked_trusted_loads_and_tamper_revokes(self):
        from core.game_extensions import (
            list_game_extensions,
            load_trusted_extension,
            revoke_game_extension,
            trust_game_extension,
        )

        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = Path(tmp) / "star_sim"
            mod_dir.mkdir(parents=True)
            ext_file = mod_dir / "extension.py"
            ext_file.write_text(
                "from core.game_extensions import GameExtension\n"
                "class StarSimExt(GameExtension):\n"
                "    module_id = 'star_sim'\n"
                "    def query_intel(self, query, category='market', location=None, telemetry=None, profile=None):\n"
                "        return {'status': 'success', 'intel': f'EXT:{query}@{location}'}\n",
                encoding="utf-8",
            )

            # 1. Untrusted by default -> MUST NOT execute
            self.assertIsNone(load_trusted_extension("star_sim", base_dir=tmp))
            listed = list_game_extensions(base_dir=tmp)
            self.assertEqual(len(listed), 1)
            self.assertFalse(listed[0]["trusted"])

            # 2. Explicitly trust -> loads and runs
            t_res = trust_game_extension("star_sim", base_dir=tmp)
            self.assertTrue(t_res["success"])
            ext = load_trusted_extension("star_sim", base_dir=tmp)
            self.assertIsNotNone(ext)
            out = ext.query_intel("Quantanium", location="Stanton")
            self.assertEqual(out["intel"], "EXT:Quantanium@Stanton")

            # 3. Tamper with the .py file -> SHA-256 mismatch auto-blocks!
            ext_file.write_text(ext_file.read_text(encoding="utf-8") + "\n# modified\n", encoding="utf-8")
            self.assertIsNone(load_trusted_extension("star_sim", base_dir=tmp))
            listed2 = list_game_extensions(base_dir=tmp)
            self.assertTrue(listed2[0]["hash_changed"])
            self.assertFalse(listed2[0]["trusted"])

            # 4. Revoke cleans registry
            revoke_game_extension("star_sim", base_dir=tmp)
            self.assertIsNone(load_trusted_extension("star_sim", base_dir=tmp))


if __name__ == "__main__":
    unittest.main()
