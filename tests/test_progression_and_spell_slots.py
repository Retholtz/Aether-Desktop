"""
tests/test_progression_and_spell_slots.py - Verification Suite
Validates 1st through 9th level slot scaling and milestone progression for Spurt & Seraphina.
"""
import unittest
from core.ttrpg_service import level_up_dnd_character


class TestProgressionAndSpellSlots(unittest.TestCase):
    def setUp(self):
        # Spurt baseline (Level 3 Sorcerer)
        self.spurt = {
            "name": "Spurt the Sorcerer",
            "level": 3,
            "vitals": {"health": {"current": 17, "max": 17}},
            "custom_meters": {
                "sorcery_points": {"label": "Sorcery Points", "current": 3, "max": 3}
            },
            "native_system_data": {
                "class_level": "Sorcerer 3",
                "spellcasting_ability": "CHA",
                "ability_scores": {"STR": 7, "DEX": 14, "CON": 12, "INT": 12, "WIS": 10, "CHA": 16},
                "spell_slots": {
                    "level_1": {"current": 4, "max": 4},
                    "level_2": {"current": 2, "max": 2}
                }
            }
        }

        # Seraphina baseline (Level 1 Artificer)
        self.seraphina = {
            "name": "Seraphina Lightbrew",
            "level": 1,
            "vitals": {"health": {"current": 10, "max": 10}},
            "custom_meters": {},
            "native_system_data": {
                "class_level": "Artificer 1",
                "spellcasting_ability": "INT",
                "ability_scores": {"STR": 8, "DEX": 12, "CON": 15, "INT": 17, "WIS": 10, "CHA": 13},
                "spell_slots": {
                    "level_1": {"current": 2, "max": 2}
                }
            }
        }

    def test_spurt_level_up_to_5_unlocks_3rd_level_spells(self):
        res = level_up_dnd_character(self.spurt, target_level=5)
        self.assertEqual(res["status"], "success")

        native = self.spurt["native_system_data"]
        slots = native["spell_slots"]

        # Level 5 Full Caster: 4 / 3 / 2 slots
        self.assertEqual(slots["level_1"]["max"], 4)
        self.assertEqual(slots["level_2"]["max"], 3)
        self.assertEqual(slots["level_3"]["max"], 2)
        self.assertEqual(slots["level_4"]["max"], 0)

        # Proficiency & Save DC (+3 prof, +3 CHA -> DC 14)
        self.assertEqual(res["proficiency_bonus"], 3)
        self.assertEqual(res["save_dc"], 14)

        # Sorcery Points bumped to 5
        self.assertEqual(self.spurt["custom_meters"]["sorcery_points"]["max"], 5)

        # Hit points bumped (d6 sorcerer: avg 4 + 1 CON = 5 HP per level * 2 levels = +10 HP)
        self.assertEqual(self.spurt["vitals"]["health"]["max"], 27)

    def test_spurt_level_up_to_17_unlocks_9th_level_spells(self):
        res = level_up_dnd_character(self.spurt, target_level=17)
        self.assertEqual(res["status"], "success")

        native = self.spurt["native_system_data"]
        slots = native["spell_slots"]

        # Level 17 Full Caster: 9th level slot unlocked
        self.assertEqual(slots["level_9"]["max"], 1)
        self.assertEqual(res["proficiency_bonus"], 6)
        self.assertEqual(res["save_dc"], 17)  # 8 + 6 + 3

    def test_seraphina_artificer_half_caster_caps_at_5th_level(self):
        # Level up Seraphina to 17
        res = level_up_dnd_character(self.seraphina, target_level=17)
        self.assertEqual(res["status"], "success")

        native = self.seraphina["native_system_data"]
        slots = native["spell_slots"]

        # Artificer 17 unlocks 5th level (1 slot), 0 for 6th-9th
        self.assertEqual(slots["level_5"]["max"], 1)
        self.assertEqual(slots["level_6"]["max"], 0)
        self.assertEqual(slots["level_9"]["max"], 0)

        # Prepared cap: INT (+3) + half level (8) = 11 prepared spells
        self.assertEqual(res["max_prepared_spells"], 11)


if __name__ == "__main__":
    unittest.main()

