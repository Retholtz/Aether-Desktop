"""
core/telemetry/providers/file_provider.py - Native File Telemetry Reader (e.g., Elite Dangerous)
"""
import os
import json
from pathlib import Path
from typing import Optional
from core.telemetry.schema import TelemetryFrame, GameTelemetryProfile
from core.logger import get_logger

logger = get_logger("FileTelemetryProvider")


class NativeFileTelemetryProvider:
    def __init__(self, profile: GameTelemetryProfile):
        self.profile = profile
        self._last_mtime: float = 0.0
        self._last_frame: Optional[TelemetryFrame] = None

    def _resolve_path(self) -> Optional[Path]:
        if self.profile.native_file_path:
            p = Path(os.path.expandvars(self.profile.native_file_path)).expanduser()
            if p.exists():
                return p
        # Default fallback for Elite Dangerous
        if "elite" in self.profile.game_id.lower():
            default_ed = Path.home() / "Saved Games" / "Frontier Developments" / "Elite Dangerous" / "Status.json"
            if default_ed.exists():
                return default_ed
        return None

    def poll(self) -> Optional[TelemetryFrame]:
        path = self._resolve_path()
        if not path:
            return None

        try:
            mtime = path.stat().st_mtime
            if mtime == self._last_mtime and self._last_frame is not None:
                return self._last_frame

            raw_text = path.read_text(encoding="utf-8", errors="ignore").strip()
            if not raw_text:
                return self._last_frame

            data = json.loads(raw_text)
            self._last_mtime = mtime
            frame = self._parse_elite_status(data)
            self._last_frame = frame
            return frame
        except Exception as e:
            logger.debug("Transient file read skip on %s: %s", path, e)
            return self._last_frame

    def _parse_elite_status(self, data: dict) -> TelemetryFrame:
        flags = int(data.get("Flags", 0))
        states = []
        vitals = {}
        resources = {}

        # Elite Dangerous Status.json Bitmasks
        shields_up = bool(flags & (1 << 16))
        overheating = bool(flags & (1 << 20))
        low_fuel = bool(flags & (1 << 25))
        mass_locked = bool(flags & (1 << 26))
        in_danger = bool(flags & (1 << 30))

        vitals["shield_pct"] = 1.0 if shields_up else 0.0
        if not shields_up:
            states.append("shields_offline")
        if overheating:
            states.append("overheating")
        if low_fuel:
            states.append("low_fuel")
        if mass_locked:
            states.append("mass_locked")
        if in_danger:
            states.append("in_combat")

        fuel_info = data.get("Fuel", {})
        if isinstance(fuel_info, dict) and "FuelMain" in fuel_info:
            resources["fuel_tons"] = fuel_info["FuelMain"]

        if "Pips" in data:
            resources["pips"] = data["Pips"]  # [Sys, Eng, Wep]

        return TelemetryFrame(
            game_id=self.profile.game_id,
            vitals=vitals,
            resources=resources,
            states=states
        )
