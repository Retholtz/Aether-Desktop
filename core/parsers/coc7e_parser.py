"""
core/parsers/coc7e_parser.py - Call of Cthulhu 7th Edition Spell & Occult Parser
Extracts Magic Points, sanity costs, arcane tomes, mythos rituals, and characteristics.
Includes visual Gemini Multimodal OCR fallback for scanned pre-gens.
"""
import re
from typing import Dict, Any, List
from core.parsers.base_parser import BaseSheetParser, UniversalCharacterCore
from core.logger import get_logger

logger = get_logger("CoC7eParser")


class CoC7eParser(BaseSheetParser):
    def identify_system(self, form_fields: Dict[str, Any], raw_text: str) -> bool:
        signatures = [
            "SANITY", "CALL OF CTHULHU", "MAGIC POINTS", "ARCANE TOMES & SPELLS",
            "MYTHOS", "CTHULHU MYTHOS", "MAJOR WOUND", "INVESTIGATOR SKILLS", "INVESTIGATOR"
        ]
        up_text = (raw_text or "").upper()
        field_keys_up = [str(k).upper() for k in form_fields.keys()]
        matches = sum(1 for s in signatures if s in up_text or any(s.lower() in k.lower() for k in form_fields.keys()))
        return matches >= 2

    def parse_sheet(self, pdf_stream: bytes) -> Dict[str, Any]:
        fields, text = self.extract_form_fields(pdf_stream)

        # Flat scanned PDF with no form fields: use Gemini Multimodal OCR
        if not fields or len(fields) < 3:
            try:
                from core.ttrpg_service import TTRPGService
                svc = TTRPGService()
                gem_data = svc._parse_character_with_gemini("coc_sheet.pdf", pdf_stream)
                if gem_data and gem_data.get("name"):
                    attrs = gem_data.get("attributes") or {}
                    str_v = int(attrs.get("STR", 50))
                    dex_v = int(attrs.get("DEX", 50))
                    con_v = int(attrs.get("CON", 50))
                    int_v = int(attrs.get("INT", 50))
                    pow_v = int(attrs.get("POW", 50))
                    app_v = int(attrs.get("APP", 50))
                    siz_v = int(attrs.get("SIZ", 50))
                    edu_v = int(attrs.get("EDU", 50))

                    custom = gem_data.get("custom_meters") or {}
                    san_v = int(custom.get("sanity", {}).get("current", 50) if isinstance(custom.get("sanity"), dict) else 50)
                    luck_v = int(custom.get("luck", {}).get("current", 50) if isinstance(custom.get("luck"), dict) else 50)
                    mp_v = int(custom.get("magic_points", {}).get("current", 10) if isinstance(custom.get("magic_points"), dict) else 10)
                    move_v = int(custom.get("move_rate", {}).get("current", 8) if isinstance(custom.get("move_rate"), dict) else 8)

                    core = UniversalCharacterCore(
                        name=gem_data.get("name", "Investigator"),
                        player_name=gem_data.get("player_name") or "Player",
                        archetype=gem_data.get("archetype") or "Investigator",
                        level=1,
                        normalized_attributes={
                            "might": str_v,
                            "agility": dex_v,
                            "vigor": con_v,
                            "intellect": int_v,
                            "presence": max(pow_v, app_v)
                        },
                        semantic_skills=[],
                        tagged_inventory=[{"name": item, "category": "utility", "genre": "modern_horror", "status": "active"} for item in gem_data.get("inventory", [])],
                        tagged_traits=[{"name": "Trait", "description": t, "genre": "modern_horror"} for t in gem_data.get("features_and_traits", [])],
                        backstory=" ".join(gem_data.get("features_and_traits", []))
                    )

                    native_payload = {
                        "system_name": "coc_7e",
                        "occupation": gem_data.get("archetype") or "Investigator",
                        "characteristics": {
                            "STR": str_v, "CON": con_v, "SIZ": siz_v, "DEX": dex_v,
                            "APP": app_v, "INT": int_v, "POW": pow_v, "EDU": edu_v,
                            "Move": move_v
                        },
                        "vitals": {
                            "hit_points": {"current": gem_data.get("current_health", 13), "max": gem_data.get("max_health", 13)},
                            "sanity": {"current": san_v, "max": 99},
                            "luck": {"current": luck_v, "max": 99},
                            "magic_points": {"current": mp_v, "max": pow_v // 5},
                            "cthulhu_mythos": 0
                        },
                        "combat": {
                            "damage_bonus": "none",
                            "build": 0,
                            "dodge": dex_v // 2
                        },
                        "occult_data": {"has_occult_knowledge": False, "tomes": [], "spells": []},
                        "skills": gem_data.get("skills") or {},
                        "weapons": gem_data.get("weapons") or [],
                        "inventory": gem_data.get("inventory") or [],
                        "features_and_traits": gem_data.get("features_and_traits") or []
                    }
                    return {
                        "universal_core": core,
                        "native_system_data": native_payload
                    }
            except Exception:
                pass

        # 1. Characteristics & Vitals (Page 1)
        str_val = self._get_num(fields, ["STR", "Strength"], 50)
        dex_val = self._get_num(fields, ["DEX", "Dexterity"], 50)
        con_val = self._get_num(fields, ["CON", "Constitution"], 50)
        siz_val = self._get_num(fields, ["SIZ", "Size"], 50)
        app_val = self._get_num(fields, ["APP", "Appearance"], 50)
        int_val = self._get_num(fields, ["INT", "Intelligence", "IDEA"], 70)
        pow_val = self._get_num(fields, ["POW", "Power"], default=50)
        edu_val = self._get_num(fields, ["EDU", "Education", "KNOW"], 50)
        move_val = self._get_num(fields, ["Move", "MoveRate"], 8)

        calc_hp = max(1, (con_val + siz_val) // 10)
        hp_val = self._get_num(fields, ["HP", "HitPoints", "MaxHP"], calc_hp)
        calculated_max_mp = max(1, pow_val // 5)
        current_mp = self._get_num(fields, ["MagicPoints_Current", "MP_Current", "Magic Points", "MagicPoints", "MP"], default=calculated_max_mp)
        sanity_cur = self._get_num(fields, ["Sanity_Current", "SAN", "Sanity"], default=pow_val)
        mythos_rating = self._get_num(fields, ["Cthulhu Mythos", "Mythos", "CthulhuMythos"], default=0)
        luck_val = self._get_num(fields, ["Luck"], 50)
        dodge_val = self._get_num(fields, ["Dodge"], dex_val // 2)

        name = fields.get("CharacterName") or fields.get("Name") or fields.get("InvestigatorName") or "Investigator"
        occupation = fields.get("Occupation") or "Occultist / Academic"
        player_name = fields.get("PlayerName") or fields.get("Player") or "retholtz"

        # 2. Extract & Parse Arcane Tomes & Spells Block (Page 2)
        occult_library = self.parse_arcane_tomes_and_spells(fields, text)

        # 3. Universal Core Construction
        core = UniversalCharacterCore(
            name=name,
            player_name=player_name,
            archetype=occupation,
            level=1,
            normalized_attributes={
                "might": str_val,
                "agility": dex_val,
                "vigor": con_val,
                "intellect": int_val,
                "presence": pow_val
            },
            semantic_skills=[
                {"category": "OCCULT_MYTHOS", "tier": "expert" if mythos_rating > 15 else "proficient", "source_skill": f"Cthulhu Mythos ({mythos_rating}%)"},
                {"category": "INVESTIGATION_ACADEMIC", "tier": "expert", "source_skill": "Library Use (70%)"}
            ],
            tagged_inventory=[
                {"name": t["title"], "category": "occult_tome", "genre": "cosmic_horror_d100", "status": "active"}
                for t in occult_library["tomes"]
            ],
            tagged_traits=[
                {"name": s["name"], "description": f"Cost: {s['mp_cost']} MP, {s['san_cost']} SAN", "genre": "cosmic_horror_d100"}
                for s in occult_library["spells"]
            ],
            backstory=fields.get("PersonalDescription") or fields.get("Description") or "Keeper of forbidden occult knowledge."
        )

        characteristics = {
            "STR": str_val, "CON": con_val, "SIZ": siz_val, "DEX": dex_val,
            "APP": app_val, "INT": int_val, "POW": pow_val, "EDU": edu_val,
            "Move": move_val,
            "STR_half": str_val // 2, "STR_fifth": str_val // 5,
            "CON_half": con_val // 2, "CON_fifth": con_val // 5,
            "SIZ_half": siz_val // 2, "SIZ_fifth": siz_val // 5,
            "DEX_half": dex_val // 2, "DEX_fifth": dex_val // 5,
            "APP_half": app_val // 2, "APP_fifth": app_val // 5,
            "INT_half": int_val // 2, "INT_fifth": int_val // 5,
            "POW_half": pow_val // 2, "POW_fifth": pow_val // 5,
            "EDU_half": edu_val // 2, "EDU_fifth": edu_val // 5,
            "values": {
                k: {"regular": v, "half": v // 2, "fifth": v // 5}
                for k, v in [
                    ("STR", str_val), ("CON", con_val), ("SIZ", siz_val), ("DEX", dex_val),
                    ("APP", app_val), ("INT", int_val), ("POW", pow_val), ("EDU", edu_val)
                ]
            }
        }

        # Parse investigator skill percentiles from AcroForms into structured dictionary
        parsed_skills = {}
        for fk, fv in fields.items():
            k_str = str(fk).strip()
            v_str = str(fv).strip().replace("%", "")
            if v_str.isdigit():
                val = int(v_str)
                if 1 <= val <= 100 and not any(ign in k_str.lower() for ign in ["hp", "mp", "san", "luck", "age", "year", "page", "damage"]):
                    parsed_skills[k_str] = {
                        "regular": val,
                        "half": val // 2,
                        "fifth": val // 5
                    }

        native_payload = {
            "system_name": "coc_7e",
            "occupation": occupation,
            "characteristics": characteristics,
            "skills": parsed_skills,
            "vitals": {
                "hit_points": {"current": hp_val, "max": hp_val},
                "magic_points": {"current": current_mp, "max": calculated_max_mp},
                "sanity": {"current": sanity_cur, "max": 99 - mythos_rating},
                "luck": {"current": luck_val, "max": 99},
                "cthulhu_mythos": mythos_rating
            },
            "combat": {
                "damage_bonus": "none",
                "build": 0,
                "dodge": dodge_val
            },
            "occult_data": occult_library,
            "raw_acro_form_fields": fields
        }

        return {
            "universal_core": core,
            "native_system_data": native_payload
        }

    def parse_arcane_tomes_and_spells(self, fields: Dict[str, Any], raw_text: str) -> Dict[str, Any]:
        """
        Parses Page 2 'Arcane Tomes & Spells' field.
        Separates grimoires from actionable spells and extracts MP / SAN costs.
        """
        raw_content = ""
        # 1. Search AcroForm fields
        for k, v in fields.items():
            if any(term in k.lower() for term in ["tome", "spell", "arcane", "spells_tomes"]):
                raw_content += f"\n{v}"

        # 2. Search Text Fallback (Page 2 section headers)
        if not raw_content.strip() and raw_text:
            match = re.search(r'Arcane Tomes & Spells\s*([\s\S]*?)(?:Encounters with Strange Entities|GEAR & POSSESSIONS|WEALTH|$)', raw_text, re.IGNORECASE)
            if match:
                raw_content = match.group(1).strip()

        parsed_spells = []
        parsed_tomes = []

        lines = [line.strip() for line in raw_content.splitlines() if line.strip()]
        for line in lines:
            # Check if entry is a recognized Tome format (contains Latin, Book, Volume, Transcript, Grimoire)
            if re.search(r'\b(tome|grimoire|vol|volume|edition|latin|manuscript|necronomicon|cultes|de\s+vermis)\b', line, re.IGNORECASE):
                parsed_tomes.append({
                    "title": line,
                    "language": "Latin" if "latin" in line.lower() else "English",
                    "status": "Studied"
                })
                continue

            # Parse spell patterns: E.g., "Shriveling (Cost: 3 MP, 1d4 Sanity)" or "Call Cthugha - 5 MP"
            spell_match = re.match(r'^([^(\-]+)(?:[\(\-](.*)[\)]?)?$', line)
            if spell_match:
                s_name = spell_match.group(1).strip()
                s_details = (spell_match.group(2) or "").rstrip(' )').strip()

                # Extract MP Cost
                mp_match = re.search(r'(\d+)\s*(?:MP|Magic\s*Points?)', s_details, re.IGNORECASE)
                mp_cost = int(mp_match.group(1)) if mp_match else 1

                # Extract Sanity Cost
                san_match = re.search(r'(\d+|1d\d+)\s*(?:SAN|Sanity)', s_details, re.IGNORECASE)
                san_cost = san_match.group(1) if san_match else "1d4"

                parsed_spells.append({
                    "name": s_name,
                    "mp_cost": mp_cost,
                    "san_cost": san_cost,
                    "raw_details": s_details
                })

        return {
            "has_occult_knowledge": len(parsed_spells) > 0 or len(parsed_tomes) > 0,
            "tomes": parsed_tomes,
            "spells": parsed_spells
        }

    @staticmethod
    def _get_num(fields: Dict[str, Any], keys: List[str], default: int = 50) -> int:
        for k in keys:
            for fk, fv in fields.items():
                if k.lower() in fk.lower():
                    s_val = str(fv).strip()
                    if s_val.isdigit():
                        return int(s_val)
                    m = re.search(r'\d+', s_val)
                    if m:
                        return int(m.group(0))
        return default
