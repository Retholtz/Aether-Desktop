"""
core/schema/character_schema.py - Pydantic Validation Schema 2.1
"""
from typing import Dict, Any, List, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator


class VitalMetric(BaseModel):
    current: int
    max: int
    label: str = "Health"
    temp: Optional[int] = 0


class VitalsBlock(BaseModel):
    health: VitalMetric
    guard: VitalMetric


class NormalizedAttributes(BaseModel):
    might: int = Field(ge=10, le=100)
    agility: int = Field(ge=10, le=100)
    vigor: int = Field(ge=10, le=100)
    intellect: int = Field(ge=10, le=100)
    presence: int = Field(ge=10, le=100)


class SemanticSkill(BaseModel):
    category: str
    tier: str
    source_skill: str


class TaggedInventoryItem(BaseModel):
    name: str
    qty: str = "1"
    weight: str = "--"
    category: str = "Gear"
    notes: str = ""
    finite_scarcity: bool = False
    salvageable: bool = True
    genre: Optional[str] = "fantasy_d20"
    status: Optional[str] = "active"


class TaggedTrait(BaseModel):
    name: str
    description: str
    genre: str = "fantasy_d20"


class RoleplayAnchors(BaseModel):
    personality_traits: str = ""
    ideals: str = ""
    bonds: str = ""
    flaws: str = ""
    allies_and_organizations: str = ""


class AppearanceDescription(BaseModel):
    gender: str = ""
    age: str = ""
    size: str = "Medium"
    height: str = ""
    weight: str = ""
    alignment: str = ""
    faith: str = ""
    skin: str = ""
    eyes: str = ""
    hair: str = ""
    creature_type: str = "Humanoid"


class CustomMeter(BaseModel):
    label: str
    current: int
    max: int
    reset_on: str = "long_rest"


class UniversalCore(BaseModel):
    name: str
    player_name: str = "Player"
    archetype: str
    level: int = Field(ge=1, le=30)
    normalized_attributes: NormalizedAttributes
    semantic_skills: List[SemanticSkill] = []
    tagged_inventory: List[TaggedInventoryItem] = []
    tagged_traits: List[TaggedTrait] = []
    backstory: str = ""
    roleplay_anchors: RoleplayAnchors = Field(default_factory=RoleplayAnchors)
    custom_meters: Dict[str, CustomMeter] = {}
    description: AppearanceDescription = Field(default_factory=AppearanceDescription)


class SpellSlotInfo(BaseModel):
    current: int
    max: int
    reset_on: str = "long_rest"
    is_pact_magic: Optional[bool] = False
    pact_max: Optional[int] = None
    pact_current: Optional[int] = None


class SpellEntry(BaseModel):
    name: str
    level: int = Field(ge=0, le=9)
    is_cantrip: bool = False
    is_ritual: bool = False
    is_always_prepared: bool = False
    prepared: bool = True
    cast_time: str = "1A"
    range: str = "Touch"
    components: str = "V, S"
    duration: str = "Instantaneous"


class NativeSystemData(BaseModel):
    system_name: str = "dnd_5e"
    class_level: str
    is_pact_magic: bool = False
    species: str = "Human"
    background: str = ""
    inspiration: bool = False
    ability_save_dc: Union[int, str] = 10
    spell_attack_bonus: str = "+0"
    spellcasting_ability: str = ""
    armor_class: int = 10
    initiative_bonus: int = 0
    speed: str = "30 ft."
    defenses: str = ""
    resistances: str = ""
    hit_points: Dict[str, Any]
    hit_dice: Dict[str, Any]
    ability_scores: Dict[str, int]
    saving_throws: Dict[str, Any]
    skills: Dict[str, str]
    passives: Dict[str, int]
    weapons: List[Dict[str, Any]] = []
    currency: Dict[str, int] = Field(default_factory=lambda: {"cp": 0, "sp": 0, "ep": 0, "gp": 0, "pp": 0})
    proficiencies: Dict[str, Any] = {}
    attuned_magic_items: Dict[str, int] = Field(default_factory=lambda: {"current": 0, "max": 3})
    spell_slots: Dict[str, SpellSlotInfo] = {}
    max_prepared_leveled_spells: int = 0
    spells: List[SpellEntry] = []
    custom_meters: Dict[str, CustomMeter] = {}
    features_and_traits: List[str] = []
    inventory: List[Dict[str, Any]] = []
    weight_carried: str = "0 lb."
    encumbered: str = "150 lb."
    push_drag_lift: str = "300 lb."
    description: AppearanceDescription = Field(default_factory=AppearanceDescription)


class CharacterDocument(BaseModel):
    schema_version: str = Field(default="2.1")
    id: str
    name: str
    variant_label: str = "Canonical Root"
    player_name: str = "Player"
    level: int = Field(ge=1, le=30)
    experience_points: int = 0
    archetype: str
    species: str = "Human"
    background: str = ""
    ruleset: str = "dnd_5e"
    ruleset_label: str = "D&D 5E"
    native_genre: str = "heroic_fantasy"
    native_system: str = "dnd_5e"
    vitals: VitalsBlock
    attributes: Dict[str, int]
    armor_class: int
    speed: str = "30 ft."
    hit_dice: str
    saving_throws: Dict[str, Any]
    currency: Dict[str, int]
    weapons: List[Dict[str, Any]] = []
    inventory: List[Dict[str, Any]] = []
    features_and_traits: List[str] = []
    universal_core: UniversalCore
    native_system_data: NativeSystemData

    @field_validator("schema_version")
    @classmethod
    def validate_version(cls, v: str) -> str:
        if v != "2.1":
            raise ValueError(f"Unsupported schema version: '{v}'. Expected '2.1'")
        return v
