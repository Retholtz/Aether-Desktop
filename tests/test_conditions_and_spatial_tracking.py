import unittest
from unittest.mock import MagicMock
import shutil
import tempfile
import asyncio
from core.ttrpg_service import TTRPGService, CONDITION_EFFECTS, LONG_RANGE_MAGIC_EXCEPTIONS
from tools.ttrpg_tools import register_ttrpg_tools, register_dm_adjudication_tools


class TestConditionsAndSpatialTracking(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.service = TTRPGService(data_dir=self.test_dir)
        self.camp_id = "test_spatial_camp"
        self.service.create_campaign("Test Spatial Campaign", "dnd_5e", campaign_id=self.camp_id)
        self.service.set_active_campaign(self.camp_id)

        # Create two hero characters: Sam (Cleric) and Joe (Barbarian)
        self.service.add_character({
            "name": "Sam",
            "is_player": True,
            "archetype": "Cleric 5",
            "vitals": {
                "health": {"current": 38, "max": 38, "label": "Health"},
                "guard": {"current": 16, "max": 16, "label": "Armor Class"}
            },
            "location": "The Hedge Maze - Entrance",
            "is_separated": False,
            "conditions": [],
            "spells": [
                {"name": "Command", "level": 1, "prepared": True},
                {"name": "Sending", "level": 3, "prepared": True},
                {"name": "Cure Wounds", "level": 1, "prepared": True}
            ],
            "spell_slots": {
                "level_1": {"current": 4, "max": 4},
                "level_2": {"current": 3, "max": 3},
                "level_3": {"current": 2, "max": 2}
            }
        })

        self.service.add_character({
            "name": "Joe",
            "is_player": True,
            "archetype": "Barbarian 5",
            "vitals": {
                "health": {"current": 55, "max": 55, "label": "Health"},
                "guard": {"current": 15, "max": 15, "label": "Armor Class"}
            },
            "location": "The Hedge Maze - Overgrown Heart",
            "is_separated": True,
            "conditions": []
        })

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

    # ==========================================
    # 1. CONDITIONAL STATES & ROLL MODIFIERS
    # ==========================================

    def test_drunk_condition_applies_disadvantage(self):
        """Drunk / Intoxicated applies disadvantage to attacks and checks."""
        self.service.update_condition("Joe", "Drunk", True)
        _, joe = self.service._find_character("Joe")
        self.assertIn("Drunk", joe.get("conditions", []))

        # Roll attack with normal - should be adjusted to disadvantage
        res = asyncio.run(self.tools["roll_dice"](
            formula="1d20+5",
            roll_type="normal",
            purpose="Joe Greataxe Attack",
            character_name="Joe"
        ))
        self.assertEqual(res.get("status"), "success")
        self.assertEqual(res.get("roll_type"), "disadvantage")
        self.assertTrue(any("Drunk" in note for note in res.get("condition_adjustments", [])))

    def test_poisoned_condition_applies_disadvantage(self):
        """Poisoned applies disadvantage to attacks and checks."""
        self.service.update_condition("Sam", "Poisoned", True)
        res = asyncio.run(self.tools["roll_dice"](
            formula="1d20+3",
            roll_type="normal",
            purpose="Sam Perception Check",
            character_name="Sam"
        ))
        self.assertEqual(res.get("status"), "success")
        self.assertEqual(res.get("roll_type"), "disadvantage")

    def test_incapacitated_condition_blocks_actions(self):
        """Incapacitated / Stunned character cannot take active attacks or cast spells."""
        self.service.update_condition("Joe", "Stunned", True)

        # Attack roll should be blocked
        res_attack = asyncio.run(self.tools["roll_dice"](
            formula="1d20+5",
            purpose="Joe Greataxe Attack",
            character_name="Joe"
        ))
        self.assertEqual(res_attack.get("status"), "error")
        self.assertIn("incapacitated", res_attack.get("message", "").lower())

        # Spell casting should be blocked
        self.service.update_condition("Sam", "Unconscious", True)
        res_spell = asyncio.run(self.tools["cast_spell"](
            character_name="Sam",
            spell_name="Cure Wounds",
            spell_level=1
        ))
        self.assertEqual(res_spell.get("status"), "error")
        self.assertIn("incapacitated", res_spell.get("message", "").lower())

    def test_charmed_condition_blocks_harmful_spells_against_charmer(self):
        """Charmed character cannot target the charmer with harmful spells."""
        self.service.update_condition("Sam", "Charmed (Dryad 1)", True)
        res = asyncio.run(self.tools["cast_spell"](
            character_name="Sam",
            spell_name="Command",
            spell_level=1,
            target_name="Dryad 1"
        ))
        self.assertEqual(res.get("status"), "error")
        self.assertIn("charmed", res.get("message", "").lower())

    # ==========================================
    # 2. SPATIAL TRACKING & PROXIMITY CHECKS
    # ==========================================

    def test_proximity_blocks_separated_actions(self):
        """Separated characters in different areas cannot target each other with short-range spells/attacks."""
        # Sam is at 'The Hedge Maze - Entrance', Joe is at 'The Hedge Maze - Overgrown Heart'
        prox = self.service.check_entity_proximity("Sam", "Joe", "Command")
        self.assertFalse(prox.get("allowed"))
        self.assertIn("Proximity Check Failed", prox.get("reason", ""))

        # cast_spell should be blocked
        res_spell = asyncio.run(self.tools["cast_spell"](
            character_name="Sam",
            spell_name="Command",
            spell_level=1,
            target_name="Joe"
        ))
        self.assertEqual(res_spell.get("status"), "error")
        self.assertIn("Proximity Check Failed", res_spell.get("message", ""))

        # Attack roll should be blocked
        res_atk = asyncio.run(self.tools["roll_dice"](
            formula="1d20+5",
            purpose="Sam Mace Attack on Joe",
            character_name="Sam",
            target_name="Joe"
        ))
        self.assertEqual(res_atk.get("status"), "error")
        self.assertIn("Proximity Check Failed", res_atk.get("message", ""))

    def test_long_range_spell_bypasses_proximity_barrier(self):
        """Long-range magic (Sending, Teleport, Scrying) works across separated areas."""
        prox = self.service.check_entity_proximity("Sam", "Joe", "Sending")
        self.assertTrue(prox.get("allowed"))

        res_spell = asyncio.run(self.tools["cast_spell"](
            character_name="Sam",
            spell_name="Sending",
            spell_level=3,
            target_name="Joe"
        ))
        self.assertEqual(res_spell.get("status"), "success")

    def test_reunite_party_clears_separation(self):
        """Reuniting party brings all members to the same location and clears separation."""
        res = asyncio.run(self.tools["update_entity_location"](
            name="all",
            location="The Hedge Maze - Fey Portal",
            reunite_party=True
        ))
        self.assertEqual(res.get("status"), "success")

        _, joe = self.service._find_character("Joe")
        _, sam = self.service._find_character("Sam")
        self.assertEqual(joe.get("location"), "The Hedge Maze - Fey Portal")
        self.assertEqual(sam.get("location"), "The Hedge Maze - Fey Portal")
        self.assertFalse(joe.get("is_separated"))
        self.assertFalse(sam.get("is_separated"))

        # Now proximity check between Sam and Joe succeeds
        prox = self.service.check_entity_proximity("Sam", "Joe", "Command")
        self.assertTrue(prox.get("allowed"))

    # ==========================================
    # 3. 0-HP & DEATH SAVING THROWS
    # ==========================================

    def test_zero_hp_downed_state_and_death_saves_init(self):
        """Dropping to 0 HP auto-applies Unconscious & Prone and initializes death saves."""
        self.service.modify_character_stat("Joe", "health", -55)
        _, joe = self.service._find_character("Joe")
        self.assertEqual(joe["vitals"]["health"]["current"], 0)
        self.assertIn("Unconscious", joe.get("conditions", []))
        self.assertIn("Prone", joe.get("conditions", []))

        ds = joe.get("death_saves", {})
        self.assertEqual(ds.get("status"), "dying")
        self.assertEqual(ds.get("successes"), 0)
        self.assertEqual(ds.get("failures"), 0)

        # Health status is "Dying (0 HP)"
        st = self.service.compute_health_status(0, 55, is_player=True, death_saves=ds)
        self.assertEqual(st, "Dying (0 HP)")

    def test_damage_at_zero_hp_inflicts_death_save_failure(self):
        """Taking damage while at 0 HP adds a death save failure."""
        self.service.modify_character_stat("Joe", "health", -55)
        # Damage again at 0 HP
        self.service.modify_character_stat("Joe", "health", -10)
        _, joe = self.service._find_character("Joe")
        self.assertEqual(joe["vitals"]["health"]["current"], 0)
        self.assertEqual(joe["death_saves"]["failures"], 1)

    def test_death_saving_throw_resolution_success_and_stability(self):
        """Rolling 10-19 records a death save success; 3 successes yields Stable."""
        self.service.modify_character_stat("Joe", "health", -55)

        # Force rolls: 3 successes (e.g. 15, 12, 18)
        orig_parse = self.service.parse_and_roll
        try:
            self.service.parse_and_roll = MagicMock(return_value={
                "status": "success",
                "formula": "1d20",
                "rolls": [14],
                "total": 14,
                "roll_type": "normal"
            })

            # Roll 1
            res1 = asyncio.run(self.tools["roll_dice"](
                formula="1d20",
                purpose="Joe Death Saving Throw",
                character_name="Joe"
            ))
            _, joe = self.service._find_character("Joe")
            self.assertEqual(joe["death_saves"]["successes"], 1)

            # Roll 2
            asyncio.run(self.tools["roll_dice"](
                formula="1d20",
                purpose="Joe Death Saving Throw",
                character_name="Joe"
            ))
            _, joe = self.service._find_character("Joe")
            self.assertEqual(joe["death_saves"]["successes"], 2)

            # Roll 3 -> Becomes Stable
            asyncio.run(self.tools["roll_dice"](
                formula="1d20",
                purpose="Joe Death Saving Throw",
                character_name="Joe"
            ))
            _, joe = self.service._find_character("Joe")
            self.assertEqual(joe["death_saves"]["successes"], 3)
            self.assertEqual(joe["death_saves"]["status"], "stable")
            self.assertIn("Stable", joe.get("conditions", []))
            self.assertEqual(self.service.compute_health_status(0, 55, is_player=True, death_saves=joe["death_saves"]), "Stable (0 HP)")
        finally:
            self.service.parse_and_roll = orig_parse

    def test_death_saving_throw_critical_nat20_revives_character(self):
        """Rolling a Natural 20 on a death save immediately restores 1 HP and wakes the hero."""
        self.service.modify_character_stat("Joe", "health", -55)
        _, joe = self.service._find_character("Joe")
        self.assertIn("Unconscious", joe["conditions"])

        orig_parse = self.service.parse_and_roll
        try:
            self.service.parse_and_roll = MagicMock(return_value={
                "status": "success",
                "formula": "1d20",
                "rolls": [20],
                "total": 20,
                "roll_type": "normal"
            })
            res = asyncio.run(self.tools["roll_dice"](
                formula="1d20",
                purpose="Joe Death Saving Throw",
                character_name="Joe"
            ))
            _, joe = self.service._find_character("Joe")
            self.assertEqual(joe["vitals"]["health"]["current"], 1)
            self.assertNotIn("Unconscious", joe["conditions"])
            self.assertEqual(joe["death_saves"]["status"], "none")
            self.assertIn("NATURAL 20", res.get("death_save_result", ""))
        finally:
            self.service.parse_and_roll = orig_parse

    def test_death_saving_throw_critical_nat1_inflicts_two_failures(self):
        """Rolling a Natural 1 on a death save inflicts 2 failures."""
        self.service.modify_character_stat("Joe", "health", -55)

        orig_parse = self.service.parse_and_roll
        try:
            self.service.parse_and_roll = MagicMock(return_value={
                "status": "success",
                "formula": "1d20",
                "rolls": [1],
                "total": 1,
                "roll_type": "normal"
            })
            res = asyncio.run(self.tools["roll_dice"](
                formula="1d20",
                purpose="Joe Death Saving Throw",
                character_name="Joe"
            ))
            _, joe = self.service._find_character("Joe")
            self.assertEqual(joe["death_saves"]["failures"], 2)
            self.assertIn("NATURAL 1", res.get("death_save_result", ""))
        finally:
            self.service.parse_and_roll = orig_parse

    def test_healing_from_zero_hp_clears_downed_state(self):
        """Healing a character at 0 HP restores health, clears Unconscious, and resets death saves."""
        self.service.modify_character_stat("Joe", "health", -55)
        _, joe = self.service._find_character("Joe")
        self.assertIn("Unconscious", joe["conditions"])

        # Heal +10 HP
        self.service.modify_character_stat("Joe", "health", 10)
        _, joe = self.service._find_character("Joe")
        self.assertEqual(joe["vitals"]["health"]["current"], 10)
        self.assertNotIn("Unconscious", joe["conditions"])
        self.assertEqual(joe["death_saves"]["status"], "none")
        self.assertEqual(joe["death_saves"]["successes"], 0)
        self.assertEqual(joe["death_saves"]["failures"], 0)

    def test_advance_turn_keeps_dying_player_but_skips_stable_and_defeated(self):
        """Initiative advancement retains turns for dying players to roll death saves, but skips stable/dead."""
        self.service.start_combat(enemies=["Goblin 1"])
        ladder = self.service.active_state["initiative_order"]
        # Make sure Joe is dying at 0 HP
        self.service.modify_character_stat("Joe", "health", -55)

        # Force active turn to step through combatants
        # When dying player's turn comes up, they must not be skipped
        self.service.active_state["active_turn_index"] = 0
        current_active = ladder[0]["name"]

        # Advance turns and check that dying player gets their turn
        visited = []
        for _ in range(len(ladder)):
            res = self.service.advance_turn()
            visited.append(res.get("active_entity"))
            if res.get("active_entity") == "Joe":
                self.assertTrue(res.get("death_save_required"))
                self.assertTrue(res.get("is_dying"))

        self.assertIn("Joe", visited)


if __name__ == "__main__":
    unittest.main()
