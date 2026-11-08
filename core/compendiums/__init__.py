"""
core/compendiums/__init__.py - Unified D&D 5e (2014 & 2024) SRD + Homebrew Compendium
"""
from typing import Dict, Any, List, Optional
from core.compendiums.homebrew_manager import HomebrewManager, get_default_homebrew_manager, query_match_rank
from core.compendiums.dnd_weapons import (
    list_weapons, get_weapon, list_weapon_masteries, format_weapon_for_sheet
)
from core.compendiums.dnd_armor import (
    list_armor, get_armor, compute_armor_class
)
from core.compendiums.dnd_items import (
    list_items, get_item, expand_pack_contents, format_item_for_sheet,
    cost_to_cp, currency_dict_to_cp, cp_to_currency_dict, format_cp,
    deduct_currency, add_currency
)
from core.compendiums.dnd_spells import (
    list_spells, get_spell, get_spells_for_class, format_spell_for_sheet,
    validate_character_spell_assignment, SUBCLASS_SPELL_LISTS,
    CASTER_CLASSES, CLASS_PREPARATION_TYPE, PHB_TO_SRD_ALIASES
)
from core.compendiums.dnd_advancement import (
    XP_THRESHOLDS,
    list_classes,
    get_class_definition,
    list_feats,
    get_feat,
    proficiency_bonus_for_level,
    parse_character_classes,
    format_class_summary,
    check_multiclass_prerequisites,
    compute_multiclass_spell_slots,
    get_advancement_options,
    apply_level_up,
)


def search_compendium(
    category: str = "all",
    query: str = "",
    edition: str = "all",
    filters: Optional[Dict[str, Any]] = None,
    homebrew_only: bool = False,
    limit: int = 200,
    homebrew_mgr: Optional[HomebrewManager] = None,
) -> List[Dict[str, Any]]:
    cat = (category or "all").lower().strip()
    flt = filters or {}
    results: List[Dict[str, Any]] = []

    if cat in ("all", "weapons", "weapon"):
        results.extend(list_weapons(
            edition=edition,
            category=flt.get("subcategory") or flt.get("weapon_category"),
            prop_filter=flt.get("property"),
            mastery=flt.get("mastery"),
            include_magic=flt.get("include_magic", True),
            homebrew_only=homebrew_only,
            query=query,
            homebrew_mgr=homebrew_mgr,
        ))
    if cat in ("all", "armor"):
        results.extend(list_armor(
            edition=edition,
            category=flt.get("armor_category") or flt.get("subcategory"),
            include_magic=flt.get("include_magic", True),
            homebrew_only=homebrew_only,
            query=query,
            homebrew_mgr=homebrew_mgr,
        ))
    if cat in ("all", "items", "item", "gear"):
        results.extend(list_items(
            edition=edition,
            category=flt.get("item_category") or flt.get("subcategory"),
            rarity=flt.get("rarity"),
            include_magic=flt.get("include_magic", True),
            shop_only=bool(flt.get("shop_only", False)),
            homebrew_only=homebrew_only,
            query=query,
            homebrew_mgr=homebrew_mgr,
        ))
    if cat in ("all", "spells", "spell"):
        lvl = flt.get("level")
        if lvl == "" or lvl is None:
            lvl_int = None
        else:
            lvl_int = int(lvl)
        results.extend(list_spells(
            edition=edition,
            class_name=flt.get("class_name"),
            level=lvl_int,
            max_level=flt.get("max_level"),
            school=flt.get("school"),
            ritual_only=bool(flt.get("ritual_only", False)),
            homebrew_only=homebrew_only,
            query=query,
            homebrew_mgr=homebrew_mgr,
        ))

    if query and cat in ("all",):
        results.sort(key=lambda x: (query_match_rank(x, query), not x.get("is_homebrew", False), x.get("name", "").lower()))

    if limit and limit > 0:
        return results[:limit]
    return results


def get_compendium_stats(homebrew_mgr: Optional[HomebrewManager] = None) -> Dict[str, int]:
    return {
        "weapons": len(list_weapons(homebrew_mgr=homebrew_mgr)),
        "armor": len(list_armor(homebrew_mgr=homebrew_mgr)),
        "items": len(list_items(homebrew_mgr=homebrew_mgr)),
        "spells": len(list_spells(homebrew_mgr=homebrew_mgr)),
        "homebrew_total": len((homebrew_mgr or get_default_homebrew_manager()).list_entries("all")),
    }
