"""
core/compendiums/dnd_spells.py - D&D 5e (2014 SRD 5.1 & 2024 SRD 5.2) Spells Compendium
Provides complete SRD class-indexed spell lists, edition comparison (2014 vs 2024),
PHB <-> SRD name aliases, prepared/known filtering, and character-sheet formatting.
"""
import os
import re
import json
from functools import lru_cache
from typing import Dict, Any, List, Optional, Union
from core.compendiums.homebrew_manager import HomebrewManager, get_default_homebrew_manager, query_match_rank

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# PHB / D&D Beyond named-spell <-> SRD generic name aliases
PHB_TO_SRD_ALIASES: Dict[str, str] = {
    "aganazzar's scorcher": "aganazzar's scorcher",
    "bigby's hand": "arcane hand",
    "drawmij's instant summons": "instant summons",
    "evard's black tentacles": "black tentacles",
    "leomund's secret chest": "secret chest",
    "leomund's tiny hut": "tiny hut",
    "melf's acid arrow": "acid arrow",
    "mordenkainen's faithful hound": "faithful hound",
    "mordenkainen's magnificent mansion": "magnificent mansion",
    "mordenkainen's private sanctum": "private sanctum",
    "mordenkainen's sword": "arcane sword",
    "nystul's magic aura": "arcanist's magic aura",
    "otiluke's freezing sphere": "freezing sphere",
    "otiluke's resilient sphere": "resilient sphere",
    "otto's irresistible dance": "irresistible dance",
    "rary's telepathic bond": "telepathic bond",
    "tasha's hideous laughter": "hideous laughter",
    "tenser's floating disk": "floating disk",
}

CASTER_CLASSES = (
    "Artificer",
    "Bard",
    "Cleric",
    "Druid",
    "Paladin",
    "Ranger",
    "Sorcerer",
    "Warlock",
    "Wizard",
)

# Preparation style per D&D 5e class
CLASS_PREPARATION_TYPE: Dict[str, str] = {
    "cleric": "prepared_full_list",
    "druid": "prepared_full_list",
    "paladin": "prepared_full_list",
    "artificer": "prepared_full_list",
    "wizard": "prepared_spellbook",
    "bard": "known",
    "sorcerer": "known",
    "warlock": "known_pact",
    "ranger": "known_half",  # Note: in 2024 Rangers prepare spells; supported in both!
    "eldritch knight": "known_third",
    "arcane trickster": "known_third",
}

