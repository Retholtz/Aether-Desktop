"""
Unit tests for Compendium Spell Class List Validation & Aether DM Story Pricing in Merchant Shops.
"""

import unittest
from unittest.mock import MagicMock
from core.compendiums.dnd_spells import validate_character_spell_assignment
from core.ttrpg_service import TTRPGService
from core.gui_bridge import GuiBridge
from tools.ttrpg_tools import register_ttrpg_tools


class TestSpellClassValidation(unittest.TestCase):
    def test_primary_caster_allowed(self):
        char = {
            "name": "Eldrin",
            "archetype": "Wizard",
            "class_levels": {"Wizard": 3},
            "ruleset": "dnd5e"
        }
        res = validate_character_spell_assignment(char, "magic-missile", edition="2024")
        self.assertTrue(res["can_learn"])
        self.assertFalse(res["is_out_of_class"])
        self.assertEqual(res["matched_via"], "class_spell_list")

    def test_primary_caster_disallowed(self):
        char = {
            "name": "Barian",
            "archetype": "Bard",
            "class_levels": {"Bard": 3},
            "ruleset": "dnd5e"
        }
        # Sacred Flame is a Cleric-only spell
        res = validate_character_spell_assignment(char, "sacred-flame", edition="2024")
        self.assertFalse(res["can_learn"])
        self.assertTrue(res["is_out_of_class"])
        self.assertIn("Cleric", res["allowed_spell_classes"])
        self.assertIn("it is on the [Cleric] spell list", res["reason"])

    def test_bypass_class_check(self):
        char = {
            "name": "Barian",
            "archetype": "Bard",
            "class_levels": {"Bard": 3},
            "ruleset": "dnd5e"
        }
        res = validate_character_spell_assignment(char, "sacred-flame", edition="2024", bypass_class_check=True)
        self.assertTrue(res["can_learn"])
        self.assertEqual(res["matched_via"], "bypass")

    def test_multiclass_character(self):
        char = {
            "name": "Lyra",
            "archetype": "Bard / Cleric",
            "class_levels": {"Bard": 3, "Cleric": 1},
            "ruleset": "dnd5e"
        }
        # Bard spell
        res_bard = validate_character_spell_assignment(char, "vicious-mockery", edition="2024")
        self.assertTrue(res_bard["can_learn"])
        self.assertEqual(res_bard["matched_via"], "class_spell_list")

        # Cleric spell
        res_cleric = validate_character_spell_assignment(char, "sacred-flame", edition="2024")
        self.assertTrue(res_cleric["can_learn"])
        self.assertEqual(res_cleric["matched_via"], "class_spell_list")

    def test_third_caster_eldritch_knight(self):
        char = {
            "name": "Valen",
            "archetype": "Fighter (Eldritch Knight)",
            "class_levels": {"Fighter": 4},
            "subclass": "Eldritch Knight",
            "ruleset": "dnd5e"
        }
        # Shield is a Wizard/Sorcerer spell
        res = validate_character_spell_assignment(char, "shield", edition="2024")
        self.assertTrue(res["can_learn"])
        self.assertEqual(res["matched_via"], "subclass_spell_list")

    def test_non_caster_martial(self):
        char = {
            "name": "Garrick",
            "archetype": "Fighter (Champion)",
            "class_levels": {"Fighter": 4},
            "subclass": "Champion",
            "ruleset": "dnd5e"
        }
        res = validate_character_spell_assignment(char, "shield", edition="2024")
        self.assertFalse(res["can_learn"])
        self.assertIn("non-spellcaster", res["reason"].lower())

    def test_subclass_bonus_spells(self):
        # Light domain gets Fireball
        light_cleric = {
            "name": "Dawn",
            "archetype": "Cleric",
            "class_levels": {"Cleric": 5},
            "subclass": "Light Domain",
            "ruleset": "dnd5e"
        }
        res_light = validate_character_spell_assignment(light_cleric, "fireball", edition="2024")
        self.assertTrue(res_light["can_learn"])
        self.assertEqual(res_light["matched_via"], "subclass_bonus")

        # Life domain does NOT get Fireball
        life_cleric = {
            "name": "Mercy",
            "archetype": "Cleric",
            "class_levels": {"Cleric": 5},
            "subclass": "Life Domain",
            "ruleset": "dnd5e"
        }
        res_life = validate_character_spell_assignment(life_cleric, "fireball", edition="2024")
        self.assertFalse(res_life["can_learn"])

    def test_bard_magical_secrets(self):
        # High level bard (L10) has Magical Secrets
        bard10 = {
            "name": "Maestro",
            "archetype": "Bard",
            "class_levels": {"Bard": 10},
            "ruleset": "dnd5e"
        }
        res = validate_character_spell_assignment(bard10, "fireball", edition="2024")
        self.assertTrue(res["can_learn"])
        self.assertTrue(res["is_magical_secrets"])
        self.assertEqual(res["matched_via"], "magical_secrets")

        # Low level bard (L3) does NOT have Magical Secrets
        bard3 = {
            "name": "Novice",
            "archetype": "Bard",
            "class_levels": {"Bard": 3},
            "ruleset": "dnd5e"
        }
        res3 = validate_character_spell_assignment(bard3, "fireball", edition="2024")
        self.assertFalse(res3["can_learn"])

    def test_non_dnd_ruleset_bypassed(self):
        coc_char = {
            "name": "Harvey",
            "archetype": "Investigator",
            "ruleset": "coc"
        }
        res = validate_character_spell_assignment(coc_char, "fireball", edition="2024")
        self.assertTrue(res["can_learn"])
        self.assertEqual(res["matched_via"], "non_dnd")


