import asyncio
import ctypes
import io
import logging
import sys
import threading
import time
import wave
import weakref
from ctypes import POINTER, Structure, byref, c_ubyte, c_uint, c_ulong, c_ushort, c_void_p, cast
from ctypes.wintypes import DWORD, LPWSTR
from typing import Any, Callable, Dict, List, Optional
import numpy as np
import sounddevice as sd

logger = logging.getLogger("Aether.Audio")

_pa_lock = threading.RLock()
_active_pipelines: "weakref.WeakSet[AudioPipeline]" = weakref.WeakSet()
_last_win_audio_fingerprint: Optional[tuple] = None


class VADTurnDetector:
    def __init__(self, silence_timeout_ms: int = 1400):
        self.silence_timeout_ms = silence_timeout_ms
        self.is_speech_active = False
        self.silence_start_time = None

    def update_silence_threshold(self, silence_timeout_ms: int):
        """Allows dynamic adjustment from GUI settings without restarting audio thread."""
        self.silence_timeout_ms = max(500, min(silence_timeout_ms, 4000))

    def process_frame(self, is_voice_detected: bool) -> str:
        """
        State logic:
        - Returns 'SPEECH_CONTINUING' if currently speaking.
        - Returns 'SILENCE_WAITING' if inside the trailing silence grace window.
        - Returns 'TURN_COMPLETE' once trailing silence exceeds silence_timeout_ms.
        - Returns 'IDLE' when waiting for new speech.
        """
        now = time.monotonic() * 1000.0

        if is_voice_detected:
            self.is_speech_active = True
            self.silence_start_time = None
            return "SPEECH_CONTINUING"

        if self.is_speech_active:
            if self.silence_start_time is None:
                self.silence_start_time = now
                return "SILENCE_WAITING"

            elapsed_silence = now - self.silence_start_time
            if elapsed_silence >= self.silence_timeout_ms:
                self.is_speech_active = False
                self.silence_start_time = None
                return "TURN_COMPLETE"
            else:
                return "SILENCE_WAITING"

        return "IDLE"


