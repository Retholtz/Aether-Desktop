"""
Unit tests for Closed-Loop Outcome Verification, Autonomous Self-Correction,
and Document Canvas skills in Aether-Desktop.
"""

import inspect
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from core.session_lifecycle import SessionLifecycleManager
import scripts.library.wait_for_google_docs_canvas as gdocs_canvas


class TestClosedLoopVerification(unittest.TestCase):
    def test_engine_directive_contains_closed_loop_verification(self):
        """Verifies engine.py contains the Closed-Loop Outcome Verification and Self-Correction Protocol."""
        engine_file = os.path.join(os.path.dirname(__file__), "..", "core", "engine.py")
        with open(engine_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Closed-loop directive must be present
        self.assertIn("CLOSED-LOOP OUTCOME VERIFICATION & AUTONOMOUS SELF-CORRECTION", content)
        self.assertIn("The Closed-Loop Execution Rule:", content)
        self.assertIn("Mandatory Visual Outcome Verification:", content)
        self.assertIn("capture_screen_snapshot", content)
        self.assertIn("Autonomous Self-Correction Loop ('Keep Trying Until It Succeeds')", content)
        self.assertIn("find_and_click_element", content)
        self.assertIn("target_description='document canvas'", content)

        # The old snapshot suppression directive must NOT be present
        self.assertNotIn("Avoid calling `capture_screen_snapshot` after routine atomic actions", content)

    def test_session_lifecycle_compactor_model_updated(self):
        """Verifies SessionLifecycleManager uses gemini-3.8-flash instead of deprecated gemini-2.5-flash."""
        lifecycle = SessionLifecycleManager()
        sig = inspect.signature(lifecycle.generate_session_summary)
        model_param = sig.parameters.get("model")
        self.assertIsNotNone(model_param)
        self.assertEqual(model_param.default, "gemini-3.8-flash")

        lifecycle_file = os.path.join(os.path.dirname(__file__), "..", "core", "session_lifecycle.py")
        with open(lifecycle_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("gemini-2.5-flash", content)

    def test_skills_catalog_wait_for_google_docs_canvas(self):
        """Verifies skills_catalog.json has updated description and parameters for wait_for_google_docs_canvas."""
        catalog_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "library", "skills_catalog.json")
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)

        self.assertIn("wait_for_google_docs_canvas", catalog)
        entry = catalog["wait_for_google_docs_canvas"]
        self.assertIn("canvas", entry["description"].lower())
        self.assertIn("focus", entry["description"].lower())
        self.assertIn("text", entry.get("parameters", {}))

    def test_google_docs_canvas_clipboard_operations(self):
        """Tests that wait_for_google_docs_canvas clipboard helpers correctly set and retrieve text."""
        test_payload = "Test Document Body Content for Stanley Orlop Verification"
        ok = gdocs_canvas.set_clipboard_text(test_payload)
        self.assertTrue(ok)
        retrieved = gdocs_canvas.get_clipboard_text()
        self.assertEqual(retrieved, test_payload)

    @patch("win32gui.GetWindowRect")
    @patch("win32gui.GetForegroundWindow")
    @patch("win32gui.IsWindowVisible")
    @patch("win32gui.GetWindowText")
    @patch("win32gui.EnumWindows")
    @patch("scripts.library.wait_for_google_docs_canvas.bring_hwnd_to_foreground")
    @patch("scripts.library.wait_for_google_docs_canvas.click_canvas")
    @patch("scripts.library.wait_for_google_docs_canvas.send_paste_input")
    def test_focus_and_paste_google_docs_workflow(
        self,
        mock_paste,
        mock_click,
        mock_bring,
        mock_enum,
        mock_text,
        mock_visible,
        mock_fg,
        mock_rect,
    ):
        """Tests the end-to-end focus, center canvas click, and paste pipeline."""
        mock_hwnd = 12345
        mock_fg.return_value = mock_hwnd
        mock_rect.return_value = (100, 50, 1100, 850)  # width 1000, height 800

        # Simulate finding the Docs window
        def fake_enum(cb, extra):
            mock_visible.return_value = True
            mock_text.return_value = "Untitled document - Google Docs - Google Chrome"
            cb(mock_hwnd, None)
        mock_enum.side_effect = fake_enum

        result = gdocs_canvas.focus_and_paste_google_docs(text="Formal Resignation Letter")
        self.assertTrue(result)
        mock_bring.assert_called_once_with(mock_hwnd)
        # Expected canvas click coords:
        # width = 1000, 50% = 500 => x = 100 + 500 = 600
        # height = 800, 35% = 280 => y = 50 + 280 = 330
        mock_click.assert_called_once_with(600, 330)
        mock_paste.assert_called_once()


if __name__ == "__main__":
    unittest.main()
