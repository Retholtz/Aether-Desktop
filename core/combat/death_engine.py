"""
core/combat/death_engine.py - D&D 5e Death & Dying State Machine
"""
import random
from typing import Dict, Any, Optional, Tuple, Union
from pydantic import BaseModel, Field
from core.logger import get_logger

logger = get_logger("DeathEngine")


class DeathSaveTracker(BaseModel):
    successes: int = Field(default=0, ge=0, le=3)
    failures: int = Field(default=0, ge=0, le=3)
    status: str = Field(default="dying")  # "dying", "stable", "dead", "revived"


class DeathSaveResult(tuple):
    """
    Tuple (state, msg) compatible with both tuple unpacking and dict access.
    Supports:
        ds, msg = DeathEngine.resolve_death_save(...)
        res["failures"] / res.get("status")
    """
    def __new__(cls, state: Dict[str, Any], msg: str):
        return super().__new__(cls, (state, msg))

    def __init__(self, state: Dict[str, Any], msg: str):
        self.state = state
        self.msg = msg

    def __getitem__(self, item: Union[int, str]):
        if isinstance(item, str):
            return self.state[item]
        return super().__getitem__(item)

    def get(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, default)

    def __contains__(self, key: Any) -> bool:
        if isinstance(key, str):
            return key in self.state
        return super().__contains__(key)

    def keys(self):
        return self.state.keys()

    def values(self):
        return self.state.values()

    def items(self):
        return self.state.items()


class DeathEngine:
    @staticmethod
    def initialize_death_state() -> Dict[str, Any]:
        return {
            "successes": 0,
            "failures": 0,
            "status": "dying"
        }

    @classmethod
    def resolve_death_save(
        cls,
        death_state_or_entity: Dict[str, Any],
        roll_override: Optional[int] = None
    ) -> DeathSaveResult:
        """
        Resolves a single death saving throw.
        Accepts either a death_saves dict or an entity dict containing 'death_saves'.
        Returns: DeathSaveResult(updated_death_state, outcome_message)
        """
        is_entity = "death_saves" in death_state_or_entity and isinstance(death_state_or_entity["death_saves"], dict)
        state = dict(death_state_or_entity["death_saves"] if is_entity else death_state_or_entity)

        roll = roll_override if roll_override is not None else random.randint(1, 20)

        if roll == 20:
            state["status"] = "revived"
            state["successes"] = 0
            state["failures"] = 0
            msg = "Natural 20! Regains 1 HP and springs back to consciousness."

            if is_entity:
                vitals = death_state_or_entity.setdefault("vitals", {})
                health = vitals.setdefault("health", {"current": 0, "max": 10})
                health["current"] = 1
                conds = death_state_or_entity.setdefault("conditions", [])
                death_state_or_entity["conditions"] = [
                    c for c in conds if str(c).lower() not in ("unconscious", "prone", "dying")
                ]
                death_state_or_entity["death_saves"] = state

            return DeathSaveResult(state, msg)

        if roll == 1:
            state["failures"] = min(3, state.get("failures", 0) + 2)
            msg = f"Natural 1! Suffered 2 death save failures ({state['failures']}/3)."
        elif roll >= 10:
            state["successes"] = min(3, state.get("successes", 0) + 1)
            msg = f"Roll of {roll} succeeded ({state['successes']}/3)."
        else:
            state["failures"] = min(3, state.get("failures", 0) + 1)
            msg = f"Roll of {roll} failed ({state['failures']}/3)."

        if state["failures"] >= 3:
            state["status"] = "dead"
            msg += " Character has succumbed to their wounds and died."
            if is_entity:
                conds = death_state_or_entity.setdefault("conditions", [])
                if "Dead" not in conds:
                    conds.append("Dead")
        elif state["successes"] >= 3:
            state["status"] = "stable"
            msg += " Character is stabilized and unconscious."
            if is_entity:
                conds = death_state_or_entity.setdefault("conditions", [])
                if "Stable" not in conds:
                    conds.append("Stable")

        if is_entity:
            death_state_or_entity["death_saves"] = state

        return DeathSaveResult(state, msg)

    @classmethod
    def apply_damage_at_zero(
        cls,
        death_state_or_entity: Dict[str, Any],
        damage_amount: Optional[int] = None,
        is_critical: bool = False,
        max_hp: int = 10,
        damage: Optional[int] = None
    ) -> DeathSaveResult:
        """
        Applies RAW 5e damage consequences while at 0 HP.
        Supports both damage_amount and damage parameter names.
        """
        actual_damage = damage_amount if damage_amount is not None else (damage if damage is not None else 0)
        is_entity = "death_saves" in death_state_or_entity and isinstance(death_state_or_entity["death_saves"], dict)
        state = dict(death_state_or_entity["death_saves"] if is_entity else death_state_or_entity)

        # Massive Damage Check
        if actual_damage >= max_hp:
            state["failures"] = 3
            state["status"] = "dead"
            msg = f"Massive damage ({actual_damage} >= Max HP {max_hp})! Slayed instantly."
            if is_entity:
                conds = death_state_or_entity.setdefault("conditions", [])
                if "Dead" not in conds:
                    conds.append("Dead")
                death_state_or_entity["death_saves"] = state
            return DeathSaveResult(state, msg)

        added_fails = 2 if is_critical else 1
        state["failures"] = min(3, state.get("failures", 0) + added_fails)

        if state["failures"] >= 3:
            state["status"] = "dead"
            msg = f"Took damage at 0 HP ({added_fails} fail). Character has died."
            if is_entity:
                conds = death_state_or_entity.setdefault("conditions", [])
                if "Dead" not in conds:
                    conds.append("Dead")
        else:
            msg = f"Took damage at 0 HP. Accrued {added_fails} failure(s) ({state['failures']}/3)."

        if is_entity:
            death_state_or_entity["death_saves"] = state

        return DeathSaveResult(state, msg)
