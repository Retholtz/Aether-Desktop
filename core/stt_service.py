"""
Aether Desktop - Speech-to-Text (STT) Service & Dispatcher
Prevents non-model endpoints (e.g. WebSocket or fake transcribe IDs) from causing 404s,
and routes one-shot audio transcription to a valid multimodal Flash model.
"""

import os
import logging
from typing import Optional, Any
from google import genai
from google.genai import types
from core.config_manager import load_config, unprotect_secret

logger = logging.getLogger("Aether.STTService")

_active_engine: Optional[Any] = None


def get_active_engine() -> Optional[Any]:
    """Returns the globally active Aether engine instance if available."""
    global _active_engine
    if _active_engine is not None:
        return _active_engine
    try:
        import main
        if getattr(main, "active_engine", None) is not None:
            return main.active_engine
    except Exception:
        pass
    return None


def set_active_engine(engine: Any) -> None:
    """Sets the globally active Aether engine instance."""
    global _active_engine
    _active_engine = engine


def get_genai_client(api_key: Optional[str] = None) -> genai.Client:
    """Returns an authenticated Google GenAI client instance."""
    engine = get_active_engine()
    if engine is not None:
        client = getattr(engine, "genai_client", None) or getattr(engine, "client", None)
        if client is not None:
            return client

    cfg = load_config()
    key = api_key
    if not key:
        api_cfg = cfg.get("api", {})
        enc_key = api_cfg.get("api_key_encrypted", "")
        if enc_key:
            try:
                key = unprotect_secret(enc_key)
            except Exception:
                pass
        if not key:
            key = api_cfg.get("api_key") or os.environ.get("GEMINI_API_KEY", "")

    return genai.Client(api_key=key)


def resolve_stt_model_endpoint(stt_endpoint: Optional[str] = None, fallback: Optional[str] = None) -> str:
    """
    Sanitizes STT endpoint and resolves fake / WebSocket IDs down to a valid
    multimodal Gemini Flash model suitable for client.models.generate_content.
    """
    cfg = load_config()
    endpoint = stt_endpoint or cfg.get("stt_endpoint") or cfg.get("stt_model_endpoint") or "primary_flash"

    # Non-existent or WebSocket-only endpoints that fail with 404 in generateContent
    if endpoint in (
        "gemini_live_audio",
        "gemini_live",
        "gemini-3.5-transcribe",
        "gemini-3.5-transcribe-live",
        "gemini-3.8-transcribe",
        "gemini-3.8-transcribe-live",
        "primary_flash_stt",
        "primary_flash",
    ):
        return fallback or cfg.get("primary_model_endpoint") or cfg.get("tier1_fast_model") or "gemini-2.5-flash"

    if endpoint.startswith("models/"):
        endpoint = endpoint.replace("models/", "")

    return endpoint or fallback or "gemini-2.5-flash"


def transcribe_audio_buffer(audio_bytes: bytes, mime_type: str = "audio/wav", stt_endpoint: Optional[str] = None) -> str:
    """
    Transcribes raw audio bytes into text.
    Routes WebSocket streams to active live sessions, or resolves to a valid
    multimodal Gemini Flash model to avoid 404 NOT_FOUND errors.
    """
    cfg = load_config()
    endpoint = stt_endpoint or cfg.get("stt_endpoint", "primary_flash")

    # 1. If configured for Live WebSocket streaming, route to the live connection
    if endpoint in ("gemini_live_audio", "gemini_live"):
        # Live session already handles audio natively over WebSocket;
        # if called standalone, delegate to the active live session or fall back to flash
        engine = get_active_engine()
        if engine and hasattr(engine, "send_live_audio_buffer"):
            res = engine.send_live_audio_buffer(audio_bytes)
            if res:
                return res
        # Fallback to flash if live socket is unavailable:
        endpoint = cfg.get("primary_model_endpoint", "gemini-2.5-flash")

    # 2. Sanitize model endpoint: ensure we don't pass fake or non-existent endpoints
    # Standard multimodal Flash models perform native audio transcription via generate_content
    if endpoint in (
        "gemini-3.5-transcribe",
        "gemini-3.5-transcribe-live",
        "gemini-3.8-transcribe",
        "gemini-3.8-transcribe-live",
        "gemini_live_audio",
        "primary_flash_stt",
        "primary_flash",
    ):
        # Resolve to a valid multimodal Gemini Flash model
        endpoint = cfg.get("primary_model_endpoint", "gemini-2.5-flash")

    if endpoint.startswith("models/"):
        endpoint = endpoint.replace("models/", "")

    # 3. Call generate_content with audio data
    client = get_genai_client()
    try:
        response = client.models.generate_content(
            model=endpoint,
            contents=[
                {
                    "role": "user",
                    "parts": [
                        {"inline_data": {"mime_type": mime_type, "data": audio_bytes}},
                        {"text": "Transcribe the spoken audio verbatim. Output only the transcribed text with no extra commentary."}
                    ]
                }
            ],
            config=types.GenerateContentConfig(
                temperature=0.0,
                thinking_config=types.ThinkingConfig(thinking_budget=0)
            )
        )
        return response.text.strip() if response and response.text else ""
    except Exception as e:
        print(f"[ERROR] [STT] Transcription failed on model '{endpoint}': {e}")
        logger.error(f"[ERROR] [STT] Transcription failed on model '{endpoint}': {e}")
        raise
