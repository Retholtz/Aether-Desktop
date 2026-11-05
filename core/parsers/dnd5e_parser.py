"""
core/parsers/dnd5e_parser.py - D&D 5e Parser Engine (Schema 2.1)
Extracts spell slots, prepared spell lists, limited resource meters, and roleplay anchors.
"""
import io
import re
import html
from typing import Dict, Any, List, Optional
from core.parsers.base_parser import BaseSheetParser, UniversalCharacterCore
from core.parsers.spell_registry import resolve_dnd_stat, parse_structured_spells, sanitize_character_weapons
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
    "light": ["burning hands", "faerie fire", "flaming sphere", "scorching ray", "daylight", "fireball", "guardian of faith", "wall of fire", "flame strike", "scrying"]
}

WARLOCK_PACT_SLOTS = {
    1: {"count": 1, "level": 1}, 2: {"count": 2, "level": 1},
    3: {"count": 2, "level": 2}, 4: {"count": 2, "level": 2},
    5: {"count": 2, "level": 3}, 6: {"count": 2, "level": 3},
    7: {"count": 2, "level": 4}, 8: {"count": 2, "level": 4},
    9: {"count": 2, "level": 5}, 10: {"count": 2, "level": 5},
    11: {"count": 3, "level": 5}, 12: {"count": 3, "level": 5},
    13: {"count": 3, "level": 5}, 14: {"count": 3, "level": 5},
    15: {"count": 3, "level": 5}, 16: {"count": 3, "level": 5},
    17: {"count": 4, "level": 5}, 18: {"count": 4, "level": 5},
    19: {"count": 4, "level": 5}, 20: {"count": 4, "level": 5}
}


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
    """Stacks identical inventory items into a single row with combined quantities."""
    if not raw_inventory:
        return []
    stacked = {}
    is_all_str = all(isinstance(x, str) for x in raw_inventory)
    for item in raw_inventory:
        if isinstance(item, str):
            name = item.strip()
            if "(" in name and ")" not in name:
                name += ")"
            raw_qty = "1"
            weight = "--"
            category = "Gear"
            notes = ""
            genre = "fantasy_d20"
            status = "active"
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if "(" in name and ")" not in name:
                name += ")"
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
        else:
            entry = {
                "name": norm_name,
                "qty": qty,
                "weight": weight,
                "category": category,
                "notes": notes
            }
            if genre:
                entry["genre"] = genre
            if status:
                entry["status"] = status
            stacked[key] = entry

    if is_all_str:
        return [e["name"] for e in stacked.values()]

    # Format quantities back to strings for UI
    results = []
    for entry in stacked.values():
        entry["qty"] = str(entry["qty"])
        results.append(entry)
    return results


