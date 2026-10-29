import os
import asyncio
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
from core.audio_ducking_service import AudioDuckingService

class TestAudioDuckingService(unittest.TestCase):
    def setUp(self):
        self.service = AudioDuckingService(
            enabled=True,
            duck_percentage=60,
            restore_delay_ms=0
        )

    def test_settings_update(self):
        self.service.update_settings(enabled=False, duck_percentage=80)
        self.assertFalse(self.service.enabled)
        self.assertEqual(self.service.duck_percentage, 80)

        # Clamping
        self.service.update_settings(duck_percentage=150)
        self.assertEqual(self.service.duck_percentage, 100)

        self.service.update_settings(duck_percentage=-20)
        self.assertEqual(self.service.duck_percentage, 0)

    @patch("core.audio_ducking_service.PYCAW_AVAILABLE", True)
    @patch("core.audio_ducking_service.comtypes")
    @patch("core.audio_ducking_service.AudioUtilities")
    def test_duck_and_restore_cycle(self, mock_audio_utils, mock_comtypes):
        # Mock Game Session (PID 9999)
        mock_game_vol = MagicMock()
        mock_game_vol.GetMasterVolume.return_value = 1.0
        
        mock_game_session = MagicMock()
        mock_game_session.Process = MagicMock()
        mock_game_session.Process.pid = 9999
        mock_game_session._ctl.QueryInterface.return_value = mock_game_vol

        # Mock Aether Session (Own PID)
        mock_aether_vol = MagicMock()
        mock_aether_session = MagicMock()
        mock_aether_session.Process = MagicMock()
        mock_aether_session.Process.pid = self.service.own_pid
        mock_aether_session._ctl.QueryInterface.return_value = mock_aether_vol

        mock_audio_utils.GetAllSessions.return_value = [mock_game_session, mock_aether_session]

        # 1. Duck
        asyncio.run(self.service.duck())
        self.assertTrue(self.service._is_ducked)
        # 60% reduction -> volume should become 0.4
        mock_game_vol.SetMasterVolume.assert_called_with(0.4, None)
        # Aether must NOT be altered
        mock_aether_vol.SetMasterVolume.assert_not_called()

        # 2. Unduck
        asyncio.run(self.service.unduck())
        self.assertFalse(self.service._is_ducked)
        # Game should be restored to 1.0
        mock_game_vol.SetMasterVolume.assert_called_with(1.0, None)

    @patch("core.audio_ducking_service.PYCAW_AVAILABLE", True)
    @patch("core.audio_ducking_service.comtypes")
    @patch("core.audio_ducking_service.AudioUtilities")
    def test_disabled_service_does_not_duck(self, mock_audio_utils, mock_comtypes):
        self.service.enabled = False
        asyncio.run(self.service.duck())
        self.assertFalse(self.service._is_ducked)
        mock_audio_utils.GetAllSessions.assert_not_called()

    @patch("core.audio_ducking_service.PYCAW_AVAILABLE", False)
    def test_unsupported_environment(self):
        self.assertFalse(self.service.is_supported())
        asyncio.run(self.service.duck())
        self.assertFalse(self.service._is_ducked)

    def test_gui_bridge_ducking_endpoints(self):
        from core.gui_bridge import GUIBridge
        mock_engine = MagicMock()
        mock_engine.settings = {
            "audio_ducking": {
                "enabled": True,
                "duck_percentage": 60
            }
        }
        mock_ducking_service = MagicMock()
        mock_engine.audio_ducking_service = mock_ducking_service

        bridge = GUIBridge(engine=mock_engine)
        settings = bridge.get_audio_ducking_settings()
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["duck_percentage"], 60)

        update_res = bridge.update_audio_ducking_settings(enabled=False, duck_percentage=45)
        self.assertEqual(update_res["status"], "success")
        self.assertFalse(update_res["settings"]["enabled"])
        self.assertEqual(update_res["settings"]["duck_percentage"], 45)
        mock_ducking_service.update_settings.assert_called_with(enabled=False, duck_percentage=45)
        mock_engine.save_settings.assert_called()

    def test_engine_state_transitions(self):
        from core.engine import AetherEngine
        engine = MagicMock(spec=AetherEngine)
        engine.audio_ducking_service = MagicMock()
        engine.audio_ducking_service.duck = AsyncMock()
        engine.audio_ducking_service.unduck = AsyncMock()

        # Bind the actual method to mock engine
        engine._on_engine_state_changed = AetherEngine._on_engine_state_changed.__get__(engine, AetherEngine)

        # Active speech states trigger duck
        for state in ("listening", "speaking", "thinking", "hearing", "transcribing"):
            asyncio.run(engine._on_engine_state_changed(state))
            engine.audio_ducking_service.duck.assert_called()

        # Idle states trigger unduck
        for state in ("idle", "ready", "standby", "disconnected"):
            asyncio.run(engine._on_engine_state_changed(state))
            engine.audio_ducking_service.unduck.assert_called()


if __name__ == "__main__":
    unittest.main()