# Subclass bonus spells for D&D 5e (SRD domains, oaths, patrons, and circles)
SUBCLASS_SPELL_LISTS: Dict[str, List[str]] = {
    # Cleric Domains
    "life domain": ["Bless", "Cure Wounds", "Lesser Restoration", "Spiritual Weapon", "Beacon of Hope", "Revivify", "Death Ward", "Guardian of Faith", "Mass Cure Wounds", "Raise Dead"],
    "life": ["Bless", "Cure Wounds", "Lesser Restoration", "Spiritual Weapon", "Beacon of Hope", "Revivify", "Death Ward", "Guardian of Faith", "Mass Cure Wounds", "Raise Dead"],
    "light domain": ["Burning Hands", "Faerie Fire", "Flaming Sphere", "Scorching Ray", "Daylight", "Fireball", "Guardian of Faith", "Wall of Fire", "Flame Strike", "Scrying"],
    "light": ["Burning Hands", "Faerie Fire", "Flaming Sphere", "Scorching Ray", "Daylight", "Fireball", "Guardian of Faith", "Wall of Fire", "Flame Strike", "Scrying"],
    "nature domain": ["Animal Friendship", "Speak with Animals", "Barkskin", "Spike Growth", "Plant Growth", "Wind Wall", "Dominate Beast", "Grasping Vine", "Insect Plague", "Tree Stride"],
    "nature": ["Animal Friendship", "Speak with Animals", "Barkskin", "Spike Growth", "Plant Growth", "Wind Wall", "Dominate Beast", "Grasping Vine", "Insect Plague", "Tree Stride"],
    "tempest domain": ["Fog Cloud", "Thunderwave", "Gust of Wind", "Shatter", "Call Lightning", "Sleet Storm", "Control Water", "Ice Storm", "Destructive Wave", "Insect Plague"],
    "tempest": ["Fog Cloud", "Thunderwave", "Gust of Wind", "Shatter", "Call Lightning", "Sleet Storm", "Control Water", "Ice Storm", "Destructive Wave", "Insect Plague"],
    "trickery domain": ["Charm Person", "Disguise Self", "Mirror Image", "Pass without Trace", "Blink", "Dispel Magic", "Dimension Door", "Polymorph", "Dominate Person", "Modify Memory"],
    "trickery": ["Charm Person", "Disguise Self", "Mirror Image", "Pass without Trace", "Blink", "Dispel Magic", "Dimension Door", "Polymorph", "Dominate Person", "Modify Memory"],
    "war domain": ["Divine Favor", "Shield of Faith", "Magic Weapon", "Spiritual Weapon", "Crusader's Mantle", "Spirit Guardians", "Freedom of Movement", "Stoneskin", "Flame Strike", "Hold Monster"],
    "war": ["Divine Favor", "Shield of Faith", "Magic Weapon", "Spiritual Weapon", "Crusader's Mantle", "Spirit Guardians", "Freedom of Movement", "Stoneskin", "Flame Strike", "Hold Monster"],

    # Paladin Oaths
    "oath of devotion": ["Protection from Evil and Good", "Sanctuary", "Lesser Restoration", "Zone of Truth", "Beacon of Hope", "Dispel Magic", "Freedom of Movement", "Guardian of Faith", "Commune", "Flame Strike"],
    "devotion": ["Protection from Evil and Good", "Sanctuary", "Lesser Restoration", "Zone of Truth", "Beacon of Hope", "Dispel Magic", "Freedom of Movement", "Guardian of Faith", "Commune", "Flame Strike"],
    "oath of vengeance": ["Bane", "Hunter's Mark", "Hold Person", "Misty Step", "Haste", "Protection from Energy", "Banishment", "Dimension Door", "Hold Monster", "Scrying"],
    "vengeance": ["Bane", "Hunter's Mark", "Hold Person", "Misty Step", "Haste", "Protection from Energy", "Banishment", "Dimension Door", "Hold Monster", "Scrying"],
    "oath of the ancients": ["Ensnaring Strike", "Speak with Animals", "Moonbeam", "Misty Step", "Plant Growth", "Protection from Energy", "Ice Storm", "Stoneskin", "Commune with Nature", "Tree Stride"],
    "ancients": ["Ensnaring Strike", "Speak with Animals", "Moonbeam", "Misty Step", "Plant Growth", "Protection from Energy", "Ice Storm", "Stoneskin", "Commune with Nature", "Tree Stride"],

    # Warlock Patrons
    "the fiend": ["Burning Hands", "Command", "Blindness/Deafness", "Scorching Ray", "Fireball", "Stinking Cloud", "Fire Shield", "Wall of Fire", "Flame Strike", "Hallow"],
    "fiend": ["Burning Hands", "Command", "Blindness/Deafness", "Scorching Ray", "Fireball", "Stinking Cloud", "Fire Shield", "Wall of Fire", "Flame Strike", "Hallow"],
    "the archfey": ["Faerie Fire", "Sleep", "Calm Emotions", "Phantasmal Force", "Blink", "Plant Growth", "Dominate Beast", "Greater Invisibility", "Dominate Person", "Seeming"],
    "archfey": ["Faerie Fire", "Sleep", "Calm Emotions", "Phantasmal Force", "Blink", "Plant Growth", "Dominate Beast", "Greater Invisibility", "Dominate Person", "Seeming"],
    "the great old one": ["Dissonant Whispers", "Tasha's Hideous Laughter", "Hideous Laughter", "Detect Thoughts", "Phantasmal Force", "Clairvoyance", "Sending", "Dominate Beast", "Evard's Black Tentacles", "Black Tentacles", "Dominate Person", "Telekinesis"],
    "great old one": ["Dissonant Whispers", "Tasha's Hideous Laughter", "Hideous Laughter", "Detect Thoughts", "Phantasmal Force", "Clairvoyance", "Sending", "Dominate Beast", "Evard's Black Tentacles", "Black Tentacles", "Dominate Person", "Telekinesis"],

    # Druid Circles
    "circle of the land": ["Mirror Image", "Misty Step", "Barkskin", "Spider Climb", "Invisibility", "Pass without Trace", "Lightning Bolt", "Darkness", "Acid Arrow", "Web", "Call Lightning", "Plant Growth", "Haste", "Water Walk", "Stinking Cloud", "Gaseous Form", "Control Water", "Freedom of Movement", "Divination", "Stone Shape", "Greater Invisibility", "Cloudkill", "Insect Plague", "Wall of Stone"],
    "land": ["Mirror Image", "Misty Step", "Barkskin", "Spider Climb", "Invisibility", "Pass without Trace", "Lightning Bolt", "Darkness", "Acid Arrow", "Web", "Call Lightning", "Plant Growth", "Haste", "Water Walk", "Stinking Cloud", "Gaseous Form", "Control Water", "Freedom of Movement", "Divination", "Stone Shape", "Greater Invisibility", "Cloudkill", "Insect Plague", "Wall of Stone"],
}