class AudioPipeline:
    def __init__(
        self,
        input_device: int,
        output_device: int,
        mode: str = "always_on",
        software_gate: bool = False,
        on_speech_state: Optional[Callable[[str], None]] = None,
        vad_trailing_silence_ms: int = 1400
    ):
        self.input_device = input_device
        self.output_device = output_device
        self.mode = mode  # "always_on" or "ptt"
        self.software_gate = software_gate  # if True, drops mic while Aether speaks
        self.on_speech_state = on_speech_state
        self.ptt_active = False
        self.vad_trailing_silence_ms = vad_trailing_silence_ms
        self.vad_turn_detector = VADTurnDetector(silence_timeout_ms=vad_trailing_silence_ms)

        self.target_input_rate = 16000
        self.target_output_rate = 24000
        
        in_info = sd.query_devices(self.input_device)
        out_info = sd.query_devices(self.output_device)
        self.input_device_name = str(in_info.get("name", "")).strip()
        self.output_device_name = str(out_info.get("name", "")).strip()
        self.hw_in_rate = int(in_info['default_samplerate'])
        self.hw_out_rate = int(out_info['default_samplerate'])
        
        self.input_queue = asyncio.Queue()
        self.utterance_queue = asyncio.Queue()  # Emits completed WAV byte utterances for STT
        self.output_buffer = bytearray()
        self.buffer_lock = threading.Lock()
        
        self.loop = None
        self._running = False
        self._stop_lock = threading.Lock()
        self.in_stream = None
        self.out_stream = None
        self.is_speaking = False
        self.is_calibrating = False
        self.current_mic_level = 0.0  # Normalized 0.0 - 1.0 for UI visualizer
        _active_pipelines.add(self)

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

    def set_vad_trailing_silence(self, silence_ms: int):
        """Allows dynamic adjustment from GUI settings without restarting audio thread."""
        self.vad_trailing_silence_ms = silence_ms
        if hasattr(self, "vad_turn_detector") and self.vad_turn_detector:
            self.vad_turn_detector.update_silence_threshold(silence_ms)
            logger.info(f"[AUDIO] VAD trailing silence threshold updated to {self.vad_turn_detector.silence_timeout_ms}ms")

    def set_ptt(self, active: bool):
        was_active = self.ptt_active
        self.ptt_active = active
        if active and not was_active:
            # PTT pressed: begin speech capture immediately
            self._is_in_speech = True
            self._speech_frames = list(self._preroll_frames)
            self._utterance_peak_rms = 0.0
            self._silence_count = 0
            if hasattr(self, "vad_turn_detector") and self.vad_turn_detector:
                self.vad_turn_detector.is_speech_active = True
                self.vad_turn_detector.silence_start_time = None
            if self.on_speech_state:
                try:
                    self.on_speech_state("speech_detected")
                except Exception:
                    pass
        elif was_active and not active:
            # PTT released: finalize any speech immediately
            self._finalize_utterance()

    def set_mode(self, mode: str):
        self.mode = mode

    def set_software_gate(self, enabled: bool):
        self.software_gate = enabled

    def _input_callback(self, indata, frames, time_info, status):
        if not self._running:
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

        # Recalculate RMS from the filtered speech signal and update UI meter
        rms = float(np.sqrt(np.mean(resampled**2)))
        self.current_mic_level = min(1.0, rms * 8.0)

        pcm16 = (np.clip(resampled, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.input_queue.put_nowait, pcm16)

        # Check PTT condition & direct speech ingestion
        if self.mode == "ptt":
            if not self.ptt_active:
                self._preroll_frames.append(pcm16)
                if len(self._preroll_frames) > self._preroll_limit:
                    self._preroll_frames.pop(0)
                return
            # While PTT is held: directly capture incoming audio without premature VAD cutoffs
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
            if rms > self._utterance_peak_rms:
                self._utterance_peak_rms = rms
            # Failsafe: max ~30 seconds continuous PTT hold guard
            if len(self._speech_frames) >= 600:
                self._finalize_utterance()
            return

        # Adaptive noise floor tracking (exponential moving average over non-speech)
        if not self._is_in_speech:
            self._noise_floor = 0.95 * self._noise_floor + 0.05 * min(0.05, rms)
            # Near-field speaker dominance: adapt thresholds well above ambient room noise
            self._vad_onset_threshold = max(0.028, self._noise_floor * 2.2 + 0.008)
            self._vad_hangover_threshold = max(0.016, self._noise_floor * 1.35 + 0.004)

        # Dual-threshold hysteresis VAD & Utterance Segmentation for Modular Pipeline
        # When in silence grace window, require onset threshold to resume speech (prevents ambient flutter from resetting timer)
        is_silence_waiting = (
            hasattr(self, "vad_turn_detector")
            and self.vad_turn_detector
            and self.vad_turn_detector.silence_start_time is not None
        )
        if is_silence_waiting:
            is_speech_energy = (rms >= self._vad_onset_threshold)
        elif self._is_in_speech:
            is_speech_energy = (rms >= self._vad_hangover_threshold)
        else:
            is_speech_energy = (rms >= self._vad_onset_threshold)

        vad_state = self.vad_turn_detector.process_frame(is_speech_energy)

        # Snappy barge-in / kill phrase: finalize faster (~400ms) when assistant is actively speaking
        if vad_state == "SILENCE_WAITING" and self.is_speaking and not self.software_gate:
            if self.vad_turn_detector.silence_start_time:
                now_ms = time.monotonic() * 1000.0
                if (now_ms - self.vad_turn_detector.silence_start_time) >= 400.0:
                    vad_state = "TURN_COMPLETE"
                    self.vad_turn_detector.is_speech_active = False
                    self.vad_turn_detector.silence_start_time = None

        if vad_state == "SPEECH_CONTINUING":
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
        elif vad_state == "SILENCE_WAITING":
            self._speech_frames.append(pcm16)
            self._silence_count += 1
        elif vad_state == "TURN_COMPLETE":
            self._speech_frames.append(pcm16)
            self._silence_count += 1
            self._finalize_utterance()
        else:  # IDLE
            self._preroll_frames.append(pcm16)
            if len(self._preroll_frames) > self._preroll_limit:
                self._preroll_frames.pop(0)

    def _finalize_utterance(self):
        if hasattr(self, "vad_turn_detector") and self.vad_turn_detector:
            self.vad_turn_detector.is_speech_active = False
            self.vad_turn_detector.silence_start_time = None
        if not self._speech_frames:
            self._is_in_speech = False
            self._silence_count = 0
            self._utterance_peak_rms = 0.0
            if self.on_speech_state:
                try:
                    self.on_speech_state("speech_idle")
                except Exception:
                    pass
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
            if self.on_speech_state:
                try:
                    self.on_speech_state("speech_idle")
                except Exception:
                    pass
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
        self._utterance_peak_rms = 0.0

        # Minimum speech duration: ~350ms (11,200 bytes at 16kHz 16-bit mono)
        if len(total_pcm) < 11200:
            logger.debug(f"[VAD GATE] Utterance too short ({len(total_pcm)} bytes < 11200), discarding.")
            if self.on_speech_state:
                try:
                    self.on_speech_state("speech_idle")
                except Exception:
                    pass
            return

        if self.on_speech_state:
            try:
                self.on_speech_state("speech_finalized")
            except Exception:
                pass

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
        if not self._running:
            outdata.fill(0)
            return

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
        was_in_speech = self._is_in_speech
        self._speech_frames = []
        self._is_in_speech = False
        self._silence_count = 0
        self._utterance_peak_rms = 0.0
        if hasattr(self, "vad_turn_detector") and self.vad_turn_detector:
            self.vad_turn_detector.is_speech_active = False
            self.vad_turn_detector.silence_start_time = None
        if was_in_speech and self.on_speech_state:
            try:
                self.on_speech_state("speech_idle")
            except Exception:
                pass

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

    def _close_streams_only(self):
        in_s = self.in_stream
        self.in_stream = None
        if in_s is not None:
            try:
                in_s.abort(ignore_errors=True)
                in_s.close(ignore_errors=True)
            except Exception:
                pass

        out_s = self.out_stream
        self.out_stream = None
        if out_s is not None:
            try:
                out_s.abort(ignore_errors=True)
                out_s.close(ignore_errors=True)
            except Exception:
                pass

    def _open_and_start_streams_sync(self, input_device: Optional[int] = None, output_device: Optional[int] = None):
        if sys.platform == "win32":
            try:
                ctypes.windll.ole32.CoInitializeEx(None, 0)
            except Exception:
                pass

        if input_device is not None:
            self.input_device = input_device
        if output_device is not None:
            self.output_device = output_device

        in_info = sd.query_devices(self.input_device)
        out_info = sd.query_devices(self.output_device)
        self.input_device_name = str(in_info.get("name", "")).strip()
        self.output_device_name = str(out_info.get("name", "")).strip()
        self.hw_in_rate = int(in_info["default_samplerate"])
        self.hw_out_rate = int(out_info["default_samplerate"])

        in_channels = 1
        try:
            sd.check_input_settings(device=self.input_device, channels=1, samplerate=self.hw_in_rate)
        except Exception:
            in_channels = min(max(1, int(in_info.get("max_input_channels", 1))), 2)

        out_channels = min(max(1, int(out_info.get("max_output_channels", 2))), 2)

        in_blocksize = 2400 if self.hw_in_rate == 48000 else 2048
        self.in_stream = sd.InputStream(
            device=self.input_device,
            samplerate=self.hw_in_rate,
            channels=in_channels,
            dtype="float32",
            blocksize=in_blocksize,
            callback=self._input_callback,
        )
        self.out_stream = sd.OutputStream(
            device=self.output_device,
            samplerate=self.hw_out_rate,
            channels=out_channels,
            dtype="float32",
            blocksize=2048,
            callback=self._output_callback,
        )

        for attempt in range(3):
            try:
                self.in_stream.start()
                break
            except Exception as e:
                if attempt == 2:
                    raise
                logger.warning(f"[AUDIO] Retrying input stream start (attempt {attempt+1}): {e}")
                time.sleep(0.05)

        for attempt in range(3):
            try:
                self.out_stream.start()
                break
            except Exception as e:
                if attempt == 2:
                    raise
                logger.warning(f"[AUDIO] Retrying output stream start (attempt {attempt+1}): {e}")
                time.sleep(0.05)

    def switch_devices(self, input_device: int, output_device: int) -> bool:
        """Hot-swaps active input/output devices on a running AudioPipeline without stopping the session."""
        with _pa_lock:
            with self._stop_lock:
                if not self._running:
                    self.input_device = input_device
                    self.output_device = output_device
                    return True
                try:
                    self._close_streams_only()
                    self._open_and_start_streams_sync(input_device=input_device, output_device=output_device)
                    logger.info(
                        f"[AUDIO] Switched active streams to Input #{self.input_device} ({self.input_device_name}), "
                        f"Output #{self.output_device} ({self.output_device_name})"
                    )
                    return True
                except Exception as e:
                    logger.error(f"[AUDIO] Failed to hot-swap audio devices ({input_device}, {output_device}): {e}")
                    return False

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self._running = True
        with _pa_lock:
            self._open_and_start_streams_sync()

    def stop(self):
        with self._stop_lock:
            if not self._running and self.in_stream is None and self.out_stream is None:
                return
            self._running = False
            self.clear_output_buffer()
            self._close_streams_only()


# =============================================================================
# Windows Core Audio (IMMDeviceEnumerator) + PortAudio Synchronization
# =============================================================================

class _GUID(Structure):
    _fields_ = [
        ("Data1", c_ulong),
        ("Data2", c_ushort),
        ("Data3", c_ushort),
        ("Data4", c_ubyte * 8),
    ]


class _PROPERTYKEY(Structure):
    _fields_ = [
        ("fmtid", _GUID),
        ("pid", DWORD),
    ]


class _PROPVARIANT(Structure):
    _fields_ = [
        ("vt", c_ushort),
        ("wReserved1", c_ushort),
        ("wReserved2", c_ushort),
        ("wReserved3", c_ushort),
        ("pwszVal", LPWSTR),
        ("padding", c_ubyte * 8),
    ]


def _parse_guid(guid_str: str) -> _GUID:
    clean = guid_str.strip("{}").replace("-", "")
    return _GUID(
        int(clean[0:8], 16),
        int(clean[8:12], 16),
        int(clean[12:16], 16),
        (c_ubyte * 8)(*(int(clean[i : i + 2], 16) for i in range(16, 32, 2))),
    )


_CLSID_MMDeviceEnumerator = _parse_guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
_IID_IMMDeviceEnumerator = _parse_guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
_PKEY_Device_FriendlyName = _PROPERTYKEY(_parse_guid("A45C254E-DF1C-4EFD-8020-67D146A850E0"), 14)


def _com_call(interface_ptr: c_void_p, index: int, restype: Any, argtypes: list, *args) -> Any:
    vtable = cast(interface_ptr, POINTER(POINTER(c_void_p))).contents
    func = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtable[index])
    return func(interface_ptr, *args)


