"""
scripts/sync_srd_data.py - Downloads and normalizes the official D&D 5e SRD 5.1 (2014) and SRD 5.2 (2024)
JSON datasets (CC-BY-4.0 / OGL from 5e-bits/5e-database) into clean local compendium JSON files under
core/compendiums/data/.

Outputs:
  - core/compendiums/data/srd_weapons.json
  - core/compendiums/data/srd_armor.json
  - core/compendiums/data/srd_items.json
  - core/compendiums/data/srd_spells.json
  - core/compendiums/data/srd_weapon_masteries.json
"""
import os
import re
import json
import urllib.request
from typing import Dict, Any, List, Optional

BASE_2014 = "https://raw.githubusercontent.com/5e-bits/5e-database/main/src/2014/en"
BASE_2024 = "https://raw.githubusercontent.com/5e-bits/5e-database/main/src/2024/en"

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_DIR = os.path.join(ROOT_DIR, "core", "compendiums", "data")

COIN_TO_CP = {
    "cp": 1,
    "sp": 10,
    "ep": 50,
    "gp": 100,
    "pp": 1000,
}

# Suggested fallback pricing (in GP) for SRD magic items when vendor pricing is enabled
RARITY_DEFAULT_GP = {
    "common": 50,
    "uncommon": 250,
    "rare": 2500,
    "very rare": 20000,
    "legendary": 100000,
    "artifact": 0,
    "varies": 500,
}

# Canonical Artificer spell additions (beyond the 8 core SRD caster classes)
ARTIFICER_SRD_SPELLS = {
    # Cantrips
    "acid splash", "create bonfire", "fire bolt", "frostbite", "guidance",
    "light", "mage hand", "mending", "message", "poison spray", "prestidigitation",
    "ray of frost", "resistance", "shocking grasp", "spare the dying", "thorn whip",
    # 1st Level
    "alarm", "cure wounds", "detect magic", "disguise self", "expeditious retreat",
    "faerie fire", "false life", "feather fall", "grease", "identify", "jump",
    "longstrider", "purify food and drink", "sanctuary",
    # 2nd Level
    "aid", "alter self", "arcane lock", "blur", "continual flame", "darkvision",
    "enhance ability", "enlarge/reduce", "heat metal", "invisibility",
    "lesser restoration", "levitate", "magic mouth", "magic weapon",
    "protection from poison", "rope trick", "see invisibility", "spider climb", "web",
    # 3rd Level
    "blink", "create food and water", "dispel magic", "fly", "glyph of warding",
    "haste", "protection from energy", "revivify", "water breathing", "water walk",
    # 4th Level
    "arcane eye", "fabricate", "freedom of movement", "leomund's secret chest",
    "mordenkainen's faithful hound", "mordenkainen's private sanctum",
    "otiluke's resilient sphere", "stone shape", "stoneskin",
    # 5th Level
    "animate objects", "bigby's hand", "creation", "greater restoration",
    "wall of stone",
}


def fetch_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "Aether-Desktop-SRD-Sync/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _cost_to_dict(cost_obj: Any) -> Optional[Dict[str, Any]]:
    if not cost_obj:
        return None
    if isinstance(cost_obj, (int, float)):
        return {"quantity": int(cost_obj), "unit": "gp"}
    if isinstance(cost_obj, dict) and "quantity" in cost_obj:
        return {"quantity": int(cost_obj.get("quantity", 0)), "unit": str(cost_obj.get("unit", "gp")).lower()}
    return None


def _cost_to_cp(cost_dict: Optional[Dict[str, Any]]) -> int:
    if not cost_dict:
        return 0
    qty = int(cost_dict.get("quantity", 0))
    unit = str(cost_dict.get("unit", "gp")).lower().strip()
    return qty * COIN_TO_CP.get(unit, 100)


def _join_desc(raw_desc: Any) -> str:
    if isinstance(raw_desc, list):
        return "\n\n".join(str(x).strip() for x in raw_desc if str(x).strip())
    if isinstance(raw_desc, str):
        return raw_desc.strip()
    return ""


