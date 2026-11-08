"""
core/compendiums/dnd_weapons.py - D&D 5e (2014 SRD 5.1 & 2024 SRD 5.2) Weapons Database
Provides normalized Simple/Martial Melee/Ranged Weapons, Magic Weapons, 2024 Weapon
Masteries, filtering, and character-sheet formatting helpers.
"""
import os
import json
from functools import lru_cache
from typing import Dict, Any, List, Optional
from core.compendiums.homebrew_manager import HomebrewManager, get_default_homebrew_manager, query_match_rank

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


@lru_cache(maxsize=1)
def _load_srd_weapons() -> List[Dict[str, Any]]:
    path = os.path.join(_DATA_DIR, "srd_weapons.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def list_weapon_masteries() -> List[Dict[str, Any]]:
    path = os.path.join(_DATA_DIR, "srd_weapon_masteries.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _all_weapons(homebrew_mgr: Optional[HomebrewManager] = None) -> List[Dict[str, Any]]:
    mgr = homebrew_mgr or get_default_homebrew_manager()
    srd = [dict(x, compendium_type="weapons") for x in _load_srd_weapons()]
    hb = mgr.list_entries("weapons")
    if not hb:
        return srd
    by_idx = {x["index"]: x for x in srd}
    for h in hb:
        by_idx[h["index"]] = h
    return list(by_idx.values())


def list_weapons(
    edition: str = "all",
    category: Optional[str] = None,
    prop_filter: Optional[str] = None,
    mastery: Optional[str] = None,
    include_magic: bool = True,
    homebrew_only: bool = False,
    query: str = "",
    homebrew_mgr: Optional[HomebrewManager] = None,
) -> List[Dict[str, Any]]:
    items = _all_weapons(homebrew_mgr)
    ed = (edition or "all").lower().strip()
    q = (query or "").lower().strip()
    cat_f = (category or "").lower().strip()
    prop_f = (prop_filter or "").lower().strip()
    mast_f = (mastery or "").lower().strip()

    results = []
    for w in items:
        if homebrew_only and not w.get("is_homebrew"):
            continue
        if not include_magic and w.get("is_magic"):
            continue
        if ed in ("2014", "2024") and ed not in w.get("editions", ["2014", "2024"]):
            continue
        if cat_f and cat_f not in w.get("category", "").lower() and cat_f not in w.get("weapon_class", "").lower():
            continue
        if prop_f and not any(prop_f in p.lower() for p in w.get("properties", [])):
            continue
        if mast_f and mast_f not in w.get("mastery", "").lower():
            continue
        if q:
            haystack = " ".join([
                w.get("name", ""),
                w.get("category", ""),
                w.get("damage", ""),
                w.get("mastery", ""),
                " ".join(w.get("properties", [])),
                w.get("description", ""),
            ]).lower()
            if q not in haystack:
                continue
        results.append(w)

    return sorted(
        results,
        key=lambda x: (
            query_match_rank(x, q) if q else 0,
            not x.get("is_homebrew", False),
            x.get("is_magic", False),
            x.get("name", "").lower(),
        ),
    )


def get_weapon(
    name_or_index: str,
    homebrew_mgr: Optional[HomebrewManager] = None,
) -> Optional[Dict[str, Any]]:
    target = (name_or_index or "").strip().lower()
    if not target:
        return None
    items = _all_weapons(homebrew_mgr)
    for w in items:
        if w.get("index", "").lower() == target or w.get("name", "").lower() == target:
            return dict(w)
    # Partial match fallback
    for w in items:
        if target in w.get("name", "").lower():
            return dict(w)
    return None


def format_weapon_for_sheet(
    weapon: Dict[str, Any],
    str_mod: int = 0,
    dex_mod: int = 0,
    prof_bonus: int = 2,
    proficient: bool = True,
    edition: str = "2024",
) -> Dict[str, Any]:
    """Converts a compendium weapon record into a character-sheet weapon object."""
    props_low = [p.lower() for p in weapon.get("properties", [])]
    is_ranged = "ranged" in weapon.get("range_type", "").lower()
    is_finesse = "finesse" in props_low
    if is_finesse:
        ability_mod = max(str_mod, dex_mod)
    elif is_ranged:
        ability_mod = dex_mod
    else:
        ability_mod = str_mod

    magic_bonus = 0
    atk_raw = str(weapon.get("atk_bonus") or "").strip()
    if atk_raw.startswith("+") and atk_raw[1:].isdigit():
        magic_bonus = int(atk_raw[1:])

    total_atk = ability_mod + (prof_bonus if proficient else 0) + magic_bonus
    atk_str = f"+{total_atk}" if total_atk >= 0 else str(total_atk)

    dmg_mod = ability_mod + magic_bonus
    dmg_dice = weapon.get("damage_dice") or ""
    dmg_type = weapon.get("damage_type") or ""
    if dmg_dice:
        mod_suffix = f"+{dmg_mod}" if dmg_mod > 0 else (str(dmg_mod) if dmg_mod < 0 else "")
        dmg_str = f"{dmg_dice}{mod_suffix} {dmg_type}".strip()
    else:
        dmg_str = weapon.get("damage") or "1d4"

    notes_parts = list(weapon.get("properties", []))
    if weapon.get("range") and weapon.get("range") != "5 ft.":
        notes_parts.append(f"Range {weapon['range']}")
    if edition == "2024" and weapon.get("mastery"):
        notes_parts.append(f"Mastery: {weapon['mastery']}")

    return {
        "name": weapon["name"],
        "atk_bonus": atk_str,
        "damage": dmg_str,
        "notes": ", ".join(notes_parts),
        "weight": weapon.get("weight", 0),
        "cost": weapon.get("cost"),
        "mastery": weapon.get("mastery", ""),
        "compendium_index": weapon.get("index", ""),
    }
