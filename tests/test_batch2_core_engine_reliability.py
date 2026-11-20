"""
Batch 2 Reliability Unit Tests:
Covers Payload Sanitizer, Task Orchestrator, SessionManager, ConfigManager,
ModelDiscovery TTS probe, SessionLifecycle compactor, WebSocket Gateway,
AetherEngine (receive loop, rotation, TTRPG turn config, startup dedup), and
GuiBridge (invoke_ttrpg_tool, FIFO event queue, config merge, dynamic DM roster, async cleanup).
"""

import asyncio
import importlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types as pytypes
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.config_manager import ConfigManager, save_config_atomic, sync_config_schema
from core.engine import AetherEngine
from core.gui_bridge import GuiBridge
from core.model_discovery import verify_tts_endpoint
from core.orchestrator import ORCHESTRATOR_ALLOWED_TOOL_NAMES, TaskOrchestrator
from core.session_lifecycle import SessionLifecycleManager
from core.session_manager import SessionManager
from tools.payload_sanitizer import EXEMPT_TOOLS, sanitize_tool_result


def _make_fc(name: str, args: dict):
    return SimpleNamespace(name=name, args=args)


def _make_model_response(text: str = "", function_calls=None, finish_reason: str = "STOP"):
    parts = []
    if text:
        parts.append(SimpleNamespace(text=text, thought=False))
    content = SimpleNamespace(role="model", parts=parts)
    candidate = SimpleNamespace(content=content, finish_reason=finish_reason)
    return SimpleNamespace(
        candidates=[candidate],
        text=text,
        function_calls=function_calls or [],
    )


