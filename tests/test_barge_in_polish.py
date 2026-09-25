import unittest
from unittest.mock import MagicMock, patch
import numpy as np

from core.audio_output import InterruptibleAudioPlayer, apply_micro_fade_out
from core.audio_stream import VoiceActivityDetector, AudioPipeline
from core.engine import AetherEngine


class TestInterruptibleAudioPlayer(unittest.TestCase):
    def test_enqueue_and_atomic_barge_in_flush(self):
        player = InterruptibleAudioPlayer(sample_rate=24000, channels=1)
        chunk = (np.full(1024, 12000, dtype=np.int16)).tobytes()

        player.enqueue_chunk(chunk)
        player.enqueue_chunk(chunk)
        player.is_playing = True
        player._last_chunk = chunk
        self.assertEqual(player.audio_queue.qsize(), 2)

        # Trigger barge-in
        player.trigger_barge_in()
        self.assertTrue(player._interrupt_event.is_set())
        self.assertTrue(player.audio_queue.empty())
        self.assertFalse(player.is_playing)
        self.assertIsNotNone(player._pending_fade_chunk)

        # Enqueue while interrupted should be ignored
        player.enqueue_chunk(chunk)
        self.assertTrue(player.audio_queue.empty())

        # Reset interrupt allows enqueuing again
        player.reset_interrupt()
        self.assertFalse(player._interrupt_event.is_set())
        player.enqueue_chunk(chunk)
        self.assertEqual(player.audio_queue.qsize(), 1)

    def test_micro_fade_out_eliminates_pop(self):
        # 20ms of constant full-scale tone at 24kHz (480 samples)
        raw = np.full(480, 16000, dtype=np.int16).tobytes()
        faded_bytes = apply_micro_fade_out(raw, sample_rate=24000, fade_ms=8.0)
        faded_samples = np.frombuffer(faded_bytes, dtype=np.int16)

        # 8ms at 24kHz = 192 samples
        self.assertEqual(len(faded_samples), 192)
        # First sample starts at original amplitude, last sample tapers smoothly to 0
        self.assertAlmostEqual(int(faded_samples[0]), 16000, delta=5)
        self.assertEqual(int(faded_samples[-1]), 0)


class TestVoiceActivityDetectorEchoGating(unittest.TestCase):
    def test_default_parameters(self):
        vad = VoiceActivityDetector()
        self.assertAlmostEqual(vad.base_energy_threshold, 0.015)
        self.assertAlmostEqual(vad.speaker_ducking_factor, 2.4)
        self.assertEqual(vad.trailing_silence_ms, 1400)

    def test_speaker_bleed_rejected_when_assistant_speaking_int16(self):
        vad = VoiceActivityDetector(base_energy_threshold=0.015, speaker_ducking_factor=2.4)
        # Simulated speaker bleed with RMS ~0.025 (int16 value ~819)
        # Above base threshold (0.015) so it would trigger when silent,
        # but below ducked threshold (0.015 * 2.4 = 0.036) when assistant is speaking.
        bleed_frame = np.full(1024, int(0.025 * 32768), dtype=np.int16)

        self.assertTrue(vad.is_user_speaking(bleed_frame, is_assistant_speaking=False))
        self.assertFalse(vad.is_user_speaking(bleed_frame, is_assistant_speaking=True))

    def test_genuine_user_speech_detected_when_assistant_speaking_int16(self):
        vad = VoiceActivityDetector(base_energy_threshold=0.015, speaker_ducking_factor=2.4)
        # Near-field human barge-in voice with RMS ~0.060 (exceeds 0.036 ducked threshold)
        user_frame = np.full(1024, int(0.060 * 32768), dtype=np.int16)

        self.assertTrue(vad.is_user_speaking(user_frame, is_assistant_speaking=False))
        self.assertTrue(vad.is_user_speaking(user_frame, is_assistant_speaking=True))


