"""
tests/test_sheet_parsers_and_transmutation.py - Verification Suite
Validates D&D 5e parsing, cross-genre transmutation, and 'Once and Done' state isolation.
"""
import os
import shutil
import unittest
from core.parsers.base_parser import UniversalCharacterCore
from core.parsers.dnd5e_parser import DnD5eParser
from core.parsers.cyberpunk_red_parser import CyberpunkRedParser
from core.parsers.coc7e_parser import CoC7eParser
from core.parsers.pf2e_parser import PF2eParser
from core.parsers.factory import SheetParserFactory
from core.transmutation_engine import TransmutationEngine
from core.ttrpg_service import TTRPGService


class TestSheetParsersAndTransmutation(unittest.TestCase):
    def setUp(self):
        self.test_dir = "tests/test_campaign_isolation"
        self.manifest_file = "tests/test_manifest.json"
        self.service = TTRPGService(data_dir=self.test_dir, manifest_path=self.manifest_file)
        self.service.active_state = {
            "campaign_id": "test_camp_01",
            "characters": {}
        }

        # Sample D&D 5e Root Payload (representing Shadow)
        self.sample_root_file = {
            "id": "char_shadow_root",
            "native_genre": "fantasy_d20",
            "universal_core": {
                "name": "Shadow",
                "archetype": "Rogue 1",
                "level": 1,
                "normalized_attributes": {
                    "might": 40,
                    "agility": 80,
                    "vigor": 60,
                    "intellect": 75,
                    "presence": 45
                },
                "tagged_inventory": [
                    {"name": "Shortsword", "category": "melee_light", "genre": "fantasy_d20"},
                    {"name": "Leather Armor", "category": "armor_light", "genre": "fantasy_d20"},
                    {"name": "Holy Relic of Pelor", "category": "divine_artifact", "genre": "fantasy_d20"}
                ]
            },
            "native_system_data": {
                "hit_points": {"current": 9, "max": 9},
                "armor_class": 13
            }
        }

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)
        if os.path.exists(self.manifest_file):
            try:
                os.remove(self.manifest_file)
            except Exception:
                pass

    def test_transmutation_fantasy_to_cyberpunk(self):
        variant = TransmutationEngine.refactor_character(
            self.sample_root_file,
            target_genre="cybernetic_action",
            transmute_gear=True
        )

        # Attribute Scaling Verification (Agility 80/100 -> REF/DEX 8/10)
        stats = variant["target_system_attributes"]
        self.assertEqual(stats["REF"], 8)
        self.assertEqual(stats["DEX"], 8)
        self.assertEqual(stats["BODY"], 4)

        # Gear Transmutation Verification
        active_gear = [item["name"] for item in variant["active_inventory"]]
        self.assertTrue(any("Vibro-Blade" in g for g in active_gear))
        self.assertTrue(any("Sub-Dermal Weave" in g for g in active_gear))

        # Incompatible gear quarantined
        quarantined = [q["original_item"] for q in variant["quarantined_stash"]]
        self.assertIn("Holy Relic of Pelor", quarantined)

    def test_transmutation_cyberpunk_to_fantasy(self):
        cyber_char = {
            "id": "char_v_root",
            "native_genre": "cybernetic_action",
            "universal_core": {
                "name": "V",
                "archetype": "Solo",
                "level": 4,
                "normalized_attributes": {
                    "might": 70,
                    "agility": 80,
                    "vigor": 60,
                    "intellect": 50,
                    "presence": 50
                },
                "tagged_inventory": [
                    {"name": "Heavy Pistol", "category": "ranged_ballistic", "genre": "cybernetic_action"},
                    {"name": "Kevlar Vest", "category": "armor_ballistic", "genre": "cybernetic_action"}
                ]
            }
        }
        variant = TransmutationEngine.refactor_character(cyber_char, target_genre="fantasy_d20")
        stats = variant["target_system_attributes"]
        self.assertEqual(stats["STR"], 14)  # (70/100)*20
        self.assertEqual(stats["DEX"], 16)  # (80/100)*20
        active_gear = [item["name"] for item in variant["active_inventory"]]
        self.assertTrue(any("Repeating Hand Crossbow" in g for g in active_gear))
        self.assertTrue(any("Hardened Studded Leather" in g for g in active_gear))

    def test_once_and_done_state_isolation_preserves_deltas(self):
        # 1. Ingest character into campaign
        ingest_res = self.service.import_character_to_active_campaign(self.sample_root_file)
        self.assertEqual(ingest_res["status"], "success")

        # 2. Player takes 4 damage and gains 'Poisoned' condition in combat
        self.service.modify_character_stat("Shadow", "health", -4)
        self.service.update_condition("Shadow", "Poisoned", active=True)

        live_char = self.service.active_state["characters"]["char_shadow_root"]
        self.assertEqual(live_char["vitals"]["health"]["current"], 5)
        self.assertIn("Poisoned", live_char["conditions"])

        # 3. Simulate UI Refresh or Accidental Re-Import: Verify live delta is NOT overwritten
        re_import = self.service.import_character_to_active_campaign(self.sample_root_file)
        self.assertEqual(re_import["status"], "already_loaded")

        # 4. Authoritative roster call check
        roster = self.service.get_campaign_roster()
        shadow = next(c for c in roster if c["id"] == "char_shadow_root")
        self.assertEqual(shadow["vitals"]["health"]["current"], 5, "HP was reset to template max!")
        self.assertIn("Poisoned", shadow["conditions"], "Condition was wiped on refresh!")

    def test_factory_and_system_parsers_signatures(self):
        factory = SheetParserFactory()
        self.assertEqual(len(factory.parsers), 4)

        # Test system identification signatures
        dnd_fields = {"CharacterName": "Kaelen", "ProfBonus": "+2", "PassivePerception": "14"}
        self.assertTrue(factory.parsers[0].identify_system(dnd_fields, ""))

        cyber_fields = {"Handle": "Silverhand"}
        self.assertTrue(factory.parsers[1].identify_system(cyber_fields, "HUMANITY CYBERWARE DEATH SAVE"))

        coc_fields = {"CharacterName": "Edward"}
        self.assertTrue(factory.parsers[2].identify_system(coc_fields, "SANITY CALL OF CTHULHU MAGIC POINTS"))

        pf2_fields = {"Character Name": "Valeros"}
        self.assertTrue(factory.parsers[3].identify_system(pf2_fields, "PATHFINDER HERO POINTS CLASS DC"))

    def test_vault_roundtrip_preserves_features_and_currency(self):
        from core.character_vault_service import CharacterVaultService
        vault_test_dir = "tests/test_vault_dir"
        vault_service = CharacterVaultService(vault_dir=vault_test_dir)
        try:
            sample_char = {
                "name": "Kaelen",
                "player_name": "retholtz",
                "archetype": "Paladin 1",
                "level": 1,
                "ruleset": "dnd_5e",
                "native_genre": "heroic_fantasy",
                "vitals": {"health": {"current": 20, "max": 20}, "guard": {"current": 18, "max": 18}},
                "currency": {"cp": 5, "sp": 10, "ep": 0, "gp": 25, "pp": 1},
                "weapons": [{"name": "Warhammer", "atk_bonus": "+5", "damage": "1d8+3"}],
                "inventory": ["Shield", "Plate Armor", "Holy Symbol"],
                "features_and_traits": ["Divine Sense", "Lay on Hands (5 HP pool)"],
                "spells": ["Bless", "Cure Wounds"]
            }

            # 1. Export from campaign to vault
            export_res = vault_service.import_from_campaign(sample_char)
            self.assertEqual(export_res["status"], "success")

            # 2. Retrieve from vault and verify all rich fields survived
            char_in_vault = vault_service.get_character("char_kaelen_root")
            self.assertIsNotNone(char_in_vault)
            self.assertEqual(char_in_vault["currency"]["gp"], 25)
            self.assertEqual(len(char_in_vault["features_and_traits"]), 2)
            self.assertIn("Divine Sense", char_in_vault["features_and_traits"])
            self.assertIn("Lay on Hands (5 HP pool)", char_in_vault["features_and_traits"])
            self.assertEqual(len(char_in_vault["inventory"]), 3)
            self.assertEqual(len(char_in_vault["spells"]), 2)

            # 3. Ingest from vault into campaign
            ingest_res = self.service.import_character_to_active_campaign(char_in_vault, overwrite_existing=True)
            self.assertEqual(ingest_res["status"], "success")

            live_char = self.service.active_state["characters"]["char_kaelen_root"]
            self.assertEqual(live_char["currency"]["gp"], 25)
            self.assertEqual(len(live_char["features_and_traits"]), 2)
            self.assertIn("Divine Sense", live_char["features_and_traits"])
            self.assertEqual(len(live_char["inventory"]), 3)
            self.assertEqual(len(live_char["spells"]), 2)
        finally:
            if os.path.exists(vault_test_dir):
                shutil.rmtree(vault_test_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

