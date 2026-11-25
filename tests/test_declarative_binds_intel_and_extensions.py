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
        from core.telemetry.declarative import DeclarativeFileTelemetryAdapter

        # Legacy profile with scratchpad_only + full path process_name hydrates to pure declarative adapter
        prof = {
            "display_name": "Elite Dangerous 64",
            "process_name": r"D:\SteamLibrary\steamapps\common\Elite Dangerous\Products\elite-dangerous-odyssey-64\EliteDangerous64.exe",
            "telemetry_type": "scratchpad_only",
        }
        adapter = create_telemetry_adapter("elite_dangerous_64", profile=prof)
        self.assertIsInstance(adapter, DeclarativeFileTelemetryAdapter)

        # Verify declarative FSDJump extracts Powerplay & controlling faction purely from JSON rules
        adapter._parse_event_line(json.dumps({
            "event": "FSDJump",
            "StarSystem": "Kipsigines",
            "Body": "Kipsigines A",
            "Docked": False,
            "ControllingPower": "Yuri Grom",
            "PowerplayState": "Exploited",
            "SystemFaction": {"Name": "Terra-EX Astro Corp"},
            "SystemAllegiance": "Federation",
            "SystemSecurity_Localised": "Medium",
        }))
        st = adapter.get_state()
        self.assertEqual(st["star_system"], "Kipsigines")
        self.assertEqual(st["controlling_power"], "Yuri Grom")
        self.assertEqual(st["powerplay_state"], "Exploited")
        self.assertEqual(st["system_faction"], "Terra-EX Astro Corp")
        summary = adapter.get_summary_prompt_context()
        self.assertIn("Yuri Grom", summary)
        self.assertIn("Terra-EX Astro Corp", summary)

        # Verify Spansh navigation JSON module includes controlling_power & controlling_minor_faction
        from core.game_modules import get_module_for_game
        mod = get_module_for_game("elite_dangerous_64", profile=prof)
        nav_fields = mod["intel_endpoints"]["navigation"]["result_fields"]
        self.assertEqual(nav_fields.get("controlling_power"), "controlling_power")
        self.assertEqual(nav_fields.get("power_state"), "power_state")
        self.assertEqual(nav_fields.get("controlling_minor_faction"), "controlling_minor_faction")

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


