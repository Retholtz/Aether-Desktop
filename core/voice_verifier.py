import io
import os
import time
import wave
import urllib.request
from typing import Optional, Tuple, List, Dict

import numpy as np
try:
    import sherpa_onnx
except ImportError:
    sherpa_onnx = None

from core.logger import get_logger
from core.paths import resource_path, user_data_path

logger = get_logger("VoiceVerifier")

DEFAULT_MODEL_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_CAM++.onnx"
BUNDLED_MODEL_PATH = resource_path("models", "wespeaker_en_voxceleb_CAM++.onnx")
DEFAULT_MODEL_PATH = user_data_path("models", "wespeaker_en_voxceleb_CAM++.onnx")
DEFAULT_PROFILE_PATH = user_data_path("profile", "user_voiceprint.npy")

class VoiceProfileVerifier:
    """
    Offline target speaker verification utilizing sherpa-onnx and CAM++ voice embeddings.
    Filters ambient chatter, TV dialogue, and secondary speakers before STT/LLM execution.
    """
    def __init__(
        self,
        model_path: str = DEFAULT_MODEL_PATH,
        profile_path: str = DEFAULT_PROFILE_PATH
    ):
        self.model_path = model_path
        self.profile_path = profile_path
        self.extractor: Optional[sherpa_onnx.SpeakerEmbeddingExtractor] = None
        self.manager: Optional[sherpa_onnx.SpeakerEmbeddingManager] = None
        self.enrolled_embedding: Optional[np.ndarray] = None
        self.staged_samples: List[np.ndarray] = []
        self._is_initialized = False

        self._init_engine()

    def _init_engine(self):
        """Initializes the ONNX embedding extractor and loads any pre-existing voiceprint."""
        try:
            if sherpa_onnx is None:
                logger.warning("[VOICE VERIFIER] sherpa_onnx is not installed. Voice biometrics verification disabled.")
                return
            self._ensure_model_file()
            if not os.path.exists(self.model_path):
                logger.warning(f"[VOICE VERIFIER] Model file not found at {self.model_path}")
                return

            config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=self.model_path,
                num_threads=2,
                debug=False
            )
            self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
            self.manager = sherpa_onnx.SpeakerEmbeddingManager(self.extractor.dim)
            self._is_initialized = True
            logger.info(f"[VOICE VERIFIER] Initialized CAM++ extractor (dim: {self.extractor.dim})")

            # Load existing enrolled profile if present
            self.load_profile()
        except Exception as e:
            logger.error(f"[VOICE VERIFIER INIT ERROR] {e}")

    def _ensure_model_file(self):
        """Uses bundled model if present, or atomically downloads the pretrained CAM++ ONNX model to a writable path."""
        if os.path.exists(self.model_path) and os.path.getsize(self.model_path) > 1000000:
            return
        if (
            self.model_path == DEFAULT_MODEL_PATH
            and os.path.exists(BUNDLED_MODEL_PATH)
            and os.path.getsize(BUNDLED_MODEL_PATH) > 1000000
        ):
            self.model_path = BUNDLED_MODEL_PATH
            return

        target_dir = os.path.dirname(self.model_path) or "."
        os.makedirs(target_dir, exist_ok=True)
        tmp_path = f"{self.model_path}.tmp.{os.getpid()}"
        logger.info(f"[VOICE VERIFIER] Downloading CAM++ model from {DEFAULT_MODEL_URL}...")
        t0 = time.time()
        try:
            req = urllib.request.Request(DEFAULT_MODEL_URL, headers={"User-Agent": "Aether-Desktop"})
            with urllib.request.urlopen(req, timeout=30) as resp, open(tmp_path, "wb") as out_f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out_f.write(chunk)
            if os.path.getsize(tmp_path) <= 1000000:
                raise ValueError("Downloaded CAM++ model file is unexpectedly small/incomplete.")
            os.replace(tmp_path, self.model_path)
            dur = time.time() - t0
            logger.info(f"[VOICE VERIFIER] Model downloaded successfully in {dur:.1f}s")
        except Exception as e:
            logger.error(f"[VOICE VERIFIER DOWNLOAD ERROR] {e}")
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    def is_enrolled(self) -> bool:
        """Returns True if a valid user voiceprint profile is loaded."""
        return self.enrolled_embedding is not None and len(self.enrolled_embedding) > 0

    def load_profile(self) -> bool:
        """Loads user voiceprint embedding from disk."""
        if os.path.exists(self.profile_path):
            try:
                emb = np.load(self.profile_path)
                if emb.ndim == 1 and emb.size == (self.extractor.dim if self.extractor else 512):
                    norm = np.linalg.norm(emb)
                    if norm > 1e-6:
                        emb = emb / norm
                    self.enrolled_embedding = emb.astype(np.float32)
                    if self.manager:
                        if "user" in self.manager:
                            self.manager.remove("user")
                        self.manager.add("user", self.enrolled_embedding)
                    logger.info(f"[VOICE VERIFIER] Loaded user voiceprint from {self.profile_path}")
                    return True
            except Exception as e:
                logger.error(f"[VOICE VERIFIER] Error loading profile: {e}")
        return False

    def _trim_and_normalize(self, audio: np.ndarray, sample_rate: int = 16000) -> Optional[np.ndarray]:
        """Trims leading/trailing silence and peak-normalizes audio for robust, consistent embedding extraction."""
        if len(audio) == 0:
            return None

        peak = float(np.max(np.abs(audio)))
        if peak < 0.0015:  # Hard floor: pure background hiss
            logger.warning(f"[VOICE VERIFIER] Signal amplitude too low for embedding (Peak: {peak:.4f})")
            return None

        # Frame-based energy VAD (30ms frames = 480 samples at 16kHz)
        frame_len = int(sample_rate * 0.03)
        if len(audio) >= frame_len * 2:
            num_frames = len(audio) // frame_len
            frames = audio[:num_frames * frame_len].reshape(num_frames, frame_len)
            frame_rms = np.sqrt(np.mean(frames**2, axis=1))
            max_rms = float(np.max(frame_rms))

            # Estimate ambient background noise floor from quiet frames (25th percentile)
            ambient_floor = float(np.percentile(frame_rms, 25))

            # Speech threshold: must be clearly above ambient room noise floor AND at least 12% of peak vocal energy
            speech_thresh = max(ambient_floor * 1.5, 0.12 * max_rms, 0.008)
            speech_frames = np.where(frame_rms >= speech_thresh)[0]

            if len(speech_frames) > 0:
                # Retain 60ms pre-roll and 90ms post-roll padding
                start_idx = max(0, (speech_frames[0] - 2) * frame_len)
                end_idx = min(len(audio), (speech_frames[-1] + 3) * frame_len)
                audio = audio[start_idx:end_idx]

        # Final peak normalization to 0.75
        trimmed_peak = float(np.max(np.abs(audio)))
        if trimmed_peak > 1e-4:
            audio = (audio / trimmed_peak * 0.75).astype(np.float32)

        return audio

    @staticmethod
    def _tile_short_audio(audio: np.ndarray, sample_rate: int = 16000, min_duration_sec: float = 1.65) -> np.ndarray:
        """
        Temporally tiles short voiced segments (e.g. 2-word phrases like 'Hey Aether')
        with a smooth 20ms pause gap so CAM++ statistical pooling receives sufficient vocal frames.
        """
        target_len = int(sample_rate * min_duration_sec)
        if len(audio) >= target_len or len(audio) == 0:
            return audio
        gap = np.zeros(int(sample_rate * 0.02), dtype=np.float32)
        pieces = [audio]
        total = len(audio)
        while total < target_len:
            pieces.append(gap)
            pieces.append(audio)
            total += len(gap) + len(audio)
        return np.concatenate(pieces)[:target_len]

    def _compute_stream_embedding(self, waveform: np.ndarray, sample_rate: int) -> Optional[np.ndarray]:
        stream = self.extractor.create_stream()
        stream.accept_waveform(sample_rate=sample_rate, waveform=waveform)
        stream.input_finished()
        emb = np.array(self.extractor.compute(stream), dtype=np.float32)
        norm = np.linalg.norm(emb)
        if norm > 1e-6:
            emb = emb / norm
        return emb

    def _extract_embedding_and_duration_from_wav(self, wav_bytes: bytes) -> Tuple[Optional[np.ndarray], float]:
        """Thread-safe extraction returning (normalized 512-dim embedding, voiced_duration_sec)."""
        if not self._is_initialized or not self.extractor:
            return None, 0.0
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                sample_rate = wf.getframerate()
                frames = wf.readframes(wf.getnframes())

            if sample_width != 2:
                return None, 0.0

            audio_data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

            if channels > 1:
                audio_data = audio_data.reshape(-1, channels).mean(axis=1)

            # Clean and normalize audio
            processed_audio = self._trim_and_normalize(audio_data, sample_rate)
            if processed_audio is None or len(processed_audio) < int(sample_rate * 0.18):
                logger.warning("[VOICE VERIFIER] Speech segment too short or silent after trimming")
                return None, 0.0

            voiced_dur = len(processed_audio) / float(max(1, sample_rate))
            emb = self._compute_stream_embedding(processed_audio, sample_rate)
            # For short 1-3 word phrases (< 1.65s, such as "Hey Aether" or "Aether stop"),
            # also compute a temporally-tiled embedding to stabilize CAM++ statistical pooling.
            if len(processed_audio) < int(sample_rate * 1.65):
                tiled_audio = self._tile_short_audio(processed_audio, sample_rate, min_duration_sec=1.65)
                emb_tiled = self._compute_stream_embedding(tiled_audio, sample_rate)
                if emb is not None and emb_tiled is not None:
                    blended = emb + emb_tiled
                    b_norm = np.linalg.norm(blended)
                    if b_norm > 1e-6:
                        blended = blended / b_norm
                    if self.enrolled_embedding is not None:
                        sim_raw = float(np.dot(emb, self.enrolled_embedding))
                        sim_blended = float(np.dot(blended, self.enrolled_embedding))
                        emb = blended if sim_blended >= sim_raw else emb
                    else:
                        emb = blended
            return emb, voiced_dur
        except Exception as e:
            logger.error(f"[VOICE VERIFIER] Embedding extraction failed: {e}")
            return None, 0.0

    def extract_embedding_from_wav(self, wav_bytes: bytes) -> Optional[np.ndarray]:
        """Extracts and normalizes a 512-dim embedding from WAV bytes with silence trimming and gain normalization."""
        emb, _ = self._extract_embedding_and_duration_from_wav(wav_bytes)
        return emb

    def add_calibration_sample(self, wav_bytes: bytes) -> Dict:
        """Adds a calibration sample during onboarding."""
        emb = self.extract_embedding_from_wav(wav_bytes)
        if emb is None:
            return {
                "success": False,
                "error": "Audio too quiet or unclear. Please speak firmly into your microphone."
            }

        self.staged_samples.append(emb)
        count = len(self.staged_samples)
        return {
            "success": True,
            "sample_count": count,
            "samples_count": count,
            "required_samples": 3,
            "message": f"Sample {count} captured successfully."
        }

    def clear_staged_samples(self):
        """Clears staged calibration samples."""
        self.staged_samples.clear()

    def finalize_calibration(self) -> Dict:
        """Averages staged calibration samples and saves the master voiceprint."""
        if not self.staged_samples:
            return {"success": False, "error": "No calibration samples recorded."}

        try:
            os.makedirs(os.path.dirname(self.profile_path) or ".", exist_ok=True)
            stacked = np.stack(self.staged_samples, axis=0)
            centroid = np.mean(stacked, axis=0)
            norm = np.linalg.norm(centroid)
            if norm > 1e-6:
                centroid = centroid / norm
            centroid = centroid.astype(np.float32)

            np.save(self.profile_path, centroid)
            self.enrolled_embedding = centroid

            if self.manager:
                if "user" in self.manager:
                    self.manager.remove("user")
                self.manager.add("user", self.enrolled_embedding)

            sample_count = len(self.staged_samples)
            self.staged_samples.clear()
            logger.info(f"[VOICE VERIFIER] Enrolled voice profile with {sample_count} calibration samples.")
            return {
                "success": True,
                "samples_count": sample_count,
                "sample_count": sample_count,
                "message": f"Voice profile enrolled with {sample_count} samples."
            }
        except Exception as e:
            logger.error(f"[VOICE VERIFIER] Failed to finalize profile: {e}")
            return {"success": False, "error": str(e)}

    def verify(self, wav_bytes: bytes, threshold: float = 0.40) -> Tuple[bool, float]:
        """
        Verifies if the audio belongs to the enrolled user.
        Applies a single bounded duration-adaptive threshold relaxation for short 1-3 word commands
        (e.g. 'Hey Aether', 'Aether stop') which naturally exhibit lower pooling cosine scores.
        Fails CLOSED if an enrolled voiceprint exists on disk but the verifier failed to initialize.
        Returns: (is_match: bool, similarity_score: float)
        """
        if not self.is_enrolled():
            if self.profile_path and os.path.exists(self.profile_path):
                logger.error(
                    "[VOICE GATE] Enrolled voiceprint exists on disk but verifier is not initialized; "
                    "failing CLOSED."
                )
                return False, 0.0
            return True, 1.0

        t0 = time.perf_counter()
        emb, voiced_dur = self._extract_embedding_and_duration_from_wav(wav_bytes)
        if emb is None:
            return False, 0.0

        raw_similarity = float(np.dot(emb, self.enrolled_embedding))

        # Short utterances (< 1.8s, like "Hey Aether") have fewer phonetic frames in CAM++ pooling.
        # Apply a single bounded short-clip compensation (+0.04 max) WITHOUT stacking a second
        # threshold reduction, keeping the effective raw cosine floor safe (e.g. >= 0.36 for 0.40 gate).
        if voiced_dur < 1.8:
            short_factor = max(0.0, min(1.0, (1.8 - voiced_dur) / 1.0))
            similarity = min(1.0, raw_similarity + 0.04 * short_factor)
        else:
            similarity = raw_similarity
        effective_threshold = max(0.32, threshold)

        latency_ms = (time.perf_counter() - t0) * 1000

        is_match = similarity >= effective_threshold
        logger.info(
            f"[VOICE GATE] Score: {similarity:.3f} (raw={raw_similarity:.3f}, dur={voiced_dur:.2f}s) | "
            f"EffThresh: {effective_threshold:.2f} (cfg={threshold:.2f}) | Match: {is_match} ({latency_ms:.1f}ms)"
        )
        return is_match, similarity

    def delete_profile(self) -> Dict:
        """Deletes user voiceprint profile from disk and memory."""
        try:
            if os.path.exists(self.profile_path):
                os.remove(self.profile_path)
            self.enrolled_embedding = None
            if self.manager and "user" in self.manager:
                self.manager.remove("user")
            self.staged_samples.clear()
            logger.info("[VOICE VERIFIER] User voice profile deleted.")
            return {"success": True, "message": "Voice profile deleted."}
        except Exception as e:
            logger.error(f"[VOICE VERIFIER] Delete profile error: {e}")
            return {"success": False, "error": str(e)}

    def get_status(self) -> Dict:
        """Returns enrollment status and metadata for UI display."""
        staged = len(self.staged_samples)
        return {
            "is_ready": self._is_initialized,
            "enrolled": self.is_enrolled(),
            "samples_staged": staged,
            "staged_samples": staged,
            "required_samples": 3
        }
