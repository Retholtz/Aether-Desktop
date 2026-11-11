"""
tests/test_phase2_combat_lifecycle.py - Phase 2 Combat State Machine, Death Engine, Reactions & Tactical Space
"""
import unittest
import json
import os
import shutil
from pathlib import Path

from core.combat.death_engine import DeathEngine, DeathSaveTracker, DeathSaveResult
from core.combat.concentration import ConcentrationManager, ConcentrationRecord
from core.combat.reactions import evaluate_reaction_triggers
from core.combat.condition_manager import ConditionRecord, tick_conditions
from core.scene.corpse_manager import CorpseManager, CorpseContainer
from core.scene.range_bands import RangeBand, RangeBandTracker
from core.scene.scene_manager import SceneManager
from core.vault.graveyard import archive_to_graveyard
from core.combat.combat_manager import CombatManager, Combatant


class TestPhase2CombatLifecycle(unittest.TestCase):
    def setUp(self):
        self.test_dir = "tests/test_phase2_temp"
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)
        os.makedirs(self.test_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    # =========================================================================
    # Task 2.1: Death State Machine & Death Saving Throws
    # =========================================================================

    def test_death_saves_progression(self):
        """Verify RAW 5e death saving throw progression, Nat 20 revival, Nat 1 critical failures."""
        state = DeathEngine.initialize_death_state()
        self.assertEqual(state["successes"], 0)
        self.assertEqual(state["failures"], 0)
        self.assertEqual(state["status"], "dying")

        # Roll >= 10 adds 1 success
        res, msg = DeathEngine.resolve_death_save(state, roll_override=12)
        self.assertEqual(res["successes"], 1)
        self.assertEqual(res["failures"], 0)
        self.assertEqual(res["status"], "dying")

        # Roll < 10 adds 1 failure
        res, msg = DeathEngine.resolve_death_save(res, roll_override=8)
        self.assertEqual(res["successes"], 1)
        self.assertEqual(res["failures"], 1)
        self.assertEqual(res["status"], "dying")

        # Reach 3 successes -> status becomes "stable"
        res, _ = DeathEngine.resolve_death_save(res, roll_override=14)
        res, msg = DeathEngine.resolve_death_save(res, roll_override=15)
        self.assertEqual(res["successes"], 3)
        self.assertEqual(res["status"], "stable")

        # Reach 3 failures -> status becomes "dead"
        failing_state = {"successes": 0, "failures": 2, "status": "dying"}
        dead_res, _ = DeathEngine.resolve_death_save(failing_state, roll_override=5)
        self.assertEqual(dead_res["failures"], 3)
        self.assertEqual(dead_res["status"], "dead")

        # Nat 1 adds 2 failures immediately
        nat1_state = {"successes": 0, "failures": 0, "status": "dying"}
        res_nat1, _ = DeathEngine.resolve_death_save(nat1_state, roll_override=1)
        self.assertEqual(res_nat1["failures"], 2)

        # Nat 20 restores 1 HP immediately and revives
        entity = {
            "id": "char_hero",
            "name": "Hero",
            "vitals": {"health": {"current": 0, "max": 20}},
            "conditions": ["Unconscious", "Prone", "Dying"],
            "death_saves": {"successes": 1, "failures": 2, "status": "dying"}
        }
        res_nat20, msg_20 = DeathEngine.resolve_death_save(entity, roll_override=20)
        self.assertEqual(res_nat20["status"], "revived")
        self.assertEqual(res_nat20["successes"], 0)
        self.assertEqual(res_nat20["failures"], 0)
        self.assertEqual(entity["vitals"]["health"]["current"], 1)
        self.assertNotIn("Unconscious", entity["conditions"])
        self.assertNotIn("Prone", entity["conditions"])

    def test_damage_at_zero(self):
        """Verify RAW 5e damage at 0 HP: normal hits, critical hits, and massive damage."""
        # Normal hit adds 1 failure
        state = {"successes": 0, "failures": 0, "status": "dying"}
        res, _ = DeathEngine.apply_damage_at_zero(state, damage_amount=5, is_critical=False, max_hp=20)
        self.assertEqual(res["failures"], 1)

        # Critical hit adds 2 failures
        res, _ = DeathEngine.apply_damage_at_zero(res, damage_amount=6, is_critical=True, max_hp=20)
        self.assertEqual(res["failures"], 3)
        self.assertEqual(res["status"], "dead")

        # Massive damage (damage >= max_hp) slays character outright
        fresh_state = {"successes": 0, "failures": 0, "status": "dying"}
        massive_res, msg = DeathEngine.apply_damage_at_zero(
            fresh_state,
            damage_amount=25,
            is_critical=False,
            max_hp=20
        )
        self.assertEqual(massive_res["failures"], 3)
        self.assertEqual(massive_res["status"], "dead")
        self.assertIn("Massive damage", msg)

        # Passing full entity updates conditions
        entity = {
            "id": "char_tank",
            "name": "Tank",
            "vitals": {"health": {"current": 0, "max": 30}},
            "conditions": [],
            "death_saves": {"successes": 0, "failures": 0, "status": "dying"}
        }
        DeathEngine.apply_damage_at_zero(entity, damage_amount=35, max_hp=30)
        self.assertIn("Dead", entity["conditions"])
        self.assertEqual(entity["death_saves"]["status"], "dead")

    # =========================================================================
    # Task 2.2: Corpse Containers & Expiration Timers
    # =========================================================================

    def test_corpse_decay_and_gentle_repose(self):
        """Verify corpse container generation, inventory transfer, revivify rounds, and Gentle Repose freeze."""
        corpse_mgr = CorpseManager()
        scene_mgr = SceneManager(campaign_id="test_camp", base_data_dir=self.test_dir)

        entity = {
            "id": "char_wizard",
            "name": "Elminster Jr",
            "inventory": [{"name": "Spellbook", "qty": "1"}, {"name": "Staff of Power", "qty": "1"}],
            "weapons": [{"name": "Dagger", "damage": "1d4"}]
        }

        corpse = corpse_mgr.spawn_corpse_from_entity(
            entity=entity,
            current_round=2,
            location_id="dungeon_room_3",
            scene_manager=scene_mgr
        )

        self.assertIn("Elminster Jr", corpse.name)
        self.assertEqual(corpse.revivify_rounds_remaining, 10)
        self.assertEqual(corpse.raise_dead_days_remaining, 10.0)
        self.assertEqual(len(corpse.inventory), 3)  # Spellbook, Staff, Dagger
        self.assertEqual(len(entity["inventory"]), 0)  # Emptied from live entity
        self.assertIn("dungeon_room_3", scene_mgr.active_containers)

        # Tick 3 combat rounds
        corpse_mgr.tick_corpse_timers(rounds_passed=3, days_passed=0.0)
        self.assertEqual(corpse.revivify_rounds_remaining, 7)

        # Apply Gentle Repose: freezes timers for 10 days
        applied = corpse_mgr.apply_gentle_repose(corpse.corpse_id)
        self.assertTrue(applied)
        self.assertTrue(corpse.gentle_repose_active)
        self.assertEqual(corpse.gentle_repose_days_remaining, 10.0)

        # Tick combat rounds while gentle repose is active: revivify counter MUST NOT decrement
        corpse_mgr.tick_corpse_timers(rounds_passed=5, days_passed=2.0)
        self.assertEqual(corpse.revivify_rounds_remaining, 7)
        self.assertEqual(corpse.gentle_repose_days_remaining, 8.0)

        # Fast forward 8 more days: gentle repose expires
        corpse_mgr.tick_days(8.0)
        self.assertFalse(corpse.gentle_repose_active)
        self.assertEqual(corpse.gentle_repose_days_remaining, 0.0)

        # Subsequent round tick resumes revivify decay
        corpse_mgr.tick_round()
        self.assertEqual(corpse.revivify_rounds_remaining, 6)

    # =========================================================================
    # Task 2.3: Active Concentration Engine & Damage Interrupts
    # =========================================================================

    def test_concentration_lifecycle(self):
        """Verify concentration setting, DC calculation, dropping previous spell, and damage saves."""
        conc_mgr = ConcentrationManager()

        # DC calculation: DC = max(10, damage // 2)
        self.assertEqual(conc_mgr.get_dc_for_damage(6), 10)
        self.assertEqual(conc_mgr.get_dc_for_damage(20), 10)
        self.assertEqual(conc_mgr.get_dc_for_damage(24), 12)
        self.assertEqual(conc_mgr.get_dc_for_damage(50), 25)

        # Begin concentration
        caster = {
            "id": "char_sorc",
            "name": "Ignis",
            "attributes": {"Constitution": 14},  # +2 CON save
            "features_and_traits": []
        }
        conc_mgr.begin_concentration(
            caster_id="char_sorc",
            spell_name="Haste",
            spell_level=3,
            current_round=1,
            dependents=["char_fighter"]
        )
        self.assertTrue(conc_mgr.is_concentrating("char_sorc"))

        # Setting a new concentration spell drops the previous one
        dropped = conc_mgr.set_concentration(
            caster_id="char_sorc",
            spell_name="Wall of Fire",
            spell_level=4,
            current_round=2
        )
        self.assertIsNotNone(dropped)
        self.assertEqual(dropped.spell_name, "Haste")
        self.assertEqual(conc_mgr.active_concentration["char_sorc"].spell_name, "Wall of Fire")

        # Test Concentration save on damage: High roll succeeds
        # Damage 24 -> DC 12. Roll 10 + 2 CON = 12 -> Success!
        maintained, dc, narrative = conc_mgr.check_concentration_on_damage(
            caster,
            damage_taken=24,
            roll_override=10
        )
        self.assertTrue(maintained)
        self.assertEqual(dc, 12)
        self.assertTrue(conc_mgr.is_concentrating("char_sorc"))

        # Test Concentration save on damage: Low roll fails and drops concentration
        # Damage 24 -> DC 12. Roll 8 + 2 CON = 10 -> Fail!
        maintained, dc, narrative = conc_mgr.check_concentration_on_damage(
            caster,
            damage_taken=24,
            roll_override=8
        )
        self.assertFalse(maintained)
        self.assertEqual(dc, 12)
        self.assertFalse(conc_mgr.is_concentrating("char_sorc"))

        # Advantage from War Caster
        caster_warcaster = {
            "id": "char_cleric",
            "name": "Aurelia",
            "attributes": {"Constitution": 10},  # +0 CON save
            "features_and_traits": ["War Caster"]
        }
        conc_mgr.set_concentration("char_cleric", "Spirit Guardians", 3)
        maintained, dc, narrative = conc_mgr.check_concentration_on_damage(
            caster_warcaster,
            damage_taken=10,
            roll_override=12
        )
        self.assertTrue(maintained)
        self.assertIn("Advantage", narrative)

    # =========================================================================
    # Task 2.4: Range Bands & Opportunity Attacks
    # =========================================================================

    def test_range_bands_and_opportunity_attacks(self):
        """Verify abstract 3-tier range bands and opportunity attack triggers on leaving ENGAGED."""
        tracker = RangeBandTracker()
        tracker.set_position("hero_1", RangeBand.ENGAGED)
        self.assertEqual(tracker.get_position("hero_1"), RangeBand.ENGAGED)

        # Leaving ENGAGED without disengaging triggers opportunity attack
        hostiles = [{"id": "goblin_a", "name": "Goblin Sneak"}]
        res = tracker.shift_band("hero_1", RangeBand.NEAR, has_disengaged=False, hostiles_in_melee=hostiles)
        self.assertEqual(res["new_band"], "near")
        self.assertEqual(len(res["events"]), 1)
        self.assertEqual(res["events"][0]["event"], "opportunity_attack_opportunity")
        self.assertIn("goblin_a", res["events"][0]["hostiles"])

        # Leaving ENGAGED with Disengage action DOES NOT trigger opportunity attack
        tracker.set_position("hero_1", RangeBand.ENGAGED)
        res_disengage = tracker.shift_band("hero_1", RangeBand.NEAR, has_disengaged=True, hostiles_in_melee=hostiles)
        self.assertEqual(len(res_disengage["events"]), 0)

        # Moving from NEAR to FAR does not trigger melee opportunity attacks
        res_far = tracker.shift_band("hero_1", RangeBand.FAR, has_disengaged=False, hostiles_in_melee=hostiles)
        self.assertEqual(len(res_far["events"]), 0)

    def test_reactions_evaluation(self):
        """Verify reaction polling for opportunity attacks, Shield spell, and Counterspell."""
        # 1. on_leave_melee
        hostiles = [
            {"id": "orc_1", "name": "Orc Berserker", "reaction_used": False},
            {"id": "orc_2", "name": "Orc Chieftain", "reaction_used": True}  # Reaction already spent
        ]
        context_melee = {"hostiles": hostiles, "entity_id": "hero_1", "has_disengaged": False}
        reactions = evaluate_reaction_triggers("on_leave_melee", context_melee)
        self.assertEqual(len(reactions), 1)
        self.assertEqual(reactions[0]["actor_id"], "orc_1")
        self.assertEqual(reactions[0]["reaction_type"], "opportunity_attack")

        # 2. on_attacked_hit (Shield / Hellish Rebuke)
        target = {
            "id": "char_wizard",
            "name": "Wizard",
            "reaction_used": False,
            "spells": [{"name": "Shield"}, {"name": "Hellish Rebuke"}]
        }
        context_hit = {"target": target, "attacker_id": "orc_1"}
        hit_reactions = evaluate_reaction_triggers("on_attacked_hit", context_hit)
        self.assertEqual(len(hit_reactions), 2)
        types = [r["reaction_type"] for r in hit_reactions]
        self.assertIn("cast_shield", types)
        self.assertIn("cast_hellish_rebuke", types)

        # 3. on_spell_cast (Counterspell within 60 ft / NEAR range)
        opponents = [
            {
                "id": "opp_sorcerer",
                "name": "Evil Mage",
                "range_band": "near",
                "reaction_used": False,
                "spells": [{"name": "Counterspell"}]
            },
            {
                "id": "opp_distant",
                "name": "Distant Archer",
                "range_band": "far",  # > 60 ft away, cannot counter
                "reaction_used": False,
                "spells": [{"name": "Counterspell"}]
            }
        ]
        context_cast = {"caster_id": "hero_cleric", "opponents": opponents}
        cast_reactions = evaluate_reaction_triggers("on_spell_cast", context_cast)
        self.assertEqual(len(cast_reactions), 1)
        self.assertEqual(cast_reactions[0]["actor_id"], "opp_sorcerer")
        self.assertEqual(cast_reactions[0]["reaction_type"], "cast_counterspell")

    # =========================================================================
    # Task 2.5: Condition Timers & Structured Ticks
    # =========================================================================

    def test_condition_manager_ticks(self):
        """Verify structured condition ticking, start/end of turn saves, and duration expiration."""
        entity = {
            "id": "char_paladin",
            "name": "Paladin",
            "attributes": {"Wisdom": 14},  # +2 WIS save
            "structured_conditions": [
                {
                    "condition": "Hold Person",
                    "duration_rounds": 10,
                    "save_dc": 13,
                    "save_type": "Wisdom",
                    "save_timing": "end_of_turn"
                },
                {
                    "condition": "Stunned",
                    "duration_rounds": 1,
                    "save_dc": None,
                    "save_type": None,
                    "save_timing": "none"
                }
            ],
            "conditions": ["Hold Person", "Stunned"]
        }

        # Start of turn: Hold Person has end_of_turn timing, so no save is rolled yet
        start_logs = tick_conditions(entity, timing="start_of_turn")
        self.assertEqual(len(start_logs), 0)
        self.assertIn("Hold Person", entity["conditions"])

        # End of turn: Roll save vs DC 13. Roll 8 + 2 WIS = 10 -> Fail!
        fail_logs = tick_conditions(entity, timing="end_of_turn", roll_override=8)
        self.assertTrue(any("failed DC 13" in log for log in fail_logs))
        self.assertIn("Hold Person", entity["conditions"])
        # Stunned had 1 round duration, so it expires at end of turn
        self.assertNotIn("Stunned", entity["conditions"])
        self.assertTrue(any("Stunned" in log and "expired" in log for log in fail_logs))

        # Next End of turn: Roll save vs DC 13. Roll 12 + 2 WIS = 14 -> Success!
        success_logs = tick_conditions(entity, timing="end_of_turn", roll_override=12)
        self.assertTrue(any("succeeded on DC 13" in log for log in success_logs))
        self.assertNotIn("Hold Person", entity["conditions"])

    # =========================================================================
    # Task 2.6: TPK Fail-Forward Routing & Graveyard Archival
    # =========================================================================

    def test_tpk_detection_and_routing(self):
        """Verify Total Party Kill detection and fail-forward branching routes."""
        combat = CombatManager(system_id="dnd_5e")
        participants = [
            {
                "id": "pc_fighter",
                "name": "Fighter",
                "is_adversary": False,
                "vitals": {"health": {"current": 0, "max": 20}},
                "death_saves": {"successes": 0, "failures": 0, "status": "dying"},
                "inventory": [{"name": "Greatsword", "qty": "1"}]
            },
            {
                "id": "pc_cleric",
                "name": "Cleric",
                "is_adversary": False,
                "vitals": {"health": {"current": 0, "max": 18}},
                "death_saves": {"successes": 0, "failures": 3, "status": "dead"},
                "conditions": ["Dead"],
                "inventory": [{"name": "Mace", "qty": "1"}]
            },
            {
                "id": "enemy_dragon",
                "name": "Red Dragon",
                "is_adversary": True,
                "vitals": {"health": {"current": 100, "max": 100}}
            }
        ]

        combat.start_encounter(participants)
        self.assertTrue(combat.check_tpk_condition())
        self.assertTrue(combat.check_tpk())

        # Calling next_turn triggers tpk event
        next_res = combat.next_turn()
        self.assertEqual(next_res.get("event"), "tpk_triggered")

        # Test Branch 1: prison_break
        res_prison = combat.handle_tpk_routing("prison_break")
        self.assertEqual(res_prison["branch"], "prison_break")
        fighter = combat._find_combatant("pc_fighter")
        self.assertEqual(fighter.raw_data["vitals"]["health"]["current"], 1)
        self.assertEqual(len(fighter.raw_data["inventory"]), 0)  # Gear moved to quarantined stash
        self.assertEqual(len(fighter.raw_data["quarantined_stash"]), 1)

        # Test Branch 2: patron_pact
        res_pact = combat.handle_tpk_routing("patron_pact")
        self.assertEqual(res_pact["branch"], "patron_pact")
        self.assertEqual(fighter.raw_data["vitals"]["health"]["current"], 20)
        self.assertIn("Geas / Debt", fighter.raw_data["conditions"])

        # Test Branch 3: underworld_odyssey
        res_underworld = combat.handle_tpk_routing("underworld_odyssey")
        self.assertEqual(res_underworld["branch"], "underworld_odyssey")
        self.assertEqual(fighter.raw_data["vitals"]["health"]["current"], 10)  # 20 // 2
        self.assertIn("Shadowfell Taint", fighter.raw_data["conditions"])

        # Test Branch 4: archive (Graveyard Archival)
        campaign_dir = os.path.join(self.test_dir, "tpk_camp")
        os.makedirs(campaign_dir, exist_ok=True)
        # Create dummy state.json
        state_path = os.path.join(campaign_dir, "state.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"characters": {"pc_fighter": {}, "pc_cleric": {}}}, f)

        res_archive = combat.handle_tpk_routing("archive", campaign_id="tpk_camp", storage_root=self.test_dir)
        self.assertEqual(res_archive["branch"], "archive")
        self.assertEqual(res_archive["archived_count"], 2)
        self.assertFalse(combat.is_active)  # Encounter closed

        # Verify graveyard files exist on disk
        fighter_grave = os.path.join(self.test_dir, "tpk_camp", "graveyard", "pc_fighter.json")
        cleric_grave = os.path.join(self.test_dir, "tpk_camp", "graveyard", "pc_cleric.json")
        self.assertTrue(os.path.exists(fighter_grave))
        self.assertTrue(os.path.exists(cleric_grave))

        # Verify purged from active state.json
        with open(state_path, "r", encoding="utf-8") as f:
            updated_state = json.load(f)
        self.assertNotIn("pc_fighter", updated_state["characters"])
        self.assertNotIn("pc_cleric", updated_state["characters"])

    def test_combat_manager_death_interrupt(self):
        """Verify next_turn interrupts when an active PC is dying, and skips when stable."""
        combat = CombatManager(system_id="dnd_5e")
        participants = [
            {
                "id": "pc_dying",
                "name": "Dying Hero",
                "is_adversary": False,
                "vitals": {"health": {"current": 0, "max": 15}},
                "death_saves": {"successes": 0, "failures": 0, "status": "dying"}
            },
            {
                "id": "pc_healthy",
                "name": "Healthy Ally",
                "is_adversary": False,
                "vitals": {"health": {"current": 15, "max": 15}}
            }
        ]

        initiatives = {"Dying Hero": 10, "Healthy Ally": 20}
        combat.start_encounter(participants, reported_initiatives=initiatives)
        # Turn 0 is Healthy Ally (init 20)
        self.assertEqual(combat.turn_order[combat.turn_index].name, "Healthy Ally")

        # Healthy Ally ends turn -> Advances to Dying Hero
        hud = combat.next_turn()
        self.assertEqual(hud.get("event"), "awaiting_death_save")
        self.assertEqual(hud.get("combatant_id"), "pc_dying")

        # If Dying Hero becomes stabilized, advancing turn skips without death save prompt
        dying_hero = combat._find_combatant("pc_dying")
        dying_hero.raw_data["death_saves"]["status"] = "stable"

        # Now when advancing to Dying Hero, it skips automatically to Healthy Ally
        hud2 = combat.next_turn()
        self.assertEqual(hud2.get("active_combatant"), "Healthy Ally")


if __name__ == "__main__":
    unittest.main()