class TestTypedMacroSchemaAndAdHocSequenceTool(unittest.TestCase):
    def test_save_and_run_key_sequence_have_explicit_typed_step_schema_and_execute(self):
        from core.game_manager import GameManager
        from core.game_engine import GameEngine
        from tools.game_tools import register_game_tools

        class _CapturingDispatcher:
            def __init__(self):
                self.declarations = {}
                self.handlers = {}

            def register(self, name, description="", parameters=None):
                def _dec(fn):
                    self.declarations[name] = {
                        "name": name,
                        "description": description,
                        "parameters": parameters,
                    }
                    self.handlers[name] = fn
                    return fn
                return _dec

        with tempfile.TemporaryDirectory() as tmp:
            mgr = GameManager(profiles_path=str(Path(tmp) / "profiles.json"))
            mgr.add_game("Space Sim", process_name="SpaceSim.exe")
            mgr.set_active_profile("space_sim")

            disp = _CapturingDispatcher()
            tools = register_game_tools(disp, mgr, engine=MagicMock())

            # 1. Both tools MUST expose explicit typed items.properties so Gemini Live can author steps
            for tool_name in ("save_game_macro", "run_game_key_sequence"):
                self.assertIn(tool_name, disp.declarations)
                self.assertIn(tool_name, GameEngine.CORE_GAME_TOOL_NAMES)
                step_items = disp.declarations[tool_name]["parameters"]["properties"]["steps"]["items"]
                self.assertIn("properties", step_items)
                for field in ("action", "key", "repeat", "interval_ms", "hold_ms", "phrase", "target", "delay_ms"):
                    self.assertIn(field, step_items["properties"])

            # 2. Execute ad-hoc 3x W + Space sequence directly
            steps = [
                {"action": "press", "key": "w", "repeat": 3, "interval_ms": 5},
                {"action": "press", "key": "space"},
            ]
            with patch("tools.os_controls.focus_window"), \
                 patch("tools.os_controls.send_directinput_key", return_value=True) as mock_key:
                res = tools["run_game_key_sequence"](steps=steps, description="Navigate menu and confirm")
            self.assertEqual(res["status"], "executed")
            pressed = [c.args[0] for c in mock_key.call_args_list]
            self.assertEqual(pressed, ["w", "w", "w", "space"])

            # 3. Save macro + run_now=True saves AND executes cleanly
            with patch("tools.os_controls.focus_window"), \
                 patch("tools.os_controls.send_directinput_key", return_value=True) as mock_key2:
                save_res = tools["save_game_macro"](
                    name="refuel",
                    steps=steps,
                    description="Press W 3 times then spacebar",
                    run_now=True,
                )
            self.assertEqual(save_res["status"], "success")
            self.assertEqual(save_res["step_count"], 2)
            self.assertEqual(save_res["execution"]["status"], "executed")
            self.assertEqual([c.args[0] for c in mock_key2.call_args_list], ["w", "w", "w", "space"])

            # 4. click_game_ui_element calls relative DirectInput wake nudge before clicking
            with patch("core.game_nav.UniversalGameNavigator.locate_ui_element_point", return_value=(400, 300)), \
                 patch("tools.os_controls.focus_window"), \
                 patch("tools.os_controls.move_mouse_absolute") as mock_move, \
                 patch("tools.os_controls.nudge_mouse_relative") as mock_nudge, \
                 patch("tools.os_controls.click_mouse_button") as mock_click:
                click_res = tools["click_game_ui_element"](target_description="Refuel button")
            self.assertEqual(click_res["status"], "executed")
            mock_move.assert_called_with(400, 300)
            mock_nudge.assert_called_once_with(1, 0)
            mock_click.assert_called_once()
            mgr.close()

    def test_pre_plot_cursor_nudge_and_closed_loop_vision_ui_task_and_extension_import(self):
        from core.game_nav import GameNavigator
        from core.game_engine import GameEngine
        from core.game_extensions import import_game_extension, list_game_extensions, delete_game_extension, trust_game_extension
        from core.game_manager import GameManager
        from tools.game_tools import register_game_tools

        # 1. search_and_route_destination pre-plot cursor center + nudge + space hold
        mock_adapter = MagicMock()
        mock_adapter.get_state.return_value = {"route_destination": "Shinrarta Dezhra", "route_jumps": 3}
        nav = GameNavigator(
            client=MagicMock(),
            target_process="SpaceSim.exe",
            map_controls={
                "search_routing": {
                    "map_key": "m",
                    "open_wait_sec": 0.01,
                    "input_method": "clipboard_paste",
                    "submit_key": "",
                    "select_result_wait_sec": 0.01,
                    "post_input_keys": [{"key": "down", "delay_ms": 10}, {"key": "enter", "delay_ms": 10}],
                    "prefer_plot_key_first": True,
                    "pre_plot_move_center": True,
                    "pre_plot_mouse_nudge": [15, 10],
                    "plot_key": "space",
                    "plot_hold_sec": 0.05,
                    "verify_telemetry_field": "route_destination",
                    "verify_timeout_sec": 0.2,
                }
            },
            telemetry_adapter=mock_adapter,
        )
        with patch("tools.clipboard.set_clipboard_text", return_value=True), \
             patch("tools.os_controls.focus_window"), \
             patch("tools.os_controls.send_directinput_combo"), \
             patch("tools.os_controls.nudge_mouse_relative") as mock_nudge, \
             patch("core.game_nav.move_mouse_absolute") as mock_move, \
             patch("core.game_nav.send_directinput_key") as mock_key, \
             patch.object(nav, "capture_viewport", return_value=(b"jpg", 1920, 1080)), \
             patch.object(nav, "is_map_screen", return_value=True):
            route_res = nav.search_and_route_destination("Shinrarta Dezhra")

        self.assertEqual(route_res["status"], "success")
        self.assertTrue(route_res["verified"])
        mock_move.assert_any_call(960, 540)
        mock_nudge.assert_any_call(15, 10)
        self.assertIn("space", [c.args[0] for c in mock_key.call_args_list])

        # 2. Closed-loop Vision UI/Menu Navigator (adapts to current highlight cursor & visually verifies)
        vision_steps = iter([
            {"highlighted_now": "Auto Launch", "observation": "Two rows below Refuel", "action": "KEYS", "keys": ["w", "w"], "wait_ms": 50},
            {"highlighted_now": "Refuel icon", "observation": "Refuel icon highlighted", "action": "KEYS", "keys": ["space"], "wait_ms": 50},
            {"highlighted_now": "Refuel icon", "observation": "Refuel completed and disabled", "action": "VERIFIED_DONE", "reason": "Ship refueled to 100%."},
        ])
        with patch("tools.os_controls.focus_window"), \
             patch("core.game_nav.send_directinput_key") as mock_ui_key, \
             patch.object(nav, "capture_viewport", return_value=(b"jpg", 1920, 1080)), \
             patch.object(nav, "_call_vision_json", side_effect=lambda *a, **kw: next(vision_steps)):
            ui_res = nav.complete_ui_task("Refuel ship", max_steps=5)

        self.assertEqual(ui_res["status"], "verified_done")
        self.assertTrue(ui_res["verified"])
        self.assertEqual(ui_res["steps_executed"], 2)
        self.assertEqual([c.args[0] for c in mock_ui_key.call_args_list], ["w", "w", "space"])
        self.assertIn("complete_game_ui_task", GameEngine.CORE_GAME_TOOL_NAMES)

        # Spotter-only blocks complete_game_ui_task
        with tempfile.TemporaryDirectory() as tmp:
            mgr = GameManager(profiles_path=str(Path(tmp) / "profiles.json"))
            mgr.add_game("Competitive FPS", process_name="FPS.exe")
            mgr.set_active_profile("competitive_fps")
            mgr.set_input_mode("competitive_fps", "spotter_only")
            disp = _DummyDispatcher()
            tools = register_game_tools(disp, mgr, engine=MagicMock())
            blocked = tools["complete_game_ui_task"](goal="Open inventory")
            self.assertEqual(blocked["status"], "blocked_by_spotter_only")
            mgr.close()

        # 3. Tier 2 .py extension import -> untrusted -> trust -> delete + Games UI anchors
        with tempfile.TemporaryDirectory() as tmp:
            src_py = Path(tmp) / "my_custom_plugin.py"
            src_py.write_text(
                "from core.game_extensions import GameExtension\n"
                "class MyExt(GameExtension):\n"
                "    module_id = 'space_sim'\n",
                encoding="utf-8",
            )
            ext_root = str(Path(tmp) / "ext_store")
            imp = import_game_extension("space_sim", str(src_py), base_dir=ext_root)
            self.assertTrue(imp["success"])
            self.assertFalse(imp["trusted"])
            self.assertEqual(len(list_game_extensions(base_dir=ext_root)), 1)
            self.assertTrue(trust_game_extension("space_sim", base_dir=ext_root)["success"])
            self.assertTrue(list_game_extensions(base_dir=ext_root)[0]["trusted"])
            self.assertTrue(delete_game_extension("space_sim", base_dir=ext_root)["success"])
            self.assertEqual(len(list_game_extensions(base_dir=ext_root)), 0)

        html_src = Path("ui/static/index.html").read_text(encoding="utf-8")
        js_src = Path("ui/static/games_tab.js").read_text(encoding="utf-8")
        self.assertIn("btn-import-game-extension", html_src)
        self.assertIn("game-extensions-list", html_src)
        self.assertIn("import_game_extension_dialog", js_src)
        self.assertIn("trust_game_extension", js_src)

        # 4. Copy AI Module Authoring Prompt endpoint, UI button, and Guide doc
        from core.game_modules import build_module_authoring_prompt
        from core.gui_bridge import GUIBridge
        prompt_txt = build_module_authoring_prompt("No Man's Sky")
        self.assertIn("No Man's Sky", prompt_txt)
        self.assertIn("schema_version", prompt_txt)
        self.assertIn("declarative_file", prompt_txt)
        self.assertIn("intel_endpoints", prompt_txt)

        with tempfile.TemporaryDirectory() as tmp:
            mgr2 = GameManager(profiles_path=str(Path(tmp) / "profiles.json"))
            mgr2.add_game("Star Citizen", process_name="StarCitizen.exe")
            mgr2.set_active_profile("star_citizen")
            bridge = GUIBridge(MagicMock())
            bridge.game_mgr = mgr2
            p_res = bridge.get_game_module_ai_prompt("star_citizen")
            self.assertTrue(p_res["success"])
            self.assertEqual(p_res["game_name"], "Star Citizen")
            self.assertIn("Star Citizen", p_res["prompt"])
            st_none = bridge.get_game_module_status("star_citizen")
            self.assertFalse(st_none["has_module"])
            st_ed = bridge.get_game_module_status("elite_dangerous")
            self.assertTrue(st_ed["has_module"])
            self.assertTrue(st_ed["is_bundled"])
            mgr2.close()

        self.assertIn("btn-copy-module-ai-prompt", html_src)
        self.assertIn("get_game_module_ai_prompt", js_src)
        self.assertIn("get_game_module_status", js_src)
        self.assertTrue(Path("docs/GAME_MODULES_GUIDE.md").is_file())

    def test_complete_ui_task_cleanup_keys_and_self_learning_fast_replay(self):
        from core.game_nav import GameNavigator

        nav = GameNavigator(client=MagicMock(), target_process="SpaceSim.exe", game_name="Space Sim")

        # 1. Cold run: opens panel, requests docking, confirms, and closes panel via cleanup_keys=["1"]
        cold_steps = iter([
            {"highlighted_now": "none", "observation": "Cockpit", "action": "KEYS", "keys": ["1", "e", "e"], "wait_ms": 50},
            {"highlighted_now": "Station", "observation": "Contacts open", "action": "KEYS", "keys": ["space", "s", "space"], "wait_ms": 50},
            {"highlighted_now": "Request Docking", "observation": "Docking request granted", "action": "VERIFIED_DONE", "cleanup_keys": ["1"], "reason": "Docking request granted."},
        ])
        with patch("tools.os_controls.focus_window"), \
             patch("core.game_nav.send_directinput_key") as mock_cold_key, \
             patch.object(nav, "capture_viewport", return_value=(b"jpg", 1920, 1080)), \
             patch.object(nav, "_call_vision_json", side_effect=lambda *a, **kw: next(cold_steps)):
            cold_res = nav.complete_ui_task("Request Docking", max_steps=5)

        self.assertEqual(cold_res["status"], "verified_done")
        self.assertTrue(cold_res["just_executed"])
        self.assertFalse(cold_res["already_done"])
        self.assertEqual(cold_res["cleanup_keys"], ["1"])
        self.assertIn("learned_recipe", cold_res)
        self.assertEqual([c.args[0] for c in mock_cold_key.call_args_list], ["1", "e", "e", "space", "s", "space", "1"])

        # 2. Warm run: replays learned_recipe directly + 1-shot Vision verify + closes panel!
        vision_calls = []

        def _warm_vision(*args, **kwargs):
            vision_calls.append(args[1])
            return {"completed": True, "observation": "Docking request granted.", "cleanup_keys": ["1"]}

        with patch("tools.os_controls.focus_window"), \
             patch("core.game_nav.send_directinput_key") as mock_warm_key, \
             patch.object(nav, "capture_viewport", return_value=(b"jpg", 1920, 1080)), \
             patch.object(nav, "_call_vision_json", side_effect=_warm_vision):
            warm_res = nav.complete_ui_task("Request Docking", learned_recipe=cold_res["learned_recipe"])

        self.assertEqual(warm_res["status"], "verified_done")
        self.assertTrue(warm_res["replayed_recipe"])
        self.assertEqual(len(vision_calls), 1)  # Only ONE 1-shot verify call instead of 3+!
        self.assertEqual(warm_res["cleanup_keys"], ["1"])
        self.assertEqual([c.args[0] for c in mock_warm_key.call_args_list], ["1", "e", "e", "space", "s", "space", "1"])

    def test_declarative_poi_tracking_auto_resolution_and_map_bookmark_routing(self):
        from core.telemetry.declarative import DeclarativeFileTelemetryAdapter
        from core.game_manager import GameManager
        from core.game_engine import GameEngine
        from core.game_nav import GameNavigator
        from tools.game_tools import register_game_tools

        # 1. Declarative poi_trackers extracts carrier name/callsign/system purely from JSON rules
        adapter = DeclarativeFileTelemetryAdapter(game_id="elite_dangerous", journal_dir=tempfile.gettempdir())
        adapter._parse_event_line(json.dumps({
            "event": "CarrierStats",
            "Name": "LONE WOLF",
            "Callsign": "T9J-44F",
        }))
        adapter._parse_event_line(json.dumps({
            "event": "CarrierLocation",
            "StarSystem": "Col 285 Sector SJ-Q d5-68",
        }))
        t_pois = adapter.get_state().get("tracked_pois") or {}
        self.assertIn("fleet_carrier", t_pois)
        self.assertEqual(t_pois["fleet_carrier"]["name"], "LONE WOLF")
        self.assertEqual(t_pois["fleet_carrier"]["callsign"], "T9J-44F")
        self.assertEqual(t_pois["fleet_carrier"]["system"], "Col 285 Sector SJ-Q d5-68")
        self.assertIn("Col 285 Sector SJ-Q d5-68", adapter.get_summary_prompt_context())

        # 2. remember_game_poi + automatic POI -> parent system resolution in search_and_route_destination
        self.assertIn("route_to_map_bookmark", GameEngine.CORE_GAME_TOOL_NAMES)
        self.assertIn("remember_game_poi", GameEngine.CORE_GAME_TOOL_NAMES)

        with tempfile.TemporaryDirectory() as tmp:
            mgr = GameManager(profiles_path=str(Path(tmp) / "profiles.json"))
            mgr.add_game("Space Sim", process_name="SpaceSim.exe")
            mgr.set_active_profile("space_sim")
            disp = _DummyDispatcher()
            tools = register_game_tools(disp, mgr, engine=MagicMock())

            rem = tools["remember_game_poi"](
                name="Lone Wolf",
                location="Col 285 Sector SJ-Q d5-68",
                aliases=["T9J-44F", "my carrier"],
            )
            self.assertEqual(rem["status"], "success")
            self.assertIn("Col 285 Sector SJ-Q d5-68", mgr.get_active_game_context())

            routed_targets = []
            with patch("core.game_nav.GameNavigator.search_and_route_destination", side_effect=lambda destination_name: (routed_targets.append(destination_name) or {"status": "success", "destination": destination_name, "message": "Plotted."})):
                r_res = tools["search_and_route_destination"](destination_name="Lone Wolf")
            self.assertEqual(routed_targets, ["Col 285 Sector SJ-Q d5-68"])
            self.assertEqual(r_res["resolved_system"], "Col 285 Sector SJ-Q d5-68")
            mgr.close()

        # 3. route_to_map_bookmark switches to requested category and plots item #3
        mock_adapter = MagicMock()
        mock_adapter.get_state.return_value = {"route_destination": "BOEWNST KS-S C20-959", "route_jumps": 12}
        nav = GameNavigator(
            client=MagicMock(),
            target_process="SpaceSim.exe",
            map_controls={
                "search_routing": {"verify_telemetry_field": "route_destination"},
                "bookmarks": {
                    "default_category": "favorites",
                    "categories": ["favorites", "systems", "bodies", "stations", "settlements"],
                    "plot_hold_sec": 0.05,
                },
            },
            telemetry_adapter=mock_adapter,
        )
        bm_steps = iter([
            {"state": "NEED_SWITCH_CATEGORY", "point": [260, 82], "observation": "Favourites open, need Systems"},
            {"state": "FOUND_BOOKMARK", "point": [420, 200], "bookmark_title": "BOEWNST KS-S C20-959", "system_name": "BOEWNST KS-S C20-959", "observation": "Found item #3"},
        ])
        with patch("tools.os_controls.focus_window"), \
             patch("tools.os_controls.nudge_mouse_relative"), \
             patch("core.game_nav.move_mouse_absolute") as mock_move, \
             patch("core.game_nav.click_mouse_button") as mock_click, \
             patch.object(nav, "capture_viewport", return_value=(b"jpg", 1920, 1080)), \
             patch.object(nav, "is_map_screen", return_value=True), \
             patch.object(nav, "_call_vision_json", side_effect=lambda *a, **kw: next(bm_steps)):
            bm_res = nav.route_to_map_bookmark(bookmark_number=3, category="system")

        self.assertEqual(bm_res["status"], "success")
        self.assertTrue(bm_res["verified"])
        self.assertEqual(bm_res["category"], "Systems")
        self.assertEqual(bm_res["destination"], "BOEWNST KS-S C20-959")
        self.assertGreaterEqual(mock_click.call_count, 2)

    def test_rename_game_and_voice_poi_override_precedence(self):
        """Renaming display_name preserves internal ID/bindings, and voice POI updates override stale telemetry in-place."""
        from core.game_manager import GameManager
        from tools.game_tools import register_game_tools

        class _DummyDispatcher:
            def __init__(self):
                self.tools = {}
            def register(self, name, description="", parameters=None):
                def _dec(fn):
                    self.tools[name] = fn
                    return fn
                return _dec

        with tempfile.TemporaryDirectory() as tmp:
            mgr = GameManager(profiles_path=str(Path(tmp) / "profiles.json"))
            mgr.add_game("Elite Dangerous 64", process_name="EliteDangerous64.exe")
            mgr.set_active_profile("elite_dangerous_64")

            # Rename display_name only
            ren = mgr.rename_game("elite_dangerous_64", "Elite Dangerous")
            self.assertTrue(ren["success"])
            prof = mgr.get_profile("elite_dangerous_64")
            self.assertEqual(prof["display_name"], "Elite Dangerous")
            self.assertEqual(prof.get("process_name"), "EliteDangerous64.exe")

            # Stop any background adapter and clear host-journal POIs before attaching mock telemetry
            if mgr.active_telemetry_adapter is not None:
                mgr.active_telemetry_adapter.stop()
            prof["known_pois"] = {}

            # Attach mock live telemetry with an older Fleet Carrier location
            mock_tel = MagicMock()
            mock_tel.get_state.return_value = {
                "tracked_pois": {
                    "fleet_carrier": {
                        "name": "LONE WOLF",
                        "callsign": "T9J-44F",
                        "system": "Old System",
                        "aliases": ["fleet carrier", "carrier", "my carrier"],
                        "updated_at": 100.0,
                    }
                }
            }
            mgr.active_telemetry_adapter = mock_tel

            disp = _DummyDispatcher()
            tools = register_game_tools(disp, mgr, engine=MagicMock())

            # Voice update by alias ("carrier") must update fleet_carrier IN-PLACE and override "Old System"
            upd = tools["remember_game_poi"](name="carrier", location="Shinrarta Dezhra")
            self.assertEqual(upd["status"], "success")
            self.assertEqual(upd["poi"], "LONE WOLF")
            merged = mgr.get_merged_pois("elite_dangerous_64")
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0]["system"], "Shinrarta Dezhra")
            self.assertEqual(merged[0]["source"], "voice")

            # Routing to "Lone Wolf" must resolve to the voice-updated system, not stale telemetry
            routed = []
            with patch("core.game_nav.GameNavigator.search_and_route_destination", side_effect=lambda destination_name: (routed.append(destination_name) or {"status": "success", "destination": destination_name, "message": "Plotted."})):
                r_res = tools["search_and_route_destination"](destination_name="Lone Wolf")
            self.assertEqual(routed, ["Shinrarta Dezhra"])
            self.assertEqual(r_res["resolved_system"], "Shinrarta Dezhra")

            # Deleting the saved override falls back cleanly to live telemetry
            del_res = mgr.delete_poi("elite_dangerous_64", "fleet_carrier")
            self.assertTrue(del_res["success"])
            self.assertEqual(del_res["pois"][0]["system"], "Old System")

            # Scan POIs loads declarative default_pois and upgrades raw slug names from telemetry history
            mock_tel.scan_poi_history.return_value = {
                "fleet_carrier": {"name": "LONE WOLF", "callsign": "T9J-44F", "system": "Col 285 Sector SJ-Q d5-68", "aliases": ["carrier"]}
            }
            prof["known_pois"] = {"fleet_carrier": {"name": "fleet_carrier", "system": "Col 285 Sector SJ-Q d5-68", "aliases": []}}
            scan_res = mgr.scan_game_pois("elite_dangerous_64")
            self.assertTrue(scan_res["success"])
            self.assertGreater(scan_res["added"], 10)
            self.assertEqual(prof["known_pois"]["fleet_carrier"]["name"], "LONE WOLF")
            self.assertEqual(prof["known_pois"]["fleet_carrier"]["callsign"], "T9J-44F")
            self.assertIn("selene_jean", prof["known_pois"])
            self.assertEqual(prof["known_pois"]["selene_jean"]["system"], "Kuk")
            mgr.close()

    def test_event_triggers_and_companion_chitchat(self):
        """GameTriggerEngine evaluates When/If->Then rules, enforces Spotter-Only, and Chit-Chat persists settings."""
        from core.game_manager import GameManager
        from core.game_triggers import GameTriggerEngine, CompanionChitChatManager, evaluate_trigger_condition

        self.assertTrue(evaluate_trigger_condition("!state.hardpoints_deployed", {"hardpoints_deployed": False}))
        self.assertFalse(evaluate_trigger_condition("!state.hardpoints_deployed", {"hardpoints_deployed": True}))
        self.assertTrue(evaluate_trigger_condition("state.fuel_pct < 25 and state.supercruise", {"fuel_pct": 18, "supercruise": True}))

        with tempfile.TemporaryDirectory() as tmp:
            mgr = GameManager(profiles_path=str(Path(tmp) / "profiles.json"))
            mgr.add_game("Elite Dangerous", process_name="EliteDangerous64.exe")
            gid = mgr.data["active_profile"]

            # Starter presets from elite_dangerous.aethermodule.json load disabled by default
            trigs = mgr.get_triggers(gid)
            self.assertGreaterEqual(len(trigs), 5)
            self.assertTrue(all(not t.get("enabled", False) for t in trigs))

            # Enable auto_deploy_weapons and verify condition guard + execution
            tog = mgr.toggle_trigger(gid, "auto_deploy_weapons", True)
            self.assertTrue(tog["success"])

            te = GameTriggerEngine(mgr, engine=None)
            # Condition fails when hardpoints already deployed -> 0 enqueued
            self.assertEqual(te.handle_event(gid, "UnderAttack", {}, {"hardpoints_deployed": True}), 0)

            # Spotter-Only blocks synthetic keybind triggers
            mgr.set_input_mode(gid, "spotter_only")
            blocked = te.execute_trigger_rule(gid, next(t for t in mgr.get_triggers(gid) if t["id"] == "auto_deploy_weapons"), is_test=True)
            self.assertEqual(blocked["status"], "blocked_by_spotter_only")
            te.stop()

            # Chit-Chat settings persist and build profanity + emotive-voice prompts when enabled
            cc_res = mgr.set_chitchat_settings(gid, frequency="occasional", screen_peek=True, allow_profanity=True, emotive_voice=True)
            self.assertTrue(cc_res["success"])
            saved_cc = mgr.get_chitchat_settings(gid)
            self.assertEqual(saved_cc, {
                "frequency": "occasional",
                "screen_peek": True,
                "allow_profanity": True,
                "emotive_voice": True,
                "topic_jokes": False,
                "topic_facts": True,
                "topic_relevant": True,
                "chat_local": False,
                "chat_system": False,
                "chat_npc": False,
                "chat_squadron": True,
                "chat_player": True,
                "chat_translate": True,
            })
            self.assertIn("EMOTIVE VOCAL DELIVERY", mgr.build_copilot_personality_directive(mgr.get_profile(gid)))

            cc_mgr = CompanionChitChatManager(mgr, engine=None)
            ctx_first = cc_mgr._enrich_event_context("Docked", {"StationName": "Jameson Memorial", "StarSystem": "Shinrarta Dezhra"}, {})
            self.assertIn("First time arriving at 'Jameson Memorial'", ctx_first)
            ctx_second = cc_mgr._enrich_event_context("Docked", {"StationName": "Jameson Memorial", "StarSystem": "Shinrarta Dezhra"}, {})
            self.assertNotIn("First time arriving", ctx_second)
            ctx_anarchy = cc_mgr._enrich_event_context("FSDJump", {"StarSystem": "Riedquat", "SystemSecurity_Localised": "Anarchy"}, {})
            self.assertIn("Anarchy", ctx_anarchy)
            stealth_prompt = cc_mgr._build_chitchat_prompt(gid, "Live visual observation while playing Days Gone", saved_cc, screen_note="Huge horde of Freakers down the road", urgent=True, peek_mood="stealth")
            self.assertIn("whisper", stealth_prompt.lower())
            self.assertIn("profanity", stealth_prompt.lower())
            combat_prompt = cc_mgr._build_chitchat_prompt(gid, "Event=UnderAttack", saved_cc, event_type="UnderAttack")
            self.assertIn("combat pace", combat_prompt.lower())
            cc_mgr.stop()
            mgr.close()

        # Verify Settings-driven Game Mode model resolver honors Tier 1/2 and locks out Neon/Tier 3
        from core.model_discovery import get_configured_game_model
        fast_m, _ = get_configured_game_model({"tier1_fast_model": "gemini-3.8-flash", "tier2_moderate_model": "gemini-4-hydrogen:medium", "tier3_heavy_model": "gemini-4-neon:high"}, prefer_fast=True)
        self.assertEqual(fast_m, "gemini-3.8-flash")
        auth_m, _ = get_configured_game_model({"tier1_fast_model": "gemini-3.8-live", "tier2_moderate_model": "gemini-4-hydrogen:medium", "tier3_heavy_model": "gemini-4-neon:high"}, prefer_fast=False)
        self.assertEqual(auth_m, "gemini-4-hydrogen")
        capped_m, _ = get_configured_game_model({"tier1_fast_model": "gemini-4-neon:high", "tier2_moderate_model": "gemini-4-neon:high", "tier3_heavy_model": "gemini-4-neon:high"}, prefer_fast=True)
        self.assertEqual(capped_m, "gemini-4-hydrogen")

        # Verify Game Mode excludes delegate_heavy_task and ReflexionEngine uses Tier 2 (Hydrogen) & pauses in Game Mode
        from core.game_engine import GameEngine
        from core.optimizer import ReflexionEngine
        self.assertNotIn("delegate_heavy_task", GameEngine.CORE_GAME_TOOL_NAMES)
        fake_eng = MagicMock()
        fake_eng.config = {"tier2_moderate_model": "gemini-4-hydrogen:medium", "tier3_heavy_model": "gemini-4-neon:high"}
        fake_eng.last_user_turn_timestamp = 0.0
        fake_eng.is_audio_streaming = False
        fake_eng.is_speaking = False
        fake_eng.is_tool_running = False
        fake_eng.game_engine.manager.is_game_mode_active.return_value = True
        rx = ReflexionEngine(engine=fake_eng)
        self.assertEqual(rx.heavy_model, "gemini-4-hydrogen")
        self.assertEqual(rx.thinking_level, "low")
        self.assertFalse(rx._is_engine_idle())
        fake_eng.game_engine.manager.is_game_mode_active.return_value = False
        self.assertTrue(rx._is_engine_idle())

    def test_special_instructions_and_user_keybind_routing_sequence(self):
        """Special Instructions load from module default & reset cleanly; map routing pulls user keybinds strictly."""
        from core.game_manager import GameManager
        from core.game_nav import GameNavigator

        with tempfile.TemporaryDirectory() as tmp:
            mgr = GameManager(profiles_path=str(Path(tmp) / "profiles.json"))
            mgr.add_game("Elite Dangerous", process_name="EliteDangerous64.exe")
            gid = mgr.data["active_profile"]

            si = mgr.get_special_instructions(gid)
            self.assertIn("Interface Mode", si["text"])
            self.assertFalse(si["is_custom"])

            upd = mgr.set_special_instructions(gid, "Custom setup note")
            self.assertEqual(upd["text"], "Custom setup note")
            self.assertTrue(upd["is_custom"])

            rst = mgr.set_special_instructions(gid, None)
            self.assertIn("Interface Mode", rst["text"])
            self.assertFalse(rst["is_custom"])
            mgr.close()

        # Verify pure user-keybind routing sequence (custom Shift+G, Up, Space, Down, Q) + close map
        mock_adapter = MagicMock()
        mock_adapter.get_state.return_value = {"route_destination": "Sol", "route_jumps": 3}
        user_binds = {
            "open galaxy map": {"key": "g", "modifiers": ["shift"]},
            "ui panel up": {"key": "up", "modifiers": []},
            "ui panel down": {"key": "down", "modifiers": []},
            "ui panel select": {"key": "space", "modifiers": []},
            "galaxy cam yaw left": {"key": "q", "modifiers": []},
        }
        routing_cfg = {
            "search_routing": {
                "map_open_actions": ["open galaxy map"],
                "open_wait_sec": 0.05,
                "pre_input_steps": [
                    {"bind_actions": ["ui panel up"], "hold_ms": 50, "delay_ms": 50},
                    {"bind_actions": ["ui panel select"], "hold_ms": 50, "delay_ms": 50},
                ],
                "input_method": "clipboard_paste",
                "submit_key": "",
                "select_result_wait_sec": 0.05,
                "post_input_keys": [
                    {"bind_actions": ["ui panel down"], "hold_ms": 50, "delay_ms": 50},
                    {"bind_actions": ["ui panel select"], "hold_ms": 50, "delay_ms": 50},
                    {"bind_actions": ["galaxy cam yaw left"], "hold_ms": 250, "delay_ms": 50},
                ],
                "prefer_plot_key_first": True,
                "plot_bind_actions": ["ui panel select"],
                "plot_hold_sec": 0.15,
                "verify_telemetry_field": "route_destination",
                "close_map_on_verified": True,
            }
        }
        nav = GameNavigator(client=None, target_process="SpaceSim.exe", map_controls=routing_cfg, keybinds=user_binds, telemetry_adapter=mock_adapter)
        pressed_keys = []
        pressed_combos = []
        with patch("tools.os_controls.focus_window"), \
             patch("core.game_nav.send_directinput_key", side_effect=lambda k, duration_sec=0.1: pressed_keys.append((k, duration_sec))), \
             patch("tools.os_controls.send_directinput_combo", side_effect=lambda mods, k, hold_duration=0.1: pressed_combos.append((list(mods), k))), \
             patch.object(nav, "capture_viewport", return_value=(b"jpg", 1920, 1080)), \
             patch.object(nav, "is_map_screen", return_value=False):
            res = nav.search_and_route_destination("Sol")
        self.assertEqual(res["status"], "success")
        self.assertIn((["shift"], "g"), pressed_combos)
        self.assertEqual(pressed_combos.count((["shift"], "g")), 2)  # Opened AND closed with user's Shift+G!
        self.assertEqual([k for k, _ in pressed_keys], ["up", "space", "down", "space", "q", "space"])

        # Missing required bind must report missing_keybinds cleanly (no silent hardcoded fallback)
        nav_missing = GameNavigator(client=None, target_process="SpaceSim.exe", map_controls=routing_cfg, keybinds={"open galaxy map": {"key": "g", "modifiers": ["shift"]}}, telemetry_adapter=mock_adapter)
        with patch("tools.os_controls.focus_window"), \
             patch("core.game_nav.send_directinput_key"), \
             patch("tools.os_controls.send_directinput_combo"), \
             patch.object(nav_missing, "capture_viewport", return_value=(b"jpg", 1920, 1080)), \
             patch.object(nav_missing, "is_map_screen", return_value=True):
            res_miss = nav_missing.search_and_route_destination("Sol")
        self.assertEqual(res_miss["status"], "missing_keybinds")
        self.assertIn("ui panel up", res_miss["missing_keybinds"])


