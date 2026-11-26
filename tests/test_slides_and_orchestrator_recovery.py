"""
Unit tests for presentation generation & delivery hardening:
1. AST gatekeeper allowlist for python-pptx (`pptx`) and `matplotlib`
2. TaskOrchestrator self-correction on `execute_automation_script` AST block vs. hard-abort on app whitelist block
3. ScriptRunner UTF-8 stdout/stderr encoding without cp1252 UnicodeEncodeError or mojibake
4. OAuthManager.upload_pptx_to_google_slides() Drive API multipart conversion & re-auth handling
5. Program-agnostic presentation app aliases (PowerPoint, LibreOffice Impress, OnlyOffice, WPS)
"""

import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.oauth_manager import GOOGLE_SCOPES, GOOGLE_YOUTUBE_SCOPES, OAuthManager
from core.orchestrator import ORCHESTRATOR_ALLOWED_TOOL_NAMES, TaskOrchestrator
from security.ast_gatekeeper import BUNDLED_THIRD_PARTY_MODULES, validate_python_code
from security.whitelist import WhitelistValidator
from tools.dispatcher import ToolDispatcher, get_all_tool_declarations
from tools.email_tools import upload_docx_to_google_docs, upload_pptx_to_google_slides
from tools.script_runner import ScriptRunner


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


class TestPresentationAllowlistAndUtf8ScriptRunner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_pptx_and_matplotlib_allowed_by_ast_gatekeeper(self):
        self.assertIn("pptx", BUNDLED_THIRD_PARTY_MODULES)
        self.assertIn("matplotlib", BUNDLED_THIRD_PARTY_MODULES)
        code = (
            "import matplotlib\n"
            "matplotlib.use('Agg')\n"
            "import matplotlib.pyplot as plt\n"
            "from pptx import Presentation\n"
            "from pptx.util import Inches\n"
            "prs = Presentation()\n"
            "slide = prs.slides.add_slide(prs.slide_layouts[0])\n"
            "slide.shapes.title.text = 'Inpatient Diabetes Management'\n"
        )
        ok, errs = validate_python_code(code)
        self.assertTrue(ok, f"Expected pptx + matplotlib script to pass AST check, got: {errs}")

    def test_script_runner_handles_utf8_unicode_and_creates_pptx(self):
        """Verifies ScriptRunner sets UTF-8 pipes (no cp1252 crash on >= / em-dash) and can generate a real .pptx."""
        from core.telemetry_db import TelemetryDB

        db = TelemetryDB(db_path=os.path.join(self.temp_dir, "telemetry.db"))
        try:
            runner = ScriptRunner(workspace_root=self.temp_dir, telemetry_db=db)
            out_pptx = os.path.join(self.temp_dir, "diabetes_test.pptx")
            script = (
                "from pptx import Presentation\n"
                "prs = Presentation()\n"
                "slide = prs.slides.add_slide(prs.slide_layouts[0])\n"
                "slide.shapes.title.text = 'Targets: BG 140–180 mg/dL — Threshold ≥ 180 mg/dL'\n"
                f"prs.save(r'''{out_pptx}''')\n"
                "print('Targets: 140–180 mg/dL — Threshold ≥ 180 mg/dL')\n"
            )
            res = runner.execute_script(script, script_type="python", description="UTF-8 pptx test")
            self.assertEqual(res["status"], "success", f"Script failed: {res}")
            self.assertTrue(os.path.isfile(out_pptx))
            self.assertIn("≥ 180", res["stdout"])
            self.assertIn("—", res["stdout"])
            self.assertNotIn("â€”", res["stdout"])
        finally:
            db.close()

    def test_program_agnostic_office_aliases(self):
        self.assertEqual(WhitelistValidator.normalize_app_name("powerpoint"), "powerpnt.exe")
        self.assertEqual(WhitelistValidator.normalize_app_name("Microsoft PowerPoint"), "powerpnt.exe")
        self.assertEqual(WhitelistValidator.normalize_app_name("LibreOffice"), "soffice.exe")
        self.assertEqual(WhitelistValidator.normalize_app_name("LibreOffice Impress"), "simpress.exe")
        self.assertEqual(WhitelistValidator.normalize_app_name("impress"), "simpress.exe")
        self.assertEqual(WhitelistValidator.normalize_app_name("LibreOffice Writer"), "swriter.exe")
        self.assertEqual(WhitelistValidator.normalize_app_name("LibreOffice Calc"), "scalc.exe")
        self.assertEqual(WhitelistValidator.normalize_app_name("onlyoffice"), "DesktopEditors.exe")
        self.assertEqual(WhitelistValidator.normalize_app_name("calc"), "calc.exe")


