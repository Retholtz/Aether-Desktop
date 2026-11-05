"""
tests/test_cleric_bloodhunter_parser.py - Verification Suite
"""
import unittest
from unittest.mock import patch
from core.parsers.dnd5e_parser import DnD5eParser


class TestClericAndBloodHunterParser(unittest.TestCase):
    def setUp(self):
        self.parser = DnD5eParser()

    @patch.object(DnD5eParser, "extract_form_fields")
    def test_rhaz_blackscale_cleric_ingestion(self, mock_extract):
        mock_fields = {
            "CharacterName": "Rhaz Blackscale",
            "ClassLevel": "Cleric 1 (Lizardfolk, Acolyte)",
            "WIS": "16", "CON": "16", "DEX": "14", "STR": "10", "INT": "10", "CHA": "8",
            "SlotsTotal1": "2",
            "Wpn Name": "Crossbow, Light", "Wpn1 AtkBonus": "+4", "Wpn1 Damage": "1d8+2 Piercing",
            "GP": "5", "PP": "1",
            "Passive1": "15", "Passive2": "15", "Passive3": "10",
            "FeaturesAndTraits": "* Additional Cleric Spells • TCoE 30\n* Hit Points • PHB 57\nSpellcasting\nDivine Domain • Death Domain\nReaper\nHungry Jaws\nSpeed 30 ft. (Walking)"
        }
        mock_text = """
        D&D BEYOND
        SAVING THROWS
        CANTRIPS ===
        Sacred Flame Cleric DEX 13 1A 60 ft. V,S
        Toll the Dead Cleric WIS 13 1A 60 ft. V,S
        1st LEVEL===
        2 Slots OO
        False Life Cleric 1A Self V,S,M
        Ray of Sickness Cleric CON 13 1A 60 ft. V,S
        Cure Wounds Cleric 1A Touch V,S
        """
        mock_extract.return_value = (mock_fields, mock_text)

        res = self.parser.parse_sheet(b"dummy")
        native = res["native_system_data"]
        meters = native["custom_meters"]
        spells = native["spells"]

        # 1. Class level string cleaned
        self.assertEqual(res["archetype"], "Cleric 1")

        # 2. Spell slots parsed properly
        self.assertEqual(native["spell_slots"]["level_1"]["max"], 2)
        self.assertEqual(native["spell_slots"]["level_1"]["current"], 2)

        # 3. Preparation limits (WIS +3 + Level 1 = 4 prepared spells)
        self.assertEqual(native["max_prepared_leveled_spells"], 4)

        # 4. Death Domain spells always prepared
        false_life = next(s for s in spells if s["name"].lower() == "false life")
        cure_wounds = next(s for s in spells if s["name"].lower() == "cure wounds")
        self.assertTrue(false_life["is_always_prepared"])
        self.assertTrue(false_life["prepared"])
        self.assertFalse(cure_wounds["is_always_prepared"])
        self.assertFalse(cure_wounds["prepared"])

        # 5. Speed absent from custom_meters
        self.assertNotIn("speed", meters)

        # 6. Trait headers stripped (including bullet asterisks)
        self.assertNotIn("Additional Cleric Spells", native["features_and_traits"])
        self.assertFalse(any("hit points" in t.lower() for t in native["features_and_traits"]))

        # 7. Hungry Jaws Lizardfolk meter
        self.assertIn("hungry_jaws", meters)
        self.assertEqual(meters["hungry_jaws"]["reset_on"], "short_rest")

        # 8. Root-level weapons & currency
        self.assertEqual(len(res["weapons"]), 1)
        self.assertEqual(res["weapons"][0]["name"], "Crossbow, Light")
        self.assertEqual(res["currency"]["gp"], 5)
        self.assertEqual(res["currency"]["pp"], 1)

        # 9. Passives from Passive1..3
        self.assertEqual(res["passives"]["perception"], 15)
        self.assertEqual(res["passives"]["insight"], 15)

    @patch.object(DnD5eParser, "extract_form_fields")
    def test_selanar_blood_hunter_ingestion(self, mock_extract):
        mock_fields = {
            "CharacterName": "Selanar Fenthana",
            "ClassLevel": "Blood Hunter 1 (Sea Elf, Outlander)",
            "DEX": "16", "CON": "14", "INT": "14", "WIS": "12", "STR": "10", "CHA": "10",
            "EquipmentRow1": "Studded Leather", "EquipmentQty1": "1",
            "EquipmentRow2": "Shortsword", "EquipmentQty2": "1",
            "FeaturesAndTraits": "Hunter's Bane\nBlood Maledict: Invoke a blood curse 1/Short Rest\nChild of the Sea\nSpeed 30 ft."
        }
        mock_text = "D&D BEYOND\nFEATURES ===\nBlood Maledict 1/Short Rest\nEQUIPMENT"
        mock_extract.return_value = (mock_fields, mock_text)

        res = self.parser.parse_sheet(b"dummy")
        native = res["native_system_data"]
        meters = native["custom_meters"]

        # 1. d10 Hit Die for Blood Hunter
        self.assertEqual(res["hit_dice"], "1d10")
        self.assertEqual(meters["hit_dice"]["label"], "Hit Dice (1d10)")

        # 2. Blood Maledict extracted
        self.assertIn("blood_maledict", meters)
        self.assertEqual(meters["blood_maledict"]["max"], 1)
        self.assertEqual(meters["blood_maledict"]["reset_on"], "short_rest")

        # 3. Speed purged
        self.assertNotIn("speed", meters)

        # 4. Structured inventory dictionaries
        self.assertIsInstance(res["inventory"], list)
        self.assertIsInstance(res["inventory"][0], dict)
        self.assertEqual(res["inventory"][0]["name"], "Studded Leather")


if __name__ == "__main__":
    unittest.main()