class TestSyncedUIBindsContextActionsAndNewsFeed(unittest.TestCase):
    def test_complete_ui_task_injects_synced_user_binds_and_executes_combos(self):
        from core.game_nav import UniversalGameNavigator

        user_binds = {
            "left panel": {"key": "1", "modifiers": ["shift"], "description": "Focus Navigation / Left Panel"},
            "ui panel up": {"key": "up", "modifiers": [], "description": "UI Panel Up"},
            "ui panel down": {"key": "down", "modifiers": [], "description": "UI Panel Down"},
            "ui panel select": {"key": "space", "modifiers": [], "description": "UI Panel Select"},
            "next panel tab": {"key": "e", "modifiers": [], "description": "Next Panel Tab"},
            "close menu": {"key": "backspace", "modifiers": [], "description": "UI Back / Close Menu"},
        }
        nav = UniversalGameNavigator(
            client=MagicMock(),
            target_process="SpaceSim.exe",
            game_name="Space Sim",
            keybinds=user_binds,
        )
        seen_prompts = []
        responses = iter([
            {
                "highlighted_now": "none",
                "observation": "Opening left panel and tabbing to contacts",
                "action": "KEYS",
                "keys": ["shift+1", "e", "down", "space"],
                "hold_ms": 80,
                "interval_ms": 60,
                "wait_ms": 180,
            },
            {
                "highlighted_now": "Request Docking",
                "observation": "Docking request granted banner visible",
                "action": "VERIFIED_DONE",
                "cleanup_keys": ["shift+1"],
                "reason": "Docking request granted.",
            },
        ])

        def fake_vision(frame, prompt, fast_mode=True):
            seen_prompts.append(prompt)
            return next(responses)

        pressed_keys = []
        pressed_combos = []
        with patch("tools.os_controls.focus_window"), \
             patch("core.game_nav.send_directinput_key", side_effect=lambda k, duration_sec=0.08: pressed_keys.append(k)), \
             patch("core.game_nav.send_directinput_combo", side_effect=lambda mods, k, hold_duration=0.08: pressed_combos.append((list(mods), k))), \
             patch.object(nav, "capture_viewport", return_value=(b"jpg", 1920, 1080)), \
             patch.object(nav, "_call_vision_json", side_effect=fake_vision):
            res = nav.complete_ui_task("Request docking", max_steps=4)

        self.assertEqual(res["status"], "verified_done")
        # Prompt must contain the player's actual synced binds (including shift+1 and arrow keys)
        self.assertIn("PLAYER'S ACTUAL SYNCED UI / PANEL KEYBINDS", seen_prompts[0])
        self.assertIn('"shift+1"', seen_prompts[0])
        self.assertIn('"up"', seen_prompts[0])
        self.assertIn('"down"', seen_prompts[0])
        self.assertIn('"backspace"', seen_prompts[0])
        # Modifier combo executed both during KEYS step and cleanup_keys!
        self.assertEqual(pressed_combos, [(["shift"], "1"), (["shift"], "1")])
        self.assertEqual(pressed_keys, ["e", "down", "space"])

    def test_context_aware_sequences_pick_matching_vehicle_variant_and_compound_conditions(self):
        from core.game_macro_runner import MacroRunner, evaluate_condition

        # Compound condition evaluator checks
        self.assertTrue(evaluate_condition("state.in_srv or state.on_foot", {"in_srv": False, "on_foot": True}))
        self.assertFalse(evaluate_condition("state.in_srv or state.on_foot", {"in_srv": False, "on_foot": False}))
        self.assertTrue(evaluate_condition("(state.docked or state.landed) and state.in_main_ship", {"docked": False, "landed": True, "in_main_ship": True}))
        self.assertFalse(evaluate_condition("(state.docked or state.landed) and state.in_main_ship", {"docked": False, "landed": True, "in_main_ship": False}))

        profile = {
            "display_name": "Space Sim",
            "keybinds": {
                "ship disembark key": {"key": "f", "modifiers": []},
                "srv disembark key": {"key": "g", "modifiers": []},
            },
            "sequences": {
                "disembark from ship": {
                    "condition": "(state.docked or state.landed) and state.in_main_ship",
                    "condition_skip_ack": "Must be docked or landed in main ship.",
                    "ack": "Leaving ship on foot.",
                    "aliases": ["disembark", "get out"],
                    "sync": True,
                    "steps": [{"action": "trigger_bind", "phrase": "ship disembark key"}],
                },
                "disembark from srv": {
                    "condition": "state.in_srv",
                    "condition_skip_ack": "Not in SRV.",
                    "ack": "Leaving SRV on foot.",
                    "aliases": ["disembark", "get out"],
                    "sync": True,
                    "steps": [{"action": "trigger_bind", "phrase": "srv disembark key"}],
                },
            },
        }
        runner = MacroRunner()
        pressed = []

        class _FakeAdapter:
            def __init__(self, flags):
                self._flags = flags

            def get_state_flags(self):
                return dict(self._flags)

        with patch("tools.os_controls.send_directinput_key", side_effect=lambda k, **kw: (pressed.append(k), True)[1]):
            # When landed in main ship -> picks "disembark from ship" (key 'f')
            res_ship = runner.execute_action("disembark", profile, _FakeAdapter({"landed": True, "in_main_ship": True, "in_srv": False}))
            self.assertEqual(res_ship["status"], "executed")
            self.assertEqual(res_ship["ack"], "Leaving ship on foot.")
            # When driving SRV -> same phrase "disembark" picks "disembark from srv" (key 'g')!
            res_srv = runner.execute_action("disembark", profile, _FakeAdapter({"landed": False, "in_main_ship": False, "in_srv": True}))
            self.assertEqual(res_srv["status"], "executed")
            self.assertEqual(res_srv["ack"], "Leaving SRV on foot.")
            # When flying in space -> neither matches, skips gracefully with condition_skip_ack
            res_fly = runner.execute_action("disembark", profile, _FakeAdapter({"landed": False, "docked": False, "in_main_ship": True, "in_srv": False}))
            self.assertEqual(res_fly["status"], "condition_skipped")
        self.assertEqual(pressed, ["f", "g"])

    def test_declarative_news_feed_strips_html_truncates_and_works_with_empty_query(self):
        from core.game_intel import query_declarative_intel
        from core.game_modules import get_module_for_game

        mod = get_module_for_game("elite_dangerous")
        self.assertIsNotNone(mod)
        news_ep = mod["intel_endpoints"]["news"]
        self.assertEqual(news_ep["method"], "GET")

        fake_feed = {
            "data": [
                {
                    "attributes": {
                        "title": "New Starport Construction Announced",
                        "field_galnet_date": "19 NOV 3312",
                        "body": {
                            "value": "<p>Brewer Corporation &amp; allies have announced a <b>major</b> initiative " + ("spanning many systems " * 25) + "</p>"
                        },
                    }
                }
            ]
        }
        with patch("core.game_intel._http_json", return_value=fake_feed):
            res = query_declarative_intel(news_ep, query="", location="Sol")

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["count"], 1)
        row = res["results"][0]
        self.assertEqual(row["title"], "New Starport Construction Announced")
        self.assertEqual(row["date"], "19 NOV 3312")
        self.assertNotIn("<p>", row["summary"])
        self.assertNotIn("<b>", row["summary"])
        self.assertIn("Brewer Corporation & allies", row["summary"])
        self.assertTrue(row["summary"].endswith("..."))
        self.assertLessEqual(len(row["summary"]), 285)


