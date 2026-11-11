"""
core/ttrpg_service.py - Universal System-Agnostic Tabletop Roleplaying Engine
Handles cryptographic dice parsing, character vitals mutations, combat initiative,
campaign editing, and DM/GM document grounding (rulesets, campaigns, character sheets).
"""
import os
import re
import json
import copy
import uuid
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

# Structured D&D 5e & Universal Roleplaying Conditions and Mechanical Impacts
CONDITION_EFFECTS: Dict[str, Dict[str, Any]] = {
    "Drunk": {
        "disadvantage_rolls": ["attack", "check"],
        "disadvantage_abilities": ["dexterity", "intelligence", "wisdom", "dex", "int", "wis"],
        "icon": "🍺",
        "description": "Disadvantage on attack rolls and ability checks (Dex/Int/Wis)."
    },
    "Intoxicated": {
        "disadvantage_rolls": ["attack", "check"],
        "disadvantage_abilities": ["dexterity", "intelligence", "wisdom", "dex", "int", "wis"],
        "icon": "🍺",
        "description": "Disadvantage on attack rolls and ability checks (Dex/Int/Wis)."
    },
    "Confused": {
        "disadvantage_rolls": ["check"],
        "disadvantage_abilities": ["intelligence", "wisdom", "int", "wis"],
        "no_reactions": True,
        "confusion_check": True,
        "icon": "🌀",
        "description": "Disadvantage on Int/Wis checks, cannot take reactions; rolls 1d10 confusion behavior."
    },
    "Poisoned": {
        "disadvantage_rolls": ["attack", "check"],
        "icon": "☠️",
        "description": "Disadvantage on attack rolls and ability checks."
    },
    "Frightened": {
        "disadvantage_rolls": ["attack", "check"],
        "icon": "😨",
        "description": "Disadvantage on ability checks and attack rolls while source of fear is in sight."
    },
    "Blinded": {
        "disadvantage_rolls": ["attack"],
        "icon": "🙈",
        "description": "Disadvantage on attack rolls; attacks against have advantage."
    },
    "Restrained": {
        "disadvantage_rolls": ["attack"],
        "icon": "🕸️",
        "description": "Speed 0; disadvantage on attack rolls and Dex saves."
    },
    "Prone": {
        "disadvantage_rolls": ["attack"],
        "icon": "🧎",
        "description": "Disadvantage on attack rolls; melee attacks within 5ft have advantage."
    },
    "Charmed": {
        "icon": "💖",
        "description": "Cannot attack the charmer or target them with harmful abilities/spells."
    },
    "Stunned": {
        "incapacitated": True,
        "icon": "💫",
        "description": "Incapacitated: cannot take actions/reactions, automatically fails Str/Dex saves."
    },
    "Paralyzed": {
        "incapacitated": True,
        "icon": "⚡",
        "description": "Incapacitated: cannot move or speak, automatically fails Str/Dex saves."
    },
    "Unconscious": {
        "incapacitated": True,
        "icon": "💤",
        "description": "Incapacitated: unaware, drops held items, falls prone, fails Str/Dex saves."
    },
    "Incapacitated": {
        "incapacitated": True,
        "icon": "🛑",
        "description": "Cannot take actions, bonus actions, or reactions."
    },
    "Petrified": {
        "incapacitated": True,
        "icon": "🗿",
        "description": "Transformed into solid stone; incapacitated, fails Str/Dex saves."
    },
    "Commanded: Halt": {
        "incapacitated": True,
        "icon": "🛑",
        "description": "Commanded to halt: cannot take actions or move on its turn."
    },
    "Dead": {
        "incapacitated": True,
        "dead": True,
        "icon": "💀",
        "description": "Deceased. Cannot take turns, actions, or rolls until revived."
    },
    "Stable": {
        "icon": "🩹",
        "description": "Stable at 0 HP. Unconscious, but does not roll death saving throws."
    }
}

LONG_RANGE_MAGIC_EXCEPTIONS = {
    "sending", "scrying", "teleport", "teleportation circle", "dream",
    "clairvoyance", "message", "telepathic bond", "astral projection", "gate",
    "project image", "commune", "contact other plane", "animal messenger"
}

MERCHANT_LOCATION_PRESETS: List[Dict[str, Any]] = [
    {"id": "village_general", "label": "Village General Store (1.0x Standard)", "multiplier": 1.0, "specialty": "general"},
    {"id": "city_bazaar", "label": "City Bazaar / Guild Market (0.9x Discount)", "multiplier": 0.9, "specialty": "general"},
    {"id": "faction_quartermaster", "label": "Faction / Allied Quartermaster (0.8x Ally Rate)", "multiplier": 0.8, "specialty": "weapons_armor"},
    {"id": "camp_outpost", "label": "Frontier / Camp Outpost (1.25x Markup)", "multiplier": 1.25, "specialty": "general"},
    {"id": "black_market", "label": "Shady Alley / Black Market (1.5x Markup)", "multiplier": 1.5, "specialty": "magic_poison"},
    {"id": "luxury_emporium", "label": "Arcane / Luxury Emporium (1.15x Fine Goods)", "multiplier": 1.15, "specialty": "arcane"},
]


