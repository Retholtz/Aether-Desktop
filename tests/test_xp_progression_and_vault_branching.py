"""
tests/test_xp_progression_and_vault_branching.py
Unit tests verifying:
1. D&D 5e monster XP calculations and combat XP awards.
2. Party XP splitting (e.g. 5 Apes = 500 XP split across 4 heroes = 125 XP each).
3. Level-up milestone notifications (Level 1: 0, Level 2: 300, etc.).
4. Character Vault export modes: Overwrite Canonical Root vs Create Branch Variant.
5. Vault existence checks in the GUI bridge.
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from core.ttrpg_service import TTRPGService
from core.character_vault_service import CharacterVaultService
from core.gui_bridge import GUIBridge


class TestXPProgressionAndVaultBranching(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="aether_xp_vault_test_")
        self.ttrpg_dir = os.path.join(self.test_dir, "ttrpg")
        self.vault_dir = os.path.join(self.test_dir, "vault")
        os.makedirs(self.ttrpg_dir, exist_ok=True)
        os.makedirs(self.vault_dir, exist_ok=True)

        self.ttrpg_service = TTRPGService(storage_dir=self.ttrpg_dir)
        self.vault_service = CharacterVaultService(vault_dir=self.vault_dir)

        # Setup campaign with 4 heroes: Gale (L1, 0 XP), Shadow (L20), Sam (L20), Joe (L20)
        self.camp_id = "test_xp_camp"
        self.ttrpg_service.create_campaign("Test XP Campaign", system_preset="dnd_5e", campaign_id=self.camp_id)
        self.ttrpg_service.active_campaign_id = self.camp_id

        self.ttrpg_service.add_character({
            "id": "char_gale",
            "name": "Gale",
            "level": 1,
            "experience_points": 0,
            "is_player": True,
            "ruleset": "dnd_5e",
            "vitals": {"health": {"current": 8, "max": 8, "label": "Health"}, "guard": {"current": 12, "max": 12, "label": "Armor Defense"}}
        })
        self.ttrpg_service.add_character({
            "id": "char_shadow",
            "name": "Shadow",
            "level": 20,
            "experience_points": 355000,
            "is_player": True,
            "ruleset": "dnd_5e",
            "vitals": {"health": {"current": 194, "max": 194, "label": "Health"}, "guard": {"current": 18, "max": 18, "label": "Armor Defense"}}
        })
        self.ttrpg_service.add_character({
            "id": "char_sam",
            "name": "Sam",
            "level": 20,
            "experience_points": 355000,
            "is_player": True,
            "ruleset": "dnd_5e",
            "vitals": {"health": {"current": 150, "max": 150, "label": "Health"}, "guard": {"current": 16, "max": 16, "label": "Armor Defense"}}
        })
        self.ttrpg_service.add_character({
            "id": "char_joe",
            "name": "Joe",
            "level": 20,
            "experience_points": 355000,
            "is_player": True,
            "ruleset": "dnd_5e",
            "vitals": {"health": {"current": 210, "max": 210, "label": "Health"}, "guard": {"current": 17, "max": 17, "label": "Armor Defense"}}
        })

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_calculate_monster_xp_presets(self):
        """Standard 5e monster presets must return canonical XP values."""
        self.assertEqual(self.ttrpg_service.calculate_monster_xp("Ape"), 100)
        self.assertEqual(self.ttrpg_service.calculate_monster_xp("Giant Ape"), 2900)
        self.assertEqual(self.ttrpg_service.calculate_monster_xp("Sprite"), 50)
        self.assertEqual(self.ttrpg_service.calculate_monster_xp("Dryad"), 200)
        self.assertEqual(self.ttrpg_service.calculate_monster_xp("Goblin"), 50)
        self.assertEqual(self.ttrpg_service.calculate_monster_xp("Adult Red Dragon"), 18000)

    def test_combat_5_apes_awards_125_xp_each(self):
        """
        Defeating 5 apes (5 * 100 = 500 XP total) split across 4 party members
        must award 125 XP to each hero.
        """
        # Start combat with 5 Apes
        enemies = [f"Ape {i}" for i in range(1, 6)]
        res_start = self.ttrpg_service.start_combat(enemies=enemies)
        self.assertEqual(res_start["status"], "success")

        # Mark all apes defeated
        ladder = self.ttrpg_service.active_state["initiative_order"]
        for c in ladder:
            if not c.get("is_player"):
                c["current_hp"] = 0
                c["status"] = "Defeated"
        self.ttrpg_service.save_active_campaign_state()

        # End combat
        res_end = self.ttrpg_service.end_combat()
        self.assertEqual(res_end["status"], "success")
        self.assertEqual(res_end["xp_awarded"], 500)
        self.assertEqual(res_end["xp_per_character"], 125)

        # Check Gale's XP
        _, gale = self.ttrpg_service._find_character("Gale")
        self.assertEqual(gale["experience_points"], 125)
        self.assertEqual(gale["level"], 1)

        # Check Shadow's XP
        _, shadow = self.ttrpg_service._find_character("Shadow")
        self.assertEqual(shadow["experience_points"], 355125)

    def test_level_up_threshold_notification(self):
        """When a character reaches the threshold (300 XP for Level 2), level_up_ready is True."""
        # Award Gale 125 XP
        r1 = self.ttrpg_service.manage_character_progression("Gale", action="award_xp", amount_or_level=125)
        self.assertFalse(r1["level_up_ready"])
        self.assertEqual(r1["experience_points"], 125)

        # Award Gale another 175 XP (Total: 300 XP)
        r2 = self.ttrpg_service.manage_character_progression("Gale", action="award_xp", amount_or_level=175)
        self.assertTrue(r2["level_up_ready"])
        self.assertEqual(r2["experience_points"], 300)
        self.assertIn("QUALIFIES FOR LEVEL UP", r2["message"])

        # Shadow (Level 20) receiving XP never triggers level_up_ready
        r3 = self.ttrpg_service.manage_character_progression("Shadow", action="award_xp", amount_or_level=500)
        self.assertFalse(r3["level_up_ready"])

    def test_vault_export_overwrite_vs_branch(self):
        """
        Character Vault export must support overwriting Canonical Root and creating Branch variants.
        """
        gale_campaign_char = {
            "name": "Gale",
            "level": 2,
            "experience_points": 300,
            "archetype": "Evocation Wizard",
            "ruleset": "dnd_5e",
            "vitals": {"health": {"current": 14, "max": 14, "label": "Health"}, "guard": {"current": 13, "max": 13, "label": "Armor Defense"}},
            "inventory": ["Spellbook", "Arcane Focus", "Potion of Healing"]
        }
        camp_meta = {"campaign_id": "elf_hunt", "title": "Elf Hunt", "genre": "heroic_fantasy"}

        # 1. First save as Canonical Root
        res_root = self.vault_service.import_from_campaign(gale_campaign_char, campaign_meta=camp_meta, save_mode="overwrite")
        self.assertEqual(res_root["status"], "success")
        self.assertEqual(res_root["character"]["id"], "char_gale_root")
        self.assertTrue(res_root["character"]["is_root"])
        self.assertIsNone(res_root["character"]["parent_id"])
        self.assertEqual(res_root["character"]["variant_label"], "Canonical Root")

        # 2. Level up Gale further in campaign and export as a Branch
        gale_campaign_char["level"] = 3
        gale_campaign_char["experience_points"] = 900
        gale_campaign_char["inventory"].append("Wand of Magic Missiles")

        res_branch = self.vault_service.import_from_campaign(
            gale_campaign_char,
            campaign_meta=camp_meta,
            save_mode="branch",
            branch_label="Elf Hunt Veteran"
        )
        self.assertEqual(res_branch["status"], "success")
        self.assertFalse(res_branch["character"]["is_root"])
        self.assertEqual(res_branch["character"]["parent_id"], "char_gale_root")
        self.assertEqual(res_branch["character"]["variant_label"], "Elf Hunt Veteran")
        self.assertEqual(res_branch["character"]["level"], 3)

        # 3. Verify original Canonical Root is still at Level 2 (preserved!)
        root_reloaded = self.vault_service.get_character("char_gale_root")
        self.assertIsNotNone(root_reloaded)
        self.assertEqual(root_reloaded["level"], 2)
        self.assertEqual(root_reloaded["experience_points"], 300)

        # 4. Overwrite Canonical Root
        res_overwrite = self.vault_service.import_from_campaign(
            gale_campaign_char,
            campaign_meta=camp_meta,
            save_mode="overwrite"
        )
        self.assertEqual(res_overwrite["status"], "success")
        self.assertEqual(res_overwrite["character"]["id"], "char_gale_root")
        self.assertEqual(res_overwrite["character"]["level"], 3)
        self.assertEqual(res_overwrite["character"]["experience_points"], 900)

    def test_gui_bridge_vault_check_and_export(self):
        """GUIBridge exposes check_vault_character_exists and save_campaign_character_to_vault with mode."""
        bridge = GUIBridge()
        bridge.ttrpg_service = self.ttrpg_service
        eng_mock = MagicMock()
        eng_mock.vault_service = self.vault_service
        bridge._engine = eng_mock

        # Initially Gale is not in Vault
        check_before = bridge.check_vault_character_exists("Gale")
        self.assertFalse(check_before["exists"])

        # Save to Vault via bridge as overwrite
        res_save = bridge.save_campaign_character_to_vault("Gale", save_mode="overwrite")
        self.assertEqual(res_save["status"], "success")

        # Now Gale exists in Vault
        check_after = bridge.check_vault_character_exists("Gale")
        self.assertTrue(check_after["exists"])

        # Save as branch via bridge
        res_branch = bridge.save_campaign_character_to_vault("Gale", save_mode="branch", branch_label="Campaign Hero")
        self.assertEqual(res_branch["status"], "success")
        self.assertFalse(res_branch["character"]["is_root"])

    def test_multiclass_level_calculation_and_cap(self):
        """Multiclass characters (e.g. Barbarian 5 / Bard 5 / Druid 5 / Warlock 5) calculate total level 20."""
        # 1. From form fields parser
        char_data = self.ttrpg_service._parse_character_from_form_fields({
            "CharacterName": "Joe",
            "ClassLevel": "Barbarian 5 / Bard 5 / Druid 5 / Warlock 5",
            "XP": "712"
        }, filename="Multiclass_4.pdf")
        self.assertEqual(char_data["level"], 20)
        self.assertEqual(char_data["class_and_level"], "Barbarian 5 / Bard 5 / Druid 5 / Warlock 5")

        # 2. Add character to campaign
        res_add = self.ttrpg_service.add_character({
            "id": "char_joe_multi",
            "name": "Joe",
            "class_and_level": "Barbarian 5 / Bard 5 / Druid 5 / Warlock 5",
            "experience_points": 712,
            "is_player": True,
            "ruleset": "dnd_5e"
        })
        self.assertEqual(res_add["status"], "success")
        added_char = res_add["character"]
        self.assertEqual(added_char["level"], 20)

        # 3. Vault save preserves level 20
        camp_meta = {"campaign_id": self.camp_id, "title": "Test XP Campaign", "system_preset": "dnd_5e"}
        res = self.vault_service.import_from_campaign(added_char, campaign_meta=camp_meta, save_mode="overwrite")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["character"]["level"], 20)



if __name__ == "__main__":
    unittest.main()
