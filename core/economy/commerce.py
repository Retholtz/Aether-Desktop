"""
core/economy/commerce.py - Multi-Denomination Currency & Transaction Engine
"""
import re
from typing import Dict, Any, List, Tuple, Optional, Union
from core.logger import get_logger

logger = get_logger("CommerceEngine")


class CommerceResult(tuple):
    """
    Tuple result (success, msg, new_wallet, new_inventory) supporting both
    tuple unpacking and dict-style key access.
    """
    def __new__(cls, success: bool, msg: str, new_wallet: Dict[str, int], new_inventory: List[Dict[str, Any]]):
        return super().__new__(cls, (success, msg, new_wallet, new_inventory))

    def __init__(self, success: bool, msg: str, new_wallet: Dict[str, int], new_inventory: List[Dict[str, Any]]):
        self.success = success
        self.msg = msg
        self.new_wallet = new_wallet
        self.new_inventory = new_inventory

    def __getitem__(self, item: Union[int, str]):
        if isinstance(item, str):
            if item == "success":
                return self.success
            if item in ("msg", "message"):
                return self.msg
            if item in ("new_wallet", "wallet"):
                return self.new_wallet
            if item in ("new_inventory", "inventory"):
                return self.new_inventory
            raise KeyError(f"Unknown key '{item}' in CommerceResult")
        return super().__getitem__(item)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


class CurrencyWallet:
    RATIOS = {"pp": 1000, "gp": 100, "ep": 50, "sp": 10, "cp": 1}

    @classmethod
    def to_copper(cls, wallet: Dict[str, int]) -> int:
        return sum(wallet.get(coin, 0) * mult for coin, mult in cls.RATIOS.items())

    @classmethod
    def from_copper(cls, total_cp: int) -> Dict[str, int]:
        rem = total_cp
        pp = rem // 1000; rem %= 1000
        gp = rem // 100; rem %= 100
        ep = rem // 50; rem %= 50
        sp = rem // 10; rem %= 10
        cp = rem
        return {"cp": cp, "sp": sp, "ep": ep, "gp": gp, "pp": pp}

    @classmethod
    def deduct_currency(cls, wallet: Dict[str, int], cost_gp: float) -> Tuple[bool, str, Dict[str, int]]:
        cost_cp = int(round(cost_gp * 100))
        total_cp = cls.to_copper(wallet)

        if total_cp < cost_cp:
            return False, f"Insufficient funds: need {cost_gp:.2f} GP, have {total_cp / 100:.2f} GP equivalent.", dict(wallet)

        new_total_cp = total_cp - cost_cp
        return True, f"Successfully paid {cost_gp:.2f} GP.", cls.from_copper(new_total_cp)

    @classmethod
    def add_currency(cls, wallet: Dict[str, int], gain_gp: float) -> Tuple[bool, str, Dict[str, int]]:
        gain_cp = int(round(gain_gp * 100))
        total_cp = cls.to_copper(wallet) + gain_cp
        return True, f"Successfully received {gain_gp:.2f} GP.", cls.from_copper(total_cp)


