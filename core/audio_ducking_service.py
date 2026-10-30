"""
core/audio_ducking_service.py - Per-Session Windows Audio Attenuation
Ducks background game and media audio while keeping Aether's speech at 100%.
Includes fail-safe volume cleanup on normal shutdown, unhandled exceptions, and sudden process exit.
"""
import os
import atexit
import asyncio
from contextlib import asynccontextmanager, contextmanager
from typing import Dict, Any, Optional, Callable, Set
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
    SYSTEM_EXCLUSIONS = {
        "rtkuwp_rs5.exe",
        "audiodg.exe",
        "system",
        "system idle process",
        "python.exe",
        "pythonw.exe",
        "aether.exe",
        "aether-desktop.exe",
    }

    def __init__(
        self,
        enabled: bool = True,
        duck_percentage: int = 60,
        restore_delay_ms: int = 150,
        is_output_active_fn: Optional[Callable[[], bool]] = None
    ):
        self.enabled = enabled
        self.duck_percentage = max(0, min(100, int(duck_percentage)))
        self.restore_delay_ms = restore_delay_ms
        self.is_output_active_fn = is_output_active_fn
        self.own_pid = os.getpid()

        self._is_ducked: bool = False
        self._original_volumes: Dict[int, float] = {}  # session_pid -> pre-duck volume level
        self._lock = asyncio.Lock()
        self._unduck_gen: int = 0

        # Register process-level emergency restore via atexit
        atexit.register(self.restore_all)

    @property
    def _saved_volumes(self) -> Dict[int, float]:
        """Backwards compatibility alias for _original_volumes."""
        return self._original_volumes

    def is_supported(self) -> bool:
        return PYCAW_AVAILABLE

    def _ensure_com(self):
        """Ensures COM is initialized on the calling thread."""
        try:
            if comtypes:
                comtypes.CoInitialize()
        except Exception:
            pass

    def _release_com(self):
        """Releases COM on the calling thread."""
        try:
            if comtypes:
                comtypes.CoUninitialize()
        except Exception:
            pass

    @contextmanager
    def _com_apartment(self):
        """
        Context manager ensuring COM apartment is initialized on the calling/worker thread
        and properly uninitialized on block exit.
        """
        initialized = False
        try:
            if comtypes:
                comtypes.CoInitialize()
                initialized = True
        except Exception:
            pass
        try:
            yield
        finally:
            if initialized:
                try:
                    if comtypes:
                        comtypes.CoUninitialize()
                except Exception:
                    pass

    def update_settings(self, enabled: Optional[bool] = None, duck_percentage: Optional[int] = None):
        if enabled is not None:
            self.enabled = enabled
        if duck_percentage is not None:
            self.duck_percentage = max(0, min(100, int(duck_percentage)))
        logger.info(f"[DUCKING] Settings updated: enabled={self.enabled}, duck_pct={self.duck_percentage}%")

    # --- Synchronous Core Operations with Automatic Rollback ---
    def duck_all(self, exclude_pids: Optional[Set[int]] = None) -> None:
        """
        Ducks active audio sessions and caches their baseline volume.
        Never overwrites _original_volumes if another duck command is received while already in a ducked state.
        Attempts immediate rollback if an unexpected error occurs during attenuation.
        """
        if not self.enabled or not self.is_supported():
            return

        if self._is_ducked:
            return  # Prevent compounding attenuation; keep original volume baseline

        with self._com_apartment():
            exclude = set(exclude_pids or ())
            exclude.add(self.own_pid)

            try:
                sessions = AudioUtilities.GetAllSessions()
                attenuation_factor = (100.0 - self.duck_percentage) / 100.0
                ducked_count = 0

                for session in sessions:
                    try:
                        if not session.Process:
                            continue

                        pid = session.Process.pid
                        if not pid or pid in exclude:
                            continue

                        proc_name = ""
                        try:
                            proc_name = session.Process.name().lower()
                        except Exception:
                            pass

                        if proc_name in self.SYSTEM_EXCLUSIONS:
                            continue

                        volume_ctl = session._ctl.QueryInterface(ISimpleAudioVolume)
                        if not volume_ctl:
                            continue

                        current_vol = volume_ctl.GetMasterVolume()

                        # Cache original baseline volume (never overwrite while ducked)
                        if pid not in self._original_volumes:
                            # Safeguard: if an application volume was left severely attenuated (<= 5%) from an
                            # ungraceful shutdown, baseline it to 100% instead of locking in the degraded state.
                            if current_vol <= 0.05:
                                self._original_volumes[pid] = 1.0
                            else:
                                self._original_volumes[pid] = current_vol

                        original_vol = self._original_volumes[pid]
                        target_vol = max(0.0, min(1.0, original_vol * attenuation_factor))

                        # Idempotent: only apply if master volume differs noticeably (> 1%)
                        if abs(current_vol - target_vol) > 0.01:
                            volume_ctl.SetMasterVolume(target_vol, None)
                            ducked_count += 1
                    except Exception as e_session:
                        logger.debug(f"[DUCKING] Skipped session due to error: {e_session}")

                self._is_ducked = True
                if ducked_count > 0:
                    logger.info(f"[DUCKING] Ducked {ducked_count} active audio sessions by {self.duck_percentage}%.")
            except Exception as e:
                logger.error(f"[DUCKING] Failed to duck sessions: {e}. Attempting rollback.")
                self.restore_all()

    def restore_all(self) -> None:
        """
        Restores all ducked audio sessions to their pre-duck volume level.
        Thread-safe, idempotent, and safe for interpreter shutdown (atexit).
        """
        if not self.is_supported():
            return

        if not self._original_volumes and not self._is_ducked:
            return  # Nothing to restore

        with self._com_apartment():
            restored_count = 0

            try:
                sessions = AudioUtilities.GetAllSessions()
                for session in sessions:
                    try:
                        if not session.Process:
                            continue

                        pid = session.Process.pid
                        if pid and pid in self._original_volumes:
                            original_vol = self._original_volumes[pid]
                            volume_ctl = session._ctl.QueryInterface(ISimpleAudioVolume)
                            if volume_ctl:
                                volume_ctl.SetMasterVolume(original_vol, None)
                                restored_count += 1
                    except Exception as e_session:
                        logger.debug(f"[DUCKING] Skipped session restore due to error: {e_session}")

                if restored_count > 0:
                    logger.info(f"[DUCKING] Restored {restored_count} audio sessions to baseline.")
            except Exception as e:
                logger.error(f"[DUCKING] Failed during session volume restoration: {e}")
            finally:
                self._original_volumes.clear()
                self._is_ducked = False

    # Backwards compatibility alias for restore_all
    restore_volumes_sync = restore_all
    _apply_ducking_sync = duck_all
    _apply_restore_sync = restore_all

    # --- Asynchronous Interfaces with DAC Synchronization ---
    async def duck(self):
        """Attenuates active non-Aether audio sessions by duck_percentage."""
        if not self.enabled or not self.is_supported():
            return

        self._unduck_gen += 1

        async with self._lock:
            await asyncio.to_thread(self.duck_all)

    async def unduck(self):
        """Restores all modified audio sessions back to their original volume."""
        if not self.is_supported() or not self._is_ducked:
            return

        current_gen = self._unduck_gen

        # 1. Wait for DAC hardware audio output to finish speaking
        if self.is_output_active_fn:
            try:
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 5.0
                while self.is_output_active_fn() and loop.time() < deadline:
                    if current_gen != self._unduck_gen:
                        return
                    await asyncio.sleep(0.05)
            except Exception:
                pass

        # 2. Wait restoration delay
        if self.restore_delay_ms > 0:
            await asyncio.sleep(self.restore_delay_ms / 1000.0)

        if current_gen != self._unduck_gen:
            return

        async with self._lock:
            if not self._is_ducked or current_gen != self._unduck_gen:
                return

            await asyncio.to_thread(self.restore_all)

    # --- Scope Context Managers ---
    @asynccontextmanager
    async def duck_scope(self):
        """
        Async context manager guaranteeing session volumes are restored
        even if an unhandled exception or cancellation occurs.
        """
        await self.duck()
        try:
            yield self
        finally:
            await self.unduck()

    @contextmanager
    def sync_duck_scope(self, exclude_pids: Optional[Set[int]] = None):
        """
        Synchronous context manager guaranteeing session volumes are restored
        even if an unhandled exception or interrupt occurs.
        """
        self.duck_all(exclude_pids=exclude_pids)
        try:
            yield self
        finally:
            self.restore_all()


# External alias for backwards compatibility
AudioDucker = AudioDuckingService

