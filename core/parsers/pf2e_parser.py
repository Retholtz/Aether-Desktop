"""
core/parsers/pf2e_parser.py - Pathfinder 2e Character & Spellcasting Parser
Extracts traditions, spell attack/DC, rank 1-10 slots, focus pools, cantrips, and universal attributes.
Includes visual Gemini Multimodal OCR fallback for scanned pre-gens (e.g. Amiri, Merisiel).
"""
import io
import re
from typing import Dict, Any, List, Optional, Tuple
from core.parsers.base_parser import BaseSheetParser, UniversalCharacterCore
from core.logger import get_logger

logger = get_logger("PF2eParser")


class PF2eParser(BaseSheetParser):
    def identify_system(self, form_fields: Dict[str, Any], raw_text: str) -> bool:
        signatures = [
            "PATHFINDER", "PAIZO", "CLASS DC", "MAGICAL TRADITION", "SPELL SLOTS",
            "FOCUS SPELLS", "CANTRIPS", "BULK", "HERO POINTS", "ANCESTRY", "HERITAGE",
            "STRIKES", "FORTITUDE", "REFLEX", "WILL"
        ]
        up_text = (raw_text or "").upper()
        field_keys_up = [str(k).upper() for k in form_fields.keys()]

        # Immediate direct hit on primary identifiers
        if "PATHFINDER" in up_text or "PAIZO" in up_text or "CLASS DC" in up_text:
            return True
        if any("PATHFINDER" in k or "CLASS DC" in k or "BULK" in k for k in field_keys_up):
            return True

        matches = sum(1 for s in signatures if s in up_text or any(s in k for k in field_keys_up))
        return matches >= 2

    def parse_sheet(self, pdf_stream: bytes) -> Dict[str, Any]:
        fields, text = self.extract_form_fields(pdf_stream)

        # Flat scanned PDF or pregen without fillable form fields: use Gemini Multimodal OCR
        if not fields or len(fields) < 3:
            try:
                from core.ttrpg_service import TTRPGService
                svc = TTRPGService()
                gem_data = svc._parse_character_with_gemini("pathfinder_sheet.pdf", pdf_stream)
                if gem_data and gem_data.get("name"):
                    attrs = gem_data.get("attributes") or {}
                    str_v = int(attrs.get("Strength") or attrs.get("STR") or attrs.get("Might") or 10)
                    dex_v = int(attrs.get("Dexterity") or attrs.get("DEX") or attrs.get("Agility") or 10)
                    con_v = int(attrs.get("Constitution") or attrs.get("CON") or attrs.get("Vigor") or 10)
                    int_v = int(attrs.get("Intelligence") or attrs.get("INT") or attrs.get("Intellect") or 10)
                    wis_v = int(attrs.get("Wisdom") or attrs.get("WIS") or attrs.get("Presence") or 10)
                    cha_v = int(attrs.get("Charisma") or attrs.get("CHA") or attrs.get("Presence") or 10)

                    if 0 < str_v <= 7: str_v = str_v * 2 + 10
                    if 0 < dex_v <= 7: dex_v = dex_v * 2 + 10
                    if 0 < con_v <= 7: con_v = con_v * 2 + 10
                    if 0 < int_v <= 7: int_v = int_v * 2 + 10
                    if 0 < wis_v <= 7: wis_v = wis_v * 2 + 10
                    if 0 < cha_v <= 7: cha_v = cha_v * 2 + 10

                    feats_list = gem_data.get("features_and_traits") or []
                    inv_list = gem_data.get("inventory") or []

                    core = UniversalCharacterCore(
                        name=gem_data.get("name", "Pathfinder Hero"),
                        player_name=gem_data.get("player_name") or "Player",
                        archetype=gem_data.get("archetype") or "Hero 1",
                        level=int(gem_data.get("level") or 1),
                        normalized_attributes={
                            "might": min(100, max(10, int((str_v / 20.0) * 100))),
                            "agility": min(100, max(10, int((dex_v / 20.0) * 100))),
                            "vigor": min(100, max(10, int((con_v / 20.0) * 100))),
                            "intellect": min(100, max(10, int((int_v / 20.0) * 100))),
                            "presence": min(100, max(10, int(((wis_v + cha_v) / 40.0) * 100)))
                        },
                        semantic_skills=[],
                        tagged_inventory=[{"name": item, "category": "utility", "genre": "fantasy_d20", "status": "active"} for item in inv_list],
                        tagged_traits=[{"name": "Feat / Ability", "description": t, "genre": "fantasy_d20"} for t in feats_list],
                        backstory=" ".join(feats_list)
                    )

                    native_payload = {
                        "system_name": "pathfinder_2e",
                        "class": gem_data.get("archetype") or "Hero 1",
                        "level": int(gem_data.get("level") or 1),
                        "ac": gem_data.get("guard") or 15,
                        "hit_points": {"current": gem_data.get("current_health", 20), "max": gem_data.get("max_health", 20)},
                        "ability_scores": {
                            "Strength": str_v, "Dexterity": dex_v, "Constitution": con_v,
                            "Intelligence": int_v, "Wisdom": wis_v, "Charisma": cha_v
                        },
                        "weapons": gem_data.get("weapons", []),
                        "inventory": inv_list,
                        "features_and_traits": feats_list,
                        "skills": gem_data.get("skills", {}),
                        "speed": gem_data.get("speed", "25 ft."),
                        "initiative": gem_data.get("initiative", ""),
                        "currency": gem_data.get("currency", {"cp": 0, "sp": 0, "ep": 0, "gp": 10, "pp": 0}),
                        "spellcasting": gem_data.get("spellcasting", {"is_caster": False})
                    }
                    return {
                        "universal_core": core,
                        "native_system_data": native_payload
                    }
            except Exception:
                pass

        # 1. Base Attributes & Identity (Pages 1-3)
        name = fields.get("Character Name") or self._regex_search(r'CHARACTER NAME\s*\n\s*([^\n]+)', text) or "Pathfinder Explorer"
        char_class = fields.get("Class") or self._regex_search(r'CLASS\s*\n\s*([^\n]+)', text) or "Adventurer"
        level_str = fields.get("Level") or self._regex_search(r'LEVEL\s*\n\s*(\d+)', text) or "1"
        level = int(level_str) if str(level_str).isdigit() else 1

        # 2. Extract Full Spellcasting Block (Page 4)
        spellcasting_data = self.parse_spellcasting_page(fields, text, level)

        # 3. Normalized Core Construction
        semantic_skills = [
            {"category": "STEALTH_INFILTRATION", "tier": "trained", "source_skill": "Stealth"},
            {"category": "ACROBATICS_MOBILITY", "tier": "trained", "source_skill": "Acrobatics"}
        ]
        if spellcasting_data.get("tradition") and spellcasting_data.get("tradition") != "None":
            semantic_skills.append({
                "category": f"MAGIC_{spellcasting_data['tradition'].upper()}",
                "tier": "expert" if level >= 7 else "trained",
                "source_skill": f"{spellcasting_data['tradition']} Spellcasting"
            })

        core = UniversalCharacterCore(
            name=name,
            player_name=fields.get("Player Name", "retholtz"),
            archetype=f"{char_class} {level}",
            level=level,
            normalized_attributes={
                "might": 50,
                "agility": 75,
                "vigor": 60,
                "intellect": 70 if spellcasting_data.get("tradition") == "Arcane" else 50,
                "presence": 75 if spellcasting_data.get("tradition") in ("Divine", "Occult") else 50
            },
            semantic_skills=semantic_skills,
            tagged_inventory=[
                {"name": "Standard Spell Component Pouch", "category": "magic_focus", "genre": "fantasy_d20", "status": "active"}
            ] if spellcasting_data.get("is_caster") else [],
            tagged_traits=[
                {"name": f"{spellcasting_data.get('caster_type', 'Prepared')} Caster", "description": f"Tradition: {spellcasting_data.get('tradition', 'None')}", "genre": "fantasy_d20"}
            ] if spellcasting_data.get("is_caster") else [],
            backstory=fields.get("Background", "Wandering Pathfinder scholar.")
        )

        native_payload = {
            "system_name": "pathfinder_2e",
            "class": char_class,
            "level": level,
            "spellcasting": spellcasting_data,
            "raw_acro_form_fields": fields
        }

        return {
            "universal_core": core,
            "native_system_data": native_payload
        }

    def parse_spellcasting_page(self, fields: Dict[str, Any], raw_text: str, level: int) -> Dict[str, Any]:
        """Parses Page 4: Traditions, Statistics, Slots, Focus, Innate, and Spells."""
        # A. Magical Tradition Detection
        traditions = ["Arcane", "Divine", "Occult", "Primal"]
        active_tradition = "None"
        for t in traditions:
            if fields.get(t.lower()) in ("Yes", "On", "1", "True") or f"/{t}" in fields or re.search(rf'\b{t}\b', raw_text, re.IGNORECASE):
                active_tradition = t
                break

        # B. Caster Modality (Prepared vs Spontaneous)
        caster_type = "Prepared"
        if fields.get("spontaneous_caster") in ("Yes", "On", "1") or "Spontaneous Caster" in raw_text:
            caster_type = "Spontaneous"

        # C. Spell Statistics
        spell_dc = self._find_number(fields, ["SpellDC", "Spell_DC", "ClassDC"], default=10 + level)
        spell_attack = self._find_number(fields, ["SpellAttack", "Spell_Attack", "SpellAttackRoll"], default=level + 2)

        # D. Spell Slots (Ranks 1 to 10)
        spell_slots = {}
        for rank in range(1, 11):
            day_key = f"SpellSlots_Rank{rank}_Total"
            remain_key = f"SpellSlots_Rank{rank}_Remaining"

            # Search form keys or fallback regex
            total_slots = self._find_number(fields, [day_key, f"Slots_{rank}", f"Rank{rank}_Slots"], default=0)
            if total_slots == 0:
                match = re.search(rf'Spell Rank {rank}\s*(\d+)', raw_text)
                if match:
                    total_slots = int(match.group(1))

            spell_slots[f"rank_{rank}"] = {
                "max": total_slots,
                "current": self._find_number(fields, [remain_key], default=total_slots)
            }

        # E. Focus Spells & Pool (Max 3 Focus Points)
        focus_pool_max = self._find_number(fields, ["FocusPool", "Focus_Pool_Max", "FocusPoints_Max"], default=1)
        focus_pool_max = min(3, max(0, focus_pool_max))
        focus_rank = (level + 1) // 2

        focus_spells = self._extract_table_rows(fields, prefix="FocusSpell", max_rows=6)

        # F. Innate Spells & Rituals
        innate_spells = self._extract_table_rows(fields, prefix="InnateSpell", max_rows=4)
        rituals = self._extract_table_rows(fields, prefix="Ritual", max_rows=4)

        # G. Prepared/Known Spells Table
        known_spells = self._extract_known_spells(fields, raw_text)

        is_caster = (active_tradition != "None") or any(s["max"] > 0 for s in spell_slots.values()) or len(known_spells) > 0

        return {
            "is_caster": is_caster,
            "tradition": active_tradition,
            "caster_type": caster_type,
            "spell_dc": spell_dc,
            "spell_attack": spell_attack,
            "cantrip_rank": (level + 1) // 2,
            "spell_slots": spell_slots,
            "focus_magic": {
                "focus_points": {"current": focus_pool_max, "max": focus_pool_max},
                "focus_rank": focus_rank,
                "spells": focus_spells
            },
            "innate_spells": innate_spells,
            "rituals": rituals,
            "spell_list": known_spells
        }

    # --- Field Helpers ---
    @staticmethod
    def _find_number(fields: Dict[str, Any], keys: List[str], default: int = 0) -> int:
        for k in keys:
            for fk, fv in fields.items():
                if k.lower() in fk.lower():
                    s_val = str(fv).strip()
                    # Match integers, including optional leading + or -
                    if s_val.lstrip('+-').isdigit():
                        return int(s_val)
        return default

    @staticmethod
    def _regex_search(pattern: str, text: str) -> Optional[str]:
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1).strip() if m else None

    @staticmethod
    def _extract_table_rows(fields: Dict[str, Any], prefix: str, max_rows: int = 6) -> List[Dict[str, Any]]:
        rows = []
        for i in range(1, max_rows + 1):
            name = fields.get(f"{prefix}_Name_{i}") or fields.get(f"{prefix}Name{i}")
            if name:
                rows.append({
                    "name": str(name).strip(),
                    "actions": str(fields.get(f"{prefix}_Actions_{i}", "◆◆")).strip(),
                    "details": str(fields.get(f"{prefix}_Details_{i}", "")).strip()
                })
        return rows

    @staticmethod
    def _extract_known_spells(fields: Dict[str, Any], text: str) -> List[Dict[str, Any]]:
        spells = []
        for i in range(1, 25):
            s_name = fields.get(f"Spell_Name_{i}") or fields.get(f"SpellName{i}")
            if s_name:
                rank_raw = fields.get(f"Spell_Rank_{i}", 1)
                rank_val = int(str(rank_raw).strip()) if str(rank_raw).strip().isdigit() else 1
                spells.append({
                    "name": str(s_name).strip(),
                    "rank": rank_val,
                    "actions": str(fields.get(f"Spell_Actions_{i}", "◆◆")).strip(),
                    "prepared": fields.get(f"Spell_Prep_{i}") in ("Yes", "On", "1", "True")
                })

        if not spells and text:
            matches = re.findall(r'(?:◆|◆◆|◆◆◆|Reaction|Free)?\s*([A-Za-z\s\']+)\s+Rank\s+(\d+)', text)
            for m in matches:
                spells.append({"name": m[0].strip(), "rank": int(m[1]), "actions": "◆◆", "prepared": True})

        return spells
