"""
core/telemetry/schema.py - Universal Game Telemetry Data Contracts
"""
import time
from typing import Dict, Any, List, Optional, Tuple
from pydantic import BaseModel, Field


class HudBarRegion(BaseModel):
    name: str                          # e.g., "health", "stamina", "mana"
    bbox_pct: Tuple[float, float, float, float]  # (left, top, width, height) in 0.0-1.0 screen ratios
    color_profile: str = "red"         # "red", "green", "blue", "yellow", "cyan"


HudBarCalibration = HudBarRegion


class GameTelemetryProfile(BaseModel):
    game_id: str
    provider_type: str = "vision_hud"  # "native_file" or "vision_hud"
    poll_interval_sec: float = 0.5
    native_file_path: Optional[str] = None
    hud_bars: List[HudBarRegion] = Field(default_factory=list)


class TelemetryFrame(BaseModel):
    game_id: str
    timestamp: float = Field(default_factory=time.time)
    vitals: Dict[str, float] = Field(default_factory=dict)     # Normalized 0.0 to 1.0
    resources: Dict[str, Any] = Field(default_factory=dict)    # Discrete counts/values
    states: List[str] = Field(default_factory=list)            # Active status flags
    raw_alerts: List[str] = Field(default_factory=list)        # Discrete events
