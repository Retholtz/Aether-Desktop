import json
import os
import shutil
import unittest
from core.ttrpg_service import TTRPGService
from tools.ttrpg_tools import register_ttrpg_tools
from core.game_engine import GameEngine


class FakeDispatcher:
    def __init__(self):
        self.tools = {}

    def register_tool(self, name, description, parameters, handler):
        self.tools[name] = {
            "description": description,
            "parameters": parameters,
            "handler": handler
        }


class TestTTRPGService(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = "tests/test_campaigns_dir"
        self.test_manifest = "tests/test_campaigns.json"
        self.service = TTRPGService(data_dir=self.test_dir, manifest_path=self.test_manifest)

        # Create a test campaign and inject sample state
        self.service.create_campaign(
            title="Test Campaign",
            system_preset="heroic_d20",
            gm_mode="interactive_narrator",
            setting_overview="Dark fantasy citadel.",
            tone="Gritty, heroic",
            campaign_id="test_camp"
        )
        self.service.active_state["characters"] = {
            "char_01": {
                "id": "char_01",
                "name": "Kaelen",
                "vitals": {
                    "health": {"current": 20, "max": 20, "label": "Health"}
                },
                "custom_meters": {
                    "mana": {"current": 10, "max": 10, "label": "Mana"}
                },
                "conditions": []
            }
        }
        self.service.save_active_campaign_state()

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)
        if os.path.exists(self.test_manifest):
            os.remove(self.test_manifest)

    def test_dice_parser_bounds_and_modifiers(self):
        # 1. Standard Roll
        res = self.service.parse_and_roll("1d20+5")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["sides"], 20)
        self.assertEqual(res["modifier"], 5)
        self.assertTrue(6 <= res["total"] <= 25)

        # 2. Advantage Roll
        adv_res = self.service.parse_and_roll("1d20", roll_type="advantage")
        self.assertEqual(adv_res["status"], "success")
        self.assertTrue(1 <= adv_res["total"] <= 20)
        self.assertIn("kept_die", adv_res)

        # 3. Invalid Syntax Guard
        bad_res = self.service.parse_and_roll("invalid_dice")
        self.assertEqual(bad_res["status"], "error")

    def test_modify_character_stat_and_clamping(self):
        # Apply 5 damage
        dmg_res = self.service.modify_character_stat("Kaelen", "health", -5)
        self.assertEqual(dmg_res["status"], "success")
        self.assertEqual(dmg_res["new_value"], 15)

        # Test floor clamp at 0
        overkill_res = self.service.modify_character_stat("Kaelen", "health", -100)
        self.assertEqual(overkill_res["new_value"], 0)

        # Test custom meter (Mana)
        mana_res = self.service.modify_character_stat("Kaelen", "mana", -3, is_custom_meter=True)
        self.assertEqual(mana_res["status"], "success")
        self.assertEqual(mana_res["new_value"], 7)

    def test_initiative_ladder_and_turn_progression(self):
        self.service.add_to_initiative("Goblin", 10, is_player=False)
        self.service.add_to_initiative("Kaelen", 18, is_player=True)

        ladder = self.service.active_state["initiative_order"]
        self.assertEqual(ladder[0]["name"], "Kaelen")  # Highest first
        self.assertEqual(ladder[1]["name"], "Goblin")

        # Turn progression
        turn_1 = self.service.advance_turn()
        self.assertEqual(turn_1["active_entity"], "Goblin")

        turn_2 = self.service.advance_turn()
        self.assertEqual(turn_2["active_entity"], "Kaelen")
        self.assertEqual(turn_2["round"], 2)  # Wrapped to Round 2

    def test_condition_tracking(self):
        self.service.update_condition("Kaelen", "Blinded", active=True)
        self.assertIn("Blinded", self.service.active_state["characters"]["char_01"]["conditions"])

        self.service.update_condition("Kaelen", "Blinded", active=False)
        self.assertNotIn("Blinded", self.service.active_state["characters"]["char_01"]["conditions"])

    def test_update_campaign_details(self):
        upd = self.service.update_campaign(
            campaign_id="test_camp",
            title="Curse of the Obsidian Spire",
            system_preset="investigative_d100",
            gm_mode="tactical_referee",
            setting_overview="An ancient spire rising from theastral sea.",
            tone="Cosmic mystery, high tension"
        )
        self.assertEqual(upd["status"], "success")
        self.assertEqual(upd["campaign"]["title"], "Curse of the Obsidian Spire")
        self.assertEqual(upd["campaign"]["system_preset"], "investigative_d100")
        self.assertEqual(upd["campaign"]["gm_mode"], "tactical_referee")
        self.assertEqual(upd["campaign"]["tone"], "Cosmic mystery, high tension")

    def test_campaign_documents_upload_read_and_gm_prompt(self):
        # 1. Upload a Ruleset Markdown document
        ruleset_bytes = b"# D&D 5e House Rules\nCritical hits deal maximum die damage plus a roll."
        res_doc = self.service.add_campaign_document(
            filename="house_rules.md",
            content_bytes=ruleset_bytes,
            doc_category="ruleset"
        )
        self.assertEqual(res_doc["status"], "success")
        self.assertEqual(res_doc["document"]["category"], "ruleset")
        self.assertGreater(res_doc["document"]["token_estimate"], 0)

        # 2. Upload a JSON Character Sheet and verify auto-import to roster
        char_sheet_json = json.dumps({
            "name": "Seraphina",
            "player_name": "Elena",
            "archetype": "Cleric of Light",
            "max_health": 38,
            "guard": 18
        }).encode("utf-8")
        res_char_doc = self.service.add_campaign_document(
            filename="seraphina_sheet.json",
            content_bytes=char_sheet_json,
            doc_category="character_sheet"
        )
        self.assertEqual(res_char_doc["status"], "success")
        self.assertIn("Seraphina", res_char_doc["imported_characters"])

        # 3. List documents (character sheets removed after import; only rulesets/campaigns remain)
        listed = self.service.list_campaign_documents()
        self.assertEqual(listed["status"], "success")
        self.assertEqual(len(listed["documents"]), 1)
        doc_filenames = [d["filename"] for d in listed["documents"]]
        self.assertIn("house_rules.md", doc_filenames)
        self.assertNotIn("seraphina_sheet.json", doc_filenames)

        # 4. Read single document and read 'all'
        read_single = self.service.read_campaign_document("house_rules.md")
        self.assertEqual(read_single["status"], "success")
        self.assertIn("Critical hits deal maximum", read_single["content"])

        read_all = self.service.read_campaign_document("all")
        self.assertEqual(read_all["status"], "success")
        self.assertEqual(read_all["document_count"], 1)
        self.assertIn("house_rules.md", read_all["content"])
        self.assertNotIn("seraphina_sheet.json", read_all["content"])

        # 5. Verify GM context prompt includes campaign, characters, and uploaded docs
        gm_prompt = self.service.build_gm_context_prompt()
        self.assertIn("Test Campaign", gm_prompt)
        self.assertIn("Seraphina", gm_prompt)
        self.assertIn("house_rules.md", gm_prompt)

        # 6. Delete document
        del_res = self.service.delete_campaign_document("house_rules.md")
        self.assertEqual(del_res["status"], "success")
        self.assertEqual(len(self.service.list_campaign_documents()["documents"]), 0)

    def test_refresh_campaign_files_and_characters(self):
        # Drop a Markdown character sheet directly into the campaign's files directory
        files_dir = self.service.get_campaign_files_dir("test_camp")
        sheet_md = (
            "# Character Sheet\n"
            "Character Name: Bram Ironfoot\n"
            "Player: Alex\n"
            "Class & Level: Barbarian 4\n"
            "Max HP: 45\n"
            "Armor Class: 16\n"
            "STR: 18\n"
            "DEX: 14\n"
            "CON: 16\n"
            "INT: 9\n"
            "CHA: 12\n"
        )
        with open(os.path.join(files_dir, "bram_character_sheet.md"), "w", encoding="utf-8") as f:
            f.write(sheet_md)

        ref_res = self.service.refresh_campaign_files_and_characters("test_camp")
        self.assertEqual(ref_res["status"], "success")
        self.assertIn("bram_character_sheet.md", ref_res["newly_indexed_files"])
        self.assertIn("Bram Ironfoot", ref_res["imported_characters"])

        # Verify Bram Ironfoot is now in the active campaign roster with parsed stats
        char_id, bram = self.service._find_character("Bram Ironfoot")
        self.assertIsNotNone(bram)
        self.assertEqual(bram["player_name"], "Alex")
        self.assertEqual(bram["archetype"], "Barbarian 4")
        self.assertEqual(bram["vitals"]["health"]["max"], 45)
        self.assertEqual(bram["vitals"]["guard"]["current"], 16)
        self.assertEqual(bram["attributes"]["Might"], 18)

    def test_pdf_form_fields_character_extraction_and_upsert(self):
        # 1. Test D&D Beyond form fields parsing
        sample_fields = {
            "CharacterName": "Eric",
            "PLAYER NAME": "retholtz",
            "CLASS  LEVEL": "Fighter 1",
            "RACE": "Human",
            "BACKGROUND": "Noble",
            "MaxHP": "14",
            "AC": "19",
            "STR": "15",
            "DEX": "12",
            "CON": "14",
            "INT": "13",
            "WIS": "10",
            "CHA": "8",
            "Total": "1d10",
            "Speed": "30 ft. (Walking\\"
        }
        parsed = self.service._parse_character_from_form_fields(sample_fields, "retholtz_173064916.pdf")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["name"], "Eric")
        self.assertEqual(parsed["player_name"], "retholtz")
        self.assertIn("Fighter 1", parsed["archetype"])
        self.assertEqual(parsed["max_health"], 14)
        self.assertEqual(parsed["guard"], 19)
        self.assertEqual(parsed["Might"], 15)
        self.assertEqual(parsed["Agility"], 12)
        self.assertEqual(parsed["Vigor"], 14)
        self.assertEqual(parsed["Intellect"], 13)
        self.assertEqual(parsed["Presence"], 10)
        self.assertIn("hit_dice", parsed["custom_meters"])

        # 2. Test upsert when character was previously loaded as a placeholder with default 12s
        self.service.active_state["characters"]["char_placeholder"] = {
            "id": "char_placeholder",
            "name": "retholtz_173064916",
            "player_name": "Player",
            "archetype": "Adventurer",
            "is_player": True,
            "vitals": {
                "health": {"current": 5, "max": 5, "label": "Health"},
                "guard": {"current": 12, "max": 12, "label": "Armor Defense"}
            },
            "attributes": {"Might": 12, "Agility": 12, "Vigor": 12, "Intellect": 12, "Presence": 12},
            "custom_meters": {},
            "conditions": []
        }
        upsert_res = self.service.upsert_character_from_sheet(parsed, filename="retholtz_173064916.pdf")
        self.assertEqual(upsert_res["status"], "success")
        self.assertEqual(upsert_res["action"], "updated")

        updated_char = self.service.active_state["characters"]["char_placeholder"]
        self.assertEqual(updated_char["name"], "Eric")
        self.assertEqual(updated_char["vitals"]["health"]["max"], 14)
        self.assertEqual(updated_char["vitals"]["guard"]["current"], 19)
        self.assertEqual(updated_char["attributes"]["Might"], 15)

    def test_fuzzy_name_matching_on_character_sheet_refresh(self):
        # Character created with typo "Shiela"
        self.service.active_state["characters"]["char_shiela"] = {
            "id": "char_shiela",
            "name": "Shiela",
            "vitals": {"health": {"current": 5, "max": 5, "label": "Health"}, "guard": {"current": 10, "max": 10, "label": "Armor Defense"}},
            "attributes": {"Might": 12, "Agility": 12, "Vigor": 12, "Intellect": 12, "Presence": 12}
        }
        sheila_payload = {
            "name": "Sheila",
            "player_name": "retholtz",
            "archetype": "Rogue 1 (Human, Farmer)",
            "max_health": 10,
            "guard": 13,
            "Might": 8,
            "Agility": 15,
            "Vigor": 10,
            "Intellect": 14,
            "Presence": 13
        }
        res = self.service.upsert_character_from_sheet(sheila_payload, filename="retholtz_173064981.pdf")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["action"], "updated")
        char = self.service.active_state["characters"]["char_shiela"]
        self.assertEqual(char["name"], "Sheila")
        self.assertEqual(char["vitals"]["health"]["max"], 10)
        self.assertEqual(char["vitals"]["guard"]["current"], 13)
        self.assertEqual(char["attributes"]["Agility"], 15)

    def test_gemini_multimodal_ocr_mock(self):
        class MockModels:
            def generate_content(self, model, contents, config):
                class MockResp:
                    text = json.dumps({
                        "is_character_sheet": True,
                        "name": "Valerius",
                        "player_name": "Dave",
                        "archetype": "Cleric of the Forge 3",
                        "max_health": 27,
                        "current_health": 27,
                        "guard": 18,
                        "Might": 16,
                        "Agility": 10,
                        "Vigor": 14,
                        "Intellect": 12,
                        "Presence": 16,
                        "custom_meters": {},
                        "conditions": []
                    })
                return MockResp()

        class MockGenAIClient:
            models = MockModels()

        ocr_service = TTRPGService(
            data_dir=self.test_dir,
            manifest_path=self.test_manifest,
            client_getter=lambda: MockGenAIClient()
        )
        fake_pdf_bytes = b"%PDF-1.4 minimal scanned test bytes"
        char_data = ocr_service._parse_character_with_gemini("scanned_sheet.pdf", fake_pdf_bytes)
        self.assertIsNotNone(char_data)
        self.assertEqual(char_data["name"], "Valerius")
        self.assertEqual(char_data["guard"], 18)
        self.assertEqual(char_data["max_health"], 27)
        self.assertEqual(char_data["Presence"], 16)

    async def test_ttrpg_tools_registration_and_execution(self):
        dispatcher = FakeDispatcher()
        register_ttrpg_tools(dispatcher, self.service)

        self.assertIn("roll_dice", dispatcher.tools)
        self.assertIn("modify_character_vitals", dispatcher.tools)
        self.assertIn("advance_combat_turn", dispatcher.tools)
        self.assertIn("set_character_condition", dispatcher.tools)
        self.assertIn("list_campaign_documents", dispatcher.tools)
        self.assertIn("read_campaign_document", dispatcher.tools)
        self.assertIn("upsert_ttrpg_character", dispatcher.tools)
        self.assertIn("update_campaign_details", dispatcher.tools)
        self.assertIn("record_campaign_notes", dispatcher.tools)
        self.assertIn("get_campaign_notes", dispatcher.tools)

        roll_res = await dispatcher.tools["roll_dice"]["handler"]("2d6+3", "normal", "Sword Slash")
        self.assertEqual(roll_res["status"], "success")
        self.assertEqual(roll_res["purpose"], "Sword Slash")
        self.assertTrue(5 <= roll_res["total"] <= 15)

        upsert_res = await dispatcher.tools["upsert_ttrpg_character"]["handler"](
            name="Thorne",
            player_name="Marcus",
            archetype="Paladin",
            max_health=44,
            guard=19
        )
        self.assertEqual(upsert_res["status"], "success")
        self.assertEqual(upsert_res["character"]["name"], "Thorne")

        # Test recording notes via tool
        notes_res = await dispatcher.tools["record_campaign_notes"]["handler"](
            summary="Party defeated the goblin sentries and discovered a map to the obsidian crypt.",
            location="Forgotten Pass",
            key_clues=["Obsidian Crypt Map"],
            active_quest="Infiltrate the crypt",
            last_action="Secured the map"
        )
        self.assertEqual(notes_res["status"], "success")
        self.assertEqual(notes_res["latest_recap"], "Party defeated the goblin sentries and discovered a map to the obsidian crypt.")

        fetched_notes = await dispatcher.tools["get_campaign_notes"]["handler"]()
        self.assertEqual(fetched_notes["status"], "success")
        self.assertEqual(fetched_notes["current_location"], "Forgotten Pass")
        self.assertIn("Obsidian Crypt Map", fetched_notes["discovered_clues"])

    async def test_roll_dice_single_notification_emission(self):
        events = []
        class DualDispatcher(FakeDispatcher):
            def __init__(self):
                super().__init__()
                self.on_event = lambda event_type, data: events.append((event_type, data))
            def notify(self, event_type, data):
                if self.on_event:
                    self.on_event(event_type, data)

        dual_dispatcher = DualDispatcher()
        register_ttrpg_tools(dual_dispatcher, self.service)

        roll_res = await dual_dispatcher.tools["roll_dice"]["handler"]("1d20+2", "normal", "Sheila Perception Check")
        self.assertEqual(roll_res["status"], "success")
        self.assertIn("roll_id", roll_res)
        dice_events = [e for e in events if e[0] == "dice_roll_event"]
        self.assertEqual(len(dice_events), 1, "dice_roll_event must only be emitted once per roll!")

    async def test_roll_dice_character_association(self):
        """Verifies roll_dice associates rolls with explicit or inferred character name."""
        from tools.ttrpg_tools import register_ttrpg_tools
        events = []
        class DualDispatcher(FakeDispatcher):
            def __init__(self):
                super().__init__()
                self.on_event = lambda event_type, data: events.append((event_type, data))

        dual_dispatcher = DualDispatcher()
        self.service.active_state = {
            "characters": {
                "char_01": {"name": "Joe", "archetype": "Barbarian"},
                "char_02": {"name": "Sam", "archetype": "Warlock"}
            },
            "party_caller": "Joe"
        }
        register_ttrpg_tools(dual_dispatcher, self.service)

        # 1. Explicit character_name provided
        res1 = await dual_dispatcher.tools["roll_dice"]["handler"](
            formula="1d20+3",
            roll_type="normal",
            purpose="Investigation check for Elara",
            character_name="Joe"
        )
        self.assertEqual(res1["character_name"], "Joe")
        self.assertIn("[Joe]", res1["summary"])

        # 2. Inferred character_name from purpose
        res2 = await dual_dispatcher.tools["roll_dice"]["handler"](
            formula="1d20+5",
            roll_type="normal",
            purpose="Sam Eldritch Blast attack"
        )
        self.assertEqual(res2["character_name"], "Sam")

        # 3. Fallback to party caller when unnamed
        res3 = await dual_dispatcher.tools["roll_dice"]["handler"](
            formula="1d20+2",
            roll_type="normal",
            purpose="Perception check to search room"
        )
        self.assertEqual(res3["character_name"], "Joe")

    def test_record_and_get_campaign_notes_and_recap(self):
        rec_res = self.service.record_campaign_notes(
            summary="Eric and Sheila discovered a locked chest with an unusual military sigil.",
            location="Ambushed Wagon",
            key_clues=["Locked Chest", "Cold Black Cloth"],
            active_quest="Hunt the elf Imis Elvan",
            last_action="Inspected wreckage"
        )
        self.assertEqual(rec_res["status"], "success")
        self.assertIn("Locked Chest", rec_res["entry"]["key_clues"])
        self.assertEqual(self.service.active_state["latest_recap"], "Eric and Sheila discovered a locked chest with an unusual military sigil.")
        self.assertEqual(self.service.active_state["current_location"], "Ambushed Wagon")

        # Read back via get_campaign_notes
        notes = self.service.get_campaign_notes("test_camp")
        self.assertEqual(notes["status"], "success")
        self.assertEqual(len(notes["journal"]), 1)
        self.assertEqual(notes["journal"][0]["location"], "Ambushed Wagon")

        # Read back via read_campaign_document("journal")
        doc_res = self.service.read_campaign_document("journal")
        self.assertEqual(doc_res["status"], "success")
        self.assertIn("Ambushed Wagon", doc_res["content"])
        self.assertIn("Locked Chest", doc_res["content"])

    def test_build_gm_context_prompt_grounding_and_recap(self):
        self.service.active_state["campaign_synopsis"] = {
            "module_title": "Elfhunt by Jussi R. Foltmar Svendsen",
            "setting": "Jungles of Chult, Forgotten Realms",
            "primary_hub": "Camp Highmore",
            "target_or_threat": "Imis Elvan",
            "core_mission": "Track down and capture the elven fugitive Imis Elvan",
            "key_npcs": ["High Chief Highmore", "Chief Filip", "Chief Shandra"]
        }
        self.service.record_campaign_notes(
            summary="Party secured the locked chest and found the cold black cloth.",
            location="Jungle Edge",
            active_quest="Pursue Imis Elvan"
        )

        prompt = self.service.build_gm_context_prompt()
        self.assertIn("GROUNDED CAMPAIGN ADVENTURE MODULE", prompt)
        self.assertIn("Jungles of Chult", prompt)
        self.assertIn("Camp Highmore", prompt)
        self.assertIn("Chief Filip", prompt)
        self.assertIn("PERSISTENT CAMPAIGN MEMORY & LAST TURN RECAP", prompt)
        self.assertIn("Party secured the locked chest", prompt)
        self.assertIn("DIRECTIVE ON RESUMING PLAY", prompt)

    def test_voice_fast_path_interceptions_and_tab_order(self):
        ge = GameEngine()
        res_roll = ge.intercept_voice_turn("Aether, roll 1d20+5 with advantage")
        self.assertTrue(res_roll.handled)
        self.assertEqual(res_roll.action_type, "ttrpg_roll_dice")
        self.assertEqual(res_roll.details["formula"], "1d20+5")
        self.assertEqual(res_roll.details["roll_type"], "advantage")

        res_turn = ge.intercept_voice_turn("Aether, next turn")
        self.assertTrue(res_turn.handled)
        self.assertEqual(res_turn.action_type, "ttrpg_advance_turn")

        # Verify top tab order in ui/static/index.html:
        # Chats -> Stored Chats -> Programs -> Projects -> Games -> Role Play
        with open("ui/static/index.html", "r", encoding="utf-8") as f:
            html = f.read()
        idx_chats = html.index('id="btn-tab-chat"')
        idx_stored = html.index('id="btn-tab-stored"')
        idx_programs = html.index('id="btn-tab-programs"')
        idx_projects = html.index('id="btn-tab-projects"')
        idx_games = html.index('id="btn-tab-games"')
        idx_roleplay = html.index('id="btn-tab-roleplay"')
        self.assertTrue(idx_chats < idx_stored < idx_programs < idx_projects < idx_games < idx_roleplay)
        self.assertIn('id="edit-campaign-btn"', html)
        self.assertIn('id="delete-campaign-btn"', html)
        self.assertIn('id="refresh-roster-btn"', html)
        self.assertIn('id="refresh-docs-btn"', html)
        self.assertIn('id="btn-upload-rp-doc"', html)
        self.assertIn('id="btn-become-dm"', html)
        self.assertIn('id="modal-camp-dropzone"', html)
        self.assertIn('id="input-modal-camp-files"', html)
        self.assertIn('id="modal-camp-staged-list"', html)

    def test_create_campaign_with_initial_files_and_auto_title(self):
        # Test creating campaign with initial files (character sheet and module)
        char_json = json.dumps({
            "name": "Eldrin Swiftblade",
            "player_name": "Dave",
            "archetype": "Rogue 3 (Elf)",
            "max_health": 24,
            "guard": 15,
            "Might": 10,
            "Agility": 16,
            "Vigor": 14,
            "Intellect": 13,
            "Presence": 12
        })

        initial_files = [
            {
                "filename": "eldrin_sheet.json",
                "content": char_json,
                "category": "character_sheet"
            },
            {
                "filename": "The_Lost_Sanctum_Adventure.md",
                "content": "# The Lost Sanctum\nAn ancient temple forgotten in the Misty Crags.\nStarting Hub: Oakhaven Outpost.\nThreat: Malakor the Undying.",
                "category": "campaign"
            }
        ]

        # Leave title blank so it auto-derives from the adventure module filename!
        res = self.service.create_campaign(
            title="",
            system_preset="heroic_d20",
            gm_mode="interactive_narrator",
            setting_overview="",
            tone="Mysterious, dangerous",
            initial_files=initial_files
        )

        self.assertEqual(res["status"], "success")
        camp = res["campaign"]
        # Title auto-derived from "The_Lost_Sanctum_Adventure.md" -> "The Lost Sanctum Adventure"
        self.assertIn("The Lost Sanctum", camp["title"])
        self.assertEqual(camp["party_count"], 1)

        # Characters imported into state, character sheet removed (one-and-done)
        state = self.service.active_state
        self.assertIn("documents", state)
        self.assertNotIn("eldrin_sheet.json", state["documents"])
        self.assertIn("The_Lost_Sanctum_Adventure.md", state["documents"])

        files_dir = os.path.join(self.service.data_dir, camp["id"], "files")
        self.assertFalse(os.path.exists(os.path.join(files_dir, "eldrin_sheet.json")))
        self.assertTrue(os.path.exists(os.path.join(files_dir, "The_Lost_Sanctum_Adventure.md")))

        # Character roster check
        chars = list(state["characters"].values())
        self.assertEqual(len(chars), 1)
        self.assertEqual(chars[0]["name"], "Eldrin Swiftblade")
        self.assertEqual(chars[0]["vitals"]["health"]["max"], 24)
        self.assertEqual(chars[0]["vitals"]["guard"]["max"], 15)
        self.assertEqual(chars[0]["attributes"]["Agility"], 16)

    def test_system_presets_and_mechanics_directive(self):
        # Create campaign with dnd_5e
        res = self.service.create_campaign(
            title="D&D 5E Adventure",
            system_preset="dnd_5e",
            gm_mode="interactive_narrator",
            setting_overview="Faerûn borderlands.",
            tone="High fantasy adventure",
            campaign_id="dnd_camp"
        )
        self.assertEqual(res["status"], "success")

        # Check GM context prompt has ruleset directive
        prompt = self.service.build_gm_context_prompt()
        self.assertIn("[RULESET MECHANICS DIRECTIVE]", prompt)
        self.assertIn("System: Dungeons & Dragons 5th Edition (D&D 5e)", prompt)
        self.assertIn("Advantage", prompt)
        self.assertIn("Death Saving Throws", prompt)

    def test_lookup_ruleset_rule(self):
        # Add a custom house rule document
        doc_content = "House Rule: Potions can be consumed as a Bonus Action instead of a standard Action."
        self.service.add_campaign_document("house_rules.txt", doc_content.encode("utf-8"), doc_category="ruleset")

        # Test lookup
        result = self.service.lookup_ruleset_rule("potion")
        self.assertEqual(result["status"], "success")
        self.assertIn("Bonus Action", result["ruling"])

        # Test tool dispatcher registration
        dispatcher = FakeDispatcher()
        register_ttrpg_tools(dispatcher, self.service)
        self.assertIn("lookup_ruleset_rule", dispatcher.tools)
        self.assertIn("manage_character_progression", dispatcher.tools)

    def test_character_progression_and_level_up(self):
        # 1. Award XP
        res_xp = self.service.manage_character_progression("Kaelen", action="award_xp", amount_or_level=350)
        self.assertEqual(res_xp["status"], "success")
        self.assertEqual(res_xp["experience_points"], 350)
        self.assertTrue(res_xp["level_up_ready"])

        # 2. Level Up
        old_hp = self.service.active_state["characters"]["char_01"]["vitals"]["health"]["max"]
        res_lvl = self.service.manage_character_progression("Kaelen", action="level_up")
        self.assertEqual(res_lvl["status"], "success")
        self.assertEqual(res_lvl["level"], 2)
        new_hp = self.service.active_state["characters"]["char_01"]["vitals"]["health"]["max"]
        self.assertGreater(new_hp, old_hp)

    def test_dm_difficulty_directives(self):
        # Update difficulty to helpful
        self.service.update_campaign(difficulty="helpful")
        prompt = self.service.build_gm_context_prompt()
        self.assertIn("[DM GUIDANCE & CHALLENGE LEVEL: HELPFUL / BEGINNER-FRIENDLY MENTOR]", prompt)
        self.assertIn("ALWAYS offer 2-3 clear, recommended options", prompt)

        # Update difficulty to hard
        self.service.update_campaign(difficulty="hard")
        prompt_hard = self.service.build_gm_context_prompt()
        self.assertIn("[DM GUIDANCE & CHALLENGE LEVEL: HARD / GRITTY TACTICAL]", prompt_hard)

    def test_record_campaign_notes_robustness(self):
        # Pass key_clues as a comma-separated string rather than list
        res = self.service.record_campaign_notes(
            summary="Party explored ancient ruin.",
            location="Ruined Shrine",
            key_clues="Iron Key, Ancient Tablet"
        )
        self.assertEqual(res["status"], "success")
        self.assertIn("Iron Key", res["discovered_clues"])
        self.assertIn("Ancient Tablet", res["discovered_clues"])

    def test_ttrpg_target_speaker_filter_auto_toggle(self):
        from unittest.mock import MagicMock
        from core.gui_bridge import GuiBridge

        bridge = MagicMock(spec=GuiBridge)
        bridge._config = {
            "audio": {
                "voice_biometrics": {
                    "enabled": True,
                    "threshold": 0.40
                }
            }
        }
        bridge._config_lock = MagicMock()
        bridge._config_lock.__enter__ = MagicMock(return_value=True)
        bridge._config_lock.__exit__ = MagicMock(return_value=False)
        bridge._ttrpg_prev_target_speaker_filter = None

        saved_settings = []
        def mock_save(enabled, thresh):
            bridge._config["audio"]["voice_biometrics"]["enabled"] = enabled
            bridge._config["audio"]["voice_biometrics"]["threshold"] = thresh
            saved_settings.append((enabled, thresh))
            return {"success": True}

        bridge.save_voice_biometrics_settings = mock_save
        bridge.set_ttrpg_session_active = GuiBridge.set_ttrpg_session_active.__get__(bridge, GuiBridge)

        # 1. Start adventure: should auto-toggle filter OFF because it was ON
        res_start = bridge.set_ttrpg_session_active(True)
        self.assertTrue(res_start["success"])
        self.assertTrue(res_start["filter_toggled"])
        self.assertFalse(bridge._config["audio"]["voice_biometrics"]["enabled"])
        self.assertTrue(bridge._ttrpg_prev_target_speaker_filter)

        # 2. Stop adventure: should restore filter ON because it was ON prior to the session
        res_stop = bridge.set_ttrpg_session_active(False)
        self.assertTrue(res_stop["success"])
        self.assertTrue(res_stop["filter_restored"])
        self.assertTrue(bridge._config["audio"]["voice_biometrics"]["enabled"])
        self.assertIsNone(bridge._ttrpg_prev_target_speaker_filter)

        # 3. If filter was already OFF when adventure starts:
        bridge._config["audio"]["voice_biometrics"]["enabled"] = False
        res_start2 = bridge.set_ttrpg_session_active(True)
        self.assertTrue(res_start2["success"])
        self.assertFalse(res_start2["filter_toggled"])
        self.assertFalse(bridge._config["audio"]["voice_biometrics"]["enabled"])

        # Stopping should keep it OFF
        res_stop2 = bridge.set_ttrpg_session_active(False)
        self.assertTrue(res_stop2["success"])
        self.assertFalse(res_stop2["filter_restored"])
        self.assertFalse(bridge._config["audio"]["voice_biometrics"]["enabled"])

    def test_smart_scene_extraction_and_query_matching(self):
        mock_module = (
            "CAMPAIGN OVERVIEW\n"
            "Welcome to the Jungle of Chult.\n"
            "TABLE OF CONTENTS\n"
            "Act 1 Scene 1: The Beginning. (Page 4)\n"
            "Act 1 Scene 2: Meeting at the House of Chiefs. (Page 6)\n\n"
            "Camp Points of Interest:\n"
            "1. Johan Petry's House. For the full description, see Act 1 Scene 1: The Beginning.\n"
            "2. House of Chiefs.\n\n"
            "Scene 1: The Beginning\n"
            "The players are invited to Johan Petry's House. Johan Petry (human veteran) is a thin old man "
            "with grey hair and a short grey beard. He has a quest for you: hunt down an elf woman called Imis Elvan "
            "who has fled into the jungle.\n"
            "The party now has four choices:\n"
            "1. Speak with Chief Shandra.\n"
            "2. Speak with Chief Filip at the barracks.\n"
            "3. Gather information from the rogues.\n"
            "4. Track Imis Elvan into the jungle.\n"
        )
        scene_text = TTRPGService._extract_smart_scene(mock_module, "Scene 1: The Beginning")
        self.assertIsNotNone(scene_text)
        self.assertTrue(scene_text.startswith("Scene 1: The Beginning"))
        self.assertIn("Johan Petry (human veteran)", scene_text)
        self.assertIn("Imis Elvan", scene_text)
        self.assertNotIn("TABLE OF CONTENTS", scene_text)

        npc_excerpt = TTRPGService._extract_smart_query(mock_module, "Johan Petry")
        self.assertIn("Johan Petry (human veteran)", npc_excerpt)
        self.assertIn("quest for you", npc_excerpt)

    def test_mode_isolation_agent_game_and_roleplay(self):
        """
        Verifies strict operational mode separation:
        1. Agent Mode: Normal Start Aether with no game running -> Desktop Voice Assistant (No GM context)
        2. Game Mode: Start Aether with video game running (e.g. Crimson Desert) -> Video Game Companion (No GM context)
        3. Role Play Mode: Explicit Start/Resume Adventure -> Tabletop DM Context (GM Grounding, difficulty, roster)
        """
        from unittest.mock import MagicMock
        from core.engine import AetherEngine

        engine = MagicMock(spec=AetherEngine)
        engine._ttrpg_session_active = False
        engine.is_ttrpg_session_active = AetherEngine.is_ttrpg_session_active.__get__(engine, AetherEngine)
        engine.set_ttrpg_session_active = AetherEngine.set_ttrpg_session_active.__get__(engine, AetherEngine)
        engine.is_running = False
        engine._inject_temporal_anchor = lambda x: x
        engine._base_system_instruction = "You are Aether, a fast, knowledgeable desktop voice assistant."
        engine.config_getter = lambda: {"api": {"system_instruction": "You are Aether, a fast, knowledgeable desktop voice assistant.", "voice_accent": "default"}}

        # Set up active campaign in TTRPG service
        engine.ttrpg_service = self.service
        self.service.manifest["active_campaign"] = "test_camp"
        self.service.active_state["campaign_synopsis"] = {
            "module_title": "Elfhunt",
            "setting": "Jungles of Chult",
            "target_or_threat": "Imis Elvan"
        }

        # Set up mock game engine for Crimson Desert
        mock_game_engine = MagicMock()
        mock_game_engine.is_game_mode_active.return_value = False
        mock_game_engine.build_system_instruction.return_value = "You are Aether, a tactical combat and gaming companion for Crimson Desert."
        engine.game_engine = mock_game_engine
        engine.game_mgr = None
        engine.project_service = None

        engine._build_system_instruction = AetherEngine._build_system_instruction.__get__(engine, AetherEngine)

        # SCENARIO 1: AGENT MODE (Start Aether pressed, no game running)
        engine.set_ttrpg_session_active(False)
        mock_game_engine.is_game_mode_active.return_value = False
        inst_agent = engine._build_system_instruction()
        self.assertIn("desktop voice assistant", inst_agent)
        self.assertNotIn("UNIVERSAL TABLETOP ROLEPLAYING ENGINE", inst_agent)
        self.assertNotIn("Jungles of Chult", inst_agent)
        self.assertNotIn("Crimson Desert", inst_agent)

        # SCENARIO 2: GAME MODE (Crimson Desert active)
        engine.set_ttrpg_session_active(False)
        mock_game_engine.is_game_mode_active.return_value = True
        inst_game = engine._build_system_instruction()
        self.assertIn("tactical combat and gaming companion for Crimson Desert", inst_game)
        self.assertNotIn("UNIVERSAL TABLETOP ROLEPLAYING ENGINE", inst_game)
        self.assertNotIn("Jungles of Chult", inst_game)

        # SCENARIO 3: ROLE PLAY MODE (Start / Resume Adventure clicked)
        engine.set_ttrpg_session_active(True)
        self.assertTrue(engine.is_ttrpg_session_active())
        inst_rp = engine._build_system_instruction()
        self.assertIn("UNIVERSAL TABLETOP ROLEPLAYING ENGINE", inst_rp)
        self.assertIn("Jungles of Chult", inst_rp)
        self.assertNotIn("Crimson Desert", inst_rp)

        # SCENARIO 4: Deactivating Role Play returns to Game Mode if game active
        engine.set_ttrpg_session_active(False)
        self.assertFalse(engine.is_ttrpg_session_active())
        inst_restored = engine._build_system_instruction()
        self.assertIn("Crimson Desert", inst_restored)
        self.assertNotIn("UNIVERSAL TABLETOP ROLEPLAYING ENGINE", inst_restored)

    async def test_manage_character_inventory_and_weapons(self):
        # 1. Add weapons and inventory items
        add_res = self.service.manage_character_inventory(
            character_name="Kaelen",
            action="add",
            items=["Health Potion", "50 ft. Silk Rope"],
            weapons=[
                {"name": "Longsword", "atk_bonus": "+5", "damage": "1d8+3", "notes": "Versatile (1d10)"},
                {"name": "Dual Daggers", "atk_bonus": "+4", "damage": "1d2+6", "notes": "Finesse, Light"}
            ],
            reason="Found in treasure chest"
        )
        self.assertEqual(add_res["status"], "success")
        self.assertIn("Health Potion", add_res["inventory"])
        self.assertIn("50 ft. Silk Rope", add_res["inventory"])
        self.assertEqual(len(add_res["weapons"]), 2)
        self.assertEqual(add_res["weapons"][1]["name"], "Dual Daggers")
        self.assertEqual(add_res["weapons"][1]["atk_bonus"], "+4")

        # 2. Verify state persistence on disk
        _, char = self.service._find_character("Kaelen")
        self.assertIn("Health Potion", char["inventory"])

        # 3. Remove an item and weapon
        rem_res = self.service.manage_character_inventory(
            character_name="Kaelen",
            action="remove",
            items=["Health Potion"],
            weapons=[{"name": "Longsword"}],
            reason="Consumed and lost in battle"
        )
        self.assertEqual(rem_res["status"], "success")
        self.assertNotIn("Health Potion", rem_res["inventory"])
        self.assertIn("50 ft. Silk Rope", rem_res["inventory"])
        wpn_names = [w["name"] for w in rem_res["weapons"]]
        self.assertNotIn("Longsword", wpn_names)
        self.assertIn("Dual Daggers", wpn_names)

    async def test_manage_party_inventory_tool_dispatch(self):
        dispatcher = FakeDispatcher()
        register_ttrpg_tools(dispatcher, self.service)
        self.assertIn("manage_party_inventory", dispatcher.tools)

        tool_handler = dispatcher.tools["manage_party_inventory"]["handler"]

        # Call tool to loot gear
        result = await tool_handler(
            character_name="Kaelen",
            action="add",
            items=["Ring of Protection", "Torch x3"],
            weapons=[{"name": "Heavy Crossbow", "atk_bonus": "+3", "damage": "1d10", "notes": "Ammunition, heavy"}],
            reason="Looted from dungeon armory"
        )
        self.assertEqual(result["status"], "success")
        self.assertIn("Ring of Protection", result["inventory"])
        self.assertTrue(any(w["name"] == "Heavy Crossbow" for w in result["weapons"]))

        # Test living entity / captive NPC exclusion
        self.service.active_state["campaign_synopsis"] = {
            "key_npcs": ["Imis Elvan (wood elf illusionist)", "Chief Filip"]
        }
        npc_result = await tool_handler(
            character_name="Kaelen",
            action="add",
            items=["Imis Elvan", "Bound Captive", "Camp Highmore Defense Map"],
            reason="Capturing the target"
        )
        self.assertEqual(npc_result["status"], "success")
        self.assertIn("Camp Highmore Defense Map", npc_result["inventory"])
        self.assertNotIn("Imis Elvan", npc_result["inventory"])
        self.assertNotIn("Bound Captive", npc_result["inventory"])

    def test_full_character_sheet_data_in_system_instruction(self):
        # Update Kaelen with initiative, spells, features, skills
        _, char = self.service._find_character("Kaelen")
        char["initiative"] = "+3"
        char["spells"] = ["Mage Hand", "Shield", "Misty Step"]
        char["features_and_traits"] = ["Sneak Attack (1d6)", "Cunning Action"]
        char["skills"] = {"Stealth": "+7", "Acrobatics": "+5", "Perception": "+3"}
        self.service.save_active_campaign_state()

        instruction = self.service.build_gm_context_prompt()
        self.assertIn("Init: +3", instruction)
        self.assertIn("Mage Hand", instruction)
        self.assertIn("Sneak Attack", instruction)

    def test_reformat_weapons_or_items(self):
        # 1. Test exact bug string from user: comma-prefixed weapon with mod and dice
        res1 = self.service.reformat_weapons_or_items(",Dual Daggers (+4, 1d2 + 6)")
        self.assertEqual(res1["status"], "success")
        self.assertTrue(len(res1["weapons"]) >= 1)
        w1 = res1["weapons"][0]
        self.assertEqual(w1["name"], "Dual Daggers")
        self.assertEqual(w1["atk_bonus"], "+4")
        self.assertIn("1d2", w1["damage"])

        # 2. Test weapon with comma in name and properties
        res2 = self.service.reformat_weapons_or_items("Crossbow, Heavy (+3, 1d10+1 Piercing, Heavy, Loading)")
        self.assertEqual(res2["status"], "success")
        w2 = res2["weapons"][0]
        self.assertIn(w2["name"], ("Crossbow, Heavy", "Heavy Crossbow"))
        self.assertEqual(w2["atk_bonus"], "+3")
        self.assertIn("1d10", w2["damage"])
        self.assertIn("Heavy", w2["notes"])

        # 3. Test mixed loot with items, weapons, and currency
        res3 = self.service.reformat_weapons_or_items("Potion of Healing, 50 ft. Silk Rope, Longsword (+4, 1d8+2 Slashing), 50 GP")
        self.assertEqual(res3["status"], "success")
        self.assertTrue(any(w["name"] == "Longsword" for w in res3["weapons"]))
        self.assertTrue(any("Potion of Healing" in it for it in res3["items"]))
        self.assertEqual(res3["currency"].get("gp"), 50)

    def test_heuristic_reformat_weapons_or_items_offline(self):
        # Deterministic offline heuristic parser tests
        res = self.service._heuristic_reformat_weapons_or_items(",Dual Daggers (+4, 1d2 + 6)")
        self.assertEqual(len(res["weapons"]), 1)
        self.assertEqual(res["weapons"][0]["name"], "Dual Daggers")
        self.assertEqual(res["weapons"][0]["atk_bonus"], "+4")
        self.assertEqual(res["weapons"][0]["damage"], "1d2 + 6")

        res2 = self.service._heuristic_reformat_weapons_or_items("Crossbow, Heavy (+3, 1d10+1 Piercing, Heavy, Loading)")
        self.assertEqual(len(res2["weapons"]), 1)
        self.assertEqual(res2["weapons"][0]["name"], "Crossbow, Heavy")
        self.assertEqual(res2["weapons"][0]["atk_bonus"], "+3")
        self.assertEqual(res2["weapons"][0]["damage"], "1d10+1")
        self.assertIn("Heavy", res2["weapons"][0]["notes"])

    def test_start_or_resume_ttrpg_campaign_prompts(self):
        """
        Verifies that start_or_resume_ttrpg_campaign:
        1. Does not hardcode or bake character names (e.g. (Eric, Sheila)) into the prompt text,
           directing Aether to pull the party dynamically from the roster.
        2. Does not mention specific campaign NPCs like Johan Petry in the module hint.
        3. Correctly differentiates Helpful DM mode for Physical Dice vs Digital Dice:
           - In physical mode: educates players on what physical dice to roll and exact modifiers.
           - In digital mode: rolls digital dice automatically without asking players to roll.
        """
        from unittest.mock import MagicMock
        from core.gui_bridge import GuiBridge

        bridge = MagicMock(spec=GuiBridge)
        bridge._engine = MagicMock()
        bridge._engine.is_running = True
        bridge.set_ttrpg_session_active = MagicMock()

        # Mock ttrpg_service
        bridge.ttrpg_service = MagicMock()
        bridge.ttrpg_service.manifest = {
            "active_campaign": "army_of_the_damned",
            "campaigns": {
                "army_of_the_damned": {
                    "id": "army_of_the_damned",
                    "title": "Army of the Damned",
                    "difficulty": "helpful"
                }
            }
        }
        bridge.ttrpg_service.active_state = {
            "characters": {
                "char_eric": {"name": "Eric"},
                "char_sheila": {"name": "Sheila"}
            },
            "documents": {
                "armyofthedamned.pdf": {"category": "campaign"}
            },
            "journal": [],
            "latest_recap": ""
        }

        sent_messages = []
        def mock_send(ctx, cid, text):
            sent_messages.append((ctx, cid, text))
            return {"status": "success"}

        bridge.send_workspace_message = mock_send

        # Test Case 1: Physical Dice Mode in Helpful difficulty
        bridge.ttrpg_service.get_dice_mode.return_value = "physical"
        GuiBridge.start_or_resume_ttrpg_campaign(bridge)
        self.assertEqual(len(sent_messages), 1)
        prompt1 = sent_messages[-1][2]

        self.assertIn("Army of the Damned", prompt1)
        self.assertIn("Introduce the setting and our characters from the party roster", prompt1)
        self.assertNotIn("(Eric, Sheila)", prompt1)
        self.assertNotIn("Johan Petry", prompt1)
        self.assertIn("armyofthedamned.pdf", prompt1)
        self.assertIn("physical dice", prompt1.lower())
        self.assertIn("educate us on what physical dice to roll with our exact modifiers", prompt1)

        # Test Case 2: Digital Dice Mode in Helpful difficulty
        bridge.ttrpg_service.get_dice_mode.return_value = "digital"
        GuiBridge.start_or_resume_ttrpg_campaign(bridge)
        prompt2 = sent_messages[-1][2]

        self.assertIn("digital dice", prompt2.lower())
        self.assertIn("roll digital dice for us automatically", prompt2)
        self.assertNotIn("tell us what dice to roll with our exact modifiers", prompt2)
        self.assertNotIn("(Eric, Sheila)", prompt2)
        self.assertNotIn("Johan Petry", prompt2)

        # Test Case 3: Resume campaign
        bridge.ttrpg_service.active_state["latest_recap"] = "Escaped the misty forest."
        bridge.ttrpg_service.active_state["current_location"] = "Stensia Valley"
        GuiBridge.start_or_resume_ttrpg_campaign(bridge)
        prompt3 = sent_messages[-1][2]

        self.assertIn("resume our campaign 'Army of the Damned' for our party from the party roster!", prompt3)
        self.assertNotIn("Eric, Sheila", prompt3)
        self.assertIn("Stensia Valley", prompt3)

    def test_add_and_update_character_robustness(self):
        """
        Verifies that add_character and GuiBridge.add_ttrpg_character:
        1. Safely handle None values, formatted strings (+2), and partial payloads without raising TypeError.
        2. Successfully persist updated stats (STR, DEX) and HP (current and max).
        3. Preserve existing character features, inventory, custom meters, and conditions on edit.
        """
        # Create base character
        base_payload = {
            "name": "Thorin",
            "player_name": "Alice",
            "archetype": "Fighter 1",
            "ruleset": "dnd_5e",
            "max_health": 20,
            "current_health": 20,
            "guard": 16,
            "Strength": 16,
            "Dexterity": 12,
            "Constitution": 14,
            "Intelligence": 10,
            "Wisdom": 12,
            "Charisma": 8,
            "weapons": [{"name": "Battleaxe", "damage": "1d8+3"}],
            "inventory": ["Shield", "Rations"],
            "features_and_traits": ["Second Wind"],
            "conditions": ["Blessed"],
            "custom_meters": {"stamina": {"current": 3, "max": 5, "label": "Stamina"}}
        }
        res_add = self.service.add_character(base_payload)
        self.assertEqual(res_add["status"], "success")
        char_id = res_add["character"]["id"]

        # Now simulate editing stats and HP via a form payload that has None values (e.g. from JS NaN -> null)
        edit_payload = {
            "id": char_id,
            "name": "Thorin",
            "player_name": "Alice",
            "ruleset": "dnd_5e",
            "max_health": 28,          # Increased HP
            "current_health": 24,       # Modified current HP
            "guard": 17,               # Increased Guard
            "Strength": 18,            # Increased STR
            "Dexterity": 14,           # Increased DEX
            "level": "+2",             # Formatted string level
            "experience_points": None, # None from JS null/NaN
            "temp_hp": None,
            "custom_meters": None      # Must preserve existing custom meters
        }
        res_edit = self.service.add_character(edit_payload)
        self.assertEqual(res_edit["status"], "success")
        updated = res_edit["character"]

        # Verify stats and HP updated
        self.assertEqual(updated["vitals"]["health"]["max"], 28)
        self.assertEqual(updated["vitals"]["health"]["current"], 24)
        self.assertEqual(updated["armor_class"], 17)
        self.assertEqual(updated["attributes"]["Strength"], 18)
        self.assertEqual(updated["attributes"]["Dexterity"], 14)
        self.assertEqual(updated["level"], 2)
        self.assertEqual(updated["experience_points"], 0)

        # Verify preserved non-overwritten fields
        self.assertEqual(len(updated["weapons"]), 1)
        self.assertEqual(updated["weapons"][0]["name"], "Battleaxe")
        self.assertIn("Shield", updated["inventory"])
        self.assertIn("Second Wind", updated["features_and_traits"])
        self.assertIn("Blessed", updated["conditions"])
        self.assertIn("stamina", updated["custom_meters"])

        # Test GuiBridge.add_ttrpg_character wrapper with error handling
        from unittest.mock import MagicMock
        from core.gui_bridge import GuiBridge
        mock_bridge = MagicMock(spec=GuiBridge)
        mock_bridge.ttrpg_service = self.service
        mock_bridge._on_engine_event = MagicMock()

        bridge_res = GuiBridge.add_ttrpg_character(mock_bridge, edit_payload)
        self.assertEqual(bridge_res["status"], "success")
        mock_bridge._on_engine_event.assert_called_with("ttrpg_state_updated", bridge_res)

    def test_character_sheet_one_and_done_import_and_removal(self):
        """
        Verify that uploaded character sheets are imported into the Party Roster
        and immediately removed from both state['documents'] and disk (files/),
        preserving evolved character stats across campaign reloads and read_campaign_document('all').
        """
        # 1. Create a campaign
        camp_res = self.service.create_campaign(
            title="Grim Hollow Saga",
            system_preset="dnd_5e",
            campaign_id="grim_hollow"
        )
        cid = camp_res["campaign"]["id"]

        # 2. Add an adventure module
        mod_res = self.service.add_campaign_document(
            filename="act1_ruins.md",
            content_bytes=b"# Act 1: The Sunken Ruins\nA gloomy mist envelops the party.",
            doc_category="campaign",
            campaign_id=cid
        )
        self.assertEqual(mod_res["status"], "success")

        # 3. Add a character sheet JSON (or PDF/text)
        sheet_data = {
            "name": "Morgath Stonefist",
            "character_class": "Fighter",
            "level": 1,
            "armor_class": 16,
            "hp_max": 20,
            "hp_current": 20,
            "stats": {"Strength": 16, "Constitution": 14}
        }
        sheet_bytes = json.dumps(sheet_data).encode("utf-8")

        res_upload = self.service.add_campaign_document(
            filename="morgath_sheet.json",
            content_bytes=sheet_bytes,
            doc_category="character_sheet",
            campaign_id=cid
        )

        self.assertEqual(res_upload["status"], "success")
        self.assertTrue(res_upload.get("removed_after_import"))
        self.assertIn("imported", res_upload.get("message", "").lower())
        self.assertIn("removed", res_upload.get("message", "").lower())

        # 4. Verify character is in Party Roster
        state = self.service.active_state
        chars = state.get("characters", {})
        char_obj = next((c for c in chars.values() if c.get("name") == "Morgath Stonefist"), None)
        self.assertIsNotNone(char_obj)
        self.assertEqual(char_obj["name"], "Morgath Stonefist")
        self.assertEqual(char_obj["vitals"]["health"]["max"], 20)
        char_id = char_obj["id"]

        # 5. Verify character sheet is NOT in documents
        self.assertNotIn("morgath_sheet.json", state.get("documents", {}))
        self.assertIn("act1_ruins.md", state.get("documents", {}))

        # 6. Verify file does not exist on disk
        files_dir = os.path.join(self.service.data_dir, cid, "files")
        self.assertFalse(os.path.exists(os.path.join(files_dir, "morgath_sheet.json")))
        self.assertTrue(os.path.exists(os.path.join(files_dir, "act1_ruins.md")))

        # 7. Verify read_campaign_document('all') only returns adventure/rules, not sheet
        all_docs = self.service.read_campaign_document("all", campaign_id=cid)
        self.assertEqual(all_docs["status"], "success")
        self.assertIn("act1_ruins.md", all_docs["content"])
        self.assertNotIn("morgath_sheet.json", all_docs["content"])

        # 8. Evolve character stats (Level 3, HP 35, XP 1800, Blessed condition)
        evolve_payload = {
            "id": char_id,
            "name": "Morgath Stonefist",
            "level": 3,
            "experience_points": 1800,
            "max_health": 35,
            "health": 32,
            "conditions": ["Blessed"],
            "campaign_id": cid
        }
        res_evolve = self.service.add_character(evolve_payload)
        self.assertEqual(res_evolve["status"], "success")

        # 9. Reload campaign state
        self.service.load_active_campaign_state()
        reloaded_state = self.service.active_state

        # 10. Verify evolved character stats remain untouched and NOT wiped
        reloaded_char = reloaded_state["characters"][char_id]
        self.assertEqual(reloaded_char["level"], 3)
        self.assertEqual(reloaded_char["experience_points"], 1800)
        self.assertEqual(reloaded_char["vitals"]["health"]["max"], 35)
        self.assertEqual(reloaded_char["vitals"]["health"]["current"], 32)
        self.assertIn("Blessed", reloaded_char["conditions"])
        self.assertNotIn("morgath_sheet.json", reloaded_state.get("documents", {}))

    def test_module_adherence_levels_and_prompt(self):
        # 1. Create campaign with creative sandbox
        res_create = self.service.create_campaign(
            title="Sandbox Odyssey",
            module_adherence="creative"
        )
        self.assertEqual(res_create["status"], "success")
        cid = res_create["campaign"]["id"]
        self.assertEqual(res_create["campaign"]["module_adherence"], "creative")

        # Mock a synopsis with acts and scenes
        self.service.active_state["campaign_synopsis"] = {
            "module_title": "Sandbox Odyssey",
            "setting": "The Whispering Wilds",
            "primary_hub": "Oakhaven",
            "starting_scene": {"scene_name": "Scene 1: Departure", "location": "Oakhaven Gate"},
            "acts_and_scenes": [
                {
                    "act": "Act 1: The Trail",
                    "scenes": [{"scene_name": "Scene 1: Departure", "location": "Oakhaven Gate"}]
                }
            ]
        }
        self.service.save_active_campaign_state()

        # Prompt should have Level 1 (Creative Sandbox)
        prompt_creative = self.service.build_gm_context_prompt()
        self.assertIn("Level 1 (Creative Sandbox / High GM Freedom)", prompt_creative)
        self.assertIn("[MODULE ADHERENCE LEVEL: 1 / 3 — CREATIVE SANDBOX (HIGH GM FREEDOM)]", prompt_creative)
        self.assertIn("CREATIVE GM FREEDOM", prompt_creative)

        # 2. Update to step 2 (Guided / Balanced)
        res_upd2 = self.service.update_campaign(campaign_id=cid, module_adherence="2")
        self.assertEqual(res_upd2["status"], "success")
        self.assertEqual(res_upd2["campaign"]["module_adherence"], "balanced")

        prompt_balanced = self.service.build_gm_context_prompt()
        self.assertIn("Level 2 (Guided / Balanced Narrative)", prompt_balanced)
        self.assertIn("[MODULE ADHERENCE LEVEL: 2 / 3 — GUIDED & BALANCED NARRATIVE]", prompt_balanced)
        self.assertIn("GUIDED / BALANCED MODULE PACING", prompt_balanced)

        # 3. Update to step 3 (Strict Module)
        res_upd3 = self.service.update_campaign(campaign_id=cid, module_adherence="strict")
        self.assertEqual(res_upd3["status"], "success")
        self.assertEqual(res_upd3["campaign"]["module_adherence"], "strict")

        prompt_strict = self.service.build_gm_context_prompt()
        self.assertIn("Level 3 (Strict Module Fidelity / By the Book)", prompt_strict)
        self.assertIn("[MODULE ADHERENCE LEVEL: 3 / 3 — STRICT MODULE FIDELITY (BY THE BOOK)]", prompt_strict)
        self.assertIn("STRICT MODULE FIDELITY", prompt_strict)
        self.assertIn("SKILL CHECKS NEVER SKIP SCENES, ENCOUNTERS, OR TRAVEL", prompt_strict)

    def test_party_disarm_persistence_and_retrieval(self):
        # Setup characters with weapons
        self.service.active_state["characters"] = {
            "char_1": {
                "name": "Shadow",
                "weapons": [{"name": "Blackrazor", "atk_bonus": "+3", "damage": "2d6 Slashing", "notes": "Two-Handed"}],
                "inventory": ["Lockpicks", "Rope"]
            },
            "char_2": {
                "name": "Joe",
                "weapons": [{"name": "Greatsword", "atk_bonus": "+3", "damage": "2d6", "notes": ""}],
                "inventory": ["Torch"]
            }
        }
        self.service.save_active_campaign_state()

        # 1. Disarm the entire party upon arrest
        disarm_res = self.service.manage_character_inventory(
            character_name="all",
            action="disarm",
            storage_location="Camp Highmore Guard Barracks - Evidence Chest",
            reason="Arrested by Filip and his guards"
        )
        self.assertEqual(disarm_res["status"], "success")
        self.assertEqual(disarm_res["total_stored_items"], 2)

        # Characters should only have Unarmed Strike
        chars = self.service.active_state["characters"]
        self.assertEqual(len(chars["char_1"]["weapons"]), 1)
        self.assertEqual(chars["char_1"]["weapons"][0]["name"], "Unarmed Strike")
        self.assertEqual(len(chars["char_2"]["weapons"]), 1)
        self.assertEqual(chars["char_2"]["weapons"][0]["name"], "Unarmed Strike")

        # Stored items should have 2 entries with location
        stored = self.service.active_state.get("stored_items", [])
        self.assertEqual(len(stored), 2)
        self.assertEqual(stored[0]["storage_location"], "Camp Highmore Guard Barracks - Evidence Chest")
        self.assertEqual(stored[0]["name"], "Blackrazor")
        self.assertEqual(stored[1]["name"], "Greatsword")

        # 2. GM Prompt injection should list confiscated items
        prompt = self.service.build_gm_context_prompt()
        self.assertIn("[CONFISCATED & STORED PARTY ITEMS / WORLD CACHES]", prompt)
        self.assertIn("Camp Highmore Guard Barracks - Evidence Chest", prompt)
        self.assertIn("Shadow's Blackrazor", prompt)
        self.assertIn("Joe's Greatsword", prompt)

        # 3. Retrieve weapons back from storage
        reclaim_res = self.service.manage_character_inventory(
            character_name="all",
            action="retrieve",
            storage_location="Camp Highmore Guard Barracks - Evidence Chest"
        )
        self.assertEqual(reclaim_res["status"], "success")
        self.assertEqual(reclaim_res["remaining_stored_count"], 0)
        self.assertTrue(any(w["name"] == "Blackrazor" for w in chars["char_1"]["weapons"]))
        self.assertTrue(any(w["name"] == "Greatsword" for w in chars["char_2"]["weapons"]))

    def test_auto_summarize_session_turns_jail_fallback(self):
        # Simulate dialogue turns where party surrenders and is placed in cell
        turns = [
            {"role": "user", "parts": [{"text": "Head to the gate"}]},
            {"role": "user", "parts": [{"text": "Go peacefully for now"}]},
            {"role": "model", "parts": [{"text": "You raise your hands and surrender peacefully, deciding not to fight the overwhelming numbers. Filip nods smugly as the guards move in, roughly binding your hands behind your backs. You are marched through the camp, past the curious onlookers, to a squat wooden building serving as a jail. The heavy door closes behind you, and you find yourselves in a small, dimly lit cell, the smell of stale straw hanging in the air. What now?"}]}
        ]

        # Ensure active state exists
        self.service.active_state["current_location"] = "Tane Here's hideout"
        self.service.active_state["latest_recap"] = "Old recap"

        res = self.service.auto_summarize_session_to_journal(turns, session_id="test_session")
        self.assertIsNotNone(res)
        self.assertEqual(res["status"], "success")
        self.assertIn("Cell", self.service.active_state.get("current_location"))
        self.assertIn("jail", self.service.active_state.get("latest_recap").lower())

    def test_npc_psychology_and_social_interaction_directive(self):
        """Verifies that build_gm_context_prompt injects NPC psychology, sensory context, and social check rules."""
        prompt = self.service.build_gm_context_prompt()
        self.assertIn("[NPC PSYCHOLOGY, SENSORY LOG AWARENESS & SOCIAL INTERACTION DIRECTIVE — MANDATORY]", prompt)
        self.assertIn("SURMISE THROUGH THE NPC'S EYES", prompt)
        self.assertIn("ANTI-GM METAGAMING", prompt)
        self.assertIn("MANDATORY SOCIAL CHECKS (WORDS ALONE DO NOT PASS)", prompt)
        self.assertIn("Wary / Hunted / Suspicious NPC", prompt)


if __name__ == "__main__":
    unittest.main()
