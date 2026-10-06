"""
Aether Desktop - Push-to-Talk (PTT) End-to-End Pipeline Unit Tests
Verifies hotkey detection, foreground window typing safeguard,
AudioPipeline PTT speech capture, VAD gate bypass, and queue emission.
"""

import asyncio
import io
import os
import sys
import unittest
import wave
from unittest.mock import MagicMock, patch

import numpy as np

from core.audio_stream import AudioPipeline
from core.hotkey_manager import HotkeyManager, KBDLLHOOKSTRUCT, WM_KEYDOWN, WM_KEYUP


class TestPushToTalkPipeline(unittest.TestCase):
    """Verifies Push-to-Talk activation, hotkey mechanics, and audio finalization."""

    def setUp(self):
        self.config = {
            "audio": {
                "mode": "ptt",
                "ptt_type": "hold",
                "ptt_key": "Space",
                "ptt_key_display": "Space",
                "ptt_vk": 32,
                "ptt_modifiers": [],
                "vad_trailing_silence_ms": 1400,
            },
            "ui": {
                "hud_mode_hotkey": "Ctrl+Space",
                "hud_mode_vk": 32,
                "hud_mode_modifiers": ["Control"],
                "game_mode_hotkey": "Ctrl+Shift+G",
                "game_mode_vk": 0x47,
                "game_mode_modifiers": ["Control", "Shift"],
            },
        }

    def test_hotkey_manager_ptt_hold_trigger(self):
        """Tests that holding and releasing target VK triggers on_ptt_change(True/False)."""
        ptt_events = []

        def on_ptt_change(active: bool):
            ptt_events.append(active)

        mgr = HotkeyManager(
            config_getter=lambda: self.config,
            on_ptt_change=on_ptt_change,
        )
        self.assertTrue(mgr.enabled)
        self.assertEqual(mgr.target_vk, 32)
        self.assertEqual(mgr.ptt_type, "hold")

        # Mock ctypes struct for Space key down
        lParam = id(mgr)  # dummy pointer address
        mock_kb = MagicMock()
        mock_kb.vkCode = 32

        with patch.object(KBDLLHOOKSTRUCT, "from_address", return_value=mock_kb):
            with patch("ctypes.windll.user32.CallNextHookEx", return_value=0):
                # Simulate Space KeyDown
                mgr._running = True
                mgr._low_level_keyboard_proc(0, WM_KEYDOWN, lParam)
                self.assertEqual(ptt_events, [True])

                # Simulate autorepeat KeyDown (should not emit duplicate True)
                mgr._low_level_keyboard_proc(0, WM_KEYDOWN, lParam)
                self.assertEqual(ptt_events, [True])

                # Simulate Space KeyUp
                mgr._low_level_keyboard_proc(0, WM_KEYUP, lParam)
                self.assertEqual(ptt_events, [True, False])

    def test_hotkey_manager_typing_safeguard_in_aether_foreground(self):
        """Tests that typing safeguard only suppresses when Aether Desktop is the foreground window."""
        ptt_events = []

        def on_ptt_change(active: bool):
            ptt_events.append(active)

        mgr = HotkeyManager(
            config_getter=lambda: self.config,
            on_ptt_change=on_ptt_change,
        )
        mgr._running = True
        mgr._input_focused = True

        lParam = id(mgr)
        mock_kb = MagicMock()
        mock_kb.vkCode = 32

        with patch.object(KBDLLHOOKSTRUCT, "from_address", return_value=mock_kb):
            with patch("ctypes.windll.user32.CallNextHookEx", return_value=0):
                # Case A: Aether IS the active foreground window -> typing in text box suppresses Space PTT
                with patch.object(mgr, "_is_aether_foreground", return_value=True):
                    mgr._low_level_keyboard_proc(0, WM_KEYDOWN, lParam)
                    self.assertEqual(ptt_events, [])  # Suppressed!

                # Case B: User switched to Game / external window -> typing safeguard NEVER suppresses PTT!
                with patch.object(mgr, "_is_aether_foreground", return_value=False):
                    mgr._low_level_keyboard_proc(0, WM_KEYDOWN, lParam)
                    self.assertEqual(ptt_events, [True])  # Triggered!
                    self.assertFalse(mgr._input_focused)  # Auto-cleared stale focus

                    mgr._low_level_keyboard_proc(0, WM_KEYUP, lParam)
                    self.assertEqual(ptt_events, [True, False])

    def test_audio_pipeline_ptt_captures_and_bypasses_vad_ambient_gate(self):
        """
        Tests that AudioPipeline captures speech while PTT is held, and bypasses
        the automated ambient noise / SNR cutoff when finalising the utterance.
        """
        loop = asyncio.new_event_loop()
        try:
            pipeline = AudioPipeline(
                mode="ptt",
                always_on_mode="wake_word",
                wake_word_enabled=False,
                loop=loop,
            )
            pipeline.target_input_rate = 16000
            pipeline.hw_in_rate = 16000
            pipeline._running = True
            self.assertEqual(pipeline.mode, "ptt")

            # 1. Start PTT
            pipeline.set_ptt(True)
            self.assertTrue(pipeline.ptt_active)
            self.assertTrue(pipeline._is_in_speech)
            self.assertTrue(pipeline._was_ptt_utterance)

            # 2. Feed simulated speech frames: moderate/soft RMS = 0.014
            # (In always-on mode, rms < 0.016 would be discarded by VAD gate!)
            t = np.linspace(0, 0.05, 800, endpoint=False)
            sine_wave = (0.02 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

            for _ in range(8):  # 8 frames * 50ms = 400ms (> 150ms min duration)
                pipeline._input_callback(sine_wave, len(sine_wave), None, None)

            self.assertGreater(len(pipeline._speech_frames), 0)
            self.assertGreater(pipeline._utterance_peak_rms, 0.01)

            # 3. Release PTT
            pipeline.set_ptt(False)
            self.assertFalse(pipeline.ptt_active)

            # 4. Check utterance queue: should have received finalized WAV bytes!
            self.assertFalse(pipeline.utterance_queue.empty())
            wav_bytes = pipeline.utterance_queue.get_nowait()
            self.assertGreater(len(wav_bytes), 1000)

            # Verify it's a valid WAV format at 16kHz mono
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                self.assertEqual(wf.getnchannels(), 1)
                self.assertEqual(wf.getsampwidth(), 2)
                self.assertEqual(wf.getframerate(), 16000)
                n_frames = wf.getnframes()
                self.assertGreater(n_frames, 2000)
        finally:
            loop.close()

    def test_audio_pipeline_ptt_short_utterance_threshold(self):
        """Verifies PTT allows crisp short utterances down to ~150ms (~4800 bytes)."""
        loop = asyncio.new_event_loop()
        try:
            pipeline = AudioPipeline(
                mode="ptt",
                wake_word_enabled=False,
                loop=loop,
            )
            pipeline.target_input_rate = 16000
            pipeline.hw_in_rate = 16000
            pipeline._running = True

            pipeline.set_ptt(True)
            # Feed 4 frames = 200ms = 6400 bytes (> 4800 bytes PTT threshold, but < 11200 always-on threshold)
            t = np.linspace(0, 0.05, 800, endpoint=False)
            sine_wave = (0.025 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

            for _ in range(4):
                pipeline._input_callback(sine_wave, len(sine_wave), None, None)

            pipeline.set_ptt(False)

            # PTT should succeed!
            self.assertFalse(pipeline.utterance_queue.empty())
            wav_bytes = pipeline.utterance_queue.get_nowait()
            self.assertGreater(len(wav_bytes), 4800)
        finally:
            loop.close()

    def test_audio_pipeline_ptt_silent_frame_discard(self):
        """Verifies completely dead / silent microphone frames are discarded with warning."""
        loop = asyncio.new_event_loop()
        try:
            pipeline = AudioPipeline(
                mode="ptt",
                wake_word_enabled=False,
                loop=loop,
            )
            pipeline.target_input_rate = 16000
            pipeline.hw_in_rate = 16000
            pipeline._running = True

            pipeline.set_ptt(True)
            # Feed 5 frames of pure 0.0 silence
            silent_wave = np.zeros(800, dtype=np.float32)
            for _ in range(5):
                pipeline._input_callback(silent_wave, len(silent_wave), None, None)

            pipeline.set_ptt(False)

            # Should be discarded because peak RMS is 0.0 < 0.0008
            self.assertTrue(pipeline.utterance_queue.empty())
        finally:
            loop.close()

    def test_audio_callback_ptt_bypasses_wake_word_gating(self):
        """Verifies audio_callback bypasses WakeWordDetector gating in PTT mode or when PTT is held."""
        loop = asyncio.new_event_loop()
        try:
            pipeline = AudioPipeline(
                mode="ptt",
                wake_word_enabled=True,
                loop=loop,
            )
            dummy_pcm = b"\x00\x00" * 800

            # 1. PTT mode: callback should push directly to vad_queue even with wake_word_enabled=True
            self.assertEqual(pipeline.vad_queue.qsize(), 0)
            pipeline.audio_callback(dummy_pcm, 800, None, 0)
            self.assertEqual(pipeline.vad_queue.qsize(), 1)

            # 2. In always_on mode with wake detector in IDLE_LISTENING: suppressed
            pipeline.mode = "always_on"
            pipeline.ptt_active = False
            pipeline.audio_callback(dummy_pcm, 800, None, 0)
            # Queue size should still be 1 (second frame dropped by wake gate)
            self.assertEqual(pipeline.vad_queue.qsize(), 1)

            # 3. Holding PTT button in always_on mode: immediately bypasses wake gate!
            pipeline.set_ptt(True)
            pipeline.audio_callback(dummy_pcm, 800, None, 0)
            self.assertEqual(pipeline.vad_queue.qsize(), 2)
        finally:
            loop.close()

    def test_set_ptt_auto_starts_engine_when_idle(self):
        """Verifies calling set_ptt(True) on GUIBridge auto-starts assistant if stopped."""
        from core.gui_bridge import GUIBridge
        loop = asyncio.new_event_loop()
        try:
            bridge = GUIBridge(loop=loop)
            bridge._engine.is_running = False
            bridge.start_assistant = MagicMock(return_value={"success": True})

            res = bridge.set_ptt(True)
            self.assertTrue(res["success"])
            bridge.start_assistant.assert_called_once()
        finally:
            loop.close()

    def test_save_config_does_not_resize_hud(self):
        """Verifies save_config does NOT invoke set_mode to prevent HUD overlay resizing on settings change."""
        from core.gui_bridge import GUIBridge
        loop = asyncio.new_event_loop()
        try:
            bridge = GUIBridge(loop=loop)
            bridge.set_mode = MagicMock()

            # Save config with hud_mode inside ui dict
            res = bridge.save_config({"ui": {"hud_mode": "mini"}, "audio": {"mode": "ptt"}})
            self.assertTrue(res["success"])
            # set_mode should NOT be called!
            bridge.set_mode.assert_not_called()
        finally:
            loop.close()

    def test_preroll_captures_early_speech(self):
        """Verifies that speech uttered before PTT is pressed is captured via preroll frames."""
        loop = asyncio.new_event_loop()
        try:
            pipeline = AudioPipeline(
                mode="ptt",
                wake_word_enabled=False,
                loop=loop,
            )
            pipeline.target_input_rate = 16000
            pipeline.hw_in_rate = 16000
            pipeline._running = True

            frame_len = 800
            t = np.linspace(0, 0.05, frame_len, endpoint=False)
            speech_frame = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

            # Speak 6 frames BEFORE pressing PTT (human started speaking ~300ms before PTT press)
            for _ in range(6):
                pipeline._input_callback(speech_frame, frame_len, None, None)

            # Pre-roll should have accumulated all 6 frames
            self.assertEqual(len(pipeline._preroll_frames), 6)

            # Now user presses PTT
            pipeline.set_ptt(True)

            # Speak 4 more frames while holding PTT
            for _ in range(4):
                pipeline._input_callback(speech_frame, frame_len, None, None)

            # User releases PTT
            pipeline.set_ptt(False)

            # Utterance should be in queue containing all 10 frames (6 preroll + 4 held)
            self.assertFalse(pipeline.utterance_queue.empty())
            wav_bytes = pipeline.utterance_queue.get_nowait()
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                n_samples = wf.getnframes()
                self.assertEqual(n_samples, 10 * frame_len)
        finally:
            loop.close()

    def test_settings_persistence_mode_and_endpoints(self):
        """Verifies mode, stt_endpoint, and tts_endpoint persist correctly in GuiBridge."""
        import tempfile
        from core.gui_bridge import GUIBridge

        loop = asyncio.new_event_loop()
        tf = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tf.close()
        try:
            bridge = GUIBridge(config_path=tf.name, loop=loop)
            res = bridge.save_config({
                "mode": "ptt",
                "stt_endpoint": "primary_flash_stt",
                "tts_endpoint": "gemini_live",
                "audio": {
                    "mode": "ptt"
                }
            })
            self.assertTrue(res["success"])

            saved_cfg = bridge.get_config()
            self.assertEqual(saved_cfg.get("mode"), "ptt")
            self.assertEqual(saved_cfg.get("audio", {}).get("mode"), "ptt")
            self.assertEqual(saved_cfg.get("stt_endpoint"), "primary_flash_stt")
            self.assertEqual(saved_cfg.get("tts_endpoint"), "gemini_live")

            # Load clean from disk to ensure persistence across sessions
            bridge2 = GUIBridge(config_path=tf.name, loop=loop)
            loaded_cfg = bridge2.get_config()
            self.assertEqual(loaded_cfg.get("mode"), "ptt")
            self.assertEqual(loaded_cfg.get("audio", {}).get("mode"), "ptt")
            self.assertEqual(loaded_cfg.get("stt_endpoint"), "primary_flash_stt")
            self.assertEqual(loaded_cfg.get("tts_endpoint"), "gemini_live")
        finally:
            loop.close()
            if os.path.exists(tf.name):
                os.remove(tf.name)


if __name__ == "__main__":
    unittest.main()
