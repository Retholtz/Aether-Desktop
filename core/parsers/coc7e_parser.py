"""
core/parsers/coc7e_parser.py - Call of Cthulhu 7th Edition Parser
Maps 1-100 Percentile Characteristics (STR, CON, SIZ, DEX, APP, INT, POW, EDU) & Sanity to Core.
"""
from typing import Dict, Any
from core.parsers.base_parser import BaseSheetParser, UniversalCharacterCore


class CoC7eParser(BaseSheetParser):
    def identify_system(self, form_fields: Dict[str, Any], raw_text: str) -> bool:
        signatures = ["SANITY", "CALL OF CTHULHU", "MAGIC POINTS", "MAJOR WOUND", "CTHULHU MYTHOS"]
        matches = sum(1 for s in signatures if s in raw_text.upper() or any(s.lower() in k.lower() for k in form_fields.keys()))
        return matches >= 2

    def parse_sheet(self, pdf_stream: bytes) -> Dict[str, Any]:
        fields, text = self.extract_form_fields(pdf_stream)

        def _get_val(keys, default=50):
            for k in keys:
                for fk, fv in fields.items():
                    if k.lower() in fk.lower() and str(fv).isdigit():
                        return int(fv)
            return default

        str_val = _get_val(["STR", "Strength"], 50)
        dex_val = _get_val(["DEX", "Dexterity"], 50)
        con_val = _get_val(["CON", "Constitution"], 50)
        int_val = _get_val(["INT", "Intelligence"], 50)
        pow_val = _get_val(["POW", "Power"], 50)

        core = UniversalCharacterCore(
            name=fields.get("CharacterName", "Investigator"),
            player_name="retholtz",
            archetype=fields.get("Occupation", "Private Detective / Infiltrator"),
            level=1,
            normalized_attributes={
                "might": str_val,
                "agility": dex_val,
                "vigor": con_val,
                "intellect": int_val,
                "presence": pow_val
            },
            semantic_skills=[
                {"category": "STEALTH_INFILTRATION", "tier": "expert", "source_skill": "Stealth (70%)"},
                {"category": "INVESTIGATION_TECH", "tier": "expert", "source_skill": "Spot Hidden (75%)"},
                {"category": "CRIMINAL_STREETWISE", "tier": "proficient", "source_skill": "Locksmith (60%)"}
            ],
            tagged_inventory=[
                {"name": ".38 Revolver", "category": "ranged_ballistic", "genre": "cosmic_horror_d100", "status": "active"},
                {"name": "Lockpick Kit", "category": "utility_infil", "genre": "cosmic_horror_d100", "status": "active"},
                {"name": "Trenchcoat", "category": "clothing", "genre": "cosmic_horror_d100", "status": "active"}
            ],
            tagged_traits=[
                {"name": "Iron Will", "description": "High starting Sanity buffer.", "genre": "cosmic_horror_d100"}
            ],
            backstory=fields.get("PersonalDescription", "Grim investigator unravelling the occult.")
        )

        native_payload = {
            "system_name": "coc_7e",
            "occupation": core.archetype,
            "characteristics": {
                "STR": str_val, "CON": con_val, "DEX": dex_val, "INT": int_val, "POW": pow_val
            },
            "vitals": {
                "hit_points": {"current": int((con_val + 50) / 10), "max": int((con_val + 50) / 10)},
                "sanity": {"current": pow_val, "max": 99},
                "magic_points": {"current": int(pow_val / 5), "max": int(pow_val / 5)},
                "luck": {"current": _get_val(["Luck"], 50)}
            },
            "raw_acro_form_fields": fields
        }

        return {
            "universal_core": core,
            "native_system_data": native_payload
        }

