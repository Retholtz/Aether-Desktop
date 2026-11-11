"""
core/rulesets/coc7e_ruleset.py - Call of Cthulhu 7e d100 Roll-Under Adapter
"""
import random
from typing import Dict, Any, Optional
from core.rulesets.base_ruleset import (
    TTRPGRulesetAdapter, CheckResult, AttackResult, DamageReport, RestResult
)


class CoC7eRuleset(TTRPGRulesetAdapter):
    system_id = "coc_7e"
    system_label = "Call of Cthulhu 7th Edition"

    def calculate_initiative(self, entity_data: Dict[str, Any], reported_roll: Optional[int] = None) -> float:
        # Static DEX sorting for turn order
        dex = entity_data.get("attributes", {}).get("DEX", 50)
        return float(dex)

    def resolve_check(
        self,
        entity_data: Dict[str, Any],
        stat_or_skill: str,
        target_dc: int,  # In CoC, target_dc is the investigator's skill value (1-99)
        reported_roll: Optional[int] = None
    ) -> CheckResult:
        d100 = reported_roll if reported_roll is not None else random.randint(1, 100)
        skill_val = target_dc

        half_val = skill_val // 2
        fifth_val = skill_val // 5

        # Fumble determination
        fumble_threshold = 100 if skill_val >= 50 else 96

        if d100 == 1:
            deg = "critical_success"
            is_success = True
        elif d100 >= fumble_threshold:
            deg = "critical_failure"  # Fumble
            is_success = False
        elif d100 <= fifth_val:
            deg = "extreme_success"
            is_success = True
        elif d100 <= half_val:
            deg = "hard_success"
            is_success = True
        elif d100 <= skill_val:
            deg = "success"  # Regular Success
            is_success = True
        else:
            deg = "failure"
            is_success = False

        breakdown = f"d100 ({d100}) vs Skill ({skill_val}) [Hard: {half_val}, Extreme: {fifth_val}] -> {deg.upper()}"
        return CheckResult(
            character_name=entity_data.get("name", "Investigator"),
            stat_or_skill=stat_or_skill,
            total_roll=d100,
            target_dc=skill_val,
            is_success=is_success,
            is_critical=(deg in ("critical_success", "critical_failure")),
            breakdown=breakdown,
            degree=deg,
            raw_roll=d100,
            modifier=0
        )

    def resolve_attack(
        self,
        attacker_data: Dict[str, Any],
        target_data: Dict[str, Any],
        weapon_data: Dict[str, Any],
        reported_attack_roll: Optional[int] = None,
        reported_damage_roll: Optional[int] = None
    ) -> AttackResult:
        skill_val = int(weapon_data.get("skill_val", 50))
        check = self.resolve_check(attacker_data, "Fighting/Firearms", skill_val, reported_roll=reported_attack_roll)
        dmg = reported_damage_roll if reported_damage_roll is not None else random.randint(1, 6)

        return AttackResult(
            attacker_name=attacker_data.get("name", "Attacker"),
            target_name=target_data.get("name", "Target"),
            attack_roll=check.total_roll,
            target_ac=skill_val,
            is_hit=check.is_success,
            is_critical=(check.degree in ("critical_success", "extreme_success")),
            damage_dealt=dmg if check.is_success else 0,
            damage_type="kinetic",
            breakdown=check.breakdown
        )

    def apply_damage(
        self,
        target_data: Dict[str, Any],
        damage_amount: int,
        damage_type: str = "physical",
        is_critical: bool = False
    ) -> DamageReport:
        if damage_type.lower() == "sanity":
            cur_san = target_data.setdefault("vitals", {}).setdefault("sanity", {}).get("current", 50)
            new_san = max(0, cur_san - damage_amount)
            target_data["vitals"]["sanity"]["current"] = new_san
            if damage_amount >= 5:
                target_data.setdefault("conditions", []).append("Temporary Insanity (Bout of Madness)")
            return DamageReport(
                target_name=target_data.get("name", "Investigator"),
                damage_taken=damage_amount,
                damage_type="sanity",
                previous_hp=cur_san,
                current_hp=new_san,
                is_unconscious=False,
                is_instant_death=False,
                absorbed_by_guard=0
            )

        cur_hp = target_data.setdefault("vitals", {}).setdefault("health", {}).get("current", 10)
        new_hp = max(0, cur_hp - damage_amount)
        target_data["vitals"]["health"]["current"] = new_hp

        return DamageReport(
            target_name=target_data.get("name", "Investigator"),
            damage_taken=damage_amount,
            damage_type="physical",
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
        cur_hp = entity_data.get("vitals", {}).get("health", {}).get("current", 1)
        max_hp = entity_data.get("vitals", {}).get("health", {}).get("max", 10)
        entity_data["vitals"]["health"]["current"] = min(max_hp, cur_hp + 3)
        return RestResult(
            character_name=entity_data.get("name", "Investigator"),
            rest_type=rest_type,
            hp_recovered=3,
            hit_dice_recovered="N/A",
            spell_slots_restored="N/A",
            narrative=f"{entity_data.get('name', 'Investigator')} rested and recovered 3 HP."
        )
