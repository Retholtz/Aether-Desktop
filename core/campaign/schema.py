"""
core/campaign/schema.py - Structured Data Definitions for BYOC & World State
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, List, Optional


class LocationType(str, Enum):
    REGION = "region"
    SETTLEMENT = "settlement"
    DISTRICT = "district"
    POINT_OF_INTEREST = "point_of_interest"
    DUNGEON = "dungeon"
    ROOM = "room"


class QuestStatus(str, Enum):
    UNASSIGNED = "unassigned"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class FactionDisposition(str, Enum):
    ALLIED = "allied"
    FRIENDLY = "friendly"
    NEUTRAL = "neutral"
    SUSPICIOUS = "suspicious"
    HOSTILE = "hostile"


@dataclass
class LocationNode:
    id: str
    name: str
    type: LocationType
    parent_id: Optional[str] = None
    description: str = ""
    is_discovered: bool = False
    hazards: List[str] = field(default_factory=list)
    hidden_secrets: List[str] = field(default_factory=list)
    connected_location_ids: List[str] = field(default_factory=list)


@dataclass
class FactionRecord:
    id: str
    name: str
    disposition: FactionDisposition = FactionDisposition.NEUTRAL
    influence_score: int = 50  # 0 to 100
    agenda: str = ""
    public_reputation: str = ""
    hidden_motives: str = ""


@dataclass
class QuestObjective:
    id: str
    description: str
    is_completed: bool = False
    is_mandatory: bool = True


@dataclass
class QuestRecord:
    id: str
    title: str
    status: QuestStatus = QuestStatus.UNASSIGNED
    giver_npc: str = ""
    summary: str = ""
    objectives: List[QuestObjective] = field(default_factory=list)
    rewards: Dict[str, Any] = field(default_factory=dict)
    prerequisite_quest_ids: List[str] = field(default_factory=list)


@dataclass
class ClueRecord:
    id: str
    title: str
    content: str
    is_revealed: bool = False
    source_location_id: Optional[str] = None
    source_npc: Optional[str] = None
    tags: List[str] = field(default_factory=list)


@dataclass
class CampaignManifest:
    id: str
    title: str
    ruleset: str = "dnd_5e"
    genre: str = "heroic_fantasy"
    tone_sliders: Dict[str, int] = field(default_factory=lambda: {
        "grittiness": 5,      # 1 (heroic) to 10 (meat grinder)
        "magic_tech_level": 7, # 1 (low/mundane) to 10 (high fantasy/cyberware)
        "political_intrigue": 5 # 1 (dungeon crawl) to 10 (heavy roleplay/scheming)
    })
    premise: str = ""
    difficulty: str = "helpful"
    active_location_id: Optional[str] = None
    factions: Dict[str, FactionRecord] = field(default_factory=dict)
    locations: Dict[str, LocationNode] = field(default_factory=dict)
    quests: Dict[str, QuestRecord] = field(default_factory=dict)
    clues: Dict[str, ClueRecord] = field(default_factory=dict)
    world_rules: Dict[str, Any] = field(default_factory=dict)
    module_adherence: str = "strict"
