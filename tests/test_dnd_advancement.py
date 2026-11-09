"""
tests/test_dnd_advancement.py - Phase 3 D&D 5e (2014 & 2024) Guided Level-Up & Multiclass Suite
Tests single-class & multiclass progression, retroactive CON & Tough HP, CON save preservation,
2014 vs 2024 subclass & half-caster slot rounding, Warlock Pact isolation on Short Rest,
2024 Level-19 Epic Boons (stat cap 30), DM prompt_level_up_wizard tool, Vault branch isolation,
and engine.py TTRPG workspace tool allowlist.
"""
import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from core.compendiums.dnd_advancement import (
    apply_level_up,
    compute_multiclass_spell_slots,
    get_advancement_options,
)
from core.character_vault_service import CharacterVaultService
from core.parsers.dnd5e_parser import calculate_multiclass_spell_slots
from core.ttrpg_service import TTRPGService
from tools.ttrpg_tools import register_progression_tools


def _make_fighter(name="Garrett", level=3, str_val=16, dex_val=12, con_val=14, cha_val=10):
    con_mod = (con_val - 10) // 2
    hp_max = 10 + con_mod + (level - 1) * (6 + con_mod)
    return {
        "id": "char_fighter_1",
        "name": name,
        "ruleset": "dnd_5e",
        "ruleset_edition": "2024",
        "level": level,
        "experience_points": 900,
        "archetype": f"Fighter {level}",
        "vitals": {"health": {"current": hp_max, "max": hp_max, "label": "Health"}},
        "attributes": {
            "Strength": str_val, "Dexterity": dex_val, "Constitution": con_val,
            "Intelligence": 10, "Wisdom": 12, "Charisma": cha_val,
        },
        "saving_throws": {
            "Strength": f"+{(str_val - 10) // 2 + 2}",
            "Constitution": f"+{con_mod + 2}",
            "Dexterity": f"+{(dex_val - 10) // 2}",
            "Intelligence": "+0",
            "Wisdom": "+1",
            "Charisma": f"+{(cha_val - 10) // 2}",
        },
        "custom_meters": {
            "hit_dice": {"label": f"Hit Dice ({level}d10)", "current": level, "max": level, "reset_on": "long_rest"},
            "second_wind": {"label": "Second Wind", "current": 2, "max": 2, "reset_on": "short_rest"},
        },
        "native_system_data": {
            "class_level": f"Fighter {level}",
            "ability_scores": {
                "STR": str_val, "DEX": dex_val, "CON": con_val,
                "INT": 10, "WIS": 12, "CHA": cha_val,
            },
        },
    }


def _make_wizard(name="Lyra", level=4, int_val=17, con_val=14, edition="2024"):
    return {
        "id": "char_wizard_1",
        "name": name,
        "ruleset": "dnd_5e",
        "ruleset_edition": edition,
        "level": level,
        "experience_points": 2700,
        "archetype": f"Wizard {level}",
        "vitals": {"health": {"current": 26, "max": 26, "label": "Health"}},
        "attributes": {
            "Strength": 8, "Dexterity": 14, "Constitution": con_val,
            "Intelligence": int_val, "Wisdom": 12, "Charisma": 10,
        },
        "custom_meters": {
            "hit_dice": {"label": f"Hit Dice ({level}d6)", "current": level, "max": level, "reset_on": "long_rest"},
        },
        "spells": [
            {"name": "Burning Hands", "level": "1st Level", "atk_bonus": "DC 13 DEX", "prepared": True},
            {"name": "Fire Bolt", "level": "Cantrip", "atk_bonus": "+5", "is_cantrip": True, "prepared": True},
        ],
        "native_system_data": {
            "class_level": f"Wizard {level}",
            "spellcasting_ability": "INT",
            "ability_scores": {
                "STR": 8, "DEX": 14, "CON": con_val,
                "INT": int_val, "WIS": 12, "CHA": 10,
            },
        },
    }


