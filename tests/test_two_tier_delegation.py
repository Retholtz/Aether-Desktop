"""
Unit tests for Tier 2 Delegation (Gemini 4 Argon Integration).
Validates on-demand task delegation for research dossiers, Python automation,
deep analysis, and ReflexionEngine configuration for thinking_level.
"""

import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from tools.dispatcher import ToolDispatcher, DELEGATE_HEAVY_TASK_DECLARATION
from core.game_engine import GameEngine
from core.optimizer import ReflexionEngine
from core.config_manager import sync_config_schema


class TestTwoTierDelegation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.dossier_dir = os.path.join(self.test_dir, "dossiers")
        os.makedirs(self.dossier_dir, exist_ok=True)

        self.mock_config = {
            "primary_model_endpoint": "gemini-3.8-flash",
            "tier2_heavy_model": "gemini-4-argon",
            "tier2_thinking_level": "high",
            "tier2_thinking_budget": 2048,
            "api": {
                "model_id": "gemini-3.8-flash",
                "pro_model_id": "gemini-4-argon",
                "tier2_thinking_level": "high"
            }
        }
        self.dispatcher = ToolDispatcher(
            config_getter=lambda: self.mock_config
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_declarations_include_delegate_heavy_task(self):
        """Verifies delegate_heavy_task is present in core declarations and game tools."""
        core_decls = self.dispatcher.get_core_declarations()
        names = [d.get("name") for d in core_decls]
        self.assertIn("delegate_heavy_task", names)
        self.assertIn("export_research_dossier", names)

        # GameEngine core tools
        self.assertIn("delegate_heavy_task", GameEngine.CORE_GAME_TOOL_NAMES)
        self.assertIn("export_research_dossier", GameEngine.CORE_GAME_TOOL_NAMES)

        # ToolDispatcher all declarations (sent to Gemini Live)
        all_decls = self.dispatcher.get_all_tool_declarations()
        all_names = [d.get("name") for d in all_decls]
        self.assertIn("delegate_heavy_task", all_names)
        self.assertIn("export_research_dossier", all_names)

    async def test_delegate_research_dossier_to_argon(self):
        """Verifies research dossier delegation passes thinking_level='high' to gemini-4-argon."""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "# Comprehensive Research Dossier\n\n| Item | Value |\n|---|---|\n| Test | Passed |\n"
        mock_client.models.generate_content.return_value = mock_resp
        self.dispatcher.genai_client = mock_client

        with patch("tools.dispatcher.export_research_dossier") as mock_export:
            mock_export.return_value = {
                "status": "success",
                "file_path": "/fake/path/dossier.md",
                "filename": "dossier.md",
                "char_count": len(mock_resp.text)
            }

            result = await self.dispatcher.dispatch("delegate_heavy_task", {
                "task_type": "research_dossier",
                "title": "Quantum_Computing_Analysis",
                "instructions": "Synthesize latest error-correction benchmarks.",
                "context_data": "Google Willow processor data."
            })

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["task_type"], "research_dossier")
            self.assertEqual(result["file_path"], "/fake/path/dossier.md")

            # Check client call arguments
            call_kwargs = mock_client.models.generate_content.call_args[1]
            self.assertEqual(call_kwargs["model"], "gemini-4-argon")
            cfg = call_kwargs["config"]
            self.assertIn(str(cfg.thinking_config.thinking_level).lower(), ("high", "thinkinglevel.high", "'high'"))
            self.assertEqual(cfg.max_output_tokens, 65536)

            # Check export_research_dossier call
            mock_export.assert_called_once()
            export_kwargs = mock_export.call_args[1]
            self.assertEqual(export_kwargs["title"], "Quantum_Computing_Analysis")
            self.assertIn("| Item | Value |", export_kwargs["content_markdown"])

    async def test_delegate_heavy_task_pastes_when_requested(self):
        """Verifies delegate_heavy_task automatically invokes paste_markdown_to_target_app when paste_to_active_doc=True."""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "# Perfect Sourdough Guide\n\n| Day | Feeding |\n|---|---|\n| Day 1 | 50g flour + 50g water |\n"
        mock_client.models.generate_content.return_value = mock_resp
        self.dispatcher.genai_client = mock_client

        with patch("tools.dispatcher.paste_markdown_to_target_app") as mock_paste, \
             patch("tools.dispatcher.export_research_dossier") as mock_export:
            mock_export.return_value = {"status": "success", "file_path": "/fake/sourdough.md"}
            mock_paste.return_value = {"status": "success", "message": "Pasted"}

            result = await self.dispatcher.dispatch("delegate_heavy_task", {
                "task_type": "research_dossier",
                "title": "Sourdough_Guide",
                "instructions": "Create a detailed sourdough guide and paste into Google Docs.",
                "paste_to_active_doc": True,
                "target_app": "chrome"
            })

            self.assertEqual(result["status"], "success")
            self.assertTrue(result.get("pasted_to_doc"))
            mock_paste.assert_called_once()
            paste_args = mock_paste.call_args[0]
            self.assertIn("# Perfect Sourdough Guide", paste_args[0])

    async def test_delegate_python_automation_to_argon(self):
        """Verifies python automation task delegation generates and executes Python code."""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "```python\nimport sys\nprint('Argon script executed')\n```"
        mock_client.models.generate_content.return_value = mock_resp
        self.dispatcher.genai_client = mock_client

        with patch.object(self.dispatcher.script_runner, "execute_script") as mock_exec:
            mock_exec.return_value = {"status": "success", "stdout": "Argon script executed\n"}

            result = await self.dispatcher.dispatch("delegate_heavy_task", {
                "task_type": "python_automation",
                "title": "Clean Temp Files",
                "instructions": "Delete files older than 7 days in temp.",
                "execute_if_code": True
            })

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["task_type"], "python_automation")
            self.assertIn("import sys", result["generated_code"])
            mock_exec.assert_called_once()

    async def test_delegate_deep_analysis_to_argon(self):
        """Verifies deep analysis delegation returns structured analysis."""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "Deep analytical findings with 3-step proof."
        mock_client.models.generate_content.return_value = mock_resp
        self.dispatcher.genai_client = mock_client

        result = await self.dispatcher.dispatch("delegate_heavy_task", {
            "task_type": "deep_analysis",
            "title": "Cryptographic Hash Comparison",
            "instructions": "Evaluate collision resistance."
        })

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["task_type"], "deep_analysis")
        self.assertEqual(result["analysis"], "Deep analytical findings with 3-step proof.")


    async def test_export_dossier_shallow_stub_auto_enriches_via_argon(self):
        """Verifies that a shallow dossier stub passed to export_research_dossier auto-delegates to Argon."""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "# The Ultimate Sourdough Starter & Bread Guide (Argon High Reasoning)\n\n## Deep Science of Fermentation\nExhaustive details..."
        mock_client.models.generate_content.return_value = mock_resp
        self.dispatcher.genai_client = mock_client

        with patch("tools.dispatcher.export_research_dossier") as mock_export:
            mock_export.return_value = {
                "status": "success",
                "file_path": "/fake/path/sourdough.md",
                "filename": "sourdough.md",
                "char_count": len(mock_resp.text)
            }

            # Simulate Flash calling export_research_dossier with a 200-char stub
            result = await self.dispatcher.dispatch("export_research_dossier", {
                "title": "Sourdough_Guide_From_Scratch",
                "content_markdown": "# Sourdough Guide\nMix flour and water for 7 days.",
                "category": "general"
            })

            self.assertEqual(result["status"], "success")
            # Verified client was called because shallow stub was auto-enriched via Argon
            mock_client.models.generate_content.assert_called_once()
            call_kwargs = mock_client.models.generate_content.call_args[1]
            self.assertEqual(call_kwargs["model"], "gemini-4-argon")


