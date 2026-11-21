"""
core/telemetry/telemetry_service.py - Agnostic Background Telemetry Polling & Engine Bridge.

Resolves game telemetry profiles dynamically from declarative JSON Game Modules
(`data/game_modules/*.aethermodule.json`) and user profile overrides, with automatic
3rd-party co-pilot alert suppression (e.g. EDCoPilot.exe coexistence).
"""

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from core.logger import get_logger
from core.telemetry.copilot_gate import CopilotEventGate
from core.telemetry.providers.file_provider import NativeFileTelemetryProvider
from core.telemetry.providers.http_provider import LocalhostHttpTelemetryProvider
from core.telemetry.providers.vision_hud_provider import VisionHudTelemetryProvider
from core.telemetry.schema import GameTelemetryProfile

logger = get_logger("TelemetryService")

# Dynamically populated on demand from bundled + user `.aethermodule.json` files
DEFAULT_PROFILES: Dict[str, GameTelemetryProfile] = {}


def _is_any_process_running(process_names: List[str]) -> bool:
    """Returns True if any listed companion process (e.g. 'EDCoPilot.exe') is currently running."""
    if not process_names:
        return False
    targets = {str(p).strip().lower() for p in process_names if str(p).strip()}
    if not targets:
        return False
    try:
        import psutil

        for proc in psutil.process_iter(["name"]):
            pname = (proc.info.get("name") or "").strip().lower()
            if pname in targets:
                return True
    except Exception:
        pass
    return False


class TelemetryService:
    def __init__(self, on_copilot_trigger: Optional[Callable[[str], Awaitable[None]]] = None):
        self.enabled: bool = False
        self.active_game_id: Optional[str] = None
        self._profile_override: Optional[Dict[str, Any]] = None
        self.on_copilot_trigger = on_copilot_trigger
        self.gate = CopilotEventGate()
        self._task: Optional[Any] = None
        self._pause_until_ts: float = 0.0
        self._last_suppress_check_ts: float = 0.0
        self._alerts_suppressed_by_companion: bool = False

    def notify_ui_macro_triggered(self, phrase: str = "", pause_sec: float = 8.0) -> None:
        """Pauses vision HUD telemetry sampling briefly when UI overlays (Map, Inventory, Pause) open."""
        lower = (phrase or "").lower()
        if any(tok in lower for tok in ("map", "inventory", "menu", "pause")):
            self._pause_until_ts = time.time() + pause_sec
            logger.debug("Paused vision HUD telemetry for %.1fs after UI macro '%s'.", pause_sec, phrase)

    def set_enabled(
        self,
        game_id: str,
        enabled: bool,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        profile_override: Optional[Dict[str, Any]] = None,
    ) -> bool:
        norm_id = (game_id or "").strip().lower().replace(" ", "_")
        self.active_game_id = norm_id
        self._profile_override = profile_override
        self.enabled = bool(enabled)

        if self.enabled:
            self.gate = CopilotEventGate()
            if not self._task or self._task.done():
                self._schedule_poll_loop(loop)
            logger.info("Telemetry enabled for game: %s", norm_id)
        else:
            if self._task and not self._task.done():
                self._task.cancel()
            logger.info("Telemetry disabled.")
        return self.enabled

    def _schedule_poll_loop(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop and (loop is None or loop is running_loop):
            self._task = running_loop.create_task(self._poll_loop())
        elif loop and loop.is_running():
            self._task = asyncio.run_coroutine_threadsafe(self._poll_loop(), loop)
        else:
            logger.debug("Telemetry enabled without an active asyncio loop; background poll deferred.")

    def _resolve_active_profile(self) -> GameTelemetryProfile:
        from core.game_modules import build_telemetry_profile_from_module

        gid = self.active_game_id or "generic"
        resolved = build_telemetry_profile_from_module(gid, profile=self._profile_override)
        if resolved is not None:
            DEFAULT_PROFILES[gid] = resolved
            return resolved
        fallback = DEFAULT_PROFILES.get(gid)
        if fallback is not None:
            return fallback
        logger.warning("No telemetry module found for '%s'; defaulting to generic vision HUD.", gid)
        return GameTelemetryProfile(game_id=gid)

    async def _poll_loop(self):
        profile = self._resolve_active_profile()

        if profile.provider_type == "native_file":
            provider = NativeFileTelemetryProvider(profile)
        elif profile.provider_type == "localhost_http":
            provider = LocalhostHttpTelemetryProvider(profile)
        else:
            provider = VisionHudTelemetryProvider(profile)

        while self.enabled:
            try:
                if profile.provider_type == "vision_hud" and time.time() < self._pause_until_ts:
                    await asyncio.sleep(profile.poll_interval_sec)
                    continue
                frame = await asyncio.to_thread(provider.poll)
                if frame:
                    # Respect 3rd-party co-pilot alert suppression (checked at most every 5s)
                    now = time.time()
                    if profile.suppress_alerts_when_running and (now - self._last_suppress_check_ts >= 5.0):
                        self._last_suppress_check_ts = now
                        self._alerts_suppressed_by_companion = await asyncio.to_thread(
                            _is_any_process_running, profile.suppress_alerts_when_running
                        )
                    alert = self.gate.evaluate(frame, alert_rules=profile.alert_rules)
                    if alert and self.on_copilot_trigger and not self._alerts_suppressed_by_companion:
                        await self.on_copilot_trigger(alert["prompt"])
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Telemetry poll error: %s", e)

            await asyncio.sleep(profile.poll_interval_sec)