class TestAudioPipelineBargeInImmunity(unittest.TestCase):
    def setUp(self):
        self.speech_events = []
        with patch("sounddevice.query_devices") as mock_query:
            mock_query.return_value = {"default_samplerate": 16000.0}
            self.pipeline = AudioPipeline(
                input_device=0,
                output_device=0,
                mode="always_on",
                on_speech_state=lambda s: self.speech_events.append(s),
                vad_trailing_silence_ms=1400
            )
            self.pipeline._running = True

    def test_speaker_bleed_does_not_self_interrupt_pipeline(self):
        # Simulate assistant speaking
        self.pipeline.is_speaking = True
        self.pipeline._current_output_rms = 0.08

        # Feed 1kHz sine wave at speaker bleed amplitude (RMS ~0.035)
        t = np.linspace(0, 0.05, 800, endpoint=False)
        bleed_signal = (0.05 * np.sin(2 * np.pi * 400 * t)).astype(np.float32).reshape(-1, 1)

        self.pipeline._input_callback(bleed_signal, 800, None, None)
        self.assertFalse(self.pipeline._is_in_speech)
        self.assertNotIn("speech_detected", self.speech_events)

    def test_loud_user_barge_in_triggers_speech_detected(self):
        self.pipeline.is_speaking = True
        self.pipeline._current_output_rms = 0.05

        # Feed near-field user voice signal (RMS ~0.18)
        t = np.linspace(0, 0.05, 800, endpoint=False)
        user_signal = (0.25 * np.sin(2 * np.pi * 300 * t)).astype(np.float32).reshape(-1, 1)

        self.pipeline._input_callback(user_signal, 800, None, None)
        self.assertTrue(self.pipeline._is_in_speech)
        self.assertIn("speech_detected", self.speech_events)

    def test_pipeline_trigger_barge_in_renders_soft_fade_on_output_callback(self):
        pcm = np.full(1024, 16000, dtype=np.int16).tobytes()
        self.pipeline.write_output_chunk(pcm)
        self.assertTrue(self.pipeline.is_speaking)

        self.pipeline.trigger_barge_in()
        self.assertFalse(self.pipeline.is_speaking)
        self.assertTrue(self.pipeline.is_output_empty())
        self.assertIsNotNone(self.pipeline._fade_out_buffer)

        outdata = np.ones((512, 1), dtype=np.float32)
        self.pipeline._output_callback(outdata, 512, None, None)
        # First sample has non-zero faded audio, tail of frame is cleanly zeroed
        self.assertGreater(float(outdata[0, 0]), 0.1)
        self.assertEqual(float(outdata[-1, 0]), 0.0)


class TestEngineInterruptionProtocol(unittest.TestCase):
    def test_on_vad_speech_detected_triggers_full_barge_in_protocol(self):
        engine = AetherEngine(config_getter=lambda: {"api": {"agent_name": "Aether"}})
        engine.is_running = True
        engine.is_speaking = True

        engine.audio_player = MagicMock()
        engine.audio = MagicMock()
        engine.audio.is_speaking = True
        engine.live_session = MagicMock()
        engine.hud_window = MagicMock()
        engine.notify = MagicMock()

        engine.on_vad_speech_detected()

        # 1. Local audio player & pipeline stopped immediately
        engine.audio_player.trigger_barge_in.assert_called_once()
        engine.audio.trigger_barge_in.assert_called_once()

        # 2. Engine speaker flag reset
        self.assertFalse(engine.is_speaking)

        # 3. Upstream Gemini Live session notified
        self.assertTrue(
            engine.live_session.send_client_content.called or engine.live_session.send.called
        )

        # 4. HUD UI notified via evaluate_js
        engine.hud_window.evaluate_js.assert_called_once_with(
            "window.onAssistantInterrupted && window.onAssistantInterrupted()"
        )


if __name__ == "__main__":
    unittest.main()
