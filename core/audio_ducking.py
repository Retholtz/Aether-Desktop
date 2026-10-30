"""
core/audio_ducking.py - Compatibility alias module for AudioDuckingService.
Allows `from core.audio_ducking import AudioDucker, AudioDuckingService`.
"""
from core.audio_ducking_service import AudioDuckingService, AudioDucker

__all__ = ["AudioDuckingService", "AudioDucker"]

