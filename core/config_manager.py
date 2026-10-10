"""
Aether Desktop - Configuration Manager
Handles loading, schema validation, DPAPI secret protection, and persistence
for config.json, including wake_phrase, kill_phrase, and voice settings.
"""

import copy
import json
import os
import tempfile
from typing import Any, Dict, Optional

from security.crypto import protect_secret, unprotect_secret
from core.startup_manager import set_boot_on_startup


def apply_startup_configuration(cfg: dict):
    """Synchronizes registry state with stored config value."""
    boot_enabled = cfg.get("boot_on_startup", False)
    set_boot_on_startup(boot_enabled)


DEFAULT_CURATED_MODELS: Dict[str, List[Dict[str, str]]] = {
    "tier1_options": [
        {"id": "gemini-3.8-flash", "label": "Gemini 3.8 Flash (High Speed)"},
        {"id": "gemini-3.7-flash", "label": "Gemini 3.7 Flash"},
        {"id": "gemini-3.6-flash", "label": "Gemini 3.6 Flash"},
        {"id": "gemini-3.5-flash", "label": "Gemini 3.5 Flash"},
        {"id": "gemini-3.5-flash-lite", "label": "Gemini 3.5 Flash Lite"}
    ],
    "tier2_options": [
        {"id": "gemini-3.8-flash-extended", "label": "Gemini 3.8 Extended (Deep Reasoning)"},
        {"id": "gemini-3.8-live-extended-thinking", "label": "Gemini 3.8 Live Extended Thinking"},
        {"id": "gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro Preview"},
        {"id": "gemini-3.7-flash", "label": "Gemini 3.7 Flash"},
        {"id": "gemini-3.6-flash", "label": "Gemini 3.6 Flash"},
        {"id": "gemini-3.5-flash", "label": "Gemini 3.5 Flash"}
    ],
    "stt_options": [
        {"id": "gemini_live_audio", "label": "Gemini Live Native Audio Stream (Real-Time Bidirectional - Recommended)"},
        {"id": "primary_flash_stt", "label": "Gemini Flash Multimodal Audio (REST One-Shot Fallback)"}
    ],
    "tts_options": [
        {"id": "gemini-live-voice-stream", "label": "Gemini Live Multimodal Voice Stream (~0.5s Realtime WebSocket - Recommended)"},
        {"id": "gemini-3.8-flash-tts", "label": "Gemini 3.8 Flash TTS (Streaming Cloud Speech)"},
        {"id": "edge-neural-tts", "label": "Edge Neural TTS (300+ Regional & Accent Voices)"},
        {"id": "windows-sapi5", "label": "Windows Native SAPI5 / Local Voices (Instantaneous Offline)"},
        {"id": "local-tts", "label": "Local TTS Server (Kokoro / OpenAI / FastTTS)"}
    ]
}

