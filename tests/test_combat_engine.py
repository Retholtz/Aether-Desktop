"""
tests/test_combat_engine.py - Automated End-to-End Combat Verification
"""
import unittest
from core.combat.combat_manager import CombatManager
from core.scene.scene_manager import SceneManager


class TestCoreGMEngine(unittest.TestCase):
    def setUp(self):
        self.combat = CombatManager(system_id="dnd_5e")
        self.scene = SceneManager(campaign_id="test_run", base_data_dir="/tmp/aether_tests")
        self.scene.clear_adversaries()

        # Dummy player character (Seraphina, Artificer 1)
        self.player = {
            "id": "char_seraphina",
            "name": "Seraphina Lightbrew",
            "is_adversary": False,
            "vitals": {"health": {"current": 10, "max": 10}, "guard": {"current": 12, "max": 12}},
            "native_system_data": {
                "armor_class": 12,
                "initiative_bonus": 1,
                "ability_scores": {"Dexterity": 12, "Constitution": 15},
                "hit_points": {"current": 10, "max": 10, "temp": 0},
                "weapons": [{"name": "Dagger", "atk_bonus": "+3", "damage": "1d4+1 piercing"}],
                "spell_slots": {"level_1": {"current": 0, "max": 2, "reset_on": "long_rest"}},
                "custom_meters": {
                    "hit_dice": {"label": "Hit Dice (1d8)", "current": 1, "max": 1, "reset_on": "long_rest"},
                    "healing_hands": {"label": "Healing Hands", "current": 0, "max": 1, "reset_on": "long_rest"}
                }
            }
        }
        self.scene.set_party([self.player])

    def test_end_to_end_combat_and_rest(self):
        # 1. Spawn 2 Goblins into scene
        gob1 = self.scene.spawn_adversary("Goblin Raider", hp=7, ac=15, attack_bonus="+4", damage="1d6+2", dex_score=14)
        gob2 = self.scene.spawn_adversary("Goblin Archer", hp=7, ac=13, attack_bonus="+4", damage="1d6+2", dex_score=14)

        participants = self.scene.get_all_participants()
        self.assertEqual(len(participants), 3)

        # 2. Start Combat with forced initiative values
        initiatives = {
            "Seraphina Lightbrew": 18,
            "Goblin Raider": 12,
            "Goblin Archer": 6
        }
        hud = self.combat.start_encounter(participants, reported_initiatives=initiatives)
        self.assertTrue(hud["is_active"])
        self.assertEqual(hud["round"], 1)
        self.assertEqual(hud["active_combatant"], "Seraphina Lightbrew")

        # 3. Seraphina attacks Goblin Raider (Forced Hit)
        res = self.combat.execute_attack(
            "Seraphina Lightbrew",
            "Goblin Raider",
            weapon_name="Dagger",
            reported_atk_roll=18,
            reported_dmg_roll=5
        )
        self.assertTrue(res["attack_result"].is_hit)
        self.assertEqual(res["damage_report"].current_hp, 2)  # 7 - 5 = 2

        # 4. Advance turns
        hud = self.combat.next_turn()
        self.assertEqual(hud["active_combatant"], "Goblin Raider")

        hud = self.combat.next_turn()
        self.assertEqual(hud["active_combatant"], "Goblin Archer")

        # Advance into Round 2
        hud = self.combat.next_turn()
        self.assertEqual(hud["round"], 2)
        self.assertEqual(hud["active_combatant"], "Seraphina Lightbrew")

        # 5. Seraphina defeats Goblin Raider
        res2 = self.combat.execute_attack(
            "Seraphina Lightbrew",
            "Goblin Raider",
            weapon_name="Dagger",
            reported_atk_roll=20,
            reported_dmg_roll=5
        )
        self.assertTrue(res2["damage_report"].is_downed)
        self.assertEqual(res2["damage_report"].current_hp, 0)

        # 6. End encounter
        summary = self.combat.end_encounter()
        self.assertFalse(self.combat.is_active)
        self.assertEqual(summary["rounds_elapsed"], 2)

        # 7. Execute Long Rest on Seraphina
        rest_report = self.combat.adapter.execute_rest(self.player, rest_type="long")
        self.assertEqual(self.player["native_system_data"]["hit_points"]["current"], 10)
        self.assertEqual(self.player["native_system_data"]["spell_slots"]["level_1"]["current"], 2)
        self.assertEqual(self.player["native_system_data"]["custom_meters"]["healing_hands"]["current"], 1)


if __name__ == "__main__":
    unittest.main()
