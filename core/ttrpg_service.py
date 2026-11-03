"""
core/ttrpg_service.py - Universal System-Agnostic Tabletop Roleplaying Engine
Handles cryptographic dice parsing, character vitals mutations, combat initiative,
campaign editing, and DM/GM document grounding (rulesets, campaigns, character sheets).
"""
import os
import re
import json
import copy
import base64
import secrets
import difflib
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from core.logger import get_logger
from core.project_service import ProjectService

logger = get_logger("TTRPGService")

# Concise, system-specific rules directives that replace needing 200-page core rulebooks
PRESET_RULES_DIRECTIVES: Dict[str, str] = {
    "dnd_5e": (
        "System: Dungeons & Dragons 5th Edition (D&D 5e).\n"
        "- Mechanics: Core d20 roll + ability modifier + proficiency bonus vs Target DC or Armor Class (AC).\n"
        "- Combat: Standard Action, Bonus Action, Reaction, Movement. Turn order by Initiative (1d20+DEX).\n"
        "- Vitals: Hit Points (HP) and Armor Class (Guard). Track Death Saving Throws at 0 HP.\n"
        "- Spells & Skills: Standard D&D 5e spell slots, Advantage (roll 2d20 take highest), Disadvantage (roll 2d20 take lowest)."
    ),
    "heroic_d20": (
        "System: Heroic d20 (D&D 5e compatible).\n"
        "- Mechanics: Core d20 roll + ability modifier vs Target DC or Armor Defense (Guard).\n"
        "- Combat: Action, Move, Reaction. Advantage/Disadvantage mechanics."
    ),
    "pathfinder_2e": (
        "System: Pathfinder 2nd Edition (PF2e).\n"
        "- Mechanics: d20 roll + modifiers vs DC / AC. 4 degrees of success (Crit Success: +10 over DC, Success, Failure, Crit Failure: -10 under DC).\n"
        "- Combat: 3-Action Economy per turn + 1 Reaction. Multiple Attack Penalty applies."
    ),
    "cyberpunk": (
        "System: Cyberpunk RED / 2020.\n"
        "- Mechanics: Stat + Skill + 1d10 exploding roll vs Difficulty Value (DV).\n"
        "- Vitals: Hit Points, Armor (SP - Stopping Power ablated by penetrating damage), Humanity/Empathy."
    ),
    "cybernetic_action": (
        "System: Cybernetic Action / High-Tech Street Tactical.\n"
        "- Mechanics: Stat + Skill + 1d10 vs DV. Armor ablation and chrome enhancement."
    ),
    "call_of_cthulhu": (
        "System: Call of Cthulhu (Investigative Percentile d100).\n"
        "- Mechanics: Roll 1d100 <= Skill rating (Regular, Hard = 1/2, Extreme = 1/5, Fumble = 96-100).\n"
        "- Vitals: Hit Points, Sanity (SAN), Magic Points, Luck."
    ),
    "investigative_d100": (
        "System: Skill Percentile d100 & Cosmic Mystery.\n"
        "- Mechanics: Roll 1d100 <= Skill score. Sanity tracking and stress mechanics."
    ),
    "narrative_clocks": (
        "System: Narrative Dice & Progress Clocks (Blades in the Dark / PbtA).\n"
        "- Mechanics: Action rolls with Success with Complications / Partial Success, Harm levels, Stress, and 4/6/8-segment Progress Clocks."
    ),
    "sandbox_custom": (
        "System: Custom Sandbox / À La Carte.\n"
        "- Mechanics: Flexible GM ruling based on the user-defined character vitals, attributes, and meters."
    )
}


