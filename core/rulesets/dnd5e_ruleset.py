"""
core/rulesets/dnd5e_ruleset.py - Concrete Strategy Adapter for D&D 5th Edition
"""
import random
import re
from typing import Dict, Any, List, Optional
from core.rulesets.base_ruleset import (
    TTRPGRulesetAdapter, CheckResult, AttackResult, DamageReport, RestResult
)


class DnD5eRuleset(TTRPGRulesetAdapter):
    system_id = "dnd_5e"
    system_label = "D&D 5th Edition"

    def calculate_initiative(
        self,
        entity_data: Dict[str, Any],
        reported_roll: Optional[int] = None
    ) -> float:
        native = entity_data.get("native_system_data", entity_data)
        init_bonus = native.get("initiative_bonus", 0)
        if isinstance(init_bonus, str):
            m = re.search(r'[-+]?\d+', init_bonus)
            init_bonus = int(m.group(0)) if m else 0

        raw_roll = reported_roll if reported_roll is not None else random.randint(1, 20)
        # Tie-breaker decimal derived from DEX score
        dex_score = native.get("ability_scores", {}).get("Dexterity", 10)
        tie_breaker = dex_score / 100.0
        return float(raw_roll + init_bonus + tie_breaker)

    def resolve_check(
        self,
        entity_data: Dict[str, Any],
        stat_or_skill: str,
        target_dc: int,
        reported_roll: Optional[int] = None
    ) -> CheckResult:
        native = entity_data.get("native_system_data", entity_data)
        name = entity_data.get("name", "Unknown")
        raw_roll = reported_roll if reported_roll is not None else random.randint(1, 20)

        # Modifier extraction
        mod = 0
        skills = native.get("skills", {})
        norm_target = stat_or_skill.strip().title()

        if norm_target in skills:
            s_val = skills[norm_target]
            m = re.search(r'[-+]?\d+', str(s_val))
            mod = int(m.group(0)) if m else 0
        else:
            scores = native.get("ability_scores", {})
            if norm_target in scores:
                mod = (scores[norm_target] - 10) // 2

        total = raw_roll + mod
        is_success = total >= target_dc
        degree = "critical_success" if raw_roll == 20 else ("critical_failure" if raw_roll == 1 else ("success" if is_success else "failure"))
        narrative = f"{name} rolled {raw_roll} + {mod} = {total} vs DC {target_dc} ({degree.replace('_', ' ').title()})."

        return CheckResult(
            entity_name=name,
            stat_or_skill=stat_or_skill,
            target_dc=target_dc,
            raw_roll=raw_roll,
            modifier=mod,
            total=total,
            success=is_success,
            degree=degree,
            narrative=narrative
        )

    def resolve_attack(
        self,
        attacker_data: Dict[str, Any],
        target_data: Dict[str, Any],
        weapon_data: Dict[str, Any],
        reported_attack_roll: Optional[int] = None,
        reported_damage_roll: Optional[int] = None
    ) -> AttackResult:
        att_name = attacker_data.get("name", "Attacker")
        tar_name = target_data.get("name", "Target")
        wpn_name = weapon_data.get("name", "Weapon")

        # Attack bonus
        atk_bonus_str = str(weapon_data.get("atk_bonus", "+0"))
        m_atk = re.search(r'[-+]?\d+', atk_bonus_str)
        atk_bonus = int(m_atk.group(0)) if m_atk else 0

        # Defense AC
        tar_native = target_data.get("native_system_data", target_data)
        target_ac = tar_native.get("armor_class", 10)

        raw_atk = reported_attack_roll if reported_attack_roll is not None else random.randint(1, 20)
        is_crit = raw_atk == 20
        is_fumble = raw_atk == 1
        attack_total = raw_atk + atk_bonus

        is_hit = False if is_fumble else (True if is_crit else attack_total >= target_ac)

        # Damage evaluation
        dmg_type = "slashing"
        damage_str = str(weapon_data.get("damage", "1d6"))
        m_type = re.search(r'[A-Za-z]+$', damage_str)
        if m_type:
            dmg_type = m_type.group(0).lower()

        damage_dealt = 0
        if is_hit:
            if reported_damage_roll is not None:
                damage_dealt = reported_damage_roll
            else:
                damage_dealt = self._simulate_dice_roll(damage_str, is_crit)

        narrative = f"{att_name} attacked {tar_name} with {wpn_name}: [{raw_atk} + {atk_bonus} = {attack_total} vs AC {target_ac}] -> "
        narrative += f"CRITICAL HIT! ({damage_dealt} {dmg_type})" if is_crit else (f"HIT! ({damage_dealt} {dmg_type})" if is_hit else "MISS!")

        return AttackResult(
            attacker_name=att_name,
            target_name=tar_name,
            weapon_name=wpn_name,
            raw_roll=raw_atk,
            attack_bonus=atk_bonus,
            attack_total=attack_total,
            target_defense=target_ac,
            is_hit=is_hit,
            is_critical=is_crit,
            is_fumble=is_fumble,
            damage_rolled=damage_dealt,
            damage_type=dmg_type,
            narrative=narrative
        )

    def apply_damage(
        self,
        target_data: Dict[str, Any],
        damage_amount: int,
        damage_type: str = "bludgeoning",
        is_critical: bool = False
    ) -> DamageReport:
        native = target_data.get("native_system_data", target_data)
        tar_name = target_data.get("name", "Target")
        hp_dict = native.get("hit_points", {"current": 10, "max": 10, "temp": 0})
        vitals_health = target_data.get("vitals", {}).get("health", {})

        prev_hp = hp_dict.get("current", 10)
        temp_hp = hp_dict.get("temp", 0)
        max_hp = hp_dict.get("max", 10)

        rem_dmg = damage_amount
        temp_lost = 0

        # Absorb with temporary HP first
        if temp_hp > 0:
            if rem_dmg <= temp_hp:
                temp_lost = rem_dmg
                temp_hp -= rem_dmg
                rem_dmg = 0
            else:
                temp_lost = temp_hp
                rem_dmg -= temp_hp
                temp_hp = 0

        new_hp = max(0, prev_hp - rem_dmg)
        hp_dict["current"] = new_hp
        hp_dict["temp"] = temp_hp
        if vitals_health:
            vitals_health["current"] = new_hp

        is_downed = new_hp == 0
        is_instant_death = is_downed and (rem_dmg - prev_hp >= max_hp)

        notes = []
        if temp_lost > 0:
            notes.append(f"Absorbed {temp_lost} temp HP.")
        if is_instant_death:
            notes.append("INSTANT DEATH triggered (massive damage exceeding Max HP).")
        elif is_downed:
            notes.append("Character has fallen unconscious at 0 HP.")

        return DamageReport(
            target_name=tar_name,
            damage_received=damage_amount,
            damage_type=damage_type,
            previous_hp=prev_hp,
            current_hp=new_hp,
            temp_hp_lost=temp_lost,
            is_downed=is_downed,
            is_instant_death=is_instant_death,
            notes=notes
        )

    def execute_rest(
        self,
        entity_data: Dict[str, Any],
        rest_type: str,
        dice_mode: str = "digital",
        reported_rolls: Optional[List[int]] = None
    ) -> RestResult:
        native = entity_data.get("native_system_data", entity_data)
        name = entity_data.get("name", "Unknown")
        hp_dict = native.get("hit_points", {})
        con_score = native.get("ability_scores", {}).get("Constitution", 10)
        con_mod = (con_score - 10) // 2

        recovered_slots = {}
        recovered_meters = []
        hp_recovered = 0
        hd_spent = 0

        r_type = rest_type.lower().strip()

        if r_type == "short":
            # 1. Recover Pact Magic slots
            slots = native.get("spell_slots", {})
            for s_lvl, s_data in slots.items():
                if s_data.get("is_pact_magic") or s_data.get("reset_on") == "short_rest":
                    if s_data.get("current", 0) < s_data.get("max", 0):
                        recovered_slots[s_lvl] = s_data["max"] - s_data["current"]
                        s_data["current"] = s_data["max"]

            # 2. Reset Short Rest Custom Meters
            meters = native.get("custom_meters", {})
            for m_key, m_val in meters.items():
                if m_val.get("reset_on") == "short_rest":
                    m_val["current"] = m_val["max"]
                    recovered_meters.append(m_val.get("label", m_key))

            # 3. Hit Dice Expenditure for HP Recovery
            hd_meter = meters.get("hit_dice", {})
            cur_hd = hd_meter.get("current", 0)
            cur_hp = hp_dict.get("current", 0)
            max_hp = hp_dict.get("max", 10)

            if cur_hp < max_hp and cur_hd > 0:
                m_die = re.search(r'd(\d+)', hd_meter.get("label", "d8"))
                die_sides = int(m_die.group(1)) if m_die else 8
                
                roll_val = reported_rolls[0] if (reported_rolls and len(reported_rolls) > 0) else random.randint(1, die_sides)
                heal = max(1, roll_val + con_mod)
                actual_heal = min(max_hp - cur_hp, heal)
                
                hp_dict["current"] = cur_hp + actual_heal
                hd_meter["current"] -= 1
                hp_recovered = actual_heal
                hd_spent = 1

            hd_remaining = meters.get("hit_dice", {}).get("current", 0)
            narrative = f"{name} finished a Short Rest. Healed {hp_recovered} HP ({hd_spent} Hit Die used). Refreshed: {', '.join(recovered_meters + list(recovered_slots.keys())) or 'None'}."

        else:  # Long Rest
            # Full HP recovery
            max_hp = hp_dict.get("max", 10)
            hp_recovered = max_hp - hp_dict.get("current", 0)
            hp_dict["current"] = max_hp

            # Recover up to half total Hit Dice
            meters = native.get("custom_meters", {})
            hd_meter = meters.get("hit_dice", {})
            if hd_meter:
                max_hd = hd_meter.get("max", 1)
                cur_hd = hd_meter.get("current", 0)
                restore_hd = max(1, max_hd // 2)
                hd_meter["current"] = min(max_hd, cur_hd + restore_hd)

            # Recover all spell slots
            slots = native.get("spell_slots", {})
            for s_lvl, s_data in slots.items():
                if s_data.get("current", 0) < s_data.get("max", 0):
                    recovered_slots[s_lvl] = s_data["max"] - s_data["current"]
                    s_data["current"] = s_data["max"]

            # Reset all meters
            for m_key, m_val in meters.items():
                if m_val.get("reset_on") in ("long_rest", "short_rest"):
                    m_val["current"] = m_val["max"]
                    recovered_meters.append(m_val.get("label", m_key))

            hd_remaining = hd_meter.get("current", 0) if hd_meter else 0
            narrative = f"{name} finished a Long Rest. Healed to Max HP ({max_hp}). Spell slots and meters fully refreshed."

        # Keep root vitals synchronized
        if "vitals" in entity_data and "health" in entity_data["vitals"]:
            entity_data["vitals"]["health"]["current"] = hp_dict["current"]

        return RestResult(
            entity_name=name,
            rest_type=rest_type,
            hp_recovered=hp_recovered,
            hit_dice_spent=hd_spent,
            hit_dice_remaining=hd_remaining,
            slots_recovered=recovered_slots,
            meters_recovered=recovered_meters,
            narrative=narrative
        )

    def _simulate_dice_roll(self, damage_str: str, is_crit: bool = False) -> int:
        clean = damage_str.split()[0]
        m = re.match(r'(\d+)d(\d+)(?:([+-])(\d+))?', clean, re.IGNORECASE)
        if not m:
            return 4
        num_dice, sides, sign, bonus = m.groups()
        num_dice = int(num_dice) * (2 if is_crit else 1)
        sides = int(sides)
        mod = int(bonus) if bonus else 0
        if sign == "-":
            mod = -mod
        return sum(random.randint(1, sides) for _ in range(num_dice)) + mod