@lru_cache(maxsize=1)
def _load_srd_spells() -> List[Dict[str, Any]]:
    path = os.path.join(_DATA_DIR, "srd_spells.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _all_spells(homebrew_mgr: Optional[HomebrewManager] = None) -> List[Dict[str, Any]]:
    mgr = homebrew_mgr or get_default_homebrew_manager()
    srd = [dict(x, compendium_type="spells") for x in _load_srd_spells()]
    hb = mgr.list_entries("spells")
    if not hb:
        return srd
    by_idx = {x["index"]: x for x in srd}
    for h in hb:
        by_idx[h["index"]] = h
    return list(by_idx.values())


def _normalize_class_query(class_name: str) -> str:
    low = (class_name or "").strip().lower()
    if "eldritch knight" in low:
        return "Wizard"
    if "arcane trickster" in low:
        return "Wizard"
    for c in CASTER_CLASSES:
        if c.lower() in low:
            return c
    return class_name.strip().title()


def list_spells(
    edition: str = "all",
    class_name: Optional[str] = None,
    level: Optional[int] = None,
    max_level: Optional[int] = None,
    school: Optional[str] = None,
    ritual_only: bool = False,
    concentration: Optional[bool] = None,
    homebrew_only: bool = False,
    query: str = "",
    homebrew_mgr: Optional[HomebrewManager] = None,
) -> List[Dict[str, Any]]:
    spells = _all_spells(homebrew_mgr)
    ed = (edition or "all").lower().strip()
    raw_q = (query or "").lower().strip()
    q = PHB_TO_SRD_ALIASES.get(raw_q, raw_q) if raw_q else ""
    cls_norm = _normalize_class_query(class_name) if class_name else None
    school_f = (school or "").lower().strip()

    results = []
    for sp in spells:
        if homebrew_only and not sp.get("is_homebrew"):
            continue
        if ed in ("2014", "2024") and ed not in sp.get("editions", ["2014", "2024"]):
            continue
        if cls_norm and cls_norm not in sp.get("classes", []):
            continue
        sp_lvl = int(sp.get("level", 0))
        if level is not None and sp_lvl != int(level):
            continue
        if max_level is not None and sp_lvl > int(max_level):
            continue
        if school_f and school_f != sp.get("school", "").lower():
            continue
        if ritual_only and not sp.get("ritual"):
            continue
        if concentration is not None and bool(sp.get("concentration")) != bool(concentration):
            continue
        if q:
            haystack = " ".join([
                sp.get("name", ""),
                sp.get("school", ""),
                " ".join(sp.get("classes", [])),
                sp.get("damage", ""),
                sp.get("description", ""),
            ]).lower()
            if q not in haystack and (raw_q == q or raw_q not in haystack):
                continue
        results.append(sp)

    return sorted(
        results,
        key=lambda x: (
            query_match_rank(x, q) if q else 0,
            not x.get("is_homebrew", False),
            int(x.get("level", 0)),
            x.get("name", "").lower(),
        ),
    )


def get_spell(
    name_or_index: str,
    edition: str = "2024",
    homebrew_mgr: Optional[HomebrewManager] = None,
) -> Optional[Dict[str, Any]]:
    raw = re.sub(r"\s*(?:\[.*?\]|\(.*?\))", "", str(name_or_index or "")).strip().lower()
    if not raw:
        return None
    target = PHB_TO_SRD_ALIASES.get(raw, raw)
    spells = _all_spells(homebrew_mgr)

    matched: Optional[Dict[str, Any]] = None
    for sp in spells:
        if sp.get("index", "").lower() == target or sp.get("name", "").lower() == target:
            matched = dict(sp)
            break
    if not matched:
        for sp in spells:
            if target in sp.get("name", "").lower():
                matched = dict(sp)
                break

    if matched:
        ed = (edition or "2024").strip()
        if ed == "2024" and matched.get("description_2024"):
            matched["active_description"] = matched["description_2024"]
        elif ed == "2014" and matched.get("description_2014"):
            matched["active_description"] = matched["description_2014"]
        else:
            matched["active_description"] = matched.get("description", "")
    return matched


def get_spells_for_class(
    class_name: str,
    max_spell_level: int = 9,
    edition: str = "all",
    include_cantrips: bool = True,
    homebrew_mgr: Optional[HomebrewManager] = None,
) -> List[Dict[str, Any]]:
    results = list_spells(
        edition=edition,
        class_name=class_name,
        max_level=max_spell_level,
        homebrew_mgr=homebrew_mgr,
    )
    if not include_cantrips:
        results = [s for s in results if int(s.get("level", 0)) > 0]
    return results


def _spell_level_label(lvl: int) -> str:
    if lvl <= 0:
        return "Cantrip"
    if 11 <= (lvl % 100) <= 13:
        return f"{lvl}th Level"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(lvl % 10, "th")
    return f"{lvl}{suffix} Level"


def format_spell_for_sheet(
    spell: Dict[str, Any],
    spell_atk: str = "",
    spell_dc: str = "",
    prepared: bool = True,
    is_always_prepared: bool = False,
) -> Dict[str, Any]:
    """Converts a compendium spell entry into a character-sheet spell entry."""
    lvl = int(spell.get("level", 0))
    is_cantrip = (lvl == 0)
    atk_or_dc = ""
    if spell.get("dc_type") and spell_dc:
        atk_or_dc = f"DC {spell_dc} {spell['dc_type']}"
    elif spell.get("attack_type") and spell_atk:
        atk_or_dc = spell_atk

    casting_time = spell.get("casting_time", "1 action")
    duration = spell.get("duration", "")

    return {
        "name": spell["name"],
        "level": spell.get("level_label") or _spell_level_label(lvl),
        "atk_bonus": atk_or_dc,
        "damage": spell.get("damage", ""),
        "range": spell.get("range", "Self"),
        "school": spell.get("school", ""),
        "casting_time": casting_time,
        "cast_time": casting_time,
        "duration": duration,
        "concentration": bool(spell.get("concentration", False)),
        "is_ritual": bool(spell.get("ritual", False)),
        "is_cantrip": is_cantrip,
        "prepared": True if (is_cantrip or is_always_prepared) else bool(prepared),
        "is_always_prepared": bool(is_always_prepared),
        "notes": f"{duration}".strip(),
        "compendium_index": spell.get("index", ""),
    }


def validate_character_spell_assignment(
    char_data: Dict[str, Any],
    spell_entry_or_name: Union[Dict[str, Any], str],
    edition: str = "2024",
    bypass_class_check: bool = False,
    homebrew_mgr: Optional[HomebrewManager] = None,
) -> Dict[str, Any]:
    """
    Validates whether a D&D character can learn or be assigned a spell based on class spell lists,
    third-caster subclasses (Eldritch Knight, Arcane Trickster), subclass domain/patron/oath spells,
    and Bard Magical Secrets.

    Returns:
        {
            "can_learn": bool,
            "reason": str,
            "is_out_of_class": bool,
            "is_magical_secrets": bool,
            "matched_via": str,
            "allowed_spell_classes": List[str],
            "character_classes": List[str],
        }
    """
    if bypass_class_check:
        return {
            "can_learn": True,
            "reason": "Class restriction bypassed by DM or player override.",
            "is_out_of_class": False,
            "is_magical_secrets": False,
            "matched_via": "bypass",
            "allowed_spell_classes": [],
            "character_classes": [],
        }

    # Fetch compendium spell record
    if isinstance(spell_entry_or_name, dict) and "name" in spell_entry_or_name:
        sp = spell_entry_or_name
    else:
        sp = get_spell(str(spell_entry_or_name), edition=edition, homebrew_mgr=homebrew_mgr)

    if not sp:
        return {
            "can_learn": False,
            "reason": f"Spell '{spell_entry_or_name}' not found in compendium.",
            "is_out_of_class": False,
            "is_magical_secrets": False,
            "matched_via": "not_found",
            "allowed_spell_classes": [],
            "character_classes": [],
        }

    # Non-D&D ruleset bypass (Call of Cthulhu, Cyberpunk, Pathfinder)
    ruleset = str(
        char_data.get("ruleset")
        or char_data.get("native_system")
        or char_data.get("native_genre")
        or ""
    ).lower()
    if any(r in ruleset for r in ("coc", "cthulhu", "cyberpunk", "pf2e", "pathfinder")):
        return {
            "can_learn": True,
            "reason": f"Non-D&D ruleset ({ruleset}).",
            "is_out_of_class": False,
            "is_magical_secrets": False,
            "matched_via": "non_dnd",
            "allowed_spell_classes": [],
            "character_classes": [],
        }

    # Parse character's class breakdown & subclasses
    from core.compendiums.dnd_advancement import parse_character_classes, format_class_summary
    class_levels, subclasses = parse_character_classes(char_data)
    char_name = str(char_data.get("name") or "Character").strip()
    char_class_names = list(class_levels.keys())
    char_class_summary = format_class_summary(class_levels)

    # Spell's permitted classes
    raw_classes = sp.get("classes", [])
    spell_classes = [str(c).strip().title() for c in raw_classes if str(c).strip()]
    if not spell_classes:
        # Universal / unclassified spell
        return {
            "can_learn": True,
            "reason": "Universal spell with no class restriction.",
            "is_out_of_class": False,
            "is_magical_secrets": False,
            "matched_via": "universal",
            "allowed_spell_classes": [],
            "character_classes": char_class_names,
        }

    # 1. Direct class match (e.g. Bard taking Bard spell, Sorcerer taking Sorcerer spell)
    matching_classes = [c for c in char_class_names if c in spell_classes]
    if matching_classes:
        return {
            "can_learn": True,
            "reason": f"Spell is on the {', '.join(matching_classes)} spell list.",
            "is_out_of_class": False,
            "is_magical_secrets": False,
            "matched_via": "class_spell_list",
            "allowed_spell_classes": spell_classes,
            "character_classes": char_class_names,
        }

    # 2. Third-Caster Martial Archetypes (Eldritch Knight Fighter, Arcane Trickster Rogue use Wizard spell list)
    fighter_sub = str(subclasses.get("Fighter") or "").lower()
    rogue_sub = str(subclasses.get("Rogue") or "").lower()
    arch_str = str(char_data.get("archetype") or char_data.get("class_and_level") or "").lower()

    if "Fighter" in class_levels and ("eldritch knight" in fighter_sub or "eldritch knight" in arch_str):
        if "Wizard" in spell_classes:
            return {
                "can_learn": True,
                "reason": "Fighter (Eldritch Knight) casts from the Wizard spell list.",
                "is_out_of_class": False,
                "is_magical_secrets": False,
                "matched_via": "subclass_spell_list",
                "allowed_spell_classes": spell_classes,
                "character_classes": char_class_names,
            }

    if "Rogue" in class_levels and ("arcane trickster" in rogue_sub or "arcane trickster" in arch_str):
        if "Wizard" in spell_classes:
            return {
                "can_learn": True,
                "reason": "Rogue (Arcane Trickster) casts from the Wizard spell list.",
                "is_out_of_class": False,
                "is_magical_secrets": False,
                "matched_via": "subclass_spell_list",
                "allowed_spell_classes": spell_classes,
                "character_classes": char_class_names,
            }

    # 3. Subclass Domain / Oath / Patron / Circle bonus spell lists
    sp_name_low = sp["name"].strip().lower()
    all_sub_strings = [v.lower() for v in subclasses.values()] + [
        str(char_data.get("subclass") or "").lower(),
        arch_str,
    ]
    for sub_key, bonus_list in SUBCLASS_SPELL_LISTS.items():
        if any(sub_key in s for s in all_sub_strings):
            if any(b.strip().lower() == sp_name_low for b in bonus_list):
                return {
                    "can_learn": True,
                    "reason": f"Granted by {sub_key.title()} subclass feature.",
                    "is_out_of_class": False,
                    "is_magical_secrets": False,
                    "matched_via": "subclass_bonus",
                    "allowed_spell_classes": spell_classes,
                    "character_classes": char_class_names,
                }

    # 4. Bard Magical Secrets
    if "Bard" in class_levels:
        b_lvl = class_levels["Bard"]
        b_sub = (subclasses.get("Bard") or char_data.get("subclass") or arch_str).lower()
        is_lore = "lore" in b_sub
        ed = "2014" if str(edition).strip() == "2014" else "2024"
        if ed == "2014":
            # 2014: Lore Bard at level 6+, all Bards at level 10+
            if b_lvl >= 10 or (is_lore and b_lvl >= 6):
                return {
                    "can_learn": True,
                    "reason": f"Eligible via Bard Magical Secrets ({'Lore Bard L6+' if (is_lore and b_lvl < 10) else 'L10+'}).",
                    "is_out_of_class": False,
                    "is_magical_secrets": True,
                    "matched_via": "magical_secrets",
                    "allowed_spell_classes": spell_classes,
                    "character_classes": char_class_names,
                }
        else:
            # 2024: Level 10+ Bard Magical Secrets prepares from Cleric, Druid, and Wizard lists
            if b_lvl >= 10 and any(sc in spell_classes for sc in ("Cleric", "Druid", "Wizard")):
                return {
                    "can_learn": True,
                    "reason": "Eligible via 2024 Bard Magical Secrets (Cleric/Druid/Wizard spell lists).",
                    "is_out_of_class": False,
                    "is_magical_secrets": True,
                    "matched_via": "magical_secrets",
                    "allowed_spell_classes": spell_classes,
                    "character_classes": char_class_names,
                }

    # 5. Ineligible / Out-of-Class
    has_caster = (
        any(c in CASTER_CLASSES for c in char_class_names)
        or ("Fighter" in class_levels and ("eldritch knight" in fighter_sub or "eldritch knight" in arch_str))
        or ("Rogue" in class_levels and ("arcane trickster" in rogue_sub or "arcane trickster" in arch_str))
    )
    if not has_caster:
        msg = f"{char_name} is a {char_class_summary} (non-spellcaster) and cannot learn spells."
    else:
        msg = (
            f"Cannot assign '{sp['name']}': it is on the [{', '.join(spell_classes)}] spell list(s), "
            f"but {char_name} is a {char_class_summary}."
        )

    return {
        "can_learn": False,
        "reason": msg,
        "is_out_of_class": True,
        "is_magical_secrets": False,
        "matched_via": "none",
        "allowed_spell_classes": spell_classes,
        "character_classes": char_class_names,
    }
