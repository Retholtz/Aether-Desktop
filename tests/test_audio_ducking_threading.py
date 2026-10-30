"""
tests/test_audio_ducking_threading.py - COM Apartment Threading Tests for Audio Ducking.
Verifies ducking and restoration execute cleanly in worker threads, thread pools,
and asyncio.to_thread without COMError or 'CoInitialize has not been called'.
"""
import asyncio
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from core.audio_ducking import AudioDucker


class TestAudioDuckingCOMThreading(unittest.TestCase):

    def setUp(self):
        self.ducker = AudioDucker()

    def tearDown(self):
        # Ensure audio sessions are restored if a test fails midway
        if hasattr(self.ducker, "restore_all"):
            self.ducker.restore_all()

    def test_duck_and_restore_in_raw_os_thread(self):
        """Verifies ducking runs cleanly in a newly spawned OS thread without pre-existing COM state."""
        error = None

        def worker():
            nonlocal error
            try:
                # Execute duck and restore cycle
                self.ducker.duck_all()
                self.ducker.restore_all()
            except Exception as e:
                error = e

        thread = threading.Thread(target=worker, name="DuckingWorkerThread")
        thread.start()
        thread.join(timeout=5.0)

        self.assertFalse(thread.is_alive(), "Worker thread timed out.")
        self.assertIsNone(
            error, f"Ducking raised an error in worker thread: {error}"
        )

    def test_duck_in_threadpool_executor(self):
        """Verifies ducking runs across pool workers (e.g. asyncio.to_thread background tasks)."""
        with ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(self.ducker.duck_all)
            duck_res = future.result(timeout=5.0)

            restore_future = executor.submit(self.ducker.restore_all)
            restore_res = restore_future.result(timeout=5.0)

        # Assert no unhandled COM exceptions occurred
        self.assertTrue(True)

    def test_asyncio_to_thread_integration(self):
        """Verifies calling ducking via asyncio.to_thread in an event loop."""

        async def run_async():
            await asyncio.to_thread(self.ducker.duck_all)
            await asyncio.sleep(0.05)
            await asyncio.to_thread(self.ducker.restore_all)

        asyncio.run(run_async())


if __name__ == "__main__":
    unittest.main()

