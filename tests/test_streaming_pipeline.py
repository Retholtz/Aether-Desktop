"""
tests/test_streaming_pipeline.py - Unit tests for WebSocket streaming optimizations,
PTT stream gating, end-of-turn sentinel dispatch, and config synchronization.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np

from core.audio_stream import AudioPipeline
from core.config_manager import sync_config_schema, ConfigManager
from core.engine import AetherEngine


class TestStreamingPipeline(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_ptt_streaming_isolation_and_sentinel(self):
        """
        Verifies:
        1. In PTT mode with ptt_active=False, no frames leak into input_queue.
        2. Calling set_ptt(True) flushes rolling pre-roll into input_queue.
        3. While ptt_active=True, mic frames stream into input_queue.
        4. Calling set_ptt(False) queues b"__END_OF_TURN__" into input_queue.
        """
        pipeline = AudioPipeline(
            mode="ptt",
            wake_word_enabled=False,
            loop=self.loop,
        )
        pipeline.target_input_rate = 16000
        pipeline.hw_in_rate = 16000
        pipeline._running = True

        # 1. Feed audio while PTT is NOT pressed
        dummy_frame = (0.02 * np.sin(np.linspace(0, 0.05, 800, endpoint=False))).astype(np.float32)
        for _ in range(5):
            pipeline._input_callback(dummy_frame, len(dummy_frame), None, None)

        # Pre-roll should have accumulated, but input_queue MUST be completely empty
        self.assertGreater(len(pipeline._preroll_frames), 0)
        self.assertTrue(pipeline.input_queue.empty(), "Unpressed PTT must not leak audio into input_queue!")

        # 2. Press PTT
        pipeline.set_ptt(True)
        self.assertTrue(pipeline.ptt_active)
        # Pre-roll frames should now have been transferred to input_queue
        self.assertFalse(pipeline.input_queue.empty(), "Pre-roll frames must be pushed to input_queue on PTT press!")
        initial_queued_count = pipeline.input_queue.qsize()

        # 3. Stream frames while PTT is held
        for _ in range(3):
            pipeline._input_callback(dummy_frame, len(dummy_frame), None, None)

        self.assertGreater(pipeline.input_queue.qsize(), initial_queued_count)

        # 4. Release PTT
        pipeline.set_ptt(False)
        self.assertFalse(pipeline.ptt_active)

        # Drain input_queue and verify b"__END_OF_TURN__" is present at the end
        items = []
        while not pipeline.input_queue.empty():
            items.append(pipeline.input_queue.get_nowait())

        self.assertIn(b"__END_OF_TURN__", items, "Releasing PTT must queue __END_OF_TURN__ sentinel!")
        self.assertEqual(items[-1], b"__END_OF_TURN__")

    def test_open_mic_streaming_gating(self):
        """
        Verifies that in open mic mode (always_on without wake word):
        1. Ambient background silence does not leak to input_queue.
        2. Speech frames stream to input_queue.
        3. __END_OF_TURN__ is emitted on utterance finalization.
        """
        pipeline = AudioPipeline(
            mode="always_on",
            always_on_mode="always_on",
            wake_word_enabled=False,
            vad_trailing_silence_ms=800,
            loop=self.loop,
        )
        pipeline.target_input_rate = 16000
        pipeline.hw_in_rate = 16000
        pipeline._running = True

        # 1. Feed quiet noise floor (RMS < 0.005)
        quiet_frame = (np.random.randn(800) * 0.002).astype(np.float32)
        for _ in range(6):
            pipeline._input_callback(quiet_frame, len(quiet_frame), None, None)

        self.assertTrue(pipeline.input_queue.empty(), "Quiet ambient audio must not route to input_queue in open mic!")

        # 2. Feed clear speech signal (audible 440Hz tone, RMS > 0.030)
        t = np.linspace(0, 0.05, 800, endpoint=False)
        loud_speech = (0.05 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        for _ in range(10):
            pipeline._input_callback(loud_speech, len(loud_speech), None, None)

        self.assertFalse(pipeline.input_queue.empty(), "Speech audio must stream to input_queue!")

        # 3. Finalize speech
        pipeline._finalize_utterance()
        items = []
        while not pipeline.input_queue.empty():
            items.append(pipeline.input_queue.get_nowait())

        self.assertIn(b"__END_OF_TURN__", items)

    def test_send_loop_handles_audio_stream_end_sentinel(self):
        """
        Verifies that AetherEngine._send_loop drains input_queue and converts
        b"__END_OF_TURN__" into an active_session.send_realtime_input(audio_stream_end=True) call.
        """
        engine = AetherEngine()
        engine.is_running = True

        mock_audio = MagicMock()
        mock_audio.input_queue = asyncio.Queue()
        engine.audio = mock_audio

        # Put dummy audio chunk and end-of-turn sentinel into input_queue
        dummy_pcm = b"\x01\x00" * 800
        mock_audio.input_queue.put_nowait(dummy_pcm)
        mock_audio.input_queue.put_nowait(b"__END_OF_TURN__")

        mock_session = AsyncMock()

        # Run _send_loop for a brief iteration
        async def run_brief_send_loop():
            send_task = asyncio.create_task(engine._send_loop(session=mock_session))
            await asyncio.sleep(0.05)
            engine.is_running = False
            send_task.cancel()
            try:
                await send_task
            except asyncio.CancelledError:
                pass

        self.loop.run_until_complete(run_brief_send_loop())

        # Verify that send_realtime_input was called with audio Blob AND audio_stream_end=True
        calls = mock_session.send_realtime_input.call_args_list
        has_audio = any("audio" in call.kwargs for call in calls)
        has_end_signal = any(call.kwargs.get("audio_stream_end") is True for call in calls)

        self.assertTrue(has_audio, "send_realtime_input must receive PCM audio Blob!")
        self.assertTrue(has_end_signal, "send_realtime_input must receive audio_stream_end=True on turn sentinel!")

    def test_config_sync_pipeline_mode_and_vad_silence(self):
        """
        Verifies that sync_config_schema and ConfigManager synchronize
        pipeline_mode to 'live' and vad_trailing_silence_ms to 800.
        """
        raw_cfg = {
            "api": {"pipeline_mode": "live"},
            "audio": {}
        }
        synced = sync_config_schema(raw_cfg)

        self.assertEqual(synced["pipeline_mode"], "live")
        self.assertEqual(synced["api"]["pipeline_mode"], "live")
        self.assertEqual(synced["vad_trailing_silence_ms"], 800)
        self.assertEqual(synced["audio"]["vad_trailing_silence_ms"], 800)


if __name__ == "__main__":
    unittest.main()
