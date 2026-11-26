"""
Aether Desktop - Dynamic Model Discovery Service
Discovers, filters, categorizes, and health-checks Google GenAI model endpoints
(Chat, TTS, STT) with active probing to eliminate deprecated and broken models.
"""

import os
import io
import wave
import re
import json
import time
from typing import List, Dict, Any, Optional, Tuple

from core.paths import user_data_path

MANIFEST_PATH = user_data_path("data", "models_manifest.json")

PRIMARY_CURATED_PROFILES: List[Dict[str, Any]] = [
    {"id": "gemini-3.8-live", "display_name": "3.8 - Live (Default)"},
    {"id": "custom", "display_name": "Custom"},
]

MODERATE_CURATED_PROFILES: List[Dict[str, Any]] = [
    {"id": "gemini-4-hydrogen", "display_name": "4.0 - Hydrogen (Default)"},
    {"id": "custom", "display_name": "Custom"},
]

TIER1_CURATED_PROFILES: List[Dict[str, Any]] = [
    {"id": "gemini-4-hydrogen:xhigh", "display_name": "4.0 Hydrogen - Extra"},
    {"id": "gemini-4-hydrogen:high", "display_name": "4.0 Hydrogen - High"},
    {"id": "gemini-4-hydrogen:medium", "display_name": "4.0 Hydrogen - Medium (Default)"},
    {"id": "gemini-4-hydrogen:low", "display_name": "4.0 Hydrogen - Low"},
    {"id": "gemini-4-hydrogen:instant", "display_name": "Gemini 4.0 Hydrogen Instant (Default)"},
    {"id": "gemini-3.8-flash", "display_name": "Gemini 3.8 Flash - Live (High Speed)"},
]

TIER2_CURATED_PROFILES: List[Dict[str, Any]] = [
    {"id": "gemini-4-neon", "display_name": "4.0 - Neon (Default)"},
    {"id": "gemini-4-carbon", "display_name": "4.0 - Carbon"},
    {"id": "custom", "display_name": "Custom"},
]

HEAVY_CURATED_PROFILES = TIER2_CURATED_PROFILES

TTRPG_CURATED_PROFILES: List[Dict[str, str]] = [
    {"id": "gemini-3.8-flash", "display_name": "Gemini 3.8 Flash (Fastest)"},
    {"id": "gemini-4-hydrogen", "display_name": "Gemini 4.0 Hydrogen (Default)"},
    {"id": "gemini-4-neon", "display_name": "Gemini 4.0 Neon"},
    {"id": "custom", "display_name": "Custom Endpoint"},
]


def get_ttrpg_model_options() -> List[Dict[str, str]]:
    """Returns curated model/thinking profiles available for the TTRPG Live Game Master."""
    return [dict(item) for item in TTRPG_CURATED_PROFILES]


