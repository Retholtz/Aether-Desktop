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
    "pf2e": "pathfinder_2e",

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

    def _file_path(self, char_id: str) -> str:
        safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', char_id.lower())
        return os.path.join(self.vault_dir, f"{safe_id}.json")

    # --- Character Retrieval & Listing ---
    def get_character(self, char_id: str) -> Optional[Dict[str, Any]]:
        path = self._file_path(char_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[VAULT] Failed reading character '{char_id}': {e}")
            return None

    def list_characters(self) -> List[Dict[str, Any]]:
        """Lists all characters in the vault, categorized by root and variants."""
        results = []
        if not os.path.exists(self.vault_dir):
            return results
        for fname in os.listdir(self.vault_dir):
            if fname.endswith(".json"):
                fpath = os.path.join(self.vault_dir, fname)
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
                            "inventory_count": len(data.get("inventory", [])),
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
                item_genre = item.get("genre", native_genre)
                if item_genre != target_genre:
                    stash.append({
                        "name": item.get("name"),
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
                    raw_bytes=content_bytes
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
            assigned_ruleset = ruleset_override or ch.get("native_system") or ch.get("ruleset") or ch.get("system_preset") or "dnd_5e"
            assigned_genre = ch.get("native_genre") or ("cybernetic_action" if "cyber" in assigned_ruleset.lower() else "heroic_fantasy")

            # Determine health with proper fallbacks and bounds clamping
            max_hp = int(ch.get("max_health") or ch.get("max_hp") or ch.get("health") or 20)
            cur_hp = int(ch.get("current_health") or ch.get("current_hp") or ch.get("health") or max_hp)
            cur_hp = max(0, min(max_hp, cur_hp))
            guard_ac = int(ch.get("guard") or ch.get("armor_class") or ch.get("ac") or 10)

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
                    "guard": {"current": guard_ac, "max": max(1, guard_ac), "label": "Armor Defense"}
                }

            char_record = {
                "schema_version": "1.0",
                "id": char_id,
                "parent_id": None,
                "is_root": True,
                "name": c_name,
                "variant_label": "Canonical Root",
                "player_name": ch.get("player_name") or "Player",
                "level": int(ch.get("level") or 1),
                "archetype": ch.get("archetype") or ch.get("class") or "Adventurer",
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
                "weapons": ch.get("weapons") or [],
                "inventory": ch.get("inventory") or [],
                "traits_and_abilities": ch.get("traits_and_abilities") or ch.get("features") or [],
                "quarantined_stash": [],
                "source_document": filename,
                "updated_at": _utc_now_iso()
            }

            self._save_character(char_record)
            saved_characters.append(char_record)
            logger.info(f"[VAULT] Imported '{c_name}' into vault from document '{filename}' (Ruleset: {char_record['ruleset_label']}).")

        return {
            "status": "success",
            "characters": saved_characters,
            "count": len(saved_characters),
            "message": f"Successfully imported {len(saved_characters)} character(s) into vault from '{filename}'."
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
        ruleset = camp_meta.get("system_preset") or camp_meta.get("ruleset") or character_data.get("native_system") or "dnd_5e"
        genre = camp_meta.get("genre") or character_data.get("native_genre") or ("cybernetic_action" if "cyber" in ruleset.lower() else "heroic_fantasy")

        char_record = json.loads(json.dumps(character_data))
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

        # Find in inventory
        found_idx = next((i for i, it in enumerate(inv) if it.get("name", "").lower() == item_name.lower()), None)
        item_data = inv.pop(found_idx) if found_idx is not None else {"name": item_name}

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
        """Public method to save or upsert character data."""
        if not char_data.get("id"):
            base_name = re.sub(r'[^a-zA-Z0-9_]', '', char_data.get("name", "hero").lower())
            now_str = _utc_now_iso().replace('-', '').replace(':', '').replace('.', '')[:14]
            char_data["id"] = f"char_{base_name}_{now_str}"
        char_data["updated_at"] = _utc_now_iso()
        self._save_character(char_data)
        return {"status": "success", "character": char_data}

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
        path = self._file_path(char_data["id"])
        with open(path, "w", encoding="utf-8") as f:
            json.dump(char_data, f, indent=2)