def _detect_attunement(entry: Dict[str, Any], desc_text: str) -> tuple[bool, str]:
    if "attunement" in entry and isinstance(entry["attunement"], bool):
        return entry["attunement"], ""
    m = re.search(r'\(requires attunement(?:\s+by\s+[^)]+)?\)', desc_text, re.IGNORECASE)
    if m:
        return True, m.group(0).strip("()")
    return False, ""


def normalize_equipment_and_magic(
    eq14: List[Dict[str, Any]],
    eq24: List[Dict[str, Any]],
    mi14: List[Dict[str, Any]],
    mi24: List[Dict[str, Any]],
    po24: List[Dict[str, Any]],
    masteries24: List[Dict[str, Any]],
) -> Dict[str, Any]:
    weapons_map: Dict[str, Dict[str, Any]] = {}
    armor_map: Dict[str, Dict[str, Any]] = {}
    items_map: Dict[str, Dict[str, Any]] = {}
    mastery_desc_map = {m["name"].lower(): _join_desc(m.get("desc") or m.get("description")) for m in masteries24}

    def _is_weapon_2014(x: Dict[str, Any]) -> bool:
        return x.get("equipment_category", {}).get("index") == "weapon"

    def _is_armor_2014(x: Dict[str, Any]) -> bool:
        return x.get("equipment_category", {}).get("index") == "armor"

    def _cat_indices_2024(x: Dict[str, Any]) -> List[str]:
        return [c.get("index", "") for c in x.get("equipment_categories", [])]

    def _norm_weapon(raw: Dict[str, Any], edition: str) -> Dict[str, Any]:
        idx = raw["index"]
        cats24 = _cat_indices_2024(raw)
        if edition == "2014":
            w_class = raw.get("weapon_category", "Simple")
            r_type = raw.get("weapon_range", "Melee")
            cat_label = raw.get("category_range", f"{w_class} {r_type}")
        else:
            w_class = "Martial" if "martial-weapons" in cats24 else "Simple"
            r_type = "Ranged" if "ranged-weapons" in cats24 else "Melee"
            cat_label = f"{w_class} {r_type}"

        dmg_obj = raw.get("damage") or {}
        dmg_dice = dmg_obj.get("damage_dice", "")
        dmg_type = (dmg_obj.get("damage_type") or {}).get("name", "")
        damage_str = f"{dmg_dice} {dmg_type}".strip()

        th_obj = raw.get("two_handed_damage") or {}
        th_dice = th_obj.get("damage_dice", "")
        th_type = (th_obj.get("damage_type") or {}).get("name", dmg_type)
        th_str = f"{th_dice} {th_type}".strip() if th_dice else ""

        rng_obj = raw.get("range") or {}
        if rng_obj.get("long"):
            rng_str = f"{rng_obj.get('normal', 20)}/{rng_obj['long']} ft."
        elif rng_obj.get("normal"):
            rng_str = f"{rng_obj['normal']} ft."
        else:
            rng_str = "5 ft."

        throw_obj = raw.get("throw_range") or {}
        if throw_obj.get("normal") and throw_obj.get("long"):
            throw_str = f"Thrown ({throw_obj['normal']}/{throw_obj['long']} ft.)"
        else:
            throw_str = ""

        props = [p.get("name", "") for p in raw.get("properties", []) if p.get("name")]
        if throw_str and not any(p.lower().startswith("thrown") for p in props):
            # Keep clean property name list; add range detail in notes
            pass

        mastery_obj = raw.get("mastery") or {}
        mastery_name = mastery_obj.get("name", "")
        mastery_desc = mastery_desc_map.get(mastery_name.lower(), "")

        cost = _cost_to_dict(raw.get("cost"))
        desc = _join_desc(raw.get("description") or raw.get("desc") or raw.get("special"))

        return {
            "index": idx,
            "name": raw["name"],
            "category": cat_label,
            "weapon_class": w_class,
            "range_type": r_type,
            "damage": damage_str,
            "damage_dice": dmg_dice,
            "damage_type": dmg_type,
            "two_handed_damage": th_str,
            "versatile_dice": th_dice,
            "range": rng_str,
            "throw_range": f"{throw_obj.get('normal')}/{throw_obj.get('long')} ft." if throw_obj else "",
            "properties": props,
            "mastery": mastery_name,
            "mastery_desc": mastery_desc,
            "cost": cost or {"quantity": 0, "unit": "gp"},
            "cost_cp": _cost_to_cp(cost),
            "weight": float(raw.get("weight") or 0),
            "description": desc,
            "editions": [edition],
            "is_magic": False,
            "rarity": "Common",
            "requires_attunement": False,
            "is_homebrew": False,
        }

    def _norm_armor(raw: Dict[str, Any], edition: str) -> Dict[str, Any]:
        idx = raw["index"]
        cats24 = _cat_indices_2024(raw)
        if edition == "2014":
            a_cat = raw.get("armor_category", "Light")
            cat_label = "Shield" if a_cat.lower() == "shield" else f"{a_cat} Armor"
        else:
            if "shields" in cats24:
                cat_label = "Shield"
            elif "heavy-armor" in cats24:
                cat_label = "Heavy Armor"
            elif "medium-armor" in cats24:
                cat_label = "Medium Armor"
            else:
                cat_label = "Light Armor"

        ac_obj = raw.get("armor_class") or {}
        cost = _cost_to_dict(raw.get("cost"))
        return {
            "index": idx,
            "name": raw["name"],
            "category": cat_label,
            "ac_base": int(ac_obj.get("base", 10)),
            "dex_bonus": bool(ac_obj.get("dex_bonus", False)),
            "max_dex_bonus": ac_obj.get("max_bonus"),
            "str_minimum": int(raw.get("str_minimum") or 0),
            "stealth_disadvantage": bool(raw.get("stealth_disadvantage", False)),
            "don_time": str(raw.get("don_time") or ("1 action" if cat_label == "Shield" else "1-10 minutes")),
            "doff_time": str(raw.get("doff_time") or ("1 action" if cat_label == "Shield" else "1-5 minutes")),
            "cost": cost or {"quantity": 0, "unit": "gp"},
            "cost_cp": _cost_to_cp(cost),
            "weight": float(raw.get("weight") or 0),
            "description": _join_desc(raw.get("description") or raw.get("desc")),
            "editions": [edition],
            "is_magic": False,
            "rarity": "Common",
            "requires_attunement": False,
            "is_homebrew": False,
        }

    def _norm_gear(raw: Dict[str, Any], edition: str) -> Dict[str, Any]:
        idx = raw["index"]
        cats24 = _cat_indices_2024(raw)
        if edition == "2014":
            eq_cat = (raw.get("equipment_category") or {}).get("name", "Adventuring Gear")
            gear_cat = (raw.get("gear_category") or {}).get("name", "")
            tool_cat = raw.get("tool_category", "")
            veh_cat = raw.get("vehicle_category", "")
            subcat = gear_cat or tool_cat or veh_cat or eq_cat
        else:
            eq_cat = (raw.get("equipment_categories") or [{}])[0].get("name", "Adventuring Gear")
            if "equipment-packs" in cats24:
                eq_cat = "Adventuring Gear"
                subcat = "Equipment Packs"
            elif "ammunition" in cats24:
                eq_cat = "Adventuring Gear"
                subcat = "Ammunition"
            elif "tools" in cats24:
                eq_cat = "Tools"
                subcat = (raw.get("equipment_categories") or [{}])[0].get("name", "Tools")
            else:
                subcat = eq_cat

        contents = []
        for c_ent in raw.get("contents") or []:
            it_obj = c_ent.get("item") or {}
            if it_obj.get("name"):
                contents.append({
                    "index": it_obj.get("index", ""),
                    "name": it_obj["name"],
                    "quantity": int(c_ent.get("quantity", 1)),
                })

        cost = _cost_to_dict(raw.get("cost"))
        desc = _join_desc(raw.get("description") or raw.get("desc") or raw.get("special"))
        consumable = any(k in (raw["name"] + " " + subcat).lower() for k in (
            "ration", "potion", "oil", "acid", "alchemist's fire", "holy water",
            "antitoxin", "poison", "torch", "candle", "ammunition", "arrow", "bolt", "bullet", "needle", "chalk"
        ))

        return {
            "index": idx,
            "name": raw["name"],
            "category": eq_cat,
            "subcategory": subcat,
            "cost": cost or {"quantity": 0, "unit": "gp"},
            "cost_cp": _cost_to_cp(cost),
            "weight": float(raw.get("weight") or 0),
            "quantity_bundle": int(raw.get("quantity") or 1),
            "rarity": "Common",
            "is_magic": False,
            "requires_attunement": False,
            "attunement_note": "",
            "consumable": consumable,
            "contents": contents,
            "description": desc,
            "editions": [edition],
            "is_homebrew": False,
        }

    # 1. Process 2014 Equipment
    for raw in eq14:
        idx = raw["index"]
        if _is_weapon_2014(raw):
            weapons_map[idx] = _norm_weapon(raw, "2014")
        elif _is_armor_2014(raw):
            armor_map[idx] = _norm_armor(raw, "2014")
        else:
            items_map[idx] = _norm_gear(raw, "2014")

    # 2. Merge 2024 Equipment (preserves 2024 mastery, updated descriptions/costs)
    for raw in eq24:
        idx = raw["index"]
        cats24 = _cat_indices_2024(raw)
        if "weapons" in cats24:
            w24 = _norm_weapon(raw, "2024")
            if idx in weapons_map:
                existing = weapons_map[idx]
                editions = sorted(list(set(existing["editions"] + ["2024"])))
                # Prefer 2024 mastery and richer desc while keeping fields complete
                merged = {**existing, **{k: v for k, v in w24.items() if v not in (None, "", [], 0)}}
                merged["editions"] = editions
                weapons_map[idx] = merged
            else:
                weapons_map[idx] = w24
        elif "armor" in cats24:
            a24 = _norm_armor(raw, "2024")
            if idx in armor_map:
                existing = armor_map[idx]
                editions = sorted(list(set(existing["editions"] + ["2024"])))
                merged = {**existing, **{k: v for k, v in a24.items() if v not in (None, "", [])}}
                merged["editions"] = editions
                armor_map[idx] = merged
            else:
                armor_map[idx] = a24
        else:
            g24 = _norm_gear(raw, "2024")
            if idx in items_map:
                existing = items_map[idx]
                editions = sorted(list(set(existing["editions"] + ["2024"])))
                # Keep 2014 pack contents if 2024 lacks expanded contents array
                merged = {**existing, **{k: v for k, v in g24.items() if v not in (None, "", [])}}
                if not merged.get("contents") and existing.get("contents"):
                    merged["contents"] = existing["contents"]
                merged["editions"] = editions
                items_map[idx] = merged
            else:
                items_map[idx] = g24

    # 3. Merge 2024 Poisons
    for p in po24:
        idx = f"poison-{p['index']}" if p["index"] in items_map else p["index"]
        cost = _cost_to_dict(p.get("cost")) or {"quantity": 100, "unit": "gp"}
        items_map[idx] = {
            "index": idx,
            "name": p["name"],
            "category": "Poisons",
            "subcategory": f"{str(p.get('type', 'Poison')).title()} Poison",
            "cost": cost,
            "cost_cp": _cost_to_cp(cost),
            "weight": 0.5,
            "quantity_bundle": 1,
            "rarity": "Common",
            "is_magic": False,
            "requires_attunement": False,
            "attunement_note": "",
            "consumable": True,
            "contents": [],
            "description": _join_desc(p.get("description") or p.get("desc")),
            "editions": ["2024"],
            "is_homebrew": False,
        }

    # 4. Process Magic Items (2014 & 2024)
    def _ingest_magic_item(raw: Dict[str, Any], edition: str):
        idx = raw["index"]
        eq_cat_obj = raw.get("equipment_category") or {}
        cat_idx = eq_cat_obj.get("index", "wondrous-items")
        cat_name = eq_cat_obj.get("name", "Wondrous Items")
        rarity = (raw.get("rarity") or {}).get("name", "Uncommon")
        desc = _join_desc(raw.get("desc") or raw.get("description"))
        req_attune, attune_note = _detect_attunement(raw, desc)
        est_gp = RARITY_DEFAULT_GP.get(rarity.lower(), 250)
        cost = _cost_to_dict(raw.get("cost")) or ({"quantity": est_gp, "unit": "gp"} if est_gp > 0 else {"quantity": 0, "unit": "gp"})

        if cat_idx in ("weapon", "weapons"):
            key = f"magic-{idx}" if idx in weapons_map and not weapons_map[idx]["is_magic"] else idx
            if key in weapons_map:
                weapons_map[key]["editions"] = sorted(list(set(weapons_map[key]["editions"] + [edition])))
                if desc and len(desc) > len(weapons_map[key].get("description", "")):
                    weapons_map[key]["description"] = desc
                weapons_map[key]["requires_attunement"] = weapons_map[key]["requires_attunement"] or req_attune
            else:
                # Extract bonus if +1/+2/+3 in name
                m_plus = re.search(r'\+(\d)', raw["name"])
                atk_b = f"+{m_plus.group(1)}" if m_plus else ""
                weapons_map[key] = {
                    "index": key,
                    "name": raw["name"],
                    "category": "Magic Weapon",
                    "weapon_class": "Magic",
                    "range_type": "Melee/Ranged",
                    "damage": "",
                    "damage_dice": "",
                    "damage_type": "",
                    "atk_bonus": atk_b,
                    "two_handed_damage": "",
                    "versatile_dice": "",
                    "range": "Varies",
                    "throw_range": "",
                    "properties": ["Magical"],
                    "mastery": "",
                    "mastery_desc": "",
                    "cost": cost,
                    "cost_cp": _cost_to_cp(cost),
                    "weight": 3.0,
                    "description": desc,
                    "editions": [edition],
                    "is_magic": True,
                    "rarity": rarity,
                    "requires_attunement": req_attune,
                    "attunement_note": attune_note,
                    "is_homebrew": False,
                }
        elif cat_idx == "armor":
            key = f"magic-{idx}" if idx in armor_map and not armor_map[idx]["is_magic"] else idx
            if key in armor_map:
                armor_map[key]["editions"] = sorted(list(set(armor_map[key]["editions"] + [edition])))
                if desc and len(desc) > len(armor_map[key].get("description", "")):
                    armor_map[key]["description"] = desc
                armor_map[key]["requires_attunement"] = armor_map[key]["requires_attunement"] or req_attune
            else:
                m_plus = re.search(r'\+(\d)', raw["name"])
                ac_bonus = int(m_plus.group(1)) if m_plus else 0
                armor_map[key] = {
                    "index": key,
                    "name": raw["name"],
                    "category": "Magic Armor",
                    "ac_base": 0,
                    "ac_bonus": ac_bonus,
                    "dex_bonus": True,
                    "max_dex_bonus": None,
                    "str_minimum": 0,
                    "stealth_disadvantage": False,
                    "don_time": "5-10 minutes",
                    "doff_time": "1-5 minutes",
                    "cost": cost,
                    "cost_cp": _cost_to_cp(cost),
                    "weight": 15.0,
                    "description": desc,
                    "editions": [edition],
                    "is_magic": True,
                    "rarity": rarity,
                    "requires_attunement": req_attune,
                    "attunement_note": attune_note,
                    "is_homebrew": False,
                }
        else:
            key = f"magic-{idx}" if idx in items_map and not items_map[idx]["is_magic"] else idx
            consumable = cat_idx in ("potion", "potions", "scroll", "ammunition")
            if key in items_map:
                items_map[key]["editions"] = sorted(list(set(items_map[key]["editions"] + [edition])))
                if desc and len(desc) > len(items_map[key].get("description", "")):
                    items_map[key]["description"] = desc
                items_map[key]["requires_attunement"] = items_map[key]["requires_attunement"] or req_attune
            else:
                items_map[key] = {
                    "index": key,
                    "name": raw["name"],
                    "category": "Magic Items",
                    "subcategory": cat_name,
                    "cost": cost,
                    "cost_cp": _cost_to_cp(cost),
                    "weight": 1.0,
                    "quantity_bundle": 1,
                    "rarity": rarity,
                    "is_magic": True,
                    "requires_attunement": req_attune,
                    "attunement_note": attune_note,
                    "consumable": consumable,
                    "contents": [],
                    "description": desc,
                    "editions": [edition],
                    "is_homebrew": False,
                }

    for mi in mi14:
        _ingest_magic_item(mi, "2014")
    for mi in mi24:
        _ingest_magic_item(mi, "2024")

    return {
        "weapons": sorted(weapons_map.values(), key=lambda x: (x["is_magic"], x["name"].lower())),
        "armor": sorted(armor_map.values(), key=lambda x: (x["is_magic"], x["name"].lower())),
        "items": sorted(items_map.values(), key=lambda x: (x["is_magic"], x["category"], x["name"].lower())),
        "weapon_masteries": [
            {
                "index": m["index"],
                "name": m["name"],
                "description": _join_desc(m.get("desc") or m.get("description")),
            }
            for m in masteries24
        ],
    }


