"""
core/rulesets/pf2e_ruleset.py - Pathfinder 2e 4-Degree Ruleset Adapter
"""
import random
from typing import Dict, Any, Optional
from core.rulesets.base_ruleset import (
    TTRPGRulesetAdapter, CheckResult, AttackResult, DamageReport, RestResult
)


class PF2eRuleset(TTRPGRulesetAdapter):
    system_id = "pf2e"
    system_label = "Pathfinder 2nd Edition"

    def calculate_initiative(self, entity_data: Dict[str, Any], reported_roll: Optional[int] = None) -> float:
        # PF2e defaults initiative to Perception DC/Roll
        passives = entity_data.get("passives", {})
        percep_mod = passives.get("perception", 10) - 10
        if "Perception" in entity_data.get("skills", {}):
            try:
                percep_mod = int(str(entity_data["skills"]["Perception"]).replace("+", ""))
            except ValueError:
                pass
        d20 = reported_roll if reported_roll is not None else random.randint(1, 20)
        return float(d20 + percep_mod)

    def get_map(self, strike_number: int, is_agile: bool = False) -> int:
        """Computes Multiple Attack Penalty (MAP) based on attack count and Agile trait."""
        if strike_number <= 1:
            return 0
        elif strike_number == 2:
            return -4 if is_agile else -5
        else:
            return -8 if is_agile else -10

    def resolve_check(
        self,
        entity_data: Dict[str, Any],
        stat_or_skill: str,
        target_dc: int,
        reported_roll: Optional[int] = None
    ) -> CheckResult:
        mod = entity_data.get("skills", {}).get(stat_or_skill, 0)
        if isinstance(mod, str):
            try:
                mod = int(mod.split()[0].replace("+", ""))
            except ValueError:
                mod = 0
        elif not isinstance(mod, int):
            mod = 0

        d20 = reported_roll if reported_roll is not None else random.randint(1, 20)
        total = d20 + mod

        # Determine initial degree of success based on +/- 10
        if total >= target_dc + 10:
            deg = 3  # Critical Success
        elif total >= target_dc:
            deg = 2  # Success
        elif total <= target_dc - 10:
            deg = 0  # Critical Failure
        else:
            deg = 1  # Failure

        # Natural 20 steps up one degree; Natural 1 steps down one degree
        if d20 == 20:
            deg = min(3, deg + 1)
        elif d20 == 1:
            deg = max(0, deg - 1)

        deg_map = {
            3: "critical_success",
            2: "success",
            1: "failure",
            0: "critical_failure"
        }
        degree_str = deg_map[deg]
        is_success = deg >= 2

        breakdown = f"d20 ({d20}) + Mod ({mod}) = {total} vs DC {target_dc} -> {degree_str.upper()}"
        return CheckResult(
            character_name=entity_data.get("name", "Character"),
            stat_or_skill=stat_or_skill,
            total_roll=total,
            target_dc=target_dc,
            is_success=is_success,
            is_critical=(deg in (0, 3)),
            breakdown=breakdown,
            degree=degree_str,
            raw_roll=d20,
            modifier=mod
        )

    def resolve_attack(
        self,
        attacker_data: Dict[str, Any],
        target_data: Dict[str, Any],
        weapon_data: Dict[str, Any],
        reported_attack_roll: Optional[int] = None,
        reported_damage_roll: Optional[int] = None
    ) -> AttackResult:
        atk_bonus = 0
        raw_bonus = weapon_data.get("atk_bonus", 0)
        try:
            atk_bonus = int(str(raw_bonus).replace("+", ""))
        except ValueError:
            atk_bonus = 0

        target_ac = target_data.get("armor_class", 10)
        d20 = reported_attack_roll if reported_attack_roll is not None else random.randint(1, 20)
        total = d20 + atk_bonus

        check = self.resolve_check(attacker_data, "Strike", target_ac, reported_roll=d20)
        dmg = reported_damage_roll if reported_damage_roll is not None else random.randint(1, 8) + 2
        if check.degree == "critical_success":
            dmg *= 2

        return AttackResult(
            attacker_name=attacker_data.get("name", "Attacker"),
            target_name=target_data.get("name", "Target"),
            attack_roll=total,
            target_ac=target_ac,
            is_hit=check.is_success,
            is_critical=(check.degree == "critical_success"),
            damage_dealt=dmg if check.is_success else 0,
            damage_type=weapon_data.get("damage_type", "physical"),
            breakdown=check.breakdown
        )

    def apply_damage(
        self,
        target_data: Dict[str, Any],
        damage_amount: int,
        damage_type: str = "physical",
        is_critical: bool = False
    ) -> DamageReport:
        cur_hp = target_data.setdefault("vitals", {}).setdefault("health", {}).get("current", 10)
        new_hp = max(0, cur_hp - damage_amount)
        target_data["vitals"]["health"]["current"] = new_hp

        return DamageReport(
            target_name=target_data.get("name", "Target"),
            damage_taken=damage_amount,
            damage_type=damage_type,
            previous_hp=cur_hp,
            current_hp=new_hp,
            is_unconscious=(new_hp == 0),
            is_instant_death=False,
            absorbed_by_guard=0
        )

    def execute_rest(
        self,
        entity_data: Dict[str, Any],
        rest_type: str,
        dice_mode: str = "digital",
        reported_rolls: Optional[Any] = None
    ) -> RestResult:
        # Full rest restores HP to max and resets focus pool
        max_hp = entity_data.get("vitals", {}).get("health", {}).get("max", 10)
        entity_data["vitals"]["health"]["current"] = max_hp
        return RestResult(
            character_name=entity_data.get("name", "Character"),
            rest_type=rest_type,
            hp_recovered=max_hp,
            hit_dice_recovered="N/A",
            spell_slots_restored="Focus Points Restored",
            narrative=f"{entity_data.get('name', 'Character')} completed a rest in PF2e."
        )