class CommerceEngine:
    EQUIPMENT_PACKS = {
        "explorer's pack": [
            {"name": "Backpack", "qty": "1", "weight": "5 lb."},
            {"name": "Bedroll", "qty": "1", "weight": "7 lb."},
            {"name": "Mess Kit", "qty": "1", "weight": "1 lb."},
            {"name": "Tinderbox", "qty": "1", "weight": "1 lb."},
            {"name": "Torches", "qty": "10", "weight": "10 lb."},
            {"name": "Rations", "qty": "10", "weight": "20 lb."},
            {"name": "Waterskin", "qty": "1", "weight": "5 lb."},
            {"name": "Hempen Rope (50 ft.)", "qty": "1", "weight": "10 lb."}
        ],
        "priest's pack": [
            {"name": "Backpack", "qty": "1", "weight": "5 lb."},
            {"name": "Blanket", "qty": "1", "weight": "3 lb."},
            {"name": "Candles", "qty": "10", "weight": "--"},
            {"name": "Tinderbox", "qty": "1", "weight": "1 lb."},
            {"name": "Alms Box", "qty": "1", "weight": "2 lb."},
            {"name": "Block of Incense", "qty": "2", "weight": "--"},
            {"name": "Censer", "qty": "1", "weight": "2 lb."},
            {"name": "Vestments", "qty": "1", "weight": "4 lb."},
            {"name": "Rations", "qty": "2", "weight": "4 lb."},
            {"name": "Waterskin", "qty": "1", "weight": "5 lb."}
        ],
        "dungeoneer's pack": [
            {"name": "Backpack", "qty": "1", "weight": "5 lb."},
            {"name": "Crowbar", "qty": "1", "weight": "5 lb."},
            {"name": "Hammer", "qty": "1", "weight": "3 lb."},
            {"name": "Pitons", "qty": "10", "weight": "2.5 lb."},
            {"name": "Torches", "qty": "10", "weight": "10 lb."},
            {"name": "Tinderbox", "qty": "1", "weight": "1 lb."},
            {"name": "Rations", "qty": "10", "weight": "20 lb."},
            {"name": "Waterskin", "qty": "1", "weight": "5 lb."},
            {"name": "Hempen Rope (50 ft.)", "qty": "1", "weight": "10 lb."}
        ],
        "burglar's pack": [
            {"name": "Backpack", "qty": "1", "weight": "5 lb."},
            {"name": "Ball Bearings (bag of 1,000)", "qty": "1", "weight": "2 lb."},
            {"name": "String (10 feet)", "qty": "1", "weight": "--"},
            {"name": "Bell", "qty": "1", "weight": "--"},
            {"name": "Candles", "qty": "5", "weight": "--"},
            {"name": "Crowbar", "qty": "1", "weight": "5 lb."},
            {"name": "Hammer", "qty": "1", "weight": "3 lb."},
            {"name": "Pitons", "qty": "10", "weight": "2.5 lb."},
            {"name": "Hooded Lantern", "qty": "1", "weight": "2 lb."},
            {"name": "Oil (flask)", "qty": "2", "weight": "2 lb."},
            {"name": "Rations", "qty": "5", "weight": "10 lb."},
            {"name": "Tinderbox", "qty": "1", "weight": "1 lb."},
            {"name": "Waterskin", "qty": "1", "weight": "5 lb."}
        ]
    }

    @classmethod
    def unpack_equipment_pack(cls, pack_name: str) -> List[Dict[str, Any]]:
        """Unpacks bundled kits into discrete inventory item copies."""
        key = pack_name.strip().lower()
        pack = cls.EQUIPMENT_PACKS.get(key, [])
        return [dict(i) for i in pack]

    @classmethod
    def execute_purchase(
        cls,
        buyer_wallet: Dict[str, int],
        cost_gp: float,
        item: Dict[str, Any],
        buyer_inventory: List[Dict[str, Any]]
    ) -> CommerceResult:
        success, msg, new_wallet = CurrencyWallet.deduct_currency(buyer_wallet, cost_gp)
        if not success:
            return CommerceResult(False, msg, dict(buyer_wallet), list(buyer_inventory))

        item_name_lower = item.get("name", "").strip().lower()
        new_inv = [dict(i) for i in buyer_inventory]

        if item_name_lower in cls.EQUIPMENT_PACKS:
            unpacked = cls.unpack_equipment_pack(item_name_lower)
            new_inv.extend(unpacked)
            msg += f" Unpacked {item.get('name')} into inventory."
        else:
            # Check stackability
            existing = next((i for i in new_inv if i.get("name", "").lower() == item_name_lower), None)
            if existing and str(existing.get("qty", "1")).isdigit() and str(item.get("qty", "1")).isdigit():
                existing["qty"] = str(int(existing["qty"]) + int(item.get("qty", "1")))
            else:
                new_inv.append(dict(item))

        logger.info("Purchase completed: %s for %s GP. New liquid: %s CP", item.get("name"), cost_gp, CurrencyWallet.to_copper(new_wallet))
        return CommerceResult(True, msg, new_wallet, new_inv)

    @classmethod
    def execute_sale(
        cls,
        seller_wallet: Dict[str, int],
        item_value_gp: float,
        item_name: str,
        seller_inventory: List[Dict[str, Any]],
        markup_multiplier: float = 0.5
    ) -> CommerceResult:
        """
        Sells an item from seller_inventory at item_value_gp * markup_multiplier,
        crediting proceeds to seller_wallet.
        """
        item_name_lower = item_name.strip().lower()
        new_inv = [dict(i) for i in seller_inventory]

        # Find item in inventory
        target_idx = None
        for idx, itm in enumerate(new_inv):
            if itm.get("name", "").strip().lower() == item_name_lower:
                target_idx = idx
                break

        if target_idx is None:
            return CommerceResult(False, f"Item '{item_name}' not found in seller inventory.", dict(seller_wallet), new_inv)

        # Handle quantity decrement or removal
        target_item = new_inv[target_idx]
        cur_qty = int(target_item.get("qty", 1)) if str(target_item.get("qty", 1)).isdigit() else 1
        if cur_qty > 1:
            target_item["qty"] = str(cur_qty - 1)
        else:
            new_inv.pop(target_idx)

        gain_gp = round(item_value_gp * markup_multiplier, 2)
        _, msg, new_wallet = CurrencyWallet.add_currency(seller_wallet, gain_gp)
        full_msg = f"Sold {item_name} for {gain_gp:.2f} GP. {msg}"
        logger.info(full_msg)
        return CommerceResult(True, full_msg, new_wallet, new_inv)
