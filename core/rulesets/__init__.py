"""
core/rulesets/__init__.py - Ruleset Adapter Registry
"""
from typing import Dict, Type
from core.rulesets.base_ruleset import TTRPGRulesetAdapter
from core.rulesets.dnd5e_ruleset import DnD5eRuleset
from core.rulesets.pf2e_ruleset import PF2eRuleset
from core.rulesets.coc7e_ruleset import CoC7eRuleset
from core.rulesets.cyberpunk_ruleset import CyberpunkRuleset

_REGISTRY: Dict[str, Type[TTRPGRulesetAdapter]] = {
    "dnd_5e": DnD5eRuleset,
    "dnd5e": DnD5eRuleset,
    "5e": DnD5eRuleset,
    "pf2e": PF2eRuleset,
    "pathfinder_2e": PF2eRuleset,
    "pathfinder2e": PF2eRuleset,
    "coc_7e": CoC7eRuleset,
    "coc7e": CoC7eRuleset,
    "call_of_cthulhu_7e": CoC7eRuleset,
    "cyberpunk_red": CyberpunkRuleset,
    "cyberpunkred": CyberpunkRuleset,
    "cyberpunk": CyberpunkRuleset
}


def get_ruleset(system_id: str) -> TTRPGRulesetAdapter:
    """Instantiates the appropriate ruleset adapter for the given system ID."""
    norm_id = system_id.lower().strip().replace("-", "_")
    adapter_cls = _REGISTRY.get(norm_id)
    if not adapter_cls:
        raise ValueError(f"Unsupported TTRPG system: '{system_id}'. Supported systems: {list(_REGISTRY.keys())}")
    return adapter_cls()


__all__ = [
    "TTRPGRulesetAdapter",
    "DnD5eRuleset",
    "PF2eRuleset",
    "CoC7eRuleset",
    "CyberpunkRuleset",
    "get_ruleset"
]
