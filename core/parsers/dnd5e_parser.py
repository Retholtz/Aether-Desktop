"""
core/parsers/dnd5e_parser.py - Extended Ingestion & Schema Normalizer (Schema 2.1)
Extracts spell slots, prepared spell lists, limited resource meters, and roleplay anchors.
"""
import io
import re
import html
from dataclasses import asdict, is_dataclass
from typing import Dict, Any, List, Optional, Tuple
from core.parsers.base_parser import BaseSheetParser, UniversalCharacterCore
from core.parsers.spell_registry import resolve_dnd_stat, parse_structured_spells, sanitize_character_weapons, DND_SPELL_REGISTRY
from core.logger import get_logger

logger = get_logger("DnD5eParser")

# Known Subclass Spells that are ALWAYS prepared and bypass the preparation cap
ALWAYS_PREPARED_SUBCLASS_SPELLS = {
    # Paladin Oaths
    "devotion": ["protection from evil and good", "sanctuary", "lesser restoration", "zone of truth", "beacon of hope", "dispel magic", "freedom of movement", "guardian of faith", "commune", "flame strike"],
    "vengeance": ["bane", "hunter's mark", "hold person", "misty step", "haste", "protection from energy", "banishment", "dimension door", "hold monster", "scrying"],
    "ancients": ["ensnaring strike", "speak with animals", "moonbeam", "misty step", "plant growth", "protection from energy", "ice storm", "stoneskin", "commune with nature", "tree stride"],
    # Druid Circles
    "land": ["barkskin", "mirror image", "misty step", "water walk", "lightning bolt", "freedom of movement", "solid fog", "divination"],
    # Cleric Domains
    "life": ["bless", "cure wounds", "lesser restoration", "spiritual weapon", "beacon of hope", "revivify", "death ward", "guardian of faith", "mass cure wounds", "raise dead"],
    "light": ["burning hands", "faerie fire", "flaming sphere", "scorching ray", "daylight", "fireball", "guardian of faith", "wall of fire", "flame strike", "scrying"],
    "death": ["false life", "ray of sickness", "blindness/deafness", "ray of enfeeblement", "animate dead", "vampiric touch", "blight", "death ward", "antilife shell", "cloudkill"],
    "trickery": ["charm person", "disguise self", "mirror image", "pass without trace", "blink", "dispel magic", "dimension door", "polymorph", "dominate person", "modify memory"]
}

WARLOCK_PACT_SLOTS = {
    1:  {"count": 1, "level": 1}, 2:  {"count": 2, "level": 1},
    3:  {"count": 2, "level": 2}, 4:  {"count": 2, "level": 2},
    5:  {"count": 2, "level": 3}, 6:  {"count": 2, "level": 3},
    7:  {"count": 2, "level": 4}, 8:  {"count": 2, "level": 4},
    9:  {"count": 2, "level": 5}, 10: {"count": 2, "level": 5},
    11: {"count": 3, "level": 5}, 12: {"count": 3, "level": 5},
    13: {"count": 3, "level": 5}, 14: {"count": 3, "level": 5},
    15: {"count": 3, "level": 5}, 16: {"count": 3, "level": 5},
    17: {"count": 4, "level": 5}, 18: {"count": 4, "level": 5},
    19: {"count": 4, "level": 5}, 20: {"count": 4, "level": 5}
}

MULTICLASS_SLOT_TABLE = {
    1:  {1: 2},
    2:  {1: 3},
    3:  {1: 4, 2: 2},
    4:  {1: 4, 2: 3},
    5:  {1: 4, 2: 3, 3: 2},
    6:  {1: 4, 2: 3, 3: 3},
    7:  {1: 4, 2: 3, 3: 3, 4: 1},
    8:  {1: 4, 2: 3, 3: 3, 4: 2},
    9:  {1: 4, 2: 3, 3: 3, 4: 3, 5: 1},
    10: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
    11: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1},
    12: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1},
    13: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1},
    14: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1},
    15: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1},
    16: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1},
    17: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1, 9: 1},
    18: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 1, 7: 1, 8: 1, 9: 1},
    19: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 1, 8: 1, 9: 1},
    20: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 2, 8: 1, 9: 1}
}


def parse_multiclass_string(class_str: str) -> List[Dict[str, Any]]:
    """
    Parses complex class strings into discrete class entries.
    Example: 'Paladin 6 / Sorcerer 4' -> [{'class': 'Paladin', 'level': 6}, {'class': 'Sorcerer', 'level': 4}]
    """
    clean = re.sub(r'[\(\[].*?[\)\]]', '', class_str).strip()
    segments = [s.strip() for s in clean.split('/') if s.strip()]
    parsed = []
    for seg in segments:
        m = re.search(r'([A-Za-z\s]+?)\s*(\d+)', seg)
        if m:
            c_name = m.group(1).strip()
            c_lvl = int(m.group(2))
            parsed.append({"class": c_name, "level": c_lvl})
        elif seg.strip():
            parsed.append({"class": seg.strip(), "level": 1})
    return parsed if parsed else [{"class": "Fighter", "level": 1}]


