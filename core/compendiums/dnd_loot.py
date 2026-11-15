"""
core/compendiums/dnd_loot.py - D&D 5e Tiered Loot Tables, Sheet Proficiency Matching & Quest Reward Engine

Provides:
- D&D 5e (2014 & 2024) sheet weapon/armor proficiency resolution (including multiclassing).
- Strict 5e RAW proficiency checks (no proficiency bonus on non-proficient weapons;
  equipped non-proficient armor/shield detection for STR/DEX disadvantage & spellcasting lock).
- Level-tier scaling guardrails (Levels 1-4, 5-10, 11-16, 17-20).
- Unbiased random world/chest loot generation (`random_chest` mode).
- Class-matched quest loot selection prioritizing highest class level with random tie-breaking
  (`class_reward` mode).
- Tier-bounded creative homebrew weapon/armor/item creation persisted via `HomebrewManager`.
"""
import json
import os
import random
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Set, Tuple

from core.compendiums.dnd_armor import get_armor, list_armor
from core.compendiums.dnd_items import get_item, list_items, format_item_for_sheet
from core.compendiums.dnd_weapons import (
    format_weapon_for_sheet,
    get_weapon,
    list_weapons,
)
from core.compendiums.homebrew_manager import HomebrewManager, get_default_homebrew_manager

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# Canonical D&D 5e Class Starting Proficiencies (1st class taken at Level 1)
CLASS_STARTING_PROFICIENCIES: Dict[str, Dict[str, Any]] = {
    "barbarian": {
        "armor": {"light armor", "medium armor", "shields"},
        "weapons": {"simple weapons", "martial weapons"},
        "preferred_weapons_str": ["Greataxe", "Maul", "Greatsword", "Battleaxe", "Halberd"],
        "preferred_weapons_dex": ["Scimitar", "Shortsword", "Shortbow"],
        "preferred_armor": ["Half Plate", "Breastplate", "Scale Mail", "Shield"],
        "style": "martial",
    },
    "bard": {
        "armor": {"light armor"},
        "weapons": {"simple weapons", "hand crossbows", "longswords", "rapiers", "shortswords"},
        "preferred_weapons_str": ["Longsword", "Mace", "Quarterstaff"],
        "preferred_weapons_dex": ["Rapier", "Shortsword", "Hand Crossbow", "Dagger"],
        "preferred_armor": ["Studded Leather", "Leather"],
        "style": "hybrid",
    },
    "cleric": {
        "armor": {"light armor", "medium armor", "shields"},
        "weapons": {"simple weapons"},
        "preferred_weapons_str": ["Mace", "Morningstar", "Light Hammer", "Greatclub"],
        "preferred_weapons_dex": ["Light Crossbow", "Dagger", "Sling"],
        "preferred_armor": ["Half Plate", "Breastplate", "Scale Mail", "Shield"],
        "style": "caster_support",
    },
    "druid": {
        "armor": {"light armor", "medium armor", "shields"},
        "weapons": {
            "clubs", "daggers", "darts", "javelins", "maces",
            "quarterstaffs", "scimitars", "sickles", "slings", "spears",
        },
        "preferred_weapons_str": ["Quarterstaff", "Spear", "Mace", "Club"],
        "preferred_weapons_dex": ["Scimitar", "Dagger", "Sling", "Dart"],
        "preferred_armor": ["Hide", "Studded Leather", "Leather", "Shield"],
        "style": "caster",
    },
    "fighter": {
        "armor": {"light armor", "medium armor", "heavy armor", "shields"},
        "weapons": {"simple weapons", "martial weapons"},
        "preferred_weapons_str": ["Greatsword", "Longsword", "Halberd", "Battleaxe", "Warhammer"],
        "preferred_weapons_dex": ["Rapier", "Longbow", "Shortsword", "Heavy Crossbow"],
        "preferred_armor": ["Plate", "Splint", "Half Plate", "Chain Mail", "Shield"],
        "style": "martial",
    },
    "monk": {
        "armor": set(),
        "weapons": {"simple weapons", "shortswords"},
        "preferred_weapons_str": ["Spear", "Quarterstaff", "Shortsword"],
        "preferred_weapons_dex": ["Shortsword", "Dagger", "Dart", "Shortbow"],
        "preferred_armor": [],
        "style": "skirmisher",
    },
    "paladin": {
        "armor": {"light armor", "medium armor", "heavy armor", "shields"},
        "weapons": {"simple weapons", "martial weapons"},
        "preferred_weapons_str": ["Longsword", "Greatsword", "Warhammer", "Halberd", "Lance"],
        "preferred_weapons_dex": ["Rapier", "Shortsword", "Javelin"],
        "preferred_armor": ["Plate", "Splint", "Chain Mail", "Half Plate", "Shield"],
        "style": "martial",
    },
    "ranger": {
        "armor": {"light armor", "medium armor", "shields"},
        "weapons": {"simple weapons", "martial weapons"},
        "preferred_weapons_str": ["Longsword", "Battleaxe", "Spear"],
        "preferred_weapons_dex": ["Longbow", "Shortsword", "Scimitar", "Rapier", "Hand Crossbow"],
        "preferred_armor": ["Studded Leather", "Half Plate", "Breastplate"],
        "style": "martial_dex",
    },
    "rogue": {
        "armor": {"light armor"},
        "weapons": {"simple weapons", "hand crossbows", "longswords", "rapiers", "shortswords"},
        "preferred_weapons_str": ["Shortsword", "Dagger", "Rapier"],
        "preferred_weapons_dex": ["Dagger", "Rapier", "Shortsword", "Shortbow", "Hand Crossbow"],
        "preferred_armor": ["Studded Leather", "Leather"],
        "style": "finesse",
    },
    "sorcerer": {
        "armor": set(),
        "weapons": {"daggers", "darts", "slings", "quarterstaffs", "light crossbows"},
        "preferred_weapons_str": ["Quarterstaff", "Dagger"],
        "preferred_weapons_dex": ["Dagger", "Light Crossbow", "Dart"],
        "preferred_armor": [],
        "style": "caster",
    },
    "warlock": {
        "armor": {"light armor"},
        "weapons": {"simple weapons"},
        "preferred_weapons_str": ["Quarterstaff", "Spear", "Mace"],
        "preferred_weapons_dex": ["Dagger", "Light Crossbow", "Quarterstaff"],
        "preferred_armor": ["Studded Leather", "Leather"],
        "style": "caster",
    },
    "wizard": {
        "armor": set(),
        "weapons": {"daggers", "darts", "slings", "quarterstaffs", "light crossbows"},
        "preferred_weapons_str": ["Quarterstaff", "Dagger"],
        "preferred_weapons_dex": ["Dagger", "Light Crossbow", "Dart"],
        "preferred_armor": [],
        "style": "caster",
    },
    "artificer": {
        "armor": {"light armor", "medium armor", "shields"},
        "weapons": {"simple weapons"},
        "preferred_weapons_str": ["Light Hammer", "Mace", "Quarterstaff"],
        "preferred_weapons_dex": ["Light Crossbow", "Dagger"],
        "preferred_armor": ["Half Plate", "Breastplate", "Scale Mail", "Shield"],
        "style": "caster_support",
    },
}