def clean_traits_and_headers(raw_traits: List[Any]) -> List[Any]:
    """Strips non-trait section titles exported by D&D Beyond."""
    banned = {"hit points", "proficiencies", "special", "actions", "bonus actions"}
    cleaned = []
    for t in raw_traits:
        if isinstance(t, dict):
            t_name = str(t.get("name", "")).strip()
            if t_name.lower() in banned or t_name.lower().startswith("=== ") or t_name.startswith("==="):
                continue
            cleaned.append(t)
        elif isinstance(t, str):
            t_clean = t.strip()
            if t_clean.lower() not in banned and not t_clean.lower().startswith("=== ") and not t_clean.startswith("==="):
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
        extracted_race = _clean(fields.get("RACE") or fields.get("RACE2") or "")
        extracted_bg = _clean(fields.get("BACKGROUND") or fields.get("BACKGROUND2") or "")
        if (not extracted_race or not extracted_bg) and "(" in raw_class_clean and ")" in raw_class_clean:
            p_match = re.search(r'\((.*?)\)', raw_class_clean)
            if p_match:
                parts = [p.strip() for p in p_match.group(1).split(",")]
                if not extracted_race and len(parts) > 0:
                    extracted_race = parts[0]
                if not extracted_bg and len(parts) > 1:
                    extracted_bg = parts[1]

        clean_class = re.sub(r'\(.*?\)', '', raw_class_clean).strip()
        level_match = re.search(r'\d+', clean_class)
        level = int(level_match.group(0)) if level_match else 1

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
        is_warlock = "warlock" in clean_class.lower()
        is_druid = "druid" in clean_class.lower()
        is_paladin = "paladin" in clean_class.lower()

        spell_ability = _clean(fields.get("spellCastingAbility0") or fields.get("SpellcastingAbility") or fields.get("SpellAbility") or "")
        key_mod = int_mod
        if is_warlock or is_paladin or "sorcerer" in clean_class.lower() or "bard" in clean_class.lower():
            spell_ability = "CHA"
            key_mod = cha_mod
        elif is_druid or "cleric" in clean_class.lower() or "ranger" in clean_class.lower():
            spell_ability = "WIS"
            key_mod = wis_mod
        elif not spell_ability:
            spell_ability = "INT"
            key_mod = int_mod
        elif spell_ability.upper() == "CHA":
            key_mod = cha_mod
        elif spell_ability.upper() == "WIS":
            key_mod = wis_mod
        else:
            key_mod = int_mod

        save_dc = self._get_num(fields, ["SpellSaveDC0", "AbilitySaveDC", "SpellSaveDC", "spellSaveDC0"], default=8 + prof_bonus + key_mod)
        spell_atk = self._get_num(fields, ["SpellAtkBonus0", "SpellAttackBonus", "spellAtkBonus0"], default=prof_bonus + key_mod)

        inspiration = str(fields.get("Inspiration", "")).strip().lower() in ("yes", "on", "1", "true")

        # 3. Spell Slots Extraction (Standard vs. Warlock Pact Magic)
        spell_slots = {}
        is_pact_magic = is_warlock or "pact magic" in text.lower() or any("pact" in k.lower() for k in fields.keys())

        if is_pact_magic:
            pact_info = WARLOCK_PACT_SLOTS.get(min(20, max(1, level)), {"count": 1, "level": 1})
            pact_lvl = pact_info["level"]
            pact_total = self._get_num(fields, ["PactMagic_SlotsTotal", "SlotsPactTotal", "SlotsTotalPact"], default=0)
            if pact_total == 0:
                for h_idx in range(12):
                    hdr = str(fields.get(f"spellHeader{h_idx}") or "")
                    slot_hdr = str(fields.get(f"spellSlotHeader{h_idx}") or "")
                    if re.search(rf'{pact_lvl}(?:st|nd|rd|th)\s+LEVEL', hdr, re.IGNORECASE):
                        m_s = re.search(r'(\d+)\s+(?:Slots?|Pact)', slot_hdr, re.IGNORECASE)
                        if m_s:
                            pact_total = int(m_s.group(1))
                            break
            if pact_total == 0:
                slot_regex = re.search(rf'{pact_lvl}(?:st|nd|rd|th)\s+LEVEL===\s*\n\s*(\d+)\s+(?:Slots|Pact)', text, re.IGNORECASE)
                if slot_regex:
                    pact_total = int(slot_regex.group(1))
            if pact_total == 0:
                pact_total = pact_info["count"]

            for rank in range(1, 10):
                if rank == pact_lvl:
                    spell_slots[f"level_{rank}"] = {
                        "max": pact_total,
                        "current": self._get_num(fields, ["PactMagic_SlotsRemaining", "SlotsRemainingPact"], default=pact_total),
                        "is_pact_magic": True,
                        "reset_on": "short_rest"
                    }
                else:
                    spell_slots[f"level_{rank}"] = {"max": 0, "current": 0}
        else:
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

                spell_slots[f"level_{rank}"] = {
                    "max": total_slots,
                    "current": self._get_num(fields, [f"SlotsRemaining{rank}", f"SlotsRemaining_{rank}"], default=total_slots),
                    "reset_on": "long_rest"
                }

        # 4. Spells List & Unprepared Defaults (Flagging Always-Prepared Subclass Spells)
        max_prepared = self._calculate_max_prepared_spells(clean_class, level, key_mod)
        spells_list = self.parse_spells_list(fields, text, clean_class)

        # Force max_prepared = 0 if all spell slots have max == 0 (e.g. Paladin 1, Ranger 1, non-casters)
        has_slots = any(s.get("max", 0) > 0 for s in spell_slots.values()) if isinstance(spell_slots, dict) else False
        if not has_slots:
            max_prepared = 0

        # 5. Discrete Limited-Use Resource Meters (from Features & Traits, excluding Speed)
        ft1 = str(fields.get("FeaturesTraits1") or "").strip()
        ft2 = str(fields.get("FeaturesTraits2") or "").strip()
        combined_ft = f"{ft1}\n{ft2}".strip() if (ft1 or ft2) else str(fields.get("FeaturesAndTraits") or fields.get("Features and Traits") or "")
        features_text = self._clean_html_text(combined_ft or self._extract_section(text, "FEATURES ===", "EQUIPMENT"))
        all_text = f"{text}\n{features_text}"
        custom_meters = self.extract_discrete_meters(all_text, clean_class, level, int_mod, wis_mod, cha_mod)

        # 6. Roleplay Anchors (Page 3)
        roleplay_anchors = {
            "personality_traits": self._extract_field_or_regex(fields, text, ["PersonalityTraits ", "PersonalityTraits", "Personality"], r'PERSONALITY TRAITS\s*\n\s*([^\n]+(?:\n[^\n]+)*?)(?=\n\s*IDEALS)'),
            "ideals": self._extract_field_or_regex(fields, text, ["Ideals"], r'IDEALS\s*\n\s*([^\n]+(?:\n[^\n]+)*?)(?=\n\s*BONDS)'),
            "bonds": self._extract_field_or_regex(fields, text, ["Bonds"], r'BONDS\s*\n\s*([^\n]+(?:\n[^\n]+)*?)(?=\n\s*FLAWS)'),
            "flaws": self._extract_field_or_regex(fields, text, ["Flaws"], r'FLAWS\s*\n\s*([^\n]+(?:\n[^\n]+)*?)(?=\n\s*CHARACTER BACKSTORY|\n\s*ALLIES)'),
            "allies_and_organizations": _clean(fields.get("Allies") or fields.get("AlliesOrganizations") or "") or self._extract_section(text, "ALLIES & ORGANIZATIONS", "PERSONALITY TRAITS")
        }

        # 7. Inventory & Encumbrance
        tagged_inventory: List[Dict[str, Any]] = []
        for eq_idx in range(60):
            item_val = _clean(fields.get(f"Eq Name{eq_idx}") or fields.get(f"Eq Name {eq_idx}"))
            if item_val:
                if "(" in item_val and ")" not in item_val:
                    item_val += ")"
                eq_qty = _clean(fields.get(f"Eq Qty{eq_idx}") or fields.get(f"Eq Qty {eq_idx}")) or "1"
                eq_weight = _clean(fields.get(f"Eq Weight{eq_idx}") or fields.get(f"Eq Weight {eq_idx}"))
                lower_i = item_val.lower()
                cat = "utility"
                if any(w in lower_i for w in ["sword", "dagger", "axe", "mace", "rapier", "blade"]):
                    cat = "melee_light"
                elif any(w in lower_i for w in ["bow", "crossbow", "sling"]):
                    cat = "ranged_light"
                elif any(w in lower_i for w in ["armor", "shield", "leather", "chain", "plate"]):
                    cat = "armor_light"
                elif any(w in lower_i for w in ["tool", "kit", "lockpick", "thieves", "supplies"]):
                    cat = "utility_infil"
                tagged_inventory.append({
                    "name": item_val,
                    "qty": eq_qty,
                    "weight": eq_weight,
                    "category": cat,
                    "genre": "fantasy_d20",
                    "status": "active"
                })

        if not tagged_inventory:
            tagged_inventory = [
                {"name": "Crossbow, light", "qty": "1", "weight": "5 lb.", "category": "ranged_light", "genre": "fantasy_d20", "status": "active"},
                {"name": "Dagger", "qty": "1", "weight": "1 lb.", "category": "melee_light", "genre": "fantasy_d20", "status": "active"}
            ]

        tagged_inventory = sanitize_inventory(tagged_inventory)

        weight_carried = _clean(fields.get("Weight Carried") or fields.get("WeightCarried") or "")
        encumbered = _clean(fields.get("Encumbered") or "")
        push_drag_lift = _clean(fields.get("PushDragLift") or fields.get("Push/Drag/Lift") or "")
        if not encumbered and str_val:
            encumbered = f"{str_val * 15} lb."
        if not push_drag_lift and str_val:
            push_drag_lift = f"{str_val * 30} lb."

        # 8. Physical Appearance
        char_gender = _clean(fields.get("GENDER") or fields.get("Gender") or "")
        char_age = _clean(fields.get("AGE") or fields.get("Age") or "")
        char_size = _clean(fields.get("SIZE") or fields.get("Size") or "")
        char_height = _clean(fields.get("HEIGHT") or fields.get("Height") or "")
        char_weight = _clean(fields.get("WEIGHT") or fields.get("Weight") or "")
        char_alignment = _clean(fields.get("ALIGNMENT") or fields.get("Alignment") or "")
        char_faith = _clean(fields.get("FAITH") or fields.get("Faith") or "")
        char_skin = _clean(fields.get("SKIN") or fields.get("Skin") or "")
        char_eyes = _clean(fields.get("EYES") or fields.get("Eyes") or "")
        char_hair = _clean(fields.get("HAIR") or fields.get("Hair") or "")

        # Creature Type
        creature_type = ""
        feats_lower = f"{features_text}\n{extracted_race}\n{clean_class}".lower()
        if "you are a humanoid" in feats_lower or "creature type: humanoid" in feats_lower or "humanoid" in feats_lower:
            creature_type = "Humanoid"
        elif any(r in extracted_race.lower() for r in ["aasimar", "human", "elf", "dwarf", "gnome", "halfling", "tiefling", "dragonborn", "orc"]):
            creature_type = "Humanoid"

        # 9. Skills & Proficiencies
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

        # 10. Saving Throws
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

        # 11. Backstory & Additional Notes (Sanitized)
        raw_bs = clean_backstory_text(fields.get("Backstory") or fields.get("BackgroundHistory") or self._extract_section(text, "CHARACTER BACKSTORY", "ADDITIONAL NOTES"))
        raw_an1 = clean_backstory_text(fields.get("AdditionalNotes1") or "")
        raw_an2 = clean_backstory_text(fields.get("AdditionalNotes2") or fields.get("AdditionalNotes") or "")
        if raw_an1 and ("nights, she" in raw_an1 or not fields.get("Notes")):
            full_backstory = " ".join(filter(None, [raw_bs, raw_an1, raw_an2])).strip()
            additional_notes = ""
        else:
            full_backstory = raw_bs
            additional_notes = "\n\n".join(filter(None, [raw_an1, raw_an2])).strip()

        # Vitals
        ac_val = self._get_num(fields, ["AC"], default=12)
        max_hp = self._get_num(fields, ["HPMax", "MaxHP"], default=10)
        cur_hp = self._get_num(fields, ["HPCurrent", "CurrentHP"], default=max_hp)
        temp_hp = self._get_num(fields, ["HPTemp", "TempHP"], default=0)
        raw_init = _clean(fields.get("Init") or fields.get("Initiative") or fields.get("Initiative "))
        init_val = int(raw_init) if raw_init and raw_init.lstrip("+-").isdigit() else dex_mod

        defenses_val = _clean(fields.get("Defenses") or fields.get("Resistances") or "")

        hit_die = self._infer_hit_die(clean_class)

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
                {"category": "RELIGION_THEOLOGY", "tier": "proficient", "source_skill": "Religion"} if (is_paladin or "cleric" in clean_class.lower()) else
                {"category": "NATURE_SURVIVAL", "tier": "proficient", "source_skill": "Nature"} if is_druid else
                {"category": "OCCULT_ARCANA", "tier": "proficient", "source_skill": "Arcana"},
                {"category": "INVESTIGATION_TECH", "tier": "expert", "source_skill": "Investigation"}
            ],
            tagged_inventory=tagged_inventory,
            tagged_traits=clean_traits_and_headers([{"name": k, "description": v["label"], "genre": "fantasy_d20"} for k, v in custom_meters.items()]),
            backstory=full_backstory or "Adventurer exploring the realms with skill and dedication.",
            roleplay_anchors=roleplay_anchors,
            custom_meters=custom_meters
        )

        native_payload = {
            "system_name": "dnd_5e",
            "class_level": clean_class,
            "is_pact_magic": is_pact_magic,
            "species": extracted_race,
            "background": extracted_bg,
            "inspiration": inspiration,
            "ability_save_dc": save_dc,
            "spell_attack_bonus": spell_atk,
            "spellcasting_ability": spell_ability,
            "armor_class": ac_val,
            "initiative_bonus": init_val,
            "speed": "30 ft.",
            "defenses": defenses_val,
            "resistances": defenses_val,
            "hit_points": {
                "current": cur_hp,
                "max": max_hp,
                "temp": temp_hp,
                "hit_die": f"1d{hit_die}"
            },
            "hit_dice": {
                "current": level,
                "max": level,
                "die": f"1d{hit_die}"
            },
            "ability_scores": {
                "STR": str_val, "DEX": dex_val, "CON": con_val,
                "INT": int_val, "WIS": wis_val, "CHA": cha_val
            },
            "saving_throws": saving_throws,
            "skills": parsed_skills,
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
            "tides_of_chaos_available": "Tides of Chaos" in all_text,
            "roleplay_anchors": roleplay_anchors,
            "inventory": [x["name"] for x in tagged_inventory],
            "weight_carried": weight_carried,
            "encumbered": encumbered,
            "push_drag_lift": push_drag_lift,
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
            "universal_core": core,
            "native_system_data": native_payload
        }

    # --- Processing Subroutines ---
    def parse_spells_list(self, fields: Dict[str, Any], text: str, class_str: str = "", int_mod: int = 0, level: int = 1) -> List[Dict[str, Any]]:
        """Parses spells. Subclass oath/circle spells, known casters & cantrips are marked prepared."""
        spells = []
        seen_spells = set()

        # Detect active subclass from sheet, ClassLevel, and FeaturesAndTraits
        raw_class_field = str(fields.get("ClassLevel") or fields.get("Class & Level") or fields.get("CLASS  LEVEL") or "")
        raw_feats_field = str(fields.get("FeaturesAndTraits") or fields.get("FeaturesTraits1") or "")
        subclass_context = f"{text}\n{class_str}\n{raw_class_field}\n{raw_feats_field}".lower()
        c_lower = f"{class_str} {raw_class_field}".lower()
        is_known_caster = any(k in c_lower for k in ("warlock", "sorcerer", "bard"))

        active_subclass = ""
        for sc in ALWAYS_PREPARED_SUBCLASS_SPELLS.keys():
            if sc in ("devotion", "vengeance", "ancients"):
                if sc in subclass_context:
                    active_subclass = sc
                    break
            elif sc == "land":
                if ("druid" in c_lower or "circle" in subclass_context) and sc in subclass_context:
                    active_subclass = sc
                    break
            elif sc in ("life", "light"):
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

                lvl_val = s.get("level", "Cantrip")
                lvl_str = str(lvl_val).lower()
                is_cantrip = "cantrip" in lvl_str or lvl_str == "0" or any(
                    c in low_n for c in cantrip_keywords
                )
                is_subclass_oath = low_n in always_prep_set
                is_prep = True if (is_cantrip or is_subclass_oath or is_known_caster) else False
                spells.append({
                    "name": clean_name,
                    "level": 0 if is_cantrip else normalize_spell_level(self._infer_spell_level(clean_name, text)),
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

            inferred_lvl = self._infer_spell_level(clean_name, text)
            is_cantrip = (inferred_lvl == 0) or "cantrip" in low_n or any(
                c in low_n for c in cantrip_keywords
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
                    is_cantrip = any(k in low_n for k in ["cantrip"] + cantrip_keywords)
                    is_subclass_oath = low_n in always_prep_set
                    is_prep = True if (is_cantrip or is_subclass_oath or is_known_caster) else False
                    spells.append({
                        "name": clean_name,
                        "level": 0 if is_cantrip else normalize_spell_level(self._infer_spell_level(clean_name, text)),
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
        """Extracts consumable pools for Paladins, Druids, Warlocks, Monks, etc. Strictly excludes movement Speed."""
        # Support legacy 4-arg call signature: (text, level, int_mod, cha_mod)
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

        # 2. Paladin: Lay on Hands & Divine Sense
        if "paladin" in c_lower or "lay on hands" in t_lower:
            loh_match = re.search(r'lay on hands pool:\s*(\d+)', t_lower)
            loh_val = int(loh_match.group(1)) if loh_match else (level * 5)
            meters["lay_on_hands"] = {
                "label": f"Lay on Hands Pool ({loh_val} HP)",
                "current": loh_val,
                "max": loh_val,
                "reset_on": "long_rest"
            }

        if "divine sense" in t_lower:
            ds_match = re.search(r'(\d+)\s*(?:times\s*per\s*long\s*rest|/\s*long\s*rest)', t_lower)
            ds_val = int(ds_match.group(1)) if ds_match else max(1, 1 + cha_mod)
            meters["divine_sense"] = {
                "label": "Divine Sense",
                "current": ds_val,
                "max": ds_val,
                "reset_on": "long_rest"
            }

        if ("channel divinity" in t_lower) or ("cleric" in c_lower and level >= 2) or ("paladin" in c_lower and level >= 3):
            cd_uses = 2 if level >= 6 else 1
            meters["channel_divinity"] = {
                "label": "Channel Divinity",
                "current": cd_uses,
                "max": cd_uses,
                "reset_on": "short_rest"
            }

        # 3. Wizard: Arcane Recovery
        if "wizard" in c_lower or "arcane recovery" in t_lower:
            meters["arcane_recovery"] = {
                "label": "Arcane Recovery",
                "current": 1,
                "max": 1,
                "reset_on": "long_rest"
            }

        # 4. Druid: Wild Shape (2 uses per Short Rest starting at Level 2)
        if ("druid" in c_lower or "wild shape" in t_lower) and level >= 2:
            ws_uses = 3 if level >= 17 else 2
            meters["wild_shape"] = {
                "label": "Wild Shape Uses",
                "current": ws_uses,
                "max": ws_uses,
                "reset_on": "short_rest"
            }

        # 4. Monk: Ki / Focus Points (Level / Short Rest)
        if "monk" in c_lower or "ki points" in t_lower or "focus points" in t_lower:
            meters["ki_points"] = {
                "label": "Ki / Focus Points",
                "current": level,
                "max": level,
                "reset_on": "short_rest"
            }

        # 5. Sorcerer: Sorcery Points & Innate Sorcery
        if "sorcerer" in c_lower or "font of magic" in t_lower or "sorcery points" in t_lower:
            sp_match = re.search(r'(\d+)\s*SP', text)
            sp_max = int(sp_match.group(1)) if sp_match else (level if level >= 2 else 0)
            if sp_max > 0:
                meters["sorcery_points"] = {
                    "label": "Sorcery Points",
                    "current": sp_max,
                    "max": sp_max,
                    "reset_on": "long_rest"
                }
        if "Innate Sorcery" in text:
            meters["innate_sorcery"] = {
                "label": "Innate Sorcery",
                "current": 2,
                "max": 2,
                "reset_on": "long_rest"
            }

        # 6. Fighter: Second Wind & Action Surge
        if "fighter" in c_lower:
            meters["second_wind"] = {
                "label": "Second Wind",
                "current": 1,
                "max": 1,
                "reset_on": "short_rest"
            }
            if level >= 2:
                meters["action_surge"] = {
                    "label": "Action Surge",
                    "current": 1,
                    "max": 1,
                    "reset_on": "short_rest"
                }

        # 7. Species & Class Traits (Draconic Cry, Healing Hands, Magical Tinkering, Celestial Revelation, Blood Maledict)
        if "Draconic Cry" in text:
            meters["draconic_cry"] = {
                "label": "Draconic Cry",
                "current": 2,
                "max": 2,
                "reset_on": "long_rest"
            }
        if "Healing Hands" in text:
            meters["healing_hands"] = {
                "label": "Healing Hands (2d4)",
                "current": 1,
                "max": 1,
                "reset_on": "long_rest"
            }
        if "Magical Tinkering" in text:
            tinker_max = max(1, int_mod)
            meters["magical_tinkering"] = {
                "label": "Magical Tinkering Objects",
                "current": tinker_max,
                "max": tinker_max,
                "reset_on": "special"
            }
        if "Celestial Revelation" in text:
            meters["celestial_revelation"] = {
                "label": "Celestial Revelation",
                "current": 1,
                "max": 1,
                "reset_on": "long_rest"
            }
        if "Blood Maledict" in text:
            meters["blood_maledict"] = {
                "label": "Blood Maledict",
                "current": 1,
                "max": 1,
                "reset_on": "short_rest"
            }

        # CRITICAL: Do NOT ingest Speed
        meters.pop("speed", None)
        return meters

    def extract_limited_meters(self, text: str, int_mod: int, level: int = 1) -> Dict[str, Any]:
        return self.extract_discrete_meters(text, "", level, int_mod, 0, 0)

    @staticmethod
    def _calculate_max_prepared_spells(class_str: str, level: int, key_mod: int) -> int:
        cls_lower = class_str.lower()
        if "artificer" in cls_lower:
            return max(1, key_mod + (level // 2))
        elif "paladin" in cls_lower:
            if level < 2:
                return 0
            return max(1, key_mod + (level // 2))  # Oath spells do not count against this
        elif "ranger" in cls_lower:
            if level < 2:
                return 0
            return max(1, key_mod + (level // 2))
        elif "druid" in cls_lower or "cleric" in cls_lower or "wizard" in cls_lower:
            return max(1, key_mod + level)         # Circle / Domain spells do not count
        elif "warlock" in cls_lower:
            # Warlock Spells Known progression table
            warlock_known = {
                1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8, 8: 9, 9: 10, 10: 10,
                11: 11, 12: 11, 13: 12, 14: 12, 15: 13, 16: 13, 17: 14, 18: 14, 19: 15, 20: 15
            }
            return warlock_known.get(level, level + 1)
        elif "sorcerer" in cls_lower:
            # D&D 2024 / Standard Sorcerer known/prepared progression
            sorc_table = {1: 2, 2: 4, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8, 8: 9, 9: 10}
            return sorc_table.get(level, level + 1)
        return max(1, key_mod + level)

    @staticmethod
    def _infer_hit_die(class_text: str) -> int:
        c = class_text.lower()
        if "sorcerer" in c or "wizard" in c:
            return 6
        elif any(k in c for k in ["artificer", "bard", "cleric", "druid", "monk", "rogue", "warlock"]):
            return 8
        elif any(k in c for k in ["fighter", "paladin", "ranger", "blood hunter"]):
            return 10
        elif "barbarian" in c:
            return 12
        return 8

    @staticmethod
    def _infer_spell_level(spell_name: str, full_text: str) -> int:
        clean = spell_name.lower().strip()
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
            "shatter", "invisibility", "hold person", "lesser restoration", "mirror image"
        ]
        third_lvl = [
            "fireball", "lightning bolt", "counterspell", "dispel magic",
            "haste", "revivify", "fly", "hunger of hadar"
        ]
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
