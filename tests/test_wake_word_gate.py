import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.wake_word import WakeWordDetector, AudioGateState, ensure_wake_sleep_sounds
from core.config_manager import ConfigManager, sync_config_schema
from core.audio_stream import AudioPipeline


class TestWakeWordGate(unittest.TestCase):
    def setUp(self):
        self.woke_triggered = False
        self.slept_triggered = False

        def on_wake():
            self.woke_triggered = True

        def on_sleep():
            self.slept_triggered = True

        self.detector = WakeWordDetector(
            wake_phrase="Hey Aether",
            sleep_phrase="Aether stop listening",
            listening_mode="wake_word",
            idle_timeout_sec=0.2,  # Short timeout for fast unit testing
            on_wake_callback=on_wake,
            on_sleep_callback=on_sleep
        )

    def test_initial_state_is_idle(self):
        self.assertEqual(self.detector.state, AudioGateState.IDLE_LISTENING)
        # Ambient audio should be suppressed
        should_route = self.detector.process_frame(b'\x00\x01' * 160)
        self.assertFalse(should_route)

    @patch.object(WakeWordDetector, '_check_hotword_trigger', return_value=True)
    def test_wake_phrase_opens_gate(self, mock_trigger):
        should_route = self.detector.process_frame(b'\x05\x05' * 160)
        self.assertTrue(should_route)
        self.assertEqual(self.detector.state, AudioGateState.ACTIVE_CONVERSATION)
        self.assertTrue(self.woke_triggered)

    def test_auto_sleep_after_timeout_in_wake_word_mode(self):
        self.detector.transition_to_active()
        self.assertEqual(self.detector.state, AudioGateState.ACTIVE_CONVERSATION)

        # Wait past 0.2s idle timeout
        time.sleep(0.25)

        should_route = self.detector.process_frame(b'\x00\x00' * 160)
        self.assertFalse(should_route)
        self.assertEqual(self.detector.state, AudioGateState.IDLE_LISTENING)
        self.assertTrue(self.slept_triggered)

    def test_wake_sleep_toggle_mode_does_not_timeout_until_sleep_phrase(self):
        toggle_detector = WakeWordDetector(
            wake_phrase="Hey Aether",
            sleep_phrase="Aether stop listening",
            listening_mode="wake_sleep_toggle",
            idle_timeout_sec=0.15,
        )
        self.assertEqual(toggle_detector.state, AudioGateState.IDLE_LISTENING)

        # Wake with "Hey Aker" (phonetic variant)
        self.assertTrue(toggle_detector.check_transcript("Hey Aker"))
        self.assertEqual(toggle_detector.state, AudioGateState.ACTIVE_CONVERSATION)

        # Wait past 0.15s timeout -> must stay ACTIVE_CONVERSATION in toggle mode!
        time.sleep(0.25)
        self.assertTrue(toggle_detector.process_frame(b'\x01\x01' * 160))
        self.assertEqual(toggle_detector.state, AudioGateState.ACTIVE_CONVERSATION)

        # Now say "Aether stop listening" -> toggles mic OFF to IDLE_LISTENING
        self.assertTrue(toggle_detector.check_sleep_transcript("Aether stop listening"))
        self.assertEqual(toggle_detector.state, AudioGateState.IDLE_LISTENING)
        self.assertFalse(toggle_detector.process_frame(b'\x01\x01' * 160))

    def test_always_on_mode_routes_continuously(self):
        open_detector = WakeWordDetector(
            wake_phrase="Hey Aether",
            sleep_phrase="Aether stop listening",
            listening_mode="always_on",
        )
        self.assertEqual(open_detector.state, AudioGateState.ACTIVE_CONVERSATION)
        self.assertTrue(open_detector.process_frame(b'\x01\x02' * 160))

    def test_relaxed_phonetic_wake_phrase_matching_including_hey_aker(self):
        # Verify "Hey Aker" and other common STT mis-transcriptions wake the agent
        for variant in [
            "Hey Aker",
            "Hey Aker what time is it",
            "Hey Acre",
            "Hey Ether",
            "Hey Arthur",
            "Hey Asher",
            "Hi Aker",
            "Okay Aether",
            "Aether open browser",
        ]:
            matched, remainder = self.detector.matches_wake_phrase(variant)
            self.assertTrue(matched, f"Expected relaxed wake phrase match for: '{variant}'")

        matched_cmd, rem_cmd = self.detector.matches_wake_phrase("Hey Aker what is on my screen")
        self.assertTrue(matched_cmd)
        self.assertEqual(rem_cmd, "what is on my screen")

    def test_sleep_phrase_matching_variants(self):
        for phrase in [
            "Aether stop listening",
            "Aker stop listening",
            "Ether stop listening",
            "stop listening",
            "Aether go to sleep",
        ]:
            self.assertTrue(
                self.detector.matches_sleep_phrase(phrase),
                f"Expected sleep phrase match for: '{phrase}'"
            )

    def test_pre_roll_buffer_retains_trailing_frames(self):
        sample_chunk = b'\xAA\xBB' * 500
        self.detector.process_frame(sample_chunk)
        buffered = self.detector.get_pre_roll_bytes()
        self.assertTrue(len(buffered) >= len(sample_chunk))
        self.assertIn(sample_chunk, buffered)

    def test_record_active_turn_refreshes_timeout(self):
        self.detector.transition_to_active()
        time.sleep(0.12)
        self.detector.record_active_turn()
        time.sleep(0.12)
        should_route = self.detector.process_frame(b'\x01\x02' * 160)
        self.assertTrue(should_route)
        self.assertEqual(self.detector.state, AudioGateState.ACTIVE_CONVERSATION)

    def test_config_manager_persists_wake_sleep_kill_and_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "config.json"
            mgr = ConfigManager(cfg_path)
            loaded = mgr.load()
            self.assertEqual(loaded["wake_phrase"], "Hey Aether")
            self.assertEqual(loaded["sleep_phrase"], "Aether stop listening")
            self.assertEqual(loaded["kill_phrase"], "Aether stop")

            saved = mgr.save({
                "agent_name": "Nova",
                "wake_phrase": "Hey Nova",
                "sleep_phrase": "Nova stop listening",
                "kill_phrase": "Nova stop",
                "always_on_mode": "wake_sleep_toggle",
                "tts_speed": 1.15,
            })
            self.assertEqual(saved["wake_phrase"], "Hey Nova")
            self.assertEqual(saved["sleep_phrase"], "Nova stop listening")
            self.assertEqual(saved["always_on_mode"], "wake_sleep_toggle")
            self.assertTrue(saved["wake_word_enabled"])

    def test_audio_pipeline_callback_gating_and_pre_roll(self):
        pipeline = AudioPipeline(
            wake_phrase="Hey Aether",
            sleep_phrase="Aether stop listening",
            kill_phrase="Aether stop",
            always_on_mode="wake_word",
            wake_word_enabled=True,
            idle_timeout_seconds=8.0,
        )
        # 1. In IDLE_LISTENING, audio_callback should suppress frames from vad_queue
        frame_idle = b'\x10\x20' * 160
        pipeline.audio_callback(frame_idle, 160, {}, 0)
        self.assertTrue(pipeline.vad_queue.empty())

        # 2. When wake detector triggers, pre-roll + current frame are routed to vad_queue
        with patch.object(pipeline.wake_detector, '_check_hotword_trigger', return_value=True):
            frame_wake = b'\x30\x40' * 160
            pipeline.audio_callback(frame_wake, 160, {}, 0)
            self.assertFalse(pipeline.vad_queue.empty())
            routed = pipeline.vad_queue.get_nowait()
            self.assertIn(frame_idle, routed)
            self.assertTrue(routed.endswith(frame_wake))

    def test_settings_ui_grid_and_always_on_modes_structure(self):
        html_path = Path(__file__).resolve().parent.parent / "ui" / "static" / "index.html"
        html = html_path.read_text(encoding="utf-8")
        self.assertIn('id="input-agent-name"', html)
        self.assertIn('id="input-wake-phrase"', html)
        self.assertIn('id="input-sleep-phrase"', html)
        self.assertIn('id="input-kill-phrase"', html)
        self.assertIn('id="select-tts-endpoint"', html)
        self.assertIn('id="select-output-voice"', html)
        self.assertIn('id="input-tts-speed"', html)
        self.assertIn('id="tts-speed-label"', html)
        self.assertIn('id="select-voice-accent"', html)
        self.assertIn('id="alwaysOnSettingsGroup"', html)
        self.assertIn('id="alwaysOnModeOpen"', html)
        self.assertIn('id="alwaysOnModeWakeWord"', html)
        self.assertIn('id="alwaysOnModeToggle"', html)
        # Verify Kill Phrase is in the left column before Voice Accent (right column under AI Output Voice)
        # and outside alwaysOnSettingsGroup so it stays visible in PTT mode
        kill_idx = html.index('id="input-kill-phrase"')
        accent_idx = html.index('id="select-voice-accent"')
        always_on_idx = html.index('id="alwaysOnSettingsGroup"')
        self.assertLess(kill_idx, accent_idx)
        self.assertLess(kill_idx, always_on_idx)
        self.assertEqual(html.count('id="input-kill-phrase"'), 1)

    def test_wake_and_sleep_sound_files_exist(self):
        sounds_dir = Path(__file__).resolve().parent.parent / "ui" / "sounds"
        ensure_wake_sleep_sounds(sounds_dir)
        self.assertTrue((sounds_dir / "wake.wav").exists())
        self.assertTrue((sounds_dir / "sleep.wav").exists())

    def test_short_two_word_wake_phrase_clears_voice_biometrics_threshold(self):
        import io
        import wave
        import numpy as np
        from core.voice_verifier import VoiceProfileVerifier

        with tempfile.TemporaryDirectory() as tmpdir:
            verifier = VoiceProfileVerifier(profile_path=str(Path(tmpdir) / "voice.npy"))
            # Create a unit-norm reference embedding
            ref = np.zeros(512, dtype=np.float32)
            ref[0] = 1.0
            verifier.enrolled_embedding = ref
            verifier._is_initialized = True
            verifier.extractor = MagicMock()

            # Candidate embedding with raw cosine similarity = 0.39 (just below 0.40 standard threshold)
            cand = np.zeros(512, dtype=np.float32)
            cand[0] = 0.39
            cand[1] = float(np.sqrt(1.0 - 0.39**2))

            # Generate a 0.75-second WAV (typical 2-word "Hey Aether" duration)
            sr = 16000
            t = np.linspace(0, 0.75, int(sr * 0.75), endpoint=False)
            samples = (0.25 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(samples.tobytes())
            short_wav = buf.getvalue()

            with patch.object(verifier, "_compute_stream_embedding", return_value=cand):
                is_match, score = verifier.verify(short_wav, threshold=0.40)
                self.assertTrue(is_match, f"Expected 0.75s 'Hey Aether' with 0.39 raw similarity to pass 0.40 gate, got score={score}")
                self.assertGreaterEqual(score, 0.40)

    def test_self_echo_cancellation_ignores_assistant_speech_but_allows_kill_phrase(self):
        from core.engine import AetherEngine

        engine = AetherEngine(config_getter=lambda: {"api": {"agent_name": "Aether"}})
        assistant_response = "I have checked your calendar and you have a project strategy meeting at three thirty this afternoon."

        # 1. Exact or partial snippets of Aether's own speech picked up by mic must be detected as self-echo
        self.assertTrue(engine._is_self_echo("checked your calendar and you have", assistant_response, "Aether stop", "Aether"))
        self.assertTrue(engine._is_self_echo("project strategy meeting at three", assistant_response, "Aether stop", "Aether"))
        self.assertTrue(engine._is_self_echo("this afternoon", assistant_response, "Aether stop", "Aether"))

        # 2. Kill phrases ("Aether stop", phonetic "Aker stop") and Sleep phrase ("Aether stop listening") must NEVER be flagged as self-echo
        self.assertFalse(engine._is_self_echo("Aether stop", assistant_response, "Aether stop", "Aether"))
        self.assertFalse(engine._is_self_echo("Aker stop", assistant_response, "Aether stop", "Aether"))
        self.assertTrue(engine._is_kill_command("Aker stop", "Aether stop", "Aether"))
        self.assertFalse(engine._is_self_echo("Aether stop listening", assistant_response, "Aether stop", "Aether"))

        # 3. Genuine user interruption question must NOT be flagged as self-echo
        self.assertFalse(engine._is_self_echo("What is the weather in Tokyo tomorrow?", assistant_response, "Aether stop", "Aether"))

    def test_aether_start_listening_toggles_standby_to_listening(self):
        events = []
        detector = WakeWordDetector(
            wake_phrase="Hey Aether",
            sleep_phrase="Aether stop listening",
            listening_mode="wake_sleep_toggle",
            on_wake_callback=lambda: events.append("listening"),
            on_sleep_callback=lambda: events.append("standby"),
        )
        self.assertEqual(detector.state, AudioGateState.IDLE_LISTENING)

        # Crucial: "Aether, start listening" must NOT match sleep_phrase ("Aether stop listening")
        self.assertFalse(detector.matches_sleep_phrase("Aether, start listening"))
        self.assertFalse(detector.matches_sleep_phrase("Aker, start listening"))

        # "Aether, start listening" must match wake_phrase and strip "start listening" from remainder
        matched, rem = detector.matches_wake_phrase("Aether, start listening")
        self.assertTrue(matched)
        self.assertEqual(rem, "")

        # Transition from Standby -> Listening
        self.assertTrue(detector.check_transcript("Aether, start listening"))
        self.assertEqual(detector.state, AudioGateState.ACTIVE_CONVERSATION)
        self.assertIn("listening", events)

        # Toggle back from Listening -> Standby with "Aether stop listening"
        self.assertTrue(detector.matches_sleep_phrase("Aether stop listening"))
        self.assertTrue(detector.check_sleep_transcript("Aether stop listening"))
        self.assertEqual(detector.state, AudioGateState.IDLE_LISTENING)
        self.assertEqual(events[-1], "standby")


if __name__ == '__main__':
    unittest.main()