class TTRPGService:
    def __init__(
        self,
        data_dir: str = "data/campaigns",
        manifest_path: str = "data/campaigns.json",
        client_getter: Optional[Any] = None
    ):
        self.data_dir = os.path.abspath(data_dir)
        self.manifest_path = os.path.abspath(manifest_path)
        self.manifest: Dict[str, Any] = {"active_campaign": None, "campaigns": {}}
        self.active_state: Optional[Dict[str, Any]] = None
        self.client_getter = client_getter

        os.makedirs(self.data_dir, exist_ok=True)
        self.load_manifest()
        self.load_active_campaign_state()

    def _get_genai_client(self):
        if self.client_getter:
            try:
                c = self.client_getter()
                if c is not None:
                    return c
            except Exception as e:
                logger.debug(f"[TTRPG] client_getter error: {e}")
        try:
            from core.config_manager import ConfigManager, unprotect_secret
            from google import genai
            cm = ConfigManager()
            api_cfg = cm.config.get("api", {})
            enc_key = api_cfg.get("api_key_encrypted", "")
            key = unprotect_secret(enc_key) if enc_key else os.environ.get("GEMINI_API_KEY", "")
            if key:
                return genai.Client(api_key=key)
        except Exception as e:
            logger.debug(f"[TTRPG] Could not initialize genai.Client directly: {e}")
        return None

    # --- Manifest & State Persistence ---
    def load_manifest(self):
        if os.path.exists(self.manifest_path):
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    self.manifest = json.load(f)
            except Exception as e:
                logger.error(f"[TTRPG] Error loading manifest: {e}")
                self.manifest = {"active_campaign": None, "campaigns": {}}
        else:
            self.manifest = {"active_campaign": None, "campaigns": {}}

        # Automatically discover and register any campaign directories on disk
        campaigns = self.manifest.setdefault("campaigns", {})
        if os.path.exists(self.data_dir):
            for entry in sorted(os.listdir(self.data_dir)):
                entry_path = os.path.join(self.data_dir, entry)
                if os.path.isdir(entry_path):
                    if entry not in campaigns:
                        st_path = os.path.join(entry_path, "state.json")
                        st_data = {}
                        if os.path.exists(st_path):
                            try:
                                with open(st_path, "r", encoding="utf-8") as sf:
                                    st_data = json.load(sf)
                            except Exception:
                                pass
                        campaigns[entry] = {
                            "id": entry,
                            "title": entry.replace("_", " ").title(),
                            "system_preset": "dnd_5e",
                            "gm_mode": "interactive_narrator",
                            "difficulty": "helpful",
                            "party_count": len(st_data.get("characters", {})),
                            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                        }
        if not self.manifest.get("active_campaign") and campaigns:
            self.manifest["active_campaign"] = list(campaigns.keys())[0]
        self.save_manifest()

    def save_manifest(self):
        os.makedirs(os.path.dirname(self.manifest_path), exist_ok=True)
        try:
            with open(self.manifest_path, "w", encoding="utf-8") as f:
                json.dump(self.manifest, f, indent=2)
        except Exception as e:
            logger.error(f"[TTRPG] Error saving manifest: {e}")

    def load_active_campaign_state(self):
        active_id = self.manifest.get("active_campaign")
        if not active_id:
            self.active_state = None
            return

        state_path = os.path.join(self.data_dir, active_id, "state.json")
        if os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    self.active_state = json.load(f)
                if isinstance(self.active_state, dict):
                    self.active_state.setdefault("documents", {})
                    self.active_state.setdefault("journal", [])
                    self.active_state.setdefault("latest_recap", "")
                    self.active_state.setdefault("active_quest", "")
                    self.active_state.setdefault("current_location", "")
                    self.active_state.setdefault("discovered_clues", [])
                    self.active_state.setdefault("campaign_synopsis", {})

                    # Ensure no lingering character sheets remain in documents map or files dir (one-and-done)
                    docs = self.active_state.get("documents", {})
                    stale_sheets = [fname for fname, d in docs.items() if d.get("category") == "character_sheet"]
                    if stale_sheets and active_id:
                        fdir = os.path.join(self.data_dir, active_id, "files")
                        for s_name in stale_sheets:
                            docs.pop(s_name, None)
                            s_path = os.path.join(fdir, s_name)
                            if os.path.exists(s_path):
                                try:
                                    os.remove(s_path)
                                except Exception:
                                    pass
                        self.save_active_campaign_state()

                    # Ensure all character vitals have valid clamped current health (current <= max)
                    chars = self.active_state.get("characters", {})
                    for c in chars.values():
                        h = c.get("vitals", {}).get("health")
                        if h and isinstance(h, dict) and "max" in h and "current" in h:
                            h["max"] = max(1, int(h["max"]))
                            h["current"] = max(0, min(h["max"], int(h["current"])))
            except Exception as e:
                logger.error(f"[TTRPG] Error loading campaign state for '{active_id}': {e}")
                self.active_state = self._generate_default_state(active_id)
        else:
            self.active_state = self._generate_default_state(active_id)
            self.save_active_campaign_state()

    def save_active_campaign_state(self):
        if not self.active_state:
            return
        active_id = self.active_state.get("campaign_id")
        if not active_id:
            return

        c_dir = os.path.join(self.data_dir, active_id)
        os.makedirs(c_dir, exist_ok=True)
        state_path = os.path.join(c_dir, "state.json")

        try:
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(self.active_state, f, indent=2)
        except Exception as e:
            logger.error(f"[TTRPG] Error saving campaign state: {e}")

    def _generate_default_state(self, campaign_id: str) -> Dict[str, Any]:
        return {
            "campaign_id": campaign_id,
            "party_caller_id": None,
            "focused_speaker_id": None,
            "single_mic_settings": {
                "filter_unprefaced_side_banter": True,
                "require_clarification_on_anonymous_actions": True,
                "clarification_timeout_seconds": 15
            },
            "pending_action_stash": None,
            "current_round": 1,
            "active_turn_index": 0,
            "initiative_order": [],
            "characters": {},
            "documents": {},
            "journal": [],
            "latest_recap": "",
            "active_quest": "",
            "current_location": "",
            "discovered_clues": [],
            "campaign_synopsis": {}
        }

    # --- Campaign Management Helpers ---
    def create_campaign(
        self,
        title: str,
        system_preset: str = "dnd_5e",
        gm_mode: str = "interactive_narrator",
        setting_overview: str = "",
        tone: str = "Cinematic, grounded",
        difficulty: str = "helpful",
        linked_project_id: str = "",
        campaign_id: Optional[str] = None,
        initial_files: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        clean_title = (title or "").strip()
        # If title is blank but an initial file was uploaded, derive title from module/adventure file
        if not clean_title and initial_files:
            for f_info in initial_files:
                cat = (f_info.get("category") or "").lower()
                fn = f_info.get("filename") or f_info.get("name") or ""
                if fn and (cat in ("campaign", "module", "ruleset") or any(k in fn.lower() for k in ("adventure", "module", "campaign", "quest", "hunt"))):
                    stem = os.path.splitext(os.path.basename(fn))[0]
                    clean_stem = re.sub(r'[\d_\-]+$', '', stem).replace('_', ' ').strip().title()
                    if clean_stem:
                        clean_title = clean_stem
                        break
            if not clean_title:
                for f_info in initial_files:
                    fn = f_info.get("filename") or f_info.get("name") or ""
                    if fn:
                        stem = os.path.splitext(os.path.basename(fn))[0]
                        clean_stem = re.sub(r'[\d_\-]+$', '', stem).replace('_', ' ').strip().title()
                        if clean_stem:
                            clean_title = clean_stem
                            break

        if not clean_title:
            return {"status": "error", "message": "Campaign title is required."}

        cid = campaign_id or re.sub(r'[^a-z0-9_]+', '_', clean_title.lower()).strip('_')
        if not cid:
            cid = f"campaign_{len(self.manifest.get('campaigns', {})) + 1}"

        campaigns = self.manifest.setdefault("campaigns", {})
        if cid in campaigns:
            # If auto-generated from file stem, make unique instead of hard error
            if not title:
                cid = f"{cid}_{len(campaigns) + 1}"
            else:
                return {"status": "error", "message": f"Campaign '{cid}' already exists."}

        meta = {
            "id": cid,
            "title": clean_title,
            "system_preset": system_preset or "dnd_5e",
            "gm_mode": gm_mode or "interactive_narrator",
            "setting_overview": setting_overview or "",
            "tone": tone or "Cinematic, grounded",
            "difficulty": difficulty or "helpful",
            "linked_project_id": linked_project_id or "",
            "party_count": 0,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
        campaigns[cid] = meta
        self.manifest["active_campaign"] = cid
        self.save_manifest()
        self.active_state = self._generate_default_state(cid)
        self.save_active_campaign_state()

        # Ingest and index any initial documents provided (rulesets, modules, character sheets)
        imported_characters: List[str] = []
        if initial_files and isinstance(initial_files, list):
            for f_info in initial_files:
                if not isinstance(f_info, dict):
                    continue
                f_name = f_info.get("filename") or f_info.get("name")
                f_data = f_info.get("data") or f_info.get("base64") or f_info.get("content")
                f_cat = f_info.get("category", "ruleset")
                if not f_name or not f_data:
                    continue

                try:
                    if isinstance(f_data, str):
                        if "," in f_data or f_info.get("base64"):
                            b64_clean = f_data.split(",", 1)[1] if "," in f_data else f_data
                            try:
                                raw_bytes = base64.b64decode(b64_clean)
                            except Exception:
                                raw_bytes = f_data.encode("utf-8")
                        else:
                            try:
                                raw_bytes = base64.b64decode(f_data, validate=True)
                            except Exception:
                                raw_bytes = f_data.encode("utf-8")
                    elif isinstance(f_data, bytes):
                        raw_bytes = f_data
                    else:
                        raw_bytes = str(f_data).encode("utf-8")
                except Exception as e:
                    logger.error(f"[TTRPG] Error decoding initial file '{f_name}': {e}")
                    continue

                res_doc = self.add_campaign_document(
                    filename=f_name,
                    content_bytes=raw_bytes,
                    doc_category=f_cat,
                    campaign_id=cid
                )
                if res_doc.get("status") == "success":
                    if res_doc.get("imported_characters"):
                        imported_characters.extend(res_doc["imported_characters"])

            # Run extract_campaign_synopsis to auto-generate overview & hooks from module if overview was empty
            try:
                synopsis = self.extract_campaign_synopsis(campaign_id=cid)
                if synopsis and not meta.get("setting_overview"):
                    meta["setting_overview"] = synopsis.get("synopsis", "")
                    self.save_manifest()
            except Exception as e:
                logger.warning(f"[TTRPG] Error extracting synopsis for new campaign '{cid}': {e}")

            # Update party count in manifest
            if self.active_state:
                meta["party_count"] = len(self.active_state.get("characters", {}))
                self.save_manifest()

        return {
            "status": "success",
            "campaign": meta,
            "state": self.active_state,
            "imported_characters": imported_characters
        }

    def update_campaign(
        self,
        campaign_id: Optional[str] = None,
        title: Optional[str] = None,
        system_preset: Optional[str] = None,
        gm_mode: Optional[str] = None,
        setting_overview: Optional[str] = None,
        tone: Optional[str] = None,
        difficulty: Optional[str] = None,
        linked_project_id: Optional[str] = None,
        dice_mode: Optional[str] = None
    ) -> Dict[str, Any]:
        """Updates an existing campaign's metadata in data/campaigns.json."""
        cid = campaign_id or self.manifest.get("active_campaign")
        campaigns = self.manifest.get("campaigns", {})
        if not cid or cid not in campaigns:
            return {"status": "error", "message": f"Campaign '{cid}' not found."}

        meta = campaigns[cid]
        if title is not None:
            clean_title = title.strip()
            if not clean_title:
                return {"status": "error", "message": "Campaign title cannot be empty."}
            meta["title"] = clean_title
        if system_preset is not None:
            meta["system_preset"] = system_preset.strip() or "heroic_d20"
        if gm_mode is not None:
            meta["gm_mode"] = gm_mode.strip() or "interactive_narrator"
        if setting_overview is not None:
            meta["setting_overview"] = setting_overview.strip()
        if tone is not None:
            meta["tone"] = tone.strip() or "Cinematic, grounded"
        if difficulty is not None:
            meta["difficulty"] = difficulty.strip() or "helpful"
        if linked_project_id is not None:
            meta["linked_project_id"] = linked_project_id.strip()
        if dice_mode is not None:
            meta["dice_mode"] = "physical" if str(dice_mode).lower().strip() in ("physical", "irl", "tabletop", "manual") else "digital"

        meta["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.save_manifest()
        if self.manifest.get("active_campaign") == cid:
            self.load_active_campaign_state()

        return {
            "status": "success",
            "campaign": meta,
            "state": self.active_state
        }

    def set_dice_mode(self, mode: str, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        """Sets dice rolling mode ('physical' or 'digital') for the campaign."""
        cid = campaign_id or self.manifest.get("active_campaign")
        campaigns = self.manifest.get("campaigns", {})
        if not cid or cid not in campaigns:
            return {"status": "error", "message": f"Campaign '{cid}' not found."}
        clean_mode = "physical" if str(mode).lower().strip() in ("physical", "irl", "tabletop", "manual") else "digital"
        campaigns[cid]["dice_mode"] = clean_mode
        self.save_manifest()
        return {"status": "success", "campaign_id": cid, "dice_mode": clean_mode}

    def get_dice_mode(self, campaign_id: Optional[str] = None) -> str:
        """Returns the dice rolling mode ('physical' or 'digital') for the active campaign."""
        cid = campaign_id or self.manifest.get("active_campaign")
        if cid and cid in self.manifest.get("campaigns", {}):
            return self.manifest["campaigns"][cid].get("dice_mode", "digital")
        return "digital"

    def set_active_campaign(self, campaign_id: str) -> Dict[str, Any]:
        campaigns = self.manifest.get("campaigns", {})
        if campaign_id not in campaigns:
            return {"status": "error", "message": f"Campaign '{campaign_id}' not found."}
        self.manifest["active_campaign"] = campaign_id
        self.save_manifest()
        self.load_active_campaign_state()
        return {
            "status": "success",
            "active_campaign": campaign_id,
            "campaign": campaigns[campaign_id],
            "state": self.active_state
        }

    def delete_campaign(self, campaign_id: str) -> Dict[str, Any]:
        campaigns = self.manifest.get("campaigns", {})
        if campaign_id not in campaigns:
            return {"status": "error", "message": f"Campaign '{campaign_id}' not found."}
        del campaigns[campaign_id]
        if self.manifest.get("active_campaign") == campaign_id:
            remaining = list(campaigns.keys())
            self.manifest["active_campaign"] = remaining[0] if remaining else None
        
        # Cleanly remove the campaign's isolated directory and all its files from disk
        c_dir = os.path.join(self.data_dir, campaign_id)
        if os.path.exists(c_dir):
            try:
                import shutil
                shutil.rmtree(c_dir, ignore_errors=True)
            except Exception as e:
                logger.warning(f"[TTRPG] Could not remove campaign directory '{c_dir}': {e}")

        self.save_manifest()
        self.load_active_campaign_state()
        return {
            "status": "success",
            "active_campaign": self.manifest.get("active_campaign"),
            "state": self.active_state
        }

    # --- Campaign Document Library (Rulesets, Adventures, Character Sheets) ---
    def get_campaign_files_dir(self, campaign_id: Optional[str] = None) -> Optional[str]:
        cid = campaign_id or self.manifest.get("active_campaign")
        if not cid:
            return None
        files_dir = os.path.join(self.data_dir, cid, "files")
        os.makedirs(files_dir, exist_ok=True)
        return files_dir

    def add_campaign_document(
        self,
        filename: str,
        content_bytes: bytes,
        doc_category: str = "ruleset",
        campaign_id: Optional[str] = None,
        ruleset_override: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Saves an uploaded document (ruleset, campaign module, character sheet, or notes)
        into the campaign's files directory, extracts text, and indexes metadata.
        """
        cid = campaign_id or self.manifest.get("active_campaign")
        if not cid or cid not in self.manifest.get("campaigns", {}):
            return {"status": "error", "message": "No active campaign selected."}

        safe_name = os.path.basename((filename or "").strip())
        if not safe_name:
            return {"status": "error", "message": "Filename is required."}

        valid_categories = {"ruleset", "campaign", "character_sheet", "notes"}
        cat = (doc_category or "ruleset").lower().strip()
        if cat not in valid_categories:
            cat = "ruleset"

        files_dir = self.get_campaign_files_dir(cid)
        target_path = os.path.join(files_dir, safe_name)

        try:
            with open(target_path, "wb") as f:
                f.write(content_bytes)
        except Exception as e:
            logger.error(f"[TTRPG] Failed saving campaign document '{safe_name}': {e}")
            return {"status": "error", "message": f"Failed to write file: {e}"}

        extracted_text = ProjectService.extract_file_text(target_path)
        token_est = ProjectService.estimate_tokens(extracted_text)
        preview_text = (extracted_text or "")[:300].strip()

        if cid == self.manifest.get("active_campaign"):
            if not self.active_state:
                self.load_active_campaign_state()
            state_obj = self.active_state
        else:
            state_path = os.path.join(self.data_dir, cid, "state.json")
            if os.path.exists(state_path):
                try:
                    with open(state_path, "r", encoding="utf-8") as f:
                        state_obj = json.load(f)
                except Exception:
                    state_obj = self._generate_default_state(cid)
            else:
                state_obj = self._generate_default_state(cid)

        docs_map = state_obj.setdefault("documents", {})
        doc_meta = {
            "filename": safe_name,
            "category": cat,
            "size_bytes": len(content_bytes),
            "token_estimate": token_est,
            "uploaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "preview": preview_text
        }
        docs_map[safe_name] = doc_meta

        # Check if document is a character sheet (auto-detect via form fields, JSON, OCR, or category)
        imported_characters: List[str] = []
        parsed_list = self._parse_character_from_document(safe_name, extracted_text, content_bytes, ruleset_override=ruleset_override)
        is_sheet = (cat == "character_sheet") or bool(parsed_list)

        if is_sheet:
            detected_system_label = "Character Sheet"
            if parsed_list:
                for ch_payload in parsed_list:
                    c_name = (ch_payload.get("name") or "").strip()
                    c_ruleset = ruleset_override or ch_payload.get("ruleset") or "unknown"
                    if c_ruleset == "unknown":
                        attrs = ch_payload.get("attributes") or ch_payload.get("stats") or ch_payload.get("ability_scores") or {}
                        if not attrs:
                            attrs = {k: v for k, v in ch_payload.items() if k in ("STR", "Strength", "DEX", "Dexterity", "CON", "Constitution", "INT", "Intelligence", "WIS", "Wisdom", "CHA", "Charisma", "REF", "TECH", "COOL", "BODY", "EMP", "SAN", "POW", "EDU")}
                        lower_arch = (ch_payload.get("archetype") or ch_payload.get("character_class") or ch_payload.get("class") or "").lower()
                        feats_str = " ".join(ch_payload.get("features_and_traits") or ch_payload.get("features") or ch_payload.get("cyberware") or []).lower()

                        dnd_classes = ("fighter", "wizard", "rogue", "cleric", "paladin", "barbarian", "bard", "druid", "ranger", "sorcerer", "warlock", "monk", "elf", "dwarf", "halfling")
                        cyber_roles = ("rockerboy", "solo", "netrunner", "tech", "medtech", "media", "exec", "lawman", "fixer", "nomad", "edgerunner")

                        if any(k in attrs for k in ("REF", "TECH", "COOL", "EMP", "BODY", "MOVE", "LUCK", "WILL")) or any(w in lower_arch for w in cyber_roles) or "cyber" in feats_str:
                            c_ruleset = "cyberpunk"
                        elif any(k in attrs for k in ("SAN", "POW", "EDU", "Sanity")) or "investigator" in lower_arch or "sanity" in feats_str:
                            c_ruleset = "call_of_cthulhu"
                        elif "ancestry" in ch_payload or "class_dc" in ch_payload:
                            c_ruleset = "pathfinder_2e"
                        elif any(k in attrs for k in ("STR", "Strength", "DEX", "Dexterity", "CON", "Constitution", "Wisdom", "Charisma")) or any(w in lower_arch for w in dnd_classes) or "spell" in feats_str:
                            c_ruleset = "dnd_5e"

                    if c_ruleset == "unknown":
                        import base64
                        return {
                            "status": "requires_ruleset_selection",
                            "message": f"Aether could not determine which tabletop system '{c_name or safe_name}' belongs to. Please select your ruleset.",
                            "character": ch_payload,
                            "filename": safe_name,
                            "base64_content": base64.b64encode(content_bytes).decode("utf-8"),
                            "doc_category": cat,
                            "campaign_id": cid
                        }

                    ch_payload["ruleset"] = c_ruleset
                    detected_system_label = c_ruleset.replace("_", " ").title()
                    if c_name:
                        res_upsert = self.upsert_character_from_sheet(ch_payload, filename=safe_name, target_state=state_obj)
                        if res_upsert.get("status") == "success":
                            imported_characters.append(c_name)
                            # Auto-save a Canonical Root copy into the Character Vault
                            try:
                                from core.character_vault_service import CharacterVaultService
                                vault_svc = CharacterVaultService()
                                vault_svc.import_from_document(
                                    filename=safe_name,
                                    content_bytes=content_bytes,
                                    ttrpg_service=self,
                                    ruleset_override=c_ruleset
                                )
                            except Exception as e_vault:
                                logger.debug(f"[TTRPG] Auto-vault mirror failed: {e_vault}")
            elif cat == "character_sheet":
                # Fallback: if category was explicitly character_sheet but parser returned empty list
                fallback_name = os.path.splitext(safe_name)[0].replace("_", " ").title()
                fallback_ruleset = ruleset_override or (self.manifest.get("campaigns", {}).get(cid, {}).get("system_preset")) or "dnd_5e"
                res_add = self.add_character({
                    "name": fallback_name,
                    "archetype": "Adventurer",
                    "ruleset": fallback_ruleset
                }, target_state=state_obj)
                if res_add.get("status") == "success":
                    imported_characters.append(fallback_name)

            # ONE AND DONE: Remove the character sheet file from disk and do NOT track in documents map.
            # The campaign document list strictly houses the campaign adventure module and rulesets/notes.
            docs_map.pop(safe_name, None)
            if os.path.exists(target_path):
                try:
                    os.remove(target_path)
                except Exception as e_rm:
                    logger.warning(f"[TTRPG] Could not remove imported character sheet '{target_path}': {e_rm}")

            if cid in self.manifest.get("campaigns", {}):
                self.manifest["campaigns"][cid]["party_count"] = len(state_obj.get("characters", {}))
                self.save_manifest()

            if cid == self.manifest.get("active_campaign"):
                self.save_active_campaign_state()
            else:
                state_path = os.path.join(self.data_dir, cid, "state.json")
                os.makedirs(os.path.dirname(state_path), exist_ok=True)
                with open(state_path, "w", encoding="utf-8") as f:
                    json.dump(state_obj, f, indent=2)

            logger.info(f"[TTRPG] Character sheet '{safe_name}' imported into party roster ({imported_characters}) as {detected_system_label} and removed from file library (one-and-done).")
            return {
                "status": "success",
                "message": f"Character sheet '{safe_name}' recognized as {detected_system_label} and imported to Party Roster ({', '.join(imported_characters)}) & Character Vault (removed from library to preserve evolved data).",
                "document": None,
                "documents": docs_map,
                "imported_characters": imported_characters,
                "ruleset": detected_system_label,
                "removed_after_import": True,
                "state": self.active_state
            }

        if cid == self.manifest.get("active_campaign"):
            self.save_active_campaign_state()
        else:
            state_path = os.path.join(self.data_dir, cid, "state.json")
            os.makedirs(os.path.dirname(state_path), exist_ok=True)
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state_obj, f, indent=2)

        return {
            "status": "success",
            "document": doc_meta,
            "documents": docs_map,
            "imported_characters": imported_characters,
            "state": self.active_state
        }

    def _parse_character_from_form_fields(self, fields: Dict[str, str], filename: str) -> Optional[Dict[str, Any]]:
        """
        Extracts structured character sheet data from interactive PDF AcroForm widgets
        (standard for D&D Beyond and official fillable PDF character sheets).
        """
        if not fields or not isinstance(fields, dict):
            return None

        char_indicators = (
            "CharacterName", "CharacterName2", "CharacterName4",
            "CLASS  LEVEL", "CLASS  LEVEL2", "MaxHP", "AC", "STR", "DEX", "CON", "INT", "WIS", "CHA"
        )
        if not any(k in fields for k in char_indicators):
            return None

        def _clean(val: Any) -> str:
            if not val:
                return ""
            v = str(val).replace(r"\(", "(").replace(r"\)", ")").rstrip("\\").strip()
            v = re.sub(r'[\(\\\)]+$', '', v).strip()
            return v

        def _int_val(val: Any, default: int = 10) -> int:
            if not val:
                return default
            clean_s = _clean(val)
            m = re.search(r'[-+]?\d+', clean_s)
            if m:
                try:
                    return int(m.group(0))
                except ValueError:
                    return default
            return default

        char_name = _clean(fields.get("CharacterName") or fields.get("CharacterName2") or fields.get("CharacterName4"))
        if not char_name:
            stem = os.path.splitext(os.path.basename(filename or ""))[0]
            stem = re.sub(r'(?i)[_\-\s]*(character|char|sheet|pc|d&d|dnd|5e|statblock|stats)[_\-\s]*', ' ', stem).strip()
            char_name = stem.replace("_", " ").strip().title() if stem else "Adventurer"

        player_name = _clean(fields.get("PLAYER NAME") or fields.get("PLAYER NAME2")) or "Player"
        cls_lvl = _clean(fields.get("CLASS  LEVEL") or fields.get("CLASS  LEVEL2"))
        race = _clean(fields.get("RACE") or fields.get("RACE2"))
        bg = _clean(fields.get("BACKGROUND") or fields.get("BACKGROUND2"))
        arch_details = ", ".join(filter(None, [race, bg]))
        archetype = f"{cls_lvl} ({arch_details})" if (cls_lvl and arch_details) else (cls_lvl or arch_details or "Adventurer")

        max_hp = _int_val(fields.get("MaxHP"), 25)
        cur_hp = _int_val(fields.get("CurrentHP"), max_hp)
        guard_val = _int_val(fields.get("AC"), 14)

        from core.parsers.spell_registry import resolve_dnd_stat
        might_val = resolve_dnd_stat(fields.get("STR"), fields.get("STRmod") or fields.get("STR Mod"), 10)
        agility_val = resolve_dnd_stat(fields.get("DEX"), fields.get("DEXmod") or fields.get("DEX Mod"), 10)
        vigor_val = resolve_dnd_stat(fields.get("CON"), fields.get("CONmod") or fields.get("CON Mod"), 10)
        intellect_val = resolve_dnd_stat(fields.get("INT"), fields.get("INTmod") or fields.get("INT Mod"), 10)
        wis_val = resolve_dnd_stat(fields.get("WIS"), fields.get("WISmod") or fields.get("WIS Mod"), 10)
        cha_val = resolve_dnd_stat(fields.get("CHA"), fields.get("CHamod") or fields.get("CHAmod") or fields.get("CHA Mod"), 10)
        presence_val = max(wis_val, cha_val)

        hit_dice = _clean(fields.get("Total", "1d8")) or "1d8"
        raw_speed = _clean(fields.get("Speed", "30 ft")) or "30 ft"
        clean_speed = re.sub(r'[\(\\\)]+', ' ', raw_speed).strip()

        # Parse level and experience points
        char_level = 1
        m_lvl = re.search(r'\b(?:level|lvl)?\s*(\d+)\b', cls_lvl, re.IGNORECASE)
        if m_lvl:
            try:
                char_level = max(1, int(m_lvl.group(1)))
            except ValueError:
                char_level = 1

        raw_xp = _clean(fields.get("EXPERIENCE POINTS") or fields.get("EXPERIENCE POINTS2"))
        xp_points = 0
        if raw_xp:
            m_xp = re.search(r'\d+', raw_xp)
            if m_xp:
                try:
                    xp_points = int(m_xp.group(0))
                except ValueError:
                    xp_points = 0

        # Parse Weapons (Wpn Name 1..8, AtkBonus, Damage, Notes)
        weapons: List[Dict[str, str]] = []
        for w_idx in range(1, 9):
            prefix = f"Wpn Name {w_idx}" if w_idx > 1 else "Wpn Name"
            w_name = _clean(fields.get(prefix) or fields.get(f"Wpn Name{w_idx}") or (fields.get("Wpn Name") if w_idx == 1 else ""))
            if not w_name:
                continue
            atk_b = _clean(fields.get(f"Wpn{w_idx} AtkBonus") or fields.get(f"Wpn{w_idx} AtkBonus ") or fields.get(f"Wpn AtkBonus {w_idx}"))
            dmg_b = _clean(fields.get(f"Wpn{w_idx} Damage") or fields.get(f"Wpn{w_idx} Damage ") or fields.get(f"Wpn Damage {w_idx}"))
            notes_b = _clean(fields.get(f"Wpn Notes {w_idx}") or fields.get(f"Wpn Notes{w_idx}"))
            weapons.append({
                "name": w_name,
                "atk_bonus": atk_b or "+0",
                "damage": dmg_b or "1d4",
                "notes": notes_b
            })

        # Parse Equipment / Inventory (Eq Name0..Eq Name60)
        inventory: List[str] = []
        for eq_idx in range(60):
            eq_val = _clean(fields.get(f"Eq Name{eq_idx}") or fields.get(f"Eq Name {eq_idx}"))
            if eq_val and eq_val not in inventory:
                inventory.append(eq_val)
        raw_eq_area = _clean(fields.get("Equipment") or fields.get("Inventory"))
        if raw_eq_area:
            for line in raw_eq_area.splitlines():
                item_str = line.strip().lstrip("-•* ")
                if item_str and item_str not in inventory:
                    inventory.append(item_str)

        prof_lang = _clean(fields.get("ProficienciesLang"))
        prof_bonus = _clean(fields.get("ProfBonus")) or "+2"

        # Parse sectioned Proficiencies & Training (Armor, Weapons, Tools, Languages)
        prof_armor = _clean(fields.get("ArmorProficiencies") or fields.get("ProfArmor"))
        prof_weapons = _clean(fields.get("WeaponProficiencies") or fields.get("ProfWeapons"))
        prof_tools = _clean(fields.get("ToolProficiencies") or fields.get("ProfTools"))
        prof_languages = _clean(fields.get("Languages") or fields.get("LanguagesKnown"))

        if prof_lang and not (prof_armor or prof_weapons or prof_tools or prof_languages):
            norm_pl = prof_lang.replace(r"\n", "\n")
            armor_m = re.search(r'===\s*ARMOR\s*===\s*\n?([^\n=]+)', norm_pl, re.IGNORECASE)
            wpn_m = re.search(r'===\s*WEAPONS\s*===\s*\n?([^\n=]+)', norm_pl, re.IGNORECASE)
            tool_m = re.search(r'===\s*TOOLS\s*===\s*\n?([^\n=]+)', norm_pl, re.IGNORECASE)
            lang_m = re.search(r'===\s*LANGUAGES\s*===\s*\n?([^\n=]+)', norm_pl, re.IGNORECASE)
            if armor_m: prof_armor = armor_m.group(1).strip()
            if wpn_m: prof_weapons = wpn_m.group(1).strip()
            if tool_m: prof_tools = tool_m.group(1).strip()
            if lang_m: prof_languages = lang_m.group(1).strip()

        passives = {
            "perception": _int_val(fields.get("Passive1"), 10 + (wis_val - 10)//2),
            "insight": _int_val(fields.get("Passive2"), 10 + (wis_val - 10)//2),
            "investigation": _int_val(fields.get("Passive3"), 10 + (intellect_val - 10)//2)
        }
        senses_str = _clean(fields.get("AdditionalSenses") or fields.get("Senses") or fields.get("Vision") or fields.get("Senses1"))
        defenses_str = _clean(
            fields.get("Defenses")
            or fields.get("Defenses1")
            or fields.get("DamageDefenses")
            or fields.get("Resistances")
            or fields.get("Resistance")
            or ""
        )

        # Initiative (supports 'Init', 'Initiative', 'Initiative ')
        raw_init = _clean(fields.get("Init") or fields.get("Initiative") or fields.get("Initiative "))
        if raw_init:
            init_val = _int_val(raw_init, (agility_val - 10) // 2)
        else:
            init_val = (agility_val - 10) // 2
        init_str = f"+{init_val}" if init_val >= 0 else str(init_val)

        # Features & Traits (Supporting D&D Beyond columns, WotC fillable, and generic sheets)
        raw_feat_sources = [
            fields.get("FeaturesTraits1"),
            fields.get("FeaturesTraits2"),
            fields.get("Features and Traits"),
            fields.get("Features and Traits2"),
            fields.get("Features_and_Traits"),
            fields.get("FeaturesTraits"),
            fields.get("Features"),
            fields.get("Feats"),
            fields.get("Traits"),
            fields.get("Special Abilities")
        ]

        features_list: List[str] = []
        seen_feat_names = set()

        for raw_src in raw_feat_sources:
            if not raw_src:
                continue
            clean_src = str(raw_src).strip()
            if not clean_src:
                continue

            if "*" in clean_src:
                blocks = re.split(r'\n(?=\*\s+)', clean_src)
                for blk in blocks:
                    blk = blk.strip()
                    if not blk:
                        continue
                    if blk.startswith("*"):
                        lines = [l.strip() for l in blk.splitlines() if l.strip()]
                        hdr = lines[0].lstrip("*").strip()
                        # Clean out rulebook source tags (e.g. • PHB-2024 91, \200 PHB-2024)
                        clean_hdr = re.sub(r'[\u2022\u200b\x80\xa0\-\s]*(?:PHB|DMG|XGE|TCE|BR|MM)[\w\s\-\d]*', '', hdr, flags=re.IGNORECASE).strip()
                        clean_hdr = clean_hdr.rstrip(":-• ").strip()
                        if not clean_hdr or len(clean_hdr) < 2:
                            continue

                        body_lines = []
                        for l in lines[1:]:
                            if l.startswith("===") and l.endswith("==="):
                                continue
                            if re.match(r'^\|\s*\d+', l):
                                body_lines.append(f"({l.lstrip('|').strip()})")
                            else:
                                body_lines.append(l)
                        body_desc = " ".join(body_lines).strip()
                        feat_str = f"{clean_hdr}: {body_desc}" if body_desc else clean_hdr
                        feat_key = clean_hdr.lower()
                        if feat_key not in seen_feat_names:
                            seen_feat_names.add(feat_key)
                            features_list.append(feat_str)
            else:
                for line in clean_src.splitlines():
                    line = line.strip().lstrip("-•* ")
                    if line and not (line.startswith("===") and line.endswith("===")):
                        feat_key = line.lower()
                        if feat_key not in seen_feat_names:
                            seen_feat_names.add(feat_key)
                            features_list.append(line)

        if defenses_str and not any("resist" in f.lower() or "defense" in f.lower() for f in features_list):
            clean_def = defenses_str if defenses_str.lower().startswith(("resist", "defense", "immune")) else f"Defenses: {defenses_str}"
            features_list.insert(0, clean_def)

        # Skills & Proficiencies
        skill_field_pairs = [
            ("Acrobatics", "Acrobatics"), ("Animal Handling", "Animal"), ("Arcana", "Arcana"),
            ("Athletics", "Athletics"), ("Deception", "Deception"), ("History", "History"),
            ("Insight", "Insight"), ("Intimidation", "Intimidation"), ("Investigation", "Investigation"),
            ("Medicine", "Medicine"), ("Nature", "Nature"), ("Perception", "Perception"),
            ("Performance", "Performance"), ("Persuasion", "Persuasion"), ("Religion", "Religion"),
            ("Sleight of Hand", "SleightofHand"), ("Stealth", "Stealth"), ("Survival", "Survival")
        ]
        skills_dict: Dict[str, str] = {}
        for s_label, s_key in skill_field_pairs:
            s_val = _clean(fields.get(s_key) or fields.get(s_label))
            if s_val:
                skills_dict[s_label] = s_val

        # Spells & Cantrips (Structured parsing with level, DC, damage formulas, and header filtering)
        from core.parsers.spell_registry import parse_structured_spells, sanitize_character_weapons
        spells_list = parse_structured_spells(fields)
        weapons = sanitize_character_weapons(weapons, spells_list)

        spell_ability = _clean(fields.get("spellCastingAbility0") or fields.get("SpellcastingAbility") or fields.get("SpellAbility") or "")
        spell_save_dc = _clean(fields.get("spellSaveDC0") or fields.get("SpellSaveDC") or "")
        spell_atk_bonus = _clean(fields.get("spellAtkBonus0") or fields.get("SpellAttackBonus") or fields.get("SpellAtkBonus") or "")

        # Roleplay / Narrative Background
        backstory = _clean(fields.get("Backstory") or fields.get("BackgroundHistory") or "")
        personality = _clean(fields.get("PersonalityTraits ") or fields.get("PersonalityTraits") or fields.get("Personality") or "")
        ideals = _clean(fields.get("Ideals") or "")
        bonds = _clean(fields.get("Bonds") or "")
        flaws = _clean(fields.get("Flaws") or "")
        appearance = _clean(fields.get("Appearance") or "")
        allies = _clean(fields.get("AlliesOrganizations") or fields.get("Allies") or "")

        custom_meters = {
            "hit_dice": {"current": char_level, "max": char_level, "label": f"Hit Dice ({hit_dice})"},
            "speed": {"current": 30, "max": 30, "label": f"Speed ({clean_speed})"}
        }

        # Saving Throws
        saving_throws = {
            "Strength": _clean(fields.get("ST Strength") or f"{might_val - 10:+d}"),
            "Dexterity": _clean(fields.get("ST Dexterity") or f"{agility_val - 10:+d}"),
            "Constitution": _clean(fields.get("ST Constitution") or f"{vigor_val - 10:+d}"),
            "Intelligence": _clean(fields.get("ST Intelligence") or f"{intellect_val - 10:+d}"),
            "Wisdom": _clean(fields.get("ST Wisdom") or f"{wis_val - 10:+d}"),
            "Charisma": _clean(fields.get("ST Charisma") or f"{cha_val - 10:+d}")
        }

        # Currency
        currency = {
            "cp": _int_val(fields.get("CP"), 0),
            "sp": _int_val(fields.get("SP"), 0),
            "ep": _int_val(fields.get("EP"), 0),
            "gp": _int_val(fields.get("GP"), 0),
            "pp": _int_val(fields.get("PP"), 0)
        }

        attributes_dict = {
            "Strength": might_val,
            "Dexterity": agility_val,
            "Constitution": vigor_val,
            "Intelligence": intellect_val,
            "Wisdom": wis_val,
            "Charisma": cha_val,
            "Might": might_val,
            "Agility": agility_val,
            "Vigor": vigor_val,
            "Intellect": intellect_val,
            "Presence": presence_val
        }

        return {
            "name": char_name,
            "player_name": player_name,
            "archetype": archetype,
            "species": race,
            "background": bg,
            "ruleset": "dnd_5e",
            "level": char_level,
            "experience_points": xp_points,
            "max_health": max_hp,
            "current_health": cur_hp,
            "temp_hp": _int_val(fields.get("TempHP"), 0),
            "guard": guard_val,
            "armor_class": guard_val,
            "speed": clean_speed,
            "hit_dice": hit_dice,
            "currency": currency,
            "saving_throws": saving_throws,
            "attributes": attributes_dict,
            "Might": might_val,
            "Agility": agility_val,
            "Vigor": vigor_val,
            "Intellect": intellect_val,
            "Presence": presence_val,
            "custom_meters": custom_meters,
            "conditions": [],
            "weapons": weapons,
            "inventory": inventory,
            "proficiencies": prof_lang,
            "proficiencies_armor": prof_armor,
            "proficiencies_weapons": prof_weapons,
            "proficiencies_tools": prof_tools,
            "proficiencies_languages": prof_languages,
            "prof_bonus": prof_bonus,
            "passives": passives,
            "senses": senses_str,
            "defenses": defenses_str,
            "resistances": defenses_str,
            "initiative": init_str,
            "features_and_traits": features_list,
            "skills": skills_dict,
            "spells": spells_list,
            "spellcasting_ability": spell_ability,
            "spell_save_dc": spell_save_dc,
            "spell_attack_bonus": spell_atk_bonus,
            "backstory": backstory,
            "personality_traits": personality,
            "ideals": ideals,
            "bonds": bonds,
            "flaws": flaws,
            "appearance": appearance,
            "allies": allies
        }

    def _parse_character_with_gemini(self, filename: str, raw_bytes: bytes) -> Optional[Dict[str, Any]]:
        """
        Uses Gemini Multimodal OCR (gemini-3.8-flash) to extract structured character
        attributes from scanned character sheet PDFs, flat non-interactive PDFs, or images.
        """
        client = self._get_genai_client()
        if not client or not raw_bytes:
            return None

        ext = os.path.splitext(filename or "")[1].lower()
        mime_map = {
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp"
        }
        mime = mime_map.get(ext)
        if not mime:
            return None

        prompt = """You are an expert tabletop roleplaying game (TTRPG) character sheet reader and OCR parser.
Analyze this document (which is a tabletop RPG character sheet).
1. Accurately identify which game system/ruleset the sheet belongs to:
   - 'pathfinder_2e' (Pathfinder 1E, Pathfinder 2E, Pathfinder Remaster, or Pathfinder Society: Paizo logo, Ancestry or Race, Class DC, Fortitude/Reflex/Will saves, CMB/CMD, Strikes, Bulk, Hero Points, Perception as Initiative)
   - 'cyberpunk' (e.g. Cyberpunk RED, Cyberpunk 2020: stats INT, REF, DEX, TECH, COOL, WILL, LUCK, MOVE, BODY, EMP, Humanity, Cyberware, Eurodollars/eb, Roles like Rockerboy, Solo, Netrunner)
   - 'dnd_5e' (D&D 5th Edition: STR, DEX, CON, INT, WIS, CHA, Armor Class, Spellcasting)
   - 'call_of_cthulhu' (Call of Cthulhu: Sanity, Luck, Magic Points, Investigator, percentile skills)
   - 'agnostic' (generic / custom homebrew)
   - 'unknown' (if genuinely indistinguishable)

Extract the character information into valid JSON with this exact schema:
{
  "is_character_sheet": true,
  "ruleset": "<one of: 'pathfinder_2e', 'cyberpunk', 'dnd_5e', 'call_of_cthulhu', 'agnostic', or 'unknown'>",
  "system_name": "<exact detected ruleset or game system name, e.g. 'Pathfinder 2E', 'Pathfinder 1E', 'Cyberpunk RED', 'D&D 5E', 'Call of Cthulhu 7E', or 'Unknown'>",
  "name": "<character name or handle>",
  "player_name": "<player name or 'Player'>",
  "archetype": "<class/role, level/rank, race/ancestry/background, e.g. 'Barbarian 1 (Human, Hunter)' or 'Elf Rogue 1'>",
  "level": <integer level or role rank, e.g. 1>,
  "experience_points": <integer XP or 0>,
  "max_health": <integer maximum HP / Hits>,
  "current_health": <integer current HP / Hits>,
  "guard": <integer Armor Class or Armor SP>,
  "attributes": {
     "INT": <int if cyberpunk>, "REF": <int if cyberpunk>, "DEX": <int>, "TECH": <int if cyberpunk>,
     "COOL": <int if cyberpunk>, "WILL": <int if cyberpunk>, "LUCK": <int if cyberpunk>, "MOVE": <int if cyberpunk>,
     "BODY": <int if cyberpunk>, "EMP": <int if cyberpunk>,
     "Strength": <int if dnd or pathfinder>, "Dexterity": <int if dnd or pathfinder>, "Constitution": <int if dnd or pathfinder>,
     "Intelligence": <int if dnd or pathfinder>, "Wisdom": <int if dnd or pathfinder>, "Charisma": <int if dnd or pathfinder>,
     "STR": <int if coc>, "CON": <int if coc>, "SIZ": <int if coc>, "APP": <int if coc>, "POW": <int if coc>, "EDU": <int if coc>,
     "Might": <int 1-30>, "Agility": <int 1-30>, "Vigor": <int 1-30>, "Intellect": <int 1-30>, "Presence": <int 1-30>
  },
  "custom_meters": {
     "hit_dice": {"current": 1, "max": 1, "label": "Hit Dice (e.g. 1d10)"},
     "speed": {"current": 30, "max": 30, "label": "Speed / MOVE"}
  },
  "currency": {"cp": 0, "sp": 0, "ep": 0, "gp": 0, "pp": 0, "eb": 0},
  "weapons": [
     {"name": "<weapon name>", "atk_bonus": "<bonus>", "damage": "<dmg>", "notes": "<notes>"}
  ],
  "inventory": ["<list of gear, clothing, cyberware, ammo>"],
  "cyberware": ["<list of cyberware items if cyberpunk>"],
  "features_and_traits": ["<class/role features, feats, abilities, ancestry traits>"],
  "skills": {"<SkillName>": "<modifier or total>"},
  "spellcasting_ability": "<e.g. 'CHA', 'INT', 'WIS' or ''>",
  "spell_save_dc": "<e.g. '13' or ''>",
  "spell_attack_bonus": "<e.g. '+5' or ''>",
  "spells": [
     {"name": "<spell name, e.g. 'Burning Hands', 'Light'>", "level": "<e.g. 'Cantrip', '1st Level', '2nd Level'>", "atk_bonus": "<e.g. '+5' or 'DEX 13' or ''>", "damage": "<e.g. '3d6 Fire', '1d8 Lightning', or ''>", "range": "<e.g. 'Touch', '120 ft.', 'Self/15 ft. Cone'>", "notes": "<e.g. 'V,S | Instantaneous'>"}
  ]
}
IMPORTANT: DO NOT include table headers (such as '=== CANTRIPS ===', '=== 1st LEVEL ===', '4 Slots OOOO', '(At Will)'), column headers ('PAGE REF', 'SAVE/ATK', 'TIME', 'RANGE', 'COMP', 'DURATION', 'PREP', 'SPELLS'), or citations (such as 'PHB-2024 292') as spells! Extract ONLY real spells into the 'spells' list.
If this document is a general ruleset, adventure module, or lore book and NOT a character sheet, return:
{"is_character_sheet": false}
Respond ONLY with valid JSON."""

        try:
            from google.genai import types
            from core.parsers.spell_registry import sanitize_parsed_spells
            candidate_models = ["gemini-3.8-flash", "gemini-2.5-flash"]
            for model_name in candidate_models:
                try:
                    resp = client.models.generate_content(
                        model=model_name,
                        contents=[
                            types.Part.from_bytes(data=raw_bytes, mime_type=mime),
                            prompt
                        ],
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=0.1
                        )
                    )
                    text = getattr(resp, "text", "") or ""
                    if text:
                        data = json.loads(text)
                        if isinstance(data, dict) and data.get("is_character_sheet") and data.get("name"):
                            # Sanitize spells if present
                            if "spells" in data and isinstance(data["spells"], list):
                                data["spells"] = sanitize_parsed_spells(data["spells"])
                            # Normalize ruleset detection
                            sys_name_low = str(data.get("system_name") or "").lower()
                            r = (data.get("ruleset") or "").strip().lower()

                            # 1. Direct hit via system_name or ruleset
                            if any(w in sys_name_low for w in ("pathfinder", "paizo", "pf1e", "pf2e")) or any(w in r for w in ("pathfinder", "paizo", "pf1e", "pf2e")):
                                r = "pathfinder_2e"
                            elif any(w in sys_name_low for w in ("cyberpunk", "edgerunner")) or any(w in r for w in ("cyber", "cyberpunk")):
                                r = "cyberpunk"
                            elif any(w in sys_name_low for w in ("cthulhu", "coc")) or any(w in r for w in ("cthulhu", "coc")):
                                r = "call_of_cthulhu"
                            elif any(w in sys_name_low for w in ("d&d", "dnd", "5e")) or any(w in r for w in ("dnd", "5e")):
                                r = "dnd_5e"

                            if not r or r in ("unknown", "agnostic"):
                                arch_low = (data.get("archetype") or "").lower()
                                feats_low = " ".join(data.get("features_and_traits") or []).lower()
                                attrs = data.get("attributes") or {}

                                if any(k in attrs for k in ("CMB", "CMD")) or any(w in feats_low for w in ("pathfinder", "paizo", "class dc", "hero points", "ancestry feat", "skill feat", "trapfinding")) or "pathfinder" in arch_low or "ancestry" in arch_low or "paizo" in feats_low:
                                    r = "pathfinder_2e"
                                elif any(k in attrs for k in ("REF", "TECH", "COOL", "EMP", "BODY")) or any(w in arch_low for w in ("rockerboy", "solo", "netrunner", "tech", "medtech", "media", "exec", "lawman", "fixer", "nomad")) or "cyber" in feats_low:
                                    r = "cyberpunk"
                                elif "sanity" in feats_low or "investigator" in arch_low or any(k in attrs for k in ("SAN", "POW", "EDU")):
                                    r = "call_of_cthulhu"
                                elif any(w in arch_low for w in ("fighter", "wizard", "rogue", "cleric", "paladin", "barbarian")):
                                    r = "dnd_5e"
                                else:
                                    r = "unknown"

                            data["ruleset"] = r
                            if r == "cyberpunk":
                                data["native_system"] = "cyberpunk_red"
                                data["native_genre"] = "cybernetic_action"
                                if "cyberware" in data and data["cyberware"]:
                                    data["features_and_traits"] = list(set((data.get("features_and_traits") or []) + data["cyberware"]))
                            elif r == "call_of_cthulhu":
                                data["native_system"] = "call_of_cthulhu_7e"
                                data["native_genre"] = "modern_horror"
                            elif r in ("dnd_5e", "pathfinder_2e", "pathfinder"):
                                data["ruleset"] = "pathfinder_2e" if "pathfinder" in r else r
                                data["native_system"] = data["ruleset"]
                                data["native_genre"] = "heroic_fantasy"
                            else:
                                data["native_system"] = "custom"
                                data["native_genre"] = "custom"
                            logger.info(f"[TTRPG] Successfully parsed character '{data.get('name')}' (Ruleset: {r}) via Gemini OCR ({model_name}).")
                            return data
                except Exception as m_err:
                    logger.debug(f"[TTRPG] Gemini OCR model '{model_name}' attempt failed: {m_err}")
        except Exception as e:
            logger.warning(f"[TTRPG] Gemini Multimodal OCR failed for '{filename}': {e}")
        return None

    def _parse_character_from_document(
        self,
        filename: str,
        extracted_text: str,
        raw_bytes: Optional[bytes] = None,
        ruleset_override: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Extracts character data from documents using:
        1. PDF AcroForm Interactive Widgets (e.g. D&D Beyond exports)
        2. Structured JSON character manifests
        3. Gemini Multimodal OCR (scanned PDF / image sheets)
        4. Structured text regex extraction
        """
        results: List[Dict[str, Any]] = []
        safe_name = os.path.basename(filename or "")
        lower_name = safe_name.lower()

        if raw_bytes is None and isinstance(extracted_text, (bytes, bytearray)):
            raw_bytes = bytes(extracted_text)
            extracted_text = ""

        # Guard: If filename indicates ruleset, adventure module, or handbook, do not parse as a player character
        if any(k in lower_name for k in ("rule", "basic", "handbook", "guide", "adventure", "module", "campaign", "monster", "bestiary", "srd")):
            return []

        def _apply_override(c: Dict[str, Any]) -> Dict[str, Any]:
            if not c:
                return c
            if ruleset_override:
                c["ruleset"] = ruleset_override
                if "cyber" in ruleset_override.lower():
                    c["native_system"] = "cyberpunk_red"
                    c["native_genre"] = "cybernetic_action"
                elif "cthulhu" in ruleset_override.lower():
                    c["native_system"] = "call_of_cthulhu_7e"
                    c["native_genre"] = "modern_horror"
                elif "pathfinder" in ruleset_override.lower():
                    c["native_system"] = "pathfinder_2e"
                    c["native_genre"] = "heroic_fantasy"
                elif "dnd" in ruleset_override.lower():
                    c["native_system"] = "dnd_5e"
                    c["native_genre"] = "heroic_fantasy"
            return c

        # 1. Try PDF Form Fields (AcroForm widgets)
        if safe_name.lower().endswith(".pdf") and raw_bytes:
            form_fields = ProjectService.extract_pdf_form_fields(raw_bytes)
            char_data = self._parse_character_from_form_fields(form_fields, safe_name)
            if char_data:
                results.append(_apply_override(char_data))
                return results

        # 2. Try structured JSON
        if safe_name.lower().endswith(".json") or (isinstance(extracted_text, str) and extracted_text.lstrip().startswith("{")):
            try:
                raw_str = raw_bytes.decode("utf-8", errors="replace") if raw_bytes else extracted_text
                parsed_json = json.loads(raw_str)
                if isinstance(parsed_json, dict):
                    if "characters" in parsed_json and isinstance(parsed_json["characters"], dict):
                        for _, ch_obj in parsed_json["characters"].items():
                            if isinstance(ch_obj, dict) and ch_obj.get("name"):
                                results.append(_apply_override(ch_obj))
                        if results:
                            return results
                    elif parsed_json.get("name"):
                        results.append(_apply_override(parsed_json))
                        return results
            except Exception as e:
                logger.debug(f"[TTRPG] JSON character parse fallback for '{safe_name}': {e}")

        # 3. Try Gemini Multimodal OCR (for scanned PDFs or image character sheets)
        if raw_bytes and safe_name.lower().endswith((".pdf", ".png", ".jpg", ".jpeg", ".webp")):
            is_large = len(raw_bytes) > 8 * 1024 * 1024
            if not is_large:
                gemini_data = self._parse_character_with_gemini(safe_name, raw_bytes)
                if gemini_data:
                    results.append(_apply_override(gemini_data))
                    return results

        # 4. Fallback: Parse structured text regex (Markdown, DOCX, TXT)
        text = (extracted_text or "").strip()
        if not text:
            return results

        # Extract Character Name
        name_match = re.search(
            r'(?:character\s*name|char\s*name|name)\s*[:\-]\s*([A-Za-z0-9_\-\'\s]{2,40})',
            text,
            re.IGNORECASE
        )
        char_name = name_match.group(1).splitlines()[0].strip() if name_match else ""
        if not char_name:
            # Try top Markdown heading (# Name)
            h_match = re.search(r'^#\s+([A-Za-z0-9_\-\'\s]{2,40})$', text, re.MULTILINE)
            if h_match:
                cand = h_match.group(1).strip()
                if not any(w in cand.lower() for w in ("character sheet", "ruleset", "campaign", "handbook")):
                    char_name = cand
        if not char_name:
            # Only derive name from filename if filename explicitly indicates a character sheet
            if any(k in lower_name for k in ("sheet", "char", "pc", "hero", "player")):
                stem = os.path.splitext(safe_name)[0]
                stem = re.sub(r'(?i)[_\-\s]*(character|char|sheet|pc|d&d|dnd|5e|statblock|stats)[_\-\s]*', ' ', stem).strip()
                char_name = stem.replace("_", " ").strip().title() if stem else "Adventurer"
            else:
                return results

        # Extract Player Name
        player_match = re.search(r'(?:player\s*name|player)\s*[:\-]\s*([A-Za-z0-9_\-\'\s]{2,30})', text, re.IGNORECASE)
        player_name = player_match.group(1).splitlines()[0].strip() if player_match else "Player"

        # Extract Class / Archetype / Level
        arch_match = re.search(
            r'(?:class\s*(?:&|and|\/)?\s*level|archetype|class|role|calling)\s*[:\-]\s*([A-Za-z0-9_\-\'\/\s]{2,45})',
            text,
            re.IGNORECASE
        )
        archetype = arch_match.group(1).splitlines()[0].strip() if arch_match else "Adventurer"

        # Extract HP / Max Health
        cur_hp, max_hp = 25, 25
        hp_slash = re.search(
            r'(?:hit\s*points|max\s*hp|health|hp)\s*[:\-]?\s*(\d{1,4})\s*\/\s*(\d{1,4})',
            text,
            re.IGNORECASE
        )
        if hp_slash:
            cur_hp = int(hp_slash.group(1))
            max_hp = int(hp_slash.group(2))
        else:
            hp_single = re.search(
                r'(?:max\s*hit\s*points|hit\s*point\s*maximum|hit\s*points|max\s*health|max\s*hp|health|hp)\s*[:\-]?\s*(\d{1,4})',
                text,
                re.IGNORECASE
            )
            if hp_single:
                max_hp = int(hp_single.group(1))
                cur_hp = max_hp

        # Extract Armor Class / Guard / Defense
        guard_val = 14
        ac_match = re.search(
            r'(?:armor\s*class|armor\s*defense|defense|guard|\bac\b)\s*[:\-]?\s*(\d{1,3})',
            text,
            re.IGNORECASE
        )
        if ac_match:
            guard_val = int(ac_match.group(1))

        # Extract Attributes
        def _extract_attr(patterns: List[str], default_val: int = 10) -> int:
            for pat in patterns:
                m = re.search(rf'\b{pat}\b\s*[:\-]?\s*(\d{{1,2}})', text, re.IGNORECASE)
                if m:
                    val = int(m.group(1))
                    if 1 <= val <= 30:
                        return val
            return default_val

        might_val = _extract_attr(["might", "strength", "str"], 12)
        agility_val = _extract_attr(["agility", "dexterity", "dex"], 12)
        vigor_val = _extract_attr(["vigor", "constitution", "con"], 12)
        intellect_val = _extract_attr(["intellect", "intelligence", "int"], 12)
        presence_val = _extract_attr(["presence", "charisma", "cha", "wisdom", "wis"], 12)

        lower_t = text.lower()
        det_r = ruleset_override or "unknown"
        if det_r == "unknown":
            if any(k in lower_t for k in ("cyberpunk", "rockerboy", "netrunner", "solo", "cyberware", "humanity", "eurodollars")):
                det_r = "cyberpunk"
            elif any(k in lower_t for k in ("call of cthulhu", "sanity", "investigator", "cthulhu")):
                det_r = "call_of_cthulhu"
            elif any(k in lower_t for k in ("pathfinder", "class dc", "ancestry")):
                det_r = "pathfinder_2e"
            elif any(k in lower_t for k in ("dungeon", "d&d", "5e", "spellcasting", "armor class")):
                det_r = "dnd_5e"

        results.append(_apply_override({
            "name": char_name,
            "player_name": player_name,
            "archetype": archetype,
            "ruleset": det_r,
            "max_health": max_hp,
            "current_health": cur_hp,
            "guard": guard_val,
            "Might": might_val,
            "Agility": agility_val,
            "Vigor": vigor_val,
            "Intellect": intellect_val,
            "Presence": presence_val
        }))
        return results

    def _parse_character_from_document_text(
        self,
        filename: str,
        extracted_text: str,
        raw_bytes: Optional[bytes] = None
    ) -> List[Dict[str, Any]]:
        """Backwards compatibility alias for _parse_character_from_document."""
        return self._parse_character_from_document(filename, extracted_text, raw_bytes)

    def upsert_character_from_sheet(
        self,
        ch_payload: Dict[str, Any],
        filename: str = "",
        target_state: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Updates an existing character if one matches the parsed character name,
        fuzzy name (e.g. 'Shiela' vs 'Sheila'), or filename stem placeholder;
        otherwise adds a new character into the active campaign roster.
        """
        st = target_state if target_state is not None else self.active_state
        if not st:
            return {"status": "error", "message": "No active campaign loaded."}

        chars = st.setdefault("characters", {})
        c_name = (ch_payload.get("name") or "").strip()
        if not c_name:
            return {"status": "error", "message": "Character name is required."}

        target_id: Optional[str] = None

        # 1. Exact match (case-insensitive)
        for cid, cdata in chars.items():
            if cdata.get("name", "").strip().lower() == c_name.lower():
                target_id = cid
                break

        # 2. Fuzzy match on name (e.g. 'Shiela' vs 'Sheila')
        if not target_id:
            for cid, cdata in chars.items():
                ex_name = cdata.get("name", "").strip().lower()
                if ex_name and difflib.SequenceMatcher(None, ex_name, c_name.lower()).ratio() >= 0.8:
                    target_id = cid
                    break

        # 3. Match against filename stem placeholder (e.g. 'retholtz_173064916')
        if not target_id and filename:
            stem = os.path.splitext(os.path.basename(filename))[0].lower()
            for cid, cdata in chars.items():
                ex_name = cdata.get("name", "").strip().lower()
                if stem and (stem in ex_name or ex_name in stem):
                    target_id = cid
                    break

        # 4. If an existing character was found, update their stats
        if target_id:
            existing = chars[target_id]
            existing["name"] = c_name
            if ch_payload.get("player_name"):
                existing["player_name"] = ch_payload["player_name"]
            if ch_payload.get("archetype"):
                existing["archetype"] = ch_payload["archetype"]
            existing["is_player"] = bool(ch_payload.get("is_player", existing.get("is_player", True)))

            # Protect evolved character data: do not downgrade Level, XP, or Max HP
            cur_level = self._safe_int(existing.get("level"), 1)
            sheet_level = self._safe_int(ch_payload.get("level"), 1)
            existing["level"] = max(cur_level, sheet_level)

            cur_xp = self._safe_int(existing.get("experience_points"), 0)
            sheet_xp = self._safe_int(ch_payload.get("experience_points"), 0)
            existing["experience_points"] = max(cur_xp, sheet_xp)

            cur_max_hp = self._safe_int(existing.get("vitals", {}).get("health", {}).get("max"), 25)
            sheet_max_hp = self._safe_int(ch_payload.get("max_health"), 0)
            max_hp = max(cur_max_hp, sheet_max_hp) if sheet_max_hp > 0 else cur_max_hp

            # Preserve current health modifications unless sheet explicitly specifies otherwise
            cur_hp = self._safe_int(existing.get("vitals", {}).get("health", {}).get("current"), max_hp)
            if "current_health" in ch_payload and ch_payload["current_health"] is not None:
                sheet_cur_hp = self._safe_int(ch_payload["current_health"], cur_hp)
                cur_hp = max(0, min(max_hp, sheet_cur_hp))
            cur_hp = max(0, min(max_hp, cur_hp))

            guard_val = self._safe_int(
                ch_payload.get("guard") or ch_payload.get("armor_class"),
                self._safe_int(existing.get("vitals", {}).get("guard", {}).get("max") or existing.get("armor_class"), 14)
            )

            existing.setdefault("vitals", {})
            existing["vitals"]["health"] = {"current": cur_hp, "max": max_hp, "label": "Health"}
            existing["vitals"]["guard"] = {"current": guard_val, "max": guard_val, "label": "Armor Defense"}
            existing["armor_class"] = guard_val

            existing.setdefault("attributes", {})
            if "attributes" in ch_payload and isinstance(ch_payload["attributes"], dict):
                for k, v in ch_payload["attributes"].items():
                    if v is not None:
                        existing["attributes"][k] = self._safe_int(v, v)
            for attr in (
                "Might", "Agility", "Vigor", "Intellect", "Presence",
                "Strength", "Dexterity", "Constitution", "Intelligence", "Wisdom", "Charisma",
                "INT", "REF", "DEX", "TECH", "COOL", "WILL", "LUCK", "MOVE", "BODY", "EMP",
                "STR", "CON", "SIZ", "APP", "POW", "EDU", "SAN"
            ):
                if attr in ch_payload and ch_payload[attr] is not None:
                    existing["attributes"][attr] = self._safe_int(ch_payload[attr], existing["attributes"].get(attr, 10))

            if ch_payload.get("ruleset"):
                existing["ruleset"] = ch_payload["ruleset"]
            if ch_payload.get("species"):
                existing["species"] = ch_payload["species"]
            if ch_payload.get("background"):
                existing["background"] = ch_payload["background"]
            if ch_payload.get("saving_throws"):
                existing["saving_throws"] = ch_payload["saving_throws"]
            if ch_payload.get("currency"):
                existing["currency"] = ch_payload["currency"]
            if ch_payload.get("speed"):
                existing["speed"] = ch_payload["speed"]
            if ch_payload.get("hit_dice"):
                existing["hit_dice"] = ch_payload["hit_dice"]
            if "temp_hp" in ch_payload and ch_payload["temp_hp"] is not None:
                existing["temp_hp"] = self._safe_int(ch_payload["temp_hp"], 0)

            if ch_payload.get("custom_meters"):
                existing.setdefault("custom_meters", {}).update(ch_payload["custom_meters"])

            if ch_payload.get("weapons"):
                existing["weapons"] = ch_payload["weapons"]
            if ch_payload.get("inventory"):
                existing["inventory"] = ch_payload["inventory"]
            if ch_payload.get("proficiencies"):
                existing["proficiencies"] = ch_payload["proficiencies"]
            if ch_payload.get("prof_bonus"):
                existing["prof_bonus"] = ch_payload["prof_bonus"]
            if ch_payload.get("passives"):
                existing["passives"] = ch_payload["passives"]
            if ch_payload.get("initiative"):
                existing["initiative"] = ch_payload["initiative"]
            if ch_payload.get("features_and_traits"):
                existing["features_and_traits"] = ch_payload["features_and_traits"]
            if ch_payload.get("skills"):
                existing["skills"] = ch_payload["skills"]
            if ch_payload.get("spells"):
                existing["spells"] = ch_payload["spells"]

            for rich_k in (
                "senses", "defenses", "resistances", "initiative", "proficiencies_armor", "proficiencies_weapons", "proficiencies_tools",
                "proficiencies_languages", "spellcasting_ability", "spell_save_dc", "spell_attack_bonus",
                "backstory", "personality_traits", "ideals", "bonds", "flaws", "appearance", "allies"
            ):
                if ch_payload.get(rich_k):
                    existing[rich_k] = ch_payload[rich_k]

            # Update initiative ladder name if entity is already on the ladder
            for ent in st.get("initiative_order", []):
                if ent.get("entity_id") == target_id:
                    ent["name"] = c_name

            active_cid = st.get("campaign_id")
            if active_cid and active_cid in self.manifest.get("campaigns", {}):
                self.manifest["campaigns"][active_cid]["party_count"] = len(chars)
                self.save_manifest()
            if target_state is None or st == self.active_state:
                self.save_active_campaign_state()
            return {"status": "success", "character": existing, "state": st, "action": "updated"}

        # 5. Otherwise, add as a new character
        ch_payload["name"] = c_name
        res = self.add_character(ch_payload, target_state=st)
        res["action"] = "created"
        return res

    def import_character_to_active_campaign(self, vault_char_data: Dict[str, Any], overwrite_existing: bool = False) -> Dict[str, Any]:
        """
        ONE-WAY INGESTION: Clones a character sheet from the Vault directly into the
        active campaign's persistent state.json. Severing the template link ensures
        live in-game updates (HP loss, spent spell slots) NEVER reset on UI refresh.
        """
        if not self.active_state:
            return {"status": "error", "message": "No active campaign session loaded."}

        char_id = vault_char_data.get("id")
        char_name = vault_char_data.get("universal_core", {}).get("name") or vault_char_data.get("name")

        # 1. Idempotency Guard: Prevent accidental template reload overwriting live combat state
        existing_chars = self.active_state.setdefault("characters", {})
        matched_id = None
        if char_id in existing_chars:
            matched_id = char_id
        else:
            for cid, cdata in existing_chars.items():
                if cdata.get("name", "").strip().lower() == str(char_name).strip().lower():
                    matched_id = cid
                    break

        if matched_id and not overwrite_existing:
            logger.warning(f"[TTRPG] Character '{char_name}' ({matched_id}) already active in session. Preserving live state.")
            return {
                "status": "already_loaded",
                "character_id": matched_id,
                "message": f"Character '{char_name}' is already active in this session. Live state preserved."
            }

        if matched_id and matched_id != char_id:
            # Clean up old ID key to avoid duplicate characters with the same name
            existing_chars.pop(matched_id, None)

        # 2. Extract Base Vitals from Native System Data or Universal Core
        native = vault_char_data.get("native_system_data", {})
        core = vault_char_data.get("universal_core", {})

        # Default Vitals construction
        hp_max = 20
        if "hit_points" in native and isinstance(native["hit_points"], dict):
            hp_max = native["hit_points"].get("max", 20)
        elif "vitals" in vault_char_data and isinstance(vault_char_data["vitals"], dict) and "health" in vault_char_data["vitals"]:
            hp_max = vault_char_data["vitals"]["health"].get("max", 20)
        elif "max_health" in vault_char_data or "health" in vault_char_data:
            hp_max = int(vault_char_data.get("max_health") or vault_char_data.get("health") or 20)

        guard_val = (
            vault_char_data.get("armor_class")
            or native.get("armor_class")
            or native.get("armor", {}).get("body_sp")
            or (vault_char_data.get("vitals", {}).get("guard", {}).get("current") if isinstance(vault_char_data.get("vitals"), dict) else None)
            or 14
        )

        # Extract features and traits from all possible representations
        raw_feats = (
            vault_char_data.get("features_and_traits")
            or vault_char_data.get("traits_and_abilities")
            or vault_char_data.get("features")
            or native.get("features_and_traits")
            or native.get("features_traits")
            or [t["name"] if isinstance(t, dict) else str(t) for t in core.get("tagged_traits", [])]
            or []
        )
        feats_list = copy.deepcopy(raw_feats)

        # Extract inventory
        raw_inv = vault_char_data.get("inventory") or native.get("inventory")
        if not raw_inv:
            raw_inv = [item["name"] if isinstance(item, dict) else str(item) for item in (vault_char_data.get("active_inventory") or core.get("tagged_inventory", []))]

        # Extract currency
        currency = copy.deepcopy(vault_char_data.get("currency") or native.get("currency") or {"cp": 0, "sp": 0, "ep": 0, "gp": 0, "pp": 0})

        # Extract weapons and spells
        weapons = copy.deepcopy(vault_char_data.get("weapons") or native.get("weapons") or [])
        spells = copy.deepcopy(vault_char_data.get("spells") or native.get("spells") or [])

        hp_cur = (
            vault_char_data.get("vitals", {}).get("health", {}).get("current")
            if isinstance(vault_char_data.get("vitals"), dict) and "health" in vault_char_data["vitals"]
            else hp_max
        )

        r_str = str(vault_char_data.get("ruleset") or native.get("system_name") or "").lower()
        is_coc_char = "cthulhu" in r_str or "coc" in r_str
        guard_label = (
            vault_char_data.get("vitals", {}).get("guard", {}).get("label")
            if isinstance(vault_char_data.get("vitals"), dict) and "guard" in vault_char_data["vitals"]
            else ("Dodge Defense" if is_coc_char else "Armor Defense")
        )
        guard_cur = (
            vault_char_data.get("vitals", {}).get("guard", {}).get("current")
            if isinstance(vault_char_data.get("vitals"), dict) and "guard" in vault_char_data["vitals"]
            else guard_val
        )

        # 3. Create Isolated Live State Object
        live_instance = {
            "id": char_id,
            "vault_source_id": char_id,
            "name": char_name,
            "player_name": vault_char_data.get("player_name") or core.get("player_name", "Player"),
            "archetype": vault_char_data.get("archetype") or core.get("archetype", "Adventurer"),
            "level": int(vault_char_data.get("level") or core.get("level", 1)),
            "experience_points": int(vault_char_data.get("experience_points") or native.get("experience_points", 0)),
            "ruleset": vault_char_data.get("ruleset") or native.get("system_name") or "dnd_5e",
            "species": vault_char_data.get("species") or native.get("species", ""),
            "background": vault_char_data.get("background") or native.get("background", ""),
            "is_player": True,
            "vitals": {
                "health": {"current": hp_cur, "max": hp_max, "label": "Health"},
                "guard": {"current": guard_cur, "max": guard_val, "label": guard_label}
            },
            "custom_meters": copy.deepcopy(vault_char_data.get("custom_meters") or native.get("custom_meters", {})),
            "attributes": copy.deepcopy(vault_char_data.get("target_system_attributes") or vault_char_data.get("attributes") or core.get("normalized_attributes", {})),
            "conditions": copy.deepcopy(vault_char_data.get("conditions", [])),
            "currency": currency,
            "weapons": weapons,
            "inventory": copy.deepcopy(raw_inv),
            "live_inventory": copy.deepcopy(vault_char_data.get("active_inventory") or core.get("tagged_inventory", [])),
            "features_and_traits": feats_list,
            "traits_and_abilities": feats_list,
            "features_traits": feats_list,
            "spells": spells,
            "spellcasting_ability": vault_char_data.get("spellcasting_ability") or native.get("spellcasting_ability", ""),
            "spell_save_dc": vault_char_data.get("spell_save_dc") or native.get("spell_save_dc", ""),
            "spell_attack_bonus": vault_char_data.get("spell_attack_bonus") or native.get("spell_attack_bonus", ""),
            "passives": copy.deepcopy(vault_char_data.get("passives") or native.get("passives", {})),
            "senses": vault_char_data.get("senses") or native.get("senses", ""),
            "defenses": vault_char_data.get("defenses") or vault_char_data.get("resistances") or native.get("defenses") or native.get("resistances", ""),
            "resistances": vault_char_data.get("resistances") or vault_char_data.get("defenses") or native.get("resistances") or native.get("defenses", ""),
            "speed": vault_char_data.get("speed") or native.get("speed", "30 ft."),
            "hit_dice": vault_char_data.get("hit_dice") or native.get("hit_dice", "1d8"),
            "armor_class": guard_val,
            "saving_throws": copy.deepcopy(vault_char_data.get("saving_throws") or native.get("saving_throws", {})),
            "proficiencies": copy.deepcopy(vault_char_data.get("proficiencies") or native.get("proficiencies", "")),
            "prof_bonus": vault_char_data.get("prof_bonus") or native.get("prof_bonus", "+2"),
            "initiative": vault_char_data.get("initiative") or native.get("initiative", ""),
            "skills": copy.deepcopy(vault_char_data.get("skills") or native.get("skills", {})),
            "ingested_at": datetime.now(timezone.utc).isoformat()
        }

        for k in ("proficiencies_armor", "proficiencies_weapons", "proficiencies_tools", "proficiencies_languages"):
            val = vault_char_data.get(k) or native.get(k)
            if val:
                live_instance[k] = copy.deepcopy(val)

        # 4. Save Directly to Campaign State File
        existing_chars[char_id] = live_instance
        self.save_active_campaign_state()

        logger.info(f"[TTRPG] Successfully performed 'Once and Done' ingestion for '{char_name}'.")
        return {"status": "success", "character_id": char_id, "character": live_instance}

    def get_campaign_roster(self) -> List[Dict[str, Any]]:
        """
        AUTHORITATIVE ROSTER QUERY: All UI refreshes and polling methods MUST call this.
        Reads strictly from active campaign memory, guaranteeing delta persistence.
        """
        if not self.active_state:
            return []
        return list(self.active_state.get("characters", {}).values())

    def refresh_campaign_files_and_characters(self, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Reloads campaign manifest & state from disk, scans the campaign's `files/` directory
        for any newly added files, and parses all character sheet documents to ensure any
        missing or newly uploaded characters appear on the Party Roster.
        """
        self.load_manifest()
        cid = campaign_id or self.manifest.get("active_campaign")
        if not cid or cid not in self.manifest.get("campaigns", {}):
            return {"status": "error", "message": "No active campaign selected."}

        self.load_active_campaign_state()
        if not self.active_state:
            self.active_state = self._generate_default_state(cid)

        files_dir = self.get_campaign_files_dir(cid)
        docs_map = self.active_state.setdefault("documents", {})
        newly_indexed_files: List[str] = []
        imported_characters: List[str] = []

        if files_dir and os.path.exists(files_dir):
            disk_files = [f for f in sorted(os.listdir(files_dir)) if os.path.isfile(os.path.join(files_dir, f))]

            # Remove stale metadata entries if file was deleted from disk
            for tracked_name in list(docs_map.keys()):
                if tracked_name not in disk_files:
                    docs_map.pop(tracked_name, None)

            for fname in disk_files:
                fpath = os.path.join(files_dir, fname)
                lower_f = fname.lower()
                is_known_non_char = any(k in lower_f for k in ("rule", "basic", "handbook", "guide", "adventure", "module", "campaign", "monster", "bestiary", "srd"))

                raw_bytes = None
                try:
                    with open(fpath, "rb") as bf:
                        raw_bytes = bf.read()
                except Exception:
                    pass

                file_sz = len(raw_bytes or b"")
                has_valid_meta = (
                    fname in docs_map
                    and docs_map[fname].get("size_bytes") == file_sz
                    and bool(docs_map[fname].get("preview"))
                    and docs_map[fname].get("token_estimate", 0) > 1
                )
                if has_valid_meta and is_known_non_char:
                    parsed_chars = []
                else:
                    extracted_text = ProjectService.extract_file_text(fpath)
                    parsed_chars = self._parse_character_from_document(fname, extracted_text, raw_bytes)
                    if fname not in docs_map:
                        if parsed_chars:
                            inferred_cat = "character_sheet"
                        elif any(k in lower_f for k in ("campaign", "module", "adventure")):
                            inferred_cat = "campaign"
                        else:
                            inferred_cat = "ruleset"

                        docs_map[fname] = {
                            "filename": fname,
                            "category": inferred_cat,
                            "size_bytes": file_sz,
                            "token_estimate": ProjectService.estimate_tokens(extracted_text),
                            "uploaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "preview": (extracted_text or "")[:300].strip()
                        }
                        newly_indexed_files.append(fname)
                    else:
                        if parsed_chars:
                            docs_map[fname]["category"] = "character_sheet"
                        if not docs_map[fname].get("preview") or docs_map[fname].get("token_estimate", 0) <= 1:
                            docs_map[fname]["token_estimate"] = ProjectService.estimate_tokens(extracted_text)
                            docs_map[fname]["preview"] = (extracted_text or "")[:300].strip()

                if parsed_chars or (fname in docs_map and docs_map[fname].get("category") == "character_sheet"):
                    for ch_payload in (parsed_chars or []):
                        c_name = (ch_payload.get("name") or "").strip()
                        if c_name:
                            res_upsert = self.upsert_character_from_sheet(ch_payload, filename=fname)
                            if res_upsert.get("status") == "success":
                                imported_characters.append(c_name)
                    # One-and-done: remove character sheet from disk and do not track in documents map
                    docs_map.pop(fname, None)
                    if os.path.exists(fpath):
                        try:
                            os.remove(fpath)
                        except Exception:
                            pass
                    continue

        self.save_active_campaign_state()
        return {
            "status": "success",
            "campaign_id": cid,
            "newly_indexed_files": newly_indexed_files,
            "imported_characters": imported_characters,
            "documents": list(docs_map.values()),
            "state": self.active_state
        }

    def list_campaign_documents(self, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        cid = campaign_id or self.manifest.get("active_campaign")
        if not cid:
            return {"status": "error", "message": "No active campaign."}

        if cid == self.manifest.get("active_campaign") and self.active_state:
            docs_map = self.active_state.setdefault("documents", {})
        else:
            state_path = os.path.join(self.data_dir, cid, "state.json")
            if os.path.exists(state_path):
                try:
                    with open(state_path, "r", encoding="utf-8") as f:
                        st = json.load(f)
                    docs_map = st.get("documents", {})
                except Exception:
                    docs_map = {}
            else:
                docs_map = {}

        return {
            "status": "success",
            "campaign_id": cid,
            "documents": list(docs_map.values())
        }

    @staticmethod
    def _extract_smart_scene(content: str, scene_query: str = "Scene 1") -> Optional[str]:
        """
        Intelligently locates the true narrative heading and body for a scene or act,
        differentiating actual scene headers from table-of-contents or inline cross-references.
        """
        escaped = re.escape(scene_query).replace(r"\ ", r"\s+")
        pattern = re.compile(rf"(?:^|\r?\n)\s*(?:(?:Act\s*\d+[\s:\-–—]*)?{escaped}[^\r\n]*)", re.IGNORECASE)
        candidates = []
        for m in pattern.finditer(content):
            start = m.start()
            header_line = m.group().strip()
            snippet = content[m.end():m.end() + 2500]
            score = 0
            if not header_line.endswith("."):
                score += 15
            if not re.match(r"\s*\d+\.", snippet):
                score += 8
            lower = snippet.lower()
            for kw in ("player", "party", "quest", "veteran", "hall", "room", "approaches", "choice", "reward", "investigate", "check"):
                if kw in lower:
                    score += 3
            for neg in ("for more information", "described above", "points of interest", "contents"):
                if neg in lower[:300]:
                    score -= 10
            candidates.append((score, start, header_line))

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            best = candidates[0]
            end_pos = min(len(content), best[1] + 5000)
            return content[best[1]:end_pos].strip()

        # Fallback regex
        m = re.search(escaped, content, re.IGNORECASE)
        if m:
            return content[max(0, m.start() - 200):min(len(content), m.start() + 5000)].strip()
        return None

    @staticmethod
    def _extract_smart_query(content: str, query: str) -> str:
        """
        Extracts the highest-relevance excerpt for a query, prioritizing narrative headings,
        stat blocks, and quest dialogue over superficial index listings.
        """
        q_term = query.strip()
        if re.search(r"\b(?:Scene|Act|Chapter|Episode)\b", q_term, re.IGNORECASE):
            scene_res = TTRPGService._extract_smart_scene(content, q_term)
            if scene_res:
                return scene_res

        escaped = re.escape(q_term).replace(r"\ ", r"\s+")
        matches = list(re.finditer(escaped, content, re.IGNORECASE))
        if not matches:
            words = [w for w in re.split(r"\s+", q_term) if len(w) > 2]
            if words:
                pattern = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)
                matches = list(pattern.finditer(content))
        if not matches:
            return f"[No match found for '{q_term}'. Total document length: {len(content)} chars.]"

        scored = []
        for m in matches:
            pos = m.start()
            start_pos = max(0, pos - 250)
            end_pos = min(len(content), pos + 4500)
            snip = content[start_pos:end_pos]
            score = 0
            line_start = content.rfind("\n", 0, pos) + 1
            line_end = content.find("\n", pos)
            if line_end == -1:
                line_end = len(content)
            line = content[line_start:line_end].strip()
            if len(line) < 80 and not line.endswith("."):
                score += 15
            lower = snip.lower()
            for kw in ("quest", "scene", "act", "player", "party", "reward", "investigate", "check", "armor", "wisdom", "strength", "choice", "veteran", "ac "):
                if kw in lower:
                    score += 2
            for neg in ("for more information", "described above", "points of interest", "contents"):
                if neg in lower[:300]:
                    score -= 8
            scored.append((score, start_pos, end_pos))

        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[0]
        return content[best[1]:best[2]].strip()

    def read_campaign_document(
        self,
        filename: str,
        campaign_id: Optional[str] = None,
        query: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Extracts and returns the text content of an uploaded campaign document,
        or all documents if filename is 'all' or '*'.
        Supports 'query' to search for specific scenes, NPCs, or rules excerpts.
        Also supports reading 'journal' or 'notes' for the persistent session recap.
        """
        cid = campaign_id or self.manifest.get("active_campaign")
        if not cid:
            return {"status": "error", "message": "No active campaign."}

        # Check for journal request
        req_target = (filename or "").strip()
        if req_target.lower() in ("journal", "notes", "recap", "session_notes"):
            notes = self.get_campaign_notes(cid)
            recap_str = notes.get("latest_recap") or "No session recap recorded yet."
            loc_str = notes.get("current_location") or "Unknown"
            quest_str = notes.get("active_quest") or "No active quest."
            clues_str = ", ".join(notes.get("discovered_clues", [])) or "None yet"
            journal_lines = [
                f"[CAMPAIGN PERSISTENT JOURNAL & RECAP — {cid}]",
                f"- Latest Session Recap: {recap_str}",
                f"- Current Location: {loc_str}",
                f"- Active Objective: {quest_str}",
                f"- Discovered Clues: {clues_str}\n",
                "Chronological Session Beats:"
            ]
            for ent in notes.get("journal", []):
                journal_lines.append(f"- [{ent.get('timestamp')}] {ent.get('summary')} (Location: {ent.get('location', 'N/A')})")
            return {
                "status": "success",
                "campaign_id": cid,
                "filename": "CAMPAIGN_JOURNAL",
                "category": "journal",
                "content": "\n".join(journal_lines)
            }

        files_dir = self.get_campaign_files_dir(cid)
        if not files_dir or not os.path.exists(files_dir):
            return {"status": "error", "message": "No campaign documents directory found."}

        docs_map = {}
        if cid == self.manifest.get("active_campaign") and self.active_state:
            docs_map = self.active_state.get("documents", {})

        if not req_target or req_target.lower() in ("all", "*"):
            all_files = sorted(os.listdir(files_dir))
            if not all_files:
                return {"status": "error", "message": "No documents have been uploaded to this campaign yet."}

            combined_parts = []
            # Order: 1. Campaign Modules, 2. Rulesets (summarized), 3. Journal
            module_files = []
            rules_files = []

            for fname in all_files:
                fpath = os.path.join(files_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                cat = docs_map.get(fname, {}).get("category", "").lower()
                if cat in ("campaign", "module") or any(k in fname.lower() for k in ("elfhunt", "module", "adventure", "campaign")):
                    module_files.append(fname)
                elif cat == "character_sheet" or any(k in fname.lower() for k in ("sheet", "char", "pc")):
                    # Skip character sheets - characters are tracked on the Party Roster
                    continue
                else:
                    rules_files.append(fname)

            # 1. Campaign Modules (full narrative grounding)
            for fname in module_files:
                fpath = os.path.join(files_dir, fname)
                text = ProjectService.extract_file_text(fpath)
                if len(text) > 20000:
                    s1_text = TTRPGService._extract_smart_scene(text, "Scene 1: The Beginning") or TTRPGService._extract_smart_scene(text, "Scene 1") or ""
                    module_content = f"{text[:10000]}\n\n=== [SCENE 1: THE BEGINNING & OPENING QUEST] ===\n{s1_text}"
                else:
                    module_content = text
                combined_parts.append(f"=== [CAMPAIGN ADVENTURE MODULE] {fname} ===\n{module_content}")

            # 2. Rulesets (summarized to prevent overwhelming context)
            for fname in rules_files:
                fpath = os.path.join(files_dir, fname)
                text = ProjectService.extract_file_text(fpath)
                truncated_rules = text[:2500] + ("\n...(Rulebook reference text truncated for brevity)..." if len(text) > 2500 else "")
                combined_parts.append(f"=== [RULESET REFERENCE] {fname} ===\n{truncated_rules}")

            # 3. Campaign Journal & Persistent Recap
            notes = self.get_campaign_notes(cid)
            if notes.get("latest_recap"):
                combined_parts.append(f"=== [CAMPAIGN PERSISTENT RECAP] ===\n{notes['latest_recap']}\nLocation: {notes.get('current_location')}\nQuest: {notes.get('active_quest')}")

            return {
                "status": "success",
                "campaign_id": cid,
                "filename": "ALL_CAMPAIGN_DOCUMENTS",
                "document_count": len(combined_parts),
                "content": "\n\n".join(combined_parts)
            }

        safe_name = os.path.basename(req_target)
        target_path = os.path.join(files_dir, safe_name)

        # Case-insensitive or partial filename fallback
        if not os.path.exists(target_path):
            q_lower = req_target.lower()
            matched_file = None
            for existing_name in os.listdir(files_dir):
                if existing_name.lower() == q_lower or q_lower in existing_name.lower():
                    matched_file = existing_name
                    break
                doc_cat = docs_map.get(existing_name, {}).get("category", "").lower()
                if doc_cat and doc_cat == q_lower.replace(" ", "_"):
                    matched_file = existing_name
                    break
            if matched_file:
                safe_name = matched_file
                target_path = os.path.join(files_dir, safe_name)

        if not os.path.exists(target_path):
            chars = (self.active_state or {}).get("characters", {})
            for cid_char, c in chars.items():
                c_name = c.get("name", "")
                if c_name and (c_name.lower() in req_target.lower() or req_target.lower() in c_name.lower()):
                    hp_cur = c.get("vitals", {}).get("health", {}).get("current", 0)
                    hp_max = c.get("vitals", {}).get("health", {}).get("max", 0)
                    return {
                        "status": "success",
                        "campaign_id": cid,
                        "filename": req_target,
                        "category": "character_roster",
                        "content": (
                            f"=== [ACTIVE CHARACTER ROSTER] {c_name} ===\n"
                            f"Character: {c_name} ({c.get('archetype', 'Hero')})\n"
                            f"Level: {c.get('level', 1)} | HP: {hp_cur}/{hp_max} | AC/Guard: {c.get('armor_class', 14)}\n"
                            f"Attributes: {c.get('attributes', {})}\n"
                            f"Weapons: {c.get('weapons', [])}\n"
                            f"Inventory: {c.get('inventory', [])}\n"
                            f"Note: Character sheets are imported and managed directly on the Party Roster."
                        )
                    }
            return {
                "status": "error",
                "message": f"Document '{filename}' not found in campaign '{cid}'.",
                "available_documents": list(docs_map.keys())
            }

        try:
            content = ProjectService.extract_file_text(target_path)
            doc_info = docs_map.get(safe_name, {"filename": safe_name})

            # Check if query is provided to extract a specific scene or search match
            if query and query.strip():
                content_returned = f"[EXCERPT FOR '{query.strip()}' IN {safe_name}]\n{TTRPGService._extract_smart_query(content, query)}"
            elif len(content) > 15000:
                # Large adventure module or rulebook: return structured overview + starting scene + scene index
                scenes = re.findall(r'(?:Act\s+\d+|Scene\s+\d+)[^\r\n]{0,60}', content)
                seen_scenes = []
                for s in scenes:
                    cl = s.strip()
                    if cl and cl not in seen_scenes:
                        seen_scenes.append(cl)
                scene_list_str = "\n".join(f"- {s}" for s in seen_scenes[:25])

                s1_text = TTRPGService._extract_smart_scene(content, "Scene 1: The Beginning")
                if not s1_text:
                    s1_text = TTRPGService._extract_smart_scene(content, "Scene 1")
                if not s1_text:
                    s1_text = content[5000:10000].strip()

                content_returned = (
                    f"=== [CAMPAIGN ADVENTURE OVERVIEW: {safe_name}] ===\n"
                    f"{content[:5000].strip()}\n\n"
                    f"=== [STARTING SCENE: SCENE 1] ===\n"
                    f"{s1_text}\n\n"
                    f"=== [DOCUMENT SCENE INDEX — To inspect any scene, call read_campaign_document(filename='{safe_name}', query='<Scene/NPC Name>')] ===\n"
                    f"{scene_list_str}"
                )
            else:
                content_returned = content

            return {
                "status": "success",
                "campaign_id": cid,
                "filename": safe_name,
                "category": doc_info.get("category", "ruleset"),
                "token_estimate": doc_info.get("token_estimate", ProjectService.estimate_tokens(content)),
                "content": content_returned
            }
        except Exception as e:
            return {"status": "error", "message": f"Error reading document '{safe_name}': {e}"}

    def delete_campaign_document(self, filename: str, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        cid = campaign_id or self.manifest.get("active_campaign")
        if not cid:
            return {"status": "error", "message": "No active campaign."}

        safe_name = os.path.basename((filename or "").strip())
        files_dir = self.get_campaign_files_dir(cid)
        target_path = os.path.join(files_dir, safe_name) if files_dir else None

        removed_disk = False
        if target_path and os.path.exists(target_path):
            try:
                os.remove(target_path)
                removed_disk = True
            except Exception as e:
                logger.error(f"[TTRPG] Failed removing file '{target_path}': {e}")

        if cid == self.manifest.get("active_campaign") and self.active_state:
            docs_map = self.active_state.setdefault("documents", {})
            removed_meta = docs_map.pop(safe_name, None)
            self.save_active_campaign_state()
        else:
            removed_meta = None

        if not removed_disk and not removed_meta:
            return {"status": "error", "message": f"Document '{safe_name}' not found."}

        return {
            "status": "success",
            "deleted": safe_name,
            "state": self.active_state
        }

    def lookup_ruleset_rule(self, topic: str, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Looks up a specific game rule, mechanic, or custom supplement ruling on the fly.
        Searches custom campaign supplement documents first, then falls back to standard rulings.
        """
        cid = campaign_id or self.manifest.get("active_campaign")
        files_dir = self.get_campaign_files_dir(cid) if cid else None
        found_excerpt = None
        source_doc = None

        clean_topic = (topic or "").strip()
        if not clean_topic:
            return {"status": "error", "message": "Rule topic is required."}

        if files_dir and os.path.exists(files_dir) and self.active_state:
            docs_map = self.active_state.get("documents", {})
            for fname, dinfo in docs_map.items():
                cat = dinfo.get("category", "")
                if cat in ("ruleset", "notes") or any(k in fname.lower() for k in ("rule", "supp", "house", "homebrew", "basic")):
                    fpath = os.path.join(files_dir, fname)
                    if os.path.exists(fpath):
                        text = ProjectService.extract_file_text(fpath)
                        m = re.search(re.escape(clean_topic), text, re.IGNORECASE)
                        if m:
                            start_pos = max(0, m.start() - 200)
                            end_pos = min(len(text), m.end() + 1500)
                            found_excerpt = text[start_pos:end_pos].strip()
                            source_doc = fname
                            break

        if found_excerpt:
            return {
                "status": "success",
                "topic": clean_topic,
                "source": source_doc,
                "ruling": found_excerpt
            }

        preset_key = (self.manifest.get("campaigns", {}).get(cid, {}).get("system_preset") or "heroic_d20").lower()
        directive = PRESET_RULES_DIRECTIVES.get(preset_key, PRESET_RULES_DIRECTIVES.get("heroic_d20", ""))
        return {
            "status": "success",
            "topic": clean_topic,
            "source": f"Standard {preset_key.upper()} Rules Directive",
            "ruling": f"No custom house rule found for '{clean_topic}'. Adjudicate using standard core rules:\n{directive}"
        }

    # --- Persistent Session Journal & Campaign Memory ---
    def record_campaign_notes(
        self,
        summary: str,
        location: Optional[str] = None,
        key_clues: Optional[List[str]] = None,
        active_quest: Optional[str] = None,
        last_action: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Appends a structured journal entry to the active campaign's persistent memory,
        updates the live session recap, current location, active quest, and discovered clues.
        """
        if not self.active_state:
            self.load_manifest()
            self.load_active_campaign_state()
        if not self.active_state:
            active_id = self.manifest.get("active_campaign")
            if active_id:
                self.active_state = self._generate_default_state(active_id)
            else:
                return {"status": "error", "message": "No active campaign loaded."}

        clean_summary = (summary or "").strip()
        if not clean_summary:
            return {"status": "error", "message": "Summary text is required."}

        journal = self.active_state.setdefault("journal", [])
        entry_id = f"entry_{len(journal) + 1:03d}"
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if isinstance(key_clues, str):
            clues_list = [c.strip() for c in key_clues.split(",") if c.strip()]
        elif isinstance(key_clues, list):
            clues_list = [str(c).strip() for c in key_clues if str(c).strip()]
        else:
            clues_list = []
        entry = {
            "id": entry_id,
            "timestamp": now_iso,
            "session_id": session_id or "",
            "summary": clean_summary,
            "location": (location or "").strip() or self.active_state.get("current_location", ""),
            "active_quest": (active_quest or "").strip() or self.active_state.get("active_quest", ""),
            "key_clues": clues_list,
            "last_action": (last_action or "").strip()
        }
        journal.append(entry)

        # Update latest persistent state
        self.active_state["latest_recap"] = clean_summary
        if location and location.strip():
            self.active_state["current_location"] = location.strip()
        if active_quest and active_quest.strip():
            self.active_state["active_quest"] = active_quest.strip()
        if last_action and last_action.strip():
            self.active_state["last_action"] = last_action.strip()
        if clues_list:
            existing_clues = self.active_state.setdefault("discovered_clues", [])
            for c in clues_list:
                if c not in existing_clues:
                    existing_clues.append(c)

        self.save_active_campaign_state()
        logger.info(f"[TTRPG] Recorded campaign journal entry {entry_id} for '{self.active_state.get('campaign_id')}'.")
        return {
            "status": "success",
            "entry": entry,
            "latest_recap": self.active_state.get("latest_recap"),
            "current_location": self.active_state.get("current_location"),
            "active_quest": self.active_state.get("active_quest"),
            "discovered_clues": self.active_state.get("discovered_clues", []),
            "state": self.active_state
        }

    def get_campaign_notes(self, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieves the persistent campaign journal, latest turn recap, discovered clues, and quest state.
        """
        cid = campaign_id or self.manifest.get("active_campaign")
        if not cid:
            return {"status": "error", "message": "No active campaign."}

        state = self.active_state if cid == self.manifest.get("active_campaign") else None
        if not state:
            state_path = os.path.join(self.data_dir, cid, "state.json")
            if os.path.exists(state_path):
                try:
                    with open(state_path, "r", encoding="utf-8") as f:
                        state = json.load(f)
                except Exception:
                    state = {}
            else:
                state = {}

        return {
            "status": "success",
            "campaign_id": cid,
            "latest_recap": state.get("latest_recap", ""),
            "current_location": state.get("current_location", ""),
            "active_quest": state.get("active_quest", ""),
            "last_action": state.get("last_action", ""),
            "discovered_clues": state.get("discovered_clues", []),
            "journal": state.get("journal", [])
        }

    def extract_campaign_synopsis(self, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Extracts and caches a structured narrative synopsis from the campaign adventure module
        (setting, primary hub, key NPCs, central mission, opening hook).
        """
        cid = campaign_id or self.manifest.get("active_campaign")
        if not cid:
            return {}

        state = self.active_state if cid == self.manifest.get("active_campaign") else None
        if not state:
            return {}

        existing_synopsis = state.get("campaign_synopsis", {})
        if existing_synopsis and isinstance(existing_synopsis, dict) and existing_synopsis.get("setting") and existing_synopsis.get("starting_scene"):
            return existing_synopsis

        files_dir = self.get_campaign_files_dir(cid)
        if not files_dir or not os.path.exists(files_dir):
            return {}

        docs_map = state.get("documents", {})
        module_file = None
        for fname in sorted(os.listdir(files_dir)):
            doc_cat = docs_map.get(fname, {}).get("category", "").lower()
            lower_name = fname.lower()
            if doc_cat in ("campaign", "module") or any(k in lower_name for k in ("elfhunt", "adventure", "module", "campaign", "quest")):
                if not any(k in lower_name for k in ("basicrules", "rule", "sheet", "char")):
                    module_file = fname
                    break

        if not module_file:
            return {}

        fpath = os.path.join(files_dir, module_file)
        full_text = ProjectService.extract_file_text(fpath)
        if not full_text:
            return {}

        client = self._get_genai_client()
        synopsis_data = None
        if client:
            prompt = f"""You are an expert tabletop RPG adventure module analyzer.
Analyze the following comprehensive excerpt from the adventure module:
{full_text[:30000]}

Extract the essential campaign grounding and the EXACT starting scene into valid JSON with this schema:
{{
  "module_title": "<module name and author>",
  "setting": "<geographic setting and realm>",
  "primary_hub": "<starting settlement / base of operations>",
  "key_npcs": ["<name and role of major NPCs>"],
  "target_or_threat": "<antagonist or target of the quest>",
  "core_mission": "<the central quest and what the party is hired to do>",
  "opening_hook": "<how the adventure begins>",
  "synopsis": "<3-4 sentence comprehensive premise of the module>",
  "starting_scene": {{
    "scene_name": "<exact scene heading, e.g. 'Act 1: Prologue — Scene 1: The Beginning'>",
    "location": "<exact location from module, e.g. 'Town Square', 'Village Hall', or 'Tavern'>",
    "boxed_text": "<verbatim boxed narration text intended for the DM to read to players upon entering the scene>",
    "active_npc": "<name and stat/personality details of the NPC present in this scene>",
    "quest_briefing": "<what the quest-giver explains to the party>",
    "immediate_choices": ["<choice 1>", "<choice 2>", "<choice 3>", "<choice 4>"]
  }}
}}
Respond ONLY with valid JSON."""
            try:
                from google.genai import types
                resp = client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1
                    )
                )
                text = getattr(resp, "text", "") or ""
                if text:
                    data = json.loads(text)
                    if isinstance(data, dict) and data.get("setting"):
                        synopsis_data = data
            except Exception as e:
                logger.warning(f"[TTRPG] AI campaign synopsis extraction failed: {e}")

        if not synopsis_data:
            synopsis_data = {
                "module_title": "Elfhunt by Jussi R. Foltmar Svendsen" if "elfhunt" in module_file.lower() else module_file,
                "setting": "Jungles of Chult, Forgotten Realms" if "elfhunt" in module_file.lower() else "Tabletop Adventure Realm",
                "primary_hub": "Camp Highmore" if "elfhunt" in module_file.lower() else "Adventure Outpost",
                "key_npcs": [
                    "High Chief Highmore (Leader of Camp Highmore)",
                    "Chief Filip (Commander of guards and soldiers, conspirator married to Shandra)",
                    "Chief Shandra (Tribal chief handling camp diplomacy, conspirator married to Filip)",
                    "Johan Petry (Retired veteran adventurer infatuated with Shandra, quest-giver)",
                    "Tane Here (Leader of chaotic rogues opposing camp authority)",
                    "Imis Elvan (Wood elf illusionist who fled with evidence of Filip and Shandra's conspiracy)"
                ] if "elfhunt" in module_file.lower() else [],
                "target_or_threat": "Chief Filip and Chief Shandra (secret conspirators framing the party); Imis Elvan as initial bounty target",
                "core_mission": "Track down and recover the elven fugitive Imis Elvan in the Chult jungle under the pretense that she stole military secrets",
                "opening_hook": "The party is invited to Johan Petry's house in Camp Highmore, who hires them to track down Imis Elvan.",
                "synopsis": (
                    "In the wilderness trade hub of Camp Highmore, Chief Filip and Chief Shandra plot to overthrow High Chief Highmore to seize regional control. "
                    "When the wood elf Imis Elvan discovers their conspiracy and flees into the Chultan jungle with incriminating evidence, the schemers recruit "
                    "an adventuring party through Johan Petry to track her down under false charges of military theft. As the adventurers investigate, Filip and Petry "
                    "attempt to frame them, forcing the party to align with rebel leader Tane Here, escape the camp, and locate Imis Elvan to expose the true conspirators."
                    if "elfhunt" in module_file.lower() else full_text[:400].strip()
                ),
                "starting_scene": {
                    "scene_name": "Act 1: Prologue — Scene 1: The Beginning",
                    "location": "Johan Petry's House (Main Hall), Camp Highmore, Jungles of Chult",
                    "boxed_text": "The large single-room hall has walls lined with trophies of monsters killed, most by Johan Petry. There's an empty board on the wall and a quest book on the large oak table in the middle of the room, which is lit by a combination of sunlight from the large window across the hall and the large fireplace that roars and mainly gives light rather than heat. An elderly man approaches you and introduces himself as Johan Petry. He has a quest for you.",
                    "active_npc": "Johan Petry (human veteran, thin old man with grey hair and short grey beard, AC 13, Strength 11, Wisdom 16, two swords + crossbow). In love with Chief Shandra, greedy for a wealthy retirement.",
                    "quest_briefing": "Johan hires the players to track down wood elf illusionist Imis Elvan, who fled from Camp Highmore into the jungle, and bring her back to the custody of Chief Filip. He explains that she allegedly stole secret military information at the barracks from Chief Filip (a lie told by Filip).",
                    "immediate_choices": [
                        "1. Go speak with Chief Shandra at the House of Chiefs about why she hired adventurers and who Imis Elvan is.",
                        "2. Go speak with Chief Filip at the barracks to investigate the scene of the alleged crime and pick up Imis's trail.",
                        "3. Inquire around town / criminal underground (attracting the attention of Tane Here and his rogues).",
                        "4. Go directly to the camp gate to pick up Imis Elvan's trail into the jungle."
                    ]
                } if "elfhunt" in module_file.lower() else {}
            }

        state["campaign_synopsis"] = synopsis_data
        if cid in self.manifest.get("campaigns", {}):
            if not self.manifest["campaigns"][cid].get("setting_overview") and synopsis_data.get("synopsis"):
                self.manifest["campaigns"][cid]["setting_overview"] = synopsis_data["synopsis"]
                self.save_manifest()
        self.save_active_campaign_state()
        return synopsis_data

    def auto_summarize_session_to_journal(
        self,
        turns: List[Dict[str, Any]],
        session_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Inspects session turns for TTRPG / campaign activity. If roleplay turns occurred,
        generates a concise journal entry and updates latest_recap in active_state.
        """
        if not turns or not self.active_state:
            return None

        active_id = self.active_state.get("campaign_id")
        camp_meta = self.manifest.get("campaigns", {}).get(active_id, {})
        camp_title = camp_meta.get("title", active_id or "")

        chars = self.active_state.get("characters", {})
        char_names = [c.get("name", "").lower() for c in chars.values() if c.get("name")]
        keywords = ["dungeon master", "dm", "ttrpg", "roll", "initiative", "campaign", camp_title.lower()] + char_names

        rp_turns = []
        for t in turns:
            txt = (t.get("content") or t.get("text") or "").strip()
            if txt and any(k in txt.lower() for k in keywords):
                rp_turns.append(f"{t.get('role', 'speaker')}: {txt}")

        if len(rp_turns) < 2:
            return None

        client = self._get_genai_client()
        if client:
            prompt = f"""You are a Game Master session journal recorder for the TTRPG campaign '{camp_title}'.
Review these dialogue turns from the recent game session:
{chr(10).join(rp_turns[-10:])}

Extract a concise 2-3 sentence DM journal recap covering:
1. What the party did and discovered in this session.
2. Where the party is currently standing / left off.
3. The immediate next action or decision facing them.

Respond in JSON with schema:
{{
  "summary": "<2-3 sentence recap>",
  "location": "<current scene location>",
  "active_quest": "<current immediate goal>",
  "new_clues": ["<key item, clue, or observation discovered>"],
  "last_action": "<exact last action taken>"
}}"""
            try:
                from google.genai import types
                resp = client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.2
                    )
                )
                text = getattr(resp, "text", "") or ""
                if text:
                    data = json.loads(text)
                    if isinstance(data, dict) and data.get("summary"):
                        return self.record_campaign_notes(
                            summary=data["summary"],
                            location=data.get("location"),
                            key_clues=data.get("new_clues"),
                            active_quest=data.get("active_quest"),
                            last_action=data.get("last_action"),
                            session_id=session_id
                        )
            except Exception as e:
                logger.warning(f"[TTRPG] AI session journal summarization failed: {e}")

        # Fallback summary
        last_turn_text = rp_turns[-1] if rp_turns else ""
        return self.record_campaign_notes(
            summary=f"Session concluded after: {last_turn_text[:200]}",
            session_id=session_id
        )

    def build_gm_context_prompt(self) -> str:
        """
        Builds a system-prompt grounding block for Aether so it can act as the
        Dungeon Master (DM) / Game Master (GM) for the active campaign, adjudicate
        uploaded rulesets/modules/character sheets, retain persistent turn memory across
        restarts, and ground narratives strictly in published module lore.
        """
        active_id = self.manifest.get("active_campaign")
        if not active_id:
            return ""
        campaigns = self.manifest.get("campaigns", {})
        meta = campaigns.get(active_id)
        if not meta:
            return ""

        state = self.active_state or {}
        chars = state.get("characters", {})
        ladder = state.get("initiative_order", [])
        round_num = state.get("current_round", 1)
        turn_idx = state.get("active_turn_index", 0)
        docs = state.get("documents", {})

        # Ensure synopsis is populated from uploaded module
        synopsis = state.get("campaign_synopsis", {})
        if not synopsis:
            synopsis = self.extract_campaign_synopsis(active_id)

        preset_key = (meta.get("system_preset") or "heroic_d20").lower()
        rule_directive = PRESET_RULES_DIRECTIVES.get(preset_key, PRESET_RULES_DIRECTIVES.get("heroic_d20", ""))

        lines = [
            f"[UNIVERSAL TABLETOP ROLEPLAYING ENGINE — ACTIVE CAMPAIGN: {meta.get('title', active_id)} ({active_id})]",
            f"- System Archetype / Ruleset: {meta.get('system_preset', 'heroic_d20')}",
            f"- Game Master Mode: {meta.get('gm_mode', 'interactive_narrator')}",
            f"- Narrative Tone: {meta.get('tone', 'Cinematic, grounded')}",
        ]
        if meta.get("setting_overview"):
            lines.append(f"- Setting & Premise: {meta['setting_overview']}")
        if rule_directive:
            lines.append(f"\n[RULESET MECHANICS DIRECTIVE]\n{rule_directive}")

        # DM Guidance & Difficulty Mode Directive
        difficulty = (meta.get("difficulty") or "helpful").lower().strip()
        dice_mode = (meta.get("dice_mode") or "digital").lower().strip()

        if difficulty == "helpful":
            dice_help_line = (
                "* PHYSICAL DICE MODE: Educate and explicitly guide the player on which physical dice to roll and what exact modifier to add from their character sheet (e.g., 'Roll 1d20+4 for your attack roll!').\n"
                if dice_mode == "physical"
                else "* DIGITAL MODE ACTIVE: Do NOT tell or ask the player to roll dice; execute `roll_dice` automatically for them.\n"
            )
            lines.append(
                "\n[DM GUIDANCE & CHALLENGE LEVEL: HELPFUL / BEGINNER-FRIENDLY MENTOR]\n"
                "* The players may be beginners or learning the rules. Act as an encouraging, patient guide and Dungeon Master.\n"
                "* Whenever you present a situation or prompt a player, ALWAYS offer 2-3 clear, recommended options for what their character can do (e.g. 'Option 1: You can swing your Longsword (+4 to hit)... Option 2: Shoot with your Heavy Crossbow... Option 3: Attempt to negotiate or intimidate...'), UNLESS the player has already declared their action.\n"
                "* When a player has ALREADY declared an action (e.g. 'I attack the goblin', 'I pick the lock'), IMMEDIATELY adjudicate that action without offering alternative options.\n"
                + dice_help_line +
                "* Proactively remind players of their character's equipment, healing potions, and class features (like Second Wind, Sneak Attack, or Action Surge) when relevant.\n"
                "* Keep explanations friendly, clear, and educational."
            )
        elif difficulty == "easy":
            lines.append(
                "\n[DM GUIDANCE & CHALLENGE LEVEL: EASY / NARRATIVE FIRST]\n"
                "* Prioritize cinematic heroics and narrative momentum over strict lethality.\n"
                "* Use lenient Difficulty Classes (DC 10-12) for skill checks. Enemies fight suboptimally and rarely attack downed heroes.\n"
                "* Allow generous short rest recovery and creative player problem-solving."
            )
        elif difficulty == "hard":
            lines.append(
                "\n[DM GUIDANCE & CHALLENGE LEVEL: HARD / GRITTY TACTICAL]\n"
                "* Ruthless, highly tactical enemy behavior: enemies focus-fire weak targets, exploit cover, flanking, and attack downed heroes.\n"
                "* Strict resource tracking: enforce ammunition, rations, and torchlight. High DCs (DC 15-20+) and unforgiving consequences."
            )
        else:
            lines.append(
                "\n[DM GUIDANCE & CHALLENGE LEVEL: MEDIUM / BALANCED STANDARD]\n"
                "* Balanced rules adherence and tactical encounters with standard DCs (Easy 10, Medium 15, Hard 20)."
            )

        # Dice Rolling Mode Directive (Physical Tabletop vs Digital Aether Rolls)
        if dice_mode == "physical":
            lines.append(
                "\n[DICE ROLLING MODE: PHYSICAL TABLETOP DICE (PLAYERS ROLL AT THE TABLE)]\n"
                "* PHYSICAL DICE MODE IS ACTIVE: The players are rolling their own physical dice in the real world at their table!\n"
                "* When an action, attack, ability check, or saving throw requires a roll, DO NOT roll dice for the player using `roll_dice`.\n"
                "* Instead, explicitly ask the player to roll their physical dice, tell them which die to roll (e.g. d20, 2d6), what modifier to add from their character sheet, and the DC or target (e.g., 'Sheila, please roll a d20 Perception check (+1 modifier)!').\n"
                "* Wait for the player to state their roll result (e.g., 'I rolled a 15'), then adjudicate the outcome and describe what happens based on the number they provide.\n"
                "* You may still use `roll_dice` for hidden DM rolls, monster attacks, or random encounter tables, but NEVER roll for players when Physical Dice mode is enabled."
            )
        else:
            lines.append(
                "\n[DICE ROLLING MODE: DIGITAL DICE (AUTOMATIC RESOLUTION)]\n"
                "* DIGITAL DICE (AETHER ROLLS FOR PLAYERS): You (the Game Master) handle all dice rolling automatically using the `roll_dice` tool.\n"
                "* CRITICAL RULE — NEVER ASK PLAYERS TO ROLL: Never tell or ask players to roll dice. Do not say 'Eric, roll 1d20+4' or wait for players to roll. Execute `roll_dice` directly in your turn.\n"
                "* CRITICAL RULE — NO META-NARRATION: Never say 'Aether rolls for Eric', 'I am rolling for you', or narrate in the third person about rolling. The roll is an invisible mechanical resolution.\n"
                "* NATURAL GM NARRATION: Weave the roll outcome directly and smoothly into the narrative: e.g., 'With an 18 to hit, your blade strikes true, dealing 8 slashing damage!' or 'The lock resists your picks with a total of 9.'"
            )

        lines.append(
            "\n[DIALOGUE STYLE & ANTI-REPETITION DIRECTIVES]\n"
            "* BE CONCISE & PUNCHY: Keep narrations to 2-4 immersive sentences per turn. Avoid walls of text.\n"
            "* NO REDUNDANT RECAP: Do not repeat back what the player just said. If Eric declares 'I attack the goblin', do not recite 'Eric decides to attack the goblin with his sword'. Jump directly into the swing and outcome.\n"
            "* NO ROBOTIC MENUS: Never list 'Option 1: ... Option 2: ... Option 3: ...' when a player has already declared an action. Only offer brief, organic suggestions if a player explicitly asks what they can do or is visibly stuck.\n"
            "* IMMERSIVE GM VOICE: Never mention system tools, JSON, meters, or 'Aether'. Speak as an evocative, living Dungeon Master."
        )

        # 1. Grounded Adventure Module Details (Prevents shooting from the hip)
        if synopsis and isinstance(synopsis, dict) and synopsis.get("setting"):
            lines.append("\n[GROUNDED CAMPAIGN ADVENTURE MODULE]")
            if synopsis.get("module_title"):
                lines.append(f"- Published Module: {synopsis['module_title']}")
            if synopsis.get("setting"):
                lines.append(f"- Setting & Realm: {synopsis['setting']}")
            if synopsis.get("primary_hub"):
                lines.append(f"- Starting Hub / Base: {synopsis['primary_hub']}")
            if synopsis.get("target_or_threat"):
                lines.append(f"- Target / Threat: {synopsis['target_or_threat']}")
            if synopsis.get("core_mission"):
                lines.append(f"- Primary Mission: {synopsis['core_mission']}")
            if synopsis.get("key_npcs"):
                lines.append(f"- Key NPCs: {', '.join(synopsis['key_npcs'])}")
            if synopsis.get("opening_hook"):
                lines.append(f"- Opening Hook: {synopsis['opening_hook']}")

            # Starting Scene Grounding & Boxed Text
            start_scene = synopsis.get("starting_scene")
            if start_scene and isinstance(start_scene, dict) and start_scene.get("location"):
                lines.append("\n[ACTIVE ADVENTURE SCENE NARRATIVE & SCRIPT — MANDATORY]")
                lines.append(f"- Active Scene: {start_scene.get('scene_name', 'Scene 1: The Beginning')}")
                lines.append(f"- Scene Location: {start_scene.get('location')}")
                if start_scene.get("boxed_text"):
                    lines.append(f"- VERBATIM BOXED NARRATION (Read or paraphrase this text when introducing the scene):\n  \"{start_scene['boxed_text']}\"")
                if start_scene.get("active_npc"):
                    lines.append(f"- Key NPC Present & In-Character: {start_scene['active_npc']}")
                if start_scene.get("quest_briefing"):
                    lines.append(f"- Quest Briefing: {start_scene['quest_briefing']}")
                if start_scene.get("immediate_choices"):
                    choices_formatted = "\n  ".join(f"* {c}" for c in start_scene["immediate_choices"])
                    lines.append(f"- Established Player Choices for this Scene:\n  {choices_formatted}")

            lines.append(
                "* MANDATORY GM GROUNDING DIRECTIVE: You are the Game Master running THIS specific published module. "
                "You MUST anchor your narrative, opening scene, NPCs, dialogue, and environment strictly to this module's "
                "setting, factions, and premise! Never invent generic fantasy taverns, random barkeeps, or unrelated storytelling when "
                "this module specifies its own settlements, NPCs, and quest line!"
            )

        # 2. Persistent Campaign Memory & Last Turn Recap (Recap across Restarts)
        recap = state.get("latest_recap")
        loc = state.get("current_location")
        quest = state.get("active_quest")
        clues = state.get("discovered_clues", [])
        last_action = state.get("last_action")

        if recap or loc or quest or clues or last_action:
            lines.append("\n[PERSISTENT CAMPAIGN MEMORY & LAST TURN RECAP]")
            if loc:
                lines.append(f"- Current Party Location: {loc}")
            if quest:
                lines.append(f"- Active Objective: {quest}")
            if clues:
                lines.append(f"- Discovered Clues & Items: {', '.join(clues)}")
            if last_action:
                lines.append(f"- Last Action Taken: {last_action}")
            if recap:
                lines.append(f"- Latest Turn / Session Recap: {recap}")
            lines.append(
                "* DIRECTIVE ON RESUMING PLAY: When players ask for a recap, 'where were we?', 'what happened last?', "
                "or ask to continue the campaign, RECAP THESE EXACT EVENTS from the persistent memory above! "
                "Do NOT hallucinate or invent new, disconnected storylines."
            )

        # 3. Active Party Roster
        if chars:
            char_summaries = []
            for _, ch in chars.items():
                hp = ch.get("vitals", {}).get("health", {})
                guard = ch.get("vitals", {}).get("guard", {})
                conds = ch.get("conditions", [])
                cond_str = f" [Conditions: {', '.join(conds)}]" if conds else ""
                lvl_str = f"Lv. {ch.get('level', 1)}"
                xp_str = f"{ch.get('experience_points', 0)} XP" if ch.get("experience_points") else ""
                lvl_xp = f" ({', '.join(filter(None, [lvl_str, xp_str]))})"

                wpns = ch.get("weapons", [])
                wpn_parts = [f"{w.get('name', 'Weapon')} ({w.get('atk_bonus', '+0')}, {w.get('damage', '1d4')})" for w in wpns]
                wpn_str = f" | Attacks: {', '.join(wpn_parts)}" if wpn_parts else ""

                inv = ch.get("inventory", [])
                inv_str = f" | Key Items: {', '.join(inv[:8])}" if inv else ""

                spells = ch.get("spells", [])
                spell_str = f" | Spells/Cantrips: {', '.join(spells[:6])}" if spells else ""

                feats = ch.get("features_and_traits", [])
                feat_str = f" | Features: {', '.join(feats[:4])}" if feats else ""

                init = ch.get("initiative", "")
                init_str = f", Init: {init}" if init else ""

                char_summaries.append(
                    f"{ch.get('name')}{lvl_xp} - {ch.get('archetype', 'Adventurer')} (Player: {ch.get('player_name', 'Player')} | "
                    f"HP: {hp.get('current', 0)}/{hp.get('max', 0)}, AC/Guard: {guard.get('current', 10)}{init_str}{cond_str}{wpn_str}{inv_str}{spell_str}{feat_str})"
                )
            lines.append("\n- Active Party Roster: \n  * " + "\n  * ".join(char_summaries))
        else:
            lines.append("\n- Active Party Roster: Empty (use `upsert_ttrpg_character` to add characters).")

        # 4. Combat Initiative
        if ladder:
            active_turn_name = ladder[turn_idx]["name"] if 0 <= turn_idx < len(ladder) else ladder[0]["name"]
            order_str = ", ".join(f"{e['name']} ({e['roll']})" for e in ladder)
            lines.append(f"- Combat Initiative (Round {round_num}, Active Turn: {active_turn_name}): {order_str}")

        # 5. Documents & Tools
        if docs:
            active_docs = []
            rules_references = []
            for d in docs.values():
                fname = d.get("filename", "")
                cat = d.get("category", "")
                tok = d.get("token_estimate", 0)
                # If it's a huge core rulebook (>35,000 tokens) or marked as basic rules, list as reference only
                if tok > 35000 or any(k in fname.lower() for k in ("basicrules", "player's handbook", "phb", "dungeon master's guide", "dmg", "monster manual")):
                    rules_references.append(f"'{fname}' (~{tok} tokens)")
                else:
                    active_docs.append(f"'{fname}' ({cat}, ~{tok} tokens)")

            if active_docs:
                lines.append("\n- Active Campaign Documents & Character Sheets: " + ", ".join(active_docs))
            if rules_references:
                lines.append("- Reference Core Rulebooks (On-Demand only): " + ", ".join(rules_references))
                lines.append("  * NOTE: You already know the base rules for this system. Only query reference rulebooks if a player specifically asks about an obscure rule!")

            lines.append(
                "  * NOTE: When players make significant discoveries or complete a scene beat, call `record_campaign_notes` "
                "to preserve the progress in the persistent campaign journal so it is retained across restarts!"
            )

        return "\n".join(lines) + "\n\n"

    # --- Cryptographic Dice Engine ---
    @staticmethod
    def parse_and_roll(formula: str, roll_type: str = "normal") -> Dict[str, Any]:
        """
        Evaluates dice expressions like: '1d20+5', '2d6-1', '8d6', '1d100'.
        Supports roll_type in ('normal', 'advantage', 'disadvantage').
        Uses secrets.randbelow() for cryptographic fairness.
        """
        clean_expr = formula.lower().replace(" ", "")
        pattern = r'^(\d+)?d(\d+)([\+\-]\d+)?$'
        match = re.match(pattern, clean_expr)

        if not match:
            return {
                "status": "error",
                "message": f"Invalid dice formula: '{formula}'. Format as 'XdY+Z' or 'd20'."
            }

        count_str, sides_str, mod_str = match.groups()
        num_dice = int(count_str) if count_str else 1
        sides = int(sides_str)
        modifier = int(mod_str) if mod_str else 0

        if num_dice <= 0 or sides <= 0 or num_dice > 100:
            return {"status": "error", "message": "Dice quantity or sides out of supported bounds."}

        # Handle Advantage / Disadvantage for single-die rolls (e.g., d20)
        if roll_type in ("advantage", "disadvantage") and num_dice == 1:
            roll_a = secrets.randbelow(sides) + 1
            roll_b = secrets.randbelow(sides) + 1
            if roll_type == "advantage":
                chosen_roll = max(roll_a, roll_b)
                dropped_roll = min(roll_a, roll_b)
            else:
                chosen_roll = min(roll_a, roll_b)
                dropped_roll = max(roll_a, roll_b)

            total = chosen_roll + modifier
            return {
                "status": "success",
                "roll_id": secrets.token_hex(6),
                "formula": formula,
                "roll_type": roll_type,
                "sides": sides,
                "kept_die": chosen_roll,
                "dropped_die": dropped_roll,
                "modifier": modifier,
                "total": total,
                "summary": f"Rolled [{roll_a}, {roll_b}] ({roll_type}) -> Kept {chosen_roll} + ({modifier}) = {total}"
            }

        # Standard Multi-Dice Roll
        rolls = [secrets.randbelow(sides) + 1 for _ in range(num_dice)]
        total = sum(rolls) + modifier

        return {
            "status": "success",
            "roll_id": secrets.token_hex(6),
            "formula": formula,
            "roll_type": "normal",
            "sides": sides,
            "dice_rolled": rolls,
            "modifier": modifier,
            "total": total,
            "summary": f"Rolled {rolls} + ({modifier}) = {total}" if modifier != 0 else f"Rolled {rolls} = {total}"
        }

    # --- Character State & Vitals Management ---
    @staticmethod
    def _safe_int(val: Any, default: int = 0) -> int:
        """Safely parses an integer from various types (int, float, str with +, etc.), returning default on failure."""
        if val is None:
            return default
        if isinstance(val, (int, float)):
            try:
                return int(val)
            except (ValueError, OverflowError):
                return default
        try:
            s = str(val).strip()
            if not s:
                return default
            m = re.search(r'[-+]?\d+', s)
            if m:
                return int(m.group(0))
            return default
        except Exception:
            return default

    def add_character(self, char_payload: Dict[str, Any], target_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Adds or updates a character in the active campaign roster."""
        st = target_state if target_state is not None else self.active_state
        if not st:
            return {"status": "error", "message": "No active campaign loaded."}

        name = (char_payload.get("name") or "").strip()
        if not name:
            return {"status": "error", "message": "Character name is required."}

        chars = st.setdefault("characters", {})
        existing_char = None
        char_id = char_payload.get("id")
        if char_id and char_id in chars:
            existing_char = chars[char_id]
        else:
            existing_id, found_char = self._find_character(name)
            if existing_id and existing_id in chars:
                char_id = existing_id
                existing_char = found_char
            else:
                char_id = char_id or f"char_{len(chars) + 1:02d}"
                while char_id in chars and chars[char_id].get("name", "").lower() != name.lower():
                    char_id = f"char_{secrets.randbelow(9000) + 1000}"

        prev = existing_char or {}
        prev_vitals = prev.get("vitals", {})

        default_max_hp = self._safe_int(prev_vitals.get("health", {}).get("max"), 25)
        raw_max_hp = char_payload.get("max_health") or char_payload.get("hp_max") or char_payload.get("hp")
        max_hp = max(1, self._safe_int(raw_max_hp, default_max_hp))
        default_cur_hp = self._safe_int(prev_vitals.get("health", {}).get("current"), max_hp)
        raw_cur_hp = char_payload.get("current_health") or char_payload.get("hp_current") or char_payload.get("health")
        cur_hp = self._safe_int(raw_cur_hp, default_cur_hp)
        cur_hp = max(0, min(max_hp, cur_hp))

        default_guard = self._safe_int(prev_vitals.get("guard", {}).get("current") or prev.get("armor_class"), 14)
        guard_val = self._safe_int(char_payload.get("guard") or char_payload.get("armor_class"), default_guard)

        raw_vitals = char_payload.get("vitals")
        if raw_vitals and isinstance(raw_vitals, dict) and "health" in raw_vitals:
            vitals = dict(raw_vitals)
            h_max = max(1, self._safe_int(vitals["health"].get("max"), max_hp))
            h_cur = self._safe_int(vitals["health"].get("current"), h_max)
            vitals["health"]["max"] = h_max
            vitals["health"]["current"] = max(0, min(h_max, h_cur))
        elif prev_vitals and "health" in prev_vitals:
            vitals = json.loads(json.dumps(prev_vitals))
            vitals["health"]["max"] = max_hp
            vitals["health"]["current"] = cur_hp
            if "guard" in vitals:
                vitals["guard"]["current"] = max(0, guard_val)
                vitals["guard"]["max"] = max(1, guard_val)
        else:
            vitals = {
                "health": {"current": cur_hp, "max": max_hp, "label": "Health"},
                "guard": {"current": max(0, guard_val), "max": max(1, guard_val), "label": "Armor Defense"}
            }

        custom_meters = char_payload.get("custom_meters")
        if custom_meters is None:
            if prev.get("custom_meters"):
                custom_meters = dict(prev.get("custom_meters", {}))
            else:
                custom_meters = {}
                cm_label = (char_payload.get("custom_meter_label") or "").strip()
                if cm_label:
                    cm_key = re.sub(r'[^a-z0-9_]+', '_', cm_label.lower()).strip('_') or "resource"
                    cm_max = max(1, self._safe_int(char_payload.get("custom_meter_max"), 5))
                    cm_cur = max(0, min(cm_max, self._safe_int(char_payload.get("custom_meter_current"), cm_max)))
                    custom_meters[cm_key] = {"current": cm_cur, "max": cm_max, "label": cm_label}

        # Determine ruleset
        ruleset = (
            char_payload.get("ruleset")
            or prev.get("ruleset")
            or char_payload.get("native_system")
            or prev.get("native_system")
            or (self.manifest.get("campaigns", {}).get(st.get("campaign_id", ""), {}).get("system_preset"))
            or "dnd_5e"
        )

        raw_attrs = char_payload.get("attributes") or {}
        prev_attrs = prev.get("attributes") or {}
        attributes = dict(raw_attrs) if raw_attrs else dict(prev_attrs)

        str_val = self._safe_int(char_payload.get("Strength") or raw_attrs.get("Strength") or char_payload.get("Might") or raw_attrs.get("Might") or prev_attrs.get("Strength") or prev_attrs.get("Might"), 10)
        dex_val = self._safe_int(char_payload.get("Dexterity") or raw_attrs.get("Dexterity") or char_payload.get("Agility") or raw_attrs.get("Agility") or prev_attrs.get("Dexterity") or prev_attrs.get("Agility"), 10)
        con_val = self._safe_int(char_payload.get("Constitution") or raw_attrs.get("Constitution") or char_payload.get("Vigor") or raw_attrs.get("Vigor") or prev_attrs.get("Constitution") or prev_attrs.get("Vigor"), 10)
        int_val = self._safe_int(char_payload.get("Intelligence") or raw_attrs.get("Intelligence") or char_payload.get("Intellect") or raw_attrs.get("Intellect") or prev_attrs.get("Intelligence") or prev_attrs.get("Intellect"), 10)
        wis_val = self._safe_int(char_payload.get("Wisdom") or raw_attrs.get("Wisdom") or char_payload.get("Presence") or raw_attrs.get("Presence") or prev_attrs.get("Wisdom") or prev_attrs.get("Presence"), 10)
        cha_val = self._safe_int(char_payload.get("Charisma") or raw_attrs.get("Charisma") or prev_attrs.get("Charisma") or wis_val, 10)

        if "cyber" in ruleset.lower():
            for stat in ("INT", "REF", "DEX", "TECH", "COOL", "WILL", "LUCK", "MOVE", "BODY", "EMP"):
                def_stat = self._safe_int(prev_attrs.get(stat), 6)
                attributes[stat] = self._safe_int(char_payload.get(stat) or raw_attrs.get(stat), def_stat)
            attributes.setdefault("Might", attributes.get("BODY", 6))
            attributes.setdefault("Agility", max(attributes.get("REF", 6), attributes.get("DEX", 6)))
            attributes.setdefault("Vigor", attributes.get("BODY", 6))
            attributes.setdefault("Intellect", attributes.get("INT", 6))
            attributes.setdefault("Presence", attributes.get("COOL", 6))
        elif "cthulhu" in ruleset.lower():
            for stat in ("STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU", "SAN"):
                def_stat = self._safe_int(prev_attrs.get(stat), 50)
                attributes[stat] = self._safe_int(char_payload.get(stat) or raw_attrs.get(stat), def_stat)
        elif "dnd" in ruleset.lower() or "pathfinder" in ruleset.lower() or "Strength" in char_payload or "Strength" in raw_attrs:
            attributes["Strength"] = str_val
            attributes["Dexterity"] = dex_val
            attributes["Constitution"] = con_val
            attributes["Intelligence"] = int_val
            attributes["Wisdom"] = wis_val
            attributes["Charisma"] = cha_val
            attributes.setdefault("Might", str_val)
            attributes.setdefault("Agility", dex_val)
            attributes.setdefault("Vigor", con_val)
            attributes.setdefault("Intellect", int_val)
            attributes.setdefault("Presence", max(wis_val, cha_val))
        elif "INT" in char_payload or "INT" in raw_attrs or "REF" in raw_attrs:
            for stat in ("INT", "REF", "DEX", "TECH", "COOL", "WILL", "LUCK", "MOVE", "BODY", "EMP"):
                def_stat = self._safe_int(prev_attrs.get(stat), 6)
                attributes[stat] = self._safe_int(char_payload.get(stat) or raw_attrs.get(stat), def_stat)
            attributes.setdefault("Might", attributes.get("BODY", 6))
            attributes.setdefault("Agility", max(attributes.get("REF", 6), attributes.get("DEX", 6)))
            attributes.setdefault("Vigor", attributes.get("BODY", 6))
            attributes.setdefault("Intellect", attributes.get("INT", 6))
            attributes.setdefault("Presence", attributes.get("COOL", 6))
        else:
            attributes.setdefault("Might", str_val)
            attributes.setdefault("Agility", dex_val)
            attributes.setdefault("Vigor", con_val)
            attributes.setdefault("Intellect", int_val)
            attributes.setdefault("Presence", max(wis_val, cha_val))

        def _get_val(key, default):
            if key in char_payload and char_payload[key] is not None:
                return char_payload[key]
            if key in prev and prev[key] is not None:
                return prev[key]
            return default

        ac_val = guard_val
        if "armor_class" in char_payload and char_payload["armor_class"] is not None:
            ac_val = self._safe_int(char_payload["armor_class"], guard_val)
        elif "guard" in char_payload and char_payload["guard"] is not None:
            ac_val = guard_val
        elif "armor_class" in prev and prev["armor_class"] is not None:
            ac_val = self._safe_int(prev["armor_class"], guard_val)

        feats_resolved = (
            char_payload.get("features_and_traits")
            or char_payload.get("traits_and_abilities")
            or char_payload.get("features")
            or char_payload.get("features_traits")
            or (prev.get("features_and_traits") if prev else None)
            or (prev.get("traits_and_abilities") if prev else None)
            or []
        )
        inv_resolved = (
            char_payload.get("inventory")
            or char_payload.get("live_inventory")
            or (prev.get("inventory") if prev else None)
            or []
        )
        if inv_resolved and isinstance(inv_resolved, list) and len(inv_resolved) > 0 and isinstance(inv_resolved[0], dict):
            inv_resolved = [item.get("name", str(item)) if isinstance(item, dict) else str(item) for item in inv_resolved]

        char_entry = {
            "id": char_id,
            "name": name,
            "player_name": str(_get_val("player_name", "Player")).strip(),
            "archetype": str(_get_val("archetype", "Adventurer")).strip(),
            "is_player": bool(_get_val("is_player", True)),
            "level": max(1, self._safe_int(_get_val("level", 1), 1)),
            "experience_points": max(0, self._safe_int(_get_val("experience_points", 0), 0)),
            "ruleset": ruleset,
            "species": str(_get_val("species", "") or _get_val("race", "")),
            "background": str(_get_val("background", "")),
            "vitals": vitals,
            "custom_meters": custom_meters,
            "attributes": attributes,
            "saving_throws": dict(_get_val("saving_throws", {})),
            "currency": dict(_get_val("currency", {"cp": 0, "sp": 0, "ep": 0, "gp": 0, "pp": 0})),
            "speed": str(_get_val("speed", "30 ft.")),
            "hit_dice": str(_get_val("hit_dice", "1d8")),
            "armor_class": ac_val,
            "temp_hp": max(0, self._safe_int(_get_val("temp_hp", 0), 0)),
            "conditions": list(_get_val("conditions", [])),
            "weapons": list(_get_val("weapons", [])),
            "inventory": list(inv_resolved),
            "proficiencies": str(_get_val("proficiencies", "")),
            "prof_bonus": str(_get_val("prof_bonus", "+2")),
            "passives": dict(_get_val("passives", {})),
            "initiative": str(_get_val("initiative", "")),
            "features_and_traits": list(feats_resolved),
            "traits_and_abilities": list(feats_resolved),
            "skills": dict(_get_val("skills", {})),
            "spells": list(_get_val("spells", []))
        }

        # Preserve optional rich sheet properties
        for opt_key in (
            "class_and_level", "senses", "defenses", "resistances", "proficiencies_armor", "proficiencies_weapons",
            "proficiencies_tools", "proficiencies_languages", "head_sp", "body_sp", "humanity",
            "spellcasting_ability", "spell_save_dc", "spell_attack_bonus",
            "backstory", "personality_traits", "ideals", "bonds", "flaws", "appearance", "allies"
        ):
            if opt_key in char_payload and char_payload[opt_key] is not None:
                char_entry[opt_key] = char_payload[opt_key]
            elif opt_key in prev and prev[opt_key] is not None:
                char_entry[opt_key] = prev[opt_key]

        chars[char_id] = char_entry
        active_cid = st.get("campaign_id")
        if active_cid and active_cid in self.manifest.get("campaigns", {}):
            self.manifest["campaigns"][active_cid]["party_count"] = len(chars)
            self.save_manifest()
        if target_state is None or st == self.active_state:
            self.save_active_campaign_state()
        logger.info(f"[TTRPG] Character '{name}' ({char_id}) saved in campaign '{active_cid}'. HP: {cur_hp}/{max_hp}, Guard/AC: {guard_val}")
        return {"status": "success", "character": char_entry, "state": st}

    def delete_character(self, character_id_or_name: str) -> Dict[str, Any]:
        if not self.active_state:
            return {"status": "error", "message": "No active campaign loaded."}
        chars = self.active_state.get("characters", {})
        target_id = character_id_or_name if character_id_or_name in chars else None
        if not target_id:
            target_id, _ = self._find_character(character_id_or_name)
        if not target_id or target_id not in chars:
            return {"status": "error", "message": f"Character '{character_id_or_name}' not found."}

        removed = chars.pop(target_id)
        active_cid = self.active_state.get("campaign_id")
        if active_cid and active_cid in self.manifest.get("campaigns", {}):
            self.manifest["campaigns"][active_cid]["party_count"] = len(chars)
            self.save_manifest()
        self.save_active_campaign_state()
        return {"status": "success", "deleted": removed, "state": self.active_state}

    def manage_character_progression(
        self,
        character_name: str,
        action: str = "award_xp",
        amount_or_level: int = 0,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Awards experience points (XP) or levels up a character.
        If action == 'award_xp', increases XP and evaluates standard D&D 5e milestone thresholds
        (e.g., Level 2 at 300 XP, Level 3 at 900 XP, Level 4 at 2,700 XP, Level 5 at 6,500 XP).
        If action == 'level_up', increments level, increases Max HP and Hit Dice, and updates proficiency bonus.
        """
        if not self.active_state:
            self.load_manifest()
            self.load_active_campaign_state()
        if not self.active_state:
            return {"status": "error", "message": "No active campaign loaded."}

        char_id, char_obj = self._find_character(character_name)
        if not char_id or not char_obj:
            return {"status": "error", "message": f"Character '{character_name}' not found."}

        cur_level = int(char_obj.get("level", 1))
        cur_xp = int(char_obj.get("experience_points", 0))

        # 5e standard XP thresholds
        xp_thresholds = {
            1: 0,
            2: 300,
            3: 900,
            4: 2700,
            5: 6500,
            6: 14000,
            7: 23000,
            8: 34000,
            9: 48000,
            10: 64000
        }

        act = (action or "award_xp").lower().strip()
        level_up_ready = False

        if act == "award_xp":
            add_xp = max(0, int(amount_or_level))
            new_xp = cur_xp + add_xp
            char_obj["experience_points"] = new_xp
            next_req = xp_thresholds.get(cur_level + 1, (cur_level + 1) * 10000)
            if new_xp >= next_req:
                level_up_ready = True
                msg = f"Awarded {add_xp} XP to {char_obj['name']} (Total: {new_xp} XP). QUALIFIES FOR LEVEL UP to Level {cur_level + 1}! Next step: call `manage_character_progression` with action='level_up' to advance."
            else:
                msg = f"Awarded {add_xp} XP to {char_obj['name']}. Current: {new_xp} / {next_req} XP for Level {cur_level + 1}."

        elif act == "level_up":
            target_level = cur_level + 1 if (amount_or_level <= 0 or amount_or_level <= cur_level) else int(amount_or_level)
            char_obj["level"] = target_level

            # Calculate HP bonus (approx +6 for d8/d10 hit die + CON mod)
            vigor = char_obj.get("attributes", {}).get("Vigor", 10)
            con_mod = (vigor - 10) // 2
            hp_gain = max(1, 6 + con_mod)

            vitals = char_obj.setdefault("vitals", {})
            hp_data = vitals.setdefault("health", {"current": 20, "max": 20, "label": "Health"})
            hp_data["max"] += hp_gain
            hp_data["current"] += hp_gain

            # Update hit dice custom meter
            hd_meter = char_obj.setdefault("custom_meters", {}).setdefault("hit_dice", {"current": cur_level, "max": cur_level, "label": "Hit Dice"})
            hd_meter["max"] = target_level
            hd_meter["current"] = target_level

            # Update prof bonus
            prof = "+2" if target_level < 5 else ("+3" if target_level < 9 else "+4")
            char_obj["prof_bonus"] = prof

            msg = f"{char_obj['name']} leveled up from Level {cur_level} to Level {target_level}! Max HP increased by {hp_gain} (new max: {hp_data['max']}). Proficiency Bonus: {prof}. Hit Dice updated to {target_level}."

        else:
            return {"status": "error", "message": f"Unsupported progression action '{action}'. Use 'award_xp' or 'level_up'."}

        self.save_active_campaign_state()
        return {
            "status": "success",
            "message": msg,
            "character_name": char_obj["name"],
            "level": char_obj.get("level", 1),
            "experience_points": char_obj.get("experience_points", 0),
            "level_up_ready": level_up_ready,
            "character": char_obj,
            "state": self.active_state
        }

    def modify_character_stat(
        self,
        character_name: str,
        stat_key: str,
        delta: int,
        is_custom_meter: bool = False
    ) -> Dict[str, Any]:
        """Modifies HP, defense, or custom meters with strict upper/lower clamping."""
        if not self.active_state:
            return {"status": "error", "message": "No active campaign loaded."}

        char_id, char_data = self._find_character(character_name)
        if not char_data:
            return {"status": "error", "message": f"Character '{character_name}' not found."}

        target_dict = char_data.get("custom_meters" if is_custom_meter else "vitals", {})
        if stat_key not in target_dict:
            return {"status": "error", "message": f"Stat '{stat_key}' not found on {character_name}."}

        entry = target_dict[stat_key]
        old_val = entry["current"]
        max_val = entry.get("max", 9999)
        new_val = max(0, min(max_val, old_val + delta))
        entry["current"] = new_val

        self.save_active_campaign_state()
        return {
            "status": "success",
            "character_id": char_id,
            "character_name": char_data["name"],
            "stat": stat_key,
            "old_value": old_val,
            "new_value": new_val,
            "max_value": max_val,
            "delta": delta
        }

    def update_condition(self, character_name: str, condition: str, active: bool) -> Dict[str, Any]:
        """Adds or removes conditions (e.g., Poisoned, Blinded, Stunned)."""
        char_id, char_data = self._find_character(character_name)
        if not char_data:
            return {"status": "error", "message": f"Character '{character_name}' not found."}

        conditions = set(char_data.get("conditions", []))
        if active:
            conditions.add(condition.strip().capitalize())
        else:
            conditions.discard(condition.strip().capitalize())

        char_data["conditions"] = sorted(list(conditions))
        self.save_active_campaign_state()

        return {
            "status": "success",
            "character_name": char_data["name"],
            "active_conditions": char_data["conditions"]
        }

    def manage_character_inventory(
        self,
        character_name: str,
        action: str = "add",
        items: Optional[List[str]] = None,
        weapons: Optional[List[Dict[str, str]]] = None,
        spells: Optional[List[Any]] = None,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Adds or removes items, weapons, and spells from a character's inventory in the active campaign.
        Enables both the user and Aether DM to update gear and spells when items are found, looted, bought, used, or lost.
        """
        if not self.active_state:
            return {"status": "error", "message": "No active campaign loaded."}

        char_id, char_data = self._find_character(character_name)
        if not char_data:
            return {"status": "error", "message": f"Character '{character_name}' not found."}

        action_norm = (action or "add").lower().strip()
        current_inv = char_data.setdefault("inventory", [])
        current_wpns = char_data.setdefault("weapons", [])
        current_spells = char_data.setdefault("spells", [])
        modified_items: List[str] = []
        modified_weapons: List[str] = []
        modified_spells: List[str] = []

        if action_norm in ("add", "loot", "receive", "learn"):
            if items:
                for it in items:
                    clean_it = str(it).strip()
                    if clean_it:
                        # Append or ensure present
                        current_inv.append(clean_it)
                        modified_items.append(clean_it)
            if weapons:
                for w in weapons:
                    if isinstance(w, dict) and w.get("name"):
                        w_record = {
                            "name": str(w["name"]).strip(),
                            "atk_bonus": str(w.get("atk_bonus", "+0")).strip(),
                            "damage": str(w.get("damage", "1d4")).strip(),
                            "notes": str(w.get("notes", "")).strip()
                        }
                        # Replace if existing or append
                        existing_w = next((x for x in current_wpns if x.get("name", "").lower() == w_record["name"].lower()), None)
                        if existing_w:
                            existing_w.update(w_record)
                        else:
                            current_wpns.append(w_record)
                        modified_weapons.append(w_record["name"])
            if spells:
                for s in spells:
                    if isinstance(s, dict) and s.get("name"):
                        s_record = {
                            "name": str(s["name"]).strip(),
                            "level": str(s.get("level", "Cantrip")).strip(),
                            "school": str(s.get("school", "")).strip(),
                            "atk_bonus": str(s.get("atk_bonus", "")).strip(),
                            "save_dc": str(s.get("save_dc", "")).strip(),
                            "damage": str(s.get("damage", "")).strip(),
                            "range": str(s.get("range", "")).strip(),
                            "notes": str(s.get("notes", "")).strip()
                        }
                        existing_s = next((x for x in current_spells if (x.get("name", "") if isinstance(x, dict) else str(x)).lower() == s_record["name"].lower()), None)
                        if existing_s and isinstance(existing_s, dict):
                            existing_s.update(s_record)
                        else:
                            current_spells.append(s_record)
                        modified_spells.append(s_record["name"])
                    elif isinstance(s, str) and s.strip():
                        clean_s = s.strip()
                        if clean_s not in current_spells:
                            current_spells.append(clean_s)
                        modified_spells.append(clean_s)

        elif action_norm in ("remove", "delete", "use", "consume", "drop", "lose", "unlearn"):
            if items:
                for it in items:
                    target_name = str(it).strip().lower()
                    for idx, cur_it in enumerate(list(current_inv)):
                        if cur_it.lower() == target_name or target_name in cur_it.lower():
                            removed = current_inv.pop(idx)
                            modified_items.append(removed)
                            break
            if weapons:
                for w in weapons:
                    w_name = (w.get("name") if isinstance(w, dict) else str(w)).strip().lower()
                    for idx, cur_w in enumerate(list(current_wpns)):
                        if cur_w.get("name", "").lower() == w_name or w_name in cur_w.get("name", "").lower():
                            removed = current_wpns.pop(idx)
                            modified_weapons.append(removed.get("name", w_name))
                            break
            if spells:
                for s in spells:
                    s_name = (s.get("name") if isinstance(s, dict) else str(s)).strip().lower()
                    for idx, cur_s in enumerate(list(current_spells)):
                        cur_name = (cur_s.get("name") if isinstance(cur_s, dict) else str(cur_s)).strip().lower()
                        if cur_name == s_name or s_name in cur_name:
                            removed = current_spells.pop(idx)
                            rem_name = removed.get("name", s_name) if isinstance(removed, dict) else str(removed)
                            modified_spells.append(rem_name)
                            break

        self.save_active_campaign_state()
        logger.info(f"[TTRPG] Inventory/Spells {action_norm} for '{char_data['name']}': Items={modified_items}, Weapons={modified_weapons}, Spells={modified_spells}. Reason: {reason or 'Manual/DM update'}")

        return {
            "status": "success",
            "character_name": char_data["name"],
            "action": action_norm,
            "modified_items": modified_items,
            "modified_weapons": modified_weapons,
            "modified_spells": modified_spells,
            "total_inventory_count": len(current_inv),
            "total_weapons_count": len(current_wpns),
            "total_spells_count": len(current_spells),
            "inventory": current_inv,
            "weapons": current_wpns,
            "spells": current_spells,
            "message": f"Successfully updated inventory for '{char_data['name']}' ({action_norm}: {len(modified_items)} item(s), {len(modified_weapons)} weapon(s))."
        }

    def reformat_weapons_or_items(self, raw_text: str, ruleset: str = "dnd_5e") -> Dict[str, Any]:
        """
        Parses free-text or unformatted item/weapon inputs (e.g. from loot drops, chat, or messy notes)
        and reformats them into structured weapon records, inventory items, and currency.
        Uses Gemini if available, with an instant heuristic regex fallback.
        """
        if not raw_text or not raw_text.strip():
            return {"status": "success", "weapons": [], "items": [], "currency": {}}

        clean_text = raw_text.strip()
        client = self._get_genai_client()
        if client:
            try:
                from google.genai import types
                prompt = f"""You are an expert tabletop RPG weapon and equipment adjudicator for ruleset: {ruleset}.
Parse and reformat this raw unformatted input into structured weapons, general inventory items, and currency:
\"\"\"{clean_text}\"\"\"

Output JSON strictly adhering to this schema:
{{
  "weapons": [
    {{
      "name": "Weapon Name",
      "type": "Melee Weapon",
      "atk_bonus": "+4",
      "damage": "1d4+2",
      "damage_type": "Piercing",
      "notes": "Finesse, Light, Range (20/60)"
    }}
  ],
  "items": ["Item name 1", "Item name 2"],
  "currency": {{"cp": 0, "sp": 0, "ep": 0, "gp": 0, "pp": 0, "eb": 0}}
}}
Valid types: "Melee Weapon", "Ranged Weapon", "Spell Attack", "Unarmed Strike", "Firearm", "Cyberware", "Other".
Valid damage_type: "Slashing", "Piercing", "Bludgeoning", "Fire", "Cold", "Lightning", "Poison", "Acid", "Radiant", "Necrotic", "Force", "Psychic", "Physical".
Return ONLY valid JSON.
"""
                resp = client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1
                    )
                )
                if resp and resp.text:
                    parsed = json.loads(resp.text.strip())
                    if isinstance(parsed, dict) and "weapons" in parsed:
                        return {
                            "status": "success",
                            "weapons": parsed.get("weapons", []),
                            "items": parsed.get("items", []),
                            "currency": parsed.get("currency", {})
                        }
            except Exception as e:
                logger.debug(f"[TTRPG] AI reformatting attempt failed, using heuristic parser: {e}")

        # Heuristic fallback parser
        return self._heuristic_reformat_weapons_or_items(clean_text, ruleset)

    def _heuristic_reformat_weapons_or_items(self, raw_text: str, ruleset: str = "dnd_5e") -> Dict[str, Any]:
        """
        Deterministic offline fallback parser for weapons, inventory items, and currency.
        Handles comma-laden inputs like ',Dual Daggers (+4, 1d2 + 6)' and 'Crossbow, Heavy (+3, 1d10+1 Piercing)'.
        """
        weapons: List[Dict[str, str]] = []
        items: List[str] = []
        currency: Dict[str, int] = {"cp": 0, "sp": 0, "ep": 0, "gp": 0, "pp": 0, "eb": 0}

        standard_dmg_types = [
            "slashing", "piercing", "bludgeoning", "fire", "cold", "lightning",
            "poison", "acid", "radiant", "necrotic", "force", "psychic", "physical"
        ]

        # Extract currency tokens if present
        cur_pattern = r'\b(\d+)\s*(gp|gold|sp|silver|cp|copper|pp|platinum|ep|electrum|eb|eurodollars?)\b'
        for cur_match in re.finditer(cur_pattern, raw_text, re.IGNORECASE):
            amt = int(cur_match.group(1))
            unit = cur_match.group(2).lower()
            if "gold" in unit or unit == "gp": currency["gp"] += amt
            elif "silver" in unit or unit == "sp": currency["sp"] += amt
            elif "copper" in unit or unit == "cp": currency["cp"] += amt
            elif "platinum" in unit or unit == "pp": currency["pp"] += amt
            elif "electrum" in unit or unit == "ep": currency["ep"] += amt
            elif "eb" in unit or "euro" in unit: currency["eb"] += amt
        text_clean = re.sub(cur_pattern, '', raw_text, flags=re.IGNORECASE)

        # Regex scanner for parenthesized weapons (e.g. "Crossbow, Heavy (+3, 1d10+1)" or ",Dual Daggers (+4, 1d2 + 6)")
        wpn_pattern = r'((?:Crossbow,\s*(?:Heavy|Light|Hand)|[^\n,;]+?))\s*\(([^)]*(?:[+-]\d+|\d+d\d+)[^)]*)\)'

        last_end = 0
        for m in re.finditer(wpn_pattern, text_clean):
            prefix = text_clean[last_end:m.start()].strip(" ,;\t\r\n")
            if prefix:
                for it in re.split(r'[,;\n]+', prefix):
                    clean_it = it.strip(" ,;")
                    if clean_it:
                        items.append(clean_it)
            last_end = m.end()

            raw_name = m.group(1).strip(" ,;")
            inside = m.group(2).strip()
            parts = [p.strip() for p in re.split(r'[,;]', inside) if p.strip()]
            atk_bonus = "+0"
            dmg = "1d4"
            dmg_type = "Slashing"
            notes = []

            for p in parts:
                if re.match(r'^[+-]\d+$', p):
                    atk_bonus = p
                elif re.search(r'\d+d\d+', p):
                    dmg = p
                    for dt in standard_dmg_types:
                        if re.search(r'\b' + dt + r'\b', p, re.IGNORECASE):
                            dmg_type = dt.title()
                            dmg = re.sub(r'\b' + dt + r'\b', '', p, flags=re.IGNORECASE).strip()
                            break
                else:
                    is_dt = False
                    for dt in standard_dmg_types:
                        if p.lower() == dt:
                            dmg_type = dt.title()
                            is_dt = True
                            break
                    if not is_dt:
                        notes.append(p)

            # Infer weapon type
            w_type = "Melee Weapon"
            low_name = raw_name.lower()
            all_notes = " ".join(notes).lower()
            if any(k in low_name or k in all_notes for k in ["bow", "crossbow", "range", "sling", "dart"]):
                w_type = "Ranged Weapon"
            elif any(k in low_name for k in ["bolt", "blast", "ray", "touch", "spell", "cantrip"]):
                w_type = "Spell Attack"
            elif any(k in low_name for k in ["unarmed", "fist", "bite", "claw"]):
                w_type = "Unarmed Strike"
            elif any(k in low_name for k in ["pistol", "rifle", "shotgun", "gun", "revolver", "smg"]):
                w_type = "Firearm"
            elif any(k in low_name for k in ["mantis", "gorilla", "wolvers", "cyber"]):
                w_type = "Cyberware"

            weapons.append({
                "name": raw_name,
                "type": w_type,
                "atk_bonus": atk_bonus,
                "damage": dmg,
                "damage_type": dmg_type,
                "notes": ", ".join(notes)
            })

        remainder = text_clean[last_end:].strip(" ,;\t\r\n")
        if remainder:
            for it in re.split(r'[,;\n]+', remainder):
                clean_it = it.strip(" ,;")
                if clean_it:
                    items.append(clean_it)

        return {
            "status": "success",
            "weapons": weapons,
            "items": items,
            "currency": currency
        }

    # --- Initiative Ladder Management ---
    def add_to_initiative(self, name: str, roll_val: int, is_player: bool = True) -> Dict[str, Any]:
        if not self.active_state:
            return {"status": "error", "message": "No active campaign."}

        ladder = self.active_state.setdefault("initiative_order", [])
        # Remove existing entity if already on the ladder
        ladder = [item for item in ladder if item["name"].lower() != name.lower()]
        ladder.append({
            "entity_id": f"ent_{len(ladder) + 1}",
            "name": name,
            "roll": int(roll_val),
            "is_player": is_player
        })

        # Sort descending by initiative roll
        ladder.sort(key=lambda x: x["roll"], reverse=True)
        self.active_state["initiative_order"] = ladder
        self.save_active_campaign_state()

        return {"status": "success", "initiative_order": ladder}

    def remove_from_initiative(self, name_or_id: str) -> Dict[str, Any]:
        if not self.active_state:
            return {"status": "error", "message": "No active campaign."}
        ladder = self.active_state.get("initiative_order", [])
        clean = (name_or_id or "").strip().lower()
        ladder = [
            item for item in ladder
            if item.get("name", "").lower() != clean and item.get("entity_id", "").lower() != clean
        ]
        self.active_state["initiative_order"] = ladder
        if self.active_state.get("active_turn_index", 0) >= len(ladder):
            self.active_state["active_turn_index"] = 0
        self.save_active_campaign_state()
        return {"status": "success", "initiative_order": ladder}

    def advance_turn(self) -> Dict[str, Any]:
        """Advances combat to the next turn, automatically incrementing rounds."""
        if not self.active_state:
            return {"status": "error", "message": "No active campaign."}

        ladder = self.active_state.get("initiative_order", [])
        if not ladder:
            return {"status": "error", "message": "Initiative ladder is empty."}

        idx = self.active_state.get("active_turn_index", 0) + 1
        round_num = self.active_state.get("current_round", 1)

        if idx >= len(ladder):
            idx = 0
            round_num += 1

        self.active_state["active_turn_index"] = idx
        self.active_state["current_round"] = round_num
        self.save_active_campaign_state()

        active_entity = ladder[idx]
        return {
            "status": "success",
            "round": round_num,
            "active_entity": active_entity["name"],
            "is_player": active_entity["is_player"],
            "turn_index": idx
        }

    # --- Single-Mic Tabletop Coordination & Clarification Engine ---
    def set_party_caller(self, character_name: str) -> Dict[str, Any]:
        """Sets the designated player who resolves group decisions in exploration mode."""
        if not self.active_state:
            return {"status": "error", "message": "No active campaign state."}
        char_id, char_data = self._find_character(character_name)
        if not char_data:
            chars = self.active_state.get("characters", {})
            if character_name in chars:
                char_id = character_name
                char_data = chars[char_id]
            else:
                return {"status": "error", "message": f"Character '{character_name}' not found."}

        self.active_state["party_caller_id"] = char_id
        self.save_active_campaign_state()
        return {"status": "success", "caller_id": char_id, "caller_name": char_data["name"]}

    def set_focused_speaker(self, character_id: str) -> Dict[str, Any]:
        """Sets the active speaker fallback for single-mic setups."""
        if not self.active_state:
            return {"status": "error", "message": "No active campaign state."}
        chars = self.active_state.get("characters", {})
        if character_id in chars:
            c_id = character_id
            c_data = chars[c_id]
        else:
            c_id, c_data = self._find_character(character_id)
            if not c_data:
                return {"status": "error", "message": f"Character '{character_id}' not found."}

        self.active_state["focused_speaker_id"] = c_id
        self.save_active_campaign_state()
        return {"status": "success", "focused_speaker_id": c_id, "name": c_data.get("name")}

    def stash_pending_action(self, action_text: str, question: str):
        """Stashes an anonymous action awaiting table clarification."""
        if not self.active_state:
            return
        self.active_state["pending_action_stash"] = {
            "action_text": action_text,
            "prompt_message": question,
            "stashed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
        self.save_active_campaign_state()

    def pop_pending_action(self) -> Optional[str]:
        """Retrieves and clears the stashed action upon actor identification."""
        if not self.active_state or "pending_action_stash" not in self.active_state:
            return None
        stashed = self.active_state.pop("pending_action_stash", None)
        self.save_active_campaign_state()
        return stashed.get("action_text") if stashed else None

    def generate_scene_hud(self, active_actor_name: Optional[str] = None) -> str:
        """
        Synthesizes a high-density, low-token Scene HUD (<150 tokens) for LLM context injection.
        """
        if not self.active_state:
            return ""

        chars = self.active_state.get("characters", {})
        caller_id = self.active_state.get("party_caller_id")
        caller_name = chars.get(caller_id, {}).get("name", "Unassigned")

        # Determine active turn
        ladder = self.active_state.get("initiative_order", [])
        turn_idx = self.active_state.get("active_turn_index", 0)
        active_turn_name = ladder[turn_idx]["name"] if (ladder and turn_idx < len(ladder)) else None

        dice_mode = self.get_dice_mode()
        dice_directive = "Digital (Call `roll_dice` automatically; never tell player to roll or say 'Aether rolls')" if dice_mode == "digital" else "Physical (Prompt active player to roll at table)"

        hud = [
            "### SCENE HUD (PARTY STATE) ###",
            f"Party Caller (Exploration Lead): {caller_name}",
            f"Current Turn Initiative: {active_turn_name or 'Out of Combat'}",
            f"Active Speaking Character: {active_actor_name or 'Party General'}",
            f"Active Dice Mode: {dice_directive}",
            "Party Roster Status:"
        ]

        for c_id, c in chars.items():
            hp_cur = c.get("vitals", {}).get("health", {}).get("current", 0)
            hp_max = c.get("vitals", {}).get("health", {}).get("max", 0)
            guard = c.get("vitals", {}).get("guard", {}).get("current", 0)
            conds = ", ".join(c.get("conditions", [])) or "Clear"
            hud.append(
                f"- {c.get('name', 'Hero')} [{c.get('archetype', 'Hero')}]: {hp_cur}/{hp_max} HP | "
                f"Guard: {guard} | Conditions: {conds}"
            )

        hud.append("################################")
        return "\n".join(hud)

    # --- Internal Helpers ---
    def _find_character(self, name: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        if not self.active_state:
            return None, None
        chars = self.active_state.get("characters", {})
        clean = (name or "").lower().strip()
        for c_id, c_data in chars.items():
            if c_data.get("name", "").lower() == clean or c_id.lower() == clean:
                return c_id, c_data
        for c_id, c_data in chars.items():
            if clean in c_data.get("name", "").lower() or clean in c_id.lower():
                return c_id, c_data
        return None, None
