"""
Unit tests for:
1. App-Agnostic Tier 2 Hydrogen Task Orchestrator (core/orchestrator.py)
2. Argon -> Hydrogen -> Any-App file_path handoff & loud clipboard target verification
3. Explicit Tier Override & Rerun Command Routing (core/engine.py)
4. Quoted-dialogue accent continuity directives
"""

import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.engine import (
    AetherEngine,
    DOSSIER_GENERATION_DIRECTIVE,
    build_accent_directive,
    build_accent_reminder,
    build_live_accent_anchor_turns,
    resolve_accent_language_code,
)
from core.game_engine import GameEngine
from core.orchestrator import ORCHESTRATOR_ALLOWED_TOOL_NAMES, TaskOrchestrator
from tools.dispatcher import (
    DELEGATE_HEAVY_TASK_DECLARATION,
    DELEGATE_MODERATE_TASK_DECLARATION,
    ToolDispatcher,
)


def _make_fc(name: str, args: dict):
    return SimpleNamespace(name=name, args=args)


def _make_model_response(text: str = "", function_calls=None):
    parts = []
    if text:
        parts.append(SimpleNamespace(text=text, thought=False))
    content = SimpleNamespace(role="model", parts=parts)
    candidate = SimpleNamespace(content=content, finish_reason="STOP")
    return SimpleNamespace(
        candidates=[candidate],
        text=text,
        function_calls=function_calls or [],
    )


