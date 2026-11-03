"""
core/transmutation_engine.py - Cross-Genre Character Refactoring & Gear Transmutation
Scales normalized core attributes into target system distributions and quarantines anachronisms.
"""
import copy
from typing import Dict, Any, List, Optional
from core.parsers.base_parser import UniversalCharacterCore


class TransmutationEngine:
    # Scale conversion mappings
    @staticmethod
    def scale_attributes(normalized: Dict[str, int], target_genre: str) -> Dict[str, Any]:
        """
        Translates 0-100 normalized core attributes to target system stats.
        - fantasy_d20: STR, DEX, CON, INT, WIS, CHA (Scale 3-20)
        - cybernetic_action: INT, REF, DEX, TECH, COOL, WILL, BODY, EMP (Scale 1-10)
        - cosmic_horror_d100: STR, CON, DEX, INT, POW (Scale 1-100)
        """
        might = normalized.get("might", 50)
        agility = normalized.get("agility", 50)
        vigor = normalized.get("vigor", 50)
        intellect = normalized.get("intellect", 50)
        presence = normalized.get("presence", 50)

        if target_genre == "fantasy_d20":
            return {
                "STR": max(3, min(20, int((might / 100.0) * 20))),
                "DEX": max(3, min(20, int((agility / 100.0) * 20))),
                "CON": max(3, min(20, int((vigor / 100.0) * 20))),
                "INT": max(3, min(20, int((intellect / 100.0) * 20))),
                "WIS": max(3, min(20, int((presence / 100.0) * 20))),
                "CHA": max(3, min(20, int((presence / 100.0) * 20)))
            }

        elif target_genre == "cybernetic_action":
            return {
                "BODY": max(1, min(10, int((might / 100.0) * 10))),
                "REF": max(1, min(10, int((agility / 100.0) * 10))),
                "DEX": max(1, min(10, int((agility / 100.0) * 10))),
                "WILL": max(1, min(10, int((vigor / 100.0) * 10))),
                "INT": max(1, min(10, int((intellect / 100.0) * 10))),
                "TECH": max(1, min(10, int((intellect / 100.0) * 10))),
                "COOL": max(1, min(10, int((presence / 100.0) * 10))),
                "EMP": max(1, min(10, int((presence / 100.0) * 10)))
            }

        elif target_genre == "cosmic_horror_d100":
            return {
                "STR": might,
                "DEX": agility,
                "CON": vigor,
                "INT": intellect,
                "POW": presence
            }

        return normalized

    @classmethod
    def refactor_character(
        cls,
        root_character_file: Dict[str, Any],
        target_genre: str,
        transmute_gear: bool = True
    ) -> Dict[str, Any]:
        """
        Creates a linked variant child branch from a canonical root.
        Applies attribute scaling and setting quarantine/transmutation.
        """
        root_core = root_character_file.get("universal_core", {})
        root_id = root_character_file.get("id", "root")

        char_name = root_core.get("name") if hasattr(root_core, "get") else getattr(root_core, "name", "Hero")
        char_level = root_core.get("level", 1) if hasattr(root_core, "get") else getattr(root_core, "level", 1)
        norm_attrs = root_core.get("normalized_attributes", {}) if hasattr(root_core, "get") else getattr(root_core, "normalized_attributes", {})
        inventory = root_core.get("tagged_inventory", []) if hasattr(root_core, "get") else getattr(root_core, "tagged_inventory", [])

        variant_id = f"{root_id}_{target_genre}"
        variant = {
            "schema_version": "2.0",
            "id": variant_id,
            "is_root": False,
            "parent_id": root_id,
            "native_genre": root_character_file.get("native_genre"),
            "current_genre": target_genre,
            "name": f"{char_name} ({target_genre.replace('_', ' ').title()} Variant)",
            "level": char_level,
            "universal_core": copy.deepcopy(root_core.to_dict() if hasattr(root_core, "to_dict") else root_core),
            "target_system_attributes": cls.scale_attributes(norm_attrs, target_genre),
            "active_inventory": [],
            "quarantined_stash": []
        }

        # Transmutation & Quarantine Pipeline
        for item in inventory:
            item_genre = item.get("genre", variant["native_genre"])

            if item_genre == target_genre:
                variant["active_inventory"].append(item)
            else:
                if transmute_gear:
                    transmuted = cls._transmute_item(item, target_genre)
                    if transmuted:
                        variant["active_inventory"].append(transmuted)
                        continue

                # Quarantine if cannot be transmuted
                variant["quarantined_stash"].append({
                    "original_item": item.get("name", "Unknown item"),
                    "reason": f"Inoperable in {target_genre} environment."
                })

        return variant

    @staticmethod
    def _transmute_item(item: Dict[str, Any], target_genre: str) -> Optional[Dict[str, Any]]:
        cat = item.get("category", "")
        name = item.get("name", "")

        # Fantasy -> Cyberpunk
        if target_genre == "cybernetic_action":
            if cat == "melee_light":
                return {"name": f"Vibro-Blade ({name})", "category": "light_melee", "damage": "1d6", "genre": target_genre}
            elif cat == "ranged_light":
                return {"name": "Light Auto-Pistol", "category": "ranged_ballistic", "ammo": 15, "genre": target_genre}
            elif cat == "armor_light":
                return {"name": "Sub-Dermal Weave Vest", "category": "armor_ballistic", "sp": 7, "genre": target_genre}
            elif cat == "utility_infil":
                return {"name": "Electronic Lockpick Suite", "category": "tech_kit", "genre": target_genre}

        # Cyberpunk -> Fantasy
        elif target_genre == "fantasy_d20":
            if "pistol" in name.lower() or cat == "ranged_ballistic":
                return {"name": "Repeating Hand Crossbow", "category": "ranged_light", "damage": "1d6", "genre": target_genre}
            elif "kevlar" in name.lower() or cat == "armor_ballistic":
                return {"name": "Hardened Studded Leather", "category": "armor_light", "ac_bonus": 2, "genre": target_genre}

        # Any -> Call of Cthulhu
        elif target_genre == "cosmic_horror_d100":
            if "pistol" in name.lower():
                return {"name": ".32 Revolver", "category": "ranged_ballistic", "damage": "1d8", "genre": target_genre}
            elif cat == "utility_infil":
                return {"name": "Set of Skeleton Keys", "category": "utility_infil", "genre": target_genre}

        return None

