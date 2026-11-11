"""
core/combat/concentration.py - Concentration Tracking & Damage Interrupt Engine
"""
import random
from typing import Dict, Any, Optional, Tuple, List
from pydantic import BaseModel, Field
from core.logger import get_logger

logger = get_logger("ConcentrationManager")


class ConcentrationRecord(BaseModel):
    caster_id: str
    spell_name: str
    spell_level: int
    cast_round: int
    dependents: List[str] = Field(default_factory=list)
    dependent_entities: List[str] = Field(default_factory=list)
    dependent_room_effects: List[str] = Field(default_factory=list)


class ConcentrationManager:
    def __init__(self):
        self.active_concentration: Dict[str, ConcentrationRecord] = {}

    def begin_concentration(
        self,
        caster_id: str,
        spell_name: str,
        spell_level: int,
        current_round: int,
        dependents: Optional[List[str]] = None,
        dependent_room_effects: Optional[List[str]] = None
    ) -> Optional[ConcentrationRecord]:
        """
        Begins concentration on a new spell.
        If the caster is already concentrating, drops the previous spell immediately.
        """
        dropped_record = self.active_concentration.pop(caster_id, None)
        if dropped_record:
            logger.info("Caster %s dropped %s to concentrate on %s", caster_id, dropped_record.spell_name, spell_name)

        dept_list = dependents or []
        room_fx = dependent_room_effects or []
        new_record = ConcentrationRecord(
            caster_id=caster_id,
            spell_name=spell_name,
            spell_level=spell_level,
            cast_round=current_round,
            dependents=dept_list,
            dependent_entities=dept_list,
            dependent_room_effects=room_fx
        )
        self.active_concentration[caster_id] = new_record
        return dropped_record

    def set_concentration(
        self,
        caster_id: str,
        spell_name: str,
        spell_level: int,
        dependents: Optional[List[str]] = None,
        dependent_room_effects: Optional[List[str]] = None,
        current_round: int = 1
    ) -> Optional[ConcentrationRecord]:
        """Alias for begin_concentration matching Task 2.3 specification."""
        return self.begin_concentration(
            caster_id=caster_id,
            spell_name=spell_name,
            spell_level=spell_level,
            current_round=current_round,
            dependents=dependents,
            dependent_room_effects=dependent_room_effects
        )

    def get_dc_for_damage(self, damage_amount: int) -> int:
        """Returns DC = max(10, floor(damage / 2))."""
        return max(10, damage_amount // 2)

    def break_concentration(self, caster_id: str) -> Optional[ConcentrationRecord]:
        """Dismisses the ongoing concentration spell and purges its record."""
        record = self.active_concentration.pop(caster_id, None)
        if record:
            logger.info("Concentration broken for caster %s on spell %s", caster_id, record.spell_name)
        return record

    def is_concentrating(self, caster_id: str) -> bool:
        """Checks if a caster currently maintains concentration."""
        return caster_id in self.active_concentration

    def check_concentration_on_damage(
        self,
        caster_data: Dict[str, Any],
        damage_taken: int,
        roll_override: Optional[int] = None
    ) -> Tuple[bool, int, str]:
        """
        Evaluates a concentration saving throw following damage intake.
        DC = max(10, floor(damage / 2)).
        Supports War Caster / Eldritch Mind advantage.
        Returns: (is_maintained, dc, narrative)
        """
        caster_id = caster_data.get("id", caster_data.get("name", "caster"))
        if not self.is_concentrating(caster_id):
            return True, 0, f"{caster_data.get('name', 'Caster')} is not concentrating on any spell."

        record = self.active_concentration[caster_id]
        dc = self.get_dc_for_damage(damage_taken)

        # Calculate Constitution save modifier
        con_save = 0
        native = caster_data.get("native_system_data", {})
        saves = caster_data.get("saving_throws") or native.get("saving_throws") or {}
        if "Constitution" in saves or "CON" in saves:
            raw_s = saves.get("Constitution", saves.get("CON"))
            try:
                con_save = int(str(raw_s).replace("+", ""))
            except ValueError:
                con_save = 0
        else:
            con_score = (
                caster_data.get("attributes", {}).get("Constitution")
                or caster_data.get("attributes", {}).get("Vigor")
                or native.get("ability_scores", {}).get("Constitution")
                or 10
            )
            con_save = (int(con_score) - 10) // 2

        # Check for Advantage from War Caster or Eldritch Mind
        feats = " ".join([
            str(f) for f in (
                caster_data.get("features_and_traits", [])
                + native.get("features_and_traits", [])
                + [t.get("name", "") if isinstance(t, dict) else str(t) for t in caster_data.get("universal_core", {}).get("tagged_traits", [])]
            )
        ]).lower()
        has_advantage = "war caster" in feats or "eldritch mind" in feats

        if roll_override is not None:
            r1 = roll_override
            r2 = roll_override
        else:
            r1 = random.randint(1, 20)
            r2 = random.randint(1, 20) if has_advantage else r1

        final_d20 = max(r1, r2) if has_advantage else r1
        total_save = final_d20 + con_save

        if total_save >= dc:
            adv_note = " (with Advantage)" if has_advantage else ""
            narrative = f"{caster_data.get('name', 'Caster')} maintains concentration on {record.spell_name}! (Rolled {final_d20}+{con_save} = {total_save} vs DC {dc}{adv_note})"
            return True, dc, narrative
        else:
            self.break_concentration(caster_id)
            narrative = f"{caster_data.get('name', 'Caster')} lost concentration on {record.spell_name}! (Rolled {final_d20}+{con_save} = {total_save} vs DC {dc})"
            return False, dc, narrative