def _com_release(interface_ptr: Optional[c_void_p]) -> None:
    if interface_ptr:
        _com_call(interface_ptr, 2, c_ulong, [])


def _read_mmdevice_props(ole32, p_device: c_void_p) -> Dict[str, str]:
    dev_id = ""
    name = "Unknown"
    p_id = LPWSTR()
    if _com_call(p_device, 5, ctypes.HRESULT, [POINTER(LPWSTR)], byref(p_id)) == 0:
        try:
            dev_id = p_id.value or ""
        finally:
            ole32.CoTaskMemFree(p_id)

    p_props = c_void_p()
    if _com_call(p_device, 4, ctypes.HRESULT, [DWORD, POINTER(c_void_p)], 0, byref(p_props)) == 0:
        try:
            pv = _PROPVARIANT()
            if (
                _com_call(
                    p_props,
                    5,
                    ctypes.HRESULT,
                    [POINTER(_PROPERTYKEY), POINTER(_PROPVARIANT)],
                    byref(_PKEY_Device_FriendlyName),
                    byref(pv),
                )
                == 0
            ):
                try:
                    if pv.vt == 31 and pv.pwszVal:
                        name = pv.pwszVal.strip()
                finally:
                    ole32.PropVariantClear(byref(pv))
        finally:
            _com_release(p_props)
    return {"name": name, "device_id": dev_id}


