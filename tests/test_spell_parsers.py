"""
tests/test_spell_parsers.py - Automated Unit Test Suite
Validates Pathfinder 2e slot/focus extraction, Call of Cthulhu MP/Tome/Spell parsing,
and deterministic spell execution agent tools.
"""
import unittest
import asyncio
from unittest.mock import patch, MagicMock
from core.parsers.pf2e_parser import PF2eParser
from core.parsers.coc7e_parser import CoC7eParser
from tools.spell_tools import register_spell_tools
from core.ttrpg_service import TTRPGService


class TestSpellParsers(unittest.TestCase):
    def setUp(self):
        self.pf2e_parser = PF2eParser()
        self.coc_parser = CoC7eParser()

    @patch.object(PF2eParser, "extract_form_fields")
    def test_pf2e_spellcasting_page_extraction(self, mock_extract):
        mock_fields = {
            "Character Name": "Valeros",
            "Class": "Wizard",
            "Level": "3",
            "arcane": "Yes",
            "SpellDC": "19",
            "SpellAttack": "+9",
            "SpellSlots_Rank1_Total": "3",
            "SpellSlots_Rank1_Remaining": "2",
            "SpellSlots_Rank2_Total": "2",
            "SpellSlots_Rank2_Remaining": "2",
            "FocusPoints_Max": "2",
            "FocusSpell_Name_1": "Force Bolt",
            "FocusSpell_Actions_1": "◆",
            "Spell_Name_1": "Breathe Fire",
            "Spell_Rank_1": "1",
            "Spell_Prep_1": "Yes"
        }
        mock_extract.return_value = (mock_fields, "MAGICAL TRADITION: Arcane\nSPELL SLOTS")

        res = self.pf2e_parser.parse_sheet(b"dummy_stream")
        native = res["native_system_data"]["spellcasting"]

        # Validate tradition and stats
        self.assertTrue(native["is_caster"])
        self.assertEqual(native["tradition"], "Arcane")
        self.assertEqual(native["spell_dc"], 19)
        self.assertEqual(native["spell_attack"], 9)

        # Validate Slots
        self.assertEqual(native["spell_slots"]["rank_1"]["max"], 3)
        self.assertEqual(native["spell_slots"]["rank_1"]["current"], 2)
        self.assertEqual(native["spell_slots"]["rank_2"]["max"], 2)

        # Validate Focus Magic
        self.assertEqual(native["focus_magic"]["focus_points"]["max"], 2)
        self.assertEqual(len(native["focus_magic"]["spells"]), 1)
        self.assertEqual(native["focus_magic"]["spells"][0]["name"], "Force Bolt")

    @patch.object(CoC7eParser, "extract_form_fields")
    def test_coc_tomes_and_spells_parsing(self, mock_extract):
        mock_fields = {
            "CharacterName": "Dr. Armitage",
            "Occupation": "Professor",
            "POW": "75",
            "Sanity_Current": "68",
            "Arcane_Spells_Tomes": """
            Necronomicon (Latin translation, 1582)
            Shriveling (Cost: 3 MP, 1d4 Sanity)
            Dread Curse of Azathoth - 4 MP, 1d6 SAN
            """
        }
        mock_extract.return_value = (mock_fields, "CALL OF CTHULHU\nARCANE TOMES & SPELLS")

        res = self.coc_parser.parse_sheet(b"dummy_stream")
        native = res["native_system_data"]
        occult = native["occult_data"]

        # Validate Magic Points derivation (75 POW // 5 = 15 MP)
        self.assertEqual(native["vitals"]["magic_points"]["max"], 15)

        # Validate Tome Extraction
        self.assertEqual(len(occult["tomes"]), 1)
        self.assertIn("Necronomicon", occult["tomes"][0]["title"])
        self.assertEqual(occult["tomes"][0]["language"], "Latin")

        # Validate Spell Parsing with Costs
        self.assertEqual(len(occult["spells"]), 2)
        shrivel = occult["spells"][0]
        self.assertEqual(shrivel["name"], "Shriveling")
        self.assertEqual(shrivel["mp_cost"], 3)
        self.assertEqual(shrivel["san_cost"], "1d4")

        curse = occult["spells"][1]
        self.assertEqual(curse["name"], "Dread Curse of Azathoth")
        self.assertEqual(curse["mp_cost"], 4)
        self.assertEqual(curse["san_cost"], "1d6")

    def test_spell_tools_execution(self):
        class MockDispatcher:
            def __init__(self):
                self.tools = {}

            def register_tool(self, name, description, parameters, handler):
                self.tools[name] = handler

        dispatcher = MockDispatcher()
        service = MagicMock(spec=TTRPGService)

        # Set up a mock PF2e character
        pf_char = {
            "name": "Valeros",
            "native_system_data": {
                "spellcasting": {
                    "spell_slots": {
                        "rank_1": {"current": 2, "max": 3}
                    },
                    "focus_magic": {
                        "focus_points": {"current": 2, "max": 2}
                    }
                }
            }
        }

        # Set up a mock CoC character
        coc_char = {
            "name": "Dr. Armitage",
            "vitals": {
                "magic_points": {"current": 10, "max": 15},
                "sanity": {"current": 65, "max": 99}
            }
        }

        def mock_find(name):
            if "Valeros" in name:
                return "char_valeros", pf_char
            elif "Armitage" in name:
                return "char_armitage", coc_char
            return None, None

        service._find_character.side_effect = mock_find
        service.parse_and_roll.return_value = {"status": "success", "total": 3}

        register_spell_tools(dispatcher, service)
        self.assertIn("cast_pathfinder_spell", dispatcher.tools)
        self.assertIn("cast_coc_spell", dispatcher.tools)

        # Test casting PF2e slot spell
        res_slot = asyncio.run(dispatcher.tools["cast_pathfinder_spell"]("Valeros", "Breathe Fire", 1))
        self.assertEqual(res_slot["status"], "success")
        self.assertEqual(pf_char["native_system_data"]["spellcasting"]["spell_slots"]["rank_1"]["current"], 1)

        # Test casting PF2e focus spell
        res_focus = asyncio.run(dispatcher.tools["cast_pathfinder_spell"]("Valeros", "Force Bolt", 1, is_focus_spell=True))
        self.assertEqual(res_focus["status"], "success")
        self.assertEqual(pf_char["native_system_data"]["spellcasting"]["focus_magic"]["focus_points"]["current"], 1)

        # Test casting CoC spell
        res_coc = asyncio.run(dispatcher.tools["cast_coc_spell"]("Dr. Armitage", "Shriveling", mp_cost=3, san_loss_formula="1d4"))
        self.assertEqual(res_coc["status"], "success")
        self.assertEqual(res_coc["mp_spent"], 3)
        self.assertEqual(res_coc["mp_remaining"], 7)
        self.assertEqual(res_coc["sanity_lost"], 3)
        self.assertEqual(res_coc["new_sanity"], 62)


if __name__ == "__main__":
    unittest.main()

