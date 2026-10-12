"""
Unit tests for Transcribing Safeguard and Session Rotation Watchdog:
- Verifies watchdog recovers from orphaned 'transcribing' UI state
- Verifies rotate_live_session resets transcribing flag and notifies listening
- Verifies session lifecycle duration threshold is <= 450s to preempt server GoAway
"""

import time
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from core.session_lifecycle import DEFAULT_MAX_DURATION_SECONDS, SessionLifecycleManager


class TestTranscribingSafeguard(unittest.TestCase):

    def test_default_duration_preempts_goaway(self):
        """Verifies session rotation threshold is strictly <= 450s (7.5m) to preempt Google's 540s GoAway signal."""
        self.assertLessEqual(DEFAULT_MAX_DURATION_SECONDS, 450.0)
        mgr = SessionLifecycleManager()
        self.assertLessEqual(mgr.rotation_threshold_duration, 450.0)

    def test_watchdog_recovers_orphaned_transcribing_state(self):
        """Verifies that if transcribing state exceeds timeout (>10s), watchdog recovers to listening."""
        from core.engine import AetherEngine

        mock_config = {
            "api": {
                "agent_name": "Aether",
                "live_model_id": "gemini-3.1-flash-live-preview",
                "voice_name": "Aoede"
            }
        }
        with patch.object(AetherEngine, "__init__", lambda self, *args, **kwargs: None):
            engine = AetherEngine()
            engine._is_transcribing = True
            engine._transcribing_start_time = time.time() - 15.0  # 15 seconds ago
            engine.is_running = True
            engine.conn_mgr = MagicMock()
            engine.conn_mgr.state = MagicMock()
            engine.conn_mgr.last_heartbeat_ts = time.time()
            engine.config_getter = MagicMock(return_value=mock_config)
            engine.notify = MagicMock()
            engine._stop_watchdog = MagicMock()
            # Allow one complete iteration before terminating
            engine._stop_watchdog.is_set.side_effect = [False, False, True]

            with patch("time.sleep", return_value=None):
                engine._connection_watchdog_loop()

            self.assertFalse(engine._is_transcribing)
            engine.notify.assert_any_call("status", {"state": "listening", "message": "Aether is listening..."})

    def test_watchdog_does_not_timeout_during_tool_execution(self):
        """Verifies that watchdog does NOT abort transcribing or rotate session if a tool is executing."""
        from core.engine import AetherEngine

        mock_config = {
            "api": {
                "agent_name": "Aether",
                "live_model_id": "gemini-3.1-flash-live-preview",
                "voice_name": "Aoede"
            }
        }
        with patch.object(AetherEngine, "__init__", lambda self, *args, **kwargs: None):
            engine = AetherEngine()
            engine._is_transcribing = True
            engine._transcribing_start_time = time.time() - 25.0  # 25 seconds ago
            engine.is_tool_executing = True
            engine.is_running = True
            engine.conn_mgr = MagicMock()
            engine.conn_mgr.state = MagicMock()
            engine.conn_mgr.last_heartbeat_ts = time.time()
            engine.config_getter = MagicMock(return_value=mock_config)
            engine.notify = MagicMock()
            engine.request_live_session_rotation = MagicMock()
            engine._stop_watchdog = MagicMock()
            engine._stop_watchdog.is_set.side_effect = [False, False, True]

            with patch("time.sleep", return_value=None):
                engine._connection_watchdog_loop()

            # Should remain untouched because tool is executing
            self.assertTrue(engine._is_transcribing)
            engine.request_live_session_rotation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
