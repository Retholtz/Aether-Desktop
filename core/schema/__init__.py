"""
core/schema - Schema 2.1 Definitions and Validation Engine
"""
from core.schema.character_schema import (
    VitalMetric,
    VitalsBlock,
    NormalizedAttributes,
    SemanticSkill,
    TaggedInventoryItem,
    TaggedTrait,
    RoleplayAnchors,
    AppearanceDescription,
    CustomMeter,
    UniversalCore,
    SpellSlotInfo,
    SpellEntry,
    NativeSystemData,
    CharacterDocument,
)
from core.schema.validator import validate_character_payload

__all__ = [
    "VitalMetric",
    "VitalsBlock",
    "NormalizedAttributes",
    "SemanticSkill",
    "TaggedInventoryItem",
    "TaggedTrait",
    "RoleplayAnchors",
    "AppearanceDescription",
    "CustomMeter",
    "UniversalCore",
    "SpellSlotInfo",
    "SpellEntry",
    "NativeSystemData",
    "CharacterDocument",
    "validate_character_payload",
]
