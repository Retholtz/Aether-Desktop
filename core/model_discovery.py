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
from typing import List, Dict, Any

MANIFEST_PATH = os.path.join("data", "models_manifest.json")

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
    "chat_models": [
        {"id": "gemini-3.8-flash", "display_name": "Gemini 3.8 Flash (High Speed)"},
        {"id": "gemini-3.7-flash", "display_name": "Gemini 3.7 Flash"},
        {"id": "gemini-3.6-flash", "display_name": "Gemini 3.6 Flash"},
        {"id": "gemini-3.5-flash", "display_name": "Gemini 3.5 Flash"},
        {"id": "gemini-3.5-flash-lite", "display_name": "Gemini 3.5 Flash Lite"},
        {"id": "gemini-3.1-pro-preview", "display_name": "Gemini 3.1 Pro Preview"}
    ],
    "heavy_models": [
        {"id": "gemini-3.8-flash-extended", "display_name": "Gemini 3.8 Extended (Deep Reasoning)"},
        {"id": "gemini-3.8-live-extended-thinking", "display_name": "Gemini 3.8 Live Extended Thinking"},
        {"id": "gemini-3.1-pro-preview", "display_name": "Gemini 3.1 Pro Preview"},
        {"id": "gemini-3.7-flash", "display_name": "Gemini 3.7 Flash"},
        {"id": "gemini-3.6-flash", "display_name": "Gemini 3.6 Flash"},
        {"id": "gemini-3.5-flash", "display_name": "Gemini 3.5 Flash"}
    ],
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

DEFAULT_CATEGORIZED_MODELS["tier1_options"] = DEFAULT_CATEGORIZED_MODELS["chat_models"]
DEFAULT_CATEGORIZED_MODELS["tier2_options"] = DEFAULT_CATEGORIZED_MODELS["heavy_models"]

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
    """Probes a conversational chat model with a 1-token verification query."""
    try:
        res = client.models.generate_content(
            model=model_id,
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
            config={"response_mime_type": "audio/mp3"}
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

    # Sort with newest versions first
    verified_chat.sort(key=model_chronological_key, reverse=True)
    verified_tts.sort(key=model_chronological_key, reverse=True)
    verified_stt.sort(key=model_chronological_key, reverse=True)

    result = {
        "updated_at": time.time(),
        "chat_models": verified_chat or DEFAULT_CATEGORIZED_MODELS["chat_models"],
        "tts_models": verified_tts or DEFAULT_CATEGORIZED_MODELS["tts_models"],
        "stt_models": verified_stt or DEFAULT_CATEGORIZED_MODELS["stt_models"],
        "heavy_models": DEFAULT_CATEGORIZED_MODELS["heavy_models"],
        "tier1_options": verified_chat or DEFAULT_CATEGORIZED_MODELS["chat_models"],
        "tier2_options": DEFAULT_CATEGORIZED_MODELS["heavy_models"],
    }

    try:
        os.makedirs(os.path.dirname(MANIFEST_PATH), exist_ok=True)
        with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print(f"[INFO] [MODEL_DISCOVERY] Verified {len(result['chat_models'])} Chat, {len(result['tts_models'])} TTS, and {len(result['stt_models'])} STT models.")
    except Exception as e:
        print(f"[WARN] [MODEL_DISCOVERY] Cache write error: {e}")

    return result

