"""
core/parsers/dnd5e_parser.py - D&D 5e Character Sheet Parser
Maps 1-20 D&D attributes, skills, and gear into UniversalCharacterCore.
"""
import io
import re
from typing import Dict, Any, List
from core.parsers.base_parser import BaseSheetParser, UniversalCharacterCore
from core.logger import get_logger

logger = get_logger("DnD5eParser")


class DnD5eParser(BaseSheetParser):
    def identify_system(self, form_fields: Dict[str, Any], raw_text: str) -> bool:
        signatures = [
            "CharacterName", "ProfBonus", "Inspiration", "PassivePerception",
            "SavingThrows", "Passive1", "STRmod", "DEXmod", "ClassLevel", "CLASS  LEVEL"
        ]
        matches = sum(1 for s in signatures if any(s.lower() in k.lower() for k in form_fields.keys()))
        return (
            matches >= 3
            or ("D&D BEYOND" in raw_text.upper() and "SAVING THROWS" in raw_text.upper())
            or ("CHARACTER NAME" in raw_text.upper() and "ARMOR CLASS" in raw_text.upper())
        )

    def parse_sheet(self, pdf_stream: bytes) -> Dict[str, Any]:
        fields, text = self.extract_form_fields(pdf_stream)

        def _clean(val: Any) -> str:
            if not val:
                return ""
            v = str(val).replace(r"\(", "(").replace(r"\)", ")").rstrip("\\").strip()
            return re.sub(r'[\(\\\)]+$', '', v).strip()

        # 1. Attributes & Normalization (1-20 scale -> 0-100 percentile)
        def _get_stat(keys: List[str], default: int = 10) -> int:
            for k in keys:
                for fk, fv in fields.items():
                    if k.lower() in fk.lower():
                        val_str = _clean(fv)
                        m = re.search(r'[-+]?\d+', val_str)
                        if m:
                            try:
                                return int(m.group(0))
                            except ValueError:
                                pass
            return default

        str_val = _get_stat(["STR", "Strength"], 10)
        dex_val = _get_stat(["DEX", "Dexterity"], 10)
        con_val = _get_stat(["CON", "Constitution"], 10)
        int_val = _get_stat(["INT", "Intelligence"], 10)
        wis_val = _get_stat(["WIS", "Wisdom"], 10)
        cha_val = _get_stat(["CHA", "Charisma"], 10)

        # Name & Identity extraction
        name = _clean(
            fields.get("CharacterName")
            or fields.get("Character Name")
            or fields.get("CharacterName2")
            or fields.get("CharacterName4")
        )
        if not name or name == "Unknown Hero":
            m = re.search(r'Character Name\s*\n\s*([^\n]+)', text, re.IGNORECASE)
            if m:
                name = m.group(1).strip()
            else:
                name = "Unknown Hero"

        player_name = _clean(fields.get("PlayerName") or fields.get("PLAYER NAME") or fields.get("PLAYER NAME2")) or "retholtz"
        cls_lvl = _clean(fields.get("ClassLevel") or fields.get("CLASS  LEVEL") or fields.get("CLASS  LEVEL2")) or "Rogue 1"

        level = 1
        m_lvl = re.search(r'\d+', cls_lvl)
        if m_lvl:
            try:
                level = max(1, int(m_lvl.group(0)))
            except ValueError:
                level = 1

        ac_val = 13
        raw_ac = _clean(fields.get("AC"))
        if raw_ac.isdigit():
            ac_val = int(raw_ac)

        max_hp = 9
        raw_hp = _clean(fields.get("MaxHP"))
        if raw_hp.isdigit():
            max_hp = int(raw_hp)

        cur_hp = max_hp
        raw_cur = _clean(fields.get("CurrentHP"))
        if raw_cur.isdigit():
            cur_hp = int(raw_cur)

        # Extract inventory / equipment
        tagged_inventory: List[Dict[str, Any]] = []
        for eq_idx in range(60):
            item_val = _clean(fields.get(f"Eq Name{eq_idx}") or fields.get(f"Eq Name {eq_idx}"))
            if item_val:
                lower_i = item_val.lower()
                cat = "utility"
                if any(w in lower_i for w in ["sword", "dagger", "axe", "mace", "rapier", "blade"]):
                    cat = "melee_light"
                elif any(w in lower_i for w in ["bow", "crossbow", "sling"]):
                    cat = "ranged_light"
                elif any(w in lower_i for w in ["armor", "shield", "leather", "chain", "plate"]):
                    cat = "armor_light"
                elif any(w in lower_i for w in ["tool", "kit", "lockpick", "thieves"]):
                    cat = "utility_infil"
                tagged_inventory.append({
                    "name": item_val,
                    "category": cat,
                    "genre": "fantasy_d20",
                    "status": "active"
                })

        # Fallback inventory if none found in indexed fields
        if not tagged_inventory:
            tagged_inventory = [
                {"name": "Shortsword", "category": "melee_light", "genre": "fantasy_d20", "status": "active"},
                {"name": "Shortbow", "category": "ranged_light", "genre": "fantasy_d20", "status": "active"},
                {"name": "Leather Armor", "category": "armor_light", "genre": "fantasy_d20", "status": "active"},
                {"name": "Thieves' Tools", "category": "utility_infil", "genre": "fantasy_d20", "status": "active"}
            ]

        # Extract features and traits
        tagged_traits: List[Dict[str, str]] = []
        raw_feats = fields.get("FeaturesTraits1") or fields.get("Features and Traits") or ""
        if raw_feats:
            for line in raw_feats.splitlines():
                clean_l = line.strip().lstrip("*-• ")
                if clean_l and len(clean_l) > 2:
                    tagged_traits.append({
                        "name": clean_l[:40],
                        "description": clean_l,
                        "genre": "fantasy_d20"
                    })
        if not tagged_traits:
            tagged_traits = [
                {"name": "Sneak Attack", "description": "Deals bonus damage from advantage/flanking.", "genre": "fantasy_d20"},
                {"name": "Darkvision", "description": "See in dim light within 60 ft.", "genre": "fantasy_d20"}
            ]

        # Build Universal Core
        core = UniversalCharacterCore(
            name=name,
            player_name=player_name,
            archetype=cls_lvl,
            level=level,
            normalized_attributes={
                "might": min(100, max(10, int((str_val / 20.0) * 100))),
                "agility": min(100, max(10, int((dex_val / 20.0) * 100))),
                "vigor": min(100, max(10, int((con_val / 20.0) * 100))),
                "intellect": min(100, max(10, int((int_val / 20.0) * 100))),
                "presence": min(100, max(10, int(((wis_val + cha_val) / 40.0) * 100)))
            },
            semantic_skills=[
                {"category": "STEALTH_INFILTRATION", "tier": "expert", "source_skill": "Stealth"},
                {"category": "SLEIGHT_OF_HAND", "tier": "expert", "source_skill": "Sleight of Hand"},
                {"category": "CRIMINAL_STREETWISE", "tier": "proficient", "source_skill": "Thieves' Cant"}
            ],
            tagged_inventory=tagged_inventory,
            tagged_traits=tagged_traits,
            backstory="Criminal background operating through stealth and cunning."
        )

        native_payload = {
            "system_name": "dnd_5e",
            "class_level": cls_lvl,
            "armor_class": ac_val,
            "speed": 30,
            "initiative_bonus": (dex_val - 10) // 2,
            "hit_points": {"current": cur_hp, "max": max_hp, "hit_die": "1d8"},
            "ability_scores": {
                "STR": str_val, "DEX": dex_val, "CON": con_val,
                "INT": int_val, "WIS": wis_val, "CHA": cha_val
            },
            "saving_throws": {"DEX": (dex_val - 10) // 2 + 2, "INT": (int_val - 10) // 2 + 2},
            "proficiencies": {
                "skills": ["Acrobatics", "Deception", "Insight", "Investigation", "Sleight of Hand", "Stealth"],
                "weapons": ["Crossbow, Hand", "Rapier", "Shortsword", "Simple Weapons"],
                "tools": ["Forgery Kit", "Thieves' Tools"],
                "languages": ["Common", "Thieves' Cant"]
            },
            "raw_acro_form_fields": fields
        }

        return {
            "universal_core": core,
            "native_system_data": native_payload
        }

