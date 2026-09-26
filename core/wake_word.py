"""
Aether Desktop - Local Wake Word Voice Gate & State Machine
Implements low-power IDLE_LISTENING vs. ACTIVE_CONVERSATION state machine,
600ms rolling pre-roll ring buffer, offline keyword spotter integration,
relaxed phonetic/fuzzy wake & sleep phrase matching, and 3 listening modes:
1) always_on: Continuous open mic (no wake phrase required)
2) wake_word: Listen only with Wake Phrase (auto-sleeps after silence)
3) wake_sleep_toggle: Toggle listening ON with Wake Phrase and OFF with Stop Listening Phrase
"""

import collections
import difflib
import math
import os
import re
import struct
import sys
import threading
import time
import wave
from enum import Enum
from typing import Callable, List, Optional, Tuple

import numpy as np


AETHER_PHONETIC_VARIANTS = (
    "aether", "ether", "ather", "either", "eather", "heather",
    "aker", "acre", "acer", "arthur", "asher", "archer", "actor",
    "after", "alter", "amber", "ava", "eva", "asa", "ever", "easter",
    "cater", "hater", "later", "peter", "feather", "weather", "leather",
    "tether", "nether", "aider", "ader", "laser", "razor", "major",
    "8th", "eighth", "aeth", "eth", "eater", "heater", "meter",
    "leader", "reader", "maker", "baker", "taker", "shaker", "waker",
    "quaker", "faker", "paper", "vapor", "favor", "saver", "safer",
)

WAKE_GREETINGS = (
    "hey", "hi", "ok", "okay", "hello", "yo", "ay", "eh", "he", "hay", "wake", "listen"
)


class AudioGateState(Enum):
    IDLE_LISTENING = "IDLE_LISTENING"
    ACTIVE_CONVERSATION = "ACTIVE_CONVERSATION"


def ensure_wake_sleep_sounds(base_dir: Optional[str] = None) -> Tuple[str, str]:
    """
    Ensures subtle wake.wav and sleep.wav chime files exist under ui/sounds/.
    Returns (wake_wav_path, sleep_wav_path).
    """
    if base_dir is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sounds_dir = os.path.join(base_dir, "ui", "sounds")
    os.makedirs(sounds_dir, exist_ok=True)

    wake_path = os.path.join(sounds_dir, "wake.wav")
    sleep_path = os.path.join(sounds_dir, "sleep.wav")

    if not os.path.exists(wake_path):
        _synthesize_chime_wav(wake_path, freqs=(523.25, 659.25), duration_sec=0.18, volume=0.22)
    if not os.path.exists(sleep_path):
        _synthesize_chime_wav(sleep_path, freqs=(587.33, 440.00), duration_sec=0.20, volume=0.18)

    return wake_path, sleep_path


def _synthesize_chime_wav(
    filepath: str,
    freqs: Tuple[float, float] = (523.25, 659.25),
    duration_sec: float = 0.18,
    sample_rate: int = 24000,
    volume: float = 0.22
):
    """Generates a clean two-tone cosine-tapered WAV chime file."""
    try:
        total_samples = int(sample_rate * duration_sec)
        half = total_samples // 2
        samples = np.zeros(total_samples, dtype=np.float32)

        for idx, (start, end, freq) in enumerate([(0, half, freqs[0]), (half, total_samples, freqs[1])]):
            seg_len = max(1, end - start)
            t = np.arange(seg_len, dtype=np.float32) / sample_rate
            env = np.sin(np.pi * np.arange(seg_len, dtype=np.float32) / seg_len) ** 0.8
            tone = np.sin(2.0 * math.pi * freq * t) * env * volume
            samples[start:end] = tone

        pcm16 = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        with wave.open(filepath, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm16)
    except Exception as e:
        print(f"[WARN] [WAKE_WORD] Could not synthesize sound file {filepath}: {e}")


