"""
core/audio_ducking_service.py - Per-Session Windows Audio Attenuation
Ducks background game and media audio while keeping Aether's speech at 100%.
"""
import os
import asyncio
from typing import Dict, Any, Optional
from core.logger import get_logger

logger = get_logger("AudioDucking")

# Lazy-import pycaw and comtypes for non-Windows mock support
try:
    import comtypes
    from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
    PYCAW_AVAILABLE = True
except ImportError:
    PYCAW_AVAILABLE = False
    AudioUtilities = None
    ISimpleAudioVolume = None


class AudioDuckingService:
    def __init__(
        self,
        enabled: bool = True,
        duck_percentage: int = 60,
        restore_delay_ms: int = 150
    ):
        self.enabled = enabled
        self.duck_percentage = max(0, min(100, int(duck_percentage)))
        self.restore_delay_ms = restore_delay_ms
        self.own_pid = os.getpid()

        self._is_ducked = False
        self._saved_volumes: Dict[int, float] = {}  # session_hash -> original_volume
        self._lock = asyncio.Lock()

    def is_supported(self) -> bool:
        return PYCAW_AVAILABLE

    def update_settings(self, enabled: Optional[bool] = None, duck_percentage: Optional[int] = None):
        if enabled is not None:
            self.enabled = enabled
        if duck_percentage is not None:
            self.duck_percentage = max(0, min(100, int(duck_percentage)))
        logger.info(f"[DUCKING] Settings updated: enabled={self.enabled}, duck_pct={self.duck_percentage}%")

    async def duck(self):
        """Attenuates active non-Aether audio sessions by duck_percentage."""
        if not self.enabled or not self.is_supported():
            return

        async with self._lock:
            if self._is_ducked:
                return  # Already attenuated

            await asyncio.to_thread(self._apply_ducking_sync)
            self._is_ducked = True

    async def unduck(self):
        """Restores all modified audio sessions back to their original volume."""
        if not self.is_supported():
            return

        async with self._lock:
            if not self._is_ducked:
                return

            if self.restore_delay_ms > 0:
                await asyncio.sleep(self.restore_delay_ms / 1000.0)

            await asyncio.to_thread(self._apply_restore_sync)
            self._is_ducked = False
            self._saved_volumes.clear()

    # --- Synchronous Worker Operations (Runs on Thread via COM) ---
    def _apply_ducking_sync(self):
        try:
            comtypes.CoInitialize()
            sessions = AudioUtilities.GetAllSessions()
            attenuation_factor = (100.0 - self.duck_percentage) / 100.0

            for session in sessions:
                try:
                    # Exclude Aether's process
                    if session.Process and session.Process.pid == self.own_pid:
                        continue

                    volume_ctl = session._ctl.QueryInterface(ISimpleAudioVolume)
                    if not volume_ctl:
                        continue

                    current_vol = volume_ctl.GetMasterVolume()
                    session_id = hash(session.ProcessId if session.Process else id(session))

                    # Cache pre-ducked volume
                    if session_id not in self._saved_volumes:
                        self._saved_volumes[session_id] = current_vol

                    new_vol = max(0.0, min(1.0, current_vol * attenuation_factor))
                    volume_ctl.SetMasterVolume(new_vol, None)
                except Exception as e_session:
                    logger.debug(f"[DUCKING] Skipped session due to error: {e_session}")

            logger.debug(f"[DUCKING] Ducked {len(self._saved_volumes)} active audio sessions by {self.duck_percentage}%.")
        except Exception as e:
            logger.error(f"[DUCKING] Failed to apply audio attenuation: {e}")
        finally:
            comtypes.CoUninitialize()

    def _apply_restore_sync(self):
        try:
            comtypes.CoInitialize()
            sessions = AudioUtilities.GetAllSessions()

            for session in sessions:
                try:
                    if session.Process and session.Process.pid == self.own_pid:
                        continue

                    session_id = hash(session.ProcessId if session.Process else id(session))
                    if session_id in self._saved_volumes:
                        original_vol = self._saved_volumes[session_id]
                        volume_ctl = session._ctl.QueryInterface(ISimpleAudioVolume)
                        if volume_ctl:
                            volume_ctl.SetMasterVolume(original_vol, None)
                except Exception as e_session:
                    logger.debug(f"[DUCKING] Skipped session restore due to error: {e_session}")

            logger.debug("[DUCKING] Audio sessions restored to original volumes.")
        except Exception as e:
            logger.error(f"[DUCKING] Failed to restore audio sessions: {e}")
        finally:
            comtypes.CoUninitialize()

