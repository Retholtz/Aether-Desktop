import asyncio
import io
import logging
import threading
import wave
from typing import Callable, Optional
import numpy as np
import sounddevice as sd

logger = logging.getLogger("Aether.Audio")

class AudioPipeline:
    def __init__(
        self,
        input_device: int,
        output_device: int,
        mode: str = "always_on",
        software_gate: bool = False,
        on_speech_state: Optional[Callable[[str], None]] = None
    ):
        self.input_device = input_device
        self.output_device = output_device
        self.mode = mode  # "always_on" or "ptt"
        self.software_gate = software_gate  # if True, drops mic while Aether speaks
        self.on_speech_state = on_speech_state
        self.ptt_active = False

        self.target_input_rate = 16000
        self.target_output_rate = 24000
        
        in_info = sd.query_devices(self.input_device)
        out_info = sd.query_devices(self.output_device)
        self.hw_in_rate = int(in_info['default_samplerate'])
        self.hw_out_rate = int(out_info['default_samplerate'])
        
        self.input_queue = asyncio.Queue()
        self.utterance_queue = asyncio.Queue()  # Emits completed WAV byte utterances for STT
        self.output_buffer = bytearray()
        self.buffer_lock = threading.Lock()
        
        self.loop = None
        self._running = False
        self.is_speaking = False
        self.is_calibrating = False
        self.current_mic_level = 0.0  # Normalized 0.0 - 1.0 for UI visualizer

        # Voice Activity Detection (VAD) state for utterance segmentation
        # Adaptive noise floor tracking prevents getting trapped by PC/room noise
        self._speech_frames = []
        self._preroll_frames = []
        self._silence_count = 0
        self._is_in_speech = False
        self._noise_floor = 0.010            # Dynamically tracked ambient background RMS
        self._vad_onset_threshold = 0.028    # RMS threshold to trigger speech onset (adaptive near-field)
        self._vad_hangover_threshold = 0.016 # Lower RMS threshold to maintain speech (adaptive)
        self._silence_limit = 18             # ~900ms of sub-hangover silence before finalizing (18 blocks at ~50ms/block)
        self._preroll_limit = 8              # ~400ms pre-speech audio retained
        self._postroll_padding = 5           # ~250ms post-speech audio retained
        self._max_speech_frames = 200        # ~10 seconds maximum utterance guard to prevent endless accumulation

        # Digital 85Hz high-pass filter state (attenuates AC rumble, fan hum, and desk vibrations)
        self._hp_alpha = 0.9677
        self._hp_prev_x = 0.0
        self._hp_prev_y = 0.0

        # Near-field speaker dominance tracking (ensures loud speaker is separated from room noise)
        self._utterance_peak_rms = 0.0

    def set_ptt(self, active: bool):
        was_active = self.ptt_active
        self.ptt_active = active
        if was_active and not active:
            # PTT released: finalize any speech immediately
            self._finalize_utterance()

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

        # Mute agent speech ingestion while user is actively recording voice calibration samples
        if getattr(self, "is_calibrating", False):
            return

        mono = np.mean(indata, axis=1) if indata.ndim > 1 else indata.flatten()
        
        if self.hw_in_rate == 48000 and self.target_input_rate == 16000:
            rem = len(mono) % 3
            if rem > 0:
                mono = mono[:-rem]
            resampled = mono.reshape(-1, 3).mean(axis=1)
        elif self.hw_in_rate != self.target_input_rate:
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

        # Apply 85Hz digital high-pass filter (strips AC rumble, fan hum, and desk contact noise)
        filtered = np.empty_like(resampled)
        prev_x = self._hp_prev_x
        prev_y = self._hp_prev_y
        alpha = self._hp_alpha
        for i in range(len(resampled)):
            curr_x = resampled[i]
            curr_y = alpha * (prev_y + curr_x - prev_x)
            filtered[i] = curr_y
            prev_x = curr_x
            prev_y = curr_y
        self._hp_prev_x = prev_x
        self._hp_prev_y = prev_y
        resampled = filtered

        # Recalculate RMS from the filtered speech signal
        rms = float(np.sqrt(np.mean(resampled**2)))
        self.current_mic_level = min(1.0, rms * 8.0)

        pcm16 = (np.clip(resampled, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.input_queue.put_nowait, pcm16)

        # Adaptive noise floor tracking (exponential moving average over non-speech)
        if not self._is_in_speech:
            self._noise_floor = 0.95 * self._noise_floor + 0.05 * min(0.05, rms)
            # Near-field speaker dominance: adapt thresholds well above ambient room noise
            self._vad_onset_threshold = max(0.028, self._noise_floor * 2.2 + 0.008)
            self._vad_hangover_threshold = max(0.016, self._noise_floor * 1.35 + 0.004)

        # Dual-threshold hysteresis VAD & Utterance Segmentation for Modular Pipeline
        if self._is_in_speech:
            is_speech_energy = (rms >= self._vad_hangover_threshold)
        else:
            is_speech_energy = (rms >= self._vad_onset_threshold)

        if is_speech_energy:
            if not self._is_in_speech:
                self._is_in_speech = True
                self._speech_frames = list(self._preroll_frames)
                self._utterance_peak_rms = rms
                if self.on_speech_state:
                    try:
                        self.on_speech_state("speech_detected")
                    except Exception:
                        pass
            self._speech_frames.append(pcm16)
            self._silence_count = 0
            if rms > self._utterance_peak_rms:
                self._utterance_peak_rms = rms

            # Failsafe: if continuous speech exceeds ~10s, finalize immediately to avoid hanging
            if len(self._speech_frames) >= self._max_speech_frames:
                self._finalize_utterance()
        else:
            if self._is_in_speech:
                self._speech_frames.append(pcm16)
                self._silence_count += 1
                # Snappy barge-in / kill phrase: finalize faster (~400ms vs ~900ms) when assistant is speaking
                active_limit = 8 if (self.is_speaking and not self.software_gate) else self._silence_limit
                if self._silence_count >= active_limit:
                    self._finalize_utterance()
            else:
                self._preroll_frames.append(pcm16)
                if len(self._preroll_frames) > self._preroll_limit:
                    self._preroll_frames.pop(0)

    def _finalize_utterance(self):
        if not self._speech_frames:
            self._is_in_speech = False
            self._silence_count = 0
            self._utterance_peak_rms = 0.0
            return

        # Speaker Dominance Check: require near-field speech loudness or clear SNR above ambient floor
        snr = self._utterance_peak_rms / max(0.005, self._noise_floor)
        if self._utterance_peak_rms < 0.030 and snr < 2.0:
            logger.debug(
                f"[VAD GATE] Discarded ambient noise utterance "
                f"(Peak: {self._utterance_peak_rms:.4f}, Noise: {self._noise_floor:.4f}, SNR: {snr:.1f}x)"
            )
            self._speech_frames = []
            self._is_in_speech = False
            self._silence_count = 0
            self._utterance_peak_rms = 0.0
            return

        # Keep only up to _postroll_padding frames of silence at the end of utterance
        # to ensure soft trailing syllables ('t', 's', 'k') are retained without dead air
        trim_silence = max(0, self._silence_count - self._postroll_padding)
        if trim_silence > 0 and len(self._speech_frames) > trim_silence:
            frames_to_send = self._speech_frames[:-trim_silence]
        else:
            frames_to_send = self._speech_frames

        total_pcm = b"".join(frames_to_send)
        self._speech_frames = []
        self._is_in_speech = False
        self._silence_count = 0

        if self.on_speech_state:
            try:
                self.on_speech_state("speech_finalized")
            except Exception:
                pass

        # Minimum speech duration: ~350ms (11,200 bytes at 16kHz 16-bit mono)
        if len(total_pcm) >= 11200:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.target_input_rate)
                wf.writeframes(total_pcm)
            buf.seek(0)
            wav_bytes = buf.read()
            if self.loop and self.loop.is_running():
                self.loop.call_soon_threadsafe(self.utterance_queue.put_nowait, wav_bytes)

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

            # Duck audio volume to 25% (-12dB) if user is actively speaking in earbud/barge-in mode
            if self._is_in_speech and not self.software_gate:
                samples_24k = samples_24k * 0.25

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

    def reset_vad(self):
        """Resets VAD tracking state to clean listening."""
        self._speech_frames = []
        self._is_in_speech = False
        self._silence_count = 0
        self._utterance_peak_rms = 0.0

    def kill_output(self):
        """Immediately halts speech playback and purges all buffered output."""
        self.clear_output_buffer()
        self.reset_vad()

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
        
        in_blocksize = 2400 if self.hw_in_rate == 48000 else 2048
        self.in_stream = sd.InputStream(
            device=self.input_device,
            samplerate=self.hw_in_rate,
            channels=1,
            dtype='float32',
            blocksize=in_blocksize,
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


def get_available_audio_devices() -> dict:
    """
    Enumerates truly available, active audio input and output devices.
    Prioritizes modern Windows WASAPI endpoints, filters out broken WDM-KS pins
    and virtual sound mappers, validates each device with a test stream open,
    and sorts the system default devices to the top.
    """
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()

        # Check if WASAPI is available (native Windows Core Audio Session API)
        wasapi_api_idx = None
        for idx, api in enumerate(hostapis):
            if "wasapi" in api.get("name", "").lower():
                wasapi_api_idx = idx
                break

        def _collect(preferred_hostapi=None):
            ins = []
            outs = []

            if preferred_hostapi is not None:
                def_in = hostapis[preferred_hostapi].get("default_input_device", -1)
                def_out = hostapis[preferred_hostapi].get("default_output_device", -1)
            else:
                sd_def = sd.default.device
                def_in = sd_def[0] if isinstance(sd_def, (list, tuple)) else -1
                def_out = sd_def[1] if isinstance(sd_def, (list, tuple)) else -1

            for idx, dev in enumerate(devices):
                api_name = hostapis[dev["hostapi"]]["name"]
                dev_name = dev["name"].strip()

                # Filter out raw WDM-KS driver pins (fail blocking stream open)
                if "wdm-ks" in api_name.lower():
                    continue

                # Filter by preferred hostapi if specified
                if preferred_hostapi is not None and dev["hostapi"] != preferred_hostapi:
                    continue

                # Filter generic virtual aliases and unconfigured pins
                if any(p in dev_name for p in ["Sound Mapper", "Primary Sound Driver", "Primary Sound Capture"]):
                    continue
                if dev_name in ["Input ()", "Headphones ()"]:
                    continue

                sr = int(dev["default_samplerate"])
                rate_str = f"{sr // 1000}kHz" if sr % 1000 == 0 else f"{sr / 1000:.1f}kHz"

                # Check and validate input
                if dev["max_input_channels"] > 0:
                    try:
                        with sd.InputStream(device=idx, channels=1, samplerate=sr):
                            pass
                        is_def = (idx == def_in)
                        display = f"{dev_name} [Default]" if is_def else dev_name
                        ins.append({
                            "index": idx,
                            "name": dev_name,
                            "display_name": display,
                            "label": f"{display} ({rate_str})",
                            "is_default": is_def,
                            "channels": dev["max_input_channels"],
                            "samplerate": sr,
                            "api": api_name
                        })
                    except Exception:
                        pass

                # Check and validate output
                if dev["max_output_channels"] > 0:
                    try:
                        ch = min(int(dev["max_output_channels"]), 2)
                        with sd.OutputStream(device=idx, channels=ch, samplerate=sr):
                            pass
                        is_def = (idx == def_out)
                        display = f"{dev_name} [Default]" if is_def else dev_name
                        outs.append({
                            "index": idx,
                            "name": dev_name,
                            "display_name": display,
                            "label": f"{display} ({rate_str})",
                            "is_default": is_def,
                            "channels": dev["max_output_channels"],
                            "samplerate": sr,
                            "api": api_name
                        })
                    except Exception:
                        pass

            # Sort so system default device is first, then alphabetical by name
            ins.sort(key=lambda x: (not x["is_default"], x["name"].lower()))
            outs.sort(key=lambda x: (not x["is_default"], x["name"].lower()))
            return ins, outs

        # First attempt with WASAPI if available
        inputs, outputs = _collect(preferred_hostapi=wasapi_api_idx)

        # Fallback to non-WDM-KS devices if WASAPI found nothing
        if not inputs or not outputs:
            fb_ins, fb_outs = _collect(preferred_hostapi=None)
            if not inputs:
                inputs = fb_ins
            if not outputs:
                outputs = fb_outs

        return {"inputs": inputs, "outputs": outputs}
    except Exception as e:
        logger.error(f"Error enumerating audio devices: {e}")
        return {"inputs": [], "outputs": [], "error": str(e)}


def resolve_valid_audio_devices(configured_in: int, configured_out: int) -> tuple[int, int]:
    """
    Validates configured audio device indices and resolves to available defaults if needed.
    Prevents engine crashes if a configured audio device is unplugged or invalid.
    """
    avail = get_available_audio_devices()
    inputs = avail.get("inputs", [])
    outputs = avail.get("outputs", [])

    valid_in = configured_in
    if not any(d["index"] == configured_in for d in inputs):
        if inputs:
            valid_in = inputs[0]["index"]
            logger.warning(
                f"Configured input device #{configured_in} is unavailable. "
                f"Defaulting to available device #{valid_in} ({inputs[0]['name']})."
            )

    valid_out = configured_out
    if not any(d["index"] == configured_out for d in outputs):
        if outputs:
            valid_out = outputs[0]["index"]
            logger.warning(
                f"Configured output device #{configured_out} is unavailable. "
                f"Defaulting to available device #{valid_out} ({outputs[0]['name']})."
            )

    return valid_in, valid_out
