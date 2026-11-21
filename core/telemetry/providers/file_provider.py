"""
core/telemetry/providers/file_provider.py - Universal Schema-Driven JSON File Telemetry Provider.

Reads any live JSON status file (plus optional sibling companion JSONs like NavRoute.json,
Cargo.json, Market.json) using declarative field, bitmask, and state mappings supplied by
the game's `.aethermodule.json` or `GameTelemetryProfile`.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logger import get_logger
from core.telemetry.schema import GameTelemetryProfile, TelemetryFrame

logger = get_logger("FileTelemetryProvider")


def _get_nested(data: Dict[str, Any], dotted_path: str) -> Any:
    """Safely resolves a dotted path like 'Fuel.FuelMain' from a nested dict."""
    cur: Any = data
    for part in str(dotted_path or "").split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


class NativeFileTelemetryProvider:
    """Generic file-backed telemetry reader driven by declarative module mappings."""

    def __init__(self, profile: GameTelemetryProfile):
        self.profile = self._hydrate_profile_mappings(profile)
        self._last_mtime: float = 0.0
        self._last_companion_mtimes: Dict[str, float] = {}
        self._last_frame: Optional[TelemetryFrame] = None

    def _hydrate_profile_mappings(self, profile: GameTelemetryProfile) -> GameTelemetryProfile:
        """
        If a caller constructed a minimal `GameTelemetryProfile(game_id=..., native_file_path=...)`
        without explicit mappings, hydrate declarative mappings from the matching JSON module.
        """
        if profile.bitmask_mappings or profile.vital_mappings or profile.resource_mappings:
            return profile
        try:
            from core.game_modules import build_telemetry_profile_from_module

            mod_prof = build_telemetry_profile_from_module(profile.game_id)
            if mod_prof:
                return profile.model_copy(
                    update={
                        "native_file_path": profile.native_file_path or mod_prof.native_file_path,
                        "companion_files": profile.companion_files or mod_prof.companion_files,
                        "bitmask_mappings": mod_prof.bitmask_mappings,
                        "state_rules": mod_prof.state_rules,
                        "vital_mappings": mod_prof.vital_mappings,
                        "resource_mappings": mod_prof.resource_mappings,
                        "alert_rules": profile.alert_rules or mod_prof.alert_rules,
                        "suppress_alerts_when_running": (
                            profile.suppress_alerts_when_running or mod_prof.suppress_alerts_when_running
                        ),
                    }
                )
        except Exception as e:
            logger.debug("Module hydration skipped for '%s': %s", profile.game_id, e)
        return profile

    def _resolve_path(self) -> Optional[Path]:
        if self.profile.native_file_path:
            p = Path(os.path.expandvars(self.profile.native_file_path)).expanduser()
            if p.exists():
                return p
        return None

    def _read_companion_files(self, status_path: Path) -> Dict[str, Any]:
        """Reads optional sibling JSON companion files (e.g. NavRoute.json, Cargo.json, Market.json)."""
        extras: Dict[str, Any] = {}
        if not self.profile.companion_files:
            return extras

        base_dir = status_path.parent
        for fname in self.profile.companion_files:
            cpath = base_dir / fname
            if not cpath.is_file():
                continue
            try:
                raw = cpath.read_text(encoding="utf-8", errors="ignore").strip()
                if not raw:
                    continue
                cdata = json.loads(raw)
                stem = cpath.stem.lower()
                if stem == "navroute" and isinstance(cdata, dict):
                    route = cdata.get("Route") or []
                    if isinstance(route, list) and route:
                        extras["route_jumps"] = max(0, len(route) - 1)
                        dest = route[-1]
                        if isinstance(dest, dict) and dest.get("StarSystem"):
                            extras["route_destination"] = dest["StarSystem"]
                elif stem == "cargo" and isinstance(cdata, dict):
                    extras["cargo_count"] = int(cdata.get("Count", 0))
                    inv = cdata.get("Inventory") or []
                    if isinstance(inv, list):
                        extras["cargo_items"] = [
                            {
                                "name": item.get("Name_Localised") or item.get("Name"),
                                "count": item.get("Count", 0),
                            }
                            for item in inv[:15]
                            if isinstance(item, dict)
                        ]
                elif stem == "market" and isinstance(cdata, dict):
                    if cdata.get("StationName"):
                        extras["market_station"] = cdata["StationName"]
                    if cdata.get("StarSystem"):
                        extras["market_system"] = cdata["StarSystem"]
                elif isinstance(cdata, dict):
                    extras[stem] = cdata
            except Exception as e:
                logger.debug("Companion file read skip on %s: %s", cpath, e)
        return extras

    def _companion_mtimes_changed(self, status_path: Path) -> bool:
        if not self.profile.companion_files:
            return False
        base_dir = status_path.parent
        changed = False
        for fname in self.profile.companion_files:
            cpath = base_dir / fname
            mtime = cpath.stat().st_mtime if cpath.is_file() else 0.0
            if self._last_companion_mtimes.get(fname) != mtime:
                self._last_companion_mtimes[fname] = mtime
                changed = True
        return changed

    def poll(self) -> Optional[TelemetryFrame]:
        path = self._resolve_path()
        if not path:
            return None

        try:
            mtime = path.stat().st_mtime
            comp_changed = self._companion_mtimes_changed(path)
            if mtime == self._last_mtime and not comp_changed and self._last_frame is not None:
                return self._last_frame

            raw_text = path.read_text(encoding="utf-8", errors="ignore").strip()
            if not raw_text:
                return self._last_frame

            data = json.loads(raw_text)
            self._last_mtime = mtime
            companion_resources = self._read_companion_files(path)
            frame = self._parse_json_status(data, companion_resources=companion_resources)
            self._last_frame = frame
            return frame
        except Exception as e:
            logger.debug("Transient file read skip on %s: %s", path, e)
            return self._last_frame

    def _parse_json_status(
        self,
        data: Dict[str, Any],
        companion_resources: Optional[Dict[str, Any]] = None,
    ) -> TelemetryFrame:
        decoded_flags: Dict[str, bool] = {}
        states: List[str] = []
        vitals: Dict[str, float] = {}
        resources: Dict[str, Any] = dict(companion_resources or {})

        # 1. Decode declarative bitmasks (e.g. Flags, Flags2, or any integer status field)
        for field_name, bit_map in (self.profile.bitmask_mappings or {}).items():
            if field_name not in data or not isinstance(bit_map, dict):
                continue
            try:
                raw_bits = int(data.get(field_name, 0))
            except (TypeError, ValueError):
                continue
            for flag_name, mask in bit_map.items():
                decoded_flags[flag_name] = bool(raw_bits & int(mask))

        # 2. Evaluate declarative state rules
        for rule in (self.profile.state_rules or []):
            flag_name = rule.get("flag")
            state_name = rule.get("state")
            if not flag_name or not state_name:
                continue
            req_flag = rule.get("require_flag")
            if req_flag and req_flag in decoded_flags:
                # If a required context flag (e.g. in_main_ship) is explicitly known and False,
                # skip only when an alternate mode bit (like on_foot / in_srv / in_fighter) is set.
                alt_active = any(
                    decoded_flags.get(alt, False)
                    for alt in ("on_foot", "in_srv", "in_fighter")
                )
                if not decoded_flags[req_flag] and alt_active:
                    continue
            if flag_name in decoded_flags:
                flag_val = decoded_flags[flag_name]
            elif "default_if_missing" in rule:
                flag_val = bool(rule["default_if_missing"])
            else:
                continue
            if flag_val is bool(rule.get("when", True)):
                if state_name not in states:
                    states.append(state_name)

        # 3. Extract declarative vitals
        for vital_name, spec in (self.profile.vital_mappings or {}).items():
            if not isinstance(spec, dict):
                continue
            if "from_flag" in spec:
                fn = spec["from_flag"]
                if fn in decoded_flags:
                    vitals[vital_name] = 1.0 if decoded_flags[fn] else 0.0
                continue
            path_expr = spec.get("path")
            if path_expr:
                raw_val = _get_nested(data, path_expr)
                if raw_val is not None:
                    try:
                        num = float(raw_val)
                        scale = float(spec.get("scale_if_gt_1") or 0)
                        if scale > 0 and num > 1.0:
                            num = num / scale
                        vitals[vital_name] = max(0.0, min(1.0, num))
                    except (TypeError, ValueError):
                        pass

        # 4. Extract declarative resources
        for res_name, path_expr in (self.profile.resource_mappings or {}).items():
            val = _get_nested(data, path_expr)
            if val is not None:
                resources[res_name] = val

        # 5. Generic fallback if no mappings were configured on a custom JSON file
        if not self.profile.bitmask_mappings and not self.profile.vital_mappings and not self.profile.resource_mappings:
            for k, v in data.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    if k.lower().endswith("_pct"):
                        vitals[k.lower()] = max(0.0, min(1.0, float(v) / 100.0 if v > 1.0 else float(v)))
                    else:
                        resources[k] = v
                elif isinstance(v, (str, list)):
                    resources[k] = v

        return TelemetryFrame(
            game_id=self.profile.game_id,
            vitals=vitals,
            resources=resources,
            states=states,
        )
