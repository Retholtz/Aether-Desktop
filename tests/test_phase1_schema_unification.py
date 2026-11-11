"""
tests/test_phase1_schema_unification.py - Test Suite for Schema 2.1 Validation, Store Unification & Gating Attributes
"""
import unittest
import json
import os
import shutil
from pathlib import Path
from pydantic import ValidationError

from core.schema.validator import validate_character_payload
from core.campaign.schema import LocationNode, LocationType, ClueRecord, QuestRecord, QuestObjective, QuestStatus
from core.campaign.campaign_manager import CampaignManager, CampaignStoreFacade
from core.character_vault_service import CharacterVaultService
from core.ttrpg_service import TTRPGService
from tools.campaign_tools import tool_update_quest_status
import tools.campaign_tools as ct


class TestPhase1SchemaAndGating(unittest.TestCase):
    def setUp(self):
        self.test_dir = "tests/test_phase1_temp"
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)
        os.makedirs(self.test_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_sam_level_20_validation(self):
        """Verify Sam (Level 20 Warlock) passes Schema 2.1 Pydantic validation."""
        sam_path = Path("char_sam_root.json")
        if not sam_path.exists():
            self.skipTest("char_sam_root.json not found")
        with open(sam_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        valid, errors, doc = validate_character_payload(data)
        self.assertTrue(valid, f"Sam failed validation: {errors}")
        self.assertEqual(doc.level, 20)
        self.assertEqual(doc.schema_version, "2.1")
        self.assertIn("mystic_arcanum_9th", doc.universal_core.custom_meters)

    def test_shadow_multiclass_validation(self):
        """Verify Shadow (Paladin 10 / Sorcerer 10) passes Schema 2.1 validation."""
        shadow_path = Path("char_shadow_root.json")
        if not shadow_path.exists():
            self.skipTest("char_shadow_root.json not found")
        with open(shadow_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        valid, errors, doc = validate_character_payload(data)
        self.assertTrue(valid, f"Shadow failed validation: {errors}")
        self.assertEqual(doc.level, 20)
        self.assertEqual(doc.native_system_data.spell_slots["level_8"].max, 1)

    def test_location_node_spell_tier_gating(self):
        """Verify max_spell_tier defaults to 1 and enforces 1-9 bounds."""
        hamlet = LocationNode(id="village_oakhaven", name="Oakhaven", type=LocationType.SETTLEMENT)
        self.assertEqual(hamlet.max_spell_tier, 1)

        city = LocationNode(id="city_waterdeep", name="Waterdeep", type=LocationType.SETTLEMENT, max_spell_tier=5)
        self.assertEqual(city.max_spell_tier, 5)

        with self.assertRaises(Exception):
            LocationNode(id="invalid_node", name="Invalid", max_spell_tier=10)

        with self.assertRaises(Exception):
            LocationNode(id="invalid_node_zero", name="Invalid", max_spell_tier=0)

    def test_scarcity_ammunition_flagging(self):
        """Verify modern calibers receive finite_scarcity=True."""
        from core.parsers.dnd5e_parser import sanitize_inventory
        raw_inv = [
            {"name": "Dagger", "qty": "2", "weight": "1 lb."},
            {"name": "9mm Ammunition", "qty": "30", "weight": "1 lb."},
            {"name": "Shotgun Slug 12ga", "qty": "10", "weight": "1 lb."},
            {"name": "5.56 NATO rounds", "qty": "60", "weight": "2 lb."},
            {"name": "Heavy Energy Cell", "qty": "2", "weight": "1 lb."}
        ]
        sanitized = sanitize_inventory(raw_inv)
        item_map = {item["name"]: item for item in sanitized}
        self.assertFalse(item_map["Dagger"].get("finite_scarcity", False))
        self.assertTrue(item_map["9mm Ammunition"].get("finite_scarcity", False))
        self.assertTrue(item_map["Shotgun Slug 12ga"].get("finite_scarcity", False))
        self.assertTrue(item_map["5.56 NATO rounds"].get("finite_scarcity", False))
        self.assertTrue(item_map["Heavy Energy Cell"].get("finite_scarcity", False))

    def test_character_corruption_raises_validation_error(self):
        """Character payload corruption (missing vitals, invalid attribute bounds) raises descriptive ValidationError."""
        # 1. Missing vitals on import_character_to_campaign
        ttrpg = TTRPGService(data_dir=os.path.join(self.test_dir, "ttrpg_data"))
        corrupt_char_no_vitals = {
            "schema_version": "2.1",
            "id": "char_corrupt_1",
            "name": "Corrupt Hero",
            "level": 5,
            "archetype": "Fighter",
            "vitals": None
        }
        with self.assertRaises(ValidationError) as ctx:
            ttrpg.import_character_to_campaign(corrupt_char_no_vitals)
        self.assertIn("vitals", str(ctx.exception).lower())

        # 2. Invalid attribute bounds (might < 10)
        corrupt_char_bad_bounds = {
            "schema_version": "2.1",
            "id": "char_corrupt_2",
            "name": "Weakling Hero",
            "level": 1,
            "archetype": "Wizard",
            "vitals": {
                "health": {"current": 10, "max": 10, "label": "Health"},
                "guard": {"current": 10, "max": 10, "label": "Guard"}
            },
            "universal_core": {
                "name": "Weakling Hero",
                "archetype": "Wizard",
                "level": 1,
                "normalized_attributes": {
                    "might": 5,  # Invalid: bounded 10 <= x <= 100
                    "agility": 50,
                    "vigor": 50,
                    "intellect": 50,
                    "presence": 50
                }
            }
        }
        with self.assertRaises(ValidationError) as ctx:
            ttrpg.import_character_to_campaign(corrupt_char_bad_bounds)
        self.assertIn("greater than or equal to 10", str(ctx.exception))

        # 3. Vault save_character gatekeeping
        vault_svc = CharacterVaultService(vault_dir=os.path.join(self.test_dir, "vault"))
        with self.assertRaises(ValidationError):
            vault_svc.save_character(corrupt_char_no_vitals)

        with self.assertRaises(ValidationError):
            vault_svc.save_character(corrupt_char_bad_bounds)

    def test_unified_campaign_store_and_clue_synchronization(self):
        """Verify CampaignStoreFacade / CampaignManager unifies campaign_state.json and synchronizes clues."""
        camp_dir = os.path.join(self.test_dir, "campaigns")
        cm = CampaignManager(campaign_id="test_unified_camp", storage_root=camp_dir)

        # 1. Verify unified file persistence on save
        cm.save()
        unified_file = os.path.join(camp_dir, "test_unified_camp", "campaign_state.json")
        self.assertTrue(os.path.exists(unified_file))

        # 2. Register clue and assert synchronization with state["discovered_clues"]
        clue = ClueRecord(
            id="clue_altar_sigil",
            title="Sigil of the Blood Altar",
            content="A crimson mark glowing behind the sacrificial stone."
        )
        cm.register_clue(clue)

        # Direct in-memory synchronization
        self.assertIn("Sigil of the Blood Altar", cm.state["discovered_clues"])

        # Reload from disk and verify persistence in campaign_state.json and state.json
        reloaded = CampaignManager(campaign_id="test_unified_camp", storage_root=camp_dir)
        self.assertIn("Sigil of the Blood Altar", reloaded.state["discovered_clues"])
        self.assertIn("clue_altar_sigil", reloaded.manifest.clues)

        with open(unified_file, "r", encoding="utf-8") as f:
            unified_data = json.load(f)
        self.assertIn("Sigil of the Blood Altar", unified_data.get("discovered_clues", []))

    def test_quest_completion_synchronization_via_tool(self):
        """Verify tool_update_quest_status synchronously updates world context and session state."""
        camp_dir = os.path.join(self.test_dir, "campaigns")
        cm = CampaignManager(campaign_id="active_campaign", storage_root=camp_dir)

        # Add quest
        q = QuestRecord(
            id="quest_slay_gargoyle",
            title="Slay the Cathedral Gargoyle",
            status=QuestStatus.ACTIVE,
            objectives=[
                QuestObjective(id="obj_reach_roof", description="Ascend to the bell tower roof"),
                QuestObjective(id="obj_defeat_beast", description="Defeat the stone gargoyle")
            ]
        )
        cm.add_quest(q)

        # Point campaign tool singleton to this instance
        old_campaign = ct._CAMPAIGN
        try:
            ct._CAMPAIGN = cm
            res = tool_update_quest_status("quest_slay_gargoyle", "completed")
            self.assertEqual(res["new_status"], "completed")

            # Check world context
            self.assertEqual(cm.manifest.quests["quest_slay_gargoyle"].status, QuestStatus.COMPLETED)
            self.assertTrue(all(o.is_completed for o in cm.manifest.quests["quest_slay_gargoyle"].objectives))

            # Check session state
            self.assertEqual(cm.state["quests"]["quest_slay_gargoyle"]["status"], "completed")
            self.assertIn("Slay the Cathedral Gargoyle", cm.state["completed_quests"])
            self.assertEqual(cm.state.get("active_quest"), "")

            # Verify persisted unified state on disk
            unified_file = os.path.join(camp_dir, "active_campaign", "campaign_state.json")
            with open(unified_file, "r", encoding="utf-8") as f:
                disk_data = json.load(f)
            self.assertEqual(disk_data["manifest"]["quests"]["quest_slay_gargoyle"]["status"], "completed")
            self.assertIn("Slay the Cathedral Gargoyle", disk_data.get("completed_quests", []))
        finally:
            ct._CAMPAIGN = old_campaign


if __name__ == "__main__":
    unittest.main()
