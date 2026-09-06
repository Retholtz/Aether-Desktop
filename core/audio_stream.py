import asyncio
import threading
import numpy as np
import sounddevice as sd

class AudioPipeline:
    def __init__(
        self,
        input_device: int,
        output_device: int,
        mode: str = "always_on",
        software_gate: bool = False
    ):
        self.input_device = input_device
        self.output_device = output_device
        self.mode = mode  # "always_on" or "ptt"
        self.software_gate = software_gate  # if True, drops mic while Aether speaks
        self.ptt_active = False

        self.target_input_rate = 16000
        self.target_output_rate = 24000
        
        in_info = sd.query_devices(self.input_device)
        out_info = sd.query_devices(self.output_device)
        self.hw_in_rate = int(in_info['default_samplerate'])
        self.hw_out_rate = int(out_info['default_samplerate'])
        
        self.input_queue = asyncio.Queue()
        self.output_buffer = bytearray()
        self.buffer_lock = threading.Lock()
        
        self.loop = None
        self._running = False
        self.is_speaking = False
        self.current_mic_level = 0.0  # Normalized 0.0 - 1.0 for UI visualizer

    def set_ptt(self, active: bool):
        self.ptt_active = active

    def set_mode(self, mode: str):
        self.mode = mode

    def set_software_gate(self, enabled: bool):
        self.software_gate = enabled

    def _input_callback(self, indata, frames, time_info, status):
        if not self._running:
            return
        
        # Calculate approximate RMS for UI audio meter
        rms = float(np.sqrt(np.mean(indata**2)))
        self.current_mic_level = min(1.0, rms * 8.0)

        # Check PTT condition
        if self.mode == "ptt" and not self.ptt_active:
            return

        # Check software echo-cancellation gate (if enabled)
        if self.software_gate and self.is_speaking:
            return

        mono = np.mean(indata, axis=1) if indata.ndim > 1 else indata.flatten()
        
        if self.hw_in_rate != self.target_input_rate:
            target_length = int(round(len(mono) * self.target_input_rate / self.hw_in_rate))
            if target_length > 0:
                resampled = np.interp(
                    np.linspace(0.0, 1.0, target_length, endpoint=False),
                    np.linspace(0.0, 1.0, len(mono), endpoint=False),
                    mono
                )
            else:
                resampled = mono
        else:
            resampled = mono

        pcm16 = (np.clip(resampled, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.input_queue.put_nowait, pcm16)

    def _output_callback(self, outdata, frames, time_info, status):
        if self.hw_out_rate != self.target_output_rate:
            needed_model_samples = int(round(frames * self.target_output_rate / self.hw_out_rate))
        else:
            needed_model_samples = frames

        needed_bytes = needed_model_samples * 2

        with self.buffer_lock:
            buf_len = len(self.output_buffer)
            if buf_len > 0:
                take_bytes = min(buf_len, needed_bytes)
                raw_chunk = bytes(self.output_buffer[:take_bytes])
                del self.output_buffer[:take_bytes]
                if len(self.output_buffer) == 0:
                    self.is_speaking = False
            else:
                raw_chunk = None
                self.is_speaking = False

        if raw_chunk:
            samples_24k = np.frombuffer(raw_chunk, dtype=np.int16).astype(np.float32) / 32767.0

            if self.hw_out_rate != self.target_output_rate:
                target_frames = int(round(len(samples_24k) * self.hw_out_rate / self.target_output_rate))
                if target_frames > 0:
                    resampled = np.interp(
                        np.linspace(0.0, 1.0, target_frames, endpoint=False),
                        np.linspace(0.0, 1.0, len(samples_24k), endpoint=False),
                        samples_24k
                    )
                else:
                    resampled = np.zeros(0, dtype=np.float32)
            else:
                resampled = samples_24k

            actual_len = min(len(resampled), frames)
            outdata[:actual_len, 0] = resampled[:actual_len]
            if actual_len < frames:
                outdata[actual_len:, 0] = 0.0

            if outdata.shape[1] > 1:
                for ch in range(1, outdata.shape[1]):
                    outdata[:, ch] = outdata[:, 0]
        else:
            outdata.fill(0)

    def clear_output_buffer(self):
        with self.buffer_lock:
            self.output_buffer.clear()
            self.is_speaking = False

    def kill_output(self):
        """Immediately halts speech playback and purges all buffered output."""
        self.clear_output_buffer()

    def write_output_chunk(self, data: bytes):
        with self.buffer_lock:
            self.is_speaking = True
            self.output_buffer.extend(data)

    def is_output_empty(self) -> bool:
        with self.buffer_lock:
            return len(self.output_buffer) == 0

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self._running = True
        
        self.in_stream = sd.InputStream(
            device=self.input_device,
            samplerate=self.hw_in_rate,
            channels=1,
            dtype='float32',
            blocksize=2048,
            callback=self._input_callback
        )
        self.out_stream = sd.OutputStream(
            device=self.output_device,
            samplerate=self.hw_out_rate,
            channels=2,
            dtype='float32',
            blocksize=2048,
            callback=self._output_callback
        )
        self.in_stream.start()
        self.out_stream.start()

    def stop(self):
        self._running = False
        self.clear_output_buffer()
        if hasattr(self, 'in_stream'):
            try:
                self.in_stream.stop()
                self.in_stream.close()
            except Exception:
                pass
        if hasattr(self, 'out_stream'):
            try:
                self.out_stream.stop()
                self.out_stream.close()
            except Exception:
                pass