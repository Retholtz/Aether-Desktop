"""
core/combat/reactions.py - Reaction Polling & Interrupt Trigger Engine
"""
from typing import Dict, Any, List, Optional
from core.logger import get_logger

logger = get_logger("Reactions")


def evaluate_reaction_triggers(trigger_type: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Evaluates trigger conditions and returns eligible reaction responses.
    Supported Triggers:
      - 'on_leave_melee': Triggers Opportunity Attack for engaged hostiles.
      - 'on_attacked_hit': Checks if target has Shield or Hellish Rebuke prepared with reaction available.
      - 'on_spell_cast': Checks if opposing casters within 60 ft have Counterspell prepared.
    """
    eligible_reactions: List[Dict[str, Any]] = []

    if trigger_type == "on_leave_melee":
        hostiles = context.get("hostiles", [])
        mover_id = context.get("entity_id")
        has_disengaged = bool(context.get("has_disengaged", False))
        if not has_disengaged:
            for hostile in hostiles:
                if hostile.get("reaction_used", False):
                    continue
                eligible_reactions.append({
                    "reaction_type": "opportunity_attack",
                    "actor_id": hostile.get("id", hostile.get("name")),
                    "actor_name": hostile.get("name"),
                    "target_id": mover_id,
                    "description": f"{hostile.get('name')} can make an Opportunity Attack against {mover_id}."
                })

    elif trigger_type == "on_attacked_hit":
        target = context.get("target", {})
        if not target.get("reaction_used", False):
            # Check spells prepared
            spells = [
                s.get("name", "").lower() if isinstance(s, dict) else str(s).lower()
                for s in target.get("spells", target.get("native_system_data", {}).get("spells", []))
            ]
            if "shield" in spells:
                eligible_reactions.append({
                    "reaction_type": "cast_shield",
                    "actor_id": target.get("id", target.get("name")),
                    "actor_name": target.get("name"),
                    "ac_bonus": 5,
                    "description": f"{target.get('name')} can cast Shield (+5 AC, negates Magic Missile)."
                })
            if "hellish rebuke" in spells:
                eligible_reactions.append({
                    "reaction_type": "cast_hellish_rebuke",
                    "actor_id": target.get("id", target.get("name")),
                    "actor_name": target.get("name"),
                    "target_id": context.get("attacker_id"),
                    "description": f"{target.get('name')} can cast Hellish Rebuke against the attacker."
                })

    elif trigger_type == "on_spell_cast":
        spell_caster_id = context.get("caster_id")
        opponents = context.get("opponents", [])
        for opp in opponents:
            if opp.get("id") == spell_caster_id or opp.get("reaction_used", False):
                continue
            # Must be within 60 ft (ENGAGED or NEAR)
            band = opp.get("range_band", "near")
            if band in ("engaged", "near"):
                opp_spells = [
                    s.get("name", "").lower() if isinstance(s, dict) else str(s).lower()
                    for s in opp.get("spells", opp.get("native_system_data", {}).get("spells", []))
                ]
                if "counterspell" in opp_spells:
                    eligible_reactions.append({
                        "reaction_type": "cast_counterspell",
                        "actor_id": opp.get("id", opp.get("name")),
                        "actor_name": opp.get("name"),
                        "target_id": spell_caster_id,
                        "description": f"{opp.get('name')} can cast Counterspell to interrupt the casting."
                    })

    return eligible_reactions
