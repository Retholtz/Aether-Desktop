"""
tests/test_campaign_engine.py - Verification Suite for Campaign & World State Engine
"""
import os
import shutil
import unittest
from core.campaign.campaign_manager import CampaignManager
from core.campaign.schema import (
    LocationNode, LocationType, QuestRecord, QuestObjective,
    QuestStatus, ClueRecord, FactionRecord, FactionDisposition
)


class TestCampaignEngine(unittest.TestCase):
    def setUp(self):
        self.test_dir = "/tmp/aether_campaign_tests"
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        self.manager = CampaignManager(campaign_id="test_realm", storage_root=self.test_dir)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_full_campaign_lifecycle(self):
        # 1. Initialize Manifest Parameters
        self.manager.manifest.title = "Echoes of the Underdark"
        self.manager.manifest.premise = "Investigate the silence of the dwarven outpost."
        self.manager.manifest.tone_sliders = {"grittiness": 7, "magic_tech_level": 5, "political_intrigue": 6}

        # 2. Setup Location Tree
        region = LocationNode(
            id="loc_surface",
            name="Spine of the World",
            type=LocationType.REGION,
            description="Blizzard-swept peaks."
        )
        dungeon = LocationNode(
            id="loc_outpost",
            name="Durahn's Gate",
            type=LocationType.DUNGEON,
            parent_id="loc_surface",
            description="A fortified stone archway buried under glacial ice.",
            hazards=["Freezing Cold (DC 10 CON)"],
            hidden_secrets=["A collapsed ventilation shaft bypasses the main portcullis."]
        )
        self.manager.add_location(region)
        self.manager.add_location(dungeon)

        # Move to dungeon
        nav = self.manager.set_active_location("loc_outpost")
        self.assertEqual(nav["active_location"], "Durahn's Gate")
        self.assertIn("Freezing Cold (DC 10 CON)", nav["hazards"])

        # 3. Quests & Objectives
        q1 = QuestRecord(
            id="quest_find_gate",
            title="The Silent Outpost",
            status=QuestStatus.ACTIVE,
            giver_npc="Elder Morvin",
            objectives=[
                QuestObjective(id="obj_locate", description="Locate the entry portcullis"),
                QuestObjective(id="obj_breach", description="Breach the interior courtyard")
            ]
        )
        self.manager.add_quest(q1)

        # Complete first objective
        res_obj = self.manager.complete_objective("quest_find_gate", "obj_locate")
        self.assertEqual(res_obj["quest_status"], "active")

        # 4. Factions
        f1 = FactionRecord(
            id="fac_iron_consortium",
            name="Iron Consortium",
            disposition=FactionDisposition.NEUTRAL,
            influence_score=50,
            public_reputation="Dwarven mining guild with strict contracts."
        )
        self.manager.manifest.factions[f1.id] = f1

        # Adjust reputation
        res_fac = self.manager.adjust_faction_reputation("fac_iron_consortium", delta_influence=+15, new_disposition=FactionDisposition.FRIENDLY)
        self.assertEqual(res_fac["influence"], 65)
        self.assertEqual(res_fac["disposition"], "friendly")

        # 5. Clues & Secret Partitioning
        c1 = ClueRecord(
            id="clue_journal",
            title="Scattered Scribe Journal",
            content="The miners didn't dig too deep—they were hollowed out from above.",
            is_revealed=False
        )
        self.manager.register_clue(c1)

        # Validate prompt context before revealing clue
        context_pre = self.manager.generate_prompt_context()
        self.assertIn("ACTIVE CAMPAIGN: Echoes of the Underdark", context_pre)
        self.assertIn("CURRENT LOCATION: Durahn's Gate", context_pre)
        self.assertIn("[X] Locate the entry portcullis", context_pre)
        self.assertNotIn("hollowed out from above", context_pre)  # Unrevealed clue remains hidden
        self.assertNotIn("ventilation shaft", context_pre)       # Hidden location secret remains hidden

        # Reveal clue
        self.manager.reveal_clue("clue_journal")
        context_post = self.manager.generate_prompt_context()
        self.assertIn("The miners didn't dig too deep", context_post)

        # 6. Persistence Validation
        reloaded = CampaignManager(campaign_id="test_realm", storage_root=self.test_dir)
        self.assertEqual(reloaded.manifest.title, "Echoes of the Underdark")
        self.assertEqual(reloaded.manifest.active_location_id, "loc_outpost")
        self.assertEqual(reloaded.manifest.quests["quest_find_gate"].objectives[0].is_completed, True)
        self.assertEqual(reloaded.manifest.clues["clue_journal"].is_revealed, True)


if __name__ == "__main__":
    unittest.main()