DEFAULT_VOICE_CONFIG: Dict[str, Any] = {
    "agent_name": "Aether",
    "wake_phrase": "Hey Aether",
    "sleep_phrase": "Aether stop listening",
    "kill_phrase": "Aether stop",
    "always_on_mode": "wake_word",
    "tts_endpoint": "gemini_live",
    "tts_voice": "Achernar",
    "tts_speed": 1.0,
    "voice_accent": "default",
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

    # Audio Mode: "always_on" vs "ptt"
    mode = (
        audio_cfg.get("mode")
        or cfg.get("mode")
        or "always_on"
    )
    cfg["mode"] = mode
    audio_cfg["mode"] = mode

    # Push-To-Talk settings
    ptt_type = (
        audio_cfg.get("ptt_type")
        or cfg.get("ptt_type")
        or "hold"
    )
    cfg["ptt_type"] = ptt_type
    audio_cfg["ptt_type"] = ptt_type

    ptt_key = (
        audio_cfg.get("ptt_key")
        or cfg.get("ptt_key")
        or "Space"
    )
    cfg["ptt_key"] = ptt_key
    audio_cfg["ptt_key"] = ptt_key

    ptt_key_display = (
        audio_cfg.get("ptt_key_display")
        or cfg.get("ptt_key_display")
        or ptt_key
    )
    cfg["ptt_key_display"] = ptt_key_display
    audio_cfg["ptt_key_display"] = ptt_key_display

    ptt_vk = audio_cfg.get("ptt_vk") if "ptt_vk" in audio_cfg else cfg.get("ptt_vk", 32)
    cfg["ptt_vk"] = ptt_vk
    audio_cfg["ptt_vk"] = ptt_vk

    ptt_modifiers = audio_cfg.get("ptt_modifiers") if "ptt_modifiers" in audio_cfg else cfg.get("ptt_modifiers", [])
    cfg["ptt_modifiers"] = list(ptt_modifiers or [])
    audio_cfg["ptt_modifiers"] = list(ptt_modifiers or [])

    # UI settings
    ui_cfg = cfg.setdefault("ui", {})
    ui_cfg.setdefault("floating_overlay", "on_minimize")
    ui_cfg.setdefault("minimize_to_tray", True)
    hud_mode = ui_cfg.get("hud_mode") or cfg.get("hud_mode") or "normal"
    ui_cfg["hud_mode"] = hud_mode
    cfg["hud_mode"] = hud_mode

    hud_mode_hotkey = ui_cfg.get("hud_mode_hotkey") or cfg.get("hud_mode_hotkey") or "Ctrl+Space"
    ui_cfg["hud_mode_hotkey"] = hud_mode_hotkey
    cfg["hud_mode_hotkey"] = hud_mode_hotkey

    hud_mode_display = ui_cfg.get("hud_mode_key_display") or cfg.get("hud_mode_key_display") or hud_mode_hotkey
    ui_cfg["hud_mode_key_display"] = hud_mode_display
    cfg["hud_mode_key_display"] = hud_mode_display

    hud_mode_vk = ui_cfg.get("hud_mode_vk") if "hud_mode_vk" in ui_cfg else cfg.get("hud_mode_vk", 32)
    ui_cfg["hud_mode_vk"] = hud_mode_vk
    cfg["hud_mode_vk"] = hud_mode_vk

    hud_mode_mods = ui_cfg.get("hud_mode_modifiers") if "hud_mode_modifiers" in ui_cfg else cfg.get("hud_mode_modifiers", ["Control"])
    ui_cfg["hud_mode_modifiers"] = list(hud_mode_mods or [])
    cfg["hud_mode_modifiers"] = list(hud_mode_mods or [])

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
        or cfg.get("tts_model_endpoint")
        or cfg.get("tts_endpoint")
        or "gemini_live"
    )
    cfg["tts_endpoint"] = tts_endpoint
    cfg["tts_model_endpoint"] = tts_endpoint
    api_cfg.setdefault("tts_endpoint", tts_endpoint)
    api_cfg.setdefault("tts_model_id", tts_endpoint)

    stt_endpoint = (
        api_cfg.get("stt_endpoint")
        or api_cfg.get("stt_model_id")
        or cfg.get("stt_model_endpoint")
        or cfg.get("stt_endpoint")
        or "gemini_live_audio"
    )
    if stt_endpoint in ("gemini-3.8-flash", "gemini-3.5-transcribe", "gemini-3.5-transcribe-live", "gemini-live-native-audio"):
        stt_endpoint = "gemini_live_audio"
    cfg["stt_model_endpoint"] = stt_endpoint
    cfg["stt_endpoint"] = stt_endpoint
    api_cfg["stt_endpoint"] = stt_endpoint
    api_cfg["stt_model_id"] = stt_endpoint

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
    if str(voice_accent).strip().lower() in ("default (native / standard)", "default", "none", "neutral"):
        voice_accent = "default"
    cfg["voice_accent"] = voice_accent
    api_cfg["voice_accent"] = voice_accent

    pipeline_mode = (
        cfg.get("pipeline_mode")
        or api_cfg.get("pipeline_mode")
        or "live"
    ).lower()
    cfg["pipeline_mode"] = pipeline_mode
    api_cfg["pipeline_mode"] = pipeline_mode

    vad_silence = int(cfg.get("vad_trailing_silence_ms") or audio_cfg.get("vad_trailing_silence_ms") or 800)
    cfg["vad_trailing_silence_ms"] = vad_silence
    audio_cfg["vad_trailing_silence_ms"] = vad_silence

    # Curated Models Configuration Store
    if "models" not in cfg or not isinstance(cfg["models"], dict):
        cfg["models"] = copy.deepcopy(DEFAULT_CURATED_MODELS)
    else:
        for opt_key, opt_val in DEFAULT_CURATED_MODELS.items():
            if opt_key not in cfg["models"] or not cfg["models"][opt_key]:
                cfg["models"][opt_key] = copy.deepcopy(opt_val)

    # Dynamic Model Discovery Endpoints
    primary_model = (
        cfg.get("primary_model_endpoint")
        or cfg.get("tier1_fast_model")
        or api_cfg.get("model_id")
        or "gemini-3.8-flash"
    )
    cfg["primary_model_endpoint"] = primary_model
    cfg["tier1_fast_model"] = primary_model
    api_cfg["model_id"] = primary_model

    heavy_model = (
        cfg.get("tier2_heavy_model")
        or api_cfg.get("pro_model_id")
        or "gemini-3.8-flash-extended"
    )
    cfg["tier2_heavy_model"] = heavy_model
    api_cfg["pro_model_id"] = heavy_model

    cfg.setdefault("boot_on_startup", False)
    cfg.setdefault("start_minimized", False)

    # Desktop Vision schema defaults
    vision_cfg = cfg.setdefault("vision", {})
    vision_cfg.setdefault("enabled", True)
    vision_cfg.setdefault("fps", 1.0)
    vision_cfg.setdefault("monitor", "auto")
    vision_cfg.setdefault("endpoint", "gemini-3.8-flash-snapshot")
    vision_cfg.setdefault("resolution", [768, 768])
    vision_cfg.setdefault("jpeg_quality", 70)

    # Audio Software Gate & Language defaults
    software_gate = audio_cfg.get("software_gate") if "software_gate" in audio_cfg else cfg.get("software_gate", False)
    cfg["software_gate"] = bool(software_gate)
    audio_cfg["software_gate"] = bool(software_gate)
    audio_cfg.setdefault("preferred_language", "en-US")

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
                "pipeline_mode",
                "wake_phrase",
                "sleep_phrase",
                "stop_listening_phrase",
                "kill_phrase",
                "always_on_mode",
                "tts_endpoint",
                "tts_model_endpoint",
                "stt_endpoint",
                "stt_model_endpoint",
                "tts_voice",
                "tts_speed",
                "voice_accent",
                "wake_word_enabled",
                "idle_timeout_seconds",
                "vad_trailing_silence_ms",
                "boot_on_startup",
                "start_minimized",
                "primary_model_endpoint",
                "tier1_fast_model",
                "tier2_heavy_model",
                "mode",
                "ptt_type",
                "ptt_key",
                "ptt_key_display",
                "ptt_vk",
                "ptt_modifiers",
                "hud_mode",
                "software_gate",
            ):
                if key in updates:
                    self.config[key] = updates[key]

            if "primary_model_endpoint" in updates:
                self.config["primary_model_endpoint"] = updates["primary_model_endpoint"]
                self.config["tier1_fast_model"] = updates["primary_model_endpoint"]
                self.config.setdefault("api", {})["model_id"] = updates["primary_model_endpoint"]
            if "tier2_heavy_model" in updates:
                self.config["tier2_heavy_model"] = updates["tier2_heavy_model"]
                self.config.setdefault("api", {})["pro_model_id"] = updates["tier2_heavy_model"]
            if "stt_model_endpoint" in updates or "stt_endpoint" in updates:
                stt_val = updates.get("stt_endpoint") or updates.get("stt_model_endpoint")
                self.config["stt_model_endpoint"] = stt_val
                self.config["stt_endpoint"] = stt_val
                self.config.setdefault("api", {})["stt_model_id"] = stt_val
                self.config.setdefault("api", {})["stt_endpoint"] = stt_val
            if "tts_model_endpoint" in updates or "tts_endpoint" in updates:
                tts_val = updates.get("tts_endpoint") or updates.get("tts_model_endpoint")
                self.config["tts_model_endpoint"] = tts_val
                self.config["tts_endpoint"] = tts_val
                self.config.setdefault("api", {})["tts_model_id"] = tts_val
                self.config.setdefault("api", {})["tts_endpoint"] = tts_val
            if "pipeline_mode" in updates:
                pipe_val = str(updates["pipeline_mode"]).lower()
                self.config["pipeline_mode"] = pipe_val
                self.config.setdefault("api", {})["pipeline_mode"] = pipe_val

            # Apply nested dictionary updates
            for section in ("api", "audio", "vision", "security", "ui", "user", "models"):
                if section in updates and isinstance(updates[section], dict):
                    sec_copy = dict(updates[section])
                    sec_copy.pop("new_api_key", None)
                    self.config.setdefault(section, {}).update(sec_copy)
                    if "pipeline_mode" in sec_copy:
                        self.config["pipeline_mode"] = str(sec_copy["pipeline_mode"]).lower()
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
                        self.config["tts_model_endpoint"] = sec_copy["tts_endpoint"]
                    if "tts_model_endpoint" in sec_copy:
                        self.config["tts_model_endpoint"] = sec_copy["tts_model_endpoint"]
                        self.config["tts_endpoint"] = sec_copy["tts_model_endpoint"]
                        self.config.setdefault("api", {})["tts_model_id"] = sec_copy["tts_model_endpoint"]
                    if "mode" in sec_copy:
                        self.config["mode"] = sec_copy["mode"]
                    if "ptt_type" in sec_copy:
                        self.config["ptt_type"] = sec_copy["ptt_type"]
                    if "ptt_key" in sec_copy:
                        self.config["ptt_key"] = sec_copy["ptt_key"]
                    if "ptt_key_display" in sec_copy:
                        self.config["ptt_key_display"] = sec_copy["ptt_key_display"]
                    if "ptt_vk" in sec_copy:
                        self.config["ptt_vk"] = sec_copy["ptt_vk"]
                    if "ptt_modifiers" in sec_copy:
                        self.config["ptt_modifiers"] = sec_copy["ptt_modifiers"]
                    if "hud_mode" in sec_copy:
                        self.config["hud_mode"] = sec_copy["hud_mode"]
                    if "stt_endpoint" in sec_copy:
                        self.config["stt_endpoint"] = sec_copy["stt_endpoint"]
                        self.config["stt_model_endpoint"] = sec_copy["stt_endpoint"]
                        self.config.setdefault("api", {})["stt_endpoint"] = sec_copy["stt_endpoint"]
                        self.config.setdefault("api", {})["stt_model_id"] = sec_copy["stt_endpoint"]
                    if "stt_model_endpoint" in sec_copy:
                        self.config["stt_model_endpoint"] = sec_copy["stt_model_endpoint"]
                        self.config.setdefault("api", {})["stt_model_id"] = sec_copy["stt_model_endpoint"]
                        self.config.setdefault("api", {})["stt_endpoint"] = sec_copy["stt_model_endpoint"]
                    if "stt_model_id" in sec_copy:
                        self.config["stt_model_endpoint"] = sec_copy["stt_model_id"]
                        self.config.setdefault("api", {})["stt_endpoint"] = sec_copy["stt_model_id"]
                    if "tts_model_id" in sec_copy:
                        self.config["tts_model_endpoint"] = sec_copy["tts_model_id"]
                        self.config["tts_endpoint"] = sec_copy["tts_model_id"]
                    if "boot_on_startup" in sec_copy:
                        self.config["boot_on_startup"] = sec_copy["boot_on_startup"]
                    if "start_minimized" in sec_copy:
                        self.config["start_minimized"] = sec_copy["start_minimized"]
                    if "primary_model_endpoint" in sec_copy:
                        self.config["primary_model_endpoint"] = sec_copy["primary_model_endpoint"]
                        self.config["tier1_fast_model"] = sec_copy["primary_model_endpoint"]
                        self.config.setdefault("api", {})["model_id"] = sec_copy["primary_model_endpoint"]
                    if "tier2_heavy_model" in sec_copy:
                        self.config["tier2_heavy_model"] = sec_copy["tier2_heavy_model"]
                        self.config.setdefault("api", {})["pro_model_id"] = sec_copy["tier2_heavy_model"]

        sync_config_schema(self.config)
        try:
            save_config_atomic(self.config, self.config_path)
        except Exception as e:
            print(f"[CONFIG_MANAGER SAVE ERROR] {e}")
        return self.config


def save_config_atomic(config_data: dict, config_path: str = "config.json"):
    """
    Writes configuration to a temporary file before atomically renaming it.
    Prevents corrupting config.json if the process is killed mid-write.
    """
    abs_config_path = os.path.abspath(config_path)
    base_dir = os.path.dirname(abs_config_path)
    os.makedirs(base_dir, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", dir=base_dir, delete=False, suffix=".tmp", encoding="utf-8") as tf:
        temp_name = tf.name
        json.dump(config_data, tf, indent=2)

    # os.replace is an atomic operation on Windows (NTFS) and POSIX
    os.replace(temp_name, abs_config_path)

    # Synchronize startup registry if boot_on_startup is in config_data
    if isinstance(config_data, dict) and "boot_on_startup" in config_data:
        try:
            apply_startup_configuration(config_data)
        except Exception as e:
            print(f"[CONFIG_MANAGER] Failed to apply startup configuration: {e}")


def load_config(config_path: str = "config.json") -> Dict[str, Any]:
    return ConfigManager(config_path=config_path).load()


def save_config(updates: Dict[str, Any], config_path: str = "config.json") -> Dict[str, Any]:
    mgr = ConfigManager(config_path=config_path)
    return mgr.save(updates)

