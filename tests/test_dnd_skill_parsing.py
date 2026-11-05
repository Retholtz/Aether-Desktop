"""
tests/test_dnd_skill_parsing.py - Verification suite for D&D 5E Skill & Saving Throw Proficiencies.
"""
import os
import unittest
from core.ttrpg_service import TTRPGService
from core.parsers.dnd5e_parser import DnD5eParser
from core.project_service import ProjectService


class TestDnDSkillProficiencies(unittest.TestCase):
    def setUp(self):
        self.test_dir = "tests/test_campaign_skills"
        self.manifest_file = "tests/test_skills_manifest.json"
        self.service = TTRPGService(data_dir=self.test_dir, manifest_path=self.manifest_file)
        self.seraphina_pdf_path = "C:/Users/retho/.gemini/antigravity/brain/8eeb041e-1fc6-439d-824e-5dea41734b25/.user_uploaded/media_1793816022545_869d3d8b.pdf"

    def tearDown(self):
        import shutil
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)
        if os.path.exists("tests/test_vault_parens"):
            shutil.rmtree("tests/test_vault_parens", ignore_errors=True)
        if os.path.exists(self.manifest_file):
            try:
                os.remove(self.manifest_file)
            except OSError:
                pass

    def test_seraphina_pdf_skill_proficiencies(self):
        """Verifies that Seraphina Lightbrew only parses Arcana, Insight, Investigation, and Persuasion as proficient."""
        if not os.path.exists(self.seraphina_pdf_path):
            self.skipTest("Seraphina PDF not found in artifacts directory.")

        with open(self.seraphina_pdf_path, "rb") as f:
            pdf_bytes = f.read()

        parsed = self.service._parse_character_from_document("Seraphina.pdf", "", pdf_bytes)
        self.assertIsNotNone(parsed)
        self.assertGreaterEqual(len(parsed), 1)

        char = parsed[0]
        skills = char.get("skills", {})

        # Verify exactly the 4 proficient skills
        proficient_detected = [skill for skill, val in skills.items() if "(Proficient)" in str(val)]
        expected_proficient = ["Arcana", "Insight", "Investigation", "Persuasion"]
        self.assertCountEqual(proficient_detected, expected_proficient, f"Detected skills: {proficient_detected}")

        # Check values
        self.assertIn("+5 (Proficient)", skills["Arcana"])
        self.assertIn("+2 (Proficient)", skills["Insight"])
        self.assertIn("+5 (Proficient)", skills["Investigation"])
        self.assertIn("+3 (Proficient)", skills["Persuasion"])

        # Check non-proficient skills
        self.assertEqual(skills["Athletics"], "-1")
        self.assertEqual(skills["History"], "+3")
        self.assertEqual(skills["Nature"], "+3")
        self.assertEqual(skills["Religion"], "+3")
        self.assertEqual(skills["Acrobatics"], "+1")
        self.assertEqual(skills["Stealth"], "+1")
        self.assertEqual(skills["Perception"], "+0")

        # Saving throws
        saves = char.get("saving_throws", {})
        self.assertEqual(saves.get("Constitution"), "+4")
        self.assertEqual(saves.get("Intelligence"), "+5")
        self.assertEqual(saves.get("Strength"), "-1")
        self.assertEqual(saves.get("Dexterity"), "+1")
        self.assertEqual(saves.get("Wisdom"), "+0")
        self.assertEqual(saves.get("Charisma"), "+1")

    def test_dnd5e_parser_mathematical_fallback(self):
        """Tests mathematical derivation when explicit Prof checkboxes are missing."""
        parser = DnD5eParser()
        # STR 10 (mod 0), DEX 14 (mod +2), CON 12 (mod +1), INT 16 (mod +3), WIS 8 (mod -1), CHA 14 (mod +2), ProfBonus: +2
        fields = {
            "CharacterName": "Test Wizard",
            "CLASS  LEVEL": "Wizard 1",
            "STR": "10", "DEX": "14", "CON": "12", "INT": "16", "WIS": "8", "CHA": "14",
            "ProfBonus": "+2",
            # Proficient skills (+profBonus added)
            "Arcana": "+5",       # 3 + 2 = 5 -> Proficient
            "History": "+5",      # 3 + 2 = 5 -> Proficient
            # Unproficient skills (equal to base modifier)
            "Investigation": "+3", # 3 -> Not proficient
            "Nature": "+3",        # 3 -> Not proficient
            "Religion": "+3",      # 3 -> Not proficient
            "Athletics": "+0",     # 0 -> Not proficient
            "Acrobatics": "+2",    # 2 -> Not proficient
            "Insight": "-1",       # -1 -> Not proficient
            "Persuasion": "+2"     # 2 -> Not proficient
        }
        res = parser.parse_sheet(fields)
        prof_skills = res["native_system_data"]["proficiencies"]["skills"]
        self.assertIn("Arcana", prof_skills)
        self.assertIn("History", prof_skills)
        self.assertNotIn("Investigation", prof_skills)
        self.assertNotIn("Athletics", prof_skills)
        self.assertNotIn("Persuasion", prof_skills)

    def test_inventory_parenthesis_preservation_and_healing(self):
        """Verifies that inventory items ending in parentheticals like 'Rations (1 day)' and 'Rope, Hempen (50 feet)' retain their closing parenthesis and are auto-healed if truncated."""
        from core.character_vault_service import CharacterVaultService
        parser = DnD5eParser()
        fields = {
            "CharacterName": "Parenthesis Tester",
            "CLASS  LEVEL": "Artificer 1",
            "STR": "10", "DEX": "10", "CON": "10", "INT": "10", "WIS": "10", "CHA": "10",
            "Eq Name0": r"Rations \(1 day\)",
            "Eq Name1": "Rope, Hempen (50 feet",
            "Eq Name2": "Waterskin"
        }
        res = parser.parse_sheet(fields)
        inv = res["native_system_data"]["inventory"]
        self.assertIn("Rations (1 day)", inv)
        self.assertIn("Rope, Hempen (50 feet)", inv)
        self.assertIn("Waterskin", inv)

        # Test CharacterVaultService auto-healing
        vault_svc = CharacterVaultService(vault_dir="tests/test_vault_parens")
        res_saved = vault_svc.save_character({
            "name": "Parens Hero",
            "inventory": ["Rations (1 day", "Rope, Hempen (50 feet", "Dagger"]
        })
        saved = res_saved["character"]
        self.assertEqual(saved["inventory"], ["Rations (1 day)", "Rope, Hempen (50 feet)", "Dagger"])
        tagged = saved["universal_core"]["tagged_inventory"]
        tagged_names = [x["name"] for x in tagged]
        self.assertEqual(tagged_names, ["Rations (1 day)", "Rope, Hempen (50 feet)", "Dagger"])

        # Test Seraphina PDF parse directly
        if os.path.exists(self.seraphina_pdf_path):
            with open(self.seraphina_pdf_path, "rb") as f:
                pdf_bytes = f.read()
            parsed = self.service._parse_character_from_document("Seraphina.pdf", "", pdf_bytes)
            self.assertIsNotNone(parsed)
            char = parsed[0]
            seraphina_inv = char.get("inventory", [])
            inv_names = [x.get("name") if isinstance(x, dict) else str(x) for x in seraphina_inv]
            self.assertIn("Rations (1 day)", inv_names)
            self.assertIn("Rope, Hempen (50 feet)", inv_names)
            self.assertNotIn("Rations (1 day", inv_names)
            self.assertNotIn("Rope, Hempen (50 feet", inv_names)

            # Assert structured weights and quantities
            rations_item = next((x for x in seraphina_inv if isinstance(x, dict) and x.get("name") == "Rations (1 day)"), None)
            self.assertIsNotNone(rations_item)
            self.assertEqual(rations_item.get("qty"), "10")
            self.assertEqual(rations_item.get("weight"), "20 lb.")

            # Assert Encumbrance & Push/Drag/Lift
            self.assertEqual(char.get("encumbered"), "120 lb.")
            self.assertEqual(char.get("push_drag_lift"), "240 lb.")

            # Assert Creature Type
            self.assertEqual(char.get("creature_type"), "Humanoid")

            # Assert Physical Appearance Details
            desc = char.get("description", {})
            self.assertEqual(desc.get("gender") or char.get("gender"), "Female")
            self.assertEqual(str(desc.get("age") or char.get("age")), "35")
            self.assertEqual(desc.get("size") or char.get("size"), "Small")
            self.assertEqual(desc.get("height") or char.get("height"), "3' 6\"")
            self.assertEqual(str(desc.get("weight") or char.get("weight")), "45")
            self.assertEqual(desc.get("alignment") or char.get("alignment"), "Neutral Good")
            self.assertEqual(desc.get("faith") or char.get("faith"), "Gond")
            self.assertEqual(desc.get("skin") or char.get("skin"), "Pale")
            self.assertEqual(desc.get("eyes") or char.get("eyes"), "Bright blue")
            self.assertEqual(desc.get("hair") or char.get("hair"), "Silvery-white")

            # Assert Backstory
            backstory = char.get("backstory", "")
            self.assertTrue(len(backstory) > 50)
            self.assertIn("Seraphina", backstory)



if __name__ == "__main__":
    unittest.main()
