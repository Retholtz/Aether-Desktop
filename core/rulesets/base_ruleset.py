"""
core/rulesets/base_ruleset.py - Abstract Strategy Contract for TTRPG Systems
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional


@dataclass
class CheckResult:
    character_name: str
    stat_or_skill: str
    total_roll: int
    target_dc: int
    is_success: bool
    is_critical: bool
    breakdown: str
    degree: str = "success"  # 'critical_success', 'success', 'partial_success', 'failure', 'critical_failure'
    consequence: Optional[str] = None
    raw_roll: int = 10
    modifier: int = 0

    @property
    def entity_name(self) -> str:
        return self.character_name

    @entity_name.setter
    def entity_name(self, val: str) -> None:
        self.character_name = val

    @property
    def total(self) -> int:
        return self.total_roll

    @total.setter
    def total(self, val: int) -> None:
        self.total_roll = val

    @property
    def success(self) -> bool:
        return self.is_success

    @success.setter
    def success(self, val: bool) -> None:
        self.is_success = val

    @property
    def narrative(self) -> str:
        return self.breakdown

    @narrative.setter
    def narrative(self, val: str) -> None:
        self.breakdown = val


@dataclass
class AttackResult:
    attacker_name: str
    target_name: str
    weapon_name: str = "Weapon"
    raw_roll: int = 10
    attack_bonus: int = 0
    attack_total: int = 10
    target_defense: int = 10
    is_hit: bool = False
    is_critical: bool = False
    is_fumble: bool = False
    damage_rolled: int = 0
    damage_type: str = "physical"
    narrative: str = ""
    attack_roll: Optional[int] = None
    target_ac: Optional[int] = None
    damage_dealt: Optional[int] = None
    breakdown: Optional[str] = None

    def __post_init__(self):
        if self.attack_roll is not None:
            self.attack_total = self.attack_roll
            self.raw_roll = self.attack_roll
        else:
            self.attack_roll = self.attack_total
        if self.target_ac is not None:
            self.target_defense = self.target_ac
        else:
            self.target_ac = self.target_defense
        if self.damage_dealt is not None:
            self.damage_rolled = self.damage_dealt
        else:
            self.damage_dealt = self.damage_rolled
        if self.breakdown is not None:
            self.narrative = self.breakdown
        else:
            self.breakdown = self.narrative


@dataclass
class DamageReport:
    target_name: str
    damage_received: int = 0
    damage_type: str = "physical"
    previous_hp: int = 10
    current_hp: int = 10
    temp_hp_lost: int = 0
    is_downed: bool = False
    is_instant_death: bool = False
    notes: List[str] = field(default_factory=list)
    damage_taken: Optional[int] = None
    absorbed_by_guard: int = 0
    is_unconscious: Optional[bool] = None

    def __post_init__(self):
        if self.damage_taken is not None:
            self.damage_received = self.damage_taken
        else:
            self.damage_taken = self.damage_received
        if self.is_unconscious is not None:
            self.is_downed = self.is_unconscious
        else:
            self.is_unconscious = self.is_downed


@dataclass
class RestResult:
    entity_name: str = ""
    rest_type: str = "short"
    hp_recovered: int = 0
    hit_dice_spent: int = 0
    hit_dice_remaining: int = 0
    slots_recovered: Dict[str, int] = field(default_factory=dict)
    meters_recovered: List[str] = field(default_factory=list)
    narrative: str = ""
    character_name: Optional[str] = None
    hit_dice_recovered: Optional[Any] = None
    spell_slots_restored: Optional[Any] = None

    def __post_init__(self):
        if self.character_name:
            self.entity_name = self.character_name
        else:
            self.character_name = self.entity_name


class TTRPGRulesetAdapter(ABC):
    """Abstract Strategy interface required for all game systems."""
    system_id: str
    system_label: str

    @abstractmethod
    def calculate_initiative(
        self,
        entity_data: Dict[str, Any],
        reported_roll: Optional[int] = None
    ) -> float:
        """Calculates a numerical initiative score for turn queue sorting."""
        pass

    @abstractmethod
    def resolve_check(
        self,
        entity_data: Dict[str, Any],
        stat_or_skill: str,
        target_dc: int,
        reported_roll: Optional[int] = None
    ) -> CheckResult:
        """Resolves an attribute or skill check against a target difficulty."""
        pass

    @abstractmethod
    def resolve_attack(
        self,
        attacker_data: Dict[str, Any],
        target_data: Dict[str, Any],
        weapon_data: Dict[str, Any],
        reported_attack_roll: Optional[int] = None,
        reported_damage_roll: Optional[int] = None
    ) -> AttackResult:
        """Resolves an attack roll against the target's defense metric."""
        pass

    @abstractmethod
    def apply_damage(
        self,
        target_data: Dict[str, Any],
        damage_amount: int,
        damage_type: str = "bludgeoning",
        is_critical: bool = False
    ) -> DamageReport:
        """Applies damage to an entity's health/armor pools and checks status triggers."""
        pass

    @abstractmethod
    def execute_rest(
        self,
        entity_data: Dict[str, Any],
        rest_type: str,
        dice_mode: str = "digital",
        reported_rolls: Optional[List[int]] = None
    ) -> RestResult:
        """Applies rest recovery to vitals, spell slots, and custom meters."""
        pass
