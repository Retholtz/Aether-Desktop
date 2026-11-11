"""
core/rulesets/cyberpunk_ruleset.py - Exploding 1d10 and Armor SP Ablation
"""
import random
from typing import Dict, Any, Optional
from core.rulesets.base_ruleset import (
    TTRPGRulesetAdapter, CheckResult, AttackResult, DamageReport, RestResult
)


class CyberpunkRuleset(TTRPGRulesetAdapter):
    system_id = "cyberpunk_red"
    system_label = "Cyberpunk RED"

    def calculate_initiative(self, entity_data: Dict[str, Any], reported_roll: Optional[int] = None) -> float:
        ref = entity_data.get("attributes", {}).get("REF", 6)
        d10 = reported_roll if reported_roll is not None else random.randint(1, 10)
        return float(ref + d10)

    def resolve_check(
        self,
        entity_data: Dict[str, Any],
        stat_or_skill: str,
        target_dc: int,  # Difficulty Value (DV)
        reported_roll: Optional[int] = None
    ) -> CheckResult:
        skills = entity_data.get("skills", {})
        stat_base = skills.get(stat_or_skill, 10)
        if isinstance(stat_base, str):
            try:
                stat_base = int(stat_base.replace("+", "").strip())
            except ValueError:
                stat_base = 10
        elif not isinstance(stat_base, int):
            stat_base = 10

        d10 = reported_roll if reported_roll is not None else random.randint(1, 10)
        total_roll = d10
        is_crit_success = False
        is_crit_fail = False

        if d10 == 10:
            is_crit_success = True
            extra = random.randint(1, 10)
            total_roll += extra
            breakdown_mod = f"10 (EXPLODED +{extra})"
        elif d10 == 1:
            is_crit_fail = True
            extra = random.randint(1, 10)
            total_roll -= extra
            breakdown_mod = f"1 (CRIT FAIL -{extra})"
        else:
            breakdown_mod = str(d10)

        total = total_roll + stat_base
        is_success = total >= target_dc

        deg = "success" if is_success else "failure"
        if is_crit_success and is_success:
            deg = "critical_success"
        elif is_crit_fail and not is_success:
            deg = "critical_failure"

        breakdown = f"1d10 ({breakdown_mod}) + Base ({stat_base}) = {total} vs DV {target_dc} -> {deg.upper()}"
        return CheckResult(
            character_name=entity_data.get("name", "Edgerunner"),
            stat_or_skill=stat_or_skill,
            total_roll=total,
            target_dc=target_dc,
            is_success=is_success,
            is_critical=(is_crit_success or is_crit_fail),
            breakdown=breakdown,
            degree=deg,
            raw_roll=d10,
            modifier=stat_base
        )

    def resolve_attack(
        self,
        attacker_data: Dict[str, Any],
        target_data: Dict[str, Any],
        weapon_data: Dict[str, Any],
        reported_attack_roll: Optional[int] = None,
        reported_damage_roll: Optional[int] = None
    ) -> AttackResult:
        dv = target_data.get("dv", 15)
        check = self.resolve_check(attacker_data, "Handgun/Rifle", dv, reported_roll=reported_attack_roll)
        dmg = reported_damage_roll if reported_damage_roll is not None else sum(random.randint(1, 6) for _ in range(3))

        return AttackResult(
            attacker_name=attacker_data.get("name", "Attacker"),
            target_name=target_data.get("name", "Target"),
            attack_roll=check.total_roll,
            target_ac=dv,
            is_hit=check.is_success,
            is_critical=check.is_critical,
            damage_dealt=dmg if check.is_success else 0,
            damage_type="ballistic",
            breakdown=check.breakdown
        )

    def apply_damage(
        self,
        target_data: Dict[str, Any],
        damage_amount: int,
        damage_type: str = "ballistic",
        is_critical: bool = False
    ) -> DamageReport:
        # SP Armor Ablation Check
        guard_block = target_data.setdefault("vitals", {}).setdefault("guard", {})
        sp = guard_block.get("current", 0)
        cur_hp = target_data.setdefault("vitals", {}).setdefault("health", {}).get("current", 20)

        absorbed = min(sp, damage_amount)
        penetrating_damage = max(0, damage_amount - sp)

        if penetrating_damage > 0:
            # Armor ablated by 1
            new_sp = max(0, sp - 1)
            guard_block["current"] = new_sp
            new_hp = max(0, cur_hp - penetrating_damage)
            target_data["vitals"]["health"]["current"] = new_hp
        else:
            new_hp = cur_hp

        return DamageReport(
            target_name=target_data.get("name", "Target"),
            damage_taken=penetrating_damage,
            damage_type=damage_type,
            previous_hp=cur_hp,
            current_hp=new_hp,
            is_unconscious=(new_hp == 0),
            is_instant_death=False,
            absorbed_by_guard=absorbed
        )

    def execute_rest(
        self,
        entity_data: Dict[str, Any],
        rest_type: str,
        dice_mode: str = "digital",
        reported_rolls: Optional[Any] = None
    ) -> RestResult:
        body = entity_data.get("attributes", {}).get("BODY", 5)
        cur_hp = entity_data.get("vitals", {}).get("health", {}).get("current", 0)
        max_hp = entity_data.get("vitals", {}).get("health", {}).get("max", 20)
        recovered = min(max_hp - cur_hp, body)
        entity_data["vitals"]["health"]["current"] = cur_hp + recovered

        return RestResult(
            character_name=entity_data.get("name", "Edgerunner"),
            rest_type=rest_type,
            hp_recovered=recovered,
            hit_dice_recovered="N/A",
            spell_slots_restored="N/A",
            narrative=f"{entity_data.get('name', 'Edgerunner')} rested and recovered {recovered} HP."
        )
