import unittest
import time
from unittest.mock import MagicMock, patch
from core.connection_manager import LiveConnectionManager, ConnectionState
from core.engine import AetherEngine


class TestLiveConnectionManager(unittest.TestCase):
    def test_state_transitions(self):
        mgr = LiveConnectionManager()
        self.assertEqual(mgr.state, ConnectionState.DISCONNECTED)

        mgr.set_connected()
        self.assertEqual(mgr.state, ConnectionState.CONNECTED)
        self.assertEqual(mgr.retry_count, 0)

        mgr.set_disconnected(reason="Simulated Wi-Fi Drop")
        self.assertEqual(mgr.state, ConnectionState.DISCONNECTED)

    def test_backoff_delays(self):
        mgr = LiveConnectionManager(base_delay=1.0, max_delay=10.0, jitter_factor=0.0)
        
        # Attempt 1 -> 1.0s
        d1 = mgr.compute_next_backoff()
        self.assertEqual(d1, 1.0)
        self.assertEqual(mgr.state, ConnectionState.RECONNECTING)
        self.assertEqual(mgr.retry_count, 1)

        # Attempt 2 -> 2.0s
        d2 = mgr.compute_next_backoff()
        self.assertEqual(d2, 2.0)

        # Attempt 3 -> 4.0s
        d3 = mgr.compute_next_backoff()
        self.assertEqual(d3, 4.0)

        # Confirm max_retries limit
        mgr.retry_count = 10
        self.assertFalse(mgr.should_attempt_reconnect())

    def test_record_activity_updates_heartbeat(self):
        mgr = LiveConnectionManager()
        mgr.set_connected()
        initial_hb = mgr.last_heartbeat_ts
        time.sleep(0.02)
        mgr.record_activity()
        self.assertGreater(mgr.last_heartbeat_ts, initial_hb)

    def test_engine_reconnect_and_rehydration(self):
        engine = AetherEngine()
        engine.turn_history = [
            {"role": "user", "content": "Hello Aether, look up Dr. John Smith"},
            {"role": "assistant", "content": "I cannot fulfill requests involving personal information of private individuals."}
        ]
        mock_session = MagicMock()
        with patch.object(engine, "_establish_raw_live_stream", return_value=mock_session):
            engine.reconnect_live_session(reason="Unit Test Recovery")
            self.assertEqual(engine.conn_mgr.state, ConnectionState.CONNECTED)
            self.assertEqual(engine.live_session, mock_session)
            self.assertTrue(mock_session.send_client_content.called)
            sent_turns = mock_session.send_client_content.call_args.kwargs.get("turns", [])
            self.assertEqual(len(sent_turns), 2)
            self.assertNotIn("I cannot fulfill", sent_turns[1]["content"])

    def test_receive_loop_handles_socket_drop(self):
        engine = AetherEngine()
        engine.live_session = MagicMock()
        engine.conn_mgr.set_connected()

        def side_effect_drop():
            engine._stop_requested = True
            raise ConnectionResetError("Simulated Wi-Fi adapter reset")

        with patch.object(engine, "_read_next_live_message", side_effect=side_effect_drop):
            engine._receive_loop()

        self.assertEqual(engine.conn_mgr.state, ConnectionState.DISCONNECTED)


if __name__ == "__main__":
    unittest.main()
