"""
Unit tests for Preferred Language (STT & Spoken Response Lock) across Gemini Live and GuiBridge.
"""

import asyncio
import types as pytypes
import unittest
from unittest.mock import MagicMock, patch

from core.connection_manager import ConnectionState
from core.engine import (
    ACCENT_REMINDER_END_MARKER,
    ACCENT_START_MARKER,
    Engine,
    LANGUAGE_END_MARKER,
    LANGUAGE_START_MARKER,
    apply_language_directive,
    build_language_directive,
    resolve_preferred_language_code,
    strip_language_directive,
)
from core.gui_bridge import GuiBridge


class _FakeConnectCtx:
    def __init__(self, session=None, error=None):
        self._session = session or MagicMock()
        self._error = error

    async def __aenter__(self):
        if self._error is not None:
            raise self._error
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestPreferredLanguageLock(unittest.TestCase):
    def test_resolve_preferred_language_code_defaults_and_reads_audio(self):
        self.assertEqual(resolve_preferred_language_code(None), "en-US")
        self.assertEqual(resolve_preferred_language_code({}), "en-US")
        self.assertEqual(resolve_preferred_language_code({"audio": {"preferred_language": ""}}), "en-US")
        self.assertEqual(resolve_preferred_language_code({"audio": {"preferred_language": "en-GB"}}), "en-GB")
        self.assertEqual(resolve_preferred_language_code({"preferred_language": "fr-FR"}), "fr-FR")

    def test_apply_and_strip_language_directive_avoids_duplication(self):
        base = "You are Aether."
        applied_en = apply_language_directive(base, "en-US")
        self.assertIn(LANGUAGE_START_MARKER, applied_en)
        self.assertIn(LANGUAGE_END_MARKER, applied_en)
        self.assertIn("English (United States) (en-US)", applied_en)
        self.assertIn(base, applied_en)

        applied_fr = apply_language_directive(applied_en, "fr-FR")
        self.assertEqual(applied_fr.count(LANGUAGE_START_MARKER), 1)
        self.assertIn("French (fr-FR)", applied_fr)
        self.assertNotIn("English (United States)", applied_fr)
        self.assertEqual(strip_language_directive(applied_fr), base)

    def test_system_instruction_includes_language_lock_and_preserves_accent_markers(self):
        cfg = {
            "api": {"agent_name": "Aether", "voice_accent": "British", "system_instruction": "Be helpful."},
            "audio": {"preferred_language": "en-US"},
        }
        eng = Engine(config_getter=lambda: cfg)
        inst = eng._build_system_instruction()
        self.assertTrue(inst.startswith(ACCENT_START_MARKER))
        self.assertTrue(inst.endswith(ACCENT_REMINDER_END_MARKER))
        self.assertIn("English (United States) (en-US)", inst)
        self.assertIn("British accent", inst)

        # Also verify Game Mode inherits the preferred language lock
        mock_ge = MagicMock()
        mock_ge.is_game_mode_active.return_value = True
        mock_ge.build_system_instruction.return_value = "GAME MODE INSTRUCTION"
        eng.game_engine = mock_ge
        gm_inst = eng._build_system_instruction()
        self.assertIn("GAME MODE INSTRUCTION", gm_inst)
        self.assertIn("English (United States) (en-US)", gm_inst)

    def test_build_live_config_sets_transcription_language_codes_and_speech_fallback(self):
        cfg = {
            "api": {"voice_accent": "default", "voice_name": "Sulafat"},
            "audio": {"preferred_language": "en-US"},
        }
        eng = Engine(config_getter=lambda: cfg)
        live_cfg = eng._build_live_config()
        self.assertEqual(live_cfg.input_audio_transcription.language_codes, ["en-US"])
        self.assertEqual(live_cfg.output_audio_transcription.language_codes, ["en-US"])
        self.assertEqual(live_cfg.speech_config.language_code, "en-US")

        # Accent-specific language code takes precedence for speech synthesis while STT keeps preferred_language
        cfg["api"]["voice_accent"] = "British"
        live_cfg_uk = eng._build_live_config()
        self.assertEqual(live_cfg_uk.input_audio_transcription.language_codes, ["en-US"])
        self.assertEqual(live_cfg_uk.speech_config.language_code, "en-GB")

        # Fallback builder flag clears language_codes cleanly
        live_cfg_unconstrained = eng._build_live_config(use_transcription_language_codes=False)
        self.assertIsNone(live_cfg_unconstrained.input_audio_transcription.language_codes)
        self.assertIsNone(live_cfg_unconstrained.output_audio_transcription.language_codes)

    def test_open_live_session_falls_back_if_endpoint_rejects_language_codes(self):
        cfg = {
            "api": {"voice_accent": "British", "voice_name": "Sulafat"},
            "audio": {"preferred_language": "en-US"},
        }
        eng = Engine(config_getter=lambda: cfg)
        fake_session = MagicMock()
        seen_configs = []

        def fake_connect(model=None, config=None):
            seen_configs.append(config)
            if len(seen_configs) == 1:
                return _FakeConnectCtx(error=RuntimeError("Invalid argument: unknown field language_codes in AudioTranscriptionConfig"))
            return _FakeConnectCtx(session=fake_session)

        fake_client = pytypes.SimpleNamespace(
            aio=pytypes.SimpleNamespace(live=pytypes.SimpleNamespace(connect=fake_connect))
        )

        cm, session = asyncio.run(
            eng._open_live_session_with_fallback(
                client=fake_client,
                model_id="gemini-3.8-live",
                system_instruction_text="You are Aether.",
                voice_name="Sulafat",
                temperature=0.7,
                use_resumption=True,
            )
        )
        self.assertIs(session, fake_session)
        self.assertEqual(len(seen_configs), 2)
        self.assertEqual(seen_configs[0].input_audio_transcription.language_codes, ["en-US"])
        self.assertIsNone(seen_configs[1].input_audio_transcription.language_codes)

    def test_gui_bridge_invalidates_live_session_on_preferred_language_change(self):
        mock_engine = MagicMock()
        mock_engine.is_running = True
        mock_engine._active_pipeline_mode = "live"
        mock_engine.conn_mgr.state = ConnectionState.CONNECTED

        bridge = GuiBridge(engine=mock_engine)
        bridge._config = {
            "voice_accent": "British",
            "tts_voice": "Sulafat",
            "api": {"voice_accent": "British", "voice_name": "Sulafat"},
            "ui": {},
            "audio": {"preferred_language": "en-US"},
        }

        with patch("core.gui_bridge.save_config_atomic"):
            res = bridge.save_config({"audio": {"preferred_language": "en-GB"}})
            self.assertTrue(res["success"])
            mock_engine.rebuild_base_system_instruction.assert_called_once()
            mock_engine.invalidate_live_session.assert_called_once()
            reason = mock_engine.invalidate_live_session.call_args[1]["reason"]
            self.assertIn("en-US -> en-GB", reason)


if __name__ == "__main__":
    unittest.main()
