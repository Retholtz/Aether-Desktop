"""
core/scene/scene_manager.py - Scene State & Ephemeral Adversary Management
"""
import json
import os
import uuid
from typing import Dict, Any, List, Optional


class SceneManager:
    def __init__(self, campaign_id: str, base_data_dir: str = "data/campaigns"):
        self.campaign_id = campaign_id
        self.scene_file = os.path.join(base_data_dir, campaign_id, "scene_state.json")
        self.state: Dict[str, Any] = {
            "scene_title": "Wilderness Encampment",
            "location_path": "Faerun/Sword Coast/Wilds",
            "environment_hazards": [],
            "party_roster": [],
            "adversaries": []
        }
        self.load_state()

    def load_state(self) -> None:
        if os.path.exists(self.scene_file):
            try:
                with open(self.scene_file, "r", encoding="utf-8") as f:
                    self.state = json.load(f)
            except Exception:
                pass

    def save_state(self) -> None:
        os.makedirs(os.path.dirname(self.scene_file), exist_ok=True)
        with open(self.scene_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def set_party(self, characters: List[Dict[str, Any]]) -> None:
        """Sets active player character entities."""
        self.state["party_roster"] = characters
        self.save_state()

    def spawn_adversary(
        self,
        name: str,
        hp: int,
        ac: int,
        attack_bonus: str = "+4",
        damage: str = "1d6+2",
        dex_score: int = 12
    ) -> Dict[str, Any]:
        """Spawns an ephemeral adversary into the scene without modifying player vault records."""
        adv_id = f"adv_{uuid.uuid4().hex[:8]}"
        adversary = {
            "id": adv_id,
            "name": name,
            "is_adversary": True,
            "vitals": {
                "health": {"current": hp, "max": hp, "label": "Health"},
                "guard": {"current": ac, "max": ac, "label": "Armor Defense"}
            },
            "native_system_data": {
                "armor_class": ac,
                "initiative_bonus": (dex_score - 10) // 2,
                "ability_scores": {"Dexterity": dex_score, "Constitution": 10},
                "hit_points": {"current": hp, "max": hp, "temp": 0},
                "weapons": [
                    {"name": "Standard Attack", "atk_bonus": attack_bonus, "damage": damage}
                ]
            }
        }
        self.state["adversaries"].append(adversary)
        self.save_state()
        return adversary

    def clear_adversaries(self) -> None:
        """Purges defeated adversaries upon encounter resolution."""
        self.state["adversaries"].clear()
        self.save_state()

    def get_all_participants(self) -> List[Dict[str, Any]]:
        """Returns unified participant list for CombatManager."""
        return self.state["party_roster"] + self.state["adversaries"]
