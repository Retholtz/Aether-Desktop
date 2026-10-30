import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from core.media_controller_service import MediaControllerService, MediaTrackInfo
from tools.media_tools import register_media_tools
from core.game_engine import GameEngine


class TestMediaControllerService(unittest.TestCase):
    def setUp(self):
        self.service = MediaControllerService(verbal_ack=True)

    def test_status_mapping(self):
        self.assertEqual(self.service._parse_playback_status(4), "Playing")
        self.assertEqual(self.service._parse_playback_status(5), "Paused")
        self.assertEqual(self.service._parse_playback_status(0), "Closed")
        self.assertEqual(self.service._parse_playback_status(99), "Unknown")

    @patch("core.media_controller_service.WINRT_MEDIA_AVAILABLE", True)
    def test_get_current_media_info_success(self):
        mock_props = MagicMock()
        mock_props.title = "Stardust"
        mock_props.artist = "Synthwave Collective"
        mock_props.album_title = "Neon Skies"

        mock_playback = MagicMock()
        mock_playback.playback_status = 4  # Playing

        mock_session = MagicMock()
        mock_session.get_playback_info.return_value = mock_playback
        mock_session.try_get_media_properties_async = AsyncMock(return_value=mock_props)
        mock_session.source_app_user_model_id = "Spotify.exe"

        self.service._resolve_session = AsyncMock(return_value=mock_session)

        info = asyncio.run(self.service.get_current_media_info())
        self.assertIsNotNone(info)
        self.assertEqual(info.title, "Stardust")
        self.assertEqual(info.artist, "Synthwave Collective")
        self.assertEqual(info.playback_status, "Playing")
        self.assertEqual(info.source_app_id, "Spotify.exe")

    @patch("core.media_controller_service.WINRT_MEDIA_AVAILABLE", True)
    def test_execute_transport_commands(self):
        mock_session = MagicMock()
        mock_session.try_play_async = AsyncMock(return_value=True)
        mock_session.try_pause_async = AsyncMock(return_value=True)
        mock_session.try_skip_next_async = AsyncMock(return_value=True)
        mock_session.try_skip_previous_async = AsyncMock(return_value=True)

        self.service._resolve_session = AsyncMock(return_value=mock_session)

        # Test Play
        success, msg = asyncio.run(self.service.execute_transport_command("play"))
        self.assertTrue(success)
        mock_session.try_play_async.assert_called_once()

        # Test Pause
        success, msg = asyncio.run(self.service.execute_transport_command("pause"))
        self.assertTrue(success)
        mock_session.try_pause_async.assert_called_once()

        # Test Next
        success, msg = asyncio.run(self.service.execute_transport_command("next"))
        self.assertTrue(success)
        mock_session.try_skip_next_async.assert_called_once()

        # Test Previous
        success, msg = asyncio.run(self.service.execute_transport_command("previous"))
        self.assertTrue(success)
        mock_session.try_skip_previous_async.assert_called_once()

    @patch("core.media_controller_service.WINRT_MEDIA_AVAILABLE", True)
    def test_execute_command_no_session(self):
        self.service._resolve_session = AsyncMock(return_value=None)
        success, msg = asyncio.run(self.service.execute_transport_command("play", fallback_to_media_key=False))
        self.assertFalse(success)
        self.assertIn("No active media playback session", msg)

    @patch("core.media_controller_service.WINRT_MEDIA_AVAILABLE", True)
    def test_execute_command_hardware_key_fallback(self):
        self.service._resolve_session = AsyncMock(return_value=None)
        with patch.object(self.service, "_send_media_key", return_value=True) as mock_key:
            success, msg = asyncio.run(self.service.execute_transport_command("next", fallback_to_media_key=True))
            self.assertTrue(success)
            self.assertEqual(msg, "Skipped to next track.")
            mock_key.assert_called_once_with("next")

    @patch("core.media_controller_service.WINRT_MEDIA_AVAILABLE", False)
    def test_service_unsupported_platform(self):
        success, msg = asyncio.run(self.service.execute_transport_command("play", fallback_to_media_key=False))
        self.assertFalse(success)
        self.assertIn("not available on this platform", msg)

        info = asyncio.run(self.service.get_current_media_info())
        self.assertIsNone(info)

    @patch("ctypes.windll.user32.SendInput", return_value=2)
    def test_send_media_key_direct(self, mock_send_input):
        res = self.service._send_media_key("next")
        self.assertTrue(res)
        self.assertEqual(mock_send_input.call_count, 1)

        mock_send_input.reset_mock()
        res = self.service._send_media_key("pause")
        self.assertTrue(res)
        self.assertEqual(mock_send_input.call_count, 1)

    @patch("core.media_controller_service.WINRT_MEDIA_AVAILABLE", True)
    def test_toggle_command(self):
        mock_session = MagicMock()
        mock_session.try_toggle_play_pause_async = AsyncMock(return_value=True)
        self.service._resolve_session = AsyncMock(return_value=mock_session)

        success, msg = asyncio.run(self.service.execute_transport_command("toggle"))
        self.assertTrue(success)
        mock_session.try_toggle_play_pause_async.assert_called_once()

    @patch("core.media_controller_service.WINRT_MEDIA_AVAILABLE", True)
    def test_unsupported_command(self):
        mock_session = MagicMock()
        self.service._resolve_session = AsyncMock(return_value=mock_session)

        success, msg = asyncio.run(self.service.execute_transport_command("fast_forward"))
        self.assertFalse(success)
        self.assertIn("Unsupported media command", msg)

    @patch("core.media_controller_service.WINRT_MEDIA_AVAILABLE", True)
    def test_media_tools_registration(self):
        mock_dispatcher = MagicMock()
        tools_registered = {}

        def mock_reg(name, description, parameters, handler):
            tools_registered[name] = handler

        mock_dispatcher.register_tool.side_effect = mock_reg
        register_media_tools(mock_dispatcher, self.service)

        self.assertIn("get_current_playing_media", tools_registered)
        self.assertIn("control_media_playback", tools_registered)

        # Test tool handlers
        self.service.get_current_media_info = AsyncMock(return_value=MediaTrackInfo(
            title="Blinding Lights", artist="The Weeknd", playback_status="Playing"
        ))
        res = asyncio.run(tools_registered["get_current_playing_media"]())
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["track"]["title"], "Blinding Lights")

        self.service.execute_transport_command = AsyncMock(return_value=(True, "Paused playback."))
        res = asyncio.run(tools_registered["control_media_playback"]("pause"))
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["message"], "Paused playback.")

    def test_fast_path_voice_interceptions(self):
        engine = GameEngine()

        # Track query
        res = engine.intercept_voice_turn("what song is this", agent_name="Aether")
        self.assertTrue(res.handled)
        self.assertEqual(res.action_type, "media_query")
        self.assertTrue(res.skip_llm)

        res = engine.intercept_voice_turn("what's playing", agent_name="Aether")
        self.assertTrue(res.handled)
        self.assertEqual(res.action_type, "media_query")

        # Pause
        res = engine.intercept_voice_turn("pause music", agent_name="Aether")
        self.assertTrue(res.handled)
        self.assertEqual(res.action_type, "media_pause")

        res = engine.intercept_voice_turn("pause", agent_name="Aether")
        self.assertTrue(res.handled)
        self.assertEqual(res.action_type, "media_pause")

        # Resume / Play
        res = engine.intercept_voice_turn("resume track", agent_name="Aether")
        self.assertTrue(res.handled)
        self.assertEqual(res.action_type, "media_play")

        res = engine.intercept_voice_turn("play music", agent_name="Aether")
        self.assertTrue(res.handled)
        self.assertEqual(res.action_type, "media_play")

        # Skip / Next
        res = engine.intercept_voice_turn("next song", agent_name="Aether")
        self.assertTrue(res.handled)
        self.assertEqual(res.action_type, "media_next")

        res = engine.intercept_voice_turn("skip", agent_name="Aether")
        self.assertTrue(res.handled)
        self.assertEqual(res.action_type, "media_next")

        # Previous
        res = engine.intercept_voice_turn("previous track", agent_name="Aether")
        self.assertTrue(res.handled)
        self.assertEqual(res.action_type, "media_previous")

        res = engine.intercept_voice_turn("prev song", agent_name="Aether")
        self.assertTrue(res.handled)
        self.assertEqual(res.action_type, "media_previous")

    def test_parse_media_title(self):
        # YouTube Music in Chrome with bullet
        res = self.service._parse_media_title("Faded • Alan Walker - YouTube Music - Google Chrome", "chrome.exe")
        self.assertIsNotNone(res)
        info, kind = res
        self.assertEqual(info.title, "Faded")
        self.assertEqual(info.artist, "Alan Walker")
        self.assertEqual(kind, "youtube_music")

        # YouTube Music PWA / Widget
        res = self.service._parse_media_title("Faded • Alan Walker", "chrome.exe")
        self.assertIsNotNone(res)
        info, kind = res
        self.assertEqual(info.title, "Faded")
        self.assertEqual(info.artist, "Alan Walker")

        # YouTube Music idle tab
        res = self.service._parse_media_title("YouTube Music - Google Chrome", "chrome.exe")
        self.assertIsNone(res)

        # Spotify
        res = self.service._parse_media_title("Queen - Bohemian Rhapsody", "spotify.exe")
        self.assertIsNotNone(res)
        info, kind = res
        self.assertEqual(info.title, "Bohemian Rhapsody")
        self.assertEqual(info.artist, "Queen")
        self.assertEqual(kind, "spotify")

    @patch("win32gui.PostMessage")
    def test_send_media_command_with_hwnd(self, mock_post):
        with patch.object(self.service, "_send_media_key", return_value=True):
            res = self.service._send_media_command("next", target_hwnd=12345)
            self.assertTrue(res)
            mock_post.assert_called_once_with(12345, 0x0319, 12345, 11 << 16)

    def test_core_game_tool_names_exposure(self):
        self.assertIn("get_current_playing_media", GameEngine.CORE_GAME_TOOL_NAMES)
        self.assertIn("control_media_playback", GameEngine.CORE_GAME_TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()