def calculate_multiclass_spell_slots(
    classes: List[Dict[str, Any]],
    edition: str = "2014",
) -> Dict[str, Dict[str, Any]]:
    """Calculates unified spell slots according to the official multiclass spell matrix (2014 & 2024)."""
    ecl = 0
    has_spellcaster = False
    is_2024 = str(edition or "2014").strip() == "2024"

    for c in classes:
        name = c["class"].lower()
        lvl = c["level"]

        if any(fc in name for fc in ["wizard", "cleric", "druid", "sorcerer", "bard"]):
            ecl += lvl
            has_spellcaster = True
        elif any(hc in name for hc in ["paladin", "ranger"]):
            ecl += ((lvl + 1) // 2) if is_2024 else (lvl // 2)
            has_spellcaster = True
        elif "artificer" in name:
            ecl += (lvl + 1) // 2
            has_spellcaster = True
        elif any(tc in name for tc in ["eldritch knight", "arcane trickster"]):
            ecl += ((lvl + 2) // 3) if is_2024 else (lvl // 3)
            has_spellcaster = True

    slots = {f"level_{r}": {"current": 0, "max": 0, "reset_on": "long_rest"} for r in range(1, 10)}
    if not has_spellcaster or ecl == 0:
        return slots

    clamped_ecl = min(20, max(1, ecl))
    row = MULTICLASS_SLOT_TABLE.get(clamped_ecl, {})
    for rank, total in row.items():
        slots[f"level_{rank}"] = {
            "current": total,
            "max": total,
            "reset_on": "long_rest"
        }
    return slots


def extract_multiclass_hit_dice(classes: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], str]:
    """Generates discrete hit dice meters for multiclass entities."""
    meters = {}
    die_labels = []

    for c in classes:
        name = c["class"]
        lvl = c["level"]
        c_lower = name.lower()

        if "barbarian" in c_lower:
            die = 12
        elif any(k in c_lower for k in ["fighter", "paladin", "ranger", "blood hunter"]):
            die = 10
        elif any(k in c_lower for k in ["sorcerer", "wizard"]):
            die = 6
        else:
            die = 8

        pool_key = f"hit_dice_{c_lower.replace(' ', '_')}"
        label = f"Hit Dice ({lvl}d{die})"
        die_labels.append(f"{lvl}d{die}")

        meters[pool_key] = {
            "label": label,
            "current": lvl,
            "max": lvl,
            "reset_on": "long_rest"
        }

    return meters, ", ".join(die_labels)


def extract_mystic_arcanum_meters(warlock_level: int) -> Dict[str, Any]:
    """Generates discrete 1/Long Rest meters for Warlock Mystic Arcanum spells."""
    meters = {}
    if warlock_level >= 11:
        meters["mystic_arcanum_6th"] = {
            "label": "Mystic Arcanum (6th Level)",
            "current": 1,
            "max": 1,
            "reset_on": "long_rest"
        }
    if warlock_level >= 13:
        meters["mystic_arcanum_7th"] = {
            "label": "Mystic Arcanum (7th Level)",
            "current": 1,
            "max": 1,
            "reset_on": "long_rest"
        }
    if warlock_level >= 15:
        meters["mystic_arcanum_8th"] = {
            "label": "Mystic Arcanum (8th Level)",
            "current": 1,
            "max": 1,
            "reset_on": "long_rest"
        }
    if warlock_level >= 17:
        meters["mystic_arcanum_9th"] = {
            "label": "Mystic Arcanum (9th Level)",
            "current": 1,
            "max": 1,
            "reset_on": "long_rest"
        }
    return meters


def heal_truncated_field(acro_value: str, full_pdf_text: str, next_section_headers: List[str]) -> str:
    """
    Detects truncated AcroForm strings and stitches trailing sentences 
    from the raw PDF text content stream.
    """
    if not acro_value or len(acro_value.strip()) < 15:
        return acro_value

    clean = acro_value.strip()
    terminal_tokens = ('.', '!', '?', '"', "'", ')', '}')
    
    # Check if field was cut off mid-sentence
    if clean.endswith(terminal_tokens):
        return clean

    if not full_pdf_text:
        return clean

    # Take trailing anchor segment (last 25 characters) to locate in raw PDF stream
    anchor = clean[-25:].replace(r"\(", "(").replace(r"\)", ")")
    escaped_anchor = re.escape(anchor)

    # Build termination regex from candidate headers
    if next_section_headers:
        stop_pattern = "|".join([rf'\n\s*{re.escape(h)}' for h in next_section_headers])
        search_regex = rf'{escaped_anchor}([\s\S]*?)(?={stop_pattern}|$)'
    else:
        search_regex = rf'{escaped_anchor}([\s\S]*?)$'

    match = re.search(search_regex, full_pdf_text, re.IGNORECASE)
    if match:
        extension = match.group(1).strip()
        # Weld words broken across PDF line wraps
        extension = re.sub(r'([a-zA-Z])-\s+([a-zA-Z])', r'\1\2', extension)
        extension = re.sub(r'\s+', ' ', extension)
        return f"{clean} {extension}".strip()

    return clean


class InventoryList(list):
    """List subclass that matches item names when checked with `item_name in inventory` and compares equal to both list of dicts and list of names."""
    def __contains__(self, item: Any) -> bool:
        if super().__contains__(item):
            return True
        if isinstance(item, str):
            return any(isinstance(x, dict) and x.get("name") == item for x in self)
        return False

    def __eq__(self, other: Any) -> bool:
        if super().__eq__(other):
            return True
        if isinstance(other, list) and all(isinstance(x, str) for x in other):
            return [x.get("name") if isinstance(x, dict) else x for x in self] == other
        return False

BANNED_TRAIT_HEADERS = {
    "hit points", "proficiencies", "core sorcerer traits", "core wizard traits",
    "spellcasting", "additional cleric spells", "special", "actions", "bonus actions",
    "action", "reactions", "reaction"
}


def extract_clean_trait_name(trait_str: str) -> str:
    """
    Strips leading bullets, symbols, and formatting before isolating the trait name.
    Avoids generating empty strings from bulleted sub-lists.
    """
    if not trait_str:
        return ""
    # Strip leading bullets, asterisks, dashes, pipes, and whitespace
    clean = re.sub(r'^[\*\s\u2022\|\-]+', '', trait_str).strip()
    if not clean or clean.startswith("==="):
        return ""
    
    # Isolate the name prior to source citations (•), tables (|), or action timers (:)
    name_candidate = re.split(r'[•|:]', clean)[0].strip()
    
    # If the delimiter was at the front or name is empty, fallback to the first sentence/line
    if not name_candidate:
        name_candidate = clean.split('\n')[0][:40].strip()
        
    return name_candidate


def normalize_spell_level(lvl_val: Any) -> int:
    """Converts a spell level token ('Cantrip', '1st Level', '2', 0) to a deterministic integer."""
    if isinstance(lvl_val, int):
        return lvl_val
    s = str(lvl_val or "").strip().lower()
    if "cantrip" in s or s == "0":
        return 0
    m = re.search(r'\d+', s)
    return int(m.group(0)) if m else 1


def sanitize_inventory(raw_inventory: List[Any]) -> List[Any]:
    """Stacks identical inventory items into a single row with combined quantities, always returning uniform structured dicts."""
    if not raw_inventory:
        return []
    stacked = {}
    for item in raw_inventory:
        if isinstance(item, str):
            name = item.strip()
            raw_qty = "1"
            weight = "--"
            category = "Gear"
            notes = ""
            genre = "fantasy_d20"
            status = "active"
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            raw_qty = str(item.get("qty", "1")).strip()
            weight = item.get("weight", "--")
            category = item.get("category", "Gear")
            notes = item.get("notes", "")
            genre = item.get("genre")
            status = item.get("status")
        else:
            continue

        if not name:
            continue

        # Clean escaped parentheses and heal unclosed parentheses
        name = name.replace(r"\(", "(").replace(r"\)", ")").strip()
        if "(" in name and ")" not in name:
            name += ")"

        norm_name = name
        if re.search(r'^oil\b', norm_name, re.IGNORECASE):
            norm_name = "Oil"
        elif re.search(r'^holy water\b', norm_name, re.IGNORECASE):
            norm_name = "Holy Water"
        elif re.search(r'^(?:lamp|lantern)\b', norm_name, re.IGNORECASE):
            norm_name = "Lamp / Lantern"
        elif re.search(r'^ink\b', norm_name, re.IGNORECASE):
            norm_name = "Ink & Pen"
        elif re.search(r'^potion of healing\b', norm_name, re.IGNORECASE):
            norm_name = "Potion of Healing"

        key = norm_name.lower()
        qty = int(raw_qty) if raw_qty.isdigit() else 1

        if key in stacked:
            stacked[key]["qty"] += qty
            if stacked[key]["weight"] in ("--", "", None) and weight not in ("--", "", None):
                stacked[key]["weight"] = weight
        else:
            entry = {
                "name": norm_name,
                "qty": qty,
                "weight": weight if weight not in (None, "") else "--",
                "category": category,
                "notes": notes
            }
            if genre:
                entry["genre"] = genre
            if status:
                entry["status"] = status
            stacked[key] = entry

    # Format quantities back to strings for UI
    results = []
    for entry in stacked.values():
        entry["qty"] = str(entry["qty"])
        results.append(entry)
    return InventoryList(results)


def clean_traits_and_headers(raw_traits: List[Any]) -> List[Any]:
    """Strips non-trait section titles, empty names, and source citations exported by D&D Beyond."""
    cleaned = []
    for t in raw_traits:
        if isinstance(t, dict):
            t_name = str(t.get("name", "")).strip()
            base_name = re.sub(r'^[\*\s\u2022\|\-]+', '', t_name)
            base_name = re.sub(r'\s*[•|:].*$', '', base_name).strip().lower()
            
            # Skip empty names or banned section titles
            if not base_name or base_name in BANNED_TRAIT_HEADERS or base_name.startswith("==="):
                continue
            cleaned.append(t)
        elif isinstance(t, str):
            t_clean = t.strip()
            base_name = re.sub(r'^[\*\s\u2022\|\-]+', '', t_clean)
            base_name = re.sub(r'\s*[•|:].*$', '', base_name).strip().lower()
            if not base_name or base_name in BANNED_TRAIT_HEADERS or base_name.startswith("==="):
                continue
            cleaned.append(t_clean)
    return cleaned


def clean_backstory_text(text: str) -> str:
    """Cleans PDF line breaks, PostScript continuations, and broken words into clean prose."""
    if not text:
        return ""
    s = str(text)
    s = html.unescape(s)
    s = s.replace(r"\(", "(").replace(r"\)", ")")
    # Weld words broken across PDF line breaks with backslash (e.g. yo\ ung, yo\\\rung)
    s = re.sub(r'([a-zA-Z])\\+[\r\n\s]+([a-zA-Z])', r'\1\2', s)
    # Clean escaped line breaks
    s = re.sub(r'\\+[\r\n]+', '\n', s)
    s = re.sub(r'\\+([a-zA-Z0-9])', r'\1', s)
    # Strip any remaining backslashes
    s = s.replace('\\', '')
    # Normalize line endings and multiple blank lines
    s = s.replace('\r\n', '\n').replace('\r', '\n')
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()


class DnD5eParser(BaseSheetParser):
    def identify_system(self, form_fields: Dict[str, Any], raw_text: str) -> bool:
        signatures = [
            "CharacterName", "ProfBonus", "Inspiration", "PassivePerception",
            "SavingThrows", "Passive1", "STRmod", "DEXmod", "ClassLevel", "CLASS  LEVEL",
            "SpellSaveDC0", "PactMagic"
        ]
        matches = sum(1 for s in signatures if any(s.lower() in k.lower() for k in form_fields.keys()))
        return (
            matches >= 2
            or ("D&D BEYOND" in raw_text.upper() and "SAVING THROWS" in raw_text.upper())
            or ("CHARACTER NAME" in raw_text.upper() and "ARMOR CLASS" in raw_text.upper())
        )

    def parse_sheet(self, pdf_stream: Any) -> Dict[str, Any]:
        fields, text = self.extract_form_fields(pdf_stream)

        def _clean(val: Any) -> str:
            if not val:
                return ""
            v = str(val).replace(r"\(", "(").replace(r"\)", ")").rstrip("\\").strip()
            if v.endswith("\\"):
                v = v.rstrip("\\").strip()
            if v.endswith(")") and v.count("(") < v.count(")"):
                v = v[:-1].strip()
            return v

        # 1. Identity & Class Level Cleaning
        raw_name = (
            fields.get("CharacterName")
            or fields.get("Character Name")
            or fields.get("CharacterName2")
            or fields.get("CharacterName4")
            or self._regex_search(r'CHARACTER NAME\s*\n\s*([^\n]+)', text)
            or "Adventurer"
        )
        clean_name = _clean(raw_name)

        raw_class = (
            fields.get("ClassLevel")
            or fields.get("CLASS  LEVEL")
            or fields.get("CLASS  LEVEL2")
            or fields.get("Class & Level")
            or self._regex_search(r'CLASS & LEVEL\s*\n\s*([^\n]+)', text)
            or "Fighter 1"
        )
        raw_class_clean = _clean(raw_class)

        # Separate race and background if embedded in Class & Level parenthetical
        extracted_race = _clean(fields.get("RACE") or fields.get("RACE2") or fields.get("Race") or "")
        extracted_bg = _clean(fields.get("BACKGROUND") or fields.get("BACKGROUND2") or fields.get("Background") or "")
        if (not extracted_race or not extracted_bg) and "(" in raw_class_clean and ")" in raw_class_clean:
            p_match = re.search(r'\((.*?)\)', raw_class_clean)
            if p_match:
                parts = [p.strip() for p in p_match.group(1).split(",")]
                if not extracted_race and len(parts) > 0:
                    extracted_race = parts[0]
                if not extracted_bg and len(parts) > 1:
                    extracted_bg = parts[1]

        class_entries = parse_multiclass_string(raw_class_clean)
        total_level = sum(c["level"] for c in class_entries)
        clean_archetype = " / ".join(f"{c['class']} {c['level']}" for c in class_entries)
        clean_class = clean_archetype
        level = total_level

        # Segmented Hit Dice
        hd_meters, hit_dice_str = extract_multiclass_hit_dice(class_entries)
        hit_die = self._infer_hit_die(clean_class)
        hit_dice_display = hit_dice_str if len(class_entries) > 1 else f"{level}d{hit_die}"

        player_name = _clean(fields.get("PlayerName") or fields.get("PLAYER NAME") or fields.get("PLAYER NAME2")) or "Player"

        # 2. Ability Scores & Save DC
        str_val = resolve_dnd_stat(fields.get("STR"), fields.get("STRmod") or fields.get("STRmod ") or fields.get("STR Mod"), 10)
        dex_val = resolve_dnd_stat(fields.get("DEX"), fields.get("DEXmod") or fields.get("DEXmod ") or fields.get("DEX Mod"), 10)
        con_val = resolve_dnd_stat(fields.get("CON"), fields.get("CONmod") or fields.get("CONmod ") or fields.get("CON Mod"), 10)
        int_val = resolve_dnd_stat(fields.get("INT"), fields.get("INTmod") or fields.get("INTmod ") or fields.get("INT Mod"), 10)
        wis_val = resolve_dnd_stat(fields.get("WIS"), fields.get("WISmod") or fields.get("WISmod ") or fields.get("WIS Mod"), 10)
        cha_val = resolve_dnd_stat(fields.get("CHA"), fields.get("CHamod") or fields.get("CHAmod") or fields.get("CHAmod ") or fields.get("CHA Mod"), 10)

        # Direct dictionary fallback if resolve_dnd_stat returned default
        if str_val == 10 and fields.get("STR") and str(fields.get("STR")).isdigit():
            str_val = int(fields.get("STR"))
        if dex_val == 10 and fields.get("DEX") and str(fields.get("DEX")).isdigit():
            dex_val = int(fields.get("DEX"))
        if con_val == 10 and fields.get("CON") and str(fields.get("CON")).isdigit():
            con_val = int(fields.get("CON"))
        if int_val == 10 and fields.get("INT") and str(fields.get("INT")).isdigit():
            int_val = int(fields.get("INT"))
        if wis_val == 10 and fields.get("WIS") and str(fields.get("WIS")).isdigit():
            wis_val = int(fields.get("WIS"))
        if cha_val == 10 and fields.get("CHA") and str(fields.get("CHA")).isdigit():
            cha_val = int(fields.get("CHA"))

        int_mod = (int_val - 10) // 2
        dex_mod = (dex_val - 10) // 2
        str_mod = (str_val - 10) // 2
        con_mod = (con_val - 10) // 2
        wis_mod = (wis_val - 10) // 2
        cha_mod = (cha_val - 10) // 2

        prof_bonus_str = _clean(fields.get("ProfBonus")) or "+2"
        m_pb = re.search(r'[-+]?\d+', prof_bonus_str)
        prof_bonus = int(m_pb.group(0)) if m_pb else 2

        # Primary Spellcasting Ability & DC
        cls_lower = clean_class.lower()
        is_warlock = any("warlock" in c["class"].lower() for c in class_entries) or "warlock" in cls_lower
        is_druid = any("druid" in c["class"].lower() for c in class_entries) or "druid" in cls_lower
        is_paladin = any("paladin" in c["class"].lower() for c in class_entries) or "paladin" in cls_lower
        is_cleric = any("cleric" in c["class"].lower() for c in class_entries) or "cleric" in cls_lower
        is_sorcerer = any("sorcerer" in c["class"].lower() for c in class_entries) or "sorcerer" in cls_lower
        is_bard = any("bard" in c["class"].lower() for c in class_entries) or "bard" in cls_lower
        is_ranger = any("ranger" in c["class"].lower() for c in class_entries) or "ranger" in cls_lower
        is_blood_hunter = any("blood hunter" in c["class"].lower() for c in class_entries) or "blood hunter" in cls_lower
        subclass_context = f"{clean_class} {raw_class_clean} {text}".lower()

        spell_ability = _clean(fields.get("spellCastingAbility0") or fields.get("SpellcastingAbility") or fields.get("SpellAbility") or "")
        key_mod = int_mod
        if is_warlock or is_paladin or is_sorcerer or is_bard:
            spell_ability = "CHA"
            key_mod = cha_mod
        elif is_druid or is_cleric or is_ranger:
            spell_ability = "WIS"
            key_mod = wis_mod
        elif "artificer" in cls_lower or "wizard" in cls_lower:
            spell_ability = "INT"
            key_mod = int_mod
        elif is_blood_hunter and "profane soul" in subclass_context:
            spell_ability = "INT"
            key_mod = int_mod
        elif is_blood_hunter:
            spell_ability = ""
            key_mod = 0
        elif spell_ability.upper() == "CHA":
            key_mod = cha_mod
        elif spell_ability.upper() == "WIS":
            key_mod = wis_mod
        elif spell_ability.upper() == "INT":
            key_mod = int_mod
        else:
            spell_ability = ""
            key_mod = 0

        raw_dc = self._get_num(fields, ["SpellSaveDC0", "AbilitySaveDC", "SpellSaveDC", "spellSaveDC0"], default=0)
        if raw_dc == 0:
            dc_match = re.search(r'ABILITY SAVE DC\s*\n\s*(\d+)', text) or re.search(r'Spell DC\s*(\d+)', text)
            raw_dc = int(dc_match.group(1)) if dc_match else (8 + prof_bonus + key_mod if spell_ability else 0)

        raw_atk = self._get_num(fields, ["SpellAtkBonus0", "SpellAttackBonus", "spellAtkBonus0"], default=0)
        if raw_atk == 0:
            atk_match = re.search(r'Spell Attack\s*([+-]?\d+)', text)
            raw_atk = int(atk_match.group(1)) if atk_match else (prof_bonus + key_mod if spell_ability else 0)

        save_dc = raw_dc if spell_ability else ""
        spell_atk = f"{raw_atk:+d}" if spell_ability and raw_atk != 0 else ("" if not spell_ability else "+0")

        inspiration = str(fields.get("Inspiration", "")).strip().lower() in ("yes", "on", "1", "true")

        # 3. Spell Slots: Multi-Caster Matrix + Warlock Pact Isolation
        warlock_entry = next((c for c in class_entries if "warlock" in c["class"].lower()), None)
        other_casters = [c for c in class_entries if "warlock" not in c["class"].lower()]
        
        if len(class_entries) > 1 and other_casters:
            spell_slots = calculate_multiclass_spell_slots(other_casters)
        else:
            # Single-class caster handling
            spell_slots = self._extract_standard_slots(fields, text, class_entries[0]["class"], class_entries[0]["level"])

        # Layer Warlock Pact Magic if present
        is_pact_magic = (warlock_entry is not None) or "pact magic" in text.lower() or any("pact" in k.lower() for k in fields.keys())
        if warlock_entry:
            w_lvl = warlock_entry["level"]
            pact_data = WARLOCK_PACT_SLOTS.get(min(20, max(1, w_lvl)), {"count": 1, "level": 1})
            p_rank = pact_data["level"]
            p_count = pact_data["count"]
            pact_total = self._get_num(fields, ["PactMagic_SlotsTotal", "SlotsPactTotal", "SlotsTotalPact"], default=0)
            if pact_total == 0 and not other_casters and spell_slots[f"level_{p_rank}"]["max"] > 0:
                pact_total = spell_slots[f"level_{p_rank}"]["max"]
            if pact_total > 0:
                p_count = pact_total
            pact_rem = self._get_num(fields, ["PactMagic_SlotsRemaining", "SlotsRemainingPact"], default=p_count)

            pact_slot_dict = {
                "max": p_count,
                "current": pact_rem,
                "is_pact_magic": True,
                "reset_on": "short_rest"
            }
            # If standard slots exist from another spellcaster class at this rank, append warlock pact metadata
            if other_casters and spell_slots[f"level_{p_rank}"]["max"] > 0:
                spell_slots[f"level_{p_rank}"]["pact_max"] = p_count
                spell_slots[f"level_{p_rank}"]["pact_current"] = pact_rem
            else:
                spell_slots[f"level_{p_rank}"] = pact_slot_dict

        # 4. Mystic Arcanum Allocation (Warlock 11+)
        arcanum_meters = {}
        if warlock_entry:
            arcanum_meters = extract_mystic_arcanum_meters(warlock_entry["level"])

        # 5. Spells List & Preparation Limits
        has_slots = any(s.get("max", 0) > 0 for s in spell_slots.values())
        max_prepared = self._calculate_max_prepared_spells(clean_class, level, key_mod, has_slots=has_slots)
        spells_list = self.parse_spells_list(fields, text, clean_class, int_mod=key_mod, level=level)

        # 6. Discrete Limited-Use Resource Meters (Purging Speed)
        raw_traits_list = self._extract_traits_list(fields, text)
        cleaned_traits = self._clean_trait_headers(raw_traits_list)

        features_text = "\n".join(cleaned_traits)
        all_text = f"{text}\n{features_text}"
        custom_meters = self.extract_discrete_meters(all_text, clean_archetype, total_level, int_mod, wis_mod, cha_mod)
        if len(class_entries) > 1:
            custom_meters.pop("hit_dice", None)
            custom_meters.update(hd_meters)
        custom_meters.update(arcanum_meters)
        custom_meters.pop("speed", None)

        # 7. Inventory Standardization
        raw_inventory = self._extract_inventory(fields, text)
        structured_inventory = InventoryList(self.sanitize_inventory(raw_inventory))

        # 8. Boundary-Healed Roleplay Anchors
        headers = ["PERSONALITY TRAITS", "IDEALS", "BONDS", "FLAWS", "CHARACTER BACKSTORY", "FEATURES ==="]
        bonds_raw = self._extract_field_or_regex(fields, text, ["Bonds"], r'BONDS\s*\n\s*([^\n]+(?:\n[^\n]+)*?)(?=\n\s*FLAWS)')
        flaws_raw = self._extract_field_or_regex(fields, text, ["Flaws"], r'FLAWS\s*\n\s*([^\n]+(?:\n[^\n]+)*?)(?=\n\s*CHARACTER BACKSTORY|\n\s*ALLIES)')
        backstory_raw = fields.get("Backstory") or fields.get("BackgroundHistory") or self._extract_section(text, "CHARACTER BACKSTORY", "ADDITIONAL NOTES")

        roleplay_anchors = {
            "personality_traits": self._extract_field_or_regex(fields, text, ["PersonalityTraits ", "PersonalityTraits", "Personality"], r'PERSONALITY TRAITS\s*\n\s*([^\n]+(?:\n[^\n]+)*?)(?=\n\s*IDEALS)'),
            "ideals": self._extract_field_or_regex(fields, text, ["Ideals"], r'IDEALS\s*\n\s*([^\n]+(?:\n[^\n]+)*?)(?=\n\s*BONDS)'),
            "bonds": heal_truncated_field(bonds_raw, text, headers),
            "flaws": heal_truncated_field(flaws_raw, text, headers),
            "allies_and_organizations": fields.get("Allies", "").strip() if isinstance(fields.get("Allies"), str) and fields.get("Allies").strip() else (_clean(fields.get("Allies") or fields.get("AlliesOrganizations") or "") or self._extract_section(text, "ALLIES & ORGANIZATIONS", "PERSONALITY TRAITS"))
        }

        raw_bs = clean_backstory_text(backstory_raw)
        raw_bs_healed = heal_truncated_field(raw_bs, text, ["ADDITIONAL NOTES", "EQUIPMENT", "FEATURES", "FEATURES ==="])
        raw_an1 = clean_backstory_text(fields.get("AdditionalNotes1") or "")
        raw_an2 = clean_backstory_text(fields.get("AdditionalNotes2") or fields.get("AdditionalNotes") or "")
        if raw_an1 and ("nights, she" in raw_an1 or not fields.get("Notes")):
            full_backstory = " ".join(filter(None, [raw_bs_healed, raw_an1, raw_an2])).strip()
            additional_notes = ""
        else:
            full_backstory = raw_bs_healed
            additional_notes = "\n\n".join(filter(None, [raw_an1, raw_an2])).strip()

        # Extract Weight Carried with Automated Fallback Calculation
        weight_carried = _clean(fields.get("Weight Carried") or fields.get("WeightCarried") or "")
        encumbered = _clean(fields.get("Encumbered") or "")
        push_drag_lift = _clean(fields.get("PushDragLift") or fields.get("Push/Drag/Lift") or "")

        # If AcroForm field is empty or evaluates to 0, compute from structured inventory
        if not weight_carried or weight_carried in ("0 lb.", "--", "0"):
            total_calc_weight = 0.0
            for itm in structured_inventory:
                w_str = str(itm.get("weight", ""))
                q_val = int(itm.get("qty", 1)) if str(itm.get("qty", "1")).isdigit() else 1
                m_w = re.search(r'([\d\.]+)', w_str)
                if m_w:
                    total_calc_weight += float(m_w.group(1)) * q_val
            if total_calc_weight > 0:
                weight_carried = f"{total_calc_weight:.1f} lb."
            else:
                weight_carried = "0 lb."

        if not encumbered and str_val:
            encumbered = f"{str_val * 15} lb."
        if not push_drag_lift and str_val:
            push_drag_lift = f"{str_val * 30} lb."

        # Physical Appearance & Identity Dictionary
        char_gender = _clean(fields.get("GENDER") or fields.get("Gender") or "")
        char_age = _clean(fields.get("AGE") or fields.get("Age") or "")
        char_size = _clean(fields.get("SIZE") or fields.get("Size") or "") or "Medium"
        char_height = _clean(fields.get("HEIGHT") or fields.get("Height") or "")
        char_weight = _clean(fields.get("WEIGHT") or fields.get("Weight") or "")
        char_alignment = _clean(fields.get("ALIGNMENT") or fields.get("Alignment") or "")
        char_faith = _clean(fields.get("FAITH") or fields.get("Faith") or "")
        char_skin = _clean(fields.get("SKIN") or fields.get("Skin") or "")
        char_eyes = _clean(fields.get("EYES") or fields.get("Eyes") or "")
        char_hair = _clean(fields.get("HAIR") or fields.get("Hair") or "")

        # Creature Type Resolution
        creature_type = ""
        feats_lower = f"{features_text}\n{extracted_race}\n{clean_class}".lower()
        if "you are a humanoid" in feats_lower or "creature type: humanoid" in feats_lower or "humanoid" in feats_lower:
            creature_type = "Humanoid"
        elif any(r in extracted_race.lower() for r in ["aasimar", "human", "elf", "dwarf", "gnome", "halfling", "tiefling", "dragonborn", "orc", "lizardfolk", "kobold"]):
            creature_type = "Humanoid"
        else:
            creature_type = "Humanoid"

        appearance_dict = {
            "gender": char_gender,
            "age": char_age,
            "size": char_size,
            "height": char_height,
            "weight": char_weight,
            "alignment": char_alignment,
            "faith": char_faith,
            "skin": char_skin,
            "eyes": char_eyes,
            "hair": char_hair,
            "creature_type": creature_type
        }

        # Skills & Proficiencies
        dnd_skill_stats = {
            "Athletics": str_val, "Acrobatics": dex_val, "Sleight of Hand": dex_val, "Stealth": dex_val,
            "Arcana": int_val, "History": int_val, "Investigation": int_val, "Nature": int_val, "Religion": int_val,
            "Animal Handling": wis_val, "Insight": wis_val, "Medicine": wis_val, "Perception": wis_val, "Survival": wis_val,
            "Deception": cha_val, "Intimidation": cha_val, "Performance": cha_val, "Persuasion": cha_val
        }
        skill_field_pairs = [
            ("Acrobatics", "Acrobatics"), ("Animal Handling", "Animal"), ("Arcana", "Arcana"),
            ("Athletics", "Athletics"), ("Deception", "Deception"), ("History", "History"),
            ("Insight", "Insight"), ("Intimidation", "Intimidation"), ("Investigation", "Investigation"),
            ("Medicine", "Medicine"), ("Nature", "Nature"), ("Perception", "Perception"),
            ("Performance", "Performance"), ("Persuasion", "Persuasion"), ("Religion", "Religion"),
            ("Sleight of Hand", "SleightofHand"), ("Stealth", "Stealth"), ("Survival", "Survival")
        ]
        parsed_skills: Dict[str, str] = {}
        proficient_skills: List[str] = []

        for s_label, s_key in skill_field_pairs:
            raw_v = fields.get(s_key) or fields.get(s_label)
            s_val = _clean(raw_v)
            prof_marker = (
                fields.get(f"{s_key}Prof")
                or fields.get(f"{s_label}Prof")
                or fields.get(f"{s_key.replace(' ', '')}Prof")
                or fields.get(f"{s_label.replace(' ', '')}Prof")
            )
            is_prof = False
            if prof_marker:
                p_str = str(prof_marker).strip().lower()
                if p_str and p_str not in ("off", "no", "false", "0", "none"):
                    is_prof = True

            clean_num_str = re.sub(r'\(.*?\)', '', s_val).strip()
            num_match = re.search(r'[-+]?\d+', clean_num_str)
            if num_match:
                s_num = int(num_match.group(0))
                base_ability = dnd_skill_stats.get(s_label, 10)
                base_mod = (base_ability - 10) // 2
                if prof_bonus > 0 and s_num >= base_mod + prof_bonus:
                    is_prof = True
            elif not s_val:
                base_ability = dnd_skill_stats.get(s_label, 10)
                base_mod = (base_ability - 10) // 2
                s_val = f"+{base_mod}" if base_mod >= 0 else str(base_mod)

            if "(proficient)" in s_val.lower():
                is_prof = True
                s_val = re.sub(r'(?i)\s*\(proficient\)', '', s_val).strip()

            if is_prof:
                parsed_skills[s_label] = f"{s_val} (Proficient)"
                proficient_skills.append(s_label)
            elif s_val:
                parsed_skills[s_label] = s_val

        # Saving Throws
        save_stat_map = [
            ("Strength", "Str", "STR", str_mod),
            ("Dexterity", "Dex", "DEX", dex_mod),
            ("Constitution", "Con", "CON", con_mod),
            ("Intelligence", "Int", "INT", int_mod),
            ("Wisdom", "Wis", "WIS", wis_mod),
            ("Charisma", "Cha", "CHA", cha_mod),
        ]
        saving_throws = {}
        for s_name, s_abbr, s_upper, b_mod in save_stat_map:
            raw_st = fields.get(f"ST {s_name}") or fields.get(f"ST {s_abbr}")
            prof_save_marker = fields.get(f"{s_abbr}Prof") or fields.get(f"{s_name}Prof")
            is_save_prof = False
            if prof_save_marker:
                p_str = str(prof_save_marker).strip().lower()
                if p_str and p_str not in ("off", "no", "false", "0", "none"):
                    is_save_prof = True

            if raw_st:
                val_clean = _clean(raw_st)
                m_st = re.search(r'[-+]?\d+', val_clean)
                st_num = int(m_st.group(0)) if m_st else b_mod
                if prof_bonus > 0 and st_num >= b_mod + prof_bonus:
                    is_save_prof = True
                saving_throws[s_name] = val_clean
                saving_throws[s_upper] = st_num
            else:
                total_mod = b_mod + (prof_bonus if is_save_prof else 0)
                saving_throws[s_name] = f"{total_mod:+d}"
                saving_throws[s_upper] = total_mod

        # Vitals
        ac_val = self._get_num(fields, ["AC"], default=12)
        max_hp = self._get_num(fields, ["HPMax", "MaxHP"], default=10)
        cur_hp = self._get_num(fields, ["HPCurrent", "CurrentHP"], default=max_hp)
        temp_hp = self._get_num(fields, ["HPTemp", "TempHP"], default=0)
        raw_init = _clean(fields.get("Init") or fields.get("Initiative") or fields.get("Initiative "))
        init_val = int(raw_init) if raw_init and raw_init.lstrip("+-").isdigit() else dex_mod
        init_str = f"{init_val:+d}" if isinstance(init_val, int) else (f"{int(init_val):+d}" if str(init_val).lstrip("+-").isdigit() else str(init_val))

        defenses_val = _clean(fields.get("Defenses") or fields.get("Resistances") or "")

        # Build structured tagged_traits with validated names
        structured_traits = []
        for t in cleaned_traits:
            t_name = extract_clean_trait_name(t)
            if t_name and t_name.lower() not in BANNED_TRAIT_HEADERS:
                structured_traits.append({
                    "name": t_name,
                    "description": t,
                    "genre": "fantasy_d20"
                })

        # Build Universal Core
        core = UniversalCharacterCore(
            name=clean_name,
            player_name=player_name,
            archetype=clean_class,
            level=level,
            normalized_attributes={
                "might": min(100, max(10, int((str_val / 20.0) * 100))),
                "agility": min(100, max(10, int((dex_val / 20.0) * 100))),
                "vigor": min(100, max(10, int((con_val / 20.0) * 100))),
                "intellect": min(100, max(10, int((int_val / 20.0) * 100))),
                "presence": min(100, max(10, int(((wis_val + cha_val) / 40.0) * 100)))
            },
            semantic_skills=[
                {"category": "RELIGION_THEOLOGY", "tier": "proficient", "source_skill": "Religion"} if (is_paladin or is_cleric) else
                {"category": "NATURE_SURVIVAL", "tier": "proficient", "source_skill": "Nature"} if is_druid else
                {"category": "OCCULT_ARCANA", "tier": "proficient", "source_skill": "Arcana"},
                {"category": "INVESTIGATION_TECH", "tier": "expert", "source_skill": "Investigation"}
            ],
            tagged_inventory=structured_inventory,
            tagged_traits=clean_traits_and_headers(structured_traits),
            backstory=full_backstory or "Adventurer exploring the realms with skill and dedication.",
            roleplay_anchors=roleplay_anchors,
            custom_meters=custom_meters,
            description=appearance_dict
        )

        extracted_weapons = self._extract_weapons(fields, text)
        extracted_currency = self._extract_currency(fields)
        extracted_passives = {
            "perception": self._get_num(fields, ["PassivePerception", "Passive Perception", "Passive1"], 10),
            "insight": self._get_num(fields, ["PassiveInsight", "Passive Insight", "Passive2"], 10),
            "investigation": self._get_num(fields, ["PassiveInvestigation", "Passive Investigation", "Passive3"], 10)
        }

        native_payload = {
            "system_name": "dnd_5e",
            "class_level": clean_class,
            "is_pact_magic": is_pact_magic,
            "species": extracted_race,
            "background": extracted_bg,
            "inspiration": inspiration,
            "ability_save_dc": save_dc if save_dc else (8 + prof_bonus + key_mod if spell_ability else 10),
            "spell_attack_bonus": spell_atk,
            "spellcasting_ability": spell_ability,
            "armor_class": ac_val,
            "initiative_bonus": init_val,
            "speed": fields.get("Speed", "30 ft.").strip(),
            "defenses": defenses_val,
            "resistances": defenses_val,
            "hit_points": {
                "current": cur_hp,
                "max": max_hp,
                "temp": temp_hp,
                "hit_die": hit_dice_display
            },
            "hit_dice": {
                "current": level,
                "max": level,
                "die": hit_dice_display
            },
            "ability_scores": {
                "Strength": str_val, "Dexterity": dex_val, "Constitution": con_val,
                "Intelligence": int_val, "Wisdom": wis_val, "Charisma": cha_val,
                "STR": str_val, "DEX": dex_val, "CON": con_val,
                "INT": int_val, "WIS": wis_val, "CHA": cha_val
            },
            "saving_throws": saving_throws,
            "skills": parsed_skills,
            "passives": extracted_passives,
            "weapons": extracted_weapons,
            "currency": extracted_currency,
            "proficiencies": {
                "skills": proficient_skills,
                "armor": _clean(fields.get("ArmorProficiencies") or fields.get("ProfArmor") or "Light Armor, Medium Armor, Shields"),
                "weapons": _clean(fields.get("WeaponProficiencies") or fields.get("ProfWeapons") or "Simple Weapons"),
                "tools": _clean(fields.get("ToolProficiencies") or fields.get("ProfTools") or "Thieves' Tools"),
                "languages": _clean(fields.get("Languages") or fields.get("LanguagesKnown") or "Common")
            },
            "attuned_magic_items": {"current": 0, "max": 3},
            "spell_slots": spell_slots,
            "max_prepared_leveled_spells": max_prepared,
            "spells": spells_list,
            "custom_meters": custom_meters,
            "features_and_traits": cleaned_traits,
            "roleplay_anchors": roleplay_anchors,
            "inventory": structured_inventory,
            "tagged_inventory": structured_inventory,
            "weight_carried": weight_carried,
            "encumbered": encumbered,
            "push_drag_lift": push_drag_lift,
            "description": appearance_dict,
            "gender": char_gender,
            "age": char_age,
            "size": char_size,
            "height": char_height,
            "weight": char_weight,
            "alignment": char_alignment,
            "faith": char_faith,
            "skin": char_skin,
            "eyes": char_eyes,
            "hair": char_hair,
            "creature_type": creature_type,
            "backstory": full_backstory,
            "additional_notes": additional_notes,
            "raw_acro_form_fields": fields
        }

        return {
            "schema_version": "2.1",
            "id": f"char_{clean_name.lower().replace(' ', '_')}_root",
            "name": clean_name,
            "variant_label": "Canonical Root",
            "player_name": player_name,
            "level": level,
            "experience_points": 0,
            "archetype": clean_class,
            "species": extracted_race or "Human",
            "background": extracted_bg,
            "ruleset": "dnd_5e",
            "ruleset_label": "D&D 5E",
            "native_genre": "heroic_fantasy",
            "native_system": "dnd_5e",
            "vitals": {
                "health": {"current": cur_hp, "max": max_hp, "label": "Health"},
                "guard": {"current": ac_val, "max": ac_val, "label": "Armor Defense"}
            },
            "attributes": native_payload["ability_scores"],
            "armor_class": ac_val,
            "speed": native_payload["speed"],
            "defenses": defenses_val,
            "resistances": defenses_val,
            "hit_dice": hit_dice_display,
            "weapons": extracted_weapons,
            "currency": extracted_currency,
            "inventory": structured_inventory,
            "tagged_inventory": structured_inventory,
            "features_and_traits": cleaned_traits,
            "spells": spells_list,
            "spellcasting_ability": spell_ability,
            "spell_save_dc": str(save_dc) if save_dc else "",
            "spell_attack_bonus": spell_atk,
            "passives": extracted_passives,
            "senses": _clean(fields.get("Senses") or fields.get("AdditionalSenses") or ""),
            "initiative": init_str,
            "proficiencies": native_payload["proficiencies"],
            "prof_bonus": f"+{prof_bonus}" if prof_bonus >= 0 else str(prof_bonus),
            "skills": parsed_skills,
            "saving_throws": native_payload["saving_throws"],
            "spell_slots": spell_slots,
            "max_prepared_leveled_spells": max_prepared,
            "universal_core": core,
            "native_system_data": native_payload,
            "is_pact_magic": is_pact_magic,
            "custom_meters": custom_meters,
            "roleplay_anchors": roleplay_anchors,
            "backstory": full_backstory,
            "additional_notes": additional_notes,
            "weight_carried": weight_carried,
            "encumbered": encumbered,
            "push_drag_lift": push_drag_lift,
            "description": appearance_dict,
            "gender": char_gender,
            "age": char_age,
            "size": char_size,
            "height": char_height,
            "weight": char_weight,
            "alignment": char_alignment,
            "faith": char_faith,
            "skin": char_skin,
            "eyes": char_eyes,
            "hair": char_hair,
            "creature_type": creature_type
        }

    # --- Processing Subroutines ---
    def _extract_standard_slots(self, fields: Dict[str, Any], text: str, class_name: str = "", level: int = 1) -> Dict[str, Dict[str, Any]]:
        spell_slots = {}
        found_any = False
        for rank in range(1, 10):
            total_slots = self._get_num(fields, [f"SlotsTotal{rank}", f"SlotsTotal_{rank}", f"SpellSlots_{rank}_Total"], default=0)
            if total_slots == 0:
                for h_idx in range(12):
                    hdr = str(fields.get(f"spellHeader{h_idx}") or "")
                    slot_hdr = str(fields.get(f"spellSlotHeader{h_idx}") or "")
                    if re.search(rf'{rank}(?:st|nd|rd|th)\s+LEVEL', hdr, re.IGNORECASE):
                        m_s = re.search(r'(\d+)\s+(?:Slots?|Pact)', slot_hdr, re.IGNORECASE)
                        if m_s:
                            total_slots = int(m_s.group(1))
                            break
            if total_slots == 0:
                slot_regex = re.search(rf'{rank}(?:st|nd|rd|th)\s+LEVEL===\s*\n\s*(\d+)\s+(?:Slots|Pact)', text, re.IGNORECASE)
                if slot_regex:
                    total_slots = int(slot_regex.group(1))

            if total_slots > 0:
                found_any = True

            spell_slots[f"level_{rank}"] = {
                "max": total_slots,
                "current": self._get_num(fields, [f"SlotsRemaining{rank}", f"SlotsRemaining_{rank}"], default=total_slots),
                "reset_on": "long_rest"
            }

        if not found_any and class_name and "warlock" not in class_name.lower():
            fallback_slots = calculate_multiclass_spell_slots([{"class": class_name, "level": level}])
            if any(s["max"] > 0 for s in fallback_slots.values()):
                return fallback_slots

        return spell_slots

    def _get_spell_level_from_headers(self, spell_idx: int, fields: Dict[str, Any]) -> Optional[int]:
        """Inspects D&D Beyond spellHeader boundaries to determine exact spell tier."""
        curr_lvl = None
        for k, v in fields.items():
            v_str = str(v).strip()
            m_hdr = re.search(r"===\s*(\d+(?:st|nd|rd|th)?\s*level|cantrips?)\s*===", v_str, re.IGNORECASE)
            if m_hdr:
                hl = m_hdr.group(1).lower()
                if "cantrip" in hl:
                    curr_lvl = 0
                else:
                    m_num = re.search(r"\d+", hl)
                    if m_num:
                        curr_lvl = int(m_num.group(0))
            m_name = re.match(r"^spellname(\d+)$", k, re.IGNORECASE)
            if m_name and int(m_name.group(1)) == spell_idx:
                return curr_lvl
        return None

    def parse_spells_list(self, fields: Dict[str, Any], text: str, class_str: str = "", int_mod: int = 0, level: int = 1) -> List[Dict[str, Any]]:
        """Parses spells, deduplicates, and validates always-prepared subclass spells."""
        spells = []
        seen_spells = set()

        raw_class_field = str(fields.get("ClassLevel") or fields.get("Class & Level") or fields.get("CLASS  LEVEL") or "")
        raw_feats_field = str(fields.get("FeaturesAndTraits") or fields.get("FeaturesTraits1") or "")
        subclass_context = f"{text}\n{class_str}\n{raw_class_field}\n{raw_feats_field}".lower()
        c_lower = f"{class_str} {raw_class_field}".lower()
        is_known_caster = any(k in c_lower for k in ("warlock", "sorcerer", "bard"))

        active_subclass = ""
        # 1. First look for explicit domain / oath / circle references (e.g. "Death Domain", "Oath of Vengeance", "Circle of the Land")
        for sc in ALWAYS_PREPARED_SUBCLASS_SPELLS.keys():
            if re.search(rf'\b{sc}\s+(?:domain|oath|circle)\b|\b(?:domain|oath|circle)[\s•\:\-]+{sc}\b', subclass_context):
                active_subclass = sc
                break

        # 2. Context-based fallback
        if not active_subclass:
            for sc in ALWAYS_PREPARED_SUBCLASS_SPELLS.keys():
                if sc in ("devotion", "vengeance", "ancients"):
                    if sc in subclass_context:
                        active_subclass = sc
                        break
                elif sc == "land":
                    if ("druid" in c_lower or "circle" in subclass_context) and sc in subclass_context:
                        active_subclass = sc
                        break
                elif sc in ("life", "light", "death", "trickery"):
                    if ("cleric" in c_lower or "domain" in subclass_context) and sc in subclass_context:
                        active_subclass = sc
                        break

        always_prep_set = set(ALWAYS_PREPARED_SUBCLASS_SPELLS.get(active_subclass, []))
        cantrip_keywords = [
            "light", "guidance", "mending", "prestidigitation", "shocking grasp",
            "sorcerous burst", "eldritch blast", "druidcraft", "sacred flame",
            "thorn whip", "toll the dead", "fire bolt"
        ]

        # 1. Try structured spells from AcroForm fields first
        spells_from_fields = parse_structured_spells(fields)
        if spells_from_fields:
            for s in spells_from_fields:
                s_name = s.get("name", "").strip()
                clean_name = re.sub(r'\s*<C>', '', s_name, flags=re.IGNORECASE)
                clean_name = re.sub(r'\s*\[R\]', '', clean_name, flags=re.IGNORECASE).strip()
                if not clean_name:
                    continue
                low_n = clean_name.lower()
                if low_n in seen_spells:
                    continue
                seen_spells.add(low_n)

                lvl_val = s.get("level")
                lvl_str = str(lvl_val).lower() if lvl_val is not None else ""
                lvl_num = normalize_spell_level(lvl_val) if lvl_val is not None else None

                is_cantrip = False
                if lvl_val is not None and (lvl_num == 0 or "cantrip" in lvl_str):
                    is_cantrip = True
                elif lvl_val is None or lvl_str in ("", "cantrip/spell"):
                    is_cantrip = any(re.search(rf'\b{re.escape(c)}\b', low_n) for c in cantrip_keywords)

                is_subclass_oath = low_n in always_prep_set
                is_prep = True if (is_cantrip or is_subclass_oath or is_known_caster) else False
                final_lvl = 0 if is_cantrip else (lvl_num if (lvl_num is not None and lvl_num > 0) else normalize_spell_level(self._infer_spell_level(clean_name, text, fields=fields)))

                spells.append({
                    "name": clean_name,
                    "level": final_lvl,
                    "is_cantrip": is_cantrip,
                    "is_ritual": "[R]" in s_name or s.get("is_ritual", False),
                    "is_always_prepared": is_subclass_oath,
                    "prepared": is_prep,
                    "cast_time": s.get("cast_time") or "1A",
                    "range": s.get("range") or "Touch",
                    "components": s.get("components") or "V, S",
                    "duration": s.get("notes") or ("1 minute" if "<C>" in s_name else "Instantaneous")
                })
            return spells

        # 2. Text extraction fallback: regex search
        spell_pattern = re.compile(
            r'([OP\s]+)?\s*([A-Za-z \t\'\-]+(?:\[R\]|<C>|\s)*)\s+(Artificer|Sorcerer|Wizard|Cleric|Druid|Paladin|Warlock|Ranger|Bard|Light Bearer)\s+(?:([0-9A-Za-z+\-\s]+?)\s+)?(Touch|Self|[\d\s]+ft[^\n]*?)\s+(V,S,M|V,S|V,M|S,M|V|S|M|[VSM,\s]+)$',
            re.IGNORECASE | re.MULTILINE
        )
        matches = spell_pattern.findall(text)
        for m in matches:
            prep_char, s_name, source, mid_tokens, s_range, comps = m
            clean_name = s_name.replace("<C>", "").replace("[R]", "").strip()
            clean_name = clean_name.split("\n")[-1].strip()
            if not clean_name:
                continue
            low_n = clean_name.lower()
            if low_n in seen_spells:
                continue
            seen_spells.add(low_n)

            inferred_lvl = self._infer_spell_level(clean_name, text, fields=fields)
            is_cantrip = (inferred_lvl == 0) or ("cantrip" in low_n) or any(
                re.search(rf'\b{re.escape(c)}\b', low_n) for c in cantrip_keywords
            )
            is_subclass_oath = low_n in always_prep_set
            is_prep = True if (is_cantrip or is_subclass_oath or is_known_caster) else False

            cast_time = "1A"
            if mid_tokens and mid_tokens.strip():
                tokens = mid_tokens.strip().split()
                cast_time = tokens[-1] if tokens else mid_tokens.strip()

            spells.append({
                "name": clean_name,
                "level": 0 if is_cantrip else normalize_spell_level(inferred_lvl if inferred_lvl > 0 else 1),
                "is_cantrip": is_cantrip,
                "is_ritual": "[R]" in s_name,
                "is_always_prepared": is_subclass_oath,
                "prepared": is_prep,
                "cast_time": cast_time,
                "range": s_range.strip() if s_range else "Touch",
                "components": comps.strip(),
                "duration": "1 minute" if "<C>" in s_name else "Instantaneous"
            })

        # Fallback to line scan if regex matched nothing
        if not spells:
            for line in text.splitlines():
                line = line.strip()
                if not line or any(w in line for w in ["SAVING THROWS", "D&D BEYOND", "LEVEL===", "Slots"]):
                    continue
                m = re.match(
                    r'^([OP])?\s*(.+?)\s+(?:(Artificer|Light Bearer|Wizard|Cleric|Druid|Sorcerer|Warlock|Bard|Paladin|Ranger)\s+)?(?:(\w+)\s+)?(\d+[A-Za-z]+|\d+m|\d+BA|\d+R)?\s*(Touch|Self|[\d\s]+ft[^\n]*?)\s+(V,S,M|V,S|V,M|S,M|V|S|M)$',
                    line,
                    re.IGNORECASE
                )
                if m:
                    prep_char, s_name, source, save_atk, cast_time, s_range, comps = m.groups()
                    clean_name = s_name.replace("<C>", "").replace("[R]", "").strip()
                    if not clean_name:
                        continue
                    low_n = clean_name.lower()
                    if low_n in seen_spells:
                        continue
                    seen_spells.add(low_n)
                    is_cantrip = ("cantrip" in low_n) or any(re.search(rf'\b{re.escape(k)}\b', low_n) for k in cantrip_keywords)
                    is_subclass_oath = low_n in always_prep_set
                    is_prep = True if (is_cantrip or is_subclass_oath or is_known_caster) else False
                    spells.append({
                        "name": clean_name,
                        "level": 0 if is_cantrip else normalize_spell_level(self._infer_spell_level(clean_name, text, fields=fields)),
                        "is_cantrip": is_cantrip,
                        "is_ritual": "[R]" in s_name,
                        "is_always_prepared": is_subclass_oath,
                        "prepared": is_prep,
                        "cast_time": cast_time.strip() if cast_time else "1A",
                        "range": s_range.strip() if s_range else "Touch",
                        "components": comps.strip(),
                        "duration": "1 minute" if "<C>" in s_name else "Instantaneous"
                    })

        return spells

    def extract_discrete_meters(
        self,
        text: str,
        class_str: Any = "",
        level: int = 1,
        int_mod: int = 0,
        wis_mod: int = 0,
        cha_mod: int = 0
    ) -> Dict[str, Any]:
        """Extracts consumable pools. Strictly excludes movement Speed."""
        if isinstance(class_str, int):
            cha_mod = wis_mod
            int_mod = level
            level = class_str
            class_str = ""

        meters = {}
        c_lower = str(class_str).lower()
        t_lower = text.lower()

        # 1. Hit Dice Meter
        hit_die = self._infer_hit_die(class_str if class_str else text)
        meters["hit_dice"] = {
            "label": f"Hit Dice ({level}d{hit_die})",
            "current": level,
            "max": level,
            "reset_on": "long_rest"
        }

        # 2. Blood Hunter: Blood Maledict (1/Short Rest, 2 at level >= 6)
        if "blood hunter" in c_lower or "blood maledict" in t_lower:
            bm_uses = 2 if level >= 6 else 1
            meters["blood_maledict"] = {
                "label": "Blood Maledict (Blood Curse)",
                "current": bm_uses,
                "max": bm_uses,
                "reset_on": "short_rest"
            }

        # 3. Paladin: Lay on Hands & Divine Sense
        if "paladin" in c_lower or "lay on hands" in t_lower:
            loh_match = re.search(r'lay on hands pool:\s*(\d+)', t_lower)
            pal_m = re.search(r'paladin\s*(\d+)', c_lower)
            pal_lvl = int(pal_m.group(1)) if pal_m else level
            loh_val = int(loh_match.group(1)) if loh_match else (pal_lvl * 5)
            meters["lay_on_hands"] = {
                "label": f"Lay on Hands Pool ({loh_val} HP)",
                "current": loh_val,
                "max": loh_val,
                "reset_on": "long_rest"
            }

        if "channel divinity: divine sense" in t_lower:
            # 2024 rules: Powered by Channel Divinity, not a standalone pool
            pass
        elif "divine sense" in t_lower:
            ds_match = re.search(r'divine\s+sense[\s\S]{1,60}?(\d+)\s*(?:times\s*per\s*long\s*rest|/\s*long\s*rest)', t_lower)
            ds_val = int(ds_match.group(1)) if ds_match else max(1, 1 + cha_mod)
            meters["divine_sense"] = {
                "label": "Divine Sense",
                "current": ds_val,
                "max": ds_val,
                "reset_on": "long_rest"
            }

        pal_m = re.search(r'paladin\s*(\d+)', c_lower)
        pal_lvl = int(pal_m.group(1)) if pal_m else level
        cleric_m = re.search(r'cleric\s*(\d+)', c_lower)
        cleric_lvl = int(cleric_m.group(1)) if cleric_m else level
        if ("channel divinity" in t_lower) or ("cleric" in c_lower and cleric_lvl >= 2) or ("paladin" in c_lower and pal_lvl >= 3):
            cd_lvl = max(cleric_lvl if "cleric" in c_lower else 0, pal_lvl if "paladin" in c_lower else 0, level if ("cleric" not in c_lower and "paladin" not in c_lower) else 0)
            cd_uses = 2 if cd_lvl >= 6 else 1
            meters["channel_divinity"] = {
                "label": "Channel Divinity",
                "current": cd_uses,
                "max": cd_uses,
                "reset_on": "short_rest"
            }

        # 4. Wizard: Arcane Recovery
        if "wizard" in c_lower or "arcane recovery" in t_lower:
            meters["arcane_recovery"] = {
                "label": "Arcane Recovery",
                "current": 1,
                "max": 1,
                "reset_on": "long_rest"
            }

        # 5. Druid: Wild Shape (2 uses per Short Rest starting at Level 2)
        druid_m = re.search(r'druid\s*(\d+)', c_lower)
        druid_lvl = int(druid_m.group(1)) if druid_m else level
        if ("druid" in c_lower or "wild shape" in t_lower) and druid_lvl >= 2:
            ws_uses = 3 if druid_lvl >= 17 else 2
            meters["wild_shape"] = {
                "label": "Wild Shape Uses",
                "current": ws_uses,
                "max": ws_uses,
                "reset_on": "short_rest"
            }

        # 6. Monk: Ki / Focus Points (Level / Short Rest)
        if "monk" in c_lower or "ki points" in t_lower or "focus points" in t_lower:
            monk_m = re.search(r'monk\s*(\d+)', c_lower)
            monk_lvl = int(monk_m.group(1)) if monk_m else level
            meters["ki_points"] = {
                "label": "Ki / Focus Points",
                "current": monk_lvl,
                "max": monk_lvl,
                "reset_on": "short_rest"
            }

        # 7. Sorcerer: Sorcery Points & Innate Sorcery
        if "sorcerer" in c_lower or "font of magic" in t_lower or "sorcery points" in t_lower:
            sp_match = re.search(r'(\d+)\s*SP', text)
            sorc_m = re.search(r'sorcerer\s*(\d+)', c_lower)
            sorc_lvl = int(sorc_m.group(1)) if sorc_m else level
            sp_max = int(sp_match.group(1)) if sp_match else (sorc_lvl if sorc_lvl >= 2 else 0)
            if sp_max > 0:
                meters["sorcery_points"] = {
                    "label": "Sorcery Points",
                    "current": sp_max,
                    "max": sp_max,
                    "reset_on": "long_rest"
                }
        if "innate sorcery" in t_lower or "Innate Sorcery" in text:
            meters["innate_sorcery"] = {
                "label": "Innate Sorcery",
                "current": 2,
                "max": 2,
                "reset_on": "long_rest"
            }

        # 8. Fighter: Second Wind & Action Surge
        if "fighter" in c_lower:
            fighter_m = re.search(r'fighter\s*(\d+)', c_lower)
            fighter_lvl = int(fighter_m.group(1)) if fighter_m else level
            meters["second_wind"] = {
                "label": "Second Wind",
                "current": 1,
                "max": 1,
                "reset_on": "short_rest"
            }
            if fighter_lvl >= 2:
                meters["action_surge"] = {
                    "label": "Action Surge",
                    "current": 1,
                    "max": 1,
                    "reset_on": "short_rest"
                }

        # 9. Species & Class Traits
        if "draconic cry" in t_lower:
            meters["draconic_cry"] = {
                "label": "Draconic Cry",
                "current": 2,
                "max": 2,
                "reset_on": "long_rest"
            }
        if "healing hands" in t_lower:
            meters["healing_hands"] = {
                "label": "Healing Hands (2d4)",
                "current": 1,
                "max": 1,
                "reset_on": "long_rest"
            }
        if "magical tinkering" in t_lower:
            tinker_max = max(1, int_mod)
            meters["magical_tinkering"] = {
                "label": "Magical Tinkering Objects",
                "current": tinker_max,
                "max": tinker_max,
                "reset_on": "special"
            }
        if "celestial revelation" in t_lower:
            meters["celestial_revelation"] = {
                "label": "Celestial Revelation",
                "current": 1,
                "max": 1,
                "reset_on": "long_rest"
            }
        if "hungry jaws" in t_lower:
            meters["hungry_jaws"] = {
                "label": "Hungry Jaws",
                "current": 1,
                "max": 1,
                "reset_on": "short_rest"
            }

        # 10. High-Level Consumable Traits & Rest Meters
        if "breath weapon" in t_lower:
            bw_match = re.search(r'breath weapon.*?(\d+)\s*(?:times\s*per\s*long\s*rest|/\s*long\s*rest)', t_lower)
            pb_val = (level - 1) // 4 + 2
            bw_uses = int(bw_match.group(1)) if bw_match else pb_val
            meters["breath_weapon"] = {
                "label": "Breath Weapon",
                "current": bw_uses,
                "max": bw_uses,
                "reset_on": "long_rest"
            }

        if "draconic flight" in t_lower:
            meters["draconic_flight"] = {
                "label": "Draconic Flight",
                "current": 1,
                "max": 1,
                "reset_on": "long_rest"
            }

        if "dark one's own luck" in t_lower or "dark one’s own luck" in t_lower:
            dool_match = re.search(r'dark one[’\']s own luck[\s\S]*?(\d+)\s*(?:times\s*per\s*long\s*rest|/\s*long\s*rest)', t_lower)
            dool_uses = int(dool_match.group(1)) if dool_match else 3
            meters["dark_ones_own_luck"] = {
                "label": "Dark One's Own Luck",
                "current": dool_uses,
                "max": dool_uses,
                "reset_on": "long_rest"
            }

        if re.search(r'hurl\s+through\s+hell', t_lower):
            meters["hurl_through_hell"] = {
                "label": "Hurl Through Hell",
                "current": 1,
                "max": 1,
                "reset_on": "long_rest"
            }

        if re.search(r'boon\s+of\s+fate', t_lower):
            meters["boon_of_fate"] = {
                "label": "Boon of Fate",
                "current": 1,
                "max": 1,
                "reset_on": "short_rest"
            }

        if re.search(r'magical\s+cunning', t_lower):
            meters["magical_cunning"] = {
                "label": "Magical Cunning",
                "current": 1,
                "max": 1,
                "reset_on": "long_rest"
            }

        # CRITICAL: Rigorous Purge of Speed
        meters.pop("speed", None)
        return meters

    def extract_limited_meters(self, text: str, int_mod: int, level: int = 1) -> Dict[str, Any]:
        return self.extract_discrete_meters(text, "", level, int_mod, 0, 0)

    @staticmethod
    def _calculate_max_prepared_spells(class_str: str, level: int, key_mod: int, has_slots: bool = True) -> int:
        if not has_slots:
            return 0

        class_entries = parse_multiclass_string(class_str)
        
        # If multiclassed, calculate each class independently and sum
        if len(class_entries) > 1:
            total_prep = 0
            for c in class_entries:
                c_name = c["class"].lower()
                c_lvl = c["level"]
                if any(k in c_name for k in ["druid", "cleric", "wizard"]):
                    total_prep += max(1, key_mod + c_lvl)
                elif "paladin" in c_name or "artificer" in c_name:
                    total_prep += max(1, key_mod + (c_lvl // 2))
                elif "ranger" in c_name:
                    total_prep += max(0, key_mod + (c_lvl // 2)) if c_lvl >= 2 else 0
                elif "sorcerer" in c_name:
                    sorc_table = {1: 2, 2: 4, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8, 8: 9, 9: 10, 10: 11,
                                  11: 12, 12: 12, 13: 13, 14: 13, 15: 14, 16: 14, 17: 15, 18: 15, 19: 15, 20: 15}
                    total_prep += sorc_table.get(c_lvl, min(15, c_lvl + 1))
                elif "warlock" in c_name:
                    warlock_known = {1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8, 8: 9, 9: 10, 10: 10,
                                     11: 11, 12: 11, 13: 12, 14: 12, 15: 13, 16: 13, 17: 14, 18: 14, 19: 15, 20: 15}
                    total_prep += warlock_known.get(c_lvl, min(15, c_lvl + 1))
            return total_prep

        # Single-class fallback
        cls_lower = class_str.lower()
        if "artificer" in cls_lower or "paladin" in cls_lower:
            return max(1, key_mod + (level // 2))
        elif any(k in cls_lower for k in ["druid", "cleric", "wizard"]):
            return max(1, key_mod + level)
        elif "ranger" in cls_lower:
            if level < 2:
                return 0
            return max(1, key_mod + (level // 2))
        elif "sorcerer" in cls_lower:
            sorc_table = {1: 2, 2: 4, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8, 8: 9, 9: 10, 10: 11,
                          11: 12, 12: 12, 13: 13, 14: 13, 15: 14, 16: 14, 17: 15, 18: 15, 19: 15, 20: 15}
            return sorc_table.get(level, min(15, level + 1))
        elif "warlock" in cls_lower:
            warlock_known = {1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8, 8: 9, 9: 10, 10: 10,
                             11: 11, 12: 11, 13: 12, 14: 12, 15: 13, 16: 13, 17: 14, 18: 14, 19: 15, 20: 15}
            return warlock_known.get(level, min(15, level + 1))
        return max(1, key_mod + level)

    @staticmethod
    def _infer_hit_die(class_text: str) -> int:
        c = class_text.lower()
        if "sorcerer" in c or "wizard" in c:
            return 6
        elif any(k in c for k in ["fighter", "paladin", "ranger", "blood hunter"]):
            return 10
        elif "barbarian" in c:
            return 12
        return 8

    @staticmethod
    def _clean_trait_headers(raw_traits: List[str]) -> List[str]:
        """Strips source-tagged section headers like 'Hit Points • TCoE 10'."""
        cleaned = []
        for t in raw_traits:
            t_clean = t.strip()
            # Strip leading bullet points, asterisks, dashes, pipes
            base_name = re.sub(r'^[\*\s\u2022\|\-]+', '', t_clean)
            # Strip trailing citation sources
            base_name = re.sub(r'\s*[•|:].*$', '', base_name).strip().lower()
            if base_name and base_name not in BANNED_TRAIT_HEADERS and not base_name.startswith("==="):
                cleaned.append(t_clean)
        return cleaned

    @staticmethod
    def sanitize_inventory(raw_inventory: List[Any]) -> List[Any]:
        return sanitize_inventory(raw_inventory)

    @staticmethod
    def _infer_spell_level(spell_name: str, full_text: str, fields: Optional[Dict[str, Any]] = None) -> int:
        clean = spell_name.lower().strip()
        clean_no_tags = re.sub(r'\s*\[.*?\]|\s*<.*?>', '', clean).strip()
        reg_info = DND_SPELL_REGISTRY.get(clean) or DND_SPELL_REGISTRY.get(clean_no_tags)
        if reg_info and "level" in reg_info:
            return normalize_spell_level(reg_info["level"])

        if fields:
            curr_lvl = None
            for k, v in fields.items():
                v_str = str(v).strip()
                m_hdr = re.search(r"===\s*(\d+(?:st|nd|rd|th)?\s*level|cantrips?)\s*===", v_str, re.IGNORECASE)
                if m_hdr:
                    hl = m_hdr.group(1).lower()
                    if "cantrip" in hl:
                        curr_lvl = 0
                    else:
                        m_num = re.search(r"\d+", hl)
                        if m_num:
                            curr_lvl = int(m_num.group(0))
                m_name = re.match(r"^spellname(\d+)$", k, re.IGNORECASE)
                if m_name:
                    name_in_field = v_str.lower().strip()
                    if clean == name_in_field or clean_no_tags == name_in_field or clean in name_in_field:
                        if curr_lvl is not None:
                            return curr_lvl

        cantrip_match = re.search(rf'CANTRIPS[\s\S]*?{re.escape(clean)}', full_text, re.IGNORECASE)
        if cantrip_match:
            rest = re.sub(r'^CANTRIPS[=:\s]*', '', cantrip_match.group(0), flags=re.IGNORECASE)
            if not re.search(r'\d+(?:st|nd|rd|th)\s+LEVEL', rest, re.IGNORECASE):
                return 0
        for lvl in range(1, 10):
            lvl_hdr = rf'{lvl}(?:st|nd|rd|th)\s+LEVEL'
            match = re.search(rf'{lvl_hdr}[\s\S]*?{re.escape(clean)}', full_text, re.IGNORECASE)
            if match:
                matched_snippet = match.group(0)
                rest = re.sub(rf'^{lvl_hdr}[=:\s]*', '', matched_snippet, flags=re.IGNORECASE)
                if not re.search(r'\d+(?:st|nd|rd|th)\s+LEVEL', rest, re.IGNORECASE):
                    return lvl

        second_lvl = [
            "dragon's breath", "spider climb", "misty step", "scorching ray",
            "shatter", "invisibility", "hold person", "lesser restoration", "mirror image",
            "darkness", "mind spike", "suggestion", "levitate"
        ]
        third_lvl = [
            "fireball", "lightning bolt", "counterspell", "dispel magic",
            "haste", "revivify", "fly", "hunger of hadar", "animate dead", "vampiric touch",
            "stinking cloud"
        ]
        fourth_lvl = [
            "banishment", "dimension door", "blight", "fire shield", "wall of fire",
            "polymorph", "greater invisibility", "ice storm"
        ]
        fifth_lvl = [
            "planar binding", "contact other plane", "teleportation circle", "geas",
            "insect plague", "cloudkill", "cone of cold", "hold monster", "wall of force", "synaptic static"
        ]
        sixth_lvl = [
            "create undead", "chain lightning", "disintegrate", "heal", "eyebite", "true seeing"
        ]
        seventh_lvl = [
            "finger of death", "delayed blast fireball", "teleport", "forcecage", "plane shift"
        ]
        eighth_lvl = [
            "dominate monster", "sunburst", "feeblemind", "maze", "mind blank", "demiplane"
        ]
        ninth_lvl = [
            "astral projection", "meteor swarm", "power word kill", "time stop", "wish", "true polymorph", "foresight"
        ]
        if any(s in clean for s in ninth_lvl):
            return 9
        if any(s in clean for s in eighth_lvl):
            return 8
        if any(s in clean for s in seventh_lvl):
            return 7
        if any(s in clean for s in sixth_lvl):
            return 6
        if any(s in clean for s in fifth_lvl):
            return 5
        if any(s in clean for s in fourth_lvl):
            return 4
        if any(s in clean for s in third_lvl):
            return 3
        if any(s in clean for s in second_lvl):
            return 2
        return 1

    @staticmethod
    def _clean_html_text(raw_text: str) -> str:
        if not raw_text:
            return ""
        clean = html.unescape(raw_text)
        clean = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', clean, flags=re.IGNORECASE)
        clean = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', clean, flags=re.IGNORECASE)
        clean = re.sub(r'<[^>]+>', '', clean)
        return clean.strip()

    @staticmethod
    def _extract_section(text: str, start_header: str, end_header: str) -> str:
        pattern = rf'{re.escape(start_header)}\s*===*\s*([\s\S]*?)(?={re.escape(end_header)}|$)'
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    def _extract_field_or_regex(self, fields: Dict[str, Any], text: str, field_keys: List[str], regex_pattern: str) -> str:
        val = ""
        for k in field_keys:
            if fields.get(k):
                val = str(fields[k]).strip()
                break
        if not val:
            m = re.search(regex_pattern, text, re.IGNORECASE)
            if m:
                val = m.group(1).strip()
        return self._clean_html_text(val)

    def _extract_traits_list(self, fields: Dict[str, Any], text: str) -> List[str]:
        raw = fields.get("FeaturesAndTraits", "") or fields.get("Features and Traits", "")
        if not raw:
            trait_parts = []
            for i in range(1, 10):
                val = str(fields.get(f"FeaturesTraits{i}") or "").strip()
                if val:
                    trait_parts.append(val)
            if trait_parts:
                raw = "\n".join(trait_parts)
        if not raw:
            raw = self._extract_section(text, "FEATURES ===", "EQUIPMENT")
        if not raw:
            return []
        return [line.strip() for line in raw.split("\n") if line.strip()]

    def _extract_inventory(self, fields: Dict[str, Any], text: str) -> List[Dict[str, Any]]:
        """Extracts inventory rows from AcroForm fields (EquipmentRow and Eq Name)."""
        inv = []
        for i in range(1, 35):
            item_name = fields.get(f"EquipmentRow{i}", "").strip()
            if item_name:
                qty = fields.get(f"EquipmentQty{i}", "1").strip()
                weight = fields.get(f"EquipmentWeight{i}", "--").strip()
                inv.append({"name": item_name, "qty": qty, "weight": weight, "category": "Gear", "notes": ""})
        for i in range(60):
            item_val = fields.get(f"Eq Name{i}") or fields.get(f"Eq Name {i}")
            if item_val:
                item_str = str(item_val).replace(r"\(", "(").replace(r"\)", ")").strip()
                if "(" in item_str and ")" not in item_str:
                    item_str += ")"
                eq_qty = fields.get(f"Eq Qty{i}") or fields.get(f"Eq Qty {i}") or "1"
                eq_weight = fields.get(f"Eq Weight{i}") or fields.get(f"Eq Weight {i}") or "--"
                lower_i = item_str.lower()
                cat = "Gear"
                if any(w in lower_i for w in ["sword", "dagger", "axe", "mace", "rapier", "blade"]):
                    cat = "Weapon"
                elif any(w in lower_i for w in ["bow", "crossbow", "sling"]):
                    cat = "Weapon"
                elif any(w in lower_i for w in ["armor", "shield", "leather", "chain", "plate"]):
                    cat = "Armor"
                elif any(w in lower_i for w in ["tool", "kit", "lockpick", "thieves", "supplies"]):
                    cat = "Tool"
                elif any(w in lower_i for w in ["ration", "potion", "water"]):
                    cat = "Consumable"
                inv.append({
                    "name": item_str,
                    "qty": str(eq_qty).strip(),
                    "weight": str(eq_weight).strip(),
                    "category": cat,
                    "notes": ""
                })
        return inv

    def _extract_weapons(self, fields: Dict[str, Any], text: str = "") -> List[Dict[str, Any]]:
        weapons = []
        for i in range(1, 10):
            name_keys = [f"WpnName{i}", f"Wpn Name {i}", f"Wpn Name{i}"]
            if i == 1:
                name_keys.extend(["Wpn Name", "WpnName"])
            name = ""
            for k in name_keys:
                if fields.get(k):
                    name = str(fields[k]).strip()
                    break
            if name:
                atk = fields.get(f"Wpn{i} AtkBonus") or fields.get(f"Wpn{i}AtkBonus") or fields.get(f"WpnAtkBonus{i}") or "+0"
                dmg = fields.get(f"Wpn{i} Damage") or fields.get(f"Wpn{i}Damage") or fields.get(f"WpnDamage{i}") or "1d6"
                notes = fields.get(f"Wpn Notes {i}") or fields.get(f"Wpn Notes{i}") or fields.get(f"WpnNotes{i}") or ""
                weapons.append({
                    "name": name,
                    "atk_bonus": str(atk).strip(),
                    "damage": str(dmg).strip(),
                    "notes": str(notes).strip()
                })
        return weapons

    def _extract_saving_throws(self, fields: Dict[str, Any]) -> Dict[str, str]:
        stats = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]
        res = {}
        for s in stats:
            val = fields.get(f"{s}Save", fields.get(s, "+0"))
            res[s] = f"{int(val):+d}" if str(val).lstrip('+-').isdigit() else "+0"
        return res

    def _extract_currency(self, fields: Dict[str, Any]) -> Dict[str, int]:
        return {
            "cp": self._get_num(fields, ["CP"], 0),
            "sp": self._get_num(fields, ["SP"], 0),
            "ep": self._get_num(fields, ["EP"], 0),
            "gp": self._get_num(fields, ["GP"], 0),
            "pp": self._get_num(fields, ["PP"], 0)
        }

    def _extract_skills(self, fields: Dict[str, Any]) -> Dict[str, str]:
        skills = [
            "Acrobatics", "Animal Handling", "Arcana", "Athletics", "Deception",
            "History", "Insight", "Intimidation", "Investigation", "Medicine",
            "Nature", "Perception", "Performance", "Persuasion", "Religion",
            "Sleight of Hand", "Stealth", "Survival"
        ]
        res = {}
        for s in skills:
            k = s.replace(" ", "")
            bonus = fields.get(f"{k}Bonus", "+0")
            is_prof = fields.get(f"{k}Prof", "0") in ("1", "Yes", "true")
            res[s] = f"{bonus} (Proficient)" if is_prof else str(bonus)
        return res

    @staticmethod
    def _get_stat(fields: Dict[str, Any], keys: List[str], default: int = 10) -> int:
        for k in keys:
            for fk, fv in fields.items():
                if k.lower() in fk.lower() and str(fv).strip().isdigit():
                    return int(str(fv).strip())
        return default

    @staticmethod
    def _get_num(fields: Dict[str, Any], keys: List[str], default: int = 0) -> int:
        for k in keys:
            for fk, fv in fields.items():
                if k.lower() == fk.lower() or (len(k) > 4 and k.lower() in fk.lower()):
                    s_clean = re.sub(r'\(.*?\)', '', str(fv)).strip()
                    m = re.search(r'[-+]?\d+', s_clean)
                    if m:
                        try:
                            return int(m.group(0))
                        except ValueError:
                            pass
        return default

    @staticmethod
    def _regex_search(pattern: str, text: str) -> Optional[str]:
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1).strip() if m else None
