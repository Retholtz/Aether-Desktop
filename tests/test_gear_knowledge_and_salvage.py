import unittest
import tempfile
import os
import shutil
import json
from unittest.mock import MagicMock
from core.ttrpg_service import TTRPGService

class TestGearKnowledgeAndSalvage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.service = TTRPGService(storage_dir=self.temp_dir)
        self.service.event_bus = MagicMock()

        # Initialize mock campaign state
        self.service.active_state = {
            "campaign_id": "camp_test_knowledge",
            "campaign_name": "Siege of Highmore",
            "current_location": "Camp Highmore Dungeon Cells",
            "stored_items": [],
            "characters": {
                "char_garrick": {
                    "id": "char_garrick",
                    "name": "Garrick",
                    "class": "Fighter",
                    "level": 3,
                    "weapons": [
                        {"name": "Longsword", "atk_bonus": "+5", "damage": "1d8+3 Slashing"},
                        {"name": "Dagger", "atk_bonus": "+5", "damage": "1d4+3 Piercing"}
                    ],
                    "inventory": [
                        {"name": "Golden Dragon Helmet", "qty": "1", "category": "Armor"},
                        {"name": "The One Ring", "qty": "1", "category": "Magic Item"},
                        {"name": "Torch", "qty": "3", "category": "Gear"}
                    ],
                    "spells": [],
                    "conditions": []
                },
                "char_lyra": {
                    "id": "char_lyra",
                    "name": "Lyra",
                    "class": "Rogue",
                    "level": 3,
                    "weapons": [
                        {"name": "Shortbow", "atk_bonus": "+6", "damage": "1d6+3 Piercing"},
                        {"name": "Rapier", "atk_bonus": "+6", "damage": "1d8+3 Piercing"}
                    ],
                    "inventory": [
                        {"name": "Thieves' Tools", "qty": "1", "category": "Tools"},
                        {"name": "Health Potion", "qty": "1", "category": "Potion"}
                    ],
                    "spells": [],
                    "conditions": []
                }
            }
        }
        self.service.active_campaign_id = "camp_test_knowledge"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_disarm_information_asymmetry_defaults_to_unknown(self):
        """Verifies disarming strips weapons, replaces with Unarmed Strike, and records location as unknown."""
        res = self.service.manage_character_inventory(
            character_name="all",
            action="disarm",
            storage_location="Camp Highmore Guard Barracks - Evidence Chest",
            reason="Arrested by Chief Filip"
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["location_knowledge"], "unknown")

        garrick = self.service.active_state["characters"]["char_garrick"]
        lyra = self.service.active_state["characters"]["char_lyra"]

        # Manufactured weapons removed, Unarmed Strike equipped
        self.assertEqual(len(garrick["weapons"]), 1)
        self.assertEqual(garrick["weapons"][0]["name"], "Unarmed Strike")
        self.assertEqual(len(lyra["weapons"]), 1)
        self.assertEqual(lyra["weapons"][0]["name"], "Unarmed Strike")

        # Stored items recorded with unknown location knowledge and confiscated status
        stored = self.service.active_state["stored_items"]
        self.assertEqual(len(stored), 4) # Longsword, Dagger, Shortbow, Rapier
        for entry in stored:
            self.assertEqual(entry["status"], "confiscated")
            self.assertEqual(entry["location_knowledge"], "unknown")
            self.assertTrue(entry["salvageable"])
            self.assertEqual(entry["storage_location"], "Camp Highmore Guard Barracks - Evidence Chest")

    def test_discover_location_reveals_evidence_to_players(self):
        """Tests that discover_location updates knowledge state from unknown to discovered with clue."""
        self.service.manage_character_inventory(
            character_name="all",
            action="disarm",
            storage_location="Camp Highmore Guard Barracks - Evidence Chest"
        )

        res = self.service.manage_character_inventory(
            character_name="all",
            action="discover_location",
            storage_location="Camp Highmore Guard Barracks - Evidence Chest",
            discovery_clue="DC 14 Perception check: Overheard guards in the corridor laughing about locking swords in the barracks chest"
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(len(res["discovered_items"]), 4)

        stored = self.service.active_state["stored_items"]
        for entry in stored:
            self.assertEqual(entry["location_knowledge"], "discovered")
            self.assertIn("Overheard guards", entry["discovery_clue"])

    def test_beast_swallowing_and_salvage(self):
        """Tests beast swallowing gear (inside_creature, salvageable=True) and recovering upon slaying."""
        # Garrick's helmet is swallowed by a dragon
        res_remove = self.service.manage_character_inventory(
            character_name="Garrick",
            action="remove",
            items=["Golden Dragon Helmet"],
            status="inside_creature",
            holder_or_creature="Ancient Red Dragon",
            salvageable=True,
            location_knowledge="known",
            reason="Swallowed whole during breath attack"
        )
        self.assertEqual(res_remove["status"], "success")

        # Helmet is no longer in Garrick's inventory
        garrick = self.service.active_state["characters"]["char_garrick"]
        inv_names = [it["name"] for it in garrick["inventory"]]
        self.assertNotIn("Golden Dragon Helmet", inv_names)

        # Helmet is tracked inside the beast
        stored = self.service.active_state["stored_items"]
        helm_entry = next((e for e in stored if e["name"] == "Golden Dragon Helmet"), None)
        self.assertIsNotNone(helm_entry)
        self.assertEqual(helm_entry["status"], "inside_creature")
        self.assertEqual(helm_entry["holder_or_creature"], "Ancient Red Dragon")
        self.assertEqual(helm_entry["location_knowledge"], "known")
        self.assertTrue(helm_entry["salvageable"])

        # Party slays the beast and salvages it
        res_salvage = self.service.manage_character_inventory(
            character_name="Garrick",
            action="retrieve",
            holder_or_creature="Ancient Red Dragon",
            items=["Golden Dragon Helmet"]
        )
        self.assertEqual(res_salvage["status"], "success")
        self.assertEqual(len(res_salvage["reclaimed_items"]), 1)

        # Helmet is restored to Garrick
        updated_inv_names = [it["name"] for it in garrick["inventory"]]
        self.assertIn("Golden Dragon Helmet", updated_inv_names)
        self.assertEqual(len(self.service.active_state["stored_items"]), 0)

    def test_permanent_destruction_in_lava_cannot_be_reclaimed(self):
        """Tests that items destroyed in lava/acid are permanently lost and cannot be retrieved."""
        res_dest = self.service.manage_character_inventory(
            character_name="Garrick",
            action="destroy",
            items=["The One Ring"],
            storage_location="Mount Doom Lava Pit",
            reason="Cast into the fires of Mount Doom",
            salvageable=False
        )
        self.assertEqual(res_dest["status"], "success")
        self.assertFalse(res_dest["salvageable"])

        # The One Ring is removed from Garrick's inventory
        garrick = self.service.active_state["characters"]["char_garrick"]
        inv_names = [it["name"] for it in garrick["inventory"]]
        self.assertNotIn("The One Ring", inv_names)

        # Recorded in stored_items as destroyed and unsalvageable
        stored = self.service.active_state["stored_items"]
        ring_entry = next((e for e in stored if e["name"] == "The One Ring"), None)
        self.assertIsNotNone(ring_entry)
        self.assertEqual(ring_entry["status"], "destroyed")
        self.assertFalse(ring_entry["salvageable"])

        # Attempting to retrieve / reclaim fails to restore the ring
        res_retrieve = self.service.manage_character_inventory(
            character_name="Garrick",
            action="retrieve",
            items=["The One Ring"]
        )
        self.assertEqual(res_retrieve["status"], "success")
        self.assertEqual(len(res_retrieve["reclaimed_items"]), 0)
        self.assertEqual(len(res_retrieve["rejected_destroyed"]), 1)
        self.assertIn("The One Ring", res_retrieve["rejected_destroyed"])
        self.assertIn("could not be retrieved because they are destroyed", res_retrieve["message"])

        # Still not in inventory
        inv_names_after = [it["name"] for it in garrick["inventory"]]
        self.assertNotIn("The One Ring", inv_names_after)

    def test_destroy_already_confiscated_item(self):
        """Tests destroying an item that was already in stored_items (e.g. evidence locker burned down)."""
        self.service.manage_character_inventory(
            character_name="all",
            action="disarm",
            storage_location="Guard Barracks Evidence Locker"
        )

        res_burn = self.service.manage_character_inventory(
            character_name="Garrick",
            action="destroy",
            weapons=[{"name": "Longsword"}],
            storage_location="Burned Barracks Ash",
            reason="Incinerated in barracks fire"
        )
        self.assertEqual(res_burn["status"], "success")

        stored = self.service.active_state["stored_items"]
        longsword_entry = next((e for e in stored if e["name"] == "Longsword"), None)
        self.assertIsNotNone(longsword_entry)
        self.assertEqual(longsword_entry["status"], "destroyed")
        self.assertFalse(longsword_entry["salvageable"])

    def test_system_prompt_reflects_knowledge_and_salvage_guidance(self):
        """Verifies system prompt includes dynamic knowledge rules, creature details, and retrieval instructions."""
        self.service.manifest = {
            "active_campaign": "camp_test_knowledge",
            "campaigns": {
                "camp_test_knowledge": {
                    "id": "camp_test_knowledge",
                    "title": "Siege of Highmore",
                    "system_preset": "heroic_d20"
                }
            }
        }
        self.service.manage_character_inventory(
            character_name="all",
            action="disarm",
            storage_location="Barracks Armory",
            location_knowledge="unknown"
        )
        self.service.manage_character_inventory(
            character_name="Garrick",
            action="remove",
            items=["Golden Dragon Helmet"],
            status="inside_creature",
            holder_or_creature="Red Dragon",
            salvageable=True,
            location_knowledge="known"
        )
        self.service.manage_character_inventory(
            character_name="Garrick",
            action="destroy",
            items=["The One Ring"],
            storage_location="Mount Doom Lava Pit",
            salvageable=False
        )

        prompt = self.service.build_gm_context_prompt()
        self.assertIn("[CONFISCATED, STORED, OR LOST PARTY ITEMS / WORLD CACHES]", prompt)
        self.assertIn("Knowledge to players: unknown", prompt)
        self.assertIn("Holder/Beast: Red Dragon", prompt)
        self.assertIn("UNSALVAGEABLE/DESTROYED", prompt)
        self.assertIn("INFORMATION ASYMMETRY & DYNAMIC KNOWLEDGE RULES", prompt)
        self.assertIn("BEAST SALVAGE", prompt)
        self.assertIn("PERMANENT DESTRUCTION", prompt)

    def test_auto_reconcile_stored_items_on_campaign_notes(self):
        """Verifies that record_campaign_notes auto-reconciles unknown stored items to discovered when clues/summary reveal location."""
        self.service.manage_character_inventory(
            character_name="all",
            action="disarm",
            storage_location="Evidence Cache",
            location_knowledge="unknown"
        )
        stored_before = self.service.active_state["stored_items"]
        for it in stored_before:
            self.assertEqual(it["location_knowledge"], "unknown")

        # Simulate turn note recording where perception check spotted the weapons
        self.service.record_campaign_notes(
            summary="Shadow rolled a 14 on Perception and spotted the confiscated gear and weapons inside a large wooden chest in the guard room across the corridor.",
            key_clues=["Confiscated weapons, including Blackrazor, are stored inside a large wooden chest in the guard room across the corridor."],
            last_action="Shadow performed a Perception check (total 14) through the cell doorway to locate the party's weapons"
        )

        stored_after = self.service.active_state["stored_items"]
        for it in stored_after:
            self.assertEqual(it["location_knowledge"], "discovered")
            self.assertIn("large wooden chest", it["storage_location"].lower())
            self.assertTrue(len(it["discovery_clue"]) > 0)

if __name__ == "__main__":
    unittest.main()
