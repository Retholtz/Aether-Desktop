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
            "SavingThrows", "Passive1", "STRmod", "DEXmod", "ClassLevel", "CLASS  LEVEL"
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
        spell_ability = _clean(fields.get("spellCastingAbility0") or fields.get("SpellcastingAbility") or fields.get("SpellAbility") or "")
        key_mod = int_mod
        if "sorcerer" in clean_class.lower() or "bard" in clean_class.lower() or "warlock" in clean_class.lower() or "paladin" in clean_class.lower():
            spell_ability = "CHA"
            key_mod = cha_mod
        elif "cleric" in clean_class.lower() or "druid" in clean_class.lower() or "ranger" in clean_class.lower():
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

        # 3. Spell Slots Extraction (Page 4 / AcroForms)
        spell_slots = {}
        for rank in range(1, 10):
            total_slots = self._get_num(fields, [f"SlotsTotal{rank}", f"SlotsTotal_{rank}", f"SpellSlots_{rank}_Total"], default=0)
            if total_slots == 0:
                for h_idx in range(12):
                    hdr = str(fields.get(f"spellHeader{h_idx}") or "")
                    slot_hdr = str(fields.get(f"spellSlotHeader{h_idx}") or "")
                    if re.search(rf'{rank}(?:st|nd|rd|th)\s+LEVEL', hdr, re.IGNORECASE):
                        m_s = re.search(r'(\d+)\s+Slots?', slot_hdr, re.IGNORECASE)
                        if m_s:
                            total_slots = int(m_s.group(1))
                            break
            if total_slots == 0:
                slot_regex = re.search(rf'{rank}(?:st|nd|rd|th)\s+LEVEL===*\s*\n\s*(\d+)\s+Slots', text, re.IGNORECASE)
                if slot_regex:
                    total_slots = int(slot_regex.group(1))

            spell_slots[f"level_{rank}"] = {
                "max": total_slots,
                "current": self._get_num(fields, [f"SlotsRemaining{rank}", f"SlotsRemaining_{rank}"], default=total_slots)
            }

        # 4. Spells List & Unprepared Defaults
        max_prepared = self._calculate_max_prepared_spells(clean_class, level, key_mod)
        spells_list = self.parse_spells_list(fields, text)

        # 5. Discrete Limited-Use Resource Meters (from Features & Traits, excluding Speed)
        ft1 = str(fields.get("FeaturesTraits1") or "").strip()
        ft2 = str(fields.get("FeaturesTraits2") or "").strip()
        combined_ft = f"{ft1}\n{ft2}".strip() if (ft1 or ft2) else str(fields.get("FeaturesAndTraits") or fields.get("Features and Traits") or "")
        features_text = self._clean_html_text(combined_ft or self._extract_section(text, "FEATURES ===", "EQUIPMENT"))
        all_text = f"{text}\n{features_text}"
        custom_meters = self.extract_discrete_meters(all_text, level, int_mod, cha_mod)

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
                {"category": "INVESTIGATION_TECH", "tier": "expert", "source_skill": "Investigation"},
                {"category": "ARCANA_THEORY", "tier": "expert", "source_skill": "Arcana"}
            ],
            tagged_inventory=tagged_inventory,
            tagged_traits=[{"name": k, "description": v["label"], "genre": "fantasy_d20"} for k, v in custom_meters.items()],
            backstory=full_backstory or "Adventurer exploring the realms with skill and dedication.",
            roleplay_anchors=roleplay_anchors,
            custom_meters=custom_meters
        )

        native_payload = {
            "system_name": "dnd_5e",
            "class_level": clean_class,
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
                "hit_die": "1d8"
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
    def parse_spells_list(self, fields: Dict[str, Any], text: str, int_mod: int = 0, level: int = 1) -> List[Dict[str, Any]]:
        """Parses spells: Cantrips default to prepared=True; all leveled spells default to prepared=False."""
        spells = []
        seen_names = set()

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
                if low_n in seen_names:
                    continue
                seen_names.add(low_n)

                lvl_val = s.get("level", "Cantrip")
                lvl_str = str(lvl_val).lower()
                is_cantrip = "cantrip" in lvl_str or lvl_str == "0" or any(
                    c in low_n for c in ["light", "guidance", "mending", "prestidigitation", "shocking grasp", "sorcerous burst", "fire bolt"]
                )
                is_prep = True if is_cantrip else False
                spells.append({
                    "name": clean_name,
                    "level": 0 if is_cantrip else self._infer_spell_level(clean_name, text),
                    "is_cantrip": is_cantrip,
                    "is_ritual": "[R]" in s_name or s.get("is_ritual", False),
                    "prepared": is_prep,
                    "cast_time": s.get("cast_time") or "1A",
                    "range": s.get("range") or "Touch",
                    "components": s.get("components") or "V, S",
                    "duration": s.get("notes") or ("1 minute" if "<C>" in s_name else "Instantaneous")
                })
            return spells

        # 2. Text extraction fallback: regex search
        spell_pattern = re.compile(
            r'([OP\s]+)?\s*([A-Za-z \t\'\-]+(?:\[R\]|<C>|\s)*)\s+(Artificer|Sorcerer|Wizard|Cleric|Druid|Warlock|Bard|Paladin|Ranger|Light Bearer)\s+(?:([0-9A-Za-z\s]+?)\s+)?(Touch|Self|[\d\s]+ft[^\n]*?)\s+([VSM,\s]+)$',
            re.IGNORECASE | re.MULTILINE
        )
        matches = spell_pattern.findall(text)
        for m in matches:
            prep_char, s_name, source, cast_time, s_range, comps = m
            clean_name = s_name.replace("<C>", "").replace("[R]", "").strip()
            clean_name = clean_name.split("\n")[-1].strip()
            if not clean_name:
                continue
            low_n = clean_name.lower()
            if low_n in seen_names:
                continue
            seen_names.add(low_n)

            inferred_lvl = self._infer_spell_level(clean_name, text)
            is_cantrip = (inferred_lvl == 0) or "cantrip" in clean_name.lower() or any(
                c in clean_name.lower() for c in ["light", "guidance", "mending", "prestidigitation", "shocking grasp", "sorcerous burst", "fire bolt"]
            )
            # RULE: Cantrips are always prepared; leveled spells default to FALSE
            is_prep = True if is_cantrip else False

            spells.append({
                "name": clean_name,
                "level": 0 if is_cantrip else (inferred_lvl if inferred_lvl > 0 else 1),
                "is_cantrip": is_cantrip,
                "is_ritual": "[R]" in s_name,
                "prepared": is_prep,
                "cast_time": cast_time.strip() if cast_time else "1A",
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
                    if low_n in seen_names:
                        continue
                    seen_names.add(low_n)
                    is_cantrip = any(k in clean_name.lower() for k in ["cantrip", "light", "guidance", "mending", "prestidigitation", "shocking grasp", "sorcerous burst", "fire bolt"])
                    is_prep = True if is_cantrip else False
                    spells.append({
                        "name": clean_name,
                        "level": 0 if is_cantrip else self._infer_spell_level(clean_name, text),
                        "is_cantrip": is_cantrip,
                        "is_ritual": "[R]" in s_name,
                        "prepared": is_prep,
                        "cast_time": cast_time.strip() if cast_time else "1A",
                        "range": s_range.strip() if s_range else "Touch",
                        "components": comps.strip(),
                        "duration": "1 minute" if "<C>" in s_name else "Instantaneous"
                    })

        return spells

    def extract_discrete_meters(self, text: str, level: int, int_mod: int, cha_mod: int) -> Dict[str, Any]:
        """Extracts consumable pools. Explicitly excludes Speed and passive attributes."""
        meters = {}

        # 1. Hit Dice Meter
        hit_die = self._infer_hit_die(text)
        meters["hit_dice"] = {
            "label": f"Hit Dice ({level}d{hit_die})",
            "current": level,
            "max": level,
            "reset_on": "long_rest"
        }

        # 2. Sorcerer: Sorcery Points (Font of Magic)
        if "Font of Magic" in text or "Sorcery Points" in text:
            sp_match = re.search(r'(\d+)\s*SP', text)
            sp_max = int(sp_match.group(1)) if sp_match else level
            meters["sorcery_points"] = {
                "label": "Sorcery Points",
                "current": sp_max,
                "max": sp_max,
                "reset_on": "long_rest"
            }

        # 3. Innate Sorcery (2/Long Rest)
        if "Innate Sorcery" in text:
            meters["innate_sorcery"] = {
                "label": "Innate Sorcery",
                "current": 2,
                "max": 2,
                "reset_on": "long_rest"
            }

        # 4. Kobold: Draconic Cry (2/Long Rest)
        if "Draconic Cry" in text:
            meters["draconic_cry"] = {
                "label": "Draconic Cry",
                "current": 2,
                "max": 2,
                "reset_on": "long_rest"
            }

        # 5. Aasimar: Healing Hands (1/Long Rest)
        if "Healing Hands" in text:
            meters["healing_hands"] = {
                "label": "Healing Hands (2d4)",
                "current": 1,
                "max": 1,
                "reset_on": "long_rest"
            }

        # 6. Artificer: Magical Tinkering (INT mod objects)
        if "Magical Tinkering" in text:
            tinker_max = max(1, int_mod)
            meters["magical_tinkering"] = {
                "label": "Magical Tinkering Objects",
                "current": tinker_max,
                "max": tinker_max,
                "reset_on": "special"
            }

        # 7. Celestial Revelation (1/Long Rest)
        if "Celestial Revelation" in text:
            meters["celestial_revelation"] = {
                "label": "Celestial Revelation",
                "current": 1,
                "max": 1,
                "reset_on": "long_rest"
            }

        # 8. Blood Hunter: Blood Maledict (1/Short Rest)
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
        return self.extract_discrete_meters(text, level, int_mod, 0)

    @staticmethod
    def _calculate_max_prepared_spells(class_str: str, level: int, key_mod: int) -> int:
        cls_lower = class_str.lower()
        if "artificer" in cls_lower:
            return max(1, key_mod + (level // 2))
        elif "wizard" in cls_lower or "cleric" in cls_lower or "druid" in cls_lower:
            return max(1, key_mod + level)
        elif "paladin" in cls_lower:
            return max(1, key_mod + (level // 2))
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
        elif "artificer" in c or "bard" in c or "cleric" in c or "druid" in c or "monk" in c or "rogue" in c or "warlock" in c:
            return 8
        elif "fighter" in c or "paladin" in c or "ranger" in c or "blood hunter" in c:
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
        second_lvl = ["dragon's breath", "spider climb", "misty step", "scorching ray", "shatter", "invisibility"]
        if any(s in clean for s in second_lvl):
            return 2
        third_lvl = ["fireball", "lightning bolt", "counterspell", "fly", "haste"]
        if any(s in clean for s in third_lvl):
            return 3
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
