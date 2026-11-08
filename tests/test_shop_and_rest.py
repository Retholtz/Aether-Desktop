"""
tests/test_shop_and_rest.py - Comprehensive verification for Phase 2:
- Location-Based Merchant Shop (markups/discounts, multi-coin change, packs, buy/sell, AC & attunement sync)
- Interactive Short / Long Rest (digital & physical Hit Dice, consumables, armor/shield swap, max-3 attunement cap,
  2014 vs 2024 spell preparation & Full vs Half Hit Dice recovery)
- Aether DM tools (`open_merchant_shop`, `merchant_transaction`, `prompt_party_rest`)
"""
import os
import tempfile
import shutil
import unittest
from unittest.mock import MagicMock

from core.ttrpg_service import TTRPGService
from core.compendiums.dnd_items import currency_dict_to_cp
from tools.ttrpg_tools import register_ttrpg_tools


class TestShopAndRestPhase2(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="aether_p2_test_")
        self.service = TTRPGService(
            storage_dir=os.path.join(self.tmp_dir, "campaigns"),
            manifest_path=os.path.join(self.tmp_dir, "manifest.json"),
        )
        self.service.manifest["active_campaign"] = "test_p2"
        self.service.manifest["campaigns"]["test_p2"] = {
            "id": "test_p2",
            "title": "Test Phase 2",
            "system_preset": "dnd_5e",
            "ruleset_edition": "2024",
            "dice_mode": "digital",
        }
        self.service.save_manifest()

        self.service.active_state = {
            "campaign_id": "test_p2",
            "ruleset_edition": "2024",
            "current_location": "Phandalin Square",
            "characters": {
                "char_shadow": {
                    "id": "char_shadow",
                    "name": "Shadow",
                    "archetype": "Cleric 3",
                    "class_and_level": "Cleric 3",
                    "level": 3,
                    "ruleset": "dnd_5e",
                    "prof_bonus": "+2",
                    "spellcasting_ability": "WIS",
                    "spell_save_dc": 13,
                    "spell_attack_bonus": "+5",
                    "armor_class": 16,
                    "armor_equipped": "Chain Mail",
                    "shield_equipped": False,
                    "attributes": {
                        "Strength": 15,
                        "Dexterity": 14,
                        "Constitution": 14,
                        "Intelligence": 10,
                        "Wisdom": 16,
                        "Charisma": 12,
                    },
                    "vitals": {
                        "health": {"current": 12, "max": 24, "label": "Health"},
                        "guard": {"current": 16, "max": 16, "label": "Armor Defense"},
                    },
                    "custom_meters": {
                        "hit_dice": {"label": "Hit Dice (3d8)", "current": 2, "max": 3, "reset_on": "long_rest"},
                        "channel_divinity": {"label": "Channel Divinity", "current": 0, "max": 1, "reset_on": "short_rest"},
                    },
                    "currency": {"cp": 5, "sp": 2, "ep": 0, "gp": 50, "pp": 2},
                    "weapons": [
                        {"name": "Mace", "atk_bonus": "+4", "damage": "1d6+2 Bludgeoning", "notes": "1H"}
                    ],
                    "inventory": [
                        {"name": "Chain Mail", "qty": "1", "category": "Armor", "equipped": True, "notes": "AC 16"},
                        {"name": "Scale Mail", "qty": "1", "category": "Armor", "equipped": False, "notes": "AC 14 + DEX (max 2)"},
                        {"name": "Shield", "qty": "1", "category": "Shield", "equipped": False, "notes": "+2 AC"},
                        {"name": "Potion of Healing", "qty": "2", "category": "Consumable", "notes": "2d4+2 HP"},
                        {"name": "Rations (1 day)", "qty": "5", "category": "Consumable", "notes": "Food"},
                        {"name": "Cloak of Protection", "qty": "1", "category": "Magic Item", "is_magic": True, "requires_attunement": True, "notes": "Requires Attunement"},
                        {"name": "Ring of Protection", "qty": "1", "category": "Magic Item", "is_magic": True, "requires_attunement": True, "notes": "Requires Attunement"},
                        {"name": "Boots of Elvenkind", "qty": "1", "category": "Magic Item", "is_magic": True, "requires_attunement": True, "notes": "Not attunement, wait - let's mark custom"},
                        {"name": "Wand of Web", "qty": "1", "category": "Magic Item", "is_magic": True, "requires_attunement": True, "notes": "Requires Attunement"},
                        {"name": "Amulet of Health", "qty": "1", "category": "Magic Item", "is_magic": True, "requires_attunement": True, "notes": "Requires Attunement"},
                    ],
                    "attuned_items": ["Cloak of Protection"],
                    "spells": [
                        {"name": "Sacred Flame", "level": "Cantrip", "is_cantrip": True, "prepared": True},
                        {"name": "Cure Wounds", "level": "1st Level", "is_cantrip": False, "prepared": True},
                    ],
                    "native_system_data": {
                        "ability_scores": {"STR": 15, "DEX": 14, "CON": 14, "INT": 10, "WIS": 16, "CHA": 12},
                        "class_level": "Cleric 3",
                        "armor_class": 16,
                        "armor_equipped": "Chain Mail",
                        "shield_equipped": False,
                        "currency": {"cp": 5, "sp": 2, "ep": 0, "gp": 50, "pp": 2},
                        "spell_slots": {
                            "level_1": {"current": 1, "max": 4},
                            "level_2": {"current": 0, "max": 2},
                        },
                        "spells": [
                            {"name": "Sacred Flame", "level": "Cantrip", "is_cantrip": True, "prepared": True},
                            {"name": "Cure Wounds", "level": "1st Level", "is_cantrip": False, "prepared": True},
                        ],
                        "inventory": [
                            {"name": "Chain Mail", "qty": "1", "category": "Armor", "equipped": True},
                            {"name": "Potion of Healing", "qty": "2", "category": "Consumable"},
                            {"name": "Rations (1 day)", "qty": "5", "category": "Consumable"},
                        ],
                    },
                },
                "char_sylvan": {
                    "id": "char_sylvan",
                    "name": "Sylvan",
                    "archetype": "Ranger 4",
                    "class_and_level": "Ranger 4",
                    "level": 4,
                    "ruleset": "dnd_5e",
                    "prof_bonus": "+2",
                    "spellcasting_ability": "WIS",
                    "armor_class": 14,
                    "attributes": {"STR": 12, "DEX": 16, "CON": 12, "INT": 10, "WIS": 16, "CHA": 10},
                    "vitals": {
                        "health": {"current": 20, "max": 32, "label": "Health"},
                        "guard": {"current": 14, "max": 14, "label": "Armor Defense"},
                    },
                    "custom_meters": {
                        "hit_dice": {"label": "Hit Dice (4d10)", "current": 2, "max": 4, "reset_on": "long_rest"},
                    },
                    "currency": {"cp": 0, "sp": 0, "ep": 0, "gp": 100, "pp": 0},
                    "weapons": [],
                    "inventory": [],
                    "spells": [
                        {"name": "Hunter's Mark", "level": "1st Level", "is_cantrip": False, "prepared": True},
                    ],
                    "native_system_data": {
                        "ability_scores": {"STR": 12, "DEX": 16, "CON": 12, "INT": 10, "WIS": 16, "CHA": 10},
                        "class_level": "Ranger 4",
                        "spell_slots": {"level_1": {"current": 1, "max": 3}},
                        "spells": [
                            {"name": "Hunter's Mark", "level": "1st Level", "is_cantrip": False, "prepared": True},
                        ],
                    },
                },
            },
        }
        self.service.save_active_campaign_state()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # --- Merchant Shop Tests ---

    def test_merchant_catalog_markups_and_discounts(self):
        # Standard 1.0x vs 1.5x Black Market vs 0.8x Quartermaster on Longsword (base 15 gp = 1500 cp)
        std = self.service.get_merchant_catalog(specialty="weapons_armor", query="Longsword", price_multiplier=1.0)
        ls_std = next(x for x in std["items"] if x["name"] == "Longsword")
        self.assertEqual(ls_std["adjusted_cost_cp"], 1500)

        markup = self.service.get_merchant_catalog(specialty="weapons_armor", query="Longsword", price_multiplier=1.5)
        ls_mk = next(x for x in markup["items"] if x["name"] == "Longsword")
        self.assertEqual(ls_mk["adjusted_cost_cp"], 2250)  # 22 gp 5 sp

        disc = self.service.get_merchant_catalog(specialty="weapons_armor", query="Longsword", price_multiplier=0.8)
        ls_ds = next(x for x in disc["items"] if x["name"] == "Longsword")
        self.assertEqual(ls_ds["adjusted_cost_cp"], 1200)  # 12 gp

        potions = self.service.get_merchant_catalog(specialty="potions", price_multiplier=1.25)
        self.assertTrue(any("healing" in x["name"].lower() for x in potions["items"]))
        self.assertFalse(any(x["name"] == "Longsword" for x in potions["items"]))

    def test_purchase_multi_coin_change_pack_and_insufficient_funds(self):
        shadow = self.service.active_state["characters"]["char_shadow"]
        start_cp = currency_dict_to_cp(shadow["currency"])  # 50 gp + 2 pp + 2 sp + 5 cp = 7025 cp
        self.assertEqual(start_cp, 7025)

        # Buy 2x Rations (1 day) @ 5 sp each with 1.5x frontier markup -> 150 cp total
        res_rat = self.service.purchase_merchant_item(
            character_name="Shadow",
            category="items",
            index_or_name="Rations (1 day)",
            qty=2,
            price_multiplier=1.5,
        )
        self.assertEqual(res_rat["status"], "success")
        self.assertEqual(res_rat["total_cost_cp"], 150)
        self.assertEqual(currency_dict_to_cp(shadow["currency"]), 7025 - 150)
        self.assertEqual(currency_dict_to_cp(shadow["native_system_data"]["currency"]), 7025 - 150)

        # Buy Explorer's Pack (10 gp base = 1000 cp @ 0.8x = 800 cp) with expansion
        res_pack = self.service.purchase_merchant_item(
            character_name="Shadow",
            category="items",
            index_or_name="Explorer's Pack",
            qty=1,
            price_multiplier=0.8,
            expand_pack=True,
        )
        self.assertEqual(res_pack["status"], "success")
        self.assertEqual(res_pack["total_cost_cp"], 800)
        self.assertGreater(len(res_pack["granted"]), 3)

        # Try buying Plate Armor (1500 gp) -> must reject with Insufficient funds and preserve wallet
        cp_before_fail = currency_dict_to_cp(shadow["currency"])
        res_fail = self.service.purchase_merchant_item(
            character_name="Shadow",
            category="armor",
            index_or_name="Plate Armor",
            qty=1,
            price_multiplier=1.0,
        )
        self.assertEqual(res_fail["status"], "error")
        self.assertIn("Insufficient funds", res_fail["message"])
        self.assertEqual(currency_dict_to_cp(shadow["currency"]), cp_before_fail)

    def test_sell_partial_stack_weapon_equipped_armor_and_unarmed_block(self):
        shadow = self.service.active_state["characters"]["char_shadow"]

        # 1. Partial stack sell: 2 of 5 Rations (1 day) (base 5 sp = 50 cp * 0.5 * 2 = 50 cp)
        cp_before = currency_dict_to_cp(shadow["currency"])
        res_sell_r = self.service.sell_merchant_item("Shadow", "Rations (1 day)", sell_rate=0.5, qty=2)
        self.assertEqual(res_sell_r["status"], "success")
        self.assertEqual(res_sell_r["sold_qty"] if "sold_qty" in res_sell_r else res_sell_r["qty"], 2)
        self.assertEqual(res_sell_r["payout_cp"], 50)
        self.assertEqual(currency_dict_to_cp(shadow["currency"]), cp_before + 50)
        rat_left = next(x for x in shadow["inventory"] if isinstance(x, dict) and "Rations" in x["name"])
        self.assertEqual(str(rat_left["qty"]), "3")
        nat_rat = next(x for x in shadow["native_system_data"]["inventory"] if isinstance(x, dict) and "Rations" in x["name"])
        self.assertEqual(str(nat_rat["qty"]), "3")

        # 2. Selling equipped Chain Mail recomputes AC to unarmored (10 + DEX +2 = 12)
        res_arm = self.service.sell_merchant_item("Shadow", "Chain Mail", sell_rate=0.5, qty=1)
        self.assertEqual(res_arm["status"], "success")
        self.assertEqual(shadow["armor_class"], 12)
        self.assertEqual(shadow["vitals"]["guard"]["current"], 12)
        self.assertEqual(shadow["armor_equipped"], "Unarmored")

        # 3. Selling weapon (Mace) removes it from weapons list
        res_wpn = self.service.sell_merchant_item("Shadow", "Mace", sell_rate=0.5)
        self.assertEqual(res_wpn["status"], "success")
        self.assertFalse(any(w["name"] == "Mace" for w in shadow["weapons"]))

        # 4. Selling attuned item cleans up attunement
        res_att = self.service.sell_merchant_item("Shadow", "Cloak of Protection", override_value_gp=200)
        self.assertEqual(res_att["status"], "success")
        self.assertNotIn("Cloak of Protection", shadow["attuned_items"])

        # 5. Unarmed Strike cannot be sold
        shadow["weapons"].append({"name": "Unarmed Strike", "atk_bonus": "+4", "damage": "1d4+2"})
        res_unarmed = self.service.sell_merchant_item("Shadow", "Unarmed Strike")
        self.assertEqual(res_unarmed["status"], "error")
        self.assertIn("Unarmed Strike", res_unarmed["message"])

    # --- Interactive Short / Long Rest Tests ---

    def test_rest_modal_state_and_edition_spell_differences(self):
        # 2024 rules: Cleric can swap from full class list; Ranger (2024) ALSO prepares from class list!
        st_2024 = self.service.get_rest_modal_state(rest_type="long", edition="2024")
        self.assertEqual(st_2024["status"], "success")
        sh_2024 = next(c for c in st_2024["characters"] if c["name"] == "Shadow")
        syl_2024 = next(c for c in st_2024["characters"] if c["name"] == "Sylvan")

        self.assertTrue(sh_2024["spellcasting"]["can_swap_from_class_list"])
        cleric_elig = [s["name"] for s in sh_2024["spellcasting"]["eligible_class_spells"]]
        self.assertIn("Bless", cleric_elig)
        self.assertIn("Spiritual Weapon", cleric_elig)
        self.assertEqual(sh_2024["spellcasting"]["max_prepared_leveled_spells"], 6)  # 2024 Cleric L3 = 6

        self.assertEqual(syl_2024["spellcasting"]["prep_style"], "prepared_full_list")
        self.assertTrue(syl_2024["spellcasting"]["can_swap_from_class_list"])

        # 2014 rules: Ranger is 'known_half' caster
        st_2014 = self.service.get_rest_modal_state(rest_type="long", edition="2014")
        syl_2014 = next(c for c in st_2014["characters"] if c["name"] == "Sylvan")
        sh_2014 = next(c for c in st_2014["characters"] if c["name"] == "Shadow")
        self.assertEqual(syl_2014["spellcasting"]["prep_style"], "known_half")
        self.assertFalse(syl_2014["spellcasting"]["can_swap_from_class_list"])
        self.assertEqual(sh_2014["spellcasting"]["max_prepared_leveled_spells"], 6)  # 3 WIS + 3 Lv

    def test_interactive_short_rest_digital_consumables_gear_and_attunement_cap(self):
        shadow = self.service.active_state["characters"]["char_shadow"]

        # 1. Attunement cap > 3 must reject atomically WITHOUT mutating HP, Hit Dice, Potions, or Armor!
        hp_before = shadow["vitals"]["health"]["current"]
        bad_res = self.service.commit_interactive_rest({
            "rest_type": "short",
            "dice_mode": "digital",
            "edition": "2024",
            "characters": {
                "char_shadow": {
                    "hit_dice_to_spend": 1,
                    "consumables_used": [{"name": "Potion of Healing", "qty": 1}],
                    "equipped_armor": "Scale Mail",
                    "shield_equipped": True,
                    "attuned_items": [
                        "Cloak of Protection",
                        "Ring of Protection",
                        "Wand of Web",
                        "Amulet of Health",
                    ],
                }
            },
        })
        self.assertEqual(bad_res["status"], "error")
        self.assertIn("more than 3", bad_res["message"])
        self.assertEqual(shadow["vitals"]["health"]["current"], hp_before)
        self.assertEqual(shadow["armor_equipped"], "Chain Mail")

        # 2. Valid Short Rest: Spend 1 Hit Die + drink 1 Potion of Healing + equip Scale Mail & Shield + 3 attuned items
        ok_res = self.service.commit_interactive_rest({
            "rest_type": "short",
            "dice_mode": "digital",
            "edition": "2024",
            "characters": {
                "char_shadow": {
                    "hit_dice_to_spend": 1,
                    "consumables_used": [{"name": "Potion of Healing", "qty": 1}],
                    "equipped_armor": "Scale Mail",
                    "shield_equipped": True,
                    "attuned_items": [
                        "Cloak of Protection",
                        "Ring of Protection",
                        "Wand of Web",
                    ],
                }
            },
        })
        self.assertEqual(ok_res["status"], "success")
        # Hit Dice: 2 - 1 = 1 remaining
        self.assertEqual(shadow["custom_meters"]["hit_dice"]["current"], 1)
        # Channel Divinity resets on short rest: 0 -> 1
        self.assertEqual(shadow["custom_meters"]["channel_divinity"]["current"], 1)
        # HP healed > 12 (clamped <= 24)
        self.assertGreater(shadow["vitals"]["health"]["current"], 12)
        self.assertLessEqual(shadow["vitals"]["health"]["current"], 24)
        # Potion of Healing decremented 2 -> 1 in both root & native
        pot_root = next(i for i in shadow["inventory"] if isinstance(i, dict) and i["name"] == "Potion of Healing")
        pot_nat = next(i for i in shadow["native_system_data"]["inventory"] if isinstance(i, dict) and i["name"] == "Potion of Healing")
        self.assertEqual(str(pot_root["qty"]), "1")
        self.assertEqual(str(pot_nat["qty"]), "1")
        # Armor & Shield swap: Scale Mail (14 + min(DEX 2, 2)) + Shield (+2) = AC 18 & stealth_disadvantage=True
        self.assertEqual(shadow["armor_class"], 18)
        self.assertEqual(shadow["armor_equipped"], "Scale Mail")
        self.assertTrue(shadow["shield_equipped"])
        self.assertTrue(shadow["stealth_disadvantage"])
        self.assertEqual(shadow["vitals"]["guard"]["current"], 18)
        # Attuned 3/3 synced
        self.assertEqual(len(shadow["attuned_items"]), 3)
        self.assertEqual(shadow["attuned_magic_items"], {"current": 3, "max": 3})

    def test_interactive_short_rest_physical_dice_and_long_rest_editions(self):
        shadow = self.service.active_state["characters"]["char_shadow"]
        sylvan = self.service.active_state["characters"]["char_sylvan"]

        # Physical Short Rest: Player reports +7 HP for 1 Hit Die
        res_phys = self.service.commit_interactive_rest({
            "rest_type": "short",
            "dice_mode": "physical",
            "edition": "2014",
            "characters": {
                "char_shadow": {
                    "hit_dice_to_spend": 1,
                    "manual_hp_gain": 7,
                }
            },
        })
        self.assertEqual(res_phys["status"], "success")
        self.assertEqual(shadow["vitals"]["health"]["current"], 19)  # 12 + 7
        self.assertEqual(shadow["custom_meters"]["hit_dice"]["current"], 1)  # 2 - 1

        # Long Rest under 2014 rules: Sylvan (2/4 HD) recovers HALF max Hit Dice -> 2 + 2 = 4; Shadow (1/3 HD) -> 1 + max(1, 3//2)=2
        sylvan["custom_meters"]["hit_dice"]["current"] = 1
        shadow["custom_meters"]["hit_dice"]["current"] = 1
        res_long_2014 = self.service.commit_interactive_rest({
            "rest_type": "long",
            "edition": "2014",
            "characters": {
                "char_shadow": {
                    "prepared_spell_names": ["Bless", "Guiding Bolt"],
                }
            },
        })
        self.assertEqual(res_long_2014["status"], "success")
        self.assertEqual(sylvan["custom_meters"]["hit_dice"]["current"], 3)  # 1 + (4 // 2) = 3
        self.assertEqual(shadow["custom_meters"]["hit_dice"]["current"], 2)  # 1 + max(1, 3 // 2) = 2
        self.assertEqual(shadow["vitals"]["health"]["current"], 24)
        self.assertEqual(shadow["spell_slots"]["level_2"]["current"], 2)
        # Spell prep swap: Cure Wounds -> unprepared; Bless & Guiding Bolt added and prepared; Sacred Flame (cantrip) still prepared
        sp_map = {s["name"]: s for s in shadow["spells"]}
        self.assertTrue(sp_map["Sacred Flame"]["prepared"])
        self.assertFalse(sp_map["Cure Wounds"]["prepared"])
        self.assertTrue(sp_map["Bless"]["prepared"])
        self.assertTrue(sp_map["Guiding Bolt"]["prepared"])
        # Mirror check in native_system_data
        nat_sp = {s["name"]: s for s in shadow["native_system_data"]["spells"]}
        self.assertTrue(nat_sp["Bless"]["prepared"])
        self.assertFalse(nat_sp["Cure Wounds"]["prepared"])

        # Long Rest under 2024 rules: Hit Dice recover to FULL max
        sylvan["custom_meters"]["hit_dice"]["current"] = 0
        shadow["custom_meters"]["hit_dice"]["current"] = 0
        res_long_2024 = self.service.commit_interactive_rest({"rest_type": "long", "edition": "2024"})
        self.assertEqual(res_long_2024["status"], "success")
        self.assertEqual(sylvan["custom_meters"]["hit_dice"]["current"], 4)
        self.assertEqual(shadow["custom_meters"]["hit_dice"]["current"], 3)

    # --- DM Tools Tests ---

    async def test_dm_tools_merchant_and_prompt_rest(self):
        dispatcher = MagicMock()
        ui_events = []
        register_ttrpg_tools(dispatcher, self.service, lambda ev, data: ui_events.append((ev, data)))
        tools = {call.kwargs["name"]: call.kwargs["handler"] for call in dispatcher.register_tool.call_args_list}

        self.assertIn("open_merchant_shop", tools)
        self.assertIn("merchant_transaction", tools)
        self.assertIn("prompt_party_rest", tools)

        # 1. open_merchant_shop emits shop_opened & saves active_shop
        shop_res = await tools["open_merchant_shop"](
            shop_name="Tane Here's Surplus",
            location_name="Camp Highmore Outpost",
            price_multiplier=1.25,
            specialty="weapons_armor",
            description="No credit. Gold upfront.",
        )
        self.assertEqual(shop_res["status"], "success")
        self.assertEqual(self.service.active_state["active_shop"]["price_multiplier"], 1.25)
        self.assertTrue(any(ev == "shop_opened" and data["shop_name"] == "Tane Here's Surplus" for ev, data in ui_events))

        # 2. merchant_transaction buy Shield with equip=True & sell
        tx_buy = await tools["merchant_transaction"](
            character_name="Sylvan",
            action="buy",
            item_name="Leather Armor",
            category="armor",
            price_multiplier=1.25,
            equip=True,
        )
        self.assertEqual(tx_buy["status"], "success")
        sylvan = self.service.active_state["characters"]["char_sylvan"]
        self.assertEqual(sylvan["armor_equipped"], "Leather Armor")
        self.assertEqual(sylvan["armor_class"], 14)  # 11 + DEX 3

        tx_sell = await tools["merchant_transaction"](
            character_name="Shadow",
            action="sell",
            item_name="Mace",
            sell_rate=0.5,
        )
        self.assertEqual(tx_sell["status"], "success")
        self.assertTrue(any(ev == "roster_updated" for ev, _ in ui_events))

        # 3. prompt_party_rest emits rest_prompt_opened
        rest_res = await tools["prompt_party_rest"](rest_type="long", reason="Camping beside the silver stream")
        self.assertEqual(rest_res["status"], "success")
        self.assertEqual(rest_res["rest_type"], "long")
        self.assertTrue(any(ev == "rest_prompt_opened" and data["rest_type"] == "long" for ev, data in ui_events))


if __name__ == "__main__":
    unittest.main()
