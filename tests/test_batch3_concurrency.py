import os
import time
import json
import threading
import unittest
from unittest.mock import MagicMock, patch

from core.config_manager import save_config_atomic
from core.session_lifecycle import SessionLifecycleManager


class TestBatch3Concurrency(unittest.TestCase):
    def test_atomic_config_write(self):
        test_file = "test_config_atomic.json"
        data = {"status": "ok", "timestamp": time.time()}
        
        try:
            save_config_atomic(data, config_path=test_file)
            self.assertTrue(os.path.exists(test_file))
            with open(test_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(loaded["status"], "ok")
        finally:
            if os.path.exists(test_file):
                os.remove(test_file)

    def test_concurrent_singleton_initialization(self):
        from core.user_memory import get_user_memory
        
        instances = []
        def worker():
            inst = get_user_memory()
            instances.append(inst)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads must receive the identical object reference
        first = instances[0]
        for inst in instances:
            self.assertIs(first, inst)

    def test_session_rotation_timeout_guard(self):
        lifecycle = SessionLifecycleManager()
        lifecycle.rotation_in_progress = True
        lifecycle.turn_count = lifecycle.rotation_threshold_turns + 5

        # While rotation is actively in progress (< 120s), needs_rotation is False
        lifecycle.rotation_started_at = time.time()
        self.assertFalse(lifecycle.needs_rotation())

        # If rotation is stuck for > 120 seconds, lock expires and needs_rotation resets it
        lifecycle.rotation_started_at = time.time() - 130.0
        self.assertTrue(lifecycle.needs_rotation())
        self.assertFalse(lifecycle.rotation_in_progress)

    def test_session_rotate_session_execution(self):
        lifecycle = SessionLifecycleManager()
        lifecycle.record_turn("user", "Hello Aether")
        self.assertEqual(len(lifecycle.raw_transcript), 1)

        lifecycle.rotate_session()
        self.assertFalse(lifecycle.rotation_in_progress)
        self.assertEqual(lifecycle.turn_count, 0)

    def test_engine_reconnect_retains_live_session_pointer(self):
        from core.engine import AetherEngine
        engine = AetherEngine()
        old_mock_session = MagicMock()
        new_mock_session = MagicMock()

        engine.live_session = old_mock_session
        engine.session = old_mock_session

        pointer_during_connect = []

        def mock_establish(config):
            # Verify old session pointer is NOT set to None while connecting
            pointer_during_connect.append(engine.live_session)
            return new_mock_session

        with patch.object(engine, "_establish_raw_live_stream", side_effect=mock_establish):
            engine.reconnect_live_session(reason="Batch3 Concurrency Test")

        self.assertEqual(len(pointer_during_connect), 1)
        self.assertIs(pointer_during_connect[0], old_mock_session)
        self.assertIs(engine.live_session, new_mock_session)
        old_mock_session.close.assert_called_once()

    def test_gui_bridge_audio_watcher_stop(self):
        from core.gui_bridge import GUIBridge
        bridge = GUIBridge()
        self.assertTrue(bridge._audio_watcher_thread.is_alive())

        bridge.stop_audio_watcher()
        self.assertFalse(bridge._audio_watcher_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