class TestOrchestratorAstBlockRecovery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dispatcher = ToolDispatcher(config_getter=lambda: {})

    async def test_orchestrator_recovers_from_script_ast_block(self):
        """AST block on execute_automation_script must NOT hard-abort; Hydrogen gets to retry and succeed."""
        calls = []

        async def fake_dispatch(fn_name, fn_args):
            calls.append(fn_name)
            if len(calls) == 1:
                return {
                    "status": "blocked",
                    "error": "AST Gatekeeper Rejected: Import of non-allowlisted module 'pandas' is prohibited.",
                    "message": "Script execution was blocked by the safety gatekeeper: Import of non-allowlisted module 'pandas' is prohibited.",
                }
            return {"status": "success", "stdout": "PPTX built cleanly"}

        self.dispatcher.dispatch = AsyncMock(side_effect=fake_dispatch)

        turn_1 = _make_model_response(function_calls=[
            _make_fc("execute_automation_script", {"script_code": "import pandas"}),
        ])
        turn_2 = _make_model_response(function_calls=[
            _make_fc("execute_automation_script", {"script_code": "from pptx import Presentation"}),
        ])
        turn_3 = _make_model_response(text="Rewrote script with python-pptx and generated the presentation.")

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [turn_1, turn_2, turn_3]

        orch = TaskOrchestrator(self.dispatcher, max_iterations=5)
        res = await orch.run(
            client=mock_client,
            model="gemini-4-hydrogen",
            thinking_level="medium",
            title="Build Slides",
            instructions="Create a slide deck.",
        )

        self.assertEqual(res["status"], "success")
        self.assertEqual(mock_client.models.generate_content.call_count, 3)
        self.assertEqual(calls, ["execute_automation_script", "execute_automation_script"])
        self.assertIn("python-pptx", res["answer"])

    async def test_orchestrator_still_halts_on_app_whitelist_block(self):
        """launch_application returning status=blocked must still halt immediately."""
        self.dispatcher.dispatch = AsyncMock(return_value={
            "status": "blocked",
            "message": "Security Blocked: 'powerpnt.exe' is not in your allowed application whitelist.",
        })
        turn_1 = _make_model_response(function_calls=[
            _make_fc("launch_application", {"app_name": "powerpoint"}),
        ])
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = turn_1

        orch = TaskOrchestrator(self.dispatcher, max_iterations=5)
        res = await orch.run(
            client=mock_client,
            model="gemini-4-hydrogen",
            thinking_level="medium",
            title="Open PowerPoint",
            instructions="Open PowerPoint.",
        )
        self.assertEqual(res["status"], "blocked")
        self.assertEqual(mock_client.models.generate_content.call_count, 1)


