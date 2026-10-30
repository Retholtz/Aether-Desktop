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
    def test_volume_degradation_prevention(self, mock_audio_utils, mock_comtypes):
        """Verify that degraded volumes (e.g. 3% left by a prior crash) are baselined to 100% and repeated ducks don't cascade."""
        mock_vol = MagicMock()
        mock_vol.GetMasterVolume.return_value = 0.03  # App left at 3% from prior crash

        mock_session = MagicMock()
        mock_session.Process = MagicMock()
        mock_session.Process.pid = 8888
        mock_session._ctl.QueryInterface.return_value = mock_vol

        mock_audio_utils.GetAllSessions.return_value = [mock_session]

        # Duck should baseline degraded volume to 1.0 and set target to 0.4
        asyncio.run(self.service.duck())
        self.assertEqual(self.service._original_volumes[8888], 1.0)
        self.assertEqual(self.service._saved_volumes[8888], 1.0)
        mock_vol.SetMasterVolume.assert_called_with(0.4, None)

        # Repeated duck while already ducked should NOT overwrite _original_volumes
        mock_vol.GetMasterVolume.return_value = 0.4
        self.service.duck_all()
        self.assertEqual(self.service._original_volumes[8888], 1.0)

        # Restore restores cleanly to 1.0
        asyncio.run(self.service.unduck())
        mock_vol.SetMasterVolume.assert_called_with(1.0, None)

    @patch("core.audio_ducking_service.PYCAW_AVAILABLE", True)
    @patch("core.audio_ducking_service.comtypes")
    @patch("core.audio_ducking_service.AudioUtilities")
    def test_restore_all_idempotency_multiple_calls(self, mock_audio_utils, mock_comtypes):
        """Verify that restore_all() executes cleanly and idempotently even when called multiple times."""
        mock_vol = MagicMock()
        mock_vol.GetMasterVolume.return_value = 1.0
        mock_session = MagicMock()
        mock_session.Process = MagicMock()
        mock_session.Process.pid = 7777
        mock_session._ctl.QueryInterface.return_value = mock_vol
        mock_audio_utils.GetAllSessions.return_value = [mock_session]

        self.service.duck_all()
        self.assertTrue(self.service._is_ducked)
        self.assertIn(7777, self.service._original_volumes)

        # Call restore_all multiple times
        self.service.restore_all()
        self.assertFalse(self.service._is_ducked)
        self.assertEqual(len(self.service._original_volumes), 0)
        mock_vol.SetMasterVolume.assert_called_with(1.0, None)

        mock_vol.reset_mock()
        # Second and third calls must be no-ops and safe
        self.service.restore_all()
        self.service.restore_all()
        mock_vol.SetMasterVolume.assert_not_called()

    @patch("core.audio_ducking_service.PYCAW_AVAILABLE", True)
    @patch("core.audio_ducking_service.comtypes")
    @patch("core.audio_ducking_service.AudioUtilities")
    def test_sync_duck_scope_handles_exception_and_keyboard_interrupt(self, mock_audio_utils, mock_comtypes):
        """Verify sync_duck_scope restores volumes upon simulated KeyboardInterrupt or unhandled exception."""
        mock_vol = MagicMock()
        mock_vol.GetMasterVolume.return_value = 0.85
        mock_session = MagicMock()
        mock_session.Process = MagicMock()
        mock_session.Process.pid = 6666
        mock_session._ctl.QueryInterface.return_value = mock_vol
        mock_audio_utils.GetAllSessions.return_value = [mock_session]

        # 1. Test simulated KeyboardInterrupt inside sync_duck_scope
        with self.assertRaises(KeyboardInterrupt):
            with self.service.sync_duck_scope():
                self.assertTrue(self.service._is_ducked)
                self.assertEqual(self.service._original_volumes[6666], 0.85)
                raise KeyboardInterrupt("Simulated Ctrl+C")

        # Must be restored back to baseline volume
        self.assertFalse(self.service._is_ducked)
        self.assertEqual(len(self.service._original_volumes), 0)
        mock_vol.SetMasterVolume.assert_called_with(0.85, None)

        # 2. Test RuntimeError inside sync_duck_scope
        mock_vol.reset_mock()
        with self.assertRaises(RuntimeError):
            with self.service.sync_duck_scope():
                self.assertTrue(self.service._is_ducked)
                raise RuntimeError("Simulated crash")

        self.assertFalse(self.service._is_ducked)
        mock_vol.SetMasterVolume.assert_called_with(0.85, None)

    @patch("core.audio_ducking_service.PYCAW_AVAILABLE", True)
    @patch("core.audio_ducking_service.comtypes")
    @patch("core.audio_ducking_service.AudioUtilities")
    def test_async_duck_scope_handles_cancellation_and_exception(self, mock_audio_utils, mock_comtypes):
        """Verify async duck_scope restores volumes upon exception or task cancellation."""
        mock_vol = MagicMock()
        mock_vol.GetMasterVolume.return_value = 0.90
        mock_session = MagicMock()
        mock_session.Process = MagicMock()
        mock_session.Process.pid = 5555
        mock_session._ctl.QueryInterface.return_value = mock_vol
        mock_audio_utils.GetAllSessions.return_value = [mock_session]

        async def run_failing_scope():
            async with self.service.duck_scope():
                self.assertTrue(self.service._is_ducked)
                raise ValueError("Simulated async task failure")

        with self.assertRaises(ValueError):
            asyncio.run(run_failing_scope())

        self.assertFalse(self.service._is_ducked)
        mock_vol.SetMasterVolume.assert_called_with(0.90, None)

    @patch("core.audio_ducking_service.PYCAW_AVAILABLE", True)
    @patch("core.audio_ducking_service.comtypes")
    @patch("core.audio_ducking_service.AudioUtilities")
    def test_duck_rollback_on_unexpected_error(self, mock_audio_utils, mock_comtypes):
        """Verify that if an error occurs mid-duck, rollback restores any modified sessions."""
        mock_audio_utils.GetAllSessions.side_effect = RuntimeError("WASAPI driver fault")

        self.service.duck_all()
        self.assertFalse(self.service._is_ducked)
        self.assertEqual(len(self.service._original_volumes), 0)

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

        # Bind actual method
        engine._on_engine_state_changed = AetherEngine._on_engine_state_changed.__get__(engine, AetherEngine)

        # Active speech/thinking states trigger duck
        for state in ("speaking", "thinking", "hearing", "transcribing", "executing"):
            asyncio.run(engine._on_engine_state_changed(state))
            engine.audio_ducking_service.duck.assert_called()

        # Idle and listening states trigger unduck
        for state in ("idle", "ready", "standby", "listening", "disconnected"):
            asyncio.run(engine._on_engine_state_changed(state))
            engine.audio_ducking_service.unduck.assert_called()

    def test_troubleshooter_and_sound_guardrails(self):
        from security.whitelist import WhitelistValidator
        res1 = WhitelistValidator.validate_and_launch("control.exe", ["*"], target="/name Microsoft.Troubleshooting")
        self.assertEqual(res1.get("status"), "blocked")

        res2 = WhitelistValidator.validate_and_launch("mmsys.cpl", ["*"])
        self.assertEqual(res2.get("status"), "blocked")

        res3 = WhitelistValidator.validate_and_launch("msdt.exe", ["*"], target="-id AudioPlaybackDiagnostic")
        self.assertEqual(res3.get("status"), "blocked")

        # Whitelist addition should also be rejected
        add_res = WhitelistValidator.add_to_whitelist("mmsys.cpl", [])
        self.assertEqual(add_res.get("status"), "blocked")


if __name__ == "__main__":
    unittest.main()
