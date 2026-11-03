import os
import shutil
import unittest
from unittest.mock import MagicMock, AsyncMock
from core.voice_actor_parser import VoiceActorParser
from core.ttrpg_service import TTRPGService
from core.engine import AetherEngine


class TestVoiceActorParser(unittest.TestCase):
    def setUp(self):
        self.roster = {
            "char_01": {"name": "Kaelen", "archetype": "Frontline Warden"},
            "char_02": {"name": "Vespera", "archetype": "Shadow Scoundrel"},
            "char_03": {"name": "Bram", "archetype": "Arcanist"}
        }
        self.parser = VoiceActorParser(self.roster)

    def test_explicit_character_prefix(self):
        res = self.parser.parse_utterance("Kaelen: I raise my shield to brace the doorway.")
        self.assertEqual(res.actor_name, "Kaelen")
        self.assertEqual(res.character_id, "char_01")
        self.assertEqual(res.clean_action_text, "I raise my shield to brace the doorway.")
        self.assertFalse(res.requires_actor_clarification)

    def test_third_person_declaration(self):
        res = self.parser.parse_utterance("Vespera picks the lock on the rusted grate.")
        self.assertEqual(res.actor_name, "Vespera")
        self.assertEqual(res.character_id, "char_02")
        self.assertIn("Vespera picks the lock", res.clean_action_text)

    def test_combat_auto_lock_attribution(self):
        # In combat, an anonymous attack belongs to the active turn-holder
        res = self.parser.parse_utterance(
            raw_text="I swing my broadsword at the stalker.",
            is_combat_active=True,
            active_combat_char_id="char_01"
        )
        self.assertEqual(res.actor_name, "Kaelen")
        self.assertEqual(res.character_id, "char_01")
        self.assertFalse(res.requires_actor_clarification)

    def test_out_of_combat_anonymous_triggers_clarification(self):
        # Outside combat, an anonymous "I" action requires clarification
        res = self.parser.parse_utterance(
            raw_text="I search the sarcophagus for hidden levers.",
            is_combat_active=False
        )
        self.assertIsNone(res.actor_name)
        self.assertTrue(res.requires_actor_clarification)
        self.assertIn("search the sarcophagus", res.clarification_target_verb)

    def test_clarification_answer_resolution(self):
        # When table replies with just a character name
        res = self.parser.parse_utterance("Bram")
        self.assertEqual(res.actor_name, "Bram")
        self.assertEqual(res.character_id, "char_03")

        # Conversational answer
        res_conv = self.parser.parse_utterance("It's Bram")
        self.assertEqual(res_conv.actor_name, "Bram")
        self.assertEqual(res_conv.character_id, "char_03")

    def test_group_action_detection(self):
        res = self.parser.parse_utterance("We follow the passage deeper underground.")
        self.assertTrue(res.is_group_action)
        self.assertIsNone(res.actor_name)
        self.assertFalse(res.requires_actor_clarification)

    def test_as_prefix_and_gm_direct(self):
        res = self.parser.parse_utterance("Aether, As Kaelen, I prepare my bow.")
        self.assertTrue(res.is_direct_gm_command)
        self.assertEqual(res.actor_name, "Kaelen")
        self.assertEqual(res.character_id, "char_01")
        self.assertEqual(res.clean_action_text, "I prepare my bow.")

    def test_lone_name_variations_with_punctuation(self):
        for phrase in ["Bram.", "It's Bram.", "i am Bram", "I'm Bram"]:
            res = self.parser.parse_utterance(phrase)
            self.assertEqual(res.actor_name, "Bram", f"Failed for {phrase}")
            self.assertEqual(res.character_id, "char_03", f"Failed for {phrase}")

    def test_fallback_to_focused_speaker(self):
        res = self.parser.parse_utterance("Checking the door.", focused_char_id="char_02")
        self.assertEqual(res.actor_name, "Vespera")
        self.assertEqual(res.character_id, "char_02")
        self.assertEqual(res.clean_action_text, "Checking the door.")


