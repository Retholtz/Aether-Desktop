import os
import json
import time
import tempfile
import unittest
from unittest.mock import MagicMock

from core.audio_stream import VADTurnDetector, AudioPipeline
from core.gui_bridge import GuiBridge
from ui.hud_window import HUDBridge


class TestVADTurnDetector(unittest.TestCase):
    def test_default_timeout(self):
        detector = VADTurnDetector()
        self.assertEqual(detector.silence_timeout_ms, 1400)
        self.assertFalse(detector.is_speech_active)
        self.assertIsNone(detector.silence_start_time)

    def test_clamping_limits(self):
        detector = VADTurnDetector()
        detector.update_silence_threshold(200)
        self.assertEqual(detector.silence_timeout_ms, 500)

        detector.update_silence_threshold(5000)
        self.assertEqual(detector.silence_timeout_ms, 4000)

        detector.update_silence_threshold(2200)
        self.assertEqual(detector.silence_timeout_ms, 2200)

    def test_state_machine_flow(self):
        detector = VADTurnDetector(silence_timeout_ms=100)

        # 1. Idle when no voice detected
        state = detector.process_frame(False)
        self.assertEqual(state, "IDLE")

        # 2. Voice detected -> SPEECH_CONTINUING
        state = detector.process_frame(True)
        self.assertEqual(state, "SPEECH_CONTINUING")
        self.assertTrue(detector.is_speech_active)

        # 3. Voice continues
        state = detector.process_frame(True)
        self.assertEqual(state, "SPEECH_CONTINUING")

        # 4. Voice stops -> inside grace window -> SILENCE_WAITING
        state = detector.process_frame(False)
        self.assertEqual(state, "SILENCE_WAITING")
        self.assertTrue(detector.is_speech_active)
        self.assertIsNotNone(detector.silence_start_time)

        # 5. User speaks again before timeout -> speech resumed, silence timer cleared
        state = detector.process_frame(True)
        self.assertEqual(state, "SPEECH_CONTINUING")
        self.assertIsNone(detector.silence_start_time)

        # 6. User stops speaking and stays silent past timeout
        state = detector.process_frame(False)
        self.assertEqual(state, "SILENCE_WAITING")
        time.sleep(0.12)  # Exceed 100ms timeout
        state = detector.process_frame(False)
        self.assertEqual(state, "TURN_COMPLETE")
        self.assertFalse(detector.is_speech_active)
        self.assertIsNone(detector.silence_start_time)

        # 7. Subsequent frame is IDLE
        state = detector.process_frame(False)
        self.assertEqual(state, "IDLE")


class TestBridgeIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.temp_dir.name, "test_config.json")
        sample_config = {
            "vad_trailing_silence_ms": 1400,
            "api": {"agent_name": "AetherTest"},
            "audio": {"vad_trailing_silence_ms": 1400, "mode": "always_on"},
            "ui": {"hud_mode": "normal"}
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(sample_config, f)

        self.bridge = GuiBridge(config_path=self.config_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_gui_bridge_properties(self):
        self.assertIsNotNone(self.bridge.engine)
        self.assertEqual(self.bridge.engine.config.get("vad_trailing_silence_ms"), 1400)
        self.assertEqual(self.bridge.get_config().get("vad_trailing_silence_ms"), 1400)

    def test_update_vad_silence(self):
        # Mock audio_stream on engine
        mock_audio = MagicMock()
        self.bridge._engine.audio = mock_audio

        res = self.bridge.update_vad_silence(2200)
        self.assertTrue(res)

        # Config updated in memory
        self.assertEqual(self.bridge.config.get("vad_trailing_silence_ms"), 2200)
        self.assertEqual(self.bridge.get_config().get("vad_trailing_silence_ms"), 2200)

        # Config saved to disk
        with open(self.config_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved.get("vad_trailing_silence_ms"), 2200)
        self.assertEqual(saved.get("audio", {}).get("vad_trailing_silence_ms"), 2200)

        # Hot-update called on audio pipeline
        mock_audio.set_vad_trailing_silence.assert_called_with(2200)

    def test_hud_bridge_relay(self):
        mock_audio = MagicMock()
        self.bridge._engine.audio = mock_audio

        hud_bridge = HUDBridge(window=None, bridge=self.bridge)
        res = hud_bridge.update_vad_silence(1800)
        self.assertTrue(res)
        self.assertEqual(hud_bridge.get_config().get("vad_trailing_silence_ms"), 1800)
        mock_audio.set_vad_trailing_silence.assert_called_with(1800)



class TestAudioPipelineSpeechStates(unittest.TestCase):
    def setUp(self):
        self.mock_callback = MagicMock()
        # Mock sounddevice query_devices
        with unittest.mock.patch("sounddevice.query_devices") as mock_query:
            mock_query.return_value = {"default_samplerate": 16000.0}
            self.pipeline = AudioPipeline(
                input_device=0,
                output_device=0,
                mode="always_on",
                on_speech_state=self.mock_callback,
                vad_trailing_silence_ms=1400
            )

    def test_finalize_empty_frames_notifies_speech_idle(self):
        self.pipeline._speech_frames = []
        self.pipeline._is_in_speech = True
        self.pipeline._finalize_utterance()
        self.mock_callback.assert_called_with("speech_idle")
        self.assertFalse(self.pipeline._is_in_speech)

    def test_finalize_ambient_noise_discard_notifies_speech_idle(self):
        # Peak RMS < 0.030 and SNR < 2.0
        self.pipeline._speech_frames = [b"\x00\x00" * 1600]
        self.pipeline._utterance_peak_rms = 0.020
        self.pipeline._noise_floor = 0.015
        self.pipeline._is_in_speech = True
        self.pipeline._finalize_utterance()
        self.mock_callback.assert_called_with("speech_idle")
        self.assertFalse(self.pipeline._is_in_speech)
        self.assertTrue(self.pipeline.utterance_queue.empty())

    def test_finalize_short_speech_discard_notifies_speech_idle(self):
        # Loud enough (0.08) but total_pcm < 11200 bytes (~350ms)
        self.pipeline._speech_frames = [b"\x10\x00" * 1000]  # 2000 bytes (< 11200)
        self.pipeline._utterance_peak_rms = 0.080
        self.pipeline._noise_floor = 0.015
        self.pipeline._is_in_speech = True
        self.pipeline._finalize_utterance()
        self.mock_callback.assert_called_with("speech_idle")
        self.assertFalse(self.pipeline._is_in_speech)
        self.assertTrue(self.pipeline.utterance_queue.empty())

    def test_finalize_valid_speech_notifies_speech_finalized_and_enqueues(self):
        # Valid: 12000 bytes (>= 11200) and peak RMS 0.10
        self.pipeline._speech_frames = [b"\x10\x00" * 6000]  # 12000 bytes
        self.pipeline._utterance_peak_rms = 0.100
        self.pipeline._noise_floor = 0.015
        self.pipeline._is_in_speech = True
        self.pipeline.loop = MagicMock()
        self.pipeline.loop.is_running.return_value = True

        self.pipeline._finalize_utterance()
        self.mock_callback.assert_called_with("speech_finalized")
        self.assertFalse(self.pipeline._is_in_speech)
        self.pipeline.loop.call_soon_threadsafe.assert_called()

    def test_reset_vad_notifies_speech_idle_if_was_in_speech(self):
        self.pipeline._is_in_speech = True
        self.pipeline.reset_vad()
        self.mock_callback.assert_called_with("speech_idle")
        self.assertFalse(self.pipeline._is_in_speech)

    def test_ptt_press_and_release(self):
        self.pipeline.mode = "ptt"
        self.pipeline.set_ptt(True)
        self.assertTrue(self.pipeline.ptt_active)
        self.assertTrue(self.pipeline._is_in_speech)
        self.mock_callback.assert_called_with("speech_detected")

        # Now release with empty frames -> should discard and notify speech_idle
        self.pipeline._speech_frames = []
        self.mock_callback.reset_mock()
        self.pipeline.set_ptt(False)
        self.assertFalse(self.pipeline.ptt_active)
        self.mock_callback.assert_called_with("speech_idle")

    def test_input_callback_ptt_active_no_unbound_local_error(self):
        import numpy as np
        self.pipeline.mode = "ptt"
        self.pipeline._running = True
        self.pipeline.ptt_active = True
        dummy_indata = np.full((480, 1), 0.05, dtype=np.float32)

        # Should execute without throwing UnboundLocalError
        self.pipeline._input_callback(dummy_indata, 480, None, None)
        self.assertGreater(self.pipeline.current_mic_level, 0.0)
        self.assertGreater(len(self.pipeline._speech_frames), 0)


class TestEngineSpeechStateHandling(unittest.TestCase):
    def test_engine_on_speech_state_transitions(self):
        from core.engine import AetherEngine
        cfg = {"api": {"agent_name": "Aether"}}
        engine = AetherEngine(config_getter=lambda: cfg)
        engine.notify = MagicMock()
        engine.is_running = True

        # Test speech_detected
        engine._on_speech_state("speech_detected")
        engine.notify.assert_called_with("status", {"state": "hearing", "message": "Hearing speech..."})

        # Test speech_finalized
        engine._on_speech_state("speech_finalized")
        engine.notify.assert_called_with("status", {"state": "transcribing", "message": "Transcribing speech..."})

        # Test speech_idle returns to listening
        engine._on_speech_state("speech_idle")
        engine.notify.assert_called_with("status", {"state": "listening", "message": "Aether is listening..."})

        # Test speech_discarded returns to listening
        engine._on_speech_state("speech_discarded")
        engine.notify.assert_called_with("status", {"state": "listening", "message": "Aether is listening..."})


if __name__ == "__main__":
    unittest.main()


