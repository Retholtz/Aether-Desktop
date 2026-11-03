"""
core/character_vault_service.py - Character Vault & Cross-Genre Branching Service
Manages canonical character roots, crossover variant lineages, and genre-locked leveling.
"""
import os
import re
import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from core.logger import get_logger
from core.parsers.spell_registry import sanitize_parsed_spells, sanitize_character_weapons, is_junk_spell

logger = get_logger("CharacterVault")


def _utc_now_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat()
    except Exception:
        return datetime.utcnow().isoformat()

RULESET_FAMILIES = {
    # D&D 5E / Heroic Fantasy family
    "dnd_5e": "dnd_5e",
    "dnd": "dnd_5e",
    "5e": "dnd_5e",
    "d&d": "dnd_5e",
    "d&d 5e": "dnd_5e",
    "heroic_d20": "dnd_5e",
    "heroic_fantasy": "dnd_5e",
    "dungeons_and_dragons": "dnd_5e",

    # Pathfinder 2E family
    "pathfinder_2e": "pathfinder_2e",
    "pathfinder": "pathfinder_2e",
    "pathfinder_1e": "pathfinder_2e",
    "pathfinder_society": "pathfinder_2e",
    "pf2e": "pathfinder_2e",
    "pf1e": "pathfinder_2e",
    "paizo": "pathfinder_2e",

    # Cyberpunk family
    "cyberpunk": "cyberpunk",
    "cyberpunk_red": "cyberpunk",
    "cybernetic_action": "cyberpunk",

    # Call of Cthulhu / Investigative Horror
    "call_of_cthulhu": "call_of_cthulhu",
    "coc": "call_of_cthulhu",
    "investigative_d100": "call_of_cthulhu",
    "investigative_horror": "call_of_cthulhu",
    "cthulhu": "call_of_cthulhu",

    # Space Opera / Sci-Fi
    "space_opera": "space_opera",
    "sci_fi": "space_opera",

    # Narrative Clocks
    "narrative_clocks": "narrative_clocks",
    "blades_in_the_dark": "narrative_clocks",

    # Custom Sandbox
    "sandbox_custom": "sandbox_custom",
    "custom": "sandbox_custom",
}

RULESET_DISPLAY_LABELS = {
    "dnd_5e": "D&D 5E",
    "heroic_d20": "D&D 5E",
    "heroic_fantasy": "D&D 5E",
    "pathfinder_2e": "Pathfinder 2E",
    "cyberpunk": "Cyberpunk",
    "cybernetic_action": "Cyberpunk",
    "call_of_cthulhu": "Call of Cthulhu",
    "investigative_d100": "Call of Cthulhu",
    "space_opera": "Space Opera",
    "narrative_clocks": "Narrative Clocks",
    "sandbox_custom": "Custom Sandbox"
}


def normalize_ruleset_id(raw: Optional[str]) -> str:
    if not raw:
        return "generic"
    s = str(raw).lower().strip().replace(" ", "_").replace("-", "_")
    return RULESET_FAMILIES.get(s, s)


def get_ruleset_display_label(ruleset_id: Optional[str]) -> str:
    if not ruleset_id:
        return "Generic"
    norm = normalize_ruleset_id(ruleset_id)
    return RULESET_DISPLAY_LABELS.get(norm, norm.replace("_", " ").title())


def are_rulesets_compatible(ruleset_a: Optional[str], ruleset_b: Optional[str]) -> bool:
    """
    Returns True if two rulesets or genres belong to the same mechanics family
    (e.g. dnd_5e, heroic_d20, and heroic_fantasy are all D&D 5E compatible;
    cyberpunk and cybernetic_action are both Cyberpunk compatible).
    """
    if not ruleset_a or not ruleset_b:
        return True
    norm_a = normalize_ruleset_id(ruleset_a)
    norm_b = normalize_ruleset_id(ruleset_b)
    if norm_a == norm_b:
        return True
    fam_a = RULESET_FAMILIES.get(norm_a, norm_a)
    fam_b = RULESET_FAMILIES.get(norm_b, norm_b)
    return fam_a == fam_b


GENRE_DISPLAY_MAP = {
    "cybernetic_action": "Cyberpunk",
    "cyberpunk": "Cyberpunk",
    "heroic_fantasy": "Heroic Fantasy",
    "heroic_d20": "Heroic Fantasy",
    "dnd_5e": "Heroic Fantasy",
    "pathfinder_2e": "Heroic Fantasy",
    "investigative_d100": "Investigative Horror",
    "call_of_cthulhu": "Lovecraftian Horror",
    "space_opera": "Space Opera",
    "post_apoc": "Post-Apocalyptic",
    "narrative_clocks": "Narrative Clocks",
    "sandbox_custom": "Custom Sandbox"
}


