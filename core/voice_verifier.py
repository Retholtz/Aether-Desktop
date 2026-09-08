import io
import os
import time
import wave
import urllib.request
from typing import Optional, Tuple, List, Dict

import numpy as np
import sherpa_onnx

from core.logger import get_logger

logger = get_logger("VoiceVerifier")

DEFAULT_MODEL_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_CAM++.onnx"
DEFAULT_MODEL_PATH = "models/wespeaker_en_voxceleb_CAM++.onnx"
DEFAULT_PROFILE_PATH = "profile/user_voiceprint.npy"

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
        """Downloads the pretrained CAM++ ONNX model if missing."""
        if os.path.exists(self.model_path) and os.path.getsize(self.model_path) > 1000000:
            return
        os.makedirs(os.path.dirname(self.model_path) or ".", exist_ok=True)
        logger.info(f"[VOICE VERIFIER] Downloading CAM++ model from {DEFAULT_MODEL_URL}...")
        t0 = time.time()
        try:
            urllib.request.urlretrieve(DEFAULT_MODEL_URL, self.model_path)
            dur = time.time() - t0
            logger.info(f"[VOICE VERIFIER] Model downloaded successfully in {dur:.1f}s")
        except Exception as e:
            logger.error(f"[VOICE VERIFIER DOWNLOAD ERROR] {e}")

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

            # Speech threshold is 8% of peak frame energy or floor 0.002
            speech_thresh = max(0.002, 0.08 * max_rms)
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

    def extract_embedding_from_wav(self, wav_bytes: bytes) -> Optional[np.ndarray]:
        """Extracts and normalizes a 512-dim embedding from WAV bytes with silence trimming and gain normalization."""
        if not self._is_initialized or not self.extractor:
            return None
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                sample_rate = wf.getframerate()
                frames = wf.readframes(wf.getnframes())

            if sample_width != 2:
                return None

            audio_data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

            if channels > 1:
                audio_data = audio_data.reshape(-1, channels).mean(axis=1)

            # Clean and normalize audio
            processed_audio = self._trim_and_normalize(audio_data, sample_rate)
            if processed_audio is None or len(processed_audio) < int(sample_rate * 0.25):
                logger.warning("[VOICE VERIFIER] Speech segment too short or silent after trimming")
                return None

            stream = self.extractor.create_stream()
            stream.accept_waveform(sample_rate=sample_rate, waveform=processed_audio)
            stream.input_finished()

            emb = np.array(self.extractor.compute(stream), dtype=np.float32)
            norm = np.linalg.norm(emb)
            if norm > 1e-6:
                emb = emb / norm
            return emb
        except Exception as e:
            logger.error(f"[VOICE VERIFIER] Embedding extraction failed: {e}")
            return None

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

    def verify(self, wav_bytes: bytes, threshold: float = 0.45) -> Tuple[bool, float]:
        """
        Verifies if the audio belongs to the enrolled user.
        Returns: (is_match: bool, similarity_score: float)
        """
        if not self.is_enrolled():
            return True, 1.0

        t0 = time.perf_counter()
        emb = self.extract_embedding_from_wav(wav_bytes)
        if emb is None:
            return False, 0.0

        similarity = float(np.dot(emb, self.enrolled_embedding))
        latency_ms = (time.perf_counter() - t0) * 1000

        is_match = (similarity >= threshold)
        logger.info(f"[VOICE GATE] Score: {similarity:.3f} | Thresh: {threshold:.2f} | Match: {is_match} ({latency_ms:.1f}ms)")
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
