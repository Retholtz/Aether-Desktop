"""
tests/test_phase4_narrative_and_scenes.py - Phase 4 Narrative, Quest Gating, Moral Friction & Sub-Scenes
"""
import unittest

from core.briefing import ColdStartBriefingEngine
from core.campaign.schema import CampaignManifest, LocationNode, LocationType, QuestRecord, QuestStatus
from core.campaign.campaign_manager import CampaignManager
from core.rulesets.dnd5e_ruleset import DnD5eRuleset
from core.rulesets.moral_engine import MoralComplianceEngine
from core.scene.scene_manager import SceneManager
from core.combat.combat_manager import CombatManager
from tools.lore_tools import tool_query_campaign_lore


class TestPhase4NarrativeAndScenes(unittest.TestCase):
    def test_cold_start_briefing_generation(self):
        """Verify 3-sentence synthesis and atmospheric context assembling."""
        manifest = {
            "name": "Curse of the Sunken Citadel",
            "active_location_id": "chamber_of_echoes",
            "locations": {
                "chamber_of_echoes": {
                    "name": "Chamber of Echoes",
                    "description": "Black basalt walls drip with brackish water.",
                    "hazards": ["Toxic Spores"]
                }
            },
            "quests": {
                "q1": {
                    "title": "Purge the Spore Heart",
                    "status": "ACTIVE",
                    "objectives": [{"description": "Burn the core", "is_mandatory": True, "is_completed": False}]
                }
            }
        }
        state = {
            "latest_recap": "The heroes slew the guardian hydra.",
            "characters": [{"vitals": {"health": {"current": 45}}}]
        }
        briefing = ColdStartBriefingEngine.synthesize_cold_start_briefing(manifest, state)
        self.assertIn("Curse of the Sunken Citadel", briefing.spoken_intro)
        self.assertIn("Chamber of Echoes", briefing.spoken_intro)
        self.assertIn("Burn the core", briefing.spoken_intro)

    def test_quest_prerequisite_gating(self):
        """Verify that activating a quest fails if prerequisites are not completed."""
        cm = CampaignManager()
        cm.manifest = CampaignManifest(id="camp_test", name="Test")
        cm.manifest.quests["q1"] = QuestRecord(id="q1", title="Find the Key", status=QuestStatus.ACTIVE)
        cm.manifest.quests["q2"] = QuestRecord(
            id="q2",
            title="Open the Vault",
            status=QuestStatus.NOT_STARTED,
            prerequisite_quest_ids=["q1"]
        )

        # Attempt activation while q1 is still ACTIVE -> Fails
        success, msg = cm.update_quest_status("q2", QuestStatus.ACTIVE)
        self.assertFalse(success)
        self.assertIn("Unfulfilled prerequisites", msg)

        # Complete q1 -> Activation succeeds
        cm.manifest.quests["q1"].status = QuestStatus.COMPLETED
        success, msg = cm.update_quest_status("q2", QuestStatus.ACTIVE)
        self.assertTrue(success)

    def test_fail_forward_partial_success(self):
        """Verify that rolling DC - 1 or DC - 2 yields partial_success with consequence."""
        ruleset = DnD5eRuleset()
        char = {"name": "Rogue", "attributes": {"Dexterity": 10}, "skills": {}}
        # DC 15, Roll 13 -> total 13 (DC - 2) -> partial_success
        result = ruleset.resolve_check(char, "Dexterity", target_dc=15, reported_roll=13)
        self.assertTrue(result.is_success)
        self.assertEqual(result.degree, "partial_success")
        self.assertIsNotNone(result.consequence)

    def test_paladin_oath_moral_warning(self):
        """Verify Paladin tenet friction flags theft and warns of Oath_Strained."""
        char = {"name": "Sir Galahad", "archetype": "Paladin 10 (Oath of Devotion)"}
        warning = MoralComplianceEngine.evaluate_oath_compliance(char, "theft")
        self.assertTrue(warning.requires_warning)
        self.assertEqual(warning.resulting_condition, "Oath_Strained")
        self.assertIn("Honesty & Honor", warning.warning_prompt)

    def test_split_party_combat_isolation(self):
        """Verify combat includes only co-located entities, excluding remote allies."""
        sm = SceneManager()
        sm.place_entity("hero_a", "room_cell")
        sm.place_entity("guard_1", "room_cell")
        sm.place_entity("hero_b", "room_courtyard")  # Remote

        all_chars = [
            {"id": "hero_a", "name": "Hero A"},
            {"id": "guard_1", "name": "Guard 1"},
            {"id": "hero_b", "name": "Hero B"}
        ]
        cm = CombatManager()
        combatants = cm.start_encounter_in_location("room_cell", sm, all_chars)
        ids = [c["id"] for c in combatants]
        self.assertIn("hero_a", ids)
        self.assertIn("guard_1", ids)
        self.assertNotIn("hero_b", ids)

    def test_lore_tool_spoiler_partitioning(self):
        """Verify unrevealed secrets are omitted for player role but present for DM."""
        manifest = {
            "locations": {
                "dungeon_crypt": {
                    "name": "Old Crypt",
                    "description": "Dusty tombs line the hall.",
                    "hidden_secrets": ["Secret door behind the east sarcophagus."]
                }
            },
            "clues": {
                "clue_1": {"title": "Cult Letter", "content": "The ritual begins at dusk.", "is_revealed": False}
            }
        }
        # Player Query
        res_player = tool_query_campaign_lore("Crypt", role="player", manifest_data=manifest)
        loc_player = res_player["locations"][0]
        self.assertNotIn("hidden_secrets", loc_player)
        self.assertEqual(len(res_player["clues"]), 0)  # Unrevealed clue scrubbed

        # DM Query
        res_dm = tool_query_campaign_lore("Crypt", role="dm", manifest_data=manifest)
        loc_dm = res_dm["locations"][0]
        self.assertIn("hidden_secrets", loc_dm)


if __name__ == "__main__":
    unittest.main()
