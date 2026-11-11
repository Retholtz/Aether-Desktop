"""
core/combat/condition_manager.py - Condition Lifecycles and Turn Tick Engine
"""
import random
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from core.logger import get_logger

logger = get_logger("ConditionManager")


class ConditionRecord(BaseModel):
    condition: str
    duration_rounds: int = Field(default=1, ge=0)
    save_dc: Optional[int] = None
    save_type: Optional[str] = None  # e.g. "WIS", "CON", "DEX"
    save_timing: str = "end_of_turn"  # "start_of_turn", "end_of_turn", "none"


def tick_conditions(
    entity: Dict[str, Any],
    timing: str = "end_of_turn",
    roll_override: Optional[int] = None
) -> List[str]:
    """
    Ticks condition durations and auto-evaluates saving throws.
    Timing can be 'start_of_turn' or 'end_of_turn'.
    Modifies entity conditions in place and returns outcome logs.
    """
    logs: List[str] = []
    entity_name = entity.get("name", "Entity")

    # Retrieve or normalize structured conditions list
    structured: List[Dict[str, Any]] = entity.setdefault("structured_conditions", [])
    raw_conditions: List[str] = entity.setdefault("conditions", [])

    # If raw conditions contains conditions not in structured, sync them
    existing_cond_names = {c.get("condition", "").lower() for c in structured}
    for rc in raw_conditions:
        if rc.lower() not in existing_cond_names:
            structured.append({
                "condition": rc,
                "duration_rounds": 999,
                "save_dc": None,
                "save_type": None,
                "save_timing": "none"
            })

    surviving_structured: List[Dict[str, Any]] = []

    for c in structured:
        c_name = c.get("condition", "Unknown")
        c_timing = c.get("save_timing", "none")
        save_dc = c.get("save_dc")
        save_type = c.get("save_type")

        # Evaluate saving throw if timing matches
        save_succeeded = False
        if c_timing == timing and save_dc is not None and save_type:
            # Calculate modifier
            native = entity.get("native_system_data", {})
            saves = entity.get("saving_throws") or native.get("saving_throws") or {}
            mod = 0
            if save_type in saves:
                try:
                    mod = int(str(saves[save_type]).replace("+", ""))
                except ValueError:
                    mod = 0
            else:
                score = (
                    entity.get("attributes", {}).get(save_type)
                    or native.get("ability_scores", {}).get(save_type)
                    or 10
                )
                mod = (int(score) - 10) // 2

            roll = roll_override if roll_override is not None else random.randint(1, 20)
            total = roll + mod

            if total >= save_dc:
                save_succeeded = True
                logs.append(f"{entity_name} succeeded on DC {save_dc} {save_type} save ({total})! Ended {c_name}.")
            else:
                logs.append(f"{entity_name} failed DC {save_dc} {save_type} save ({total}). {c_name} persists.")

        if save_succeeded:
            continue

        # Tick duration if end of turn
        if timing == "end_of_turn":
            dur = c.get("duration_rounds", 1) - 1
            c["duration_rounds"] = max(0, dur)
            if dur <= 0 and c.get("duration_rounds") != 999:
                logs.append(f"{c_name} on {entity_name} expired naturally.")
                continue

        surviving_structured.append(c)

    # Reflect updated condition names back to entity['conditions']
    entity["structured_conditions"] = surviving_structured
    entity["conditions"] = [c["condition"] for c in surviving_structured]

    return logs
