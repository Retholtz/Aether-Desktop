"""
tests/test_modular_ttrpg_parsers.py - Automated Test Suite
Validates PF2e Spells/Focus, CoC 7e MP/Tomes, and Cyberpunk RED Cyberware/Ablation.
"""
import unittest
import asyncio
from unittest.mock import patch, MagicMock
from core.parsers.pf2e_parser import PF2eParser
from core.parsers.coc7e_parser import CoC7eParser
from core.parsers.cyberpunk_red_parser import CyberpunkRedParser
from core.ttrpg_service import TTRPGService
from tools.ttrpg_tools import register_modular_system_tools


class TestModularTTRPGParsers(unittest.TestCase):
    def setUp(self):
        self.pf2e = PF2eParser()
        self.coc = CoC7eParser()
        self.cyberpunk = CyberpunkRedParser()

    @patch.object(PF2eParser, "extract_form_fields")
    def test_pf2e_spellcasting_and_focus_parsing(self, mock_extract):
        mock_fields = {
            "Character Name": "Kyra",
            "Class": "Cleric",
            "Level": "3",
            "divine": "Yes",
            "SpellDC": "19",
            "SpellAttack": "+9",
            "SpellSlots_Rank1_Total": "3",
            "SpellSlots_Rank1_Remaining": "2",
            "FocusPoints_Max": "2",
            "FocusSpell_Name_1": "Healing Outburst",
            "FocusSpell_Actions_1": "◆◆"
        }
        mock_extract.return_value = (mock_fields, "MAGICAL TRADITION: Divine\nSPELL SLOTS")

        res = self.pf2e.parse_sheet(b"dummy_stream")
        spells = res["native_system_data"]["spellcasting"]

        self.assertEqual(spells["tradition"], "Divine")
        self.assertEqual(spells["spell_slots"]["rank_1"]["max"], 3)
        self.assertEqual(spells["spell_slots"]["rank_1"]["current"], 2)
        self.assertEqual(spells["focus_magic"]["focus_points"]["max"], 2)
        self.assertEqual(spells["focus_magic"]["spells"][0]["name"], "Healing Outburst")

    @patch.object(CoC7eParser, "extract_form_fields")
    def test_coc_occult_tomes_and_mp_parsing(self, mock_extract):
        mock_fields = {
            "CharacterName": "Harvey Walters",
            "Occupation": "Journalist",
            "POW": "80",
            "Sanity_Current": "70",
            "Cthulhu Mythos": "12",
            "Arcane_Spells_Tomes": """
            Cultes des Goules (French, 1702)
            Deflect Evil (Cost: 2 MP, 1d3 Sanity)
            Contact Ghoul - 5 MP, 1d6 SAN
            """
        }
        mock_extract.return_value = (mock_fields, "CALL OF CTHULHU\nARCANE TOMES & SPELLS")

        res = self.coc.parse_sheet(b"dummy_stream")
        native = res["native_system_data"]
        occult = native["occult_data"]

        # POW 80 // 5 = 16 Max MP
        self.assertEqual(native["vitals"]["magic_points"]["max"], 16)
        self.assertEqual(len(occult["tomes"]), 1)
        self.assertIn("Cultes des Goules", occult["tomes"][0]["title"])

        self.assertEqual(len(occult["spells"]), 2)
        self.assertEqual(occult["spells"][0]["name"], "Deflect Evil")
        self.assertEqual(occult["spells"][0]["mp_cost"], 2)
        self.assertEqual(occult["spells"][0]["san_cost"], "1d3")

    @patch.object(CyberpunkRedParser, "extract_form_fields")
    def test_cyberpunk_red_cyberware_and_role_parsing(self, mock_extract):
        mock_fields = {
            "Handle": "Johnny",
            "Role": "Solo",
            "Rank": "5",
            "RoleNotes": "Combat Awareness +5",
            "BODY": "8",
            "REF": "8",
            "DEX": "7",
            "Neural_Link": "Yes",
            "Neural_Link_Option_1": "Sandevistan Speedware",
            "Right_Cyberarm": "Yes",
            "Right_Cyberarm_Option_1": "Popup Grenade Launcher",
            "Head_SP": "11",
            "Body_SP": "11"
        }
        mock_extract.return_value = (mock_fields, "CYBERPUNK RED\nCYBERWARE\nROLE ABILITY")

        res = self.cyberpunk.parse_sheet(b"dummy_stream")
        native = res["native_system_data"]
        cw = native["cyberware"]

        self.assertEqual(native["role_ability"]["name"], "Combat Awareness")
        self.assertEqual(native["role_ability"]["rank"], 5)
        self.assertTrue(cw["foundational_chassis"]["Neural Link"])
        self.assertTrue(cw["foundational_chassis"]["Right Cyberarm"])

        item_names = [item["name"] for item in cw["installed_items"]]
        self.assertIn("Sandevistan Speedware", item_names)
        self.assertIn("Popup Grenade Launcher", item_names)

        self.assertEqual(native["vitals"]["armor"]["head"]["sp_current"], 11)
        self.assertEqual(native["vitals"]["armor"]["body"]["sp_current"], 11)

    def test_cyberpunk_red_damage_ablation(self):
        class MockDispatcher:
            def __init__(self):
                self.tools = {}

            def register_tool(self, name, description, parameters, handler):
                self.tools[name] = handler

        dispatcher = MockDispatcher()
        service = MagicMock(spec=TTRPGService)

        cyber_char = {
            "name": "Johnny",
            "vitals": {
                "health": {"current": 40, "max": 40},
                "armor": {
                    "body": {"sp_current": 11, "sp_max": 11}
                }
            },
            "conditions": []
        }

        service._find_character.return_value = ("char_johnny", cyber_char)
        register_modular_system_tools(dispatcher, service)

        # 1. Attack does 10 damage <= SP 11: Absorbed completely, SP unchanged
        res_absorbed = asyncio.run(dispatcher.tools["apply_cyberpunk_damage"]("Johnny", raw_damage=10, hit_location="body"))
        self.assertEqual(res_absorbed["status"], "absorbed")
        self.assertEqual(res_absorbed["damage_dealt"], 0)
        self.assertEqual(cyber_char["vitals"]["armor"]["body"]["sp_current"], 11)
        self.assertEqual(cyber_char["vitals"]["health"]["current"], 40)

        # 2. Attack does 15 damage > SP 11: Penetrates for 4 damage, ablates SP to 10
        res_pen = asyncio.run(dispatcher.tools["apply_cyberpunk_damage"]("Johnny", raw_damage=15, hit_location="body"))
        self.assertEqual(res_pen["status"], "penetrated")
        self.assertEqual(res_pen["damage_dealt"], 4)
        self.assertEqual(res_pen["remaining_hp"], 36)
        self.assertEqual(res_pen["new_sp"], 10)
        self.assertEqual(cyber_char["vitals"]["armor"]["body"]["sp_current"], 10)
        self.assertEqual(cyber_char["vitals"]["health"]["current"], 36)


if __name__ == "__main__":
    unittest.main()

