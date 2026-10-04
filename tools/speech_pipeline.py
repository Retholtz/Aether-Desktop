"""
Aether Desktop - Speech Pipeline Dispatcher
Re-exports transcribe_audio_buffer and endpoint routing from core.stt_service.
"""

from core.stt_service import (
    transcribe_audio_buffer,
    resolve_stt_model_endpoint,
    get_active_engine,
    set_active_engine,
    get_genai_client,
)

__all__ = [
    "transcribe_audio_buffer",
    "resolve_stt_model_endpoint",
    "get_active_engine",
    "set_active_engine",
    "get_genai_client",
]
