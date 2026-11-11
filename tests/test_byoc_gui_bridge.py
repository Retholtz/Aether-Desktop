"""
tests/test_byoc_gui_bridge.py - Automated Verification of BYOC GUI Bridge Endpoints
"""
import os
import shutil
import unittest
from unittest.mock import MagicMock
from core.ttrpg_service import TTRPGService
from core.gui_bridge import GUIBridge


class TestBYOCGUIBridge(unittest.TestCase):
    def setUp(self):
        self.test_dir = "/tmp/aether_byoc_bridge_tests"
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        os.makedirs(self.test_dir, exist_ok=True)

        manifest_path = os.path.join(self.test_dir, "campaigns.json")
        self.service = TTRPGService(
            data_dir=os.path.join(self.test_dir, "campaigns"),
            manifest_path=manifest_path
        )
        mock_engine = MagicMock()
        mock_engine.ttrpg_service = self.service
        mock_engine.is_ttrpg_session_active.return_value = False
        self.bridge = GUIBridge(engine=mock_engine)
        self.bridge.ttrpg_service = self.service

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_byoc_lifecycle_through_bridge(self):
        # 1. Create Campaign with BYOC Data
        byoc_payload = {
            "genre": "dark_fantasy",
            "tone_sliders": {"grittiness": 8, "magic_tech_level": 4, "political_intrigue": 7},
            "starting_location": {
                "name": "Kraghold Gates",
                "type": "dungeon",
                "description": "A frostbitten citadel gatehouse.",
                "hazards": "Extreme Cold, Fragile Ice"
            },
            "starting_faction": {
                "name": "Obsidian Vanguard",
                "disposition": "hostile",
                "agenda": "Purge all outsiders from the ruins."
            },
            "starting_quest": {
                "title": "Breach Kraghold",
                "giver_npc": "Captain Vael",
                "objective": "Find a way past the fortified iron gates."
            }
        }

        res_create = self.bridge.create_ttrpg_campaign(
            title="Kraghold Expedition",
            system_preset="dnd_5e",
            setting_overview="A perilous delve into the frozen ruins.",
            byoc_data=byoc_payload
        )
        self.assertEqual(res_create["status"], "success")
        cid = res_create["campaign"]["id"]

        # 2. Query World State
        state_res = self.bridge.get_ttrpg_world_state(campaign_id=cid)
        self.assertEqual(state_res["status"], "success")
        manifest = state_res["manifest"]
        self.assertEqual(manifest["genre"], "dark_fantasy")
        self.assertEqual(manifest["tone_sliders"]["grittiness"], 8)
        self.assertIn("loc_kraghold_gates", manifest["locations"])
        self.assertIn("fac_obsidian_vanguard", manifest["factions"])
        self.assertIn("quest_breach_kraghold", manifest["quests"])

        # 3. Add Location & Navigate
        res_loc = self.bridge.byoc_add_location({
            "name": "Inner Courtyard",
            "type": "room",
            "description": "Silent courtyard paved with cracked obsidian slabs.",
            "hazards": "Lurking Wraiths"
        }, campaign_id=cid)
        self.assertEqual(res_loc["status"], "success")

        res_nav = self.bridge.byoc_set_active_location("loc_inner_courtyard", campaign_id=cid)
        self.assertEqual(res_nav["status"], "success")

        # 4. Adjust Faction
        res_fac = self.bridge.byoc_adjust_faction("fac_obsidian_vanguard", delta=+10, disposition="suspicious", campaign_id=cid)
        self.assertEqual(res_fac["status"], "success")
        self.assertEqual(res_fac["result"]["disposition"], "suspicious")

        # 5. Complete Objective
        res_obj = self.bridge.byoc_complete_objective("quest_breach_kraghold", "obj_1", campaign_id=cid)
        self.assertEqual(res_obj["status"], "success")
        self.assertEqual(res_obj["result"]["quest_status"], "completed")

        # 6. Add & Reveal Clue
        res_clue = self.bridge.byoc_add_clue({
            "title": "Frozen Logbook",
            "content": "The gate mechanism can be operated from the eastern tower."
        }, campaign_id=cid)
        self.assertEqual(res_clue["status"], "success")
        clue_id = res_clue["clue"]

        res_reveal = self.bridge.byoc_reveal_clue(clue_id, campaign_id=cid)
        self.assertEqual(res_reveal["status"], "success")
        self.assertEqual(res_reveal["result"]["revealed_clue"], "Frozen Logbook")

    def test_byoc_studio_payload_initialization(self):
        """Verify dedicated World Builder Studio payload with multi-node lists & universal tone sliders."""
        studio_payload = {
            "genre": "cyberpunk",
            "tone_sliders": {"grittiness": 9, "magic_tech_level": 10, "political_intrigue": 8},
            "locations": [
                {
                    "name": "Neon Alley 42",
                    "type": "district",
                    "description": "Rain-slicked backstreet with flickering holo-ads.",
                    "hazards": ["Acid Rain", "Corrupted Bio-Drones"]
                },
                {
                    "name": "Arasaka Sub-Vault",
                    "type": "room",
                    "description": "High security data vault behind biometric locks.",
                    "hazards": ["Laser Grid (DC 15 DEX)"]
                }
            ],
            "factions": [
                {
                    "name": "Chrome Reapers",
                    "disposition": "hostile",
                    "influence_score": 75,
                    "agenda": "Control cyberware black markets.",
                    "public_reputation": "Violent street booster gang."
                },
                {
                    "name": "Netwatch Operatives",
                    "disposition": "neutral",
                    "influence_score": 85,
                    "agenda": "Contain rogue AIs past the Blackwall.",
                    "public_reputation": "Federal cyberspace police."
                }
            ],
            "quests": [
                {
                    "title": "Extraction at Midnight",
                    "giver_npc": "Fixer Dex",
                    "summary": "Retrieve the decrypted shard from the sub-vault.",
                    "objectives": [
                        {"id": "obj_1", "description": "Infiltrate Neon Alley 42", "is_completed": True},
                        {"id": "obj_2", "description": "Bypass the laser grid", "is_completed": False}
                    ]
                }
            ],
            "clues": [
                {
                    "title": "Decryption Chip",
                    "content": "Contains military coordinates for the orbital relay.",
                    "is_revealed": False
                },
                {
                    "title": "Leaked Netwatch Badge",
                    "content": "Grants access to Sector 4 relay terminal.",
                    "is_revealed": True
                }
            ]
        }

        res = self.bridge.create_ttrpg_campaign(
            title="Night City Infiltration",
            system_preset="cyberpunk",
            setting_overview="Dark neon skyscrapers looming over street level decay.",
            byoc_data=studio_payload
        )
        self.assertEqual(res["status"], "success")
        cid = res["campaign"]["id"]

        # Verify disk persistence via CampaignManager directly
        from core.campaign.campaign_manager import CampaignManager
        c_mgr = CampaignManager(campaign_id=cid, storage_root=self.service.data_dir)
        self.assertEqual(c_mgr.manifest.genre, "cyberpunk")
        self.assertEqual(c_mgr.manifest.tone_sliders["grittiness"], 9)
        self.assertEqual(c_mgr.manifest.tone_sliders["magic_tech_level"], 10)
        self.assertEqual(c_mgr.manifest.tone_sliders["political_intrigue"], 8)

        # Verify locations
        self.assertIn("loc_neon_alley_42", c_mgr.manifest.locations)
        self.assertIn("loc_arasaka_sub_vault", c_mgr.manifest.locations)
        loc1 = c_mgr.manifest.locations["loc_neon_alley_42"]
        self.assertIn("Acid Rain", loc1.hazards)

        # Verify factions
        self.assertIn("fac_chrome_reapers", c_mgr.manifest.factions)
        self.assertIn("fac_netwatch_operatives", c_mgr.manifest.factions)
        self.assertEqual(c_mgr.manifest.factions["fac_chrome_reapers"].influence_score, 75)

        # Verify quests & objectives
        self.assertIn("quest_extraction_at_midnight", c_mgr.manifest.quests)
        q = c_mgr.manifest.quests["quest_extraction_at_midnight"]
        self.assertEqual(len(q.objectives), 2)
        self.assertTrue(q.objectives[0].is_completed)

        # Verify clues (partitioning between secret and revealed)
        self.assertIn("clue_decryption_chip", c_mgr.manifest.clues)
        self.assertIn("clue_leaked_netwatch_badge", c_mgr.manifest.clues)
        self.assertFalse(c_mgr.manifest.clues["clue_decryption_chip"].is_revealed)
        self.assertTrue(c_mgr.manifest.clues["clue_leaked_netwatch_badge"].is_revealed)

    def test_get_ttrpg_data_json_serializable_and_store_sync(self):
        """Verify get_ttrpg_data and get_ttrpg_world_state are strictly JSON serializable with LocationNodes and preserve characters across saves."""
        import json
        byoc_payload = {
            "genre": "heroic_fantasy",
            "locations": [
                {
                    "id": "loc_durahns_gate",
                    "name": "Durahn's Gate",
                    "type": "dungeon",
                    "description": "Ancient stone fortress gate.",
                    "hazards": ["Freezing Cold"]
                }
            ]
        }
        res_create = self.bridge.create_ttrpg_campaign(
            title="Serializable Realm",
            system_preset="dnd_5e",
            byoc_data=byoc_payload
        )
        self.assertEqual(res_create["status"], "success")
        cid = res_create["campaign"]["id"]

        # Ensure PyWebView JSON serialization never fails on LocationNode Pydantic models
        ttrpg_payload = self.bridge.get_ttrpg_data()
        serialized = json.dumps(ttrpg_payload)
        self.assertIn("Serializable Realm", serialized)
        self.assertEqual(ttrpg_payload["active_campaign"], cid)
        self.assertIn(cid, ttrpg_payload["campaigns"])
        self.assertIsInstance(ttrpg_payload["world_state"]["locations"]["loc_durahns_gate"], dict)

        ws_payload = self.bridge.get_ttrpg_world_state(cid)
        json.dumps(ws_payload)
        self.assertEqual(ws_payload["status"], "success")

        # Add character via TTRPGService, then mutate via BYOC -> character must NOT be wiped
        self.service.add_character({
            "name": "Aria",
            "vitals": {"health": {"current": 22, "max": 22}, "guard": {"current": 15, "max": 15}}
        })
        res_loc = self.bridge.byoc_add_location({"name": "Sunken Hall", "type": "room"}, campaign_id=cid)
        self.assertEqual(res_loc["status"], "success")

        reloaded_data = self.bridge.get_ttrpg_data()
        chars = reloaded_data["state"]["characters"]
        self.assertTrue(any(c.get("name") == "Aria" for c in chars.values()), f"Character lost after BYOC save: {chars}")


if __name__ == "__main__":
    unittest.main()
