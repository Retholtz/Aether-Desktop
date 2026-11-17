"""
Unit tests for:
1. TTRPG engine gating: switching to a campaign workspace (e.g. loading PDFs or selecting a campaign)
   must NOT activate TTRPG DM mode, strip desktop tools, or invalidate an active desktop Live session
   unless set_ttrpg_session_active(True) is explicitly called.
2. Live session rotation concurrency & tool-response protection:
   - SessionLifecycleManager.reset_metrics() preserves an active rotation_in_progress lock.
   - _is_audio_idle_for_rotation() waits while a tool response is pending.
3. Background OAuth EmailMonitorService:
   - Primes existing unread emails on first poll without spamming.
   - Emits formatted alerts ("Hey Michael, Pam Koester just emailed you...") on newly arrived unread mail.
4. Dispatcher status logging for {"success": False} responses.
"""

import asyncio
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.email_monitor import EmailMonitorService, extract_sender_display_name
from core.engine import AetherEngine
from core.session_lifecycle import SessionLifecycleManager
from tools.dispatcher import ToolDispatcher


class TestTtrpgGatingAndRotationLock(unittest.TestCase):
    def _make_stub_engine(self):
        eng = AetherEngine.__new__(AetherEngine)
        eng.is_running = True
        eng._ttrpg_session_active = False
        eng._reset_context_flag = False
        eng._rotation_scheduled = False
        eng._pending_tool_response_until = 0.0
        eng._in_active_turn = False
        eng._is_transcribing = False
        eng._is_audio_streaming = False
        eng._is_speaking = False
        eng._is_tool_running = False
        eng.is_tool_executing = False
        eng.audio = None
        eng.audio_player = None
        eng.active_workspace_type = "general"
        eng.active_workspace_context_id = "default"
        eng.base_system_prompt = "You are Aether, a fast, knowledgeable desktop voice assistant."
        eng._base_system_instruction = eng.base_system_prompt
        eng.active_system_prompt = eng.base_system_prompt
        eng.config_getter = lambda: {"api": {"agent_name": "Aether"}, "user": {"name": "Michael"}}
        eng.session_mgr = MagicMock()
        eng.session_mgr.switch_session.return_value = []
        eng.ttrpg_service = MagicMock()
        eng.ttrpg_service.manifest = {"active_campaign": "elf_hunt", "campaigns": {"elf_hunt": {"title": "Elf Hunt"}}}
        eng.ttrpg_service.build_gm_system_prompt.return_value = "UNIVERSAL TABLETOP ROLEPLAYING ENGINE"
        eng.dispatcher = MagicMock()
        eng.invalidate_live_session = MagicMock()
        eng.request_live_session_rotation = MagicMock(return_value=True)
        eng.game_engine = None
        eng.game_mgr = None
        eng.user_memory = None
        return eng

    def test_campaign_workspace_switch_does_not_activate_ttrpg_engine(self):
        eng = self._make_stub_engine()
        eng.set_active_workspace("campaign", "elf_hunt", "Elf Hunt")

        # Must remain in Desktop Agent mode with full desktop tools until Start/Resume button is clicked
        self.assertFalse(eng.is_ttrpg_session_active())
        eng.dispatcher.reset_scope.assert_called_once()
        eng.dispatcher.set_active_scope.assert_not_called()
        eng.invalidate_live_session.assert_not_called()

        # Filtered tools must still include desktop/browser tools (e.g. navigate_browser)
        decls = [{"name": "navigate_browser"}, {"name": "roll_dice"}, {"name": "search_emails"}]
        filtered = eng._filter_tools_for_workspace(decls)
        names = {d["name"] for d in filtered}
        self.assertIn("navigate_browser", names)
        self.assertIn("search_emails", names)

    def test_explicit_start_adventure_activates_ttrpg_mode_and_fences_tools(self):
        eng = self._make_stub_engine()
        eng.set_active_workspace("campaign", "elf_hunt", "Elf Hunt")
        eng.set_ttrpg_session_active(True)

        self.assertTrue(eng.is_ttrpg_session_active())
        eng.dispatcher.set_active_scope.assert_called_with(["ttrpg_tools", "media_tools"])
        eng.request_live_session_rotation.assert_called_once()

        decls = [{"name": "navigate_browser"}, {"name": "roll_dice"}, {"name": "search_emails"}]
        filtered = eng._filter_tools_for_workspace(decls)
        names = {d["name"] for d in filtered}
        self.assertEqual(names, {"roll_dice"})

        # Stopping adventure restores desktop scope
        eng.set_ttrpg_session_active(False)
        self.assertFalse(eng.is_ttrpg_session_active())
        eng.dispatcher.reset_scope.assert_called()

    def test_reset_metrics_preserves_active_rotation_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SessionLifecycleManager(chats_dir=tmp)
            mgr.rotation_in_progress = True
            mgr.turn_count = 5
            mgr.reset_metrics()
            self.assertTrue(mgr.rotation_in_progress)

            mgr.reset_metrics(clear_rotation_lock=True)
            self.assertFalse(mgr.rotation_in_progress)

    def test_is_audio_idle_waits_for_pending_tool_response(self):
        eng = self._make_stub_engine()
        eng._pending_tool_response_until = time.time() + 5.0
        self.assertFalse(eng._is_audio_idle_for_rotation())

        eng._pending_tool_response_until = time.time() - 1.0
        self.assertTrue(eng._is_audio_idle_for_rotation())