class TestReflexionArgonConfig(unittest.TestCase):
    def test_reflexion_engine_argon_thinking_config(self):
        """Verifies ReflexionEngine constructs thinking_level='high' for gemini-4-argon."""
        mock_engine = MagicMock()
        mock_engine.config = {
            "tier2_heavy_model": "gemini-4-argon",
            "tier2_thinking_level": "high",
            "tier2_thinking_budget": 2048,
        }
        engine = ReflexionEngine(engine=mock_engine)
        self.assertEqual(engine.heavy_model, "gemini-4-argon")
        self.assertEqual(engine.thinking_level, "high")

        config = engine._build_generate_config(response_mime_type="application/json")
        self.assertIn(str(config.thinking_config.thinking_level).lower(), ("high", "thinkinglevel.high", "'high'"))
        self.assertEqual(config.max_output_tokens, 65536)
        self.assertEqual(config.response_mime_type, "application/json")

    def test_schema_sync_defaults_to_argon(self):
        """Verifies sync_config_schema defaults heavy model to gemini-4-argon."""
        cfg = sync_config_schema({})
        self.assertEqual(cfg["primary_model_endpoint"], "gemini-3.8-flash")
        self.assertEqual(cfg["tier2_heavy_model"], "gemini-4-argon")
        self.assertEqual(cfg["tier2_thinking_level"], "high")
        self.assertEqual(cfg["api"]["pro_model_id"], "gemini-4-argon")


