"""
core/compendiums/dnd_armor.py - D&D 5e (2014 SRD 5.1 & 2024 SRD 5.2) Armor & Shields Database
Provides normalized Light/Medium/Heavy Armor, Shields, Magic Armor, and deterministic AC computation.
"""
import os
import json
from functools import lru_cache
from typing import Dict, Any, List, Optional
from core.compendiums.homebrew_manager import HomebrewManager, get_default_homebrew_manager, query_match_rank

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


@lru_cache(maxsize=1)
def _load_srd_armor() -> List[Dict[str, Any]]:
    path = os.path.join(_DATA_DIR, "srd_armor.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _all_armor(homebrew_mgr: Optional[HomebrewManager] = None) -> List[Dict[str, Any]]:
    mgr = homebrew_mgr or get_default_homebrew_manager()
    srd = [dict(x, compendium_type="armor") for x in _load_srd_armor()]
    hb = mgr.list_entries("armor")
    if not hb:
        return srd
    by_idx = {x["index"]: x for x in srd}
    for h in hb:
        by_idx[h["index"]] = h
    return list(by_idx.values())


def list_armor(
    edition: str = "all",
    category: Optional[str] = None,
    include_magic: bool = True,
    homebrew_only: bool = False,
    query: str = "",
    homebrew_mgr: Optional[HomebrewManager] = None,
) -> List[Dict[str, Any]]:
    items = _all_armor(homebrew_mgr)
    ed = (edition or "all").lower().strip()
    q = (query or "").lower().strip()
    cat_f = (category or "").lower().strip()

    results = []
    for a in items:
        if homebrew_only and not a.get("is_homebrew"):
            continue
        if not include_magic and a.get("is_magic"):
            continue
        if ed in ("2014", "2024") and ed not in a.get("editions", ["2014", "2024"]):
            continue
        if cat_f and cat_f not in a.get("category", "").lower():
            continue
        if q:
            haystack = " ".join([
                a.get("name", ""),
                a.get("category", ""),
                a.get("description", ""),
            ]).lower()
            if q not in haystack:
                continue
        results.append(a)

    return sorted(
        results,
        key=lambda x: (
            query_match_rank(x, q) if q else 0,
            not x.get("is_homebrew", False),
            x.get("is_magic", False),
            x.get("ac_base", 0),
            x.get("name", "").lower(),
        ),
    )


def get_armor(
    name_or_index: str,
    homebrew_mgr: Optional[HomebrewManager] = None,
) -> Optional[Dict[str, Any]]:
    target = (name_or_index or "").strip().lower()
    if not target:
        return None
    items = _all_armor(homebrew_mgr)
    for a in items:
        if a.get("index", "").lower() == target or a.get("name", "").lower() == target:
            return dict(a)
    for a in items:
        if target in a.get("name", "").lower():
            return dict(a)
    return None


def compute_armor_class(
    armor_name: Optional[str] = None,
    has_shield: bool = False,
    dex_mod: int = 0,
    shield_bonus: int = 2,
    magic_ac_bonus: int = 0,
    unarmored_bonus: int = 0,
    str_score: int = 10,
    homebrew_mgr: Optional[HomebrewManager] = None,
) -> Dict[str, Any]:
    """
    Computes total D&D 5e Armor Class and returns mechanical notes (e.g., stealth disadvantage, speed penalty).
    """
    warnings: List[str] = []
    armor_rec = get_armor(armor_name, homebrew_mgr=homebrew_mgr) if armor_name else None

    if not armor_rec or armor_rec.get("category") == "Shield":
        base_ac = 10 + dex_mod + unarmored_bonus
        stealth_disadv = False
        armor_label = "Unarmored"
    else:
        ac_base = int(armor_rec.get("ac_base") or 10)
        # Magic armor templates that only specify ac_bonus (+1/+2/+3) default to base 10 unless paired
        extra_magic = int(armor_rec.get("ac_bonus") or 0)
        if ac_base == 0 and extra_magic > 0:
            ac_base = 10
        if armor_rec.get("dex_bonus", False):
            max_dex = armor_rec.get("max_dex_bonus")
            applied_dex = min(dex_mod, int(max_dex)) if max_dex is not None else dex_mod
        else:
            applied_dex = 0

        base_ac = ac_base + applied_dex + extra_magic
        stealth_disadv = bool(armor_rec.get("stealth_disadvantage", False))
        armor_label = armor_rec["name"]

        str_min = int(armor_rec.get("str_minimum") or 0)
        if str_min > 0 and str_score < str_min:
            warnings.append(f"Requires STR {str_min} (current {str_score}): speed reduced by 10 ft.")

    total_ac = base_ac + (shield_bonus if has_shield else 0) + magic_ac_bonus
    return {
        "armor_class": total_ac,
        "armor_equipped": armor_label,
        "shield_equipped": has_shield,
        "stealth_disadvantage": stealth_disadv,
        "warnings": warnings,
    }
