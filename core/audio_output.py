import queue
import threading
from typing import Optional
import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None


def apply_micro_fade_out(pcm_bytes: bytes, sample_rate: int = 24000, fade_ms: float = 8.0) -> bytes:
    """
    Applies a microscopic linear fade-out curve (5-10ms) across a 16-bit PCM frame buffer
    to eliminate acoustic pop/click artifacts when flushing playback.
    """
    if not pcm_bytes or len(pcm_bytes) < 2:
        return b""

    even_len = len(pcm_bytes) - (len(pcm_bytes) % 2)
    samples = np.frombuffer(pcm_bytes[:even_len], dtype=np.int16).copy()
    if len(samples) == 0:
        return b""

    fade_samples = min(len(samples), max(1, int(sample_rate * (fade_ms / 1000.0))))
    samples = samples[:fade_samples]
    fade_curve = np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
    faded_samples = (samples.astype(np.float32) * fade_curve).astype(np.int16)
    return faded_samples.tobytes()


class InterruptibleAudioPlayer:
    def __init__(self, sample_rate: int = 24000, channels: int = 1):
        self.sample_rate = sample_rate
        self.channels = channels
        self.audio_queue: "queue.Queue[bytes]" = queue.Queue()
        self.is_playing = False
        self._interrupt_event = threading.Event()
        self._stream = None
        self._lock = threading.Lock()
        self._last_chunk: Optional[bytes] = None
        self._pending_fade_chunk: Optional[bytes] = None

    def enqueue_chunk(self, pcm_bytes: bytes):
        """Adds raw PCM audio chunk to output queue."""
        if not self._interrupt_event.is_set() and pcm_bytes:
            self.audio_queue.put(pcm_bytes)

    def trigger_barge_in(self):
        """
        Immediately interrupts playback:
        1. Signals the playback loop to cease writing to the sound card.
        2. Drains queued audio chunks instantly.
        3. Clears the output hardware ring buffer with a micro-fade.
        """
        self._interrupt_event.set()

        with self._lock:
            # Prepare a 5-10ms micro-fade tail from the most recent active chunk to prevent clicks/pops
            if self.is_playing and self._last_chunk:
                self._pending_fade_chunk = apply_micro_fade_out(
                    self._last_chunk,
                    sample_rate=self.sample_rate,
                    fade_ms=8.0
                )
            self.is_playing = False
            self._last_chunk = None

        # Drain queue immediately
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
            except queue.Empty:
                break

        print("[INFO] [AUDIO_OUT] Barge-in triggered. Playback flushed.")

    def reset_interrupt(self):
        with self._lock:
            self._pending_fade_chunk = None
        self._interrupt_event.clear()

    def play_loop(self):
        """Dedicated playback worker thread feeding the audio output stream."""
        if sd is None:
            return

        with sd.RawOutputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype='int16',
            blocksize=1024
        ) as stream:
            self._stream = stream
            while True:
                # If a micro-fade chunk was queued by trigger_barge_in, flush it smoothly to DAC
                fade_chunk = None
                with self._lock:
                    if self._pending_fade_chunk is not None:
                        fade_chunk = self._pending_fade_chunk
                        self._pending_fade_chunk = None
                if fade_chunk:
                    try:
                        stream.write(fade_chunk)
                    except Exception:
                        pass

                try:
                    chunk = self.audio_queue.get(timeout=0.05)
                except queue.Empty:
                    with self._lock:
                        self.is_playing = False
                    continue

                if self._interrupt_event.is_set():
                    # Interrupted: Discard chunk without sending to DAC
                    try:
                        self.audio_queue.task_done()
                    except Exception:
                        pass
                    continue

                # Write chunk to output
                try:
                    with self._lock:
                        self.is_playing = True
                        self._last_chunk = chunk
                    stream.write(chunk)
                except Exception as e:
                    print(f"[WARN] [AUDIO_OUT] Write underflow or device error: {e}")
                finally:
                    try:
                        self.audio_queue.task_done()
                    except Exception:
                        pass
