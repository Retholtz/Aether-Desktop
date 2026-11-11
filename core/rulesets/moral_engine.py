"""
core/rulesets/moral_engine.py - Paladin Oath Tenet Compliance & Moral Friction
"""
from typing import Dict, Any, Optional
from pydantic import BaseModel
from core.logger import get_logger

logger = get_logger("MoralEngine")


class MoralWarningResult(BaseModel):
    requires_warning: bool
    warning_prompt: str = ""
    conflicting_tenet: str = ""
    resulting_condition: Optional[str] = None


class MoralComplianceEngine:
    STRICT_OATH_ACTIONS = {
        "devotion": {
            "theft": ("Honesty & Honor", "Taking private belongings without consent violates the sacred tenets of Devotion."),
            "deceit": ("Honesty", "Deliberately uttering a falsehood under oath violates the tenet of Honesty."),
            "murder": ("Compassion & Honor", "Striking down an unarmed or surrendered opponent violates your sacred pledge."),
            "unprovoked_murder": ("Compassion & Honor", "Striking down an unarmed or surrendered opponent violates your sacred pledge."),
            "unprovoked_harm": ("Compassion & Honor", "Striking down an unarmed or surrendered opponent violates your sacred pledge.")
        }
    }

    @classmethod
    def evaluate_oath_compliance(
        cls,
        character: Dict[str, Any],
        action_type: str
    ) -> MoralWarningResult:
        archetype = str(character.get("archetype", "")).lower()
        char_str = str(character).lower()

        if "paladin" not in archetype and "paladin" not in char_str:
            return MoralWarningResult(requires_warning=False)

        subclass = "devotion" if "devotion" in archetype or "devotion" in char_str else None
        if not subclass or subclass not in cls.STRICT_OATH_ACTIONS:
            return MoralWarningResult(requires_warning=False)

        tenet_rules = cls.STRICT_OATH_ACTIONS[subclass]
        action_key = action_type.strip().lower()

        if action_key in tenet_rules:
            tenet_name, reason = tenet_rules[action_key]
            prompt = (
                f"Moral Friction Warning: Your sacred Oath of {subclass.title()} binds you to {tenet_name}. "
                f"{reason} Confirming this action will strain your oath and lock divine abilities."
            )
            logger.warning("Moral friction detected for %s committing %s (violates %s)", character.get("name"), action_type, tenet_name)
            return MoralWarningResult(
                requires_warning=True,
                warning_prompt=prompt,
                conflicting_tenet=tenet_name,
                resulting_condition="Oath_Strained"
            )

        return MoralWarningResult(requires_warning=False)


def evaluate_oath_compliance(character: Dict[str, Any], action_type: str) -> MoralWarningResult:
    """Convenience functional interface for MoralComplianceEngine."""
    return MoralComplianceEngine.evaluate_oath_compliance(character, action_type)
