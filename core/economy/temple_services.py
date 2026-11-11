"""
core/economy/temple_services.py - Clerical Resurrection & Atonement Engine
"""
from typing import Dict, Any, Tuple, Optional
from core.economy.commerce import CurrencyWallet
from core.scene.corpse_manager import CorpseManager
from core.logger import get_logger

logger = get_logger("TempleServices")


class TempleServicesEngine:
    SERVICES = {
        "revivify": {"min_tier": 3, "total_cost_gp": 500.0, "time_type": "rounds"},
        "raise_dead": {"min_tier": 5, "total_cost_gp": 1250.0, "time_type": "days"},
        "resurrection": {"min_tier": 7, "total_cost_gp": 2500.0, "time_type": "years"},
        "atonement": {"min_tier": 3, "total_cost_gp": 250.0, "time_type": "none"}
    }

    @classmethod
    def request_revival(
        cls,
        service_name: str,
        settlement_max_spell_tier: int,
        party_wallet: Dict[str, int],
        corpse_id: str,
        corpse_manager: CorpseManager,
        target_character: Dict[str, Any]
    ) -> Tuple[bool, str, Dict[str, int], Dict[str, Any]]:
        s_key = service_name.lower().replace(" ", "_")
        if s_key not in cls.SERVICES or s_key == "atonement":
            return False, f"Unknown revival service: '{service_name}'", party_wallet, target_character

        rule = cls.SERVICES[s_key]

        # 1. Geographic Spell Tier Gating
        if settlement_max_spell_tier < rule["min_tier"]:
            return False, f"Settlement lacks high clergy. {service_name} requires spell tier {rule['min_tier']} (settlement max: {settlement_max_spell_tier}).", party_wallet, target_character

        # 2. Corpse Expiry & Validity
        if corpse_id not in corpse_manager.corpses:
            return False, f"Corpse '{corpse_id}' not found in active spatial registry.", party_wallet, target_character

        corpse = corpse_manager.corpses[corpse_id]
        if s_key == "revivify" and corpse.revivify_rounds_left <= 0 and not corpse.gentle_repose_active:
            return False, "Target has been dead longer than 10 rounds (1 minute). Revivify failed.", party_wallet, target_character
        if s_key == "raise_dead" and corpse.raise_dead_days_left <= 0 and not corpse.gentle_repose_active:
            return False, "Target has been dead longer than 10 days. Raise Dead failed.", party_wallet, target_character
        if s_key == "resurrection" and corpse.resurrection_years_left <= 0 and not corpse.gentle_repose_active:
            return False, "Target has been dead longer than 100 years. Resurrection failed.", party_wallet, target_character

        # 3. Currency & Diamond Deduction
        success, msg, new_wallet = CurrencyWallet.deduct_currency(party_wallet, rule["total_cost_gp"])
        if not success:
            return False, msg, party_wallet, target_character

        # 4. State Transition & Corpse Consumption
        del corpse_manager.corpses[corpse_id]
        char = dict(target_character)
        char.setdefault("vitals", {}).setdefault("health", {})["current"] = 1
        char.setdefault("native_system_data", {}).setdefault("hit_points", {})["current"] = 1
        char["status"] = "active"

        conditions = char.setdefault("conditions", [])
        for c_to_remove in ("Dead", "Unconscious", "Dying"):
            while c_to_remove in conditions:
                conditions.remove(c_to_remove)

        if "death_saves" in char:
            char["death_saves"] = {"successes": 0, "failures": 0, "status": "revived"}

        # Apply -4 Resurrection Sickness debuff
        if s_key in ("raise_dead", "resurrection"):
            conditions.append("Resurrection Sickness (-4)")

        logger.info("%s revived via %s. Paid %s GP.", char.get("name"), service_name, rule["total_cost_gp"])
        return True, f"{char.get('name')} successfully raised by the temple clergy at 1 HP. {msg}", new_wallet, char

    @classmethod
    def request_atonement(
        cls,
        settlement_max_spell_tier: int,
        party_wallet: Dict[str, int],
        character: Dict[str, Any]
    ) -> Tuple[bool, str, Dict[str, int], Dict[str, Any]]:
        rule = cls.SERVICES["atonement"]
        if settlement_max_spell_tier < rule["min_tier"]:
            return False, f"Atonement requires spell tier {rule['min_tier']} clergy (local tier: {settlement_max_spell_tier}).", party_wallet, character

        char = dict(character)
        conditions = char.setdefault("conditions", [])
        if "Oath_Strained" not in conditions:
            return False, f"{char.get('name')}'s oath is not currently strained.", party_wallet, character

        success, msg, new_wallet = CurrencyWallet.deduct_currency(party_wallet, rule["total_cost_gp"])
        if not success:
            return False, msg, party_wallet, character

        while "Oath_Strained" in conditions:
            conditions.remove("Oath_Strained")

        # Restore Channel Divinity if present
        meters = char.get("native_system_data", {}).get("custom_meters", {})
        if "channel_divinity" in meters:
            meters["channel_divinity"]["current"] = meters["channel_divinity"].get("max", 1)

        logger.info("%s has atoned. Oath_Strained condition lifted.", char.get("name"))
        return True, f"Through prayer, penance, and offering ({msg}), {char.get('name')}'s oath has been restored.", new_wallet, char