def _extract_spell_damage_str(raw: Dict[str, Any]) -> tuple[str, str, Dict[str, str], Dict[str, str]]:
    dmg_obj = raw.get("damage")
    if isinstance(dmg_obj, list):
        dmg_obj = dmg_obj[0] if dmg_obj else {}
    if not isinstance(dmg_obj, dict):
        dmg_obj = {}

    dmg_type = (dmg_obj.get("damage_type") or {}).get("name", "")
    slot_map = dmg_obj.get("damage_at_slot_level") or {}
    char_map = dmg_obj.get("damage_at_character_level") or {}
    heal_map = raw.get("heal_at_slot_level") or {}

    base_dice = ""
    if char_map:
        first_k = sorted(char_map.keys(), key=lambda k: int(k) if str(k).isdigit() else 99)[0]
        base_dice = char_map[first_k]
    elif slot_map:
        first_k = sorted(slot_map.keys(), key=lambda k: int(k) if str(k).isdigit() else 99)[0]
        base_dice = slot_map[first_k]
    elif heal_map:
        first_k = sorted(heal_map.keys(), key=lambda k: int(k) if str(k).isdigit() else 99)[0]
        base_dice = f"{heal_map[first_k]} Healing"

    summary = f"{base_dice} {dmg_type}".strip() if base_dice and "Healing" not in base_dice else base_dice
    return summary, dmg_type, slot_map, char_map