class TTRPGService:
    def __init__(
        self,
        data_dir: str = "data/campaigns",
        manifest_path: str = "data/campaigns.json",
        client_getter: Optional[Any] = None,
        storage_dir: Optional[str] = None
    ):
        target_dir = storage_dir if storage_dir is not None else data_dir
        self.data_dir = os.path.abspath(target_dir)
        prod_data_dir = os.path.abspath("data/campaigns")
        if (storage_dir is not None or self.data_dir != prod_data_dir) and manifest_path == "data/campaigns.json":
            manifest_path = os.path.join(self.data_dir, "campaigns.json")
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
                        st_diff = st_data.get("difficulty")
                        if not st_diff:
                            cjson_path = os.path.join(entry_path, "campaign.json")
                            if os.path.exists(cjson_path):
                                try:
                                    with open(cjson_path, "r", encoding="utf-8") as cjf:
                                        st_diff = json.load(cjf).get("difficulty")
                                except Exception:
                                    pass
                        campaigns[entry] = {
                            "id": entry,
                            "title": entry.replace("_", " ").title(),
                            "system_preset": "dnd_5e",
                            "gm_mode": "interactive_narrator",
                            "difficulty": (st_diff or "helpful").strip().lower(),
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
                    self.active_state.setdefault("stored_items", [])
                    self.active_state.setdefault("entity_locations", {})

                    # Synchronize difficulty between manifest and active state
                    meta_diff = self.manifest.get("campaigns", {}).get(active_id, {}).get("difficulty")
                    state_diff = self.active_state.get("difficulty")
                    if state_diff and not meta_diff:
                        self.manifest.setdefault("campaigns", {}).setdefault(active_id, {})["difficulty"] = state_diff
                    elif meta_diff and not state_diff:
                        self.active_state["difficulty"] = meta_diff

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

                    # Ensure character vitals, spell slots, spatial location, and death saves are initialized
                    chars = self.active_state.get("characters", {})
                    entity_locs = self.active_state.setdefault("entity_locations", {})
                    for c in chars.values():
                        h = c.get("vitals", {}).get("health")
                        if h and isinstance(h, dict) and "max" in h and "current" in h:
                            h["max"] = max(1, int(h["max"]))
                            h["current"] = max(0, min(h["max"], int(h["current"])))
                        self.ensure_character_spell_slots(c)

                        # Spatial tracking and conditional defaults
                        c.setdefault("location", self.active_state.get("current_location", ""))
                        c.setdefault("is_separated", False)
                        c.setdefault("death_saves", {"successes": 0, "failures": 0, "status": "none"})
                        c.setdefault("conditions", [])
                        c_name = c.get("name")
                        if c_name:
                            entity_locs.setdefault(c_name, c.get("location") or self.active_state.get("current_location", ""))
                    self.clean_stale_combat_if_needed()
            except Exception as e:
                logger.error(f"[TTRPG] Error loading campaign state for '{active_id}': {e}")
                self.active_state = self._generate_default_state(active_id)
        else:
            self.active_state = self._generate_default_state(active_id)
            self.save_active_campaign_state()

    @property
    def active_campaign_id(self) -> Optional[str]:
        return self.manifest.get("active_campaign")

    @active_campaign_id.setter
    def active_campaign_id(self, val: Optional[str]):
        self.manifest["active_campaign"] = val

    def _ensure_active_state(self, campaign_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Guarantees active campaign state is loaded in memory, auto-loading from disk if needed."""
        cid = campaign_id or self.manifest.get("active_campaign")
        if not cid and self.manifest.get("campaigns"):
            cid = next(iter(self.manifest["campaigns"].keys()))
            self.manifest["active_campaign"] = cid

        if not self.active_state:
            if cid:
                self.manifest["active_campaign"] = cid
            self.load_active_campaign_state()
        elif campaign_id and self.active_state.get("campaign_id") != campaign_id:
            self.manifest["active_campaign"] = campaign_id
            self.load_active_campaign_state()

        return self.active_state

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
            unified_path = os.path.join(c_dir, "campaign_state.json")
            if os.path.exists(unified_path):
                try:
                    with open(unified_path, "r", encoding="utf-8") as uf:
                        unified = json.load(uf)
                    if isinstance(unified, dict):
                        unified["state"] = dict(self.active_state)
                        for k, v in self.active_state.items():
                            if k not in ("id", "title", "ruleset", "genre") or k not in unified:
                                unified[k] = v
                        with open(unified_path, "w", encoding="utf-8") as uf:
                            json.dump(unified, uf, indent=2)
                except Exception as ue:
                    logger.debug(f"[TTRPG] Could not sync campaign_state.json: {ue}")
        except Exception as e:
            logger.error(f"[TTRPG] Error saving campaign state: {e}")

    def _generate_default_state(self, campaign_id: str) -> Dict[str, Any]:
        diff = "helpful"
        if hasattr(self, "manifest") and isinstance(self.manifest, dict):
            diff = self.manifest.get("campaigns", {}).get(campaign_id, {}).get("difficulty", "helpful")
        return {
            "campaign_id": campaign_id,
            "difficulty": diff,
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
            "campaign_synopsis": {},
            "stored_items": []
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
        initial_files: Optional[List[Dict[str, Any]]] = None,
        module_adherence: str = "strict"
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

        clean_adh = str(module_adherence or "strict").lower().strip()
        if clean_adh in ("1", "creative"):
            adh_val = "creative"
        elif clean_adh in ("2", "balanced"):
            adh_val = "balanced"
        else:
            adh_val = "strict"

        meta = {
            "id": cid,
            "title": clean_title,
            "system_preset": system_preset or "dnd_5e",
            "gm_mode": gm_mode or "interactive_narrator",
            "setting_overview": setting_overview or "",
            "tone": tone or "Cinematic, grounded",
            "difficulty": difficulty or "helpful",
            "linked_project_id": linked_project_id or "",
            "module_adherence": adh_val,
            "party_count": 0,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
        campaigns[cid] = meta
        self.manifest["active_campaign"] = cid
        self.save_manifest()
        self.active_state = self._generate_default_state(cid)
        self.active_state["difficulty"] = difficulty or "helpful"
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
        dice_mode: Optional[str] = None,
        module_adherence: Optional[str] = None
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
            clean_diff = difficulty.strip().lower() or "helpful"
            meta["difficulty"] = clean_diff
            if self.active_state and (self.active_state.get("campaign_id") == cid or self.manifest.get("active_campaign") == cid):
                self.active_state["difficulty"] = clean_diff
                self.save_active_campaign_state()
            try:
                cjson_path = os.path.join(self.data_dir, cid, "campaign.json")
                if os.path.exists(cjson_path):
                    with open(cjson_path, "r", encoding="utf-8") as cjf:
                        cj_data = json.load(cjf)
                    cj_data["difficulty"] = clean_diff
                    with open(cjson_path, "w", encoding="utf-8") as cjf:
                        json.dump(cj_data, cjf, indent=2)
            except Exception as e:
                logger.debug(f"[TTRPG] Could not sync difficulty to campaign.json: {e}")
        if linked_project_id is not None:
            meta["linked_project_id"] = linked_project_id.strip()
        if dice_mode is not None:
            meta["dice_mode"] = "physical" if str(dice_mode).lower().strip() in ("physical", "irl", "tabletop", "manual") else "digital"
        if module_adherence is not None:
            clean_adh = str(module_adherence).lower().strip()
            if clean_adh in ("1", "creative"):
                meta["module_adherence"] = "creative"
            elif clean_adh in ("2", "balanced"):
                meta["module_adherence"] = "balanced"
            else:
                meta["module_adherence"] = "strict"

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
            self.active_state = None
        
        # Cleanly remove the campaign's isolated directory and all its files from disk
        c_dir = os.path.join(self.data_dir, campaign_id)
        if os.path.exists(c_dir):
            try:
                import shutil
                import stat

                def _force_rm(action, name, exc):
                    try:
                        os.chmod(name, stat.S_IWRITE)
                        os.remove(name)
                    except Exception:
                        pass

                shutil.rmtree(c_dir, onerror=_force_rm)
            except Exception as e:
                logger.warning(f"[TTRPG] Could not remove campaign directory '{c_dir}': {e}")

        self.save_manifest()
        if self.manifest.get("active_campaign"):
            self.load_active_campaign_state()
        else:
            self.active_state = None

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
            if v.endswith("\\"):
                v = v.rstrip("\\").strip()
            if v.endswith(")") and v.count("(") < v.count(")"):
                v = v[:-1].strip()
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
        cls_lvl = _clean(fields.get("CLASS  LEVEL") or fields.get("CLASS  LEVEL2") or fields.get("ClassLevel") or fields.get("Class & Level"))
        race = _clean(fields.get("RACE") or fields.get("RACE2"))
        bg = _clean(fields.get("BACKGROUND") or fields.get("BACKGROUND2"))

        # If race or background were missing but inside cls_lvl parenthetical (e.g. "Blood Hunter 1 (Sea Elf, Outlander)")
        if (not race or not bg) and "(" in cls_lvl and ")" in cls_lvl:
            p_m = re.search(r'\((.*?)\)', cls_lvl)
            if p_m:
                p_parts = [p.strip() for p in p_m.group(1).split(",")]
                if not race and len(p_parts) > 0:
                    race = p_parts[0]
                if not bg and len(p_parts) > 1:
                    bg = p_parts[1]
        clean_cls_lvl = re.sub(r'[\(\[].*?[\)\]]', '', cls_lvl).strip()
        archetype = clean_cls_lvl or "Adventurer"
        cls_lvl = clean_cls_lvl

        max_hp = _int_val(fields.get("MaxHP"), 25)
        cur_hp = _int_val(fields.get("CurrentHP"), max_hp)
        guard_val = _int_val(fields.get("AC"), 14)

        from core.parsers.spell_registry import resolve_dnd_stat
        might_val = resolve_dnd_stat(fields.get("STR"), fields.get("STRmod") or fields.get("STRmod ") or fields.get("STR Mod"), 10)
        agility_val = resolve_dnd_stat(fields.get("DEX"), fields.get("DEXmod") or fields.get("DEXmod ") or fields.get("DEX Mod"), 10)
        vigor_val = resolve_dnd_stat(fields.get("CON"), fields.get("CONmod") or fields.get("CONmod ") or fields.get("CON Mod"), 10)
        intellect_val = resolve_dnd_stat(fields.get("INT"), fields.get("INTmod") or fields.get("INTmod ") or fields.get("INT Mod"), 10)
        wis_val = resolve_dnd_stat(fields.get("WIS"), fields.get("WISmod") or fields.get("WISmod ") or fields.get("WIS Mod"), 10)
        cha_val = resolve_dnd_stat(fields.get("CHA"), fields.get("CHamod") or fields.get("CHAmod") or fields.get("CHAmod ") or fields.get("CHA Mod"), 10)
        presence_val = max(wis_val, cha_val)

        hit_dice = _clean(fields.get("Total", "1d8")) or "1d8"
        raw_speed = _clean(fields.get("Speed", "30 ft")) or "30 ft"
        clean_speed = re.sub(r'[\(\\\)]+', ' ', raw_speed).strip()

        # Parse level and experience points
        char_level = 1
        from core.parsers.dnd5e_parser import parse_multiclass_string
        class_entries = parse_multiclass_string(cls_lvl)
        if class_entries:
            char_level = max(1, sum(int(c.get("level", 1)) for c in class_entries))
        else:
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

        # Parse Equipment / Inventory (Eq Name0..Eq Name60, Eq Qty, Eq Weight)
        inventory: List[Dict[str, Any]] = []
        for eq_idx in range(60):
            eq_val = _clean(fields.get(f"Eq Name{eq_idx}") or fields.get(f"Eq Name {eq_idx}"))
            if eq_val:
                if "(" in eq_val and ")" not in eq_val:
                    eq_val += ")"
                eq_qty = _clean(fields.get(f"Eq Qty{eq_idx}") or fields.get(f"Eq Qty {eq_idx}")) or "1"
                eq_weight = _clean(fields.get(f"Eq Weight{eq_idx}") or fields.get(f"Eq Weight {eq_idx}"))
                lower_i = eq_val.lower()
                cat = "Gear"
                if any(w in lower_i for w in ["potion", "ration", "elixir", "scroll", "water", "food", "flask"]):
                    cat = "Consumable"
                elif any(w in lower_i for w in ["armor", "shield", "leather", "chain", "plate", "mail"]):
                    cat = "Armor"
                elif any(w in lower_i for w in ["sword", "dagger", "bow", "crossbow", "axe", "mace", "spear", "bolt", "arrow"]):
                    cat = "Weapon"
                elif any(w in lower_i for w in ["tool", "supplies", "kit", "lockpick", "thieves"]):
                    cat = "Tool"
                inventory.append({
                    "name": eq_val,
                    "qty": eq_qty,
                    "weight": eq_weight,
                    "category": cat,
                    "notes": ""
                })
        raw_eq_area = _clean(fields.get("Equipment") or fields.get("Inventory"))
        if raw_eq_area:
            existing_names = {x["name"] if isinstance(x, dict) else str(x) for x in inventory}
            for line in raw_eq_area.splitlines():
                item_str = line.strip().lstrip("-•* ")
                if item_str:
                    if "(" in item_str and ")" not in item_str:
                        item_str += ")"
                    if item_str not in existing_names:
                        inventory.append({
                            "name": item_str,
                            "qty": "1",
                            "weight": "",
                            "category": "Gear",
                            "notes": ""
                        })
                        existing_names.add(item_str)

        from core.parsers.dnd5e_parser import sanitize_inventory, clean_traits_and_headers, normalize_spell_level
        inventory = sanitize_inventory(inventory)

        # Encumbrance & Weights (Page 2 Equipment footer)
        weight_carried = _clean(fields.get("Weight Carried") or fields.get("WeightCarried") or "")
        encumbered = _clean(fields.get("Encumbered") or "")
        push_drag_lift = _clean(fields.get("PushDragLift") or fields.get("Push/Drag/Lift") or "")
        if not encumbered and might_val:
            encumbered = f"{might_val * 15} lb."
        if not push_drag_lift and might_val:
            push_drag_lift = f"{might_val * 30} lb."

        # Physical Appearance & Character Description (Page 3 Header)
        char_gender = _clean(fields.get("GENDER") or fields.get("Gender") or "")
        char_age = _clean(fields.get("AGE") or fields.get("Age") or "")
        char_size = _clean(fields.get("SIZE") or fields.get("Size") or "")
        char_height = _clean(fields.get("HEIGHT") or fields.get("Height") or "")
        char_weight = _clean(fields.get("WEIGHT") or fields.get("Weight") or "")
        char_alignment = _clean(fields.get("ALIGNMENT") or fields.get("Alignment") or "")
        char_faith = _clean(fields.get("FAITH") or fields.get("Faith") or "")
        char_skin = _clean(fields.get("SKIN") or fields.get("Skin") or "")
        char_eyes = _clean(fields.get("EYES") or fields.get("Eyes") or "")
        char_hair = _clean(fields.get("HAIR") or fields.get("Hair") or "")

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
        # Stitch FeaturesTraits1 and FeaturesTraits2 together so wrapped column blocks (e.g. Creature Type -> You are a Humanoid.) connect seamlessly
        ft1 = str(fields.get("FeaturesTraits1") or "").strip()
        ft2 = str(fields.get("FeaturesTraits2") or "").strip()
        combined_ft = f"{ft1}\n{ft2}".strip() if (ft1 or ft2) else ""

        raw_feat_sources = [
            combined_ft,
            fields.get("Features and Traits"),
            fields.get("Features and Traits2"),
            fields.get("Features_and_Traits"),
            fields.get("FeaturesTraits"),
            fields.get("Features"),
            fields.get("Feats"),
            fields.get("Traits"),
            fields.get("Special Abilities")
        ]

        # Determine Creature Type (e.g. "Humanoid", "Fey", "Monstrosity")
        creature_type = ""
        combined_text_lower = f"{combined_ft}\n{race}\n{cls_lvl}".lower()
        if "you are a humanoid" in combined_text_lower or "creature type: humanoid" in combined_text_lower:
            creature_type = "Humanoid"
        elif "humanoid" in combined_text_lower:
            creature_type = "Humanoid"
        elif any(r in race.lower() for r in ["aasimar", "human", "elf", "dwarf", "gnome", "halfling", "tiefling", "dragonborn", "orc"]):
            creature_type = "Humanoid"

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

        features_list = clean_traits_and_headers(features_list)

        # Skills & Proficiencies
        prof_bonus_int = _int_val(fields.get("ProfBonus"), 2)
        str_mod = (might_val - 10) // 2
        dex_mod = (agility_val - 10) // 2
        con_mod = (vigor_val - 10) // 2
        int_mod = (intellect_val - 10) // 2
        wis_mod = (wis_val - 10) // 2
        cha_mod = (cha_val - 10) // 2

        dnd_skill_stats = {
            "Athletics": might_val,
            "Acrobatics": agility_val,
            "Sleight of Hand": agility_val,
            "Stealth": agility_val,
            "Arcana": intellect_val,
            "History": intellect_val,
            "Investigation": intellect_val,
            "Nature": intellect_val,
            "Religion": intellect_val,
            "Animal Handling": wis_val,
            "Insight": wis_val,
            "Medicine": wis_val,
            "Perception": wis_val,
            "Survival": wis_val,
            "Deception": cha_val,
            "Intimidation": cha_val,
            "Performance": cha_val,
            "Persuasion": cha_val
        }

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
            raw_v = fields.get(s_key) or fields.get(s_label)
            s_val = _clean(raw_v)

            # Check explicit proficiency markers in AcroForm fields (e.g. ArcanaProf: 'P', InsightProf: 'P')
            prof_marker = (
                fields.get(f"{s_key}Prof")
                or fields.get(f"{s_label}Prof")
                or fields.get(f"{s_key.replace(' ', '')}Prof")
                or fields.get(f"{s_label.replace(' ', '')}Prof")
            )
            is_prof = False
            if prof_marker:
                p_str = str(prof_marker).strip().lower()
                if p_str and p_str not in ("off", "no", "false", "0", "none"):
                    is_prof = True

            clean_num_str = re.sub(r'\(.*?\)', '', s_val).strip()
            num_match = re.search(r'[-+]?\d+', clean_num_str)
            if num_match:
                s_num = int(num_match.group(0))
                base_ability = dnd_skill_stats.get(s_label, 10)
                base_mod = (base_ability - 10) // 2
                if prof_bonus_int > 0 and s_num >= base_mod + prof_bonus_int:
                    is_prof = True
            elif not s_val:
                base_ability = dnd_skill_stats.get(s_label, 10)
                base_mod = (base_ability - 10) // 2
                s_val = f"+{base_mod}" if base_mod >= 0 else str(base_mod)

            if "(proficient)" in s_val.lower():
                is_prof = True
                s_val = re.sub(r'(?i)\s*\(proficient\)', '', s_val).strip()

            if is_prof:
                skills_dict[s_label] = f"{s_val} (Proficient)"
            elif s_val:
                skills_dict[s_label] = s_val

        # Spells & Cantrips (Structured parsing with level, DC, damage formulas, and header filtering)
        from core.parsers.spell_registry import parse_structured_spells, sanitize_character_weapons
        from core.parsers.dnd5e_parser import ALWAYS_PREPARED_SUBCLASS_SPELLS, WARLOCK_PACT_SLOTS
        spells_list = parse_structured_spells(fields)
        is_known_caster = any(k in cls_lvl.lower() for k in ("warlock", "sorcerer", "bard"))

        active_subclass = ""
        sub_ctx = f"{combined_ft}\n{cls_lvl}\n{fields.get('ClassLevel', '')}".lower()
        for sc in ALWAYS_PREPARED_SUBCLASS_SPELLS.keys():
            if sc in ("devotion", "vengeance", "ancients") and sc in sub_ctx:
                active_subclass = sc
                break
            elif sc == "land" and ("druid" in cls_lvl.lower() or "circle" in sub_ctx) and sc in sub_ctx:
                active_subclass = sc
                break
            elif sc in ("life", "light") and ("cleric" in cls_lvl.lower() or "domain" in sub_ctx) and sc in sub_ctx:
                active_subclass = sc
                break
        always_prep_set = set(ALWAYS_PREPARED_SUBCLASS_SPELLS.get(active_subclass, []))

        seen_spells = set()
        deduped_spells = []
        for s in spells_list:
            if isinstance(s, dict):
                s_name = str(s.get("name", "")).strip()
                norm_key = re.sub(r'\s*(?:\[R\]|<C>)', '', s_name, flags=re.IGNORECASE).strip().lower()
                if not norm_key or norm_key in seen_spells:
                    continue
                seen_spells.add(norm_key)
                raw_lvl = str(s.get("level", "")).lower()
                s["level"] = normalize_spell_level(s.get("level"))
                is_cantrip = (s["level"] == 0) or "cantrip" in raw_lvl or raw_lvl == "0" or any(c in norm_key for c in ["light", "guidance", "mending", "prestidigitation", "shocking grasp", "sorcerous burst", "fire bolt", "eldritch blast", "druidcraft", "sacred flame", "thorn whip", "toll the dead"])
                is_subclass_oath = norm_key in always_prep_set
                s["is_cantrip"] = is_cantrip
                s["is_ritual"] = bool(s.get("is_ritual", False) or "[r]" in s_name.lower())
                s["is_always_prepared"] = is_subclass_oath
                s["prepared"] = True if (is_cantrip or is_subclass_oath or is_known_caster) else False
                deduped_spells.append(s)
        spells_list = deduped_spells
        weapons = sanitize_character_weapons(weapons, spells_list)

        spell_ability = _clean(fields.get("spellCastingAbility0") or fields.get("SpellcastingAbility") or fields.get("SpellAbility") or "")
        spell_save_dc = _clean(fields.get("spellSaveDC0") or fields.get("SpellSaveDC") or "")
        spell_atk_bonus = _clean(fields.get("spellAtkBonus0") or fields.get("SpellAttackBonus") or fields.get("SpellAtkBonus") or "")

        # Roleplay / Narrative Background & Backstory
        from core.parsers.dnd5e_parser import clean_backstory_text

        raw_bs = clean_backstory_text(fields.get("Backstory") or fields.get("BackgroundHistory") or "")
        raw_an1 = clean_backstory_text(fields.get("AdditionalNotes1") or "")
        raw_an2 = clean_backstory_text(fields.get("AdditionalNotes2") or fields.get("AdditionalNotes") or "")

        # If Backstory was wrapped across D&D Beyond columns into AdditionalNotes1 and AdditionalNotes2:
        if raw_an1 and ("nights, she" in raw_an1 or not fields.get("Notes")):
            full_backstory = " ".join(filter(None, [raw_bs, raw_an1, raw_an2])).strip()
            additional_notes = ""
        else:
            full_backstory = raw_bs
            additional_notes = "\n\n".join(filter(None, [raw_an1, raw_an2])).strip()

        personality = clean_backstory_text(fields.get("PersonalityTraits ") or fields.get("PersonalityTraits") or fields.get("Personality") or "")
        ideals = clean_backstory_text(fields.get("Ideals") or "")
        bonds = clean_backstory_text(fields.get("Bonds") or "")
        flaws = clean_backstory_text(fields.get("Flaws") or "")
        appearance = clean_backstory_text(fields.get("Appearance") or "")
        allies = clean_backstory_text(fields.get("AlliesOrganizations") or fields.get("Allies") or "")

        roleplay_anchors = {
            "personality_traits": personality,
            "ideals": ideals,
            "bonds": bonds,
            "flaws": flaws,
            "allies_and_organizations": allies
        }

        # Spell Slots Extraction
        spell_slots = {}
        is_warlock = "warlock" in cls_lvl.lower()
        is_pact_magic = is_warlock or any("pact" in str(fields.get(f"spellSlotHeader{i}") or "").lower() for i in range(12))

        for rank in range(1, 10):
            total_slots = _int_val(fields.get(f"SlotsTotal{rank}") or fields.get(f"SlotsTotal_{rank}") or fields.get(f"SpellSlots_{rank}_Total"), 0)
            if total_slots == 0:
                for h_idx in range(12):
                    hdr = str(fields.get(f"spellHeader{h_idx}") or "")
                    slot_hdr = str(fields.get(f"spellSlotHeader{h_idx}") or "")
                    if re.search(rf'{rank}(?:st|nd|rd|th)\s+LEVEL', hdr, re.IGNORECASE):
                        m_s = re.search(r'(\d+)\s+(?:Slots?|Pact)', slot_hdr, re.IGNORECASE)
                        if m_s:
                            total_slots = int(m_s.group(1))
                            break
            if total_slots == 0 and is_pact_magic:
                pact_info = WARLOCK_PACT_SLOTS.get(min(20, max(1, char_level)), {"count": 1, "level": 1})
                if rank == pact_info["level"]:
                    total_slots = _int_val(fields.get("PactMagic_SlotsTotal") or fields.get("SlotsPactTotal") or fields.get("SlotsTotalPact"), pact_info["count"])

            cur_slots = _int_val(fields.get(f"SlotsRemaining{rank}") or fields.get(f"SlotsRemaining_{rank}"), total_slots)
            slot_entry = {"current": cur_slots, "max": total_slots}
            if is_pact_magic and total_slots > 0:
                slot_entry["is_pact_magic"] = True
                slot_entry["reset_on"] = "short_rest"
            else:
                slot_entry["reset_on"] = "long_rest"
            spell_slots[f"level_{rank}"] = slot_entry

        from core.parsers.dnd5e_parser import DnD5eParser
        all_features_str = " ".join(features_list)
        custom_meters = DnD5eParser().extract_discrete_meters(all_features_str, cls_lvl, char_level, int_mod, wis_mod, cha_mod)

        cls_check = cls_lvl.lower()
        key_spell_mod = (
            cha_mod if any(c in cls_check for c in ("sorcerer", "bard", "warlock", "paladin"))
            else (wis_mod if any(c in cls_check for c in ("cleric", "druid", "ranger")) else int_mod)
        )
        max_prep_spells = DnD5eParser._calculate_max_prepared_spells(cls_lvl, char_level, key_spell_mod)
        has_slots = any(s.get("max", 0) > 0 for s in spell_slots.values()) if isinstance(spell_slots, dict) else False
        if not has_slots:
            max_prep_spells = 0

        # Saving Throws
        save_stat_map = [
            ("Strength", "Str", "STR", str_mod),
            ("Dexterity", "Dex", "DEX", dex_mod),
            ("Constitution", "Con", "CON", con_mod),
            ("Intelligence", "Int", "INT", int_mod),
            ("Wisdom", "Wis", "WIS", wis_mod),
            ("Charisma", "Cha", "CHA", cha_mod),
        ]
        saving_throws = {}
        for s_name, s_abbr, s_upper, b_mod in save_stat_map:
            raw_st = fields.get(f"ST {s_name}") or fields.get(f"ST {s_abbr}")
            prof_save_marker = fields.get(f"{s_abbr}Prof") or fields.get(f"{s_name}Prof")
            is_save_prof = False
            if prof_save_marker:
                p_str = str(prof_save_marker).strip().lower()
                if p_str and p_str not in ("off", "no", "false", "0", "none"):
                    is_save_prof = True
            if raw_st:
                val_clean = _clean(raw_st)
                m_st = re.search(r'[-+]?\d+', val_clean)
                st_num = int(m_st.group(0)) if m_st else b_mod
                if prof_bonus_int > 0 and st_num >= b_mod + prof_bonus_int:
                    is_save_prof = True
                saving_throws[s_name] = val_clean
                saving_throws[s_upper] = val_clean
            else:
                total_mod = b_mod + (prof_bonus_int if is_save_prof else 0)
                saving_throws[s_name] = f"{total_mod:+d}"
                saving_throws[s_upper] = f"{total_mod:+d}"

        inspiration = str(fields.get("Inspiration", "")).strip().lower() in ("yes", "on", "1", "true")

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
            "class_and_level": cls_lvl,
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
            "weight_carried": weight_carried,
            "encumbered": encumbered,
            "push_drag_lift": push_drag_lift,
            "gender": char_gender,
            "age": char_age,
            "size": char_size,
            "height": char_height,
            "weight": char_weight,
            "alignment": char_alignment,
            "faith": char_faith,
            "skin": char_skin,
            "eyes": char_eyes,
            "hair": char_hair,
            "creature_type": creature_type,
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
            "ability_save_dc": _int_val(spell_save_dc, 8 + prof_bonus_int + key_spell_mod),
            "is_pact_magic": is_pact_magic,
            "inspiration": inspiration,
            "spell_slots": spell_slots,
            "max_prepared_leveled_spells": max_prep_spells,
            "attuned_magic_items": {"current": 0, "max": 3},
            "roleplay_anchors": roleplay_anchors,
            "backstory": full_backstory,
            "additional_notes": additional_notes,
            "personality_traits": personality,
            "ideals": ideals,
            "bonds": bonds,
            "flaws": flaws,
            "appearance": appearance,
            "allies": allies,
            "allies_and_organizations": allies
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
  "species": "<race or species, e.g. 'Sea Elf', 'Human'>",
  "background": "<background, e.g. 'Outlander', 'Noble'>",
  "saving_throws": {"Strength": "<bonus>", "Dexterity": "<bonus>", "Constitution": "<bonus>", "Intelligence": "<bonus>", "Wisdom": "<bonus>", "Charisma": "<bonus>"},
  "backstory": "<full character backstory or personal history narrative>",
  "personality_traits": "<personality traits>",
  "ideals": "<ideals>",
  "bonds": "<bonds>",
  "flaws": "<flaws>",
  "roleplay_anchors": {
     "personality_traits": "<traits>",
     "ideals": "<ideals>",
     "bonds": "<bonds>",
     "flaws": "<flaws>",
     "allies_and_organizations": "<allies>"
  },
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
                            arch = data.get("archetype") or ""
                            if (not data.get("species") or not data.get("background")) and "(" in arch and ")" in arch:
                                p_m = re.search(r'\((.*?)\)', arch)
                                if p_m:
                                    p_parts = [p.strip() for p in p_m.group(1).split(",")]
                                    if not data.get("species") and len(p_parts) > 0:
                                        data["species"] = p_parts[0]
                                    if not data.get("background") and len(p_parts) > 1:
                                        data["background"] = p_parts[1]
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

        # 1. Try high-fidelity DnD5eParser for D&D 5e PDFs, then fallback to AcroForm widgets
        if safe_name.lower().endswith(".pdf") and raw_bytes:
            try:
                from core.parsers.dnd5e_parser import DnD5eParser
                dnd_parser = DnD5eParser()
                form_fields = ProjectService.extract_pdf_form_fields(raw_bytes)
                if dnd_parser.identify_system(form_fields, ""):
                    char_data = dnd_parser.parse_sheet(raw_bytes)
                    if char_data:
                        results.append(_apply_override(char_data))
                        return results
            except Exception as e:
                logger.warning(f"[TTRPG] DnD5eParser failed, falling back to legacy form parser: {e}")

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
            arch_str = str(ch_payload.get("class_and_level") or ch_payload.get("archetype") or "").strip()
            if "/" in arch_str:
                from core.parsers.dnd5e_parser import parse_multiclass_string
                m_entries = parse_multiclass_string(arch_str)
                if len(m_entries) > 1:
                    sheet_level = max(sheet_level, sum(int(c.get("level", 1)) for c in m_entries))
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
                inv_list = ch_payload["inventory"]
                cleaned_inv = []
                for itm in inv_list:
                    if isinstance(itm, str):
                        s = itm.strip()
                        if "(" in s and ")" not in s:
                            s += ")"
                        cleaned_inv.append(s)
                    elif isinstance(itm, dict):
                        inm = str(itm.get("name") or "").strip()
                        if "(" in inm and ")" not in inm:
                            itm["name"] = inm + ")"
                        cleaned_inv.append(itm)
                    else:
                        cleaned_inv.append(str(itm))
                existing["inventory"] = cleaned_inv
                existing["live_inventory"] = [
                    (x if isinstance(x, dict) else {"name": x, "category": "utility", "genre": existing.get("ruleset", "fantasy_d20"), "status": "active"})
                    for x in cleaned_inv
                ]
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
                "weight_carried", "encumbered", "push_drag_lift", "gender", "age", "size", "height", "weight", "alignment", "faith", "skin", "eyes", "hair",
                "creature_type", "backstory", "additional_notes",
                "personality_traits", "ideals", "bonds", "flaws", "appearance", "allies"
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

    def import_character_to_campaign(self, vault_char_data: Dict[str, Any], overwrite_existing: bool = False) -> Dict[str, Any]:
        """Strictly-validated ingestion of a character sheet into the active campaign."""
        return self.import_character_to_active_campaign(vault_char_data, overwrite_existing=overwrite_existing)

    def import_character_to_active_campaign(self, vault_char_data: Dict[str, Any], overwrite_existing: bool = False) -> Dict[str, Any]:
        """
        ONE-WAY INGESTION: Clones a character sheet from the Vault directly into the
        active campaign's persistent state.json. Severing the template link ensures
        live in-game updates (HP loss, spent spell slots) NEVER reset on UI refresh.
        """
        if not isinstance(vault_char_data, dict):
            raise ValueError("Character payload must be a dictionary.")

        # Strict Schema 2.1 Gatekeeper Validation
        from core.schema.validator import validate_character_payload
        from core.schema.character_schema import CharacterDocument
        valid, errors, doc = validate_character_payload(vault_char_data)
        if not valid:
            logger.warning(f"[TTRPG] Character payload failed Schema 2.1 validation: {errors}")
            CharacterDocument.model_validate(vault_char_data)

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
        if not self._ensure_active_state():
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
                c_clean = str(c).strip()
                if not c_clean:
                    continue
                c_low = c_clean.lower()
                is_duplicate = False
                for ec in existing_clues:
                    ec_low = str(ec).strip().lower()
                    if c_low == ec_low or (len(c_low) > 15 and (c_low in ec_low or ec_low in c_low)):
                        is_duplicate = True
                        break
                if not is_duplicate:
                    existing_clues.append(c_clean)

        # Auto-reconcile confiscated items if clues/summary reveal their location
        stored_list = self.active_state.get("stored_items", [])
        if stored_list and any(it.get("location_knowledge") in (None, "unknown") for it in stored_list):
            combined_texts = " ".join(clues_list + [clean_summary, last_action or ""])
            is_gear_located = bool(
                re.search(r'(?:confiscated|stored|party\'?s?)\s+(?:weapons?|gear|belongings?|items?|equipment|blade|swords?)[^.\n]*?(?:stored|held|located|spotted|inside|in|seen)\s+', combined_texts, re.IGNORECASE) or
                re.search(r'(?:spotting|spotted|located|found|seeing|saw)\s+(?:their|the\s+party\'?s?)\s+(?:confiscated\s+)?(?:gear|weapons?|belongings?|blade|blackrazor)', combined_texts, re.IGNORECASE) or
                re.search(r'(?:gear|weapons?|blackrazor).*(?:held|stored|kept)\s+(?:in|inside)\s+', combined_texts, re.IGNORECASE)
            )
            if is_gear_located:
                loc_match = re.search(
                    r'(?:stored|held|located|spotted|inside|in|kept\s+in)\s+([^,.;\n]+(?:chest|barracks|locker|room|station|cell|office|box))',
                    combined_texts,
                    re.IGNORECASE
                )
                revealed_loc = ""
                if loc_match:
                    revealed_loc = re.sub(r'^(?:inside\s+|in\s+)?(?:a|the|an)\s+', '', loc_match.group(1).strip(), flags=re.IGNORECASE).capitalize()

                clue_excerpt = next(
                    (c for c in clues_list if any(w in c.lower() for w in ("weapon", "gear", "chest", "blade", "corridor", "station", "barracks"))),
                    (last_action if last_action and any(w in last_action.lower() for w in ("weapon", "gear", "perception", "locate")) else clean_summary[:120])
                )
                updated_count = 0
                for it in stored_list:
                    if it.get("location_knowledge") in (None, "unknown") and it.get("status") != "destroyed":
                        it["location_knowledge"] = "discovered"
                        if revealed_loc and len(revealed_loc) > 3:
                            it["storage_location"] = revealed_loc
                        if clue_excerpt:
                            it["discovery_clue"] = clue_excerpt
                        updated_count += 1
                if updated_count > 0:
                    logger.info(f"[TTRPG] Auto-reconciled {updated_count} stored item(s) to 'discovered' from campaign journal/clues: loc='{revealed_loc}'.")

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
        if existing_synopsis and isinstance(existing_synopsis, dict) and existing_synopsis.get("setting") and existing_synopsis.get("starting_scene") and existing_synopsis.get("acts_and_scenes"):
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
            # Send up to 100,000 characters so full multi-act adventure modules are completely analyzed
            module_text = full_text[:100000]
            prompt = f"""You are an expert tabletop RPG adventure module analyzer.
Analyze the following comprehensive text from the published adventure module:
{module_text}

Extract the complete campaign structure, all acts and scenes, encounters, monsters/bosses, and encounter tables into valid JSON with this schema:
{{
  "module_title": "<module name and author>",
  "setting": "<geographic setting and realm>",
  "primary_hub": "<starting settlement / base of operations>",
  "key_npcs": [
    {{"name": "<NPC name>", "role": "<NPC role, allegiance, and stats/importance>"}}
  ],
  "target_or_threat": "<antagonist or primary target of the adventure>",
  "core_mission": "<the central quest and objectives>",
  "opening_hook": "<how the adventure begins>",
  "synopsis": "<3-4 sentence comprehensive premise of the module>",
  "starting_scene": {{
    "scene_name": "<exact scene heading, e.g. 'Act 1: Prologue — Scene 1: The Beginning'>",
    "location": "<exact location from module>",
    "boxed_text": "<verbatim boxed narration text intended for the DM to read to players upon entering the scene>",
    "active_npc": "<name and stat/personality details of the NPC present in this scene>",
    "quest_briefing": "<what the quest-giver explains to the party>",
    "immediate_choices": ["<choice 1>", "<choice 2>", "<choice 3>", "<choice 4>"]
  }},
  "acts_and_scenes": [
    {{
      "act": "<Act name and title>",
      "scenes": [
        {{
          "scene_name": "<Scene name and number>",
          "location": "<Location>",
          "boxed_text": "<Boxed descriptive text>",
          "challenges": "<DCs, hazards, enemies, or exploration challenges>"
        }}
      ]
    }}
  ],
  "encounter_tables": {{
    "<table_name>": {{"<roll>": "<encounter description>"}}
  }},
  "monsters_and_bosses": {{
    "<monster_name>": "<AC, HP, attack bonuses, special abilities, spells>"
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
                "primary_hub": "Camp Highmore (Walled trading outpost in the Chult wilderness)" if "elfhunt" in module_file.lower() else "Adventure Outpost",
                "key_npcs": [
                    {"name": "High Chief Highmore", "role": "Ruler of Camp Highmore, fair but besieged by plotting chieftains"},
                    {"name": "Chief Filip", "role": "Chief of the Tribe of the Moonweavers, overseer of camp military & guards. Conspirator married to Shandra. (CR 5 Boss: AC 17 half plate, HP 157, Greataxe +7, Leap Attack, Whirlwind, Call for Help)"},
                    {"name": "Chief Shandra", "role": "Chief of the Tribe of the Red Ravens, diplomacy & politics. Filip's secret wife and co-conspirator. Greedy, manipulative."},
                    {"name": "Johan Petry", "role": "Retired veteran adventurer (Tribe of the Black Phoenix). Infatuated with Shandra, hired the party under false pretenses."},
                    {"name": "Tane Here", "role": "Leader of the chaotic rogues / thieves' guild in Camp Highmore opposing Filip's tyranny"},
                    {"name": "Imis Elvan", "role": "Wood elf illusionist who uncovered Filip & Shandra's coup and fled into the jungle. (CR 8: AC 15, HP 192, Mirror Image, Misty Step, Cone of Cold, Thunderwave)"},
                    {"name": "Habrision and Torcassa", "role": "Wood elf scouts stationed near the camp gate who assist with tracking"}
                ] if "elfhunt" in module_file.lower() else [],
                "target_or_threat": "Chief Filip and Chief Shandra (secret schemers framing the party); Imis Elvan (initial bounty target)",
                "core_mission": "Investigate the theft of military secrets, track Imis Elvan through the Chultan jungle into the Feywild labyrinth, and uncover the coup plot against Camp Highmore.",
                "opening_hook": "The party is invited to Johan Petry's house in Camp Highmore, who hires them to track down Imis Elvan.",
                "synopsis": (
                    "In the wilderness trade hub of Camp Highmore, Chief Filip and Chief Shandra plot to overthrow High Chief Highmore during the upcoming harvest festival. "
                    "When wood elf illusionist Imis Elvan discovers their conspiracy and flees into the Chultan jungle with incriminating evidence, the schemers manipulate "
                    "Johan Petry into hiring an adventuring party to hunt her down under false charges of military theft. The hunt leads through dangerous jungle beasts, "
                    "ancient dryad-guarded fey portals, and a minotaur-defended hedge labyrinth, culminating in a climactic confrontation with Chief Filip."
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
                } if "elfhunt" in module_file.lower() else {},
                "acts_and_scenes": [
                    {
                        "act": "Act 1: Prologue (Camp Highmore)",
                        "scenes": [
                            {
                                "scene_name": "Scene 1: The Beginning",
                                "location": "Johan Petry's House",
                                "boxed_text": "The large single-room hall has walls lined with monster trophies. Johan Petry hires the party to track down wood elf Imis Elvan.",
                                "challenges": "Roleplay with Johan Petry; contract negotiation."
                            },
                            {
                                "scene_name": "Scene 2: Meeting at the House of Chiefs",
                                "location": "House of Chiefs, Camp Highmore",
                                "boxed_text": "A bustling tribal meeting hall where Chief Shandra holds audience.",
                                "challenges": "DC 15 Insight to perceive Shandra's deceit and hidden alliance with Filip."
                            },
                            {
                                "scene_name": "Scene 3: Investigation at the Barracks",
                                "location": "Guard Barracks, Camp Highmore",
                                "boxed_text": "A disciplined military compound guarded by Highmore soldiers. Commander Filip oversees drills.",
                                "challenges": "Investigating the office crime scene or questioning Chief Filip. CRITICAL MANDATORY TRIGGER: Questioning Chief Filip or investigating the barracks triggers Act 2 Scene 1 (The Arrest) in the very next scene! Filip and Johan frame the party to protect their coup. You CANNOT let the party reach the jungle without running the arrest encounter!"
                            }
                        ]
                    },
                    {
                        "act": "Act 2: Conflict (The Frame-Up)",
                        "scenes": [
                            {
                                "scene_name": "Scene 1: The Arrest",
                                "location": "Camp Highmore Streets / En Route to Gate",
                                "boxed_text": "A dozen guards come marching up towards you in a stern, orderly fashion with spears leveled and crossbows held at the ready. Chief Filip steps forward, his half plate gleaming: 'You halt! By order of the council, you are under arrest for aiding and abetting in the theft of secret military information. Drop your weapons and surrender, or we will use force!'",
                                "challenges": "Mandatory Encounter: Arrest by Chief Filip, 2 knights, and 11 Highmore guards. Party choices: 1. Surrender -> taken to underground dungeon (Act 2 Scene 2); 2. Fight -> knocked unconscious at 0 HP, stripped of weapons, wake up in dungeon; 3. Flee -> chase through camp, rescued into sewers by Tane Here (Act 3 Scene 1). The party CANNOT bypass to the jungle!"
                            },
                            {
                                "scene_name": "Scene 2: The Prison & Trial",
                                "location": "Camp Stockade / Tribal Court",
                                "boxed_text": "Dark log holding cells beneath the stockade where political prisoners wait.",
                                "challenges": "Skill checks to escape or diversion by Tane Here's rogues."
                            }
                        ]
                    },
                    {
                        "act": "Act 3: Rising Action (The Underground Resistance)",
                        "scenes": [
                            {
                                "scene_name": "Scene 1: Thieves' Hideout",
                                "location": "Hidden Cellar beneath Camp Highmore",
                                "boxed_text": "A torch-lit cellar smelling of ale and damp stone. Tane Here sits on a wooden crate polishing a dagger.",
                                "challenges": "Social interaction with Tane Here; learning Filip and Shandra's harvest festival coup plot."
                            },
                            {
                                "scene_name": "Scene 4: Stealth Escape",
                                "location": "Camp Highmore Palisades / River Drain",
                                "boxed_text": "A narrow drainage canal sneaking under the spiked wooden palisades into the jungle beyond.",
                                "challenges": "DC 14 Stealth check to slip past perimeter patrols into the Chult wilderness."
                            }
                        ]
                    },
                    {
                        "act": "Act 4: Action (The Hunt through Chult & Feywild)",
                        "scenes": [
                            {
                                "scene_name": "Scene 1: The Gate",
                                "location": "Main Gate Outskirts, Camp Highmore",
                                "boxed_text": "A well-travelled path leads away from the wooden gate flanked by wooden beams that make up the camp's palisades. Nearly a hundred feet of jungle has been cleared from the camp's boundary. The path winds into the rocky jungle covered in vines and large green trees of various sorts. Danger looms beyond but your search for the elf begins here.",
                                "challenges": "DC 20 Perception/Investigation to find torn cloth and elven brooch; DC 20 Survival to identify Imis's trail into the jungle. Wood elf scouts Habrision and Torcassa assist if needed."
                            },
                            {
                                "scene_name": "Scene 2: Jungle Animals (Wilderness Hazards)",
                                "location": "Dense Jungles of Chult",
                                "boxed_text": "The jungle canopy closes overhead like a dense emerald dome, shutting out the sun. Humid air clings to your skin as the sounds of exotic birds, buzzing insects, and distant predator calls echo through the ancient trees.",
                                "challenges": "Mandatory Jungle Hazard Encounter (roll 2d6): 2=Treant, 3=Faerie Dragons, 4=Harpies ambush from canopy, 5=Gnoll hunting pack, 6=Stalking jungle wolves, 7=Territorial ape troop, 8=Black bears, 9=Hippogriffs, 10=Ogre pair, 11=Chimera ambush (CR 6), 12=Guardian Naga."
                            },
                            {
                                "scene_name": "Scene 3: Elven Tracks & River Crossing",
                                "location": "Chultan Riverbank & Campsite",
                                "boxed_text": "A rushing jungle river cuts across the terrain, spanned only by a mossy fallen log bridge. Across the water, partially masked beneath broad palm fronds, lies a small abandoned campsite.",
                                "challenges": "DC 12 Acrobatics/Athletics to cross slippery log; DC 15 Survival to inspect campsite (finds discarded bandages and fresh tracks heading toward elven ruins)."
                            },
                            {
                                "scene_name": "Scene 4: Gatekeepers (The Fey Portal)",
                                "location": "Ancient Overgrown Stone Archway",
                                "boxed_text": "Partially swallowed by massive roots and flowering vines stands an ancient elven stone archway. A gentle, shimmering vibration in the air suggests a portal to another plane. Suddenly, the trees around you rustle with unnatural hostility as five lithe, bark-skinned figures emerge from the trunks.",
                                "challenges": "Combat Encounter: 5 Dryads guard this portal to the Feywild! They challenge intruders, casting Barkskin (AC 16, 22 HP), Shillelagh (+4 to hit, 1d8+2 magic bludgeoning), Tree Stride, and Entangle (DC 14 STR save)."
                            },
                            {
                                "scene_name": "Scene 5: Feywild Transition",
                                "location": "The Feywild Threshold",
                                "boxed_text": "Passing through the shimmering arch, the stifling heat of Chult vanishes in an instant, replaced by a crisp, cool twilight breeze. The sky above is locked in eternal purple-and-gold dusk, and the flora hums with soft, iridescent light.",
                                "challenges": "Feywild Crossing effect: Each player must make a DC 10 Wisdom saving throw or lose memory of time spent in the Feywild upon leaving."
                            },
                            {
                                "scene_name": "Scene 6: Land of the Fey",
                                "location": "Twilight Forest of the Feywild",
                                "boxed_text": "The ethereal forest feels alive, with giant bioluminescent mushrooms and shifting twilight trails that seem to warp distance and direction.",
                                "challenges": "Feywild Encounter (roll 2d6): 2=Night Hag illusionist, 3-4=Nomadic wandering Ogres, 5=Centaur hunting patrol, 6=Playful pixies, 7=Dryad grove, 8=Faerie Dragons, 9=Satyrs with enchanted wine, 10=Sprites, 11=Unicorn, 12=Fey Treant guardian."
                            },
                            {
                                "scene_name": "Scene 7: The Labyrinth",
                                "location": "The Fog-Shrouded Hedge Maze",
                                "boxed_text": "Your journey through the Feywild brings you out of the woodland to a towering wall of dense magical fog. As you draw closer, the fog disperses just enough to reveal an immense, towering hedge maze of living brambles. From within the winding paths echoes the deep, guttural snorting of heavy beasts.",
                                "challenges": "Dungeon Combat Encounter: Defended by 2 Minotaurs (AC 14, 76 HP each, Greataxe +6 to hit, 2d12+4 slashing; Gore +6 to hit, 2d8+4 piercing + Charge; Reckless attack). One minotaur attacks from the front while the second blocks their rear, forcing combat to reach the maze center!"
                            }
                        ]
                    },
                    {
                        "act": "Act 5: Resolution (The Boss Encounters & Camp Fate)",
                        "scenes": [
                            {
                                "scene_name": "Scene 1: Meeting Imis Elvan",
                                "location": "Center of the Fey Labyrinth (Redwood Grove & Fountain)",
                                "boxed_text": "The center of the labyrinth is amazing and peaceful—a large circular grove surrounded by immense redwood pine trees with strange yellow lights embedded in their bark. Luminescent fireflies drift around an ancient stone fountain. On a bed of soft moss rests an elven maiden with silver eyes, smooth black hair, and dark green facial tattoos. As you enter, she springs to her feet, hands glowing with arcane energy: 'Who are you, and who sent you?'",
                                "challenges": "Encounter with Imis Elvan (CR 8 wood elf illusionist, AC 15, 192 HP, spells: Cone of Cold, Mirror Image, Misty Step, Thunderwave, Phantasmal Killer). If calmed or captured, she reveals Filip and Shandra's coup and provides evidence."
                            },
                            {
                                "scene_name": "Scene 2: Filip's Fall",
                                "location": "Camp Highmore (House of Chiefs / Barracks)",
                                "boxed_text": "Returning to Camp Highmore with Imis and the recovered evidence, you confront Chief Filip in front of the assembled council. Filip draws his massive greataxe, his eyes blazing with fury: 'You miserable mercenaries should have died in the jungle! Highmore is mine!'",
                                "challenges": "Climactic Boss Fight: Chief Filip (CR 5 Boss: AC 17 half plate, 157 HP, Greataxe +7 to hit, 1d12+4 slashing; Leap Attack 30ft; Whirlwind Attack targeting all adjacent foes; Bonus Action 'Call for Help' summoning 2 Highmore Guards with AC 15, 22 HP)."
                            },
                            {
                                "scene_name": "Scene 3: Conclusion & Reward",
                                "location": "Council Chamber of High Chief Highmore",
                                "boxed_text": "With Filip defeated and the conspiracy laid bare, High Chief Highmore steps forward to thank the adventurers before the entire camp.",
                                "challenges": "Party vindicated; awarded 2,500 gp in uncut gemstones, regional honors, and level up to 6th level."
                            }
                        ]
                    }
                ] if "elfhunt" in module_file.lower() else [],
                "encounter_tables": {
                    "jungle_random_encounters_2d6": {
                        "2": "Treant (Protector of the jungle, peaceful unless trees harmed)",
                        "3": "Faerie Dragons (Playful Fey creatures)",
                        "4": "Harpies (Ambush from canopy with luring song)",
                        "5": "Gnolls (Pack of 4 bloodthirsty hyena-men)",
                        "6": "Jungle Wolves (Dire wolves stalking the party)",
                        "7": "Chultan Apes (Territorial troop hurling stones and charging)",
                        "8": "Black Bears (Mated pair defending den)",
                        "9": "Hippogriffs (Soaring jungle predators)",
                        "10": "Ogres (Nomadic brutes looking for meat)",
                        "11": "Chimera (Deadly dragon-lion-goat beast, CR 6)",
                        "12": "Guardian Naga (Benevolent serpent guarding ancient ruins)"
                    },
                    "feywild_random_encounters_2d6": {
                        "2": "Night Hag (Shapeshifted wicked deceiver)",
                        "3-4": "Ogres (Nomadic Fey ogres)",
                        "5": "Centaurs (Hunting band of 3-4 warriors)",
                        "6": "Pixies (Invisible pranksters testing party morals)",
                        "7": "Dryads (Fey grove guardians)",
                        "8": "Faerie Dragons (Curious and whimsical)",
                        "9": "Satyrs (Piping dancers with enchanted wine)",
                        "10": "Sprites (Tiny inquisitors detecting alignment)",
                        "11": "Unicorn (Majestic celestial guardian)",
                        "12": "Fey Treant (Elder spirit of the twilight forest)"
                    }
                } if "elfhunt" in module_file.lower() else {},
                "monsters_and_bosses": {
                    "Dryad (x5 at Portal)": "AC 16 (barkskin), HP 22, Spells: Shillelagh (+4 to hit, 1d8+2), Tree Stride, Entangle (DC 14 STR save)",
                    "Minotaur (x2 in Labyrinth)": "AC 14, HP 76, Speed 40 ft., Greataxe (+6 to hit, 2d12+4 slashing), Gore (+6 to hit, 2d8+4 piercing + Charge 10 extra damage DC 14 STR knockdown), Reckless Attack",
                    "Imis Elvan": "CR 8 Wood Elf Illusionist, AC 15, HP 192, Speed 35 ft., Spells: Cone of Cold (8d8 cold, DC 16 CON save), Mirror Image (3 duplicates), Misty Step, Thunderwave, Phantasmal Killer, Disguise Self",
                    "Chief Filip": "CR 5 Conspirator Boss, AC 17 (half plate), HP 157, Speed 30 ft., Greataxe (+7 to hit, 1d12+4 slashing), Leap Attack (jumps up to 30 ft without provoking opportunity attacks and slams target), Whirlwind (attacks all creatures within 5 ft), Bonus Action 'Call for Help' (summons 2 Highmore Guards, AC 15, HP 22)"
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
        generates a concise journal entry and updates latest_recap and current_location in active_state.
        """
        if not turns or not self.active_state:
            return None

        active_id = self.active_state.get("campaign_id")
        camp_meta = self.manifest.get("campaigns", {}).get(active_id, {})
        camp_title = camp_meta.get("title", active_id or "")

        rp_turns = []
        for t in turns:
            txt = ""
            role = "speaker"
            if isinstance(t, dict):
                role = t.get("role") or "speaker"
                if t.get("content"):
                    txt = str(t["content"]).strip()
                elif t.get("text"):
                    txt = str(t["text"]).strip()
                elif t.get("parts") and isinstance(t["parts"], list):
                    p_texts = []
                    for p in t["parts"]:
                        if isinstance(p, dict) and p.get("text"):
                            p_texts.append(p["text"])
                        elif isinstance(p, str):
                            p_texts.append(p)
                    txt = " ".join(p_texts).strip()
            elif isinstance(t, str):
                txt = t.strip()

            if txt:
                # Clean role naming for prompt
                clean_role = "GM" if role in ("model", "assistant") else "Player"
                rp_turns.append(f"{clean_role}: {txt}")

        if not rp_turns:
            return None

        recent_turns = rp_turns[-16:]

        summary_data = None
        client = self._get_genai_client()
        if client:
            prompt = f"""You are a Game Master session journal recorder for the TTRPG campaign '{camp_title}'.
Review these dialogue turns from the recent game session:
{chr(10).join(recent_turns)}

Extract a concise 2-3 sentence DM journal recap covering:
1. What the party did and discovered in this session.
2. Where the party is currently standing / left off (exact current room or location, e.g. 'Camp Highmore Jail / Cell', 'Camp Gate', etc.).
3. The immediate next action or decision facing them.

Respond in JSON with schema:
{{
  "summary": "<2-3 sentence recap>",
  "location": "<exact current scene location>",
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
                        summary_data = data
            except Exception as e:
                logger.warning(f"[TTRPG] AI session journal summarization failed: {e}")

        # Smart heuristic fallback if AI call fails or is unavailable
        if not summary_data:
            all_text = " ".join(recent_turns).lower()
            last_model_turn = ""
            for rt in reversed(recent_turns):
                if rt.startswith("GM:"):
                    last_model_turn = rt.split(":", 1)[1].strip()
                    break
            if not last_model_turn and recent_turns:
                last_model_turn = recent_turns[-1].split(":", 1)[-1].strip()

            detected_loc = None
            if any(k in all_text for k in ("jail", "cell", "prison", "dungeon", "bars on the window", "iron bars", "locked cell")):
                detected_loc = "Camp Highmore Jail / Cell"
            elif "barracks" in all_text:
                detected_loc = "Camp Highmore Barracks"
            elif any(k in all_text for k in ("camp gate", "main gate", "gate leading")):
                detected_loc = "Camp Highmore Gate"
            elif any(k in all_text for k in ("tane here", "hideout", "warehouse")):
                detected_loc = "Tane Here's Hideout"
            elif any(k in all_text for k in ("house of chiefs", "chief shandra")):
                detected_loc = "House of Chiefs"
            elif any(k in all_text for k in ("jungle", "forest", "trail", "ruin")):
                detected_loc = "Jungle Outskirts"

            last_action_text = ""
            for rt in reversed(recent_turns):
                if rt.startswith("Player:"):
                    last_action_text = rt.split(":", 1)[1].strip()
                    break

            summary_data = {
                "summary": last_model_turn[:300] if last_model_turn else "Party advanced through the campaign.",
                "location": detected_loc or self.active_state.get("current_location", "Camp Highmore"),
                "active_quest": self.active_state.get("active_quest", ""),
                "new_clues": [],
                "last_action": last_action_text[:120] if last_action_text else "Concluded turn"
            }

        return self.record_campaign_notes(
            summary=summary_data["summary"],
            location=summary_data.get("location"),
            key_clues=summary_data.get("new_clues"),
            active_quest=summary_data.get("active_quest"),
            last_action=summary_data.get("last_action"),
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

        adherence = (meta.get("module_adherence") or "strict").lower().strip()
        if adherence not in ("creative", "balanced", "strict"):
            adherence = "strict"

        adherence_labels = {
            "creative": "Level 1 (Creative Sandbox / High GM Freedom)",
            "balanced": "Level 2 (Guided / Balanced Narrative)",
            "strict": "Level 3 (Strict Module Fidelity / By the Book)"
        }

        lines = [
            f"[UNIVERSAL TABLETOP ROLEPLAYING ENGINE — ACTIVE CAMPAIGN: {meta.get('title', active_id)} ({active_id})]",
            f"- System Archetype / Ruleset: {meta.get('system_preset', 'heroic_d20')}",
            f"- Game Master Mode: {meta.get('gm_mode', 'interactive_narrator')}",
            f"- Narrative Tone: {meta.get('tone', 'Cinematic, grounded')}",
            f"- Story Adherence & GM Pacing: {adherence_labels.get(adherence, adherence_labels['strict'])}",
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
                "* PHYSICAL DICE MODE: Guide the player on which physical dice to roll and modifiers to add from their sheet.\n"
                if dice_mode == "physical"
                else "* DIGITAL DICE AUTOMATION: When a roll IS required for an established actor, call `roll_dice` automatically—never ask human players to roll physical plastic dice at their table.\n"
            )
            lines.append(
                "\n[DM GUIDANCE & CHALLENGE LEVEL: HELPFUL / BEGINNER-FRIENDLY MENTOR]\n"
                "* The players may be beginners or learning the rules. Act as an encouraging, patient guide and Dungeon Master.\n"
                "* Whenever you present a situation or prompt a player, ALWAYS offer 2-3 clear, recommended options for what their character can do (e.g. 'Option 1: You can swing your Longsword (+4 to hit)... Option 2: Shoot with your Heavy Crossbow... Option 3: Attempt to negotiate or intimidate...'), UNLESS the player has already declared their action.\n"
                "* When a player declares an action AND specifies which character performs it (e.g. 'Joe attacks the goblin', 'Shadow picks the lock'), adjudicate that action directly.\n"
                "* When a player declares an action WITHOUT naming a character (e.g. 'Press Sandra for information', 'Ask about Imis', 'Search the room', 'Head to the gate') in a multi-character party:\n"
                "  - If it is standard dialogue or movement: DO NOT ROLL DICE! Answer as the NPC or describe the travel.\n"
                "  - If a skill check is genuinely required: ASK THE TABLE who steps forward to do it (e.g. 'Who is speaking to Shandra? Joe, Sam, or Shadow?'). DO NOT pick a character arbitrarily!\n"
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
                "\n[DICE ROLLING MODE: DIGITAL DICE (AETHER ROLLS FOR PLAYERS)]\n"
                "* DIGITAL DICE ACTIVE: When a dice roll IS required for an established actor, you (the Game Master) execute `roll_dice` automatically—you do not make human players roll physical dice at their table.\n"
                "* MANDATORY PLAYER AGENCY — NEVER ARBITRARILY ASSIGN CHARACTERS:\n"
                "  1. If the player specifies who acts or explicitly requests an ability/skill check (e.g. 'Have Shadow roll investigation', 'Have Shadow use Charisma to persuade Imis', 'Joe attacks the orc', 'Sam rolls Stealth'): ALWAYS honor the player's explicit request and execute `roll_dice` immediately for that specific character, applying their sheet modifiers.\n"
                "  2. If the player states an action without naming a character (e.g. 'Press Sandra for information', 'Ask about Imis', 'Search the ruins', 'Head to the gate'):\n"
                "     - If it is casual chatter with friendly/indifferent NPCs: answer in-character as the NPC without rolling.\n"
                "     - If it is a high-stakes social claim, persuasion of a wary/suspicious NPC, or a skill check is needed: ASK THE TABLE: 'Who steps forward to speak or attempt this? Joe, Sam, or Shadow?' DO NOT arbitrarily choose a character or execute `roll_dice` until the player designates who is acting!\n"
                "* CASUAL DIALOGUE & MOVEMENT VS HIGH-STAKES SOCIAL ENCOUNTERS:\n"
                "  - Low-stakes, casual questions with indifferent or friendly NPCs ('Ask the bartender for rumors', 'Ask the guard where the market is') is NORMAL ROLEPLAY DIALOGUE — DO NOT roll dice; reply naturally in-character.\n"
                "  - Walking to an area ('Head to the western gate', 'Move to the barracks') is NORMAL MOVEMENT, NOT a Survival check!\n"
                "  - HIGH-STAKES SOCIAL CHECKS REQUIRED: When attempting to persuade, calm, deceive, or negotiate with a suspicious, terrified, or hostile NPC (e.g. 'We come in peace', 'We were framed too', 'Drop your weapon'), or when stakes are high: WORDS ALONE NEVER AUTOMATICALLY SUCCEED. You MUST execute or call for a social check (Persuasion/Deception/Intimidation or NPC Insight) as detailed in the NPC Psychology Directive below.\n"
                "* MANDATORY ACTOR ATTRIBUTION: Every dice roll MUST be explicitly attributed to a specific party character (e.g. 'Joe', 'Sam', 'Shadow') or monster/NPC/DM. Always pass `character_name` when calling `roll_dice`.\n"
                "* NATURAL GM NARRATION & CLEAN OUTPUT:\n"
                "  - Weave the character's name and final total naturally into the narrative (e.g., 'Joe rolled a 16 on his Athletics check', 'Shadow rolled an 11 on Investigation', or 'With a 22 Persuasion, Joe convinces the guard...').\n"
                "  - DO NOT read off or append dice formulas or math! NEVER say or write formula parentheticals like '(1d20+3)' or '(1d20+4)' in your spoken narration. The players already see the complete dice breakdown in the UI audit log. Keep your storytelling natural without reciting math."
            )

        lines.append(
            "\n[DIALOGUE STYLE & ANTI-REPETITION DIRECTIVES]\n"
            "* BE CONCISE & PUNCHY: Keep narrations to 2-4 immersive sentences per turn. Avoid walls of text.\n"
            "* NO REDUNDANT RECAP: Do not repeat back what the player just said. If Eric declares 'I attack the goblin', do not recite 'Eric decides to attack the goblin with his sword'. Jump directly into the swing and outcome.\n"
            "* NO ROBOTIC MENUS: Never list 'Option 1: ... Option 2: ... Option 3: ...' when a player has already declared an action. Only offer brief, organic suggestions if a player explicitly asks what they can do or is visibly stuck.\n"
            "* IMMERSIVE GM VOICE: Never mention system tools, JSON, meters, or 'Aether'. Speak as an evocative, living Dungeon Master."
        )

        lines.append(
            "\n[COMBAT ENCOUNTERS & INITIATIVE LADDER DIRECTIVE — MANDATORY]\n"
            "* INITIATIVE IS FOR COMBAT ONLY: Turn order, single-actor focus, and `advance_combat_turn()` ONLY apply during active combat encounters with hostile enemies!\n"
            "* FREE EXPLORATION OUT OF COMBAT: When not fighting foes, the game is in Free Exploration mode. Do NOT enforce turn order or single-character lockouts out of combat!\n"
            "* ORDER OF OPERATIONS ON COMBAT START:\n"
            "  - Whenever enemies attack, ambush, or a player initiates combat: YOU MUST CALL `start_combat(enemies=['...'])` FIRST before applying any damage!\n"
            "  - `start_combat` populates all combatants with their official HP and rolls initiative for everyone.\n"
            "  - NEVER call `modify_character_vitals` on an enemy before `start_combat`, or the enemy will not exist on the ladder and damage will fail!\n"
            "* CONTINUOUS COMBAT RESOLUTION & AUTOMATIC MONSTER TURNS:\n"
            "  - Combat flows sequentially along the initiative ladder: 1 -> 2 -> 3 -> 4 -> 5 -> 6.\n"
            "  - 1. RESOLVING A PLAYER'S TURN:\n"
            "     * When a player declares their action (e.g. 'Sam casts Eldritch Blast'): execute attack/damage rolls using `roll_dice`, apply damage to the enemy using `modify_character_vitals`, and call `advance_combat_turn()`.\n"
            "  - 2. AUTOMATIC MONSTER TURNS (NEVER HANG OR FREEZE ON ENEMY TURNS):\n"
            "     * HUMAN PLAYERS DO NOT CONTROL MONSTERS! If the initiative ladder lands on or advances to an ENEMY (`is_player: False`), YOU (the Game Master) MUST TAKE AND RESOLVE THE MONSTER'S TURN IMMEDIATELY in that same response!\n"
            "     * NEVER merely announce 'Minotaur attacks with greataxe' and stop without rolling! That completely freezes the game because players cannot take a monster's turn!\n"
            "     * When an enemy is active:\n"
            "       a) Execute attack roll: `roll_dice(formula='1d20+...', character_name='<Monster>', purpose='<Monster> Attack vs <Hero> AC')`.\n"
            "       b) DIRECT AC COMPARISON (NEVER ASK PLAYERS 'Does that hit your AC?'): You (the DM) have the complete Party Roster with every hero's exact Armor Class (e.g. Joe AC 15, Shadow AC 15, Gale AC 14, Sam AC 12). Compare the attack roll directly to the target hero's AC:\n"
            "          - If Attack Roll >= Target AC: It is a HIT. Roll damage immediately via `roll_dice(formula='...', character_name='<Monster>', purpose='<Monster> Damage')` and apply it immediately via `modify_character_vitals(character_name='<Hero>', stat_name='health', amount=-damage)`.\n"
            "          - If Attack Roll < Target AC: It is a MISS. Describe the blow glancing off their shield or armor.\n"
            "          - NEVER pause, hesitate, or ask the player 'Does that hit your AC, Joe?'. Adjudicate and apply hits and misses immediately!\n"
            "       c) Call `advance_combat_turn()` to move the pointer to the next combatant.\n"
            "     * If the NEXT combatant on the ladder is ALSO an enemy, resolve that enemy's turn immediately as well using the same steps!\n"
            "  - 3. HANDING OFF TO HUMAN PLAYERS:\n"
            "     * You ONLY stop and prompt when the initiative ladder reaches a HUMAN PLAYER (`is_player: True`)!\n"
            "     * Address that specific hero: '<Hero Name>, it is your turn on initiative! What do you do?'\n"
            "  - 4. NEVER SKIP PLAYERS: You must adhere strictly to turn order and never jump past a player to take a monster's turn out of sequence!\n"
            "* ZERO HALLUCINATED ROLLS & MANDATORY TOOL CALLS:\n"
            "  - Stating attack numbers or damage in text (e.g. 'That's a 19 to hit, dealing 13 piercing damage') WITHOUT calling `roll_dice` and `modify_character_vitals` is a FATAL ENGINE ERROR.\n"
            "  - Merely typing damage in prose DOES NOT update HP bars or character sheets in the UI! You MUST call `modify_character_vitals` for EVERY hit!\n"
            "* DEFEATED COMBATANTS & COMBAT END:\n"
            "  - When all hostile foes reach 0 HP, combat automatically ends and dead foes are cleared from the tracker.\n"
            "  - Once combat ends, immediately return to Free Exploration mode. Do NOT continue calling `advance_combat_turn()` or asking players to take individual turns out of combat!\n"
            "* COMBAT EXPERIENCE (XP) & REWARDS:\n"
            "  - When enemies are defeated or combat concludes, experience points (XP) are automatically calculated and awarded equally among all active party members (e.g. 5 Apes = 500 XP total, awarded 125 XP to each hero).\n"
            "  - Explicitly announce the victory and the XP reward in your narration (e.g., 'With the apes defeated, the party earns 500 XP (125 XP each)!'). If any character qualifies to level up (e.g. Gale reaching 300 XP), congratulate them and mention their level-up readiness.\n"
            "* BEHIND THE DM SCREEN — NEVER REVEAL NUMERICAL ENEMY HP TO PLAYERS:\n"
            "  - You are the Dungeon Master. Exact numerical hit points and max HP of enemies or monsters (e.g., '43/76 HP' or 'The minotaur has 4 HP left') are STRICTLY CONFIDENTIAL behind the DM screen!\n"
            "  - NEVER announce or recite numerical enemy hit points in your spoken narration or text dialogue! (e.g. NEVER say 'The minotaur takes 18 damage and is at 42 HP' or 'The minotaur's health is 4').\n"
            "  - Instead, describe enemies' physical wounds and condition qualitatively using standard D&D 5e health status tiers:\n"
            "    * Healthy (100%): Unscathed, ferocious, fresh, unfazed.\n"
            "    * Injured (51%–99%): Nicked, lightly bleeding, superficial burns/cuts, scratched armor.\n"
            "    * Bloodied (1%–50%): Bloodied, heavily battered, gasping for breath, visibly staggering under pain.\n"
            "    * Critical (1%–15%): Mortally wounded, clinging to consciousness, teetering on the edge of collapse.\n"
            "    * Defeated (0 HP): Slain, lifelessly crashing to the earth, destroyed."
        )

        lines.append(
            "\n[SPELLCASTING, CANTRIPS & SPELL SLOT DIRECTIVE — MANDATORY]\n"
            "* CALL `cast_spell` ON EVERY SPELL: Whenever ANY character casts a spell, YOU MUST CALL `cast_spell(character_name=..., spell_name=..., spell_level=...)` in that same turn!\n"
            "* CANTRIPS (LEVEL 0 — AT-WILL SPELLS):\n"
            "  - Spells like `Fire Bolt`, `Eldritch Blast`, `Sacred Flame`, `Prestidigitation`, and `Mage Hand` are CANTRIPS (Level 0).\n"
            "  - CANTRIPS COST 0 SPELL SLOTS. Characters can cast cantrips at-will indefinitely, even when they have 0 spell slots remaining (0/2)!\n"
            "  - In your narration, explicitly clarify this so players understand rules legality: e.g., 'Gale casts Fire Bolt (Cantrip — costs 0 spell slots)...'\n"
            "* LEVELED SPELLS (LEVEL 1–9):\n"
            "  - Spells like `Magic Missile`, `Shield`, `Misty Step`, `Fireball`, `Command` REQUIRE expending an available spell slot of that level or higher.\n"
            "  - If a character has 0 slots of that level or higher, `cast_spell` returns an error. The spell FAILS: announce that their slots are exhausted and they must cast a cantrip or take a rest.\n"
            "* SPELL SAVING THROWS:\n"
            "  - When a spell forces a target to save, execute `roll_dice(formula='1d20+...', character_name='<Target Name>', purpose='<Target> Saving Throw vs <Spell> (DC <DC>)')`! Never auto-grant spell success on hostile targets without rolling."
        )

        lines.append(
            "\n[STEALTH, SNEAKING & PRISON ESCAPES DIRECTIVE]\n"
            "* STEALTH CHECKS REQUIRED: Moving through an active enemy outpost, sneaking past sentries, or escaping a prison is NOT free automatic movement.\n"
            "* YOU MUST CALL FOR OR EXECUTE a Dexterity (Stealth) check (e.g. DC 12-14) against the guards' Passive Perception. If failed, sentries or patrols spot the heroes and raise the alarm."
        )

        lines.append(
            "\n[NPC PSYCHOLOGY, SENSORY LOG AWARENESS & SOCIAL INTERACTION DIRECTIVE — MANDATORY]\n"
            "* SURMISE THROUGH THE NPC'S EYES (SENSORY CONTEXT & LOG AWARENESS):\n"
            "  - Before narrating an NPC's reaction, you (the Game Master) MUST examine the recent turn log and sensory circumstances:\n"
            "    1. Did the party just fight or kill creatures/guards nearby (e.g. stepping out of a portal after slaying minotaurs or beasts)?\n"
            "    2. Are weapons drawn, spells glowing, or armor and clothes spattered with blood?\n"
            "    3. Did strangers enter an uninvited refuge, secret sanctuary, or private hideout without warning?\n"
            "    4. What is the NPC's state of mind? Are they in hiding, hunted by authorities or assassins, paranoid, or defensive?\n"
            "* ANTI-GM METAGAMING (NPCS DO NOT READ THE MODULE OR TRUTH):\n"
            "  - The NPC DOES NOT know the players are the heroes! Even if the module states that the NPC is innocent or shares mutual enemies with the party (e.g. Imis Elvan being framed by Filip just like the party), THE NPC DOES NOT KNOW THAT!\n"
            "  - To a hunted or defensive NPC, armed intruders covered in gore saying 'We come in peace' or 'Filip framed us too' looks and sounds like an assassination squad or bounty hunter ruse!\n"
            "* MANDATORY SOCIAL CHECKS (WORDS ALONE DO NOT PASS):\n"
            "  - When a party attempts to persuade, calm, deceive, or negotiate with a suspicious, terrified, or guarded NPC:\n"
            "    1. IF THE PLAYER SPECIFIES WHO SPEAKS (e.g. 'Have Shadow use Charisma to tell Imis...', 'Joe says we come in peace'):\n"
            "       YOU MUST IMMEDIATELY EXECUTE `roll_dice(formula='1d20+...', character_name='<Speaker>', purpose='<Speaker> Charisma (Persuasion/Deception) check vs <NPC>')`!\n"
            "    2. Alternatively or in combination, roll the NPC's contested Wisdom (Insight) check (e.g. `roll_dice(formula='1d20+4', character_name='<NPC Name>', purpose='<NPC> Wisdom (Insight) check to gauge sincerity')`).\n"
            "    3. DIFFICULTY CLASSES (D&D 5e DMG p. 244-245):\n"
            "       - Friendly / Low Stakes: DC 10 (or automatic).\n"
            "       - Indifferent Bystander: DC 12-14.\n"
            "       - Wary / Hunted / Suspicious NPC (e.g. Imis in hiding after bloody battle): DC 15-18.\n"
            "       - Hostile Enemy: DC 20+ (or immediate combat).\n"
            "* GRADUATED ROLEPLAY OUTCOMES & DRAMATIC TENSION:\n"
            "  - ON FAILURE: The NPC refuses to believe the party! They assume a trick, step back into a defensive stance, draw weapons or ready defensive spells (e.g. Imis readying `Misty Step` or `Cone of Cold`), and demand: 'Lies! Back away or I strike! Drop your weapons or leave!' If the party presses forward aggressively, call `start_combat(enemies=['<NPC Name>'])`.\n"
            "  - ON SUCCESS: The NPC hesitates and tentatively lowers their weapon tip, but remains vigilant and cautious: 'You say Filip framed you...? How do I know this isn't his trick? If you truly mean no harm, prove it. Why did he frame you?' The dialogue continues with hard-won trust rather than a free pass."
        )

        lines.append(
            "\n[INVENTORY, EQUIPMENT & LOOT DIRECTIVES]\n"
            "* AUTOMATIC INVENTORY UPDATES: Whenever a character finds, loots, receives, pockets, buys, drops, gives away, loses, or consumes an item or weapon (e.g., 'Joe finds a locket and puts it in his pocket', 'The wizard drinks a potion', 'Sam buys a crossbow'), you MUST IMMEDIATELY execute the `manage_party_inventory` tool in that same turn!\n"
            "* MANDATORY DISARMING & ARREST DIRECTIVE:\n"
            "  - Whenever characters are arrested, captured, imprisoned, surrender, or are ordered to disarm (e.g., Filip and the guards intercept and arrest the party):\n"
            "    YOU MUST IMMEDIATELY CALL `manage_party_inventory(character_name='all', action='disarm', storage_location='<e.g. Camp Highmore Guard Barracks - Evidence Chest>', reason='Arrested by Chief Filip')`!\n"
            "  - NEVER merely narrate disarming in text without executing this tool! Calling `action='disarm'` strips manufactured weapons from the Party Roster and secures them in the world cache under their storage location!\n"
            "* ITEM PERSISTENCE & RETRIEVAL:\n"
            "  - Weapons and gear taken from characters are NOT destroyed; their physical location is tracked in the world cache.\n"
            "  - When characters pick locks, defeat guards, search the evidence locker, or break into the guardroom, execute `manage_party_inventory(character_name='all', action='retrieve', storage_location='...')` to return their weapons to the party roster!\n"
            "* NEVER RELY SOLELY ON DIALOGUE: Simply narrating that a character took, dropped, or lost an item does NOT update the party roster or character sheet. You must execute `manage_party_inventory` so the inventory is mechanically tracked and preserved across sessions.\n"
            "* NPCS & CAPTIVES ARE NEVER INVENTORY: Do NOT call `manage_party_inventory` for persons, enemies, allies, or captives (e.g. capturing, guarding, or keeping Imis bound is a narrative/condition event, NEVER an inventory item!). Only tangible physical objects, gear, documents, weapons, and coins belong in inventory.\n"
            "* MERCHANT SHOPS & DYNAMIC STORY PRICING:\n"
            "  - AUTOMATIC STORY-BASED PRICE SCALING: As the DM, you MUST dynamically set `price_multiplier` when opening a shop or executing a transaction to reflect the current narrative context:\n"
            "    * Frontier outposts, remote wilderness/jungle bases, or isolated supply depots (e.g. Camp Highmore, Tane Here's warehouse): 1.20x to 1.35x markup due to transport hazards, distance & scarcity.\n"
            "    * Besieged, famine-stricken, war-torn regions, or black markets / contraband / rare goods: 1.50x to 2.00x markup.\n"
            "    * Major trade cities, coastal ports, or bustling trade bazaars: 0.90x to 1.00x standard competitive rates.\n"
            "    * Faction allies, military quartermasters, or merchants the party aided/saved: 0.75x to 0.85x discount.\n"
            "  - HAGGLING & ROLEPLAY DIRECTIVES:\n"
            "    * When players negotiate, haggle, or make a Charisma (Persuasion/Deception/Intimidation) check, evaluate their roleplay and roll.\n"
            "    * On a success (DC 12-15), immediately reward them by calling `open_merchant_shop` with a reduced `price_multiplier` (e.g. dropping from 1.25x down to 1.00x or 0.85x) with in-character description acknowledging the deal, so the UI updates with discounted prices in real time!\n"
            "  - When the party enters any store, shop, or meets a merchant NPC, call `open_merchant_shop(shop_name=..., location_name=..., price_multiplier=..., specialty=..., description=...)` so the UI shop pops open with story pricing!\n"
            "  - If the player buys or sells via voice/chat directly with you, execute `merchant_transaction` AND use `lookup_dnd_compendium` for exact SRD stats and prices (2014 & 2024 rules).\n"
            "  - When players decide to camp or take a Short Rest or Long Rest and want to spend Hit Dice, drink potions, swap armor/attunement, or prepare spells, call `prompt_party_rest(rest_type='short'|'long')` to open the Rest modal (or `execute_rest` for automated quick rests).\n"
            "* GUIDED LEVEL-UP & MULTICLASSING:\n"
            "  - When a character reaches an XP threshold or story milestone to gain a level, call `prompt_level_up_wizard(character_name=..., reason=...)` so the interactive D&D 5e (2014 & 2024) Level-Up & Multiclass Wizard opens for the player (do NOT silently auto-pick feats or multiclasses without letting the player use the wizard, unless they ask you to quick-level via `level_up_character`)."
        )

        stored_items_list = state.get("stored_items", [])
        if stored_items_list:
            lines.append("\n[CONFISCATED & STORED PARTY ITEMS / WORLD CACHES]")
            by_loc = {}
            for it in stored_items_list:
                loc_name = it.get("storage_location", "Unknown Evidence Cache")
                knowledge = it.get("location_knowledge", "unknown")
                status = it.get("status", "confiscated")
                salvageable = it.get("salvageable", True)
                holder = it.get("holder_or_creature", "")
                clue = it.get("discovery_clue", "")
                desc = f"{it.get('original_owner', 'Party')}'s {it.get('name', 'Item')} ({it.get('type', 'gear')}) [Status: {status}, Knowledge to players: {knowledge}"
                if holder:
                    desc += f", Holder/Beast: {holder}"
                if not salvageable:
                    desc += ", UNSALVAGEABLE/DESTROYED"
                if clue:
                    desc += f", Clue: {clue}"
                desc += "]"
                by_loc.setdefault(loc_name, []).append(desc)
            for loc_name, items_str in by_loc.items():
                lines.append(f"- Location: {loc_name}\n  * {chr(10).join('  * ' + s for s in items_str)}")
            lines.append(
                "* INFORMATION ASYMMETRY & DYNAMIC KNOWLEDGE RULES:\n"
                "  1. DISARM / ARREST: When arresting or confiscating party weapons/gear, set action='disarm', location_knowledge='unknown' unless the characters directly watched where it was locked away. In the player UI, unknown locations appear as 'Confiscated Gear (Location Unknown)'. DO NOT tell players the exact location in dialogue unless they find out in-character!\n"
                "  2. DISCOVERING LOCATION (MANDATORY TOOL CALL): If players roll Perception or Investigation (e.g. DC 12-14) to look for their weapons, overhear guards, or find a ledger, as soon as the roll succeeds, you MUST call `manage_party_inventory(action='discover_location', storage_location='<Exact Location Seen/Heard>', discovery_clue='<Perception total / visual clue>')` in that turn! DO NOT ONLY NARRATE IT IN SPEECH! Calling `manage_party_inventory` is REQUIRED so the player's roster UI updates from 'Location Unknown' to 'Location Known'!\n"
                "  3. BEAST SALVAGE: If an item/weapon is eaten or swallowed by a monster (e.g. helmet eaten by a dragon), record action='remove', status='inside_creature', holder_or_creature='<Beast Name>', salvageable=True, location_knowledge='known'. When the party defeats or slays the creature and cuts open the corpse, describe salvaging the gear and call `manage_party_inventory(action='retrieve', holder_or_creature=..., storage_location=...)` to return it to their roster!\n"
                "  4. PERMANENT DESTRUCTION: If an item falls into lava, Mount Doom, or is dissolved in a sphere of annihilation/acid, call `manage_party_inventory(action='destroy', items=[...], storage_location='Lava Pit / Mount Doom', salvageable=False)`. Destroyed items can NEVER be reclaimed or retrieved.\n"
                "  5. RETRIEVAL DIRECTIVE: Players have NO artificial one-click 'Reclaim' button in their UI; they rely on you as DM. When characters pick an armory lock, defeat guards, or find their chest, call `manage_party_inventory(character_name='all', action='retrieve', storage_location=...)` to restore their equipment!"
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
                npc_parts = [(n.get("name") if isinstance(n, dict) else str(n)).strip() for n in synopsis["key_npcs"] if n]
                lines.append(f"- Key NPCs: {', '.join(npc_parts)}")
            if synopsis.get("opening_hook"):
                lines.append(f"- Opening Hook: {synopsis['opening_hook']}")

            # Dynamic Active Scene & Module Progression
            acts_scenes = synopsis.get("acts_and_scenes", [])
            current_scene_obj = None
            current_act_name = state.get("current_act", "")
            saved_scene_name = state.get("current_scene", "")
            current_loc = state.get("current_location", "")

            # 1. Try finding matching scene from state.current_scene or current_location
            if acts_scenes:
                for act in acts_scenes:
                    act_name = act.get("act", "")
                    for sc in act.get("scenes", []):
                        sc_name = sc.get("scene_name", "")
                        sc_loc = sc.get("location", "")
                        if saved_scene_name and (saved_scene_name.lower() in sc_name.lower() or sc_name.lower() in saved_scene_name.lower()):
                            current_scene_obj = sc
                            current_act_name = act_name
                            break
                        if current_loc and (current_loc.lower() in sc_loc.lower() or sc_loc.lower() in current_loc.lower() or current_loc.lower() in sc_name.lower()):
                            current_scene_obj = sc
                            current_act_name = act_name
                            break
                    if current_scene_obj:
                        break

            # Fall back to starting scene if no scene identified
            if not current_scene_obj:
                current_scene_obj = synopsis.get("starting_scene") or {}
                if not current_act_name and acts_scenes:
                    current_act_name = acts_scenes[0].get("act", "Act 1: Prologue")

            lines.append("\n[ACTIVE ADVENTURE SCENE NARRATIVE & SCRIPT — MANDATORY]")
            if current_act_name:
                lines.append(f"- Active Act: {current_act_name}")
            lines.append(f"- Active Scene: {current_scene_obj.get('scene_name', 'Scene 1: The Beginning')}")
            lines.append(f"- Scene Location: {current_scene_obj.get('location', 'Camp Highmore')}")
            if current_scene_obj.get("boxed_text"):
                lines.append(f"- VERBATIM BOXED NARRATION (Read or paraphrase this text when introducing the scene):\n  \"{current_scene_obj['boxed_text']}\"")
            if current_scene_obj.get("active_npc"):
                lines.append(f"- Key NPC Present & In-Character: {current_scene_obj['active_npc']}")
            if current_scene_obj.get("challenges"):
                lines.append(f"- Scene Challenges / Encounters / Guardians: {current_scene_obj['challenges']}")
                if any(k in current_scene_obj["challenges"].lower() for k in ("social", "negotiat", "boss")):
                    lines.append("  * SOCIAL ENCOUNTER ADJUDICATION: Treat this NPC with realistic psychological stakes and skepticism! Do NOT auto-grant trust without successful Charisma (Persuasion/Deception) checks or proven evidence.")
            if current_scene_obj.get("quest_briefing"):
                lines.append(f"- Quest Briefing: {current_scene_obj['quest_briefing']}")
            if current_scene_obj.get("immediate_choices"):
                choices_formatted = "\n  ".join(f"* {c}" for c in current_scene_obj["immediate_choices"])
                lines.append(f"- Established Player Choices for this Scene:\n  {choices_formatted}")

            # Module Roadmap / Scene Flow
            if acts_scenes:
                lines.append("\n[PUBLISHED MODULE SCENE ROADMAP (STRICT PROGRESSION)]")
                for act in acts_scenes:
                    sc_names = [s.get("scene_name", "") for s in act.get("scenes", []) if s.get("scene_name")]
                    lines.append(f"* {act.get('act', 'Act')}: {' -> '.join(sc_names)}")

            # Encounter Tables
            enc_tables = synopsis.get("encounter_tables", {})
            if enc_tables and isinstance(enc_tables, dict):
                lines.append("\n[MODULE RANDOM ENCOUNTER TABLES]")
                for tbl_name, tbl_rolls in enc_tables.items():
                    rolls_str = "; ".join(f"[{k}] {v}" for k, v in tbl_rolls.items())
                    lines.append(f"- {tbl_name}: {rolls_str}")

            # Key Monster & Boss Stat Blocks
            bosses = synopsis.get("monsters_and_bosses", {})
            if bosses and isinstance(bosses, dict):
                lines.append("\n[KEY ENEMY & GUARDIAN STAT BLOCKS]")
                for b_name, b_stats in bosses.items():
                    lines.append(f"- {b_name}: {b_stats}")

            if adherence == "creative":
                lines.append(
                    "\n[MODULE ADHERENCE LEVEL: 1 / 3 — CREATIVE SANDBOX (HIGH GM FREEDOM)]\n"
                    "* CREATIVE GM FREEDOM: Treat the published module as an inspirational world guide, NPC roster, and thematic backdrop.\n"
                    "* You have full creative license to improvise new locations, remix, reorder or substitute encounters, invent emergent side quests, and adapt the narrative dynamically based on player choices and curiosity, rather than strictly following module chapters in order.\n"
                    "* Core NPCs, lore, and themes remain grounded in the module, but the narrative journey is flexible and emergent.\n"
                    "* Procedural pacing still applies: present one situation at a time, let players declare actions, and resolve skill checks fairly without skipping tactical gameplay."
                )
            elif adherence == "balanced":
                lines.append(
                    "\n[MODULE ADHERENCE LEVEL: 2 / 3 — GUIDED & BALANCED NARRATIVE]\n"
                    "* GUIDED / BALANCED MODULE PACING: Preserve the module's core narrative spine, major story milestones, central mystery/threat, and climactic boss encounters, while offering flexibility in scene transitions and player-driven choices.\n"
                    "* Accommodate creative player detours, alternative tactics, and emergent roleplay dialogue organically, but gently steer the party back toward the module's primary milestones.\n"
                    "* Encounters can be negotiated, bypassed through clever strategy, or tackled in varying sequence, provided the narrative remains coherent and true to the module's overarching story."
                )
                if current_scene_obj.get("critical_trigger"):
                    lines.append(f"\n* KEY MODULE TRIGGER: {current_scene_obj['critical_trigger']}")
                elif "elfhunt" in str(synopsis.get("module_title", "")).lower() or active_id == "elf_hunt":
                    lines.append(
                        "\n* KEY MODULE PLOT BEAT (ACT 1 BARRACKS -> ACT 2 CONFLICT):\n"
                        "  - If the players investigated the barracks or questioned Chief Filip, Filip and Petry suspect the party knows too much and attempt to intercept or frame them before they leave Camp Highmore."
                    )
            else:  # strict (default)
                lines.append(
                    "\n[MODULE ADHERENCE LEVEL: 3 / 3 — STRICT MODULE FIDELITY (BY THE BOOK)]\n"
                    "* STRICT MODULE FIDELITY: You are the Game Master running THIS specific published module by the book. "
                    "You MUST follow the module's written acts, scenes, and narrative progression sequentially! Never skip scenes or invent unrelated side stories.\n"
                    "* SKILL CHECKS NEVER SKIP SCENES, ENCOUNTERS, OR TRAVEL:\n"
                    "  - A high roll (e.g. DC 20+ check or Natural 20) only grants exceptional information, quick track finding, or tactical advantage WITHIN THE CURRENT SCENE!\n"
                    "  - A HIGH ROLL NEVER TELEPORTS THE PARTY PAST INTERMEDIATE SCENES, COMBATS, HAZARDS, OR DUNGEON ROOMS!\n"
                    "  - For example, succeeding on a tracking or navigation check finds the path into the next area; it does NOT bypass the wilderness monsters, environmental hazards, or guardian encounters along that path!\n"
                    "* MANDATORY GUARDIANS & COMBAT ENCOUNTERS:\n"
                    "  - If the module places enemies, guards, traps, or obstacles guarding a location, passage, or portal, the party MUST encounter them!\n"
                    "  - You CANNOT describe the party simply walking past them or arriving at the objective without triggering and resolving these encounters.\n"
                    "* PROCEDURAL PACING: Tabletop RPGs are about the journey, tactical choices, and encounters. Present one scene and obstacle at a time, let players state their actions, roll necessary checks or initiative, and adjudicate before transitioning to the next scene."
                )
                if current_scene_obj.get("critical_trigger"):
                    lines.append(f"\n* ACTIVE SCENE MANDATORY TRIGGER: {current_scene_obj['critical_trigger']}")
                elif "elfhunt" in str(synopsis.get("module_title", "")).lower() or active_id == "elf_hunt":
                    lines.append(
                        "\n* MANDATORY MODULE SCENE TRIGGERS (ACT 1 BARRACKS -> ACT 2 ARREST):\n"
                        "  - In this module, if the players visited the barracks or questioned Chief Filip, Filip immediately plots with Johan Petry to frame the party for the theft to protect their coup!\n"
                        "  - ACT 2 SCENE 1 (THE ARREST) TRIGGERS AUTOMATICALLY as soon as the party finishes at the barracks, speaks with Shandra, or attempts to reach the camp gate!\n"
                        "  - Chief Filip and a squad of 11 guards and 2 knights intercept them with spears leveled and crossbows drawn to arrest them under false charges.\n"
                        "  - DO NOT steer players into the jungle or narrate jungle travel if they questioned Chief Filip or investigated the barracks! You MUST run Act 2 Scene 1: The Arrest first!"
                    )

        # 2. Persistent Campaign Memory & Last Turn Recap (Recap across Restarts)
        recap = state.get("latest_recap")
        loc = state.get("current_location")
        quest = state.get("active_quest")
        clues_raw = state.get("discovered_clues", [])
        clues = [(c.get("name") if isinstance(c, dict) else str(c)).strip() for c in clues_raw if c]
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
                cond_parts = [(c.get("name") if isinstance(c, dict) else str(c)).strip() for c in conds if c]
                cond_str = f" [Conditions: {', '.join(cond_parts)}]" if cond_parts else ""
                lvl_str = f"Lv. {ch.get('level', 1)}"
                xp_str = f"{ch.get('experience_points', 0)} XP" if ch.get("experience_points") else ""
                lvl_xp = f" ({', '.join(filter(None, [lvl_str, xp_str]))})"

                wpns = ch.get("weapons", [])
                wpn_parts = [f"{(w.get('name') if isinstance(w, dict) else str(w))} ({w.get('atk_bonus', '+0') if isinstance(w, dict) else '+0'}, {w.get('damage', '1d4') if isinstance(w, dict) else '1d4'})" for w in wpns if w]
                wpn_str = f" | Attacks: {', '.join(wpn_parts)}" if wpn_parts else ""

                inv = ch.get("inventory", [])
                inv_parts = [(i.get("name") if isinstance(i, dict) else str(i)).strip() for i in inv[:8] if i]
                inv_str = f" | Key Items: {', '.join(inv_parts)}" if inv_parts else ""

                spells = ch.get("spells", [])
                spell_names = [(s.get("name") if isinstance(s, dict) else str(s)).strip() for s in spells if s]
                spell_str = f" | Spells/Cantrips: {', '.join(spell_names[:16])}" + (f" (+{len(spell_names)-16} more)" if len(spell_names) > 16 else "") if spell_names else ""

                # Spell Slots summary (including Warlock Pact Magic)
                slots_dict = ch.get("spell_slots") or {}
                avail_slots = []
                for r in range(1, 10):
                    s = slots_dict.get(f"level_{r}")
                    if s and isinstance(s, dict) and s.get("max", 0) > 0:
                        is_pact = bool(s.get("is_pact_magic", False) or ch.get("is_pact_magic"))
                        pact_tag = " Pact" if is_pact else ""
                        avail_slots.append(f"L{r}:{s.get('current', 0)}/{s.get('max', 0)}{pact_tag}")
                slot_str = f" | Spell Slots: [{', '.join(avail_slots)}]" if avail_slots else ""

                feats = ch.get("features_and_traits", [])
                feat_parts = [(f.get("name") if isinstance(f, dict) else str(f)).strip() for f in feats[:4] if f]
                feat_str = f" | Features: {', '.join(feat_parts)}" if feat_parts else ""

                init = ch.get("initiative", "")
                init_str = f", Init: {init}" if init else ""

                char_summaries.append(
                    f"{ch.get('name')}{lvl_xp} - {ch.get('archetype', 'Adventurer')} (Player: {ch.get('player_name', 'Player')} | "
                    f"HP: {hp.get('current', 0)}/{hp.get('max', 0)}, AC/Guard: {guard.get('current', 10)}{init_str}{cond_str}{slot_str}{wpn_str}{inv_str}{spell_str}{feat_str})"
                )
            lines.append("\n- Active Party Roster: \n  * " + "\n  * ".join(char_summaries))
        else:
            lines.append("\n- Active Party Roster: Empty (use `upsert_ttrpg_character` to add characters).")

        caller_id = state.get("party_caller_id")
        caller_name = chars.get(caller_id, {}).get("name") if (caller_id and chars and isinstance(chars.get(caller_id), dict)) else "None (Group Decision - Ask players who acts if unspecified)"
        lines.append(f"- Designated Party Caller: {caller_name}")

        # 4. Combat Initiative vs Free Exploration Mode
        self.clean_stale_combat_if_needed()
        ladder = state.get("initiative_order", [])
        turn_idx = state.get("active_turn_index", 0)
        round_num = state.get("current_round", 1)
        combat_active = self.is_combat_active()
        if combat_active:
            safe_idx = turn_idx if 0 <= turn_idx < len(ladder) else 0
            active_entity = ladder[safe_idx]
            active_turn_name = active_entity.get("name", "Unknown")
            is_player_turn = bool(active_entity.get("is_player", False))

            lines.append(f"\n[ACTIVE COMBAT ENCOUNTER (ROUND {round_num})]")
            lines.append(f"- CURRENT ACTIVE TURN: **{active_turn_name}** ({'PLAYER' if is_player_turn else 'HOSTILE ENEMY / MONSTER'})")

            ladder_lines = []
            for idx, e in enumerate(ladder):
                status_str = e.get("status", "Healthy")
                is_active_mark = "▶ CURRENT ACTIVE TURN" if idx == safe_idx else ""
                cur_hp = e.get("current_hp")
                max_hp = e.get("max_hp")
                hp_str = f"(HP: {cur_hp}/{max_hp})" if cur_hp is not None and max_hp is not None else ""
                type_str = "PLAYER" if e.get("is_player") else "MONSTER"
                ladder_lines.append(
                    f"  {idx + 1}. {e.get('name')} (Init: {e.get('roll')}) [{type_str}] [{status_str}] {hp_str} {is_active_mark}".strip()
                )
            lines.append("- Initiative Ladder & Status:\n" + "\n".join(ladder_lines))

            if not is_player_turn:
                lines.append(
                    f"\n* MANDATORY INITIATIVE ACTION — IT IS {active_turn_name.upper()}'S TURN (HOSTILE MONSTER):\n"
                    f"  - {active_turn_name} acts NOW under YOUR control as the Game Master.\n"
                    f"  - DO NOT ask the players 'What do you do?' or 'Does that hit your AC?'! Execute {active_turn_name}'s action directly: pick a target hero (e.g. Joe or Shadow), roll `roll_dice` for the attack, directly compare the roll to that hero's AC from the Party Roster above (e.g. Joe AC 15), roll damage on hit and apply it immediately via `modify_character_vitals`.\n"
                    f"  - NEVER reveal the monster's numerical HP! Describe damage and status qualitatively.\n"
                    f"  - Immediately call `advance_combat_turn()` after {active_turn_name}'s attack finishes!"
                )
            else:
                lines.append(
                    f"\n* MANDATORY INITIATIVE ACTION — IT IS {active_turn_name.upper()}'S TURN (PLAYER):\n"
                    f"  - Only {active_turn_name} takes their turn right now! Prompt {active_turn_name}: '{active_turn_name}, it is your turn on initiative! What do you do?'\n"
                    f"  - Offer action options ONLY for {active_turn_name}. Do NOT prompt other party members out of turn.\n"
                    f"  - After {active_turn_name}'s action resolves, call `advance_combat_turn()`."
                )
        else:
            lines.append(
                "\n[CURRENT GAMEPLAY MODE: FREE EXPLORATION & GROUP NARRATIVE (OUT OF COMBAT)]\n"
                "* COMBAT IS NOT ACTIVE: The party is currently out of combat (exploring, traveling, investigating, or interacting with NPCs).\n"
                "* INITIATIVE DOES NOT APPLY: The initiative ladder is STRICTLY for active combat encounters with hostile foes! When not in combat, initiative does NOT dictate the order of operations.\n"
                "* DO NOT RESTRICT ACTIONS OR RECOMMENDATIONS BY INITIATIVE:\n"
                "  - NEVER single out whoever was previously #1 on an initiative list or ask for individual turns in sequence when exploring!\n"
                "  - Address the party as a whole or the designated Party Caller (e.g. 'What would the party like to do? Joe, Sam, Shadow, Gale, how do you proceed?').\n"
                "  - Any player or character may speak, ask questions, inspect surroundings, or take action at any time in any natural order.\n"
                "  - In Helpful mode, provide suggestions for the GROUP or respond to whoever declared an action.\n"
                "* COMBAT INITIATION: Only when hostile enemies emerge and attack (e.g. Dryads ambushing at the Fey Portal) should you call `start_combat(enemies=['...'])` to activate combat initiative."
            )

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
            "\n[SCENE PROGRESSION & LOCATION TRACKING DIRECTIVE — MANDATORY]\n"
            "* Whenever the party transitions to a new location (e.g. Camp Gate -> Camp Jail Cell, Barracks -> Warehouse, Camp Highmore -> Jungle Edge), "
            "or when a major scene beat or encounter finishes:\n"
            "  YOU MUST IMMEDIATELY CALL `record_campaign_notes(location='<New Exact Location>', summary='<2-3 sentence recap>', last_action='<action>')` in that turn!\n"
            "* NEVER narrate arriving in a cell or moving across the settlement without recording the new location. This keeps the party's current location and recap synchronized across reloads and restarts!"
        )

        # Party Spatial & Separation Status
        sep_chars = [c for c in chars.values() if c.get("is_separated")]
        if sep_chars:
            lines.append("\n[PARTY SPATIAL & SEPARATION STATUS — SPLIT PARTY ALERT]")
            lines.append(f"- Primary Party Location: {loc or 'Unknown'}")
            for sc in sep_chars:
                lines.append(f"- ⚠️ SEPARATED: {sc.get('name')} is isolated at '{sc.get('location', 'Separated Area')}'!")
            lines.append(
                "* SPATIAL PROXIMITY & SEPARATION RULES:\n"
                "  - When a character is separated in a different area (e.g. Joe ran off into the deep maze while Sam is at the entrance), "
                "they CANNOT cast short-range spells (like Command, Fireball, Cure Wounds), attack, touch, or converse naturally with characters in other areas!\n"
                "  - Only long-range/planar magic (Sending, Teleport, Scrying, Dream, Message) can cross between separate areas.\n"
                "  - If a player tries to interact with a separated character or monster, enforce the proximity barrier and explain they are in different locations!\n"
                "  - To move or reunite party members, use `update_entity_location(name='Joe', location='...', is_separated=False)` or `update_entity_location(name='all', location='...', reunite_party=True)`."
            )

        # Downed Characters & Death Saves Directive
        downed_chars = [c for c in chars.values() if c.get("vitals", {}).get("health", {}).get("current", 0) <= 0]
        if downed_chars:
            lines.append("\n[DOWNED CHARACTERS & DEATH SAVING THROWS DIRECTIVE]")
            for dc in downed_chars:
                ds = dc.get("death_saves", {})
                succs = ds.get("successes", 0)
                fails = ds.get("failures", 0)
                ds_stat = ds.get("status", "dying")
                lines.append(f"- 💀 DOWNED HERO: {dc.get('name')} is at 0 HP [Status: {ds_stat.upper()}, Saves: {succs}/3 Successes, {fails}/3 Failures].")
            lines.append(
                "* D&D 5e DEATH SAVING THROW RULES:\n"
                "  1. On a downed hero's turn, they roll a DC 10 Death Saving Throw: `roll_dice(formula='1d20', character_name='<Hero>', purpose='Death Saving Throw')`:\n"
                "     - Nat 20: Immediately regains 1 HP and wakes up!\n"
                "     - 10-19: 1 Success (3 successes = Stable at 0 HP).\n"
                "     - 2-9: 1 Failure (3 failures = Dead).\n"
                "     - Nat 1: 2 Failures!\n"
                "  2. Downed heroes are UNCONSCIOUS and INCAPACITATED: they cannot take actions, attack, or cast spells!\n"
                "  3. Taking damage at 0 HP inflicts 1 Death Save failure (or 2 if critical hit).\n"
                "  4. Any healing restores HP above 0, immediately removing Unconscious and resetting death saves."
            )

        # Active Conditions Directive
        cond_chars = [c for c in chars.values() if c.get("conditions")]
        if cond_chars:
            lines.append("\n[ACTIVE CONDITIONS & MECHANICAL PENALTIES]")
            for cc in cond_chars:
                lines.append(f"- {cc.get('name')}: {', '.join(cc.get('conditions', []))}")
            lines.append(
                "* CONDITION DIRECTIVE:\n"
                "  - Drunk / Intoxicated / Poisoned / Frightened: Disadvantage on attack rolls and ability checks.\n"
                "  - Confused: Disadvantage on Int/Wis checks, cannot take reactions.\n"
                "  - Charmed: Cannot attack or target the charmer with harmful spells/abilities.\n"
                "  - Unconscious / Stunned / Paralyzed / Petrified / Commanded: Halt: Incapacitated — cannot take actions or move."
            )

        return "\n".join(lines) + "\n\n"

    # --- Cryptographic Dice Engine ---
    @staticmethod
    def parse_and_roll(formula: str, roll_type: str = "normal", character_name: Optional[str] = None) -> Dict[str, Any]:
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

        clean_char = str(character_name).strip() if character_name else None
        char_prefix = f"[{clean_char}] " if clean_char else ""

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
            res = {
                "status": "success",
                "roll_id": secrets.token_hex(6),
                "formula": formula,
                "roll_type": roll_type,
                "sides": sides,
                "kept_die": chosen_roll,
                "dropped_die": dropped_roll,
                "modifier": modifier,
                "total": total,
                "summary": f"{char_prefix}Rolled [{roll_a}, {roll_b}] ({roll_type}) -> Kept {chosen_roll} + ({modifier}) = {total}"
            }
            if clean_char:
                res["character_name"] = clean_char
            return res

        # Standard Multi-Dice Roll
        rolls = [secrets.randbelow(sides) + 1 for _ in range(num_dice)]
        total = sum(rolls) + modifier

        res = {
            "status": "success",
            "roll_id": secrets.token_hex(6),
            "formula": formula,
            "roll_type": "normal",
            "sides": sides,
            "dice_rolled": rolls,
            "modifier": modifier,
            "total": total,
            "summary": f"{char_prefix}Rolled {rolls} + ({modifier}) = {total}" if modifier != 0 else f"{char_prefix}Rolled {rolls} = {total}"
        }
        if clean_char:
            res["character_name"] = clean_char
        return res

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
        raw_vitals_in = char_payload.get("vitals")
        raw_v_health = raw_vitals_in.get("health") if isinstance(raw_vitals_in, dict) and isinstance(raw_vitals_in.get("health"), dict) else {}

        default_max_hp = self._safe_int(prev_vitals.get("health", {}).get("max"), 25)
        raw_max_hp = char_payload.get("max_health") or char_payload.get("hp_max") or char_payload.get("hp") or raw_v_health.get("max")
        max_hp = max(1, self._safe_int(raw_max_hp, default_max_hp))
        default_cur_hp = self._safe_int(prev_vitals.get("health", {}).get("current"), max_hp)
        raw_cur_hp = char_payload.get("current_health") or char_payload.get("hp_current") or char_payload.get("health") or raw_v_health.get("current")
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
        if inv_resolved and isinstance(inv_resolved, list):
            cleaned_inv = []
            for item in inv_resolved:
                if isinstance(item, dict):
                    inm = str(item.get("name") or "").strip()
                    if "(" in inm and ")" not in inm:
                        item["name"] = inm + ")"
                    cleaned_inv.append(item)
                else:
                    s = str(item).strip()
                    if "(" in s and ")" not in s:
                        s += ")"
                    cleaned_inv.append(s)
            inv_resolved = cleaned_inv

        char_arch_val = str(_get_val("archetype", "Adventurer")).strip()
        calc_lvl = max(1, self._safe_int(_get_val("level", 1), 1))
        cls_lvl_check = str(char_payload.get("class_and_level") or (prev.get("class_and_level") if prev else None) or char_arch_val).strip()
        if "/" in cls_lvl_check:
            from core.parsers.dnd5e_parser import parse_multiclass_string
            m_entries = parse_multiclass_string(cls_lvl_check)
            if len(m_entries) > 1:
                calc_lvl = max(calc_lvl, sum(int(c.get("level", 1)) for c in m_entries))

        char_entry = {
            "id": char_id,
            "name": name,
            "player_name": str(_get_val("player_name", "Player")).strip(),
            "archetype": char_arch_val,
            "is_player": bool(_get_val("is_player", True)),
            "level": calc_lvl,
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
            "location": str(_get_val("location", st.get("current_location", ""))),
            "is_separated": bool(_get_val("is_separated", False)),
            "death_saves": dict(_get_val("death_saves", {"successes": 0, "failures": 0, "status": "none"})),
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
            "weight_carried", "encumbered", "push_drag_lift", "gender", "age", "size", "height", "weight", "alignment", "faith", "skin", "eyes", "hair",
            "creature_type", "backstory", "additional_notes",
            "personality_traits", "ideals", "bonds", "flaws", "appearance", "allies",
            "inspiration", "ability_save_dc", "attuned_magic_items", "attuned_items",
            "armor_equipped", "shield_equipped", "stealth_disadvantage",
            "subclass", "subclasses", "feats", "class_levels", "ruleset_edition", "max_prepared_leveled_spells",
            "spell_slots", "roleplay_anchors", "native_system_data"
        ):
            if opt_key in char_payload and char_payload[opt_key] is not None:
                char_entry[opt_key] = char_payload[opt_key]
            elif opt_key in prev and prev[opt_key] is not None:
                char_entry[opt_key] = prev[opt_key]

        chars[char_id] = char_entry
        st.setdefault("entity_locations", {})[name] = char_entry["location"]
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

        from core.compendiums.dnd_advancement import XP_THRESHOLDS, apply_level_up, proficiency_bonus_for_level

        cur_level = int(char_obj.get("level", 1))
        cur_xp = int(char_obj.get("experience_points", 0))

        act = (action or "award_xp").lower().strip()
        level_up_ready = False

        if act == "award_xp":
            add_xp = max(0, int(amount_or_level))
            new_xp = cur_xp + add_xp
            char_obj["experience_points"] = new_xp
            next_req = XP_THRESHOLDS.get(min(20, cur_level + 1), 355000)
            if cur_level < 20 and new_xp >= next_req:
                level_up_ready = True
                msg = f"Awarded {add_xp} XP to {char_obj['name']} (Total: {new_xp} XP). QUALIFIES FOR LEVEL UP to Level {cur_level + 1}! Next step: open the Guided Level-Up Wizard or call `level_up_character`."
            elif cur_level >= 20:
                msg = f"Awarded {add_xp} XP to {char_obj['name']} (Total: {new_xp} XP, Max Level 20)."
            else:
                msg = f"Awarded {add_xp} XP to {char_obj['name']}. Current: {new_xp} / {next_req} XP for Level {cur_level + 1}."

        elif act == "level_up":
            target_level = cur_level + 1 if (amount_or_level <= 0 or amount_or_level <= cur_level) else min(20, int(amount_or_level))
            ruleset_low = str(char_obj.get("ruleset") or (self.manifest.get("campaigns") or {}).get(self.manifest.get("active_campaign") or "", {}).get("system_preset") or "dnd_5e").lower()
            is_dnd = any(k in ruleset_low for k in ("dnd", "5e", "heroic_d20", "fantasy")) or ("Strength" in (char_obj.get("attributes") or {}))
            if is_dnd:
                ed = self._get_campaign_edition()
                steps = max(1, target_level - cur_level)
                last_res = None
                for _ in range(steps):
                    last_res = apply_level_up(char_obj, {"edition": ed, "hp_mode": "fixed"})
                if last_res and last_res.get("status") == "success":
                    msg = last_res["message"]
                else:
                    return last_res or {"status": "error", "message": "Failed to level up character."}
            else:
                char_obj["level"] = target_level
                vigor = char_obj.get("attributes", {}).get("Vigor", 10)
                con_mod = (vigor - 10) // 2
                hp_gain = max(1, 6 + con_mod)
                vitals = char_obj.setdefault("vitals", {})
                hp_data = vitals.setdefault("health", {"current": 20, "max": 20, "label": "Health"})
                hp_data["max"] += hp_gain
                hp_data["current"] += hp_gain
                hd_meter = char_obj.setdefault("custom_meters", {}).setdefault("hit_dice", {"current": cur_level, "max": cur_level, "label": "Hit Dice"})
                hd_meter["max"] = target_level
                hd_meter["current"] = target_level
                prof = f"+{proficiency_bonus_for_level(target_level)}"
                char_obj["prof_bonus"] = prof
                msg = f"{char_obj['name']} leveled up from Level {cur_level} to Level {target_level}! Max HP increased by {hp_gain} (new max: {hp_data['max']}). Proficiency Bonus: {prof}."

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
        if not self._ensure_active_state():
            return {"status": "error", "message": "No active campaign loaded."}

        char_id, char_data = self._find_character(character_name)
        if not char_data:
            # Check if this character is a combatant on the active initiative ladder
            comb = self._find_combatant(character_name)
            if not comb:
                return {"status": "error", "message": f"Character '{character_name}' not found."}

            stat_normalized = stat_key.lower().strip()
            if stat_normalized in ("health", "hp"):
                max_hp = int(comb.get("max_hp", 20))
                cur_hp = int(comb.get("current_hp", max_hp))
                new_hp = max(0, min(max_hp, cur_hp + delta))
                comb["current_hp"] = new_hp
                comb["max_hp"] = max_hp
                is_p = bool(comb.get("is_player", False))
                conds = comb.setdefault("conditions", [])
                ds = comb.setdefault("death_saves", {"successes": 0, "failures": 0, "status": "none"}) if is_p else None

                if is_p:
                    if new_hp == 0:
                        if cur_hp > 0:
                            if "Unconscious" not in conds:
                                conds.append("Unconscious")
                            if "Prone" not in conds:
                                conds.append("Prone")
                            ds["status"] = "dying"
                            ds["successes"] = 0
                            ds["failures"] = 0
                        elif delta < 0:
                            ds["failures"] = ds.get("failures", 0) + 1
                            if ds.get("status") == "stable":
                                ds["status"] = "dying"
                            if ds["failures"] >= 3:
                                ds["status"] = "dead"
                                if "Dead" not in conds:
                                    conds.append("Dead")
                    elif new_hp > 0 and cur_hp == 0:
                        if "Unconscious" in conds:
                            conds.remove("Unconscious")
                        if "Dead" in conds:
                            conds.remove("Dead")
                        if "Stable" in conds:
                            conds.remove("Stable")
                        ds["status"] = "none"
                        ds["successes"] = 0
                        ds["failures"] = 0
                    status = self.compute_health_status(new_hp, max_hp, is_player=True, death_saves=ds)
                else:
                    status = self.compute_health_status(new_hp, max_hp, is_player=False)
                    if new_hp == 0 and "Defeated" not in conds:
                        conds.append("Defeated")
                    elif new_hp > 0 and "Defeated" in conds:
                        conds.remove("Defeated")

                comb["status"] = status

                self.save_active_campaign_state()
                return {
                    "status": "success",
                    "character_id": comb.get("entity_id"),
                    "character_name": comb.get("name"),
                    "stat": "health",
                    "old_value": cur_hp,
                    "new_value": new_hp,
                    "max_value": max_hp,
                    "delta": delta,
                    "health_status": status,
                    "is_combatant": True,
                    "is_player": comb.get("is_player", False),
                    "summary": f"{comb.get('name')} health adjusted by {delta:+d} ({cur_hp} -> {new_hp}/{max_hp}) [{status}]."
                }
            elif stat_normalized in ("guard", "ac", "defense", "armor_class"):
                old_ac = int(comb.get("armor_class", 10))
                new_ac = max(0, old_ac + delta)
                comb["armor_class"] = new_ac
                self.save_active_campaign_state()
                return {
                    "status": "success",
                    "character_id": comb.get("entity_id"),
                    "character_name": comb.get("name"),
                    "stat": "armor_class",
                    "old_value": old_ac,
                    "new_value": new_ac,
                    "max_value": new_ac,
                    "delta": delta,
                    "health_status": comb.get("status", "Healthy"),
                    "is_combatant": True,
                    "is_player": comb.get("is_player", False),
                    "summary": f"{comb.get('name')} AC adjusted by {delta:+d} ({old_ac} -> {new_ac})."
                }
            else:
                return {"status": "error", "message": f"Stat '{stat_key}' not supported on combatant {character_name}."}

        target_dict = char_data.get("custom_meters" if is_custom_meter else "vitals", {})
        if stat_key not in target_dict:
            return {"status": "error", "message": f"Stat '{stat_key}' not found on {character_name}."}

        entry = target_dict[stat_key]
        old_val = entry["current"]
        max_val = entry.get("max", 9999)
        new_val = max(0, min(max_val, old_val + delta))
        entry["current"] = new_val

        # Auto-apply Unconscious / Prone condition at 0 HP for characters and manage death saves
        if stat_key in ("health", "hp"):
            conds = char_data.setdefault("conditions", [])
            ds = char_data.setdefault("death_saves", {"successes": 0, "failures": 0, "status": "none"})
            if new_val == 0:
                if old_val > 0:
                    if "Unconscious" not in conds:
                        conds.append("Unconscious")
                    if "Prone" not in conds:
                        conds.append("Prone")
                    char_data["death_saves"] = {"successes": 0, "failures": 0, "status": "dying"}
                elif delta < 0:
                    # Damage at 0 HP adds 1 failure (RAW 5e)
                    ds["failures"] = ds.get("failures", 0) + 1
                    if ds.get("status") == "stable":
                        ds["status"] = "dying"
                    if ds["failures"] >= 3:
                        ds["status"] = "dead"
                        if "Dead" not in conds:
                            conds.append("Dead")
            elif new_val > 0 and old_val == 0:
                if "Unconscious" in conds:
                    conds.remove("Unconscious")
                if "Dead" in conds:
                    conds.remove("Dead")
                if "Stable" in conds:
                    conds.remove("Stable")
                char_data["death_saves"] = {"successes": 0, "failures": 0, "status": "none"}

        # Synchronize with initiative ladder if character is participating in combat
        status = self.compute_health_status(new_val, max_val, is_player=True, death_saves=char_data.get("death_saves"))
        for comb in self.active_state.get("initiative_order", []):
            if comb.get("name", "").lower() == char_data["name"].lower() or comb.get("entity_id") == char_id:
                comb["current_hp"] = new_val
                comb["max_hp"] = max_val
                comb["status"] = status
                comb["death_saves"] = char_data.get("death_saves")
                comb["conditions"] = list(char_data.get("conditions", []))
                break

        self.save_active_campaign_state()
        return {
            "status": "success",
            "character_id": char_id,
            "character_name": char_data["name"],
            "stat": stat_key,
            "old_value": old_val,
            "new_value": new_val,
            "max_value": max_val,
            "delta": delta,
            "health_status": status
        }

    def update_condition(self, character_name: str, condition: str, active: bool) -> Dict[str, Any]:
        """Adds or removes conditions (e.g., Poisoned, Blinded, Stunned, Stable, Dead)."""
        cond_raw = condition.strip()
        cond_clean = cond_raw.capitalize() if cond_raw.isalpha() else cond_raw

        char_id, char_data = self._find_character(character_name)
        if not char_data:
            comb = self._find_combatant(character_name)
            if not comb:
                return {"status": "error", "message": f"Character '{character_name}' not found."}
            conditions = set(comb.get("conditions", []))
            if active:
                conditions.add(cond_clean)
            else:
                conditions.discard(cond_clean)
            comb["conditions"] = sorted(list(conditions))
            self.save_active_campaign_state()
            return {
                "status": "success",
                "character_name": comb["name"],
                "active_conditions": comb["conditions"]
            }

        conditions = set(char_data.get("conditions", []))
        ds = char_data.setdefault("death_saves", {"successes": 0, "failures": 0, "status": "none"})
        hp_cur = char_data.get("vitals", {}).get("health", {}).get("current", 20)
        hp_max = char_data.get("vitals", {}).get("health", {}).get("max", 20)

        if active:
            conditions.add(cond_clean)
            if cond_clean.lower() == "stable":
                ds["status"] = "stable"
                ds["successes"] = max(3, ds.get("successes", 0))
            elif cond_clean.lower() == "dead":
                ds["status"] = "dead"
                ds["failures"] = max(3, ds.get("failures", 0))
        else:
            conditions.discard(cond_clean)
            if cond_clean.lower() == "stable" and ds.get("status") == "stable":
                ds["status"] = "dying" if hp_cur <= 0 else "none"
            elif cond_clean.lower() == "dead" and ds.get("status") == "dead":
                ds["status"] = "dying" if hp_cur <= 0 else "none"

        char_data["conditions"] = sorted(list(conditions))

        # Synchronize with initiative ladder if present
        for comb in self.active_state.get("initiative_order", []):
            if comb.get("name", "").lower() == char_data["name"].lower() or comb.get("entity_id") == char_id:
                comb["conditions"] = list(char_data["conditions"])
                comb["death_saves"] = ds
                comb["status"] = self.compute_health_status(hp_cur, hp_max, is_player=True, death_saves=ds)
                break

        self.save_active_campaign_state()

        return {
            "status": "success",
            "character_name": char_data["name"],
            "active_conditions": char_data["conditions"],
            "death_saves": ds
        }

    def ensure_character_spell_slots(self, char_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Guarantees that a character has their official D&D 5e spell slots table populated
        based on their class levels (including multiclassing and Warlock pact magic).
        If spell_slots is None, empty, or all zeros, computes and populates them.
        """
        if not char_data or not isinstance(char_data, dict):
            return {}

        native = char_data.get("native_system_data") if isinstance(char_data.get("native_system_data"), dict) else {}
        slots = char_data.get("spell_slots") or native.get("spell_slots")
        if slots and isinstance(slots, dict) and not char_data.get("spell_slots"):
            char_data["spell_slots"] = slots

        has_any_slots = False
        if slots and isinstance(slots, dict):
            has_any_slots = any(
                isinstance(v, dict) and v.get("max", 0) > 0
                for v in slots.values()
            )

        if not has_any_slots:
            try:
                from core.compendiums.dnd_advancement import parse_character_classes, compute_multiclass_spell_slots
                cls_map, sub_map = parse_character_classes(char_data)
                if cls_map:
                    ed = "2014" if str(char_data.get("ruleset_edition", "2024")).strip() == "2014" else "2024"
                    slot_res = compute_multiclass_spell_slots(cls_map, edition=ed, subclasses=sub_map)
                    char_data["spell_slots"] = copy.deepcopy(slot_res.get("spell_slots", {}))
                    if slot_res.get("has_pact_magic"):
                        char_data["is_pact_magic"] = True
                        char_data["pact_slots"] = slot_res.get("pact_slots")
                    if "native_system_data" in char_data and isinstance(char_data["native_system_data"], dict):
                        char_data["native_system_data"]["spell_slots"] = copy.deepcopy(char_data["spell_slots"])
                    slots = char_data["spell_slots"]
            except Exception as e:
                logger.warning(f"[TTRPG] Auto spell slot calculation failed for '{char_data.get('name')}': {e}")

        # Sanitize spell list and enforce preparation cap if over limit
        sp_list = char_data.get("spells") or native.get("spells")
        if sp_list and isinstance(sp_list, list):
            try:
                from core.parsers.spell_registry import sanitize_parsed_spells
                clean_sp = sanitize_parsed_spells(sp_list)
                max_prep = char_data.get("max_prepared_leveled_spells")
                if max_prep is None and isinstance(native, dict):
                    max_prep = native.get("max_prepared_leveled_spells")
                if isinstance(max_prep, int) and max_prep >= 0:
                    prep_count = sum(1 for s in clean_sp if isinstance(s, dict) and not s.get("is_cantrip") and not s.get("is_always_prepared") and s.get("prepared"))
                    if prep_count > max_prep:
                        kept = 0
                        for s in clean_sp:
                            if isinstance(s, dict) and not s.get("is_cantrip") and not s.get("is_always_prepared"):
                                if kept < max_prep and s.get("prepared"):
                                    kept += 1
                                else:
                                    s["prepared"] = False
                char_data["spells"] = clean_sp
                if "native_system_data" in char_data and isinstance(char_data["native_system_data"], dict):
                    char_data["native_system_data"]["spells"] = copy.deepcopy(clean_sp)
            except Exception as e:
                logger.warning(f"[TTRPG] Spell list sanitization failed for '{char_data.get('name')}': {e}")

        return slots or {}

    def cast_character_spell(
        self,
        character_name: str,
        spell_name: str,
        spell_level: int = 0,
        slot_level: Optional[int] = None,
        target_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Casts a spell for a character, deducting an expendable spell slot.
        Enforces condition restrictions (incapacitation, charm) and spatial proximity checks.
        Supports:
        - Cantrips (level 0): at-will, costs 0 slots.
        - Exact-level slot deduction.
        - Upcasting & Warlock Pact Magic.
        """
        if not self._ensure_active_state():
            return {"status": "error", "message": "No active campaign loaded."}

        char_id, char = self._find_character(character_name)
        if not char:
            return {"status": "error", "message": f"Character '{character_name}' not found."}

        # 1. Incapacitation check on caster
        conds = char.get("conditions", [])
        for c in conds:
            c_base = c.split("(")[0].strip()
            eff = CONDITION_EFFECTS.get(c_base, {})
            if eff.get("incapacitated"):
                return {
                    "status": "error",
                    "message": f"Cannot cast '{spell_name}'! {char['name']} is currently {c} and incapacitated."
                }

        # 2. Charm check: cannot target charmer with harmful spells
        if target_name:
            t_clean = target_name.strip().lower()
            for c in conds:
                if "charm" in c.lower():
                    if t_clean in c.lower() or c.strip().lower() == "charmed":
                        return {
                            "status": "error",
                            "message": f"Cannot target {target_name} with '{spell_name}'! {char['name']} is Charmed by {target_name} and cannot target them with harmful abilities or spells."
                        }

        # 3. Spatial Proximity check
        if target_name:
            prox = self.check_entity_proximity(char["name"], target_name, action_or_spell=spell_name)
            if not prox.get("allowed", True):
                return {
                    "status": "error",
                    "message": prox.get("reason", f"{char['name']} is not near {target_name} to cast '{spell_name}'.")
                }

        slots = self.ensure_character_spell_slots(char)
        native = char.get("native_system_data", {})
        spells = native.get("spells") or char.get("spells", [])

        # Check if spell is known
        clean_target = re.sub(r'\s*\[r\]', '', spell_name, flags=re.IGNORECASE).strip().lower()
        spell_entry = None
        for s in spells:
            s_name = (s.get("name") if isinstance(s, dict) else str(s)).strip()
            s_clean = re.sub(r'\s*\[r\]', '', s_name, flags=re.IGNORECASE).strip().lower()
            if s_name.lower() == spell_name.lower() or s_clean == clean_target:
                spell_entry = s if isinstance(s, dict) else {"name": s_name}
                break

        # Verification for preparation
        cls_str = str(native.get("class_level") or char.get("archetype") or char.get("class") or "").lower()
        is_wizard = "wizard" in cls_str
        if spell_level > 0 and spell_entry and isinstance(spell_entry, dict):
            is_ritual = bool(
                spell_entry.get("is_ritual", False)
                or "[r]" in str(spell_entry.get("name", "")).lower()
                or "[r]" in spell_name.lower()
            )
            if spell_entry.get("prepared") is False and not (is_wizard and is_ritual):
                return {
                    "status": "error",
                    "message": f"Cannot cast '{spell_name}'! It is not currently prepared for today."
                }

        # Deduct spell slot for leveled spells
        expended_lvl = spell_level
        if spell_level > 0:
            if slot_level and 1 <= slot_level <= 9:
                slot_key = f"level_{slot_level}"
                if slots.get(slot_key, {}).get("current", 0) <= 0:
                    return {
                        "status": "error",
                        "message": f"No Level {slot_level} spell slots remaining for {char['name']}!"
                    }
                expended_lvl = slot_level
            else:
                # Check exact level first
                exact_key = f"level_{spell_level}"
                if slots.get(exact_key, {}).get("current", 0) > 0:
                    expended_lvl = spell_level
                else:
                    # Check higher-level slots for upcasting or Warlock Pact Magic (which are L5 slots)
                    available_ranks = [
                        lvl for lvl in range(spell_level + 1, 10)
                        if slots.get(f"level_{lvl}", {}).get("current", 0) > 0
                    ]
                    if not available_ranks:
                        return {
                            "status": "error",
                            "message": f"No Level {spell_level}+ spell slots remaining for {char['name']}!"
                        }
                    # Pick lowest available slot >= spell_level (e.g. 5th-level pact slot)
                    expended_lvl = available_ranks[0]

            chosen_key = f"level_{expended_lvl}"
            slots[chosen_key]["current"] -= 1

            if "native_system_data" in char and isinstance(char["native_system_data"], dict):
                char["native_system_data"]["spell_slots"] = copy.deepcopy(slots)

        self.save_active_campaign_state()

        rem = slots.get(f"level_{expended_lvl}", {}).get("current", "At-Will") if spell_level > 0 else "At-Will"
        is_pact = slots.get(f"level_{expended_lvl}", {}).get("is_pact_magic", False)
        pact_note = " (Pact Magic)" if is_pact else (" (Upcast)" if expended_lvl > spell_level else "")
        summary_msg = f"{char['name']} cast '{spell_name}' (Level {spell_level}) using a Level {expended_lvl} slot{pact_note}. Slots remaining: {rem}."
        logger.info(f"[TTRPG] {summary_msg}")

        ret = {
            "status": "success",
            "character": char["name"],
            "spell": spell_name,
            "level": spell_level,
            "slot_level_expended": expended_lvl,
            "slots_remaining": rem,
            "is_pact_magic": is_pact,
            "summary": summary_msg
        }
        if target_name:
            ret["target_name"] = target_name
        return ret

    def manage_character_inventory(
        self,
        character_name: str,
        action: str = "add",
        items: Optional[List[str]] = None,
        weapons: Optional[List[Dict[str, str]]] = None,
        spells: Optional[List[Any]] = None,
        reason: Optional[str] = None,
        storage_location: Optional[str] = None,
        location_knowledge: Optional[str] = None,
        status: Optional[str] = None,
        holder_or_creature: Optional[str] = None,
        salvageable: Optional[bool] = None,
        discovery_clue: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Adds, removes, disarms, discovers, destroys, or retrieves items, weapons, and spells from character(s) in the active campaign.
        Enables both the user and Aether DM to update gear when items are found, looted, bought, used, lost,
        swallowed by beasts, destroyed in lava, or confiscated/disarmed upon arrest (with knowledge tracking: unknown vs discovered).
        """
        if not self._ensure_active_state():
            return {"status": "error", "message": "No active campaign loaded."}

        self.active_state.setdefault("stored_items", [])
        stored_items_list = self.active_state["stored_items"]

        # Support party-wide operations (e.g. 'all', 'party', 'everyone')
        target_chars: List[Tuple[str, Dict[str, Any]]] = []
        is_party_wide = (character_name or "").lower().strip() in ("all", "party", "everyone", "all_characters", "team")
        if is_party_wide:
            chars_dict = self.active_state.get("characters", {})
            if not chars_dict:
                return {"status": "error", "message": "No characters in party roster."}
            target_chars = list(chars_dict.items())
        else:
            cid, cdata = self._find_character(character_name)
            if not cdata:
                return {"status": "error", "message": f"Character '{character_name}' not found."}
            target_chars = [(cid, cdata)]

        action_norm = (action or "add").lower().strip()

        # Handle Retrieve / Reclaim from storage location
        if action_norm in ("retrieve", "reclaim", "recover", "salvage"):
            reclaimed_items: List[str] = []
            reclaimed_weapons: List[str] = []
            rejected_destroyed: List[str] = []
            target_char_ids = {c[0]: c[1] for c in target_chars}

            # Filter items from stored_items
            remaining_stored = []
            for entry in list(stored_items_list):
                owner_id = entry.get("original_owner_id")
                owner_name = entry.get("original_owner", "").lower()
                matches_owner = is_party_wide or (owner_id in target_char_ids) or any(c[1].get("name", "").lower() == owner_name for c in target_chars)
                matches_loc = True
                if storage_location:
                    loc_low = storage_location.lower().strip()
                    ent_loc = (entry.get("storage_location") or "").lower()
                    ent_holder = (entry.get("holder_or_creature") or "").lower()
                    matches_loc = (loc_low in ent_loc) or (ent_loc in loc_low) or (loc_low in ent_holder) or (ent_holder in loc_low)

                if holder_or_creature:
                    hold_low = holder_or_creature.lower().strip()
                    ent_holder = (entry.get("holder_or_creature") or "").lower()
                    matches_holder = (hold_low in ent_holder) or (ent_holder in hold_low)
                    matches_loc = matches_loc and matches_holder

                # If specific item/weapon names requested
                matches_item = True
                entry_name = entry.get("name", "").lower()
                if items or weapons:
                    req_names = [str(x.get("name") if isinstance(x, dict) else x).lower().strip() for x in (items or []) + (weapons or [])]
                    matches_item = any(rn in entry_name or entry_name in rn for rn in req_names)

                if matches_owner and matches_loc and matches_item:
                    # Check if item is destroyed or unsalvageable
                    if entry.get("status") == "destroyed" or entry.get("salvageable") is False:
                        rejected_destroyed.append(entry.get("name", "Unknown item"))
                        remaining_stored.append(entry)
                        continue

                    # Restore item to target character
                    dest_char = target_char_ids.get(owner_id)
                    if not dest_char:
                        dest_char = next((c[1] for c in target_chars if c[1].get("name", "").lower() == owner_name), target_chars[0][1])

                    if entry.get("type") == "weapon":
                        dest_wpns = dest_char.setdefault("weapons", [])
                        dest_wpns.append(entry.get("data") or {"name": entry.get("name", "Weapon"), "atk_bonus": "+0", "damage": "1d4"})
                        reclaimed_weapons.append(f"{dest_char['name']}: {entry['name']}")
                    else:
                        dest_inv = dest_char.setdefault("inventory", [])
                        dest_inv.append(entry.get("data") or entry.get("name", "Item"))
                        reclaimed_items.append(f"{dest_char['name']}: {entry['name']}")
                else:
                    remaining_stored.append(entry)

            self.active_state["stored_items"] = remaining_stored
            self.save_active_campaign_state()
            msg = f"Successfully retrieved {len(reclaimed_weapons)} weapon(s) and {len(reclaimed_items)} item(s) from storage."
            if rejected_destroyed:
                msg += f" Note: {len(rejected_destroyed)} item(s) could not be retrieved because they are destroyed or unsalvageable ({', '.join(rejected_destroyed)})."
            logger.info(f"[TTRPG] Retrieved stored gear: Weapons={reclaimed_weapons}, Items={reclaimed_items}, RejectedDestroyed={rejected_destroyed}. Remaining stored: {len(remaining_stored)}")

            return {
                "status": "success",
                "action": "retrieve",
                "reclaimed_weapons": reclaimed_weapons,
                "reclaimed_items": reclaimed_items,
                "rejected_destroyed": rejected_destroyed,
                "remaining_stored_count": len(remaining_stored),
                "message": msg
            }

        # Handle Disarm / Confiscate (Strips weapons, records storage location & knowledge)
        if action_norm in ("disarm", "confiscate"):
            default_loc = storage_location or (self.active_state.get("current_location") and f"{self.active_state['current_location']} - Evidence Locker") or "Camp Highmore Guard Barracks - Evidence Chest"
            effective_knowledge = (location_knowledge or "unknown").strip().lower()
            effective_status = (status or "confiscated").strip().lower()
            effective_salvageable = True if salvageable is None else bool(salvageable)
            effective_holder = holder_or_creature or ""
            effective_clue = discovery_clue or ""

            disarmed_weapons: List[str] = []
            disarmed_items: List[str] = []

            for char_id, char_data in target_chars:
                current_wpns = char_data.setdefault("weapons", [])
                current_inv = char_data.setdefault("inventory", [])

                # Disarm manufactured weapons, keep unarmed strike
                weapons_to_keep = []
                for w in list(current_wpns):
                    w_name = (w.get("name") if isinstance(w, dict) else str(w)).strip()
                    if w_name.lower() in ("unarmed strike", "fist", "fists", "punch", "kick", "natural weapon", "bite"):
                        weapons_to_keep.append(w)
                    else:
                        # Store in world cache
                        stored_entry = {
                            "id": f"stored_{uuid.uuid4().hex[:8]}",
                            "name": w_name,
                            "type": "weapon",
                            "original_owner": char_data["name"],
                            "original_owner_id": char_id,
                            "data": copy.deepcopy(w) if isinstance(w, dict) else {"name": w_name, "atk_bonus": "+0", "damage": "1d4"},
                            "storage_location": default_loc,
                            "status": effective_status,
                            "location_knowledge": effective_knowledge,
                            "holder_or_creature": effective_holder,
                            "salvageable": effective_salvageable,
                            "discovery_clue": effective_clue,
                            "stored_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "reason": reason or "Disarmed upon arrest / surrender"
                        }
                        stored_items_list.append(stored_entry)
                        disarmed_weapons.append(f"{char_data['name']}: {w_name}")

                # Ensure character has at least Unarmed Strike so sheet stays functional
                if not any(w.get("name", "").lower() in ("unarmed strike", "fist", "fists") for w in weapons_to_keep if isinstance(w, dict)):
                    weapons_to_keep.insert(0, {"name": "Unarmed Strike", "atk_bonus": "+0", "damage": "1d4 bludgeoning", "notes": "Natural attack"})
                char_data["weapons"] = weapons_to_keep

                # Also confiscate specific items if passed
                if items:
                    for it in items:
                        target_name = (it.get("name") if isinstance(it, dict) else str(it)).strip().lower()
                        for idx, cur_it in enumerate(list(current_inv)):
                            cur_name = (cur_it.get("name") if isinstance(cur_it, dict) else str(cur_it)).strip().lower()
                            if cur_name == target_name or target_name in cur_name:
                                removed = current_inv.pop(idx)
                                rem_name = removed.get("name", target_name) if isinstance(removed, dict) else str(removed)
                                stored_entry = {
                                    "id": f"stored_{uuid.uuid4().hex[:8]}",
                                    "name": rem_name,
                                    "type": "item",
                                    "original_owner": char_data["name"],
                                    "original_owner_id": char_id,
                                    "data": copy.deepcopy(removed),
                                    "storage_location": default_loc,
                                    "status": effective_status,
                                    "location_knowledge": effective_knowledge,
                                    "holder_or_creature": effective_holder,
                                    "salvageable": effective_salvageable,
                                    "discovery_clue": effective_clue,
                                    "stored_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "reason": reason or "Confiscated upon arrest"
                                }
                                stored_items_list.append(stored_entry)
                                disarmed_items.append(f"{char_data['name']}: {rem_name}")
                                break

            self.save_active_campaign_state()
            logger.info(f"[TTRPG] Disarmed party/character(s) at '{default_loc}' [knowledge={effective_knowledge}]: Weapons={disarmed_weapons}, Items={disarmed_items}. Total stored in world cache: {len(stored_items_list)}")

            return {
                "status": "success",
                "action": "disarm",
                "storage_location": default_loc,
                "location_knowledge": effective_knowledge,
                "disarmed_weapons": disarmed_weapons,
                "disarmed_items": disarmed_items,
                "total_stored_items": len(stored_items_list),
                "message": f"Successfully disarmed character(s). Confiscated {len(disarmed_weapons)} weapon(s) and secured them at '{default_loc}' (Knowledge: {effective_knowledge})."
            }

        # Handle Discover / Reveal Location (e.g. overheard guards, perception check, barracks ledger)
        if action_norm in ("discover_location", "reveal_location", "locate"):
            discovered_items: List[str] = []
            target_char_ids = {c[0]: c[1] for c in target_chars}

            for entry in stored_items_list:
                owner_id = entry.get("original_owner_id")
                owner_name = entry.get("original_owner", "").lower()
                matches_owner = is_party_wide or (owner_id in target_char_ids) or any(c[1].get("name", "").lower() == owner_name for c in target_chars)

                matches_item = True
                entry_name = entry.get("name", "").lower()
                if items or weapons:
                    req_names = [str(x.get("name") if isinstance(x, dict) else x).lower().strip() for x in (items or []) + (weapons or [])]
                    matches_item = any(rn in entry_name or entry_name in rn for rn in req_names)

                if matches_owner and matches_item:
                    entry["location_knowledge"] = (location_knowledge or "discovered").strip().lower()
                    if storage_location:
                        entry["storage_location"] = storage_location
                    if discovery_clue:
                        entry["discovery_clue"] = discovery_clue
                    if holder_or_creature:
                        entry["holder_or_creature"] = holder_or_creature
                    if status:
                        entry["status"] = status
                    discovered_items.append(f"{entry.get('original_owner', 'Party')}'s {entry.get('name')}")

            self.save_active_campaign_state()
            logger.info(f"[TTRPG] Discovered item location(s): {discovered_items} -> {storage_location} (Clue: {discovery_clue})")
            return {
                "status": "success",
                "action": "discover_location",
                "discovered_items": discovered_items,
                "storage_location": storage_location,
                "discovery_clue": discovery_clue,
                "location_knowledge": location_knowledge or "discovered",
                "message": f"Discovered location for {len(discovered_items)} item(s): {storage_location or 'Revealed'} ({discovery_clue or 'Investigation/Perception'})."
            }

        # Handle Destroy / Vaporize / Melt (e.g. dropped in Mount Doom lava, dissolved in acid)
        if action_norm in ("destroy", "vaporize", "melt"):
            destroyed_items: List[str] = []
            loc = storage_location or "Destroyed (Incinerated in Lava / Dissolved in Acid)"
            reas = reason or "Permanent destruction (incinerated/melted/vaporized)"

            # Check if matching items are currently on character(s)
            for char_id, char_data in target_chars:
                current_inv = char_data.setdefault("inventory", [])
                current_wpns = char_data.setdefault("weapons", [])

                if items:
                    for it in items:
                        target_name = (it.get("name") if isinstance(it, dict) else str(it)).strip().lower()
                        for idx, cur_it in enumerate(list(current_inv)):
                            cur_name = (cur_it.get("name") if isinstance(cur_it, dict) else str(cur_it)).strip().lower()
                            if cur_name == target_name or target_name in cur_name:
                                removed = current_inv.pop(idx)
                                rem_name = removed.get("name", target_name) if isinstance(removed, dict) else str(removed)
                                stored_entry = {
                                    "id": f"stored_{uuid.uuid4().hex[:8]}",
                                    "name": rem_name,
                                    "type": "item",
                                    "original_owner": char_data["name"],
                                    "original_owner_id": char_id,
                                    "data": copy.deepcopy(removed),
                                    "storage_location": loc,
                                    "status": "destroyed",
                                    "location_knowledge": location_knowledge or "known",
                                    "holder_or_creature": holder_or_creature or "",
                                    "salvageable": False,
                                    "discovery_clue": discovery_clue or "Item permanently destroyed",
                                    "stored_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "reason": reas
                                }
                                stored_items_list.append(stored_entry)
                                destroyed_items.append(f"{char_data['name']}: {rem_name}")
                                break

                if weapons:
                    for w in weapons:
                        w_name = (w.get("name") if isinstance(w, dict) else str(w)).strip().lower()
                        for idx, cur_w in enumerate(list(current_wpns)):
                            cur_w_name = (cur_w.get("name") if isinstance(cur_w, dict) else str(cur_w)).lower()
                            if cur_w_name == w_name or w_name in cur_w_name:
                                removed = current_wpns.pop(idx)
                                rem_name = removed.get("name", w_name) if isinstance(removed, dict) else str(removed)
                                stored_entry = {
                                    "id": f"stored_{uuid.uuid4().hex[:8]}",
                                    "name": rem_name,
                                    "type": "weapon",
                                    "original_owner": char_data["name"],
                                    "original_owner_id": char_id,
                                    "data": copy.deepcopy(removed),
                                    "storage_location": loc,
                                    "status": "destroyed",
                                    "location_knowledge": location_knowledge or "known",
                                    "holder_or_creature": holder_or_creature or "",
                                    "salvageable": False,
                                    "discovery_clue": discovery_clue or "Item permanently destroyed",
                                    "stored_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "reason": reas
                                }
                                stored_items_list.append(stored_entry)
                                destroyed_items.append(f"{char_data['name']}: {rem_name}")
                                break

            # Also check if matching items are already in stored_items and mark them destroyed
            target_char_ids = {c[0]: c[1] for c in target_chars}
            for entry in stored_items_list:
                owner_id = entry.get("original_owner_id")
                owner_name = entry.get("original_owner", "").lower()
                matches_owner = is_party_wide or (owner_id in target_char_ids) or any(c[1].get("name", "").lower() == owner_name for c in target_chars)

                entry_name = entry.get("name", "").lower()
                matches_item = False
                if items or weapons:
                    req_names = [str(x.get("name") if isinstance(x, dict) else x).lower().strip() for x in (items or []) + (weapons or [])]
                    matches_item = any(rn in entry_name or entry_name in rn for rn in req_names)

                if matches_owner and matches_item and entry.get("status") != "destroyed":
                    entry["status"] = "destroyed"
                    entry["salvageable"] = False
                    entry["storage_location"] = loc
                    entry["reason"] = reas
                    entry["location_knowledge"] = location_knowledge or "known"
                    entry["discovery_clue"] = discovery_clue or "Permanently destroyed"
                    destroyed_items.append(f"{entry.get('original_owner', 'Party')}'s {entry.get('name')}")

            self.save_active_campaign_state()
            logger.info(f"[TTRPG] Destroyed items: {destroyed_items} at '{loc}'. Salvageable: False")
            return {
                "status": "success",
                "action": "destroy",
                "destroyed_items": destroyed_items,
                "salvageable": False,
                "storage_location": loc,
                "message": f"Permanently destroyed {len(destroyed_items)} item(s) at {loc}. Cannot be recovered."
            }

        # Living beings, NPCs, and captives are not inventory items
        npc_names = set()
        synopsis = (self.active_state or {}).get("campaign_synopsis", {})
        if isinstance(synopsis, dict) and synopsis.get("key_npcs"):
            for npc in synopsis["key_npcs"]:
                n_str = (npc.get("name") if isinstance(npc, dict) else str(npc)).strip().lower()
                first = n_str.split()[0] if n_str else ""
                if len(first) > 2:
                    npc_names.add(first)
                if n_str:
                    npc_names.add(n_str)

        def _is_living_being(item_name: str) -> bool:
            low = item_name.lower().strip()
            if any(low == n or low.startswith(f"{n} ") or f" {n} " in f" {low} " for n in npc_names):
                return True
            if any(term in low for term in ("captive", "prisoner", "hostage", "npc")):
                return True
            return False

        safe_items = []
        if items:
            for it in items:
                it_name = (it.get("name") if isinstance(it, dict) else str(it)).strip()
                if _is_living_being(it_name):
                    logger.info(f"[TTRPG] Excluded living entity '{it_name}' from inventory items (NPCs/captives are not items).")
                    continue
                safe_items.append(it)

        # For Add / Remove / Use on target character(s)
        total_modified_items: List[str] = []
        total_modified_weapons: List[str] = []
        total_modified_spells: List[str] = []

        for char_id, char_data in target_chars:
            current_inv = char_data.setdefault("inventory", [])
            current_wpns = char_data.setdefault("weapons", [])
            current_spells = char_data.setdefault("spells", [])

            if action_norm in ("add", "loot", "receive", "learn"):
                if safe_items:
                    for it in safe_items:
                        if isinstance(it, dict) and it.get("name"):
                            item_record = {
                                "name": str(it["name"]).strip(),
                                "qty": str(it.get("qty", "1")).strip(),
                                "weight": str(it.get("weight", "")).strip(),
                                "category": str(it.get("category", "Gear")).strip(),
                                "notes": str(it.get("notes", "")).strip()
                            }
                            current_inv.append(item_record)
                            total_modified_items.append(item_record["name"])
                        else:
                            clean_it = str(it).strip()
                            if clean_it:
                                if current_inv and any(isinstance(x, dict) for x in current_inv):
                                    item_record = {
                                        "name": clean_it,
                                        "qty": "1",
                                        "weight": "",
                                        "category": "Gear",
                                        "notes": ""
                                    }
                                    current_inv.append(item_record)
                                else:
                                    current_inv.append(clean_it)
                                total_modified_items.append(clean_it)
                if weapons:
                    for w in weapons:
                        if isinstance(w, dict) and w.get("name"):
                            w_record = {
                                "name": str(w["name"]).strip(),
                                "atk_bonus": str(w.get("atk_bonus", "+0")).strip(),
                                "damage": str(w.get("damage", "1d4")).strip(),
                                "notes": str(w.get("notes", "")).strip()
                            }
                            existing_w = next((x for x in current_wpns if isinstance(x, dict) and x.get("name", "").lower() == w_record["name"].lower()), None)
                            if existing_w:
                                existing_w.update(w_record)
                            else:
                                current_wpns.append(w_record)
                            total_modified_weapons.append(w_record["name"])
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
                            total_modified_spells.append(s_record["name"])
                        elif isinstance(s, str) and s.strip():
                            clean_s = s.strip()
                            if clean_s not in current_spells:
                                current_spells.append(clean_s)
                            total_modified_spells.append(clean_s)

            elif action_norm in ("remove", "delete", "use", "consume", "drop", "lose", "unlearn"):
                eff_status = (status or ("inside_creature" if holder_or_creature else ("destroyed" if (salvageable is False) else "stored"))).strip().lower()
                eff_salvageable = False if eff_status == "destroyed" or salvageable is False else (True if salvageable is None else bool(salvageable))
                eff_knowledge = (location_knowledge or ("known" if (holder_or_creature or eff_status == "destroyed") else "unknown")).strip().lower()
                eff_loc = storage_location or (f"Inside {holder_or_creature}" if holder_or_creature else "Lost / Dropped")
                eff_holder = holder_or_creature or ""
                eff_clue = discovery_clue or ""

                if items:
                    for it in items:
                        target_name = (it.get("name") if isinstance(it, dict) else str(it)).strip().lower()
                        for idx, cur_it in enumerate(list(current_inv)):
                            cur_name = (cur_it.get("name") if isinstance(cur_it, dict) else str(cur_it)).strip().lower()
                            if cur_name == target_name or target_name in cur_name:
                                removed = current_inv.pop(idx)
                                rem_name = removed.get("name", target_name) if isinstance(removed, dict) else str(removed)
                                total_modified_items.append(rem_name)
                                # If storage_location, holder_or_creature, or special status is provided, preserve in stored_items
                                if (storage_location or holder_or_creature or status or salvageable is False) and action_norm not in ("use", "consume"):
                                    stored_items_list.append({
                                        "id": f"stored_{uuid.uuid4().hex[:8]}",
                                        "name": rem_name,
                                        "type": "item",
                                        "original_owner": char_data["name"],
                                        "original_owner_id": char_id,
                                        "data": copy.deepcopy(removed),
                                        "storage_location": eff_loc,
                                        "status": eff_status,
                                        "location_knowledge": eff_knowledge,
                                        "holder_or_creature": eff_holder,
                                        "salvageable": eff_salvageable,
                                        "discovery_clue": eff_clue,
                                        "stored_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                        "reason": reason or ("Swallowed / Carried away" if eff_holder else "Stored / Dropped")
                                    })
                                break
                if weapons:
                    for w in weapons:
                        w_name = (w.get("name") if isinstance(w, dict) else str(w)).strip().lower()
                        for idx, cur_w in enumerate(list(current_wpns)):
                            cur_w_name = (cur_w.get("name") if isinstance(cur_w, dict) else str(cur_w)).lower()
                            if cur_w_name == w_name or w_name in cur_w_name:
                                removed = current_wpns.pop(idx)
                                rem_name = removed.get("name", w_name) if isinstance(removed, dict) else str(removed)
                                total_modified_weapons.append(rem_name)
                                if (storage_location or holder_or_creature or status or salvageable is False) and action_norm not in ("use", "consume"):
                                    stored_items_list.append({
                                        "id": f"stored_{uuid.uuid4().hex[:8]}",
                                        "name": rem_name,
                                        "type": "weapon",
                                        "original_owner": char_data["name"],
                                        "original_owner_id": char_id,
                                        "data": copy.deepcopy(removed),
                                        "storage_location": eff_loc,
                                        "status": eff_status,
                                        "location_knowledge": eff_knowledge,
                                        "holder_or_creature": eff_holder,
                                        "salvageable": eff_salvageable,
                                        "discovery_clue": eff_clue,
                                        "stored_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                        "reason": reason or ("Swallowed / Carried away" if eff_holder else "Stored / Dropped")
                                    })
                                break
                if spells:
                    for s in spells:
                        s_name = (s.get("name") if isinstance(s, dict) else str(s)).strip().lower()
                        for idx, cur_s in enumerate(list(current_spells)):
                            cur_name = (cur_s.get("name") if isinstance(cur_s, dict) else str(cur_s)).strip().lower()
                            if cur_name == s_name or s_name in cur_name:
                                removed = current_spells.pop(idx)
                                rem_name = removed.get("name", s_name) if isinstance(removed, dict) else str(removed)
                                total_modified_spells.append(rem_name)
                                break

            # Keep live_inventory synchronized if present
            if "live_inventory" in char_data and isinstance(char_data["live_inventory"], list):
                char_data["live_inventory"] = [
                    (x if isinstance(x, dict) else {"name": x, "category": "utility", "genre": char_data.get("ruleset", "fantasy_d20"), "status": "active"})
                    for x in current_inv
                ]

        self.save_active_campaign_state()
        primary_name = "Party" if is_party_wide else target_chars[0][1]["name"]
        logger.info(f"[TTRPG] Inventory/Spells {action_norm} for '{primary_name}': Items={total_modified_items}, Weapons={total_modified_weapons}, Spells={total_modified_spells}. Reason: {reason or 'Manual/DM update'}")

        res_payload = {
            "status": "success",
            "character_name": primary_name,
            "action": action_norm,
            "modified_items": total_modified_items,
            "modified_weapons": total_modified_weapons,
            "modified_spells": total_modified_spells,
            "storage_location": storage_location,
            "total_stored_items": len(stored_items_list),
            "message": f"Successfully updated inventory for '{primary_name}' ({action_norm}: {len(total_modified_items)} item(s), {len(total_modified_weapons)} weapon(s))."
        }
        if not is_party_wide and len(target_chars) == 1:
            res_payload["inventory"] = target_chars[0][1].get("inventory", [])
            res_payload["weapons"] = target_chars[0][1].get("weapons", [])
            res_payload["spells"] = target_chars[0][1].get("spells", [])
        return res_payload

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
    MONSTER_STAT_PRESETS = {
        "ape": (19, 12),
        "baboon": (3, 12),
        "bandit": (11, 12),
        "bandit captain": (65, 15),
        "berserker": (67, 13),
        "bugbear": (27, 16),
        "cultist": (9, 12),
        "dire wolf": (37, 14),
        "giant ape": (157, 12),
        "giant spider": (26, 14),
        "gnoll": (22, 15),
        "goblin": (7, 15),
        "guard": (11, 16),
        "hobgoblin": (11, 18),
        "kobold": (5, 12),
        "minotaur": (76, 14),
        "ogre": (59, 11),
        "orc": (15, 13),
        "pixie": (1, 15),
        "skeleton": (13, 13),
        "specter": (22, 12),
        "sprite": (2, 15),
        "sprite pack": (10, 15),
        "sprites": (10, 15),
        "spy": (27, 12),
        "thug": (32, 11),
        "wight": (45, 14),
        "wolf": (11, 13),
        "zombie": (22, 8),
        "young red dragon": (178, 18),
        "adult red dragon": (256, 19),
    }

    MONSTER_XP_PRESETS = {
        "ape": 100,
        "baboon": 10,
        "bandit": 25,
        "bandit captain": 450,
        "berserker": 450,
        "bugbear": 200,
        "cultist": 25,
        "dire wolf": 200,
        "dryad": 200,
        "giant ape": 2900,
        "giant spider": 450,
        "gnoll": 100,
        "goblin": 50,
        "guard": 25,
        "guardian naga": 5900,
        "hobgoblin": 100,
        "imis": 3900,
        "kobold": 25,
        "minotaur": 700,
        "ogre": 450,
        "orc": 100,
        "pixie": 50,
        "skeleton": 50,
        "specter": 200,
        "sprite": 50,
        "sprite pack": 100,
        "sprites": 100,
        "spy": 200,
        "thug": 100,
        "treant": 5000,
        "wight": 700,
        "wolf": 50,
        "zombie": 50,
        "young red dragon": 5900,
        "adult red dragon": 18000,
    }

    def calculate_monster_xp(self, name: str, max_hp: Optional[int] = None) -> int:
        """Calculates standard D&D 5e XP for a monster by preset match or CR / HP estimate."""
        n = (name or "").lower().strip()
        for key in sorted(self.MONSTER_XP_PRESETS.keys(), key=len, reverse=True):
            if key in n:
                return self.MONSTER_XP_PRESETS[key]

        hp = max_hp or 20
        if hp <= 7:
            return 25
        elif hp <= 18:
            return 50
        elif hp <= 35:
            return 100
        elif hp <= 65:
            return 450
        elif hp <= 100:
            return 700
        elif hp <= 150:
            return 1800
        elif hp <= 220:
            return 3900
        else:
            return 5000

    @staticmethod
    def compute_health_status(current_hp: int, max_hp: int, is_player: bool = False, death_saves: Optional[Dict[str, Any]] = None) -> str:
        """
        D&D 5e qualitative health status thresholds:
        - <= 0 HP:
          - Player: "Dying (0 HP)", "Stable (0 HP)", or "Dead" (based on death_saves)
          - Non-player / Monster: "Defeated"
        - <= 15% HP: "Critical"
        - <= 50% HP: "Bloodied"
        - < 100% HP: "Injured"
        - >= 100% HP: "Healthy"
        """
        if current_hp <= 0:
            if is_player:
                if death_saves:
                    st = str(death_saves.get("status", "")).lower()
                    fails = int(death_saves.get("failures", 0))
                    succs = int(death_saves.get("successes", 0))
                    if st in ("dead", "deceased") or fails >= 3:
                        return "Dead"
                    if st in ("stable", "stable (0 hp)") or succs >= 3:
                        return "Stable (0 HP)"
                return "Dying (0 HP)"
            return "Defeated"
        if max_hp <= 0:
            return "Healthy"
        ratio = float(current_hp) / float(max_hp)
        if ratio <= 0.15:
            return "Critical"
        if ratio <= 0.50:
            return "Bloodied"
        if ratio < 1.0:
            return "Injured"
        return "Healthy"

    def _resolve_monster_stats(self, name: str, hp: Optional[int] = None, ac: Optional[int] = None) -> Tuple[int, int]:
        """Resolves HP and AC for monsters/NPCs using compendium presets or defaults."""
        final_hp = None
        final_ac = None
        if hp is not None:
            try:
                final_hp = int(hp)
            except Exception:
                pass
        if ac is not None:
            try:
                final_ac = int(ac)
            except Exception:
                pass

        if final_hp is not None and final_ac is not None:
            return final_hp, final_ac

        n = (name or "").lower().strip()
        matched_preset = None
        # Sort presets longest key first to match "giant ape" before "ape"
        for key in sorted(self.MONSTER_STAT_PRESETS.keys(), key=len, reverse=True):
            if key in n:
                matched_preset = self.MONSTER_STAT_PRESETS[key]
                break

        preset_hp, preset_ac = matched_preset if matched_preset else (20, 12)
        if final_hp is None:
            final_hp = preset_hp
        if final_ac is None:
            final_ac = preset_ac
        return final_hp, final_ac

    def add_to_initiative(self, name: str, roll_val: int, is_player: bool = True) -> Dict[str, Any]:
        if not self.active_state:
            return {"status": "error", "message": "No active campaign."}

        ladder = self.active_state.setdefault("initiative_order", [])
        # Remove existing entity if already on the ladder
        ladder = [item for item in ladder if item["name"].lower() != name.lower()]

        char_id, char_data = self._find_character(name)
        if char_data:
            vitals = char_data.get("vitals", {})
            hp_info = vitals.get("health", {})
            cur_hp = int(hp_info.get("current", 20))
            max_hp = int(hp_info.get("max", 20))
            status = self.compute_health_status(cur_hp, max_hp)
            ladder.append({
                "entity_id": char_id,
                "name": char_data.get("name", name),
                "roll": int(roll_val),
                "is_player": True,
                "current_hp": cur_hp,
                "max_hp": max_hp,
                "armor_class": int(vitals.get("guard", {}).get("current", 10)),
                "status": status,
                "conditions": list(char_data.get("conditions", []))
            })
        else:
            resolved_hp, resolved_ac = self._resolve_monster_stats(name)
            status = self.compute_health_status(resolved_hp, resolved_hp)
            e_xp = self.calculate_monster_xp(name, resolved_hp)
            ladder.append({
                "entity_id": f"ent_{len(ladder) + 1}",
                "name": name,
                "roll": int(roll_val),
                "is_player": is_player,
                "current_hp": int(resolved_hp),
                "max_hp": int(resolved_hp),
                "armor_class": int(resolved_ac),
                "status": status,
                "conditions": [],
                "xp": int(e_xp),
                "xp_awarded": False
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

    def is_combat_active(self) -> bool:
        """Returns True only if an active combat encounter with living hostile enemies is ongoing."""
        if not self._ensure_active_state():
            return False
        ladder = self.active_state.get("initiative_order", [])
        if not ladder:
            return False
        return any(
            not c.get("is_player") and c.get("status") != "Defeated" and (c.get("current_hp") is None or c.get("current_hp") > 0)
            for c in ladder
        )

    def _award_combat_xp_for_enemies(self, defeated_enemies: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculates and evenly distributes XP from defeated enemies to active party characters.
        Marks xp_awarded = True on those combatants.
        Returns summary of XP awarded and any characters ready for level-up.
        """
        if not defeated_enemies:
            return {"total_xp": 0, "xp_per_character": 0, "level_up_ready": [], "message": ""}

        unawarded = [e for e in defeated_enemies if not e.get("is_player") and not e.get("xp_awarded")]
        if not unawarded:
            return {"total_xp": 0, "xp_per_character": 0, "level_up_ready": [], "message": ""}

        total_xp = sum(int(e.get("xp") or self.calculate_monster_xp(e.get("name", ""), e.get("max_hp", 20))) for e in unawarded)
        if total_xp <= 0:
            for e in unawarded:
                e["xp_awarded"] = True
            return {"total_xp": 0, "xp_per_character": 0, "level_up_ready": [], "message": ""}

        chars = self.active_state.get("characters", {})
        party_chars = [c for c in chars.values() if c.get("is_player", True)]
        if not party_chars:
            for e in unawarded:
                e["xp_awarded"] = True
            return {"total_xp": total_xp, "xp_per_character": 0, "level_up_ready": [], "message": f"Defeated enemies were worth {total_xp} XP, but no active party characters were found."}

        xp_share = total_xp // len(party_chars)
        level_up_ready_chars = []
        for c in party_chars:
            prog_res = self.manage_character_progression(c.get("name", ""), action="award_xp", amount_or_level=xp_share)
            if prog_res.get("level_up_ready"):
                level_up_ready_chars.append({
                    "name": c.get("name", ""),
                    "level": prog_res.get("level"),
                    "experience_points": prog_res.get("experience_points")
                })

        for e in unawarded:
            e["xp_awarded"] = True

        msg = f"Awarded {total_xp} total XP ({xp_share} XP each) to {len(party_chars)} party members for defeating {len(unawarded)} hostile enemies."
        logger.info(f"[TTRPG] {msg}")
        return {
            "total_xp": total_xp,
            "xp_per_character": xp_share,
            "level_up_ready": level_up_ready_chars,
            "message": msg
        }

    def clean_stale_combat_if_needed(self) -> bool:
        """
        If the active initiative ladder only contains defeated enemies or finished combat,
        clears the ladder so a fresh combat encounter can begin cleanly.
        """
        if not self._ensure_active_state():
            return False
        ladder = self.active_state.get("initiative_order", [])
        if not ladder:
            return False

        enemies = [c for c in ladder if not c.get("is_player")]
        alive_enemies = [
            c for c in enemies
            if c.get("status") != "Defeated" and (c.get("current_hp") is None or c.get("current_hp") > 0)
        ]
        # If enemies were present on the ladder and ALL of them are defeated, combat is finished
        if enemies and not alive_enemies:
            self._award_combat_xp_for_enemies([e for e in enemies if not e.get("xp_awarded")])
            self.active_state["initiative_order"] = []
            self.active_state["active_turn_index"] = 0
            self.active_state["current_round"] = 1
            self.save_active_campaign_state()
            return True

        # If every combatant on the ladder is marked defeated
        if all(c.get("status") == "Defeated" or (c.get("current_hp") is not None and c.get("current_hp") <= 0) for c in ladder):
            self._award_combat_xp_for_enemies([e for e in enemies if not e.get("xp_awarded")])
            self.active_state["initiative_order"] = []
            self.active_state["active_turn_index"] = 0
            self.active_state["current_round"] = 1
            self.save_active_campaign_state()
            return True

        return False

    def clear_defeated_combatants(self, only_non_players: bool = True) -> Dict[str, Any]:
        """
        Removes defeated combatants from the active initiative ladder.
        Awards XP for defeated enemies to the party.
        If all enemies are defeated, completely clears and ends the combat encounter.
        """
        if not self._ensure_active_state():
            return {"status": "error", "message": "No active campaign."}

        ladder = self.active_state.get("initiative_order", [])
        if not ladder:
            return {"status": "success", "initiative_order": [], "removed": [], "xp_awarded": 0, "xp_per_character": 0, "level_up_ready_characters": [], "xp_message": ""}

        defeated_enemies_to_award = []
        remaining = []
        removed = []
        for c in ladder:
            is_defeated = c.get("status") == "Defeated" or (c.get("current_hp") is not None and c.get("current_hp") <= 0)
            if is_defeated and (not only_non_players or not c.get("is_player")):
                removed.append(c.get("name"))
                if not c.get("is_player") and not c.get("xp_awarded"):
                    defeated_enemies_to_award.append(c)
            else:
                remaining.append(c)

        xp_info = self._award_combat_xp_for_enemies(defeated_enemies_to_award) if defeated_enemies_to_award else {"total_xp": 0, "xp_per_character": 0, "level_up_ready": [], "message": ""}

        remaining_enemies = [c for c in remaining if not c.get("is_player")]
        if not remaining_enemies:
            self.active_state["initiative_order"] = []
            self.active_state["active_turn_index"] = 0
            self.active_state["current_round"] = 1
        else:
            self.active_state["initiative_order"] = remaining
            if self.active_state.get("active_turn_index", 0) >= len(remaining):
                self.active_state["active_turn_index"] = 0

        self.save_active_campaign_state()
        return {
            "status": "success",
            "removed": removed,
            "initiative_order": self.active_state["initiative_order"],
            "combat_cleared": not remaining_enemies,
            "xp_awarded": xp_info["total_xp"],
            "xp_per_character": xp_info["xp_per_character"],
            "level_up_ready_characters": xp_info["level_up_ready"],
            "xp_message": xp_info["message"]
        }

    def start_combat(
        self,
        enemies: Optional[List[Any]] = None,
        round_number: int = 1
    ) -> Dict[str, Any]:
        """
        Initializes combat encounter:
        - Automatically rolls initiative for all active party characters (1d20 + DEX mod).
        - Automatically rolls initiative for each enemy (1d20 + enemy DEX mod or +0).
        - Populates the initiative ladder in descending order with vitals & D&D 5e health status.
        - Sets active_turn_index = 0, current_round = round_number.
        - Persists state and returns the ladder.
        """
        if not self._ensure_active_state():
            return {"status": "error", "message": "No active campaign."}

        chars = self.active_state.get("characters", {})
        ladder = []

        # 1. Roll for player characters
        for cid, c in chars.items():
            c_name = c.get("name", "Hero")
            attrs = c.get("attributes", {})
            dex = attrs.get("Agility") or attrs.get("Dexterity") or attrs.get("DEX") or 10
            try:
                dex_val = int(dex)
            except Exception:
                dex_val = 10
            dex_mod = (dex_val - 10) // 2

            init_bonus = c.get("initiative") or c.get("initiative_bonus")
            bonus = dex_mod
            if init_bonus is not None:
                try:
                    bonus = int(str(init_bonus).replace("+", "").strip())
                except Exception:
                    pass

            roll_res = self.parse_and_roll(f"1d20{bonus:+d}", character_name=c_name)
            roll_val = roll_res.get("total", 10 + bonus)
            vitals = c.get("vitals", {})
            hp_info = vitals.get("health", {})
            cur_hp = int(hp_info.get("current", 20))
            max_hp = int(hp_info.get("max", 20))
            status = self.compute_health_status(cur_hp, max_hp)
            ladder.append({
                "entity_id": cid,
                "name": c_name,
                "roll": int(roll_val),
                "is_player": True,
                "current_hp": cur_hp,
                "max_hp": max_hp,
                "armor_class": int(vitals.get("guard", {}).get("current", 10)),
                "status": status,
                "conditions": list(c.get("conditions", []))
            })

        # 2. Roll for enemies
        for idx, enemy in enumerate(enemies or []):
            if isinstance(enemy, dict):
                e_name = enemy.get("name", f"Enemy {idx + 1}")
                e_mod = int(enemy.get("dex_mod", enemy.get("initiative_mod", 0)))
                e_hp = enemy.get("hp") or enemy.get("health") or enemy.get("max_hp")
                e_ac = enemy.get("ac") or enemy.get("armor_class")
            else:
                e_name = str(enemy).strip()
                e_mod = 0
                e_hp = None
                e_ac = None
            if not e_name:
                e_name = f"Enemy {idx + 1}"

            resolved_hp, resolved_ac = self._resolve_monster_stats(e_name, e_hp, e_ac)
            roll_res = self.parse_and_roll(f"1d20{e_mod:+d}", character_name=e_name)
            roll_val = roll_res.get("total", 10 + e_mod)
            status = self.compute_health_status(resolved_hp, resolved_hp)
            e_xp = enemy.get("xp") if isinstance(enemy, dict) and enemy.get("xp") is not None else self.calculate_monster_xp(e_name, resolved_hp)
            ladder.append({
                "entity_id": f"enemy_{idx + 1}",
                "name": e_name,
                "roll": int(roll_val),
                "is_player": False,
                "current_hp": int(resolved_hp),
                "max_hp": int(resolved_hp),
                "armor_class": int(resolved_ac),
                "status": status,
                "conditions": [],
                "xp": int(e_xp),
                "xp_awarded": False
            })

        # Sort descending by initiative roll
        ladder.sort(key=lambda x: x["roll"], reverse=True)
        self.active_state["initiative_order"] = ladder
        self.active_state["active_turn_index"] = 0
        self.active_state["current_round"] = int(round_number)
        self.save_active_campaign_state()

        return {
            "status": "success",
            "message": f"Combat encounter started with {len(ladder)} combatants.",
            "current_round": round_number,
            "initiative_order": ladder,
            "active_entity": ladder[0]["name"] if ladder else None
        }

    def end_combat(self) -> Dict[str, Any]:
        """Clears the initiative ladder, awards XP for any remaining defeated enemies, and resets turn tracking."""
        if not self._ensure_active_state():
            return {"status": "error", "message": "No active campaign."}

        ladder = self.active_state.get("initiative_order", [])
        # Find enemies that were defeated or whose combat is concluding with victory
        defeated_enemies = [
            c for c in ladder
            if not c.get("is_player") and not c.get("xp_awarded") and (
                c.get("status") == "Defeated" or (c.get("current_hp") is not None and c.get("current_hp") <= 0)
            )
        ]
        # If user explicitly ended combat and all enemies are defeated or this was a victory, award their XP
        xp_info = self._award_combat_xp_for_enemies(defeated_enemies) if defeated_enemies else {"total_xp": 0, "xp_per_character": 0, "level_up_ready": [], "message": ""}

        self.active_state["initiative_order"] = []
        self.active_state["active_turn_index"] = 0
        self.active_state["current_round"] = 1
        self.save_active_campaign_state()

        msg = "Combat ended. Initiative ladder cleared."
        if xp_info.get("message"):
            msg += f" {xp_info['message']}"

        return {
            "status": "success",
            "message": msg,
            "xp_awarded": xp_info["total_xp"],
            "xp_per_character": xp_info["xp_per_character"],
            "level_up_ready_characters": xp_info["level_up_ready"],
            "xp_message": xp_info.get("message", "")
        }

    def advance_turn(self) -> Dict[str, Any]:
        """Advances combat to the next turn, automatically skipping defeated combatants and incrementing rounds."""
        if not self._ensure_active_state():
            return {"status": "error", "message": "No active campaign."}

        ladder = self.active_state.get("initiative_order", [])
        if not ladder:
            return {"status": "error", "message": "Initiative ladder is empty."}

        start_idx = self.active_state.get("active_turn_index", 0)
        round_num = self.active_state.get("current_round", 1)
        total_combatants = len(ladder)

        # Check if any hostile enemies are still alive
        alive_enemies = [
            c for c in ladder
            if not c.get("is_player") and c.get("status") != "Defeated" and (c.get("current_hp") is None or c.get("current_hp") > 0)
        ]
        if not alive_enemies and any(not c.get("is_player") for c in ladder):
            # All enemies defeated! Automatically end combat and clear ladder
            end_res = self.end_combat()
            msg = "All hostile enemies have been defeated! The initiative ladder has been cleared. The party is victorious!"
            if end_res.get("xp_message"):
                msg += f" {end_res['xp_message']}"
            return {
                "status": "success",
                "round": round_num,
                "combat_ended": True,
                "message": msg,
                "active_entity": None,
                "is_player": True,
                "xp_awarded": end_res.get("xp_awarded", 0),
                "xp_per_character": end_res.get("xp_per_character", 0),
                "level_up_ready_characters": end_res.get("level_up_ready_characters", [])
            }

        # Find the next non-defeated combatant
        next_idx = start_idx
        found = False
        for _ in range(total_combatants):
            next_idx += 1
            if next_idx >= total_combatants:
                next_idx = 0
                round_num += 1

            candidate = ladder[next_idx]
            cur_hp = candidate.get("current_hp")
            status = candidate.get("status", "")
            is_player = bool(candidate.get("is_player", False))

            # Skip defeated enemies
            if not is_player and (status == "Defeated" or (cur_hp is not None and cur_hp <= 0)):
                continue

            # Skip dead or stabilized unconscious players
            if is_player and cur_hp is not None and cur_hp <= 0:
                ds = candidate.get("death_saves")
                if not ds:
                    _, c_data = self._find_character(candidate.get("name", ""))
                    if c_data:
                        ds = c_data.get("death_saves")
                ds_status = (ds.get("status") if ds else "") or status
                if str(ds_status).lower() in ("dead", "deceased") or (ds and ds.get("failures", 0) >= 3):
                    continue
                if str(ds_status).lower() in ("stable", "stable (0 hp)") or (ds and ds.get("successes", 0) >= 3):
                    continue
                # Dying player at 0 HP is kept in initiative to roll their death save!

            found = True
            break

        if not found:
            next_idx = 0

        self.active_state["active_turn_index"] = next_idx
        self.active_state["current_round"] = round_num
        self.save_active_campaign_state()

        active_entity = ladder[next_idx]
        is_player = bool(active_entity.get("is_player", False))
        is_dying = False
        if is_player and active_entity.get("current_hp") is not None and active_entity.get("current_hp") <= 0:
            is_dying = True

        return {
            "status": "success",
            "round": round_num,
            "active_entity": active_entity["name"],
            "is_player": is_player,
            "is_dying": is_dying,
            "death_save_required": is_dying,
            "turn_index": next_idx
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
            raw_conds = c.get("conditions", [])
            cond_list = [(x.get("name") if isinstance(x, dict) else str(x)).strip() for x in raw_conds if x]
            conds = ", ".join(cond_list) or "Clear"
            hud.append(
                f"- {c.get('name', 'Hero')} [{c.get('archetype', 'Hero')}]: {hp_cur}/{hp_max} HP | "
                f"Guard: {guard} | Conditions: {conds}"
            )

        hud.append("################################")
        return "\n".join(hud)

    def update_entity_location(
        self,
        name: str,
        location: str,
        is_separated: Optional[bool] = None,
        reunite_party: bool = False
    ) -> Dict[str, Any]:
        """
        Updates the spatial location of a character, NPC, or monster.
        If reunite_party is True, brings all party characters to 'location' and clears separation.
        """
        if not self._ensure_active_state():
            return {"status": "error", "message": "No active campaign state."}

        entity_locs = self.active_state.setdefault("entity_locations", {})
        chars = self.active_state.get("characters", {})

        if reunite_party:
            target_loc = location or self.active_state.get("current_location", "Main Party")
            for c_id, c_data in chars.items():
                c_data["location"] = target_loc
                c_data["is_separated"] = False
                c_name = c_data.get("name", c_id)
                entity_locs[c_name] = target_loc
            self.active_state["current_location"] = target_loc
            self.save_active_campaign_state()
            return {
                "status": "success",
                "message": f"Party reunited at '{target_loc}'.",
                "location": target_loc,
                "party_reunited": True
            }

        # Check if character in party
        char_id, char_data = self._find_character(name)
        if char_data:
            char_data["location"] = location
            if is_separated is not None:
                char_data["is_separated"] = bool(is_separated)
            else:
                main_loc = self.active_state.get("current_location", "")
                if main_loc and location.lower().strip() != main_loc.lower().strip():
                    char_data["is_separated"] = True
                else:
                    char_data["is_separated"] = False
            entity_locs[char_data["name"]] = location
            self.save_active_campaign_state()
            return {
                "status": "success",
                "entity_name": char_data["name"],
                "location": location,
                "is_separated": char_data["is_separated"],
                "message": f"{char_data['name']} location updated to '{location}' (Separated: {char_data['is_separated']})."
            }

        # Check if combatant on ladder
        comb = self._find_combatant(name)
        if comb:
            comb["location"] = location
            entity_locs[comb["name"]] = location
            self.save_active_campaign_state()
            return {
                "status": "success",
                "entity_name": comb["name"],
                "location": location,
                "message": f"Combatant '{comb['name']}' location set to '{location}'."
            }

        # Generic NPC / Entity
        clean_name = name.strip()
        entity_locs[clean_name] = location
        self.save_active_campaign_state()
        return {
            "status": "success",
            "entity_name": clean_name,
            "location": location,
            "message": f"Entity '{clean_name}' location tracked at '{location}'."
        }

    def check_entity_proximity(
        self,
        actor_name: str,
        target_name: str,
        action_or_spell: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Verifies if actor and target are co-located or within range to interact/attack/cast spells.
        Checks long-range magic exceptions (Sending, Scrying, Teleport, etc.).
        """
        if not self._ensure_active_state():
            return {"allowed": True, "reason": "No active campaign state."}

        # If action_or_spell is a long-range magic exception, allow it across any distance
        if action_or_spell:
            act_clean = re.sub(r'[^a-zA-Z0-9\s]', '', action_or_spell).strip().lower()
            if any(exc in act_clean for exc in LONG_RANGE_MAGIC_EXCEPTIONS):
                return {
                    "allowed": True,
                    "reason": f"'{action_or_spell}' is a long-range/planar spell permitted across areas."
                }

        entity_locs = self.active_state.get("entity_locations", {})
        main_loc = self.active_state.get("current_location", "Unknown Location")

        # Resolve actor location
        actor_loc = None
        char_id, char_data = self._find_character(actor_name)
        if char_data:
            actor_loc = char_data.get("location") or (main_loc if not char_data.get("is_separated") else f"{main_loc} (Separated)")
        if not actor_loc:
            comb = self._find_combatant(actor_name)
            if comb:
                actor_loc = comb.get("location")
        if not actor_loc:
            actor_loc = entity_locs.get(actor_name)
        if not actor_loc:
            actor_loc = main_loc

        # Resolve target location
        target_loc = None
        t_id, t_data = self._find_character(target_name)
        if t_data:
            target_loc = t_data.get("location") or (main_loc if not t_data.get("is_separated") else f"{main_loc} (Separated)")
        if not target_loc:
            comb = self._find_combatant(target_name)
            if comb:
                target_loc = comb.get("location")
        if not target_loc:
            target_loc = entity_locs.get(target_name)
        if not target_loc:
            target_loc = main_loc

        # If actor targets themselves, always allowed
        if actor_name.lower().strip() == target_name.lower().strip():
            return {
                "allowed": True,
                "actor_location": actor_loc,
                "target_location": target_loc,
                "reason": f"Self-targeted action: {actor_loc}"
            }

        # Normalize locations for comparison
        clean_actor_loc = re.sub(r'\s+', ' ', actor_loc).strip().lower()
        clean_target_loc = re.sub(r'\s+', ' ', target_loc).strip().lower()

        if clean_actor_loc == clean_target_loc:
            return {
                "allowed": True,
                "actor_location": actor_loc,
                "target_location": target_loc,
                "reason": f"Both are co-located at: {actor_loc}"
            }

        reason = (
            f"Proximity Check Failed: {actor_name} is at '{actor_loc}', while {target_name} is at '{target_loc}'. "
            f"They are separated and cannot target each other with '{action_or_spell or 'this action'}'."
        )
        return {
            "allowed": False,
            "actor_location": actor_loc,
            "target_location": target_loc,
            "reason": reason
        }

    # --- Internal Helpers ---
    def _find_character(self, name: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        if not self._ensure_active_state():
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

    def _find_combatant(self, name: str) -> Optional[Dict[str, Any]]:
        """Finds a combatant entity on the active initiative ladder."""
        if not self._ensure_active_state():
            return None
        ladder = self.active_state.get("initiative_order", [])
        clean = (name or "").lower().strip()
        # 1. Exact match
        for item in ladder:
            if item.get("name", "").lower() == clean or item.get("entity_id", "").lower() == clean:
                return item
        # 2. Substring match
        for item in ladder:
            if clean in item.get("name", "").lower() or clean in item.get("entity_id", "").lower():
                return item
        return None

    def level_up_character(self, character_name: str, target_level: int) -> Dict[str, Any]:
        """Advances a character to target_level and persists campaign state."""
        char_id, char = self._find_character(character_name)
        if not char:
            return {"status": "error", "message": f"Character '{character_name}' not found."}
        res = level_up_dnd_character(char, target_level)
        if res.get("status") == "success":
            self.save_active_campaign_state()
        return res

    def adjust_spell_slot(self, character_id: str, spell_level: int, delta: int) -> Dict[str, Any]:
        """
        Directly increments or decrements active spell slots from card pip clicks.
        """
        if not self.active_state:
            return {"status": "error", "message": "No active campaign session."}

        char = self.active_state.get("characters", {}).get(character_id)
        if not char:
            _, char = self._find_character(character_id)
        if not char:
            return {"status": "error", "message": f"Character '{character_id}' not found."}

        slots = char.setdefault("native_system_data", {}).setdefault("spell_slots", {})
        slot_key = f"level_{spell_level}"

        if slot_key not in slots and "spell_slots" in char and slot_key in char["spell_slots"]:
            slots[slot_key] = char["spell_slots"][slot_key]

        if slot_key not in slots:
            return {"status": "error", "message": f"Slot level {spell_level} not found."}

        s_data = slots[slot_key]
        new_cur = max(0, min(s_data["max"], s_data["current"] + delta))
        s_data["current"] = new_cur

        if "spell_slots" in char and slot_key in char["spell_slots"]:
            char["spell_slots"][slot_key]["current"] = new_cur

        self.save_active_campaign_state()
        return {
            "status": "success",
            "character_id": character_id,
            "level": spell_level,
            "current": new_cur,
            "max": s_data["max"]
        }

    def _get_character_ability_score(self, char_data: Dict[str, Any], ability: str, default: int = 10) -> int:
        """Resolves a D&D ability score (STR/DEX/CON/INT/WIS/CHA) across attributes and native_system_data."""
        attrs = char_data.get("attributes") or {}
        native = char_data.get("native_system_data") or {}
        n_scores = native.get("ability_scores") or {}

        key_map = {
            "STR": ("Strength", "STR", "Might"),
            "DEX": ("Dexterity", "DEX", "Agility"),
            "CON": ("Constitution", "CON", "Vigor"),
            "INT": ("Intelligence", "INT", "Intellect"),
            "WIS": ("Wisdom", "WIS", "Presence"),
            "CHA": ("Charisma", "CHA", "Presence"),
        }
        ab = (ability or "STR").strip().upper()[:3]
        for k in key_map.get(ab, (ab,)):
            if k in n_scores and n_scores[k] is not None:
                return self._safe_int(n_scores[k], default)
            if k in attrs and attrs[k] is not None:
                return self._safe_int(attrs[k], default)
        return default

    def _sync_char_inventory_views(self, char_data: Dict[str, Any], new_items: List[Dict[str, Any]]) -> List[str]:
        """Appends/merges structured compendium items into root inventory and secondary list mirrors."""
        added_names: List[str] = []
        inv = char_data.setdefault("inventory", [])

        def _merge_into(target_list: List[Any], entry_dict: Dict[str, Any], default_genre: str):
            name_low = entry_dict["name"].strip().lower()
            idx_low = (entry_dict.get("compendium_index") or "").strip().lower()
            notes_str = (entry_dict.get("notes") or "").strip()
            for existing in target_list:
                if isinstance(existing, dict):
                    ex_name = (existing.get("name") or "").strip().lower()
                    ex_idx = (existing.get("compendium_index") or "").strip().lower()
                    ex_notes = (existing.get("notes") or "").strip()
                    same_idx = bool(idx_low and ex_idx and idx_low == ex_idx)
                    same_name = (ex_name == name_low)
                    if (same_idx or same_name) and ex_notes == notes_str and not entry_dict.get("equipped"):
                        cur_q = self._safe_int(existing.get("qty"), 1)
                        add_q = self._safe_int(entry_dict.get("qty"), 1)
                        existing["qty"] = str(cur_q + add_q)
                        return
            cloned = copy.deepcopy(entry_dict)
            cloned.setdefault("genre", default_genre)
            cloned.setdefault("status", "active")
            target_list.append(cloned)

        ruleset_val = char_data.get("ruleset", "dnd_5e")
        for item_dict in new_items:
            _merge_into(inv, item_dict, ruleset_val)
            added_names.append(item_dict["name"])

            native = char_data.get("native_system_data")
            if isinstance(native, dict) and isinstance(native.get("inventory"), list):
                _merge_into(native["inventory"], item_dict, ruleset_val)

            if isinstance(char_data.get("live_inventory"), list):
                _merge_into(char_data["live_inventory"], item_dict, ruleset_val)

            if isinstance(char_data.get("tagged_inventory"), list):
                _merge_into(char_data["tagged_inventory"], item_dict, ruleset_val)

        return added_names

    def add_compendium_entry_to_character(
        self,
        character_name: str,
        category: str,
        index_or_name: str,
        edition: str = "2024",
        expand_pack: bool = True,
        qty: int = 1,
        equip: bool = False,
        bypass_class_check: bool = False,
        prepare_spell: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Grants a weapon, armor/shield, gear/magic item, or spell from the offline 5e SRD / Homebrew
        compendium directly onto a campaign character sheet, computing character-specific modifiers
        and keeping root & native_system_data views synchronized.
        """
        if not self._ensure_active_state():
            return {"status": "error", "message": "No active campaign loaded."}

        char_id, char_data = self._find_character(character_name)
        if not char_data:
            return {"status": "error", "message": f"Character '{character_name}' not found."}

        from core.compendiums.dnd_weapons import get_weapon, format_weapon_for_sheet
        from core.compendiums.dnd_armor import get_armor, compute_armor_class
        from core.compendiums.dnd_items import get_item, expand_pack_contents, format_item_for_sheet
        from core.compendiums.dnd_spells import get_spell, format_spell_for_sheet, validate_character_spell_assignment

        cat = (category or "").strip().lower()
        lvl = max(1, self._safe_int(char_data.get("level"), 1))
        native = char_data.get("native_system_data") if isinstance(char_data.get("native_system_data"), dict) else None

        str_score = self._get_character_ability_score(char_data, "STR", 10)
        dex_score = self._get_character_ability_score(char_data, "DEX", 10)
        str_mod = (str_score - 10) // 2
        dex_mod = (dex_score - 10) // 2

        prof_raw = char_data.get("prof_bonus") or (native.get("proficiency_bonus") if native else None)
        if prof_raw is not None:
            prof_bonus = max(2, self._safe_int(prof_raw, calculate_dnd_proficiency(lvl)))
        else:
            prof_bonus = calculate_dnd_proficiency(lvl)

        # Resolve spellcasting stats
        cast_ab = (
            char_data.get("spellcasting_ability")
            or (native.get("spellcasting_ability") if native else None)
            or ""
        ).strip().upper()[:3]
        if cast_ab not in ("INT", "WIS", "CHA"):
            arch_low = (char_data.get("archetype") or char_data.get("class_and_level") or "").lower()
            if any(c in arch_low for c in ("cleric", "druid", "ranger", "monk")):
                cast_ab = "WIS"
            elif any(c in arch_low for c in ("bard", "paladin", "sorcerer", "warlock")):
                cast_ab = "CHA"
            else:
                cast_ab = "INT"

        cast_score = self._get_character_ability_score(char_data, cast_ab, 10)
        cast_mod = (cast_score - 10) // 2
        default_dc = 8 + prof_bonus + cast_mod
        default_atk = prof_bonus + cast_mod
        spell_dc_val = self._safe_int(
            char_data.get("spell_save_dc")
            or char_data.get("ability_save_dc")
            or (native.get("ability_save_dc") if native else None),
            default_dc,
        )
        spell_atk_raw = (
            char_data.get("spell_attack_bonus")
            or (native.get("spell_attack_bonus") if native else None)
        )
        if spell_atk_raw is not None and str(spell_atk_raw).strip():
            atk_num = self._safe_int(spell_atk_raw, default_atk)
            spell_atk_str = f"+{atk_num}" if atk_num >= 0 else str(atk_num)
        else:
            spell_atk_str = f"+{default_atk}" if default_atk >= 0 else str(default_atk)

        if cat in ("weapons", "weapon"):
            wpn = get_weapon(index_or_name)
            if not wpn:
                return {"status": "error", "message": f"Weapon '{index_or_name}' not found in compendium."}
            sheet_wpn = format_weapon_for_sheet(
                wpn,
                str_mod=str_mod,
                dex_mod=dex_mod,
                prof_bonus=prof_bonus,
                proficient=True,
                edition=edition,
            )
            wpns_list = char_data.setdefault("weapons", [])
            existing_w = next(
                (w for w in wpns_list if isinstance(w, dict) and w.get("name", "").strip().lower() == sheet_wpn["name"].lower()),
                None,
            )
            if existing_w:
                existing_w.update(sheet_wpn)
            else:
                wpns_list.append(sheet_wpn)

            if native is not None and isinstance(native.get("weapons"), list):
                ex_nw = next(
                    (w for w in native["weapons"] if isinstance(w, dict) and w.get("name", "").strip().lower() == sheet_wpn["name"].lower()),
                    None,
                )
                if ex_nw:
                    ex_nw.update(copy.deepcopy(sheet_wpn))
                else:
                    native["weapons"].append(copy.deepcopy(sheet_wpn))

            self.save_active_campaign_state()
            return {
                "status": "success",
                "category": "weapons",
                "character_name": char_data["name"],
                "character_id": char_id,
                "granted": [sheet_wpn["name"]],
                "entry": sheet_wpn,
                "message": f"Granted weapon '{sheet_wpn['name']}' ({sheet_wpn['atk_bonus']} to hit, {sheet_wpn['damage']}) to {char_data['name']}.",
            }

        elif cat in ("armor", "armors", "shield", "shields"):
            arm = get_armor(index_or_name)
            if not arm:
                return {"status": "error", "message": f"Armor '{index_or_name}' not found in compendium."}
            is_shield = (arm.get("category") == "Shield")
            ac_note_parts = []
            if is_shield:
                bonus_val = 2 + int(arm.get("ac_bonus") or 0)
                ac_note_parts.append(f"+{bonus_val} AC")
            else:
                base_ac = int(arm.get("ac_base") or 10)
                extra_ac = int(arm.get("ac_bonus") or 0)
                dex_txt = ""
                if arm.get("dex_bonus"):
                    max_d = arm.get("max_dex_bonus")
                    dex_txt = f" + DEX (max {max_d})" if max_d is not None else " + DEX"
                ac_note_parts.append(f"AC {base_ac + extra_ac}{dex_txt}")
            if arm.get("stealth_disadvantage"):
                ac_note_parts.append("Stealth Disadv.")
            if arm.get("str_minimum"):
                ac_note_parts.append(f"STR {arm['str_minimum']}+")

            inv_entry = {
                "name": arm["name"],
                "qty": str(max(1, int(qty or 1))),
                "weight": f"{arm['weight']} lb" if arm.get("weight") else "",
                "category": "Shield" if is_shield else "Armor",
                "notes": ", ".join(ac_note_parts),
                "equipped": bool(equip),
                "compendium_index": arm.get("index", ""),
                "is_magic": bool(arm.get("is_magic", False)),
            }

            if equip:
                # Unequip previous items in the same slot (Armor vs Shield)
                for target_inv in (
                    [char_data.get("inventory")]
                    + ([native.get("inventory")] if native else [])
                    + [char_data.get("live_inventory"), char_data.get("tagged_inventory")]
                ):
                    if isinstance(target_inv, list):
                        for ex in target_inv:
                            if isinstance(ex, dict) and ex.get("category") == inv_entry["category"]:
                                ex["equipped"] = False

            added = self._sync_char_inventory_views(char_data, [inv_entry])
            ac_report = None
            if equip:
                if is_shield:
                    cur_armor = char_data.get("armor_equipped")
                    if not cur_armor:
                        # Look for currently equipped body armor in inventory
                        eq_arm = next(
                            (
                                it.get("name")
                                for it in char_data.get("inventory", [])
                                if isinstance(it, dict) and it.get("category") == "Armor" and it.get("equipped")
                            ),
                            None,
                        )
                        cur_armor = eq_arm
                    s_bonus = 2 + int(arm.get("ac_bonus") or 0)
                    has_sh = True
                else:
                    cur_armor = arm["name"]
                    has_sh = bool(char_data.get("shield_equipped"))
                    s_bonus = 2
                ac_report = compute_armor_class(
                    armor_name=cur_armor,
                    has_shield=has_sh,
                    dex_mod=dex_mod,
                    shield_bonus=s_bonus,
                    str_score=str_score,
                )
                new_ac = ac_report["armor_class"]
                char_data["armor_class"] = new_ac
                char_data["armor_equipped"] = ac_report["armor_equipped"]
                char_data["shield_equipped"] = ac_report["shield_equipped"]
                char_data["stealth_disadvantage"] = ac_report["stealth_disadvantage"]
                guard_obj = char_data.setdefault("vitals", {}).setdefault("guard", {"current": new_ac, "max": new_ac, "label": "Armor Defense"})
                guard_obj["current"] = new_ac
                guard_obj["max"] = max(1, new_ac)
                if native is not None:
                    native["armor_class"] = new_ac
                    native["armor_equipped"] = ac_report["armor_equipped"]
                    native["shield_equipped"] = ac_report["shield_equipped"]

            self.save_active_campaign_state()
            msg = f"Granted armor '{arm['name']}' to {char_data['name']}."
            if ac_report:
                msg += f" Equipped! AC is now {ac_report['armor_class']}."
                if ac_report["warnings"]:
                    msg += " (" + "; ".join(ac_report["warnings"]) + ")"
            return {
                "status": "success",
                "category": "armor",
                "character_name": char_data["name"],
                "character_id": char_id,
                "granted": added,
                "entry": inv_entry,
                "ac_report": ac_report,
                "message": msg,
            }

        elif cat in ("items", "item", "gear", "magic_items"):
            it = get_item(index_or_name)
            if not it:
                return {"status": "error", "message": f"Item '{index_or_name}' not found in compendium."}

            item_qty = max(1, int(qty or 1))
            if expand_pack and it.get("contents"):
                expanded = expand_pack_contents(it.get("index") or it["name"])
                for sub in expanded:
                    sub["qty"] = str(max(1, self._safe_int(sub.get("qty"), 1)) * item_qty)
                entries_to_add = expanded
            else:
                entries_to_add = [format_item_for_sheet(it, qty=item_qty)]

            added = self._sync_char_inventory_views(char_data, entries_to_add)
            self.save_active_campaign_state()
            return {
                "status": "success",
                "category": "items",
                "character_name": char_data["name"],
                "character_id": char_id,
                "granted": added,
                "expanded_pack": bool(expand_pack and it.get("contents")),
                "message": f"Added {len(added)} item entry(s) ('{it['name']}') to {char_data['name']}'s inventory.",
            }

        elif cat in ("spells", "spell"):
            sp = get_spell(index_or_name, edition=edition)
            if not sp:
                return {"status": "error", "message": f"Spell '{index_or_name}' not found in compendium."}

            # Enforce D&D 5e class spell list validation (blocks wrong-class assignment unless bypass_class_check=True)
            val = validate_character_spell_assignment(
                char_data=char_data,
                spell_entry_or_name=sp,
                edition=edition,
                bypass_class_check=bypass_class_check,
            )
            if not val.get("can_learn"):
                return {
                    "status": "error",
                    "code": "invalid_spell_class",
                    "message": val.get("reason"),
                    "allowed_spell_classes": val.get("allowed_spell_classes", []),
                    "character_classes": val.get("character_classes", []),
                }

            from core.compendiums.dnd_advancement import check_character_spell_legality

            lvl_val = int(sp.get("level", 0))
            is_cantrip_val = (lvl_val == 0)
            is_subclass_bonus = (val.get("matched_via") == "subclass_bonus")

            # Determine preparation status
            if prepare_spell is not None:
                prep_flag = bool(prepare_spell)
            elif is_cantrip_val or is_subclass_bonus:
                prep_flag = True
            else:
                # Default: auto-prepare if under max prep capacity, else add to spellbook unprepared
                leg_before = check_character_spell_legality(char_data, edition)
                if leg_before.get("is_caster") and leg_before.get("prepared_count", 0) >= leg_before.get("max_prepared", 0):
                    prep_flag = False
                else:
                    prep_flag = True

            sheet_spell = format_spell_for_sheet(
                sp,
                spell_atk=spell_atk_str,
                spell_dc=str(spell_dc_val),
                prepared=prep_flag,
                is_always_prepared=is_subclass_bonus,
            )

            def _upsert_spell(spell_list: List[Any], sp_entry: Dict[str, Any]):
                sp_low = sp_entry["name"].strip().lower()
                for idx_s, cur_s in enumerate(spell_list):
                    if isinstance(cur_s, dict) and cur_s.get("name", "").strip().lower() == sp_low:
                        cur_s.update(copy.deepcopy(sp_entry))
                        return
                    elif isinstance(cur_s, str) and cur_s.strip().lower() == sp_low:
                        spell_list[idx_s] = copy.deepcopy(sp_entry)
                        return
                spell_list.append(copy.deepcopy(sp_entry))

            root_spells = char_data.setdefault("spells", [])
            _upsert_spell(root_spells, sheet_spell)
            if native is not None:
                native_spells = native.setdefault("spells", [])
                _upsert_spell(native_spells, sheet_spell)

            self.save_active_campaign_state()

            # Check character spell preparation / roster legality after grant
            legality = check_character_spell_legality(char_data, edition)

            prep_status_str = " (Prepared)" if sheet_spell.get("prepared") else " (Unprepared in Spellbook)"
            if val.get("matched_via") == "magical_secrets":
                success_msg = f"Added spell '{sheet_spell['name']}' ({sheet_spell['level']}){prep_status_str} to {char_data['name']} via Bard Magical Secrets."
            elif val.get("matched_via") == "bypass":
                success_msg = f"Added out-of-class spell '{sheet_spell['name']}' ({sheet_spell['level']}){prep_status_str} to {char_data['name']} (DM Override)."
            elif val.get("matched_via") == "subclass_bonus":
                success_msg = f"Added spell '{sheet_spell['name']}' ({sheet_spell['level']}){prep_status_str} to {char_data['name']} ({val.get('reason')})."
            else:
                success_msg = f"Added spell '{sheet_spell['name']}' ({sheet_spell['level']}){prep_status_str} to {char_data['name']}."

            if not legality.get("is_legal"):
                success_msg += f" ⚠️ Preparation limit exceeded ({legality['prepared_count']}/{legality['max_prepared']})."

            return {
                "status": "success",
                "category": "spells",
                "character_name": char_data["name"],
                "character_id": char_id,
                "granted": [sheet_spell["name"]],
                "entry": sheet_spell,
                "validation": val,
                "legality": legality,
                "message": success_msg,
            }

        return {"status": "error", "message": f"Unknown compendium category '{category}'. Use weapons, armor, items, or spells."}

    def check_character_spell_legality(self, character_name: str, edition: str = "2024") -> Dict[str, Any]:
        """Checks if a character's prepared and known spells adhere to D&D 5e limits."""
        if not self._ensure_active_state():
            return {"is_legal": True, "reason": "No active campaign"}
        cid, char_data = self._find_character(character_name)
        if not char_data:
            return {"is_legal": True, "reason": f"Character '{character_name}' not found"}
        from core.compendiums.dnd_advancement import check_character_spell_legality
        return check_character_spell_legality(char_data, edition=edition)

    # =========================================================================
    # Phase 2: Location-Based Merchant Shop & Interactive Short / Long Rest
    # =========================================================================

    def _get_campaign_edition(self, explicit_edition: Optional[str] = None) -> str:
        if explicit_edition and str(explicit_edition).strip() in ("2014", "2024"):
            return str(explicit_edition).strip()
        meta = (self.manifest.get("campaigns") or {}).get(self.manifest.get("active_campaign") or "", {})
        ed = str(meta.get("ruleset_edition") or (self.active_state or {}).get("ruleset_edition") or "2024").strip()
        return ed if ed in ("2014", "2024") else "2024"

    def _normalize_wallet(self, char_data: Dict[str, Any]) -> Dict[str, int]:
        native = char_data.get("native_system_data") if isinstance(char_data.get("native_system_data"), dict) else {}
        src = char_data.get("currency")
        if not isinstance(src, dict) or not src:
            src = native.get("currency") if isinstance(native.get("currency"), dict) else {}
        gp_raw = src.get("gp") if src.get("gp") is not None else src.get("gold", 0)
        return {
            "cp": max(0, self._safe_int(src.get("cp"), 0)),
            "sp": max(0, self._safe_int(src.get("sp"), 0)),
            "ep": max(0, self._safe_int(src.get("ep"), 0)),
            "gp": max(0, self._safe_int(gp_raw, 0)),
            "pp": max(0, self._safe_int(src.get("pp"), 0)),
        }

    def _write_wallet(self, char_data: Dict[str, Any], wallet: Dict[str, int]) -> None:
        cur = char_data.setdefault("currency", {})
        if isinstance(cur, dict):
            for k in ("cp", "sp", "ep", "gp", "pp"):
                cur[k] = max(0, int(wallet.get(k) or 0))
            if "gold" in cur:
                cur["gold"] = cur["gp"]
        else:
            char_data["currency"] = {k: max(0, int(wallet.get(k) or 0)) for k in ("cp", "sp", "ep", "gp", "pp")}
        native = char_data.get("native_system_data")
        if isinstance(native, dict):
            ncur = native.setdefault("currency", {})
            if isinstance(ncur, dict):
                for k in ("cp", "sp", "ep", "gp", "pp"):
                    ncur[k] = max(0, int(wallet.get(k) or 0))
                if "gold" in ncur:
                    ncur["gold"] = ncur["gp"]

    def _recompute_and_apply_character_ac(
        self,
        char_data: Dict[str, Any],
        armor_name: Optional[str],
        has_shield: bool,
    ) -> Dict[str, Any]:
        from core.compendiums.dnd_armor import get_armor, compute_armor_class

        dex_mod = (self._get_character_ability_score(char_data, "DEX", 10) - 10) // 2
        str_score = self._get_character_ability_score(char_data, "STR", 10)

        # Resolve shield bonus (+2 base + magic ac_bonus if shield is magical in inventory)
        shield_bonus = 2
        native = char_data.get("native_system_data") if isinstance(char_data.get("native_system_data"), dict) else None
        eq_armor_clean = (armor_name or "").strip()
        if eq_armor_clean.lower() in ("none", "unarmored", ""):
            eq_armor_clean = ""

        # Sync equipped flags across inventory mirrors
        all_invs = [char_data.get("inventory"), char_data.get("live_inventory"), char_data.get("tagged_inventory")]
        if native and isinstance(native.get("inventory"), list):
            all_invs.append(native["inventory"])

        shield_toggled = False
        for inv_list in all_invs:
            if not isinstance(inv_list, list):
                continue
            for entry in inv_list:
                if not isinstance(entry, dict):
                    continue
                e_name = (entry.get("name") or "").strip()
                e_cat = (entry.get("category") or "").strip().lower()
                arm_ref = None
                if e_cat in ("armor", "shield", "", "gear", "magic"):
                    cand_arm = get_armor(e_name)
                    if cand_arm and (e_cat in ("armor", "shield") or cand_arm.get("name", "").lower() == e_name.lower() or len(e_name) >= 5):
                        arm_ref = cand_arm
                is_shield_item = (e_cat == "shield") or bool(arm_ref and arm_ref.get("category") == "Shield")
                is_armor_item = (e_cat == "armor") or bool(arm_ref and arm_ref.get("category") != "Shield")

                if is_shield_item:
                    if has_shield and not shield_toggled:
                        entry["equipped"] = True
                        shield_toggled = True
                        if arm_ref and arm_ref.get("ac_bonus"):
                            shield_bonus = 2 + int(arm_ref.get("ac_bonus") or 0)
                    else:
                        entry["equipped"] = False
                elif is_armor_item:
                    entry["equipped"] = bool(eq_armor_clean and e_name.lower() == eq_armor_clean.lower())
            shield_toggled = False

        report = compute_armor_class(
            armor_name=eq_armor_clean or None,
            has_shield=bool(has_shield),
            dex_mod=dex_mod,
            shield_bonus=shield_bonus,
            str_score=str_score,
        )
        new_ac = report["armor_class"]
        char_data["armor_class"] = new_ac
        char_data["armor_equipped"] = report["armor_equipped"]
        char_data["shield_equipped"] = report["shield_equipped"]
        char_data["stealth_disadvantage"] = report["stealth_disadvantage"]
        guard_obj = char_data.setdefault("vitals", {}).setdefault("guard", {"current": new_ac, "max": new_ac, "label": "Armor Defense"})
        guard_obj["current"] = new_ac
        guard_obj["max"] = max(1, new_ac)
        if native is not None:
            native["armor_class"] = new_ac
            native["armor_equipped"] = report["armor_equipped"]
            native["shield_equipped"] = report["shield_equipped"]
        return report

    def _decrement_inventory_item(
        self,
        char_data: Dict[str, Any],
        item_name: str,
        qty: int = 1,
    ) -> Tuple[int, Optional[Dict[str, Any]]]:
        """Decrements/removes `qty` matching `item_name` across all inventory mirrors. Returns (qty_removed, sample_entry)."""
        target_low = (item_name or "").strip().lower()
        if not target_low:
            return 0, None

        qty_needed = max(1, int(qty or 1))
        native = char_data.get("native_system_data") if isinstance(char_data.get("native_system_data"), dict) else None
        inv_lists = [char_data.get("inventory")]
        if native and isinstance(native.get("inventory"), list):
            inv_lists.append(native["inventory"])
        if isinstance(char_data.get("live_inventory"), list):
            inv_lists.append(char_data["live_inventory"])
        if isinstance(char_data.get("tagged_inventory"), list):
            inv_lists.append(char_data["tagged_inventory"])

        removed_count = 0
        sample_entry: Optional[Dict[str, Any]] = None

        for list_idx, inv in enumerate(inv_lists):
            if not isinstance(inv, list):
                continue

            # Exact name match first, then substring fallback
            match_idx = None
            for i, it in enumerate(inv):
                nm = (it.get("name") if isinstance(it, dict) else str(it)).strip().lower()
                nm_clean = re.sub(r"\s*(?:\(x\s*\d+\)|x\s*\d+)$", "", nm, flags=re.IGNORECASE).strip()
                if nm == target_low or nm_clean == target_low:
                    match_idx = i
                    break
            if match_idx is None:
                for i, it in enumerate(inv):
                    nm = (it.get("name") if isinstance(it, dict) else str(it)).strip().lower()
                    if target_low in nm:
                        match_idx = i
                        break
            if match_idx is None:
                continue

            entry = inv[match_idx]
            if isinstance(entry, dict):
                cur_q = max(1, self._safe_int(entry.get("qty"), 1))
                take = min(qty_needed, cur_q)
                if list_idx == 0:
                    removed_count = take
                    sample_entry = copy.deepcopy(entry)
                if cur_q - take > 0:
                    entry["qty"] = str(cur_q - take)
                else:
                    inv.pop(match_idx)
            else:
                s_val = str(entry).strip()
                qm = re.search(r"\s*x\s*(\d+)$", s_val, flags=re.IGNORECASE)
                cur_q = int(qm.group(1)) if qm else 1
                take = min(qty_needed, cur_q)
                if list_idx == 0:
                    removed_count = take
                    sample_entry = {"name": re.sub(r"\s*x\s*\d+$", "", s_val, flags=re.IGNORECASE).strip(), "qty": str(cur_q), "category": "Gear"}
                if cur_q - take > 0:
                    base_n = re.sub(r"\s*x\s*\d+$", "", s_val, flags=re.IGNORECASE).strip()
                    inv[match_idx] = f"{base_n} x{cur_q - take}" if (cur_q - take) > 1 else base_n
                else:
                    inv.pop(match_idx)

        return removed_count, sample_entry

    def set_active_shop(
        self,
        shop_name: str = "Local Merchant",
        location_name: str = "",
        price_multiplier: float = 1.0,
        specialty: str = "general",
        description: str = "",
        include_magic: bool = False,
    ) -> Dict[str, Any]:
        if not self._ensure_active_state():
            return {"status": "error", "message": "No active campaign loaded."}
        mult = round(max(0.5, min(2.0, float(price_multiplier or 1.0))), 2)
        shop_obj = {
            "shop_name": (shop_name or "Local Merchant").strip(),
            "location_name": (location_name or self.active_state.get("current_location") or "Camp / Settlement").strip(),
            "price_multiplier": mult,
            "specialty": (specialty or "general").strip().lower(),
            "description": (description or "").strip(),
            "include_magic": bool(include_magic),
            "opened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self.active_state["active_shop"] = shop_obj
        self.save_active_campaign_state()
        return {"status": "success", "shop": shop_obj}

    def get_merchant_catalog(
        self,
        specialty: str = "all",
        query: str = "",
        edition: Optional[str] = None,
        price_multiplier: float = 1.0,
        include_magic: bool = False,
    ) -> Dict[str, Any]:
        """Builds a priced shop inventory across Weapons, Armor, and Items applying regional markup/discount."""
        from core.compendiums.dnd_weapons import list_weapons
        from core.compendiums.dnd_armor import list_armor
        from core.compendiums.dnd_items import list_items, cost_to_cp, format_cp

        ed = self._get_campaign_edition(edition) if (edition or "").lower() != "all" else "all"
        mult = round(max(0.5, min(2.0, float(price_multiplier or 1.0))), 2)
        spec = (specialty or "all").strip().lower()
        q = (query or "").strip()

        catalog: List[Dict[str, Any]] = []

        def _enrich_entry(raw: Dict[str, Any], cat_key: str) -> Optional[Dict[str, Any]]:
            base_cp = int(raw.get("cost_cp") or cost_to_cp(raw.get("cost")) or 0)
            if base_cp <= 0 and not raw.get("is_homebrew") and not include_magic:
                return None
            adj_cp = max(1, int(round(base_cp * mult))) if base_cp > 0 else 0
            ent = dict(raw)
            ent["compendium_type"] = cat_key
            ent["base_cost_cp"] = base_cp
            ent["base_cost_display"] = raw.get("cost_display") or format_cp(base_cp)
            ent["adjusted_cost_cp"] = adj_cp
            ent["adjusted_cost_display"] = format_cp(adj_cp) if adj_cp > 0 else "Negotiable / Priceless"
            ent["price_multiplier"] = mult
            return ent

        include_wpns = spec in ("all", "general", "weapons", "weapons_armor", "smith")
        include_arms = spec in ("all", "general", "armor", "armorer", "weapons_armor", "smith")
        include_items = spec in ("all", "general", "gear", "outfitter", "items", "potions", "alchemist", "magic_poison", "arcane", "magic")

        if include_magic and spec in ("arcane", "magic", "magic_poison"):
            include_wpns = True
            include_arms = True

        if include_wpns:
            for w in list_weapons(edition=ed, include_magic=include_magic, query=q):
                if spec in ("arcane", "magic") and not w.get("is_magic") and not w.get("is_homebrew"):
                    continue
                enriched = _enrich_entry(w, "weapons")
                if enriched:
                    catalog.append(enriched)

        if include_arms:
            for a in list_armor(edition=ed, include_magic=include_magic, query=q):
                if spec in ("arcane", "magic") and not a.get("is_magic") and not a.get("is_homebrew"):
                    continue
                enriched = _enrich_entry(a, "armor")
                if enriched:
                    catalog.append(enriched)

        if include_items:
            for it in list_items(edition=ed, include_magic=include_magic, query=q):
                sub_low = f"{it.get('category', '')} {it.get('subcategory', '')} {it.get('name', '')}".lower()
                if spec in ("potions", "alchemist"):
                    if not any(k in sub_low for k in ("potion", "elixir", "alchem", "healer", "antitoxin", "herb")):
                        continue
                elif spec == "magic_poison":
                    if not (it.get("is_magic") or "poison" in sub_low or "potion" in sub_low or it.get("is_homebrew")):
                        continue
                elif spec in ("arcane", "magic"):
                    if not (it.get("is_magic") or any(k in sub_low for k in ("arcane", "focus", "spell", "scroll", "potion", "component"))):
                        continue
                enriched = _enrich_entry(it, "items")
                if enriched:
                    catalog.append(enriched)

        # Sort shop cleanly: exact query relevance -> category order -> cost -> name
        cat_rank = {"weapons": 0, "armor": 1, "items": 2}
        catalog.sort(key=lambda x: (cat_rank.get(x.get("compendium_type"), 3), x.get("adjusted_cost_cp", 0), x.get("name", "").lower()))

        active_shop = (self.active_state or {}).get("active_shop")
        return {
            "status": "success",
            "edition": ed,
            "specialty": spec,
            "price_multiplier": mult,
            "include_magic": bool(include_magic),
            "count": len(catalog),
            "items": catalog,
            "presets": MERCHANT_LOCATION_PRESETS,
            "active_shop": active_shop,
        }

    def purchase_merchant_item(
        self,
        character_name: str,
        category: str = "auto",
        index_or_name: str = "",
        qty: int = 1,
        price_multiplier: float = 1.0,
        expand_pack: bool = True,
        edition: Optional[str] = None,
        equip: bool = False,
    ) -> Dict[str, Any]:
        """Purchases a weapon/armor/item for a character, deducting multi-coin D&D currency with automatic change."""
        if not self._ensure_active_state():
            return {"status": "error", "message": "No active campaign loaded."}
        char_id, char_data = self._find_character(character_name)
        if not char_data:
            return {"status": "error", "message": f"Character '{character_name}' not found."}

        from core.compendiums.dnd_weapons import get_weapon
        from core.compendiums.dnd_armor import get_armor
        from core.compendiums.dnd_items import (
            get_item, cost_to_cp, currency_dict_to_cp, deduct_currency, format_cp,
        )

        ed = self._get_campaign_edition(edition)
        cat_clean = (category or "auto").strip().lower()
        target = (index_or_name or "").strip()
        if not target:
            return {"status": "error", "message": "No item name provided for purchase."}

        entry = None
        resolved_cat = cat_clean
        if cat_clean in ("auto", "all", ""):
            entry = get_weapon(target)
            if entry:
                resolved_cat = "weapons"
            else:
                entry = get_armor(target)
                if entry:
                    resolved_cat = "armor"
                else:
                    entry = get_item(target)
                    if entry:
                        resolved_cat = "items"
        elif cat_clean in ("weapons", "weapon"):
            entry = get_weapon(target)
            resolved_cat = "weapons"
        elif cat_clean in ("armor", "armors", "shield", "shields"):
            entry = get_armor(target)
            resolved_cat = "armor"
        else:
            entry = get_item(target)
            resolved_cat = "items"

        if not entry:
            return {"status": "error", "message": f"'{index_or_name}' not found in merchant catalog."}

        buy_qty = max(1, int(qty or 1))
        mult = max(0.5, min(2.0, float(price_multiplier or 1.0)))
        base_cp = int(entry.get("cost_cp") or cost_to_cp(entry.get("cost")) or 0)
        unit_cp = max(1, int(round(base_cp * mult))) if base_cp > 0 else 0
        total_cp = unit_cp * buy_qty

        wallet = self._normalize_wallet(char_data)
        ok, new_wallet, coin_msg = deduct_currency(wallet, total_cp)
        if not ok:
            return {"status": "error", "message": f"{char_data['name']}: {coin_msg}"}

        grant_res = self.add_compendium_entry_to_character(
            character_name=char_data["name"],
            category=resolved_cat,
            index_or_name=entry.get("index") or entry["name"],
            edition=ed,
            expand_pack=bool(expand_pack),
            qty=buy_qty,
            equip=bool(equip),
        )
        if grant_res.get("status") != "success":
            return grant_res

        self._write_wallet(char_data, new_wallet)
        self.save_active_campaign_state()
        rem_cp = currency_dict_to_cp(new_wallet)
        return {
            "status": "success",
            "character_name": char_data["name"],
            "character_id": char_id,
            "category": resolved_cat,
            "item_name": entry["name"],
            "qty": buy_qty,
            "unit_price_cp": unit_cp,
            "total_cost_cp": total_cp,
            "total_cost_display": format_cp(total_cp),
            "currency": new_wallet,
            "wallet_display": format_cp(rem_cp),
            "granted": grant_res.get("granted", []),
            "message": f"{char_data['name']} bought {buy_qty}x {entry['name']} for {format_cp(total_cp)}. Remaining: {format_cp(rem_cp)}.",
        }

    def sell_merchant_item(
        self,
        character_name: str,
        item_name: str,
        sell_rate: float = 0.5,
        qty: int = 1,
        source: str = "auto",
        override_value_gp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Sells an item or weapon back to a merchant, crediting multi-coin payout and updating AC/attunement if needed."""
        if not self._ensure_active_state():
            return {"status": "error", "message": "No active campaign loaded."}
        char_id, char_data = self._find_character(character_name)
        if not char_data:
            return {"status": "error", "message": f"Character '{character_name}' not found."}

        from core.compendiums.dnd_weapons import get_weapon
        from core.compendiums.dnd_armor import get_armor
        from core.compendiums.dnd_items import (
            get_item, cost_to_cp, add_currency, currency_dict_to_cp, format_cp,
        )

        target = (item_name or "").strip()
        if not target:
            return {"status": "error", "message": "No item specified to sell."}

        rate = max(0.1, min(1.0, float(sell_rate or 0.5)))
        sell_qty_req = max(1, int(qty or 1))
        src_mode = (source or "auto").strip().lower()

        sold_qty = 0
        sold_entry: Optional[Dict[str, Any]] = None
        sold_from = "inventory"

        if src_mode in ("auto", "inventory", "items", "armor"):
            sold_qty, sold_entry = self._decrement_inventory_item(char_data, target, qty=sell_qty_req)

        if sold_qty <= 0 and src_mode in ("auto", "weapons", "weapon"):
            wpns = char_data.get("weapons") or []
            native = char_data.get("native_system_data") if isinstance(char_data.get("native_system_data"), dict) else None
            t_low = target.lower()
            w_idx = next(
                (i for i, w in enumerate(wpns) if ((w.get("name") if isinstance(w, dict) else str(w)).strip().lower() == t_low)),
                None,
            )
            if w_idx is None:
                w_idx = next(
                    (i for i, w in enumerate(wpns) if t_low in (w.get("name") if isinstance(w, dict) else str(w)).strip().lower()),
                    None,
                )
            if w_idx is not None:
                rem_w = wpns[w_idx]
                w_nm = (rem_w.get("name") if isinstance(rem_w, dict) else str(rem_w)).strip()
                if w_nm.lower() in ("unarmed strike", "fist", "fists", "natural weapon"):
                    return {"status": "error", "message": "Cannot sell Unarmed Strike."}
                wpns.pop(w_idx)
                if native and isinstance(native.get("weapons"), list):
                    for nw_i, nw in enumerate(native["weapons"]):
                        nw_nm = (nw.get("name") if isinstance(nw, dict) else str(nw)).strip().lower()
                        if nw_nm == w_nm.lower():
                            native["weapons"].pop(nw_i)
                            break
                sold_qty = 1
                sold_entry = {"name": w_nm, "qty": "1", "category": "Weapon"}
                sold_from = "weapons"

        if sold_qty <= 0 or not sold_entry:
            return {"status": "error", "message": f"'{item_name}' was not found on {char_data['name']}'s sheet."}

        resolved_name = sold_entry.get("name") or target
        if override_value_gp is not None:
            unit_base_cp = max(0, int(round(float(override_value_gp) * 100)))
        else:
            ref = get_item(resolved_name) or get_armor(resolved_name) or get_weapon(resolved_name)
            if ref:
                raw_cp = int(ref.get("cost_cp") or cost_to_cp(ref.get("cost")) or 0)
                bundle_q = max(1, int(ref.get("quantity_bundle") or 1))
                unit_base_cp = max(1, raw_cp // bundle_q) if raw_cp > 0 else 100
            else:
                unit_base_cp = 100

        payout_cp = max(1, int(round(unit_base_cp * rate * sold_qty)))
        wallet = self._normalize_wallet(char_data)
        new_wallet = add_currency(wallet, payout_cp)
        self._write_wallet(char_data, new_wallet)

        # If sold item was equipped armor or shield, recompute AC
        if sold_entry.get("equipped") or (sold_entry.get("category") in ("Armor", "Shield")):
            still_armor = next(
                (it.get("name") for it in char_data.get("inventory", []) if isinstance(it, dict) and it.get("category") == "Armor" and it.get("equipped")),
                None,
            )
            still_shield = any(
                isinstance(it, dict) and it.get("category") == "Shield" and it.get("equipped")
                for it in char_data.get("inventory", [])
            )
            self._recompute_and_apply_character_ac(char_data, still_armor, still_shield)

        # If sold item was attuned, remove from attunement
        att_list = char_data.get("attuned_items")
        if isinstance(att_list, list):
            new_att = [a for a in att_list if a.strip().lower() != resolved_name.lower()]
            if len(new_att) != len(att_list):
                char_data["attuned_items"] = new_att
                char_data["attuned_magic_items"] = {"current": len(new_att), "max": 3}

        self.save_active_campaign_state()
        total_cp = currency_dict_to_cp(new_wallet)
        return {
            "status": "success",
            "character_name": char_data["name"],
            "character_id": char_id,
            "sold_item": resolved_name,
            "sold_from": sold_from,
            "qty": sold_qty,
            "payout_cp": payout_cp,
            "payout_display": format_cp(payout_cp),
            "currency": new_wallet,
            "wallet_display": format_cp(total_cp),
            "message": f"{char_data['name']} sold {sold_qty}x {resolved_name} for {format_cp(payout_cp)}. Total purse: {format_cp(total_cp)}.",
        }

    def _parse_class_spell_profile(self, char_data: Dict[str, Any], edition: str = "2024") -> Dict[str, Any]:
        """Resolves spellcasting classes, preparation style, max slot level, and prepared spell limit."""
        from core.compendiums.dnd_spells import CLASS_PREPARATION_TYPE, CASTER_CLASSES

        native = char_data.get("native_system_data") if isinstance(char_data.get("native_system_data"), dict) else {}
        class_str = str(
            char_data.get("class_and_level")
            or native.get("class_level")
            or char_data.get("archetype")
            or ""
        ).strip()
        lvl_fallback = max(1, self._safe_int(char_data.get("level"), 1))
        ed = "2024" if str(edition).strip() == "2024" else "2014"

        found_classes: List[Tuple[str, int]] = []
        for cls in CASTER_CLASSES:
            m = re.search(rf"\b{re.escape(cls)}\s*(\d+)?\b", class_str, flags=re.IGNORECASE)
            if m:
                c_lvl = int(m.group(1)) if m.group(1) else lvl_fallback
                found_classes.append((cls, max(1, c_lvl)))

        # Check subclass casters (Eldritch Knight / Arcane Trickster -> Wizard list)
        if not found_classes:
            if "eldritch knight" in class_str.lower() or "arcane trickster" in class_str.lower():
                found_classes.append(("Wizard", lvl_fallback))

        slots = native.get("spell_slots") or char_data.get("spell_slots") or {}
        slot_max_rank = 0
        for rank in range(1, 10):
            s_ent = slots.get(f"level_{rank}")
            if isinstance(s_ent, dict) and int(s_ent.get("max") or 0) > 0:
                slot_max_rank = rank

        is_caster = bool(found_classes or slot_max_rank > 0 or char_data.get("spells") or native.get("spells"))
        primary_class = found_classes[0][0] if found_classes else ""
        primary_lvl = found_classes[0][1] if found_classes else lvl_fallback

        if slot_max_rank <= 0 and found_classes:
            p_low = primary_class.lower()
            if p_low in ("cleric", "druid", "wizard", "bard", "sorcerer"):
                slot_max_rank = min(9, (primary_lvl + 1) // 2)
            elif p_low == "warlock":
                slot_max_rank = min(5, (primary_lvl + 1) // 2)
            elif p_low in ("paladin", "ranger", "artificer"):
                if ed == "2024" or p_low == "artificer" or primary_lvl >= 2:
                    slot_max_rank = min(5, max(1, (primary_lvl + 3) // 4))

        prep_style = CLASS_PREPARATION_TYPE.get(primary_class.lower(), "known")
        if primary_class.lower() == "ranger" and ed == "2024":
            prep_style = "prepared_full_list"

        can_swap_from_class_list = primary_class.lower() in ("cleric", "druid", "paladin", "artificer") or (
            primary_class.lower() == "ranger" and ed == "2024"
        )
        can_swap_from_spellbook = primary_class.lower() == "wizard"
        can_swap_spells = bool(can_swap_from_class_list or can_swap_from_spellbook)

        # Compute max prepared cap
        stored_cap = char_data.get("max_prepared_leveled_spells")
        if stored_cap is None:
            stored_cap = native.get("max_prepared_leveled_spells")

        int_mod = (self._get_character_ability_score(char_data, "INT", 10) - 10) // 2
        wis_mod = (self._get_character_ability_score(char_data, "WIS", 10) - 10) // 2
        cha_mod = (self._get_character_ability_score(char_data, "CHA", 10) - 10) // 2

        mod_by_class = {
            "cleric": wis_mod, "druid": wis_mod, "ranger": wis_mod,
            "wizard": int_mod, "artificer": int_mod,
            "paladin": cha_mod, "bard": cha_mod, "sorcerer": cha_mod, "warlock": cha_mod,
        }
        c_mod = mod_by_class.get(primary_class.lower(), int_mod)

        if stored_cap is not None and self._safe_int(stored_cap, 0) > 0:
            max_prepared = self._safe_int(stored_cap, 1)
        elif ed == "2024" and primary_class.lower() in ("cleric", "druid", "wizard"):
            prep_2024_full = {
                1: 4, 2: 5, 3: 6, 4: 7, 5: 9, 6: 10, 7: 11, 8: 12, 9: 14, 10: 15,
                11: 16, 12: 16, 13: 17, 14: 17, 15: 18, 16: 18, 17: 19, 18: 20, 19: 21, 20: 22,
            }
            max_prepared = prep_2024_full.get(min(20, primary_lvl), primary_lvl + 2)
        elif ed == "2024" and primary_class.lower() in ("paladin", "ranger"):
            prep_2024_half = {
                1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 6, 7: 7, 8: 7, 9: 9, 10: 9,
                11: 10, 12: 10, 13: 11, 14: 11, 15: 12, 16: 12, 17: 14, 18: 14, 19: 15, 20: 15,
            }
            max_prepared = prep_2024_half.get(min(20, primary_lvl), max(2, primary_lvl // 2 + 2))
        elif primary_class.lower() in ("cleric", "druid", "wizard"):
            max_prepared = max(1, c_mod + primary_lvl)
        elif primary_class.lower() in ("paladin", "artificer", "ranger"):
            max_prepared = max(1, c_mod + max(1, primary_lvl // 2))
        else:
            max_prepared = max(1, primary_lvl + 1)

        return {
            "is_caster": is_caster,
            "class_names": [c for c, _ in found_classes],
            "primary_class": primary_class,
            "prep_style": prep_style,
            "can_swap_spells": can_swap_spells,
            "can_swap_from_class_list": can_swap_from_class_list,
            "can_swap_from_spellbook": can_swap_from_spellbook,
            "max_slot_level": slot_max_rank,
            "max_prepared_leveled_spells": max_prepared,
            "edition": ed,
        }

    def get_rest_modal_state(self, rest_type: str = "short", edition: Optional[str] = None) -> Dict[str, Any]:
        """Builds full interactive Rest UI payload across the active campaign party."""
        if not self._ensure_active_state():
            return {"status": "error", "message": "No active campaign loaded."}

        from core.compendiums.dnd_armor import get_armor
        from core.compendiums.dnd_items import get_item
        from core.compendiums.dnd_spells import get_spells_for_class

        ed = self._get_campaign_edition(edition)
        dice_mode = self.get_dice_mode()
        chars_dict = self.active_state.get("characters", {})
        char_summaries: List[Dict[str, Any]] = []

        for cid, ch in chars_dict.items():
            native = ch.get("native_system_data") if isinstance(ch.get("native_system_data"), dict) else {}
            vitals = ch.get("vitals") or {}
            hp_obj = vitals.get("health") or {"current": ch.get("current_health", 10), "max": ch.get("max_health", 10)}
            cur_hp = self._safe_int(hp_obj.get("current"), 10)
            max_hp = max(1, self._safe_int(hp_obj.get("max"), 10))
            lvl = max(1, self._safe_int(ch.get("level"), 1))

            str_score = self._get_character_ability_score(ch, "STR", 10)
            dex_score = self._get_character_ability_score(ch, "DEX", 10)
            con_score = self._get_character_ability_score(ch, "CON", 10)
            dex_mod = (dex_score - 10) // 2
            con_mod = (con_score - 10) // 2

            # Hit dice resolution (from custom_meters['hit_dice'], root hit_dice string, or class default)
            meters = ch.get("custom_meters") or native.get("custom_meters") or {}
            hd_meter = meters.get("hit_dice") if isinstance(meters, dict) else None
            raw_hd_str = str(
                (hd_meter.get("label") if isinstance(hd_meter, dict) else "")
                or ch.get("hit_dice")
                or (native.get("hit_dice", {}).get("die") if isinstance(native.get("hit_dice"), dict) else native.get("hit_dice"))
                or ""
            )
            die_m = re.search(r"d(4|6|8|10|12)\b", raw_hd_str, flags=re.IGNORECASE)
            if die_m:
                die_face = int(die_m.group(1))
            else:
                arch_low = (ch.get("archetype") or ch.get("class_and_level") or "").lower()
                if any(k in arch_low for k in ("wizard", "sorcerer")):
                    die_face = 6
                elif any(k in arch_low for k in ("fighter", "paladin", "ranger")):
                    die_face = 10
                elif "barbarian" in arch_low:
                    die_face = 12
                else:
                    die_face = 8

            hd_cur = self._safe_int(hd_meter.get("current"), lvl) if isinstance(hd_meter, dict) else lvl
            hd_max = max(1, self._safe_int(hd_meter.get("max"), lvl)) if isinstance(hd_meter, dict) else lvl

            # Expendable class meters (excluding hit_dice)
            exp_meters = []
            if isinstance(meters, dict):
                for m_key, m_val in meters.items():
                    if m_key == "hit_dice" or not isinstance(m_val, dict):
                        continue
                    exp_meters.append({
                        "key": m_key,
                        "label": m_val.get("label") or m_key,
                        "current": self._safe_int(m_val.get("current"), 0),
                        "max": max(1, self._safe_int(m_val.get("max"), 1)),
                        "reset_on": m_val.get("reset_on", "long_rest"),
                    })

            # Scan inventory for Consumables, Armor/Shields, and Attunable Magic Items
            raw_inv = ch.get("inventory") if isinstance(ch.get("inventory"), list) else (native.get("inventory") or [])
            consumables: List[Dict[str, Any]] = []
            owned_armor: List[Dict[str, Any]] = []
            owned_shields: List[Dict[str, Any]] = []
            attunable_items: List[Dict[str, Any]] = []
            seen_att = set()

            for it in raw_inv:
                if isinstance(it, dict):
                    i_name = (it.get("name") or "").strip()
                    i_qty = max(1, self._safe_int(it.get("qty"), 1))
                    i_cat = (it.get("category") or "").strip()
                    i_notes = (it.get("notes") or "").strip()
                    i_eq = bool(it.get("equipped", False))
                    i_magic = bool(it.get("is_magic", False))
                    i_req_att = bool(it.get("requires_attunement", False))
                else:
                    s_raw = str(it).strip()
                    qm = re.search(r"^(.*?)\s*x\s*(\d+)$", s_raw, flags=re.IGNORECASE)
                    i_name = qm.group(1).strip() if qm else s_raw
                    i_qty = int(qm.group(2)) if qm else 1
                    i_cat = ""
                    i_notes = ""
                    i_eq = False
                    i_magic = False
                    i_req_att = False

                if not i_name:
                    continue
                low_n = i_name.lower()

                # Armor/Shield detection
                cat_low = i_cat.lower()
                arm_ref = None
                if cat_low in ("armor", "shield", "", "gear", "magic"):
                    cand_arm = get_armor(i_name)
                    if cand_arm and (cat_low in ("armor", "shield") or cand_arm.get("name", "").lower() == low_n or len(low_n) >= 5):
                        arm_ref = cand_arm
                if cat_low == "shield" or (arm_ref and arm_ref.get("category") == "Shield"):
                    owned_shields.append({
                        "name": i_name,
                        "equipped": i_eq,
                        "ac_bonus": 2 + int((arm_ref or {}).get("ac_bonus") or 0),
                        "notes": i_notes or "+2 AC",
                    })
                elif i_cat.lower() == "armor" or (arm_ref and arm_ref.get("category") != "Shield"):
                    owned_armor.append({
                        "name": i_name,
                        "equipped": i_eq,
                        "ac_base": (arm_ref or {}).get("ac_base", 10),
                        "dex_bonus": (arm_ref or {}).get("dex_bonus", True),
                        "max_dex_bonus": (arm_ref or {}).get("max_dex_bonus"),
                        "ac_bonus": (arm_ref or {}).get("ac_bonus", 0),
                        "str_minimum": (arm_ref or {}).get("str_minimum", 0),
                        "stealth_disadvantage": bool((arm_ref or {}).get("stealth_disadvantage", False)),
                        "notes": i_notes,
                    })

                # Consumable detection
                item_ref = get_item(i_name)
                is_cons = (
                    i_cat.lower() == "consumable"
                    or bool((item_ref or {}).get("consumable"))
                    or any(kw in low_n for kw in ("potion", "elixir", "ration", "goodberry", "antitoxin", "healer", "scroll"))
                )
                if is_cons:
                    heal_formula = ""
                    if "supreme" in low_n and ("healing" in low_n or "potion" in low_n):
                        heal_formula = "10d4+20"
                    elif "superior" in low_n and ("healing" in low_n or "potion" in low_n):
                        heal_formula = "8d4+8"
                    elif "greater" in low_n and ("healing" in low_n or "potion" in low_n):
                        heal_formula = "4d4+4"
                    elif "healing" in low_n or "healing potion" in low_n:
                        heal_formula = "2d4+2"
                    elif "goodberry" in low_n:
                        heal_formula = "1"
                    consumables.append({
                        "name": i_name,
                        "qty": i_qty,
                        "heal_formula": heal_formula,
                        "notes": i_notes,
                    })

                # Attunable item detection
                if i_req_att or (item_ref and item_ref.get("requires_attunement")) or (i_magic and "attun" in i_notes.lower()):
                    if low_n not in seen_att:
                        seen_att.add(low_n)
                        attunable_items.append({
                            "name": i_name,
                            "rarity": (item_ref or {}).get("rarity", "Magic"),
                            "description": (item_ref or {}).get("description", i_notes),
                        })

            for w in ch.get("weapons") or []:
                if isinstance(w, dict) and w.get("name"):
                    wn = w["name"].strip()
                    w_notes = str(w.get("notes") or "")
                    if "attun" in w_notes.lower() and wn.lower() not in seen_att:
                        seen_att.add(wn.lower())
                        attunable_items.append({"name": wn, "rarity": "Magic", "description": w_notes})

            attuned_items = list(ch.get("attuned_items") or [])
            for att_name in attuned_items:
                if att_name and att_name.strip().lower() not in seen_att:
                    seen_att.add(att_name.strip().lower())
                    attunable_items.append({"name": att_name.strip(), "rarity": "Magic", "description": ""})

            equipped_armor_name = ch.get("armor_equipped") or ""
            if not equipped_armor_name:
                eq_arm = next((a["name"] for a in owned_armor if a.get("equipped")), "")
                equipped_armor_name = eq_arm
            shield_equipped = bool(ch.get("shield_equipped")) or any(s.get("equipped") for s in owned_shields)

            # Spellcasting profile & eligible class spells
            spell_prof = self._parse_class_spell_profile(ch, edition=ed)
            raw_spells = native.get("spells") if isinstance(native.get("spells"), list) and native.get("spells") else (ch.get("spells") or [])
            cur_spells: List[Dict[str, Any]] = []
            for sp in raw_spells:
                if isinstance(sp, dict):
                    s_lvl_raw = sp.get("level", 0)
                    if isinstance(s_lvl_raw, int):
                        s_lvl = s_lvl_raw
                    else:
                        lm = re.search(r"\d+", str(s_lvl_raw))
                        s_lvl = 0 if "cantrip" in str(s_lvl_raw).lower() else (int(lm.group(0)) if lm else 1)
                    cur_spells.append({
                        "name": sp.get("name", ""),
                        "level": s_lvl,
                        "level_label": sp.get("level") if isinstance(sp.get("level"), str) else ("Cantrip" if s_lvl == 0 else f"Level {s_lvl}"),
                        "school": sp.get("school", ""),
                        "is_cantrip": bool(sp.get("is_cantrip") or s_lvl == 0),
                        "prepared": bool(sp.get("prepared", True) if (sp.get("is_cantrip") or s_lvl == 0 or sp.get("is_always_prepared")) else sp.get("prepared", False)),
                        "is_always_prepared": bool(sp.get("is_always_prepared", False)),
                    })
                elif isinstance(sp, str) and sp.strip():
                    cur_spells.append({
                        "name": sp.strip(),
                        "level": 1,
                        "level_label": "Spell",
                        "school": "",
                        "is_cantrip": "cantrip" in sp.lower(),
                        "prepared": True,
                        "is_always_prepared": False,
                    })

            eligible_class_spells: List[Dict[str, Any]] = []
            if spell_prof["can_swap_from_class_list"] and spell_prof["primary_class"]:
                max_sl = max(1, spell_prof["max_slot_level"])
                for cs in get_spells_for_class(
                    class_name=spell_prof["primary_class"],
                    max_spell_level=max_sl,
                    edition=ed,
                    include_cantrips=False,
                ):
                    eligible_class_spells.append({
                        "name": cs["name"],
                        "index": cs.get("index", ""),
                        "level": int(cs.get("level", 1)),
                        "level_label": cs.get("level_label", ""),
                        "school": cs.get("school", ""),
                        "concentration": bool(cs.get("concentration", False)),
                        "ritual": bool(cs.get("ritual", False)),
                        "damage": cs.get("damage", ""),
                        "description": str(cs.get("description_2024") if ed == "2024" and cs.get("description_2024") else cs.get("description", ""))[:240],
                    })

            slots = native.get("spell_slots") or ch.get("spell_slots") or {}
            char_summaries.append({
                "id": ch.get("id") or cid,
                "name": ch.get("name", cid),
                "archetype": ch.get("archetype", "Adventurer"),
                "level": lvl,
                "hp": {"current": cur_hp, "max": max_hp, "temp_hp": self._safe_int(vitals.get("temp_hp", ch.get("temp_hp")), 0)},
                "armor_class": self._safe_int(ch.get("armor_class", vitals.get("guard", {}).get("current")), 10),
                "armor_equipped": equipped_armor_name,
                "shield_equipped": shield_equipped,
                "dex_mod": dex_mod,
                "str_score": str_score,
                "hit_dice": {
                    "current": hd_cur,
                    "max": hd_max,
                    "die_face": die_face,
                    "die_str": f"1d{die_face}",
                    "con_mod": con_mod,
                },
                "expendable_meters": exp_meters,
                "consumables": consumables,
                "owned_armor": owned_armor,
                "owned_shields": owned_shields,
                "attunable_items": attunable_items,
                "attuned_items": attuned_items,
                "max_attuned": 3,
                "spell_slots": slots,
                "spellcasting": {
                    **spell_prof,
                    "current_spells": cur_spells,
                    "eligible_class_spells": eligible_class_spells,
                },
            })

        return {
            "status": "success",
            "rest_type": "long" if str(rest_type).lower().strip() == "long" else "short",
            "dice_mode": dice_mode,
            "edition": ed,
            "characters": char_summaries,
        }

    def commit_interactive_rest(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Commits an interactive Short or Long Rest across party characters:
        - Enforces max-3 Attunement limit
        - Spends Hit Dice (digital auto-roll OR physical reported healing)
        - Uses Consumables / Healing Potions
        - Swaps equipped Armor / Shield & recomputes AC
        - Updates Prepared Spells / Class Spell List preparations
        - Recharges Pact Slots / Short-Rest pools or performs full Long Rest recovery (2014 vs 2024 Hit Dice recovery)
        """
        if not self._ensure_active_state():
            return {"status": "error", "message": "No active campaign loaded."}

        from core.compendiums.dnd_spells import get_spell, format_spell_for_sheet

        data = payload or {}
        is_long = (str(data.get("rest_type") or "short").lower().strip() == "long")
        dice_mode = (str(data.get("dice_mode") or self.get_dice_mode()).lower().strip())
        ed = self._get_campaign_edition(data.get("edition"))
        char_inputs = data.get("characters") if isinstance(data.get("characters"), dict) else {}

        chars_dict = self.active_state.get("characters", {})

        # Pre-pass validation: never partially save if any hero exceeds max 3 attunement slots
        resolved_targets: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
        for cid, ch in chars_dict.items():
            c_in = char_inputs.get(cid) or char_inputs.get(ch.get("name", "")) or {}
            if "attuned_items" in c_in and c_in["attuned_items"] is not None:
                att_in = [str(x).strip() for x in c_in["attuned_items"] if str(x).strip()]
                dedup_att = list(dict.fromkeys(att_in))
                if len(dedup_att) > 3:
                    return {
                        "status": "error",
                        "message": f"{ch.get('name', cid)} cannot attune to more than 3 magic items (selected {len(dedup_att)}).",
                    }
            resolved_targets.append((cid, ch, c_in))

        reports: List[str] = []
        char_outcomes: Dict[str, Any] = {}

        for cid, ch, c_in in resolved_targets:
            name = ch.get("name", cid)
            native = ch.get("native_system_data") if isinstance(ch.get("native_system_data"), dict) else None
            vitals = ch.setdefault("vitals", {})
            hp_obj = vitals.setdefault("health", {"current": 10, "max": 10, "label": "Health"})
            max_hp = max(1, self._safe_int(hp_obj.get("max"), 10))
            hp_obj["max"] = max_hp
            hp_obj["current"] = max(0, min(max_hp, self._safe_int(hp_obj.get("current"), max_hp)))

            lvl = max(1, self._safe_int(ch.get("level"), 1))
            con_mod = (self._get_character_ability_score(ch, "CON", 10) - 10) // 2

            meters = ch.setdefault("custom_meters", {})
            if "hit_dice" not in meters:
                raw_hd_str = str(ch.get("hit_dice") or (native.get("hit_dice", {}).get("die") if native and isinstance(native.get("hit_dice"), dict) else "") or "")
                dm = re.search(r"d(4|6|8|10|12)\b", raw_hd_str, flags=re.IGNORECASE)
                d_face = int(dm.group(1)) if dm else 8
                meters["hit_dice"] = {"label": f"Hit Dice ({lvl}d{d_face})", "current": lvl, "max": lvl, "reset_on": "long_rest"}
            hd_meter = meters["hit_dice"]
            dm_hd = re.search(r"d(4|6|8|10|12)\b", str(hd_meter.get("label") or ch.get("hit_dice") or ""), flags=re.IGNORECASE)
            die_face = int(dm_hd.group(1)) if dm_hd else 8

            notes: List[str] = []

            # 1. Hit Dice healing (Short Rest)
            hd_spend_req = max(0, self._safe_int(c_in.get("hit_dice_to_spend"), 0))
            if not is_long and hd_spend_req > 0 and hd_meter.get("current", 0) > 0:
                spent = min(hd_spend_req, int(hd_meter.get("current", 0)))
                healed_total = 0
                if dice_mode == "physical":
                    healed_total = max(0, self._safe_int(c_in.get("manual_hp_gain"), 0))
                else:
                    for _ in range(spent):
                        roll_r = self.parse_and_roll(f"1d{die_face}", character_name=name)
                        roll_v = roll_r.get("total", 4) if roll_r.get("status") == "success" else 4
                        healed_total += max(1, roll_v + con_mod)
                old_hp = hp_obj["current"]
                hp_obj["current"] = min(max_hp, old_hp + healed_total)
                hd_meter["current"] -= spent
                notes.append(f"spent {spent} Hit Dice (+{hp_obj['current'] - old_hp} HP -> {hp_obj['current']}/{max_hp})")

            # 2. Consumables used
            for cons in (c_in.get("consumables_used") or []):
                if isinstance(cons, str):
                    c_name, c_qty, c_manual = cons, 1, None
                elif isinstance(cons, dict):
                    c_name = str(cons.get("name") or "").strip()
                    c_qty = max(1, self._safe_int(cons.get("qty"), 1))
                    c_manual = cons.get("manual_hp_gain")
                else:
                    continue
                if not c_name:
                    continue
                taken, _ = self._decrement_inventory_item(ch, c_name, qty=c_qty)
                if taken <= 0:
                    continue
                low_cn = c_name.lower()
                heal_f = ""
                if "supreme" in low_cn and ("healing" in low_cn or "potion" in low_cn):
                    heal_f = "10d4+20"
                elif "superior" in low_cn and ("healing" in low_cn or "potion" in low_cn):
                    heal_f = "8d4+8"
                elif "greater" in low_cn and ("healing" in low_cn or "potion" in low_cn):
                    heal_f = "4d4+4"
                elif "healing" in low_cn or "potion of healing" in low_cn:
                    heal_f = "2d4+2"
                elif "goodberry" in low_cn:
                    heal_f = "1"

                if heal_f:
                    gain = 0
                    if c_manual is not None and dice_mode == "physical":
                        gain = max(0, self._safe_int(c_manual, 0))
                    else:
                        for _ in range(taken):
                            if heal_f == "1":
                                gain += 1
                            else:
                                r_p = self.parse_and_roll(heal_f, character_name=name)
                                gain += max(1, r_p.get("total", 5))
                    old_hp = hp_obj["current"]
                    hp_obj["current"] = min(max_hp, old_hp + gain)
                    notes.append(f"used {taken}x {c_name} (+{hp_obj['current'] - old_hp} HP)")
                else:
                    notes.append(f"used {taken}x {c_name}")

            # 3. Armor & Shield Swap + Live AC Recompute
            if "equipped_armor" in c_in or "shield_equipped" in c_in:
                new_arm = c_in.get("equipped_armor", ch.get("armor_equipped"))
                new_sh = bool(c_in.get("shield_equipped", ch.get("shield_equipped", False)))
                ac_res = self._recompute_and_apply_character_ac(ch, new_arm, new_sh)
                notes.append(f"gear -> AC {ac_res['armor_class']} ({ac_res['armor_equipped']}{'+Shield' if ac_res['shield_equipped'] else ''})")

            # 4. Attunement Slots (Max 3 enforced above)
            if "attuned_items" in c_in and c_in["attuned_items"] is not None:
                clean_att = list(dict.fromkeys([str(x).strip() for x in c_in["attuned_items"] if str(x).strip()]))
                ch["attuned_items"] = clean_att
                ch["attuned_magic_items"] = {"current": len(clean_att), "max": 3}
                if native is not None:
                    native["attuned_items"] = list(clean_att)
                    native["attuned_magic_items"] = {"current": len(clean_att), "max": 3}
                notes.append(f"attuned ({len(clean_att)}/3)")

            # 5. Prepared Spells / Class Spell List Swap
            if "prepared_spell_names" in c_in and isinstance(c_in["prepared_spell_names"], list):
                target_prep = {str(x).strip().lower(): str(x).strip() for x in c_in["prepared_spell_names"] if str(x).strip()}
                spell_prof = self._parse_class_spell_profile(ch, edition=ed)
                prof_b = max(2, self._safe_int(ch.get("prof_bonus") or (native.get("proficiency_bonus") if native else None), calculate_dnd_proficiency(lvl)))
                cast_ab = (ch.get("spellcasting_ability") or (native.get("spellcasting_ability") if native else None) or "INT").strip().upper()[:3]
                if cast_ab not in ("INT", "WIS", "CHA"):
                    p_cls = spell_prof.get("primary_class", "").lower()
                    cast_ab = "WIS" if p_cls in ("cleric", "druid", "ranger") else ("CHA" if p_cls in ("bard", "paladin", "sorcerer", "warlock") else "INT")
                c_mod = (self._get_character_ability_score(ch, cast_ab, 10) - 10) // 2
                s_dc = str(8 + prof_b + c_mod)
                s_atk = f"+{prof_b + c_mod}" if (prof_b + c_mod) >= 0 else str(prof_b + c_mod)

                # Merge existing spells from root & native
                existing_raw = []
                if isinstance(ch.get("spells"), list) and ch["spells"]:
                    existing_raw = list(ch["spells"])
                elif native and isinstance(native.get("spells"), list):
                    existing_raw = list(native["spells"])

                updated_spells: List[Dict[str, Any]] = []
                present_lows = set()
                for s_ent in existing_raw:
                    if isinstance(s_ent, dict):
                        clone = copy.deepcopy(s_ent)
                        sn_low = (clone.get("name") or "").strip().lower()
                        is_c = bool(clone.get("is_cantrip") or "cantrip" in str(clone.get("level", "")).lower())
                        is_alw = bool(clone.get("is_always_prepared", False))
                        if not is_c and not is_alw:
                            clone["prepared"] = (sn_low in target_prep)
                        else:
                            clone["prepared"] = True
                        updated_spells.append(clone)
                        present_lows.add(sn_low)
                    elif isinstance(s_ent, str) and s_ent.strip():
                        sn = s_ent.strip()
                        sn_low = sn.lower()
                        is_c = "cantrip" in sn_low
                        updated_spells.append({
                            "name": sn,
                            "level": "Cantrip" if is_c else "1st Level",
                            "is_cantrip": is_c,
                            "prepared": True if is_c else (sn_low in target_prep),
                        })
                        present_lows.add(sn_low)

                for low_k, orig_name in target_prep.items():
                    if low_k not in present_lows:
                        sp_comp = get_spell(orig_name, edition=ed)
                        if sp_comp:
                            updated_spells.append(format_spell_for_sheet(sp_comp, spell_atk=s_atk, spell_dc=s_dc, prepared=True))
                        else:
                            updated_spells.append({"name": orig_name, "level": "1st Level", "is_cantrip": False, "prepared": True})

                ch["spells"] = updated_spells
                if native is not None:
                    native["spells"] = copy.deepcopy(updated_spells)
                prep_count = sum(1 for sp in updated_spells if not sp.get("is_cantrip") and sp.get("prepared"))
                notes.append(f"{prep_count} leveled spells prepared")

            # 6. Apply Rest Recovery (Long vs Short)
            root_slots = ch.get("spell_slots") if isinstance(ch.get("spell_slots"), dict) else {}
            nat_slots = native.get("spell_slots") if native and isinstance(native.get("spell_slots"), dict) else {}
            merged_slots = nat_slots or root_slots

            if is_long:
                hp_obj["current"] = max_hp
                vitals["temp_hp"] = 0
                ch["temp_hp"] = 0
                for s_v in merged_slots.values():
                    if isinstance(s_v, dict) and "max" in s_v:
                        s_v["current"] = s_v["max"]
                if ed == "2024":
                    hd_meter["current"] = hd_meter["max"]
                else:
                    rec_hd = max(1, int(hd_meter.get("max", 1)) // 2)
                    hd_meter["current"] = min(int(hd_meter.get("max", 1)), int(hd_meter.get("current", 0)) + rec_hd)
                for m_k, m_v in meters.items():
                    if m_k != "hit_dice" and isinstance(m_v, dict) and m_v.get("reset_on") in ("long_rest", "short_rest", "rest"):
                        m_v["current"] = m_v["max"]
                ch["conditions"] = [c for c in ch.get("conditions", []) if c == "Attuned"]
                notes.append("Full Long Rest recovery")
            else:
                is_pact = bool(native.get("is_pact_magic", False) if native else ch.get("is_pact_magic", False))
                has_long_slots = any(
                    isinstance(sv, dict) and sv.get("reset_on") == "long_rest" and int(sv.get("max") or 0) > 0
                    for sv in merged_slots.values()
                )
                for s_v in merged_slots.values():
                    if isinstance(s_v, dict):
                        if s_v.get("pact_max") is not None:
                            s_v["pact_current"] = s_v["pact_max"]
                        if s_v.get("is_pact_magic") or s_v.get("reset_on") == "short_rest" or (is_pact and not has_long_slots and s_v.get("reset_on") != "long_rest"):
                            s_v["current"] = s_v["max"]
                for m_v in meters.values():
                    if isinstance(m_v, dict) and m_v.get("reset_on") in ("short_rest", "rest"):
                        m_v["current"] = m_v["max"]
                notes.append("Short Rest pools recharged")

            # Mirror updated slots & meters to native/root
            if native is not None:
                if merged_slots:
                    native["spell_slots"] = copy.deepcopy(merged_slots)
                native["custom_meters"] = copy.deepcopy(meters)
            if merged_slots:
                ch["spell_slots"] = copy.deepcopy(merged_slots)

            summary_part = f"{name}: " + "; ".join(notes)
            reports.append(summary_part)
            char_outcomes[cid] = {
                "name": name,
                "hp_current": hp_obj["current"],
                "hp_max": max_hp,
                "armor_class": ch.get("armor_class"),
                "hit_dice_remaining": hd_meter.get("current", 0),
                "notes": notes,
            }

        self.save_active_campaign_state()
        rest_label = "Long Rest" if is_long else "Short Rest"
        return {
            "status": "success",
            "rest_type": rest_label,
            "edition": ed,
            "dice_mode": dice_mode,
            "reports": reports,
            "characters": char_outcomes,
            "summary": f"{rest_label} completed ({ed} rules): " + " | ".join(reports),
        }

    # --- Phase 3: D&D 5e (2014 & 2024) Guided Level-Up & Multiclass Engine ---

    def get_character_advancement_options(
        self,
        character_name_or_id: str,
        edition: Optional[str] = None,
        target_class: Optional[str] = None,
        bypass_prereqs: bool = False,
    ) -> Dict[str, Any]:
        """Returns complete D&D 5e advancement & multiclass preview options for an active campaign hero."""
        if not self._ensure_active_state():
            return {"status": "error", "message": "No active campaign loaded."}

        char_id, char_obj = self._find_character(character_name_or_id)
        if not char_obj:
            return {"status": "error", "message": f"Character '{character_name_or_id}' not found."}

        from core.compendiums.dnd_advancement import get_advancement_options

        ed = self._get_campaign_edition(edition)
        opts = get_advancement_options(
            char_obj,
            edition=ed,
            target_class=target_class,
            bypass_prereqs=bypass_prereqs,
        )
        opts["source"] = "campaign"
        opts["character_id"] = char_id
        opts["dice_mode"] = self.get_dice_mode()
        return opts

    def apply_dnd_level_up(
        self,
        character_name_or_id: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Applies guided D&D 5e (2014 or 2024) single-class or multiclass advancement and saves state."""
        if not self._ensure_active_state():
            return {"status": "error", "message": "No active campaign loaded."}

        char_id, char_obj = self._find_character(character_name_or_id)
        if not char_obj:
            return {"status": "error", "message": f"Character '{character_name_or_id}' not found."}

        from core.compendiums.dnd_advancement import apply_level_up

        data = dict(payload or {})
        data["edition"] = self._get_campaign_edition(data.get("edition"))
        res = apply_level_up(char_obj, data)
        if res.get("status") != "success":
            return res

        self.save_active_campaign_state()
        res["character_id"] = char_id
        res["state"] = self.active_state
        return res


# Full Caster Slot Table (Levels 1-20 -> Ranks 1-9)
FULL_CASTER_TABLE = {
    1:  [2, 0, 0, 0, 0, 0, 0, 0, 0],
    2:  [3, 0, 0, 0, 0, 0, 0, 0, 0],
    3:  [4, 2, 0, 0, 0, 0, 0, 0, 0],
    4:  [4, 3, 0, 0, 0, 0, 0, 0, 0],
    5:  [4, 3, 2, 0, 0, 0, 0, 0, 0],
    6:  [4, 3, 3, 0, 0, 0, 0, 0, 0],
    7:  [4, 3, 3, 1, 0, 0, 0, 0, 0],
    8:  [4, 3, 3, 2, 0, 0, 0, 0, 0],
    9:  [4, 3, 3, 3, 1, 0, 0, 0, 0],
    10: [4, 3, 3, 3, 2, 0, 0, 0, 0],
    11: [4, 3, 3, 3, 2, 1, 0, 0, 0],
    12: [4, 3, 3, 3, 2, 1, 0, 0, 0],
    13: [4, 3, 3, 3, 2, 1, 1, 0, 0],
    14: [4, 3, 3, 3, 2, 1, 1, 0, 0],
    15: [4, 3, 3, 3, 2, 1, 1, 1, 0],
    16: [4, 3, 3, 3, 2, 1, 1, 1, 0],
    17: [4, 3, 3, 3, 2, 1, 1, 1, 1],
    18: [4, 3, 3, 3, 3, 1, 1, 1, 1],
    19: [4, 3, 3, 3, 3, 2, 1, 1, 1],
    20: [4, 3, 3, 3, 3, 2, 2, 1, 1],
}

# Half Caster / Artificer Slot Table (Levels 1-20 -> Ranks 1-5)
HALF_CASTER_TABLE = {
    1:  [2, 0, 0, 0, 0],
    2:  [2, 0, 0, 0, 0],
    3:  [3, 0, 0, 0, 0],
    4:  [3, 0, 0, 0, 0],
    5:  [4, 2, 0, 0, 0],
    6:  [4, 2, 0, 0, 0],
    7:  [4, 3, 0, 0, 0],
    8:  [4, 3, 0, 0, 0],
    9:  [4, 3, 2, 0, 0],
    10: [4, 3, 2, 0, 0],
    11: [4, 3, 3, 0, 0],
    12: [4, 3, 3, 0, 0],
    13: [4, 3, 3, 1, 0],
    14: [4, 3, 3, 1, 0],
    15: [4, 3, 3, 2, 0],
    16: [4, 3, 3, 2, 0],
    17: [4, 3, 3, 3, 1],
    18: [4, 3, 3, 3, 1],
    19: [4, 3, 3, 3, 2],
    20: [4, 3, 3, 3, 2],
}


def calculate_dnd_proficiency(level: int) -> int:
    return 2 + ((level - 1) // 4)


def level_up_dnd_character(char_data: Dict[str, Any], target_level: int) -> Dict[str, Any]:
    """
    Scales a D&D 5e character to target_level:
    - Bumps Hit Dice and Max HP (using fixed rule average + CON mod)
    - Recomputes Proficiency Bonus (+2 to +6)
    - Updates 1st through 9th level spell slots
    - Updates Ability Save DC and Spell Attack Bonus
    - Updates max prepared leveled spells allowance
    """
    native = char_data.setdefault("native_system_data", {})
    core = char_data.setdefault("universal_core", {})

    old_level = char_data.get("level", 1)
    if target_level <= old_level:
        return {"status": "noop", "message": f"Character already level {old_level}."}

    # Extract Constitution Modifier
    scores = native.get("ability_scores", {})
    con_val = scores.get("CON", 10)
    con_mod = (con_val - 10) // 2

    # Extract Spellcasting Ability Modifier
    spell_ability = native.get("spellcasting_ability", "INT")
    key_val = scores.get(spell_ability, 10)
    key_mod = (key_val - 10) // 2

    # 1. Hit Dice & Hit Points Scaling
    class_str = native.get("class_level", core.get("archetype", "Adventurer 1"))
    hit_die = 8
    if any(k in class_str.lower() for k in ["sorcerer", "wizard"]):
        hit_die = 6
    elif any(k in class_str.lower() for k in ["fighter", "paladin", "ranger"]):
        hit_die = 10
    elif "barbarian" in class_str.lower():
        hit_die = 12

    avg_hp_gain = (hit_die // 2) + 1 + con_mod
    levels_gained = target_level - old_level
    hp_entry = char_data.setdefault("vitals", {}).setdefault("health", {"current": 10, "max": 10})
    hp_entry["max"] += (avg_hp_gain * levels_gained)
    hp_entry["current"] += (avg_hp_gain * levels_gained)

    # 2. Update Level & Proficiency
    char_data["level"] = target_level
    core["level"] = target_level
    clean_base_class = re.sub(r'\d+', '', class_str).strip()
    updated_class_str = f"{clean_base_class} {target_level}"
    native["class_level"] = updated_class_str
    core["archetype"] = updated_class_str

    new_prof = calculate_dnd_proficiency(target_level)
    native["proficiency_bonus"] = new_prof
    native["ability_save_dc"] = 8 + new_prof + key_mod
    native["spell_attack_bonus"] = new_prof + key_mod

    # 3. Update Hit Dice Meter
    meters = char_data.setdefault("custom_meters", {})
    meters["hit_dice"] = {
        "label": f"Hit Dice ({target_level}d{hit_die})",
        "current": target_level,
        "max": target_level,
        "reset_on": "long_rest"
    }

    # 4. Spell Slots Matrix Scaling
    is_full = any(c in class_str.lower() for c in ["sorcerer", "wizard", "cleric", "druid", "bard"])
    is_half = any(c in class_str.lower() for c in ["artificer", "paladin", "ranger"])

    spell_slots = native.setdefault("spell_slots", {})

    if is_full:
        slot_array = FULL_CASTER_TABLE.get(min(20, target_level), [0]*9)
        for rank in range(1, 10):
            max_s = slot_array[rank - 1]
            spell_slots[f"level_{rank}"] = {"current": max_s, "max": max_s}

        # Prepared cap for Full Casters
        if "sorcerer" in class_str.lower():
            sorc_caps = {1: 2, 2: 4, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8, 8: 9, 9: 10, 10: 11,
                         11: 12, 12: 12, 13: 13, 14: 13, 15: 14, 16: 14, 17: 15, 18: 15, 19: 15, 20: 15}
            native["max_prepared_leveled_spells"] = sorc_caps.get(target_level, target_level + 1)
            # Sorcery Points scale 1:1 with level starting at level 2
            meters["sorcery_points"] = {
                "label": "Sorcery Points",
                "current": target_level,
                "max": target_level,
                "reset_on": "long_rest"
            }
        else:
            native["max_prepared_leveled_spells"] = max(1, key_mod + target_level)

    elif is_half:
        slot_array = HALF_CASTER_TABLE.get(min(20, target_level), [0]*5)
        for rank in range(1, 6):
            max_s = slot_array[rank - 1]
            spell_slots[f"level_{rank}"] = {"current": max_s, "max": max_s}
        for rank in range(6, 10):
            spell_slots[f"level_{rank}"] = {"current": 0, "max": 0}

        native["max_prepared_leveled_spells"] = max(1, key_mod + (target_level // 2))

    # Synchronize top-level fields if char_data tracks them
    char_data["spell_slots"] = spell_slots
    char_data["max_prepared_leveled_spells"] = native.get("max_prepared_leveled_spells", 0)

    return {
        "status": "success",
        "character": char_data.get("name"),
        "new_level": target_level,
        "max_hp": hp_entry["max"],
        "proficiency_bonus": new_prof,
        "save_dc": native["ability_save_dc"],
        "max_prepared_spells": native.get("max_prepared_leveled_spells", 0),
        "spell_slots": spell_slots
    }
