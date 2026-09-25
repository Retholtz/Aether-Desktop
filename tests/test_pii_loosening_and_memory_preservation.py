"""
Unit tests verifying Specification: PII Loosening, Memory Preservation, and Dynamic Search Fallback.
"""

import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from google.genai import types
from core.engine import get_permissive_safety_settings, RESEARCH_AND_GENEALOGY_DIRECTIVE
from core.optimizer import ReflexionEngine, RECONCILIATION_PROMPT
from core.user_memory import UserMemory, init_memory_db, get_all_facts, replace_facts
from tools.payload_sanitizer import sanitize_tool_result, EXEMPT_TOOLS
from tools.memory_tools import query_user_memory, search_past_sessions
from tools.dispatcher import ToolDispatcher


class TestPIILooseningAndMemoryPreservation(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.data_dir = os.path.join(self.temp_dir.name, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.memory_db = os.path.join(self.data_dir, "user_memory.db")
        self.manifest_db = os.path.join(self.data_dir, "session_manifest.db")
        init_memory_db(self.memory_db)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_permissive_safety_settings_block_only_high(self):
        """1. Verify get_permissive_safety_settings enforces BLOCK_ONLY_HIGH on all 5 HarmCategories."""
        settings = get_permissive_safety_settings()
        self.assertEqual(len(settings), 5)
        expected_categories = {
            types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            types.HarmCategory.HARM_CATEGORY_CIVIC_INTEGRITY,
        }
        actual_categories = {s.category for s in settings}
        self.assertEqual(actual_categories, expected_categories)
        for s in settings:
            self.assertEqual(s.threshold, types.HarmBlockThreshold.BLOCK_ONLY_HIGH)

    def test_research_directive_defined(self):
        """2. Verify RESEARCH_AND_GENEALOGY_DIRECTIVE contains explicit authorization and fallback rules."""
        self.assertIn("PUBLIC RECORDS, BIOGRAPHICAL & GENEALOGICAL RESEARCH DIRECTIVE", RESEARCH_AND_GENEALOGY_DIRECTIVE)
        self.assertIn("Never refuse a request to look up a named individual", RESEARCH_AND_GENEALOGY_DIRECTIVE)
        self.assertIn("immediately perform a live Google Search in the same turn", RESEARCH_AND_GENEALOGY_DIRECTIVE)

    def test_empty_tool_directive_hints(self):
        """3. Verify query_user_memory and search_past_sessions return directive hints on 0 matches."""
        mem = UserMemory(db_path=self.memory_db)

        # Direct tool function: query_user_memory
        mem_res = query_user_memory(search_term="Dr. Nonexistent", memory=mem)
        self.assertEqual(mem_res["status"], "not_found_in_user_profile")
        self.assertEqual(mem_res["count"], 0)
        self.assertIn("directive", mem_res)
        self.assertIn("immediately execute google_search", mem_res["directive"])

        # Direct tool function: search_past_sessions
        sess_res = search_past_sessions(query="Dr. Nonexistent", db_path=self.manifest_db)
        self.assertIn("No previous sessions matched query: 'Dr. Nonexistent'", sess_res)
        self.assertIn("immediately execute google_search", sess_res)

        # Dispatcher integration for query_user_memory & search_past_sessions
        dispatcher = ToolDispatcher()
        dispatcher.user_memory = mem
        disp_mem_res = asyncio.run(dispatcher.dispatch("query_user_memory", {"search_term": "Dr. Nonexistent"}))
        self.assertEqual(disp_mem_res["status"], "not_found_in_user_profile")
        self.assertIn("directive", disp_mem_res)
        self.assertIn("immediately execute google_search", disp_mem_res["directive"])

    def test_reflexion_preserves_family_and_explicit_keys(self):
        """4. Verify family/dates categories and explicit profile keys survive _reconcile_and_prune_memory."""
        self.assertIn('"family"', RECONCILIATION_PROMPT)
        self.assertIn('"dates"', RECONCILIATION_PROMPT)

        mem = UserMemory(db_path=self.memory_db)
        mem.remember_fact("family", "last_name", "Holtz")
        mem.remember_fact("family", "wife_name", "Elena")
        mem.remember_fact("family", "brother_name", "Marcus")
        mem.remember_fact("family", "paternal_grandfather", "Arthur Holtz")
        mem.remember_fact("dates", "wedding_anniversary", "2018-06-15")
        mem.remember_fact("preference", "editor_theme", "Dark Mode")

        # Even if the LLM reconciliation output omits some family facts or returns generic facts,
        # replace_facts and _reconcile_and_prune_memory must preserve family/dates & explicit keys.
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps([
            {"category": "family", "key": "last_name", "fact": "last_name: Holtz"},
            {"category": "preference", "fact": "User prefers Dark Mode editor theme"}
        ])
        mock_client.models.generate_content.return_value = mock_response

        engine = ReflexionEngine(memory_db=self.memory_db)
        engine.client = mock_client
        engine._reconcile_and_prune_memory()

        # Verify safety_settings were passed to Tier 2 generate_content
        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        self.assertIsNotNone(call_kwargs["config"].safety_settings)
        self.assertEqual(len(call_kwargs["config"].safety_settings), 5)

        # Verify all family, dates, and explicit profile keys remain intact
        profile = mem.get_facts(category="family")
        family_keys = {row["key"]: row["value"] for row in profile}
        self.assertEqual(family_keys.get("last_name"), "Holtz")
        self.assertEqual(family_keys.get("wife_name"), "Elena")
        self.assertEqual(family_keys.get("brother_name"), "Marcus")
        self.assertEqual(family_keys.get("paternal_grandfather"), "Arthur Holtz")

        dates_profile = mem.get_facts(category="dates")
        dates_keys = {row["key"]: row["value"] for row in dates_profile}
        self.assertEqual(dates_keys.get("wedding_anniversary"), "2018-06-15")

    def test_payload_sanitizer_exempts_memory_and_profile_tools(self):
        """5. Verify get_user_profile and query_user_memory bypass the 800-char payload truncation limit."""
        self.assertIn("get_user_profile", EXEMPT_TOOLS)
        self.assertIn("query_user_memory", EXEMPT_TOOLS)
        self.assertIn("inspect_screen_context", EXEMPT_TOOLS)

        large_facts = [
            {"category": "family", "key": f"relative_{i}", "value": f"Detailed biographical note #{i} " + ("X" * 80)}
            for i in range(20)
        ]
        large_payload = {
            "status": "success",
            "category": "all",
            "count": len(large_facts),
            "facts": large_facts,
            "result": json.dumps(large_facts)
        }
        self.assertGreater(len(large_payload["result"]), 1500)

        # Exempt tool should return full payload intact without truncation
        sanitized_profile = sanitize_tool_result("get_user_profile", large_payload)
        self.assertNotIn("summary", sanitized_profile)
        self.assertEqual(len(sanitized_profile["facts"]), 20)
        self.assertEqual(sanitized_profile["result"], large_payload["result"])

        sanitized_query = sanitize_tool_result("query_user_memory", large_payload)
        self.assertNotIn("summary", sanitized_query)
        self.assertEqual(len(sanitized_query["facts"]), 20)

        # Non-exempt tool with >800 chars should still be truncated
        sanitized_other = sanitize_tool_result("run_terminal_cmd", large_payload, base_dir=self.temp_dir.name)
        self.assertIn("summary", sanitized_other)
        self.assertIn("TRUNCATED", sanitized_other["summary"])

    def test_pywebview_js_api_reflection_skips_engine_internals(self):
        """6. Verify AetherEngine._serializable is False so pywebview never reflects into engine.hud_window or voice_verifier."""
        from core.engine import AetherEngine
        from core.gui_bridge import GuiBridge

        self.assertFalse(getattr(AetherEngine, "_serializable", True))
        cfg_path = os.path.join(self.temp_dir.name, "config.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({"api": {"agent_name": "Aether"}}, f)

        bridge = GuiBridge(config_path=cfg_path)
        self.assertFalse(getattr(bridge.engine, "_serializable", True))


if __name__ == "__main__":
    unittest.main()
