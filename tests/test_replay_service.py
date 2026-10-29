import asyncio
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from core.replay_service import ReplayService
from tools.replay_tools import register_replay_tools
from core.game_engine import GameEngine


class TestReplayService(unittest.TestCase):
    def setUp(self):
        self.service = ReplayService(
            backend="shadowplay",
            spoken_confirmation="Captured."
        )

    @patch("ctypes.windll.user32.keybd_event")
    def test_pulse_shadowplay(self, mock_keybd):
        res = self.service._pulse_shadowplay()
        self.assertTrue(res)
        self.assertEqual(mock_keybd.call_count, 4)

    @patch("ctypes.windll.user32.keybd_event")
    def test_pulse_game_bar(self, mock_keybd):
        res = self.service._pulse_game_bar()
        self.assertTrue(res)
        self.assertEqual(mock_keybd.call_count, 6)

    @patch("aiohttp.ClientSession.ws_connect")
    def test_trigger_obs_replay_success(self, mock_ws_connect):
        self.service.backend = "obs_websocket"

        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(side_effect=[
            {"op": 0, "d": {"rpcVersion": 1}},  # Hello
            {"op": 2},                          # Identified
            {"op": 7, "d": {"requestStatus": {"result": True}}}  # RequestResponse
        ])
        mock_ws_connect.return_value.__aenter__.return_value = mock_ws

        result = asyncio.run(self.service.trigger_clip())
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["spoken_confirmation"], "Captured.")

    @patch.object(ReplayService, "_pulse_shadowplay", return_value=True)
    def test_trigger_clip_dispatches_correctly(self, mock_pulse):
        self.service.backend = "shadowplay"
        res = asyncio.run(self.service.trigger_clip())
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["backend"], "shadowplay")
        mock_pulse.assert_called_once()

    @patch.object(ReplayService, "_pulse_game_bar", return_value=True)
    def test_trigger_clip_game_bar_dispatch(self, mock_pulse):
        self.service.backend = "xbox_game_bar"
        res = asyncio.run(self.service.trigger_clip())
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["backend"], "xbox_game_bar")
        mock_pulse.assert_called_once()

    @patch.object(ReplayService, "_pulse_shadowplay", return_value=True)
    def test_trigger_clip_unknown_backend_fallback(self, mock_pulse):
        self.service.backend = "unsupported_backend"
        res = asyncio.run(self.service.trigger_clip())
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["backend"], "shadowplay")
        mock_pulse.assert_called_once()


class TestReplayTools(unittest.TestCase):
    def test_register_replay_tools(self):
        registered = {}

        class DummyDispatcher:
            def register_tool(self, name, declaration=None, handler=None, **kwargs):
                registered[name] = {
                    "declaration": declaration,
                    "handler": handler or kwargs.get("handler"),
                    "description": kwargs.get("description")
                }

        mock_service = MagicMock(spec=ReplayService)
        mock_service.trigger_clip = AsyncMock(return_value={
            "status": "success",
            "backend": "shadowplay",
            "message": "Replay clip saved.",
            "spoken_confirmation": "Captured."
        })

        dispatcher = DummyDispatcher()
        register_replay_tools(dispatcher, mock_service)

        self.assertIn("trigger_instant_replay", registered)
        handler = registered["trigger_instant_replay"]["handler"]
        res = asyncio.run(handler())
        self.assertEqual(res["status"], "success")
        mock_service.trigger_clip.assert_called_once()


class TestVoiceInterceptionReplay(unittest.TestCase):
    def setUp(self):
        self.game_engine = GameEngine()
        self.mock_manager = MagicMock()
        self.mock_manager.data = {"active_profile": "crimson_desert"}
        self.game_engine.manager = self.mock_manager

    def test_tool_in_core_game_tool_names(self):
        self.assertIn("trigger_instant_replay", GameEngine.CORE_GAME_TOOL_NAMES)

    def test_voice_interception_patterns(self):
        trigger_phrases = [
            "Aether, clip that",
            "clip that",
            "Aether clip that!",
            "please clip that",
            "save clip",
            "Aether, save clip",
            "capture clip",
            "record that",
            "save replay",
            "clip this",
            "Aether, save replay!"
        ]

        for phrase in trigger_phrases:
            with self.subTest(phrase=phrase):
                res = self.game_engine.intercept_voice_turn(phrase, agent_name="Aether")
                self.assertTrue(res.handled, f"Failed for phrase: {phrase}")
                self.assertEqual(res.action_type, "replay_capture")
                self.assertTrue(res.skip_llm)
                self.assertFalse(res.requires_context_reset)
                self.assertEqual(res.tts_message, "Captured.")

    def test_non_replay_voice_phrases(self):
        normal_phrases = [
            "How do I defeat this boss?",
            "What is the weather outside?",
            "Tell me about clipping coupons",
            "Search for audio clips on youtube"
        ]

        for phrase in normal_phrases:
            with self.subTest(phrase=phrase):
                res = self.game_engine.intercept_voice_turn(phrase, agent_name="Aether")
                self.assertNotEqual(res.action_type, "replay_capture")


if __name__ == "__main__":
    unittest.main()
