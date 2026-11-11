"""
core/inventory/encumbrance.py - Dynamic Weight & Movement Penalty Engine
"""
import re
from typing import Dict, Any, List


def calculate_character_encumbrance(strength: int, inventory: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Computes total carried weight and evaluates 5e movement speed penalties.
    """
    total_weight = 0.0
    for item in inventory:
        w_val = 0.0
        w_raw = str(item.get("weight", "0")).replace("lb.", "").replace("lb", "").strip()
        m = re.search(r'[\d\.]+', w_raw)
        if m:
            try:
                w_val = float(m.group(0))
            except ValueError:
                w_val = 0.0

        qty = int(item.get("qty", 1)) if str(item.get("qty", 1)).isdigit() else 1
        total_weight += w_val * qty

    carrying_capacity = strength * 15.0
    push_drag_lift = strength * 30.0
    encumbered_limit = strength * 5.0

    is_encumbered = total_weight > carrying_capacity
    is_overloaded = total_weight > push_drag_lift

    speed_penalty = 0
    if is_overloaded:
        speed_penalty = 999  # Immobilized
    elif is_encumbered:
        speed_penalty = 20   # Standard encumbrance movement penalty (min 5 ft.)

    return {
        "weight_carried_lb": round(total_weight, 2),
        "carrying_capacity_lb": carrying_capacity,
        "push_drag_lift_lb": push_drag_lift,
        "encumbered_limit_lb": encumbered_limit,
        "is_encumbered": is_encumbered,
        "is_overloaded": is_overloaded,
        "speed_penalty_ft": speed_penalty
    }
