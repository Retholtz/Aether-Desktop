import os
import shutil
import unittest
from core.character_vault_service import (
    CharacterVaultService,
    are_rulesets_compatible,
    get_ruleset_display_label,
    normalize_ruleset_id
)
from core.session_manager import SessionManager


class TestCharacterVault(unittest.TestCase):
    def setUp(self):
        self.test_dir = "tests/test_vault_chars"
        self.service = CharacterVaultService(vault_dir=self.test_dir)

        # Inject sample root character: Level 4 Fantasy Rogue
        self.sample_root = {
            "id": "char_retholtz_root",
            "is_root": True,
            "parent_id": None,
            "name": "Retholtz",
            "level": 4,
            "native_genre": "heroic_fantasy",
            "attributes": {"Agility": 18, "Vigor": 12},
            "inventory": [
                {"name": "Steel Broadsword", "genre": "heroic_fantasy"},
                {"name": "Thieves' Tools", "genre": "heroic_fantasy"}
            ],
            "traits_and_abilities": [],
            "quarantined_stash": []
        }
        self.service._save_character(self.sample_root)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_import_native_genre_returns_root(self):
        res = self.service.import_to_campaign("char_retholtz_root", target_genre="heroic_fantasy")
        self.assertEqual(res["status"], "success")
        self.assertFalse(res["is_crossover"])
        self.assertEqual(res["character"]["id"], "char_retholtz_root")

    def test_import_crossover_creates_branch_and_quarantines(self):
        res = self.service.import_to_campaign(
            "char_retholtz_root",
            target_genre="cybernetic_action",
            quarantine_unmatched=True
        )
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["is_crossover"])

        variant = res["character"]
        self.assertEqual(variant["parent_id"], "char_retholtz_root")
        self.assertEqual(variant["level"], 4)
        self.assertIn("Cyberpunk Crossover", variant["name"])

        # Inventory check: Steel Broadsword should be quarantined
        self.assertEqual(len(variant["inventory"]), 0)
        self.assertEqual(len(variant["quarantined_stash"]), 2)
        self.assertEqual(variant["quarantined_stash"][0]["name"], "Steel Broadsword")

    def test_level_up_in_native_genre_updates_root(self):
        res = self.service.level_up_character(
            char_id="char_retholtz_root",
            current_campaign_genre="heroic_fantasy",
            level_increment=1,
            stat_boosts={"Agility": 1}
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["new_level"], 5)
        self.assertTrue(res["is_root_updated"])

        # Verify disk persistence
        loaded = self.service.get_character("char_retholtz_root")
        self.assertEqual(loaded["level"], 5)
        self.assertEqual(loaded["attributes"]["Agility"], 19)

    def test_level_up_in_crossover_isolates_branch(self):
        # 1. Create cyberpunk variant
        fork_res = self.service.import_to_campaign("char_retholtz_root", target_genre="cybernetic_action")
        variant_id = fork_res["character"]["id"]

        # 2. Level up the variant to Level 5
        lvl_res = self.service.level_up_character(
            char_id=variant_id,
            current_campaign_genre="cybernetic_action",
            level_increment=1,
            new_traits=[{"name": "Neural Overdrive", "description": "Hacking speed boost"}]
        )
        self.assertEqual(lvl_res["new_level"], 5)
        self.assertFalse(lvl_res["is_root_updated"])

        # 3. Assert Variant is Level 5 with new trait
        variant_loaded = self.service.get_character(variant_id)
        self.assertEqual(variant_loaded["level"], 5)
        self.assertEqual(len(variant_loaded["traits_and_abilities"]), 1)

        # 4. Assert Root is STILL Level 4 without the cyberpunk trait
        root_loaded = self.service.get_character("char_retholtz_root")
        self.assertEqual(root_loaded["level"], 4)
        self.assertEqual(len(root_loaded["traits_and_abilities"]), 0)

    def test_ruleset_compatibility_mapping(self):
        # D&D / Heroic family
        self.assertTrue(are_rulesets_compatible("dnd_5e", "heroic_d20"))
        self.assertTrue(are_rulesets_compatible("dnd_5e", "heroic_fantasy"))
        self.assertTrue(are_rulesets_compatible("5e", "dungeons_and_dragons"))
        self.assertTrue(are_rulesets_compatible("dnd", "heroic_d20"))

        # Cyberpunk family
        self.assertTrue(are_rulesets_compatible("cyberpunk", "cybernetic_action"))
        self.assertTrue(are_rulesets_compatible("cyberpunk_red", "cyberpunk"))

        # Call of Cthulhu / d100
        self.assertTrue(are_rulesets_compatible("call_of_cthulhu", "investigative_d100"))

        # Cross-family incompatibilities
        self.assertFalse(are_rulesets_compatible("dnd_5e", "cyberpunk"))
        self.assertFalse(are_rulesets_compatible("heroic_d20", "cybernetic_action"))
        self.assertFalse(are_rulesets_compatible("dnd_5e", "call_of_cthulhu"))

        # Display labels
        self.assertEqual(get_ruleset_display_label("dnd_5e"), "D&D 5E")
        self.assertEqual(get_ruleset_display_label("heroic_d20"), "D&D 5E")
        self.assertEqual(get_ruleset_display_label("cyberpunk"), "Cyberpunk")
        self.assertEqual(get_ruleset_display_label("cybernetic_action"), "Cyberpunk")

    def test_import_to_campaign_with_compatible_ruleset_bypasses_crossover(self):
        # Create a D&D character with weapons and inventory
        dnd_hero = {
            "id": "char_kaelen_dnd",
            "is_root": True,
            "parent_id": None,
            "name": "Kaelen",
            "level": 3,
            "ruleset": "dnd_5e",
            "ruleset_label": "D&D 5E",
            "native_genre": "heroic_fantasy",
            "native_system": "dnd_5e",
            "inventory": [
                {"name": "Heavy Crossbow", "genre": "heroic_fantasy"},
                {"name": "Longsword", "genre": "heroic_fantasy"}
            ],
            "quarantined_stash": []
        }
        self.service._save_character(dnd_hero)

        # Import into heroic_d20 campaign (D&D 5E family match)
        res = self.service.import_to_campaign("char_kaelen_dnd", target_genre="heroic_d20")
        self.assertEqual(res["status"], "success")
        self.assertFalse(res["is_crossover"])
        self.assertTrue(res["ruleset_match"])
        self.assertEqual(res["character"]["id"], "char_kaelen_dnd")
        # Ensure zero items are quarantined
        self.assertEqual(len(res["character"]["inventory"]), 2)
        self.assertEqual(len(res["character"]["quarantined_stash"]), 0)

    def test_import_from_campaign_creates_canonical_root(self):
        roster_char = {
            "name": "Sheila",
            "level": 2,
            "archetype": "Ranger",
            "player_name": "Player 2",
            "vitals": {
                "health": {"current": 18, "max": 18, "label": "Health"},
                "guard": {"current": 14, "max": 14, "label": "Armor Defense"}
            },
            "inventory": [{"name": "Shortbow", "genre": "heroic_fantasy"}]
        }
        campaign_meta = {
            "id": "iron_reach",
            "system_preset": "heroic_d20",
            "genre": "heroic_fantasy"
        }
        res = self.service.import_from_campaign(roster_char, campaign_meta)
        self.assertEqual(res["status"], "success")

        saved = self.service.get_character("char_sheila_root")
        self.assertIsNotNone(saved)
        self.assertTrue(saved["is_root"])
        self.assertEqual(saved["name"], "Sheila")
        self.assertEqual(saved["ruleset"], "heroic_d20")
        self.assertEqual(saved["ruleset_label"], "D&D 5E")
        self.assertEqual(saved["level"], 2)

    def test_import_from_document_json(self):
        json_content = b'''{
            "name": "Morrigan",
            "level": 3,
            "class": "Sorcerer",
            "ruleset": "dnd_5e",
            "health": 22,
            "armor_class": 13,
            "inventory": [
                {"name": "Arcane Focus Wand", "genre": "heroic_fantasy"}
            ]
        }'''
        res = self.service.import_from_document("morrigan_sheet.json", json_content)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["count"], 1)

        saved = self.service.get_character("char_morrigan_root")
        self.assertIsNotNone(saved)
        self.assertTrue(saved["is_root"])
        self.assertEqual(saved["name"], "Morrigan")
        self.assertEqual(saved["archetype"], "Sorcerer")
        self.assertEqual(saved["ruleset"], "dnd_5e")
        self.assertEqual(saved["ruleset_label"], "D&D 5E")
        self.assertEqual(saved["level"], 3)
        self.assertEqual(len(saved["inventory"]), 1)

    def test_import_from_document_health_bounds_clamping(self):
        # Character like Shadow with low level 1 HP (max_health=9, current_health=9)
        json_content = b'''{
            "name": "Shadow",
            "level": 1,
            "class": "Rogue",
            "ruleset": "dnd_5e",
            "max_health": 9,
            "current_health": 9,
            "armor_class": 10
        }'''
        res = self.service.import_from_document("shadow_sheet.json", json_content)
        self.assertEqual(res["status"], "success")

        saved = self.service.get_character("char_shadow_root")
        self.assertIsNotNone(saved)
        # Ensure HP is strictly 9 / 9 and NOT 20 / 9!
        self.assertEqual(saved["vitals"]["health"]["current"], 9)
        self.assertEqual(saved["vitals"]["health"]["max"], 9)


