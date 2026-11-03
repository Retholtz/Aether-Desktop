"""
core/parsers/coc7e_parser.py - Call of Cthulhu 7th Edition Parser
Maps 1-100 Percentile Characteristics (STR, CON, SIZ, DEX, APP, INT, POW, EDU), Sanity, Luck,
Magic Points, and percentile skills into UniversalCharacterCore and Schema 2.0 native data.
"""
from typing import Dict, Any, List
import re
from core.parsers.base_parser import BaseSheetParser, UniversalCharacterCore


class CoC7eParser(BaseSheetParser):
    def identify_system(self, form_fields: Dict[str, Any], raw_text: str) -> bool:
        signatures = ["SANITY", "CALL OF CTHULHU", "MAGIC POINTS", "MAJOR WOUND", "CTHULHU MYTHOS", "INVESTIGATOR SKILLS", "INVESTIGATOR"]
        matches = sum(1 for s in signatures if s in raw_text.upper() or any(s.lower() in k.lower() for k in form_fields.keys()))
        return matches >= 2

    def parse_sheet(self, pdf_stream: bytes) -> Dict[str, Any]:
        fields, text = self.extract_form_fields(pdf_stream)

        # If flat scanned PDF with no form fields, use Gemini Multimodal OCR
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
                        "system_name": "call_of_cthulhu_7e",
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
                            "magic_points": {"current": mp_v, "max": pow_v // 5}
                        },
                        "combat": {
                            "damage_bonus": "none",
                            "build": 0,
                            "dodge": dex_v // 2
                        },
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

        def _get_val(keys, default=50):
            for k in keys:
                for fk, fv in fields.items():
                    if k.lower() in fk.lower():
                        m = re.search(r'\d+', str(fv))
                        if m:
                            return int(m.group(0))
            return default

        def _get_text(keys, default=""):
            for k in keys:
                for fk, fv in fields.items():
                    if k.lower() in fk.lower() and str(fv).strip():
                        return str(fv).strip()
            return default

        str_val = _get_val(["STR", "Strength"], 50)
        dex_val = _get_val(["DEX", "Dexterity"], 50)
        con_val = _get_val(["CON", "Constitution"], 50)
        siz_val = _get_val(["SIZ", "Size"], 50)
        app_val = _get_val(["APP", "Appearance"], 50)
        int_val = _get_val(["INT", "Intelligence", "Idea"], 50)
        pow_val = _get_val(["POW", "Power"], 50)
        edu_val = _get_val(["EDU", "Education", "Know"], 50)
        move_val = _get_val(["Move", "MoveRate"], 8)

        calc_hp = max(1, (con_val + siz_val) // 10)
        hp_val = _get_val(["HP", "HitPoints", "MaxHP"], calc_hp)
        san_val = _get_val(["Sanity", "SAN"], pow_val)
        luck_val = _get_val(["Luck"], 50)
        mp_val = _get_val(["MagicPoints", "MP"], pow_val // 5)
        dodge_val = _get_val(["Dodge"], dex_val // 2)

        name = _get_text(["CharacterName", "Name", "InvestigatorName"], "Investigator")
        occupation = _get_text(["Occupation"], "History Student")

        # Parse skills from form fields
        skills_dict: Dict[str, str] = {}
        for fk, fv in fields.items():
            if str(fv).strip().isdigit() and any(w in fk.lower() for w in ("skill", "brawl", "firearms", "library", "spot", "stealth", "history", "psychology", "charm", "drive")):
                skills_dict[fk] = f"{fv}%"

        # Backstory & traits
        desc = _get_text(["PersonalDescription", "Description"])
        ideology = _get_text(["Ideology", "Beliefs"])
        people = _get_text(["SignificantPeople"])
        locs = _get_text(["MeaningfulLocations"])
        possessions = _get_text(["TreasuredPossessions"])
        traits_txt = _get_text(["Traits"])

        traits_list = []
        if desc: traits_list.append(f"Personal Description: {desc}")
        if ideology: traits_list.append(f"Ideology/Beliefs: {ideology}")
        if people: traits_list.append(f"Significant People: {people}")
        if locs: traits_list.append(f"Meaningful Locations: {locs}")
        if possessions: traits_list.append(f"Treasured Possessions: {possessions}")
        if traits_txt: traits_list.append(f"Traits: {traits_txt}")

        gear_txt = _get_text(["Gear", "Possessions", "Equipment"])
        inv_list = [g.strip() for g in gear_txt.splitlines() if g.strip()] if gear_txt else []

        core = UniversalCharacterCore(
            name=name,
            player_name=_get_text(["Player", "PlayerName"], "Player"),
            archetype=occupation,
            level=1,
            normalized_attributes={
                "might": str_val,
                "agility": dex_val,
                "vigor": con_val,
                "intellect": int_val,
                "presence": max(pow_val, app_val)
            },
            semantic_skills=[],
            tagged_inventory=[{"name": item, "category": "utility", "genre": "modern_horror", "status": "active"} for item in inv_list],
            tagged_traits=[{"name": "Trait", "description": t, "genre": "modern_horror"} for t in traits_list],
            backstory=" ".join(traits_list)
        )

        native_payload = {
            "system_name": "call_of_cthulhu_7e",
            "occupation": occupation,
            "characteristics": {
                "STR": str_val, "CON": con_val, "SIZ": siz_val, "DEX": dex_val,
                "APP": app_val, "INT": int_val, "POW": pow_val, "EDU": edu_val,
                "Move": move_val
            },
            "vitals": {
                "hit_points": {"current": hp_val, "max": hp_val},
                "sanity": {"current": san_val, "max": 99},
                "luck": {"current": luck_val, "max": 99},
                "magic_points": {"current": mp_val, "max": pow_val // 5}
            },
            "combat": {
                "damage_bonus": _get_text(["DamageBonus", "DB"], "none"),
                "build": _get_val(["Build"], 0),
                "dodge": dodge_val
            },
            "skills": skills_dict,
            "weapons": [{"name": "Unarmed", "atk_bonus": f"{dex_val // 2}%", "damage": "1d3 + db", "notes": "Attacks: 1"}],
            "inventory": inv_list,
            "features_and_traits": traits_list,
            "raw_acro_form_fields": fields
        }

        return {
            "universal_core": core,
            "native_system_data": native_payload
        }


