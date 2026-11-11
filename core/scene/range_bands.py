"""
core/scene/range_bands.py - Abstract Tactical Spatial Range Bands
"""
from enum import Enum
from typing import Dict, Any, Optional, List
from core.logger import get_logger

logger = get_logger("RangeBands")


class RangeBand(str, Enum):
    ENGAGED = "engaged"  # Melee (5 ft.)
    NEAR = "near"        # Close Range (30 ft.)
    FAR = "far"          # Ranged (60–120 ft.)


class RangeBandTracker:
    def __init__(self):
        self.entity_positions: Dict[str, RangeBand] = {}

    def set_position(self, entity_id: str, band: RangeBand) -> None:
        self.entity_positions[entity_id] = band

    def get_position(self, entity_id: str) -> RangeBand:
        return self.entity_positions.get(entity_id, RangeBand.NEAR)

    def shift_band(
        self,
        entity_id: str,
        target_band: RangeBand,
        has_disengaged: bool = False,
        hostiles_in_melee: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Shifts an entity between spatial bands.
        Moving from ENGAGED to NEAR (or FAR) without Disengage triggers an opportunity attack event.
        """
        current_band = self.get_position(entity_id)
        self.set_position(entity_id, target_band)

        triggered_events = []
        if current_band == RangeBand.ENGAGED and target_band != RangeBand.ENGAGED and not has_disengaged:
            hostiles = hostiles_in_melee or []
            triggered_events.append({
                "event": "opportunity_attack_opportunity",
                "entity_id": entity_id,
                "from_band": current_band.value,
                "to_band": target_band.value,
                "hostiles": [h.get("id", h.get("name")) for h in hostiles]
            })
            logger.info("Entity %s provoked opportunity attack shifting %s -> %s", entity_id, current_band, target_band)

        return {
            "entity_id": entity_id,
            "previous_band": current_band.value,
            "new_band": target_band.value,
            "events": triggered_events
        }
