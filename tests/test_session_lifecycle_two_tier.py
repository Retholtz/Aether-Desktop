"""
Tests for Two-Tier Memory System:
- Domain-aware Game Compactor schema and facets vs Desktop compactor
- Seamless system instruction injection for rotated in-game sessions
- Deep Game State retrieval tools (get_game_scratchpad, search_copilot_log)
- GameEngine immersion directives and tool fencing
"""

import unittest
from unittest.mock import AsyncMock, MagicMock
import os
import shutil
import tempfile
import json

from core.session_lifecycle import (
    SessionLifecycleManager,
    COMPACTOR_PROMPT,
    GAME_COMPACTOR_PROMPT
)
from core.game_engine import GameEngine
from core.game_manager import GameManager
from tools.dispatcher import ToolDispatcher
from tools.game_tools import register_game_tools


class TestTwoTierMemorySystem(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.chats_dir = os.path.join(self.test_dir, "chats")
        self.profiles_path = os.path.join(self.test_dir, "games.json")
        self.lifecycle = SessionLifecycleManager(chats_dir=self.chats_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_deterministic_fallback_desktop(self):
        """When client is None and is_game_mode is False, compactor outputs desktop bullets."""
        self.lifecycle.record_turn("user", "Check my open browser tabs")
        self.lifecycle.record_turn("assistant", "Found Chrome running with 3 tabs")

        summary = await self.lifecycle.generate_session_summary(
            client=None,
            is_game_mode=False
        )
        self.assertIn("User asked: Check my open browser tabs", summary)
        self.assertIn("Assistant performed: Found Chrome running with 3 tabs", summary)
        self.assertNotIn("ACTIVE GAME & UNIVERSE", summary)

    async def test_deterministic_fallback_game_mode(self):
        """When client is None and is_game_mode is True, compactor outputs structured game facets."""
        self.lifecycle.record_turn("user", "Deploy heat sink and plot course to Sol")
        self.lifecycle.record_turn("assistant", "Heat sink deployed. Route plotted to Sol.")

        summary = await self.lifecycle.generate_session_summary(
            client=None,
            is_game_mode=True,
            game_title="Elite Dangerous"
        )
        self.assertIn("ACTIVE GAME & UNIVERSE: Elite Dangerous", summary)
        self.assertIn("PRIMARY OBJECTIVE: In-game co-pilot support", summary)
        self.assertIn("IMMEDIATE SUB-TASK:", summary)
        self.assertIn("LOCATION & VESSEL/STATUS:", summary)
        self.assertIn("KEY RECENT DECISIONS & TACTICAL DIRECTIVES:", summary)
        self.assertIn("NOTABLE DISCOVERIES/INTEL:", summary)

    async def test_llm_compactor_uses_game_prompt_when_in_game_mode(self):
        """Verifies LLM prompt contains GAME_COMPACTOR_PROMPT and game context when is_game_mode=True."""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_part = MagicMock()
        mock_part.text = "- ACTIVE GAME & UNIVERSE: Cyberpunk 2077\n- PRIMARY OBJECTIVE: Meet Hanako at Embers"
        mock_candidate = MagicMock()
        mock_candidate.content.parts = [mock_part]
        mock_resp.candidates = [mock_candidate]
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        self.lifecycle.record_turn("user", "Where are we heading next?")
        self.lifecycle.record_turn("assistant", "Embers, meeting Hanako.")

        summary = await self.lifecycle.generate_session_summary(
            client=mock_client,
            is_game_mode=True,
            game_title="Cyberpunk 2077",
            active_game_context="Location: Heywood"
        )
        self.assertEqual(summary, "- ACTIVE GAME & UNIVERSE: Cyberpunk 2077\n- PRIMARY OBJECTIVE: Meet Hanako at Embers")

        call_args = mock_client.aio.models.generate_content.call_args[1]
        prompt_sent = call_args["contents"]
        self.assertIn("Active Game: Cyberpunk 2077", prompt_sent)
        self.assertIn("Location: Heywood", prompt_sent)
        self.assertIn(GAME_COMPACTOR_PROMPT, prompt_sent)
        self.assertNotIn(COMPACTOR_PROMPT, prompt_sent)

    def test_build_compacted_instruction_framing(self):
        """Verifies proper instruction framing for Game Mode vs Desktop Mode."""
        base = "You are Aether."
        summary = "- ACTIVE GAME & UNIVERSE: Star Citizen\n- PRIMARY OBJECTIVE: Cargo haul"

        # Game Mode
        game_inst = self.lifecycle.build_compacted_instruction(base, summary=summary, is_game_mode=True)
        self.assertIn("ACTIVE IN-GAME TACTICAL STATE & CO-PILOT WORKING MEMORY", game_inst)
        self.assertIn("Stay in-character as the dedicated co-pilot", game_inst)
        self.assertIn(summary, game_inst)

        # Desktop Mode
        desktop_summary = "- User asked to compile code\n- Working on Aether-Desktop"
        desktop_inst = self.lifecycle.build_compacted_instruction(base, summary=desktop_summary, is_game_mode=False)
        self.assertIn("ACTIVE DESKTOP CONTEXT & CONVERSATION STATE", desktop_inst)
        self.assertIn("Seamlessly continue assisting the user without re-introducing yourself", desktop_inst)
        self.assertIn(desktop_summary, desktop_inst)

    def test_tier2_scratchpad_and_copilot_log_tools(self):
        """Verifies get_game_scratchpad and search_copilot_log tools."""
        dispatcher = ToolDispatcher()
        initial_payload = {
            "game_mode_enabled": True,
            "active_profile": "test_game",
            "profiles": {
                "test_game": {
                    "display_name": "Test Space Sim",
                    "process_name": "spacesim.exe",
                    "scratchpad_raw": "Remember to buy fuel at Sector 4\nRepair shields at Outpost Alpha",
                    "scratchpad": {
                        "active_quests": ["Deliver cargo to Outpost Alpha"],
                        "crafting_materials": ["5x Titanium"],
                        "general_notes": ["Bounty on pirate boss"]
                    },
                    "copilot_log": [
                        {
                            "category": "landmark",
                            "summary": "Derelict freighter floating near moon",
                            "location": "Sector 4B",
                            "details": "May have salvage"
                        },
                        {
                            "category": "warning",
                            "summary": "Heavy pirate patrols near asteroid belt",
                            "location": "Belt 9",
                            "details": "Avoid without escort"
                        }
                    ]
                }
            }
        }
        with open(self.profiles_path, "w", encoding="utf-8") as f:
            json.dump(initial_payload, f)

        game_mgr = GameManager(profiles_path=self.profiles_path)
        tools = register_game_tools(dispatcher, game_mgr)

        self.assertIn("get_game_scratchpad", tools)
        self.assertIn("search_copilot_log", tools)

        # 1. Test get_game_scratchpad
        scratch_res = tools["get_game_scratchpad"](category="all")
        self.assertEqual(scratch_res["game"], "Test Space Sim")
        self.assertIn("Remember to buy fuel at Sector 4", scratch_res["scratchpad_raw"])
        self.assertIn("Deliver cargo to Outpost Alpha", scratch_res["structured_notes"]["active_quests"])

        # 2. Test search_copilot_log with category filter
        log_res = tools["search_copilot_log"](category="landmark")
        self.assertEqual(log_res["total_matches"], 1)
        self.assertEqual(log_res["entries"][0]["summary"], "Derelict freighter floating near moon")

        # 3. Test search_copilot_log with keyword query
        pirate_res = tools["search_copilot_log"](query="pirate")
        self.assertEqual(pirate_res["total_matches"], 1)
        self.assertEqual(pirate_res["entries"][0]["location"], "Belt 9")

        # 4. Test get_current_game_telemetry returns scratchpad and recent copilot entries
        telem_res = tools["get_current_game_telemetry"]()
        self.assertIn("scratchpad", telem_res)
        self.assertIn("recent_copilot_observations", telem_res)
        self.assertEqual(len(telem_res["recent_copilot_observations"]), 2)

    def test_game_engine_two_tier_fencing_and_instruction(self):
        """Verifies GameEngine CORE_GAME_TOOL_NAMES includes new tools and instruction guides model."""
        dispatcher = ToolDispatcher()
        with open(self.profiles_path, "w", encoding="utf-8") as f:
            json.dump({
                "game_mode_enabled": True,
                "active_profile": "rpg_game",
                "profiles": {
                    "rpg_game": {
                        "display_name": "Eldritch Realm",
                        "universe_name": "the Eldritch Realm",
                        "process_name": "eldritch.exe",
                        "scratchpad_raw": "",
                        "copilot_log": []
                    }
                }
            }, f)

        engine = GameEngine(dispatcher=dispatcher, profiles_path=self.profiles_path)
        engine.set_game_mode(True, user_explicit=True)
        self.assertIn("get_game_scratchpad", engine.CORE_GAME_TOOL_NAMES)
        self.assertIn("search_copilot_log", engine.CORE_GAME_TOOL_NAMES)

        allowed = engine.get_allowed_tool_names()
        self.assertIn("get_game_scratchpad", allowed)
        self.assertIn("search_copilot_log", allowed)

        instruction = engine.build_system_instruction()
        self.assertIsNotNone(instruction)
        self.assertIn("TWO-TIER MEMORY", instruction)
        self.assertIn("get_game_scratchpad", instruction)
        self.assertIn("search_copilot_log", instruction)
        engine.stop()


if __name__ == "__main__":
    unittest.main()
