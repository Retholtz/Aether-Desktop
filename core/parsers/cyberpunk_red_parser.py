"""
core/parsers/cyberpunk_red_parser.py - Cyberpunk RED Perks, Cyberware & Armor SP Parser
Extracts Role Abilities, Cyberware trees, weapons, and Armor Stopping Power (SP).
Includes visual Gemini Multimodal OCR fallback for scanned pre-gens.
"""
import re
from typing import Dict, Any, List
from core.parsers.base_parser import BaseSheetParser, UniversalCharacterCore
from core.logger import get_logger

logger = get_logger("CyberpunkRedParser")


class CyberpunkRedParser(BaseSheetParser):
    def identify_system(self, form_fields: Dict[str, Any], raw_text: str) -> bool:
        signatures = ["HUMANITY", "CYBERWARE", "DEATH SAVE", "SERIOUSLY WOUNDED", "ROLE ABILITY"]
        matches = sum(1 for s in signatures if s in raw_text.upper() or any(s.lower() in k.lower() for k in form_fields.keys()))
        return matches >= 2 or "CYBERPUNK" in raw_text.upper()

    def parse_sheet(self, pdf_stream: bytes) -> Dict[str, Any]:
        fields, text = self.extract_form_fields(pdf_stream)

        # Fallback for flat scanned sheets without fields
        if not fields or len(fields) < 3:
            try:
                from core.ttrpg_service import TTRPGService
                svc = TTRPGService()
                gem_data = svc._parse_character_with_gemini("cyberpunk_sheet.pdf", pdf_stream)
                if gem_data and gem_data.get("name"):
                    attrs = gem_data.get("attributes") or {}
                    stats = {
                        "INT": int(attrs.get("INT", 6)),
                        "REF": int(attrs.get("REF", 6)),
                        "DEX": int(attrs.get("DEX", 7)),
                        "TECH": int(attrs.get("TECH", 6)),
                        "COOL": int(attrs.get("COOL", 6)),
                        "WILL": int(attrs.get("WILL", 5)),
                        "LUCK": int(attrs.get("LUCK", 5)),
                        "MOVE": int(attrs.get("MOVE", 6)),
                        "BODY": int(attrs.get("BODY", 5)),
                        "EMP": int(attrs.get("EMP", 5))
                    }
                    role_str = gem_data.get("archetype") or "Solo 4"
                    role_parts = role_str.split()
                    role_name = role_parts[0] if role_parts else "Solo"
                    role_rank = int(role_parts[1]) if len(role_parts) > 1 and role_parts[1].isdigit() else 4

                    core = UniversalCharacterCore(
                        name=gem_data.get("name", "Edgerunner"),
                        player_name=gem_data.get("player_name") or "Player",
                        archetype=role_str,
                        level=role_rank,
                        normalized_attributes={
                            "might": min(100, stats["BODY"] * 10),
                            "agility": min(100, int(((stats["REF"] + stats["DEX"]) / 2.0) * 10)),
                            "vigor": min(100, stats["WILL"] * 10),
                            "intellect": min(100, int(((stats["INT"] + stats["TECH"]) / 2.0) * 10)),
                            "presence": min(100, int(((stats["COOL"] + stats["EMP"]) / 2.0) * 10))
                        },
                        semantic_skills=[],
                        tagged_inventory=[{"name": item, "category": "utility", "genre": "cybernetic_action", "status": "active"} for item in gem_data.get("inventory", [])],
                        tagged_traits=[{"name": "Trait", "description": t, "genre": "cybernetic_action"} for t in gem_data.get("features_and_traits", [])],
                        backstory=" ".join(gem_data.get("features_and_traits", []))
                    )

                    head_sp = int(gem_data.get("head_sp", 7))
                    body_sp = int(gem_data.get("body_sp", 7))
                    hp_val = int(gem_data.get("max_health", 35))

                    native_payload = {
                        "system_name": "cyberpunk_red",
                        "handle": gem_data.get("name", "Edgerunner"),
                        "role": role_name,
                        "role_ability": {
                            "name": self._infer_role_ability(role_name),
                            "rank": role_rank,
                            "notes": ""
                        },
                        "stats": stats,
                        "vitals": {
                            "hit_points": {"current": gem_data.get("current_health", hp_val), "max": hp_val, "seriously_wounded": hp_val // 2},
                            "humanity": {"current": int(gem_data.get("humanity", 45)), "max": stats["EMP"] * 10},
                            "armor": {
                                "head": {"sp_current": head_sp, "sp_max": head_sp},
                                "body": {"sp_current": body_sp, "sp_max": body_sp},
                                "shield": {"hp_current": 0, "hp_max": 0}
                            }
                        },
                        "cyberware": {"foundational_chassis": {}, "installed_items": [{"name": cw, "category": "General"} for cw in gem_data.get("cyberware", [])]},
                        "weapons": gem_data.get("weapons", []),
                        "raw_acro_form_fields": {}
                    }
                    return {
                        "universal_core": core,
                        "native_system_data": native_payload
                    }
            except Exception:
                pass

        handle = fields.get("Handle") or fields.get("CharacterName") or "Edgerunner"
        role_name = fields.get("Role") or "Solo"
        role_rank = self._get_num(fields, ["Rank", "RoleRank", "Role Ability Rank"], default=4)
        role_ability = fields.get("RoleAbility") or fields.get("Role Ability") or self._infer_role_ability(role_name)

        stats = {
            "INT": self._get_num(fields, ["INT"], 6),
            "REF": self._get_num(fields, ["REF"], 6),
            "DEX": self._get_num(fields, ["DEX"], 7),
            "TECH": self._get_num(fields, ["TECH"], 6),
            "COOL": self._get_num(fields, ["COOL"], 6),
            "WILL": self._get_num(fields, ["WILL"], 5),
            "LUCK": self._get_num(fields, ["LUCK"], 5),
            "MOVE": self._get_num(fields, ["MOVE"], 6),
            "BODY": self._get_num(fields, ["BODY"], 5),
            "EMP": self._get_num(fields, ["EMP"], 5)
        }

        head_sp = self._get_num(fields, ["Head_SP", "Armor_Head", "SP_Head"], default=7)
        body_sp = self._get_num(fields, ["Body_SP", "Armor_Body", "SP_Body"], default=7)
        shield_hp = self._get_num(fields, ["Shield_HP", "Shield"], default=0)

        hp_max = self._get_num(fields, ["HitPoints_Max", "HP_Max", "Hit Points"], default=35)
        humanity_cur = self._get_num(fields, ["Humanity_Current", "Humanity"], default=stats["EMP"] * 10)

        cyberware_suite = self.parse_cyberware_suite(fields, text)
        weapons = self.parse_weapons(fields, text)

        tagged_traits = [
            {
                "name": f"{role_ability} (Rank {role_rank})",
                "description": fields.get("RoleNotes", f"Primary role ability for {role_name}."),
                "genre": "cybernetic_action"
            }
        ]
        for cw in cyberware_suite["installed_items"]:
            tagged_traits.append({
                "name": cw["name"],
                "description": f"Category: {cw['category']}. {cw.get('details', '')}",
                "genre": "cybernetic_action"
            })

        core = UniversalCharacterCore(
            name=handle,
            player_name=fields.get("PlayerName", "retholtz"),
            archetype=f"{role_name} {role_rank}",
            level=role_rank,
            normalized_attributes={
                "might": min(100, stats["BODY"] * 10),
                "agility": min(100, int(((stats["REF"] + stats["DEX"]) / 2.0) * 10)),
                "vigor": min(100, stats["WILL"] * 10),
                "intellect": min(100, int(((stats["INT"] + stats["TECH"]) / 2.0) * 10)),
                "presence": min(100, int(((stats["COOL"] + stats["EMP"]) / 2.0) * 10))
            },
            semantic_skills=[
                {"category": "STEALTH_INFILTRATION", "tier": "expert", "source_skill": "Stealth"},
                {"category": "COMBAT_FIREARMS", "tier": "expert", "source_skill": "Handgun / Shoulder Arms"},
                {"category": "TECH_OPERATIONS", "tier": "proficient", "source_skill": "Cybertech"}
            ],
            tagged_inventory=[
                {"name": w["name"], "category": "firearm", "genre": "cybernetic_action", "damage": w["damage"]}
                for w in weapons
            ],
            tagged_traits=tagged_traits,
            backstory=fields.get("CulturalOrigins", "Street Infiltrator operating in the sprawl.")
        )

        native_payload = {
            "system_name": "cyberpunk_red",
            "handle": handle,
            "role": role_name,
            "role_ability": {
                "name": role_ability,
                "rank": role_rank,
                "notes": fields.get("RoleNotes", "")
            },
            "stats": stats,
            "vitals": {
                "hit_points": {"current": hp_max, "max": hp_max, "seriously_wounded": hp_max // 2},
                "humanity": {"current": humanity_cur, "max": stats["EMP"] * 10},
                "armor": {
                    "head": {"sp_current": head_sp, "sp_max": head_sp},
                    "body": {"sp_current": body_sp, "sp_max": body_sp},
                    "shield": {"hp_current": shield_hp, "hp_max": shield_hp}
                }
            },
            "cyberware": cyberware_suite,
            "weapons": weapons,
            "raw_acro_form_fields": fields
        }

        return {
            "universal_core": core,
            "native_system_data": native_payload
        }

    def parse_cyberware_suite(self, fields: Dict[str, Any], text: str) -> Dict[str, Any]:
        categories = [
            "Cyberaudio Suite", "Right Cybereye", "Left Cybereye",
            "Neural Link", "Right Cyberarm", "Left Cyberarm",
            "Right Cyberleg", "Left Cyberleg", "Internal Cyberware",
            "External Cyberware", "Fashionware", "Borgware"
        ]

        installed = []
        foundational_flags = {}

        for cat in categories:
            base_key = cat.replace(" ", "_").lower()
            # Check direct boolean or presence
            is_installed = any(
                fields.get(k) in ("Yes", "On", "1", "True")
                for k in (base_key, cat.replace(" ", "_"), cat)
            ) or f"[{cat}]" in text
            foundational_flags[cat] = is_installed

            if is_installed:
                installed.append({"name": cat, "category": "Foundational", "details": "Foundational Unit"})

            for slot_idx in range(1, 5):
                slot_val = (
                    fields.get(f"{base_key}_option_{slot_idx}") or
                    fields.get(f"{base_key}_opt_{slot_idx}") or
                    fields.get(f"{cat.replace(' ', '_')}_Option_{slot_idx}") or
                    fields.get(f"{cat.replace(' ', '_')}_Opt_{slot_idx}")
                )
                if slot_val and str(slot_val).strip():
                    installed.append({
                        "name": str(slot_val).strip(),
                        "category": cat,
                        "slot": slot_idx
                    })

        if not installed and text:
            for cat in categories:
                match = re.search(rf'{re.escape(cat)}\s*\n\s*Data:\s*([^\n]+)', text, re.IGNORECASE)
                if match and match.group(1).strip():
                    installed.append({"name": match.group(1).strip(), "category": cat})

        return {
            "foundational_chassis": foundational_flags,
            "installed_items": installed
        }

    def parse_weapons(self, fields: Dict[str, Any], text: str) -> List[Dict[str, Any]]:
        weapons = []
        for i in range(1, 6):
            w_name = fields.get(f"Weapon_{i}_Name") or fields.get(f"WeaponName_{i}")
            if w_name and str(w_name).strip():
                weapons.append({
                    "name": str(w_name).strip(),
                    "damage": str(fields.get(f"Weapon_{i}_DMG", "3d6")),
                    "ammo": self._get_num(fields, [f"Weapon_{i}_Ammo"], default=12),
                    "rof": self._get_num(fields, [f"Weapon_{i}_ROF"], default=1),
                    "notes": str(fields.get(f"Weapon_{i}_Notes", ""))
                })
        return weapons

    @staticmethod
    def _infer_role_ability(role: str) -> str:
        role_map = {
            "Solo": "Combat Awareness",
            "Netrunner": "Interface",
            "Tech": "Maker",
            "Medtech": "Medicine",
            "Media": "Credibility",
            "Exec": "Team",
            "Lawman": "Backup",
            "Fixer": "Operator",
            "Nomad": "Moto",
            "Rockerboy": "Charismatic Impact"
        }
        for k, v in role_map.items():
            if k.lower() in role.lower():
                return v
        return "Special Ability"

    @staticmethod
    def _get_num(fields: Dict[str, Any], keys: List[str], default: int = 0) -> int:
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