class TestProgramManagerWhitelist(unittest.TestCase):
    def test_program_manager_get_whitelist_names(self):
        """Verifies ProgramManager provides get_whitelist_names without AttributeError."""
        from core.program_manager import ProgramManager
        pm = ProgramManager()
        names = pm.get_whitelist_names()
        self.assertIsInstance(names, list)
        self.assertTrue(len(names) > 0)
        # Should include standard executables like chrome or notepad
        has_common = any("chrome" in n.lower() or "notepad" in n.lower() for n in names)
        self.assertTrue(has_common, "Expected common apps in program whitelist names")


class TestClipboardUtilities(unittest.TestCase):
    def test_clipboard_module_exports(self):
        """Verifies tools.clipboard and root clipboard provide necessary HTML/text functions."""
        import tools.clipboard as tc
        import clipboard as rc
        for mod in (tc, rc):
            self.assertTrue(callable(getattr(mod, "set_clipboard_html", None)))
            self.assertTrue(callable(getattr(mod, "set_clipboard_text", None)))
            self.assertTrue(callable(getattr(mod, "get_clipboard_text", None)))
            self.assertTrue(callable(getattr(mod, "format_windows_html_clipboard", None)))

        payload = tc.format_windows_html_clipboard("<p>Hello Aether</p>")
        self.assertIn(b"StartHTML:", payload)
        self.assertIn(b"<!--StartFragment--><p>Hello Aether</p><!--EndFragment-->", payload)

    def test_markdown_to_html_formatting(self):
        """Verifies markdown tables, headers, and lists are formatted into styled HTML with unescaped linebreaks."""
        from tools.clipboard import markdown_to_html_for_clipboard
        md = "# Heading 1\n## Heading 2\n- Item 1\n- Item 2\n\n| Day | Task |\n|---|---|\n| Day 1 | Feed 1.<br>Feed 2. |\n"
        html = markdown_to_html_for_clipboard(md)
        self.assertIn("<h1", html)
        self.assertIn("<h2", html)
        self.assertIn("<table", html)
        self.assertIn("<th", html)
        self.assertIn("white-space: nowrap", html)
        self.assertIn("<br/>", html)
        self.assertIn("Day 1", html)
        self.assertIn("<ul", html)

    def test_clean_markdown_typography(self):
        """Verifies LaTeX math, temperatures, operators, and chemical symbols are converted to clean Unicode."""
        from tools.clipboard import clean_markdown_typography
        raw = "Winter ($<19^\\circ\\text{C}$ / $66^\\circ\\text{F}$) with $CO_2$ and $H_2O$ and $\\pm 0.5^\\circ\\text{C}$"
        cleaned = clean_markdown_typography(raw)
        self.assertIn("<19°C", cleaned)
        self.assertIn("66°F", cleaned)
        self.assertIn("CO₂", cleaned)
        self.assertIn("H₂O", cleaned)
        self.assertIn("±0.5°C", cleaned)
        self.assertNotIn("\\circ", cleaned)
        self.assertNotIn("\\text", cleaned)


class TestCompatibilityModules(unittest.TestCase):
    def test_heavy_reasoning_compatibility(self):
        """Verifies tools.heavy_reasoning exposes delegate_heavy_task."""
        from tools.heavy_reasoning import delegate_heavy_task
        self.assertTrue(callable(delegate_heavy_task))

    def test_os_controls_compatibility_aliases(self):
        """Verifies tools.os_controls exposes send_hotkey and type_text_to_foreground."""
        from tools.os_controls import send_hotkey, type_text_to_foreground
        self.assertTrue(callable(send_hotkey))
        self.assertTrue(callable(type_text_to_foreground))


class TestWakeWordWorkingChime(unittest.TestCase):
    def test_working_chime_exists_and_plays(self):
        """Verifies working chime sound file exists and play_chime_async handles 'working'."""
        import os
        from core.wake_word import play_chime_async, SOUNDS_DIR
        working_wav = os.path.join(SOUNDS_DIR, "working.wav")
        self.assertTrue(os.path.exists(working_wav), "working.wav must exist")
        # Ensure play_chime_async('working') does not raise
        play_chime_async("working")


if __name__ == "__main__":
    unittest.main()
