"""
Unit tests for TTRPG Start/Resume Adventure lifecycle, text vs voice routing,
campaign chat_event workspace routing, and spell Prepped/Max UI helpers.
"""

import asyncio
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.connection_manager import ConnectionState
from core.engine import AetherEngine
from core.gui_bridge import GuiBridge
from core.ttrpg_service import TTRPGService


class TestTtrpgStartResumeLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="ttrpg_start_resume_")
        self.manifest_path = os.path.join(self.tmp_dir, "campaigns.json")
        self.ttrpg_svc = TTRPGService(data_dir=self.tmp_dir, manifest_path=self.manifest_path)
        self.ttrpg_svc.create_campaign(
            title="Elf Hunt",
            system_preset="dnd_5e",
            campaign_id="elf_hunt",
        )

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_bridge_with_stub_engine(self, initial_running=False, initial_mode=None):
        eng = AetherEngine.__new__(AetherEngine)
        eng.is_running = initial_running
        eng._active_pipeline_mode = initial_mode
        eng._ttrpg_session_active = False
        eng._reset_context_flag = False
        eng._rotation_scheduled = False
        eng.active_workspace_type = "general"
        eng.active_workspace_context_id = "default"
        eng.base_system_prompt = "You are Aether."
        eng._base_system_instruction = "You are Aether."
        eng.active_system_prompt = "You are Aether."
        eng.config_getter = lambda: {"api": {"agent_name": "Aether", "pipeline_mode": "live"}}
        eng.conn_mgr = MagicMock()
        eng.conn_mgr.state = ConnectionState.CONNECTED if initial_running else ConnectionState.DISCONNECTED
        eng.chat = MagicMock() if initial_running else None
        eng.session_mgr = MagicMock()
        eng.session_mgr.switch_session.return_value = []
        eng.session_manager = eng.session_mgr
        eng.ttrpg_service = self.ttrpg_svc
        eng.dispatcher = MagicMock()
        eng.invalidate_live_session = MagicMock()
        eng.request_live_session_rotation = MagicMock(return_value=True)
        eng.game_engine = None
        eng.game_mgr = None
        eng.user_memory = None
        eng._text_queue = asyncio.Queue()
        eng.send_text = AsyncMock()

        def fake_stop():
            eng.is_running = False
            eng._active_pipeline_mode = None
            eng.conn_mgr.state = ConnectionState.DISCONNECTED
            eng.chat = None

        eng.stop = MagicMock(side_effect=fake_stop)

        bridge = GuiBridge.__new__(GuiBridge)
        bridge._config_lock = __import__("threading").RLock()
        bridge._config = {
            "api": {"agent_name": "Aether", "pipeline_mode": "live"},
            "audio": {"voice_biometrics": {"enabled": False, "threshold": 0.4}},
        }
        bridge._config_path = os.path.join(self.tmp_dir, "config.json")
        bridge._engine = eng
        bridge._loop = asyncio.new_event_loop()
        bridge.ttrpg_service = self.ttrpg_svc
        bridge._on_engine_event = MagicMock()
        bridge.save_voice_biometrics_settings = MagicMock()

        def fake_start_assistant():
            eng.is_running = True
            eng._active_pipeline_mode = "modular" if eng.is_ttrpg_session_active() else "live"
            eng.conn_mgr.state = ConnectionState.CONNECTED
            eng.chat = MagicMock()
            return {"success": True, "status": "starting"}

        bridge.start_assistant = MagicMock(side_effect=fake_start_assistant)
        bridge.send_workspace_message = MagicMock(return_value={"success": True, "session_id": "campaign_elf_hunt"})
        return bridge, eng

    def test_start_adventure_from_stopped_does_not_stop_and_binds_campaign_before_start(self):
        bridge, eng = self._make_bridge_with_stub_engine(initial_running=False, initial_mode=None)
        try:
            res = bridge.start_or_resume_ttrpg_campaign("gemini-4-hydrogen:medium")
            self.assertTrue(res["success"])
            self.assertTrue(eng.is_ttrpg_session_active())
            self.assertEqual(eng.active_workspace_type, "campaign")
            self.assertEqual(eng.active_workspace_context_id, "elf_hunt")
            eng.stop.assert_not_called()
            bridge.start_assistant.assert_called_once()
            bridge.send_workspace_message.assert_called_once()
            ws_type, ctx_id, prompt = bridge.send_workspace_message.call_args[0]
            self.assertEqual(ws_type, "campaign")
            self.assertEqual(ctx_id, "elf_hunt")
            self.assertIn("Elf Hunt", prompt)
        finally:
            bridge._loop.close()

    def test_start_adventure_switches_cleanly_from_running_live_mode(self):
        bridge, eng = self._make_bridge_with_stub_engine(initial_running=True, initial_mode="live")
        try:
            res = bridge.start_or_resume_ttrpg_campaign("gemini-4-hydrogen:medium")
            self.assertTrue(res["success"])
            eng.stop.assert_called_once()
            bridge.start_assistant.assert_called_once()
            self.assertEqual(eng._active_pipeline_mode, "modular")
            bridge.send_workspace_message.assert_called_once()
        finally:
            bridge._loop.close()

    def test_start_adventure_returns_error_when_no_campaign_exists(self):
        self.ttrpg_svc.manifest["active_campaign"] = ""
        self.ttrpg_svc.manifest["campaigns"] = {}
        bridge, eng = self._make_bridge_with_stub_engine(initial_running=False, initial_mode=None)
        try:
            res = bridge.start_or_resume_ttrpg_campaign()
            self.assertFalse(res["success"])
            self.assertIn("No campaign", res["error"])
            bridge.start_assistant.assert_not_called()
        finally:
            bridge._loop.close()