# Canonical individual Simple & Martial weapons for fallback inference when an item isn't explicitly tagged
SIMPLE_WEAPON_KEYWORDS: Tuple[str, ...] = (
    "club", "dagger", "greatclub", "handaxe", "javelin", "light hammer",
    "mace", "quarterstaff", "sickle", "spear", "light crossbow", "dart",
    "shortbow", "sling",
)
MARTIAL_WEAPON_KEYWORDS: Tuple[str, ...] = (
    "battleaxe", "flail", "glaive", "greataxe", "greatsword", "halberd",
    "lance", "longsword", "maul", "morningstar", "pike", "rapier",
    "scimitar", "shortsword", "trident", "war pick", "warhammer", "whip",
    "blowgun", "hand crossbow", "heavy crossbow", "longbow", "net", "musket",
    "pistol",
)
SWORD_MARTIAL_MAGIC_NAMES: Tuple[str, ...] = (
    "flame tongue", "frost brand", "dancing sword", "defender", "dragon slayer",
    "giant slayer", "holy avenger", "luck blade", "nine lives stealer",
    "sun blade", "sword of life stealing", "sword of sharpness",
    "sword of wounding", "vorpal sword", "vicious weapon",
)

TIER_CONFIG: Dict[int, Dict[str, Any]] = {
    1: {
        "tier": 1,
        "name": "Local Heroes (Lv. 1-4)",
        "rarities": ["Common", "Uncommon"],
        "quest_rarity": "Uncommon",
        "max_magic_bonus": 1,
        "default_magic_bonus": 0,
        "max_extra_dice_count": 1,
        "max_extra_die_sides": 4,
        "gold_range": (50, 150),
        "quest_gold_default": 100,
        "quest_xp_default": 200,
    },
    2: {
        "tier": 2,
        "name": "Heroes of the Realm (Lv. 5-10)",
        "rarities": ["Uncommon", "Rare"],
        "quest_rarity": "Rare",
        "max_magic_bonus": 1,
        "default_magic_bonus": 1,
        "max_extra_dice_count": 1,
        "max_extra_die_sides": 6,
        "gold_range": (250, 750),
        "quest_gold_default": 500,
        "quest_xp_default": 1000,
    },
    3: {
        "tier": 3,
        "name": "Masters of the Realm (Lv. 11-16)",
        "rarities": ["Rare", "Very Rare"],
        "quest_rarity": "Very Rare",
        "max_magic_bonus": 2,
        "default_magic_bonus": 2,
        "max_extra_dice_count": 2,
        "max_extra_die_sides": 6,
        "gold_range": (1000, 2500),
        "quest_gold_default": 1500,
        "quest_xp_default": 3000,
    },
    4: {
        "tier": 4,
        "name": "Masters of the World (Lv. 17-20)",
        "rarities": ["Very Rare", "Legendary"],
        "quest_rarity": "Very Rare",
        "max_magic_bonus": 3,
        "default_magic_bonus": 2,
        "max_extra_dice_count": 2,
        "max_extra_die_sides": 8,
        "gold_range": (3000, 10000),
        "quest_gold_default": 5000,
        "quest_xp_default": 6000,
    },
}

_RARITY_ORDER = {
    "common": 1,
    "uncommon": 2,
    "rare": 3,
    "very rare": 4,
    "legendary": 5,
    "artifact": 6,
}


