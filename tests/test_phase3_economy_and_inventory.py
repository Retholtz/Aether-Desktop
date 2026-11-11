"""
tests/test_phase3_economy_and_inventory.py - Phase 3 Economy, Temple Revival, Encumbrance & Loot Verification
"""
import unittest
import os
import shutil

from core.economy.commerce import CurrencyWallet, CommerceEngine
from core.economy.temple_services import TempleServicesEngine
from core.scene.corpse_manager import CorpseManager
from core.scene.scene_manager import SceneManager
from core.inventory.encumbrance import calculate_character_encumbrance
from core.rulesets.dnd5e_ruleset import DnD5eRuleset


class TestPhase3EconomyAndInventory(unittest.TestCase):
    def setUp(self):
        self.test_dir = "tests/test_phase3_temp"
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)
        os.makedirs(self.test_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_currency_deduction_and_change(self):
        """Verify multi-coin deduction with exact change calculation."""
        wallet = {"pp": 2, "gp": 5, "ep": 0, "sp": 0, "cp": 0}  # Total 25 GP
        success, msg, new_wallet = CurrencyWallet.deduct_currency(wallet, cost_gp=12.5)
        self.assertTrue(success)
        # Remaining: 12.5 GP = 1 PP (10 GP) + 2 GP + 1 EP (0.5 GP)
        self.assertEqual(CurrencyWallet.to_copper(new_wallet), 1250)

    def test_equipment_pack_unpacking(self):
        """Verify explorer's pack auto-unpacks into individual gear rows."""
        inv = []
        wallet = {"pp": 0, "gp": 50, "ep": 0, "sp": 0, "cp": 0}
        item = {"name": "Explorer's Pack", "qty": "1", "weight": "59 lb."}
        success, msg, new_wallet, new_inv = CommerceEngine.execute_purchase(wallet, 10.0, item, inv)
        self.assertTrue(success)
        self.assertGreaterEqual(len(new_inv), 8)
        self.assertTrue(any(i["name"] == "Bedroll" for i in new_inv))

    def test_temple_revival_tier_gating(self):
        """Verify low-tier settlements reject high-level resurrection."""
        cm = CorpseManager()
        corpse = cm.spawn_corpse("p1", "Hero", "loc_hamlet", [], 1)
        party_wallet = {"pp": 0, "gp": 2000, "ep": 0, "sp": 0, "cp": 0}
        char = {"id": "p1", "name": "Hero", "conditions": ["Dead"]}

        # Hamlet with max_spell_tier = 1 trying to cast Raise Dead (Tier 5)
        success, msg, _, _ = TempleServicesEngine.request_revival(
            service_name="raise_dead",
            settlement_max_spell_tier=1,
            party_wallet=party_wallet,
            corpse_id=corpse.corpse_id,
            corpse_manager=cm,
            target_character=char
        )
        self.assertFalse(success)
        self.assertIn("requires spell tier 5", msg)

    def test_temple_revival_execution_and_penalty(self):
        """Verify successful resurrection deducts 1,250 GP, revives char, and sets -4 debuff."""
        cm = CorpseManager()
        corpse = cm.spawn_corpse("p1", "Hero", "loc_city", [], 1)
        party_wallet = {"pp": 0, "gp": 2000, "ep": 0, "sp": 0, "cp": 0}
        char = {"id": "p1", "name": "Hero", "conditions": ["Dead"]}

        success, msg, new_wallet, revived_char = TempleServicesEngine.request_revival(
            service_name="raise_dead",
            settlement_max_spell_tier=5,
            party_wallet=party_wallet,
            corpse_id=corpse.corpse_id,
            corpse_manager=cm,
            target_character=char
        )
        self.assertTrue(success)
        self.assertEqual(CurrencyWallet.to_copper(new_wallet), 75000)  # 2000 - 1250 = 750 GP
        self.assertEqual(revived_char["vitals"]["health"]["current"], 1)
        self.assertNotIn("Dead", revived_char["conditions"])
        self.assertIn("Resurrection Sickness (-4)", revived_char["conditions"])
        self.assertNotIn(corpse.corpse_id, cm.corpses)  # Consumed

    def test_atonement_clears_oath_strained(self):
        """Verify atonement requires Tier 3 clergy, 250 GP, and clears Oath_Strained."""
        char = {"name": "Paladin", "conditions": ["Oath_Strained"]}
        wallet = {"gp": 300}
        success, msg, new_wallet, updated_char = TempleServicesEngine.request_atonement(
            settlement_max_spell_tier=3,
            party_wallet=wallet,
            character=char
        )
        self.assertTrue(success)
        self.assertNotIn("Oath_Strained", updated_char["conditions"])
        self.assertEqual(CurrencyWallet.to_copper(new_wallet), 5000)  # 300 - 250 = 50 GP

    def test_encumbrance_speed_penalty(self):
        """Verify exceeding carrying capacity (STR * 15) triggers a speed penalty."""
        inventory = [
            {"name": "Plate Armor", "weight": "65 lb.", "qty": "1"},
            {"name": "Greatsword", "weight": "6 lb.", "qty": "1"},
            {"name": "Heavy Iron Anvil", "weight": "100 lb.", "qty": "1"}
        ]  # Total = 171 lb.
        # STR = 10 -> Capacity = 150 lb, Push/Drag/Lift = 300 lb
        res = calculate_character_encumbrance(strength=10, inventory=inventory)
        self.assertEqual(res["weight_carried_lb"], 171.0)
        self.assertTrue(res["is_encumbered"])
        self.assertFalse(res["is_overloaded"])
        self.assertEqual(res["speed_penalty_ft"], 20)

    def test_encumbrance_overloaded_immobilized(self):
        """Verify exceeding push/drag/lift (STR * 30) immobilizes character."""
        inventory = [
            {"name": "Giant Boulder", "weight": "350 lb.", "qty": "1"}
        ]
        # STR = 10 -> Capacity = 150 lb, Push/Drag/Lift = 300 lb
        res = calculate_character_encumbrance(strength=10, inventory=inventory)
        self.assertEqual(res["weight_carried_lb"], 350.0)
        self.assertTrue(res["is_encumbered"])
        self.assertTrue(res["is_overloaded"])
        self.assertEqual(res["speed_penalty_ft"], 999)

    def test_resurrection_sickness_long_rest_decay(self):
        """Verify Resurrection Sickness decays +1 per Long Rest until cleared."""
        ruleset = DnD5eRuleset()
        character = {
            "name": "Lazarus",
            "conditions": ["Resurrection Sickness (-4)"],
            "vitals": {"health": {"current": 1, "max": 20}},
            "native_system_data": {
                "hit_points": {"current": 1, "max": 20, "temp": 0},
                "ability_scores": {"Constitution": 10}
            }
        }

        # Rest 1: -4 -> -3
        ruleset.execute_rest(character, rest_type="long")
        self.assertIn("Resurrection Sickness (-3)", character["conditions"])

        # Rest 2: -3 -> -2
        ruleset.execute_rest(character, rest_type="long")
        self.assertIn("Resurrection Sickness (-2)", character["conditions"])

        # Rest 3: -2 -> -1
        ruleset.execute_rest(character, rest_type="long")
        self.assertIn("Resurrection Sickness (-1)", character["conditions"])

        # Rest 4: -1 -> Cleared
        ruleset.execute_rest(character, rest_type="long")
        self.assertNotIn("Resurrection Sickness (-1)", character["conditions"])
        self.assertFalse(any("Resurrection Sickness" in c for c in character["conditions"]))

    def test_adversary_loot_inheritance(self):
        """Verify slain adversary loot is preserved in a CorpseContainer within the scene."""
        scene = SceneManager(campaign_id="test_loot_camp", base_data_dir=self.test_dir)
        corpse_mgr = CorpseManager()

        goblin = scene.spawn_adversary(
            name="Goblin Hoarder",
            hp=10,
            ac=12,
            inventory=[{"name": "Silver Ring", "qty": "1", "weight": "0.1 lb."}],
            swallowed_items=[{"name": "Glowing Key", "qty": "1", "weight": "0.2 lb."}]
        )
        self.assertEqual(len(scene.state["adversaries"]), 1)

        corpse = scene.handle_adversary_slain(
            adversary=goblin,
            location_id="dungeon_cellar",
            current_round=3,
            corpse_manager=corpse_mgr
        )

        self.assertIn("Goblin Hoarder", corpse.name)
        self.assertEqual(len(corpse.inventory), 2)
        names = [item["name"] for item in corpse.inventory]
        self.assertIn("Silver Ring", names)
        self.assertIn("Glowing Key", names)

        # Removed from active adversary roster
        self.assertEqual(len(scene.state["adversaries"]), 0)
        # Added to active scene containers
        self.assertIn("dungeon_cellar", scene.active_containers)
        self.assertEqual(len(scene.active_containers["dungeon_cellar"]), 1)

    def test_execute_sale(self):
        """Verify selling items credits the seller's wallet with the markdown value and removes item."""
        wallet = {"pp": 0, "gp": 10, "ep": 0, "sp": 0, "cp": 0}
        inventory = [
            {"name": "Ruby Gem", "qty": "1", "weight": "0.1 lb."},
            {"name": "Arrows", "qty": "20", "weight": "1 lb."}
        ]

        # Sell Ruby Gem (Value 100 GP, 50% markup -> 50 GP)
        success, msg, new_wallet, new_inv = CommerceEngine.execute_sale(
            seller_wallet=wallet,
            item_value_gp=100.0,
            item_name="Ruby Gem",
            seller_inventory=inventory,
            markup_multiplier=0.5
        )
        self.assertTrue(success)
        self.assertEqual(CurrencyWallet.to_copper(new_wallet), 6000)  # 10 + 50 = 60 GP
        self.assertEqual(len(new_inv), 1)
        self.assertNotIn("Ruby Gem", [i["name"] for i in new_inv])

        # Sell 1 Arrow from stack of 20
        success2, msg2, new_wallet2, new_inv2 = CommerceEngine.execute_sale(
            seller_wallet=new_wallet,
            item_value_gp=1.0,
            item_name="Arrows",
            seller_inventory=new_inv,
            markup_multiplier=0.5
        )
        self.assertTrue(success2)
        self.assertEqual(new_inv2[0]["qty"], "19")


if __name__ == "__main__":
    unittest.main()
