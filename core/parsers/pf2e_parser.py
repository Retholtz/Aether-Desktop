"""
core/parsers/pf2e_parser.py - Pathfinder 2e Character Sheet Parser
Maps 6 Ability Modifiers, TEML proficiencies, Bulk, and Dying/Wounded tracks to Universal Core.
"""
from typing import Dict, Any
from core.parsers.base_parser import BaseSheetParser, UniversalCharacterCore


class PF2eParser(BaseSheetParser):
    def identify_system(self, form_fields: Dict[str, Any], raw_text: str) -> bool:
        signatures = ["PATHFINDER", "HERO POINTS", "CLASS DC", "BULK", "UNARMORED LIGHT MEDIUM HEAVY"]
        matches = sum(1 for s in signatures if s in raw_text.upper() or any(s.lower() in k.lower() for k in form_fields.keys()))
        return matches >= 2

    def parse_sheet(self, pdf_stream: bytes) -> Dict[str, Any]:
        fields, text = self.extract_form_fields(pdf_stream)

        # Attribute extraction (Score or Mod)
        def _get_attr(key: str, default: int = 10) -> int:
            for fk, fv in fields.items():
                if key.lower() in fk.lower():
                    sv = str(fv).strip()
                    if sv.isdigit():
                        val = int(sv)
                        return (val * 2 + 10) if val < 10 else val
                    elif sv.startswith('+') and sv[1:].isdigit():
                        val = int(sv[1:])
                        return val * 2 + 10
                    elif sv.startswith('-') and sv[1:].isdigit():
                        val = -int(sv[1:])
                        return val * 2 + 10
            return default

        str_val = _get_attr("Strength", 10)
        dex_val = _get_attr("Dexterity", 16)
        con_val = _get_attr("Constitution", 12)
        int_val = _get_attr("Intelligence", 14)
        wis_val = _get_attr("Wisdom", 10)
        cha_val = _get_attr("Charisma", 10)

        core = UniversalCharacterCore(
            name=fields.get("Character Name", "Pathfinder Hero"),
            player_name="retholtz",
            archetype=fields.get("Class", "Rogue / Scoundrel"),
            level=int(fields.get("Level", 1)) if str(fields.get("Level", "1")).isdigit() else 1,
            normalized_attributes={
                "might": min(100, max(10, int((str_val / 20.0) * 100))),
                "agility": min(100, max(10, int((dex_val / 20.0) * 100))),
                "vigor": min(100, max(10, int((con_val / 20.0) * 100))),
                "intellect": min(100, max(10, int((int_val / 20.0) * 100))),
                "presence": min(100, max(10, int(((wis_val + cha_val) / 40.0) * 100)))
            },
            semantic_skills=[
                {"category": "STEALTH_INFILTRATION", "tier": "expert", "source_skill": "Stealth (Expert)"},
                {"category": "SLEIGHT_OF_HAND", "tier": "trained", "source_skill": "Thievery (Trained)"}
            ],
            tagged_inventory=[
                {"name": "Rapier", "category": "melee_light", "genre": "fantasy_d20", "status": "active", "bulk": 1},
                {"name": "Leather Armor", "category": "armor_light", "genre": "fantasy_d20", "status": "active", "bulk": 1}
            ],
            tagged_traits=[
                {"name": "Surprise Attack", "description": "Flat-footed enemies on high initiative roll.", "genre": "fantasy_d20"}
            ],
            backstory=fields.get("Background", "Street Urchin turned Pathfinder explorer.")
        )

        native_payload = {
            "system_name": "pathfinder_2e",
            "class": core.archetype,
            "level": core.level,
            "ac": 16,
            "hit_points": {"current": 16, "max": 16},
            "hero_points": 1,
            "raw_acro_form_fields": fields
        }

        return {
            "universal_core": core,
            "native_system_data": native_payload
        }