class TestTTRPGServiceAndBridgeSpellGrant(unittest.TestCase):
    def setUp(self):
        self.ttrpg = TTRPGService()
        self.ttrpg.create_campaign("Test Spell Grant", system_preset="dnd_5e")
        self.ttrpg.add_character({
            "name": "Minstrel",
            "archetype": "Bard",
            "class_levels": {"Bard": 2},
            "spells": [],
            "spell_slots": {"1": {"max": 3, "current": 3}}
        })

    def test_grant_out_of_class_spell_rejected_without_bypass(self):
        res = self.ttrpg.add_compendium_entry_to_character(
            character_name="Minstrel",
            category="spells",
            index_or_name="sacred-flame",
            edition="2024",
            bypass_class_check=False
        )
        self.assertEqual(res["status"], "error")
        self.assertEqual(res.get("code"), "invalid_spell_class")
        self.assertIn("Cannot assign 'Sacred Flame'", res["message"])

        # Ensure spell was NOT added
        _, char = self.ttrpg._find_character("Minstrel")
        self.assertEqual(len(char.get("spells", [])), 0)

    def test_grant_out_of_class_spell_allowed_with_bypass(self):
        res = self.ttrpg.add_compendium_entry_to_character(
            character_name="Minstrel",
            category="spells",
            index_or_name="sacred-flame",
            edition="2024",
            bypass_class_check=True
        )
        self.assertEqual(res["status"], "success")

        # Ensure spell WAS added
        _, char = self.ttrpg._find_character("Minstrel")
        self.assertTrue(any(s["name"] == "Sacred Flame" for s in char.get("spells", [])))

    def test_grant_valid_class_spell(self):
        res = self.ttrpg.add_compendium_entry_to_character(
            character_name="Minstrel",
            category="spells",
            index_or_name="vicious-mockery",
            edition="2024",
            bypass_class_check=False
        )
        self.assertEqual(res["status"], "success")
        _, char = self.ttrpg._find_character("Minstrel")
        self.assertTrue(any(s["name"] == "Vicious Mockery" for s in char.get("spells", [])))

    def test_gui_bridge_check_spell_class_eligibility(self):
        bridge = GuiBridge()
        bridge.ttrpg_service = self.ttrpg

        check_invalid = bridge.check_spell_class_eligibility("Minstrel", "sacred-flame", "2024")
        self.assertEqual(check_invalid["status"], "success")
        self.assertFalse(check_invalid["can_learn"])

        check_valid = bridge.check_spell_class_eligibility("Minstrel", "vicious-mockery", "2024")
        self.assertEqual(check_valid["status"], "success")
        self.assertTrue(check_valid["can_learn"])


class TestAetherShopStoryPricing(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ttrpg = TTRPGService()
        self.ttrpg.create_campaign("Test Shop Story", system_preset="dnd_5e")

        self.dispatcher = MagicMock()
        self.events = []
        register_ttrpg_tools(self.dispatcher, self.ttrpg, lambda ev, data: self.events.append((ev, data)))
        self.tools = {call.kwargs["name"]: call.kwargs["handler"] for call in self.dispatcher.register_tool.call_args_list}

    async def test_open_merchant_shop_sets_story_multiplier(self):
        open_merchant_shop = self.tools["open_merchant_shop"]

        # DM opens shop in a remote frontier camp with 1.35x markup
        res = await open_merchant_shop(
            shop_name="Jungle Frontier Supply Post",
            location_name="Filip's Outpost",
            price_multiplier=1.35,
            specialty="gear",
            description="Remote supply outpost in the dense jungle. Prices are marked up 35% due to hazardous transport."
        )

        self.assertEqual(res["status"], "success")
        active_shop = self.ttrpg.active_state.get("active_shop", {})
        self.assertEqual(active_shop.get("shop_name"), "Jungle Frontier Supply Post")
        self.assertEqual(active_shop.get("price_multiplier"), 1.35)
        self.assertEqual(active_shop.get("location_name"), "Filip's Outpost")

        # Emitted shop_opened event
        shop_events = [data for ev, data in self.events if ev == "shop_opened"]
        self.assertTrue(len(shop_events) > 0)
        self.assertEqual(shop_events[-1].get("price_multiplier"), 1.35)

    async def test_open_merchant_shop_haggling_discount_update(self):
        open_merchant_shop = self.tools["open_merchant_shop"]

        # Initially 1.35x
        await open_merchant_shop(
            shop_name="Filip's Post",
            price_multiplier=1.35
        )

        # Successful persuasion check -> DM reduces to 1.10x
        await open_merchant_shop(
            shop_name="Filip's Post",
            price_multiplier=1.10,
            description="Filip smiles after your persuasive argument and knocks the rate down to 1.10x."
        )

        active_shop = self.ttrpg.active_state.get("active_shop", {})
        self.assertEqual(active_shop.get("price_multiplier"), 1.10)
        self.assertIn("1.10x", active_shop.get("description", ""))


if __name__ == "__main__":
    unittest.main()
