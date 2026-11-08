"""
core/compendiums/homebrew_manager.py - Persistent Custom / Homebrew Compendium Store
Stores user-created Weapons, Armor, Items/Magic Items, and Spells in JSON so they
integrate seamlessly alongside official D&D 5e 2014 & 2024 SRD entries across the
Compendium, Shop, Rest Spell Prep, and Level-Up systems.
"""
import os
import re
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from core.logger import get_logger

logger = get_logger("HomebrewCompendium")

VALID_CATEGORIES = ("weapons", "armor", "items", "spells")

COIN_TO_CP = {
    "cp": 1,
    "sp": 10,
    "ep": 50,
    "gp": 100,
    "pp": 1000,
}


def _utc_now_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat()
    except Exception:
        return datetime.utcnow().isoformat()


def _slugify(text: str) -> str:
    s = re.sub(r'[^a-z0-9]+', '-', str(text or '').lower()).strip('-')
    return s or uuid.uuid4().hex[:8]


def query_match_rank(entry: Dict[str, Any], query: str) -> int:
    """Ranks search relevance: 0=exact name/index, 1=prefix, 2=name substring, 3=metadata/description match."""
    q = (query or "").lower().strip()
    if not q:
        return 0
    name = str(entry.get("name") or "").lower().strip()
    idx = str(entry.get("index") or "").lower().strip()
    if name == q or idx == q:
        return 0
    if name.startswith(q) or idx.startswith(q):
        return 1
    if q in name or q in idx:
        return 2
    return 3


