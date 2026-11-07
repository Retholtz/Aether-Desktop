"""
core/rulesets/base_ruleset.py - Abstract Strategy Contract for TTRPG Systems
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional


@dataclass
class CheckResult:
    entity_name: str
    stat_or_skill: str
    target_dc: int
    raw_roll: int
    modifier: int
    total: int
    success: bool
    degree: str  # "success", "failure", "critical_success", "critical_failure"
    narrative: str


@dataclass
class AttackResult:
    attacker_name: str
    target_name: str
    weapon_name: str
    raw_roll: int
    attack_bonus: int
    attack_total: int
    target_defense: int
    is_hit: bool
    is_critical: bool
    is_fumble: bool
    damage_rolled: int
    damage_type: str
    narrative: str


@dataclass
class DamageReport:
    target_name: str
    damage_received: int
    damage_type: str
    previous_hp: int
    current_hp: int
    temp_hp_lost: int
    is_downed: bool
    is_instant_death: bool
    notes: List[str] = field(default_factory=list)


@dataclass
class RestResult:
    entity_name: str
    rest_type: str
    hp_recovered: int
    hit_dice_spent: int
    hit_dice_remaining: int
    slots_recovered: Dict[str, int]
    meters_recovered: List[str]
    narrative: str


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
