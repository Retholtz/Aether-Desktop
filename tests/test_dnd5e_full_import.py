"""
tests/test_dnd5e_full_import.py - Verification Suite
Validates complete extraction of Seraphina Lightbrew's and Selanar Fenthana's sheets:
Spell slots, prep limits, HTML sanitation, limited meters, and roleplay anchors.
"""
import os
import unittest
from unittest.mock import patch
from core.parsers.dnd5e_parser import DnD5eParser
from core.ttrpg_service import TTRPGService


class TestDnD5eFullImport(unittest.TestCase):
    def setUp(self):
        self.parser = DnD5eParser()
        self.service = TTRPGService()
        self.seraphina_pdf = "C:/Users/retho/.gemini/antigravity/brain/8eeb041e-1fc6-439d-824e-5dea41734b25/.user_uploaded/media_1793824115880_2621becf.pdf"
        self.selanar_pdf = "C:/Users/retho/.gemini/antigravity/brain/8eeb041e-1fc6-439d-824e-5dea41734b25/.user_uploaded/media_1793824115876_35bf3c5e.pdf"

    @patch.object(DnD5eParser, "extract_form_fields")
    def test_seraphina_complete_extraction(self, mock_extract):
        mock_fields = {
            "CharacterName": "Seraphina Lightbrew",
            "ClassLevel": "Artificer 1 (Aasimar, Guild Artisan / Guild Merchant)",
            "STR": "8", "DEX": "12", "CON": "15", "INT": "17", "WIS": "10", "CHA": "13",
            "Inspiration": "Off",
            "SpellSaveDC0": "13",
            "SpellAtkBonus0": "+5",
            "SlotsTotal1": "2",
            "SlotsRemaining1": "2",
            "PersonalityTraits": "I always want to know how things work and what makes people tick.. I'm full of witty aphorisms and have a proverb for every occasion.",
            "Ideals": "Generosity. My talents were given to me so that I could use them to benefit the world. (Good)",
            "Bonds": "I will get revenge on the evil forces that destroyed my place of business and ruined my livelihood.",
            "Flaws": "I'm never satisfied with what I have-I always want more.",
            "Allies": "Guild of Alchemists",
            "FeaturesAndTraits": "Healing Hands • MotM 7: As an action, you can touch a creature and heal it for <strong>2d4</strong> hit points. (1 / Long Rest • 1 Action)\nMagical Tinkering • TCoE 11: 3/Other"
        }
        mock_text = """
        D&D BEYOND
        SAVING THROWS
        CANTRIPS ===
        Guidance Artificer 1A Touch V,S
        1st LEVEL===
        2 Slots OO
        Cure Wounds Artificer 1A Touch V,S
        Faerie Fire <C> Artificer 1A 60 ft. V
        Detect Magic [R]<C> Artificer 1A Self V,S
        Alarm [R] Artificer 11m 30 ft. V,S,M
        """
        mock_extract.return_value = (mock_fields, mock_text)

        res = self.parser.parse_sheet(b"dummy_stream")
        native = res["native_system_data"]
        core = res["universal_core"]

        # 1. Cleaned Class & DC
        self.assertEqual(native["class_level"], "Artificer 1")
        self.assertEqual(native["ability_save_dc"], 13)
        self.assertFalse(native["inspiration"])

        # 2. Spell Slots
        self.assertEqual(native["spell_slots"]["level_1"]["max"], 2)
        self.assertEqual(native["spell_slots"]["level_1"]["current"], 2)

        # 3. Spells & Preparation
        spells = native["spells"]
        self.assertTrue(any(s["name"] == "Guidance" and s["prepared"] for s in spells))
        self.assertTrue(any(s["name"] == "Cure Wounds" and not s["prepared"] for s in spells))

        # Leveled spells default to unprepared on upload
        prep_leveled = [s for s in spells if not s["is_cantrip"] and s["prepared"]]
        self.assertEqual(len(prep_leveled), 0)
        self.assertEqual(native.get("max_prepared_leveled_spells"), 3)

        # 4. HTML Entity Stripping
        hands_meter = native["custom_meters"].get("healing_hands")
        self.assertIsNotNone(hands_meter)
        self.assertEqual(hands_meter["current"], 1)
        self.assertEqual(hands_meter["max"], 1)
        self.assertEqual(hands_meter["reset_on"], "long_rest")

        # 5. Roleplay Anchors
        rp = native["roleplay_anchors"]
        self.assertIn("witty aphorisms", rp["personality_traits"])
        self.assertIn("Generosity", rp["ideals"])
        self.assertIn("revenge", rp["bonds"])
        self.assertIn("never satisfied", rp["flaws"])
        self.assertEqual(rp["allies_and_organizations"], "Guild of Alchemists")

    def test_selanar_blood_hunter_real_pdf_ingestion(self):
        """Validates that Selanar Fenthana parses all fields without Gemini OCR fallback."""
        if not os.path.exists(self.selanar_pdf):
            self.skipTest("Selanar PDF missing")

        with open(self.selanar_pdf, "rb") as f:
            pdf_bytes = f.read()

        chars = self.service._parse_character_from_document("Selanar.pdf", "", pdf_bytes)
        self.assertEqual(len(chars), 1)
        c = chars[0]

        # Species & Background
        self.assertEqual(c.get("name"), "Selanar Fenthana")
        self.assertEqual(c.get("species"), "Sea Elf")
        self.assertEqual(c.get("background"), "Outlander")

        # Saving Throws (Dexterity +5, Intelligence +4)
        saves = c.get("saving_throws", {})
        self.assertEqual(saves.get("Dexterity"), "+5")
        self.assertEqual(saves.get("Intelligence"), "+4")

        # Backstory
        bs = c.get("backstory", "")
        self.assertIn("Sea of Fallen Stars", bs)
        self.assertNotIn("\\", bs)

    def test_seraphina_real_pdf_backstory_no_backslashes(self):
        """Validates that Seraphina Lightbrew's backstory is free of backslashes and joined."""
        if not os.path.exists(self.seraphina_pdf):
            self.skipTest("Seraphina PDF missing")

        with open(self.seraphina_pdf, "rb") as f:
            pdf_bytes = f.read()

        chars = self.service._parse_character_from_document("Seraphina.pdf", "", pdf_bytes)
        self.assertEqual(len(chars), 1)
        c = chars[0]

        bs = c.get("backstory", "")
        self.assertNotIn("\\", bs)
        self.assertIn("young", bs)
        self.assertIn("the world", bs)

        # Spell Slots
        slots = c.get("spell_slots", {})
        self.assertEqual(slots.get("level_1", {}).get("max"), 2)


if __name__ == "__main__":
    unittest.main()