class TestProjectTrackerConstructionPOIAndCargoManifest(unittest.TestCase):
    def test_project_tracker_auto_updates_scratchpad_poi_and_cargo_manifest(self):
        from core.telemetry.declarative import DeclarativeFileTelemetryAdapter
        from core.game_manager import GameManager
        from core.game_modules import get_module_for_game

        mod = get_module_for_game("elite_dangerous")
        self.assertIsNotNone(mod)

        with tempfile.TemporaryDirectory() as tmp:
            ed_dir = Path(tmp) / "ed_logs"
            ed_dir.mkdir()
            # Write Cargo.json with 1298 Aluminium
            (ed_dir / "Cargo.json").write_text(json.dumps({
                "timestamp": "2026-11-24T16:17:00Z",
                "event": "Cargo",
                "Vessel": "Ship",
                "Count": 1298,
                "Inventory": [
                    {"Name": "aluminium", "Name_Localised": "Aluminium", "Count": 1298}
                ],
            }), encoding="utf-8")

            # Write a journal with Loadout, Docked at SpaceConstructionDepot, Depot snapshot, and Contribution
            journal_lines = [
                json.dumps({
                    "timestamp": "2026-11-24T16:09:08Z",
                    "event": "Loadout",
                    "Ship": "panthermkii",
                    "ShipName": "HAULER ONE",
                    "CargoCapacity": 1298,
                    "MaxJumpRange": 38.13,
                    "HullHealth": 1.0,
                }),
                json.dumps({
                    "timestamp": "2026-11-24T16:09:08Z",
                    "event": "Docked",
                    "StationName": "Orbital Construction Site: Naylor's Progress",
                    "StationType": "SpaceConstructionDepot",
                    "MarketID": 3961205506,
                    "StarSystem": "Col 285 Sector SJ-Q d5-68",
                    "Body": "Col 285 Sector SJ-Q d5-68 A 4",
                }),
                json.dumps({
                    "timestamp": "2026-11-24T16:09:09Z",
                    "event": "ColonisationConstructionDepot",
                    "MarketID": 3961205506,
                    "ConstructionProgress": 0.581,
                    "ConstructionComplete": False,
                    "ConstructionFailed": False,
                    "ResourcesRequired": [
                        {"Name": "$aluminium_name;", "Name_Localised": "Aluminium", "RequiredAmount": 1394, "ProvidedAmount": 0},
                        {"Name": "$titanium_name;", "Name_Localised": "Titanium", "RequiredAmount": 809, "ProvidedAmount": 0},
                    ],
                }),
            ]
            jpath = ed_dir / "Journal.2026-11-24T110710.01.log"
            jpath.write_text("\n".join(journal_lines) + "\n", encoding="utf-8")

            # Set up GameManager with a stale manual scratchpad line + personal note
            pfile = Path(tmp) / "profiles.json"
            mgr = GameManager(profiles_path=str(pfile))
            mgr.add_game("Elite Dangerous", process_name="EliteDangerous64.exe")
            gid = mgr.data["active_profile"]
            prof = mgr.get_profile(gid)
            prof["scratchpad_raw"] = (
                "Personal reminder: upgrade FSD booster.\n"
                "Station requirements for Orbital Construction Site: Naylor's Progress: Aluminium (1394), Titanium (809), Copper (122)."
            )

            tel_cfg = dict(mod["telemetry"])
            tel_cfg["dir"] = str(ed_dir)
            prof["telemetry"] = tel_cfg

            adapter = DeclarativeFileTelemetryAdapter(game_id=gid, profile=prof, journal_dir=str(ed_dir))
            adapter.on_project_update = lambda pid, pdata: mgr.sync_project_tracker_to_scratchpad(gid, pid, pdata)
            adapter.on_poi_update = lambda pid, pdata: mgr.sync_tracked_poi_to_profile(gid, pid, pdata)
            adapter._file_handle = open(jpath, "r", encoding="utf-8")
            adapter._rehydrate_initial_state()
            adapter.refresh_companion_files()
            adapter._file_handle.close()

            # 1. Construction Site auto-tracked as POI (keyed by MarketID so multiple sites coexist)
            pois = adapter.get_state().get("tracked_pois", {})
            site1_poi_key = "construction_site_3961205506"
            self.assertIn(site1_poi_key, pois)
            self.assertEqual(pois[site1_poi_key]["name"], "Naylor's Progress")
            self.assertEqual(pois[site1_poi_key]["system"], "Col 285 Sector SJ-Q d5-68")
            self.assertIn("Orbital Construction Site: Naylor's Progress", pois[site1_poi_key]["aliases"])
            self.assertIn("construction site", pois[site1_poi_key]["aliases"])
            # Also persisted to profile known_pois!
            self.assertEqual(prof["known_pois"][site1_poi_key]["system"], "Col 285 Sector SJ-Q d5-68")

            # 2. Cargo hold manifest + capacity + jump range appear in summary context
            summary = adapter.get_summary_prompt_context()
            self.assertIn("Cargo: 1298 / 1298 (Aluminium x1298)", summary)
            self.assertIn("Max Jump: 38.1 LY", summary)
            self.assertIn("Naylor's Progress", summary)
            self.assertIn("Aluminium (1394)", summary)

            # 3. Live ColonisationContribution immediately subtracts 1298 Aluminium -> 96 remaining AND updates scratchpad!
            contrib_line = json.dumps({
                "timestamp": "2026-11-24T16:23:04Z",
                "event": "ColonisationContribution",
                "MarketID": 3961205506,
                "Contributions": [
                    {"Name": "$Aluminium_name;", "Name_Localised": "Aluminium", "Amount": 1298}
                ],
            })
            adapter._parse_event_line(contrib_line, is_initial_sync=False)

            site1_proj_key = "active_construction_3961205506"
            proj = adapter.get_state()["active_projects"][site1_proj_key]
            alu = next(r for r in proj["resources"] if r["name"] == "Aluminium")
            self.assertEqual(alu["provided"], 1298)
            self.assertEqual(alu["remaining"], 96)

            # 4. Visiting a SECOND construction project tracks BOTH simultaneously without overwriting Site 1!
            adapter._parse_event_line(json.dumps({
                "timestamp": "2026-11-24T16:40:00Z",
                "event": "Docked",
                "StationName": "Planetary Construction Site: Frontier Outpost",
                "StationType": "PlanetaryConstructionDepot",
                "MarketID": 4000099999,
                "StarSystem": "HIP 99999",
                "Body": "HIP 99999 B 2",
            }), is_initial_sync=False)
            adapter._parse_event_line(json.dumps({
                "timestamp": "2026-11-24T16:40:02Z",
                "event": "ColonisationConstructionDepot",
                "MarketID": 4000099999,
                "ConstructionProgress": 0.25,
                "ConstructionComplete": False,
                "ConstructionFailed": False,
                "ResourcesRequired": [
                    {"Name": "$steel_name;", "Name_Localised": "Steel", "RequiredAmount": 500, "ProvidedAmount": 100},
                ],
            }), is_initial_sync=False)

            self.assertIn("construction_site_4000099999", prof["known_pois"])
            self.assertIn(site1_poi_key, prof["known_pois"])
            self.assertEqual(prof["known_pois"]["construction_site_4000099999"]["name"], "Frontier Outpost")
            self.assertEqual(prof["known_pois"]["construction_site_4000099999"]["system"], "HIP 99999")

            # Scratchpad preserves personal note, replaced stale 1394 line, AND lists BOTH concurrent projects!
            sp = prof["scratchpad_raw"]
            self.assertIn("Personal reminder: upgrade FSD booster.", sp)
            self.assertNotIn("Aluminium (1394)", sp)
            self.assertIn("Naylor's Progress", sp)
            self.assertIn("Aluminium (96)", sp)
            self.assertIn("Titanium (809)", sp)
            self.assertIn("Last delivery: Aluminium x1298", sp)
            self.assertIn("Frontier Outpost (HIP 99999)", sp)
            self.assertIn("Steel (400)", sp)
            mgr.close()

    def test_chitchat_random_timers_topics_natural_profanity_and_user_yield_guard(self):
        from core.game_triggers import CompanionChitChatManager
        from core.game_manager import GameManager
        from core.game_nav import UniversalGameNavigator
        from core.game_modules import get_module_for_game

        # 1. Verify "take us out" upgraded to ui_task in Elite module
        mod = get_module_for_game("elite_dangerous")
        self.assertEqual(mod["sequences"]["take us out"]["steps"][0]["action"], "ui_task")

        # 2. Verify background ui_task immediately yields if player moves mouse & skips cleanup_keys
        nav = UniversalGameNavigator(client=MagicMock(), target_process="SpaceSim.exe", game_name="Space Sim")
        cursor_positions = iter([(100, 100), (450, 450)])
        with patch("tools.os_controls.focus_window"), \
             patch("win32api.GetCursorPos", side_effect=lambda: next(cursor_positions)), \
             patch.object(nav, "capture_viewport", return_value=(b"jpg", 1920, 1080)):
            res_yield = nav.complete_ui_task(
                "Repair ship",
                max_steps=3,
                abort_on_user_input=True,
                allow_cleanup_keys=False,
            )
        self.assertEqual(res_yield["status"], "aborted_user_active")

        # 3. Verify randomized Chit-Chat timers, topic checkboxes, persistent history, and sparse profanity
        with tempfile.TemporaryDirectory() as tmp:
            pfile = Path(tmp) / "profiles.json"
            mgr = GameManager(profiles_path=str(pfile))
            mgr.add_game("Space Sim", process_name="SpaceSim.exe")
            gid = mgr.data["active_profile"]
            res_set = mgr.set_chitchat_settings(
                gid,
                frequency="chatty",
                screen_peek=False,
                allow_profanity=True,
                emotive_voice=True,
                topic_jokes=True,
                topic_facts=True,
                topic_relevant=False,
            )
            self.assertTrue(res_set["success"])
            self.assertTrue(res_set["topic_jokes"])
            self.assertFalse(res_set["topic_relevant"])

            mgr.record_chitchat_history(gid, "joke: navigation humor (Sol)")
            cc = CompanionChitChatManager(mgr, engine=MagicMock())
            cc.stop()
            cc._roll_timers_locked("chatty")
            self.assertGreaterEqual(cc._next_idle_sec, 60.0)
            self.assertLessEqual(cc._next_idle_sec, 240.0)

            settings = cc._get_settings(gid)
            # First session remark must NEVER allow profanity even if allow_profanity=True
            self.assertFalse(cc._should_allow_profanity_now(settings, urgent=True, peek_mood="combat"))
            # Jokes & facts must NEVER allow profanity even later in session
            cc._session_chitchat_count = 3
            self.assertFalse(cc._should_allow_profanity_now(settings, urgent=True, banter_angle="joke"))
            self.assertFalse(cc._should_allow_profanity_now(settings, urgent=True, banter_angle="fact"))

            # Topic pool with only jokes + facts enabled should only pick joke or fact
            picked = {cc._pick_idle_banter_angle(settings)[0] for _ in range(4)}
            self.assertEqual(picked, {"joke", "fact"})

            prompt = cc._build_chitchat_prompt(gid, "Quiet stretch in Sol", settings, banter_angle="joke", theme_seed="engineering")
            self.assertIn("STRICT ANTI-REPETITION", prompt)
            self.assertIn("joke: navigation humor (Sol)", prompt)
            self.assertIn("no profanity on this line", prompt)

            # 4. Verify chat channel filtering & foreign-language translation relay
            mgr.game_mode_enabled = True
            mgr.is_game_running = lambda _g=None: True
            fake_engine = MagicMock()
            fake_engine.is_running = True
            fake_engine.is_speaking = False
            fake_engine._is_speaking = False
            fake_engine.is_user_speaking = False
            fake_engine._user_speaking = False
            cc.engine = fake_engine
            dispatched_prompts = []
            with patch("core.game_triggers._dispatch_engine_proactive_prompt", side_effect=lambda _e, p: (dispatched_prompts.append(p), True)[1]):
                # NPC chat is OFF by default -> must NOT relay!
                self.assertFalse(cc.on_telemetry_event(gid, "ReceiveText", {"Channel": "npc", "From": "Station", "Message_Localised": "Observe protocol"}, {}))
                # System chat is OFF by default -> must NOT relay!
                self.assertFalse(cc.on_telemetry_event(gid, "ReceiveText", {"Channel": "starsystem", "From": "RandomPilot", "Message": "o7"}, {}))
                # Player chat is ON + translate ON -> MUST relay with English translation instruction!
                self.assertTrue(cc.on_telemetry_event(gid, "ReceiveText", {"Channel": "player", "From": "George0001", "Message": "¿Dónde están todos los Thargoids?"}, {}))
            self.assertEqual(len(dispatched_prompts), 1)
            self.assertIn("George0001", dispatched_prompts[0])
            self.assertIn("¿Dónde están todos los Thargoids?", dispatched_prompts[0])
            self.assertIn("translate it naturally into English", dispatched_prompts[0])

            # 5. Verify combined dock trigger, KGBFOAM StartJump derivation, and FuelLevel threshold triggers
            el_triggers = {t["id"]: t for t in mod["default_triggers"]}
            self.assertIn("auto_dock_services", el_triggers)
            self.assertIn("auto_scoopable_star_callout", el_triggers)
            self.assertIn("auto_fuel_low_25", el_triggers)
            self.assertIn("auto_fuel_critical_10", el_triggers)

            from core.telemetry.declarative import DeclarativeFileTelemetryAdapter
            with tempfile.TemporaryDirectory() as td:
                tp = Path(td)
                (tp / "Status.json").write_text(json.dumps({"Fuel": {"FuelMain": 3.0}, "Flags": 0}), encoding="utf-8")
                prof_el = {"display_name": "Elite Dangerous", "telemetry": {**mod["telemetry"], "dir": td}}
                el_adp = DeclarativeFileTelemetryAdapter(game_id="elite_dangerous", profile=prof_el, journal_dir=td)
                fired_events = []
                el_adp.on_event = lambda ev_t, raw_e, st_s: fired_events.append((ev_t, dict(st_s)))
                # Loadout sets FuelCapacity.Main = 32 -> 3.0 / 32 = 9.4% (<10%)
                el_adp._parse_event_line(json.dumps({"event": "Loadout", "Ship": "panthermkii", "FuelCapacity": {"Main": 32.0}}))
                el_adp._parse_event_line(json.dumps({"event": "StartJump", "JumpType": "Hyperspace", "StarSystem": "Deciat", "StarClass": "K"}))
                self.assertEqual(el_adp.get_state()["next_star_scoopable"], "Scoopable (KGBFOAM)")
                el_adp._parse_event_line(json.dumps({"event": "StartJump", "JumpType": "Hyperspace", "StarSystem": "HIP 1", "StarClass": "L"}))
                self.assertEqual(el_adp.get_state()["next_star_scoopable"], "NOT scoopable")
                el_adp._poll_status_file()
                self.assertAlmostEqual(el_adp.get_state()["fuel_pct"], 9.4, places=1)
                self.assertTrue(any(ev_t == "FuelLevel" for ev_t, _ in fired_events))
            mgr.close()

    def test_declarative_trade_pair_computes_station_to_station_profits_and_trip_total(self):
        from core.game_intel import query_declarative_intel
        from core.game_modules import get_module_for_game

        mod = get_module_for_game("elite_dangerous")
        trade_ep = mod["intel_endpoints"]["trade"]
        self.assertEqual(trade_ep["mode"], "trade_pair")

        def fake_http(url, method="POST", payload=None, timeout_sec=10.0, headers=None):
            sname = ((payload or {}).get("filters") or {}).get("name", {}).get("value", "")
            if sname == "Harebell 4":
                return {
                    "results": [{
                        "name": "Harebell 4",
                        "system_name": "HIP 75521",
                        "market": [
                            {"commodity": "Lepidolite", "buy_price": 457, "supply": 20000},
                            {"commodity": "Progenitor Cells", "buy_price": 5826, "supply": 15000},
                            {"commodity": "Biowaste", "buy_price": 500, "supply": 0},
                        ],
                    }]
                }
            return {
                "results": [{
                    "name": "Washer Industries",
                    "system_name": "Col 285 Sector OE-F c12-5",
                    "market": [
                        {"commodity": "Lepidolite", "sell_price": 3999, "demand": 1},
                        {"commodity": "Progenitor Cells", "sell_price": 7445, "demand": 171326},
                        {"commodity": "Biowaste", "sell_price": 900, "demand": 500},
                    ],
                }]
            }

        tel = {"station": "Harebell 4", "star_system": "HIP 75521", "cargo_capacity": 1298, "cargo_count": 164}
        with patch("core.game_intel._http_json", side_effect=fake_http):
            res = query_declarative_intel(
                trade_ep,
                query="Washer Industries",
                location="Col 285 Sector OE-F c12-5",
                telemetry_state=tel,
            )

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["count"], 2)  # Biowaste excluded (0 supply)
        self.assertEqual(res["results"][0]["commodity"], "Lepidolite")
        self.assertEqual(res["results"][0]["profit_per_unit"], 3542)
        self.assertFalse(res["results"][0]["high_demand"])
        self.assertEqual(res["results"][1]["commodity"], "Progenitor Cells")
        self.assertTrue(res["results"][1]["high_demand"])
        self.assertEqual(res["results"][1]["haul_qty"], 1134)  # 1298 - 164 free cargo!
        self.assertEqual(res["results"][1]["est_trip_profit"], 1619 * 1134)
        self.assertIn("[low demand]", res["intel"])

    def test_declarative_shopping_list_ranks_one_stop_stations_and_excludes_carriers(self):
        from core.game_intel import query_declarative_intel, _VALUE_RESOLVER_CACHE
        from core.game_modules import get_module_for_game

        _VALUE_RESOLVER_CACHE.clear()
        mod = get_module_for_game("elite_dangerous")
        shop_ep = mod["intel_endpoints"]["shopping_list"]
        self.assertEqual(shop_ep["mode"], "shopping_list")

        fake_vocab = {"values": ["Aluminium", "Titanium", "Polymers", "Copper"]}
        fake_stations = {
            "results": [
                {
                    "name": "PRIVATE CARRIER",
                    "system_name": "Sol",
                    "type": "Drake-Class Carrier",
                    "distance": 0.5,
                    "large_pads": 8,
                    "market": [
                        {"commodity": "Aluminium", "buy_price": 1000, "supply": 5000},
                        {"commodity": "Titanium", "buy_price": 1000, "supply": 5000},
                        {"commodity": "Polymers", "buy_price": 1000, "supply": 5000},
                    ],
                },
                {
                    "name": "Small Depot",
                    "system_name": "Sol",
                    "type": "Coriolis Starport",
                    "distance": 2.0,
                    "large_pads": 4,
                    "market": [
                        {"commodity": "Aluminium", "buy_price": 1900, "supply": 500},
                    ],
                },
                {
                    "name": "Mega Hub",
                    "system_name": "Alpha Centauri",
                    "type": "Orbis Starport",
                    "distance": 4.3,
                    "large_pads": 6,
                    "market": [
                        {"commodity": "Aluminium", "buy_price": 1800, "supply": 5000},
                        {"commodity": "Titanium", "buy_price": 900, "supply": 4000},
                        {"commodity": "Polymers", "buy_price": 200, "supply": 3000},
                    ],
                },
            ]
        }
        tel = {
            "star_system": "Sol",
            "needs_large_pad": True,
            "active_projects": {
                "active_construction_1": {
                    "name": "Naylor's Progress",
                    "complete": False,
                    "resources": [
                        {"name": "Aluminium", "remaining": 96},
                        {"name": "Titanium", "remaining": 809},
                        {"name": "Polymers", "remaining": 390},
                        {"name": "Steel", "remaining": 0},
                    ],
                }
            },
        }
        with patch("core.game_intel._http_json", side_effect=[fake_vocab, fake_stations]):
            res = query_declarative_intel(shop_ep, query="project", location="Sol", telemetry_state=tel)

        self.assertEqual(res["status"], "success")
        # Carrier must be excluded; Mega Hub (3/3) must beat Small Depot (1/3) even though slightly farther!
        self.assertEqual(res["count"], 2)
        self.assertEqual(res["results"][0]["station"], "Mega Hub")
        self.assertEqual(res["results"][0]["matched_count"], 3)
        self.assertEqual(res["results"][0]["total_needed"], 3)
        self.assertEqual(res["results"][0]["full_cover_count"], 3)
        self.assertIn("Stocks 3/3 items", res["intel"])


if __name__ == "__main__":
    unittest.main()