def normalize_spells(sp14: List[Dict[str, Any]], sp24: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    spells_map: Dict[str, Dict[str, Any]] = {}

    def _level_label(lvl: int) -> str:
        if lvl == 0:
            return "Cantrip"
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(lvl, "th")
        return f"{lvl}{suffix} Level"

    def _norm_spell(raw: Dict[str, Any], edition: str) -> Dict[str, Any]:
        lvl = int(raw.get("level", 0))
        classes = sorted(list(set(c.get("name", "") for c in raw.get("classes", []) if c.get("name"))))
        if raw["name"].lower() in ARTIFICER_SRD_SPELLS and "Artificer" not in classes:
            classes = sorted(classes + ["Artificer"])

        subclasses = sorted(list(set(sc.get("name", "") for sc in raw.get("subclasses", []) if sc.get("name"))))
        dmg_summary, dmg_type, slot_map, char_map = _extract_spell_damage_str(raw)
        desc = _join_desc(raw.get("description") or raw.get("desc"))
        higher = _join_desc(raw.get("higher_level"))
        dc_type = ((raw.get("dc") or {}).get("dc_type") or {}).get("name", "")

        rng_raw = str(raw.get("range") or "Self")
        comps = list(raw.get("components") or [])
        m_comp = re.search(r'\s+Components?:\s*([VSM,\s]+)$', rng_raw, re.IGNORECASE)
        if m_comp:
            if not comps:
                comps = [c.strip().upper() for c in m_comp.group(1).split(",") if c.strip()]
            rng_raw = rng_raw[:m_comp.start()].strip()

        if not dmg_summary:
            if raw["index"] == "sorcerous-burst":
                dmg_summary, dmg_type = "1d8 Acid/Cold/Fire/Lightning/Poison/Psychic/Thunder", "Varying"
            elif raw["index"] == "shillelagh":
                dmg_summary, dmg_type = "1d8 Force/Bludgeoning", "Bludgeoning"
            else:
                m_d = re.search(r'takes?\s+(\d+d\d+(?:\s*\+\s*\d+)?)\s+(Acid|Bludgeoning|Cold|Fire|Force|Lightning|Necrotic|Piercing|Poison|Psychic|Radiant|Slashing|Thunder)\s+damage', desc, re.IGNORECASE)
                if m_d:
                    dmg_type = m_d.group(2).title()
                    dmg_summary = f"{m_d.group(1)} {dmg_type}"

        if not dc_type:
            m_save = re.search(r'(Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma)\s+saving throw', desc, re.IGNORECASE)
            if m_save:
                dc_type = m_save.group(1).title()[:3].upper()

        return {
            "index": raw["index"],
            "name": raw["name"],
            "level": lvl,
            "level_label": _level_label(lvl),
            "school": (raw.get("school") or {}).get("name", "Evocation"),
            "classes": classes,
            "subclasses": subclasses,
            "casting_time": str(raw.get("casting_time") or "1 action"),
            "range": rng_raw,
            "components": comps,
            "material": str(raw.get("material") or ""),
            "ritual": bool(raw.get("ritual", False)),
            "concentration": bool(raw.get("concentration", False)),
            "duration": str(raw.get("duration") or "Instantaneous"),
            "damage": dmg_summary,
            "damage_type": dmg_type,
            "damage_at_slot_level": slot_map,
            "damage_at_character_level": char_map,
            "heal_at_slot_level": raw.get("heal_at_slot_level") or {},
            "dc_type": dc_type,
            "attack_type": str(raw.get("attack_type") or ""),
            "description": desc,
            "description_2014": desc if edition == "2014" else "",
            "description_2024": desc if edition == "2024" else "",
            "higher_level": higher,
            "editions": [edition],
            "is_homebrew": False,
        }

    for s in sp14:
        spells_map[s["index"]] = _norm_spell(s, "2014")

    for s in sp24:
        idx = s["index"]
        n24 = _norm_spell(s, "2024")
        if idx in spells_map:
            ex = spells_map[idx]
            ex["editions"] = sorted(list(set(ex["editions"] + ["2024"])))
            ex["classes"] = sorted(list(set(ex["classes"] + n24["classes"])))
            ex["description_2024"] = n24["description_2024"]
            # Keep 2024 updated mechanics where provided, preserve 2014 description too
            if n24["damage"] and not ex["damage"]:
                ex["damage"] = n24["damage"]
                ex["damage_type"] = n24["damage_type"]
            if n24["damage_at_slot_level"]:
                ex["damage_at_slot_level"] = n24["damage_at_slot_level"]
            if n24["damage_at_character_level"]:
                ex["damage_at_character_level"] = n24["damage_at_character_level"]
            if n24["heal_at_slot_level"]:
                ex["heal_at_slot_level"] = n24["heal_at_slot_level"]
            if n24["higher_level"]:
                ex["higher_level"] = n24["higher_level"]
            if not ex["description"] and n24["description"]:
                ex["description"] = n24["description"]
        else:
            spells_map[idx] = n24

    return sorted(spells_map.values(), key=lambda x: (x["level"], x["name"].lower()))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print("Fetching 5e SRD 2014 & 2024 JSON from 5e-bits/5e-database...")
    eq14 = fetch_json(f"{BASE_2014}/5e-SRD-Equipment.json")
    eq24 = fetch_json(f"{BASE_2024}/5e-SRD-Equipment.json")
    mi14 = fetch_json(f"{BASE_2014}/5e-SRD-Magic-Items.json")
    mi24 = fetch_json(f"{BASE_2024}/5e-SRD-Magic-Items.json")
    po24 = fetch_json(f"{BASE_2024}/5e-SRD-Poisons.json")
    wm24 = fetch_json(f"{BASE_2024}/5e-SRD-Weapon-Mastery-Properties.json")
    sp14 = fetch_json(f"{BASE_2014}/5e-SRD-Spells.json")
    sp24 = fetch_json(f"{BASE_2024}/5e-SRD-Spells.json")

    gear_bundle = normalize_equipment_and_magic(eq14, eq24, mi14, mi24, po24, wm24)
    spells = normalize_spells(sp14, sp24)

    outputs = {
        "srd_weapons.json": gear_bundle["weapons"],
        "srd_armor.json": gear_bundle["armor"],
        "srd_items.json": gear_bundle["items"],
        "srd_weapon_masteries.json": gear_bundle["weapon_masteries"],
        "srd_spells.json": spells,
    }
    for fname, payload in outputs.items():
        path = os.path.join(OUT_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"Wrote {len(payload):>4} entries -> {path}")


if __name__ == "__main__":
    main()
