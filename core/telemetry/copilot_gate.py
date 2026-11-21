"""
core/telemetry/copilot_gate.py - Agnostic Edge-Triggered Event Gate & Cooldown Manager.

Evaluates normalized TelemetryFrames using generic vital hysteresis rules and
declarative per-module alert rules (no game-specific Python branches required).
"""

import time
from typing import Any, Dict, List, Optional, Set

from core.logger import get_logger
from core.telemetry.schema import TelemetryFrame

logger = get_logger("CopilotGate")


DEFAULT_STATE_ALERTS: Dict[str, Dict[str, Any]] = {
    "shields_offline": {
        "priority": 95,
        "critical": True,
        "prompt": "Co-pilot alert: Shields just went offline! Warn the player immediately in 6 words.",
    },
    "overheating": {
        "priority": 85,
        "critical": True,
        "prompt": "Co-pilot alert: Thermal overload / overheating! Warn the player in 6 words.",
    },
    "being_interdicted": {
        "priority": 96,
        "critical": True,
        "prompt": "Co-pilot alert: Interdiction detected! Warn the player immediately in 6 words.",
    },
    "low_oxygen": {
        "priority": 97,
        "critical": True,
        "prompt": "Co-pilot alert: Suit oxygen critical! Warn the player in 6 words.",
    },
    "low_fuel": {
        "priority": 50,
        "critical": False,
        "prompt": "Co-pilot alert: Fuel reserves below 25%. Notify the player briefly.",
    },
    "mass_locked": {
        "priority": 40,
        "critical": False,
        "prompt": "Co-pilot alert: Frame Shift Drive is mass locked. Mention briefly.",
    },
}

DEFAULT_RESOURCE_VITAL_RULES: Dict[str, Dict[str, Any]] = {
    "stamina_pct": {
        "key": "stamina_depleted",
        "warn_below": 0.05,
        "min_visible": 0.03,
        "reset_above": 0.35,
        "priority": 60,
        "critical": False,
        "prompt": "Co-pilot alert: Stamina exhausted. Give a brief 6-word tactical warning.",
    },
    "spirit_pct": {
        "key": "spirit_low",
        "warn_below": 0.08,
        "min_visible": 0.03,
        "reset_above": 0.35,
        "priority": 55,
        "critical": False,
        "prompt": "Co-pilot alert: Spirit energy depleted. Mention briefly in 6 words.",
    },
}


class CopilotEventGate:
    def __init__(self, global_cooldown_sec: float = 12.0):
        self.global_cooldown_sec = global_cooldown_sec
        self._last_spoken_ts: float = 0.0
        self._event_cooldowns: Dict[str, float] = {}
        self._active_states: Set[str] = set()
        self._health_warned: bool = False
        self._health_baseline_seen: bool = False
        self._last_hp: Optional[float] = None
        self._hull_warned: bool = False
        self._vital_warned: Dict[str, bool] = {}

    def evaluate(
        self,
        frame: TelemetryFrame,
        alert_rules: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Evaluates a normalized TelemetryFrame and returns a single high-priority
        co-pilot directive if a threshold edge or new state occurred outside cooldown.
        """
        now = frame.timestamp
        candidates: List[Dict[str, Any]] = []
        no_title_rule = "Do not use any title like 'Commander' unless configured for this game."
        custom_rules = alert_rules if isinstance(alert_rules, dict) else {}

        # 1. Health Threshold with Baseline Arming & Hysteresis (Warn < 30%, Reset > 50%)
        hp = frame.vitals.get("health_pct")
        if hp is not None:
            if hp >= 0.45:
                self._health_baseline_seen = True
                if hp > 0.50 and self._health_warned:
                    self._health_warned = False
            elif hp < 0.30 and hp >= 0.03 and self._health_baseline_seen and not self._health_warned:
                # Guard against instantaneous full-to-zero occlusion from opening Map/Inventory
                if self._last_hp is not None and (self._last_hp - hp) > 0.65 and hp < 0.06:
                    pass
                else:
                    self._health_warned = True
                    candidates.append({
                        "key": "low_health",
                        "priority": 100,
                        "critical": True,
                        "prompt": f"Co-pilot alert: Health critical at {int(hp * 100)}%. Warn the player in 8 words or less. {no_title_rule}",
                    })
            self._last_hp = hp

        # 1b. Hull / Armor Threshold for vehicle/space sims (Warn < 30%, Reset > 50%)
        hull = frame.vitals.get("hull_pct")
        if hull is not None:
            if hull < 0.30 and not self._hull_warned:
                self._hull_warned = True
                candidates.append({
                    "key": "low_hull",
                    "priority": 98,
                    "critical": True,
                    "prompt": f"Co-pilot alert: Hull integrity critical at {int(hull * 100)}%. Warn the player in 8 words or less.",
                })
            elif hull > 0.50 and self._hull_warned:
                self._hull_warned = False

        # 2. Generic / Module-Driven Secondary Resource Vitals (stamina, spirit, mana, energy, etc.)
        merged_vital_rules = dict(DEFAULT_RESOURCE_VITAL_RULES)
        for rk, rv in custom_rules.items():
            if not rk.startswith("state_") and isinstance(rv, dict):
                merged_vital_rules[rk] = rv

        for vital_key, rule in merged_vital_rules.items():
            val = frame.vitals.get(vital_key)
            if val is None:
                continue
            warn_below = float(rule.get("warn_below", 0.08))
            min_visible = float(rule.get("min_visible", 0.03))
            reset_above = float(rule.get("reset_above", 0.35))
            warned = self._vital_warned.get(vital_key, False)
            if val <= warn_below and val >= min_visible and not warned:
                self._vital_warned[vital_key] = True
                prompt_txt = str(rule.get("prompt") or f"Co-pilot alert: {vital_key} depleted. Mention briefly in 6 words.")
                if no_title_rule not in prompt_txt:
                    prompt_txt = f"{prompt_txt} {no_title_rule}"
                candidates.append({
                    "key": str(rule.get("key") or f"{vital_key}_low"),
                    "priority": int(rule.get("priority", 55)),
                    "critical": bool(rule.get("critical", False)),
                    "prompt": prompt_txt,
                })
            elif val > reset_above and warned:
                self._vital_warned[vital_key] = False

        # 3. Discrete State Edge Transitions (universal defaults + module-defined state_rules)
        current_states = set(frame.states)
        newly_entered = current_states - self._active_states
        self._active_states = current_states

        for state_key in newly_entered:
            spec = custom_rules.get(f"state_{state_key}") or DEFAULT_STATE_ALERTS.get(state_key)
            if isinstance(spec, dict) and spec.get("prompt"):
                candidates.append({
                    "key": f"state_{state_key}",
                    "priority": int(spec.get("priority", 50)),
                    "critical": bool(spec.get("critical", False)),
                    "prompt": str(spec["prompt"]),
                })

        if not candidates:
            return None

        # Sort highest priority first
        candidates.sort(key=lambda c: c["priority"], reverse=True)
        top = candidates[0]

        # Enforce global voice cooldown (critical alerts bypass if > 4s elapsed)
        elapsed = now - self._last_spoken_ts
        min_gap = 4.0 if top["critical"] else self.global_cooldown_sec
        if elapsed < min_gap:
            return None

        # Per-event 25-second anti-spam lock
        if now - self._event_cooldowns.get(top["key"], 0.0) < 25.0:
            return None

        self._last_spoken_ts = now
        self._event_cooldowns[top["key"]] = now
        logger.info("Co-pilot triggered [%s]: %s", top["key"], top["prompt"])
        return top
