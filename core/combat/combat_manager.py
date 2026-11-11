"""
core/combat/combat_manager.py - Rules-Agnostic Turn and Round Machine with Death Engine & TPK Routing
"""
import os
import json
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from core.rulesets import get_ruleset
from core.rulesets.base_ruleset import TTRPGRulesetAdapter, AttackResult, DamageReport
from core.combat.death_engine import DeathEngine
from core.combat.concentration import ConcentrationManager
from core.combat.condition_manager import tick_conditions
from core.vault.graveyard import archive_to_graveyard
from core.scene.range_bands import RangeBandTracker, RangeBand
from core.scene.corpse_manager import CorpseManager
from core.logger import get_logger

logger = get_logger("CombatManager")


@dataclass
class Combatant:
    entity_id: str
    name: str
    is_player: bool
    initiative: float
    raw_data: Dict[str, Any]
    conditions: List[str] = field(default_factory=list)
    has_acted_this_round: bool = False

    def __getitem__(self, item: str) -> Any:
        if item in ("id", "entity_id"):
            return self.entity_id
        if item == "name":
            return self.name
        if item == "is_player":
            return self.is_player
        if item == "initiative":
            return self.initiative
        if item == "conditions":
            return self.conditions
        if item == "health":
            vitals = self.raw_data.get("vitals")
            if isinstance(vitals, dict) and isinstance(vitals.get("health"), dict):
                return vitals["health"]
            return self.raw_data.get("health", {"current": 10, "max": 10})
        if item == "death_saves":
            return self.raw_data.setdefault("death_saves", {"successes": 0, "failures": 0, "status": "dying"})
        return self.raw_data.get(item)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            val = self[key]
            return val if val is not None else default
        except Exception:
            return default

    def setdefault(self, key: str, default: Any = None) -> Any:
        if key == "death_saves":
            return self.raw_data.setdefault("death_saves", default if default is not None else {"successes": 0, "failures": 0, "status": "dying"})
        return self.raw_data.setdefault(key, default)


