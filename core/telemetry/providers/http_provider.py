"""
core/telemetry/providers/http_provider.py - SSRF-Safe Localhost JSON Telemetry Poller.

Polls local-only (127.0.0.1 / localhost / [::1]) JSON telemetry endpoints exposed by
3rd-party simulation tools (e.g., Little Navmap for MSFS, SimHub for racing sims,
EDMC plugins, DCS-BIOS bridges) and maps fields into normalized TelemetryFrames using
declarative rules from `.aethermodule.json`.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from core.logger import get_logger
from core.telemetry.providers.file_provider import _get_nested
from core.telemetry.schema import GameTelemetryProfile, TelemetryFrame

logger = get_logger("HttpTelemetryProvider")

ALLOWED_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def is_safe_localhost_url(url: str) -> bool:
    """Returns True ONLY for http/https URLs targeting loopback (prevents SSRF)."""
    raw = str(url or "").strip()
    if not raw:
        return False
    try:
        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.hostname or "").strip().lower()
        return host in ALLOWED_LOOPBACK_HOSTS
    except Exception:
        return False


def _compare_threshold(actual: float, op: str, expected: float) -> bool:
    if op == "<":
        return actual < expected
    if op == "<=":
        return actual <= expected
    if op == ">":
        return actual > expected
    if op == ">=":
        return actual >= expected
    if op == "==":
        return actual == expected
    if op == "!=":
        return actual != expected
    return False


class LocalhostHttpTelemetryProvider:
    """Polls a loopback JSON HTTP endpoint and produces normalized TelemetryFrames."""

    def __init__(self, profile: GameTelemetryProfile):
        self.profile = profile
        self._last_frame: Optional[TelemetryFrame] = None
        self._consecutive_failures: int = 0
        self._backoff_until: float = 0.0

    def _fetch_json(self, url: str) -> Optional[Dict[str, Any]]:
        if not is_safe_localhost_url(url):
            logger.warning("Blocked non-localhost telemetry URL: %s", url)
            return None

        now = time.time()
        if now < self._backoff_until:
            return None

        timeout = max(0.2, min(5.0, float(self.profile.http_timeout_sec or 1.5)))
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "AetherDesktop/Telemetry"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(262_144).decode("utf-8", errors="replace").strip()
                if not raw:
                    return None
                parsed = json.loads(raw)
                self._consecutive_failures = 0
                self._backoff_until = 0.0
                return parsed if isinstance(parsed, dict) else {"data": parsed}
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            self._consecutive_failures += 1
            # Quiet exponential backoff up to 10s when the companion server isn't running
            backoff = min(10.0, 1.0 * (2 ** min(4, self._consecutive_failures - 1)))
            self._backoff_until = time.time() + backoff
            logger.debug("Localhost telemetry endpoint unavailable (%s): %s", url, e)
            return None

    def poll(self) -> Optional[TelemetryFrame]:
        url = (self.profile.http_endpoint or "").strip()
        if not url:
            return None

        data = self._fetch_json(url)
        if not data:
            return None

        frame = self._parse_payload(data)
        self._last_frame = frame
        return frame

    def _parse_payload(self, data: Dict[str, Any]) -> TelemetryFrame:
        decoded_flags: Dict[str, bool] = {}
        states: List[str] = []
        vitals: Dict[str, float] = {}
        resources: Dict[str, Any] = {}

        # 1. Optional bitmask fields
        for field_name, bit_map in (self.profile.bitmask_mappings or {}).items():
            raw_val = _get_nested(data, field_name)
            if raw_val is None or not isinstance(bit_map, dict):
                continue
            try:
                raw_bits = int(raw_val)
            except (TypeError, ValueError):
                continue
            for flag_name, mask in bit_map.items():
                decoded_flags[flag_name] = bool(raw_bits & int(mask))

        # 2. Declarative vitals
        for vital_name, spec in (self.profile.vital_mappings or {}).items():
            if not isinstance(spec, dict):
                continue
            if "from_flag" in spec and spec["from_flag"] in decoded_flags:
                vitals[vital_name] = 1.0 if decoded_flags[spec["from_flag"]] else 0.0
                continue
            path_expr = spec.get("path")
            if path_expr:
                raw_v = _get_nested(data, path_expr)
                if raw_v is not None:
                    try:
                        num = float(raw_v)
                        scale = float(spec.get("scale_if_gt_1") or 0)
                        if scale > 0 and num > 1.0:
                            num = num / scale
                        vitals[vital_name] = max(0.0, min(1.0, num))
                    except (TypeError, ValueError):
                        pass

        # 3. Declarative resources
        for res_name, path_expr in (self.profile.resource_mappings or {}).items():
            val = _get_nested(data, path_expr)
            if val is not None:
                resources[res_name] = val

        # 4. Flag-based state rules
        for rule in (self.profile.state_rules or []):
            flag_name = rule.get("flag")
            state_name = rule.get("state")
            if not flag_name or not state_name:
                continue
            if flag_name in decoded_flags:
                flag_val = decoded_flags[flag_name]
            else:
                direct = _get_nested(data, flag_name)
                if direct is None:
                    continue
                flag_val = bool(direct)
            if flag_val is bool(rule.get("when", True)) and state_name not in states:
                states.append(state_name)

        # 5. Numeric threshold state rules (e.g. {"path": "fuel_pct", "op": "<", "value": 0.2, "state": "low_fuel"})
        for trule in (self.profile.threshold_state_rules or []):
            path_expr = trule.get("path")
            op = str(trule.get("op") or "<").strip()
            state_name = trule.get("state")
            if not path_expr or not state_name or "value" not in trule:
                continue
            raw_v = vitals.get(path_expr)
            if raw_v is None:
                raw_v = _get_nested(data, path_expr)
            if raw_v is None:
                continue
            try:
                if _compare_threshold(float(raw_v), op, float(trule["value"])):
                    if state_name not in states:
                        states.append(state_name)
            except (TypeError, ValueError):
                pass

        return TelemetryFrame(
            game_id=self.profile.game_id,
            vitals=vitals,
            resources=resources,
            states=states,
        )
