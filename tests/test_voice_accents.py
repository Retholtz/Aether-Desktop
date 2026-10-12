"""
Unit tests for Spoken Voice Accent steering and dynamic switching across all accents.
"""

import unittest
from unittest.mock import MagicMock, patch

from core.engine import (
    Engine,
    build_accent_directive,
    build_accent_reminder,
    strip_accent_directive,
    apply_accent_directive,
    ACCENT_DIRECTIVES,
    ACCENT_START_MARKER,
    ACCENT_END_MARKER,
    ACCENT_REMINDER_START_MARKER,
    ACCENT_REMINDER_END_MARKER,
)
from core.connection_manager import ConnectionState
from core.gui_bridge import GuiBridge


class TestVoiceAccents(unittest.TestCase):
    def setUp(self):
        self.base_instruction = "You are Aether, a fast, knowledgeable desktop voice assistant."

    def test_all_13_accents_have_tailored_directives(self):
        expected_accents = [
            "cockney",
            "british",
            "scottish",
            "irish",
            "australian",
            "american southern",
            "american new york",
            "french",
            "german",
            "italian",
            "spanish",
            "indian",
            "japanese",
        ]
        for acc in expected_accents:
            directive = build_accent_directive(acc)
            self.assertTrue(directive, f"Directive for '{acc}' should not be empty")
            self.assertIn(ACCENT_START_MARKER, directive)
            self.assertIn(ACCENT_END_MARKER, directive)
            self.assertIn("SPOKEN VOICE ACCENT & DIALECT DIRECTIVE:", directive)
            self.assertIn("MANDATORY VOCAL OVERRIDE:", directive)
            self.assertIn("Regardless of the default acoustic profile or native accent", directive)
            self.assertIn("PHONOLOGY & ARTICULATION:", directive)
            self.assertIn("RHYTHM & CADENCE:", directive)

    def test_indian_accent_has_concrete_phonological_directives(self):
        directive = build_accent_directive("Indian")
        self.assertIn("Indian accent (Indian English)", directive)
        self.assertIn("retroflex consonants", directive)
        self.assertIn("syllable-timed rhythm", directive)
        self.assertIn("pure monophthong vowels", directive)
        self.assertIn("definitely", directive)

    def test_dual_anchor_reminder_generation_and_placement(self):
        reminder = build_accent_reminder("Indian")
        self.assertIn(ACCENT_REMINDER_START_MARKER, reminder)
        self.assertIn(ACCENT_REMINDER_END_MARKER, reminder)
        self.assertIn("CRITICAL SPOKEN VOICE REMINDER:", reminder)
        self.assertIn("Indian", reminder)

        full = apply_accent_directive(self.base_instruction, "Indian")
        self.assertTrue(full.startswith(ACCENT_START_MARKER))
        self.assertTrue(full.endswith(ACCENT_REMINDER_END_MARKER))
        self.assertIn(self.base_instruction, full)

        stripped = strip_accent_directive(full)
        self.assertEqual(stripped, self.base_instruction)

    def test_cockney_accent_directive_does_not_suppress_dialect(self):
        directive = build_accent_directive("Cockney")
        self.assertIn("Cockney (East London)", directive)
        # Verify it does NOT contain the contradictory phrase that caused the model to revert to standard British
        self.assertNotIn("Maintain standard English vocabulary and phrasing", directive)
        self.assertIn("intelligible", directive)

    def test_default_accent_returns_empty_directive(self):
        for def_val in ("default", "none", "neutral", "", "Default (Native / Standard)", None):
            directive = build_accent_directive(def_val)
            self.assertEqual(directive, "", f"Directive for '{def_val}' should be empty")
            reminder = build_accent_reminder(def_val)
            self.assertEqual(reminder, "", f"Reminder for '{def_val}' should be empty")

    def test_strip_and_apply_accent_directive_cycle(self):
        # 1. Start with clean instruction
        clean = self.base_instruction

        # 2. Apply Cockney
        cockney_inst = apply_accent_directive(clean, "Cockney")
        self.assertIn("Cockney (East London)", cockney_inst)
        self.assertIn(clean, cockney_inst)

        # 3. Switch to British
        british_inst = apply_accent_directive(cockney_inst, "British")
        self.assertIn("British accent", british_inst)
        self.assertNotIn("Cockney", british_inst)
        self.assertIn(clean, british_inst)

        # 4. Switch to Scottish
        scottish_inst = apply_accent_directive(british_inst, "Scottish")
        self.assertIn("Scottish accent", scottish_inst)
        self.assertNotIn("British", scottish_inst)
        self.assertIn(clean, scottish_inst)

        # 5. Switch to Default (should strip all accent directives)
        default_inst = apply_accent_directive(scottish_inst, "default")
        self.assertNotIn("SPOKEN VOICE ACCENT & DIALECT DIRECTIVE:", default_inst)
        self.assertEqual(default_inst, clean)

    def test_strip_legacy_untagged_directive(self):
        legacy = (
            "SPOKEN VOICE ACCENT & DIALECT DIRECTIVE:\n"
            "- You must speak with a natural, distinct British accent in all spoken responses.\n"
            "- Consistently articulate all words with British pronunciation, cadence, and intonation.\n"
            "- Maintain standard English vocabulary and phrasing unless asked otherwise, but always express speech with your British accent.\n\n"
            + self.base_instruction
        )
        stripped = strip_accent_directive(legacy)
        self.assertNotIn("British accent", stripped)
        self.assertNotIn("SPOKEN VOICE ACCENT & DIALECT DIRECTIVE:", stripped)
        self.assertEqual(stripped, self.base_instruction)

    def test_engine_dynamic_accent_switching(self):
        cfg = {
            "api": {
                "voice_accent": "Cockney",
                "voice_name": "Sulafat",
                "system_instruction": "You are Aether."
            }
        }
        engine = Engine(config_getter=lambda: cfg)

        # 1. Initial build has Cockney
        inst = engine._build_system_instruction()
        self.assertIn("Cockney (East London)", inst)

        # 2. Switch config to Indian
        cfg["api"]["voice_accent"] = "Indian"
        inst = engine._build_system_instruction()
        self.assertIn("Indian accent (Indian English)", inst)
        self.assertIn("retroflex consonants", inst)
        self.assertIn(ACCENT_REMINDER_START_MARKER, inst)
        self.assertNotIn("Cockney", inst)

        # 3. Switch config to British
        cfg["api"]["voice_accent"] = "British"
        inst = engine._build_system_instruction()
        self.assertIn("British accent", inst)
        self.assertNotIn("Indian", inst)
        self.assertNotIn("Cockney", inst)

        # 4. Switch config to default
        cfg["api"]["voice_accent"] = "default"
        inst = engine._build_system_instruction()
        self.assertNotIn("SPOKEN VOICE ACCENT & DIALECT DIRECTIVE:", inst)
        self.assertNotIn(ACCENT_REMINDER_START_MARKER, inst)

    def test_engine_system_instruction_empty_string_fallback(self):
        cfg = {
            "api": {
                "voice_accent": "Indian",
                "voice_name": "Puck",
                "system_instruction": ""
            },
            "ui": {
                "agent_name": "Aether"
            }
        }
        engine = Engine(config_getter=lambda: cfg)
        inst = engine._build_system_instruction()
        self.assertTrue(inst.startswith(ACCENT_START_MARKER))
        self.assertTrue(inst.endswith(ACCENT_REMINDER_END_MARKER))
        self.assertIn("You are Aether", inst)
        self.assertIn("Indian accent (Indian English)", inst)

    def test_gui_bridge_triggers_live_session_rotation_on_accent_change(self):
        mock_engine = MagicMock()
        mock_engine.is_running = True
        mock_engine.conn_mgr.state = ConnectionState.CONNECTED

        bridge = GuiBridge(engine=mock_engine)
        bridge._config = {
            "voice_accent": "British",
            "tts_voice": "Sulafat",
            "api": {"voice_accent": "British", "voice_name": "Sulafat"},
            "ui": {},
            "audio": {}
        }

        with patch("core.gui_bridge.save_config_atomic"):
            res = bridge.save_config({"api": {"voice_accent": "Cockney"}})
            self.assertTrue(res["success"])
            mock_engine.update_voice_accent_directive.assert_called_with("Cockney")
            mock_engine.invalidate_live_session.assert_called_once()
            call_reason = mock_engine.invalidate_live_session.call_args[1]["reason"]
            self.assertIn("Cockney", call_reason)

    def test_engine_invalidate_live_session_wipes_resumption_handle(self):
        engine = Engine(config_getter=lambda: {})
        engine._session_resumption_handle = "stale_token_12345"
        engine.is_running = False

        engine.invalidate_live_session(reason="Test Accent Invalidation")
        self.assertIsNone(engine._session_resumption_handle)

    def test_engine_stop_wipes_resumption_handle_and_history(self):
        engine = Engine(config_getter=lambda: {})
        engine._session_resumption_handle = "stale_token_54321"
        engine.is_running = True
        engine.session_lifecycle.turn_history.append({"turn_id": 1, "text": "old turn"})

        with patch.object(engine, "kill_audio"), patch.object(engine.hotkey_manager, "stop"):
            engine.stop()

        self.assertIsNone(engine._session_resumption_handle)
        self.assertEqual(len(engine.session_lifecycle.turn_history), 0)

    def test_update_voice_accent_directive_wipes_resumption_handle(self):
        engine = Engine(config_getter=lambda: {"api": {"voice_accent": "British"}})
        engine._session_resumption_handle = "old_accent_handle"
        engine.update_voice_accent_directive("Cockney")
        self.assertIsNone(engine._session_resumption_handle)


if __name__ == "__main__":
    unittest.main()

