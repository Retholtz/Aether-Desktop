"""
core/compendiums/dnd_items.py - D&D 5e (2014 SRD 5.1 & 2024 SRD 5.2) Items, Gear, Packs, Poisons & Magic Items
Provides Adventuring Gear, Tools, Ammunition, Poisons, Equipment Packs (with expansion), Magic Items,
and deterministic multi-denomination D&D currency math (pp/gp/ep/sp/cp).
"""
import os
import json
from functools import lru_cache
from typing import Dict, Any, List, Optional, Tuple
from core.compendiums.homebrew_manager import HomebrewManager, get_default_homebrew_manager, COIN_TO_CP, query_match_rank

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


@lru_cache(maxsize=1)
def _load_srd_items() -> List[Dict[str, Any]]:
    path = os.path.join(_DATA_DIR, "srd_items.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _all_items(homebrew_mgr: Optional[HomebrewManager] = None) -> List[Dict[str, Any]]:
    mgr = homebrew_mgr or get_default_homebrew_manager()
    srd = [dict(x, compendium_type="items") for x in _load_srd_items()]
    hb = mgr.list_entries("items")
    if not hb:
        return srd
    by_idx = {x["index"]: x for x in srd}
    for h in hb:
        by_idx[h["index"]] = h
    return list(by_idx.values())


# --- Currency Helpers ---

def cost_to_cp(cost: Any) -> int:
    if isinstance(cost, (int, float)):
        return int(round(float(cost) * 100))
    if isinstance(cost, dict):
        qty = float(cost.get("quantity") or 0)
        unit = str(cost.get("unit") or "gp").lower().strip()
        return int(round(qty * COIN_TO_CP.get(unit, 100)))
    return 0


def currency_dict_to_cp(currency: Optional[Dict[str, Any]]) -> int:
    if not isinstance(currency, dict):
        return 0
    total = 0
    for coin, mult in COIN_TO_CP.items():
        try:
            total += max(0, int(currency.get(coin) or 0)) * mult
        except (TypeError, ValueError):
            pass
    return total


def cp_to_currency_dict(total_cp: int, preserve_ep: bool = False) -> Dict[str, int]:
    rem = max(0, int(total_cp))
    gp = rem // 100
    rem %= 100
    ep = 0
    if preserve_ep:
        ep = rem // 50
        rem %= 50
    sp = rem // 10
    cp = rem % 10
    return {"cp": cp, "sp": sp, "ep": ep, "gp": gp, "pp": 0}


def format_cp(total_cp: int) -> str:
    cp_val = max(0, int(total_cp))
    if cp_val == 0:
        return "0 cp (Special)"
    if cp_val >= 100 and cp_val % 100 == 0:
        return f"{cp_val // 100:,} gp"
    if cp_val >= 10 and cp_val % 10 == 0 and cp_val < 100:
        return f"{cp_val // 10} sp"
    if cp_val < 10:
        return f"{cp_val} cp"
    gp = cp_val // 100
    rem = cp_val % 100
    sp = rem // 10
    cp = rem % 10
    parts = []
    if gp:
        parts.append(f"{gp:,} gp")
    if sp:
        parts.append(f"{sp} sp")
    if cp:
        parts.append(f"{cp} cp")
    return " ".join(parts)


def deduct_currency(currency: Dict[str, int], price_cp: int) -> Tuple[bool, Dict[str, int], str]:
    """
    Attempts to deduct `price_cp` from a character's `{cp, sp, ep, gp, pp}` wallet.
    Minimizes denomination fragmentation while automatically making change when needed.
    """
    wallet = {k: max(0, int(currency.get(k) or 0)) for k in ("cp", "sp", "ep", "gp", "pp")}
    total_cp = currency_dict_to_cp(wallet)
    if price_cp < 0:
        return False, wallet, "Price cannot be negative."
    if total_cp < price_cp:
        return False, wallet, f"Insufficient funds: needs {format_cp(price_cp)}, has {format_cp(total_cp)}."

    # Subtract smallest coins first if enough without breaking larger ones, else rebalance remainder cleanly
    if wallet["cp"] >= price_cp:
        wallet["cp"] -= price_cp
        return True, wallet, f"Paid {format_cp(price_cp)}."

    # Try exact match with higher coins or make clean change
    new_total = total_cp - price_cp
    # Preserve pp only if originally held >= excess pp equivalent, otherwise convert remainder neatly into gp/sp/cp
    orig_pp_cp = wallet["pp"] * 1000
    keep_pp = min(wallet["pp"], new_total // 1000) if orig_pp_cp > 0 else 0
    rem_after_pp = new_total - (keep_pp * 1000)
    rebalanced = cp_to_currency_dict(rem_after_pp)
    rebalanced["pp"] = keep_pp
    return True, rebalanced, f"Paid {format_cp(price_cp)}. Remaining: {format_cp(new_total)}."


def add_currency(currency: Dict[str, int], amount_cp: int) -> Dict[str, int]:
    wallet = {k: max(0, int(currency.get(k) or 0)) for k in ("cp", "sp", "ep", "gp", "pp")}
    add_coins = cp_to_currency_dict(max(0, int(amount_cp)))
    for k in ("cp", "sp", "gp"):
        wallet[k] += add_coins.get(k, 0)
    return wallet


# --- Item Queries ---

def list_items(
    edition: str = "all",
    category: Optional[str] = None,
    rarity: Optional[str] = None,
    include_magic: bool = True,
    shop_only: bool = False,
    homebrew_only: bool = False,
    query: str = "",
    homebrew_mgr: Optional[HomebrewManager] = None,
) -> List[Dict[str, Any]]:
    items = _all_items(homebrew_mgr)
    ed = (edition or "all").lower().strip()
    q = (query or "").lower().strip()
    cat_f = (category or "").lower().strip()
    rar_f = (rarity or "").lower().strip()

    results = []
    for it in items:
        if homebrew_only and not it.get("is_homebrew"):
            continue
        if not include_magic and it.get("is_magic"):
            continue
        if shop_only and int(it.get("cost_cp") or 0) <= 0:
            continue
        if ed in ("2014", "2024") and ed not in it.get("editions", ["2014", "2024"]):
            continue
        if cat_f and cat_f not in it.get("category", "").lower() and cat_f not in it.get("subcategory", "").lower():
            continue
        if rar_f and rar_f != it.get("rarity", "Common").lower():
            continue
        if q:
            haystack = " ".join([
                it.get("name", ""),
                it.get("category", ""),
                it.get("subcategory", ""),
                it.get("rarity", ""),
                it.get("description", ""),
            ]).lower()
            if q not in haystack:
                continue
        results.append(it)

    return sorted(
        results,
        key=lambda x: (
            query_match_rank(x, q) if q else 0,
            not x.get("is_homebrew", False),
            x.get("is_magic", False),
            x.get("name", "").lower(),
        ),
    )


def get_item(
    name_or_index: str,
    homebrew_mgr: Optional[HomebrewManager] = None,
) -> Optional[Dict[str, Any]]:
    target = (name_or_index or "").strip().lower()
    if not target:
        return None
    items = _all_items(homebrew_mgr)
    for it in items:
        if it.get("index", "").lower() == target or it.get("name", "").lower() == target:
            return dict(it)
    for it in items:
        if target in it.get("name", "").lower():
            return dict(it)
    return None


def expand_pack_contents(
    pack_name_or_index: str,
    homebrew_mgr: Optional[HomebrewManager] = None,
) -> List[Dict[str, Any]]:
    """
    If `pack_name_or_index` is an Equipment Pack (e.g. Burglar's Pack, Explorer's Pack),
    returns the expanded list of individual sheet inventory items inside it.
    """
    pack = get_item(pack_name_or_index, homebrew_mgr=homebrew_mgr)
    if not pack or not pack.get("contents"):
        return []
    expanded: List[Dict[str, Any]] = []
    for ent in pack["contents"]:
        sub_name = ent.get("name", "Gear")
        sub_qty = int(ent.get("quantity", 1))
        sub_ref = get_item(ent.get("index") or sub_name, homebrew_mgr=homebrew_mgr)
        wt = f"{sub_ref['weight']} lb" if sub_ref and sub_ref.get("weight") else ""
        expanded.append({
            "name": sub_name,
            "qty": str(sub_qty),
            "weight": wt,
            "category": (sub_ref or {}).get("subcategory") or "Adventuring Gear",
            "notes": f"From {pack['name']}",
        })
    return expanded


def format_item_for_sheet(item: Dict[str, Any], qty: int = 1) -> Dict[str, Any]:
    bundle_qty = int(item.get("quantity_bundle") or 1)
    total_qty = max(1, qty * bundle_qty)
    wt = item.get("weight") or 0
    return {
        "name": item["name"],
        "qty": str(total_qty),
        "weight": f"{wt} lb" if wt else "",
        "category": item.get("subcategory") or item.get("category") or "Gear",
        "notes": f"Rarity: {item['rarity']}" if item.get("is_magic") else "",
        "is_magic": bool(item.get("is_magic", False)),
        "requires_attunement": bool(item.get("requires_attunement", False)),
        "compendium_index": item.get("index", ""),
    }