class TestPayloadSanitizerBatch2(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tier2_tier3_tools_are_exempt_and_preserve_handoff_fields(self):
        for tool in ("delegate_moderate_task", "delegate_heavy_task", "export_research_dossier", "paste_to_document"):
            self.assertIn(tool, EXEMPT_TOOLS)
            payload = {
                "status": "success",
                "file_path": r"C:\dossiers\deep_report.md",
                "filename": "deep_report.md",
                "answer": "A" * 1500,
                "directive": "SPEAK_SUMMARY",
                "pasted_to_doc": True,
            }
            out = sanitize_tool_result(tool, payload, base_dir=self.temp_dir)
            self.assertEqual(out["file_path"], payload["file_path"])
            self.assertEqual(out["answer"], payload["answer"])
            self.assertEqual(out["directive"], "SPEAK_SUMMARY")
            self.assertNotIn("cache_path", out)

    def test_non_exempt_truncation_uses_unique_cache_files_and_keeps_control_fields(self):
        big1 = {"status": "success", "stdout": "X" * 1200, "file_path": "/tmp/a.txt", "directive": "KEEP"}
        big2 = {"status": "success", "stdout": "Y" * 1200, "file_path": "/tmp/b.txt"}
        out1 = sanitize_tool_result("run_custom_tool", big1, base_dir=self.temp_dir)
        out2 = sanitize_tool_result("run_custom_tool", big2, base_dir=self.temp_dir)

        self.assertIn("cache_path", out1)
        self.assertIn("cache_path", out2)
        self.assertNotEqual(out1["cache_path"], out2["cache_path"])
        self.assertEqual(out1["file_path"], "/tmp/a.txt")
        self.assertEqual(out1["directive"], "KEEP")

    def test_plain_multiline_error_not_mangled_by_traceback_cleaner(self):
        err_msg = "Validation failed:\nLine 1: missing title\nLine 2: missing body\nLine 3: invalid recipient"
        out = sanitize_tool_result("custom_validator", {"status": "error", "error": err_msg}, base_dir=self.temp_dir)
        self.assertEqual(out["error"], err_msg)

        tb = (
            "Traceback (most recent call last):\n"
            '  File "asyncio/runners.py", line 118, in run\n'
            "    return self._loop.run_until_complete(main)\n"
            '  File "tools/sample.py", line 42, in execute\n'
            "    return 1 / 0\n"
            "ZeroDivisionError: division by zero\n"
        )
        out_tb = sanitize_tool_result("custom_validator", {"status": "error", "traceback": tb}, base_dir=self.temp_dir)
        self.assertIn("tools/sample.py", out_tb["traceback"])
        self.assertIn("ZeroDivisionError", out_tb["traceback"])
        self.assertNotIn("runners.py", out_tb["traceback"])


class TestOrchestratorReliabilityBatch2(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dispatcher = MagicMock()
        self.dispatcher.notify = MagicMock()

    def test_orchestrator_exposes_native_tiling_tools(self):
        self.assertIn("tile_windows", ORCHESTRATOR_ALLOWED_TOOL_NAMES)
        self.assertIn("snap_window", ORCHESTRATOR_ALLOWED_TOOL_NAMES)

    async def test_whitelist_block_halts_remaining_calls_in_same_batch(self):
        called = []

        async def fake_dispatch(fn_name, fn_args):
            called.append(fn_name)
            if fn_name == "launch_application":
                return {"status": "blocked", "message": "Blocked by whitelist."}
            return {"status": "success"}

        self.dispatcher.dispatch = AsyncMock(side_effect=fake_dispatch)
        turn_1 = _make_model_response(function_calls=[
            _make_fc("launch_application", {"app_name": "secret.exe"}),
            _make_fc("type_text", {"text": "SHOULD NOT RUN"}),
        ])
        client = MagicMock()
        client.models.generate_content.return_value = turn_1

        orch = TaskOrchestrator(self.dispatcher, max_iterations=5)
        res = await orch.run(client, "gemini-4-hydrogen", "medium", "Test", "Open secret and type")

        self.assertEqual(res["status"], "blocked")
        self.assertEqual(called, ["launch_application"])

    async def test_max_iterations_returns_error_not_success(self):
        self.dispatcher.dispatch = AsyncMock(return_value={"status": "success"})
        client = MagicMock()
        client.models.generate_content.return_value = _make_model_response(
            function_calls=[_make_fc("wait_seconds", {"seconds": 0.2})]
        )

        orch = TaskOrchestrator(self.dispatcher, max_iterations=3)
        with patch("core.orchestrator.asyncio.sleep", new=AsyncMock()):
            res = await orch.run(client, "gemini-4-hydrogen", "medium", "Loop", "Keep waiting")

        self.assertEqual(res["status"], "error")
        self.assertIn("maximum orchestration steps", res["answer"].lower())

    async def test_empty_model_response_returns_error_not_workflow_completed(self):
        client = MagicMock()
        client.models.generate_content.return_value = _make_model_response(
            text="", function_calls=[], finish_reason="SAFETY"
        )

        orch = TaskOrchestrator(self.dispatcher, max_iterations=3)
        res = await orch.run(client, "gemini-4-hydrogen", "medium", "Empty", "Do something")

        self.assertEqual(res["status"], "error")
        self.assertNotEqual(res["answer"], "Workflow completed.")


class TestSessionAndConfigBatch2(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_session_manager_preserves_general_default_across_restarts(self):
        sess_dir = os.path.join(self.temp_dir, "sessions")
        mgr1 = SessionManager(sessions_dir=sess_dir)
        mgr1.append_turn("user", "Remember this general message")
        mgr1.append_turn("model", "Saved in general history")

        # Simulate process restart: new SessionManager instance switches immediately to a project
        mgr2 = SessionManager(sessions_dir=sess_dir)
        self.assertEqual(len(mgr2.history), 2)
        mgr2.switch_session("project", "alpha", "Alpha")
        restored = mgr2.switch_session("general", "default", "General")
        self.assertEqual(len(restored), 2)
        self.assertEqual(restored[0]["parts"][0]["text"], "Remember this general message")

    def test_config_manager_preserves_transcribe_scalar_updates_and_thinking_levels(self):
        cfg_path = os.path.join(self.temp_dir, "config.json")
        mgr = ConfigManager(config_path=cfg_path)
        mgr.save({
            "stt_endpoint": "gemini-3.5-transcribe",
            "tts_voice": "Puck",
            "tts_speed": 1.25,
            "tts_pace": "brisk",
            "voice_accent": "British",
            "mode": "ptt",
            "ptt_key": "F12",
            "software_gate": True,
            "tier3_thinking_level": "low",
            "tier2_moderate_level": "high",
            "ttrpg_thinking_level": "xhigh",
            "auto_check_updates": False,
            "skipped_update_version": "2.4.0",
            "hardware_telemetry": {"gpu_temp_alert_threshold_c": 90},
            "api": {"new_api_key": None},
        })

        reloaded = ConfigManager(config_path=cfg_path).config
        self.assertEqual(reloaded["stt_endpoint"], "gemini-3.5-transcribe")
        self.assertEqual(reloaded["api"]["stt_endpoint"], "gemini-3.5-transcribe")
        self.assertEqual(reloaded["tts_voice"], "Puck")
        self.assertEqual(reloaded["api"]["voice_name"], "Puck")
        self.assertEqual(reloaded["tts_speed"], 1.25)
        self.assertEqual(reloaded["tts_pace"], "brisk")
        self.assertEqual(reloaded["voice_accent"], "British")
        self.assertEqual(reloaded["mode"], "ptt")
        self.assertEqual(reloaded["ptt_key"], "F12")
        self.assertTrue(reloaded["software_gate"])
        self.assertEqual(reloaded["tier3_thinking_level"], "low")
        self.assertTrue(reloaded["tier3_heavy_model"].endswith(":low"))
        self.assertEqual(reloaded["tier2_moderate_level"], "high")
        self.assertTrue(reloaded["tier2_moderate_model"].endswith(":high"))
        self.assertEqual(reloaded["ttrpg_thinking_level"], "xhigh")
        self.assertTrue(reloaded["ttrpg_model_endpoint"].endswith(":xhigh"))
        self.assertFalse(reloaded["auto_check_updates"])
        self.assertEqual(reloaded["skipped_update_version"], "2.4.0")
        self.assertEqual(reloaded["hardware_telemetry"]["gpu_temp_alert_threshold_c"], 90)

    def test_save_config_atomic_cleans_temp_file_on_replace_failure(self):
        cfg_path = os.path.join(self.temp_dir, "config.json")
        with patch("core.config_manager.os.replace", side_effect=OSError("disk locked")):
            with self.assertRaises(OSError):
                save_config_atomic({"agent_name": "Aether"}, cfg_path)
        leftovers = [f for f in os.listdir(self.temp_dir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_verify_tts_endpoint_uses_audio_modality(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value.candidates = ["ok"]
        self.assertTrue(verify_tts_endpoint(mock_client, "gemini-3.8-flash-tts"))
        kwargs = mock_client.models.generate_content.call_args.kwargs
        self.assertEqual(kwargs["config"], {"response_modalities": ["AUDIO"]})


class TestLifecycleAndGatewayBatch2(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_session_compactor_fallback_uses_distinct_model(self):
        mgr = SessionLifecycleManager(chats_dir=self.temp_dir)
        mgr.record_turn("user", "Open Chrome")
        mgr.record_turn("assistant", "Opened Chrome")

        called_models = []

        async def fake_gen(model, contents, config=None):
            called_models.append(model)
            if model == "gemini-3.8-flash":
                raise RuntimeError("503 overloaded")
            part = SimpleNamespace(text="- User opened Chrome", thought=False)
            return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])

        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(side_effect=fake_gen)
        summary = await mgr.generate_session_summary(client, model="gemini-3.8-flash")
        self.assertEqual(called_models, ["gemini-3.8-flash", "gemini-4-hydrogen"])
        self.assertIn("Chrome", summary)

    async def test_ws_gateway_broadcast_snapshots_connections_during_mutation(self):
        try:
            from api.ws_gateway import ConnectionManager
        except ModuleNotFoundError:
            fake_fastapi = pytypes.ModuleType("fastapi")

            class _FakeRouter:
                def websocket(self, _path):
                    return lambda fn: fn

            fake_fastapi.APIRouter = _FakeRouter
            fake_fastapi.WebSocket = object
            fake_fastapi.WebSocketDisconnect = Exception
            with patch.dict(sys.modules, {"fastapi": fake_fastapi}):
                ws_mod = importlib.import_module("api.ws_gateway")
                ConnectionManager = ws_mod.ConnectionManager

        cm = ConnectionManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()

        async def disconnect_ws2(_msg):
            cm.disconnect("camp1", ws2)

        ws1.send_json.side_effect = disconnect_ws2
        ws2.send_json.side_effect = RuntimeError("broken pipe")

        await cm.connect("camp1", ws1)
        await cm.connect("camp1", ws2)

        # Must not raise RuntimeError: Set changed size during iteration
        await cm.broadcast_to_campaign("camp1", {"type": "ping"})
        self.assertNotIn(ws2, cm.active_campaign_connections.get("camp1", set()))


class TestEngineAndGuiBridgeBatch2(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({
                "agent_name": "Aether",
                "vision": {"enabled": True, "fps": 1.0, "resolution": [768, 768]},
                "security": {"app_whitelist": ["chrome.exe"], "allow_shell": False},
            }, f)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_receive_loop_exits_cleanly_on_empty_eof_and_dedups_partial_intercepts(self):
        engine = AetherEngine()
        engine.is_running = True
        engine.conn_mgr = MagicMock()

        # 1. Clean empty EOF exits instead of busy-spinning
        class EmptyEOFSession:
            def __init__(self):
                self.calls = 0

            async def receive(self):
                self.calls += 1
                if False:
                    yield None

        eof_sess = EmptyEOFSession()
        engine.session = eof_sess
        await engine._async_receive_loop(session=eof_sess, kill_phrase="Aether stop", agent_name="Aether")
        self.assertEqual(eof_sess.calls, 2)
        self.assertIsNone(engine.session)

        # 2. Partial transcription chunks trigger voice intercept only once per turn
        engine2 = AetherEngine()
        engine2.is_running = True
        engine2.conn_mgr = MagicMock()
        engine2.game_engine = MagicMock()
        engine2.media_service = MagicMock()
        engine2.media_service.execute_transport_command = AsyncMock()
        engine2._speak_feedback = AsyncMock()
        engine2.game_engine.intercept_voice_turn_async = AsyncMock(
            return_value=SimpleNamespace(
                handled=True,
                action_type="media_pause",
                user_message="Paused",
                tts_message="",
                details={},
                requires_context_reset=False,
            )
        )

        chunk1 = SimpleNamespace(
            tool_call=None,
            server_content=SimpleNamespace(
                input_transcription=SimpleNamespace(text="pause "),
                model_turn=None,
                output_transcription=None,
                grounding_metadata=None,
                interrupted=False,
                turn_complete=False,
            ),
        )
        chunk2 = SimpleNamespace(
            tool_call=None,
            server_content=SimpleNamespace(
                input_transcription=SimpleNamespace(text="music"),
                model_turn=None,
                output_transcription=None,
                grounding_metadata=None,
                interrupted=False,
                turn_complete=True,
            ),
        )

        class TwoChunkSession:
            async def receive(self):
                yield chunk1
                yield chunk2
                engine2.is_running = False

        s2 = TwoChunkSession()
        engine2.session = s2
        await engine2._async_receive_loop(session=s2, kill_phrase="Aether stop", agent_name="Aether")
        self.assertEqual(engine2.game_engine.intercept_voice_turn_async.call_count, 1)
        self.assertEqual(engine2.media_service.execute_transport_command.call_count, 1)

    async def test_execute_ttrpg_chat_turn_forwards_turn_config(self):
        engine = AetherEngine()
        fake_cfg = {"thinking": "low"}
        engine._execute_turn_modular = MagicMock(return_value=fake_cfg)
        engine.chat = MagicMock()
        engine.chat.send_message_async = AsyncMock(
            return_value=SimpleNamespace(candidates=[], text="The door creaks open.", function_calls=[])
        )
        out = await engine._execute_ttrpg_chat_turn("Inspect the door")
        self.assertEqual(out, "The door creaks open.")
        engine.chat.send_message_async.assert_awaited_once_with("Inspect the door", config=fake_cfg)

    def test_gui_bridge_invoke_ttrpg_tool_config_merge_fifo_events_and_dynamic_dm_prompt(self):
        mock_engine = MagicMock()
        mock_engine.is_running = True
        mock_engine.active_workspace_type = "campaign"
        mock_engine.active_workspace_context_id = "camp1"
        mock_engine.send_text = AsyncMock()
        mock_engine.dispatcher = MagicMock()
        mock_engine.dispatcher.dispatch = AsyncMock(return_value={"status": "success", "spell": "Fireball"})

        loop = asyncio.new_event_loop()
        try:
            bridge = GuiBridge(config_path=self.config_path, engine=mock_engine, loop=loop)

            # 1. invoke_ttrpg_tool must NOT crash with AttributeError on self.loop
            tool_res = bridge.invoke_ttrpg_tool("cast_spell", {"spell_name": "Fireball"})
            self.assertEqual(tool_res["status"], "success")
            self.assertEqual(tool_res["spell"], "Fireball")

            # 2. Partial vision/security save must merge rather than wipe app_whitelist/resolution
            save_res = bridge.save_config({
                "vision": {"fps": 2.0},
                "security": {"allow_shell": True},
                "api": {"new_api_key": None},
            })
            self.assertTrue(save_res["success"])
            cfg = bridge.get_raw_config()
            self.assertEqual(cfg["security"]["app_whitelist"], ["chrome.exe"])
            self.assertTrue(cfg["security"]["allow_shell"])
            self.assertEqual(cfg["vision"]["resolution"], [768, 768])
            self.assertEqual(cfg["vision"]["fps"], 2.0)

            # 3. FIFO event delivery order on single worker thread
            received = []
            done_evt = threading.Event()
            fake_win = MagicMock()

            def _eval_js(js):
                if '"type": "agent_speech_chunk"' in js:
                    received.append(js)
                    if len(received) == 10:
                        done_evt.set()

            fake_win.evaluate_js.side_effect = _eval_js
            bridge._window = fake_win
            for i in range(10):
                bridge._on_engine_event("agent_speech_chunk", {"seq": i})
            self.assertTrue(done_evt.wait(timeout=3.0))
            seqs = [json.loads(s.split("handleEvent(")[1].split(");")[0])["data"]["seq"] for s in received]
            self.assertEqual(seqs, list(range(10)))

            # 4. Campaign prompt uses actual roster names, not hardcoded personal names
            bridge.ttrpg_service = MagicMock()
            bridge.ttrpg_service.manifest = {"campaigns": {"camp1": {"title": "Starfall"}}}
            bridge.ttrpg_service.active_state = {
                "characters": {
                    "c1": {"name": "Lyra"},
                    "c2": {"name": "Bram"},
                }
            }
            bridge.ttrpg_service.build_gm_context_prompt.return_value = "GM"
            bridge.send_workspace_message("campaign", "camp1", "Search the altar")
            sent_prompt = mock_engine.send_text.call_args[0][0]
            self.assertIn("Lyra, Bram", sent_prompt)
            self.assertNotIn("Joe, Sam, Shadow", sent_prompt)
            self.assertNotIn("Olaf", sent_prompt)
            self.assertNotIn("Sandra", sent_prompt)
            self.assertNotIn("Imis", sent_prompt)

            # 5. _run_async_coro closes shared service when falling back to ephemeral asyncio.run()
            fake_svc = MagicMock()
            fake_svc.close = AsyncMock()

            async def _sample_coro():
                return {"ok": True}

            res = bridge._run_async_coro(_sample_coro(), close_services=(fake_svc,))
            self.assertEqual(res, {"ok": True})
            fake_svc.close.assert_awaited_once()
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