class TestAgnosticOrchestrator(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.mock_config = {
            "primary_model_endpoint": "gemini-3.8-live",
            "tier2_moderate_model": "gemini-4-hydrogen:medium",
            "tier3_heavy_model": "gemini-4-argon:medium",
            "api": {
                "live_model_id": "gemini-3.8-live",
                "moderate_model_id": "gemini-4-hydrogen:medium",
                "pro_model_id": "gemini-4-argon:medium",
            },
        }
        self.events = []
        self.dispatcher = ToolDispatcher(
            config_getter=lambda: self.mock_config,
            on_event=lambda ev, data: self.events.append((ev, data)),
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_tool_declarations_and_safety_boundaries(self):
        """Verifies Hydrogen orchestrator omits auto-send email and keeps Game Mode + Tier 3 wording consistent."""
        self.assertIn("workflow_orchestration", DELEGATE_MODERATE_TASK_DECLARATION["parameters"]["properties"]["task_type"]["enum"])
        self.assertIn("use Tier 3", DELEGATE_HEAVY_TASK_DECLARATION["description"])
        self.assertNotIn("use Tier 2", DELEGATE_HEAVY_TASK_DECLARATION["description"])
        self.assertIn("delegate_moderate_task", GameEngine.CORE_GAME_TOOL_NAMES)
        self.assertNotIn("send_email_draft", ORCHESTRATOR_ALLOWED_TOOL_NAMES)
        self.assertIn("workflow_orchestration", DOSSIER_GENERATION_DIRECTIVE)

    async def test_orchestrator_checklist_argon_handoff_and_multimodal_verify(self):
        """
        Verifies Hydrogen plans checklist steps, sub-contracts Argon with paste_to_active_doc=False,
        passes file_path into paste_to_document for any app, and receives multimodal screenshots.
        """
        md_file = os.path.join(self.test_dir, "report.md")
        with open(md_file, "w", encoding="utf-8") as f:
            f.write("# Espresso Calibration Guide\n\nTarget 1:2 ratio at 93°C.\n")

        async def fake_dispatch(fn_name, fn_args):
            if fn_name == "launch_application":
                return {"status": "success", "executable": "winword.exe"}
            if fn_name == "delegate_heavy_task":
                self.assertIs(fn_args.get("paste_to_active_doc"), False)
                return {
                    "status": "success",
                    "file_path": md_file,
                    "filename": "report.md",
                    "char_count": 55,
                    "pasted_to_doc": False,
                }
            if fn_name == "paste_to_document":
                return {
                    "status": "success",
                    "app_name": fn_args.get("target_app", "winword"),
                    "_jpeg_bytes": b"FAKE_JPEG_DATA",
                }
            if fn_name == "capture_screen_snapshot":
                return {"status": "success", "_jpeg_bytes": b"SECOND_JPEG_DATA"}
            return {"status": "success"}

        self.dispatcher.dispatch = AsyncMock(side_effect=fake_dispatch)

        turn_1 = _make_model_response(function_calls=[
            _make_fc("update_task_checklist", {
                "steps": [
                    {"title": "Launch Microsoft Word", "status": "in_progress"},
                    {"title": "Have Argon author guide", "status": "pending"},
                    {"title": "Paste into Word and verify", "status": "pending"},
                ],
                "current_summary": "Launching Microsoft Word",
            }),
            _make_fc("launch_application", {"app_name": "winword"}),
        ])
        turn_2 = _make_model_response(function_calls=[
            _make_fc("delegate_heavy_task", {
                "task_type": "research_dossier",
                "title": "Espresso Guide",
                "instructions": "Write detailed espresso calibration guide.",
                "paste_to_active_doc": False,
            }),
        ])
        turn_3 = _make_model_response(function_calls=[
            _make_fc("paste_to_document", {"file_path": md_file, "target_app": "winword"}),
            _make_fc("capture_screen_snapshot", {"monitor": "auto"}),
            _make_fc("update_task_checklist", {
                "steps": [
                    {"title": "Launch Microsoft Word", "status": "completed"},
                    {"title": "Have Argon author guide", "status": "completed"},
                    {"title": "Paste into Word and verify", "status": "completed"},
                ],
                "current_summary": "Verified formatted guide in Microsoft Word",
            }),
        ])
        turn_4 = _make_model_response(text="I drafted your espresso guide with Argon, pasted it into Microsoft Word, and verified it on screen.")

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [turn_1, turn_2, turn_3, turn_4]
        self.dispatcher.genai_client = mock_client

        result = await self.dispatcher.execute_moderate_task(
            task_type="workflow_orchestration",
            title="Espresso Guide in Word",
            instructions="Open Microsoft Word and create an espresso calibration guide.",
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["steps_completed"], 3)
        self.assertEqual(result["total_steps"], 3)
        self.assertIn("Microsoft Word", result["answer"])
        self.assertIn("delegate_heavy_task", result["tools_used"])
        self.assertIn("paste_to_document", result["tools_used"])
        self.assertTrue(any(ev == "chat_event" and "Hydrogen Task Plan" in str(d.get("name", "")) for ev, d in self.events))

    async def test_paste_to_document_reads_file_path_from_disk(self):
        """Verifies paste_to_document tool supports file_path for lossless Argon -> app handoff."""
        md_file = os.path.join(self.test_dir, "handoff.md")
        with open(md_file, "w", encoding="utf-8") as f:
            f.write("# Test Handoff\n\nBody text.")

        with patch("tools.dispatcher.paste_markdown_to_target_app", return_value={"status": "success", "message": "Pasted"}) as mock_paste, \
             patch.object(self.dispatcher.screen_pipeline, "capture_frame", new=AsyncMock(return_value=(b"JPG", {}))):
            res = await self.dispatcher.dispatch("paste_to_document", {
                "file_path": md_file,
                "target_app": "notepad",
            })
            self.assertEqual(res["status"], "success")
            mock_paste.assert_called_once_with("# Test Handoff\n\nBody text.", app_name="notepad")

    async def test_execute_heavy_task_honors_explicit_false_paste(self):
        """Verifies paste_to_active_doc=False stops Argon from auto-pasting even if 'Google Docs' is in instructions."""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = SimpleNamespace(text="# Full Doc\n\nDetails.")
        self.dispatcher.genai_client = mock_client

        with patch("tools.dispatcher.paste_markdown_to_target_app") as mock_paste, \
             patch("tools.dispatcher.export_research_dossier", return_value={"status": "success", "file_path": "/tmp/doc.md", "filename": "doc.md"}):
            res = await self.dispatcher.execute_heavy_task(
                task_type="research_dossier",
                title="Google Docs Guide",
                instructions="Explain X for Google Docs.",
                paste_to_active_doc=False,
                target_app="google docs",
            )
            self.assertEqual(res["status"], "success")
            self.assertFalse(res["pasted_to_doc"])
            mock_paste.assert_not_called()

    async def test_orchestrator_halts_cleanly_on_whitelist_block(self):
        """Verifies Hydrogen orchestrator stops cleanly when an app is blocked by security whitelist."""
        self.dispatcher.dispatch = AsyncMock(return_value={
            "status": "blocked",
            "message": "Application 'winword.exe' is not in your whitelist.",
        })
        turn_1 = _make_model_response(function_calls=[
            _make_fc("launch_application", {"app_name": "winword"}),
        ])
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = turn_1

        orch = TaskOrchestrator(self.dispatcher, max_iterations=5)
        res = await orch.run(
            client=mock_client,
            model="gemini-4-hydrogen",
            thinking_level="medium",
            title="Word Task",
            instructions="Open Word and write a memo.",
        )
        self.assertEqual(res["status"], "blocked")
        self.assertIn("whitelist", res["answer"].lower())
        self.assertEqual(mock_client.models.generate_content.call_count, 1)


class TestClipboardLoudFailure(unittest.TestCase):
    def test_google_docs_ignores_unrelated_chrome_tab_and_fails_loudly_if_not_loaded(self):
        """
        Verifies paste_markdown_to_target_app does NOT paste into an unrelated Chrome window
        (like Gmail/YouTube) when Google Docs was specifically requested and docs.new fails to open.
        """
        from tools.clipboard import paste_markdown_to_target_app

        with patch("tools.clipboard.set_clipboard_html", return_value=True), \
             patch("tools.clipboard._get_window_title_safe", return_value="Inbox - Gmail - Google Chrome"), \
             patch("tools.os_controls.find_hwnd_by_query", return_value=77777), \
             patch("tools.os_controls.navigate_browser", return_value={"status": "success"}) as mock_nav, \
             patch("tools.gui_primitives.GuiPrimitivesController.press_key") as mock_key, \
             patch("time.sleep", return_value=None), \
             patch("time.time", side_effect=[0.0, 5.0, 11.0]):
            res = paste_markdown_to_target_app("# Sourdough", app_name="google docs")
            self.assertEqual(res["status"], "error")
            mock_nav.assert_called_with("https://docs.new")
            mock_key.assert_not_called()

    def test_focus_window_accepts_int_hwnd_without_attribute_error(self):
        """Verifies focus_window handles an integer HWND cleanly without calling .lower() on int."""
        from tools.os_controls import focus_window

        with patch("tools.os_controls.win32gui.IsWindow", return_value=1), \
             patch("tools.os_controls.win32gui.GetWindowText", return_value="Untitled document - Google Docs"), \
             patch("tools.os_controls.bring_hwnd_to_foreground", return_value=True) as mock_fg:
            res = focus_window(123456)
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["hwnd"], 123456)
            mock_fg.assert_called_once_with(123456)


class TestTierOverrideRerunAndAccentLock(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_config = {
            "agent_name": "Aether",
            "api": {"agent_name": "Aether", "voice_accent": "British"},
        }
        with patch("core.engine.DesktopNotificationWatcher", return_value=MagicMock()), \
             patch("core.engine.LiveStreamService", return_value=MagicMock()), \
             patch("core.engine.SteamService", return_value=MagicMock()), \
             patch("core.engine.GameEngine", return_value=MagicMock()):
            self.engine = AetherEngine(config_getter=lambda: self.mock_config)
        self.engine.notify = MagicMock()
        self.engine._speak_feedback = AsyncMock()

    def test_parse_tier_control_commands(self):
        self.assertEqual(
            AetherEngine.parse_tier_control_command("Aether, rerun that last request using Hydrogen"),
            {"action": "rerun", "tier": "hydrogen"},
        )
        self.assertEqual(
            AetherEngine.parse_tier_control_command("Hey Aether, retry that previous question with Argon"),
            {"action": "rerun", "tier": "argon"},
        )
        self.assertEqual(
            AetherEngine.parse_tier_control_command("Aether, use Hydrogen for this next task"),
            {"action": "override_next", "tier": "hydrogen", "inline_task": None},
        )
        inline = AetherEngine.parse_tier_control_command("Use Argon to deep dive into geothermal heat pumps")
        self.assertEqual(inline["action"], "override_inline")
        self.assertEqual(inline["tier"], "argon")
        self.assertIsNone(AetherEngine.parse_tier_control_command("Tell me a joke about a doctor"))

    async def test_sticky_next_turn_override_and_rerun_flow(self):
        self.engine.dispatcher.dispatch = AsyncMock(return_value={
            "status": "success",
            "answer": "Hydrogen solved the valve puzzle cleanly.",
        })

        # 1. Arm sticky override
        r1 = await self.engine._maybe_handle_tier_control("Aether, use Hydrogen for this next task", speak_result=True)
        self.assertTrue(r1["handled"])
        self.assertEqual(self.engine._pending_tier_override, "hydrogen")

        # 2. Next turn consumes sticky override and routes to Hydrogen + records routable prompt
        r2 = await self.engine._maybe_handle_tier_control("How do I solve the three-valve clockwork door?", speak_result=True)
        self.assertTrue(r2["handled"])
        self.assertEqual(r2["tier"], "hydrogen")
        self.assertIsNone(self.engine._pending_tier_override)
        self.assertEqual(self.engine._last_routable_user_prompt, "How do I solve the three-valve clockwork door?")

        # 3. Rerun previous request using Argon
        self.engine.dispatcher.dispatch = AsyncMock(return_value={
            "status": "success",
            "filename": "valve_analysis.md",
            "directive": "Argon completed full solution dossier.",
        })
        r3 = await self.engine._maybe_handle_tier_control("Hey Aether, rerun that last request using Argon", speak_result=True)
        self.assertTrue(r3["handled"])
        self.assertEqual(r3["tier"], "argon")
        self.engine.dispatcher.dispatch.assert_called_once()
        call_name, call_args = self.engine.dispatcher.dispatch.call_args[0]
        self.assertEqual(call_name, "delegate_heavy_task")
        self.assertEqual(call_args["instructions"], "How do I solve the three-valve clockwork door?")

    def test_accent_directive_enforces_quoted_dialogue_continuity(self):
        directive = build_accent_directive("British")
        reminder = build_accent_reminder("British")
        self.assertIn("quotation marks", directive)
        self.assertIn("EVERY sentence", reminder)
        self.assertEqual(resolve_accent_language_code("British"), "en-GB")
        self.assertEqual(resolve_accent_language_code("Australian"), "en-AU")
        self.assertEqual(resolve_accent_language_code("Indian"), "en-IN")
        self.assertIsNone(resolve_accent_language_code("default"))
        anchors = build_live_accent_anchor_turns("British")
        self.assertEqual(len(anchors), 2)
        self.assertEqual(anchors[0].role, "user")
        self.assertEqual(anchors[1].role, "model")
        live_cfg = self.engine._build_live_config(voice_name="Sulafat")
        self.assertEqual(live_cfg.speech_config.language_code, "en-GB")


if __name__ == "__main__":
    unittest.main()
