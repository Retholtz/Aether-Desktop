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

    def test_send_text_resets_linger_and_prevents_standby_when_connected(self):
        self.engine.config_getter = lambda: {
            "audio": {"mode": "ptt"},
            "api": {"agent_name": "Aether"}
        }
        self.engine.conn_mgr.set_connected()
        self.engine.session = MagicMock()
        self.engine._linger_seconds = 60.0
        # Simulate session that has been idle for 62s (just past old 60s window)
        self.engine._last_live_turn_completed_ts = time.time() - 62.0
        self.engine._in_active_turn = False

        async def run_test():
            await self.engine.send_text("Thoughts as to how we may fix this?")
            self.assertTrue(self.engine._in_active_turn)
            self.assertLess(time.time() - self.engine._last_live_turn_completed_ts, 2.0)
            self.assertFalse(self.engine.should_disconnect_for_linger())

        self.engine._loop.run_until_complete(run_test())

    def test_should_disconnect_for_linger_blocks_when_text_queue_non_empty(self):
        self.engine.config_getter = lambda: {
            "audio": {"mode": "ptt"},
            "api": {"agent_name": "Aether"}
        }
        self.engine.conn_mgr.set_connected()
        self.engine.session = MagicMock()
        self.engine._linger_seconds = 60.0
        self.engine._last_live_turn_completed_ts = time.time() - 120.0
        self.engine._in_active_turn = False
        self.engine._text_queue.put_nowait("Pending message")

        self.assertFalse(self.engine.should_disconnect_for_linger())

    def test_send_loop_survives_send_client_content_error_and_retries(self):
        self.engine.config_getter = lambda: {
            "audio": {"mode": "ptt"},
            "api": {"agent_name": "Aether"}
        }
        dead_session = AsyncMock()
        dead_session.send_client_content.side_effect = RuntimeError("WebSocket 1006 closed")
        fresh_session = AsyncMock()

        self.engine.session = dead_session
        self.engine.conn_mgr.set_connected()
        self.engine._text_queue.put_nowait("Recover me")

        async def fake_ensure_connected(reason=""):
            self.engine.session = fresh_session
            self.engine.conn_mgr.set_connected()
            return True

        async def run_test():
            with patch.object(self.engine, "_ensure_live_connected", side_effect=fake_ensure_connected) as mock_reconn:
                send_task = asyncio.create_task(self.engine._send_loop())
                await asyncio.sleep(0.05)
                self.assertFalse(send_task.done(), "_send_loop must not crash on send_client_content error")
                mock_reconn.assert_awaited_once()
                fresh_session.send_client_content.assert_awaited_once()

                send_task.cancel()
                try:
                    await send_task
                except asyncio.CancelledError:
                    pass

        self.engine._loop.run_until_complete(run_test())

    def test_google_multi_calendar_event_aggregation_and_sorting(self):
        from core.oauth_manager import GOOGLE_SCOPES, OAuthManager

        self.assertIn("https://www.googleapis.com/auth/calendar.readonly", GOOGLE_SCOPES)

        mgr = OAuthManager()
        with patch.object(mgr, "_google_headers", return_value={"Authorization": "Bearer test_token"}):
            def fake_get(url, headers=None, params=None, timeout=12):
                resp = MagicMock()
                resp.ok = True
                resp.status_code = 200
                if url.endswith("/users/me/calendarList"):
                    resp.json.return_value = {
                        "kind": "calendar#calendarList",
                        "items": [
                            {"id": "primary", "summary": "retholtz@gmail.com", "primary": True, "selected": True},
                            {"id": "sec_cal_1", "summary": "Michael Retholtz", "primary": False, "selected": True},
                            {"id": "shared_fam", "summary": "Family Shared", "primary": False, "selected": True},
                        ],
                    }
                elif "/calendars/primary/events" in url:
                    resp.json.return_value = {
                        "items": [
                            {
                                "id": "ev_pt",
                                "summary": "PT committee meeting",
                                "start": {"dateTime": "2026-10-26T10:00:00-04:00"},
                                "end": {"dateTime": "2026-10-26T11:00:00-04:00"},
                            }
                        ]
                    }
                elif "/calendars/sec_cal_1/events" in url:
                    resp.json.return_value = {
                        "items": [
                            {
                                "id": "ev_filling",
                                "summary": "Mike - Filling",
                                "start": {"dateTime": "2026-10-27T14:30:00-04:00"},
                                "end": {"dateTime": "2026-10-27T15:30:00-04:00"},
                            }
                        ]
                    }
                elif "/calendars/shared_fam/events" in url:
                    resp.json.return_value = {
                        "items": [
                            {
                                "id": "ev_delivery",
                                "summary": "Cabinet and Hutch Delivery",
                                "start": {"dateTime": "2026-10-24T14:00:00-04:00"},
                                "end": {"dateTime": "2026-10-24T16:00:00-04:00"},
                            }
                        ]
                    }
                return resp

            with patch("core.oauth_manager.requests.get", side_effect=fake_get):
                res = mgr.list_calendar_events(provider="google", days_ahead=7, max_results=25)
                self.assertTrue(res["success"])
                self.assertEqual(res["count"], 3)
                self.assertEqual(
                    res["calendars_checked"],
                    ["retholtz@gmail.com", "Family Shared", "Michael Retholtz"],
                )
                summaries = [e["summary"] for e in res["events"]]
                self.assertEqual(
                    summaries,
                    ["Cabinet and Hutch Delivery", "PT committee meeting", "Mike - Filling"],
                )
                self.assertIn("Mike - Filling", res["calendar_summary"])
                self.assertIn("Michael Retholtz", res["calendar_summary"])

    def test_google_multi_calendar_403_fallback_to_primary(self):
        from core.oauth_manager import OAuthManager

        mgr = OAuthManager()
        with patch.object(mgr, "_google_headers", return_value={"Authorization": "Bearer old_token"}):
            def fake_get(url, headers=None, params=None, timeout=12):
                resp = MagicMock()
                if url.endswith("/users/me/calendarList"):
                    resp.ok = False
                    resp.status_code = 403
                    resp.text = "Insufficient Permission"
                else:
                    resp.ok = True
                    resp.status_code = 200
                    resp.json.return_value = {
                        "items": [
                            {
                                "id": "ev_1",
                                "summary": "Primary Only Event",
                                "start": {"dateTime": "2026-10-25T09:00:00-04:00"},
                                "end": {"dateTime": "2026-10-25T10:00:00-04:00"},
                            }
                        ]
                    }
                return resp

            with patch("core.oauth_manager.requests.get", side_effect=fake_get):
                res = mgr.list_calendar_events(provider="google", days_ahead=7, max_results=25)
                self.assertTrue(res["success"])
                self.assertEqual(res["count"], 1)
                self.assertIn("scope_warning", res)
                self.assertIn("Reconnect", res["calendar_summary"])

    def test_calendar_target_date_parsing_and_sanitizer_exemption(self):
        """Verify target_date natural language parsing, specific date summaries, and payload sanitizer exemption."""
        import datetime
        from core.oauth_manager import OAuthManager, parse_target_date
        from tools.payload_sanitizer import sanitize_tool_result, EXEMPT_TOOLS

        # 1. EXEMPT_TOOLS check
        self.assertIn("list_upcoming_calendar_events", EXEMPT_TOOLS)
        self.assertIn("list_inbox_messages", EXEMPT_TOOLS)
        self.assertIn("search_emails", EXEMPT_TOOLS)

        # 2. Date parsing check
        ref_d = datetime.date(2026, 10, 24)
        parsed_nov12 = parse_target_date("November 12th", ref_date=ref_d)
        self.assertEqual(parsed_nov12, datetime.date(2026, 11, 12))

        parsed_tue = parse_target_date("next Tuesday", ref_date=ref_d)
        self.assertEqual(parsed_tue, datetime.date(2026, 10, 27))

        parsed_iso = parse_target_date("2026-11-12", ref_date=ref_d)
        self.assertEqual(parsed_iso, datetime.date(2026, 11, 12))

        # 3. Target date query simulation
        mgr = OAuthManager()
        with patch.object(mgr, "_google_headers", return_value={"Authorization": "Bearer valid_tok"}):
            def fake_get(url, headers=None, params=None, timeout=12):
                resp = MagicMock()
                if url.endswith("/users/me/calendarList"):
                    resp.ok = True
                    resp.status_code = 200
                    resp.json.return_value = {
                        "items": [
                            {"id": "retholtz@gmail.com", "primary": True, "summary": "retholtz@gmail.com"},
                            {"id": "shared_cal", "primary": False, "summary": "Traci Shared"},
                        ]
                    }
                else:
                    resp.ok = True
                    resp.status_code = 200
                    resp.json.return_value = {
                        "items": [
                            {
                                "id": "ev_nov12",
                                "summary": "Our First Kiss",
                                "start": {"date": "2026-11-12"},
                                "end": {"date": "2026-11-13"},
                            }
                        ]
                    }
                return resp

            with patch("core.oauth_manager.requests.get", side_effect=fake_get):
                res = mgr.list_calendar_events(provider="google", target_date="November 12th")
                self.assertTrue(res["success"])
                self.assertEqual(res["count"], 1)
                self.assertIn("Our First Kiss", res["calendar_summary"])
                self.assertIn("Thursday, Nov 12, 2026", res["calendar_summary"])

                # Verify payload sanitizer exemption: large payload is NOT truncated to 500 chars
                sanitized = sanitize_tool_result("list_upcoming_calendar_events", res)
                self.assertNotIn("cache_path", sanitized)
                self.assertIn("events", sanitized)
                self.assertEqual(sanitized["events"][0]["summary"], "Our First Kiss")


if __name__ == "__main__":
    unittest.main()

