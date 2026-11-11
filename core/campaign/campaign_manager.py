"""
core/campaign/campaign_manager.py - Persistent Campaign Ledger, World State & Unified Store
"""
import json
import os
from dataclasses import asdict, is_dataclass, fields as dc_fields
from typing import Dict, Any, List, Optional, Tuple, Union
from core.campaign.schema import (
    CampaignManifest, LocationNode, LocationType,
    FactionRecord, FactionDisposition, QuestRecord,
    QuestStatus, QuestObjective, ClueRecord
)
from core.logger import get_logger

logger = get_logger("CampaignManager")


def _serialize_manifest(manifest: CampaignManifest) -> Dict[str, Any]:
    """Helper to serialize CampaignManifest into plain JSON-safe primitives."""
    def _to_plain(obj: Any) -> Any:
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        if is_dataclass(obj):
            return {f.name: _to_plain(getattr(obj, f.name)) for f in dc_fields(obj)}
        if isinstance(obj, dict):
            return {str(k): _to_plain(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_to_plain(v) for v in obj]
        if hasattr(obj, "value"):
            return obj.value
        return obj

    return _to_plain(manifest)


class QuestUpdateResult(tuple):
    """
    Tuple (success, msg) supporting tuple unpacking and dict-like subscripting.
    Supports:
        success, msg = cm.update_quest_status(...)
        res["new_status"], res["quest_title"]
    """
    def __new__(cls, success: bool, msg: str, quest_title: str = "", new_status: str = ""):
        return super().__new__(cls, (success, msg))

    def __init__(self, success: bool, msg: str, quest_title: str = "", new_status: str = ""):
        self.success = success
        self.msg = msg
        self.quest_title = quest_title
        self.new_status = new_status

    def __getitem__(self, item: Union[int, str]):
        if isinstance(item, str):
            if item == "success":
                return self.success
            if item in ("msg", "message"):
                return self.msg
            if item == "quest_title":
                return self.quest_title
            if item == "new_status":
                return self.new_status
            raise KeyError(f"Unknown key '{item}' in QuestUpdateResult")
        return super().__getitem__(item)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


class CampaignStoreFacade:
    """
    Unified persistence manager ensuring synchronization between world context
    (nodes, factions, quests, clues) and session state (active characters,
    discovered clues, journal, stored items).
    Manages data/campaigns/{id}/campaign_state.json alongside legacy campaign.json and state.json.
    """
    def __init__(self, campaign_id: str = "default_campaign", storage_root: str = "data/campaigns"):
        self.campaign_id = campaign_id
        self.storage_root = storage_root
        self.storage_dir = os.path.join(storage_root, campaign_id)
        self.unified_file = os.path.join(self.storage_dir, "campaign_state.json")
        self.manifest_file = os.path.join(self.storage_dir, "campaign.json")
        self.state_file = os.path.join(self.storage_dir, "state.json")
        self.manifest = CampaignManifest(id=campaign_id, title="Default Campaign")
        self.state: Dict[str, Any] = {
            "campaign_id": campaign_id,
            "characters": {},
            "stored_items": [],
            "discovered_clues": [],
            "journal": [],
            "active_quest": "",
            "current_location": "",
            "quests": {},
            "completed_quests": []
        }
        self.load()

    def serialize_manifest(self) -> Dict[str, Any]:
        """Returns a fully JSON-serializable dictionary representation of the campaign manifest."""
        return _serialize_manifest(self.manifest)

    def load(self) -> None:
        """Loads campaign and session state from unified JSON file or legacy files, avoiding stale overrides."""
        data: Dict[str, Any] = {}
        state_data: Dict[str, Any] = {}
        unified_mtime = 0.0

        if os.path.exists(self.unified_file):
            try:
                unified_mtime = os.path.getmtime(self.unified_file)
                with open(self.unified_file, "r", encoding="utf-8") as f:
                    unified = json.load(f)
                if isinstance(unified, dict):
                    data = unified.get("manifest") if "manifest" in unified and isinstance(unified["manifest"], dict) else unified
                    state_data = unified.get("state") if "state" in unified and isinstance(unified["state"], dict) else unified
            except Exception:
                pass

        if os.path.exists(self.manifest_file):
            try:
                man_mtime = os.path.getmtime(self.manifest_file)
                if not data or not os.path.exists(self.unified_file) or man_mtime > unified_mtime:
                    with open(self.manifest_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
            except Exception:
                pass

        if os.path.exists(self.state_file):
            try:
                st_mtime = os.path.getmtime(self.state_file)
                with open(self.state_file, "r", encoding="utf-8") as f:
                    disk_state = json.load(f)
                if isinstance(disk_state, dict):
                    if not state_data or not os.path.exists(self.unified_file) or st_mtime > unified_mtime:
                        prior_clues = state_data.get("discovered_clues", []) if isinstance(state_data, dict) else []
                        state_data = disk_state
                        if prior_clues:
                            merged_clues = state_data.setdefault("discovered_clues", [])
                            for c_txt in prior_clues:
                                if c_txt and c_txt not in merged_clues:
                                    merged_clues.append(c_txt)
                    else:
                        # Safeguard TTRPGService-owned collections against same-second stale unified state
                        for rich_key in ("characters", "documents", "journal"):
                            if disk_state.get(rich_key) and not state_data.get(rich_key):
                                state_data[rich_key] = disk_state[rich_key]
            except Exception:
                pass

        # Rehydrate manifest dataclasses and Pydantic models
        if data:
            factions = {}
            for k, v in data.get("factions", {}).items():
                if isinstance(v, FactionRecord):
                    factions[k] = v
                elif isinstance(v, dict):
                    f_data = dict(v)
                    if "disposition" in f_data and isinstance(f_data["disposition"], str):
                        try:
                            f_data["disposition"] = FactionDisposition(f_data["disposition"])
                        except Exception:
                            pass
                    factions[k] = FactionRecord(**f_data)

            locations = {}
            for k, v in data.get("locations", {}).items():
                if isinstance(v, LocationNode):
                    locations[k] = v
                elif isinstance(v, dict):
                    l_data = dict(v)
                    if "type" in l_data and isinstance(l_data["type"], str):
                        try:
                            l_data["type"] = LocationType(l_data["type"])
                        except Exception:
                            pass
                    locations[k] = LocationNode(**l_data)

            quests = {}
            for q_id, q_data in data.get("quests", {}).items():
                if isinstance(q_data, QuestRecord):
                    quests[q_id] = q_data
                elif isinstance(q_data, dict):
                    q_dict = dict(q_data)
                    if "status" in q_dict and isinstance(q_dict["status"], str):
                        try:
                            q_dict["status"] = QuestStatus(q_dict["status"])
                        except Exception:
                            pass
                    raw_objs = q_dict.pop("objectives", [])
                    objs = [o if isinstance(o, QuestObjective) else QuestObjective(**o) for o in raw_objs]
                    quests[q_id] = QuestRecord(objectives=objs, **q_dict)

            clues = {}
            for k, v in data.get("clues", {}).items():
                if isinstance(v, ClueRecord):
                    clues[k] = v
                elif isinstance(v, dict):
                    clues[k] = ClueRecord(**v)

            self.manifest = CampaignManifest(
                id=data.get("id", self.campaign_id),
                title=data.get("title", "Untitled Campaign"),
                ruleset=data.get("ruleset", "dnd_5e"),
                genre=data.get("genre", "heroic_fantasy"),
                tone_sliders=data.get("tone_sliders") if data.get("tone_sliders") is not None else {
                    "grittiness": 5, "magic_tech_level": 7, "political_intrigue": 5
                },
                premise=data.get("premise", ""),
                difficulty=data.get("difficulty", "helpful"),
                active_location_id=data.get("active_location_id"),
                factions=factions,
                locations=locations,
                quests=quests,
                clues=clues,
                world_rules=data.get("world_rules", {}),
                module_adherence=data.get("module_adherence", "strict")
            )

        # Merge session state
        if state_data:
            self.state.update(state_data)

        # Ensure synchronization between manifest clues and state discovered_clues
        discovered = self.state.setdefault("discovered_clues", [])
        for c in self.manifest.clues.values():
            c_name = c.title or c.content
            if c_name and c_name not in discovered:
                discovered.append(c_name)

        # Ensure quests are tracked in session state
        st_quests = self.state.setdefault("quests", {})
        for q_id, q in self.manifest.quests.items():
            st_quests[q_id] = {
                "title": q.title,
                "status": q.status.value if hasattr(q.status, "value") else str(q.status)
            }

    def save(self) -> None:
        """Flushes campaign state to unified JSON file and syncs legacy stores."""
        os.makedirs(self.storage_dir, exist_ok=True)
        manifest_dict = _serialize_manifest(self.manifest)
        state_dict = dict(self.state)

        # Build unified representation
        unified_dict = dict(manifest_dict)
        for k, v in state_dict.items():
            if k not in ("id", "title", "ruleset", "genre") or k not in unified_dict:
                unified_dict[k] = v
        unified_dict["manifest"] = manifest_dict
        unified_dict["state"] = state_dict

        # 1. Save unified campaign_state.json
        with open(self.unified_file, "w", encoding="utf-8") as f:
            json.dump(unified_dict, f, indent=2)

        # 2. Save legacy campaign.json (world model)
        with open(self.manifest_file, "w", encoding="utf-8") as f:
            json.dump(manifest_dict, f, indent=2)

        # 3. Save legacy state.json (session state)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(state_dict, f, indent=2)

    def register_clue(self, clue: ClueRecord) -> None:
        """Registers a clue in the world model and synchronously synchronizes it with session state discovered_clues."""
        self.manifest.clues[clue.id] = clue
        clue_text = clue.title or clue.content
        if clue_text:
            clues_list = self.state.setdefault("discovered_clues", [])
            if clue_text not in clues_list:
                clues_list.append(clue_text)
            if clue.content and clue.content != clue_text and clue.content not in clues_list:
                clues_list.append(clue.content)
        self.save()

    def update_quest_status(self, quest_id: str, new_status: Union[QuestStatus, str], force: bool = False) -> QuestUpdateResult:
        """
        Updates quest status with strict prerequisite dependency validation.
        Synchronously mirrors world context and session state.
        """
        if quest_id not in self.manifest.quests:
            return QuestUpdateResult(False, f"Quest '{quest_id}' not found.")

        quest = self.manifest.quests[quest_id]

        if isinstance(new_status, str):
            try:
                status_enum = QuestStatus(new_status.lower().strip())
            except ValueError:
                status_enum = new_status
        else:
            status_enum = new_status

        # Enforce prerequisite gating when activating a quest
        is_active_target = (status_enum == QuestStatus.ACTIVE) or (str(status_enum).lower() in ("active", "queststatus.active"))
        if is_active_target and not force:
            unmet_prereqs = []
            for prereq_id in quest.prerequisite_quest_ids:
                prereq = self.manifest.quests.get(prereq_id)
                prereq_status = prereq.status if prereq else None
                prereq_is_completed = (prereq_status == QuestStatus.COMPLETED) or (str(prereq_status).lower() in ("completed", "queststatus.completed"))
                if not prereq or not prereq_is_completed:
                    unmet_prereqs.append(prereq.title if prereq else prereq_id)

            if unmet_prereqs:
                msg = f"Cannot activate quest '{quest.title}'. Unfulfilled prerequisites: {', '.join(unmet_prereqs)}."
                logger.warning(msg)
                return QuestUpdateResult(False, msg, quest_title=quest.title, new_status=str(quest.status))

        old_status = quest.status
        quest.status = status_enum

        # Synchronously mirror to state
        self.state.setdefault("quests", {})[quest_id] = {
            "title": quest.title,
            "status": quest.status.value if hasattr(quest.status, "value") else str(quest.status)
        }

        if status_enum in (QuestStatus.COMPLETED, "completed", "COMPLETED"):
            for obj in quest.objectives:
                if obj.is_mandatory:
                    obj.is_completed = True
            completed_list = self.state.setdefault("completed_quests", [])
            if quest.title not in completed_list:
                completed_list.append(quest.title)
            if self.state.get("active_quest") in (quest.title, quest_id):
                self.state["active_quest"] = ""
        elif is_active_target:
            self.state["active_quest"] = quest.title
        elif status_enum in (QuestStatus.FAILED, QuestStatus.UNASSIGNED, "failed", "unassigned"):
            if self.state.get("active_quest") in (quest.title, quest_id):
                self.state["active_quest"] = ""

        try:
            self.save()
        except Exception:
            pass
        logger.info("Quest '%s' status changed from %s to %s", quest.title, old_status, status_enum)
        status_val = quest.status.value if hasattr(quest.status, "value") else str(quest.status)
        return QuestUpdateResult(True, f"Quest '{quest.title}' transitioned to {status_val}.", quest_title=quest.title, new_status=status_val)


class CampaignManager(CampaignStoreFacade):
    """Full campaign manager with narrative routing, spatial navigation, and GM context assembly."""

    # --- Geographic Navigation ---

    def add_location(self, node: LocationNode) -> None:
        self.manifest.locations[node.id] = node
        if not self.manifest.active_location_id:
            self.manifest.active_location_id = node.id
        self.save()

    def set_active_location(self, location_id: str) -> Dict[str, Any]:
        """Moves party to a new location node, auto-discovering it."""
        if location_id not in self.manifest.locations:
            raise ValueError(f"Location ID '{location_id}' does not exist.")
        
        loc = self.manifest.locations[location_id]
        loc.is_discovered = True
        self.manifest.active_location_id = location_id
        self.state["current_location"] = loc.name
        self.save()

        return {
            "active_location": loc.name,
            "type": loc.type.value if hasattr(loc.type, "value") else str(loc.type),
            "description": loc.description,
            "hazards": loc.hazards,
            "connections": [
                self.manifest.locations[cid].name 
                for cid in loc.connected_location_ids 
                if cid in self.manifest.locations
            ]
        }

    # --- Information & Clue Tracking ---

    def reveal_clue(self, clue_id: str) -> Dict[str, Any]:
        """Reveals a secret clue to the party, marking it for GM context injection and session state."""
        if clue_id not in self.manifest.clues:
            raise ValueError(f"Clue ID '{clue_id}' does not exist.")
        clue = self.manifest.clues[clue_id]
        clue.is_revealed = True
        clue_text = clue.title or clue.content
        if clue_text:
            clues_list = self.state.setdefault("discovered_clues", [])
            if clue_text not in clues_list:
                clues_list.append(clue_text)
        self.save()
        return {"revealed_clue": clue.title, "content": clue.content}

    # --- Quest Ledger ---

    def add_quest(self, quest: QuestRecord) -> None:
        self.manifest.quests[quest.id] = quest
        self.state.setdefault("quests", {})[quest.id] = {
            "title": quest.title,
            "status": quest.status.value if hasattr(quest.status, "value") else str(quest.status)
        }
        if quest.status == QuestStatus.ACTIVE and not self.state.get("active_quest"):
            self.state["active_quest"] = quest.title
        self.save()

    def complete_objective(self, quest_id: str, objective_id: str) -> Dict[str, Any]:
        if quest_id not in self.manifest.quests:
            raise ValueError(f"Quest ID '{quest_id}' does not exist.")
        quest = self.manifest.quests[quest_id]
        target_obj = next((o for o in quest.objectives if o.id == objective_id), None)
        if not target_obj:
            raise ValueError(f"Objective '{objective_id}' not found in quest '{quest_id}'.")
        target_obj.is_completed = True
        
        # Auto-complete quest if all mandatory objectives are fulfilled
        all_mandatory_done = all(o.is_completed for o in quest.objectives if o.is_mandatory)
        if all_mandatory_done:
            quest.status = QuestStatus.COMPLETED
            completed_list = self.state.setdefault("completed_quests", [])
            if quest.title not in completed_list:
                completed_list.append(quest.title)
            if self.state.get("active_quest") in (quest.title, quest_id):
                self.state["active_quest"] = ""

        self.state.setdefault("quests", {})[quest_id] = {
            "title": quest.title,
            "status": quest.status.value if hasattr(quest.status, "value") else str(quest.status)
        }

        self.save()
        return {
            "quest": quest.title,
            "objective_completed": target_obj.description,
            "quest_status": quest.status.value if hasattr(quest.status, "value") else str(quest.status)
        }

    # --- Faction Matrix ---

    def adjust_faction_reputation(self, faction_id: str, delta_influence: int, new_disposition: Optional[FactionDisposition] = None) -> Dict[str, Any]:
        if faction_id not in self.manifest.factions:
            raise ValueError(f"Faction ID '{faction_id}' does not exist.")
        faction = self.manifest.factions[faction_id]
        faction.influence_score = max(0, min(100, faction.influence_score + delta_influence))
        if new_disposition:
            faction.disposition = new_disposition
        self.save()
        return {
            "faction": faction.name,
            "influence": faction.influence_score,
            "disposition": faction.disposition.value if hasattr(faction.disposition, "value") else str(faction.disposition)
        }

    # --- GM Context Prompt Assembler ---

    def generate_prompt_context(self) -> str:
        """
        Builds a compact context block for injection into Aether's LLM prompt.
        Only leaks revealed clues and active/known quests. Hidden secrets stay partitioned.
        """
        lines = [
            f"### ACTIVE CAMPAIGN: {self.manifest.title} ({self.manifest.ruleset.upper()})",
            f"Premise: {self.manifest.premise}",
            f"Tone: Grittiness={self.manifest.tone_sliders.get('grittiness', 5)}/10, "
            f"Magic/Tech={self.manifest.tone_sliders.get('magic_tech_level', 5)}/10\n"
        ]

        # Location details
        if self.manifest.active_location_id and self.manifest.active_location_id in self.manifest.locations:
            loc = self.manifest.locations[self.manifest.active_location_id]
            ltype_val = loc.type.value if hasattr(loc.type, "value") else str(loc.type)
            lines.append(f"**CURRENT LOCATION: {loc.name} ({ltype_val.upper()})**")
            lines.append(f"Description: {loc.description}")
            if loc.hazards:
                lines.append(f"Active Environmental Hazards: {', '.join(loc.hazards)}")
            lines.append("")

        # Active Quests
        active_quests = [q for q in self.manifest.quests.values() if q.status == QuestStatus.ACTIVE]
        if active_quests:
            lines.append("**ACTIVE QUEST OBJECTIVES:**")
            for q in active_quests:
                lines.append(f"- {q.title} (Given by: {q.giver_npc})")
                for o in q.objectives:
                    check = "[X]" if o.is_completed else "[ ]"
                    lines.append(f"  {check} {o.description}")
            lines.append("")

        # Discovered Clues
        revealed_clues = [c for c in self.manifest.clues.values() if c.is_revealed]
        if revealed_clues:
            lines.append("**DISCOVERED LORE & CLUES:**")
            for c in revealed_clues:
                lines.append(f"- *{c.title}*: {c.content}")
            lines.append("")

        # Relevant Factions
        if self.manifest.factions:
            lines.append("**REGIONAL FACTIONS:**")
            for f in self.manifest.factions.values():
                disp_val = f.disposition.value if hasattr(f.disposition, "value") else str(f.disposition)
                lines.append(f"- {f.name}: Disposition={disp_val.upper()} (Reputation: {f.public_reputation})")
            lines.append("")

        return "\n".join(lines).strip()