def _query_windows_system_sound_devices() -> Optional[Dict[str, Any]]:
    """
    Queries Windows Core Audio (IMMDeviceEnumerator) for active Render (Output)
    and Capture (Input) endpoints matching Windows Settings -> System -> Sound.
    Executes in ~7ms without disturbing active PortAudio streams.
    """
    if sys.platform != "win32":
        return None

    try:
        ole32 = ctypes.oledll.ole32
        ole32.CoCreateInstance.argtypes = [
            POINTER(_GUID),
            c_void_p,
            DWORD,
            POINTER(_GUID),
            POINTER(c_void_p),
        ]
        ole32.CoCreateInstance.restype = ctypes.HRESULT

        hr = ole32.CoInitializeEx(None, 0)
        co_initialized = hr in (0, 1)

        def _enum_flow(p_enum: c_void_p, data_flow: int) -> List[Dict[str, Any]]:
            # data_flow: 0 = eRender (Output), 1 = eCapture (Input)
            default_id = None
            p_default = c_void_p()
            if (
                _com_call(
                    p_enum,
                    4,
                    ctypes.HRESULT,
                    [ctypes.c_int, ctypes.c_int, POINTER(c_void_p)],
                    data_flow,
                    0,  # eConsole
                    byref(p_default),
                )
                == 0
            ):
                try:
                    default_id = _read_mmdevice_props(ole32, p_default).get("device_id")
                finally:
                    _com_release(p_default)

            items: List[Dict[str, Any]] = []
            p_collection = c_void_p()
            if (
                _com_call(
                    p_enum,
                    3,
                    ctypes.HRESULT,
                    [ctypes.c_int, DWORD, POINTER(c_void_p)],
                    data_flow,
                    0x00000001,  # DEVICE_STATE_ACTIVE
                    byref(p_collection),
                )
                == 0
            ):
                try:
                    count = c_uint()
                    if _com_call(p_collection, 3, ctypes.HRESULT, [POINTER(c_uint)], byref(count)) == 0:
                        for i in range(count.value):
                            p_dev = c_void_p()
                            if (
                                _com_call(
                                    p_collection,
                                    4,
                                    ctypes.HRESULT,
                                    [c_uint, POINTER(c_void_p)],
                                    i,
                                    byref(p_dev),
                                )
                                == 0
                            ):
                                try:
                                    info = _read_mmdevice_props(ole32, p_dev)
                                    info["is_default"] = (info["device_id"] == default_id)
                                    items.append(info)
                                finally:
                                    _com_release(p_dev)
                finally:
                    _com_release(p_collection)
            return items

        try:
            p_enumerator = c_void_p()
            if (
                ole32.CoCreateInstance(
                    byref(_CLSID_MMDeviceEnumerator),
                    None,
                    0x1,
                    byref(_IID_IMMDeviceEnumerator),
                    byref(p_enumerator),
                )
                != 0
            ):
                return None
            try:
                win_outputs = _enum_flow(p_enumerator, 0)  # Render
                win_inputs = _enum_flow(p_enumerator, 1)   # Capture
            finally:
                _com_release(p_enumerator)
        finally:
            if co_initialized:
                ole32.CoUninitialize()

        fp = (
            tuple(sorted((d["device_id"], d["name"], d["is_default"]) for d in win_inputs)),
            tuple(sorted((d["device_id"], d["name"], d["is_default"]) for d in win_outputs)),
        )
        return {"inputs": win_inputs, "outputs": win_outputs, "fingerprint": fp}
    except Exception as e:
        logger.debug(f"IMMDeviceEnumerator query failed: {e}")
        return None


