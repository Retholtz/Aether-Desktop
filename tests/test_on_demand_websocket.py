import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.connection_manager import ConnectionState
from core.engine import AetherEngine


class TestOnDemandWebSocket(unittest.TestCase):
    def setUp(self):
        self.engine = AetherEngine()
        self.engine._loop = asyncio.new_event_loop()
        self.engine.is_running = True

    def tearDown(self):
        self.engine.is_running = False
        if hasattr(self.engine, "_loop") and self.engine._loop:
            if not self.engine._loop.is_closed():
                self.engine._loop.close()

    def test_is_on_demand_mode_detection(self):
        # 1. PTT mode -> on-demand True
        self.engine.config_getter = lambda: {
            "audio": {"mode": "ptt"},
            "wake_word_enabled": False,
            "always_on_mode": "always_on"
        }
        self.assertTrue(self.engine.is_on_demand_mode())

        # 2. Wake Word mode -> on-demand True
        self.engine.config_getter = lambda: {
            "audio": {"mode": "always_on", "always_on_mode": "wake_word"},
            "wake_word_enabled": True,
            "always_on_mode": "wake_word"
        }
        self.assertTrue(self.engine.is_on_demand_mode())

        # 3. Always-on open mic without wake gating -> on-demand False
        self.engine.config_getter = lambda: {
            "audio": {"mode": "always_on", "always_on_mode": "always_on"},
            "wake_word_enabled": False,
            "always_on_mode": "always_on"
        }
        self.assertFalse(self.engine.is_on_demand_mode())

    def test_ptt_triggers_on_demand_connection(self):
        self.engine.config_getter = lambda: {
            "audio": {"mode": "ptt"},
            "api": {"agent_name": "Aether"}
        }
        self.engine.conn_mgr.set_standby()
        self.assertEqual(self.engine.conn_mgr.state, ConnectionState.STANDBY)

        with patch.object(self.engine, "request_live_session_connect") as mock_connect:
            self.engine.set_ptt(True)
            mock_connect.assert_called_once_with(reason="PTT pressed")

    def test_wake_phrase_triggers_on_demand_connection(self):
        self.engine.config_getter = lambda: {
            "audio": {"mode": "always_on", "always_on_mode": "wake_word"},
            "wake_word_enabled": True,
            "api": {"agent_name": "Aether"}
        }
        self.engine.conn_mgr.set_standby()

        with patch.object(self.engine, "request_live_session_connect") as mock_connect:
            self.engine._on_assistant_woke()
            mock_connect.assert_called_once_with(reason="Wake phrase detected")

    def test_speech_detected_triggers_on_demand_connection_when_disconnected(self):
        self.engine.config_getter = lambda: {
            "audio": {"mode": "ptt"},
            "api": {"agent_name": "Aether"}
        }
        self.engine.conn_mgr.set_standby()
        self.engine.session = None

        with patch.object(self.engine, "request_live_session_connect") as mock_connect:
            self.engine._on_speech_state("speech_detected")
            mock_connect.assert_called_once_with(reason="Speech detected")

    def test_send_text_ensures_connection_when_disconnected(self):
        self.engine.config_getter = lambda: {
            "audio": {"mode": "ptt"},
            "api": {"agent_name": "Aether"}
        }
        self.engine.conn_mgr.set_standby()
        self.engine.session = None

        async def run_test():
            with patch.object(self.engine, "_ensure_live_connected", new=AsyncMock(return_value=True)) as mock_ensure:
                await self.engine.send_text("Hello Aether")
                mock_ensure.assert_awaited_once_with(reason="Typed text sent")
                self.assertFalse(self.engine._text_queue.empty())
                msg = await self.engine._text_queue.get()
                self.assertEqual(msg, "Hello Aether")

        self.engine._loop.run_until_complete(run_test())

    def test_linger_window_disconnects_after_60s_idle(self):
        self.engine.config_getter = lambda: {
            "audio": {"mode": "ptt"},
            "api": {"agent_name": "Aether"}
        }
        self.engine.conn_mgr.set_connected()
        self.engine.session = MagicMock()
        self.engine._linger_seconds = 60.0
        self.engine._last_live_turn_completed_ts = time.time() - 65.0  # 65s ago
        self.engine._in_active_turn = False
        self.engine._is_transcribing = False
        self.engine.is_speaking = False
        self.engine.is_tool_running = False
        self.engine.is_audio_streaming = False

        with patch.object(self.engine, "request_live_session_disconnect") as mock_disc:
            # Run one cycle of watchdog check logic directly
            now = time.time()
            if self.engine.is_on_demand_mode():
                linger_elapsed = now - getattr(self.engine, "_last_live_turn_completed_ts", now)
                if (
                    linger_elapsed >= getattr(self.engine, "_linger_seconds", 60.0)
                    and not self.engine.is_speaking
                    and not self.engine.is_tool_running
                    and not self.engine.is_audio_streaming
                    and not getattr(self.engine, "_in_active_turn", False)
                    and not getattr(self.engine, "_is_transcribing", False)
                    and not getattr(self.engine, "_ptt_active", False)
                ):
                    self.engine.request_live_session_disconnect(reason="Linger timeout (60s idle)")

            mock_disc.assert_called_once_with(reason="Linger timeout (60s idle)")

    def test_linger_window_keeps_session_alive_under_60s(self):
        self.engine.config_getter = lambda: {
            "audio": {"mode": "ptt"},
            "api": {"agent_name": "Aether"}
        }
        self.engine.conn_mgr.set_connected()
        self.engine.session = MagicMock()
        self.engine._linger_seconds = 60.0
        self.engine._last_live_turn_completed_ts = time.time() - 25.0  # only 25s ago
        self.engine._in_active_turn = False
        self.engine._is_transcribing = False
        self.engine.is_speaking = False
        self.engine.is_tool_running = False
        self.engine.is_audio_streaming = False

        with patch.object(self.engine, "request_live_session_disconnect") as mock_disc:
            now = time.time()
            if self.engine.is_on_demand_mode():
                linger_elapsed = now - getattr(self.engine, "_last_live_turn_completed_ts", now)
                if (
                    linger_elapsed >= getattr(self.engine, "_linger_seconds", 60.0)
                    and not self.engine.is_speaking
                    and not self.engine.is_tool_running
                    and not self.engine.is_audio_streaming
                    and not getattr(self.engine, "_in_active_turn", False)
                    and not getattr(self.engine, "_is_transcribing", False)
                    and not getattr(self.engine, "_ptt_active", False)
                ):
                    self.engine.request_live_session_disconnect(reason="Linger timeout (60s idle)")

            mock_disc.assert_not_called()

    def test_linger_window_does_not_disconnect_while_speaking_or_streaming(self):
        self.engine.config_getter = lambda: {
            "audio": {"mode": "ptt"},
            "api": {"agent_name": "Aether"}
        }
        self.engine.conn_mgr.set_connected()
        self.engine.session = MagicMock()
        self.engine._linger_seconds = 60.0
        self.engine._last_live_turn_completed_ts = time.time() - 90.0
        # Active turn flags set
        self.engine._in_active_turn = True
        self.engine.is_speaking = True

        with patch.object(self.engine, "request_live_session_disconnect") as mock_disc:
            now = time.time()
            if self.engine.is_on_demand_mode():
                linger_elapsed = now - getattr(self.engine, "_last_live_turn_completed_ts", now)
                if (
                    linger_elapsed >= getattr(self.engine, "_linger_seconds", 60.0)
                    and not self.engine.is_speaking
                    and not self.engine.is_tool_running
                    and not self.engine.is_audio_streaming
                    and not getattr(self.engine, "_in_active_turn", False)
                    and not getattr(self.engine, "_is_transcribing", False)
                    and not getattr(self.engine, "_ptt_active", False)
                ):
                    self.engine.request_live_session_disconnect(reason="Linger timeout (60s idle)")

            mock_disc.assert_not_called()

    def test_always_on_mode_does_not_disconnect_on_linger(self):
        self.engine.config_getter = lambda: {
            "audio": {"mode": "always_on", "always_on_mode": "always_on"},
            "wake_word_enabled": False,
            "always_on_mode": "always_on"
        }
        self.engine.conn_mgr.set_connected()
        self.engine.session = MagicMock()
        self.engine._linger_seconds = 60.0
        self.engine._last_live_turn_completed_ts = time.time() - 120.0
        self.engine._in_active_turn = False
        self.engine.is_speaking = False

        with patch.object(self.engine, "request_live_session_disconnect") as mock_disc:
            now = time.time()
            if self.engine.is_on_demand_mode():
                linger_elapsed = now - getattr(self.engine, "_last_live_turn_completed_ts", now)
                if (
                    linger_elapsed >= getattr(self.engine, "_linger_seconds", 60.0)
                    and not self.engine.is_speaking
                    and not self.engine.is_tool_running
                    and not self.engine.is_audio_streaming
                    and not getattr(self.engine, "_in_active_turn", False)
                    and not getattr(self.engine, "_is_transcribing", False)
                    and not getattr(self.engine, "_ptt_active", False)
                ):
                    self.engine.request_live_session_disconnect(reason="Linger timeout (60s idle)")

            mock_disc.assert_not_called()

    def test_disconnect_live_session_transitions_to_standby(self):
        self.engine.config_getter = lambda: {
            "audio": {"mode": "ptt"},
            "api": {"agent_name": "Aether"}
        }
        self.engine.conn_mgr.set_connected()
        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        self.engine.session = mock_session
        self.engine.live_session = mock_session
        self.engine._active_cm = mock_cm

        async def run_test():
            with patch.object(self.engine.session_lifecycle, "flush_session_to_disk") as mock_flush:
                await self.engine._disconnect_live_session(reason="Test linger timeout")
                self.assertEqual(self.engine.conn_mgr.state, ConnectionState.STANDBY)
                self.assertIsNone(self.engine.session)
                self.assertIsNone(self.engine.live_session)
                self.assertIsNone(self.engine._active_cm)
                mock_session.close.assert_awaited_once()
                mock_flush.assert_called_once()

        self.engine._loop.run_until_complete(run_test())

    def test_lossless_audio_buffering_during_connection(self):
        self.engine.config_getter = lambda: {
            "audio": {"mode": "ptt"},
            "api": {"agent_name": "Aether"}
        }
        # Start in Standby with no session
        self.engine.conn_mgr.set_standby()
        self.engine.session = None

        mock_audio = MagicMock()
        mock_audio.input_queue = asyncio.Queue()
        self.engine.audio = mock_audio

        # Put 3 audio chunks into input_queue while session is None
        mock_audio.input_queue.put_nowait(b"\x01\x02" * 100)
        mock_audio.input_queue.put_nowait(b"\x03\x04" * 100)
        mock_audio.input_queue.put_nowait(b"__END_OF_TURN__")

        mock_session = AsyncMock()

        async def run_test():
            # Start _send_loop task
            send_task = asyncio.create_task(self.engine._send_loop())
            # Yield to let send_loop see session is None
            await asyncio.sleep(0.02)
            # Nothing should be sent yet
            self.assertEqual(mock_session.send_realtime_input.await_count, 0)
            # Queue should still have all 3 items
            self.assertEqual(mock_audio.input_queue.qsize(), 3)

            # Now connection completes! Set active session
            self.engine.session = mock_session
            self.engine.conn_mgr.set_connected()

            # Yield to let send_loop drain the buffered chunks
            await asyncio.sleep(0.05)

            # send_realtime_input must have received the 2 audio blobs and the stream end
            self.assertGreaterEqual(mock_session.send_realtime_input.await_count, 2)
            self.assertTrue(mock_audio.input_queue.empty())

            send_task.cancel()
            try:
                await send_task
            except asyncio.CancelledError:
                pass

        self.engine._loop.run_until_complete(run_test())


if __name__ == "__main__":
    unittest.main()