def get_configured_tier2_model(cfg: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """
    Returns (base_model_id, thinking_level) for the user's configured Tier 2 Moderate endpoint
    from Settings (config.json), acting as the single source of truth for REST helper tasks.
    """
    if not isinstance(cfg, dict):
        try:
            from core.config_manager import load_config
            cfg = load_config()
        except Exception:
            cfg = {}
    api_cfg = cfg.get("api", {}) if isinstance(cfg, dict) else {}
    raw = (
        cfg.get("tier2_moderate_model")
        or (api_cfg.get("moderate_model_id") if isinstance(api_cfg, dict) else None)
        or "gemini-4-hydrogen:medium"
    )
    fallback_lvl = (
        cfg.get("tier2_moderate_level")
        or (api_cfg.get("tier2_moderate_level") if isinstance(api_cfg, dict) else None)
        or "medium"
    )
    base_model, level = resolve_model_profile(
        raw,
        default_model="gemini-4-hydrogen",
        default_level=str(fallback_lvl or "medium"),
    )
    if "live" in str(base_model).lower():
        base_model = "gemini-4-hydrogen"
    return base_model, (level or "medium")


def get_configured_game_model(
    cfg: Optional[Dict[str, Any]] = None,
    prefer_fast: bool = True,
) -> Tuple[str, str]:
    """
    Single Settings-backed model resolver for ALL Game Mode REST calls (Vision UI,
    Screen Peek, Chit-Chat, Macro/Trigger drafting, Auto-Build, Keybind Sync).
    - Reads Settings (config.json) as the source of truth:
      * prefer_fast=True  -> Settings Tier 1 (if non-Live REST), else Settings Tier 2 Moderate.
      * prefer_fast=False -> Settings Tier 2 Moderate.
    - Hard ceiling: Game Mode is strictly locked out of Tier 3 flagship/Neon models
      (reserved for Chat executive summaries and Projects); any Neon model caps to Hydrogen.
    """
    if not isinstance(cfg, dict):
        try:
            from core.config_manager import load_config
            cfg = load_config()
        except Exception:
            cfg = {}

    api_cfg = cfg.get("api", {}) if isinstance(cfg, dict) else {}
    tier3_raw = str(
        cfg.get("tier3_heavy_model")
        or (api_cfg.get("pro_model_id") if isinstance(api_cfg, dict) else "")
        or ""
    ).strip()
    tier3_base = resolve_model_profile(tier3_raw, default_model="gemini-4-neon")[0].lower() if tier3_raw else "gemini-4-neon"

    def _is_disallowed_for_game(model_id: str) -> bool:
        ml = str(model_id or "").strip().lower()
        if not ml or "live" in ml:
            return True
        if "neon" in ml:
            return True
        if tier3_base and "hydrogen" not in tier3_base and "flash" not in tier3_base and ml == tier3_base:
            return True
        return False

    if prefer_fast:
        raw_t1 = (
            cfg.get("tier1_fast_model")
            or cfg.get("primary_model_endpoint")
            or (api_cfg.get("model_id") if isinstance(api_cfg, dict) else None)
            or ""
        )
        if raw_t1:
            t1_base, t1_lvl = resolve_model_profile(str(raw_t1), default_model="gemini-3.8-flash", default_level="none")
            # Map Live WebSocket Tier 1 models (e.g. 'gemini-3.8-live') to their fast REST sibling ('gemini-3.8-flash')
            # so lightweight Vision/JSON helpers stay on Tier 1 cost instead of escalating to Tier 2 Hydrogen.
            if "live" in t1_base.lower():
                t1_rest = re.sub(r"-live\b", "-flash", t1_base, flags=re.IGNORECASE)
                if t1_rest != t1_base and not _is_disallowed_for_game(t1_rest):
                    return t1_rest, "none"
            if not _is_disallowed_for_game(t1_base):
                return t1_base, (t1_lvl or "low")

    t2_base, t2_lvl = get_configured_tier2_model(cfg)
    if _is_disallowed_for_game(t2_base):
        t2_base = "gemini-4-hydrogen"
    return t2_base, (t2_lvl or ("low" if prefer_fast else "medium"))


def resolve_model_profile(
    endpoint: Optional[str],
    default_model: str = "gemini-4-hydrogen",
    default_level: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """
    Splits a configured endpoint profile (e.g. 'gemini-4-hydrogen:medium' or 'gemini-4-neon:high')
    into (base_model_id, thinking_level).
    """
    raw = str(endpoint or "").strip()
    if raw.startswith("models/"):
        raw = raw[len("models/"):]

    level = default_level
    if ":" in raw:
        base_part, lvl_part = raw.split(":", 1)
        base_model = base_part.strip() or default_model
        level = lvl_part.strip().lower() or default_level
    else:
        base_model = raw or default_model

    if level:
        level = str(level).strip().lower()
        if level == "extra":
            level = "xhigh"
        if "argon" in base_model.lower() and level == "xhigh":
            level = "high"

    return base_model, level


def format_model_display_name(model_id: str) -> str:
    """Dynamically converts any endpoint model ID (e.g. 'gemini-4-neon', 'gemini-5-nova') into a clean UI label."""
    raw = str(model_id or "").strip()
    if raw.startswith("models/"):
        raw = raw[len("models/"):]
    if ":" in raw:
        raw = raw.split(":", 1)[0].strip()
    if not raw:
        return "Configured Model"
    parts = [p for p in re.split(r"[-_]+", raw) if p]
    formatted: List[str] = []
    for p in parts:
        low = p.lower()
        if low in ("tts", "stt", "api", "pro", "ai"):
            formatted.append(p.upper() if low != "pro" else "Pro")
        elif any(ch.isdigit() for ch in p):
            formatted.append(p)
        else:
            formatted.append(p.capitalize())
    return " ".join(formatted)


def resolve_tier_model(
    cfg: Optional[Dict[str, Any]],
    tier: int,
) -> Tuple[str, str, str]:
    """
    Single source of truth for Tier 1 / Tier 2 / Tier 3 endpoint resolution from Settings (`config.json`).
    Returns `(base_model_id, thinking_level, ui_display_label)` where `ui_display_label` dynamically
    reflects the configured endpoint (e.g. 'Tier 2 (Gemini 4 Hydrogen)', 'Tier 3 (Gemini 4 Neon)')
    so future endpoint updates only require changing Settings.
    """
    cfg_dict = cfg if isinstance(cfg, dict) else {}
    api_cfg = cfg_dict.get("api", {}) if isinstance(cfg_dict.get("api"), dict) else {}

    if tier == 1:
        raw = (
            api_cfg.get("live_model_id")
            or api_cfg.get("primary_model_id")
            or cfg_dict.get("primary_model")
            or "gemini-3.8-live"
        )
        base_model, lvl = resolve_model_profile(raw, default_model="gemini-3.8-live", default_level="")
        lvl = lvl or ""
    elif tier == 2:
        raw = (
            cfg_dict.get("tier2_moderate_model")
            or api_cfg.get("moderate_model_id")
            or "gemini-4-hydrogen:medium"
        )
        default_lvl = cfg_dict.get("tier2_thinking_level") or "medium"
        base_model, lvl = resolve_model_profile(raw, default_model="gemini-4-hydrogen", default_level=default_lvl)
        lvl = lvl or "medium"
    else:
        raw = (
            cfg_dict.get("tier3_heavy_model")
            or cfg_dict.get("tier2_heavy_model")
            or api_cfg.get("pro_model_id")
            or "gemini-4-neon:high"
        )
        default_lvl = cfg_dict.get("tier3_thinking_level") or cfg_dict.get("tier2_thinking_level") or "high"
        base_model, lvl = resolve_model_profile(raw, default_model="gemini-4-neon", default_level=default_lvl)
        lvl = lvl or "high"

    pretty = format_model_display_name(base_model)
    return base_model, lvl, f"Tier {tier} ({pretty})"


def build_thinking_config(
    model_or_profile: Optional[str],
    thinking_level: Optional[str] = None,
    thinking_budget: Optional[int] = None,
):
    """
    Builds a google.genai.types.ThinkingConfig adapted for Gemini 4 (Hydrogen/Neon/Carbon/Argon)
    thinking_level vs legacy thinking_budget models (e.g. gemini-3.8-flash).
    """
    from google.genai import types
    base_model, level = resolve_model_profile(model_or_profile, default_level=thinking_level)
    mid_low = base_model.lower()
    if level in ("instant", "none", "off", "0"):
        return types.ThinkingConfig(thinking_budget=0)
    is_gemini4_or_level = (
        any(k in mid_low for k in ("hydrogen", "neon", "carbon", "argon", "gemini-4"))
        or (
            level in ("low", "medium", "high", "xhigh")
            and not any(v in mid_low for v in ("3.8", "3.5", "3.1", "2.", "1."))
        )
    )
    if is_gemini4_or_level:
        if not level:
            level = "medium" if "hydrogen" in mid_low else "high"
        if level not in ("low", "medium", "high", "xhigh"):
            level = "medium" if "hydrogen" in mid_low else "high"
        if "argon" in mid_low and level == "xhigh":
            level = "high"
        return types.ThinkingConfig(thinking_level=level)

    budget = int(thinking_budget if thinking_budget is not None else 0)
    return types.ThinkingConfig(thinking_budget=budget)

# Keywords that disqualify a model from general conversational/reasoning chat:
CHAT_EXCLUDE_KEYWORDS = [
    "banana",       # Image fine-tunes / Nano Banana
    "image",        # Image gen
    "robotics",     # Embodied robotics (e.g. robotics-er2)
    "computer-use", # OS action execution only
    "omni",         # Specialized omni routing
    "audio",        # Native audio streaming endpoints
    "live",         # Live bidirectional WebSocket endpoints
    "tts",          # Speech synthesis
    "transcribe",   # Speech recognition
    "translate",    # Translation only
    "veo",          # Video generation
    "lyria",        # Music generation
    "imagen",       # Image generation
    "embedding",    # Vector embeddings
    "aqa",          # Benchmark QA
]

DEFAULT_CATEGORIZED_MODELS: Dict[str, List[Dict[str, Any]]] = {
    "chat_models": list(TIER1_CURATED_PROFILES),
    "primary_models": list(PRIMARY_CURATED_PROFILES),
    "moderate_models": list(MODERATE_CURATED_PROFILES),
    "heavy_models": list(TIER2_CURATED_PROFILES),
    "tts_models": [
        {"id": "gemini-3.8-flash-tts", "display_name": "Gemini 3.8 Flash TTS (Cloud)"},
        {"id": "gemini-3.8-flash-lite-tts", "display_name": "Gemini 3.8 Flash Lite TTS (Cloud)"},
        {"id": "gemini-3.1-flash-tts-preview", "display_name": "Gemini 3.1 Flash TTS Preview (Cloud)"}
    ],
    "stt_models": [
        {"id": "gemini_live_audio", "display_name": "Gemini Live Native Audio Stream (Real-Time Bidirectional - Recommended)"},
        {"id": "primary_flash_stt", "display_name": "Gemini Flash Multimodal Audio (REST One-Shot Fallback)"}
    ]
}

DEFAULT_CATEGORIZED_MODELS["primary_options"] = DEFAULT_CATEGORIZED_MODELS["primary_models"]
DEFAULT_CATEGORIZED_MODELS["tier1_options"] = DEFAULT_CATEGORIZED_MODELS["chat_models"]
DEFAULT_CATEGORIZED_MODELS["tier2_moderate_options"] = DEFAULT_CATEGORIZED_MODELS["moderate_models"]
DEFAULT_CATEGORIZED_MODELS["tier2_options"] = DEFAULT_CATEGORIZED_MODELS["heavy_models"]
DEFAULT_CATEGORIZED_MODELS["tier3_options"] = DEFAULT_CATEGORIZED_MODELS["heavy_models"]

DEFAULT_FALLBACK_MODELS = DEFAULT_CATEGORIZED_MODELS["chat_models"]


def _generate_silent_wav_bytes(duration_sec: float = 0.1) -> bytes:
    """Generates a minimal valid mono 16-bit 16kHz PCM WAV byte stream for probing STT."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)       # 16-bit
        wf.setframerate(16000)   # 16kHz
        num_frames = int(16000 * duration_sec)
        wf.writeframes(b"\x00\x00" * num_frames)
    return buf.getvalue()


def verify_chat_endpoint(client, model_id: str) -> bool:
    """Probes a conversational chat model with a verification query."""
    try:
        base_model, _ = resolve_model_profile(model_id)
        mid_low = base_model.lower()
        if any(k in mid_low for k in ("neon", "argon", "hydrogen", "carbon")):
            res = client.models.generate_content(
                model=base_model,
                contents="ping",
                config={"max_output_tokens": 1024, "thinking_config": {"thinking_level": "low"}}
            )
        else:
            res = client.models.generate_content(
                model=base_model,
                contents="ping",
                config={"max_output_tokens": 1}
            )
        return bool(res and hasattr(res, "text"))
    except Exception as err:
        print(f"[DEBUG] [MODEL_DISCOVERY] Chat probe failed for '{model_id}': {err}")
        return False


def verify_tts_endpoint(client, model_id: str) -> bool:
    """Probes a candidate TTS endpoint with a minimal synthesis request."""
    try:
        res = client.models.generate_content(
            model=model_id,
            contents="test",
            config={"response_modalities": ["AUDIO"]}
        )
        return bool(res and getattr(res, "candidates", None))
    except Exception as err:
        print(f"[DEBUG] [MODEL_DISCOVERY] TTS probe failed for '{model_id}': {err}")
        return False


def verify_stt_endpoint(client, model_id: str) -> bool:
    """Probes an STT transcription endpoint with a tiny synthetic WAV buffer."""
    try:
        wav_data = _generate_silent_wav_bytes()
        res = client.models.generate_content(
            model=model_id,
            contents=[
                {
                    "parts": [
                        {"inline_data": {"mime_type": "audio/wav", "data": wav_data}},
                        {"text": "Transcribe"}
                    ]
                }
            ]
        )
        return bool(res and hasattr(res, "text"))
    except Exception as err:
        print(f"[DEBUG] [MODEL_DISCOVERY] STT probe failed for '{model_id}': {err}")
        return False


def model_chronological_key(item: Any) -> tuple:
    """
    Computes a sorting key for Gemini models to order them chronologically
    with the newest / highest-capability models at the top.
    """
    model_id = item["id"] if isinstance(item, dict) and "id" in item else str(item)
    mid = model_id.lower()

    # 1. Semantic version (major, minor, patch)
    ver_match = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', mid)
    if ver_match:
        major = int(ver_match.group(1))
        minor = int(ver_match.group(2))
        patch = int(ver_match.group(3)) if ver_match.group(3) else 0
        version = (major, minor, patch)
    elif "gemini-exp" in mid:
        version = (1, 99, 0)
    else:
        single_ver = re.search(r'gemini-(\d+)', mid)
        version = (int(single_ver.group(1)), 0, 0) if single_ver else (0, 0, 0)

    # 2. Release date / snapshot date (e.g. 2025-02-05, 02-05, 1206)
    date_score = 0
    full_date = re.search(r'(202[4-9])[-_]?(\d{2})[-_]?(\d{2})', mid)
    if full_date:
        date_score = int(full_date.group(1) + full_date.group(2) + full_date.group(3))
    else:
        md_match = re.search(r'[-_](\d{2})[-_](\d{2})\b', mid)
        if md_match:
            date_score = int(md_match.group(1) + md_match.group(2))
        else:
            num4 = re.search(r'[-_](0[1-9]|1[0-2])([0-3][0-9])\b', mid)
            if num4:
                date_score = int(num4.group(1) + num4.group(2))

    # 3. Checkpoint / revision / alias score
    rev_match = re.search(r'[-_](00\d)\b', mid)
    if "latest" in mid:
        checkpoint_score = 9999
    elif "neon" in mid or "carbon" in mid:
        checkpoint_score = 9500
    elif "argon" in mid:
        checkpoint_score = 9000
    elif rev_match:
        checkpoint_score = int(rev_match.group(1))
    elif "preview" in mid or "exp" in mid:
        checkpoint_score = 500
    else:
        checkpoint_score = 0

    return (version, date_score, checkpoint_score, mid)


def load_cached_categorized_models() -> Dict[str, List[Dict[str, Any]]]:
    """Loads discovered models grouped by function from cache manifest or defaults."""
    if os.path.exists(MANIFEST_PATH):
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "chat_models" in data:
                    if "heavy_models" not in data:
                        data["heavy_models"] = DEFAULT_CATEGORIZED_MODELS["heavy_models"]
                    if "tier2_options" not in data:
                        data["tier2_options"] = data.get("heavy_models", DEFAULT_CATEGORIZED_MODELS["heavy_models"])
                    return data
                elif isinstance(data, dict) and "tier1_options" in data:
                    return {
                        "chat_models": data.get("tier1_options", DEFAULT_CATEGORIZED_MODELS["chat_models"]),
                        "heavy_models": data.get("tier2_options", DEFAULT_CATEGORIZED_MODELS["heavy_models"]),
                        "tts_models": data.get("tts_options", DEFAULT_CATEGORIZED_MODELS["tts_models"]),
                        "stt_models": data.get("stt_options", DEFAULT_CATEGORIZED_MODELS["stt_models"]),
                        "tier1_options": data.get("tier1_options", DEFAULT_CATEGORIZED_MODELS["chat_models"]),
                        "tier2_options": data.get("tier2_options", DEFAULT_CATEGORIZED_MODELS["heavy_models"]),
                    }
        except Exception as e:
            print(f"[WARN] [MODEL_DISCOVERY] Error loading manifest: {e}")

    return DEFAULT_CATEGORIZED_MODELS


def load_cached_models() -> List[Dict[str, Any]]:
    """Loads discovered chat models from local cache or returns defaults."""
    data = load_cached_categorized_models()
    return data.get("chat_models", DEFAULT_FALLBACK_MODELS)


def fetch_available_gemini_models(client, probe_endpoints: bool = True) -> Dict[str, List[Dict[str, Any]]]:
    """
    Queries Google GenAI API, categorizes candidate models into Chat, TTS, and STT,
    and verifies functionality via active probing before saving to cache.
    """
    if client is None:
        return load_cached_categorized_models()

    raw_candidates = []
    try:
        raw_list = client.models.list()
        for model in raw_list:
            raw_name = getattr(model, "name", "")
            mid = raw_name.replace("models/", "") if isinstance(raw_name, str) and raw_name.startswith("models/") else (raw_name if isinstance(raw_name, str) else str(getattr(model, "name", model)))
            dname = getattr(model, "display_name", mid) or mid
            raw_candidates.append({"id": mid, "display_name": dname})
    except Exception as e:
        print(f"[WARN] [MODEL_DISCOVERY] Metadata list failed: {e}. Falling back to cache.")
        return load_cached_categorized_models()

    verified_chat = []
    verified_tts = []
    verified_stt = []

    for candidate in raw_candidates:
        mid_lower = candidate["id"].lower()
        entry = {
            "id": candidate["id"],
            "display_name": f"{candidate['display_name']} ({candidate['id']})" if candidate["display_name"] != candidate["id"] else candidate["id"]
        }

        # 1. Probing TTS Endpoints
        if "tts" in mid_lower:
            if not probe_endpoints or verify_tts_endpoint(client, candidate["id"]):
                verified_tts.append(entry)
            continue

        # 2. Probing STT / Transcription Endpoints
        if "transcribe" in mid_lower:
            if not probe_endpoints or verify_stt_endpoint(client, candidate["id"]):
                verified_stt.append(entry)
            continue

        # 3. Disqualify non-conversational specialized media models
        if any(kw in mid_lower for kw in CHAT_EXCLUDE_KEYWORDS):
            continue

        # 4. Probing General Conversational Chat Models
        if mid_lower.startswith("gemini-"):
            if not probe_endpoints or verify_chat_endpoint(client, candidate["id"]):
                verified_chat.append(entry)

    # Sort with newest versions first, then assemble curated Hydrogen profiles at top & 3.8 Flash at bottom
    verified_chat.sort(key=model_chronological_key, reverse=True)
    verified_tts.sort(key=model_chronological_key, reverse=True)
    verified_stt.sort(key=model_chronological_key, reverse=True)

    verified_ids = {m["id"].lower() for m in verified_chat}
    curated_chat: List[Dict[str, Any]] = []
    if not verified_chat or "gemini-4-hydrogen" in verified_ids:
        curated_chat.extend(TIER1_CURATED_PROFILES[:5])
    for item in verified_chat:
        mid_low = item["id"].lower()
        if mid_low in ("gemini-4-hydrogen", "gemini-3.8-flash"):
            continue
        curated_chat.append(item)
    if not verified_chat or "gemini-3.8-flash" in verified_ids:
        curated_chat.append(TIER1_CURATED_PROFILES[5])

    result = {
        "updated_at": time.time(),
        "primary_models": DEFAULT_CATEGORIZED_MODELS["primary_models"],
        "primary_options": DEFAULT_CATEGORIZED_MODELS["primary_models"],
        "chat_models": curated_chat or DEFAULT_CATEGORIZED_MODELS["chat_models"],
        "moderate_models": DEFAULT_CATEGORIZED_MODELS["moderate_models"],
        "tier2_moderate_options": DEFAULT_CATEGORIZED_MODELS["moderate_models"],
        "tts_models": verified_tts or DEFAULT_CATEGORIZED_MODELS["tts_models"],
        "stt_models": verified_stt or DEFAULT_CATEGORIZED_MODELS["stt_models"],
        "heavy_models": DEFAULT_CATEGORIZED_MODELS["heavy_models"],
        "tier1_options": curated_chat or DEFAULT_CATEGORIZED_MODELS["chat_models"],
        "tier2_options": DEFAULT_CATEGORIZED_MODELS["heavy_models"],
        "tier3_options": DEFAULT_CATEGORIZED_MODELS["heavy_models"],
    }

    try:
        os.makedirs(os.path.dirname(MANIFEST_PATH), exist_ok=True)
        with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print(f"[INFO] [MODEL_DISCOVERY] Verified {len(result['chat_models'])} Chat, {len(result['tts_models'])} TTS, and {len(result['stt_models'])} STT models.")
    except Exception as e:
        print(f"[WARN] [MODEL_DISCOVERY] Cache write error: {e}")

    return result

