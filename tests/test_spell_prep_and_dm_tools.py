"""
tests/test_spell_prep_and_dm_tools.py - Spell Prep & DM Tools Verification
"""
import unittest
from unittest.mock import MagicMock, patch
from core.parsers.dnd5e_parser import DnD5eParser
from core.ttrpg_service import TTRPGService
from tools.ttrpg_tools import register_dm_adjudication_tools


class TestSpellPrepAndDMTools(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.parser = DnD5eParser()
        self.service = TTRPGService(storage_dir="tests/test_dm_camp", manifest_path="tests/manifest.json")
        self.service.active_state = {
            "campaign_id": "test_session",
            "characters": {
                "char_spurt": {
                    "id": "char_spurt",
                    "name": "Spurt",
                    "vitals": {"health": {"current": 17, "max": 17}},
                    "conditions": [],
                    "custom_meters": {
                        "sorcery_points": {"label": "Sorcery Points", "current": 3, "max": 3, "reset_on": "long_rest"},
                        "draconic_cry": {"label": "Draconic Cry", "current": 2, "max": 2, "reset_on": "long_rest"}
                    },
                    "native_system_data": {
                        "spell_slots": {
                            "level_1": {"current": 4, "max": 4},
                            "level_2": {"current": 2, "max": 2}
                        },
                        "spells": [
                            {"name": "Shocking Grasp", "level": 0, "is_cantrip": True, "prepared": True},
                            {"name": "Burning Hands", "level": 1, "is_cantrip": False, "prepared": True},
                            {"name": "Spider Climb", "level": 2, "is_cantrip": False, "prepared": False}
                        ]
                    }
                }
            }
        }
        self.dispatcher = MagicMock()
        self.ui_callback = MagicMock()
        register_dm_adjudication_tools(self.dispatcher, self.service, self.ui_callback)

    def tearDown(self):
        import shutil, os
        if os.path.exists("tests/test_dm_camp"):
            shutil.rmtree("tests/test_dm_camp", ignore_errors=True)
        if os.path.exists("tests/manifest.json"):
            try:
                os.remove("tests/manifest.json")
            except OSError:
                pass

    async def asyncTearDown(self):
        self.tearDown()

    @patch.object(DnD5eParser, "extract_form_fields")
    def test_parser_unprepares_leveled_spells_and_filters_speed(self, mock_extract):
        mock_fields = {
            "CharacterName": "Spurt",
            "ClassLevel": "Sorcerer 3",
            "CHA": "16", "DEX": "14", "CON": "12", "INT": "12", "STR": "7", "WIS": "10",
            "SlotsTotal1": "4", "SlotsTotal2": "2",
            "FeaturesAndTraits": "Draconic Cry 2/Long Rest\nFont of Magic: Sorcery Points 3 SP\nSpeed 30 ft. Walking"
        }
        mock_text = """
        D&D BEYOND
        SAVING THROWS
        CANTRIPS ===
        Shocking Grasp Sorcerer 1A Touch V,S
        1st LEVEL===
        Burning Hands Sorcerer DEX 13 1A Self V,S
        2nd LEVEL===
        Spider Climb Sorcerer 1A Touch V,S,M
        """
        mock_extract.return_value = (mock_fields, mock_text)

        res = self.parser.parse_sheet(b"dummy")
        native = res["native_system_data"]
        meters = native["custom_meters"]
        spells = native["spells"]

        # 1. Speed is filtered out
        self.assertNotIn("speed", meters)

        # 2. Sorcery Points and Draconic Cry extracted
        self.assertEqual(meters["sorcery_points"]["max"], 3)
        self.assertEqual(meters["draconic_cry"]["max"], 2)

        # 3. Preparation check: Known Casters (Sorcerer/Warlock/Bard) auto-ready all spells on import
        grasp = next(s for s in spells if s["name"] == "Shocking Grasp")
        burning = next(s for s in spells if s["name"] == "Burning Hands")
        self.assertTrue(grasp["prepared"])
        self.assertTrue(burning["prepared"])

    async def test_cast_spell_deducts_slot_and_rejects_unprepared(self):
        # Extract handlers registered in mock dispatcher
        tools = {call.kwargs["name"]: call.kwargs["handler"] for call in self.dispatcher.register_tool.call_args_list}
        cast_fn = tools["cast_spell"]

        # 1. Cast prepared Burning Hands
        res = await cast_fn("Spurt", "Burning Hands", 1)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["slots_remaining"], 3)

        # 2. Cast unprepared Spider Climb (Must fail)
        fail_res = await cast_fn("Spurt", "Spider Climb", 2)
        self.assertEqual(fail_res["status"], "error")
        self.assertIn("not currently prepared", fail_res["message"])

    async def test_modify_resource_meter_and_long_rest(self):
        tools = {call.kwargs["name"]: call.kwargs["handler"] for call in self.dispatcher.register_tool.call_args_list}
        meter_fn = tools["modify_resource_meter"]
        rest_fn = tools["execute_rest"]

        # Spend 2 Sorcery Points
        await meter_fn("Spurt", "sorcery_points", -2)
        sp_meter = self.service.active_state["characters"]["char_spurt"]["custom_meters"]["sorcery_points"]
        self.assertEqual(sp_meter["current"], 1)

        # Execute Long Rest
        await rest_fn("long")
        self.assertEqual(sp_meter["current"], 3)
        slots_1 = self.service.active_state["characters"]["char_spurt"]["native_system_data"]["spell_slots"]["level_1"]
        self.assertEqual(slots_1["current"], 4)

    async def test_shadow_and_sam_cast_dimension_door(self):
        """Verifies that Shadow (Paladin/Sorcerer) and Sam (Warlock) can both cast Dimension Door."""
        # Add Shadow (Paladin 10 / Sorcerer 10) with initially None spell_slots
        self.service.active_state["characters"]["char_shadow"] = {
            "id": "char_shadow",
            "name": "Shadow",
            "archetype": "Paladin 10 / Sorcerer 10",
            "level": 20,
            "spells": ["Dimension Door", "Fireball", "Sorcerous Burst"],
            "spell_slots": None
        }
        # Add Sam (Warlock 20) with initially None spell_slots
        self.service.active_state["characters"]["char_sam"] = {
            "id": "char_sam",
            "name": "Sam",
            "archetype": "Warlock 20",
            "level": 20,
            "spells": ["Dimension Door", "Eldritch Blast", "Hellish Rebuke"],
            "spell_slots": None
        }

        tools = {call.kwargs["name"]: call.kwargs["handler"] for call in self.dispatcher.register_tool.call_args_list}
        cast_fn = tools["cast_spell"]

        # 1. Shadow casts Dimension Door (4th level spell)
        res_shadow = await cast_fn("Shadow", "Dimension Door", 4)
        self.assertEqual(res_shadow["status"], "success")
        self.assertEqual(res_shadow["slot_level_expended"], 4)
        self.assertEqual(res_shadow["slots_remaining"], 2) # Started with 3 Level 4 slots, 1 expended -> 2 remaining

        # 2. Sam casts Dimension Door (4th level spell) using Level 5 Pact Magic slot!
        res_sam = await cast_fn("Sam", "Dimension Door", 4)
        self.assertEqual(res_sam["status"], "success")
        self.assertEqual(res_sam["slot_level_expended"], 5)
        self.assertTrue(res_sam["is_pact_magic"])
        self.assertEqual(res_sam["slots_remaining"], 3) # Started with 4 Level 5 pact slots, 1 expended -> 3 remaining

    async def test_upcast_when_exact_slot_depleted(self):
        """Verifies that a spell automatically upcasts using the lowest available higher slot when exact slot is depleted."""
        self.service.active_state["characters"]["char_spurt"]["spell_slots"] = {
            "level_1": {"current": 0, "max": 4},
            "level_2": {"current": 2, "max": 2}
        }
        self.service.active_state["characters"]["char_spurt"]["native_system_data"]["spell_slots"] = {
            "level_1": {"current": 0, "max": 4},
            "level_2": {"current": 2, "max": 2}
        }

        tools = {call.kwargs["name"]: call.kwargs["handler"] for call in self.dispatcher.register_tool.call_args_list}
        cast_fn = tools["cast_spell"]

        # Burning Hands is Level 1, but Level 1 slots are 0 -> should upcast using Level 2 slot
        res = await cast_fn("Spurt", "Burning Hands", 1)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["slot_level_expended"], 2)
        self.assertEqual(res["slots_remaining"], 1)

    def test_multiclass_and_vault_unprepped_spells_enforcement(self):
        """Verifies that multiclass and prepared casters have leveled spells unprepped in vault and clamped to legal cap."""
        from core.parsers.spell_registry import sanitize_parsed_spells

        # 1. Test raw spells sanitization
        raw_spells = [
            {"name": "Thunderclap", "level": 0, "is_cantrip": True},
            {"name": "Bless", "level": 1},
            {"name": "Cure Wounds", "level": 1, "prepared": True},
            {"name": "Dimension Door", "level": 4, "prepared": False},
            "Fireball (8d6)"
        ]
        sanitized = sanitize_parsed_spells(raw_spells)
        # Cantrip is always prepared
        self.assertTrue(next(s for s in sanitized if s["name"] == "Thunderclap")["prepared"])
        # Cure Wounds had explicit True
        self.assertTrue(next(s for s in sanitized if s["name"] == "Cure Wounds")["prepared"])
        # Dimension Door had explicit False
        self.assertFalse(next(s for s in sanitized if s["name"] == "Dimension Door")["prepared"])

        # 2. Test auto-clamp in ensure_character_spell_slots when over capacity
        overprepped_char = {
            "name": "Overprepped Wizard",
            "archetype": "Wizard 5",
            "level": 5,
            "max_prepared_leveled_spells": 2,
            "spells": [
                {"name": "Fire Bolt", "level": 0, "is_cantrip": True, "prepared": True},
                {"name": "Magic Missile", "level": 1, "is_cantrip": False, "prepared": True},
                {"name": "Shield", "level": 1, "is_cantrip": False, "prepared": True},
                {"name": "Misty Step", "level": 2, "is_cantrip": False, "prepared": True},
                {"name": "Fireball", "level": 3, "is_cantrip": False, "prepared": True}
            ]
        }
        self.service.ensure_character_spell_slots(overprepped_char)
        prepared_leveled = [s for s in overprepped_char["spells"] if not s.get("is_cantrip") and s.get("prepared")]
        # Exactly 2 prepared leveled spells remaining (clamped to max_prep=2)
        self.assertEqual(len(prepared_leveled), 2)
        # Cantrip is untouched
        self.assertTrue(next(s for s in overprepped_char["spells"] if s["name"] == "Fire Bolt")["prepared"])

if __name__ == "__main__":
    unittest.main()