class HomebrewManager:
    def __init__(self, store_path: str = "data/homebrew/dnd5e_homebrew.json"):
        self.store_path = os.path.abspath(store_path)
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        self.data: Dict[str, List[Dict[str, Any]]] = self._load()

    def _default_data(self) -> Dict[str, List[Dict[str, Any]]]:
        return {cat: [] for cat in VALID_CATEGORIES}

    def _load(self) -> Dict[str, List[Dict[str, Any]]]:
        if not os.path.exists(self.store_path):
            return self._default_data()
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return self._default_data()
            out = self._default_data()
            for cat in VALID_CATEGORIES:
                entries = raw.get(cat, [])
                if isinstance(entries, list):
                    out[cat] = entries
            return out
        except Exception as e:
            logger.error(f"[HOMEBREW] Failed to load homebrew store: {e}")
            return self._default_data()

    def _save(self):
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    @staticmethod
    def _normalize_cost(entry: Dict[str, Any]) -> tuple[Dict[str, Any], int]:
        cost_obj = entry.get("cost")
        if isinstance(cost_obj, dict) and "quantity" in cost_obj:
            qty = max(0, int(float(cost_obj.get("quantity") or 0)))
            unit = str(cost_obj.get("unit") or "gp").lower().strip()
            if unit not in COIN_TO_CP:
                unit = "gp"
            return {"quantity": qty, "unit": unit}, qty * COIN_TO_CP[unit]

        cost_gp = entry.get("cost_gp")
        if cost_gp is not None and str(cost_gp).strip() != "":
            val = float(cost_gp)
            if val >= 1 or val == 0:
                qty = int(round(val))
                return {"quantity": qty, "unit": "gp"}, int(round(val * 100))
            cp = int(round(val * 100))
            return {"quantity": cp, "unit": "cp"}, cp

        return {"quantity": 0, "unit": "gp"}, 0

    def list_entries(self, category: str = "all") -> List[Dict[str, Any]]:
        self.data = self._load()
        cat = (category or "all").lower().strip()
        if cat == "all":
            combined = []
            for k in VALID_CATEGORIES:
                for item in self.data.get(k, []):
                    copy_it = dict(item)
                    copy_it.setdefault("compendium_type", k)
                    combined.append(copy_it)
            return combined
        if cat not in VALID_CATEGORIES:
            return []
        return [dict(x, compendium_type=cat) for x in self.data.get(cat, [])]

    def save_entry(self, category: str, entry: Dict[str, Any]) -> Dict[str, Any]:
        cat = (category or "").lower().strip()
        if cat not in VALID_CATEGORIES:
            return {"status": "error", "message": f"Invalid homebrew category '{category}'. Use: {', '.join(VALID_CATEGORIES)}."}

        name = str(entry.get("name") or "").strip()
        if not name:
            return {"status": "error", "message": "Entry name is required."}

        self.data = self._load()
        entry_id = str(entry.get("index") or entry.get("id") or "").strip()
        if not entry_id:
            entry_id = f"hb-{_slugify(name)}"

        editions = entry.get("editions")
        if not editions or not isinstance(editions, list):
            editions = ["2014", "2024"]

        normalized: Dict[str, Any] = {
            "index": entry_id,
            "id": entry_id,
            "name": name,
            "compendium_type": cat,
            "description": str(entry.get("description") or "").strip(),
            "editions": editions,
            "is_homebrew": True,
            "updated_at": _utc_now_iso(),
        }

        if cat in ("weapons", "armor", "items"):
            cost_dict, cost_cp = self._normalize_cost(entry)
            normalized["cost"] = cost_dict
            normalized["cost_cp"] = cost_cp
            normalized["cost_display"] = (
                f"{cost_dict['quantity']} {cost_dict['unit']}" if cost_cp > 0 else "0 gp"
            )
            normalized["weight"] = float(entry.get("weight") or 0)
            normalized["is_magic"] = bool(entry.get("is_magic", False))
            normalized["rarity"] = str(entry.get("rarity") or ("Uncommon" if normalized["is_magic"] else "Common"))
            normalized["requires_attunement"] = bool(entry.get("requires_attunement", False))

        if cat == "weapons":
            dmg_dice = str(entry.get("damage_dice") or "").strip()
            dmg_type = str(entry.get("damage_type") or "Slashing").strip()
            dmg_full = str(entry.get("damage") or f"{dmg_dice} {dmg_type}".strip()).strip()
            props_raw = entry.get("properties") or []
            if isinstance(props_raw, str):
                props = [p.strip() for p in props_raw.split(",") if p.strip()]
            else:
                props = [str(p).strip() for p in props_raw if str(p).strip()]
            normalized.update({
                "category": str(entry.get("category") or "Martial Melee"),
                "weapon_class": str(entry.get("weapon_class") or ("Martial" if "Martial" in str(entry.get("category", "")) else "Simple")),
                "range_type": str(entry.get("range_type") or ("Ranged" if "Ranged" in str(entry.get("category", "")) else "Melee")),
                "damage": dmg_full,
                "damage_dice": dmg_dice or (dmg_full.split()[0] if dmg_full else "1d6"),
                "damage_type": dmg_type,
                "atk_bonus": str(entry.get("atk_bonus") or ""),
                "two_handed_damage": str(entry.get("two_handed_damage") or ""),
                "range": str(entry.get("range") or "5 ft."),
                "properties": props,
                "mastery": str(entry.get("mastery") or ""),
                "mastery_desc": str(entry.get("mastery_desc") or ""),
            })
        elif cat == "armor":
            a_cat = str(entry.get("category") or "Medium Armor")
            normalized.update({
                "category": a_cat,
                "ac_base": int(entry.get("ac_base") if entry.get("ac_base") is not None else 12),
                "ac_bonus": int(entry.get("ac_bonus") or 0),
                "dex_bonus": bool(entry.get("dex_bonus", "Heavy" not in a_cat and "Shield" not in a_cat)),
                "max_dex_bonus": entry.get("max_dex_bonus") if entry.get("max_dex_bonus") not in ("", None) else (2 if "Medium" in a_cat else None),
                "str_minimum": int(entry.get("str_minimum") or 0),
                "stealth_disadvantage": bool(entry.get("stealth_disadvantage", False)),
                "don_time": str(entry.get("don_time") or "5 minutes"),
                "doff_time": str(entry.get("doff_time") or "1 minute"),
            })
        elif cat == "items":
            normalized.update({
                "category": str(entry.get("category") or ("Magic Items" if normalized["is_magic"] else "Adventuring Gear")),
                "subcategory": str(entry.get("subcategory") or entry.get("category") or "Custom Gear"),
                "consumable": bool(entry.get("consumable", False)),
                "quantity_bundle": int(entry.get("quantity_bundle") or 1),
                "contents": entry.get("contents") if isinstance(entry.get("contents"), list) else [],
            })
        elif cat == "spells":
            lvl = int(entry.get("level") or 0)
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(lvl, "th")
            lvl_label = "Cantrip" if lvl == 0 else f"{lvl}{suffix} Level"
            cls_raw = entry.get("classes") or []
            if isinstance(cls_raw, str):
                classes = [c.strip().title() for c in cls_raw.split(",") if c.strip()]
            else:
                classes = [str(c).strip().title() for c in cls_raw if str(c).strip()]
            comp_raw = entry.get("components") or ["V", "S"]
            if isinstance(comp_raw, str):
                comps = [c.strip().upper() for c in comp_raw.split(",") if c.strip()]
            else:
                comps = [str(c).strip().upper() for c in comp_raw if str(c).strip()]
            normalized.update({
                "level": lvl,
                "level_label": lvl_label,
                "school": str(entry.get("school") or "Evocation"),
                "classes": classes,
                "casting_time": str(entry.get("casting_time") or "1 action"),
                "range": str(entry.get("range") or "60 feet"),
                "components": comps,
                "material": str(entry.get("material") or ""),
                "ritual": bool(entry.get("ritual", False)),
                "concentration": bool(entry.get("concentration", False)),
                "duration": str(entry.get("duration") or "Instantaneous"),
                "damage": str(entry.get("damage") or ""),
                "damage_type": str(entry.get("damage_type") or ""),
                "higher_level": str(entry.get("higher_level") or ""),
                "dc_type": str(entry.get("dc_type") or ""),
                "attack_type": str(entry.get("attack_type") or ""),
            })

        bucket = self.data.setdefault(cat, [])
        existing_idx = next((i for i, x in enumerate(bucket) if x.get("index") == entry_id or x.get("name", "").lower() == name.lower()), None)
        action = "updated" if existing_idx is not None else "created"
        if existing_idx is not None:
            bucket[existing_idx] = normalized
        else:
            bucket.append(normalized)

        self._save()
        logger.info(f"[HOMEBREW] {action.capitalize()} {cat} entry '{name}' ({entry_id}).")
        return {"status": "success", "action": action, "category": cat, "entry": normalized}

    def delete_entry(self, category: str, entry_id: str) -> Dict[str, Any]:
        cat = (category or "").lower().strip()
        if cat not in VALID_CATEGORIES:
            return {"status": "error", "message": f"Invalid homebrew category '{category}'."}
        self.data = self._load()
        bucket = self.data.get(cat, [])
        target_low = str(entry_id or "").strip().lower()
        new_bucket = [
            x for x in bucket
            if x.get("index", "").lower() != target_low
            and x.get("id", "").lower() != target_low
            and x.get("name", "").lower() != target_low
        ]
        if len(new_bucket) == len(bucket):
            return {"status": "error", "message": f"Homebrew entry '{entry_id}' not found in '{cat}'."}
        self.data[cat] = new_bucket
        self._save()
        return {"status": "success", "category": cat, "deleted_id": entry_id}


_DEFAULT_HOMEBREW = HomebrewManager()


def get_default_homebrew_manager() -> HomebrewManager:
    return _DEFAULT_HOMEBREW
