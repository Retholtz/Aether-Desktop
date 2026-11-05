"""
tests/test_warlock_druid_paladin.py - Verification Suite
Validates Warlock Pact Magic, Druid Wild Shape, and Paladin Lay on Hands / Oath prep bypass.
"""
import os
import shutil
import unittest
from unittest.mock import patch, MagicMock
from core.parsers.dnd5e_parser import DnD5eParser
from core.ttrpg_service import TTRPGService
from tools.ttrpg_tools import register_enhanced_rest_tools


class TestWarlockDruidPaladin(unittest.TestCase):
    def setUp(self):
        self.parser = DnD5eParser()
        self.service = TTRPGService(data_dir="tests/test_camp", manifest_path="tests/manifest.json")

    def tearDown(self):
        if os.path.exists("tests/test_camp"):
            shutil.rmtree("tests/test_camp", ignore_errors=True)
        if os.path.exists("tests/manifest.json"):
            try:
                os.remove("tests/manifest.json")
            except OSError:
                pass

    @patch.object(DnD5eParser, "extract_form_fields")
    def test_warlock_pact_magic_and_short_rest_recovery(self, mock_extract):
        # Level 3 Warlock: 2 slots at 2nd level
        mock_fields = {
            "CharacterName": "Malakor",
            "ClassLevel": "Warlock 3",
            "CHA": "16", "DEX": "14", "CON": "14", "INT": "10", "STR": "8", "WIS": "12",
            "PactMagic_SlotsTotal": "2",
            "PactMagic_SlotsRemaining": "0",  # Expended
            "FeaturesAndTraits": "Pact Magic • PHB 106\nEldritch Invocations • Agonizing Blast, Armor of Shadows"
        }
        mock_text = """
        D&D BEYOND
        CANTRIPS ===
        Eldritch Blast Warlock +5 1A 120 ft. V,S
        2nd LEVEL===
        Hold Person Warlock WIS 13 1A 60 ft. V,S,M
        Hunger of Hadar Warlock DEX 13 1A 150 ft. V,S,M
        """
        mock_extract.return_value = (mock_fields, mock_text)

        res = self.parser.parse_sheet(b"dummy")
        native = res["native_system_data"]
        slots = native["spell_slots"]

        # Validate Pact Magic Slot Configuration
        self.assertTrue(native["is_pact_magic"])
        self.assertEqual(slots["level_2"]["max"], 2)
        self.assertEqual(slots["level_2"]["current"], 0)
        self.assertEqual(slots["level_2"]["reset_on"], "short_rest")

        # Set in service and verify Short Rest replenishes slots
        self.service.active_state = {
            "campaign_id": "test",
            "characters": {"char_warlock": {"id": "char_warlock", "name": "Malakor", "native_system_data": native, "vitals": {"health": {"current": 20, "max": 20}}}}
        }
        dispatcher = MagicMock()
        register_enhanced_rest_tools(dispatcher, self.service, MagicMock())
        rest_fn = [c.kwargs["handler"] for c in dispatcher.register_tool.call_args_list if c.kwargs["name"] == "execute_rest"][0]

        import asyncio
        asyncio.run(rest_fn(rest_type="short", dice_mode="digital"))
        recharged_slots = self.service.active_state["characters"]["char_warlock"]["native_system_data"]["spell_slots"]["level_2"]
        self.assertEqual(recharged_slots["current"], 2)

    @patch.object(DnD5eParser, "extract_form_fields")
    def test_druid_wild_shape_meter(self, mock_extract):
        mock_fields = {
            "CharacterName": "Rowan",
            "ClassLevel": "Druid 4 (Circle of the Land)",
            "WIS": "16", "CON": "14", "DEX": "12", "STR": "10", "INT": "12", "CHA": "8",
            "SlotsTotal1": "4", "SlotsTotal2": "3",
            "FeaturesAndTraits": "Wild Shape • 2/Short Rest\nCircle of the Land • Forest"
        }
        mock_text = "D&D BEYOND\nFEATURES ===\nWild Shape 2/Short Rest\nEQUIPMENT"
        mock_extract.return_value = (mock_fields, mock_text)

        res = self.parser.parse_sheet(b"dummy")
        meters = res["native_system_data"]["custom_meters"]

        self.assertIn("wild_shape", meters)
        self.assertEqual(meters["wild_shape"]["max"], 2)
        self.assertEqual(meters["wild_shape"]["reset_on"], "short_rest")

    @patch.object(DnD5eParser, "extract_form_fields")
    def test_paladin_lay_on_hands_and_oath_spell_prep_bypass(self, mock_extract):
        mock_fields = {
            "CharacterName": "Varrick",
            "ClassLevel": "Paladin 3 (Oath of Vengeance)",
            "STR": "16", "CHA": "14", "CON": "14", "WIS": "10", "DEX": "10", "INT": "8",
            "SlotsTotal1": "3",
            "FeaturesAndTraits": "Lay on Hands • 15 HP pool\nChannel Divinity • 1/Short Rest\nOath of Vengeance"
        }
        mock_text = """
        D&D BEYOND
        1st LEVEL===
        Bane Paladin 1A 30 ft. V,S,M
        Hunter's Mark Paladin 1BA 90 ft. V
        Cure Wounds Paladin 1A Touch V,S
        """
        mock_extract.return_value = (mock_fields, mock_text)

        res = self.parser.parse_sheet(b"dummy")
        native = res["native_system_data"]
        meters = native["custom_meters"]
        spells = native["spells"]

        # Lay on Hands (Level 3 * 5 = 15 HP) & Channel Divinity
        self.assertEqual(meters["lay_on_hands"]["max"], 15)
        self.assertEqual(meters["lay_on_hands"]["reset_on"], "long_rest")
        self.assertEqual(meters["channel_divinity"]["max"], 1)
        self.assertEqual(meters["channel_divinity"]["reset_on"], "short_rest")

        # Oath Spells (Bane & Hunter's Mark) are marked always prepared
        bane = next(s for s in spells if s["name"].lower() == "bane")
        cure = next(s for s in spells if s["name"].lower() == "cure wounds")
        self.assertTrue(bane["is_always_prepared"])
        self.assertTrue(bane["prepared"])
        self.assertFalse(cure["is_always_prepared"])
        self.assertFalse(cure["prepared"])  # Leveled spell defaults to False

    @patch.object(DnD5eParser, "extract_form_fields")
    def test_warlock_pact_regex_known_spells_and_deduplication(self, mock_extract):
        mock_fields = {
            "CharacterName": "Wyll",
            "ClassLevel": "Warlock 1 (Human, Folk Hero)",
            "CHA": "16", "DEX": "13", "CON": "15", "INT": "14", "STR": "9", "WIS": "11"
        }
        mock_text = """
        D&D BEYOND
        CANTRIPS ===
        Eldritch Blast Warlock +5 1A 120 ft. V,S
        Eldritch Blast Warlock +5 1A 120 ft. V,S
        1st LEVEL===
        1 Pact
        Armor of Agathys Warlock 1A Self V,S,M
        Hex Warlock 1BA 90 ft. V,S,M
        Hex Warlock 1BA 90 ft. V,S,M
        """
        mock_extract.return_value = (mock_fields, mock_text)

        res = self.parser.parse_sheet(b"dummy")
        native = res["native_system_data"]
        slots = native["spell_slots"]
        spells = native["spells"]

        # Warlock slot regex extracts 1 Pact slot at 1st level
        self.assertEqual(slots["level_1"]["max"], 1)
        self.assertEqual(slots["level_1"]["current"], 1)
        self.assertTrue(slots["level_1"]["is_pact_magic"])

        # Known Caster (Warlock) marks all spells prepared = True and deduplicates
        names = [s["name"] for s in spells]
        self.assertEqual(names.count("Eldritch Blast"), 1)
        self.assertEqual(names.count("Hex"), 1)
        self.assertTrue(all(s["prepared"] for s in spells))

    @patch.object(DnD5eParser, "extract_form_fields")
    def test_nameroc_druid_prep_cap_and_wizard_unprepared_ritual(self, mock_extract):
        # Nameroc: Level 1 Druid with WIS 16 (+3) and INT 14 (+2) -> wis_mod + level = 4
        mock_fields = {
            "CharacterName": "Nameroc",
            "ClassLevel": "Druid 1 (Human, Sage)",
            "WIS": "16", "INT": "14", "CON": "16", "DEX": "13", "STR": "11", "CHA": "9",
            "SlotsTotal1": "2"
        }
        mock_extract.return_value = (mock_fields, "D&D BEYOND\nSAVING THROWS")
        res = self.parser.parse_sheet(b"dummy")
        self.assertEqual(res["native_system_data"]["max_prepared_leveled_spells"], 4)

        # Wizard (Presto) casting unprepared [R] ritual spell vs unprepared non-ritual spell
        from tools.ttrpg_tools import register_dm_adjudication_tools
        self.service.active_state = {
            "campaign_id": "test",
            "characters": {
                "char_presto": {
                    "id": "char_presto",
                    "name": "Presto",
                    "archetype": "Wizard 1 (Human, Scribe)",
                    "native_system_data": {
                        "class_level": "Wizard 1",
                        "spell_slots": {"level_1": {"current": 2, "max": 2}},
                        "spells": [
                            {"name": "Comprehend Languages [R]", "level": "1st Level", "is_cantrip": False, "prepared": False},
                            {"name": "Burning Hands", "level": "1st Level", "is_cantrip": False, "prepared": False}
                        ]
                    }
                }
            }
        }
        dispatcher = MagicMock()
        register_dm_adjudication_tools(dispatcher, self.service, MagicMock())
        cast_fn = [c.kwargs["handler"] for c in dispatcher.register_tool.call_args_list if c.kwargs["name"] == "cast_spell"][0]

        import asyncio
        # Wizard can cast Comprehend Languages [R] even when prepared == False
        ritual_res = asyncio.run(cast_fn("Presto", "Comprehend Languages [R]", 1))
        self.assertEqual(ritual_res["status"], "success")

        # Wizard cannot cast non-ritual Burning Hands when prepared == False
        fail_res = asyncio.run(cast_fn("Presto", "Burning Hands", 1))
        self.assertEqual(fail_res["status"], "error")

    @patch.object(DnD5eParser, "extract_form_fields")
    def test_paladin_lay_on_hands_divine_sense_and_zero_prep_cap(self, mock_extract):
        mock_fields = {
            "CharacterName": "Surina Daardendrian",
            "ClassLevel": "Paladin 1 (Dragonborn, Noble)",
            "STR": "17", "DEX": "8", "CON": "13", "INT": "12", "WIS": "10", "CHA": "15",
            "FeaturesAndTraits": "Hit Points\nProficiencies\nDivine Sense: You can use this feature 3 times per long rest.\nLay on Hands Pool: 5 / Long Rest"
        }
        mock_text = "D&D BEYOND\nSAVING THROWS\n=== ARMOR ===\n"
        mock_extract.return_value = (mock_fields, mock_text)

        res = self.parser.parse_sheet(b"dummy")
        native = res["native_system_data"]
        core = res["universal_core"]
        meters = native["custom_meters"]

        # Lay on Hands (5 HP) and Divine Sense (3 uses)
        self.assertIn("lay_on_hands", meters)
        self.assertEqual(meters["lay_on_hands"]["max"], 5)
        self.assertIn("divine_sense", meters)
        self.assertEqual(meters["divine_sense"]["max"], 3)

        # Paladin 1 has 0 spell slots -> max_prepared_leveled_spells = 0
        self.assertEqual(native["max_prepared_leveled_spells"], 0)

        # Archetype has parentheticals stripped
        self.assertEqual(core.archetype, "Paladin 1")
        self.assertEqual(native["class_level"], "Paladin 1")

        # Banned noise traits stripped
        trait_names = [t["name"].lower() for t in core.tagged_traits]
        self.assertNotIn("hit points", trait_names)
        self.assertNotIn("proficiencies", trait_names)

    @patch.object(DnD5eParser, "extract_form_fields")
    def test_wizard_arcane_recovery_and_inventory_stacking(self, mock_extract):
        mock_fields = {
            "CharacterName": "Presto",
            "ClassLevel": "Wizard 1 (Human, Scribe)",
            "INT": "15", "CON": "12", "DEX": "13", "STR": "8", "WIS": "10", "CHA": "14",
            "SlotsTotal1": "2",
            "FeaturesAndTraits": "Arcane Recovery: Once per day when you finish a short rest...\nSpellcasting"
        }
        for i in range(4):
            mock_fields[f"Eq Name{i}"] = "Dagger"
            mock_fields[f"Eq Qty{i}"] = "1"
        for i in range(4, 7):
            mock_fields[f"Eq Name{i}"] = "Lamp"
            mock_fields[f"Eq Qty{i}"] = "1"
        mock_fields["Eq Name7"] = "Oil"
        mock_fields["Eq Qty7"] = "3"
        mock_fields["Eq Name8"] = "Oil (flask)"
        mock_fields["Eq Qty8"] = "2"

        mock_text = "D&D BEYOND\nCANTRIPS ===\nFire Bolt Wizard +4 1A 120 ft. V,S\n1st LEVEL===\nMagic Missile Wizard 1A 120 ft. V,S"
        mock_extract.return_value = (mock_fields, mock_text)

        res = self.parser.parse_sheet(b"dummy")
        native = res["native_system_data"]
        core = res["universal_core"]
        meters = native["custom_meters"]
        inv = core.tagged_inventory
        spells = native["spells"]

        # Wizard Arcane Recovery
        self.assertIn("arcane_recovery", meters)
        self.assertEqual(meters["arcane_recovery"]["max"], 1)

        # Inventory stacked
        dagger_entry = next(item for item in inv if item["name"].lower() == "dagger")
        self.assertEqual(dagger_entry["qty"], "4")
        lamp_entry = next(item for item in inv if "lamp" in item["name"].lower())
        self.assertEqual(lamp_entry["qty"], "3")
        oil_entry = next(item for item in inv if item["name"].lower() == "oil")
        self.assertEqual(oil_entry["qty"], "5")

        # Spell level is integer
        missile = next(s for s in spells if s["name"] == "Magic Missile")
        self.assertEqual(missile["level"], 1)
        firebolt = next(s for s in spells if s["name"] == "Fire Bolt")
        self.assertEqual(firebolt["level"], 0)


if __name__ == "__main__":
    unittest.main()
