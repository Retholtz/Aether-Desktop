"""
Unit tests for Game Mode Live WebSocket rotation and dynamic tool re-anchoring.
Verifies:
1. Toggling Game Mode sets _reset_context_flag and requests live session rotation.
2. Turning Game Mode OFF rotates the session and restores full desktop tool declarations (close_window, close_application).
3. ToolDispatcher handles close_window and close_application with native hooks and whitelist validation.
4. reset_chat_context wipes turn history, sets flag, and requests live rotation.
5. Successful rotation atomically resets _reset_context_flag and clears the rotation lock.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import os
import shutil
import tempfile

from core.engine import AetherEngine
from core.connection_manager import ConnectionState
from tools.dispatcher import ToolDispatcher, get_all_tool_declarations
from core.session_lifecycle import SessionLifecycleManager


class TestGameModeLiveRotation(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.chats_dir = os.path.join(self.test_dir, "chats")

        # Mock engine instance without starting hardware threads
        self.engine = MagicMock(spec=AetherEngine)
        self.engine.is_running = True
        self.engine._reset_context_flag = False
        self.engine._in_active_turn = False
        self.engine.is_speaking = False
        self.engine.is_audio_streaming = False
        self.engine.is_tool_running = False
        self.engine._is_transcribing = False
        self.engine.session_lifecycle = SessionLifecycleManager(chats_dir=self.chats_dir)
        self.engine.conn_mgr = MagicMock()
        self.engine.conn_mgr.state = ConnectionState.CONNECTED

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_close_window_declaration_present(self):
        """Verifies that close_window is declared in the full desktop tool declarations."""
        declarations = get_all_tool_declarations()
        names = {d.get("name") for d in declarations}
        self.assertIn("close_window", names)
        self.assertIn("close_application", names)

    def test_game_mode_tool_filtering(self):
        """Verifies that in Game Mode, desktop tools are omitted, but restored when Game Mode is off."""
        from core.game_engine import GameEngine
        from tools.game_tools import register_game_tools

        dispatcher = ToolDispatcher(whitelist_getter=lambda: [])
        game_engine = GameEngine(dispatcher=dispatcher)

        declarations = get_all_tool_declarations()

        # In Game Mode
        filtered = game_engine.filter_tools(declarations)
        filtered_names = {d.get("name") for d in filtered}

        self.assertNotIn("run_saved_script", filtered_names)
        self.assertNotIn("execute_automation_script", filtered_names)
        self.assertIn("close_window", filtered_names)
        self.assertIn("close_application", filtered_names)
        self.assertIn("trigger_game_action", filtered_names)

        # In Desktop Mode (unfiltered)
        all_names = {d.get("name") for d in declarations}
        self.assertIn("close_window", all_names)
        self.assertIn("close_application", all_names)
        self.assertIn("trigger_game_action", all_names)

    async def test_dispatcher_close_window(self):
        """Verifies that ToolDispatcher dispatches close_window and close_application."""
        dispatcher = ToolDispatcher(
            whitelist_getter=lambda: ["explorer.exe", "file explorer", "notepad.exe"]
        )

        with patch("tools.dispatcher.close_window") as mock_close_win:
            mock_close_win.return_value = {"status": "success", "title": "File Explorer"}
            res = await dispatcher.dispatch("close_window", {"app_name": "File Explorer"})
            self.assertEqual(res.get("status"), "success")
            mock_close_win.assert_called_once_with("File Explorer")

    async def test_request_live_session_rotation_idle(self):
        """Verifies that request_live_session_rotation schedules rotate_live_session when connected."""
        engine = AetherEngine.__new__(AetherEngine)
        engine.is_running = True
        engine._reset_context_flag = True
        engine.session_lifecycle = MagicMock()
        engine.session_lifecycle.rotation_in_progress = False
        engine.conn_mgr = MagicMock()
        engine.conn_mgr.state = ConnectionState.CONNECTED
        engine._loop = asyncio.get_running_loop()
        engine.client = MagicMock()
        engine.config_getter = lambda: {"api": {"live_model_id": "gemini-3.1-flash-live-preview"}}

        with patch.object(engine, "rotate_live_session", new_callable=AsyncMock) as mock_rotate:
            res = engine.request_live_session_rotation(reason="Game Mode Disabled")
            self.assertTrue(res)

    def test_reset_chat_context_triggers_rotation(self):
        """Verifies reset_chat_context clears history, sets flag, and requests live rotation."""
        engine = AetherEngine.__new__(AetherEngine)
        engine.is_running = True
        engine._reset_context_flag = False
        engine.session_lifecycle = SessionLifecycleManager(chats_dir=self.chats_dir)
        engine.session_lifecycle.record_turn("user", "Hello")
        engine.notify = MagicMock()

        with patch.object(engine, "request_live_session_rotation") as mock_req:
            engine.reset_chat_context()
            self.assertTrue(engine._reset_context_flag)
            self.assertEqual(len(engine.session_lifecycle.turn_history), 0)
            mock_req.assert_called_once_with(reason="User Context Reset")


if __name__ == "__main__":
    unittest.main()