class TestSessionManager(unittest.TestCase):
    def setUp(self):
        self.test_sessions_dir = "tests/test_sessions_data"
        self.session_mgr = SessionManager(sessions_dir=self.test_sessions_dir)

    def tearDown(self):
        if os.path.exists(self.test_sessions_dir):
            shutil.rmtree(self.test_sessions_dir, ignore_errors=True)

    def test_session_lifecycle_and_hot_swap(self):
        # 1. Append turns to default session
        self.session_mgr.append_turn("user", "Hello Aether")
        self.session_mgr.append_turn("model", "Hello! How can I assist you today?")
        self.assertEqual(len(self.session_mgr.history), 2)

        # 2. Hot-swap to campaign session
        camp_history = self.session_mgr.switch_session("campaign", "iron_reach", "The Iron Reach")
        self.assertEqual(len(camp_history), 0)
        self.assertEqual(self.session_mgr.active_session_id, "campaign_iron_reach")

        # Append turns to campaign session
        self.session_mgr.append_turn("user", "Kaelen raises his shield.")
        self.session_mgr.append_turn("model", "A dark shadow falls across the corridor.")
        self.assertEqual(len(self.session_mgr.history), 2)

        # 3. Hot-swap to project session
        proj_history = self.session_mgr.switch_session("project", "aether_engine", "Aether Engine Core")
        self.assertEqual(len(proj_history), 0)
        self.session_mgr.append_turn("user", "Refactor the session persistence layer.")
        self.assertEqual(len(self.session_mgr.history), 1)

        # 4. Hot-swap back to campaign: verifies rehydration from disk
        rehydrated_camp = self.session_mgr.switch_session("campaign", "iron_reach")
        self.assertEqual(len(rehydrated_camp), 2)
        self.assertIn("Kaelen raises his shield", rehydrated_camp[0]["parts"][0]["text"])

        # 5. Hot-swap back to general: verifies rehydration from disk
        rehydrated_gen = self.session_mgr.switch_session("general", "default")
        self.assertEqual(len(rehydrated_gen), 2)
        self.assertIn("Hello Aether", rehydrated_gen[0]["parts"][0]["text"])