class TestTtrpgEngineQueueAndRouting(unittest.IsolatedAsyncioTestCase):
    async def test_send_text_forces_modular_mode_when_ttrpg_active_even_if_stale_live_flag(self):
        eng = AetherEngine.__new__(AetherEngine)
        eng.is_running = True
        eng._ttrpg_session_active = True
        eng._active_pipeline_mode = "live"  # stale flag
        eng.session = None
        eng.conn_mgr = MagicMock()
        eng.conn_mgr.state = ConnectionState.DISCONNECTED
        eng.config_getter = lambda: {"api": {"pipeline_mode": "live"}}
        eng._text_queue = asyncio.Queue()
        eng._ensure_live_connected = AsyncMock()
        eng.notify = MagicMock()
        eng.active_workspace_type = "campaign"
        eng.active_workspace_context_id = "elf_hunt"

        await eng.send_text("Begin our adventure!")
        eng._ensure_live_connected.assert_not_awaited()
        queued = await eng._text_queue.get()
        self.assertEqual(queued, "Begin our adventure!")

    async def test_start_drains_empty_sentinels_and_sets_modular_for_ttrpg(self):
        eng = AetherEngine.__new__(AetherEngine)
        eng.is_running = False
        eng._stop_requested = False
        eng._ttrpg_session_active = True
        eng._active_pipeline_mode = "live"
        eng._text_queue = asyncio.Queue()
        eng._text_queue.put_nowait("")
        eng._text_queue.put_nowait("real prompt")
        eng.config_getter = lambda: {"api": {"pipeline_mode": "live"}}
        eng.is_on_demand_mode = lambda: False
        eng.notify = MagicMock()
        eng.hotkey_manager = MagicMock()
        eng.startup_runner = None
        eng.reflexion_engine = None
        eng.steam_service = None
        eng.notification_watcher = None
        eng.email_monitor = None
        eng.livestream_service = None
        eng._run = AsyncMock()

        loop = asyncio.get_running_loop()
        eng.start(loop)
        await asyncio.sleep(0)

        self.assertEqual(eng._active_pipeline_mode, "modular")
        self.assertEqual(eng._text_queue.qsize(), 1)
        self.assertEqual(eng._text_queue.get_nowait(), "real prompt")

    async def test_handle_voice_transcript_routes_chat_event_to_campaign_workspace(self):
        tmp_dir = tempfile.mkdtemp(prefix="ttrpg_voice_route_")
        try:
            svc = TTRPGService(data_dir=tmp_dir, manifest_path=os.path.join(tmp_dir, "campaigns.json"))
            svc.create_campaign(title="Elf Hunt", system_preset="dnd_5e", campaign_id="elf_hunt")
            svc.active_state["characters"] = {
                "char_01": {"id": "char_01", "name": "Joe", "archetype": "Fighter 1"}
            }
            eng = AetherEngine.__new__(AetherEngine)
            eng.active_workspace_type = "campaign"
            eng.active_workspace_context_id = "elf_hunt"
            eng.ttrpg_service = svc
            eng.config_getter = lambda: {}
            eng._execute_turn_modular = MagicMock(return_value=None)
            eng.chat = AsyncMock()
            eng.chat.send_message_async.return_value = MagicMock(text="The tavern door creaks open.")
            eng.notify = MagicMock()
            eng.speak_tts = AsyncMock()

            handled = await eng._handle_voice_transcript("Joe opens the tavern door.")
            self.assertTrue(handled)
            chat_calls = [c[0][1] for c in eng.notify.call_args_list if c[0][0] == "chat_event"]
            self.assertTrue(chat_calls)
            evt = chat_calls[-1]
            self.assertEqual(evt["type"], "assistant")
            self.assertEqual(evt["workspace_type"], "campaign")
            self.assertEqual(evt["context_id"], "elf_hunt")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_roleplay_js_and_modal_js_include_prepped_max_badges(self):
        with open("ui/static/roleplay.js", encoding="utf-8") as f:
            rp_js = f.read()
        self.assertIn("PREPPED", rp_js)
        self.assertIn("${prepLeveledCount}/${maxPrepAllowed} PREPPED", rp_js)
        with open("ui/static/edit_character_modal.js", encoding="utf-8") as f:
            modal_js = f.read()
        self.assertIn("badge.innerText = `${count} / ${maxAllowed}`", modal_js)


if __name__ == "__main__":
    unittest.main()
