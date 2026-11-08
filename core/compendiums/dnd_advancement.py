"""
core/compendiums/dnd_advancement.py - D&D 5e (2014 SRD 5.1 & 2024 SRD 5.2) Leveling & Multiclassing Engine
Supports single-class and multiclass progression (Levels 1-20), 2014 vs 2024 subclass & slot rules,
multiclass exit/entry prerequisites (with optional DM override), fixed/rolled HP with retroactive CON,
ASI / Feats / 2024 Level-19 Epic Boons, Pact Magic / Mystic Arcanum, and class spell/meter synchronization.
"""
import os
import re
import copy
import json
import random
from functools import lru_cache
from typing import Dict, Any, List, Optional, Tuple

from core.compendiums.dnd_spells import (
    get_spell,
    get_spells_for_class,
    format_spell_for_sheet,
    CLASS_PREPARATION_TYPE,
)
from core.parsers.dnd5e_parser import (
    WARLOCK_PACT_SLOTS,
    MULTICLASS_SLOT_TABLE,
    extract_mystic_arcanum_meters,
)

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

XP_THRESHOLDS: Dict[int, int] = {
    1: 0, 2: 300, 3: 900, 4: 2700, 5: 6500,
    6: 14000, 7: 23000, 8: 34000, 9: 48000, 10: 64000,
    11: 85000, 12: 100000, 13: 120000, 14: 140000, 15: 165000,
    16: 195000, 17: 225000, 18: 265000, 19: 305000, 20: 355000,
}

HALF_CASTER_SINGLE_TABLE: Dict[int, List[int]] = {
    1:  [2, 0, 0, 0, 0],
    2:  [2, 0, 0, 0, 0],
    3:  [3, 0, 0, 0, 0],
    4:  [3, 0, 0, 0, 0],
    5:  [4, 2, 0, 0, 0],
    6:  [4, 2, 0, 0, 0],
    7:  [4, 3, 0, 0, 0],
    8:  [4, 3, 0, 0, 0],
    9:  [4, 3, 2, 0, 0],
    10: [4, 3, 2, 0, 0],
    11: [4, 3, 3, 0, 0],
    12: [4, 3, 3, 0, 0],
    13: [4, 3, 3, 1, 0],
    14: [4, 3, 3, 1, 0],
    15: [4, 3, 3, 2, 0],
    16: [4, 3, 3, 2, 0],
    17: [4, 3, 3, 3, 1],
    18: [4, 3, 3, 3, 1],
    19: [4, 3, 3, 3, 2],
    20: [4, 3, 3, 3, 2],
}

THIRD_CASTER_SINGLE_ECL: Dict[int, int] = {
    1: 0, 2: 0, 3: 1, 4: 2, 5: 2, 6: 2,
    7: 3, 8: 3, 9: 3, 10: 4, 11: 4, 12: 4,
    13: 5, 14: 5, 15: 5, 16: 6, 17: 6, 18: 6,
    19: 7, 20: 7,
}

ABILITY_ORDER = ("STR", "DEX", "CON", "INT", "WIS", "CHA")

ABILITY_FULL_NAMES: Dict[str, str] = {
    "STR": "Strength",
    "DEX": "Dexterity",
    "CON": "Constitution",
    "INT": "Intelligence",
    "WIS": "Wisdom",
    "CHA": "Charisma",
}

ABILITY_ALIASES: Dict[str, Tuple[str, ...]] = {
    "STR": ("STR", "Strength", "Might"),
    "DEX": ("DEX", "Dexterity", "Agility"),
    "CON": ("CON", "Constitution", "Vigor"),
    "INT": ("INT", "Intelligence", "Intellect"),
    "WIS": ("WIS", "Wisdom"),
    "CHA": ("CHA", "Charisma"),
}

ALL_DND_SKILLS: Dict[str, str] = {
    "Acrobatics": "DEX",
    "Animal Handling": "WIS",
    "Arcana": "INT",
    "Athletics": "STR",
    "Deception": "CHA",
    "History": "INT",
    "Insight": "WIS",
    "Intimidation": "CHA",
    "Investigation": "INT",
    "Medicine": "WIS",
    "Nature": "INT",
    "Perception": "WIS",
    "Performance": "CHA",
    "Persuasion": "CHA",
    "Religion": "INT",
    "Sleight of Hand": "DEX",
    "Stealth": "DEX",
    "Survival": "WIS",
}


