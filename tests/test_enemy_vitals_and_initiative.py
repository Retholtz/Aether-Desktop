import unittest
from unittest.mock import MagicMock
import shutil
import tempfile
import asyncio
from core.ttrpg_service import TTRPGService
from tools.ttrpg_tools import register_dm_adjudication_tools, register_ttrpg_tools


class TestEnemyVitalsAndInitiative(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.service = TTRPGService(data_dir=self.test_dir)
        self.camp_id = "test_combat_camp"
        self.service.create_campaign("Test Combat Campaign", "dnd_5e", campaign_id=self.camp_id)
        self.service.set_active_campaign(self.camp_id)

        # Add a hero character to the roster
        self.service.active_state["characters"] = {
            "char_hero": {
                "id": "char_hero",
                "name": "Valeros",
                "is_player": True,
                "vitals": {
                    "health": {"current": 40, "max": 40, "label": "Health"},
                    "guard": {"current": 16, "max": 16, "label": "Armor Class"}
                },
                "conditions": []
            }
        }
        self.service.save_active_campaign_state()

        # Set up mock dispatcher and tools
        self.dispatcher = MagicMock()
        self.events = []

        def event_cb(evt, data):
            self.events.append((evt, data))

        register_ttrpg_tools(self.dispatcher, self.service, notify_ui_callback=event_cb)
        register_dm_adjudication_tools(self.dispatcher, self.service, notify_ui_callback=event_cb)

        self.tools = {
            call.kwargs["name"]: call.kwargs["handler"]
            for call in self.dispatcher.register_tool.call_args_list
            if "name" in call.kwargs and "handler" in call.kwargs
        }

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_compute_health_status_thresholds(self):
        """Validates D&D 5e qualitative health status thresholds."""
        # 19 Max HP (Standard 5e Ape)
        self.assertEqual(TTRPGService.compute_health_status(19, 19), "Healthy")
        self.assertEqual(TTRPGService.compute_health_status(15, 19), "Injured")
        self.assertEqual(TTRPGService.compute_health_status(9, 19), "Bloodied")   # ~47%
        self.assertEqual(TTRPGService.compute_health_status(2, 19), "Critical")   # ~10%
        self.assertEqual(TTRPGService.compute_health_status(1, 19), "Critical")   # ~5%
        self.assertEqual(TTRPGService.compute_health_status(0, 19), "Defeated")
        self.assertEqual(TTRPGService.compute_health_status(-5, 19), "Defeated")

    def test_start_combat_populates_enemy_presets(self):
        """Validates that starting combat rolls initiative and assigns 5e presets to monsters."""
        res = self.service.start_combat(enemies=["Ape 1", "Ape 2", "Goblin Scout"], round_number=1)
        self.assertEqual(res["status"], "success")
        ladder = self.service.active_state["initiative_order"]
        self.assertEqual(len(ladder), 4)  # 1 player (Valeros) + 3 enemies

        ape1 = next(item for item in ladder if item["name"] == "Ape 1")
        self.assertFalse(ape1["is_player"])
        self.assertEqual(ape1["current_hp"], 19)
        self.assertEqual(ape1["max_hp"], 19)
        self.assertEqual(ape1["armor_class"], 12)
        self.assertEqual(ape1["status"], "Healthy")

        goblin = next(item for item in ladder if item["name"] == "Goblin Scout")
        self.assertEqual(goblin["max_hp"], 7)   # Goblin preset
        self.assertEqual(goblin["armor_class"], 15)

        hero = next(item for item in ladder if item["name"] == "Valeros")
        self.assertTrue(hero["is_player"])
        self.assertEqual(hero["current_hp"], 40)
        self.assertEqual(hero["max_hp"], 40)

    def test_modify_character_stat_on_ladder_enemy(self):
        """Validates modifying monster HP directly on the initiative ladder."""
        self.service.start_combat(enemies=["Ape 1", "Ape 2"])

        # Deal 10 damage to Ape 1 (19 -> 9: Bloodied)
        res = self.service.modify_character_stat("Ape 1", "health", -10)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["old_value"], 19)
        self.assertEqual(res["new_value"], 9)
        self.assertEqual(res["health_status"], "Bloodied")

        # Deal another 8 damage to Ape 1 (9 -> 1: Critical)
        res2 = self.service.modify_character_stat("Ape 1", "health", -8)
        self.assertEqual(res2["new_value"], 1)
        self.assertEqual(res2["health_status"], "Critical")

        # Deal 5 more damage to Ape 1 (1 -> 0: Defeated, clamped)
        res3 = self.service.modify_character_stat("Ape 1", "health", -5)
        self.assertEqual(res3["new_value"], 0)
        self.assertEqual(res3["health_status"], "Defeated")

        comb = self.service._find_combatant("Ape 1")
        self.assertIn("Defeated", comb.get("conditions", []))

    def test_modify_vitals_tool_on_initiative_enemy(self):
        """Validates that the modify_vitals tool succeeds when targeting combatants."""
        self.service.start_combat(enemies=["Ape 4"])

        # Execute modify_vitals via async tool handler
        tool = self.tools.get("modify_vitals")
        self.assertIsNotNone(tool)

        result = asyncio.run(tool(character_name="Ape 4", stat_name="health", delta=-18))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["character"], "Ape 4")
        self.assertEqual(result["old"], 19)
        self.assertEqual(result["current"], 1)
        self.assertEqual(result["health_status"], "Critical")

        # Verify ladder state persisted
        comb = self.service._find_combatant("Ape 4")
        self.assertEqual(comb["current_hp"], 1)
        self.assertEqual(comb["status"], "Critical")

    def test_player_health_sync_with_ladder(self):
        """Validates that damaging a player character synchronizes with their ladder entry."""
        self.service.start_combat(enemies=["Ape 1"])

        # Valeros takes 15 damage
        res = self.service.modify_character_stat("Valeros", "health", -15)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["new_value"], 25)

        # Roster character updated
        _, char = self.service._find_character("Valeros")
        self.assertEqual(char["vitals"]["health"]["current"], 25)

        # Ladder entry updated
        comb = self.service._find_combatant("Valeros")
        self.assertEqual(comb["current_hp"], 25)
        self.assertEqual(comb["status"], "Injured")

    def test_advance_turn_skips_defeated_combatants(self):
        """Validates that advance_turn automatically skips defeated enemies."""
        # Ladder: Hero, Ape 1, Ape 2, Ape 3
        self.service.start_combat(enemies=["Ape 1", "Ape 2", "Ape 3"])
        # Set rolls manually for deterministic order:
        # Index 0: Valeros (20)
        # Index 1: Ape 1 (18) - Defeated
        # Index 2: Ape 2 (16) - Defeated
        # Index 3: Ape 3 (14) - Alive (Critical)
        self.service.active_state["initiative_order"] = [
            {"entity_id": "char_hero", "name": "Valeros", "roll": 20, "is_player": True, "current_hp": 40, "max_hp": 40, "status": "Healthy"},
            {"entity_id": "enemy_1", "name": "Ape 1", "roll": 18, "is_player": False, "current_hp": 0, "max_hp": 19, "status": "Defeated"},
            {"entity_id": "enemy_2", "name": "Ape 2", "roll": 16, "is_player": False, "current_hp": 0, "max_hp": 19, "status": "Defeated"},
            {"entity_id": "enemy_3", "name": "Ape 3", "roll": 14, "is_player": False, "current_hp": 1, "max_hp": 19, "status": "Critical"},
        ]
        self.service.active_state["active_turn_index"] = 0  # Valeros active
        self.service.active_state["current_round"] = 1

        # Advance turn from Valeros (index 0) -> should skip Ape 1 and Ape 2, landing on Ape 3 (index 3)
        res = self.service.advance_turn()
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["active_entity"], "Ape 3")
        self.assertFalse(res["is_player"])
        self.assertEqual(res["turn_index"], 3)

        # Advance turn from Ape 3 (index 3) -> wrap to Round 2, landing on Valeros (index 0)
        res2 = self.service.advance_turn()
        self.assertEqual(res2["status"], "success")
        self.assertEqual(res2["active_entity"], "Valeros")
        self.assertTrue(res2["is_player"])
        self.assertEqual(res2["turn_index"], 0)
        self.assertEqual(res2["round"], 2)

    def test_advance_turn_detects_all_enemies_defeated(self):
        """Validates that advance_turn recognizes victory when all enemies are down."""
        self.service.start_combat(enemies=["Ape 1"])
        self.service.modify_character_stat("Ape 1", "health", -19)  # Defeated

        res = self.service.advance_turn()
        self.assertEqual(res["status"], "success")
        self.assertTrue(res.get("combat_ended"))
        self.assertIn("victorious", res.get("message", "").lower())

    def test_build_gm_context_prompt_includes_active_turn_directives(self):
        """Validates that build_gm_context_prompt emits explicit turn rules for players and monsters."""
        self.service.start_combat(enemies=["Ape 4"])
        # Set active to monster (Ape 4)
        self.service.active_state["initiative_order"][0]["is_player"] = False
        self.service.active_state["active_turn_index"] = 0
        prompt = self.service.build_gm_context_prompt()

        self.assertIn("CURRENT ACTIVE TURN:", prompt)
        self.assertIn("HOSTILE MONSTER", prompt)
        self.assertIn("DO NOT ask the players 'What do you do?'", prompt)


    def test_clean_stale_combat_and_clear_defeated(self):
        """Validates that stale combat with dead foes is cleaned and cleared."""
        # 1. Ladder with only defeated enemies
        self.service.active_state["initiative_order"] = [
            {"entity_id": "e1", "name": "Ape 1", "roll": 20, "is_player": False, "status": "Defeated", "current_hp": 0},
            {"entity_id": "e2", "name": "Ape 2", "roll": 18, "is_player": False, "status": "Defeated", "current_hp": 0},
        ]
        self.assertTrue(self.service.clean_stale_combat_if_needed())
        self.assertEqual(len(self.service.active_state["initiative_order"]), 0)

        # 2. clear_defeated_combatants
        self.service.active_state["initiative_order"] = [
            {"entity_id": "hero", "name": "Valeros", "roll": 20, "is_player": True, "status": "Healthy", "current_hp": 40},
            {"entity_id": "e1", "name": "Ape 1", "roll": 18, "is_player": False, "status": "Defeated", "current_hp": 0},
            {"entity_id": "e2", "name": "Sprite Pack", "roll": 14, "is_player": False, "status": "Healthy", "current_hp": 10},
        ]
        res = self.service.clear_defeated_combatants()
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["removed"], ["Ape 1"])
        ladder = self.service.active_state["initiative_order"]
        self.assertEqual(len(ladder), 2)
        self.assertEqual([c["name"] for c in ladder], ["Valeros", "Sprite Pack"])

    def test_roll_dice_automatic_initiative_bridge(self):
        """Validates that roll_dice with purpose='Initiative' auto-clears stale combat and populates the ladder."""
        # Leave dead apes on the ladder
        self.service.active_state["initiative_order"] = [
            {"entity_id": "e1", "name": "Ape 1", "roll": 20, "is_player": False, "status": "Defeated", "current_hp": 0},
        ]

        tool_roll = self.tools.get("roll_dice")
        self.assertIsNotNone(tool_roll)

        # 1. Hero rolls initiative
        res1 = asyncio.run(tool_roll(character_name="Valeros", formula="1d20+2", purpose="Valeros Initiative Roll"))
        self.assertEqual(res1["status"], "success")

        # Dead ape should be purged, Valeros should be on the ladder
        ladder = self.service.active_state["initiative_order"]
        self.assertEqual(len(ladder), 1)
        self.assertEqual(ladder[0]["name"], "Valeros")
        self.assertTrue(ladder[0]["is_player"])

        # 2. Sprite Pack rolls initiative
        res2 = asyncio.run(tool_roll(character_name="Sprite Pack", formula="1d20+4", purpose="Sprite Pack Initiative Roll"))
        self.assertEqual(res2["status"], "success")

        ladder2 = self.service.active_state["initiative_order"]
        self.assertEqual(len(ladder2), 2)
        sprite = next(c for c in ladder2 if c["name"] == "Sprite Pack")
        self.assertFalse(sprite["is_player"])
        self.assertEqual(sprite["max_hp"], 10)  # Preset applied!
        self.assertEqual(sprite["armor_class"], 15)


if __name__ == "__main__":
    unittest.main()