class TestTabletopClarificationEngine(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = "tests/test_campaigns_dir_single_mic"
        self.test_manifest = "tests/test_campaigns_single_mic.json"
        self.service = TTRPGService(data_dir=self.test_dir, manifest_path=self.test_manifest)
        self.service.create_campaign(
            title="Iron Reach",
            system_preset="heroic_d20",
            campaign_id="iron_reach"
        )
        self.service.active_state["characters"] = {
            "char_01": {
                "id": "char_01",
                "name": "Kaelen",
                "archetype": "Frontline Warden",
                "vitals": {
                    "health": {"current": 28, "max": 34, "label": "Health"},
                    "guard": {"current": 16, "max": 16, "label": "Guard"}
                },
                "conditions": ["Bleeding"]
            },
            "char_02": {
                "id": "char_02",
                "name": "Vespera",
                "archetype": "Shadow Scoundrel",
                "vitals": {
                    "health": {"current": 22, "max": 22, "label": "Health"},
                    "guard": {"current": 12, "max": 12, "label": "Guard"}
                },
                "conditions": []
            },
            "char_03": {
                "id": "char_03",
                "name": "Bram",
                "archetype": "Arcanist",
                "vitals": {
                    "health": {"current": 18, "max": 18, "label": "Health"},
                    "guard": {"current": 10, "max": 10, "label": "Guard"}
                },
                "conditions": []
            }
        }
        self.service.save_active_campaign_state()

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)
        if os.path.exists(self.test_manifest):
            try:
                os.remove(self.test_manifest)
            except OSError:
                pass

    def test_party_caller_and_focused_speaker(self):
        # 1. Set Party Caller
        caller_res = self.service.set_party_caller("Bram")
        self.assertEqual(caller_res["status"], "success")
        self.assertEqual(caller_res["caller_id"], "char_03")
        self.assertEqual(self.service.active_state["party_caller_id"], "char_03")

        # By ID directly
        caller_res2 = self.service.set_party_caller("char_01")
        self.assertEqual(caller_res2["status"], "success")
        self.assertEqual(caller_res2["caller_id"], "char_01")

        # 2. Set Focused Speaker
        focus_res = self.service.set_focused_speaker("char_02")
        self.assertEqual(focus_res["status"], "success")
        self.assertEqual(focus_res["focused_speaker_id"], "char_02")
        self.assertEqual(self.service.active_state["focused_speaker_id"], "char_02")

        # Unknown character error handling
        err_res = self.service.set_party_caller("UnknownHero")
        self.assertEqual(err_res["status"], "error")

    def test_stash_and_pop_pending_action(self):
        # Initially empty
        self.assertIsNone(self.service.pop_pending_action())

        # Stash an anonymous action
        self.service.stash_pending_action("I search the sarcophagus for hidden traps.", "Who is searching the sarcophagus?")
        stash = self.service.active_state.get("pending_action_stash")
        self.assertIsNotNone(stash)
        self.assertEqual(stash["action_text"], "I search the sarcophagus for hidden traps.")
        self.assertIn("stashed_at", stash)

        # Pop pending action
        popped = self.service.pop_pending_action()
        self.assertEqual(popped, "I search the sarcophagus for hidden traps.")
        self.assertNotIn("pending_action_stash", self.service.active_state)

    def test_generate_scene_hud(self):
        self.service.set_party_caller("Kaelen")
        hud = self.service.generate_scene_hud(active_actor_name="Kaelen")

        self.assertIn("### SCENE HUD (PARTY STATE) ###", hud)
        self.assertIn("Party Caller (Exploration Lead): Kaelen", hud)
        self.assertIn("Current Turn Initiative: Out of Combat", hud)
        self.assertIn("Active Speaking Character: Kaelen", hud)
        self.assertIn("Kaelen [Frontline Warden]: 28/34 HP | Guard: 16 | Conditions: Bleeding", hud)
        self.assertIn("Vespera [Shadow Scoundrel]: 22/22 HP | Guard: 12 | Conditions: Clear", hud)

        # Ensure low-token compactness (<150 words/tokens)
        token_count_approx = len(hud.split())
        self.assertLess(token_count_approx, 150)

    async def test_engine_clarification_state_machine_flow(self):
        engine = AetherEngine()
        engine.active_workspace_type = "campaign"
        engine.ttrpg_service = self.service

        # Mock out chat and notifications
        mock_chat = AsyncMock()
        mock_chat.send_message_async.return_value = MagicMock(text="The sarcophagus contains a gold amulet.")
        engine.chat = mock_chat
        engine.notify = MagicMock()
        engine.speak_tts = AsyncMock()

        # Step 1: Physical player says an anonymous action
        handled1 = await engine._handle_voice_transcript("I search the sarcophagus for hidden traps and poison needles.")
        self.assertTrue(handled1)

        # Verify clarification question was spoken directly without invoking LLM
        engine.speak_tts.assert_called_with("Who wants to search the sarcophagus for?")
        engine.notify.assert_any_call("clarification_requested", {
            "question": "Who wants to search the sarcophagus for?",
            "action": "I search the sarcophagus for hidden traps and poison needles."
        })
        mock_chat.send_message_async.assert_not_called()
        self.assertIsNotNone(self.service.active_state.get("pending_action_stash"))

        # Step 2: Physical table answers with the actor's name: "It's Bram"
        handled2 = await engine._handle_voice_transcript("It's Bram")
        self.assertTrue(handled2)

        # Verify stashed action was popped and turn resolved with Bram
        self.assertIsNone(self.service.active_state.get("pending_action_stash"))
        engine.notify.assert_any_call("speaker_focused", {"character_id": "char_03", "name": "Bram"})
        self.assertEqual(self.service.active_state.get("focused_speaker_id"), "char_03")

        mock_chat.send_message_async.assert_called_once()
        call_prompt = mock_chat.send_message_async.call_args[0][0]
        self.assertIn("Actor: Bram", call_prompt)
        self.assertIn("Declared Action: I search the sarcophagus for hidden traps and poison needles.", call_prompt)
        self.assertIn("### SCENE HUD (PARTY STATE) ###", call_prompt)
        engine.speak_tts.assert_called_with("The sarcophagus contains a gold amulet.")

    async def test_engine_group_action_flow(self):
        engine = AetherEngine()
        engine.active_workspace_type = "campaign"
        engine.ttrpg_service = self.service
        self.service.set_party_caller("Kaelen")

        mock_chat = AsyncMock()
        mock_chat.send_message_async.return_value = MagicMock(text="The party reaches the grand underground hall.")
        engine.chat = mock_chat
        engine.notify = MagicMock()
        engine.speak_tts = AsyncMock()

        handled = await engine._handle_voice_transcript("We move down the corridor.")
        self.assertTrue(handled)

        mock_chat.send_message_async.assert_called_once()
        prompt = mock_chat.send_message_async.call_args[0][0]
        self.assertIn("[GROUP ACTION]", prompt)
        self.assertIn("The party announces: 'We move down the corridor.'", prompt)
        self.assertIn("Address Kaelen as the party caller", prompt)
        engine.speak_tts.assert_called_with("The party reaches the grand underground hall.")

    def test_dm_directives_for_dice_mode_and_anti_redundancy(self):
        # 1. Digital Dice Mode
        self.service.set_dice_mode("digital")
        prompt_digital = self.service.build_gm_context_prompt()
        self.assertIn("[DICE ROLLING MODE: DIGITAL DICE (AUTOMATIC RESOLUTION)]", prompt_digital)
        self.assertIn("CRITICAL RULE — NEVER ASK PLAYERS TO ROLL", prompt_digital)
        self.assertIn("CRITICAL RULE — NO META-NARRATION", prompt_digital)
        self.assertIn("[DIALOGUE STYLE & ANTI-REPETITION DIRECTIVES]", prompt_digital)
        self.assertIn("NO REDUNDANT RECAP", prompt_digital)
        self.assertIn("NO ROBOTIC MENUS", prompt_digital)
        self.assertIn("DIGITAL MODE ACTIVE: Do NOT tell or ask the player to roll dice", prompt_digital)

        # 2. Physical Dice Mode
        self.service.set_dice_mode("physical")
        prompt_physical = self.service.build_gm_context_prompt()
        self.assertIn("[DICE ROLLING MODE: PHYSICAL TABLETOP DICE (PLAYERS ROLL AT THE TABLE)]", prompt_physical)
        self.assertIn("The players are rolling their own physical dice", prompt_physical)
        self.assertIn("Roll 1d20+4 for your attack roll!", prompt_physical)

    def test_scene_hud_includes_active_dice_mode(self):
        self.service.set_dice_mode("digital")
        hud_digital = self.service.generate_scene_hud(active_actor_name="Eric")
        self.assertIn("Active Dice Mode: Digital (Call `roll_dice` automatically; never tell player to roll or say 'Aether rolls')", hud_digital)

        self.service.set_dice_mode("physical")
        hud_physical = self.service.generate_scene_hud(active_actor_name="Eric")
        self.assertIn("Active Dice Mode: Physical (Prompt active player to roll at table)", hud_physical)

    async def test_meta_narration_cleaner_in_engine(self):
        engine = AetherEngine()
        mock_chat = AsyncMock()
        mock_chat.send_message_async.return_value = MagicMock(
            text="Aether rolls for Eric: 18 to hit! The blade slashes across the goblin's defenses."
        )
        engine.chat = mock_chat
        res = await engine._execute_ttrpg_chat_turn("Actor: Eric\nAction: I attack the goblin.")
        # Ensure 'Aether rolls for Eric: ' is completely stripped out
        self.assertEqual(res, "18 to hit! The blade slashes across the goblin's defenses.")

        # Test another variation: "The DM rolls for Eric - "
        mock_chat.send_message_async.return_value = MagicMock(
            text="The DM rolls for Eric - An 11 misses the armored ghoul."
        )
        res2 = await engine._execute_ttrpg_chat_turn("Actor: Eric\nAction: I attack the ghoul.")
        self.assertEqual(res2, "An 11 misses the armored ghoul.")


if __name__ == "__main__":
    unittest.main()