class TestVaultTools(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = "tests/test_vault_chars_tools"
        self.service = CharacterVaultService(vault_dir=self.test_dir)
        self.service._save_character({
            "id": "char_retholtz_root",
            "is_root": True,
            "name": "Retholtz",
            "level": 4,
            "native_genre": "heroic_fantasy",
            "attributes": {"Agility": 18},
            "inventory": [],
            "traits_and_abilities": []
        })

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_agent_vault_tools_dispatch(self):
        from unittest.mock import MagicMock
        from tools.vault_tools import register_vault_tools

        dispatcher = MagicMock()
        registered_tools = {}

        def mock_register_tool(name, handler=None, **kwargs):
            registered_tools[name] = {"handler": handler, **kwargs}

        dispatcher.register_tool = mock_register_tool
        register_vault_tools(dispatcher, self.service, lambda: "heroic_fantasy")

        self.assertIn("level_up_character", registered_tools)
        self.assertIn("inspect_vault_roster", registered_tools)

        # Execute inspect_vault_roster
        roster_res = await registered_tools["inspect_vault_roster"]["handler"]()
        self.assertEqual(roster_res["status"], "success")
        self.assertEqual(roster_res["count"], 1)

        # Execute level_up_character
        lvl_res = await registered_tools["level_up_character"]["handler"](
            character_id="char_retholtz_root",
            new_abilities=["Shadow Stride"],
            stat_increases={"Agility": 1}
        )
        self.assertEqual(lvl_res["status"], "success")
        self.assertEqual(lvl_res["new_level"], 5)
        self.assertTrue(lvl_res["is_root_updated"])


if __name__ == "__main__":
    unittest.main()
