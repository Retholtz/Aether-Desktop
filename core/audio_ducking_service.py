"""
core/audio_ducking_service.py - Per-Session Windows Audio Attenuation
Ducks background game and media audio while keeping Aether's speech at 100%.
Includes fail-safe volume cleanup on normal shutdown, unhandled exceptions, and sudden process exit.
"""
import os
import json
import atexit
import asyncio
import threading
from contextlib import asynccontextmanager, contextmanager
from typing import Dict, Any, Optional, Callable, Set
from core.logger import get_logger
from core.paths import user_data_path

logger = get_logger("AudioDucking")

DEFAULT_DUCKING_RECOVERY_FILE = user_data_path("data", "ducking_recovery.json")

# Lazy-import pycaw and comtypes for non-Windows mock support
try:
    import comtypes
    from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
    PYCAW_AVAILABLE = True
except OSError as e:
    # If COM was already initialized in MTA mode by another subsystem (e.g. .NET CLR),
    # comtypes defaults to STA and raises WinError -2147417850 (RPC_E_CHANGED_MODE).
    # Setting sys.coinit_flags = 0 aligns comtypes with the existing MTA thread mode.
    if getattr(e, "winerror", None) == -2147417850 or "Cannot change thread mode" in str(e):
        try:
            import sys
            sys.coinit_flags = 0
            import comtypes
            from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
            PYCAW_AVAILABLE = True
        except Exception as retry_err:
            logger.warning(f"[AUDIO_DUCKING] Failed to load comtypes/pycaw after MTA retry: {retry_err}")
            PYCAW_AVAILABLE = False
            AudioUtilities = None
            ISimpleAudioVolume = None
    else:
        logger.warning(f"[AUDIO_DUCKING] pycaw/comtypes could not be loaded: {e}")
        PYCAW_AVAILABLE = False
        AudioUtilities = None
        ISimpleAudioVolume = None