@lru_cache(maxsize=1)
def _load_srd_classes_data() -> Dict[str, Any]:
    path = os.path.join(_DATA_DIR, "srd_classes.json")
    if not os.path.exists(path):
        return {"xp_thresholds": {}, "classes": {}, "feats": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_classes(edition: str = "2024") -> List[Dict[str, Any]]:
    data = _load_srd_classes_data()
    ed = "2014" if str(edition).strip() == "2014" else "2024"
    out = []
    for name, cdef in data.get("classes", {}).items():
        c = dict(cdef)
        c["active_subclass_level"] = cdef.get(f"subclass_level_{ed}", 3)
        out.append(c)
    return out


def get_class_definition(class_name: str) -> Optional[Dict[str, Any]]:
    if not class_name:
        return None
    classes = _load_srd_classes_data().get("classes", {})
    raw = class_name.strip().lower()
    for name, cdef in classes.items():
        if name.lower() == raw or cdef.get("id") == raw:
            return dict(cdef)
    for name, cdef in classes.items():
        if name.lower() in raw:
            return dict(cdef)
    return None


def list_feats(edition: str = "2024", level: int = 4) -> List[Dict[str, Any]]:
    ed = "2014" if str(edition).strip() == "2014" else "2024"
    feats = _load_srd_classes_data().get("feats", [])
    out = []
    for f in feats:
        if ed not in f.get("editions", ["2014", "2024"]):
            continue
        if f.get("type") == "epic_boon" and level < 19:
            continue
        out.append(dict(f))
    return out


def get_feat(name: str) -> Optional[Dict[str, Any]]:
    if not name:
        return None
    low = name.strip().lower()
    for f in _load_srd_classes_data().get("feats", []):
        if f.get("name", "").lower() == low:
            return dict(f)
    return None


def proficiency_bonus_for_level(total_level: int) -> int:
    lvl = max(1, min(20, int(total_level or 1)))
    return 2 + ((lvl - 1) // 4)


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        if isinstance(val, bool):
            return default
        if isinstance(val, (int, float)):
            return int(val)
        m = re.search(r"[-+]?\d+", str(val or ""))
        return int(m.group(0)) if m else default
    except Exception:
        return default


def normalize_ability_key(ab: str) -> Optional[str]:
    low = (ab or "").strip().lower()
    mapping = {
        "str": "STR", "strength": "STR", "might": "STR",
        "dex": "DEX", "dexterity": "DEX", "agility": "DEX",
        "con": "CON", "constitution": "CON", "vigor": "CON",
        "int": "INT", "intelligence": "INT", "intellect": "INT",
        "wis": "WIS", "wisdom": "WIS",
        "cha": "CHA", "charisma": "CHA", "presence": "CHA",
    }
    return mapping.get(low)


def extract_ability_scores(char_data: Dict[str, Any]) -> Dict[str, int]:
    """Extracts canonical {STR, DEX, CON, INT, WIS, CHA} integers from char_data."""
    attrs = char_data.get("attributes") or {}
    native = char_data.get("native_system_data") if isinstance(char_data.get("native_system_data"), dict) else {}
    n_scores = native.get("ability_scores") or {}

    scores: Dict[str, int] = {}
    for ab in ABILITY_ORDER:
        found = None
        for k in ABILITY_ALIASES[ab]:
            if k in n_scores and n_scores[k] is not None:
                found = _safe_int(n_scores[k], 10)
                break
            if k in attrs and attrs[k] is not None:
                found = _safe_int(attrs[k], 10)
                break
        if found is None and ab in ("WIS", "CHA") and "Presence" in attrs:
            found = _safe_int(attrs["Presence"], 10)
        scores[ab] = found if found is not None else 10
    return scores


def write_ability_scores(char_data: Dict[str, Any], scores: Dict[str, int]) -> None:
    """Writes synchronized ability scores back to attributes, native_system_data, and universal_core."""
    attrs = char_data.setdefault("attributes", {})
    native = char_data.get("native_system_data") if isinstance(char_data.get("native_system_data"), dict) else None
    u_core = char_data.get("universal_core") if isinstance(char_data.get("universal_core"), dict) else None

    clean_scores = {ab: _safe_int(scores.get(ab, 10), 10) for ab in ABILITY_ORDER}

    # Always write full D&D names if D&D keys or schema exist; preserve short & universal keys if present
    is_dnd_schema = (
        any(ABILITY_FULL_NAMES[ab] in attrs for ab in ABILITY_ORDER)
        or "dnd" in str(char_data.get("ruleset") or "").lower()
        or "heroic" in str(char_data.get("ruleset") or "").lower()
        or native is not None
    )

    for ab, val in clean_scores.items():
        full_nm = ABILITY_FULL_NAMES[ab]
        if is_dnd_schema or full_nm in attrs:
            attrs[full_nm] = val
        if ab in attrs:
            attrs[ab] = val

    # Maintain universal/simplified attributes cleanly if present or used by UI
    if "Might" in attrs or is_dnd_schema:
        attrs["Might"] = clean_scores["STR"]
    if "Agility" in attrs or is_dnd_schema:
        attrs["Agility"] = clean_scores["DEX"]
    if "Vigor" in attrs or is_dnd_schema:
        attrs["Vigor"] = clean_scores["CON"]
    if "Intellect" in attrs or is_dnd_schema:
        attrs["Intellect"] = clean_scores["INT"]
    if "Presence" in attrs or is_dnd_schema:
        attrs["Presence"] = max(clean_scores["WIS"], clean_scores["CHA"])

    if native is not None:
        n_scores = native.setdefault("ability_scores", {})
        for ab, val in clean_scores.items():
            n_scores[ab] = val
            full_nm = ABILITY_FULL_NAMES[ab]
            if full_nm in n_scores:
                n_scores[full_nm] = val

    if u_core is not None:
        norm = u_core.setdefault("normalized_attributes", {})
        norm["might"] = min(100, max(10, int((clean_scores["STR"] / 20.0) * 100)))
        norm["agility"] = min(100, max(10, int((clean_scores["DEX"] / 20.0) * 100)))
        norm["vigor"] = min(100, max(10, int((clean_scores["CON"] / 20.0) * 100)))
        norm["intellect"] = min(100, max(10, int((clean_scores["INT"] / 20.0) * 100)))
        norm["presence"] = min(100, max(10, int((max(clean_scores["WIS"], clean_scores["CHA"]) / 20.0) * 100)))


def parse_character_classes(char_data: Dict[str, Any]) -> Tuple[Dict[str, int], Dict[str, str]]:
    """
    Returns (class_levels_dict, subclasses_dict) preserving order.
    Example: ({'Paladin': 6, 'Sorcerer': 4}, {'Paladin': 'Oath of Devotion'})
    """
    native = char_data.get("native_system_data") if isinstance(char_data.get("native_system_data"), dict) else {}
    total_lvl = max(1, _safe_int(char_data.get("level"), 1))

    # Extract existing subclasses dict if present
    subclasses: Dict[str, str] = {}
    if isinstance(char_data.get("subclasses"), dict):
        for k, v in char_data["subclasses"].items():
            if k and v:
                subclasses[str(k).strip().title()] = str(v).strip()

    # Check structured class_levels first
    raw_map = char_data.get("class_levels") or native.get("class_levels")
    if isinstance(raw_map, dict) and raw_map:
        cleaned: Dict[str, int] = {}
        for k, v in raw_map.items():
            cdef = get_class_definition(str(k))
            cname = cdef["name"] if cdef else str(k).strip().title()
            lvl = max(1, _safe_int(v, 1))
            cleaned[cname] = cleaned.get(cname, 0) + lvl
        if cleaned:
            # Attach single subclass if stored at root
            single_sub = str(char_data.get("subclass") or "").strip()
            if single_sub and not subclasses:
                first_cls = next(iter(cleaned.keys()))
                subclasses[first_cls] = single_sub
            return cleaned, subclasses

    raw_str = str(
        char_data.get("class_and_level")
        or native.get("class_level")
        or char_data.get("archetype")
        or char_data.get("class")
        or ""
    ).strip()

    if not raw_str or raw_str.lower() in ("adventurer", "hero", "unknown"):
        raw_str = f"Fighter {total_lvl}"

    # Look for embedded known third-caster or subclass names inside parentheticals
    for parens in re.findall(r"\(([^)]+)\)", raw_str):
        p_low = parens.lower()
        if "eldritch knight" in p_low:
            subclasses.setdefault("Fighter", "Eldritch Knight")
        elif "arcane trickster" in p_low:
            subclasses.setdefault("Rogue", "Arcane Trickster")

    clean_str = re.sub(r"[\(\[].*?[\)\]]", "", raw_str).strip()
    segments = [s.strip() for s in re.split(r"[/,|]+", clean_str) if s.strip()]

    parsed: Dict[str, int] = {}
    srd_names = [c["name"] for c in _load_srd_classes_data().get("classes", {}).values()]

    for seg in segments:
        matched_cls = None
        for cname in srd_names:
            if re.search(rf"\b{re.escape(cname)}\b", seg, flags=re.IGNORECASE):
                matched_cls = cname
                break
        if matched_cls:
            m_lvl = re.search(rf"\b{re.escape(matched_cls)}\s*(\d+)", seg, flags=re.IGNORECASE)
            if not m_lvl:
                m_lvl = re.search(r"(\d+)", seg)
            c_lvl = int(m_lvl.group(1)) if m_lvl else (total_lvl if len(segments) == 1 else 1)
            parsed[matched_cls] = parsed.get(matched_cls, 0) + max(1, c_lvl)
        else:
            m = re.match(r"([A-Za-z][A-Za-z\s'-]{1,30}?)(?:\s+(\d+))?$", seg)
            if m:
                c_name = m.group(1).strip().title()
                if c_name.lower() in ("level", "lv", "lvl"):
                    continue
                c_lvl = int(m.group(2)) if m.group(2) else (total_lvl if len(segments) == 1 else 1)
                parsed[c_name] = parsed.get(c_name, 0) + max(1, c_lvl)

    if not parsed:
        parsed = {"Fighter": total_lvl}

    # Check single subclass fallback at root/features if still empty
    single_sub = str(char_data.get("subclass") or "").strip()
    if single_sub and not subclasses:
        first_cls = next(iter(parsed.keys()))
        subclasses[first_cls] = single_sub
    else:
        feats_txt = " ".join(str(x) for x in (char_data.get("features_and_traits") or [])).lower()
        if "eldritch knight" in (raw_str.lower() + " " + feats_txt) and "Fighter" in parsed:
            subclasses.setdefault("Fighter", "Eldritch Knight")
        if "arcane trickster" in (raw_str.lower() + " " + feats_txt) and "Rogue" in parsed:
            subclasses.setdefault("Rogue", "Arcane Trickster")

    return parsed, subclasses


def format_class_summary(class_levels: Dict[str, int]) -> str:
    if not class_levels:
        return "Adventurer 1"
    return " / ".join(f"{cls} {lvl}" for cls, lvl in class_levels.items())


def check_multiclass_prerequisites(
    scores: Dict[str, int],
    target_class: str,
    current_classes: Dict[str, int],
    bypass: bool = False,
) -> Dict[str, Any]:
    """
    Validates D&D 5e RAW multiclass prerequisites:
    Must meet prerequisites for ALL current classes (exit) AND the new class (entry).
    Advancing an existing class in current_classes has no multiclass gate.
    """
    cdef = get_class_definition(target_class)
    canon_target = cdef["name"] if cdef else target_class.strip().title()

    if canon_target in current_classes:
        return {
            "eligible": True,
            "is_multiclass_entry": False,
            "missing": [],
            "bypassed": False,
            "target_class": canon_target,
        }

    classes_to_verify = list(current_classes.keys()) + [canon_target]
    missing: List[str] = []

    for idx, cls_name in enumerate(classes_to_verify):
        def_obj = get_class_definition(cls_name)
        if not def_obj:
            continue
        role = "New Class Entry" if idx == len(classes_to_verify) - 1 else "Current Class Exit"
        prereqs = def_obj.get("multiclass_prereqs") or {}
        if "all" in prereqs:
            for cond in prereqs["all"]:
                for ab, min_v in cond.items():
                    has_v = scores.get(ab, 10)
                    if has_v < min_v:
                        missing.append(f"{cls_name} ({role}) requires {ab} {min_v}+ (has {has_v})")
        if "any" in prereqs:
            any_opts = prereqs["any"]
            satisfied = any(
                all(scores.get(ab, 10) >= min_v for ab, min_v in cond.items())
                for cond in any_opts
            )
            if not satisfied:
                opt_txt = " or ".join(
                    " & ".join(f"{ab} {mv}+" for ab, mv in cond.items())
                    for cond in any_opts
                )
                missing.append(f"{cls_name} ({role}) requires {opt_txt}")

    return {
        "eligible": bool(bypass or len(missing) == 0),
        "is_multiclass_entry": True,
        "missing": missing,
        "bypassed": bool(bypass and len(missing) > 0),
        "target_class": canon_target,
    }


def _caster_kind_for_class(cls_name: str, cls_lvl: int, subclasses: Optional[Dict[str, str]] = None) -> str:
    subs = subclasses or {}
    cdef = get_class_definition(cls_name)
    if cdef:
        prog = cdef.get("caster_progression", "none")
        if prog != "none":
            return prog
    sub_low = str(subs.get(cls_name, "")).lower()
    if cls_name == "Fighter" and ("eldritch knight" in sub_low) and cls_lvl >= 3:
        return "third"
    if cls_name == "Rogue" and ("arcane trickster" in sub_low) and cls_lvl >= 3:
        return "third"
    return "none"


def compute_multiclass_spell_slots(
    class_levels: Dict[str, int],
    edition: str = "2024",
    subclasses: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Computes D&D 5e spell slots cleanly across single-class and multiclass builds:
    - Full Casters (Bard, Cleric, Druid, Sorcerer, Wizard): +lvl
    - Half Casters:
      * Single-class Paladin/Ranger: 0 at L1 in 2014 (or 2 slots at L1 in 2024), HALF_CASTER_SINGLE_TABLE thereafter
      * Multiclass Paladin/Ranger: floor(lvl/2) in 2014 vs ceil(lvl/2) in 2024
      * Artificer: always ceil(lvl/2)
    - Third Casters (Eldritch Knight / Arcane Trickster at L3+): single-class table or floor(lvl/3) in multiclass
    - Warlock Pact Magic: tracked independently from Spellcasting table + Mystic Arcanum L11/13/15/17!
    """
    ed = "2014" if str(edition).strip() == "2014" else "2024"
    subs = subclasses or {}

    warlock_lvl = max(0, int(class_levels.get("Warlock", 0)))
    slot_casters: List[Tuple[str, int, str]] = []
    for cname, lvl in class_levels.items():
        if cname == "Warlock":
            continue
        kind = _caster_kind_for_class(cname, lvl, subs)
        if kind != "none":
            slot_casters.append((cname, lvl, kind))

    slots: Dict[str, Dict[str, Any]] = {
        f"level_{r}": {"current": 0, "max": 0, "reset_on": "long_rest"}
        for r in range(1, 10)
    }

    ecl = 0
    if len(slot_casters) == 1:
        cname, clvl, kind = slot_casters[0]
        if kind == "full":
            ecl = min(20, clvl)
        elif kind in ("half_up", "half_floor_2014_ceil_2024"):
            if kind == "half_floor_2014_ceil_2024" and ed == "2014" and clvl == 1:
                ecl = 0
            else:
                table_arr = HALF_CASTER_SINGLE_TABLE.get(min(20, clvl), [0] * 5)
                for rank_idx, count in enumerate(table_arr, start=1):
                    slots[f"level_{rank_idx}"] = {"current": count, "max": count, "reset_on": "long_rest"}
                ecl = -1  # already populated directly
        elif kind == "third":
            ecl = THIRD_CASTER_SINGLE_ECL.get(min(20, clvl), 0)
    elif len(slot_casters) >= 2:
        for cname, clvl, kind in slot_casters:
            if kind == "full":
                ecl += clvl
            elif kind == "half_up":
                ecl += (clvl + 1) // 2
            elif kind == "half_floor_2014_ceil_2024":
                ecl += ((clvl + 1) // 2) if ed == "2024" else (clvl // 2)
            elif kind == "third":
                ecl += clvl // 3

    if ecl > 0:
        row = MULTICLASS_SLOT_TABLE.get(min(20, max(1, ecl)), {})
        for rank, count in row.items():
            slots[f"level_{rank}"] = {"current": count, "max": count, "reset_on": "long_rest"}

    has_standard_slots = any(v["max"] > 0 for v in slots.values())
    pact_info = None
    arcanum_meters: Dict[str, Any] = {}
    if warlock_lvl > 0:
        pdata = WARLOCK_PACT_SLOTS.get(min(20, warlock_lvl), {"count": 1, "level": 1})
        p_rank = pdata["level"]
        p_count = pdata["count"]
        pact_info = {"count": p_count, "level": p_rank}
        if not has_standard_slots:
            slots[f"level_{p_rank}"] = {
                "current": p_count,
                "max": p_count,
                "is_pact_magic": True,
                "reset_on": "short_rest",
            }
        else:
            slots[f"level_{p_rank}"]["pact_max"] = p_count
            slots[f"level_{p_rank}"]["pact_current"] = p_count
        arcanum_meters = extract_mystic_arcanum_meters(warlock_lvl)

    highest_standard_rank = max((r for r in range(1, 10) if slots[f"level_{r}"]["max"] > 0), default=0)
    highest_slot_rank = max(highest_standard_rank, pact_info["level"] if pact_info else 0)

    return {
        "spell_slots": slots,
        "effective_caster_level": max(0, ecl),
        "has_standard_slots": has_standard_slots,
        "has_pact_magic": warlock_lvl > 0,
        "only_pact_magic": (warlock_lvl > 0 and not has_standard_slots),
        "pact_slots": pact_info,
        "mystic_arcanum": arcanum_meters,
        "highest_slot_level": highest_slot_rank,
    }


def _class_spellcasting_profile(
    class_levels: Dict[str, int],
    scores: Dict[str, int],
    edition: str = "2024",
    subclasses: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    ed = "2014" if str(edition).strip() == "2014" else "2024"
    subs = subclasses or {}
    best_dc = 0
    best_atk = -99
    primary_ability = ""
    total_prepared_cap = 0
    any_caster = False

    for cname, clvl in class_levels.items():
        cdef = get_class_definition(cname)
        kind = _caster_kind_for_class(cname, clvl, subs)
        if kind == "none":
            continue
        if kind == "half_floor_2014_ceil_2024" and ed == "2014" and clvl < 2:
            continue
        any_caster = True
        ab = (cdef.get("spellcasting_ability") if cdef else None) or "INT"
        mod = (scores.get(ab, 10) - 10) // 2
        if mod >= best_atk or not primary_ability:
            best_atk = mod
            primary_ability = ab

        # Individual class prepared / known cap
        low = cname.lower()
        if ed == "2024" and cdef and f"spells_cap_{ed}" in cdef:
            cap_list = cdef[f"spells_cap_{ed}"]
            total_prepared_cap += cap_list[min(len(cap_list), clvl) - 1]
        elif ed == "2014" and cdef and "spells_cap_2014" in cdef:
            cap_list = cdef["spells_cap_2014"]
            total_prepared_cap += cap_list[min(len(cap_list), clvl) - 1]
        elif low in ("cleric", "druid", "wizard"):
            total_prepared_cap += max(1, mod + clvl)
        elif low in ("paladin", "ranger", "artificer"):
            total_prepared_cap += max(1, mod + max(1, clvl // 2))
        elif kind == "third":
            total_prepared_cap += max(2, clvl // 2 + 1)
        elif cdef and "spells_cap_2024" in cdef:
            cap_list = cdef["spells_cap_2024"]
            total_prepared_cap += cap_list[min(len(cap_list), clvl) - 1]
        else:
            total_prepared_cap += max(1, mod + clvl)

    total_lvl = sum(class_levels.values())
    prof = proficiency_bonus_for_level(total_lvl)
    mod_val = best_atk if primary_ability else 0
    best_dc = (8 + prof + mod_val) if any_caster else 0
    best_atk_bonus = (prof + mod_val) if any_caster else 0

    return {
        "is_caster": any_caster,
        "spellcasting_ability": primary_ability,
        "spell_save_dc": best_dc,
        "spell_attack_bonus": best_atk_bonus,
        "max_prepared_leveled_spells": total_prepared_cap if any_caster else 0,
    }


def _max_learnable_spell_level_for_class(
    cls_name: str,
    next_cls_lvl: int,
    edition: str = "2024",
    subclass: str = "",
) -> int:
    ed = "2014" if str(edition).strip() == "2014" else "2024"
    cdef = get_class_definition(cls_name)
    prog = cdef.get("caster_progression", "none") if cdef else "none"
    if prog == "none":
        sub_low = (subclass or "").lower()
        if (cls_name == "Fighter" and "eldritch knight" in sub_low) or (cls_name == "Rogue" and "arcane trickster" in sub_low):
            if next_cls_lvl < 3:
                return 0
            return min(4, 1 + (next_cls_lvl - 1) // 6)
        return 0
    if prog == "full":
        return min(9, (next_cls_lvl + 1) // 2)
    if prog == "pact":
        return min(9, WARLOCK_PACT_SLOTS.get(min(20, next_cls_lvl), {"level": 1})["level"] if next_cls_lvl < 11 else (6 + (next_cls_lvl - 11) // 2))
    if prog in ("half_up", "half_floor_2014_ceil_2024"):
        if prog == "half_floor_2014_ceil_2024" and ed == "2014" and next_cls_lvl < 2:
            return 0
        return min(5, max(1, (next_cls_lvl + 3) // 4))
    return 0


def _compute_class_meters(
    class_levels: Dict[str, int],
    scores: Dict[str, int],
    edition: str = "2024",
    existing_meters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ed = "2014" if str(edition).strip() == "2014" else "2024"
    meters = copy.deepcopy(existing_meters) if isinstance(existing_meters, dict) else {}

    total_lvl = sum(class_levels.values())
    int_mod = (scores.get("INT", 10) - 10) // 2
    cha_mod = (scores.get("CHA", 10) - 10) // 2

    # 1. Hit Dice Meters
    hd_parts: List[str] = []
    if len(class_levels) == 1:
        only_cls, only_lvl = next(iter(class_levels.items()))
        cdef = get_class_definition(only_cls)
        hd_face = cdef["hit_die"] if cdef else 8
        hd_parts.append(f"{only_lvl}d{hd_face}")
        prev_hd = meters.get("hit_dice") if isinstance(meters.get("hit_dice"), dict) else {}
        prev_max = _safe_int(prev_hd.get("max"), max(1, only_lvl - 1))
        prev_cur = _safe_int(prev_hd.get("current"), prev_max)
        gained_dice = max(0, only_lvl - prev_max)
        meters["hit_dice"] = {
            "label": f"Hit Dice ({only_lvl}d{hd_face})",
            "current": min(only_lvl, prev_cur + gained_dice),
            "max": only_lvl,
            "reset_on": "long_rest",
        }
    else:
        total_cur_hd = 0
        for cname, clvl in class_levels.items():
            cdef = get_class_definition(cname)
            hd_face = cdef["hit_die"] if cdef else 8
            hd_parts.append(f"{clvl}d{hd_face}")
            pkey = f"hit_dice_{cname.lower().replace(' ', '_')}"
            prev_seg = meters.get(pkey) if isinstance(meters.get(pkey), dict) else {}
            prev_max = _safe_int(prev_seg.get("max"), max(0, clvl - 1))
            prev_cur = _safe_int(prev_seg.get("current"), prev_max)
            gained = max(0, clvl - prev_max)
            new_cur = min(clvl, prev_cur + gained)
            total_cur_hd += new_cur
            meters[pkey] = {
                "label": f"Hit Dice — {cname} ({clvl}d{hd_face})",
                "current": new_cur,
                "max": clvl,
                "reset_on": "long_rest",
            }
        meters["hit_dice"] = {
            "label": f"Hit Dice ({', '.join(hd_parts)})",
            "current": min(total_lvl, total_cur_hd),
            "max": total_lvl,
            "reset_on": "long_rest",
        }

    # 2. Class Resource Pools
    for cname, clvl in class_levels.items():
        if cname == "Barbarian":
            rages = 2 if clvl < 3 else (3 if clvl < 6 else (4 if clvl < 12 else (5 if clvl < 17 else (6 if clvl < 20 else 99))))
            meters["rage"] = {"label": "Rage" if ed == "2014" else "Rage (2024: +1/SR)", "current": rages, "max": rages, "reset_on": "long_rest"}
        elif cname == "Bard":
            uses = max(1, cha_mod)
            reset = "short_rest" if clvl >= 5 else "long_rest"
            die = "d6" if clvl < 5 else ("d8" if clvl < 10 else ("d10" if clvl < 15 else "d12"))
            meters["bardic_inspiration"] = {"label": f"Bardic Inspiration ({die})", "current": uses, "max": uses, "reset_on": reset}
        elif cname == "Cleric" and clvl >= 2:
            if ed == "2024":
                cd = 2 if clvl < 6 else (3 if clvl < 18 else 4)
            else:
                cd = 1 if clvl < 6 else (2 if clvl < 18 else 3)
            meters["channel_divinity"] = {"label": "Channel Divinity", "current": cd, "max": cd, "reset_on": "short_rest"}
        elif cname == "Druid" and clvl >= 2:
            ws = 2 if (ed == "2014" or clvl < 6) else (3 if clvl < 17 else 4)
            meters["wild_shape"] = {"label": "Wild Shape", "current": ws, "max": ws, "reset_on": "short_rest"}
        elif cname == "Fighter":
            sw = 1 if ed == "2014" else (2 if clvl < 4 else (3 if clvl < 10 else 4))
            meters["second_wind"] = {"label": "Second Wind", "current": sw, "max": sw, "reset_on": "short_rest"}
            if clvl >= 2:
                surge = 2 if clvl >= 17 else 1
                meters["action_surge"] = {"label": "Action Surge", "current": surge, "max": surge, "reset_on": "short_rest"}
            if clvl >= 9:
                ind = 1 if clvl < 13 else (2 if clvl < 17 else 3)
                meters["indomitable"] = {"label": "Indomitable", "current": ind, "max": ind, "reset_on": "long_rest"}
        elif cname == "Monk" and clvl >= 2:
            lbl = "Focus Points" if ed == "2024" else "Ki Points"
            meters["ki_points"] = {"label": lbl, "current": clvl, "max": clvl, "reset_on": "short_rest"}
        elif cname == "Paladin":
            meters["lay_on_hands"] = {"label": f"Lay on Hands ({clvl * 5} HP)", "current": clvl * 5, "max": clvl * 5, "reset_on": "long_rest"}
            if clvl >= 3:
                cd_p = 1 if ed == "2014" else (2 if clvl < 11 else 3)
                meters["channel_divinity"] = {"label": "Channel Divinity", "current": cd_p, "max": cd_p, "reset_on": "short_rest"}
        elif cname == "Ranger" and ed == "2024":
            fe = 2 if clvl < 5 else (3 if clvl < 9 else (4 if clvl < 13 else (5 if clvl < 17 else 6)))
            meters["favored_enemy"] = {"label": "Favored Enemy (Free Hunter's Mark)", "current": fe, "max": fe, "reset_on": "long_rest"}
        elif cname == "Sorcerer":
            if ed == "2024":
                meters["innate_sorcery"] = {"label": "Innate Sorcery", "current": 2, "max": 2, "reset_on": "long_rest"}
            if clvl >= 2:
                meters["sorcery_points"] = {"label": "Sorcery Points", "current": clvl, "max": clvl, "reset_on": "long_rest"}
        elif cname == "Warlock":
            if ed == "2024" and clvl >= 2:
                meters["magical_cunning"] = {"label": "Magical Cunning", "current": 1, "max": 1, "reset_on": "long_rest"}
            for mk, mv in extract_mystic_arcanum_meters(clvl).items():
                meters[mk] = mv
        elif cname == "Wizard":
            meters["arcane_recovery"] = {"label": "Arcane Recovery", "current": 1, "max": 1, "reset_on": "long_rest"}
        elif cname == "Artificer" and clvl >= 7:
            fg = max(1, int_mod)
            meters["flash_of_genius"] = {"label": "Flash of Genius", "current": fg, "max": fg, "reset_on": "long_rest"}

    # Multiclass Warlock Pact Magic meter when coexisting with Spellcasting slots
    slot_data = compute_multiclass_spell_slots(class_levels, edition=ed)
    if slot_data["has_pact_magic"] and slot_data["has_standard_slots"] and slot_data["pact_slots"]:
        ps = slot_data["pact_slots"]
        meters["pact_magic_slots"] = {
            "label": f"Pact Magic (Level {ps['level']} Slots)",
            "current": ps["count"],
            "max": ps["count"],
            "reset_on": "short_rest",
        }
    else:
        meters.pop("pact_magic_slots", None)

    return meters


def get_advancement_options(
    char_data: Dict[str, Any],
    edition: str = "2024",
    target_class: Optional[str] = None,
    bypass_prereqs: bool = False,
) -> Dict[str, Any]:
    """
    Computes complete UI/tool preview options for the character's next level-up:
    - Current class split, total level, XP progress
    - Eligibility table across all 13 D&D classes (advance existing vs multiclass entry)
    - Fixed HP preview, subclass unlock prompt, ASI / Epic Boon detection
    - Next Spell Slots preview and learnable SRD/Homebrew spells filtered for target_class
    """
    ed = "2014" if str(edition).strip() == "2014" else "2024"
    scores = extract_ability_scores(char_data)
    class_levels, subclasses = parse_character_classes(char_data)
    total_level = sum(class_levels.values())
    xp = max(0, _safe_int(char_data.get("experience_points"), 0))
    next_total_level = min(20, total_level + 1)
    can_advance = total_level < 20

    primary_class = next(iter(class_levels.keys()), "Fighter")
    selected_cls_raw = (target_class or primary_class).strip()
    cdef = get_class_definition(selected_cls_raw)
    selected_cls = cdef["name"] if cdef else selected_cls_raw.title()

    con_mod = (scores["CON"] - 10) // 2
    old_class_lvl = class_levels.get(selected_cls, 0)
    next_class_lvl = old_class_lvl + 1

    # Class option cards
    class_options = []
    for cls_obj in list_classes(edition=ed):
        cname = cls_obj["name"]
        cur_lvl = class_levels.get(cname, 0)
        prereq = check_multiclass_prerequisites(scores, cname, class_levels, bypass=bypass_prereqs)
        hit_die = cls_obj["hit_die"]
        fixed_hp_level = max(1, cls_obj["fixed_hp"] + con_mod)
        sub_lvl = cls_obj.get(f"subclass_level_{ed}", 3)
        will_unlock_sub = ((cur_lvl + 1) >= sub_lvl) and not subclasses.get(cname)
        will_asi = (cur_lvl + 1) in cls_obj.get("asi_levels", [4, 8, 12, 16, 19])
        class_options.append({
            "name": cname,
            "current_class_level": cur_lvl,
            "next_class_level": cur_lvl + 1,
            "is_existing_class": cur_lvl > 0,
            "hit_die": hit_die,
            "fixed_hp_gain": fixed_hp_level,
            "eligible": prereq["eligible"],
            "missing_prereqs": prereq["missing"],
            "bypassed": prereq["bypassed"],
            "multiclass_proficiencies": cls_obj.get("multiclass_proficiencies_gained", "") if cur_lvl == 0 else "",
            "needs_subclass": will_unlock_sub,
            "subclass_title": cls_obj.get("subclass_title", "Subclass"),
            "subclasses": cls_obj.get("subclasses", []),
            "is_asi_level": will_asi,
            "is_epic_boon_level": (ed == "2024" and next_total_level >= 19 and will_asi),
        })

    sel_def = get_class_definition(selected_cls) or {"hit_die": 8, "fixed_hp": 5, "asi_levels": [4, 8, 12, 16, 19], "subclasses": [], "subclass_title": "Subclass"}
    sel_sub_lvl = sel_def.get(f"subclass_level_{ed}", 3)
    needs_subclass = (next_class_lvl >= sel_sub_lvl) and not subclasses.get(selected_cls)
    is_asi = next_class_lvl in sel_def.get("asi_levels", [4, 8, 12, 16, 19])
    is_epic_boon = (ed == "2024" and next_total_level >= 19 and is_asi)

    # Preview next class split & slots
    preview_classes = dict(class_levels)
    preview_classes[selected_cls] = next_class_lvl
    current_slots = compute_multiclass_spell_slots(class_levels, edition=ed, subclasses=subclasses)
    preview_slots = compute_multiclass_spell_slots(preview_classes, edition=ed, subclasses=subclasses)

    sel_prereq = check_multiclass_prerequisites(scores, selected_cls, class_levels, bypass=bypass_prereqs)

    # Features unlocked at next_class_lvl
    feat_map = sel_def.get(f"features_{ed}") or sel_def.get("features_2024") or {}
    gained_features = list(feat_map.get(str(next_class_lvl), []))

    # Spells available for target_class
    sel_subclass_name = subclasses.get(selected_cls, "")
    max_learn_lvl = _max_learnable_spell_level_for_class(selected_cls, next_class_lvl, edition=ed, subclass=sel_subclass_name)
    existing_spell_names = {
        (sp.get("name") if isinstance(sp, dict) else str(sp)).strip().lower()
        for sp in (char_data.get("spells") or [])
        if (sp.get("name") if isinstance(sp, dict) else str(sp)).strip()
    }
    learnable_spells: List[Dict[str, Any]] = []
    if max_learn_lvl > 0 or (sel_def.get("cantrips_known") and next_class_lvl >= 1):
        spell_cls_query = "Wizard" if (selected_cls in ("Fighter", "Rogue") and max_learn_lvl > 0) else selected_cls
        if spell_cls_query in ("Bard", "Cleric", "Druid", "Paladin", "Ranger", "Sorcerer", "Warlock", "Wizard", "Artificer"):
            raw_list = get_spells_for_class(
                class_name=spell_cls_query,
                max_spell_level=max(0, max_learn_lvl),
                edition=ed,
                include_cantrips=True,
            )
            for sp in raw_list:
                learnable_spells.append({
                    "name": sp["name"],
                    "index": sp.get("index", ""),
                    "level": int(sp.get("level", 0)),
                    "level_label": sp.get("level_label", "Cantrip"),
                    "school": sp.get("school", ""),
                    "already_on_sheet": sp["name"].strip().lower() in existing_spell_names,
                    "description": str(sp.get("description_2024") if ed == "2024" and sp.get("description_2024") else sp.get("description", ""))[:220],
                })

    next_xp_req = XP_THRESHOLDS.get(min(20, total_level + 1), 355000)

    return {
        "status": "success",
        "character_id": char_data.get("id", ""),
        "character_name": char_data.get("name", "Adventurer"),
        "edition": ed,
        "can_advance": can_advance,
        "current_total_level": total_level,
        "next_total_level": next_total_level,
        "current_class_summary": format_class_summary(class_levels),
        "next_class_summary": format_class_summary(preview_classes),
        "class_levels": class_levels,
        "subclasses": subclasses,
        "experience_points": xp,
        "next_level_xp": next_xp_req,
        "xp_ready": xp >= next_xp_req if can_advance else False,
        "ability_scores": scores,
        "con_mod": con_mod,
        "old_proficiency_bonus": proficiency_bonus_for_level(total_level),
        "new_proficiency_bonus": proficiency_bonus_for_level(next_total_level),
        "selected_class": selected_cls,
        "selected_class_current_level": old_class_lvl,
        "selected_class_next_level": next_class_lvl,
        "selected_hit_die": sel_def.get("hit_die", 8),
        "selected_fixed_hp_gain": max(1, sel_def.get("fixed_hp", 5) + con_mod),
        "selected_prereq": sel_prereq,
        "selected_multiclass_proficiencies": sel_def.get("multiclass_proficiencies_gained", "") if old_class_lvl == 0 else "",
        "needs_subclass": needs_subclass,
        "subclass_title": sel_def.get("subclass_title", "Subclass"),
        "subclass_options": sel_def.get("subclasses", []),
        "is_asi_level": is_asi,
        "is_epic_boon_level": is_epic_boon,
        "feats_available": list_feats(edition=ed, level=next_total_level),
        "gained_features": gained_features,
        "current_spell_slots": current_slots["spell_slots"],
        "next_spell_slots": preview_slots["spell_slots"],
        "next_pact_slots": preview_slots["pact_slots"],
        "max_learnable_spell_level": max_learn_lvl,
        "learnable_spells": learnable_spells,
        "class_options": class_options,
    }


def _recompute_saves_and_skills(
    char_data: Dict[str, Any],
    old_scores: Dict[str, int],
    new_scores: Dict[str, int],
    old_total_level: int,
    new_total_level: int,
    extra_save_profs: Optional[List[str]] = None,
) -> None:
    old_prof = proficiency_bonus_for_level(old_total_level)
    new_prof = proficiency_bonus_for_level(new_total_level)
    old_mods = {ab: (old_scores[ab] - 10) // 2 for ab in ABILITY_ORDER}
    mods = {ab: (new_scores[ab] - 10) // 2 for ab in ABILITY_ORDER}
    extra_saves = {normalize_ability_key(s) for s in (extra_save_profs or []) if normalize_ability_key(s)}

    # Saving throws update
    saves = char_data.setdefault("saving_throws", {})
    native = char_data.get("native_system_data") if isinstance(char_data.get("native_system_data"), dict) else None
    n_saves = native.setdefault("saving_throws", {}) if native is not None else {}

    # Detect existing proficiencies against PRIOR ability mod + old prof bonus
    for ab in ABILITY_ORDER:
        full = ABILITY_FULL_NAMES[ab]
        raw_v = saves.get(full) if full in saves else (saves.get(ab) if ab in saves else n_saves.get(full, n_saves.get(ab)))
        is_prof = ab in extra_saves
        if isinstance(raw_v, dict):
            is_prof = is_prof or bool(raw_v.get("proficient"))
        elif raw_v is not None:
            s_txt = str(raw_v).strip().lower()
            if "prof" in s_txt or s_txt == "true":
                is_prof = True
            else:
                num = _safe_int(raw_v, old_mods[ab])
                if num >= old_mods[ab] + old_prof:
                    is_prof = True
        new_save_val = mods[ab] + (new_prof if is_prof else 0)
        formatted = f"+{new_save_val}" if new_save_val >= 0 else str(new_save_val)
        if full in saves or isinstance(saves, dict):
            saves[full] = formatted
        if ab in saves:
            saves[ab] = formatted
        if native is not None:
            n_saves[full] = formatted

    # Passive Perception update if present
    passives = char_data.get("passives")
    if isinstance(passives, dict):
        perc_skill = (char_data.get("skills") or {}).get("Perception")
        is_perc_prof = False
        if isinstance(perc_skill, dict):
            is_perc_prof = bool(perc_skill.get("proficient"))
        elif isinstance(perc_skill, str):
            is_perc_prof = ("prof" in perc_skill.lower()) or (_safe_int(perc_skill, old_mods["WIS"]) >= old_mods["WIS"] + old_prof)
        passives["Perception"] = 10 + mods["WIS"] + (new_prof if is_perc_prof else 0)
        passives["Investigation"] = 10 + mods["INT"]
        passives["Insight"] = 10 + mods["WIS"]


def apply_level_up(
    char_data: Dict[str, Any],
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Applies a full D&D 5e (2014 or 2024) level advancement to char_data in-place:
    - Increments target_class (single-class advance OR multiclass entry)
    - Checks multiclass prerequisites (unless bypass_prereqs=True)
    - Applies Ability Score Increases / Half-Feat / Epic Boon (+1/+2 up to cap 20 or 30)
    - Calculates Hit Die HP gain ('fixed', 'rolled', 'manual_total') + retroactive CON mod HP + Tough/Fortitude HP
    - Updates Proficiency Bonus, Hit Dice pool, Class Resource Meters, Spell Slots & Pact Magic, Prepared Caps
    - Grants Subclass name, Class Milestones, Multiclass Proficiencies, and Selected Spells
    """
    data = payload or {}
    ed = "2014" if str(data.get("edition") or char_data.get("ruleset_edition") or "2024").strip() == "2014" else "2024"

    class_levels, subclasses = parse_character_classes(char_data)
    old_total_level = sum(class_levels.values())
    if old_total_level >= 20:
        return {"status": "error", "message": f"{char_data.get('name', 'Character')} is already Level 20!"}

    scores = extract_ability_scores(char_data)
    old_scores = dict(scores)
    old_con_mod = (scores["CON"] - 10) // 2

    primary_cls = next(iter(class_levels.keys()), "Fighter")
    raw_target = str(data.get("target_class") or primary_cls).strip()
    cdef = get_class_definition(raw_target)
    target_cls = cdef["name"] if cdef else raw_target.title()

    bypass_prereqs = bool(data.get("bypass_prereqs", False))
    prereq = check_multiclass_prerequisites(scores, target_cls, class_levels, bypass=bypass_prereqs)
    if not prereq["eligible"]:
        return {
            "status": "error",
            "message": "Multiclass prerequisite not met: " + "; ".join(prereq["missing"]),
            "missing_prereqs": prereq["missing"],
        }

    old_cls_lvl = class_levels.get(target_cls, 0)
    new_cls_lvl = old_cls_lvl + 1
    new_total_level = old_total_level + 1
    class_levels[target_cls] = new_cls_lvl

    # Subclass update
    chosen_subclass = str(data.get("subclass") or "").strip()
    if chosen_subclass:
        subclasses[target_cls] = chosen_subclass

    # ASI / Feat / Epic Boon handling
    sel_def = cdef or {"hit_die": 8, "fixed_hp": 5, "asi_levels": [4, 8, 12, 16, 19]}
    is_asi_lvl = new_cls_lvl in sel_def.get("asi_levels", [4, 8, 12, 16, 19])
    feat_name = str(data.get("feat") or "").strip()
    feat_obj = get_feat(feat_name) if feat_name else None
    is_epic_boon = bool((feat_obj and feat_obj.get("type") == "epic_boon") or (ed == "2024" and new_total_level >= 19 and is_asi_lvl and feat_obj and feat_obj.get("cap") == 30))
    stat_cap = 30 if is_epic_boon else 20

    raw_asi = data.get("ability_increases") or {}
    applied_asi: Dict[str, int] = {}
    if isinstance(raw_asi, dict):
        for k, v in raw_asi.items():
            ab = normalize_ability_key(str(k))
            inc = _safe_int(v, 0)
            if ab and inc > 0:
                old_s = scores[ab]
                new_s = min(stat_cap, old_s + inc)
                if new_s > old_s:
                    scores[ab] = new_s
                    applied_asi[ab] = new_s - old_s

    extra_saves: List[str] = []
    if feat_obj and feat_obj.get("grants_save_prof"):
        res_ab = data.get("resilient_ability") or (next(iter(applied_asi.keys()), None))
        if res_ab:
            norm_res = normalize_ability_key(str(res_ab))
            if norm_res:
                extra_saves.append(norm_res)
    if target_cls == "Monk" and new_cls_lvl == 14:
        extra_saves.extend(ABILITY_ORDER)
    elif target_cls == "Rogue" and new_cls_lvl == 15:
        extra_saves.append("WIS")
        if ed == "2024":
            extra_saves.append("CHA")

    new_con_mod = (scores["CON"] - 10) // 2
    delta_con_mod = new_con_mod - old_con_mod

    # Hit Points calculation
    hit_die = int(sel_def.get("hit_die", 8))
    hp_mode = str(data.get("hp_mode") or "fixed").strip().lower()
    raw_roll = data.get("hp_roll")

    if hp_mode == "manual_total" and raw_roll is not None:
        level_hp_gain = max(1, _safe_int(raw_roll, 1))
        used_die_val = max(1, level_hp_gain - new_con_mod)
    elif hp_mode == "rolled":
        if raw_roll is not None:
            used_die_val = max(1, min(hit_die, _safe_int(raw_roll, (hit_die // 2) + 1)))
        else:
            used_die_val = random.randint(1, hit_die)
        level_hp_gain = max(1, used_die_val + new_con_mod)
    else:
        used_die_val = (hit_die // 2) + 1
        level_hp_gain = max(1, used_die_val + new_con_mod)

    retro_con_hp = delta_con_mod * old_total_level
    feat_hp_bonus = 0
    existing_feats_list = char_data.get("feats") if isinstance(char_data.get("feats"), list) else []
    has_tough_already = any("tough" == str(f.get("name") if isinstance(f, dict) else f).strip().lower() for f in existing_feats_list)
    if feat_obj:
        feat_hp_bonus += int(feat_obj.get("hp_bonus_flat") or 0)
        per_lvl = int(feat_obj.get("hp_bonus_per_level") or 0)
        if per_lvl > 0:
            feat_hp_bonus += per_lvl * new_total_level
    elif has_tough_already:
        feat_hp_bonus += 2

    total_hp_gain = level_hp_gain + retro_con_hp + feat_hp_bonus

    vitals = char_data.setdefault("vitals", {})
    hp_entry = vitals.setdefault("health", {"current": 10, "max": 10, "label": "Health"})
    old_max_hp = max(1, _safe_int(hp_entry.get("max"), 10))
    old_cur_hp = max(0, _safe_int(hp_entry.get("current"), old_max_hp))
    new_max_hp = max(1, old_max_hp + total_hp_gain)
    new_cur_hp = max(0, min(new_max_hp, old_cur_hp + max(0, total_hp_gain)))
    hp_entry["max"] = new_max_hp
    hp_entry["current"] = new_cur_hp

    native = char_data.setdefault("native_system_data", {})
    if isinstance(native.get("hit_points"), dict):
        native["hit_points"]["max"] = new_max_hp
        native["hit_points"]["current"] = new_cur_hp

    # Write ability scores & recompute saves
    write_ability_scores(char_data, scores)
    _recompute_saves_and_skills(
        char_data,
        old_scores=old_scores,
        new_scores=scores,
        old_total_level=old_total_level,
        new_total_level=new_total_level,
        extra_save_profs=extra_saves,
    )

    # Class Summary, Levels, Edition, Proficiency Bonus
    class_summary = format_class_summary(class_levels)
    new_prof = proficiency_bonus_for_level(new_total_level)
    char_data["level"] = new_total_level
    char_data["class_levels"] = dict(class_levels)
    char_data["subclasses"] = dict(subclasses)
    if subclasses:
        first_c = next(iter(class_levels.keys()))
        char_data["subclass"] = subclasses.get(first_c, next(iter(subclasses.values())))
    char_data["archetype"] = class_summary
    char_data["class_and_level"] = class_summary
    char_data["ruleset_edition"] = ed
    char_data["prof_bonus"] = f"+{new_prof}"
    char_data.setdefault("ruleset", "dnd_5e")

    u_core = char_data.setdefault("universal_core", {})
    u_core["level"] = new_total_level
    u_core["archetype"] = class_summary

    native["class_level"] = class_summary
    native["class_levels"] = dict(class_levels)
    native["proficiency_bonus"] = new_prof

    # Update Hit Dice string & meters
    meters = _compute_class_meters(
        class_levels,
        scores,
        edition=ed,
        existing_meters=char_data.get("custom_meters") or native.get("custom_meters"),
    )
    hd_parts = []
    for cname, clvl in class_levels.items():
        c_obj = get_class_definition(cname)
        hd_parts.append(f"{clvl}d{c_obj['hit_die'] if c_obj else 8}")
    hd_display = ", ".join(hd_parts)
    char_data["hit_dice"] = hd_display
    char_data["custom_meters"] = copy.deepcopy(meters)
    native["custom_meters"] = copy.deepcopy(meters)

    # Multiclass proficiencies if taking Level 1 in a brand new class
    if old_cls_lvl == 0 and sel_def.get("multiclass_proficiencies_gained"):
        mc_prof = str(sel_def["multiclass_proficiencies_gained"]).strip()
        cur_prof_str = str(char_data.get("proficiencies") or "").strip()
        if mc_prof and mc_prof.lower() != "none" and target_cls.lower() not in cur_prof_str.lower():
            add_note = f"Multiclass ({target_cls}): {mc_prof}"
            char_data["proficiencies"] = f"{cur_prof_str}; {add_note}" if cur_prof_str else add_note
            native["proficiencies"] = char_data["proficiencies"]

    # Append unlocked class features, subclass & feat
    feat_map = sel_def.get(f"features_{ed}") or sel_def.get("features_2024") or {}
    gained_features = list(feat_map.get(str(new_cls_lvl), []))
    feats_list = char_data.setdefault("features_and_traits", [])
    existing_f_low = {(str(f.get("name") if isinstance(f, dict) else f)).split(":")[0].strip().lower() for f in feats_list}

    for g_feat in gained_features:
        f_title = f"{target_cls} {new_cls_lvl} — {g_feat}"
        if g_feat.lower() not in existing_f_low and f_title.lower() not in existing_f_low:
            feats_list.append(f_title)
            existing_f_low.add(f_title.lower())
    if chosen_subclass:
        sub_entry = f"{target_cls} Subclass: {chosen_subclass}"
        if sub_entry.lower() not in existing_f_low:
            feats_list.append(sub_entry)
    if feat_obj and feat_name.lower() != "ability score improvement":
        recorded_feat = {"name": feat_obj["name"], "description": feat_obj.get("description", ""), "level_gained": new_total_level}
        char_data.setdefault("feats", []).append(recorded_feat)
        f_str = f"Feat ({feat_obj['name']}): {feat_obj.get('description', '')}"
        if f_str.lower() not in existing_f_low:
            feats_list.append(f_str)

    char_data["traits_and_abilities"] = list(feats_list)
    native["features_and_traits"] = list(feats_list)

    # Recompute Spell Slots & Spellcasting Metrics
    slot_res = compute_multiclass_spell_slots(class_levels, edition=ed, subclasses=subclasses)
    spell_prof = _class_spellcasting_profile(class_levels, scores, edition=ed, subclasses=subclasses)

    char_data["spell_slots"] = copy.deepcopy(slot_res["spell_slots"])
    native["spell_slots"] = copy.deepcopy(slot_res["spell_slots"])
    char_data["is_pact_magic"] = bool(slot_res["only_pact_magic"] or slot_res["has_pact_magic"])
    native["is_pact_magic"] = bool(slot_res["only_pact_magic"] or slot_res["has_pact_magic"])

    if spell_prof["is_caster"]:
        dc_val = spell_prof["spell_save_dc"]
        atk_val = spell_prof["spell_attack_bonus"]
        atk_str = f"+{atk_val}" if atk_val >= 0 else str(atk_val)
        char_data["spellcasting_ability"] = spell_prof["spellcasting_ability"]
        char_data["spell_save_dc"] = str(dc_val)
        char_data["ability_save_dc"] = dc_val
        char_data["spell_attack_bonus"] = atk_str
        char_data["max_prepared_leveled_spells"] = spell_prof["max_prepared_leveled_spells"]

        native["spellcasting_ability"] = spell_prof["spellcasting_ability"]
        native["spell_save_dc"] = str(dc_val)
        native["ability_save_dc"] = dc_val
        native["spell_attack_bonus"] = atk_str
        native["max_prepared_leveled_spells"] = spell_prof["max_prepared_leveled_spells"]
    else:
        dc_val = 0
        atk_str = "+0"

    # Sync existing spells' DC/Atk and append newly learned spells
    cur_spells = list(char_data.get("spells") or native.get("spells") or [])
    seen_spells = set()
    for sp in cur_spells:
        if isinstance(sp, dict):
            sn = (sp.get("name") or "").strip().lower()
            if sn:
                seen_spells.add(sn)
            if spell_prof["is_caster"]:
                ref_sp = get_spell(sp.get("name", ""), edition=ed)
                if ref_sp:
                    if ref_sp.get("dc_type"):
                        sp["atk_bonus"] = f"DC {dc_val} {ref_sp['dc_type']}"
                    elif ref_sp.get("attack_type"):
                        sp["atk_bonus"] = atk_str
        elif isinstance(sp, str) and sp.strip():
            seen_spells.add(sp.strip().lower())

    added_spell_names: List[str] = []
    for sp_in in (data.get("new_spells") or []):
        sp_name = (sp_in.get("name") if isinstance(sp_in, dict) else str(sp_in)).strip()
        if not sp_name or sp_name.lower() in seen_spells:
            continue
        sp_comp = get_spell(sp_name, edition=ed)
        if sp_comp:
            prep_flag = True if (int(sp_comp.get("level", 0)) == 0 or CLASS_PREPARATION_TYPE.get(target_cls.lower(), "known") != "prepared_spellbook") else bool(sp_in.get("prepared", True) if isinstance(sp_in, dict) else True)
            cur_spells.append(format_spell_for_sheet(sp_comp, spell_atk=atk_str, spell_dc=str(dc_val), prepared=prep_flag))
            added_spell_names.append(sp_comp["name"])
            seen_spells.add(sp_comp["name"].lower())
        else:
            cur_spells.append({"name": sp_name, "level": "1st Level", "is_cantrip": False, "prepared": True})
            added_spell_names.append(sp_name)
            seen_spells.add(sp_name.lower())

    char_data["spells"] = cur_spells
    native["spells"] = copy.deepcopy(cur_spells)

    msg_parts = [f"{char_data.get('name', 'Hero')} advanced to {class_summary} (Total Level {new_total_level})"]
    msg_parts.append(f"+{total_hp_gain} Max HP ({new_max_hp} total)")
    if applied_asi:
        msg_parts.append("ASI: " + ", ".join(f"+{v} {k}" for k, v in applied_asi.items()))
    if retro_con_hp:
        msg_parts.append(f"includes +{retro_con_hp} retroactive CON HP")
    if feat_obj and feat_obj["name"] != "Ability Score Improvement":
        msg_parts.append(f"Feat: {feat_obj['name']}")
    if chosen_subclass:
        msg_parts.append(f"Subclass: {chosen_subclass}")
    if added_spell_names:
        msg_parts.append(f"Learned spells: {', '.join(added_spell_names)}")

    return {
        "status": "success",
        "character_name": char_data.get("name", "Adventurer"),
        "character_id": char_data.get("id", ""),
        "old_level": old_total_level,
        "new_level": new_total_level,
        "class_levels": dict(class_levels),
        "class_summary": class_summary,
        "edition": ed,
        "hp_gain": total_hp_gain,
        "retroactive_con_hp": retro_con_hp,
        "max_hp": new_max_hp,
        "prof_bonus": f"+{new_prof}",
        "proficiency_bonus": new_prof,
        "save_dc": dc_val,
        "spell_attack_bonus": atk_str if spell_prof["is_caster"] else "",
        "max_prepared_spells": spell_prof["max_prepared_leveled_spells"],
        "spell_slots": char_data["spell_slots"],
        "gained_features": gained_features,
        "added_spells": added_spell_names,
        "applied_asi": applied_asi,
        "message": " • ".join(msg_parts) + ".",
        "character": char_data,
    }
