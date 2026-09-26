"""
Aether Desktop - Configuration Manager
Handles loading, schema validation, DPAPI secret protection, and persistence
for config.json, including wake_phrase, kill_phrase, and voice settings.
"""

import json
import os
from typing import Any, Dict, Optional

from core.security import protect_secret, unprotect_secret

DEFAULT_VOICE_CONFIG: Dict[str, Any] = {
    "agent_name": "Aether",
    "wake_phrase": "Hey Aether",
    "sleep_phrase": "Aether stop listening",
    "kill_phrase": "Aether stop",
    "always_on_mode": "wake_word",
    "tts_endpoint": "gemini_live",
    "tts_voice": "Achernar",
    "tts_speed": 1.0,
    "voice_accent": "Default (Native / Standard)",
    "wake_word_enabled": True,
    "idle_timeout_seconds": 8.0,
}


def sync_config_schema(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensures top-level configuration keys and nested 'api' / 'audio' keys
    are synchronized for wake_phrase, sleep_phrase, kill_phrase, always_on_mode,
    agent_name, and voice settings.
    """
    api_cfg = cfg.setdefault("api", {})
    audio_cfg = cfg.setdefault("audio", {})

    # Agent Name
    agent_name = (
        cfg.get("agent_name")
        or api_cfg.get("agent_name")
        or DEFAULT_VOICE_CONFIG["agent_name"]
    ).strip() or "Aether"
    cfg["agent_name"] = agent_name
    api_cfg["agent_name"] = agent_name

    # Wake Phrase
    wake_phrase = (
        cfg.get("wake_phrase")
        or audio_cfg.get("wake_phrase")
        or api_cfg.get("wake_phrase")
        or f"Hey {agent_name}"
    ).strip() or f"Hey {agent_name}"
    cfg["wake_phrase"] = wake_phrase
    audio_cfg["wake_phrase"] = wake_phrase
    api_cfg["wake_phrase"] = wake_phrase

    # Stop Listening / Sleep Phrase
    sleep_phrase = (
        cfg.get("sleep_phrase")
        or cfg.get("stop_listening_phrase")
        or audio_cfg.get("sleep_phrase")
        or audio_cfg.get("stop_listening_phrase")
        or api_cfg.get("sleep_phrase")
        or f"{agent_name} stop listening"
    ).strip() or f"{agent_name} stop listening"
    cfg["sleep_phrase"] = sleep_phrase
    cfg["stop_listening_phrase"] = sleep_phrase
    audio_cfg["sleep_phrase"] = sleep_phrase
    audio_cfg["stop_listening_phrase"] = sleep_phrase
    api_cfg["sleep_phrase"] = sleep_phrase

    # Kill Phrase / Safe Phrase
    kill_phrase = (
        cfg.get("kill_phrase")
        or audio_cfg.get("kill_phrase")
        or audio_cfg.get("safe_phrase")
        or api_cfg.get("kill_phrase")
        or f"{agent_name} stop"
    ).strip() or f"{agent_name} stop"
    cfg["kill_phrase"] = kill_phrase
    audio_cfg["kill_phrase"] = kill_phrase
    audio_cfg["safe_phrase"] = kill_phrase

    # Always-On Listening Sub-Mode:
    # 1) "always_on" -> Continuous Open Mic (wake_word_enabled=False)
    # 2) "wake_word" -> Listen only with Wake Phrase (auto-sleeps after silence)
    # 3) "wake_sleep_toggle" -> Toggle listening with Wake/Sleep phrases (no auto-sleep timeout)
    raw_always_on_mode = (
        cfg.get("always_on_mode")
        or audio_cfg.get("always_on_mode")
        or api_cfg.get("always_on_mode")
    )
    if raw_always_on_mode in ("always_on", "wake_word", "wake_sleep_toggle"):
        always_on_mode = raw_always_on_mode
        wake_enabled = (always_on_mode != "always_on")
    else:
        wake_enabled = bool(
            cfg.get(
                "wake_word_enabled",
                audio_cfg.get("wake_word_enabled", DEFAULT_VOICE_CONFIG["wake_word_enabled"])
            )
        )
        always_on_mode = "wake_word" if wake_enabled else "always_on"

    cfg["always_on_mode"] = always_on_mode
    audio_cfg["always_on_mode"] = always_on_mode
    api_cfg["always_on_mode"] = always_on_mode

    cfg["wake_word_enabled"] = bool(wake_enabled)
    audio_cfg["wake_word_enabled"] = bool(wake_enabled)

    idle_timeout = float(
        cfg.get(
            "idle_timeout_seconds",
            audio_cfg.get("idle_timeout_seconds", DEFAULT_VOICE_CONFIG["idle_timeout_seconds"])
        )
    )
    cfg["idle_timeout_seconds"] = idle_timeout
    audio_cfg["idle_timeout_seconds"] = idle_timeout

    # TTS & Voice settings
    tts_endpoint = (
        api_cfg.get("tts_endpoint")
        or api_cfg.get("tts_model_id")
        or cfg.get("tts_endpoint")
        or "gemini-live-native"
    )
    cfg["tts_endpoint"] = tts_endpoint
    api_cfg.setdefault("tts_endpoint", tts_endpoint)
    api_cfg.setdefault("tts_model_id", tts_endpoint)

    tts_voice = (
        api_cfg.get("voice_name")
        or cfg.get("tts_voice")
        or DEFAULT_VOICE_CONFIG["tts_voice"]
    )
    cfg["tts_voice"] = tts_voice
    api_cfg["voice_name"] = tts_voice

    tts_speed = float(
        api_cfg.get(
            "voice_speed",
            cfg.get("tts_speed", DEFAULT_VOICE_CONFIG["tts_speed"])
        )
    )
    cfg["tts_speed"] = tts_speed
    api_cfg["voice_speed"] = tts_speed

    voice_accent = (
        api_cfg.get("voice_accent")
        or cfg.get("voice_accent")
        or "default"
    )
    cfg["voice_accent"] = voice_accent
    api_cfg["voice_accent"] = voice_accent

    if "vad_trailing_silence_ms" not in cfg:
        cfg["vad_trailing_silence_ms"] = audio_cfg.get("vad_trailing_silence_ms", 1400)

    return cfg


class ConfigManager:
    """Loads, synchronizes, and persists configuration settings to config.json."""

    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self.config: Dict[str, Any] = self.load()

    def load(self) -> Dict[str, Any]:
        cfg: Dict[str, Any] = {}
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception as e:
                print(f"[CONFIG_MANAGER LOAD ERROR] {e}")
        self.config = sync_config_schema(cfg)
        return self.config

    def save(self, updates: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if updates:
            # Handle API key encryption via Windows DPAPI if provided
            api_updates = updates.get("api", {})
            raw_key = api_updates.get("new_api_key", "").strip()
            if raw_key and not raw_key.startswith("*") and not raw_key.startswith("•"):
                self.config.setdefault("api", {})["api_key_encrypted"] = protect_secret(raw_key)

            # Apply top-level scalar updates
            for key in (
                "agent_name",
                "wake_phrase",
                "sleep_phrase",
                "stop_listening_phrase",
                "kill_phrase",
                "always_on_mode",
                "tts_endpoint",
                "tts_voice",
                "tts_speed",
                "voice_accent",
                "wake_word_enabled",
                "idle_timeout_seconds",
                "vad_trailing_silence_ms",
            ):
                if key in updates:
                    self.config[key] = updates[key]

            # Apply nested dictionary updates
            for section in ("api", "audio", "vision", "security", "ui", "user"):
                if section in updates and isinstance(updates[section], dict):
                    sec_copy = dict(updates[section])
                    sec_copy.pop("new_api_key", None)
                    self.config.setdefault(section, {}).update(sec_copy)
                    # Promote wake_phrase / sleep_phrase / kill_phrase / always_on_mode to top-level before sync
                    if "wake_phrase" in sec_copy:
                        self.config["wake_phrase"] = sec_copy["wake_phrase"]
                    if "sleep_phrase" in sec_copy:
                        self.config["sleep_phrase"] = sec_copy["sleep_phrase"]
                    elif "stop_listening_phrase" in sec_copy:
                        self.config["sleep_phrase"] = sec_copy["stop_listening_phrase"]
                    if "always_on_mode" in sec_copy:
                        self.config["always_on_mode"] = sec_copy["always_on_mode"]
                    if "kill_phrase" in sec_copy:
                        self.config["kill_phrase"] = sec_copy["kill_phrase"]
                    elif "safe_phrase" in sec_copy:
                        self.config["kill_phrase"] = sec_copy["safe_phrase"]
                    if "agent_name" in sec_copy:
                        self.config["agent_name"] = sec_copy["agent_name"]
                    if "voice_name" in sec_copy:
                        self.config["tts_voice"] = sec_copy["voice_name"]
                    if "voice_speed" in sec_copy:
                        self.config["tts_speed"] = float(sec_copy["voice_speed"])
                    if "voice_accent" in sec_copy:
                        self.config["voice_accent"] = sec_copy["voice_accent"]
                    if "tts_endpoint" in sec_copy:
                        self.config["tts_endpoint"] = sec_copy["tts_endpoint"]

        sync_config_schema(self.config)
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print(f"[CONFIG_MANAGER SAVE ERROR] {e}")
        return self.config


def load_config(config_path: str = "config.json") -> Dict[str, Any]:
    return ConfigManager(config_path=config_path).load()


def save_config(updates: Dict[str, Any], config_path: str = "config.json") -> Dict[str, Any]:
    mgr = ConfigManager(config_path=config_path)
    return mgr.save(updates)
