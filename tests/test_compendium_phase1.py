"""
tests/test_compendium_phase1.py - Verification for Phase 1 D&D 5e (2014 & 2024) SRD Compendiums,
Homebrew CRUD, TTRPGService sheet integration, Spell Registry enrichment, and DM compendium tool.
"""
import os
import tempfile
import shutil
import unittest
from unittest.mock import MagicMock

from core.compendiums import (
    search_compendium,
    get_compendium_stats,
    list_weapons,
    get_weapon,
    list_weapon_masteries,
    format_weapon_for_sheet,
    list_armor,
    get_armor,
    compute_armor_class,
    list_items,
    get_item,
    expand_pack_contents,
    cost_to_cp,
    currency_dict_to_cp,
    deduct_currency,
    add_currency,
    list_spells,
    get_spell,
    get_spells_for_class,
    format_spell_for_sheet,
    HomebrewManager,
)
from core.parsers.spell_registry import DND_SPELL_REGISTRY, is_spell_name
from core.ttrpg_service import TTRPGService
from tools.ttrpg_tools import register_ttrpg_tools


class TestCompendiumPhase1(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="aether_comp_p1_")
        self.hb_path = os.path.join(self.tmp_dir, "homebrew.json")
        self.hb_mgr = HomebrewManager(store_path=self.hb_path)
        self.service = TTRPGService(
            storage_dir=os.path.join(self.tmp_dir, "campaigns"),
            manifest_path=os.path.join(self.tmp_dir, "manifest.json"),
        )
        self.service.active_state = {
            "campaign_id": "test_p1",
            "characters": {
                "char_elara": {
                    "id": "char_elara",
                    "name": "Elara",
                    "archetype": "Wizard 5",
                    "level": 5,
                    "ruleset": "dnd_5e",
                    "prof_bonus": "+3",
                    "spellcasting_ability": "INT",
                    "spell_save_dc": 15,
                    "spell_attack_bonus": "+7",
                    "armor_class": 12,
                    "attributes": {
                        "Strength": 10,
                        "Dexterity": 16,
                        "Constitution": 14,
                        "Intelligence": 18,
                        "Wisdom": 12,
                        "Charisma": 10,
                    },
                    "vitals": {
                        "health": {"current": 32, "max": 32, "label": "Health"},
                        "guard": {"current": 12, "max": 12, "label": "Armor Defense"},
                    },
                    "weapons": [],
                    "inventory": [],
                    "spells": [],
                    "native_system_data": {
                        "ability_scores": {"STR": 10, "DEX": 16, "CON": 14, "INT": 18, "WIS": 12, "CHA": 10},
                        "armor_class": 12,
                        "spells": [
                            {"name": "Mage Armor", "level": "1st Level", "prepared": True, "is_cantrip": False}
                        ],
                        "inventory": [],
                    },
                }
            },
        }

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_srd_counts_and_masteries(self):
        stats = get_compendium_stats(homebrew_mgr=self.hb_mgr)
        self.assertGreaterEqual(stats["weapons"], 70)
        self.assertGreaterEqual(stats["armor"], 40)
        self.assertGreaterEqual(stats["items"], 400)
        self.assertGreaterEqual(stats["spells"], 300)

        masteries = list_weapon_masteries()
        mastery_names = {m["name"] for m in masteries}
        for req in ("Cleave", "Graze", "Nick", "Push", "Sap", "Slow", "Topple", "Vex"):
            self.assertIn(req, mastery_names)

        ls = get_weapon("longsword", homebrew_mgr=self.hb_mgr)
        self.assertIsNotNone(ls)
        self.assertEqual(ls.get("mastery"), "Sap")
        formatted = format_weapon_for_sheet(ls, str_mod=3, dex_mod=1, prof_bonus=3, edition="2024")
        self.assertEqual(formatted["atk_bonus"], "+6")
        self.assertIn("Mastery: Sap", formatted["notes"])

    def test_spells_phb_aliases_artificer_and_ordinals(self):
        tasha = get_spell("Tasha's Hideous Laughter", edition="2024", homebrew_mgr=self.hb_mgr)
        self.assertIsNotNone(tasha)
        self.assertIn("laughter", tasha["name"].lower())

        melf = get_spell("Melf's Acid Arrow", edition="2024", homebrew_mgr=self.hb_mgr)
        self.assertIsNotNone(melf)
        self.assertIn("acid arrow", melf["name"].lower())

        hut = get_spell("Leomund's Tiny Hut", edition="2014", homebrew_mgr=self.hb_mgr)
        self.assertIsNotNone(hut)
        self.assertIn("tiny hut", hut["name"].lower())

        bigby = get_spell("Bigby's Hand", edition="2024", homebrew_mgr=self.hb_mgr)
        self.assertIsNotNone(bigby)
        self.assertIn("arcane hand", bigby["name"].lower())

        artificer_lvl1 = get_spells_for_class("Artificer", max_spell_level=1, homebrew_mgr=self.hb_mgr)
        self.assertGreater(len(artificer_lvl1), 5)

        sheet_1 = format_spell_for_sheet({"name": "Magic Missile", "level": 1}, spell_atk="+7", spell_dc="15")
        sheet_2 = format_spell_for_sheet({"name": "Misty Step", "level": 2})
        sheet_3 = format_spell_for_sheet({"name": "Fireball", "level": 3})
        self.assertEqual(sheet_1["level"], "1st Level")
        self.assertEqual(sheet_2["level"], "2nd Level")
        self.assertEqual(sheet_3["level"], "3rd Level")

        # Enriched parser registry checks
        self.assertIn("leomund's tiny hut", DND_SPELL_REGISTRY)
        self.assertTrue(is_spell_name("Bigby's Hand"))
        self.assertTrue(is_spell_name("Tiny Hut [R]"))

    def test_pack_expansion_armor_ac_and_currency(self):
        expanded = expand_pack_contents("Burglar's Pack", homebrew_mgr=self.hb_mgr)
        self.assertGreater(len(expanded), 5)
        item_names = [x["name"] for x in expanded]
        self.assertTrue(any("Backpack" in n for n in item_names))

        # Light armor (+3 DEX)
        light = compute_armor_class("Leather Armor", has_shield=False, dex_mod=3, homebrew_mgr=self.hb_mgr)
        self.assertEqual(light["armor_class"], 14)

        # Medium armor caps DEX at +2 (+ shield = 14 + 2 + 2 = 18)
        med = compute_armor_class("Scale Mail", has_shield=True, dex_mod=4, homebrew_mgr=self.hb_mgr)
        self.assertEqual(med["armor_class"], 18)
        self.assertTrue(med["stealth_disadvantage"])

        # Heavy armor with low STR triggers warning
        heavy = compute_armor_class("Plate Armor", has_shield=False, dex_mod=3, str_score=11, homebrew_mgr=self.hb_mgr)
        self.assertEqual(heavy["armor_class"], 18)
        self.assertTrue(any("STR 15" in w for w in heavy["warnings"]))

        # Multi-denomination currency math
        self.assertEqual(cost_to_cp({"quantity": 15, "unit": "gp"}), 1500)
        wallet = {"cp": 5, "sp": 2, "ep": 0, "gp": 10, "pp": 0}
        self.assertEqual(currency_dict_to_cp(wallet), 1025)

        ok, rem_wallet, _ = deduct_currency(wallet, 250)  # 2 gp 5 sp
        self.assertTrue(ok)
        self.assertEqual(currency_dict_to_cp(rem_wallet), 775)

        ok_fail, _, err_msg = deduct_currency(rem_wallet, 99999)
        self.assertFalse(ok_fail)
        self.assertIn("Insufficient funds", err_msg)

        added = add_currency(rem_wallet, 125)
        self.assertEqual(currency_dict_to_cp(added), 900)

    def test_homebrew_crud_across_categories(self):
        w_res = self.hb_mgr.save_entry("weapons", {
            "name": "Sunfire Claymore",
            "damage_dice": "2d6",
            "damage_type": "Radiant",
            "atk_bonus": "+1",
            "mastery": "Cleave",
            "cost": {"quantity": 500, "unit": "gp"},
            "is_magic": True,
            "rarity": "Rare",
        })
        self.assertEqual(w_res["status"], "success")

        a_res = self.hb_mgr.save_entry("armor", {
            "name": "Starweave Coat",
            "category": "Light Armor",
            "ac_base": 13,
            "dex_bonus": True,
            "cost_gp": 350,
        })
        self.assertEqual(a_res["status"], "success")

        i_res = self.hb_mgr.save_entry("items", {
            "name": "Elixir of Starlight",
            "subcategory": "Potion",
            "consumable": True,
            "cost": {"quantity": 75, "unit": "gp"},
        })
        self.assertEqual(i_res["status"], "success")

        s_res = self.hb_mgr.save_entry("spells", {
            "name": "Arcane Pulse",
            "level": 2,
            "school": "Evocation",
            "classes": ["Wizard", "Sorcerer"],
            "damage": "4d6 Force",
            "dc_type": "CON",
        })
        self.assertEqual(s_res["status"], "success")

        self.assertEqual(len(self.hb_mgr.list_entries("all")), 4)
        sp = get_spell("Arcane Pulse", homebrew_mgr=self.hb_mgr)
        self.assertIsNotNone(sp)
        self.assertEqual(sp["level_label"], "2nd Level")

        # Update existing & delete
        del_res = self.hb_mgr.delete_entry("weapons", w_res["entry"]["index"])
        self.assertEqual(del_res["status"], "success")
        self.assertEqual(len(self.hb_mgr.list_entries("weapons")), 0)

    async def test_service_grant_and_compendium_tool(self):
        # 1. Grant weapon
        res_w = self.service.add_compendium_entry_to_character("Elara", "weapons", "dagger", edition="2024")
        self.assertEqual(res_w["status"], "success")
        elara = self.service.active_state["characters"]["char_elara"]
        self.assertEqual(elara["weapons"][0]["name"], "Dagger")
        self.assertEqual(elara["weapons"][0]["atk_bonus"], "+6")

        # 2. Grant & equip armor (Studded Leather = 12 + DEX 3 = 15)
        res_a = self.service.add_compendium_entry_to_character("Elara", "armor", "studded-leather-armor", equip=True)
        self.assertEqual(res_a["status"], "success")
        self.assertEqual(elara["armor_class"], 15)
        self.assertEqual(elara["vitals"]["guard"]["current"], 15)
        self.assertEqual(elara["native_system_data"]["armor_class"], 15)

        # 3. Grant & unpack Explorer's Pack
        res_i = self.service.add_compendium_entry_to_character("Elara", "items", "explorers-pack", expand_pack=True)
        self.assertEqual(res_i["status"], "success")
        self.assertTrue(res_i["expanded_pack"])
        self.assertGreater(len(elara["inventory"]), 3)
        self.assertEqual(len(elara["native_system_data"]["inventory"]), len(elara["inventory"]))

        # 4. Grant spell and verify both root & native_system_data sync
        res_s = self.service.add_compendium_entry_to_character("Elara", "spells", "fireball", edition="2024")
        self.assertEqual(res_s["status"], "success")
        self.assertTrue(any(s["name"] == "Fireball" for s in elara["spells"]))
        self.assertTrue(any(s["name"] == "Fireball" for s in elara["native_system_data"]["spells"]))

        # 5. Test lookup_dnd_compendium tool invocation
        dispatcher = MagicMock()
        register_ttrpg_tools(dispatcher, self.service, None)
        tools = {call.kwargs["name"]: call.kwargs["handler"] for call in dispatcher.register_tool.call_args_list}
        self.assertIn("lookup_dnd_compendium", tools)

        comp_fn = tools["lookup_dnd_compendium"]
        q1 = await comp_fn(query_or_name="Fireball", category="spells", edition="2024")
        self.assertEqual(q1["status"], "success")
        self.assertGreater(q1["count"], 0)
        self.assertEqual(q1["results"][0]["name"], "Fireball")

        q2 = await comp_fn(query_or_name="Nick", category="masteries")
        self.assertEqual(q2["status"], "success")
        self.assertEqual(q2["results"][0]["name"], "Nick")

        q3 = await comp_fn(query_or_name="Burglar's Pack", category="items", expand_pack=True)
        self.assertEqual(q3["status"], "success")
        self.assertTrue(len(q3["results"][0].get("pack_contents", [])) > 5)


if __name__ == "__main__":
    unittest.main()
