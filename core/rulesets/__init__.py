"""
core/rulesets/__init__.py - Ruleset Adapter Registry
"""
from typing import Dict, Type
from core.rulesets.base_ruleset import TTRPGRulesetAdapter
from core.rulesets.dnd5e_ruleset import DnD5eRuleset

_REGISTRY: Dict[str, Type[TTRPGRulesetAdapter]] = {
    "dnd_5e": DnD5eRuleset,
    "dnd5e": DnD5eRuleset,
    "5e": DnD5eRuleset
}


def get_ruleset(system_id: str) -> TTRPGRulesetAdapter:
    """Instantiates the appropriate ruleset adapter for the given system ID."""
    norm_id = system_id.lower().strip().replace("-", "_")
    adapter_cls = _REGISTRY.get(norm_id)
    if not adapter_cls:
        raise ValueError(f"Unsupported TTRPG system: '{system_id}'. Supported systems: {list(_REGISTRY.keys())}")
    return adapter_cls()
