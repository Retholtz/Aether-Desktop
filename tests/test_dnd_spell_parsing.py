import os
import unittest
from core.parsers.spell_registry import parse_structured_spells, sanitize_parsed_spells, sanitize_character_weapons, is_junk_spell, is_spell_name, resolve_dnd_stat
from core.ttrpg_service import TTRPGService
from core.character_vault_service import CharacterVaultService


class TestDnDSpellParsing(unittest.TestCase):
    def test_junk_spell_filtering(self):
        junk_samples = [
            "=== CANTRIPS ===",
            "=== 1st LEVEL ===",
            "4 Slots OOOO",
            "(At Will)",
            "(At Will",
            "PHB-2024 292",
            "PAGE REF",
            "SAVE/ATK",
            "TIME",
            "RANGE",
            "COMP",
            "DURATION",
            "PREP",
            "SPELLS",
            "NOTES",
            "Touch",
            "10 ft.",
            "60 ft.",
            "120 ft.",
            "Self/15 ft. Cone",
            "Self/30 ft. Sphere",
            "Touch/15 ft. Cone",
            "15 ft. Cone, V/S",
            "15 ft. Cone, V/S/M",
            "V,M",
            "V,S",
            "V,S,M",
            "1 hour",
            "8 hours",
            "Instantaneous",
            "Concentration, up to 10 minutes",
            "Concentration, up to 1 minute",
            "Concentration, up to 1 hour",
            "DEX 13",
            "CON 13",
            "--",
            "D: 1h, V/M",
            "D: 10m, 30 ft. Sphere, V/S"
        ]
        for j in junk_samples:
            self.assertTrue(is_junk_spell(j), f"Expected '{j}' to be flagged as junk")

        valid_samples = [
            "Light",
            "Prestidigitation",
            "Shocking Grasp",
            "Sorcerous Burst",
            "Burning Hands",
            "Color Spray",
            "Detect Magic [R]",
            "Mage Armor",
            "Dragon's Breath",
            "Spider Climb",
            "Fire Bolt",
            "Eldritch Blast",
            "Magic Missile",
            "Shield",
            "Misty Step",
            "Fireball"
        ]
        for v in valid_samples:
            self.assertFalse(is_junk_spell(v), f"Expected '{v}' to be flagged as a valid spell")

    def test_sanitize_dirty_spells_list(self):
        dirty = [
            "=== CANTRIPS ===", "(At Will", "Light", "Touch", "V,M", "1 hour",
            "PHB-2024 292", "D: 1h, V/M", "Prestidigitation", "10 ft.", "V,S",
            "PHB-2024 307", "D: 1h, V/S", "Shocking Grasp", "Instantaneous",
            "PHB-2024 316", "V/S", "Sorcerous Burst", "120 ft.", "PHB-2024 318",
            "=== 1st LEVEL ===", "4 Slots OOOO", "Burning Hands", "DEX 13",
            "Self/15 ft. Cone", "PHB-2024 248", "15 ft. Cone, V/S", "Color Spray",
            "CON 13", "V,S,M", "PHB-2024 251", "15 ft. Cone, V/S/M", "Detect Magic [R]",
            "Self/30 ft. Sphere", "Concentration, up to 10 minutes", "PHB-2024 262",
            "D: 10m, 30 ft. Sphere, V/S", "Mage Armor", "8 hours", "PHB-2024 293",
            "D: 8h, V/S/M", "=== 2nd LEVEL ===", "2 Slots OO", "Dragon's Breath",
            "Touch/15 ft. Cone", "Concentration, up to 1 minute", "PHB-2024 266",
            "D: 1m, 15 ft. Cone, V/S/M", "Spider Climb", "Concentration, up to 1 hour",
            "D: 1h, V/S/M"
        ]
        sanitized = sanitize_parsed_spells(dirty)
        self.assertEqual(len(sanitized), 10)
        names = [s["name"] for s in sanitized]
        expected_names = [
            "Light", "Prestidigitation", "Shocking Grasp", "Sorcerous Burst",
            "Burning Hands", "Color Spray", "Detect Magic [R]", "Mage Armor",
            "Dragon's Breath", "Spider Climb"
        ]
        self.assertEqual(names, expected_names)

        # Check levels and damages
        s_map = {s["name"]: s for s in sanitized}
        self.assertEqual(s_map["Light"]["level"], "Cantrip")
        self.assertEqual(s_map["Shocking Grasp"]["damage"], "1d8 Lightning")
        self.assertEqual(s_map["Burning Hands"]["level"], "1st Level")
        self.assertEqual(s_map["Burning Hands"]["damage"], "3d6 Fire")
        self.assertEqual(s_map["Dragon's Breath"]["level"], "2nd Level")

    def test_sanitize_weapons_filter_spells(self):
        raw_weapons = [
            {"name": "Dagger", "atk_bonus": "+4", "damage": "1d4+2 Piercing", "notes": "Finesse, Light"},
            {"name": "Scorpion Staff", "atk_bonus": "+4", "damage": "1d6+2 Poison", "notes": "Simple, Topple"},
            {"name": "Shocking Grasp", "atk_bonus": "+5", "damage": "1d8 Lightning", "notes": "V/S"},
            {"name": "Sorcerous Burst", "atk_bonus": "+5", "damage": "1d8 Acid", "notes": "V/S"},
            {"name": "Unarmed Strike", "atk_bonus": "+0", "damage": "0 Bludgeoning", "notes": ""}
        ]
        spells = [
            {"name": "Shocking Grasp", "level": "Cantrip"},
            {"name": "Sorcerous Burst", "level": "Cantrip"}
        ]
        clean_weapons = sanitize_character_weapons(raw_weapons, spells)
        self.assertEqual(len(clean_weapons), 3)
        names = [w["name"] for w in clean_weapons]
        self.assertEqual(names, ["Dagger", "Scorpion Staff", "Unarmed Strike"])
        self.assertNotIn("Shocking Grasp", names)
        self.assertNotIn("Sorcerous Burst", names)

    def test_resolve_dnd_stat_swapped_and_standard(self):
        # Swapped layout (Modifier Top): score_val has modifier, mod_val has score
        self.assertEqual(resolve_dnd_stat("-1", "8"), 8)
        self.assertEqual(resolve_dnd_stat("+1", "12"), 12)
        self.assertEqual(resolve_dnd_stat("+2", "15"), 15)
        self.assertEqual(resolve_dnd_stat("+3", "17"), 17)
        self.assertEqual(resolve_dnd_stat("+0", "10"), 10)
        self.assertEqual(resolve_dnd_stat("+1", "13"), 13)

        # Standard layout: score_val has score, mod_val has modifier
        self.assertEqual(resolve_dnd_stat("8", "-1"), 8)
        self.assertEqual(resolve_dnd_stat("12", "+1"), 12)
        self.assertEqual(resolve_dnd_stat("15", "+2"), 15)
        self.assertEqual(resolve_dnd_stat("17", "+3"), 17)
        self.assertEqual(resolve_dnd_stat("10", "+0"), 10)
        self.assertEqual(resolve_dnd_stat("13", "+1"), 13)

    def test_seraphina_pdf_ingestion_and_defenses(self):
        seraphina_pdf = r"C:\Users\retho\.gemini\antigravity\brain\8eeb041e-1fc6-439d-824e-5dea41734b25\.user_uploaded\media_1793746662714_c6bca833.pdf"
        if not os.path.exists(seraphina_pdf):
            self.skipTest("Seraphina test PDF artifact not present.")

        with open(seraphina_pdf, "rb") as f:
            pdf_bytes = f.read()

        ttrpg = TTRPGService()
        parsed_chars = ttrpg._parse_character_from_document("Seraphina_Lightbrew.pdf", pdf_bytes)
        self.assertGreater(len(parsed_chars), 0)
        char = parsed_chars[0]

        # Ability scores check
        attrs = char.get("attributes", {})
        self.assertEqual(attrs.get("Strength"), 8)
        self.assertEqual(attrs.get("Dexterity"), 12)
        self.assertEqual(attrs.get("Constitution"), 15)
        self.assertEqual(attrs.get("Intelligence"), 17)
        self.assertEqual(attrs.get("Wisdom"), 10)
        self.assertEqual(attrs.get("Charisma"), 13)

        # Initiative & Defenses & Senses
        self.assertEqual(char.get("initiative"), "+1")
        self.assertEqual(char.get("defenses"), "Resistances - Necrotic, Radiant")
        self.assertEqual(char.get("resistances"), "Resistances - Necrotic, Radiant")
        self.assertEqual(char.get("senses"), "Darkvision 60 ft.")

        # Passives check
        passives = char.get("passives", {})
        self.assertEqual(passives.get("perception"), 10)
        self.assertEqual(passives.get("insight"), 12)
        self.assertEqual(passives.get("investigation"), 15)


if __name__ == "__main__":
    unittest.main()