def play_chime_async(sound_type: str = "wake"):
    """Plays ui/sounds/wake.wav or ui/sounds/sleep.wav asynchronously without blocking audio threads."""
    try:
        wake_path, sleep_path = ensure_wake_sleep_sounds()
        target = wake_path if sound_type == "wake" else sleep_path
        if sys.platform == "win32" and os.path.exists(target):
            import winsound
            winsound.PlaySound(target, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass


class WakeWordDetector:
    def __init__(
        self,
        wake_phrase: str = "Hey Aether",
        sample_rate: int = 16000,
        pre_roll_duration_ms: int = 600,
        idle_timeout_sec: float = 8.0,
        on_wake_callback: Optional[Callable[[], None]] = None,
        on_sleep_callback: Optional[Callable[[], None]] = None,
        sleep_phrase: str = "Aether stop listening",
        listening_mode: str = "wake_word",
    ):
        self.wake_phrase = (wake_phrase or "Hey Aether").strip().lower()
        self.sleep_phrase = (sleep_phrase or "Aether stop listening").strip().lower()
        self.listening_mode = (listening_mode or "wake_word").strip().lower()
        self.sample_rate = sample_rate
        self.pre_roll_duration_ms = pre_roll_duration_ms
        self.idle_timeout_sec = float(idle_timeout_sec)
        self.on_wake_callback = on_wake_callback
        self.on_sleep_callback = on_sleep_callback

        if self.listening_mode == "always_on":
            self.state = AudioGateState.ACTIVE_CONVERSATION
        else:
            self.state = AudioGateState.IDLE_LISTENING
        self.last_speech_time = time.time()

        # Rolling ring buffer for smooth speech continuity
        chunk_size_bytes = int(self.sample_rate * 2 * (pre_roll_duration_ms / 1000.0))
        self.pre_roll_buffer = collections.deque(maxlen=chunk_size_bytes)

        self._oww_model = None
        self._custom_scorer: Optional[Callable[[bytes], float]] = None
        self._pending_hotword_trigger = False

        # Initialize openWakeWord or phonetic keyword spotter
        self._init_model()

    def _init_model(self):
        """Loads lightweight offline keyword model (e.g., openWakeWord / custom verifier)."""
        try:
            ensure_wake_sleep_sounds()
        except Exception:
            pass
        try:
            import openwakeword
            from openwakeword.model import Model
            self._oww_model = Model()
        except Exception:
            self._oww_model = None
        print(
            f"[INFO] [WAKE_WORD] Voice Gate initialized. "
            f"Mode: '{self.listening_mode}' | Wake phrase: '{self.wake_phrase}' | Sleep phrase: '{self.sleep_phrase}'"
        )

    def update_wake_phrase(self, wake_phrase: str):
        """Updates the configured wake phrase at runtime."""
        if wake_phrase and wake_phrase.strip():
            self.wake_phrase = wake_phrase.strip().lower()
            print(f"[INFO] [WAKE_WORD] Wake phrase updated to: '{self.wake_phrase}'")

    def update_sleep_phrase(self, sleep_phrase: str):
        """Updates the configured stop-listening / sleep phrase at runtime."""
        if sleep_phrase and sleep_phrase.strip():
            self.sleep_phrase = sleep_phrase.strip().lower()
            print(f"[INFO] [WAKE_WORD] Sleep phrase updated to: '{self.sleep_phrase}'")

    def update_listening_mode(self, listening_mode: str):
        """Updates the hands-free listening mode ('always_on', 'wake_word', 'wake_sleep_toggle')."""
        if listening_mode and listening_mode.strip():
            prev_mode = self.listening_mode
            self.listening_mode = listening_mode.strip().lower()
            if self.listening_mode == "always_on":
                self.state = AudioGateState.ACTIVE_CONVERSATION
                self.last_speech_time = time.time()
            elif prev_mode == "always_on" and self.listening_mode in ("wake_word", "wake_sleep_toggle"):
                self.state = AudioGateState.IDLE_LISTENING
            print(f"[INFO] [WAKE_WORD] Listening mode updated to: '{self.listening_mode}' (state={self.state.value})")

    def process_frame(self, pcm_bytes: bytes) -> bool:
        """
        Processes an incoming raw audio frame.

        Returns:
            True if audio should be routed to Cortex/VAD, False if suppressed in IDLE.
        """
        now = time.time()

        if self.listening_mode == "always_on":
            self.state = AudioGateState.ACTIVE_CONVERSATION
            return True

        if isinstance(pcm_bytes, np.ndarray):
            arr = np.asarray(pcm_bytes)
            if np.issubdtype(arr.dtype, np.floating):
                pcm_bytes = (np.clip(arr.flatten(), -1.0, 1.0) * 32767).astype(np.int16).tobytes()
            else:
                pcm_bytes = arr.astype(np.int16).tobytes()

        if self.state == AudioGateState.IDLE_LISTENING:
            self.pre_roll_buffer.extend(pcm_bytes)

            # Run offline acoustic score check against wake phrase
            if self._check_hotword_trigger(pcm_bytes):
                self.transition_to_active()
                return True
            return False

        elif self.state == AudioGateState.ACTIVE_CONVERSATION:
            # In 'wake_word' mode, auto-sleep after idle_timeout_sec of silence.
            # In 'wake_sleep_toggle' mode, keep mic ON indefinitely until sleep_phrase is spoken!
            if self.listening_mode != "wake_sleep_toggle":
                if now - self.last_speech_time > self.idle_timeout_sec:
                    self.transition_to_idle()
                    return False
            return True

        return True

    def trigger_hotword(self):
        """Programmatically flags a hotword trigger for the next frame or transitions immediately."""
        self._pending_hotword_trigger = True

    def _check_hotword_trigger(self, pcm_bytes: bytes) -> bool:
        """Evaluates audio window against wake model scores."""
        if self._pending_hotword_trigger:
            self._pending_hotword_trigger = False
            return True

        if self._custom_scorer is not None:
            try:
                score = float(self._custom_scorer(pcm_bytes))
                if score > 0.60:
                    return True
            except Exception:
                pass

        if self._oww_model is not None and pcm_bytes:
            try:
                audio_int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
                if len(audio_int16) > 0:
                    preds = self._oww_model.predict(audio_int16)
                    if isinstance(preds, dict):
                        for model_name, score in preds.items():
                            if float(score) > 0.60:
                                return True
            except Exception:
                pass

        return False

    @staticmethod
    def _extract_remainder_after_word_index(raw: str, end_word_idx: int) -> str:
        """Extracts remaining command text after `end_word_idx` (exclusive, 0-based word count)."""
        tokens = list(re.finditer(r"\S+", raw))
        if end_word_idx <= 0 or not tokens:
            return raw.strip()
        if end_word_idx >= len(tokens):
            return ""
        start_pos = tokens[end_word_idx].start()
        return raw[start_pos:].lstrip(" ,.!?:;-").strip()

    @staticmethod
    def _strip_leading_wake_decorators(remainder: str) -> str:
        """
        Strips activation phrases such as 'start listening', 'begin listening', 'resume listening',
        'wake up', 'listen' from the remainder after a wake word so pure toggle-on commands
        do not get sent to the LLM as chat questions.
        """
        if not remainder:
            return ""
        rem = remainder.lstrip(" ,.!?:;-").strip()
        pattern = (
            r"^(?:please\s+)?"
            r"(?:start\s+listening(?:\s+to\s+me)?|begin\s+listening|resume\s+listening|"
            r"continue\s+listening|keep\s+listening|wake\s+up|listen(?:\s+up|\s+to\s+me)?|"
            r"turn\s+on(?:\s+microphone|\s+mic)?|unmute(?:\s+microphone|\s+mic)?)"
            r"(?:\s+now|\s+please)*[\s,\.!?\-:]*"
        )
        rem = re.sub(pattern, "", rem, flags=re.IGNORECASE).strip()
        return rem

    def matches_wake_phrase(self, text: str) -> Tuple[bool, str]:
        """
        Evaluates whether a spoken transcript starts with or contains the configured wake phrase
        (or natural toggle-on commands like 'Aether, start listening'), using relaxed phonetic
        equivalents (e.g. 'Hey Aker', 'Hey Ether', 'Aker start listening') and fuzzy similarity.
        Returns (matched: bool, remaining_text: str).
        """
        if not text or not text.strip():
            return False, ""

        raw = text.strip()
        clean = re.sub(r"[^\w\s]", " ", raw.lower())
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            return False, ""

        target = re.sub(r"[^\w\s]", " ", self.wake_phrase.lower())
        target = re.sub(r"\s+", " ", target).strip()
        if not target:
            return True, raw

        # 1. Exact, natural activation ('aether start listening'), and phonetic candidate regex matching
        candidates: List[str] = [
            target,
            "aether start listening",
            "hey aether start listening",
            "start listening",
            "resume listening",
            "begin listening",
            "aether wake up",
            "wake up aether",
            "aether listen",
        ]
        if "aether" in target or True:
            for phonetic in AETHER_PHONETIC_VARIANTS:
                if "aether" in target:
                    cand = target.replace("aether", phonetic)
                    if cand not in candidates:
                        candidates.append(cand)
                for suffix in ("start listening", "resume listening", "begin listening", "wake up", "listen"):
                    act_cand = f"{phonetic} {suffix}"
                    if act_cand not in candidates:
                        candidates.append(act_cand)
            # Also allow any greeting + any phonetic variant (e.g. "hey aker", "hi aker", "ok aker")
            for greet in WAKE_GREETINGS:
                for phonetic in AETHER_PHONETIC_VARIANTS:
                    gp = f"{greet} {phonetic}"
                    if gp not in candidates:
                        candidates.append(gp)

        for cand in candidates:
            pattern = r"\b" + r"\s+".join(re.escape(w) for w in cand.split()) + r"\b"
            m = re.search(pattern, clean)
            if m:
                raw_match = re.search(
                    r"\b" + r"[\s,\.!?\-]+".join(re.escape(w) for w in cand.split()) + r"[\s,\.!?\-:]*",
                    raw,
                    flags=re.IGNORECASE
                )
                if raw_match:
                    remainder = raw[raw_match.end():].strip()
                else:
                    words_before = len(clean[:m.end()].split())
                    remainder = self._extract_remainder_after_word_index(raw, words_before)
                return True, self._strip_leading_wake_decorators(remainder)

        # 2. Relaxed Fuzzy & Greeting+Name Matching
        words = clean.split()
        target_words = target.split()
        target_name = target_words[-1] if target_words else "aether"

        # 2a. Greeting + Fuzzy Name match (e.g. "Hey Aker", "Hey <anything ~45%+ similar to target_name>")
        for idx in range(min(len(words) - 1, 4)):
            w_first = words[idx]
            w_second = words[idx + 1]
            first_is_greeting = (
                w_first in WAKE_GREETINGS
                or (target_words and difflib.SequenceMatcher(None, w_first, target_words[0]).ratio() >= 0.65)
            )
            if first_is_greeting:
                name_sim = difflib.SequenceMatcher(None, w_second, target_name).ratio()
                is_phonetic_variant = ("aether" in target and w_second in AETHER_PHONETIC_VARIANTS)
                starts_like_target = (
                    len(w_second) >= 3
                    and len(target_name) >= 3
                    and (w_second[0] == target_name[0] or (w_second[0] in "ae" and target_name[0] in "ae"))
                    and name_sim >= 0.38
                )
                if is_phonetic_variant or name_sim >= 0.46 or starts_like_target:
                    remainder = self._extract_remainder_after_word_index(raw, idx + 2)
                    return True, self._strip_leading_wake_decorators(remainder)

        # 2b. Sliding window fuzzy phrase similarity (>= 0.64)
        win_len = len(target_words)
        if win_len > 0 and len(words) >= win_len:
            for idx in range(min(len(words) - win_len + 1, 4)):
                window_str = " ".join(words[idx:idx + win_len])
                ratio = difflib.SequenceMatcher(None, window_str, target).ratio()
                if ratio >= 0.64:
                    remainder = self._extract_remainder_after_word_index(raw, idx + win_len)
                    return True, self._strip_leading_wake_decorators(remainder)

        # 2c. Direct leading agent name invocation (e.g., "Aether, start listening" or "Aether what time is it")
        if words:
            first_word = words[0]
            name_sim = difflib.SequenceMatcher(None, first_word, target_name).ratio()
            if (
                first_word == target_name
                or ("aether" in target and first_word in ("aether", "ether", "ather", "aker", "acre", "arthur", "asher"))
                or name_sim >= 0.75
            ):
                remainder = self._extract_remainder_after_word_index(raw, 1)
                return True, self._strip_leading_wake_decorators(remainder)

        return False, raw

    def matches_sleep_phrase(self, text: str) -> bool:
        """
        Evaluates whether a spoken transcript contains the configured Stop Listening / Sleep phrase
        (e.g. 'Aether stop listening', 'Aker stop listening', 'stop listening', 'go to sleep').
        Never matches positive activation phrases like 'Aether, start listening'.
        """
        if not text or not text.strip():
            return False

        clean = re.sub(r"[^\w\s]", " ", text.strip().lower())
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            return False

        target = re.sub(r"[^\w\s]", " ", self.sleep_phrase.lower())
        target = re.sub(r"\s+", " ", target).strip()

        wake_target = re.sub(r"[^\w\s]", " ", self.wake_phrase.lower())
        wake_target = re.sub(r"\s+", " ", wake_target).strip()

        # Guard: Never classify explicit wake / start-listening commands as sleep phrases!
        if wake_target and clean == wake_target:
            return False
        words = clean.split()
        stop_action_words = {"stop", "pause", "mute", "sleep", "standby", "off", "disable", "quiet", "silence", "halt"}
        start_action_words = {"start", "begin", "resume", "wake", "continue", "keep", "enable", "unmute"}
        if any(w in start_action_words for w in words) and not any(w in stop_action_words for w in words):
            return False

        sleep_candidates: List[str] = []
        if target:
            sleep_candidates.append(target)
            if "aether" in target:
                for phonetic in AETHER_PHONETIC_VARIANTS:
                    sleep_candidates.append(target.replace("aether", phonetic))

        # Standard natural sleep / stop-listening phrases
        sleep_candidates.extend([
            "stop listening",
            "pause listening",
            "mute listening",
            "go to sleep",
            "aether stop listening",
            "aether go to sleep",
            "aether standby",
            "aether sleep",
        ])
        for phonetic in ("ether", "ather", "aker", "acre", "arthur", "asher"):
            sleep_candidates.append(f"{phonetic} stop listening")
            sleep_candidates.append(f"{phonetic} go to sleep")

        for cand in sleep_candidates:
            pattern = r"\b" + r"\s+".join(re.escape(w) for w in cand.split()) + r"\b"
            if re.search(pattern, clean):
                return True

        # Fuzzy sliding window match against target sleep phrase (requires a stop-like action word)
        has_stop_like_word = any(
            w in stop_action_words or any(difflib.SequenceMatcher(None, w, sw).ratio() >= 0.75 for sw in stop_action_words)
            for w in words
        )
        if not has_stop_like_word:
            return False

        target_words = target.split()
        win_len = len(target_words)
        if win_len > 0 and len(words) >= win_len:
            for idx in range(len(words) - win_len + 1):
                window_str = " ".join(words[idx:idx + win_len])
                if difflib.SequenceMatcher(None, window_str, target).ratio() >= 0.68:
                    return True

        return False

    def check_transcript(self, text: str) -> bool:
        """
        Checks if a transcribed utterance contains the wake phrase and transitions
        to ACTIVE_CONVERSATION if currently in IDLE_LISTENING.
        """
        matched, _ = self.matches_wake_phrase(text)
        if matched and self.state == AudioGateState.IDLE_LISTENING:
            self.transition_to_active()
        return matched

    def check_sleep_transcript(self, text: str) -> bool:
        """
        Checks if a transcribed utterance matches the Stop Listening phrase and transitions
        to IDLE_LISTENING if currently active.
        """
        if self.matches_sleep_phrase(text):
            if self.state != AudioGateState.IDLE_LISTENING or self.listening_mode == "always_on":
                if self.listening_mode == "always_on":
                    # Allow voice command to toggle into standby mode
                    self.listening_mode = "wake_sleep_toggle"
                self.transition_to_idle()
            return True
        return False

    def transition_to_active(self):
        """Transitions state machine to ACTIVE."""
        self.state = AudioGateState.ACTIVE_CONVERSATION
        self.last_speech_time = time.time()
        print("[INFO] [WAKE_WORD] Wake phrase detected! Audio gate opened.")
        if self.on_wake_callback:
            self.on_wake_callback()

    def transition_to_idle(self):
        """Transitions state machine to IDLE."""
        self.state = AudioGateState.IDLE_LISTENING
        self.pre_roll_buffer.clear()
        print("[INFO] [WAKE_WORD] Entering IDLE_LISTENING (Standby).")
        if self.on_sleep_callback:
            self.on_sleep_callback()

    def record_active_turn(self):
        """Refreshes active conversation timer during back-and-forth speech."""
        self.last_speech_time = time.time()

    def get_pre_roll_bytes(self) -> bytes:
        """Drains the pre-roll ring buffer so speech beginning during the wake phrase is preserved."""
        return bytes(self.pre_roll_buffer)
