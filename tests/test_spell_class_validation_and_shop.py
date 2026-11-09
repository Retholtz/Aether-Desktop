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


class TestSpellPreparationAndLegality(unittest.TestCase):
    def setUp(self):
        self.ttrpg = TTRPGService()
        self.ttrpg.create_campaign("Test Spell Legality", system_preset="dnd_5e")
        self.ttrpg.add_character({
            "name": "Gale",
            "archetype": "Wizard",
            "class_levels": {"Wizard": 1},
            "attributes": {"Intelligence": 16},
            "spells": [
                {"name": "Fire Bolt", "level": 0, "is_cantrip": True, "prepared": True},
                {"name": "Mage Armor", "level": 1, "is_cantrip": False, "prepared": True},
                {"name": "Magic Missile", "level": 1, "is_cantrip": False, "prepared": True},
                {"name": "Shield", "level": 1, "is_cantrip": False, "prepared": True},
            ]
        })

    def test_check_spell_legality_within_limit(self):
        from core.compendiums.dnd_advancement import check_character_spell_legality
        _, char = self.ttrpg._find_character("Gale")
        leg = check_character_spell_legality(char, "2024")
        self.assertTrue(leg["is_legal"])
        self.assertFalse(leg["is_over_prepared"])
        self.assertEqual(leg["prepared_count"], 3)
        self.assertEqual(leg["max_prepared"], 4)
        self.assertEqual(leg["excess_prepared"], 0)

    def test_cantrips_and_domain_spells_exempt(self):
        from core.compendiums.dnd_advancement import check_character_spell_legality
        cleric = {
            "name": "Shadowheart",
            "archetype": "Cleric",
            "class_levels": {"Cleric": 1},
            "attributes": {"Wisdom": 16},
            "spells": [
                {"name": "Sacred Flame", "level": 0, "is_cantrip": True, "prepared": True},
                {"name": "Thaumaturgy", "level": 0, "is_cantrip": True, "prepared": True},
                {"name": "Bless", "level": 1, "is_cantrip": False, "prepared": True},
                {"name": "Healing Word", "level": 1, "is_cantrip": False, "prepared": True},
                {"name": "Cure Wounds", "level": 1, "is_cantrip": False, "prepared": True},
                {"name": "Guiding Bolt", "level": 1, "is_cantrip": False, "prepared": True},
                {"name": "Charm Person", "level": 1, "is_cantrip": False, "prepared": True, "is_always_prepared": True},
                {"name": "Disguise Self", "level": 1, "is_cantrip": False, "prepared": True, "is_always_prepared": True},
            ]
        }
        leg = check_character_spell_legality(cleric, "2024")
        self.assertTrue(leg["is_legal"])
        self.assertEqual(leg["prepared_count"], 4)
        self.assertEqual(leg["max_prepared"], 4)
        self.assertEqual(leg["always_prepared_count"], 2)
        self.assertEqual(leg["cantrips_count"], 2)

    def test_unprepared_spellbook_spells_exempt(self):
        from core.compendiums.dnd_advancement import check_character_spell_legality
        wizard = {
            "name": "Elminster",
            "archetype": "Wizard",
            "class_levels": {"Wizard": 1},
            "attributes": {"Intelligence": 16},
            "spells": [
                {"name": "Mage Armor", "level": 1, "is_cantrip": False, "prepared": True},
                {"name": "Magic Missile", "level": 1, "is_cantrip": False, "prepared": True},
                {"name": "Shield", "level": 1, "is_cantrip": False, "prepared": True},
                {"name": "Sleep", "level": 1, "is_cantrip": False, "prepared": True},
                {"name": "Detect Magic", "level": 1, "is_cantrip": False, "prepared": False},
                {"name": "Identify", "level": 1, "is_cantrip": False, "prepared": False},
                {"name": "Feather Fall", "level": 1, "is_cantrip": False, "prepared": False},
            ]
        }
        leg = check_character_spell_legality(wizard, "2024")
        self.assertTrue(leg["is_legal"])
        self.assertEqual(leg["prepared_count"], 4)
        self.assertEqual(leg["total_leveled_spells"], 7)

    def test_compendium_grant_auto_prepares_when_under_capacity(self):
        # Gale currently has 3 prepared leveled spells (cap is 4).
        # Adding Sleep auto-prepares it since 3 < 4.
        res = self.ttrpg.add_compendium_entry_to_character(
            character_name="Gale",
            category="spells",
            index_or_name="sleep",
            edition="2024"
        )
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["entry"]["prepared"])
        self.assertTrue(res["legality"]["is_legal"])
        self.assertEqual(res["legality"]["prepared_count"], 4)

    def test_compendium_grant_adds_unprepared_when_at_capacity(self):
        # First fill the 4th slot
        self.ttrpg.add_compendium_entry_to_character(
            character_name="Gale",
            category="spells",
            index_or_name="sleep",
            edition="2024"
        )

        # Now Gale has 4/4 prepared spells.
        # Adding a 5th spell (Thunderwave) without explicit prepare_spell defaults to unprepared in spellbook!
        res5 = self.ttrpg.add_compendium_entry_to_character(
            character_name="Gale",
            category="spells",
            index_or_name="thunderwave",
            edition="2024"
        )
        self.assertEqual(res5["status"], "success")
        self.assertFalse(res5["entry"]["prepared"])
        self.assertTrue(res5["legality"]["is_legal"])
        self.assertEqual(res5["legality"]["prepared_count"], 4)
        self.assertIn("Unprepared in Spellbook", res5["message"])

    def test_compendium_grant_explicit_prepare_flags_over_limit_for_ui(self):
        # First fill the 4th slot
        self.ttrpg.add_compendium_entry_to_character(
            character_name="Gale",
            category="spells",
            index_or_name="sleep",
            edition="2024"
        )

        # User leaves 'Prepare spell immediately' checked (prepare_spell=True)
        res_over = self.ttrpg.add_compendium_entry_to_character(
            character_name="Gale",
            category="spells",
            index_or_name="thunderwave",
            edition="2024",
            prepare_spell=True
        )
        self.assertEqual(res_over["status"], "success")
        self.assertTrue(res_over["entry"]["prepared"])
        # Legality check flags over-limit so the UI can pop open the Edit Character modal!
        self.assertFalse(res_over["legality"]["is_legal"])
        self.assertTrue(res_over["legality"]["is_over_prepared"])
        self.assertEqual(res_over["legality"]["prepared_count"], 5)
        self.assertEqual(res_over["legality"]["max_prepared"], 4)
        self.assertEqual(res_over["legality"]["excess_prepared"], 1)
        self.assertIn("Preparation limit exceeded (5/4)", res_over["message"])

    def test_gui_bridge_check_character_spell_legality(self):
        bridge = GuiBridge()
        bridge.ttrpg_service = self.ttrpg

        res = bridge.check_character_spell_legality("Gale", "2024")
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["is_legal"])
        self.assertEqual(res["max_prepared"], 4)


if __name__ == "__main__":
    unittest.main()
