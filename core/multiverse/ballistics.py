"""
core/multiverse/ballistics.py - Cross-Genre Firearm Translation Matrix
"""
from typing import Dict, Any


BALLISTIC_CONVERSION_TABLE = {
    "medium pistol": {
        "fantasy_name": "Repeating Hand Crossbow",
        "damage": "1d8+2 Piercing",
        "range": "30/120 ft.",
        "properties": ["Ammunition", "Light"],
        "finite_scarcity": True
    },
    "heavy pistol": {
        "fantasy_name": "Heavy Arbalest Pistol",
        "damage": "1d10+3 Piercing",
        "range": "40/150 ft.",
        "properties": ["Ammunition"],
        "finite_scarcity": True
    },
    "shotgun": {
        "fantasy_name": "Dwarven Blunderbuss",
        "damage": "2d8 Piercing",
        "range": "15 ft. Cone or 30 ft. Target",
        "properties": ["Ammunition", "Two-Handed", "Scatter"],
        "finite_scarcity": True
    },
    "assault rifle": {
        "fantasy_name": "Clockwork Volley Carbine",
        "damage": "2d10+3 Piercing",
        "range": "100/400 ft.",
        "properties": ["Ammunition", "Two-Handed", "Burst"],
        "finite_scarcity": True
    }
}


def transmute_ballistic_weapon(weapon_name: str) -> Dict[str, Any]:
    """Transmutes a modern or cyberpunk firearm into a balanced d20 fantasy profile."""
    w_low = weapon_name.strip().lower()
    for key, spec in BALLISTIC_CONVERSION_TABLE.items():
        if key in w_low:
            return {
                "name": f"{spec['fantasy_name']} ({weapon_name})",
                "atk_bonus": "+5",
                "damage": spec["damage"],
                "notes": f"Range: {spec['range']} | Properties: {', '.join(spec['properties'])}",
                "finite_scarcity": True
            }

    # Generic Fallback
    return {
        "name": f"Pneumatic Slug Thrower ({weapon_name})",
        "atk_bonus": "+4",
        "damage": "1d8 Piercing",
        "notes": "Range: 30/90 ft. | Foreign Munitions",
        "finite_scarcity": True
    }
