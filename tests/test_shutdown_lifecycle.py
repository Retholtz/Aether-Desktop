import asyncio
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from core.engine import AetherEngine
from core.gui_bridge import GuiBridge
from ui.hud_window import HudWindow


class _EventHook:
    def __init__(self):
        self._handlers = []

    def __iadd__(self, fn):
        self._handlers.append(fn)
        return self


class _FakeWindow:
    def __init__(self):
        self.events = MagicMock()
        self.events.closing = _EventHook()
        self.events.minimized = _EventHook()
        self.events.restored = _EventHook()
        self.events.moved = _EventHook()
        self.destroy = MagicMock()
        self.hide = MagicMock()
        self.evaluate_js = MagicMock()


class TestShutdownLifecycle(unittest.TestCase):
    def _make_hud(self, minimize_to_tray: bool = False):
        bridge = MagicMock()
        bridge.get_raw_config.return_value = {"ui": {"minimize_to_tray": minimize_to_tray}}
        bridge._is_shutting_down = False

        main_win = _FakeWindow()
        overlay_win = _FakeWindow()

        with patch("ui.hud_window.webview.create_window", side_effect=[main_win, overlay_win]), \
             patch("ui.hud_window.SystemTrayManager") as mock_tray_cls, \
             patch("ui.hud_window.clamp_window_position", return_value=(100, 100)):
            mock_tray_cls.return_value = MagicMock()
            hud = HudWindow(bridge=bridge)

        return hud, bridge, main_win, overlay_win

    def test_on_closing_without_tray_does_not_reenter_main_destroy(self):
        hud, bridge, main_win, overlay_win = self._make_hud(minimize_to_tray=False)
        closing_cb = main_win.events.closing._handlers[0]

        result = closing_cb()

        self.assertTrue(result)
        self.assertTrue(bridge._is_shutting_down)
        overlay_win.destroy.assert_called_once()
        main_win.destroy.assert_not_called()

    def test_close_all_is_idempotent_and_destroys_both_windows(self):
        hud, bridge, main_win, overlay_win = self._make_hud(minimize_to_tray=True)

        hud.close_all()
        hud.close_all()

        self.assertTrue(bridge._is_shutting_down)
        hud.tray.stop.assert_called_once()
        overlay_win.destroy.assert_called_once()
        main_win.destroy.assert_called_once()

    def test_gui_bridge_skips_js_dispatch_when_shutting_down(self):
        bridge = GuiBridge.__new__(GuiBridge)
        bridge._js_lock = threading.Lock()
        bridge._is_shutting_down = True
        bridge._window = _FakeWindow()
        bridge._overlay_window = _FakeWindow()
        bridge._is_window_ready = MagicMock(return_value=True)

        bridge._on_engine_event("status", {"state": "disconnected"})
        time.sleep(0.05)

        bridge._window.evaluate_js.assert_not_called()
        bridge._overlay_window.evaluate_js.assert_not_called()

    def test_engine_stop_does_not_block_on_ttrpg_summarize_and_stops_audio_flag(self):
        eng = AetherEngine.__new__(AetherEngine)
        eng.is_running = True
        eng._stop_requested = False
        eng.notify = MagicMock()
        eng.stop_connection_watchdog = MagicMock()
        eng.kill_audio = MagicMock()
        eng._text_queue = asyncio.Queue()
        eng._optimizer_task = None
        eng._main_task = MagicMock()
        eng._main_task.done.return_value = False
        eng._main_task.result.side_effect = TimeoutError()
        eng._loop = None
        eng.hotkey_manager = MagicMock()
        eng.reflexion_engine = None
        eng.optimizer = None

        fake_audio = MagicMock()
        fake_audio._running = True
        eng.audio = fake_audio

        eng.session_lifecycle = MagicMock()
        eng.session_lifecycle.get_raw_turns.return_value = [{"role": "user", "content": "hi"}]
        eng.session_lifecycle.session_id = "s1"

        summarize_started = threading.Event()
        release_summarize = threading.Event()

        def slow_summarize(*args, **kwargs):
            summarize_started.set()
            release_summarize.wait(timeout=2.0)

        eng.ttrpg_service = MagicMock()
        eng.ttrpg_service.active_state = {"campaign_id": "c1"}
        eng.ttrpg_service.auto_summarize_session_to_journal.side_effect = slow_summarize

        t0 = time.perf_counter()
        eng.stop()
        elapsed = time.perf_counter() - t0

        try:
            self.assertLess(elapsed, 0.5, "engine.stop() blocked on TTRPG summarization")
            self.assertFalse(fake_audio._running)
            eng._main_task.cancel.assert_called_once()
            self.assertTrue(summarize_started.wait(timeout=1.0))
        finally:
            release_summarize.set()

    def test_disconnect_live_session_times_out_hung_close(self):
        eng = AetherEngine.__new__(AetherEngine)
        eng.conn_mgr = MagicMock()
        eng.session_lifecycle = None
        eng.config_getter = lambda: {}
        eng.notify = MagicMock()

        async def hung_close():
            await asyncio.sleep(10.0)

        hung_session = MagicMock()
        hung_session.close = hung_close
        eng.session = hung_session
        eng.live_session = hung_session
        eng._active_cm = None
        eng._active_recv_task = None

        async def _run():
            t0 = time.perf_counter()
            await eng._disconnect_live_session(reason="Test")
            return time.perf_counter() - t0

        elapsed = asyncio.run(_run())
        self.assertLess(elapsed, 2.0)


if __name__ == "__main__":
    unittest.main()
