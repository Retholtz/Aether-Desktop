"""
core/scene/corpse_manager.py - Physical Corpse Tracking and Spell Expiry
"""
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from core.logger import get_logger

logger = get_logger("CorpseManager")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CorpseContainer(BaseModel):
    corpse_id: str = Field(default_factory=lambda: f"corpse_{uuid.uuid4().hex[:8]}")
    original_entity_id: str
    name: str
    location_id: str
    inventory: List[Dict[str, Any]] = Field(default_factory=list)
    time_of_death_round: int = 1
    time_of_death_timestamp: str = Field(default_factory=_utc_now_iso)
    revivify_rounds_left: int = 10
    raise_dead_days_left: float = 10.0
    resurrection_years_left: float = 100.0
    gentle_repose_active: bool = False
    gentle_repose_days_left: float = 0.0

    @property
    def revivify_rounds_remaining(self) -> int:
        return self.revivify_rounds_left

    @revivify_rounds_remaining.setter
    def revivify_rounds_remaining(self, val: int) -> None:
        self.revivify_rounds_left = val

    @property
    def raise_dead_days_remaining(self) -> float:
        return self.raise_dead_days_left

    @raise_dead_days_remaining.setter
    def raise_dead_days_remaining(self, val: float) -> None:
        self.raise_dead_days_left = val

    @property
    def resurrection_years_remaining(self) -> float:
        return self.resurrection_years_left

    @resurrection_years_remaining.setter
    def resurrection_years_remaining(self, val: float) -> None:
        self.resurrection_years_left = val

    @property
    def gentle_repose_days_remaining(self) -> float:
        return self.gentle_repose_days_left

    @gentle_repose_days_remaining.setter
    def gentle_repose_days_remaining(self, val: float) -> None:
        self.gentle_repose_days_left = val


class CorpseManager:
    def __init__(self):
        self.corpses: Dict[str, CorpseContainer] = {}

    def spawn_corpse(
        self,
        entity_id: str,
        entity_name: str,
        location_id: str,
        inventory: List[Dict[str, Any]],
        current_round: int
    ) -> CorpseContainer:
        """Spawns a physical CorpseContainer with copied/transferred inventory."""
        corpse = CorpseContainer(
            original_entity_id=entity_id,
            name=f"Corpse of {entity_name}" if not entity_name.startswith("Corpse of ") else entity_name,
            location_id=location_id,
            inventory=inventory,
            time_of_death_round=current_round
        )
        self.corpses[corpse.corpse_id] = corpse
        logger.info("Corpse created: %s at location %s", corpse.name, location_id)
        return corpse

    def spawn_corpse_from_entity(
        self,
        entity: Dict[str, Any],
        current_round: int,
        location_id: str,
        scene_manager: Optional[Any] = None
    ) -> CorpseContainer:
        """Transfers equipped and carried inventory into the corpse container."""
        entity_id = entity.get("id", entity.get("entity_id", "unknown"))
        entity_name = entity.get("name", "Unknown Entity")

        # Collect inventory from all possible entity sources
        raw_inv = []
        if isinstance(entity.get("inventory"), list):
            raw_inv.extend(entity["inventory"])
        elif isinstance(entity.get("universal_core", {}).get("tagged_inventory"), list):
            raw_inv.extend(entity["universal_core"]["tagged_inventory"])

        if isinstance(entity.get("weapons"), list):
            for w in entity["weapons"]:
                if w not in raw_inv:
                    raw_inv.append(w)

        corpse = self.spawn_corpse(
            entity_id=entity_id,
            entity_name=entity_name,
            location_id=location_id,
            inventory=raw_inv,
            current_round=current_round
        )

        # Clear original inventory from entity if mutable
        if "inventory" in entity and isinstance(entity["inventory"], list):
            entity["inventory"] = []

        # Register to SceneManager if provided
        if scene_manager and hasattr(scene_manager, "active_containers"):
            scene_manager.active_containers.setdefault(location_id, []).append(corpse.model_dump())

        return corpse

    def apply_gentle_repose(self, corpse_id: str) -> bool:
        """Applies Gentle Repose to a corpse, freezing its decomposition timers for 10 days."""
        if corpse_id not in self.corpses:
            return False
        corpse = self.corpses[corpse_id]
        corpse.gentle_repose_active = True
        corpse.gentle_repose_days_left = 10.0
        logger.info("Gentle Repose applied to %s. Decay paused for 10 days.", corpse.name)
        return True

    def tick_round(self, location_id: Optional[str] = None):
        """Ticks 1 round (6 seconds) for revivify windows."""
        for corpse in self.corpses.values():
            if location_id and corpse.location_id != location_id:
                continue
            if corpse.gentle_repose_active:
                continue
            if corpse.revivify_rounds_left > 0:
                corpse.revivify_rounds_left -= 1

    def tick_days(self, days: float):
        """Ticks calendar days for Raise Dead and Gentle Repose expiration."""
        for corpse in self.corpses.values():
            if corpse.gentle_repose_active:
                corpse.gentle_repose_days_left -= days
                if corpse.gentle_repose_days_left <= 0:
                    corpse.gentle_repose_active = False
                    corpse.gentle_repose_days_left = 0.0
                continue
            corpse.raise_dead_days_left = max(0.0, corpse.raise_dead_days_left - days)

    def tick_corpse_timers(
        self,
        rounds_passed: int = 1,
        days_passed: float = 0.0,
        location_id: Optional[str] = None
    ):
        """Unified timer tick for both round-scale combat and day-scale travel."""
        for _ in range(max(0, rounds_passed)):
            self.tick_round(location_id=location_id)
        if days_passed > 0:
            self.tick_days(days_passed)