class CombatManager:
    def __init__(self, system_id: str = "dnd_5e"):
        self.system_id = system_id
        self.adapter: TTRPGRulesetAdapter = get_ruleset(system_id)
        self.is_active: bool = False
        self.round_counter: int = 0
        self.turn_index: int = 0
        self.turn_order: List[Combatant] = []
        self.concentration_manager = ConcentrationManager()
        self.spatial_tracker = RangeBandTracker()
        self.corpse_manager = CorpseManager()

    @property
    def combatants(self) -> List[Combatant]:
        return self.turn_order

    @property
    def active_index(self) -> int:
        return self.turn_index

    @active_index.setter
    def active_index(self, val: int) -> None:
        self.turn_index = val

    @property
    def current_round(self) -> int:
        return self.round_counter

    @current_round.setter
    def current_round(self, val: int) -> None:
        self.round_counter = val

    def check_tpk_condition(self) -> bool:
        """Evaluates whether all player characters are incapacitated or slain."""
        pcs = [c for c in self.turn_order if c.is_player]
        if not pcs:
            return False
        for c in pcs:
            vitals = c.raw_data.get("vitals", {})
            hp = vitals.get("health", {}).get("current", 0) if isinstance(vitals, dict) and isinstance(vitals.get("health"), dict) else c.raw_data.get("health", {}).get("current", 0)
            if hp > 0:
                return False
        return True

    def check_tpk(self) -> bool:
        """Alias for check_tpk_condition."""
        return self.check_tpk_condition()

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
            # Default to NEAR range band
            self.spatial_tracker.set_position(p_id, RangeBand.NEAR)

        # Sort descending by initiative score
        self.turn_order.sort(key=lambda c: c.initiative, reverse=True)
        self.is_active = True
        self.round_counter = 1
        self.turn_index = 0

        return self.get_hud_state()

    def next_turn(self) -> Dict[str, Any]:
        """Advances turn pointer, evaluating conditions, death states, and TPK checks."""
        if not self.is_active or not self.turn_order:
            raise RuntimeError("Cannot advance turn: combat is not active.")

        # 1. TPK Check
        if self.check_tpk_condition():
            logger.warning("TPK Condition Detected in active encounter.")
            return {
                "event": "tpk_triggered",
                "message": "All party members have fallen. Awaiting narrative fork resolution.",
                "is_active": self.is_active,
                "round": self.round_counter
            }

        # End of turn tick for active combatant
        current = self.turn_order[self.turn_index]
        current.has_acted_this_round = True
        tick_conditions(current.raw_data, timing="end_of_turn")

        # 2. Advance pointer
        self.turn_index = (self.turn_index + 1) % len(self.turn_order)
        if self.turn_index == 0:
            self.round_counter += 1
            for c in self.turn_order:
                c.has_acted_this_round = False
            # Tick corpse decay counters
            self.corpse_manager.tick_round()

        current = self.turn_order[self.turn_index]

        # Start of turn tick for incoming combatant
        tick_conditions(current.raw_data, timing="start_of_turn")

        # 3. Check if incoming combatant is at 0 HP
        vitals = current.raw_data.get("vitals", {})
        hp = vitals.get("health", {}).get("current", 0) if isinstance(vitals, dict) and isinstance(vitals.get("health"), dict) else current.raw_data.get("health", {}).get("current", 0)

        if current.is_player and hp <= 0:
            death_data = current.raw_data.setdefault("death_saves", {"successes": 0, "failures": 0, "status": "dying"})
            # If stable, advance turn without requiring roll
            if death_data.get("status") == "stable":
                logger.info("%s is stable at 0 HP. Advancing turn without roll.", current.name)
                return self.next_turn()
            elif death_data.get("status") == "dying":
                hud = self.get_hud_state()
                hud.update({
                    "event": "awaiting_death_save",
                    "combatant_id": current.entity_id,
                    "combatant_name": current.name,
                    "current_state": death_data
                })
                return hud

        hud = self.get_hud_state()
        hud.update({
            "event": "turn_advanced",
            "combatant_id": current.entity_id,
            "combatant_name": current.name,
            "round": self.round_counter
        })
        return hud

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
        """Executes attack and applies damage via the active adapter with concentration interrupts."""
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

            # Concentration saving throw interrupt if target was concentrating
            if self.concentration_manager.is_concentrating(target.entity_id):
                conc_maintained, dc, conc_msg = self.concentration_manager.check_concentration_on_damage(
                    target.raw_data,
                    atk_result.damage_rolled
                )
                atk_result.narrative += f" {conc_msg}"

            # Check for death saving throw failures or massive damage if target at 0 HP
            if dmg_report.is_downed:
                if target.is_player:
                    max_hp = target.raw_data.get("vitals", {}).get("health", {}).get("max", 10)
                    ds = target.raw_data.setdefault("death_saves", DeathEngine.initialize_death_state())
                    updated_ds, ds_msg = DeathEngine.apply_damage_at_zero(
                        ds,
                        damage_amount=atk_result.damage_rolled,
                        is_critical=atk_result.is_critical,
                        max_hp=max_hp
                    )
                    target.raw_data["death_saves"] = updated_ds
                    atk_result.narrative += f" {ds_msg}"
                else:
                    if not target.raw_data.get("_corpse_spawned"):
                        target.raw_data["_corpse_spawned"] = True
                        corpse = self.corpse_manager.spawn_corpse_from_entity(
                            entity=target.raw_data,
                            current_round=self.round_counter,
                            location_id="current_scene"
                        )
                        atk_result.narrative += f" {target.name} falls slain, leaving behind {corpse.name}."

        return {
            "attack_result": atk_result,
            "damage_report": dmg_report,
            "narrative": atk_result.narrative
        }

    def handle_tpk_routing(
        self,
        branch_choice: str,
        campaign_id: str = "default_campaign",
        storage_root: str = "data/campaigns"
    ) -> Dict[str, Any]:
        """
        Executes fail-forward narrative branching upon Total Party Kill.
        Branches: 'prison_break', 'patron_pact', 'underworld_odyssey', 'archive'.
        """
        choice = branch_choice.strip().lower()
        pcs = [c for c in self.turn_order if c.is_player]

        if choice == "prison_break":
            for c in pcs:
                vitals = c.raw_data.setdefault("vitals", {})
                vitals.setdefault("health", {})["current"] = 1
                c.raw_data["death_saves"] = {"successes": 0, "failures": 0, "status": "stable"}
                c.raw_data["conditions"] = [cd for cd in c.conditions if str(cd).lower() not in ("dead", "dying", "unconscious")]
                # Stash gear
                inv = c.raw_data.get("inventory", [])
                c.raw_data.setdefault("quarantined_stash", []).extend(inv)
                c.raw_data["inventory"] = []
            return {
                "branch": "prison_break",
                "narrative": "The party awakens stripped of gear in ironclad dungeon cells (1 HP restored).",
                "revived_count": len(pcs)
            }

        elif choice == "patron_pact":
            for c in pcs:
                vitals = c.raw_data.setdefault("vitals", {})
                max_hp = vitals.get("health", {}).get("max", 20)
                vitals.setdefault("health", {})["current"] = max_hp
                c.raw_data["death_saves"] = {"successes": 0, "failures": 0, "status": "revived"}
                c.raw_data.setdefault("conditions", []).append("Geas / Debt")
            return {
                "branch": "patron_pact",
                "narrative": "An otherworldly entity restores the party to full health in exchange for an unyielding Geas contract.",
                "revived_count": len(pcs)
            }

        elif choice == "underworld_odyssey":
            for c in pcs:
                vitals = c.raw_data.setdefault("vitals", {})
                max_hp = vitals.get("health", {}).get("max", 20)
                vitals.setdefault("health", {})["current"] = max(1, max_hp // 2)
                c.raw_data["death_saves"] = {"successes": 0, "failures": 0, "status": "revived"}
                c.raw_data["conditions"] = ["Shadowfell Taint"]
            return {
                "branch": "underworld_odyssey",
                "narrative": "The party awakens across the River Styx on the misty shores of the Shadowfell.",
                "revived_count": len(pcs)
            }

        elif choice == "archive":
            archived_files = []
            for c in pcs:
                path = archive_to_graveyard(
                    campaign_id=campaign_id,
                    character_id=c.entity_id,
                    character_data=c.raw_data,
                    cause_of_death="Total Party Kill",
                    storage_root=storage_root
                )
                archived_files.append(path)
            self.end_encounter()
            return {
                "branch": "archive",
                "narrative": "The chronicle ends. Fallen heroes archived to the campaign graveyard.",
                "archived_count": len(archived_files),
                "files": archived_files
            }

        else:
            raise ValueError(f"Unknown TPK branch choice: '{branch_choice}'. Valid choices: 'prison_break', 'patron_pact', 'underworld_odyssey', 'archive'.")

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