class TestGoogleSlidesDriveImport(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.pptx_path = os.path.join(self.temp_dir, "diabetes_deck.pptx")
        from pptx import Presentation

        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[0])
        slide.shapes.title.text = "Inpatient Diabetes Management"
        prs.save(self.pptx_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_drive_scope_and_tool_registered(self):
        self.assertIn("https://www.googleapis.com/auth/drive.file", GOOGLE_SCOPES)
        # Google Identity rejects combining youtube.readonly with drive.file in the same OAuth request (Error 400)
        self.assertNotIn("https://www.googleapis.com/auth/youtube.readonly", GOOGLE_SCOPES)
        self.assertIn("https://www.googleapis.com/auth/youtube.readonly", GOOGLE_YOUTUBE_SCOPES)
        self.assertNotIn("https://www.googleapis.com/auth/drive.file", GOOGLE_YOUTUBE_SCOPES)
        self.assertIn("upload_pptx_to_google_slides", ORCHESTRATOR_ALLOWED_TOOL_NAMES)
        decl_names = {d.get("name") for d in get_all_tool_declarations()}
        self.assertIn("upload_pptx_to_google_slides", decl_names)

    def test_upload_pptx_resolves_bare_filename_from_data_pptx(self):
        mgr = OAuthManager()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.content = b"{}"
        mock_resp.json.return_value = {
            "id": "slide_dir_1",
            "name": "diabetes_deck",
            "webViewLink": "https://docs.google.com/presentation/d/slide_dir_1/edit",
        }
        with patch("core.paths.user_data_path", side_effect=lambda *parts: os.path.join(self.temp_dir, os.path.basename(parts[-1]))), \
             patch.object(mgr, "_google_headers", return_value={"Authorization": "Bearer test_tok"}), \
             patch("core.oauth_manager.requests.post", return_value=mock_resp):
            res = mgr.upload_pptx_to_google_slides("diabetes_deck.pptx")
        self.assertTrue(res["success"])
        self.assertEqual(res["local_file_path"], os.path.abspath(self.pptx_path))

    def test_upload_pptx_to_google_slides_success(self):
        mgr = OAuthManager()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.content = b"{}"
        mock_resp.json.return_value = {
            "id": "slide_abc123",
            "name": "Inpatient Diabetes Management",
            "mimeType": "application/vnd.google-apps.presentation",
            "webViewLink": "https://docs.google.com/presentation/d/slide_abc123/edit",
        }

        with patch.object(mgr, "_google_headers", return_value={"Authorization": "Bearer test_tok"}), \
             patch("core.oauth_manager.requests.post", return_value=mock_resp) as mock_post:
            res = mgr.upload_pptx_to_google_slides(self.pptx_path, title="Inpatient Diabetes Management")

        self.assertEqual(res["status"], "success")
        self.assertTrue(res["success"])
        self.assertEqual(res["file_id"], "slide_abc123")
        self.assertEqual(res["slides_url"], "https://docs.google.com/presentation/d/slide_abc123/edit")
        self.assertEqual(res["local_file_path"], os.path.abspath(self.pptx_path))

        # Verify multipart/related payload includes Google Slides conversion MIME type
        call_kwargs = mock_post.call_args.kwargs
        self.assertIn("multipart/related; boundary=", call_kwargs["headers"]["Content-Type"])
        self.assertIn(b"application/vnd.google-apps.presentation", call_kwargs["data"])
        self.assertIn(b"application/vnd.openxmlformats-officedocument.presentationml.presentation", call_kwargs["data"])

    def test_upload_pptx_handles_insufficient_drive_scope(self):
        mgr = OAuthManager()
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.ok = False
        mock_resp.text = '{"error":{"message":"Request had insufficient authentication scopes."}}'

        with patch.object(mgr, "_google_headers", return_value={"Authorization": "Bearer old_tok"}), \
             patch("core.oauth_manager.requests.post", return_value=mock_resp):
            res = mgr.upload_pptx_to_google_slides(self.pptx_path)

        self.assertEqual(res["status"], "error")
        self.assertFalse(res["success"])
        self.assertTrue(res.get("needs_reauth"))
        self.assertEqual(res["local_file_path"], os.path.abspath(self.pptx_path))
        self.assertIn("re-authorized", res["error"].lower())

    def test_upload_pptx_rejects_missing_and_invalid_extension(self):
        mgr = OAuthManager()
        bad_txt = os.path.join(self.temp_dir, "notes.txt")
        with open(bad_txt, "w", encoding="utf-8") as f:
            f.write("not a deck")

        res_ext = mgr.upload_pptx_to_google_slides(bad_txt)
        self.assertEqual(res_ext["status"], "error")
        self.assertIn("Unsupported presentation format", res_ext["error"])

        res_missing = mgr.upload_pptx_to_google_slides(os.path.join(self.temp_dir, "missing.pptx"))
        self.assertEqual(res_missing["status"], "error")
        self.assertIn("not found", res_missing["error"].lower())

    def test_tool_wrapper_opens_browser_on_success(self):
        fake_res = {
            "status": "success",
            "success": True,
            "slides_url": "https://docs.google.com/presentation/d/xyz999/edit",
            "local_file_path": self.pptx_path,
        }
        fake_mgr = MagicMock()
        fake_mgr.upload_pptx_to_google_slides.return_value = dict(fake_res)

        with patch("tools.email_tools.get_oauth_manager", return_value=fake_mgr), \
             patch("tools.os_controls.navigate_browser", return_value={"status": "success"}) as mock_nav:
            out = upload_pptx_to_google_slides(self.pptx_path, title="Deck", open_in_browser=True, browser_app="chrome")

        self.assertTrue(out["success"])
        self.assertTrue(out["browser_opened"])
        mock_nav.assert_called_once_with("https://docs.google.com/presentation/d/xyz999/edit", app_name="chrome")


class TestHeavyPythonAutomationHardening(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.dispatcher = ToolDispatcher(config_getter=lambda: {})
        self.dispatcher.script_runner.cache_dir = os.path.join(self.temp_dir, "cache")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_unexecuted_heavy_script_is_saved_truncated_and_marked_not_executed(self):
        """Prevents 137KB inline code blowup and hallucinated completion when execute_if_code=False."""
        huge_code = "print('slide')\n" * 6000  # ~84 KB
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = SimpleNamespace(
            text=f"```python\n{huge_code}\n```"
        )
        self.dispatcher.genai_client = mock_client

        res = await self.dispatcher.execute_heavy_task(
            task_type="python_automation",
            title="Huge Diabetes Deck",
            instructions="Generate slides",
            execute_if_code=False,
        )

        self.assertEqual(res["status"], "not_executed")
        self.assertIs(res["executed"], False)
        self.assertTrue(os.path.isfile(res["script_path"]))
        self.assertLess(len(res["generated_code"]), 5000)
        self.assertGreater(res["code_chars"], 50000)
        self.assertIn("HAS NOT BEEN EXECUTED", res["directive"])

    async def test_heavy_python_automation_propagates_script_failure_and_uses_90s_timeout(self):
        """execute_heavy_task must never report status=success when ScriptRunner fails."""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = SimpleNamespace(
            text="```python\nraise RuntimeError('boom')\n```"
        )
        self.dispatcher.genai_client = mock_client

        with patch.object(
            self.dispatcher.script_runner,
            "execute_script",
            return_value={"status": "error", "error": "RuntimeError: boom", "file": "failed.py"},
        ) as mock_exec:
            res = await self.dispatcher.execute_heavy_task(
                task_type="python_automation",
                title="Failing Deck Script",
                instructions="Build slides",
                execute_if_code=True,
            )

        self.assertEqual(res["status"], "error")
        self.assertIs(res["executed"], True)
        self.assertIn("FAILED", res["directive"])
        self.assertEqual(mock_exec.call_args.kwargs.get("timeout"), 90)

    def test_engine_directive_routes_presentations_to_hydrogen_orchestration(self):
        from core.engine import DOSSIER_GENERATION_DIRECTIVE
        self.assertIn("Google Slides", DOSSIER_GENERATION_DIRECTIVE)
        self.assertIn("data/pptx/", DOSSIER_GENERATION_DIRECTIVE)
        self.assertIn('delegate_moderate_task(task_type="workflow_orchestration"', DOSSIER_GENERATION_DIRECTIVE)


class TestGoogleDocsDriveImportAndSlideHygiene(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.docx_path = os.path.join(self.temp_dir, "resident_handout.docx")
        import docx

        doc = docx.Document()
        doc.add_heading("Inpatient Diabetes Pocket Handout", level=1)
        doc.add_paragraph("Target BG: 140–180 mg/dL for most hospitalized patients.")
        doc.save(self.docx_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_docs_tool_registered_and_hygiene_rules_present(self):
        from core.orchestrator import ORCHESTRATOR_SYSTEM_INSTRUCTION

        self.assertIn("upload_docx_to_google_docs", ORCHESTRATOR_ALLOWED_TOOL_NAMES)
        decl_names = {d.get("name") for d in get_all_tool_declarations()}
        self.assertIn("upload_docx_to_google_docs", decl_names)
        instr_low = ORCHESTRATOR_SYSTEM_INSTRUCTION.lower()
        self.assertIn("ban naive string chopping", instr_low)
        self.assertIn("audience vs. presenter separation", instr_low)
        self.assertIn("speaker_notes", instr_low)
        self.assertIn("data/docx/", instr_low)

    def test_upload_docx_to_google_docs_success_and_data_docx_resolution(self):
        mgr = OAuthManager()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.content = b"{}"
        mock_resp.json.return_value = {
            "id": "doc_abc789",
            "name": "Resident Handout",
            "mimeType": "application/vnd.google-apps.document",
            "webViewLink": "https://docs.google.com/document/d/doc_abc789/edit",
        }

        with patch("core.paths.user_data_path", side_effect=lambda *parts: os.path.join(self.temp_dir, os.path.basename(parts[-1]))), \
             patch.object(mgr, "_google_headers", return_value={"Authorization": "Bearer test_tok"}), \
             patch("core.oauth_manager.requests.post", return_value=mock_resp) as mock_post:
            res = mgr.upload_docx_to_google_docs("resident_handout.docx", title="Resident Handout")

        self.assertEqual(res["status"], "success")
        self.assertTrue(res["success"])
        self.assertEqual(res["file_id"], "doc_abc789")
        self.assertEqual(res["docs_url"], "https://docs.google.com/document/d/doc_abc789/edit")
        self.assertEqual(res["local_file_path"], os.path.abspath(self.docx_path))

        call_kwargs = mock_post.call_args.kwargs
        self.assertIn(b"application/vnd.google-apps.document", call_kwargs["data"])
        self.assertIn(b"application/vnd.openxmlformats-officedocument.wordprocessingml.document", call_kwargs["data"])

    def test_upload_docx_wrapper_opens_browser_and_rejects_bad_ext(self):
        fake_mgr = MagicMock()
        fake_mgr.upload_docx_to_google_docs.return_value = {
            "status": "success",
            "success": True,
            "docs_url": "https://docs.google.com/document/d/doc999/edit",
            "local_file_path": self.docx_path,
        }
        with patch("tools.email_tools.get_oauth_manager", return_value=fake_mgr), \
             patch("tools.os_controls.navigate_browser", return_value={"status": "success"}) as mock_nav:
            out = upload_docx_to_google_docs(self.docx_path, title="Handout", open_in_browser=True, browser_app="chrome")

        self.assertTrue(out["success"])
        self.assertTrue(out["browser_opened"])
        mock_nav.assert_called_once_with("https://docs.google.com/document/d/doc999/edit", app_name="chrome")

        bad_file = os.path.join(self.temp_dir, "image.png")
        with open(bad_file, "wb") as f:
            f.write(b"\x89PNG")
        res_bad = OAuthManager().upload_docx_to_google_docs(bad_file)
        self.assertEqual(res_bad["status"], "error")
        self.assertIn("Unsupported document format", res_bad["error"])


class TestReceiveLoop1011GracefulRecovery(unittest.IsolatedAsyncioTestCase):
    async def test_1011_server_reset_recovers_without_error_banner_or_audio_kill(self):
        """WebSocket 1011 after long background tool runs must not emit error cards or kill fallback TTS."""
        from core.engine import AetherEngine

        mock_cfg = {"agent_name": "Aether", "audio": {"mode": "ptt"}}
        with patch("core.engine.DesktopNotificationWatcher", return_value=MagicMock()), \
             patch("core.engine.LiveStreamService", return_value=MagicMock()), \
             patch("core.engine.SteamService", return_value=MagicMock()), \
             patch("core.engine.GameEngine", return_value=MagicMock()):
            engine = AetherEngine(config_getter=lambda: mock_cfg)

        events = []
        engine.notify = lambda ev, data: events.append((ev, data))
        engine.audio = MagicMock()
        engine.is_running = True

        class _FailingSession:
            def receive(self):
                raise RuntimeError("1011 None. Internal error encountered.")

        fake_sess = _FailingSession()
        engine.session = fake_sess
        engine.live_session = fake_sess

        await engine._receive_loop(fake_sess)

        self.assertIsNone(engine.session)
        engine.audio.kill_output.assert_not_called()
        self.assertFalse(any(ev == "chat_event" and d.get("type") == "error" for ev, d in events))
        self.assertFalse(any(ev == "status" and d.get("state") == "error" for ev, d in events))


if __name__ == "__main__":
    unittest.main()
