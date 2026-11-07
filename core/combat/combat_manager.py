"""
core/combat/combat_manager.py - Rules-Agnostic Turn and Round Machine
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from core.rulesets import get_ruleset
from core.rulesets.base_ruleset import TTRPGRulesetAdapter, AttackResult, DamageReport


@dataclass
class Combatant:
    entity_id: str
    name: str
    is_player: bool
    initiative: float
    raw_data: Dict[str, Any]
    conditions: List[str] = field(default_factory=list)
    has_acted_this_round: bool = False


class CombatManager:
    def __init__(self, system_id: str = "dnd_5e"):
        self.system_id = system_id
        self.adapter: TTRPGRulesetAdapter = get_ruleset(system_id)
        self.is_active: bool = False
        self.round_counter: int = 0
        self.turn_index: int = 0
        self.turn_order: List[Combatant] = []

    def start_encounter(
        self,
        participants: List[Dict[str, Any]],
        reported_initiatives: Optional[Dict[str, int]] = None
    ) -> Dict[str, Any]:
        """Calculates initiative, sorts combatants, and opens Round 1."""
        self.turn_order.clear()
        reported_initiatives = reported_initiatives or {}

        for p in participants:
            p_id = p.get("id", p.get("name", "unknown"))
            p_name = p.get("name", "Unknown")
            is_player = not p.get("is_adversary", False)
            rep_roll = reported_initiatives.get(p_name, reported_initiatives.get(p_id))

            score = self.adapter.calculate_initiative(p, reported_roll=rep_roll)
            self.turn_order.append(
                Combatant(
                    entity_id=p_id,
                    name=p_name,
                    is_player=is_player,
                    initiative=score,
                    raw_data=p
                )
            )

        # Sort descending by initiative score
        self.turn_order.sort(key=lambda c: c.initiative, reverse=True)
        self.is_active = True
        self.round_counter = 1
        self.turn_index = 0

        return self.get_hud_state()

    def next_turn(self) -> Dict[str, Any]:
        """Advances turn pointer and increments round when wrapping."""
        if not self.is_active or not self.turn_order:
            raise RuntimeError("Cannot advance turn: combat is not active.")

        self.turn_order[self.turn_index].has_acted_this_round = True
        self.turn_index += 1

        if self.turn_index >= len(self.turn_order):
            self.turn_index = 0
            self.round_counter += 1
            for c in self.turn_order:
                c.has_acted_this_round = False

        return self.get_hud_state()

    def previous_turn(self) -> Dict[str, Any]:
        """Steps backward in turn order."""
        if not self.is_active or not self.turn_order:
            raise RuntimeError("Combat is not active.")

        self.turn_index -= 1
        if self.turn_index < 0:
            if self.round_counter > 1:
                self.round_counter -= 1
                self.turn_index = len(self.turn_order) - 1
            else:
                self.turn_index = 0

        return self.get_hud_state()

    def execute_attack(
        self,
        attacker_id: str,
        target_id: str,
        weapon_name: str,
        reported_atk_roll: Optional[int] = None,
        reported_dmg_roll: Optional[int] = None
    ) -> Dict[str, Any]:
        """Executes attack and applies damage via the active adapter."""
        attacker = self._find_combatant(attacker_id)
        target = self._find_combatant(target_id)

        # Locate weapon
        weapons = attacker.raw_data.get("weapons", attacker.raw_data.get("native_system_data", {}).get("weapons", []))
        weapon = next((w for w in weapons if w.get("name", "").lower() == weapon_name.lower()), None)
        if not weapon:
            weapon = {"name": weapon_name, "atk_bonus": "+0", "damage": "1d6"}

        atk_result: AttackResult = self.adapter.resolve_attack(
            attacker.raw_data,
            target.raw_data,
            weapon,
            reported_attack_roll=reported_atk_roll,
            reported_damage_roll=reported_dmg_roll
        )

        dmg_report: Optional[DamageReport] = None
        if atk_result.is_hit and atk_result.damage_rolled > 0:
            dmg_report = self.adapter.apply_damage(
                target.raw_data,
                atk_result.damage_rolled,
                damage_type=atk_result.damage_type,
                is_critical=atk_result.is_critical
            )

        return {
            "attack_result": atk_result,
            "damage_report": dmg_report,
            "narrative": atk_result.narrative
        }

    def end_encounter(self) -> Dict[str, Any]:
        """Closes the encounter and resets combat tracking."""
        final_summary = {
            "rounds_elapsed": self.round_counter,
            "combatants_count": len(self.turn_order)
        }
        self.is_active = False
        self.round_counter = 0
        self.turn_index = 0
        self.turn_order.clear()
        return final_summary

    def get_hud_state(self) -> Dict[str, Any]:
        """Serializes current combat state for UI and LLM consumption."""
        active_name = self.turn_order[self.turn_index].name if self.is_active and self.turn_order else None
        return {
            "is_active": self.is_active,
            "round": self.round_counter,
            "turn_index": self.turn_index,
            "active_combatant": active_name,
            "queue": [
                {
                    "id": c.entity_id,
                    "name": c.name,
                    "initiative": round(c.initiative, 2),
                    "is_player": c.is_player,
                    "hp_current": c.raw_data.get("vitals", {}).get("health", {}).get("current", 0),
                    "hp_max": c.raw_data.get("vitals", {}).get("health", {}).get("max", 0),
                    "conditions": c.conditions,
                    "has_acted": c.has_acted_this_round
                }
                for c in self.turn_order
            ]
        }

    def _find_combatant(self, identifier: str) -> Combatant:
        for c in self.turn_order:
            if c.entity_id == identifier or c.name.lower() == identifier.lower():
                return c
        raise ValueError(f"Combatant '{identifier}' not found in active encounter.")
