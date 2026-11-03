"""
core/parsers/cyberpunk_red_parser.py - Cyberpunk RED Character Sheet Parser
Maps 1-10 STATS (REF, DEX, TECH, COOL, WILL, BODY, EMP) and Armor SP to Universal Core.
"""
from typing import Dict, Any
from core.parsers.base_parser import BaseSheetParser, UniversalCharacterCore


class CyberpunkRedParser(BaseSheetParser):
    def identify_system(self, form_fields: Dict[str, Any], raw_text: str) -> bool:
        signatures = ["HUMANITY", "CYBERWARE", "DEATH SAVE", "SERIOUSLY WOUNDED", "ROLE ABILITY"]
        matches = sum(1 for s in signatures if s in raw_text.upper() or any(s.lower() in k.lower() for k in form_fields.keys()))
        return matches >= 2 or "CYBERPUNK" in raw_text.upper()

    def parse_sheet(self, pdf_stream: bytes) -> Dict[str, Any]:
        fields, text = self.extract_form_fields(pdf_stream)

        # 1-10 Stat Extraction
        def _get_stat(keys, default=5):
            for k in keys:
                for fk, fv in fields.items():
                    if k.lower() in fk.lower() and str(fv).isdigit():
                        return int(fv)
            return default

        ref_val = _get_stat(["REF"], 6)
        dex_val = _get_stat(["DEX"], 7)
        body_val = _get_stat(["BODY"], 5)
        will_val = _get_stat(["WILL"], 5)
        int_val = _get_stat(["INT"], 6)
        tech_val = _get_stat(["TECH"], 6)
        cool_val = _get_stat(["COOL"], 6)
        emp_val = _get_stat(["EMP"], 5)

        # Build Universal Core (1-10 scale -> 0-100 percentile)
        core = UniversalCharacterCore(
            name=fields.get("Handle", "V / Edgerunner"),
            player_name="retholtz",
            archetype=fields.get("Role", "Solo / Infiltrator"),
            level=int(fields.get("RoleRank", 4)) if str(fields.get("RoleRank", "")).isdigit() else 4,
            normalized_attributes={
                "might": min(100, body_val * 10),
                "agility": min(100, int(((ref_val + dex_val) / 2.0) * 10)),
                "vigor": min(100, will_val * 10),
                "intellect": min(100, int(((int_val + tech_val) / 2.0) * 10)),
                "presence": min(100, int(((cool_val + emp_val) / 2.0) * 10))
            },
            semantic_skills=[
                {"category": "STEALTH_INFILTRATION", "tier": "expert", "source_skill": "Stealth"},
                {"category": "INVESTIGATION_TECH", "tier": "proficient", "source_skill": "Electronics/Security Tech"},
                {"category": "COMBAT_RANGED", "tier": "proficient", "source_skill": "Handgun"}
            ],
            tagged_inventory=[
                {"name": "Heavy Pistol", "category": "ranged_ballistic", "genre": "cybernetic_action", "status": "active"},
                {"name": "Kevlar Body Armor", "category": "armor_ballistic", "genre": "cybernetic_action", "status": "active", "sp": 7},
                {"name": "Agent / Smartphone", "category": "tech_comms", "genre": "cybernetic_action", "status": "active"}
            ],
            tagged_traits=[
                {"name": "Neural Link", "description": "Interface jacks for smartgun & cyberware link.", "genre": "cybernetic_action"}
            ],
            backstory=fields.get("CulturalOrigins", "Night City Street Infiltrator")
        )

        native_payload = {
            "system_name": "cyberpunk_red",
            "handle": core.name,
            "role": core.archetype,
            "humanity": {"current": 45, "max": 50},
            "hit_points": {"current": 35, "max": 35, "seriously_wounded": 18},
            "armor": {"head_sp": 7, "body_sp": 7},
            "stats": {
                "INT": int_val, "REF": ref_val, "DEX": dex_val, "TECH": tech_val,
                "COOL": cool_val, "WILL": will_val, "LUCK": 5, "MOVE": 6, "BODY": body_val, "EMP": emp_val
            },
            "raw_acro_form_fields": fields
        }

        return {
            "universal_core": core,
            "native_system_data": native_payload
        }