class TestDnDAdvancementEngine(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="aether_phase3_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_retroactive_con_hp_and_save_proficiency_preserved(self):
        fighter = _make_fighter(level=3, con_val=14)
        # CON 14 (+2): L1 12 + L2 8 + L3 8 = 28 HP; CON Save = +2 +2 = +4
        self.assertEqual(fighter["vitals"]["health"]["max"], 28)
        self.assertEqual(fighter["saving_throws"]["Constitution"], "+4")

        res = apply_level_up(fighter, {
            "edition": "2024",
            "target_class": "Fighter",
            "hp_mode": "fixed",
            "ability_increases": {"CON": 2},
        })
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["new_level"], 4)
        self.assertEqual(res["applied_asi"]["CON"], 2)
        self.assertEqual(res["retroactive_con_hp"], 3)
        # Level 4 fixed (6 + new CON 3 = 9) + 3 retroactive = +12 -> 40
        self.assertEqual(res["hp_gain"], 12)
        self.assertEqual(fighter["vitals"]["health"]["max"], 40)
        # CON save proficiency MUST be preserved (+3 CON + 2 Prof = +5)
        self.assertEqual(fighter["saving_throws"]["Constitution"], "+5")

    def test_rolled_hp_and_tough_feat_and_followup_level(self):
        fighter = _make_fighter(level=3, con_val=14)  # 28 HP
        res = apply_level_up(fighter, {
            "edition": "2024",
            "target_class": "Fighter",
            "hp_mode": "rolled",
            "hp_roll": 10,
            "feat": "Tough",
        })
        self.assertEqual(res["status"], "success")
        # 10 (max d10 roll) + 2 (CON) + 8 (Tough 2*4) = +20 HP -> 48
        self.assertEqual(res["hp_gain"], 20)
        self.assertEqual(fighter["vitals"]["health"]["max"], 48)

        # Advancing 4 -> 5 automatically grants +2 Tough HP on top of level HP (6 + 2 + 2 = +10)
        res2 = apply_level_up(fighter, {
            "edition": "2024",
            "target_class": "Fighter",
            "hp_mode": "fixed",
        })
        self.assertEqual(res2["hp_gain"], 10)
        self.assertEqual(fighter["vitals"]["health"]["max"], 58)
        self.assertEqual(fighter["prof_bonus"], "+3")

    def test_resilient_half_feat_and_2024_epic_boon_cap_30(self):
        fighter = _make_fighter(level=18, str_val=20, con_val=14)
        res = apply_level_up(fighter, {
            "edition": "2024",
            "target_class": "Fighter",
            "hp_mode": "fixed",
            "feat": "Boon of Fortitude",
            "ability_increases": {"STR": 1},
        })
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["new_level"], 19)
        self.assertEqual(fighter["attributes"]["Strength"], 21)
        # d10 fixed 6 + CON 2 + Boon of Fortitude 40 = +48
        self.assertEqual(res["hp_gain"], 48)

        # Resilient half-feat on Wizard grants WIS save prof
        wizard = _make_wizard(level=3, con_val=14)
        res_w = apply_level_up(wizard, {
            "edition": "2024",
            "target_class": "Wizard",
            "hp_mode": "fixed",
            "feat": "Resilient",
            "ability_increases": {"CON": 1},
            "resilient_ability": "CON",
        })
        self.assertEqual(res_w["status"], "success")
        self.assertEqual(wizard["attributes"]["Constitution"], 15)
        self.assertEqual(wizard["saving_throws"]["Constitution"], "+4")  # +2 mod + 2 prof

    def test_2014_vs_2024_subclass_levels(self):
        cleric_1 = {
            "name": "Sister Maeve",
            "ruleset": "dnd_5e",
            "level": 1,
            "archetype": "Cleric 1",
            "vitals": {"health": {"current": 10, "max": 10}},
            "attributes": {"Strength": 14, "Dexterity": 10, "Constitution": 14, "Intelligence": 10, "Wisdom": 16, "Charisma": 12},
            "native_system_data": {"class_level": "Cleric 1"},
        }
        opts_2024_l2 = get_advancement_options(cleric_1, edition="2024", target_class="Cleric")
        self.assertFalse(opts_2024_l2["needs_subclass"])  # 2024 Cleric gets subclass at L3

        opts_2014_l2 = get_advancement_options(cleric_1, edition="2014", target_class="Cleric")
        self.assertTrue(opts_2014_l2["needs_subclass"])  # 2014 Cleric had Divine Domain at L1

        apply_level_up(cleric_1, {"edition": "2024", "target_class": "Cleric"})
        opts_2024_l3 = get_advancement_options(cleric_1, edition="2024", target_class="Cleric")
        self.assertTrue(opts_2024_l3["needs_subclass"])
        self.assertIn("Life Domain", opts_2024_l3["subclass_options"])

    def test_multiclass_exit_and_entry_prerequisites_and_bypass(self):
        paladin = {
            "name": "Seraph",
            "ruleset": "dnd_5e",
            "level": 2,
            "archetype": "Paladin 2",
            "vitals": {"health": {"current": 20, "max": 20}},
            # Meets Wizard INT 13 & Paladin STR 13, but FAILS Paladin exit CHA 13!
            "attributes": {"Strength": 15, "Dexterity": 10, "Constitution": 14, "Intelligence": 14, "Wisdom": 10, "CHA": 11, "Charisma": 11},
            "native_system_data": {"class_level": "Paladin 2"},
        }
        fail_res = apply_level_up(paladin, {"edition": "2024", "target_class": "Wizard"})
        self.assertEqual(fail_res["status"], "error")
        self.assertIn("CHA 13", fail_res["message"])

        # Fighter multiclass should succeed cleanly for DEX 14 Rogue without STR 13
        rogue = {
            "name": "Shade",
            "ruleset": "dnd_5e",
            "level": 2,
            "archetype": "Rogue 2",
            "vitals": {"health": {"current": 15, "max": 15}},
            "attributes": {"Strength": 8, "Dexterity": 15, "Constitution": 12, "Intelligence": 10, "Wisdom": 10, "Charisma": 10},
            "native_system_data": {"class_level": "Rogue 2"},
        }
        ok_fighter = apply_level_up(rogue, {"edition": "2024", "target_class": "Fighter"})
        self.assertEqual(ok_fighter["status"], "success")
        self.assertEqual(ok_fighter["class_summary"], "Rogue 2 / Fighter 1")
        self.assertIn("Multiclass (Fighter)", rogue["proficiencies"])

        # DM Override bypasses missing CHA 13 on Paladin
        bypass_res = apply_level_up(paladin, {"edition": "2024", "target_class": "Wizard", "bypass_prereqs": True})
        self.assertEqual(bypass_res["status"], "success")
        self.assertEqual(bypass_res["class_summary"], "Paladin 2 / Wizard 1")

    def test_multiclass_spell_slots_2014_vs_2024_and_parser(self):
        # Paladin 3 / Sorcerer 2
        slots_2014 = compute_multiclass_spell_slots({"Paladin": 3, "Sorcerer": 2}, edition="2014")
        self.assertEqual(slots_2014["effective_caster_level"], 3)  # 3//2 (1) + 2 = 3
        self.assertEqual(slots_2014["spell_slots"]["level_2"]["max"], 2)

        slots_2024 = compute_multiclass_spell_slots({"Paladin": 3, "Sorcerer": 2}, edition="2024")
        self.assertEqual(slots_2024["effective_caster_level"], 4)  # ceil(3/2)=2 + 2 = 4
        self.assertEqual(slots_2024["spell_slots"]["level_2"]["max"], 3)

        # Third-caster Eldritch Knight 3 + Wizard 2 -> ECL 3
        ek_slots = compute_multiclass_spell_slots(
            {"Fighter": 3, "Wizard": 2},
            edition="2024",
            subclasses={"Fighter": "Eldritch Knight"},
        )
        self.assertEqual(ek_slots["effective_caster_level"], 3)
        self.assertEqual(ek_slots["spell_slots"]["level_2"]["max"], 2)

        # Verify dnd5e_parser.calculate_multiclass_spell_slots agrees
        p_2014 = calculate_multiclass_spell_slots([{"class": "Paladin", "level": 3}, {"class": "Sorcerer", "level": 2}], edition="2014")
        p_2024 = calculate_multiclass_spell_slots([{"class": "Paladin", "level": 3}, {"class": "Sorcerer", "level": 2}], edition="2024")
        self.assertEqual(p_2014["level_2"]["max"], 2)
        self.assertEqual(p_2024["level_2"]["max"], 3)

    def test_sorcerer_warlock_pact_magic_and_short_rest_isolation(self):
        svc = TTRPGService(
            storage_dir=os.path.join(self.tmp_dir, "campaigns"),
            manifest_path=os.path.join(self.tmp_dir, "manifest.json"),
        )
        sorlock = {
            "id": "char_sorlock",
            "name": "Kael",
            "ruleset": "dnd_5e",
            "ruleset_edition": "2024",
            "level": 4,
            "archetype": "Sorcerer 4",
            "vitals": {"health": {"current": 26, "max": 26}},
            "attributes": {"Strength": 8, "Dexterity": 14, "Constitution": 14, "Intelligence": 10, "Wisdom": 10, "Charisma": 16},
            "custom_meters": {"hit_dice": {"label": "Hit Dice (4d6)", "current": 4, "max": 4, "reset_on": "long_rest"}},
            "native_system_data": {
                "class_level": "Sorcerer 4",
                "spellcasting_ability": "CHA",
                "ability_scores": {"STR": 8, "DEX": 14, "CON": 14, "INT": 10, "WIS": 10, "CHA": 16},
            },
        }
        svc.active_state = {
            "campaign_id": "test_sorlock",
            "characters": {"char_sorlock": sorlock},
        }
        svc.manifest = {"active_campaign": "test_sorlock", "campaigns": {"test_sorlock": {"ruleset_edition": "2024"}}}

        # Multiclass into Warlock 1, then Warlock 2
        r1 = svc.apply_dnd_level_up("Kael", {"target_class": "Warlock", "edition": "2024"})
        self.assertEqual(r1["status"], "success")
        r2 = svc.apply_dnd_level_up("Kael", {"target_class": "Warlock", "edition": "2024"})
        self.assertEqual(r2["status"], "success")
        self.assertEqual(sorlock["archetype"], "Sorcerer 4 / Warlock 2")

        # L1 slots must have 4 long_rest Sorcerer slots PLUS 2 short_rest Pact Magic slots!
        lvl1 = sorlock["spell_slots"]["level_1"]
        self.assertEqual(lvl1["max"], 4)
        self.assertEqual(lvl1["reset_on"], "long_rest")
        self.assertEqual(lvl1["pact_max"], 2)
        self.assertIn("pact_magic_slots", sorlock["custom_meters"])
        self.assertEqual(sorlock["custom_meters"]["pact_magic_slots"]["max"], 2)

        # Expend 2 Sorcerer L1 slots AND 2 Pact Magic L1 slots
        lvl1["current"] = 2
        lvl1["pact_current"] = 0
        sorlock["custom_meters"]["pact_magic_slots"]["current"] = 0
        sorlock["native_system_data"]["spell_slots"]["level_1"] = dict(lvl1)

        # Short Rest MUST restore Pact slots ONLY, NOT Sorcerer Long Rest slots
        rest_res = svc.commit_interactive_rest({
            "rest_type": "short",
            "edition": "2024",
            "characters": {"char_sorlock": {"hit_dice_to_spend": 0}},
        })
        self.assertEqual(rest_res["status"], "success")
        after_l1 = sorlock["spell_slots"]["level_1"]
        self.assertEqual(after_l1["current"], 2)
        self.assertEqual(after_l1["pact_current"], 2)
        self.assertEqual(sorlock["custom_meters"]["pact_magic_slots"]["current"], 2)

    def test_wizard_learns_srd_spells_and_save_dc_updates(self):
        wiz = _make_wizard(level=4, int_val=17)  # +3 INT, Prof +2 -> DC 13
        res = apply_level_up(wiz, {
            "edition": "2024",
            "target_class": "Wizard",
            "hp_mode": "fixed",
            "new_spells": ["Fireball", "Counterspell"],
        })
        self.assertEqual(res["status"], "success")
        # Level 5: Prof +3, INT +3 -> Save DC 14, Atk +6
        self.assertEqual(res["proficiency_bonus"], 3)
        self.assertEqual(res["save_dc"], 14)
        names = [s["name"] for s in wiz["spells"]]
        self.assertIn("Fireball", names)
        self.assertIn("Counterspell", names)
        bh = next(s for s in wiz["spells"] if s["name"] == "Burning Hands")
        fb = next(s for s in wiz["spells"] if s["name"] == "Fireball")
        self.assertEqual(bh["atk_bonus"], "DC 14 DEX")
        self.assertEqual(fb["atk_bonus"], "DC 14 DEX")

    def test_vault_dnd_advancement_root_vs_crossover_isolation(self):
        vault = CharacterVaultService(vault_dir=os.path.join(self.tmp_dir, "vault"))
        root_hero = {
            "id": "hero_elara_root",
            "name": "Elara",
            "ruleset": "dnd_5e",
            "ruleset_edition": "2024",
            "native_genre": "heroic_fantasy",
            "is_root": True,
            "level": 3,
            "archetype": "Fighter 3",
            "vitals": {"health": {"current": 28, "max": 28}},
            "attributes": {"Strength": 16, "Dexterity": 12, "Constitution": 14, "Intelligence": 10, "Wisdom": 12, "Charisma": 10},
            "native_system_data": {"class_level": "Fighter 3"},
        }
        branch_hero = {
            "id": "hero_elara_cyber_branch",
            "name": "Elara",
            "ruleset": "dnd_5e",
            "ruleset_edition": "2024",
            "native_genre": "heroic_fantasy",
            "current_genre": "cyberpunk",
            "is_root": False,
            "root_id": "hero_elara_root",
            "level": 3,
            "archetype": "Fighter 3",
            "vitals": {"health": {"current": 28, "max": 28}},
            "attributes": {"Strength": 16, "Dexterity": 12, "Constitution": 14, "Intelligence": 10, "Wisdom": 12, "Charisma": 10},
            "native_system_data": {"class_level": "Fighter 3"},
        }
        vault._save_character(root_hero)
        vault._save_character(branch_hero)

        opts = vault.get_dnd_advancement_options("hero_elara_cyber_branch", edition="2024")
        self.assertEqual(opts["status"], "success")
        self.assertEqual(opts["source"], "vault")

        res = vault.apply_dnd_level_up(
            "hero_elara_cyber_branch",
            {"edition": "2024", "target_class": "Fighter", "ability_increases": {"STR": 2}},
            current_campaign_genre="cyberpunk",
        )
        self.assertEqual(res["status"], "success")
        self.assertFalse(res["is_root_updated"])
        self.assertIn("Crossover Variant", res["target_updated"])

        reloaded_root = vault.get_character("hero_elara_root")
        reloaded_branch = vault.get_character("hero_elara_cyber_branch")
        self.assertEqual(reloaded_root["level"], 3)
        self.assertEqual(reloaded_root["attributes"]["Strength"], 16)
        self.assertEqual(reloaded_branch["level"], 4)
        self.assertEqual(reloaded_branch["attributes"]["Strength"], 18)

    def test_dm_prompt_level_up_wizard_tool_and_engine_allowlist(self):
        svc = TTRPGService(
            storage_dir=os.path.join(self.tmp_dir, "campaigns2"),
            manifest_path=os.path.join(self.tmp_dir, "manifest2.json"),
        )
        svc.active_state = {"campaign_id": "c1", "characters": {"c_f": _make_fighter(name="Garrett", level=3)}}
        svc.manifest = {"active_campaign": "c1", "campaigns": {"c1": {"ruleset_edition": "2024"}}}

        dispatcher = MagicMock()
        ui_cb = MagicMock()
        register_progression_tools(dispatcher, svc, ui_cb)
        tools = {call.kwargs["name"]: call.kwargs["handler"] for call in dispatcher.register_tool.call_args_list}
        self.assertIn("prompt_level_up_wizard", tools)

        tool_res = asyncio.run(tools["prompt_level_up_wizard"]("Garrett", "Cleared the Goblin Citadel!"))
        self.assertEqual(tool_res["status"], "success")
        self.assertEqual(tool_res["preview"]["selected_class"], "Fighter")
        ui_cb.assert_called()
        ev_name, ev_payload = ui_cb.call_args[0]
        self.assertEqual(ev_name, "level_up_prompt_opened")
        self.assertEqual(ev_payload["reason"], "Cleared the Goblin Citadel!")

        # Verify engine.py TTRPG allowlist passes Phase 1/2/3 tools through
        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "core", "engine.py"), "r", encoding="utf-8") as fh:
            engine_src = fh.read()
        for tool_name in [
            "lookup_dnd_compendium",
            "open_merchant_shop",
            "merchant_transaction",
            "prompt_party_rest",
            "prompt_level_up_wizard",
        ]:
            self.assertIn(f'"{tool_name}"', engine_src)

    def test_warlock_mystic_arcanum_and_manage_progression_delegation(self):
        svc = TTRPGService(
            storage_dir=os.path.join(self.tmp_dir, "campaigns3"),
            manifest_path=os.path.join(self.tmp_dir, "manifest3.json"),
        )
        warlock = {
            "id": "char_warlock",
            "name": "Malakor",
            "ruleset": "dnd_5e",
            "ruleset_edition": "2024",
            "level": 10,
            "experience_points": 85000,
            "archetype": "Warlock 10",
            "vitals": {"health": {"current": 65, "max": 65}},
            "attributes": {"Strength": 8, "Dexterity": 14, "Constitution": 14, "Intelligence": 12, "Wisdom": 10, "Charisma": 18},
            "native_system_data": {"class_level": "Warlock 10", "spellcasting_ability": "CHA"},
        }
        svc.active_state = {"campaign_id": "c_w", "characters": {"char_warlock": warlock}}
        svc.manifest = {"active_campaign": "c_w", "campaigns": {"c_w": {"ruleset_edition": "2024"}}}

        prog = svc.manage_character_progression("Malakor", "level_up", 11)
        self.assertEqual(prog["status"], "success")
        self.assertEqual(warlock["level"], 11)
        self.assertIn("mystic_arcanum_6th", warlock["custom_meters"])
        self.assertEqual(warlock["custom_meters"]["mystic_arcanum_6th"]["max"], 1)

    def test_gui_bridge_dnd_and_nondnd_advancement_routing(self):
        from core.gui_bridge import GuiBridge

        bridge = GuiBridge.__new__(GuiBridge)
        svc = TTRPGService(
            storage_dir=os.path.join(self.tmp_dir, "campaigns4"),
            manifest_path=os.path.join(self.tmp_dir, "manifest4.json"),
        )
        vault = CharacterVaultService(vault_dir=os.path.join(self.tmp_dir, "vault4"))
        coc_hero = {
            "id": "coc_arthur",
            "name": "Arthur Pendelton",
            "ruleset": "coc_7e",
            "ruleset_label": "Call of Cthulhu 7E",
            "level": 1,
            "archetype": "Antiquarian",
            "vitals": {"health": {"current": 12, "max": 12}, "SAN": {"current": 65, "max": 65}},
            "attributes": {"STR": 50, "INT": 75, "EDU": 80},
        }
        dnd_vault = {
            "id": "vault_fighter",
            "name": "Garrett",
            "ruleset": "dnd_5e",
            "ruleset_edition": "2024",
            "native_genre": "heroic_fantasy",
            "is_root": True,
            "level": 3,
            "archetype": "Fighter 3",
            "vitals": {"health": {"current": 28, "max": 28}},
            "attributes": {"Strength": 16, "Dexterity": 12, "Constitution": 14, "Intelligence": 10, "Wisdom": 12, "Charisma": 10},
            "native_system_data": {"class_level": "Fighter 3"},
        }
        vault._save_character(dnd_vault)
        svc.active_state = {
            "campaign_id": "c_br",
            "characters": {"coc_arthur": coc_hero, "vault_fighter": dict(dnd_vault)},
        }
        svc.manifest = {"active_campaign": "c_br", "campaigns": {"c_br": {"ruleset_edition": "2024", "genre": "heroic_fantasy"}}}

        mock_eng = MagicMock()
        mock_eng.vault_service = vault
        mock_eng._get_current_campaign_genre.return_value = "heroic_fantasy"
        bridge._engine = mock_eng
        bridge.engine = mock_eng
        bridge.ttrpg_service = svc
        bridge._on_engine_event = MagicMock()

        coc_opts = bridge.get_character_advancement_options("Arthur Pendelton", source="campaign")
        self.assertEqual(coc_opts["status"], "success")
        self.assertFalse(coc_opts["is_dnd"])

        vault_res = bridge.apply_character_dnd_level_up(
            "vault_fighter",
            {"edition": "2024", "target_class": "Fighter", "ability_increases": {"STR": 2}},
            source="vault",
        )
        self.assertEqual(vault_res["status"], "success")
        self.assertEqual(svc.active_state["characters"]["vault_fighter"]["level"], 4)
        self.assertEqual(svc.active_state["characters"]["vault_fighter"]["attributes"]["Strength"], 18)
        self.assertEqual(svc.active_state["characters"]["vault_fighter"]["vitals"]["health"]["max"], 36)

    def test_spell_selection_constraints_and_level_20_caps(self):
        """Validates that Wizard spell learning is constrained to 2 spells per level, no cantrips at Lv 2, and Lv 20 hard cap."""
        from core.compendiums.dnd_advancement import compute_spell_selection_limits, apply_level_up, get_advancement_options

        # 1. Wizard Level 1 -> 2: 0 cantrips, 2 leveled spells
        wiz_l2 = compute_spell_selection_limits("Wizard", old_cls_lvl=1, next_cls_lvl=2, edition="2024")
        self.assertEqual(wiz_l2["max_new_cantrips"], 0)
        self.assertEqual(wiz_l2["max_new_leveled_spells"], 2)
        self.assertEqual(wiz_l2["max_total_new_spells"], 2)

        # 2. Wizard Level 0 -> 1 (Creation / multiclass entry): 3 cantrips, 6 leveled spells
        wiz_l1 = compute_spell_selection_limits("Wizard", old_cls_lvl=0, next_cls_lvl=1, edition="2024")
        self.assertEqual(wiz_l1["max_new_cantrips"], 3)
        self.assertEqual(wiz_l1["max_new_leveled_spells"], 6)

        # 3. Wizard Level 3 -> 4: 1 cantrip unlocked, 2 leveled spells
        wiz_l4 = compute_spell_selection_limits("Wizard", old_cls_lvl=3, next_cls_lvl=4, edition="2024")
        self.assertEqual(wiz_l4["max_new_cantrips"], 1)
        self.assertEqual(wiz_l4["max_new_leveled_spells"], 2)

        # 4. Cleric Level 1 -> 2: 0 cantrips, 0 leveled spells (prepares full list)
        cleric_l2 = compute_spell_selection_limits("Cleric", old_cls_lvl=1, next_cls_lvl=2, edition="2024")
        self.assertEqual(cleric_l2["max_new_cantrips"], 0)
        self.assertEqual(cleric_l2["max_new_leveled_spells"], 0)
        self.assertEqual(cleric_l2["preparation_type"], "prepared_full_list")

        # 5. Fighter (Eldritch Knight) Level 2 -> 3: 2 cantrips, 3 leveled spells
        ek_l3 = compute_spell_selection_limits("Fighter", old_cls_lvl=2, next_cls_lvl=3, edition="2024", subclass="Eldritch Knight")
        self.assertEqual(ek_l3["max_new_cantrips"], 2)
        self.assertEqual(ek_l3["max_new_leveled_spells"], 3)

        # 6. Apply Level Up for Wizard Gale (Level 1 -> 2):
        gale = {
            "id": "char_gale",
            "name": "Gale",
            "level": 1,
            "ruleset": "dnd_5e",
            "class_and_level": "Wizard 1",
            "attributes": {"Intelligence": 17, "Constitution": 14, "Dexterity": 14, "Strength": 10, "Wisdom": 12, "Charisma": 10},
            "vitals": {"health": {"current": 8, "max": 8}},
            "spells": [{"name": "Fire Bolt", "level": "Cantrip", "is_cantrip": True}],
        }

        # Check options preview returns constraints
        opts = get_advancement_options(gale, edition="2024")
        self.assertEqual(opts["max_new_cantrips"], 0)
        self.assertEqual(opts["max_new_leveled_spells"], 2)

        # Attempt to submit 3 leveled spells (exceeding limit of 2) -> must fail!
        res_fail_3 = apply_level_up(gale, {
            "edition": "2024",
            "target_class": "Wizard",
            "new_spells": ["Magic Missile", "Shield", "Mage Armor"],
        })
        self.assertEqual(res_fail_3["status"], "error")
        self.assertIn("Too many leveled spells selected", res_fail_3["message"])

        # Attempt to submit a cantrip (exceeding limit of 0) -> must fail!
        res_fail_cantrip = apply_level_up(gale, {
            "edition": "2024",
            "target_class": "Wizard",
            "new_spells": ["Acid Splash", "Magic Missile"],
        })
        self.assertEqual(res_fail_cantrip["status"], "error")
        self.assertIn("Too many cantrips selected", res_fail_cantrip["message"])

        # Valid submission of exactly 2 leveled spells -> must succeed!
        res_ok = apply_level_up(gale, {
            "edition": "2024",
            "target_class": "Wizard",
            "new_spells": ["Magic Missile", "Shield"],
        })
        self.assertEqual(res_ok["status"], "success")
        self.assertEqual(gale["level"], 2)
        learned_names = [s.get("name") for s in gale["spells"]]
        self.assertIn("Magic Missile", learned_names)
        self.assertIn("Shield", learned_names)

        # 7. Level 20 character hard cap: cannot advance to 21
        l20_char = {
            "id": "char_shadow",
            "name": "Shadow",
            "level": 20,
            "ruleset": "dnd_5e",
            "class_and_level": "Fighter 20",
            "attributes": {"Strength": 20, "Constitution": 18, "Dexterity": 16, "Intelligence": 12, "Wisdom": 14, "Charisma": 12},
            "vitals": {"health": {"current": 194, "max": 194}},
        }
        res_l20 = apply_level_up(l20_char, {"edition": "2024", "target_class": "Fighter"})
        self.assertEqual(res_l20["status"], "error")
        self.assertIn("already Level 20", res_l20["message"])


if __name__ == "__main__":
    unittest.main()
