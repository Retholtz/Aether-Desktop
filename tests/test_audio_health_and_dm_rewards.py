import asyncio
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
from google.genai import types

from core.audio_stream import AudioPipeline
from core.connection_manager import ConnectionState
from core.engine import AetherEngine
from core.gui_bridge import GuiBridge
from core.session_lifecycle import SessionLifecycleManager
from core.ttrpg_service import TTRPGService


class TestAudioPipelineOutputHealth(unittest.TestCase):
    def setUp(self):
        with patch("sounddevice.query_devices") as mock_query:
            mock_query.return_value = {"default_samplerate": 48000.0, "name": "Mock Buds"}
            self.pipeline = AudioPipeline(input_device=0, output_device=0, mode="ptt")
            self.pipeline._running = True

    def test_output_callback_tracks_timestamp_and_status(self):
        outdata = np.zeros((512, 1), dtype=np.float32)
        self.assertEqual(self.pipeline._last_output_callback_time, 0.0)
        self.pipeline._output_callback(outdata, 512, None, "output underflow")
        self.assertGreater(self.pipeline._last_output_callback_time, 0.0)
        self.assertEqual(self.pipeline._last_output_status, "output underflow")
        self.assertEqual(self.pipeline._output_status_error_count, 1)

    def test_ensure_output_stream_healthy_detects_stalled_bluetooth_callback(self):
        mock_stream = MagicMock()
        mock_stream.active = True
        self.pipeline.out_stream = mock_stream
        now = time.monotonic()
        self.pipeline._output_stream_started_at = now - 10.0
        self.pipeline._last_output_callback_time = now - 3.0  # > 1.5s stall despite active=True
        self.pipeline._last_recovery_attempt_time = 0.0

        with patch.dict(os.environ, {"AETHER_TESTING": "0"}):
            with patch("core.audio_stream._reinitialize_portaudio") as mock_reinit:
                self.pipeline.ensure_output_stream_healthy()
                mock_reinit.assert_called_once()

                # Subsequent call within 2.0s should be rate-limited
                self.pipeline.ensure_output_stream_healthy()
                mock_reinit.assert_called_once()

    def test_ensure_output_stream_healthy_skips_when_callback_is_fresh(self):
        mock_stream = MagicMock()
        mock_stream.active = True
        self.pipeline.out_stream = mock_stream
        now = time.monotonic()
        self.pipeline._output_stream_started_at = now - 10.0
        self.pipeline._last_output_callback_time = now - 0.05

        with patch.dict(os.environ, {"AETHER_TESTING": "0"}):
            with patch("core.audio_stream._reinitialize_portaudio") as mock_reinit:
                self.pipeline.ensure_output_stream_healthy()
                mock_reinit.assert_not_called()


