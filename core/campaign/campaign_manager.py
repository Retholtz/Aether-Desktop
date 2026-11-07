"""
core/campaign/campaign_manager.py - Persistent Campaign Ledger & Narrative Anchors
"""
import json
import os
from dataclasses import asdict
from typing import Dict, Any, List, Optional
from core.campaign.schema import (
    CampaignManifest, LocationNode, LocationType,
    FactionRecord, FactionDisposition, QuestRecord,
    QuestStatus, QuestObjective, ClueRecord
)


class CampaignManager:
    def __init__(self, campaign_id: str, storage_root: str = "data/campaigns"):
        self.campaign_id = campaign_id
        self.storage_dir = os.path.join(storage_root, campaign_id)
        self.manifest_file = os.path.join(self.storage_dir, "campaign.json")
        self.manifest = CampaignManifest(id=campaign_id, title="Default Campaign")
        self.load()

    def load(self) -> None:
        """Loads campaign state from JSON file or initializes defaults."""
        if os.path.exists(self.manifest_file):
            try:
                with open(self.manifest_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                # Rehydrate dataclasses
                factions = {}
                for k, v in data.get("factions", {}).items():
                    f_data = dict(v)
                    if "disposition" in f_data and isinstance(f_data["disposition"], str):
                        f_data["disposition"] = FactionDisposition(f_data["disposition"])
                    factions[k] = FactionRecord(**f_data)

                locations = {}
                for k, v in data.get("locations", {}).items():
                    l_data = dict(v)
                    if "type" in l_data and isinstance(l_data["type"], str):
                        l_data["type"] = LocationType(l_data["type"])
                    locations[k] = LocationNode(**l_data)
                
                quests = {}
                for q_id, q_data in data.get("quests", {}).items():
                    q_dict = dict(q_data)
                    if "status" in q_dict and isinstance(q_dict["status"], str):
                        q_dict["status"] = QuestStatus(q_dict["status"])
                    objs = [QuestObjective(**o) for o in q_dict.pop("objectives", [])]
                    quests[q_id] = QuestRecord(objectives=objs, **q_dict)

                clues = {k: ClueRecord(**v) for k, v in data.get("clues", {}).items()}

                self.manifest = CampaignManifest(
                    id=data.get("id", self.campaign_id),
                    title=data.get("title", "Untitled Campaign"),
                    ruleset=data.get("ruleset", "dnd_5e"),
                    genre=data.get("genre", "heroic_fantasy"),
                    tone_sliders=data.get("tone_sliders") if data.get("tone_sliders") is not None else {
                        "grittiness": 5, "magic_tech_level": 7, "political_intrigue": 5
                    },
                    premise=data.get("premise", ""),
                    active_location_id=data.get("active_location_id"),
                    factions=factions,
                    locations=locations,
                    quests=quests,
                    clues=clues,
                    world_rules=data.get("world_rules", {})
                )
            except Exception:
                pass

    def save(self) -> None:
        """Flushes campaign state to disk."""
        os.makedirs(self.storage_dir, exist_ok=True)
        raw_dict = asdict(self.manifest)
        with open(self.manifest_file, "w", encoding="utf-8") as f:
            json.dump(raw_dict, f, indent=2)

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
        self.save()

        return {
            "active_location": loc.name,
            "type": loc.type.value,
            "description": loc.description,
            "hazards": loc.hazards,
            "connections": [
                self.manifest.locations[cid].name 
                for cid in loc.connected_location_ids 
                if cid in self.manifest.locations
            ]
        }

    # --- Information & Clue Tracking ---

    def register_clue(self, clue: ClueRecord) -> None:
        self.manifest.clues[clue.id] = clue
        self.save()

    def reveal_clue(self, clue_id: str) -> Dict[str, Any]:
        """Reveals a secret clue to the party, marking it for GM context injection."""
        if clue_id not in self.manifest.clues:
            raise ValueError(f"Clue ID '{clue_id}' does not exist.")
        clue = self.manifest.clues[clue_id]
        clue.is_revealed = True
        self.save()
        return {"revealed_clue": clue.title, "content": clue.content}

    # --- Quest Ledger ---

    def add_quest(self, quest: QuestRecord) -> None:
        self.manifest.quests[quest.id] = quest
        self.save()

    def update_quest_status(self, quest_id: str, new_status: QuestStatus) -> Dict[str, Any]:
        if quest_id not in self.manifest.quests:
            raise ValueError(f"Quest ID '{quest_id}' does not exist.")
        quest = self.manifest.quests[quest_id]
        quest.status = new_status
        self.save()
        return {"quest_title": quest.title, "new_status": quest.status.value}

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

        self.save()
        return {
            "quest": quest.title,
            "objective_completed": target_obj.description,
            "quest_status": quest.status.value
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
            "disposition": faction.disposition.value
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
            lines.append(f"**CURRENT LOCATION: {loc.name} ({loc.type.value.upper()})**")
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
                lines.append(f"- {f.name}: Disposition={f.disposition.value.upper()} (Reputation: {f.public_reputation})")
            lines.append("")

        return "\n".join(lines).strip()
