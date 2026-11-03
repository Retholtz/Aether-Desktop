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

    def test_coc_vault_roundtrip_and_metrics(self):
        from core.character_vault_service import CharacterVaultService
        vault_test_dir = "tests/test_coc_vault_dir"
        vault_service = CharacterVaultService(vault_dir=vault_test_dir)
        try:
            coc_char = {
                "name": "Jessie Williams",
                "player_name": "Investigator",
                "archetype": "History Student",
                "ruleset": "call_of_cthulhu",
                "native_genre": "modern_horror",
                "native_system": "call_of_cthulhu_7e",
                "max_health": 13,
                "current_health": 13,
                "dodge": 40,
                "guard": 40,
                "attributes": {
                    "STR": 70, "CON": 80, "SIZ": 50, "DEX": 40,
                    "APP": 50, "INT": 60, "POW": 50, "EDU": 60,
                    "Move": 8
                },
                "custom_meters": {
                    "sanity": {"current": 50, "max": 99, "label": "Sanity"},
                    "luck": {"current": 55, "max": 99, "label": "Luck"},
                    "magic_points": {"current": 10, "max": 10, "label": "Magic Points"},
                    "move_rate": {"current": 8, "max": 8, "label": "Move Rate"}
                },
                "skills": {
                    "History": "70%",
                    "Fighting (Brawl)": "60%",
                    "Library Use": "60%",
                    "Dodge": "40%"
                },
                "weapons": [{"name": "Unarmed", "atk_bonus": "60%", "damage": "1d3"}],
                "inventory": ["Satchel", "Pocket Bible"],
                "features_and_traits": ["Personal Description: Youthful", "Traits: Risk taker"]
            }

            # 1. Save to vault
            res = vault_service.import_from_campaign(coc_char)
            self.assertEqual(res["status"], "success")

            # 2. Verify vault record
            char_in_vault = vault_service.get_character("char_jessie_williams_root")
            self.assertIsNotNone(char_in_vault)
            self.assertEqual(char_in_vault["ruleset"], "call_of_cthulhu")
            self.assertEqual(char_in_vault["vitals"]["guard"]["label"], "Dodge Defense")
            self.assertEqual(char_in_vault["vitals"]["guard"]["current"], 40)
            self.assertEqual(char_in_vault["custom_meters"]["sanity"]["current"], 50)
            self.assertEqual(char_in_vault["custom_meters"]["luck"]["current"], 55)
            self.assertEqual(char_in_vault["attributes"]["STR"], 70)
            self.assertEqual(char_in_vault["attributes"]["CON"], 80)
            self.assertEqual(char_in_vault["skills"]["History"], "70%")

            # 3. Ingest into active campaign
            ingest_res = self.service.import_character_to_active_campaign(char_in_vault, overwrite_existing=True)
            self.assertEqual(ingest_res["status"], "success")

            live_char = self.service.active_state["characters"]["char_jessie_williams_root"]
            self.assertEqual(live_char["ruleset"], "call_of_cthulhu")
            self.assertEqual(live_char["vitals"]["guard"]["label"], "Dodge Defense")
            self.assertEqual(live_char["vitals"]["guard"]["current"], 40)
            self.assertEqual(live_char["custom_meters"]["sanity"]["current"], 50)
            self.assertEqual(live_char["custom_meters"]["luck"]["current"], 55)
            self.assertEqual(live_char["attributes"]["STR"], 70)
            self.assertEqual(live_char["skills"]["History"], "70%")
            self.assertIn("Personal Description: Youthful", live_char["features_and_traits"])
        finally:
            if os.path.exists(vault_test_dir):
                shutil.rmtree(vault_test_dir, ignore_errors=True)

    def test_pathfinder_identification_and_vault_roundtrip(self):
        """Verifies PF1e, PF2e & Remaster identification and full Vault/Campaign roundtrip."""
        factory = SheetParserFactory()
        pf_parser = next(p for p in factory.parsers if isinstance(p, PF2eParser))

        # 1. Test PF1e Society pregen identification
        pf1e_text = "PATHFINDER SOCIETY ORGANIZED PLAY PREGENERATED CHARACTER MERISIEL ROGUE 1 FORTITUDE +2 REFLEX +6 WILL +1 CMB +2 CMD 16 BASE ATK +0"
        self.assertTrue(pf_parser.identify_system({}, pf1e_text))

        # 2. Test PF2e Remaster pregen identification
        pf2e_text = "AMIRI BARBARIAN HUMAN 1 CLASS DC 17 STRIKES LARGE BASTARD SWORD BULK PAIZO INC"
        self.assertTrue(pf_parser.identify_system({}, pf2e_text))

        # 3. Test Vault and Campaign storage for Pathfinder character
        from core.character_vault_service import CharacterVaultService
        vault_test_dir = "tests/test_pf_vault_dir"
        vault_service = CharacterVaultService(vault_dir=vault_test_dir)
        try:
            merisiel_char = {
                "name": "Merisiel",
                "player_name": "retholtz",
                "archetype": "Rogue 1 (Elf)",
                "ruleset": "pathfinder_2e",
                "native_genre": "heroic_fantasy",
                "native_system": "pathfinder_2e",
                "system_name": "Pathfinder 1E",
                "vitals": {"health": {"current": 10, "max": 10, "label": "Health"}, "guard": {"current": 17, "max": 17, "label": "Armor Defense"}},
                "attributes": {"STR": 12, "DEX": 18, "CON": 12, "INT": 12, "WIS": 13, "CHA": 10},
                "skills": {
                    "Acrobatics": "+7", "Climb": "+5", "Disable Device": "+8",
                    "Escape Artist": "+7", "Perception": "+6", "Stealth": "+7"
                },
                "weapons": [
                    {"name": "Rapier", "atk_bonus": "+4", "damage": "1d6+1"},
                    {"name": "Dagger", "atk_bonus": "+4", "damage": "1d4+1"}
                ],
                "inventory": ["Studded leather armor", "Rapier", "Dagger", "Thieves' tools"],
                "features_and_traits": [
                    "Sneak Attack +1d6", "Trapfinding +1", "Weapon Finesse", "Low-Light Vision", "Elven Immunities"
                ],
                "spells": []
            }

            # Save to Vault
            res = vault_service.import_from_campaign(merisiel_char)
            self.assertEqual(res["status"], "success")

            # Retrieve from Vault
            vault_char = vault_service.get_character("char_merisiel_root")
            self.assertIsNotNone(vault_char)
            self.assertEqual(vault_char["ruleset"], "pathfinder_2e")
            self.assertEqual(vault_char["vitals"]["guard"]["current"], 17)
            self.assertEqual(vault_char["attributes"]["DEX"], 18)
            self.assertEqual(len(vault_char["features_and_traits"]), 5)
            self.assertIn("Sneak Attack +1d6", vault_char["features_and_traits"])

            # Import to Campaign
            ingest_res = self.service.import_character_to_active_campaign(vault_char, overwrite_existing=True)
            self.assertEqual(ingest_res["status"], "success")

            live_char = self.service.active_state["characters"]["char_merisiel_root"]
            self.assertEqual(live_char["ruleset"], "pathfinder_2e")
            self.assertEqual(live_char["vitals"]["guard"]["current"], 17)
            self.assertEqual(live_char["vitals"]["health"]["current"], 10)
            self.assertEqual(len(live_char["weapons"]), 2)
            self.assertEqual(len(live_char["features_and_traits"]), 5)
            self.assertIn("Sneak Attack +1d6", live_char["features_and_traits"])
        finally:
            if os.path.exists(vault_test_dir):
                shutil.rmtree(vault_test_dir, ignore_errors=True)

    def test_crossover_item_adjudication_and_structured_spells(self):
        """Verifies item adjudication on strings/weapons and structured spells management."""
        from core.character_vault_service import CharacterVaultService
        vault_test_dir = "tests/test_adj_vault_dir"
        vault_service = CharacterVaultService(vault_dir=vault_test_dir)
        try:
            sample_char = {
                "id": "char_amiri_root",
                "name": "Amiri",
                "ruleset": "pathfinder_2e",
                "native_genre": "heroic_fantasy",
                "inventory": ["Potion of Healing", "Shield", "Rope"],
                "weapons": [{"name": "Large bastard sword", "atk_bonus": "+7", "damage": "1d8+4"}],
                "spells": []
            }
            vault_service._save_character(sample_char)

            # 1. Test adjudicate_item on string item
            adj_res = vault_service.adjudicate_item("char_amiri_root", "Potion of Healing", "quarantine", target_genre="cyberpunk")
            self.assertEqual(adj_res["status"], "success")
            char_after = vault_service.get_character("char_amiri_root")
            stash_names = [s["name"] for s in char_after["quarantined_stash"]]
            self.assertIn("Potion of Healing", stash_names)

            # 2. Test adjudicate_item on weapon
            adj_w_res = vault_service.adjudicate_item("char_amiri_root", "Large bastard sword", "relic", target_genre="cyberpunk")
            self.assertEqual(adj_w_res["status"], "success")
            char_w_after = vault_service.get_character("char_amiri_root")
            relic_names = [it["name"] for it in char_w_after["inventory"] if isinstance(it, dict) and it.get("mode") == "relic"]
            self.assertIn("Large bastard sword", relic_names)

            # 3. Test structured spells addition & removal in active campaign
            ingest_res = self.service.import_character_to_active_campaign(char_w_after, overwrite_existing=True)
            self.assertEqual(ingest_res["status"], "success")

            spell_to_add = {
                "name": "Fireball",
                "level": "3rd Level",
                "atk_bonus": "DC 14 DEX",
                "damage": "8d6 Fire",
                "range": "150 ft.",
                "notes": "1 Action"
            }
            res_add = self.service.manage_character_inventory("Amiri", action="add", spells=[spell_to_add])
            self.assertEqual(res_add["status"], "success")
            self.assertIn("Fireball", res_add["modified_spells"])

            live_amiri = self.service.active_state["characters"]["char_amiri_root"]
            spells_in_amiri = live_amiri.get("spells", [])
            self.assertEqual(len(spells_in_amiri), 1)
            self.assertEqual(spells_in_amiri[0]["damage"], "8d6 Fire")

            # Remove spell
            res_rem = self.service.manage_character_inventory("Amiri", action="remove", spells=["Fireball"])
            self.assertEqual(res_rem["status"], "success")
            self.assertEqual(len(live_amiri.get("spells", [])), 0)
        finally:
            if os.path.exists(vault_test_dir):
                shutil.rmtree(vault_test_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