def get_windows_audio_fingerprint() -> Optional[tuple]:
    """Returns a fast tuple fingerprint of active Windows System\\Sound devices and defaults."""
    res = _query_windows_system_sound_devices()
    return res["fingerprint"] if res else None


def _reinitialize_portaudio():
    """
    Safely re-initializes PortAudio (sounddevice) so newly plugged/unplugged audio hardware
    is discovered. Suspends and resumes any active AudioPipeline streams seamlessly.
    """
    with _pa_lock:
        running_pipelines = [p for p in list(_active_pipelines) if getattr(p, "_running", False)]
        for p in running_pipelines:
            try:
                p._close_streams_only()
            except Exception:
                pass

        try:
            sd._terminate()
        except Exception:
            pass
        try:
            sd._initialize()
        except Exception as e:
            logger.error(f"Failed to re-initialize PortAudio: {e}")

        if running_pipelines:
            devs = sd.query_devices()
            hostapis = sd.query_hostapis()
            wasapi_idx = next(
                (i for i, a in enumerate(hostapis) if "wasapi" in a.get("name", "").lower()),
                None,
            )
            for p in running_pipelines:
                try:
                    # Resolve updated index for the pipeline's input/output device name
                    new_in = _find_pa_index_by_name(p.input_device_name, True, devs, hostapis, wasapi_idx)
                    new_out = _find_pa_index_by_name(p.output_device_name, False, devs, hostapis, wasapi_idx)
                    if new_in is None:
                        new_in = hostapis[wasapi_idx]["default_input_device"] if wasapi_idx is not None else sd.default.device[0]
                    if new_out is None:
                        new_out = hostapis[wasapi_idx]["default_output_device"] if wasapi_idx is not None else sd.default.device[1]
                    p._open_and_start_streams_sync(input_device=new_in, output_device=new_out)
                except Exception as e:
                    logger.error(f"Failed to resume AudioPipeline after PortAudio reinit: {e}")