class TestEmailMonitorService(unittest.IsolatedAsyncioTestCase):
    async def test_primes_then_alerts_new_unread_email(self):
        alerts = []
        fake_mgr = MagicMock()
        fake_mgr.get_status.return_value = {"google": {"connected": True}, "microsoft": {"connected": False}, "imap": {"connected": False}}
        fake_mgr.list_messages.side_effect = [
            {
                "success": True,
                "provider": "google",
                "messages": [
                    {
                        "message_id": "msg_old_1",
                        "thread_id": "thr_1",
                        "from": "Newsletter <news@example.com>",
                        "subject": "Weekly Digest",
                        "snippet": "Old unread mail",
                        "date": "2026-11-17T10:00:00Z",
                    }
                ],
            },
            {
                "success": True,
                "provider": "google",
                "messages": [
                    {
                        "message_id": "msg_new_2",
                        "thread_id": "thr_2",
                        "from": "Pam Koester <pam@koesterlaw.com>",
                        "subject": "Questions about the closing",
                        "snippet": "Hi Michael, I have a few questions about tomorrow's closing.",
                        "date": "2026-11-17T14:20:00Z",
                    },
                    {
                        "message_id": "msg_old_1",
                        "thread_id": "thr_1",
                        "from": "Newsletter <news@example.com>",
                        "subject": "Weekly Digest",
                        "snippet": "Old unread mail",
                        "date": "2026-11-17T10:00:00Z",
                    },
                ],
            },
        ]

        monitor = EmailMonitorService(
            poll_interval=60.0,
            verbal_announcement=True,
            summarize_with_ai=False,
            on_new_email=lambda a: alerts.append(a),
            oauth_manager_getter=lambda: fake_mgr,
            user_name_getter=lambda: "Michael",
        )

        # First poll primes existing unread mail without alerting
        first = await monitor.poll_once()
        self.assertEqual(first, [])
        self.assertEqual(len(alerts), 0)

        # Second poll detects the new unread email from Pam Koester
        second = await monitor.poll_once()
        self.assertEqual(len(second), 1)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].sender_name, "Pam Koester")
        self.assertIn("Hey Michael, Pam Koester just emailed you about Questions about the closing.", alerts[0].alert_text)

    def test_extract_sender_display_name(self):
        self.assertEqual(extract_sender_display_name('"Pam Koester" <pam@example.com>'), "Pam Koester")
        self.assertEqual(extract_sender_display_name("pam@example.com"), "pam")
        self.assertEqual(extract_sender_display_name(""), "someone")


class TestDispatcherSuccessFalseLogging(unittest.IsolatedAsyncioTestCase):
    async def test_dispatcher_logs_warning_when_success_is_false(self):
        disp = ToolDispatcher.__new__(ToolDispatcher)
        disp._custom_handlers = {}
        disp._custom_declarations = {}
        disp._active_scopes = []
        disp._dispatch_internal = MagicMock(
            return_value=asyncio.Future()
        )
        fut = asyncio.Future()
        fut.set_result({"success": False, "error": "No email account is connected."})
        disp._dispatch_internal = MagicMock(return_value=fut)

        with self.assertLogs("Aether.Dispatcher", level="WARNING") as cm:
            res = await disp.dispatch("search_emails", {"query": "cookie recipe"})
        self.assertFalse(res["success"])
        self.assertTrue(any("Status: error" in msg for msg in cm.output))


if __name__ == "__main__":
    unittest.main()