@lru_cache(maxsize=1)
def _load_srd_classes_map() -> Dict[str, Dict[str, Any]]:
    path = os.path.join(_DATA_DIR, "srd_classes.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        cls_raw = raw.get("classes", {})
        if isinstance(cls_raw, dict):
            return {k.lower(): v for k, v in cls_raw.items()}
        if isinstance(cls_raw, list):
            return {str(v.get("name", "")).lower(): v for v in cls_raw if isinstance(v, dict)}
    except Exception:
        pass
    return {}


def get_tier_for_level(level: int) -> Dict[str, Any]:
    """Maps a character or party average level (1-20+) to its D&D 5e tier budget."""
    lvl = max(1, int(level or 1))
    if lvl <= 4:
        return dict(TIER_CONFIG[1])
    if lvl <= 10:
        return dict(TIER_CONFIG[2])
    if lvl <= 16:
        return dict(TIER_CONFIG[3])
    return dict(TIER_CONFIG[4])


def parse_class_levels(archetype_str: str) -> List[Tuple[str, int]]:
    """
    Parses strings like 'Barbarian 5 / Bard 5 / Druid 5 / Warlock 5' or 'Rogue (Assassin) 8'
    into ordered `[(canonical_class_name, level), ...]`.
    """
    raw = str(archetype_str or "").strip()
    if not raw:
        return []
    known = set(CLASS_STARTING_PROFICIENCIES.keys())
    parsed: List[Tuple[str, int]] = []
    for segment in re.split(r"[/,|;]+", raw):
        seg = segment.strip()
        if not seg:
            continue
        seg_low = seg.lower()
        found_cls = None
        for cls_name in known:
            if re.search(rf"\b{cls_name}\b", seg_low):
                found_cls = cls_name
                break
        if not found_cls:
            continue
        m_lvl = re.search(r"\b(\d{1,2})\b", seg)
        cls_lvl = int(m_lvl.group(1)) if m_lvl else 1
        parsed.append((found_cls, max(1, cls_lvl)))
    return parsed


def pick_reward_class_for_character(
    char_data: Dict[str, Any],
    rng: Optional[random.Random] = None,
) -> Optional[str]:
    """
    Selects the priority class for a Quest Reward:
    - Picks the class with the highest level.
    - When tied (e.g. 5/5/5/5 or 10/10), chooses randomly among tied top classes.
    """
    native = char_data.get("native_system_data") if isinstance(char_data.get("native_system_data"), dict) else {}
    arch = str(
        char_data.get("archetype")
        or char_data.get("class_and_level")
        or native.get("class_level")
        or ""
    )
    cls_levels = parse_class_levels(arch)
    if not cls_levels:
        return None
    max_lvl = max(lvl for _, lvl in cls_levels)
    tied = [cls_name for cls_name, lvl in cls_levels if lvl == max_lvl]
    chooser = rng or random
    return chooser.choice(tied)


def _normalize_weapon_token(tok: str) -> str:
    s = re.sub(r"[\.\(\)\[\]:]+", " ", str(tok or "").lower()).strip()
    s = re.sub(r"\s+", " ", s)
    if s.endswith("ves"):
        return s
    if s.endswith("staves"):
        return s[:-3] + "f"
    if s.endswith("staffs"):
        return s[:-1]
    if s.endswith("s") and not s.endswith("weapons") and len(s) > 4:
        return s[:-1]
    return s


def _extract_proficiency_tokens(raw: Any) -> List[str]:
    tokens: List[str] = []
    if not raw:
        return tokens
    if isinstance(raw, dict):
        for v in raw.values():
            tokens.extend(_extract_proficiency_tokens(v))
        return tokens
    if isinstance(raw, list):
        for v in raw:
            tokens.extend(_extract_proficiency_tokens(v))
        return tokens
    text = str(raw)
    # Strip header markers like === WEAPONS ===
    text = re.sub(r"===\s*[A-Za-z ]+\s*===", ",", text)
    for part in re.split(r"[,;\n/|]+", text):
        p = part.strip()
        if p and p.lower() != "none":
            tokens.append(p)
    return tokens


def get_character_proficiencies(char_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extracts normalized weapon and armor proficiencies for a character by combining:
    1. Explicit sheet fields (`proficiencies_weapons`, `proficiencies_armor`, `proficiencies`).
    2. First class starting proficiencies + multiclass proficiencies from `srd_classes.json`.
    3. Key martial subclass/feature grants (e.g. Hexblade / Pact of the Blade / Valor / Swords).
    """
    native = char_data.get("native_system_data") if isinstance(char_data.get("native_system_data"), dict) else {}

    weapon_cats: Set[str] = set()
    specific_weapons: Set[str] = set()
    armor_cats: Set[str] = set()

    def _ingest_tokens(toks: List[str], kind_hint: str = "any"):
        for t in toks:
            tl = t.lower().strip()
            if "light armor" in tl or tl == "light":
                armor_cats.add("light armor")
            if "medium armor" in tl or tl == "medium":
                armor_cats.add("medium armor")
            if "heavy armor" in tl or tl == "heavy":
                armor_cats.add("heavy armor")
            if "all armor" in tl:
                armor_cats.update({"light armor", "medium armor", "heavy armor"})
            if "shield" in tl:
                armor_cats.add("shields")
            if "simple weapon" in tl or tl == "simple":
                weapon_cats.add("simple")
            if "martial weapon" in tl or tl == "martial":
                weapon_cats.add("martial")
            if "all weapon" in tl:
                weapon_cats.update({"simple", "martial"})

            # Check known individual weapon names inside token
            norm = _normalize_weapon_token(tl)
            for kw in SIMPLE_WEAPON_KEYWORDS + MARTIAL_WEAPON_KEYWORDS:
                if kw in tl or kw == norm:
                    specific_weapons.add(kw)
            if kind_hint == "weapons" and norm and norm not in ("simple weapon", "martial weapon"):
                specific_weapons.add(norm)

    w_raw = char_data.get("proficiencies_weapons") or native.get("proficiencies_weapons")
    a_raw = char_data.get("proficiencies_armor") or native.get("proficiencies_armor")
    general_raw = char_data.get("proficiencies") or native.get("proficiencies")

    _ingest_tokens(_extract_proficiency_tokens(w_raw), kind_hint="weapons")
    _ingest_tokens(_extract_proficiency_tokens(a_raw), kind_hint="armor")

    # Handle sectioned proficiencies string or dict if specific fields were empty
    if isinstance(general_raw, dict):
        _ingest_tokens(_extract_proficiency_tokens(general_raw.get("weapons")), kind_hint="weapons")
        _ingest_tokens(_extract_proficiency_tokens(general_raw.get("armor")), kind_hint="armor")
    elif isinstance(general_raw, str) and general_raw.strip():
        _ingest_tokens(_extract_proficiency_tokens(general_raw), kind_hint="any")

    # Class & multiclass proficiencies from archetype
    arch = str(
        char_data.get("archetype")
        or char_data.get("class_and_level")
        or native.get("class_level")
        or ""
    )
    cls_levels = parse_class_levels(arch)
    srd_classes = _load_srd_classes_map()
    has_explicit_sheet = bool(_extract_proficiency_tokens(w_raw) or _extract_proficiency_tokens(a_raw))

    if cls_levels:
        first_cls = cls_levels[0][0]
        # Only use class starting proficiencies as fallback when explicit sheet armor+weapon fields weren't supplied,
        # OR union missing starting class when sheet omitted them completely.
        if not has_explicit_sheet:
            starter = CLASS_STARTING_PROFICIENCIES.get(first_cls, {})
            armor_cats.update(starter.get("armor", set()))
            _ingest_tokens(list(starter.get("weapons", set())), kind_hint="weapons")
            for extra_cls, _ in cls_levels[1:]:
                mc_str = (srd_classes.get(extra_cls, {}) or {}).get("multiclass_proficiencies_gained", "")
                if mc_str:
                    _ingest_tokens(_extract_proficiency_tokens(mc_str), kind_hint="any")

    # Subclass / Feat grants inside archetype or features
    feats = char_data.get("features_and_traits") or native.get("features_and_traits") or []
    feat_blob = (arch + " " + " ".join(str(x) for x in feats)).lower()
    if any(k in feat_blob for k in ("hexblade", "pact of the blade", "college of valor", "war domain", "tempest domain", "battlesmith", "battle smith")):
        weapon_cats.update({"simple", "martial"})
    if any(k in feat_blob for k in ("hexblade", "college of valor", "college of swords")):
        armor_cats.update({"medium armor", "shields"})
    if any(k in feat_blob for k in ("war domain", "tempest domain", "life domain", "nature domain", "order domain", "forge domain", "heavily armored")):
        armor_cats.add("heavy armor")
    if "weapon master" in feat_blob:
        weapon_cats.update({"simple", "martial"})

    return {
        "weapon_categories": sorted(weapon_cats),
        "specific_weapons": sorted(specific_weapons),
        "armor_categories": sorted(armor_cats),
        "class_levels": cls_levels,
    }


def _infer_weapon_classification(
    weapon_name: str,
    weapon_record: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """
    Returns `(weapon_class_lower, canonical_base_keyword)` e.g. `('martial', 'greataxe')` or `('simple', 'dagger')`.
    """
    rec = weapon_record or {}
    w_class = str(rec.get("weapon_class") or "").strip().lower()
    cat = str(rec.get("category") or "").strip().lower()
    name_low = str(rec.get("name") or weapon_name or "").strip().lower()
    desc_low = str(rec.get("description") or "").lower()

    base_kw = ""
    for kw in sorted(MARTIAL_WEAPON_KEYWORDS + SIMPLE_WEAPON_KEYWORDS, key=len, reverse=True):
        if kw in name_low or kw in desc_low:
            base_kw = kw
            break

    if w_class in ("simple", "martial"):
        return w_class, base_kw
    if "martial" in cat:
        return "martial", base_kw
    if "simple" in cat:
        return "simple", base_kw

    if base_kw in MARTIAL_WEAPON_KEYWORDS:
        return "martial", base_kw
    if base_kw in SIMPLE_WEAPON_KEYWORDS:
        return "simple", base_kw
    if any(s in name_low for s in SWORD_MARTIAL_MAGIC_NAMES) or "sword" in name_low or "axe" in name_low or "bow" in name_low:
        return "martial", base_kw or name_low
    return "", base_kw or _normalize_weapon_token(name_low)


def is_weapon_proficient(
    char_data: Dict[str, Any],
    weapon: Any,
    homebrew_mgr: Optional[HomebrewManager] = None,
) -> Tuple[bool, str]:
    """
    Checks whether `char_data` is proficient with `weapon` (name or dict).
    Returns `(proficient: bool, reason: str)`.
    """
    w_rec: Optional[Dict[str, Any]] = None
    if isinstance(weapon, dict):
        w_name = str(weapon.get("name") or "").strip()
        w_rec = dict(weapon)
        if not w_rec.get("weapon_class") and not w_rec.get("category") and w_name:
            comp = get_weapon(w_name, homebrew_mgr=homebrew_mgr)
            if comp:
                w_rec = {**comp, **w_rec}
    else:
        w_name = str(weapon or "").strip()
        if w_name:
            w_rec = get_weapon(w_name, homebrew_mgr=homebrew_mgr)

    if not w_name:
        return True, ""

    # Natural / Unarmed attacks are always proficient
    if any(k in w_name.lower() for k in ("unarmed", "bite", "claw", "slam", "eldritch blast", "spell")):
        return True, ""

    profs = get_character_proficiencies(char_data)
    w_cats = set(profs["weapon_categories"])
    spec = set(profs["specific_weapons"])

    # If no classes AND no explicit proficiencies exist on a minimal test mock character, fail open unless classes exist
    if not w_cats and not spec and not profs["class_levels"]:
        return True, ""

    w_class, base_kw = _infer_weapon_classification(w_name, w_rec)
    norm_name = _normalize_weapon_token(w_name)

    if w_class and w_class in w_cats:
        return True, f"Proficient ({w_class.title()} Weapons)"
    if base_kw and base_kw in spec:
        return True, f"Proficient ({base_kw.title()})"
    if norm_name and norm_name in spec:
        return True, f"Proficient ({w_name})"

    needed = f"{w_class.title()} Weapons" if w_class else w_name
    return False, f"NOT PROFICIENT in {w_name} (requires {needed}; D&D 5e RAW: omit Proficiency Bonus on attack rolls)"


def is_armor_proficient(
    char_data: Dict[str, Any],
    armor: Any,
    homebrew_mgr: Optional[HomebrewManager] = None,
) -> Tuple[bool, str]:
    """
    Checks whether `char_data` is proficient with `armor` (name or compendium/inventory dict).
    Returns `(proficient: bool, reason: str)`.
    """
    a_rec: Optional[Dict[str, Any]] = None
    if isinstance(armor, dict):
        a_name = str(armor.get("name") or "").strip()
        a_rec = dict(armor)
        if a_name and not any(k in str(a_rec.get("category") or "").lower() for k in ("light", "medium", "heavy", "shield")):
            comp = get_armor(a_name, homebrew_mgr=homebrew_mgr)
            if comp:
                a_rec = {**comp, **a_rec}
    else:
        a_name = str(armor or "").strip()
        if a_name:
            a_rec = get_armor(a_name, homebrew_mgr=homebrew_mgr)

    if not a_name or a_name.lower() in ("unarmored", "none"):
        return True, ""

    profs = get_character_proficiencies(char_data)
    a_cats = set(profs["armor_categories"])
    if not a_cats and not profs["class_levels"]:
        return True, ""

    cat_low = str((a_rec or {}).get("category") or "").lower()
    name_low = a_name.lower()

    needed = ""
    if "shield" in cat_low or "shield" in name_low:
        needed = "shields"
    elif "heavy" in cat_low or any(k in name_low for k in ("ring mail", "chain mail", "splint", "plate")):
        needed = "heavy armor"
    elif "medium" in cat_low or any(k in name_low for k in ("hide", "chain shirt", "scale mail", "breastplate", "half plate")):
        needed = "medium armor"
    elif "light" in cat_low or any(k in name_low for k in ("padded", "leather", "studded leather")):
        needed = "light armor"

    if not needed:
        return True, ""

    if needed in a_cats:
        return True, f"Proficient ({needed.title()})"
    return False, (
        f"NOT PROFICIENT in {a_name} (requires {needed.title()}; "
        "D&D 5e RAW: wearing non-proficient armor/shield imposes Disadvantage on STR/DEX checks, saves, and attacks, and prevents spellcasting)"
    )


def get_equipped_armor_penalties(
    char_data: Dict[str, Any],
    homebrew_mgr: Optional[HomebrewManager] = None,
) -> Dict[str, Any]:
    """
    Evaluates currently equipped armor/shields on `char_data` under D&D 5e RAW.
    """
    non_prof: List[str] = []
    seen_low: Set[str] = set()

    eq_armor = str(char_data.get("armor_equipped") or "").strip()
    if eq_armor and eq_armor.lower() not in ("unarmored", "none"):
        ok, _ = is_armor_proficient(char_data, eq_armor, homebrew_mgr=homebrew_mgr)
        if not ok:
            non_prof.append(eq_armor)
            seen_low.add(eq_armor.lower())

    native = char_data.get("native_system_data") if isinstance(char_data.get("native_system_data"), dict) else {}
    for inv_list in (char_data.get("inventory"), native.get("inventory")):
        if not isinstance(inv_list, list):
            continue
        for entry in inv_list:
            if not isinstance(entry, dict) or not entry.get("equipped"):
                continue
            name = str(entry.get("name") or "").strip()
            cat = str(entry.get("category") or "").lower()
            if not name or name.lower() in seen_low:
                continue
            if cat in ("armor", "shield") or get_armor(name, homebrew_mgr=homebrew_mgr):
                ok, _ = is_armor_proficient(char_data, entry, homebrew_mgr=homebrew_mgr)
                if not ok:
                    non_prof.append(name)
                    seen_low.add(name.lower())

    if bool(char_data.get("shield_equipped")) and not any("shield" in x.lower() for x in non_prof):
        ok, _ = is_armor_proficient(char_data, "Shield", homebrew_mgr=homebrew_mgr)
        if not ok:
            non_prof.append("Shield")

    has_penalty = bool(non_prof)
    return {
        "has_non_proficient_armor": has_penalty,
        "non_proficient_items": non_prof,
        "imposes_str_dex_disadvantage": has_penalty,
        "blocks_spellcasting": has_penalty,
    }


def _rarity_allowed_for_tier(rarity_str: str, tier_cfg: Dict[str, Any]) -> bool:
    r_low = str(rarity_str or "Common").lower()
    allowed_max = max(_RARITY_ORDER.get(x.lower(), 1) for x in tier_cfg["rarities"])
    # Compound strings like "Uncommon (+1), Rare (+2), or Very Rare (+3)"
    for r_name, r_rank in _RARITY_ORDER.items():
        if r_name in r_low:
            if r_rank <= allowed_max:
                return True
    return _RARITY_ORDER.get(r_low, 1) <= allowed_max


def _character_prefers_dex(char_data: Dict[str, Any]) -> bool:
    attrs = char_data.get("attributes") or {}
    native = char_data.get("native_system_data") if isinstance(char_data.get("native_system_data"), dict) else {}
    ab = native.get("ability_scores") or {}

    def _read_stat(keys: Tuple[str, ...]) -> int:
        for src in (attrs, ab):
            if isinstance(src, dict):
                for k in keys:
                    if k in src and src[k] is not None:
                        val = src[k]
                        if isinstance(val, dict):
                            val = val.get("score", val.get("value", 10))
                        try:
                            return int(val)
                        except (TypeError, ValueError):
                            pass
        return 10

    str_val = _read_stat(("Strength", "STR", "Might"))
    dex_val = _read_stat(("Dexterity", "DEX", "Agility"))
    return dex_val > str_val


def _stat_mods_and_prof(char_data: Dict[str, Any]) -> Tuple[int, int, int]:
    attrs = char_data.get("attributes") or {}
    native = char_data.get("native_system_data") if isinstance(char_data.get("native_system_data"), dict) else {}
    ab = native.get("ability_scores") or {}

    def _read_stat(keys: Tuple[str, ...]) -> int:
        for src in (attrs, ab):
            if isinstance(src, dict):
                for k in keys:
                    if k in src and src[k] is not None:
                        val = src[k]
                        if isinstance(val, dict):
                            val = val.get("score", val.get("value", 10))
                        try:
                            return int(val)
                        except (TypeError, ValueError):
                            pass
        return 10

    str_mod = (_read_stat(("Strength", "STR", "Might")) - 10) // 2
    dex_mod = (_read_stat(("Dexterity", "DEX", "Agility")) - 10) // 2
    lvl = max(1, int(char_data.get("level") or 1))
    prof_bonus = 2 + (lvl - 1) // 4
    return str_mod, dex_mod, prof_bonus


def _apply_tier_magic_weapon_variant(
    base_wpn: Dict[str, Any],
    bonus: int,
) -> Dict[str, Any]:
    """Creates a clean `+1/+2/+3` sheet-ready weapon dict from a base SRD weapon without losing its base dice/properties."""
    wpn = dict(base_wpn)
    if bonus > 0:
        wpn["name"] = f"{wpn['name']} +{bonus}"
        wpn["atk_bonus"] = f"+{bonus}"
        wpn["is_magic"] = True
        wpn["rarity"] = {1: "Uncommon", 2: "Rare", 3: "Very Rare"}.get(bonus, "Rare")
        wpn["index"] = f"{wpn.get('index', 'wpn')}-plus-{bonus}"
    return wpn


def generate_random_loot(
    party_level: int = 1,
    hoard_size: str = "standard",
    edition: str = "2024",
    include_homebrew: bool = True,
    homebrew_mgr: Optional[HomebrewManager] = None,
    rng: Optional[random.Random] = None,
) -> Dict[str, Any]:
    """
    Generates general world/chest loot unbiased by party classes (e.g. a Longbow can drop
    in a cave even if nobody in the party uses bows). Scales gold and rarity by `party_level`.
    """
    chooser = rng or random
    tier = get_tier_for_level(party_level)
    size_norm = (hoard_size or "standard").lower().strip()
    lo_g, hi_g = tier["gold_range"]
    mult = {"small": 0.5, "standard": 1.0, "boss": 1.5, "hoard": 2.5}.get(size_norm, 1.0)
    gold_total = int(round(chooser.randint(lo_g, hi_g) * mult))

    item_count = {"small": 1, "standard": 2, "boss": 3, "hoard": 4}.get(size_norm, 2)

    all_wpns = [
        w for w in list_weapons(edition=edition, include_magic=True, homebrew_mgr=homebrew_mgr)
        if (include_homebrew or not w.get("is_homebrew"))
        and "ammunition" not in w.get("name", "").lower()
        and not w.get("name", "").lower().startswith("weapon, +")
        and _rarity_allowed_for_tier(w.get("rarity", "Common"), tier)
    ]
    all_arm = [
        a for a in list_armor(edition=edition, include_magic=True, homebrew_mgr=homebrew_mgr)
        if (include_homebrew or not a.get("is_homebrew"))
        and not a.get("name", "").lower().startswith("armor, +")
        and _rarity_allowed_for_tier(a.get("rarity", "Common"), tier)
    ]
    all_items = [
        it for it in list_items(edition=edition, include_magic=True, homebrew_mgr=homebrew_mgr)
        if (include_homebrew or not it.get("is_homebrew"))
        and _rarity_allowed_for_tier(it.get("rarity", "Common"), tier)
    ]

    weapons_out: List[Dict[str, Any]] = []
    armor_out: List[Dict[str, Any]] = []
    items_out: List[Dict[str, Any]] = []

    for _ in range(item_count):
        bucket = chooser.choice(["weapon", "armor", "item"])
        if bucket == "weapon" and all_wpns:
            picked = dict(chooser.choice(all_wpns))
            if not picked.get("is_magic") and tier["default_magic_bonus"] > 0 and chooser.random() < 0.45:
                picked = _apply_tier_magic_weapon_variant(picked, chooser.randint(1, tier["max_magic_bonus"]))
            weapons_out.append(picked)
        elif bucket == "armor" and all_arm:
            armor_out.append(dict(chooser.choice(all_arm)))
        elif all_items:
            items_out.append(format_item_for_sheet(chooser.choice(all_items)))

    return {
        "mode": "random_chest",
        "tier": tier["tier"],
        "tier_name": tier["name"],
        "gold_total": gold_total,
        "weapons": weapons_out,
        "armor": armor_out,
        "items": items_out,
    }


def _clamp_extra_damage(raw_extra: str, tier_cfg: Dict[str, Any]) -> str:
    txt = str(raw_extra or "").strip()
    if not txt:
        return ""
    m = re.match(r"^\s*(\d+)d(\d+)(?:\s*(.*))?$", txt, flags=re.IGNORECASE)
    if not m:
        return ""
    cnt = min(int(m.group(1)), int(tier_cfg["max_extra_dice_count"]))
    sides = min(int(m.group(2)), int(tier_cfg["max_extra_die_sides"]))
    # Normalize die sides to valid polyhedral die
    valid_sides = (4, 6, 8, 10, 12)
    sides = max((s for s in valid_sides if s <= sides), default=4)
    dmg_type = (m.group(3) or "elemental").strip()
    return f"+{max(1, cnt)}d{sides} {dmg_type}".strip()


def create_tier_bounded_homebrew(
    char_data: Dict[str, Any],
    spec: Dict[str, Any],
    homebrew_mgr: Optional[HomebrewManager] = None,
    edition: str = "2024",
) -> Dict[str, Any]:
    """
    Registers a creative DM-designed quest weapon/armor/item in `HomebrewManager` while
    enforcing D&D 5e level-tier bounds (enhancement bonus & extra damage dice) and
    sheet proficiency matching for `char_data`.
    """
    mgr = homebrew_mgr or get_default_homebrew_manager()
    lvl = max(1, int(char_data.get("level") or 1))
    tier = get_tier_for_level(lvl)
    cat = str(spec.get("category") or "weapons").lower().strip()
    if cat not in ("weapons", "armor", "items"):
        cat = "weapons"

    name = str(spec.get("name") or "").strip()
    if not name:
        return {"status": "error", "message": "Homebrew reward requires a name."}

    raw_bonus = int(spec.get("magic_bonus") if spec.get("magic_bonus") is not None else tier["default_magic_bonus"])
    clamped_bonus = max(0, min(raw_bonus, int(tier["max_magic_bonus"])))

    entry: Dict[str, Any] = {
        "name": name,
        "description": str(spec.get("description") or f"Quest reward crafted for {char_data.get('name', 'hero')}.").strip(),
        "is_magic": bool(spec.get("is_magic", clamped_bonus > 0 or bool(spec.get("extra_damage")))),
        "rarity": tier["quest_rarity"] if clamped_bonus >= 1 else "Common",
        "requires_attunement": bool(spec.get("requires_attunement", clamped_bonus >= 2 or bool(spec.get("extra_damage")))),
    }

    base_name = str(spec.get("base_item") or "").strip()
    if cat == "weapons":
        base_wpn = get_weapon(base_name, homebrew_mgr=mgr) if base_name else None
        if base_wpn:
            ok_prof, reason = is_weapon_proficient(char_data, base_wpn, homebrew_mgr=mgr)
            if not ok_prof:
                # Replace base template with class-proficient template rather than handing out an unusable weapon
                base_wpn = None
        if not base_wpn:
            picked = select_class_loot_for_character(char_data, item_type="weapon", edition=edition, homebrew_mgr=mgr)
            base_wpn = picked.get("raw_record") if picked else None

        dmg_dice = (base_wpn or {}).get("damage_dice") or "1d8"
        dmg_type = (base_wpn or {}).get("damage_type") or "Slashing"
        extra = _clamp_extra_damage(spec.get("extra_damage", ""), tier)
        dmg_str = f"{dmg_dice} {dmg_type}".strip()
        if extra:
            dmg_str = f"{dmg_str} ({extra})"

        entry.update({
            "category": (base_wpn or {}).get("category") or "Martial Melee",
            "weapon_class": (base_wpn or {}).get("weapon_class") or "Martial",
            "range_type": (base_wpn or {}).get("range_type") or "Melee",
            "damage": dmg_str,
            "damage_dice": dmg_dice,
            "damage_type": dmg_type,
            "atk_bonus": f"+{clamped_bonus}" if clamped_bonus > 0 else "",
            "range": (base_wpn or {}).get("range") or "5 ft.",
            "properties": list((base_wpn or {}).get("properties") or ["Magical"]),
            "mastery": (base_wpn or {}).get("mastery") or "",
            "weight": (base_wpn or {}).get("weight") or 3.0,
        })
        if extra and f"Extra {extra}" not in entry["description"]:
            entry["description"] = f"{entry['description']} Deals {extra} on a hit.".strip()

    elif cat == "armor":
        base_arm = get_armor(base_name, homebrew_mgr=mgr) if base_name else None
        if base_arm:
            ok_prof, _ = is_armor_proficient(char_data, base_arm, homebrew_mgr=mgr)
            if not ok_prof:
                base_arm = None
        if not base_arm:
            picked = select_class_loot_for_character(char_data, item_type="armor", edition=edition, homebrew_mgr=mgr)
            base_arm = picked.get("raw_record") if picked else None

        entry.update({
            "category": (base_arm or {}).get("category") or "Light Armor",
            "ac_base": (base_arm or {}).get("ac_base") or 12,
            "ac_bonus": clamped_bonus,
            "dex_bonus": (base_arm or {}).get("dex_bonus", True),
            "max_dex_bonus": (base_arm or {}).get("max_dex_bonus"),
            "str_minimum": (base_arm or {}).get("str_minimum") or 0,
            "stealth_disadvantage": (base_arm or {}).get("stealth_disadvantage", False),
            "weight": (base_arm or {}).get("weight") or 10.0,
        })

    else:
        entry.update({
            "category": "Magic Items",
            "subcategory": str(spec.get("subcategory") or "Wondrous Items"),
            "consumable": bool(spec.get("consumable", False)),
            "weight": float(spec.get("weight") or 1.0),
        })

    saved = mgr.save_entry(cat, entry)
    if saved.get("status") != "success":
        return saved
    saved_entry = saved["entry"]
    return {
        "status": "success",
        "category": cat,
        "tier": tier["tier"],
        "entry": saved_entry,
    }


def select_class_loot_for_character(
    char_data: Dict[str, Any],
    item_type: str = "auto",
    edition: str = "2024",
    homebrew_mgr: Optional[HomebrewManager] = None,
    rng: Optional[random.Random] = None,
) -> Optional[Dict[str, Any]]:
    """
    Selects a level-scaled, sheet-proficient reward tailored to `char_data`'s highest class
    level (randomly choosing among tied multiclasses). Guaranteed never to return a weapon/armor
    the character lacks proficiency in when matching class rewards exist.
    """
    chooser = rng or random
    lvl = max(1, int(char_data.get("level") or 1))
    tier = get_tier_for_level(lvl)
    priority_cls = pick_reward_class_for_character(char_data, rng=chooser) or "fighter"
    cls_profile = CLASS_STARTING_PROFICIENCIES.get(priority_cls, CLASS_STARTING_PROFICIENCIES["fighter"])
    prefer_dex = _character_prefers_dex(char_data)

    want = (item_type or "auto").lower().strip()
    if want == "auto":
        # Pure casters favor wondrous/arcane/spell focus items ~45% of the time, martial favors weapons/armor
        if cls_profile["style"] == "caster" and chooser.random() < 0.45:
            want = "item"
        elif cls_profile.get("preferred_armor") and chooser.random() < 0.25:
            want = "armor"
        else:
            want = "weapon"

    str_mod, dex_mod, prof_bonus = _stat_mods_and_prof(char_data)

    if want in ("weapon", "weapons"):
        pref_names = cls_profile["preferred_weapons_dex"] if prefer_dex else cls_profile["preferred_weapons_str"]
        candidates: List[Dict[str, Any]] = []
        # 1. Try named magic weapons from SRD/Homebrew that are both proficient and fit tier
        all_wpns = list_weapons(edition=edition, include_magic=True, homebrew_mgr=homebrew_mgr)
        for w in all_wpns:
            if w.get("is_magic") and "ammunition" not in w.get("name", "").lower() and not w.get("name", "").lower().startswith("weapon, +"):
                if not _rarity_allowed_for_tier(w.get("rarity", "Common"), tier):
                    continue
                ok, _ = is_weapon_proficient(char_data, w, homebrew_mgr=homebrew_mgr)
                if ok:
                    if priority_cls == "barbarian" and "dagger" in w.get("name", "").lower():
                        continue
                    candidates.append(w)

        # 2. Build tier-appropriate +bonus variants from class-iconic proficient base weapons
        base_pool: List[Dict[str, Any]] = []
        for pname in pref_names:
            w = get_weapon(pname, homebrew_mgr=homebrew_mgr)
            if w:
                ok, _ = is_weapon_proficient(char_data, w, homebrew_mgr=homebrew_mgr)
                if ok:
                    base_pool.append(w)

        if not base_pool:
            for w in all_wpns:
                if not w.get("is_magic"):
                    ok, _ = is_weapon_proficient(char_data, w, homebrew_mgr=homebrew_mgr)
                    if ok:
                        base_pool.append(w)

        if base_pool and (not candidates or chooser.random() < 0.65):
            base_picked = chooser.choice(base_pool)
            magic_bonus = tier["default_magic_bonus"]
            raw_wpn = _apply_tier_magic_weapon_variant(base_picked, magic_bonus)
        elif candidates:
            raw_wpn = dict(chooser.choice(candidates))
        elif base_pool:
            raw_wpn = dict(chooser.choice(base_pool))
        else:
            return None

        sheet_wpn = format_weapon_for_sheet(
            raw_wpn,
            str_mod=str_mod,
            dex_mod=dex_mod,
            prof_bonus=prof_bonus,
            proficient=True,
            edition=edition,
        )
        return {
            "category": "weapons",
            "priority_class": priority_cls,
            "tier": tier["tier"],
            "raw_record": raw_wpn,
            "sheet_entry": sheet_wpn,
        }

    if want in ("armor", "armors", "shield", "shields"):
        arm_prefs = cls_profile.get("preferred_armor") or []
        arm_pool: List[Dict[str, Any]] = []
        for aname in arm_prefs:
            a = get_armor(aname, homebrew_mgr=homebrew_mgr)
            if a:
                ok, _ = is_armor_proficient(char_data, a, homebrew_mgr=homebrew_mgr)
                if ok:
                    arm_pool.append(a)
        if not arm_pool:
            for a in list_armor(edition=edition, include_magic=True, homebrew_mgr=homebrew_mgr):
                if a.get("name", "").lower().startswith("armor, +"):
                    continue
                ok, _ = is_armor_proficient(char_data, a, homebrew_mgr=homebrew_mgr)
                if ok and _rarity_allowed_for_tier(a.get("rarity", "Common"), tier):
                    arm_pool.append(a)
        if arm_pool:
            picked_arm = dict(chooser.choice(arm_pool))
            if not picked_arm.get("is_magic") and tier["default_magic_bonus"] > 0:
                bonus = tier["default_magic_bonus"]
                picked_arm["name"] = f"{picked_arm['name']} +{bonus}"
                picked_arm["ac_bonus"] = int(picked_arm.get("ac_bonus") or 0) + bonus
                picked_arm["is_magic"] = True
                picked_arm["rarity"] = {1: "Uncommon", 2: "Rare", 3: "Very Rare"}.get(bonus, "Rare")
            return {
                "category": "armor",
                "priority_class": priority_cls,
                "tier": tier["tier"],
                "raw_record": picked_arm,
                "sheet_entry": picked_arm,
            }

    # Caster / general magic item fallback scaled by tier
    magic_pool = [
        it for it in list_items(edition=edition, include_magic=True, homebrew_mgr=homebrew_mgr)
        if it.get("is_magic")
        and _rarity_allowed_for_tier(it.get("rarity", "Uncommon"), tier)
    ]
    if priority_cls in ("wizard", "sorcerer", "warlock", "bard", "druid", "cleric"):
        caster_pool = [
            it for it in magic_pool
            if any(k in (it.get("name", "") + " " + it.get("subcategory", "") + " " + it.get("description", "")).lower()
                   for k in ("staff", "wand", "rod", "ring", "amulet", "cloak", "robe", "pearl", "spell", "tome", "potion"))
        ]
        if caster_pool:
            magic_pool = caster_pool

    if magic_pool:
        picked_it = dict(chooser.choice(magic_pool))
        return {
            "category": "items",
            "priority_class": priority_cls,
            "tier": tier["tier"],
            "raw_record": picked_it,
            "sheet_entry": format_item_for_sheet(picked_it),
        }
    return None
