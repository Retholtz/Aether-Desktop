"""
core/telemetry/telemetry_service.py - Background Telemetry Polling & Engine Bridge
"""
import asyncio
import time
from typing import Dict, Optional, Callable, Awaitable, Any
from core.telemetry.schema import GameTelemetryProfile, HudBarRegion
from core.telemetry.providers.file_provider import NativeFileTelemetryProvider
from core.telemetry.providers.vision_hud_provider import VisionHudTelemetryProvider
from core.telemetry.copilot_gate import CopilotEventGate
from core.logger import get_logger

logger = get_logger("TelemetryService")

DEFAULT_PROFILES: Dict[str, GameTelemetryProfile] = {
    "crimson_desert": GameTelemetryProfile(
        game_id="crimson_desert",
        provider_type="vision_hud",
        poll_interval_sec=0.4,
        hud_bars=[
            HudBarRegion(name="health", bbox_pct=(0.04, 0.05, 0.18, 0.015), color_profile="red"),
            HudBarRegion(name="stamina", bbox_pct=(0.04, 0.072, 0.15, 0.012), color_profile="green"),
            HudBarRegion(name="spirit", bbox_pct=(0.04, 0.092, 0.14, 0.012), color_profile="yellow"),
        ]
    ),
    "elite_dangerous_64": GameTelemetryProfile(
        game_id="elite_dangerous_64",
        provider_type="native_file",
        poll_interval_sec=0.5
    ),
    "elite_dangerous": GameTelemetryProfile(
        game_id="elite_dangerous",
        provider_type="native_file",
        poll_interval_sec=0.5
    ),
}


class TelemetryService:
    def __init__(self, on_copilot_trigger: Optional[Callable[[str], Awaitable[None]]] = None):
        self.enabled: bool = False
        self.active_game_id: Optional[str] = None
        self.on_copilot_trigger = on_copilot_trigger
        self.gate = CopilotEventGate()
        self._task: Optional[Any] = None
        self._pause_until_ts: float = 0.0

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
        loop: Optional[asyncio.AbstractEventLoop] = None
    ) -> bool:
        norm_id = (game_id or "").strip().lower().replace(" ", "_")
        self.active_game_id = norm_id
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

    async def _poll_loop(self):
        profile = DEFAULT_PROFILES.get(self.active_game_id or "")
        if not profile:
            logger.warning("No telemetry profile found for '%s'; defaulting to generic vision HUD.", self.active_game_id)
            profile = GameTelemetryProfile(game_id=self.active_game_id or "generic")

        provider = (
            NativeFileTelemetryProvider(profile)
            if profile.provider_type == "native_file"
            else VisionHudTelemetryProvider(profile)
        )

        while self.enabled:
            try:
                if profile.provider_type == "vision_hud" and time.time() < self._pause_until_ts:
                    await asyncio.sleep(profile.poll_interval_sec)
                    continue
                frame = await asyncio.to_thread(provider.poll)
                if frame:
                    alert = self.gate.evaluate(frame)
                    if alert and self.on_copilot_trigger:
                        await self.on_copilot_trigger(alert["prompt"])
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Telemetry poll error: %s", e)

            await asyncio.sleep(profile.poll_interval_sec)