def _find_pa_index_by_name(
    target_name: str,
    is_input: bool,
    devices: Any,
    hostapis: Any,
    wasapi_api_idx: Optional[int],
) -> Optional[int]:
    """Matches a Windows System\\Sound friendly name to the best PortAudio device index (preferring WASAPI)."""
    if not target_name:
        return None
    target_lower = target_name.strip().lower()
    ch_key = "max_input_channels" if is_input else "max_output_channels"

    # Priority 1: Windows WASAPI exact match, then prefix/substring match
    if wasapi_api_idx is not None:
        for idx, dev in enumerate(devices):
            if dev["hostapi"] == wasapi_api_idx and dev[ch_key] > 0:
                if dev["name"].strip().lower() == target_lower:
                    return idx
        for idx, dev in enumerate(devices):
            if dev["hostapi"] == wasapi_api_idx and dev[ch_key] > 0:
                dn = dev["name"].strip().lower()
                if target_lower.startswith(dn) or dn.startswith(target_lower):
                    return idx

    # Priority 2: Windows DirectSound
    for idx, dev in enumerate(devices):
        api_name = hostapis[dev["hostapi"]]["name"].lower()
        if "directsound" in api_name and dev[ch_key] > 0:
            dn = dev["name"].strip().lower()
            if dn == target_lower or target_lower.startswith(dn) or dn.startswith(target_lower):
                return idx

    # Priority 3: MME (handles 31-character truncation)
    for idx, dev in enumerate(devices):
        api_name = hostapis[dev["hostapi"]]["name"].lower()
        if "wdm-ks" not in api_name and dev[ch_key] > 0:
            dn = dev["name"].strip().lower()
            if dn and (dn == target_lower or target_lower.startswith(dn)):
                return idx

    return None


def get_available_audio_devices(force_refresh: bool = False) -> dict:
    """
    Enumerates truly available, active audio input and output devices matching Windows System\\Sound.
    Automatically detects newly plugged-in or unplugged earbuds/headsets/speakers via Windows
    IMMDeviceEnumerator and refreshes PortAudio when endpoints or system defaults change.
    """
    global _last_win_audio_fingerprint
    try:
        with _pa_lock:
            win_data = _query_windows_system_sound_devices()
            if win_data is not None:
                if force_refresh or _last_win_audio_fingerprint is None or win_data["fingerprint"] != _last_win_audio_fingerprint:
                    _reinitialize_portaudio()
                    _last_win_audio_fingerprint = win_data["fingerprint"]
            elif force_refresh:
                _reinitialize_portaudio()

            devices = sd.query_devices()
            hostapis = sd.query_hostapis()

            wasapi_api_idx = None
            for idx, api in enumerate(hostapis):
                if "wasapi" in api.get("name", "").lower():
                    wasapi_api_idx = idx
                    break

            # If Windows IMMDeviceEnumerator succeeded, build device list matching System\Sound 1-to-1
            if win_data is not None and (win_data["inputs"] or win_data["outputs"]):
                ins = []
                outs = []

                for w_in in win_data["inputs"]:
                    pa_idx = _find_pa_index_by_name(w_in["name"], True, devices, hostapis, wasapi_api_idx)
                    if pa_idx is None:
                        continue
                    dev = devices[pa_idx]
                    api_name = hostapis[dev["hostapi"]]["name"]
                    sr = int(dev["default_samplerate"])
                    rate_str = f"{sr // 1000}kHz" if sr % 1000 == 0 else f"{sr / 1000:.1f}kHz"
                    is_def = bool(w_in["is_default"])
                    dev_name = w_in["name"]
                    display = f"{dev_name} [Default]" if is_def else dev_name
                    ins.append({
                        "index": pa_idx,
                        "name": dev_name,
                        "device_id": w_in.get("device_id", ""),
                        "display_name": display,
                        "label": f"{display} ({rate_str})",
                        "is_default": is_def,
                        "channels": dev["max_input_channels"],
                        "samplerate": sr,
                        "api": api_name,
                    })

                for w_out in win_data["outputs"]:
                    pa_idx = _find_pa_index_by_name(w_out["name"], False, devices, hostapis, wasapi_api_idx)
                    if pa_idx is None:
                        continue
                    dev = devices[pa_idx]
                    api_name = hostapis[dev["hostapi"]]["name"]
                    sr = int(dev["default_samplerate"])
                    rate_str = f"{sr // 1000}kHz" if sr % 1000 == 0 else f"{sr / 1000:.1f}kHz"
                    is_def = bool(w_out["is_default"])
                    dev_name = w_out["name"]
                    display = f"{dev_name} [Default]" if is_def else dev_name
                    outs.append({
                        "index": pa_idx,
                        "name": dev_name,
                        "device_id": w_out.get("device_id", ""),
                        "display_name": display,
                        "label": f"{display} ({rate_str})",
                        "is_default": is_def,
                        "channels": dev["max_output_channels"],
                        "samplerate": sr,
                        "api": api_name,
                    })

                ins.sort(key=lambda x: (not x["is_default"], x["name"].lower()))
                outs.sort(key=lambda x: (not x["is_default"], x["name"].lower()))
                if ins or outs:
                    return {"inputs": ins, "outputs": outs}

            # Fallback enumeration via PortAudio directly
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

                    if "wdm-ks" in api_name.lower():
                        continue
                    if preferred_hostapi is not None and dev["hostapi"] != preferred_hostapi:
                        continue
                    if any(p in dev_name for p in ["Sound Mapper", "Primary Sound Driver", "Primary Sound Capture"]):
                        continue
                    if dev_name in ["Input ()", "Headphones ()"]:
                        continue

                    sr = int(dev["default_samplerate"])
                    rate_str = f"{sr // 1000}kHz" if sr % 1000 == 0 else f"{sr / 1000:.1f}kHz"

                    if dev["max_input_channels"] > 0:
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
                            "api": api_name,
                        })

                    if dev["max_output_channels"] > 0:
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
                            "api": api_name,
                        })

                ins.sort(key=lambda x: (not x["is_default"], x["name"].lower()))
                outs.sort(key=lambda x: (not x["is_default"], x["name"].lower()))
                return ins, outs

            inputs, outputs = _collect(preferred_hostapi=wasapi_api_idx)
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