class TestSessionCompactorAndRotationContinuity(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.chats_dir = os.path.join(self.test_dir, "chats")
        self.lifecycle = SessionLifecycleManager(chats_dir=self.chats_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_compactor_disables_thinking_budget_and_skips_thought_parts(self):
        mock_client = MagicMock()
        thought_part = MagicMock()
        thought_part.text = "internal reasoning"
        thought_part.thought = True
        answer_part = MagicMock()
        answer_part.text = "- Bullet 1\n- Bullet 2"
        answer_part.thought = False

        mock_candidate = MagicMock()
        mock_candidate.content.parts = [thought_part, answer_part]
        mock_resp = MagicMock()
        mock_resp.candidates = [mock_candidate]
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        self.lifecycle.record_turn("user", "Ask the captain for weapons")
        self.lifecycle.record_turn("assistant", "Commander Quarterlight agrees.")

        summary = await self.lifecycle.generate_session_summary(client=mock_client, is_game_mode=False)
        self.assertEqual(summary, "- Bullet 1\n- Bullet 2")

        cfg = mock_client.aio.models.generate_content.call_args.kwargs["config"]
        self.assertGreaterEqual(cfg.max_output_tokens, 1024)
        self.assertIsNotNone(cfg.thinking_config)
        self.assertEqual(cfg.thinking_config.thinking_budget, 0)

    def test_rehydrate_converts_turns_for_google_sdk_session(self):
        engine = AetherEngine()
        engine.turn_history = [
            {"role": "user", "content": "Hello DM", "timestamp": 1.0},
            {"role": "assistant", "content": "Welcome hero", "timestamp": 2.0},
        ]

        class FakeGoogleSession:
            __module__ = "google.genai.live"

            def __init__(self):
                self.send_client_content = MagicMock(return_value=None)

        fake_session = FakeGoogleSession()
        engine.live_session = fake_session
        engine._rehydrate_session_context()

        fake_session.send_client_content.assert_called_once()
        turns = fake_session.send_client_content.call_args.kwargs["turns"]
        self.assertEqual(len(turns), 2)
        self.assertTrue(all(isinstance(t, types.Content) for t in turns))
        self.assertEqual(turns[0].role, "user")
        self.assertEqual(turns[1].role, "model")
        self.assertEqual(turns[1].parts[0].text, "Welcome hero")

    async def test_rotate_live_session_rehydrates_except_on_explicit_reset(self):
        async def _run_rotation(reason: str, reset_flag: bool):
            engine = AetherEngine()
            engine.is_running = True
            engine._reset_context_flag = reset_flag
            engine.session_lifecycle = SessionLifecycleManager(chats_dir=self.chats_dir)
            engine.session_lifecycle.record_turn("user", "Ask captain")
            engine.session_lifecycle.record_turn("assistant", "Captain responds")
            engine.conn_mgr = MagicMock()
            engine.audio = MagicMock()
            engine.audio.is_output_empty.return_value = True
            engine.audio.is_speaking = False
            engine.audio._is_in_speech = False
            engine.audio_player.is_playing = False
            engine._active_cm = None
            engine._active_recv_task = None

            new_session = MagicMock()
            new_cm = MagicMock()
            new_cm.__aenter__ = AsyncMock(return_value=new_session)
            new_cm.__aexit__ = AsyncMock(return_value=None)
            mock_client = MagicMock()
            mock_client.aio.live.connect.return_value = new_cm

            with patch.object(engine.session_lifecycle, "generate_session_summary", new=AsyncMock(return_value="- Summary")), \
                 patch.object(engine, "_build_system_instruction", return_value="Base"), \
                 patch.object(engine, "_receive_loop", new=AsyncMock(return_value=None)), \
                 patch.object(engine, "_rehydrate_session_context") as mock_rehydrate:
                ok = await engine.rotate_live_session(
                    client=mock_client,
                    model_id="gemini-3.1-flash-live-preview",
                    kill_phrase="Aether stop",
                    agent_name="Aether",
                    reason=reason,
                )
                self.assertTrue(ok)
                return mock_rehydrate.call_count

        self.assertEqual(await _run_rotation("Lifecycle threshold after turn", False), 1)
        self.assertEqual(await _run_rotation("User Context Reset", True), 0)


class TestEngineAudioTelemetryAndDMDirectives(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.manifest_path = os.path.join(self.test_dir, "campaigns.json")
        self.data_dir = os.path.join(self.test_dir, "campaigns")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_receive_loop_warns_when_turn_has_text_but_silent_audio(self):
        engine = AetherEngine()
        engine.is_running = True
        engine.audio = MagicMock()
        engine.audio.is_output_empty.return_value = True
        engine.audio.is_speaking = False
        engine.session_lifecycle = SessionLifecycleManager(chats_dir=self.test_dir)
        engine.conn_mgr = MagicMock()

        silent_pcm = np.zeros(480, dtype=np.int16).tobytes()
        part = MagicMock()
        part.inline_data = MagicMock(data=silent_pcm)
        part.text = None
        server_content = MagicMock()
        server_content.model_turn = MagicMock(parts=[part])
        server_content.output_transcription = MagicMock(text="Commander Quarterlight speaks.")
        server_content.input_transcription = None
        server_content.grounding_metadata = None
        server_content.interrupted = False
        server_content.turn_complete = True

        resp = MagicMock()
        resp.tool_call = None
        resp.session_resumption_update = None
        resp.go_away = None
        resp.server_content = server_content

        class FakeSession:
            async def receive(self):
                yield resp
                engine.is_running = False

        with patch("core.engine.logger") as mock_logger:
            await engine._async_receive_loop(
                session=FakeSession(),
                kill_phrase="",
                agent_name="Aether",
            )
            mock_logger.warning.assert_any_call(
                "[AUDIO_OUT] Turn produced text (30 chars) with empty/silent audio (chunks=1, peak=0.0000)!"
            )

    def test_dm_prompt_and_workspace_message_include_autonomous_reward_directive(self):
        service = TTRPGService(data_dir=self.data_dir, manifest_path=self.manifest_path)
        service.create_campaign(
            title="Reward Test",
            system_preset="dnd_5e",
            gm_mode="interactive_narrator",
            setting_overview="Baldur's Gate",
            tone="Gritty",
            campaign_id="reward_test",
        )
        service.add_character({"name": "Joe", "archetype": "Barbarian 20", "level": 20})

        gm_prompt = service.build_gm_context_prompt()
        self.assertIn("AUTONOMOUS DM GEAR & REWARD BESTOWAL", gm_prompt)
        self.assertIn("manage_party_inventory", gm_prompt)

        # Also verify GuiBridge.send_workspace_message wraps with autonomous loot directive
        mock_engine = MagicMock()
        mock_engine.is_running = True
        mock_engine.active_workspace_type = "campaign"
        mock_engine.active_workspace_context_id = "reward_test"
        mock_engine.send_text = MagicMock(return_value="mock_coro")

        bridge = GuiBridge.__new__(GuiBridge)
        bridge._engine = mock_engine
        bridge.ttrpg_service = service
        bridge._loop = asyncio.new_event_loop()
        bridge.set_ttrpg_session_active = MagicMock()
        try:
            with patch("asyncio.run_coroutine_threadsafe") as mock_run_ts:
                res = bridge.send_workspace_message(
                    "campaign",
                    "reward_test",
                    "Joe uses Charisma to ask for weapons for each character's class",
                )
                self.assertTrue(res["success"])
                sent_prompt = mock_engine.send_text.call_args.args[0]
                self.assertIn("AUTONOMOUS REWARD & LOOT BESTOWAL", sent_prompt)
                self.assertIn("manage_party_inventory", sent_prompt)
                mock_run_ts.assert_called_once()
        finally:
            bridge._loop.close()


if __name__ == "__main__":
    unittest.main()