class CharacterVaultService:
    def __init__(self, vault_dir: str = "data/vault/characters"):
        self.vault_dir = os.path.abspath(vault_dir)
        os.makedirs(self.vault_dir, exist_ok=True)

    def _file_path(self, char_id: str, genre: Optional[str] = None) -> str:
        safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', char_id.lower())
        if genre:
            genre_dir = os.path.join(self.vault_dir, genre)
            os.makedirs(genre_dir, exist_ok=True)
            return os.path.join(genre_dir, f"{safe_id}.json")
        if os.path.exists(self.vault_dir):
            for root, dirs, files in os.walk(self.vault_dir):
                if f"{safe_id}.json" in files:
                    return os.path.join(root, f"{safe_id}.json")
        return os.path.join(self.vault_dir, f"{safe_id}.json")

    # --- Character Retrieval & Listing ---
    def get_character(self, char_id: str) -> Optional[Dict[str, Any]]:
        path = self._file_path(char_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data and "spells" in data and isinstance(data["spells"], list):
                    if any(isinstance(s, str) and is_junk_spell(s) for s in data["spells"]):
                        data["spells"] = sanitize_parsed_spells(data["spells"])
                if data and "weapons" in data and isinstance(data["weapons"], list):
                    data["weapons"] = sanitize_character_weapons(data["weapons"], data.get("spells"))
                return data
        except Exception as e:
            logger.error(f"[VAULT] Failed reading character '{char_id}': {e}")
            return None

    def list_characters(self) -> List[Dict[str, Any]]:
        """Lists all characters in the vault, categorized by root and variants."""
        results = []
        if not os.path.exists(self.vault_dir):
            return results
        for root, dirs, files in os.walk(self.vault_dir):
            for fname in sorted(files):
                if fname.endswith(".json"):
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            r_id = data.get("ruleset") or data.get("native_system") or data.get("native_genre", "dnd_5e")
                            results.append({
                                "id": data.get("id"),
                                "name": data.get("name"),
                                "level": data.get("level", 1),
                                "archetype": data.get("archetype", "Unknown"),
                                "ruleset": r_id,
                                "ruleset_label": data.get("ruleset_label") or get_ruleset_display_label(r_id),
                                "native_system": data.get("native_system") or r_id,
                                "native_genre": data.get("native_genre", "generic"),
                                "current_genre": data.get("current_genre", data.get("native_genre", "generic")),
                                "is_root": data.get("is_root", True),
                                "parent_id": data.get("parent_id"),
                                "variant_label": data.get("variant_label", "Canonical Root" if data.get("is_root", True) else "Crossover Variant"),
                                "inventory_count": len(data.get("inventory", []) or data.get("universal_core", {}).get("tagged_inventory", [])),
                                "quarantine_count": len(data.get("quarantined_stash", [])),
                                "updated_at": data.get("updated_at")
                            })
                    except Exception:
                        pass
        return sorted(results, key=lambda c: (not c.get("is_root", True), c.get("name", "")))

    # --- Cross-Genre Import & Branch Forking ---
    def import_to_campaign(
        self,
        char_id: str,
        target_genre: str,
        quarantine_unmatched: bool = True
    ) -> Dict[str, Any]:
        """
        Prepares a character for a campaign. If target_genre is compatible with the character's
        ruleset or native genre, imports directly without crossover adjudication.
        Otherwise, checks for an existing branch or forks a new crossover variant.
        """
        source_char = self.get_character(char_id)
        if not source_char:
            return {"status": "error", "message": f"Character '{char_id}' not found in vault."}

        native_genre = source_char.get("native_genre", "heroic_fantasy")
        char_ruleset = source_char.get("ruleset") or source_char.get("native_system") or native_genre

        # 1. Matching Ruleset or Genre: Use the canonical character directly
        if are_rulesets_compatible(char_ruleset, target_genre) or (native_genre == target_genre):
            label = get_ruleset_display_label(char_ruleset)
            return {
                "status": "success",
                "character": source_char,
                "is_crossover": False,
                "ruleset_match": True,
                "ruleset_label": label,
                "message": f"Loaded '{source_char['name']}' directly from native vault (Ruleset Match: {label})."
            }

        # 2. Cross-Genre: Locate existing branch or fork a new variant
        variant_id = f"{source_char['id']}_{target_genre}"
        existing_variant = self.get_character(variant_id)

        if existing_variant:
            return {
                "status": "success",
                "character": existing_variant,
                "is_crossover": True,
                "message": f"Loaded existing crossover variant: '{existing_variant['name']}'."
            }

        # Fork new crossover variant
        forked_payload = json.loads(json.dumps(source_char))  # Deep copy
        forked_payload["id"] = variant_id
        forked_payload["parent_id"] = source_char["id"]
        forked_payload["is_root"] = False
        forked_payload["current_genre"] = target_genre
        genre_label = GENRE_DISPLAY_MAP.get(target_genre.lower(), target_genre.replace('_', ' ').title())
        forked_payload["name"] = f"{source_char['name']} ({genre_label} Crossover)"
        forked_payload["variant_label"] = f"{genre_label} Crossover"

        # Apply Quarantine
        if quarantine_unmatched:
            active_inv = []
            stash = forked_payload.setdefault("quarantined_stash", [])
            for item in forked_payload.get("inventory", []):
                item_name = item.get("name", str(item)) if isinstance(item, dict) else str(item)
                item_genre = item.get("genre", native_genre) if isinstance(item, dict) else native_genre
                if item_genre != target_genre:
                    stash.append({
                        "name": item_name,
                        "original_genre": item_genre,
                        "quarantine_reason": f"Dormant: Not functional in {target_genre} setting."
                    })
                else:
                    active_inv.append(item)
            forked_payload["inventory"] = active_inv

        # Save new branch in vault
        self._save_character(forked_payload)
        logger.info(f"[VAULT] Forked crossover variant '{variant_id}' from root '{source_char['id']}'.")

        return {
            "status": "success",
            "character": forked_payload,
            "is_crossover": True,
            "message": f"Created new crossover branch: '{forked_payload['name']}'."
        }

    # --- Document & Campaign Imports into Vault ---
    def import_from_document(
        self,
        filename: str,
        content_bytes: bytes,
        ttrpg_service=None,
        ruleset_override: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Parses an uploaded document (PDF AcroForm, Gemini Multimodal OCR, JSON, text)
        and stores the extracted character(s) in the Vault as Canonical Root heroes.
        """
        parsed_characters: List[Dict[str, Any]] = []

        if ttrpg_service and hasattr(ttrpg_service, "_parse_character_from_document"):
            try:
                parsed_characters = ttrpg_service._parse_character_from_document(
                    filename=filename,
                    extracted_text="",
                    raw_bytes=content_bytes,
                    ruleset_override=ruleset_override
                )
            except Exception as e:
                logger.warning(f"[VAULT] Failed parsing document via TTRPGService: {e}")

        # Fallback if ttrpg_service was not provided or found nothing
        if not parsed_characters:
            if filename.lower().endswith(".json"):
                try:
                    data = json.loads(content_bytes.decode("utf-8", errors="replace"))
                    if isinstance(data, dict):
                        if "characters" in data and isinstance(data["characters"], dict):
                            parsed_characters.extend(data["characters"].values())
                        elif data.get("name"):
                            parsed_characters.append(data)
                except Exception as e:
                    logger.debug(f"[VAULT] JSON fallback parsing failed: {e}")

        if not parsed_characters:
            return {
                "status": "error",
                "message": f"Could not extract character sheet data from '{filename}'. Please ensure it is a valid fillable PDF, scanned sheet, or JSON character export."
            }

        saved_characters = []
        for ch in parsed_characters:
            c_name = ch.get("name") or "Adventurer"
            clean_id_name = re.sub(r'[^a-zA-Z0-9_]', '', c_name.lower().replace(" ", "_"))
            char_id = f"char_{clean_id_name}_root"

            # Determine ruleset
            assigned_ruleset = ruleset_override or ch.get("ruleset") or ch.get("native_system") or "unknown"
            sys_name = str(ch.get("system_name") or "").lower()

            if any(w in sys_name for w in ("pathfinder", "paizo", "pf1e", "pf2e")) or any(w in assigned_ruleset.lower() for w in ("pathfinder", "paizo", "pf1e", "pf2e")):
                assigned_ruleset = "pathfinder_2e"
            elif any(w in sys_name for w in ("cthulhu", "coc")) or any(w in assigned_ruleset.lower() for w in ("cthulhu", "coc")):
                assigned_ruleset = "call_of_cthulhu"
            elif any(w in sys_name for w in ("cyberpunk", "edgerunner")) or any(w in assigned_ruleset.lower() for w in ("cyber", "cyberpunk")):
                assigned_ruleset = "cyberpunk"
            elif any(w in sys_name for w in ("d&d", "dnd", "5e")) or any(w in assigned_ruleset.lower() for w in ("dnd", "5e")):
                assigned_ruleset = "dnd_5e"

            if assigned_ruleset in ("unknown", "agnostic"):
                attrs = ch.get("attributes") or ch.get("stats") or ch.get("ability_scores") or {}
                if not attrs:
                    attrs = {k: v for k, v in ch.items() if k in ("STR", "Strength", "DEX", "Dexterity", "CON", "Constitution", "INT", "Intelligence", "WIS", "Wisdom", "CHA", "Charisma", "REF", "TECH", "COOL", "BODY", "EMP", "SAN", "POW", "EDU")}
                lower_arch = (ch.get("archetype") or ch.get("character_class") or ch.get("class") or "").lower()
                feats_str = " ".join(ch.get("features_and_traits") or ch.get("features") or ch.get("cyberware") or []).lower()

                dnd_classes = ("fighter", "wizard", "rogue", "cleric", "paladin", "barbarian", "bard", "druid", "ranger", "sorcerer", "warlock", "monk", "elf", "dwarf", "halfling")
                cyber_roles = ("rockerboy", "solo", "netrunner", "tech", "medtech", "media", "exec", "lawman", "fixer", "nomad", "edgerunner")

                if any(k in attrs for k in ("CMB", "CMD")) or "ancestry" in ch or "class_dc" in ch or any(w in feats_str for w in ("pathfinder", "paizo", "class dc", "hero points", "ancestry feat", "skill feat", "trapfinding")) or "pathfinder" in lower_arch or "ancestry" in lower_arch:
                    assigned_ruleset = "pathfinder_2e"
                elif any(k in attrs for k in ("REF", "TECH", "COOL", "EMP", "BODY", "MOVE", "LUCK", "WILL")) or any(w in lower_arch for w in cyber_roles) or "cyber" in feats_str:
                    assigned_ruleset = "cyberpunk"
                elif any(k in attrs for k in ("SAN", "POW", "EDU", "Sanity")) or "investigator" in lower_arch or "sanity" in feats_str:
                    assigned_ruleset = "call_of_cthulhu"
                elif any(k in attrs for k in ("STR", "Strength", "DEX", "Dexterity", "CON", "Constitution", "Wisdom", "Charisma")) or any(w in lower_arch for w in dnd_classes) or "spell" in feats_str:
                    assigned_ruleset = "dnd_5e"

            if assigned_ruleset == "unknown":
                import base64
                return {
                    "status": "requires_ruleset_selection",
                    "message": f"Could not determine ruleset for '{c_name}'. Please select which tabletop system this character belongs to.",
                    "character": ch,
                    "filename": filename,
                    "base64_content": base64.b64encode(content_bytes).decode("utf-8")
                }

            if "cyber" in assigned_ruleset.lower():
                assigned_genre = "cybernetic_action"
                assigned_ruleset = "cyberpunk"
            elif "cthulhu" in assigned_ruleset.lower():
                assigned_genre = "modern_horror"
                assigned_ruleset = "call_of_cthulhu"
            elif "pathfinder" in assigned_ruleset.lower():
                assigned_genre = "heroic_fantasy"
                assigned_ruleset = "pathfinder_2e"
            elif "dnd" in assigned_ruleset.lower() or "5e" in assigned_ruleset.lower():
                assigned_genre = "heroic_fantasy"
                assigned_ruleset = "dnd_5e"
            else:
                assigned_genre = ch.get("native_genre") or "custom"
                assigned_ruleset = "agnostic"

            # Determine health with proper fallbacks and bounds clamping
            is_coc = "cthulhu" in assigned_ruleset.lower() or "coc" in assigned_ruleset.lower()
            max_hp = int(ch.get("max_health") or ch.get("max_hp") or ch.get("health") or (13 if is_coc else 20))
            cur_hp = int(ch.get("current_health") or ch.get("current_hp") or ch.get("health") or max_hp)
            cur_hp = max(0, min(max_hp, cur_hp))
            if is_coc:
                guard_ac = int(ch.get("dodge") or ch.get("guard") or ch.get("armor_class") or 40)
                guard_label = "Dodge Defense"
            else:
                guard_ac = int(ch.get("guard") or ch.get("armor_class") or ch.get("ac") or 10)
                guard_label = "Armor Defense"

            raw_vitals = ch.get("vitals")
            if raw_vitals and isinstance(raw_vitals, dict) and "health" in raw_vitals:
                v_max = max(1, int(raw_vitals["health"].get("max") or max_hp))
                v_cur = int(raw_vitals["health"].get("current") or cur_hp)
                raw_vitals["health"]["max"] = v_max
                raw_vitals["health"]["current"] = max(0, min(v_max, v_cur))
                final_vitals = raw_vitals
            else:
                final_vitals = {
                    "health": {"current": cur_hp, "max": max(1, max_hp), "label": "Health"},
                    "guard": {"current": guard_ac, "max": max(1, guard_ac), "label": guard_label}
                }

            feats = (
                ch.get("features_and_traits")
                or ch.get("traits_and_abilities")
                or ch.get("features")
                or ch.get("features_traits")
                or []
            )

            # Build Universal Character Core & Native System Data (Schema 2.0)
            u_core = ch.get("universal_core") or self._build_universal_core(ch)
            n_data = ch.get("native_system_data") or self._build_native_system_data(ch)

            char_record = {
                "schema_version": "2.0",
                "id": char_id,
                "parent_id": None,
                "is_root": True,
                "name": c_name,
                "variant_label": "Canonical Root",
                "player_name": ch.get("player_name") or "Player",
                "level": int(ch.get("level") or 1),
                "experience_points": int(ch.get("experience_points") or 0),
                "archetype": ch.get("archetype") or ch.get("class") or "Adventurer",
                "species": ch.get("species") or ch.get("race") or "",
                "background": ch.get("background") or "",
                "ruleset": assigned_ruleset,
                "ruleset_label": get_ruleset_display_label(assigned_ruleset),
                "native_genre": assigned_genre,
                "native_system": assigned_ruleset,
                "vitals": final_vitals,
                "custom_meters": ch.get("custom_meters") or {},
                "attributes": ch.get("attributes") or {
                    "Might": 10,
                    "Agility": 10,
                    "Vigor": 10,
                    "Intellect": 10,
                    "Presence": 10
                },
                "armor_class": guard_ac,
                "speed": ch.get("speed") or "30 ft.",
                "hit_dice": ch.get("hit_dice") or "1d8",
                "saving_throws": ch.get("saving_throws") or {},
                "currency": ch.get("currency") or {"cp": 0, "sp": 0, "ep": 0, "gp": 0, "pp": 0},
                "weapons": sanitize_character_weapons(ch.get("weapons") or [], ch.get("spells") or []),
                "inventory": ch.get("inventory") or [],
                "features_and_traits": feats,
                "traits_and_abilities": feats,
                "spells": sanitize_parsed_spells(ch.get("spells") or []),
                "spellcasting_ability": ch.get("spellcasting_ability") or "",
                "spell_save_dc": ch.get("spell_save_dc") or "",
                "spell_attack_bonus": ch.get("spell_attack_bonus") or "",
                "passives": ch.get("passives") or {},
                "senses": ch.get("senses") or "",
                "defenses": ch.get("defenses") or ch.get("resistances") or "",
                "resistances": ch.get("resistances") or ch.get("defenses") or "",
                "initiative": ch.get("initiative") or "",
                "proficiencies": ch.get("proficiencies") or "",
                "proficiencies_armor": ch.get("proficiencies_armor") or "",
                "proficiencies_weapons": ch.get("proficiencies_weapons") or "",
                "proficiencies_tools": ch.get("proficiencies_tools") or "",
                "proficiencies_languages": ch.get("proficiencies_languages") or "",
                "prof_bonus": ch.get("prof_bonus") or "+2",
                "skills": ch.get("skills") or {},
                "quarantined_stash": ch.get("quarantined_stash") or [],
                "source_document": filename,
                "updated_at": _utc_now_iso(),
                "universal_core": u_core,
                "native_system_data": n_data
            }
            if "head_sp" in ch:
                char_record["head_sp"] = ch["head_sp"]
            if "body_sp" in ch:
                char_record["body_sp"] = ch["body_sp"]
            if "humanity" in ch:
                char_record["humanity"] = ch["humanity"]

            self._save_character(char_record)
            saved_characters.append(char_record)
            logger.info(f"[VAULT] Imported '{c_name}' into vault from document '{filename}' (Ruleset: {char_record['ruleset_label']}).")

        return {
            "status": "success",
            "characters": saved_characters,
            "count": len(saved_characters),
            "message": f"Successfully imported {len(saved_characters)} character(s) into vault from '{filename}'."
        }

    def _build_universal_core(self, ch: Dict[str, Any]) -> Dict[str, Any]:
        c_name = ch.get("name") or "Adventurer"
        genre = ch.get("native_genre") or "fantasy_d20"
        feats = ch.get("features_and_traits") or ch.get("traits_and_abilities") or ch.get("features") or []
        tagged_traits = []
        for f in feats:
            if isinstance(f, dict):
                tagged_traits.append(f)
            else:
                parts = str(f).split(":", 1)
                t_name = parts[0].strip()
                t_desc = parts[1].strip() if len(parts) > 1 else str(f).strip()
                tagged_traits.append({"name": t_name, "description": t_desc, "genre": genre})

        tagged_inv = []
        for item in (ch.get("inventory") or []):
            if isinstance(item, dict):
                tagged_inv.append(item)
            else:
                s_item = str(item).strip()
                if not s_item:
                    continue
                lower_i = s_item.lower()
                cat = "utility"
                if any(w in lower_i for w in ["sword", "dagger", "axe", "mace", "rapier", "blade"]):
                    cat = "melee_light"
                elif any(w in lower_i for w in ["bow", "crossbow", "sling"]):
                    cat = "ranged_light"
                elif any(w in lower_i for w in ["armor", "shield", "leather", "chain", "plate"]):
                    cat = "armor_light"
                elif any(w in lower_i for w in ["tool", "kit", "lockpick", "thieves"]):
                    cat = "utility_infil"
                tagged_inv.append({"name": s_item, "category": cat, "genre": genre, "status": "active"})

        raw_attrs = ch.get("attributes") or {}
        r_str = str(ch.get("ruleset") or ch.get("native_system") or "").lower()
        is_cyber = "cyber" in r_str or any(k in raw_attrs for k in ("REF", "TECH", "COOL", "BODY"))
        is_coc = "cthulhu" in r_str or "coc" in r_str or any(k in raw_attrs for k in ("SAN", "POW", "EDU", "SIZ"))

        if is_cyber:
            m_val = ch.get("Might") or raw_attrs.get("Might") or raw_attrs.get("BODY") or 6
            a_val = ch.get("Agility") or raw_attrs.get("Agility") or max(raw_attrs.get("REF", 6), raw_attrs.get("DEX", 6))
            v_val = ch.get("Vigor") or raw_attrs.get("Vigor") or raw_attrs.get("BODY") or 6
            i_val = ch.get("Intellect") or raw_attrs.get("Intellect") or raw_attrs.get("INT") or 6
            p_val = ch.get("Presence") or raw_attrs.get("Presence") or raw_attrs.get("COOL") or 6
            norm_attrs = {
                "might": min(100, max(10, int((m_val / 10.0) * 100))),
                "agility": min(100, max(10, int((a_val / 10.0) * 100))),
                "vigor": min(100, max(10, int((v_val / 10.0) * 100))),
                "intellect": min(100, max(10, int((i_val / 10.0) * 100))),
                "presence": min(100, max(10, int((p_val / 10.0) * 100)))
            }
        elif is_coc:
            m_val = int(raw_attrs.get("STR") or raw_attrs.get("Strength") or ch.get("Might") or 50)
            a_val = int(raw_attrs.get("DEX") or raw_attrs.get("Dexterity") or ch.get("Agility") or 50)
            v_val = int(raw_attrs.get("CON") or raw_attrs.get("Constitution") or ch.get("Vigor") or 50)
            i_val = int(raw_attrs.get("INT") or raw_attrs.get("Intelligence") or ch.get("Intellect") or 50)
            p_val = int(raw_attrs.get("POW") or raw_attrs.get("APP") or raw_attrs.get("Charisma") or ch.get("Presence") or 50)
            norm_attrs = {
                "might": min(100, max(1, m_val)),
                "agility": min(100, max(1, a_val)),
                "vigor": min(100, max(1, v_val)),
                "intellect": min(100, max(1, i_val)),
                "presence": min(100, max(1, p_val))
            }
        else:
            m_val = ch.get("Might") or raw_attrs.get("Might") or raw_attrs.get("Strength") or 10
            a_val = ch.get("Agility") or raw_attrs.get("Agility") or raw_attrs.get("Dexterity") or 10
            v_val = ch.get("Vigor") or raw_attrs.get("Vigor") or raw_attrs.get("Constitution") or 10
            i_val = ch.get("Intellect") or raw_attrs.get("Intellect") or raw_attrs.get("Intelligence") or 10
            p_val = ch.get("Presence") or raw_attrs.get("Presence") or raw_attrs.get("Charisma") or raw_attrs.get("Wisdom") or 10
            norm_attrs = {
                "might": min(100, max(10, int((m_val / 20.0) * 100))) if m_val <= 20 else min(100, m_val),
                "agility": min(100, max(10, int((a_val / 20.0) * 100))) if a_val <= 20 else min(100, a_val),
                "vigor": min(100, max(10, int((v_val / 20.0) * 100))) if v_val <= 20 else min(100, v_val),
                "intellect": min(100, max(10, int((i_val / 20.0) * 100))) if i_val <= 20 else min(100, i_val),
                "presence": min(100, max(10, int((p_val / 20.0) * 100))) if p_val <= 20 else min(100, p_val)
            }

        return {
            "name": c_name,
            "player_name": ch.get("player_name") or "Player",
            "archetype": ch.get("archetype") or ch.get("class") or "Adventurer",
            "level": int(ch.get("level") or 1),
            "normalized_attributes": norm_attrs,
            "semantic_skills": ch.get("semantic_skills") or [],
            "tagged_inventory": tagged_inv,
            "tagged_traits": tagged_traits,
            "backstory": ch.get("backstory") or ""
        }

    def _build_native_system_data(self, ch: Dict[str, Any]) -> Dict[str, Any]:
        ruleset = ch.get("ruleset") or ch.get("native_system") or "dnd_5e"
        is_coc = "cthulhu" in ruleset.lower() or "coc" in ruleset.lower()
        guard_val = ch.get("dodge") or ch.get("armor_class") or ch.get("guard") or (40 if is_coc else 14)
        vitals = ch.get("vitals") or {}
        hp_entry = vitals.get("health", {}) if isinstance(vitals, dict) else {}
        cur_hp = hp_entry.get("current") or ch.get("current_health") or ch.get("health") or (13 if is_coc else 20)
        max_hp = hp_entry.get("max") or ch.get("max_health") or (13 if is_coc else 20)

        feats = ch.get("features_and_traits") or ch.get("traits_and_abilities") or ch.get("features") or []
        raw_attrs = ch.get("attributes") or {}

        if is_coc:
            custom = ch.get("custom_meters") or {}
            san_cur = custom.get("sanity", {}).get("current", 50) if isinstance(custom.get("sanity"), dict) else (ch.get("sanity") or 50)
            luck_cur = custom.get("luck", {}).get("current", 50) if isinstance(custom.get("luck"), dict) else (ch.get("luck") or 50)
            mp_cur = custom.get("magic_points", {}).get("current", 10) if isinstance(custom.get("magic_points"), dict) else (ch.get("magic_points") or 10)
            move_val = custom.get("move_rate", {}).get("current", 8) if isinstance(custom.get("move_rate"), dict) else (ch.get("move_rate") or 8)

            return {
                "system_name": "call_of_cthulhu_7e",
                "occupation": ch.get("archetype") or ch.get("class") or "Investigator",
                "age": ch.get("age", 20),
                "sex": ch.get("sex", ""),
                "residence": ch.get("residence", ""),
                "birthplace": ch.get("birthplace", ""),
                "characteristics": {
                    "STR": int(raw_attrs.get("STR", 50)),
                    "CON": int(raw_attrs.get("CON", 50)),
                    "SIZ": int(raw_attrs.get("SIZ", 50)),
                    "DEX": int(raw_attrs.get("DEX", 50)),
                    "APP": int(raw_attrs.get("APP", 50)),
                    "INT": int(raw_attrs.get("INT", 50)),
                    "POW": int(raw_attrs.get("POW", 50)),
                    "EDU": int(raw_attrs.get("EDU", 50)),
                    "Move": move_val
                },
                "vitals": {
                    "hit_points": {"current": cur_hp, "max": max_hp},
                    "sanity": {"current": san_cur, "max": 99},
                    "luck": {"current": luck_cur, "max": 99},
                    "magic_points": {"current": mp_cur, "max": int(raw_attrs.get("POW", 50)) // 5}
                },
                "combat": {
                    "damage_bonus": ch.get("damage_bonus", "none"),
                    "build": ch.get("build", 0),
                    "dodge": int(ch.get("dodge") or int(raw_attrs.get("DEX", 40)) // 2)
                },
                "skills": ch.get("skills") or {},
                "weapons": sanitize_character_weapons(ch.get("weapons") or [], ch.get("spells") or []),
                "inventory": ch.get("inventory") or [],
                "features_and_traits": feats,
                "backstory": ch.get("backstory_details") or {}
            }

        return {
            "system_name": ruleset,
            "class_level": ch.get("archetype") or ch.get("class") or "Adventurer",
            "armor_class": guard_val,
            "speed": ch.get("speed") or "30 ft.",
            "hit_points": {"current": cur_hp, "max": max_hp},
            "ability_scores": raw_attrs,
            "saving_throws": ch.get("saving_throws") or {},
            "currency": ch.get("currency") or {"cp": 0, "sp": 0, "ep": 0, "gp": 0, "pp": 0},
            "proficiencies": ch.get("proficiencies") or "",
            "weapons": ch.get("weapons") or [],
            "features_and_traits": feats,
            "spells": ch.get("spells") or [],
            "spellcasting_ability": ch.get("spellcasting_ability") or "",
            "spell_save_dc": ch.get("spell_save_dc") or "",
            "spell_attack_bonus": ch.get("spell_attack_bonus") or "",
            "passives": ch.get("passives") or {},
            "senses": ch.get("senses") or ""
        }

    def import_from_campaign(
        self,
        character_data: Dict[str, Any],
        campaign_meta: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Exports a character from a campaign party roster into the Character Vault as a Canonical Root hero.
        """
        c_name = character_data.get("name") or "Adventurer"
        clean_id_name = re.sub(r'[^a-zA-Z0-9_]', '', c_name.lower().replace(" ", "_"))
        char_id = f"char_{clean_id_name}_root"

        camp_meta = campaign_meta or {}
        ruleset = character_data.get("ruleset") or character_data.get("native_system") or camp_meta.get("system_preset") or camp_meta.get("ruleset") or "dnd_5e"
        genre = character_data.get("native_genre") or ("cybernetic_action" if "cyber" in ruleset.lower() else ("modern_horror" if "cthulhu" in ruleset.lower() else camp_meta.get("genre", "heroic_fantasy")))

        char_record = json.loads(json.dumps(character_data))
        char_record["schema_version"] = "2.0"
        char_record["id"] = char_id
        char_record["is_root"] = True
        char_record["parent_id"] = None
        char_record["variant_label"] = "Canonical Root"
        char_record["ruleset"] = ruleset
        char_record["ruleset_label"] = get_ruleset_display_label(ruleset)
        char_record["native_system"] = ruleset
        char_record["native_genre"] = genre
        char_record.setdefault("quarantined_stash", [])
        char_record["updated_at"] = _utc_now_iso()

        is_coc = "cthulhu" in ruleset.lower() or "coc" in ruleset.lower()
        max_hp = int(character_data.get("max_health") or character_data.get("max_hp") or character_data.get("health") or (13 if is_coc else 20))
        cur_hp = int(character_data.get("current_health") or character_data.get("current_hp") or character_data.get("health") or max_hp)
        guard_val = int(character_data.get("dodge") or character_data.get("guard") or character_data.get("armor_class") or (40 if is_coc else 10))
        guard_label = "Dodge Defense" if is_coc else "Armor Defense"

        raw_vitals = char_record.get("vitals")
        if not raw_vitals or not isinstance(raw_vitals, dict) or "health" not in raw_vitals:
            char_record["vitals"] = {
                "health": {"current": cur_hp, "max": max(1, max_hp), "label": "Health"},
                "guard": {"current": guard_val, "max": max(1, guard_val), "label": guard_label}
            }
        else:
            if is_coc and raw_vitals.get("guard", {}).get("label") in ("Armor Defense", "AC", "Armor Class"):
                raw_vitals["guard"]["label"] = "Dodge Defense"
                if "dodge" in character_data:
                    raw_vitals["guard"]["current"] = int(character_data["dodge"])
                    raw_vitals["guard"]["max"] = int(character_data["dodge"])

        feats = (
            character_data.get("features_and_traits")
            or character_data.get("traits_and_abilities")
            or character_data.get("features")
            or character_data.get("features_traits")
            or []
        )
        char_record["features_and_traits"] = feats
        char_record["traits_and_abilities"] = feats
        if "spells" in char_record and isinstance(char_record["spells"], list):
            from core.parsers.spell_registry import sanitize_parsed_spells
            char_record["spells"] = sanitize_parsed_spells(char_record["spells"])
        if "weapons" in char_record and isinstance(char_record["weapons"], list):
            char_record["weapons"] = sanitize_character_weapons(char_record["weapons"], char_record.get("spells"))
        if "defenses" in character_data or "resistances" in character_data:
            char_record["defenses"] = character_data.get("defenses") or character_data.get("resistances") or ""
            char_record["resistances"] = character_data.get("resistances") or character_data.get("defenses") or ""

        # Ensure universal_core & native_system_data exist for schema 2.0
        if "universal_core" not in char_record:
            char_record["universal_core"] = self._build_universal_core(char_record)
        if "native_system_data" not in char_record:
            char_record["native_system_data"] = self._build_native_system_data(char_record)

        self._save_character(char_record)
        logger.info(f"[VAULT] Saved character '{c_name}' from campaign into Vault as Canonical Root (Ruleset: {char_record['ruleset_label']}).")

        return {
            "status": "success",
            "character": char_record,
            "message": f"Saved '{c_name}' into the Character Vault as a permanent hero."
        }

    # --- In-World Progression & Level-Up Engine ---
    def level_up_character(
        self,
        char_id: str,
        current_campaign_genre: str,
        level_increment: int = 1,
        stat_boosts: Optional[Dict[str, int]] = None,
        new_traits: Optional[List[Dict[str, str]]] = None
    ) -> Dict[str, Any]:
        """
        Levels up the character.
        If in their native genre, levels up the ROOT profile.
        If in a crossover branch, levels up ONLY the branch profile.
        """
        char = self.get_character(char_id)
        if not char:
            return {"status": "error", "message": f"Character '{char_id}' not found."}

        native_genre = char.get("native_genre")
        is_in_native_world = (native_genre == current_campaign_genre)

        old_level = char.get("level", 1)
        new_level = old_level + level_increment
        char["level"] = new_level

        # Apply attribute boosts
        if stat_boosts:
            attrs = char.setdefault("attributes", {})
            for attr, delta in stat_boosts.items():
                if attr in attrs:
                    attrs[attr] += delta
                else:
                    attrs[attr] = delta

        # Apply new abilities tagged with the acquisition genre
        if new_traits:
            traits = char.setdefault("traits_and_abilities", [])
            for t in new_traits:
                traits.append({
                    "name": t.get("name"),
                    "description": t.get("description", "Granted on level up."),
                    "genre": current_campaign_genre
                })

        char["updated_at"] = _utc_now_iso()
        self._save_character(char)

        target_type = "Canonical Root" if is_in_native_world else f"Crossover Variant ({current_campaign_genre})"
        logger.info(f"[VAULT] Leveled up '{char['name']}' to Level {new_level} [{target_type}].")

        return {
            "status": "success",
            "character_id": char["id"],
            "character_name": char["name"],
            "old_level": old_level,
            "new_level": new_level,
            "target_updated": target_type,
            "is_root_updated": is_in_native_world
        }

    def adjudicate_item(
        self,
        char_id: str,
        item_name: str,
        resolution: str,
        target_genre: Optional[str] = None,
        transmuted_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Adjudicates an out-of-genre item during crossover import:
        - 'quarantine': moves item into quarantined_stash (dormant)
        - 'transmute': converts item into in-genre equivalent
        - 'relic': permits as finite-charge relic
        """
        char = self.get_character(char_id)
        if not char:
            return {"status": "error", "message": f"Character '{char_id}' not found."}

        inv = char.setdefault("inventory", [])
        stash = char.setdefault("quarantined_stash", [])
        wpns = char.setdefault("weapons", [])

        # Find in inventory or weapons (supports string or dict records)
        def _get_item_name(x: Any) -> str:
            if isinstance(x, dict):
                return str(x.get("name") or x.get("item") or "").strip()
            return str(x).strip()

        target_name_lower = str(item_name).strip().lower()

        found_idx = next((i for i, it in enumerate(inv) if _get_item_name(it).lower() == target_name_lower), None)
        item_data: Dict[str, Any]
        if found_idx is not None:
            raw_item = inv.pop(found_idx)
            item_data = raw_item if isinstance(raw_item, dict) else {"name": str(raw_item)}
        else:
            w_idx = next((i for i, w in enumerate(wpns) if _get_item_name(w).lower() == target_name_lower), None)
            if w_idx is not None:
                raw_w = wpns.pop(w_idx)
                item_data = raw_w if isinstance(raw_w, dict) else {"name": str(raw_w)}
            else:
                item_data = {"name": item_name}

        if resolution == "quarantine":
            stash.append({
                "name": item_data.get("name", item_name),
                "original_genre": item_data.get("genre", char.get("native_genre", "generic")),
                "quarantine_reason": f"Dormant: quarantined for {target_genre or char.get('current_genre', 'setting')}."
            })
        elif resolution == "transmute":
            new_name = transmuted_name or f"{item_name} (Transmuted)"
            inv.append({
                "name": new_name,
                "genre": target_genre or char.get("current_genre", "generic"),
                "status": "active",
                "transmuted_from": item_name
            })
        elif resolution == "relic":
            inv.append({
                "name": item_data.get("name", item_name),
                "genre": target_genre or char.get("current_genre", "generic"),
                "status": "active",
                "mode": "relic",
                "charges_remaining": item_data.get("charges_remaining", 10)
            })

        char["updated_at"] = _utc_now_iso()
        self._save_character(char)
        return {"status": "success", "character": char}

    def save_character(self, char_data: Dict[str, Any]) -> Dict[str, Any]:
        """Public method to save or upsert character data in the Character Vault."""
        char_id = char_data.get("id")
        existing = self.get_character(char_id) if char_id else None
        merged = json.loads(json.dumps(existing)) if existing else {
            "schema_version": "2.0",
            "is_root": True,
            "parent_id": None,
            "variant_label": "Canonical Root",
            "quarantined_stash": []
        }

        # Merge new incoming data
        merged.update(char_data)
        if "spells" in merged and isinstance(merged["spells"], list):
            from core.parsers.spell_registry import sanitize_parsed_spells
            merged["spells"] = sanitize_parsed_spells(merged["spells"])
        if "weapons" in merged and isinstance(merged["weapons"], list):
            merged["weapons"] = sanitize_character_weapons(merged["weapons"], merged.get("spells"))
        if not merged.get("id"):
            base_name = re.sub(r'[^a-zA-Z0-9_]', '', merged.get("name", "hero").lower())
            now_str = _utc_now_iso().replace('-', '').replace(':', '').replace('.', '')[:14]
            merged["id"] = f"char_{base_name}_{now_str}"

        # Ensure vitals structure is normalized
        ruleset = merged.get("ruleset") or merged.get("native_system") or "dnd_5e"
        is_coc = "cthulhu" in ruleset.lower() or "coc" in ruleset.lower()
        max_hp = int(merged.get("max_health") or merged.get("hp_max") or merged.get("health") or (13 if is_coc else 20))
        cur_hp = int(merged.get("current_health") or merged.get("hp_current") or max_hp)
        cur_hp = max(0, min(max_hp, cur_hp))

        if is_coc:
            guard_val = int(merged.get("dodge") or merged.get("guard") or merged.get("armor_class") or 40)
            guard_label = "Dodge Defense"
        else:
            guard_val = int(merged.get("guard") or merged.get("armor_class") or 10)
            guard_label = "Armor Defense"

        merged.setdefault("vitals", {})
        merged["vitals"]["health"] = {"current": cur_hp, "max": max(1, max_hp), "label": "Health"}
        merged["vitals"]["guard"] = {"current": guard_val, "max": max(1, guard_val), "label": guard_label}
        merged["armor_class"] = guard_val

        # Update ruleset label & genre
        ruleset = merged.get("ruleset") or merged.get("native_system") or "dnd_5e"
        merged["ruleset"] = ruleset
        merged["ruleset_label"] = get_ruleset_display_label(ruleset)
        if not merged.get("native_genre"):
            merged["native_genre"] = "cybernetic_action" if "cyber" in ruleset.lower() else ("modern_horror" if "cthulhu" in ruleset.lower() else "heroic_fantasy")

        # Rebuild Universal Core and Native System Data (Schema 2.0)
        merged["universal_core"] = self._build_universal_core(merged)
        merged["native_system_data"] = self._build_native_system_data(merged)
        merged["updated_at"] = _utc_now_iso()

        self._save_character(merged)
        logger.info(f"[VAULT] Saved/updated character '{merged.get('name')}' ({merged['id']}) in Character Vault.")
        return {"status": "success", "character": merged}

    def delete_character(self, char_id: str) -> bool:
        """Deletes character file from the vault."""
        path = self._file_path(char_id)
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"[VAULT] Deleted character '{char_id}'.")
            return True
        return False

    def get_lineage(self, char_id: str) -> Dict[str, Any]:
        """Returns the root character and all child crossover variants."""
        char = self.get_character(char_id)
        if not char:
            return {"status": "error", "message": "Character not found"}

        root_id = char["id"] if char.get("is_root", True) else char.get("parent_id", char["id"])
        root = self.get_character(root_id) or char

        variants = []
        for c in self.list_characters():
            if c.get("parent_id") == root_id:
                variants.append(c)

        return {
            "status": "success",
            "root": root,
            "variants": variants
        }

    def _save_character(self, char_data: Dict[str, Any]):
        genre = char_data.get("native_genre") or char_data.get("current_genre")
        path = self._file_path(char_data["id"], genre=genre)
        old_path = self._file_path(char_data["id"])
        if old_path and os.path.exists(old_path) and os.path.abspath(old_path) != os.path.abspath(path):
            try:
                os.remove(old_path)
            except Exception as e_rm:
                logger.debug(f"[VAULT] Could not remove old file location '{old_path}': {e_rm}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(char_data, f, indent=2)
