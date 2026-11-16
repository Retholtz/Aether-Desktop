"""
core/telemetry/copilot_gate.py - Edge-Triggered Event Gate & Cooldown Manager
"""
import time
from typing import Dict, Any, List, Optional, Set
from core.telemetry.schema import TelemetryFrame
from core.logger import get_logger

logger = get_logger("CopilotGate")


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
        self._stamina_warned: bool = False
        self._spirit_warned: bool = False

    def evaluate(self, frame: TelemetryFrame) -> Optional[Dict[str, Any]]:
        """
        Evaluates a normalized TelemetryFrame and returns a single high-priority
        co-pilot directive if a threshold edge or new state occurred outside cooldown.
        """
        now = frame.timestamp
        candidates: List[Dict[str, Any]] = []
        no_title_rule = "Do not use any title like 'Commander' unless configured for this game."

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
                        "prompt": f"Co-pilot alert: Health critical at {int(hp * 100)}%. Warn the player in 8 words or less. {no_title_rule}"
                    })
            self._last_hp = hp

        # 1b. Hull / Armor Threshold for space/vehicle sims (Warn < 30%, Reset > 50%)
        hull = frame.vitals.get("hull_pct")
        if hull is not None:
            if hull < 0.30 and not self._hull_warned:
                self._hull_warned = True
                candidates.append({
                    "key": "low_hull",
                    "priority": 98,
                    "critical": True,
                    "prompt": f"Co-pilot alert: Hull integrity critical at {int(hull * 100)}%. Warn the player in 8 words or less."
                })
            elif hull > 0.50 and self._hull_warned:
                self._hull_warned = False

        # 2. Stamina Depletion with Hysteresis (Warn <= 5%, Reset > 35%)
        stam = frame.vitals.get("stamina_pct")
        if stam is not None:
            if stam <= 0.05 and stam >= 0.03 and not self._stamina_warned:
                self._stamina_warned = True
                candidates.append({
                    "key": "stamina_depleted",
                    "priority": 60,
                    "critical": False,
                    "prompt": f"Co-pilot alert: Stamina exhausted. Give a brief 6-word tactical warning. {no_title_rule}"
                })
            elif stam > 0.35 and self._stamina_warned:
                self._stamina_warned = False

        # 2b. Spirit / Mana Depletion (Crimson Desert Spirit bar: Warn <= 8%, Reset > 35%)
        spirit = frame.vitals.get("spirit_pct")
        if spirit is not None:
            if spirit <= 0.08 and spirit >= 0.03 and not self._spirit_warned:
                self._spirit_warned = True
                candidates.append({
                    "key": "spirit_low",
                    "priority": 55,
                    "critical": False,
                    "prompt": f"Co-pilot alert: Spirit energy depleted. Mention briefly in 6 words. {no_title_rule}"
                })
            elif spirit > 0.35 and self._spirit_warned:
                self._spirit_warned = False

        # 3. Discrete State Edge Transitions (e.g., shields_offline, overheating, low_fuel)
        current_states = set(frame.states)
        newly_entered = current_states - self._active_states
        self._active_states = current_states

        STATE_PROMPTS = {
            "shields_offline": (95, True, "Co-pilot alert: Shields just went offline! Warn the player immediately in 6 words."),
            "overheating": (85, True, "Co-pilot alert: Thermal overload / overheating! Warn the player in 6 words."),
            "low_fuel": (50, False, "Co-pilot alert: Fuel reserves below 25%. Notify the player briefly."),
            "mass_locked": (40, False, "Co-pilot alert: Frame Shift Drive is mass locked. Mention briefly.")
        }

        for state_key in newly_entered:
            if state_key in STATE_PROMPTS:
                prio, is_crit, prompt_txt = STATE_PROMPTS[state_key]
                candidates.append({
                    "key": f"state_{state_key}",
                    "priority": prio,
                    "critical": is_crit,
                    "prompt": prompt_txt
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