except ImportError as e:
    logger.warning(f"[AUDIO_DUCKING] pycaw/comtypes could not be loaded: {e}")
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
        is_output_active_fn: Optional[Callable[[], bool]] = None,
        recovery_file: Optional[str] = DEFAULT_DUCKING_RECOVERY_FILE,
    ):
        self.enabled = enabled
        self.duck_percentage = max(0, min(100, int(duck_percentage)))
        self.restore_delay_ms = restore_delay_ms
        self.is_output_active_fn = is_output_active_fn
        self.own_pid = os.getpid()
        self._recovery_file: Optional[str] = recovery_file

        self._is_ducked: bool = False
        self._want_ducked: bool = False
        self._duck_in_progress: bool = False
        self._original_volumes: Dict[int, float] = {}  # session_pid -> pre-duck volume level (back-compat)
        self._session_volumes: Dict[Any, float] = {}   # (pid, instance_id) -> pre-duck volume level
        self._session_proc_names: Dict[Any, str] = {}  # skey -> process name for crash-recovery matching
        self._sync_lock = threading.RLock()
        self._lock = asyncio.Lock()
        self._unduck_gen: int = 0

        # Register process-level emergency restore via atexit
        atexit.register(self.restore_all)

        # If a previous Aether process exited abruptly (e.g. crash or os._exit) while ducked,
        # immediately restore any orphaned attenuated sessions on startup.
        if self._recovery_file and os.path.exists(self._recovery_file):
            try:
                self.recover_orphaned_volumes()
            except Exception as rec_err:
                logger.warning(f"[DUCKING] Startup orphan volume recovery warning: {rec_err}")

    def _load_recovery_journal(self) -> list:
        if not self._recovery_file or not os.path.exists(self._recovery_file):
            return []
        try:
            with open(self._recovery_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("sessions", []) if isinstance(data, dict) else []
            return entries if isinstance(entries, list) else []
        except Exception as e:
            logger.debug(f"[DUCKING] Could not read recovery journal: {e}")
            return []

    def _save_recovery_journal(self) -> None:
        if not self._recovery_file:
            return
        try:
            entries = []
            for skey, orig_vol in self._session_volumes.items():
                pid = skey[0] if isinstance(skey, tuple) else skey
                inst_id = skey[1] if isinstance(skey, tuple) and len(skey) > 1 else ""
                entries.append({
                    "pid": int(pid),
                    "instance_id": str(inst_id or ""),
                    "proc_name": str(self._session_proc_names.get(skey, "") or ""),
                    "original_vol": float(orig_vol),
                })
            if not entries:
                self._clear_recovery_journal()
                return
            parent = os.path.dirname(self._recovery_file)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp_path = f"{self._recovery_file}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({"owner_pid": self.own_pid, "sessions": entries}, f, indent=2)
            os.replace(tmp_path, self._recovery_file)
        except Exception as e:
            logger.debug(f"[DUCKING] Could not write recovery journal: {e}")

    def _clear_recovery_journal(self) -> None:
        if not self._recovery_file:
            return
        try:
            if os.path.exists(self._recovery_file):
                os.remove(self._recovery_file)
        except Exception as e:
            logger.debug(f"[DUCKING] Could not clear recovery journal: {e}")

    def recover_orphaned_volumes(self) -> int:
        """
        Restores audio sessions left attenuated by an ungraceful previous shutdown/crash
        using the persisted ducking recovery journal.
        Returns the number of sessions restored.
        """
        if not self.is_supported():
            return 0
        entries = self._load_recovery_journal()
        if not entries:
            return 0

        by_pid: Dict[int, float] = {}
        by_proc: Dict[str, float] = {}
        for item in entries:
            if not isinstance(item, dict):
                continue
            try:
                orig_v = max(0.0, min(1.0, float(item.get("original_vol", 1.0))))
            except Exception:
                continue
            pid_val = item.get("pid")
            if isinstance(pid_val, int) and pid_val > 0:
                by_pid[pid_val] = orig_v
            pname = str(item.get("proc_name") or "").lower().strip()
            if pname:
                by_proc[pname] = orig_v

        if not by_pid and not by_proc:
            self._clear_recovery_journal()
            return 0

        restored_count = 0
        with self._sync_lock:
            with self._com_apartment():
                try:
                    sessions = AudioUtilities.GetAllSessions()
                    for session in sessions:
                        try:
                            if not session.Process:
                                continue
                            pid = session.Process.pid
                            proc_name = ""
                            try:
                                proc_name = session.Process.name().lower()
                            except Exception:
                                pass
                            target_orig = None
                            if pid in by_pid:
                                target_orig = by_pid[pid]
                            elif proc_name and proc_name in by_proc:
                                target_orig = by_proc[proc_name]
                            if target_orig is None:
                                continue
                            volume_ctl = session._ctl.QueryInterface(ISimpleAudioVolume)
                            if volume_ctl:
                                current_vol = volume_ctl.GetMasterVolume()
                                if current_vol < target_orig - 0.01:
                                    volume_ctl.SetMasterVolume(target_orig, None)
                                    restored_count += 1
                        except Exception as e_sess:
                            logger.debug(f"[DUCKING] Orphan recovery skipped session: {e_sess}")
                    self._clear_recovery_journal()
                    if restored_count > 0:
                        logger.info(
                            f"[DUCKING] Recovered {restored_count} orphaned audio session(s) left ducked by a prior exit."
                        )
                except Exception as e:
                    logger.warning(f"[DUCKING] Orphan volume recovery failed: {e}")
        return restored_count

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
        (preferring MTA to match worker threads & .NET CLR, safely tolerating RPC_E_CHANGED_MODE)
        and properly uninitialized on block exit only when this scope initialized it.
        """
        initialized = False
        try:
            if comtypes:
                if hasattr(comtypes, "CoInitializeEx") and hasattr(comtypes, "COINIT_MULTITHREADED"):
                    comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
                    initialized = True
                else:
                    comtypes.CoInitialize()
                    initialized = True
        except OSError as exc:
            # RPC_E_CHANGED_MODE (-2147417850) means COM is already initialized in another apartment
            if getattr(exc, "winerror", None) != -2147417850 and "Cannot change thread mode" not in str(exc):
                try:
                    if comtypes:
                        comtypes.CoInitialize()
                        initialized = True
                except Exception:
                    pass
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

    @staticmethod
    def _get_session_key(session: Any, pid: int) -> Any:
        """Builds a unique key per audio session (disambiguates multi-session browsers sharing a PID)."""
        try:
            inst_id = getattr(session, "InstanceIdentifier", None)
            if isinstance(inst_id, str) and inst_id:
                return (pid, inst_id)
        except Exception:
            pass
        return pid

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

        with self._sync_lock:
            if self._is_ducked:
                return  # Prevent compounding attenuation; keep original volume baseline

            # Load any orphaned journal entries (e.g. from a prior crash/exit) so we NEVER
            # treat an already-ducked volume (like 40%) as the new baseline and double-duck it to 16%.
            orphan_by_pid: Dict[int, float] = {}
            orphan_by_proc: Dict[str, float] = {}
            for item in self._load_recovery_journal():
                if not isinstance(item, dict):
                    continue
                try:
                    ov = max(0.0, min(1.0, float(item.get("original_vol", 1.0))))
                except Exception:
                    continue
                if isinstance(item.get("pid"), int) and item["pid"] > 0:
                    orphan_by_pid[item["pid"]] = ov
                pn = str(item.get("proc_name") or "").lower().strip()
                if pn:
                    orphan_by_proc[pn] = ov

            with self._com_apartment():
                exclude = set(exclude_pids or ())
                exclude.add(self.own_pid)

                try:
                    sessions = AudioUtilities.GetAllSessions()
                    attenuation_factor = (100.0 - self.duck_percentage) / 100.0
                    ducked_count = 0
                    ducked_names = []

                    for session in sessions:
                        try:
                            if not session.Process:
                                continue

                            pid = session.Process.pid
                            proc_name = ""
                            try:
                                proc_name = session.Process.name().lower()
                            except Exception:
                                pass

                            # Keep Aether's own PID at 100% and unmuted; skip system exclusions without
                            # forcibly overriding volume of unrelated Python processes.
                            if not pid or pid in exclude or proc_name in self.SYSTEM_EXCLUSIONS or "aether" in proc_name:
                                if pid == self.own_pid:
                                    try:
                                        own_ctl = session._ctl.QueryInterface(ISimpleAudioVolume)
                                        if own_ctl:
                                            if own_ctl.GetMasterVolume() < 0.99:
                                                own_ctl.SetMasterVolume(1.0, None)
                                            if hasattr(own_ctl, "GetMute") and own_ctl.GetMute():
                                                own_ctl.SetMute(0, None)
                                    except Exception:
                                        pass
                                continue

                            volume_ctl = session._ctl.QueryInterface(ISimpleAudioVolume)
                            if not volume_ctl:
                                continue

                            current_vol = volume_ctl.GetMasterVolume()
                            skey = self._get_session_key(session, pid)

                            # Cache original baseline volume (never overwrite while ducked)
                            if skey not in self._session_volumes:
                                orphan_orig = orphan_by_pid.get(pid)
                                if orphan_orig is None and proc_name:
                                    orphan_orig = orphan_by_proc.get(proc_name)
                                if orphan_orig is not None and current_vol < orphan_orig - 0.01:
                                    base_vol = orphan_orig
                                elif current_vol <= 0.05:
                                    # Safeguard: if an application volume was left severely attenuated (<= 5%) from an
                                    # ungraceful shutdown, baseline it to 100% instead of locking in the degraded state.
                                    base_vol = 1.0
                                else:
                                    base_vol = current_vol
                                self._session_volumes[skey] = base_vol
                                self._session_proc_names[skey] = proc_name
                                if pid not in self._original_volumes:
                                    self._original_volumes[pid] = base_vol

                            original_vol = self._session_volumes[skey]
                            target_vol = max(0.0, min(1.0, original_vol * attenuation_factor))

                            # Idempotent: only apply if master volume differs noticeably (> 1%)
                            if abs(current_vol - target_vol) > 0.01:
                                volume_ctl.SetMasterVolume(target_vol, None)
                                ducked_count += 1
                                ducked_names.append(proc_name or str(pid))
                        except Exception as e_session:
                            logger.debug(f"[DUCKING] Skipped session due to error: {e_session}")

                    self._is_ducked = True
                    if self._session_volumes:
                        self._save_recovery_journal()
                    if ducked_count > 0:
                        logger.info(f"[DUCKING] Ducked {ducked_count} active audio sessions by {self.duck_percentage}%: {ducked_names}")
                except Exception as e:
                    logger.error(f"[DUCKING] Failed to duck sessions: {e}. Attempting rollback.")
                    self.restore_all()

    def restore_all(self) -> None:
        """
        Restores all ducked audio sessions to their pre-duck volume level.
        Thread-safe, idempotent, and safe for interpreter shutdown (atexit).
        Preserves saved baselines if enumeration fails mid-restore so a retry can still recover them.
        """
        if not self.is_supported():
            return

        with self._sync_lock:
            self._want_ducked = False
            if not self._original_volumes and not self._session_volumes and not self._is_ducked:
                # Still check for any orphaned journal on disk in case shutdown happens before first duck
                if self._recovery_file and os.path.exists(self._recovery_file):
                    self.recover_orphaned_volumes()
                return  # Nothing to restore

            with self._com_apartment():
                restored_count = 0
                enumeration_succeeded = False

                try:
                    sessions = AudioUtilities.GetAllSessions()
                    enumeration_succeeded = True
                    for session in sessions:
                        try:
                            if not session.Process:
                                continue

                            pid = session.Process.pid
                            if not pid:
                                continue
                            skey = self._get_session_key(session, pid)
                            if skey in self._session_volumes:
                                original_vol = self._session_volumes[skey]
                            elif pid in self._original_volumes:
                                original_vol = self._original_volumes[pid]
                            else:
                                continue

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
                    # Only clear baselines if enumeration succeeded (or if nothing was ever committed as ducked)
                    if enumeration_succeeded or not self._is_ducked:
                        self._session_volumes.clear()
                        self._original_volumes.clear()
                        self._session_proc_names.clear()
                        self._is_ducked = False
                        self._clear_recovery_journal()

    # Backwards compatibility alias for restore_all
    restore_volumes_sync = restore_all
    _apply_ducking_sync = duck_all
    _apply_restore_sync = restore_all

    # --- Asynchronous Interfaces with DAC Synchronization ---
    async def duck(self):
        """Attenuates active non-Aether audio sessions by duck_percentage."""
        if not self.enabled or not self.is_supported():
            return

        self._want_ducked = True
        self._unduck_gen += 1
        my_gen = self._unduck_gen

        async with self._lock:
            if not self._want_ducked or my_gen != self._unduck_gen:
                return
            self._duck_in_progress = True
            try:
                await asyncio.to_thread(self.duck_all)
            finally:
                self._duck_in_progress = False

            # Critical race guard: if unduck() arrived while duck_all() was executing
            # inside COM/pycaw on the worker thread, immediately restore volumes now.
            if not self._want_ducked and (self._is_ducked or self._original_volumes or self._session_volumes):
                await asyncio.to_thread(self.restore_all)

    async def unduck(self):
        """Restores all modified audio sessions back to their original volume."""
        self._want_ducked = False
        if not self.is_supported():
            return
        if not self._is_ducked and not self._duck_in_progress and not self._original_volumes and not self._session_volumes:
            return

        current_gen = self._unduck_gen

        # 1. Wait for DAC hardware audio output to finish speaking
        if self.is_output_active_fn:
            try:
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 5.0
                while self.is_output_active_fn() and loop.time() < deadline:
                    if self._want_ducked or current_gen != self._unduck_gen:
                        return
                    await asyncio.sleep(0.05)
            except Exception:
                pass

        # 2. Wait restoration delay
        if self.restore_delay_ms > 0:
            await asyncio.sleep(self.restore_delay_ms / 1000.0)

        if self._want_ducked or current_gen != self._unduck_gen:
            return

        async with self._lock:
            if self._want_ducked or current_gen != self._unduck_gen:
                return
            if not self._is_ducked and not self._original_volumes and not self._session_volumes:
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

