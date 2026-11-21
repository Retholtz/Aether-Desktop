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
    provider_type: str = "vision_hud"  # "native_file", "localhost_http", or "vision_hud"
    poll_interval_sec: float = 0.5
    native_file_path: Optional[str] = None
    http_endpoint: Optional[str] = None
    http_timeout_sec: float = 1.5
    companion_files: List[str] = Field(default_factory=list)
    bitmask_mappings: Dict[str, Dict[str, int]] = Field(default_factory=dict)
    state_rules: List[Dict[str, Any]] = Field(default_factory=list)
    threshold_state_rules: List[Dict[str, Any]] = Field(default_factory=list)
    vital_mappings: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    resource_mappings: Dict[str, str] = Field(default_factory=dict)
    alert_rules: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    suppress_alerts_when_running: List[str] = Field(default_factory=list)
    hud_bars: List[HudBarRegion] = Field(default_factory=list)


class TelemetryFrame(BaseModel):
    game_id: str
    timestamp: float = Field(default_factory=time.time)
    vitals: Dict[str, float] = Field(default_factory=dict)     # Normalized 0.0 to 1.0
    resources: Dict[str, Any] = Field(default_factory=dict)    # Discrete counts/values
    states: List[str] = Field(default_factory=list)            # Active status flags
    raw_alerts: List[str] = Field(default_factory=list)        # Discrete events
