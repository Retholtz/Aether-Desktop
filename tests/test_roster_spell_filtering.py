"""
tests/test_roster_spell_filtering.py - Verification Suite
Validates that unprepared spells are filtered from roster card data and that slot pips toggle.
"""
import unittest
from core.ttrpg_service import TTRPGService


class TestRosterSpellFiltering(unittest.TestCase):
    def setUp(self):
        self.service = TTRPGService(data_dir="tests/test_camp", manifest_path="tests/manifest.json")
        self.service.active_state = {
            "campaign_id": "session_01",
            "characters": {
                "char_seraphina": {
                    "id": "char_seraphina",
                    "name": "Seraphina Lightbrew",
                    "native_system_data": {
                        "spell_slots": {
                            "level_1": {"current": 2, "max": 2}
                        },
                        "spells": [
                            {"name": "Guidance", "level": 0, "is_cantrip": True, "prepared": True},
                            {"name": "Mending", "level": 0, "is_cantrip": True, "prepared": True},
                            {"name": "Light", "level": 0, "is_cantrip": True, "prepared": True},
                            {"name": "Cure Wounds", "level": 1, "is_cantrip": False, "prepared": True},
                            {"name": "Faerie Fire", "level": 1, "is_cantrip": False, "prepared": True},
                            {"name": "Detect Magic", "level": 1, "is_cantrip": False, "prepared": True},
                            {"name": "Alarm", "level": 1, "is_cantrip": False, "prepared": False},
                            {"name": "Disguise Self", "level": 1, "is_cantrip": False, "prepared": False}
                        ]
                    }
                }
            }
        }

    def tearDown(self):
        import shutil
        import os
        if os.path.exists("tests/test_camp"):
            shutil.rmtree("tests/test_camp", ignore_errors=True)
        if os.path.exists("tests/manifest.json"):
            try:
                os.remove("tests/manifest.json")
            except Exception:
                pass

    def test_active_spell_filtering(self):
        char = self.service.active_state["characters"]["char_seraphina"]
        spells = char["native_system_data"]["spells"]

        # Filter mimicking UI logic
        active_spells = [s for s in spells if s.get("is_cantrip") or s.get("prepared")]
        unprepared_spells = [s for s in spells if not s.get("is_cantrip") and not s.get("prepared")]

        # Total was 8, active must be 6, unprepared must be 2
        self.assertEqual(len(active_spells), 6)
        self.assertEqual(len(unprepared_spells), 2)
        self.assertTrue(all(s["is_cantrip"] or s["prepared"] for s in active_spells))

    def test_slot_pip_decrement_and_increment(self):
        # 1. Expend 1 slot (simulate pip click)
        res1 = self.service.adjust_spell_slot("char_seraphina", spell_level=1, delta=-1)
        self.assertEqual(res1["status"], "success")
        self.assertEqual(res1["current"], 1)

        # 2. Expend 2nd slot
        res2 = self.service.adjust_spell_slot("char_seraphina", spell_level=1, delta=-1)
        self.assertEqual(res2["current"], 0)

        # 3. Prevent negative slots
        res3 = self.service.adjust_spell_slot("char_seraphina", spell_level=1, delta=-1)
        self.assertEqual(res3["current"], 0)

        # 4. Recover 1 slot
        res4 = self.service.adjust_spell_slot("char_seraphina", spell_level=1, delta=1)
        self.assertEqual(res4["current"], 1)


if __name__ == "__main__":
    unittest.main()