def _clean_device_label_to_name(label: str) -> str:
    """Extracts the base device friendly name from a UI label like 'Speakers (Realtek(R) Audio) [Default] (48kHz)'."""
    if not label:
        return ""
    s = label.strip()
    # Strip trailing sample rate like ' (48kHz)' or ' (44.1kHz)'
    if s.endswith("kHz)") and " (" in s:
        s = s[: s.rfind(" (")].strip()
    # Strip ' [Default]'
    s = s.replace("[Default]", "").strip()
    return s


def resolve_valid_audio_devices(
    configured_in: int,
    configured_out: int,
    configured_in_name: str = "",
    configured_out_name: str = "",
) -> tuple[int, int]:
    """
    Validates configured audio device indices/names against live System\\Sound endpoints
    and resolves to the active system default if a device was unplugged or if defaults changed.
    """
    avail = get_available_audio_devices()
    inputs = avail.get("inputs", [])
    outputs = avail.get("outputs", [])

    def _resolve_one(configured_idx: int, configured_label: str, candidates: list, kind: str) -> int:
        if not candidates:
            return configured_idx

        # If the user previously selected the [Default] device (or no device name saved yet),
        # follow the current Windows System\Sound [Default] device (candidates[0]).
        if not configured_label or "[default]" in configured_label.lower():
            default_dev = next((d for d in candidates if d.get("is_default")), candidates[0])
            return default_dev["index"]

        clean_target = _clean_device_label_to_name(configured_label).lower()
        if clean_target:
            for d in candidates:
                if d["name"].strip().lower() == clean_target:
                    return d["index"]
            for d in candidates:
                dn = d["name"].strip().lower()
                if clean_target in dn or dn in clean_target:
                    return d["index"]

        for d in candidates:
            if d["index"] == configured_idx:
                return d["index"]

        fallback = candidates[0]
        logger.warning(
            f"Configured {kind} device #{configured_idx} ('{configured_label}') is unavailable. "
            f"Defaulting to #{fallback['index']} ({fallback['name']})."
        )
        return fallback["index"]

    valid_in = _resolve_one(configured_in, configured_in_name, inputs, "input")
    valid_out = _resolve_one(configured_out, configured_out_name, outputs, "output")
    return valid_in, valid_out
