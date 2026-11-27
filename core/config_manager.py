"""
Aether Desktop - Configuration Manager
Handles loading, schema validation, DPAPI secret protection, and persistence
for config.json, including wake_phrase, kill_phrase, and voice settings.
"""

import copy
import json
import os
import tempfile
from typing import Any, Dict, List, Optional

from security.crypto import protect_secret, unprotect_secret

encrypt_secret = protect_secret
decrypt_secret = unprotect_secret
from core.startup_manager import set_boot_on_startup


def apply_startup_configuration(cfg: dict):
    """Synchronizes registry state with stored config value."""
    boot_enabled = cfg.get("boot_on_startup", False)
    set_boot_on_startup(boot_enabled)


DEFAULT_CURATED_MODELS: Dict[str, List[Dict[str, str]]] = {
    "primary_options": [
        {"id": "gemini-3.8-live", "display_name": "3.8 - Live (Default)", "label": "3.8 - Live (Default)"},
        {"id": "custom", "display_name": "Custom", "label": "Custom"}
    ],
    "tier1_options": [
        {"id": "gemini-3.8-live", "display_name": "3.8 - Live (Default)", "label": "3.8 - Live (Default)"},
        {"id": "custom", "display_name": "Custom", "label": "Custom"}
    ],
    "tier2_moderate_options": [
        {"id": "gemini-4-hydrogen", "display_name": "4.0 - Hydrogen (Default)", "label": "4.0 - Hydrogen (Default)"},
        {"id": "custom", "display_name": "Custom", "label": "Custom"}
    ],
    "tier2_options": [
        {"id": "gemini-4-neon", "display_name": "4.0 - Neon (Default)", "label": "4.0 - Neon (Default)"},
        {"id": "gemini-4-carbon", "display_name": "4.0 - Carbon", "label": "4.0 - Carbon"},
        {"id": "custom", "display_name": "Custom", "label": "Custom"}
    ],
    "tier3_options": [
        {"id": "gemini-4-neon", "display_name": "4.0 - Neon (Default)", "label": "4.0 - Neon (Default)"},
        {"id": "gemini-4-carbon", "display_name": "4.0 - Carbon", "label": "4.0 - Carbon"},
        {"id": "custom", "display_name": "Custom", "label": "Custom"}
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
    "tts_pace": "natural",
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
    ui_cfg.setdefault("theme", "system")
    hud_mode = str(ui_cfg.get("hud_mode") or cfg.get("hud_mode") or "normal").lower().strip()
    if hud_mode not in ("dot", "mini", "normal", "max"):
        hud_mode = "normal"
    ui_cfg["hud_mode"] = hud_mode
    cfg["hud_mode"] = hud_mode

    def _norm_hud_dims(raw_dims, default_dims):
        try:
            if isinstance(raw_dims, (list, tuple)) and len(raw_dims) == 2:
                w = max(200, min(1920, int(round(float(raw_dims[0])))))
                h = max(80, min(1200, int(round(float(raw_dims[1])))))
                return [w, h]
        except (TypeError, ValueError):
            pass
        return list(default_dims)

    ui_cfg["hud_normal_size"] = _norm_hud_dims(ui_cfg.get("hud_normal_size"), (440, 180))
    ui_cfg["hud_max_size"] = _norm_hud_dims(ui_cfg.get("hud_max_size"), (560, 480))

    raw_positions = ui_cfg.get("hud_positions")
    had_positions_dict = isinstance(raw_positions, dict)
    hud_positions = {}
    for m in ("mini", "normal", "max"):
        pos = raw_positions.get(m) if had_positions_dict else None
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            try:
                hud_positions[m] = [int(round(float(pos[0]))), int(round(float(pos[1])))]
            except (TypeError, ValueError):
                hud_positions[m] = None
        else:
            hud_positions[m] = None
    if not had_positions_dict and hud_mode in ("mini", "normal", "max"):
        leg_x = ui_cfg.get("overlay_x")
        leg_y = ui_cfg.get("overlay_y")
        if leg_x is not None and leg_y is not None:
            try:
                hud_positions[hud_mode] = [int(round(float(leg_x))), int(round(float(leg_y)))]
            except (TypeError, ValueError):
                pass
    ui_cfg["hud_positions"] = hud_positions

    # Dot Mode & Custom HUD Status Colors
    from ui.dot_overlay import (
        DEFAULT_HUD_STATUS_COLORS,
        normalize_dot_position,
        normalize_dot_size,
        sanitize_hud_colors,
    )
    from ui.vr_dot_overlay import (
        normalize_vr_dot_anchor,
        normalize_vr_dot_depth,
        normalize_vr_dot_inset,
        normalize_vr_dot_scale,
        normalize_vr_dot_x,
        normalize_vr_dot_y,
    )
    from ui.vr_overlay_manager import (
        normalize_vr_hmd_smoothing,
        normalize_vr_hud_anchor,
        normalize_vr_hud_curvature,
        normalize_vr_hud_depth,
        normalize_vr_hud_offset_x,
        normalize_vr_hud_offset_y,
        normalize_vr_hud_scale,
        normalize_vr_pitch,
        normalize_vr_roll,
        normalize_vr_wrist_smoothing,
        normalize_vr_yaw,
    )
    ui_cfg["dot_size"] = normalize_dot_size(ui_cfg.get("dot_size", 22))
    ui_cfg["dot_position"] = normalize_dot_position(ui_cfg.get("dot_position", "top_right"))
    ui_cfg["dot_monitor"] = str(ui_cfg.get("dot_monitor") or "auto")
    ui_cfg["hud_colors"] = sanitize_hud_colors(ui_cfg.get("hud_colors") or DEFAULT_HUD_STATUS_COLORS)
    ui_cfg["vr_dot_enabled"] = bool(ui_cfg.get("vr_dot_enabled", True))
    ui_cfg["vr_dot_anchor"] = normalize_vr_dot_anchor(ui_cfg.get("vr_dot_anchor", "cockpit"))
    ui_cfg["vr_dot_inset"] = normalize_vr_dot_inset(ui_cfg.get("vr_dot_inset", 0.62))
    ui_cfg["vr_dot_x"] = normalize_vr_dot_x(ui_cfg.get("vr_dot_x", 0.0))
    ui_cfg["vr_dot_y"] = normalize_vr_dot_y(ui_cfg.get("vr_dot_y", -0.12))
    ui_cfg["vr_dot_depth"] = normalize_vr_dot_depth(ui_cfg.get("vr_dot_depth", 1.25))
    ui_cfg["vr_dot_scale"] = normalize_vr_dot_scale(ui_cfg.get("vr_dot_scale", 1.00))
    ui_cfg["vr_hud_enabled"] = bool(ui_cfg.get("vr_hud_enabled", True))
    ui_cfg["vr_hud_anchor"] = normalize_vr_hud_anchor(ui_cfg.get("vr_hud_anchor", "cockpit"))
    ui_cfg["vr_hud_depth"] = normalize_vr_hud_depth(ui_cfg.get("vr_hud_depth", 1.25))
    ui_cfg["vr_hud_scale"] = normalize_vr_hud_scale(ui_cfg.get("vr_hud_scale", 1.00))
    ui_cfg["vr_hud_offset_x"] = normalize_vr_hud_offset_x(ui_cfg.get("vr_hud_offset_x", 0.0))
    ui_cfg["vr_hud_offset_y"] = normalize_vr_hud_offset_y(ui_cfg.get("vr_hud_offset_y", -0.12))
    ui_cfg["vr_hud_curvature"] = normalize_vr_hud_curvature(ui_cfg.get("vr_hud_curvature", 0.12))
    ui_cfg["vr_hud_yaw"] = normalize_vr_yaw(ui_cfg.get("vr_hud_yaw", 0.0))
    ui_cfg["vr_hud_pitch"] = normalize_vr_pitch(ui_cfg.get("vr_hud_pitch", 0.0))
    from ui.openxr_overlay_backend import normalize_vr_runtime
    ui_cfg["vr_hud_roll"] = normalize_vr_roll(ui_cfg.get("vr_hud_roll", 0.0))
    ui_cfg["vr_wrist_auto_hide"] = bool(ui_cfg.get("vr_wrist_auto_hide", True))
    ui_cfg["vr_wrist_smoothing"] = normalize_vr_wrist_smoothing(ui_cfg.get("vr_wrist_smoothing", 0.60))
    ui_cfg["vr_hmd_smoothing"] = normalize_vr_hmd_smoothing(ui_cfg.get("vr_hmd_smoothing", 0.60))
    ui_cfg["vr_runtime"] = normalize_vr_runtime(ui_cfg.get("vr_runtime", "auto"))

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
    if stt_endpoint in ("gemini-3.8-flash", "gemini-3.5-transcribe-live", "gemini-live-native-audio"):
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

    tts_pace = str(api_cfg.get("tts_pace") or cfg.get("tts_pace") or "natural").strip().lower()
    if tts_pace not in ("measured", "natural", "brisk"):
        tts_pace = "natural"
    cfg["tts_pace"] = tts_pace
    api_cfg["tts_pace"] = tts_pace

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
        api_cfg.get("pipeline_mode")
        or cfg.get("pipeline_mode")
        or "live"
    ).lower()
    cfg["pipeline_mode"] = pipeline_mode
    api_cfg["pipeline_mode"] = pipeline_mode

    if api_cfg.get("live_model_id") in (None, "", "gemini-3.1-flash-live-preview"):
        api_cfg["live_model_id"] = "gemini-3.8-live"

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
        # Upgrade legacy/pre-3-tier curated lists cleanly to Hybrid + Custom format
        t1_opts = cfg["models"].get("tier1_options") or []
        if not any(str(o.get("id", "")).lower() == "custom" for o in t1_opts if isinstance(o, dict)):
            cfg["models"]["primary_options"] = copy.deepcopy(DEFAULT_CURATED_MODELS["primary_options"])
            cfg["models"]["tier1_options"] = copy.deepcopy(DEFAULT_CURATED_MODELS["tier1_options"])
        t2m_opts = cfg["models"].get("tier2_moderate_options") or []
        if not any(str(o.get("id", "")).lower() == "gemini-4-hydrogen" for o in t2m_opts if isinstance(o, dict)):
            cfg["models"]["tier2_moderate_options"] = copy.deepcopy(DEFAULT_CURATED_MODELS["tier2_moderate_options"])
        t2_opts = cfg["models"].get("tier2_options") or []
        if not any(str(o.get("id", "")).lower() == "gemini-4-neon" for o in t2_opts if isinstance(o, dict)):
            cfg["models"]["tier2_options"] = copy.deepcopy(DEFAULT_CURATED_MODELS["tier2_options"])
            cfg["models"]["tier3_options"] = copy.deepcopy(DEFAULT_CURATED_MODELS["tier3_options"])

    # Dynamic Model Discovery Endpoints (Tier 1 Primary 3.8 Live -> Tier 2 Moderate 4.0 Hydrogen -> Tier 3 Heavy 4.0 Neon)
    primary_model = (
        cfg.get("primary_model_endpoint")
        or cfg.get("tier1_fast_model")
        or api_cfg.get("model_id")
        or "gemini-3.8-live"
    )
    if primary_model in ("gemini-4-hydrogen", "gemini-4.0-hydrogen", "custom", ""):
        primary_model = "gemini-3.8-live" if primary_model in ("custom", "") else "gemini-4-hydrogen:instant"
    cfg["primary_model_endpoint"] = primary_model
    cfg["tier1_fast_model"] = primary_model
    api_cfg["model_id"] = primary_model
    if "live" in str(primary_model).lower():
        api_cfg["live_model_id"] = primary_model

    mod_lvl_raw = str(
        cfg.get("tier2_moderate_level")
        or api_cfg.get("tier2_moderate_level")
        or "medium"
    ).strip().lower()
    if mod_lvl_raw == "extra":
        mod_lvl_raw = "xhigh"
    if mod_lvl_raw not in ("low", "medium", "high", "xhigh"):
        mod_lvl_raw = "medium"

    moderate_model = (
        cfg.get("tier2_moderate_model")
        or api_cfg.get("moderate_model_id")
        or f"gemini-4-hydrogen:{mod_lvl_raw}"
    )
    if moderate_model in ("gemini-4-hydrogen", "gemini-4.0-hydrogen", "gemini-4-hydrogen:instant", "custom", ""):
        moderate_model = f"gemini-4-hydrogen:{mod_lvl_raw}"
    if ":" in str(moderate_model):
        mod_lvl_part = str(moderate_model).split(":", 1)[1].strip().lower()
        if mod_lvl_part == "extra":
            mod_lvl_part = "xhigh"
        if mod_lvl_part in ("low", "medium", "high", "xhigh"):
            mod_lvl_raw = mod_lvl_part
    cfg["tier2_moderate_model"] = moderate_model
    cfg["tier2_moderate_level"] = mod_lvl_raw
    api_cfg["moderate_model_id"] = moderate_model
    api_cfg["tier2_moderate_level"] = mod_lvl_raw

    ttrpg_lvl_raw = str(
        cfg.get("ttrpg_thinking_level")
        or api_cfg.get("ttrpg_thinking_level")
        or "medium"
    ).strip().lower()
    if ttrpg_lvl_raw == "extra":
        ttrpg_lvl_raw = "xhigh"
    if ttrpg_lvl_raw not in ("low", "medium", "high", "xhigh"):
        ttrpg_lvl_raw = "medium"

    ttrpg_model = (
        cfg.get("ttrpg_model_endpoint")
        or api_cfg.get("ttrpg_model_id")
        or f"gemini-4-hydrogen:{ttrpg_lvl_raw}"
    )
    if ttrpg_model in ("gemini-4-hydrogen", "gemini-4.0-hydrogen", "gemini-4-hydrogen:instant", "custom", ""):
        ttrpg_model = f"gemini-4-hydrogen:{ttrpg_lvl_raw}"
    elif ttrpg_model in ("gemini-4-neon", "gemini-4.0-neon"):
        ttrpg_model = f"gemini-4-neon:{ttrpg_lvl_raw}"
    elif ttrpg_model in ("gemini-4-argon", "gemini-4-argon:low", "gemini-4-argon:medium", "gemini-4-argon:high"):
        ttrpg_model = f"gemini-4-neon:{ttrpg_lvl_raw}"
    if ":" in str(ttrpg_model):
        t_base, t_lvl = str(ttrpg_model).split(":", 1)
        t_lvl = t_lvl.strip().lower()
        if t_lvl == "extra":
            t_lvl = "xhigh"
        if "3.8-flash" in t_base.lower():
            ttrpg_model = t_base.strip()
        elif t_lvl in ("low", "medium", "high", "xhigh"):
            ttrpg_lvl_raw = t_lvl
    cfg["ttrpg_model_endpoint"] = ttrpg_model
    cfg["ttrpg_thinking_level"] = ttrpg_lvl_raw
    api_cfg["ttrpg_model_id"] = ttrpg_model
    api_cfg["ttrpg_thinking_level"] = ttrpg_lvl_raw

    existing_heavy_lvl = str(
        cfg.get("tier3_thinking_level")
        or cfg.get("tier2_thinking_level")
        or api_cfg.get("tier3_thinking_level")
        or api_cfg.get("tier2_thinking_level")
        or "high"
    ).strip().lower()
    if existing_heavy_lvl == "extra":
        existing_heavy_lvl = "xhigh"
    if existing_heavy_lvl not in ("low", "medium", "high", "xhigh"):
        existing_heavy_lvl = "high"

    heavy_model = (
        cfg.get("tier3_heavy_model")
        or cfg.get("tier2_heavy_model")
        or api_cfg.get("pro_model_id")
        or f"gemini-4-neon:{existing_heavy_lvl}"
    )
    # Migrate legacy/bare defaults to curated Neon/Carbon profile IDs
    if heavy_model in ("gemini-4-neon", "gemini-4.0-neon", "custom", ""):
        heavy_model = f"gemini-4-neon:{existing_heavy_lvl}"
    elif heavy_model in ("gemini-4-carbon", "gemini-4.0-carbon"):
        heavy_model = f"gemini-4-carbon:{existing_heavy_lvl}"
    elif heavy_model in (
        "gemini-4-argon",
        "gemini-4-argon:low",
        "gemini-4-argon:medium",
        "gemini-4-argon:high",
        "gemini-3.1-pro-preview",
        "gemini-3.8-pro",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.8-flash-extended",
    ):
        heavy_model = "gemini-4-neon:high"
        existing_heavy_lvl = "high"

    cfg["tier3_heavy_model"] = heavy_model
    cfg["tier2_heavy_model"] = heavy_model
    api_cfg["pro_model_id"] = heavy_model

    thinking_level = existing_heavy_lvl
    if ":" in str(heavy_model):
        lvl_part = str(heavy_model).split(":", 1)[1].strip().lower()
        if lvl_part == "extra":
            lvl_part = "xhigh"
        if lvl_part:
            thinking_level = lvl_part
    cfg["tier2_thinking_level"] = thinking_level
    cfg["tier3_thinking_level"] = thinking_level
    api_cfg["tier2_thinking_level"] = thinking_level
    api_cfg["tier3_thinking_level"] = thinking_level

    cfg.setdefault("boot_on_startup", False)
    cfg.setdefault("start_minimized", False)
    cfg.setdefault("auto_check_updates", True)
    cfg.setdefault("skipped_update_version", "")

    # Desktop Vision schema defaults (continuous Live video OFF by default; on-demand snapshots always active)
    vision_cfg = cfg.setdefault("vision", {})
    vision_cfg.setdefault("enabled", False)
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

    # Hardware Telemetry & Thermal Warning Threshold defaults
    hw_cfg = cfg.setdefault("hardware_telemetry", {})
    hw_cfg.setdefault("enabled", True)
    hw_cfg.setdefault("alerts_enabled", True)
    hw_cfg.setdefault("verbal_warnings", True)
    hw_cfg.setdefault("cpu_temp_alert_threshold_c", 95)
    hw_cfg.setdefault("gpu_temp_alert_threshold_c", 85)
    hw_cfg.setdefault("vram_alert_threshold_pct", 95)
    hw_cfg.setdefault("alert_sustained_seconds", 15)

    return cfg


def _resolve_config_path(config_path: Optional[str] = None) -> str:
    from core.paths import is_frozen, user_data_path
    if config_path is None or (config_path == "config.json" and is_frozen()):
        return user_data_path("config.json")
    return config_path


class ConfigManager:
    """Loads, synchronizes, and persists configuration settings to config.json."""

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = _resolve_config_path(config_path)
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
            api_updates = updates.get("api")
            if isinstance(api_updates, dict):
                raw_key = str(api_updates.get("new_api_key") or "").strip()
                if raw_key and not raw_key.startswith("*") and not raw_key.startswith("•"):
                    self.config.setdefault("api", {})["api_key_encrypted"] = protect_secret(raw_key)

            # Helper to update a model's ':level' suffix when only a thinking-level key was changed
            def _retag_model_level(model_str: Any, new_level: Any) -> Any:
                lvl = str(new_level or "").strip().lower()
                if lvl == "extra":
                    lvl = "xhigh"
                if lvl not in ("low", "medium", "high", "xhigh"):
                    return model_str
                s = str(model_str or "").strip()
                if ":" in s:
                    base = s.split(":", 1)[0].strip()
                    if base:
                        return f"{base}:{lvl}"
                return model_str

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
                "tts_pace",
                "voice_accent",
                "wake_word_enabled",
                "idle_timeout_seconds",
                "vad_trailing_silence_ms",
                "boot_on_startup",
                "start_minimized",
                "auto_check_updates",
                "skipped_update_version",
                "primary_model_endpoint",
                "ttrpg_model_endpoint",
                "ttrpg_thinking_level",
                "tier1_fast_model",
                "tier2_moderate_model",
                "tier2_moderate_level",
                "tier2_heavy_model",
                "tier3_heavy_model",
                "tier2_thinking_level",
                "tier3_thinking_level",
                "tier2_thinking_budget",
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

            # Mirror top-level scalar updates into nested sections before nested updates run
            # so sync_config_schema() does not revert top-level-only updates with stale nested values.
            api_sec = self.config.setdefault("api", {})
            audio_sec = self.config.setdefault("audio", {})
            ui_sec = self.config.setdefault("ui", {})

            if "tts_voice" in updates:
                api_sec["voice_name"] = updates["tts_voice"]
            if "tts_speed" in updates:
                api_sec["voice_speed"] = float(updates["tts_speed"])
            if "tts_pace" in updates:
                api_sec["tts_pace"] = updates["tts_pace"]
            if "voice_accent" in updates:
                api_sec["voice_accent"] = updates["voice_accent"]
            for audio_key in ("mode", "ptt_type", "ptt_key", "ptt_key_display", "ptt_vk", "ptt_modifiers", "software_gate"):
                if audio_key in updates:
                    audio_sec[audio_key] = updates[audio_key]
            if "hud_mode" in updates:
                ui_sec["hud_mode"] = updates["hud_mode"]

            if "primary_model_endpoint" in updates:
                self.config["primary_model_endpoint"] = updates["primary_model_endpoint"]
                self.config["tier1_fast_model"] = updates["primary_model_endpoint"]
                api_sec["model_id"] = updates["primary_model_endpoint"]
            if "ttrpg_model_endpoint" in updates:
                self.config["ttrpg_model_endpoint"] = updates["ttrpg_model_endpoint"]
                api_sec["ttrpg_model_id"] = updates["ttrpg_model_endpoint"]
            if "ttrpg_thinking_level" in updates:
                self.config["ttrpg_thinking_level"] = updates["ttrpg_thinking_level"]
                api_sec["ttrpg_thinking_level"] = updates["ttrpg_thinking_level"]
                if "ttrpg_model_endpoint" not in updates and not (isinstance(api_updates, dict) and "ttrpg_model_id" in api_updates):
                    retagged = _retag_model_level(self.config.get("ttrpg_model_endpoint"), updates["ttrpg_thinking_level"])
                    self.config["ttrpg_model_endpoint"] = retagged
                    api_sec["ttrpg_model_id"] = retagged
            if "tier2_moderate_model" in updates:
                self.config["tier2_moderate_model"] = updates["tier2_moderate_model"]
                api_sec["moderate_model_id"] = updates["tier2_moderate_model"]
            if "tier3_heavy_model" in updates or "tier2_heavy_model" in updates:
                heavy_val = updates.get("tier3_heavy_model") or updates.get("tier2_heavy_model")
                self.config["tier3_heavy_model"] = heavy_val
                self.config["tier2_heavy_model"] = heavy_val
                api_sec["pro_model_id"] = heavy_val
            if "tier2_thinking_level" in updates or "tier3_thinking_level" in updates:
                t_lvl = updates.get("tier3_thinking_level") or updates.get("tier2_thinking_level")
                self.config["tier2_thinking_level"] = t_lvl
                self.config["tier3_thinking_level"] = t_lvl
                api_sec["tier2_thinking_level"] = t_lvl
                api_sec["tier3_thinking_level"] = t_lvl
                if (
                    "tier3_heavy_model" not in updates
                    and "tier2_heavy_model" not in updates
                    and not (isinstance(api_updates, dict) and ("pro_model_id" in api_updates or "tier2_heavy_model" in api_updates))
                ):
                    retagged = _retag_model_level(self.config.get("tier3_heavy_model") or self.config.get("tier2_heavy_model"), t_lvl)
                    self.config["tier3_heavy_model"] = retagged
                    self.config["tier2_heavy_model"] = retagged
                    api_sec["pro_model_id"] = retagged
            if "tier2_moderate_level" in updates:
                self.config["tier2_moderate_level"] = updates["tier2_moderate_level"]
                api_sec["tier2_moderate_level"] = updates["tier2_moderate_level"]
                if "tier2_moderate_model" not in updates and not (isinstance(api_updates, dict) and "moderate_model_id" in api_updates):
                    retagged = _retag_model_level(self.config.get("tier2_moderate_model"), updates["tier2_moderate_level"])
                    self.config["tier2_moderate_model"] = retagged
                    api_sec["moderate_model_id"] = retagged
            if "stt_model_endpoint" in updates or "stt_endpoint" in updates:
                stt_val = updates.get("stt_endpoint") or updates.get("stt_model_endpoint")
                self.config["stt_model_endpoint"] = stt_val
                self.config["stt_endpoint"] = stt_val
                api_sec["stt_model_id"] = stt_val
                api_sec["stt_endpoint"] = stt_val
            if "tts_model_endpoint" in updates or "tts_endpoint" in updates:
                tts_val = updates.get("tts_endpoint") or updates.get("tts_model_endpoint")
                self.config["tts_model_endpoint"] = tts_val
                self.config["tts_endpoint"] = tts_val
                api_sec["tts_model_id"] = tts_val
                api_sec["tts_endpoint"] = tts_val
            if "pipeline_mode" in updates:
                pipe_val = str(updates["pipeline_mode"]).lower()
                self.config["pipeline_mode"] = pipe_val
                api_sec["pipeline_mode"] = pipe_val

            # Apply nested dictionary updates
            for section in ("api", "audio", "vision", "security", "ui", "user", "models", "hardware_telemetry"):
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
                    if "tts_pace" in sec_copy:
                        self.config["tts_pace"] = sec_copy["tts_pace"]
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
                        self.config["tier3_heavy_model"] = sec_copy["tier2_heavy_model"]
                        self.config.setdefault("api", {})["pro_model_id"] = sec_copy["tier2_heavy_model"]
                    if "tier2_thinking_level" in sec_copy or "tier3_thinking_level" in sec_copy:
                        t_lvl = sec_copy.get("tier3_thinking_level") or sec_copy.get("tier2_thinking_level")
                        self.config["tier2_thinking_level"] = t_lvl
                        self.config["tier3_thinking_level"] = t_lvl
                        self.config.setdefault("api", {})["tier2_thinking_level"] = t_lvl
                        self.config.setdefault("api", {})["tier3_thinking_level"] = t_lvl
                        if "tier2_heavy_model" not in sec_copy and "pro_model_id" not in sec_copy:
                            retagged = _retag_model_level(self.config.get("tier3_heavy_model") or self.config.get("tier2_heavy_model"), t_lvl)
                            self.config["tier3_heavy_model"] = retagged
                            self.config["tier2_heavy_model"] = retagged
                            self.config.setdefault("api", {})["pro_model_id"] = retagged
                    if "tier2_moderate_level" in sec_copy:
                        self.config["tier2_moderate_level"] = sec_copy["tier2_moderate_level"]
                        if "moderate_model_id" not in sec_copy and "tier2_moderate_model" not in updates:
                            retagged = _retag_model_level(self.config.get("tier2_moderate_model"), sec_copy["tier2_moderate_level"])
                            self.config["tier2_moderate_model"] = retagged
                            self.config.setdefault("api", {})["moderate_model_id"] = retagged
                    if "ttrpg_thinking_level" in sec_copy:
                        self.config["ttrpg_thinking_level"] = sec_copy["ttrpg_thinking_level"]
                        if "ttrpg_model_id" not in sec_copy and "ttrpg_model_endpoint" not in updates:
                            retagged = _retag_model_level(self.config.get("ttrpg_model_endpoint"), sec_copy["ttrpg_thinking_level"])
                            self.config["ttrpg_model_endpoint"] = retagged
                            self.config.setdefault("api", {})["ttrpg_model_id"] = retagged

        sync_config_schema(self.config)
        try:
            save_config_atomic(self.config, self.config_path)
        except Exception as e:
            print(f"[CONFIG_MANAGER SAVE ERROR] {e}")
        return self.config


def mask_sensitive_config(config_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns a deep copy of config_data with encrypted/plaintext API keys and tokens masked
    for safe transmission over UI event streams.
    """
    if not isinstance(config_data, dict):
        return {}
    safe_cfg = copy.deepcopy(config_data)
    api_sec = safe_cfg.get("api")
    if isinstance(api_sec, dict):
        enc_key = api_sec.pop("api_key_encrypted", "")
        api_sec.pop("new_api_key", None)
        api_sec.pop("api_key_display", None)
        api_sec["has_api_key"] = bool(enc_key)
        api_sec["has_key"] = bool(enc_key)
        api_sec["api_key_masked"] = "••••••••••••••••" if enc_key else ""
    return safe_cfg


def save_config_atomic(config_data: dict, config_path: Optional[str] = None):
    """
    Writes configuration to a temporary file before atomically renaming it.
    Prevents corrupting config.json if the process is killed mid-write.
    """
    abs_config_path = os.path.abspath(_resolve_config_path(config_path))
    base_dir = os.path.dirname(abs_config_path)
    os.makedirs(base_dir, exist_ok=True)

    # Strip any transient plaintext secret fields before persisting to disk
    clean_data = copy.deepcopy(config_data) if isinstance(config_data, dict) else config_data
    if isinstance(clean_data, dict) and isinstance(clean_data.get("api"), dict):
        clean_data["api"].pop("new_api_key", None)
        clean_data["api"].pop("api_key_display", None)

    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=base_dir, delete=False, suffix=".tmp", encoding="utf-8") as tf:
            temp_name = tf.name
            json.dump(clean_data, tf, indent=2)

        # os.replace is an atomic operation on Windows (NTFS) and POSIX
        os.replace(temp_name, abs_config_path)
        temp_name = None
    finally:
        if temp_name and os.path.exists(temp_name):
            try:
                os.remove(temp_name)
            except Exception:
                pass

    # Synchronize startup registry if boot_on_startup is in config_data
    if isinstance(config_data, dict) and "boot_on_startup" in config_data:
        try:
            apply_startup_configuration(config_data)
        except Exception as e:
            print(f"[CONFIG_MANAGER] Failed to apply startup configuration: {e}")


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    return ConfigManager(config_path=_resolve_config_path(config_path)).load()


def save_config(updates: Dict[str, Any], config_path: Optional[str] = None) -> Dict[str, Any]:
    mgr = ConfigManager(config_path=_resolve_config_path(config_path))
    return mgr.save(updates)

