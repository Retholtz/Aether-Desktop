"""
tests/test_dnd5e_enhancements.py - Verification Suite for Multiclass, Arcanum & Text Healing
"""
import unittest
from unittest.mock import patch
from core.parsers.dnd5e_parser import (
    DnD5eParser, parse_multiclass_string,
    calculate_multiclass_spell_slots, heal_truncated_field,
    extract_multiclass_hit_dice, extract_mystic_arcanum_meters
)


class TestDnD5eParserEnhancements(unittest.TestCase):
    def setUp(self):
        self.parser = DnD5eParser()

    def test_multiclass_parsing_and_slot_matrix(self):
        # 1. Paladin 6 / Sorcerer 4 (ECL: 3 + 4 = 7)
        classes = parse_multiclass_string("Paladin 6 / Sorcerer 4 (Dragonborn, Noble)")
        self.assertEqual(len(classes), 2)
        self.assertEqual(classes[0]["class"], "Paladin")
        self.assertEqual(classes[0]["level"], 6)
        self.assertEqual(classes[1]["class"], "Sorcerer")
        self.assertEqual(classes[1]["level"], 4)

        slots = calculate_multiclass_spell_slots(classes)
        # 7th level caster has: 4 1st, 3 2nd, 3 3rd, 1 4th
        self.assertEqual(slots["level_1"]["max"], 4)
        self.assertEqual(slots["level_2"]["max"], 3)
        self.assertEqual(slots["level_3"]["max"], 3)
        self.assertEqual(slots["level_4"]["max"], 1)
        self.assertEqual(slots["level_5"]["max"], 0)

    @patch.object(DnD5eParser, "extract_form_fields")
    def test_warlock_high_level_pact_and_arcanum(self, mock_extract):
        # Warlock 13 (3x 5th level slots + 6th & 7th Arcanum)
        mock_fields = {
            "CharacterName": "High Wyll",
            "ClassLevel": "Warlock 13",
            "CHA": "20",
            "PactMagic_SlotsTotal": "3"
        }
        mock_text = "D&D BEYOND\nSAVING THROWS\nPact Magic"
        mock_extract.return_value = (mock_fields, mock_text)

        res = self.parser.parse_sheet(b"dummy")
        slots = res["native_system_data"]["spell_slots"]
        meters = res["native_system_data"]["custom_meters"]

        # Check Pact Slots
        self.assertEqual(slots["level_5"]["max"], 3)
        self.assertTrue(slots["level_5"]["is_pact_magic"])
        self.assertEqual(slots["level_5"]["reset_on"], "short_rest")

        # Check Mystic Arcanum
        self.assertIn("mystic_arcanum_6th", meters)
        self.assertIn("mystic_arcanum_7th", meters)
        self.assertNotIn("mystic_arcanum_8th", meters)
        self.assertEqual(meters["mystic_arcanum_6th"]["reset_on"], "long_rest")

    def test_acroform_boundary_text_healing(self):
        truncated_bond = "Wyll is enchanted to be unable to speak freely of his pact with others, his tongue bound by Mizora. Its true"
        raw_pdf_stream = (
            "Wyll is enchanted to be unable to speak freely of his pact with others, his tongue bound by Mizora. "
            "Its true nature is a secret he must bear alone.\nFLAWS"
        )
        
        healed = heal_truncated_field(truncated_bond, raw_pdf_stream, ["FLAWS", "IDEALS"])
        expected = "Wyll is enchanted to be unable to speak freely of his pact with others, his tongue bound by Mizora. Its true nature is a secret he must bear alone."
        self.assertEqual(healed, expected)

    def test_multiclass_segmented_hit_dice(self):
        classes = parse_multiclass_string("Fighter 2 / Rogue 3 / Wizard 5")
        meters, die_labels = extract_multiclass_hit_dice(classes)
        self.assertEqual(die_labels, "2d10, 3d8, 5d6")
        self.assertIn("hit_dice_fighter", meters)
        self.assertIn("hit_dice_rogue", meters)
        self.assertIn("hit_dice_wizard", meters)
        self.assertEqual(meters["hit_dice_fighter"]["max"], 2)
        self.assertEqual(meters["hit_dice_rogue"]["max"], 3)
        self.assertEqual(meters["hit_dice_wizard"]["max"], 5)

    def test_warlock_all_arcanum_levels(self):
        # Level 17+ should unlock 6th, 7th, 8th, and 9th
        meters_17 = extract_mystic_arcanum_meters(17)
        self.assertIn("mystic_arcanum_6th", meters_17)
        self.assertIn("mystic_arcanum_7th", meters_17)
        self.assertIn("mystic_arcanum_8th", meters_17)
        self.assertIn("mystic_arcanum_9th", meters_17)

        # Level 10 has none
        meters_10 = extract_mystic_arcanum_meters(10)
        self.assertEqual(meters_10, {})

    def test_cantrip_word_boundary_matching(self):
        parser = DnD5eParser()
        mock_fields = {
            "CharacterName": "Mage",
            "ClassLevel": "Warlock 7",
            "spellHeader0": "=== CANTRIPS ===",
            "spellName0": "Light",
            "spellHeader4": "=== 4th LEVEL ===",
            "spellName1": "Blight"
        }
        spells = parser.parse_spells_list(mock_fields, "", "Warlock 7")
        spells_dict = {s["name"]: s for s in spells}
        self.assertEqual(spells_dict["Light"]["level"], 0)
        self.assertTrue(spells_dict["Light"]["is_cantrip"])
        self.assertEqual(spells_dict["Blight"]["level"], 4)
        self.assertFalse(spells_dict["Blight"]["is_cantrip"])

    def test_level_20_warlock_sam_pdf_mechanics(self):
        import os
        sam_path = os.path.join(os.path.dirname(__file__), "..", "Sam.pdf")
        if not os.path.exists(sam_path):
            self.skipTest("Sam.pdf not found in workspace")

        with open(sam_path, "rb") as f:
            sam = DnD5eParser().parse_sheet(f.read())

        spells = {s["name"]: s["level"] for s in sam["native_system_data"]["spells"]}
        meters = sam["native_system_data"]["custom_meters"]

        # 1. Verify Spell Tiers & Cantrip Boundary
        self.assertEqual(spells["Blight"], 4, f"Blight parsed as level {spells.get('Blight')}, expected 4")
        self.assertEqual(spells["Dimension Door"], 4)
        self.assertEqual(spells["Wall of Fire"], 4)
        self.assertEqual(spells["Create Undead"], 6)
        self.assertEqual(spells["Finger of Death"], 7, f"Finger of Death parsed as level {spells.get('Finger of Death')}, expected 7")
        self.assertEqual(spells["Dominate Monster"], 8)
        self.assertEqual(spells["Astral Projection"], 9, f"Astral Projection parsed as level {spells.get('Astral Projection')}, expected 9")

        # 2. Verify Root Hit Dice
        self.assertEqual(sam["hit_dice"], "20d8", f"Root hit dice is {sam['hit_dice']}, expected 20d8")

        # 3. Verify High-Level Meters
        self.assertIn("breath_weapon", meters)
        self.assertEqual(meters["breath_weapon"]["max"], 6)
        self.assertEqual(meters["breath_weapon"]["reset_on"], "long_rest")

        self.assertIn("draconic_flight", meters)
        self.assertEqual(meters["draconic_flight"]["max"], 1)

        self.assertIn("dark_ones_own_luck", meters)
        self.assertEqual(meters["dark_ones_own_luck"]["max"], 3)
        self.assertEqual(meters["dark_ones_own_luck"]["reset_on"], "long_rest")

        self.assertIn("hurl_through_hell", meters)
        self.assertEqual(meters["hurl_through_hell"]["max"], 1)

        self.assertIn("boon_of_fate", meters)
        self.assertEqual(meters["boon_of_fate"]["max"], 1)
        self.assertEqual(meters["boon_of_fate"]["reset_on"], "short_rest")

        self.assertIn("magical_cunning", meters)
        self.assertEqual(meters["magical_cunning"]["max"], 1)

        # 4. Features & Traits extraction across pages 1 through 6
        traits = sam["native_system_data"]["features_and_traits"]
        self.assertGreaterEqual(len(traits), 35)
        traits_text = " ".join(traits)
        self.assertIn("Dark One’s Blessing", traits_text)
        self.assertIn("Fiendish Resilience", traits_text)

    def test_multiclass_prepared_spells_calculation(self):
        # Paladin 10 / Sorcerer 10 with CHA mod 2
        # Paladin: 10 // 2 + 2 = 7
        # Sorcerer: sorc_table[10] = 11
        # Total: 18
        prep_cap = DnD5eParser._calculate_max_prepared_spells("Paladin 10 / Sorcerer 10", 20, 2)
        self.assertEqual(prep_cap, 18)

    def test_shadow_pdf_mechanics(self):
        import os
        shadow_path = os.path.join(os.path.dirname(__file__), "..", "Shadow.pdf")
        if not os.path.exists(shadow_path):
            self.skipTest("Shadow.pdf not found in workspace")

        with open(shadow_path, "rb") as f:
            shadow = DnD5eParser().parse_sheet(f.read())

        meters = shadow["native_system_data"]["custom_meters"]
        prep_cap = shadow["native_system_data"]["max_prepared_leveled_spells"]

        # 1. Multiclass Prepared Cap must equal 18
        self.assertEqual(prep_cap, 18)

        # 2. Lay on Hands is 50 HP
        self.assertEqual(meters.get("lay_on_hands", {}).get("current"), 50)

        # 3. Divine Sense must not grab 50 from Lay on Hands pool (in 2024 it is Channel Divinity)
        self.assertNotEqual(meters.get("divine_sense", {}).get("max", 0), 50)
        self.assertNotIn("divine_sense", meters)
        self.assertIn("channel_divinity", meters)

    def test_trait_name_formatting_and_empty_tag_sanitization(self):
        from core.parsers.dnd5e_parser import extract_clean_trait_name, clean_traits_and_headers

        # 1. Bulleted sub-entries assign clean names
        sub_bullet = "• Sorcerer Level 2 | 2 SP -> Spell Slot Level 1"
        self.assertEqual(extract_clean_trait_name(sub_bullet), "Sorcerer Level 2")

        bullet_with_star = "* Dark One’s Own Luck • PHB-2024 162"
        self.assertEqual(extract_clean_trait_name(bullet_with_star), "Dark One’s Own Luck")

        dash_entry = "- Action Surge: 1/Short Rest"
        self.assertEqual(extract_clean_trait_name(dash_entry), "Action Surge")

        # 2. Section headers starting with === return empty string
        self.assertEqual(extract_clean_trait_name("=== WARLOCK FEATURES ==="), "")

        # 3. clean_traits_and_headers purges empty names and banned headers
        test_traits = [
            {"name": "", "description": "Empty trait", "genre": "fantasy_d20"},
            {"name": "Action", "description": "Lone section header", "genre": "fantasy_d20"},
            {"name": "actions", "description": "Plural header", "genre": "fantasy_d20"},
            {"name": "reaction", "description": "Reaction header", "genre": "fantasy_d20"},
            {"name": "reactions", "description": "Reactions header", "genre": "fantasy_d20"},
            {"name": "special", "description": "Special header", "genre": "fantasy_d20"},
            {"name": "Font of Magic", "description": "Valid feature", "genre": "fantasy_d20"}
        ]
        sanitized = clean_traits_and_headers(test_traits)
        self.assertEqual(len(sanitized), 1)
        self.assertEqual(sanitized[0]["name"], "Font of Magic")


if __name__ == "__main__":
    unittest.main()
