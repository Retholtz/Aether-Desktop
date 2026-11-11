"""
tests/test_phase5_multisystem_and_networking.py - Phase 5 Multi-System Rulesets, WebSocket Gateway & Multiverse Warping
"""
import unittest
import asyncio
from core.rulesets import get_ruleset
from core.rulesets.pf2e_ruleset import PF2eRuleset
from core.rulesets.coc7e_ruleset import CoC7eRuleset
from core.rulesets.cyberpunk_ruleset import CyberpunkRuleset
from core.multiverse.ballistics import transmute_ballistic_weapon
from api.ws_gateway import ConnectionManager


class TestPhase5MultiSystemAndNetworking(unittest.TestCase):
    def test_ruleset_registry(self):
        """Verify dynamic registry retrieves all supported game system adapters."""
        self.assertIsInstance(get_ruleset("pf2e"), PF2eRuleset)
        self.assertIsInstance(get_ruleset("coc_7e"), CoC7eRuleset)
        self.assertIsInstance(get_ruleset("cyberpunk_red"), CyberpunkRuleset)
        self.assertEqual(get_ruleset("dnd_5e").system_id, "dnd_5e")

    def test_pf2e_four_degrees_of_success(self):
        """Verify PF2e 4-degree resolution and Nat 20/1 step-up/down."""
        ruleset = PF2eRuleset()
        char = {"name": "Valeros", "skills": {"Athletics": 5}}

        # DC 15, roll 20 -> total 25 (DC + 10) -> Crit Success
        r1 = ruleset.resolve_check(char, "Athletics", target_dc=15, reported_roll=20)
        self.assertEqual(r1.degree, "critical_success")
        self.assertTrue(r1.is_success)

        # DC 15, roll 10 -> total 15 -> Success
        r2 = ruleset.resolve_check(char, "Athletics", target_dc=15, reported_roll=10)
        self.assertEqual(r2.degree, "success")
        self.assertTrue(r2.is_success)

        # DC 15, roll 8 -> total 13 -> Failure
        r3 = ruleset.resolve_check(char, "Athletics", target_dc=15, reported_roll=8)
        self.assertEqual(r3.degree, "failure")
        self.assertFalse(r3.is_success)

        # DC 15, roll 1 -> total 6 (<= DC - 10 and Nat 1) -> Critical Failure
        r4 = ruleset.resolve_check(char, "Athletics", target_dc=15, reported_roll=1)
        self.assertEqual(r4.degree, "critical_failure")
        self.assertFalse(r4.is_success)

    def test_coc7e_d100_roll_under(self):
        """Verify CoC regular, hard, extreme, and fumble mechanics."""
        ruleset = CoC7eRuleset()
        char = {"name": "Harvey Walters"}

        # Skill = 60. Fifth = 12, Half = 30
        # Roll 10 <= 12 -> Extreme Success
        r_extreme = ruleset.resolve_check(char, "Library Use", target_dc=60, reported_roll=10)
        self.assertEqual(r_extreme.degree, "extreme_success")

        # Roll 25 <= 30 -> Hard Success
        r_hard = ruleset.resolve_check(char, "Library Use", target_dc=60, reported_roll=25)
        self.assertEqual(r_hard.degree, "hard_success")

        # Roll 50 <= 60 -> Regular Success
        r_reg = ruleset.resolve_check(char, "Library Use", target_dc=60, reported_roll=50)
        self.assertEqual(r_reg.degree, "success")

        # Roll 70 > 60 -> Failure
        r_fail = ruleset.resolve_check(char, "Library Use", target_dc=60, reported_roll=70)
        self.assertEqual(r_fail.degree, "failure")

        # Roll 100 -> Fumble
        r_fumble = ruleset.resolve_check(char, "Library Use", target_dc=60, reported_roll=100)
        self.assertEqual(r_fumble.degree, "critical_failure")

    def test_coc7e_sanity_damage(self):
        """Verify Sanity damage triggers temporary madness when loss >= 5."""
        ruleset = CoC7eRuleset()
        char = {"name": "Harvey", "vitals": {"sanity": {"current": 60}}}
        rep = ruleset.apply_damage(char, damage_amount=6, damage_type="sanity")
        self.assertEqual(rep.current_hp, 54)
        self.assertIn("Temporary Insanity (Bout of Madness)", char["conditions"])

    def test_cyberpunk_armor_sp_ablation(self):
        """Verify Cyberpunk SP armor blocks damage until exceeded, then ablates by 1."""
        ruleset = CyberpunkRuleset()
        target = {
            "name": "Solo",
            "vitals": {
                "guard": {"current": 10},  # SP = 10
                "health": {"current": 30}
            }
        }
        # Shot dealing 8 damage vs 10 SP -> Completely absorbed, 0 HP loss, 0 ablation
        r1 = ruleset.apply_damage(target, damage_amount=8)
        self.assertEqual(r1.damage_taken, 0)
        self.assertEqual(target["vitals"]["health"]["current"], 30)
        self.assertEqual(target["vitals"]["guard"]["current"], 10)

        # Shot dealing 15 damage vs 10 SP -> 5 HP taken, SP drops from 10 to 9
        r2 = ruleset.apply_damage(target, damage_amount=15)
        self.assertEqual(r2.damage_taken, 5)
        self.assertEqual(target["vitals"]["health"]["current"], 25)
        self.assertEqual(target["vitals"]["guard"]["current"], 9)

    def test_ballistic_transmutation(self):
        """Verify modern firearm maps to d20 weapon with finite_scarcity."""
        weapon = transmute_ballistic_weapon("Militech Crusher Shotgun")
        self.assertIn("Dwarven Blunderbuss", weapon["name"])
        self.assertEqual(weapon["damage"], "2d8 Piercing")
        self.assertTrue(weapon["finite_scarcity"])

        # Test Assault Rifle conversion
        ar = transmute_ballistic_weapon("Arasaka Assault Rifle")
        self.assertIn("Clockwork Volley Carbine", ar["name"])
        self.assertEqual(ar["damage"], "2d10+3 Piercing")
        self.assertTrue(ar["finite_scarcity"])

    def test_ws_connection_manager(self):
        """Verify async connection manager registration and disconnect."""
        mgr = ConnectionManager()
        self.assertEqual(len(mgr.active_campaign_connections), 0)

        class DummyWebSocket:
            def __init__(self):
                self.sent = []
            async def accept(self):
                pass
            async def send_json(self, data):
                self.sent.append(data)

        async def run_ws_test():
            ws1 = DummyWebSocket()
            ws2 = DummyWebSocket()
            await mgr.connect("camp_alpha", ws1)
            await mgr.connect("camp_alpha", ws2)
            self.assertEqual(len(mgr.active_campaign_connections["camp_alpha"]), 2)

            await mgr.broadcast_to_campaign("camp_alpha", {"type": "hud_update", "hp": 25})
            self.assertEqual(len(ws1.sent), 1)
            self.assertEqual(ws1.sent[0]["type"], "hud_update")
            self.assertEqual(len(ws2.sent), 1)

            mgr.disconnect("camp_alpha", ws1)
            self.assertEqual(len(mgr.active_campaign_connections["camp_alpha"]), 1)
            mgr.disconnect("camp_alpha", ws2)
            self.assertNotIn("camp_alpha", mgr.active_campaign_connections)

        asyncio.run(run_ws_test())


if __name__ == "__main__":
    unittest.main()
