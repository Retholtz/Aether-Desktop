import asyncio
import os
import random
import shutil
import tempfile
import unittest

from unittest.mock import patch

from core.campaign.campaign_manager import CampaignManager
from core.campaign.schema import QuestObjective, QuestRecord, QuestStatus
from core.compendiums.dnd_loot import (
    create_tier_bounded_homebrew,
    generate_random_loot,
    get_character_proficiencies,
    is_armor_proficient,
    is_weapon_proficient,
    pick_reward_class_for_character,
    select_class_loot_for_character,
)
from core.compendiums.homebrew_manager import HomebrewManager
from core.ttrpg_service import TTRPGService
from tools.ttrpg_tools import register_ttrpg_tools


class DummyDispatcher:
    def __init__(self):
        self.tools = {}

    def register_tool(self, name, description, parameters, handler):
        self.tools[name] = handler


class TestLootProficiencyAndQuests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.manifest_path = os.path.join(self.test_dir, "campaigns.json")
        self.data_dir = os.path.join(self.test_dir, "campaigns")
        self.hb_path = os.path.join(self.test_dir, "homebrew.json")
        self.service = TTRPGService(data_dir=self.data_dir, manifest_path=self.manifest_path)
        self.service.create_campaign(
            title="Loot & Proficiency Test",
            system_preset="dnd_5e",
            gm_mode="interactive_narrator",
            setting_overview="Sword Coast",
            tone="Heroic",
            campaign_id="loot_camp",
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_5e_raw_non_proficient_weapon_omits_prof_bonus_without_disadvantage(self):
        # Pure Lv 20 Rogue lacks Martial Greataxe proficiency (+6 prof omitted -> only STR +1)
        self.service.add_character({
            "id": "rogue_1",
            "name": "Vex",
            "archetype": "Rogue 20",
            "level": 20,
            "attributes": {"Strength": 12, "Dexterity": 20},
        })
        res = self.service.manage_character_inventory(
            character_name="Vex",
            action="add",
            weapons=[{"name": "Greataxe"}],
            reason="Found in cave chest",
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(len(res["proficiency_warnings"]), 1)

        wpn = next(w for w in res["weapons"] if w["name"] == "Greataxe")
        self.assertFalse(wpn["proficient"])
        self.assertIn("NOT PROFICIENT", wpn["notes"])
        # STR 12 (+1), Level 20 prof (+6) MUST be omitted -> '+1'
        self.assertEqual(wpn["atk_bonus"], "+1")

        # Verify roll_dice uses normal (NOT disadvantage) for non-proficient weapon attack per 5e RAW
        dispatcher = DummyDispatcher()
        register_ttrpg_tools(dispatcher, self.service)
        roll_res = asyncio.run(dispatcher.tools["roll_dice"](
            formula="1d20+1",
            character_name="Vex",
            purpose="Attack with Greataxe",
        ))
        self.assertEqual(roll_res["status"], "success")
        self.assertEqual(roll_res["roll_type"], "normal")
        self.assertTrue(any("omit Proficiency Bonus" in x for x in roll_res.get("condition_adjustments", [])))

    def test_5e_raw_non_proficient_armor_imposes_str_dex_disadvantage_and_blocks_spells(self):
        self.service.add_character({
            "id": "wiz_1",
            "name": "Gale",
            "archetype": "Wizard 5",
            "level": 5,
            "attributes": {"Strength": 10, "Dexterity": 14, "Intelligence": 18},
            "spells": [{"name": "Fire Bolt", "level": 0, "is_cantrip": True}],
            "spell_slots": {"level_1": {"current": 4, "max": 4}},
        })
        add_res = self.service.manage_character_inventory(
            character_name="Gale",
            action="add",
            armor=["Plate"],
            equip_armor=True,
            reason="Put on heavy guard plate",
        )
        self.assertEqual(add_res["status"], "success")
        self.assertTrue(add_res["proficiency_warnings"])

        # 1. Spellcasting MUST be blocked while wearing non-proficient Plate armor
        spell_res = self.service.cast_character_spell("Gale", "Fire Bolt", spell_level=0)
        self.assertEqual(spell_res["status"], "error")
        self.assertEqual(spell_res["code"], "non_proficient_armor_spell_block")

        # 2. STR/DEX rolls MUST suffer Disadvantage, while INT checks stay Normal
        dispatcher = DummyDispatcher()
        register_ttrpg_tools(dispatcher, self.service)
        dex_roll = asyncio.run(dispatcher.tools["roll_dice"](
            formula="1d20+2",
            character_name="Gale",
            purpose="Dexterity (Stealth) check",
        ))
        self.assertEqual(dex_roll["roll_type"], "disadvantage")
        self.assertTrue(any("Non-Proficient Armor" in x for x in dex_roll.get("condition_adjustments", [])))

        int_roll = asyncio.run(dispatcher.tools["roll_dice"](
            formula="1d20+7",
            character_name="Gale",
            purpose="Intelligence (Arcana) check",
        ))
        self.assertEqual(int_roll["roll_type"], "normal")

        # 3. Removing/unequipping armor restores spellcasting
        gale = self.service.active_state["characters"]["wiz_1"]
        self.service._recompute_and_apply_character_ac(gale, None, False)
        ok_spell = self.service.cast_character_spell("Gale", "Fire Bolt", spell_level=0)
        self.assertEqual(ok_spell["status"], "success")

    def test_class_loot_prioritizes_highest_class_and_randomizes_ties_legally(self):
        # Uneven multiclass: Fighter 12 / Wizard 3 -> always Fighter
        fighter_wiz = {
            "name": "Kael",
            "archetype": "Fighter 12 / Wizard 3",
            "level": 15,
            "attributes": {"Strength": 18, "Dexterity": 12, "Intelligence": 14},
        }
        for seed in range(5):
            self.assertEqual(pick_reward_class_for_character(fighter_wiz, rng=random.Random(seed)), "fighter")

        # Even multiclass tie: Barbarian 5 / Bard 5 / Druid 5 / Warlock 5 -> randomizes across all 4 tied classes
        joe = {
            "name": "Joe",
            "archetype": "Barbarian 5 / Bard 5 / Druid 5 / Warlock 5",
            "level": 20,
            "proficiencies_weapons": "Martial Weapons, Simple Weapons",
            "proficiencies_armor": "Heavy Armor, Light Armor, Medium Armor, Shields",
            "attributes": {"Strength": 18, "Dexterity": 15, "Charisma": 18},
        }
        seen_classes = {pick_reward_class_for_character(joe, rng=random.Random(s)) for s in range(25)}
        self.assertGreater(len(seen_classes), 1)
        self.assertTrue(seen_classes.issubset({"barbarian", "bard", "druid", "warlock"}))

        # Barbarian 20 weapon quest loot must be Tier 4 proficient & never a Dagger
        barb = {"name": "Conan", "archetype": "Barbarian 20", "level": 20, "attributes": {"Strength": 20, "Dexterity": 14}}
        for s in range(10):
            picked = select_class_loot_for_character(barb, item_type="weapon", rng=random.Random(s))
            self.assertIsNotNone(picked)
            ok_prof, _ = is_weapon_proficient(barb, picked["raw_record"])
            self.assertTrue(ok_prof)
            self.assertNotIn("dagger", picked["raw_record"]["name"].lower())

        # Rogue 10 weapon reward must be proficient for Rogue (never Greataxe/Maul)
        rogue = {"name": "Lyra", "archetype": "Rogue 10", "level": 10, "attributes": {"Strength": 10, "Dexterity": 18}}
        for s in range(10):
            picked = select_class_loot_for_character(rogue, item_type="weapon", rng=random.Random(s))
            self.assertIsNotNone(picked)
            ok_prof, _ = is_weapon_proficient(rogue, picked["raw_record"])
            self.assertTrue(ok_prof)
            self.assertNotIn("greataxe", picked["raw_record"]["name"].lower())

    def test_random_chest_loot_is_unbiased_and_scaled_by_level(self):
        low_chest = generate_random_loot(party_level=2, hoard_size="standard", rng=random.Random(42))
        high_chest = generate_random_loot(party_level=19, hoard_size="boss", rng=random.Random(42))
        self.assertEqual(low_chest["tier"], 1)
        self.assertEqual(high_chest["tier"], 4)
        self.assertLess(low_chest["gold_total"], high_chest["gold_total"])

    def test_creative_homebrew_enforces_tier_limits_and_proficiency(self):
        hb_mgr = HomebrewManager(store_path=self.hb_path)
        # Level 3 Barbarian: requested +5 magic bonus & 4d12 extra fire MUST be clamped to Tier 1 (+1 max, 1d4 max)
        low_barb = {"name": "Grog", "archetype": "Barbarian 3", "level": 3, "attributes": {"Strength": 16}}
        res = create_tier_bounded_homebrew(
            char_data=low_barb,
            spec={
                "name": "Ember Cleaver",
                "category": "weapons",
                "base_item": "Greataxe",
                "magic_bonus": 5,
                "extra_damage": "4d12 fire",
                "description": "Forged from a volcanic cinder.",
            },
            homebrew_mgr=hb_mgr,
        )
        self.assertEqual(res["status"], "success")
        entry = res["entry"]
        self.assertEqual(entry["atk_bonus"], "+1")
        self.assertIn("+1d4 fire", entry["damage"])
        self.assertEqual(entry["rarity"], "Uncommon")

    def test_complete_quest_awards_xp_gold_and_items_and_syncs_campaign_manager(self):
        cm = CampaignManager(campaign_id="loot_camp", storage_root=self.data_dir)
        cm.add_quest(QuestRecord(
            id="q_goblin_keep",
            title="Clear Cragmaw Keep",
            status=QuestStatus.ACTIVE,
            objectives=[QuestObjective(id="obj_1", description="Defeat King Grol", is_mandatory=True)],
            rewards={"xp_per_player": 500, "gold_per_player": 500, "award_class_gear": True},
        ))
        self.service.load_active_campaign_state()
        self.service.add_character({
            "id": "c_sam",
            "name": "Sam",
            "archetype": "Warlock 4",
            "level": 4,
            "experience_points": 6200,  # 500 XP puts Sam at 6700 >= 6500 (Level 5 threshold!)
            "attributes": {"Strength": 10, "Dexterity": 14, "Charisma": 18},
        })
        self.service.add_character({
            "id": "c_shadow",
            "name": "Shadow",
            "archetype": "Paladin 4",
            "level": 4,
            "experience_points": 2700,
            "attributes": {"Strength": 18, "Dexterity": 12, "Charisma": 16},
        })

        res = self.service.complete_quest(quest_title_or_id="Clear Cragmaw Keep")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["xp_per_player"], 500)
        self.assertEqual(res["gold_per_player"], 500.0)
        self.assertIn("Sam", res["level_up_ready_characters"])

        sam = self.service.active_state["characters"]["c_sam"]
        shadow = self.service.active_state["characters"]["c_shadow"]
        self.assertEqual(sam["experience_points"], 6700)
        self.assertEqual(shadow["experience_points"], 3200)
        self.assertEqual(self.service._normalize_wallet(sam)["gp"], 500)
        self.assertEqual(self.service._normalize_wallet(shadow)["gp"], 500)
        self.assertIn("Clear Cragmaw Keep", self.service.active_state["completed_quests"])

        # Prompt surfaces class-inferred proficiencies even when explicit proficiency fields were omitted
        prompt = self.service.build_gm_context_prompt()
        self.assertIn("Proficiencies:", prompt)
        self.assertIn("grant_loot", prompt)
        self.assertIn("complete_quest", prompt)

    def test_grant_loot_and_complete_quest_tools_and_engine_filter(self):
        from core.engine import AetherEngine

        self.service.add_character({
            "id": "c_joe",
            "name": "Joe",
            "archetype": "Barbarian 10",
            "level": 10,
            "attributes": {"Strength": 18, "Dexterity": 14},
        })
        dispatcher = DummyDispatcher()
        events = []
        register_ttrpg_tools(dispatcher, self.service, notify_ui_callback=lambda ev, d: events.append(ev))
        self.assertIn("grant_loot", dispatcher.tools)
        self.assertIn("complete_quest", dispatcher.tools)

        temp_hb = HomebrewManager(store_path=self.hb_path)
        with patch("core.compendiums.dnd_loot.get_default_homebrew_manager", return_value=temp_hb), \
             patch("core.compendiums.dnd_weapons.get_default_homebrew_manager", return_value=temp_hb):
            loot_res = asyncio.run(dispatcher.tools["grant_loot"](
                mode="class_reward",
                character_name="Joe",
                gold=250,
                creative_homebrew=[{
                    "character_name": "Joe",
                    "name": "Stormcleaver Greataxe",
                    "category": "weapons",
                    "base_item": "Greataxe",
                    "magic_bonus": 3,  # Tier 2 (Lv 10) must clamp to +1!
                    "extra_damage": "2d10 lightning",  # Tier 2 must clamp to 1d6!
                    "description": "Hums with thunder.",
                }],
                reason="Defeated Thunder Giant",
            ))
            self.assertEqual(loot_res["status"], "success")
            joe = self.service.active_state["characters"]["c_joe"]
            axe = next(w for w in joe["weapons"] if w["name"] == "Stormcleaver Greataxe")
            self.assertIn("+1d6 lightning", axe["damage"])
            # STR +4 + Lv10 Prof +4 + Tier2 magic +1 = +9
            self.assertEqual(axe["atk_bonus"], "+9")
            self.assertEqual(self.service._normalize_wallet(joe)["gp"], 250)
            self.assertIn("roster_updated", events)

        # Verify AetherEngine exposes grant_loot & complete_quest during campaign sessions
        eng = AetherEngine()
        eng.active_workspace_type = "campaign"
        decls = [{"name": "grant_loot"}, {"name": "complete_quest"}, {"name": "run_shell_command"}]
        filtered_names = [d["name"] for d in eng._filter_tools_for_workspace(decls)]
        self.assertEqual(filtered_names, ["grant_loot", "complete_quest"])


if __name__ == "__main__":
    unittest.main()
