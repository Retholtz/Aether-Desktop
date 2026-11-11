"""
core/combat/__init__.py
"""
from core.combat.combat_manager import CombatManager, Combatant
from core.combat.death_engine import DeathEngine, DeathSaveTracker, DeathSaveResult
from core.combat.concentration import ConcentrationManager, ConcentrationRecord
from core.combat.reactions import evaluate_reaction_triggers
from core.combat.condition_manager import ConditionRecord, tick_conditions

__all__ = [
    "CombatManager",
    "Combatant",
    "DeathEngine",
    "DeathSaveTracker",
    "DeathSaveResult",
    "ConcentrationManager",
    "ConcentrationRecord",
    "evaluate_reaction_triggers",
    "ConditionRecord",
    "tick_conditions"
]
