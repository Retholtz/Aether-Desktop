"""
Aether Desktop - Test Suite: Session Manifest Indexer & Retrieval Engine
Validates raw session JSON transcript persistence, SQLite manifest card indexing,
multi-field search across topics/actions/entities, and Cortex search tool integration.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from core.session_lifecycle import SessionLifecycleManager
from core.manifest_indexer import (
    init_manifest_db,
    index_manifest_card,
    search_manifest_index,
    _extract_and_store_manifest,
    list_stored_sessions,
    get_session_details,
    delete_session_and_transcript,
    delete_all_stored_sessions,
)
from tools.memory_tools import search_past_sessions
from tools.dispatcher import (
    ToolDispatcher,
    SEARCH_PAST_SESSIONS_DECLARATION,
    get_all_tool_declarations,
)


class TestManifestIndexer(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="aether_test_manifest_")
        self.chats_dir = os.path.join(self.test_dir, "chats")
        self.db_path = os.path.join(self.test_dir, "chat_index.db")
        os.makedirs(self.chats_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_session_lifecycle_turn_recording_and_flush(self):
        """Validates that turns and tools are recorded and persisted to JSON according to specification schema."""
        mgr = SessionLifecycleManager(chats_dir=self.chats_dir)
        mgr.record_turn("user", "Check mortgage rates for a 30-year fixed.")
        mgr.record_turn(
            "assistant",
            "Current average 30-year fixed is roughly 6.25%...",
            tools_used=["web_search"]
        )

        self.assertEqual(len(mgr.raw_transcript), 2)
        self.assertEqual(mgr.raw_transcript[0]["turn_id"], 1)
        self.assertEqual(mgr.raw_transcript[0]["role"], "user")
        self.assertEqual(mgr.raw_transcript[0]["text"], "Check mortgage rates for a 30-year fixed.")
        self.assertEqual(mgr.raw_transcript[0]["tools_used"], [])

        self.assertEqual(mgr.raw_transcript[1]["turn_id"], 2)
        self.assertEqual(mgr.raw_transcript[1]["role"], "assistant")
        self.assertEqual(mgr.raw_transcript[1]["tools_used"], ["web_search"])

        flushed_id = mgr.flush_session_to_disk()
        self.assertIsNotNone(flushed_id)

        # Verify JSON file on disk
        target_json = os.path.join(self.chats_dir, f"{flushed_id}.json")
        self.assertTrue(os.path.exists(target_json))

        with open(target_json, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["session_id"], flushed_id)
        self.assertIn("start_time", data)
        self.assertIn("end_time", data)
        self.assertEqual(len(data["turns"]), 2)
        self.assertEqual(data["turns"][1]["tools_used"], ["web_search"])

        # Raw transcript in memory must be reset for next session
        self.assertEqual(len(mgr.raw_transcript), 0)
        self.assertNotEqual(mgr.session_id, flushed_id)

    def test_empty_session_flush_returns_none(self):
        """Flushing an empty session should return None and not write any file."""
        mgr = SessionLifecycleManager(chats_dir=self.chats_dir)
        res = mgr.flush_session_to_disk()
        self.assertIsNone(res)
        files = os.listdir(self.chats_dir)
        self.assertEqual(len(files), 0)

    def test_manifest_db_indexing_and_search(self):
        """Validates SQLite table initialization, indexing, and multi-field substring queries."""
        init_manifest_db(self.db_path)

        card1 = {
            "session_id": "session_20260922_170000",
            "date": "2026-09-22",
            "topics_discussed": ["mortgage rates", "refinancing options"],
            "actions_executed": ["searched financial rates", "compared Fannie Mae benchmarks"],
            "unresolved_questions": ["user asked to recheck next Tuesday"],
            "key_entities": ["30-year fixed", "Fannie Mae"],
            "timestamp": time.time() - 100
        }
        card2 = {
            "session_id": "session_20260922_180000",
            "date": "2026-09-22",
            "topics_discussed": ["python automation", "powershell scripting"],
            "actions_executed": ["created desktop shortcut", "opened vscode"],
            "unresolved_questions": [],
            "key_entities": ["VSCode", "PowerShell"],
            "timestamp": time.time()
        }

        index_manifest_card(card1, "/dummy/path/1.json", db_path=self.db_path)
        index_manifest_card(card2, "/dummy/path/2.json", db_path=self.db_path)

        # Search by topic
        res_topic = search_manifest_index("mortgage", db_path=self.db_path)
        self.assertEqual(len(res_topic), 1)
        self.assertEqual(res_topic[0]["session_id"], "session_20260922_170000")
        self.assertIn("mortgage rates", res_topic[0]["topics"])

        # Search by entity (case-insensitive)
        res_entity = search_manifest_index("fannie mae", db_path=self.db_path)
        self.assertEqual(len(res_entity), 1)
        self.assertEqual(res_entity[0]["session_id"], "session_20260922_170000")

        # Search by action
        res_action = search_manifest_index("vscode", db_path=self.db_path)
        self.assertEqual(len(res_action), 1)
        self.assertEqual(res_action[0]["session_id"], "session_20260922_180000")

        # Search by unresolved question
        res_unresolved = search_manifest_index("recheck next Tuesday", db_path=self.db_path)
        self.assertEqual(len(res_unresolved), 1)
        self.assertEqual(res_unresolved[0]["session_id"], "session_20260922_170000")

        # Non-matching search
        res_none = search_manifest_index("quantum physics", db_path=self.db_path)
        self.assertEqual(len(res_none), 0)

        # Empty search
        self.assertEqual(search_manifest_index("", db_path=self.db_path), [])

    def test_search_past_sessions_tool_formatter(self):
        """Tests the formatted markdown string output of search_past_sessions."""
        init_manifest_db(self.db_path)
        card = {
            "session_id": "session_20260922_190000",
            "date": "2026-09-22",
            "topics_discussed": ["cloud architecture"],
            "actions_executed": ["reviewed terraform config"],
            "unresolved_questions": ["pending security review"],
            "key_entities": ["AWS", "Terraform"],
            "timestamp": time.time()
        }
        index_manifest_card(card, "/dummy/session.json", db_path=self.db_path)

        # Found query
        output = search_past_sessions(query="terraform", limit=4, db_path=self.db_path)
        self.assertIn("session_20260922_190000", output)
        self.assertIn("Topics: cloud architecture", output)
        self.assertIn("Actions: reviewed terraform config", output)
        self.assertIn("Unresolved: pending security review", output)
        self.assertIn("Entities: AWS, Terraform", output)

        # Not found query
        not_found = search_past_sessions(query="nonexistent_concept", limit=4, db_path=self.db_path)
        self.assertIn("No previous sessions matched query: 'nonexistent_concept'", not_found)

    def test_dispatcher_integration(self):
        """Validates that search_past_sessions is registered in declarations and handled by ToolDispatcher."""
        declarations = get_all_tool_declarations()
        decl_names = [d["name"] for d in declarations]
        self.assertIn("search_past_sessions", decl_names)

        # Test declaration schema
        self.assertEqual(SEARCH_PAST_SESSIONS_DECLARATION["name"], "search_past_sessions")
        self.assertIn("query", SEARCH_PAST_SESSIONS_DECLARATION["parameters"]["required"])

        # Test dispatch execution
        dispatcher = ToolDispatcher()
        init_manifest_db(self.db_path)

        with patch("tools.memory_tools.search_past_sessions") as mock_search:
            mock_search.return_value = "- [2026-09-22] Session test_123: Topics: test"
            import asyncio
            result = asyncio.run(dispatcher.dispatch("search_past_sessions", {"query": "mortgage"}))
            self.assertEqual(result["status"], "success")
            self.assertIn("Session test_123", result["result"])

    def test_extract_and_store_manifest_with_mock_llm(self):
        """Tests end-to-end manifest card extraction from a transcript using a mocked Gemini client."""
        # Create sample transcript file
        session_file = os.path.join(self.chats_dir, "test_session.json")
        sample_transcript = {
            "session_id": "test_session",
            "start_time": 1790096400.0,
            "end_time": 1790100000.0,
            "turns": [
                {
                    "turn_id": 1,
                    "timestamp": 1790096405.0,
                    "role": "user",
                    "text": "Can we look up the latest tax deadlines for LLC filing?"
                },
                {
                    "turn_id": 2,
                    "timestamp": 1790096410.0,
                    "role": "assistant",
                    "text": "For calendar year LLCs, the federal filing deadline is March 15th.",
                    "tools_used": ["web_search"]
                }
            ]
        }
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(sample_transcript, f)

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "topics_discussed": ["tax deadlines", "LLC filing"],
            "actions_executed": ["searched federal deadlines"],
            "unresolved_questions": [],
            "key_entities": ["LLC", "IRS", "March 15"]
        })
        mock_client.models.generate_content.return_value = mock_response

        _extract_and_store_manifest(
            transcript_path=session_file,
            client=mock_client,
            db_path=self.db_path
        )

        results = search_manifest_index("tax deadlines", db_path=self.db_path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["session_id"], "test_session")
        self.assertIn("tax deadlines", results[0]["topics"])
        self.assertIn("LLC", results[0]["entities"])

    def test_reset_metrics_flushes_unpersisted_turns(self):
        """Validates that reset_metrics automatically flushes transcript turns before wiping metrics."""
        mgr = SessionLifecycleManager(chats_dir=self.chats_dir)
        mgr.record_turn("user", "Hello Aether")
        mgr.record_turn("assistant", "Hello! How can I help today?")

        self.assertEqual(len(mgr.raw_transcript), 2)
        mgr.reset_metrics()

        # Should have flushed to disk
        self.assertEqual(len(mgr.raw_transcript), 0)
        files = os.listdir(self.chats_dir)
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].endswith(".json"))

    def test_list_stored_sessions_and_details(self):
        """Validates listing stored sessions on disk merged with SQLite manifest records."""
        init_manifest_db(self.db_path)

        # Create two sample session files
        s1_path = os.path.join(self.chats_dir, "session_20260922_100000.json")
        with open(s1_path, "w", encoding="utf-8") as f:
            json.dump({
                "session_id": "session_20260922_100000",
                "start_time": 1000.0,
                "end_time": 1050.0,
                "turns": [
                    {"turn_id": 1, "timestamp": 1000.0, "role": "user", "text": "What is quantum computing?"},
                    {"turn_id": 2, "timestamp": 1050.0, "role": "assistant", "text": "Quantum computing uses qubits..."}
                ]
            }, f)

        s2_path = os.path.join(self.chats_dir, "session_20260922_110000.json")
        with open(s2_path, "w", encoding="utf-8") as f:
            json.dump({
                "session_id": "session_20260922_110000",
                "start_time": 2000.0,
                "end_time": 2010.0,
                "turns": [
                    {"turn_id": 1, "timestamp": 2000.0, "role": "user", "text": "Write a python script"}
                ]
            }, f)

        # Index manifest for s1 only
        index_manifest_card({
            "session_id": "session_20260922_100000",
            "date": "2026-09-22",
            "topics_discussed": ["quantum computing", "qubits"],
            "actions_executed": ["explained physics"],
            "unresolved_questions": [],
            "key_entities": ["qubits", "superposition"],
            "timestamp": 1000.0
        }, transcript_path=s1_path, db_path=self.db_path)

        # 1. List sessions
        sessions = list_stored_sessions(chats_dir=self.chats_dir, db_path=self.db_path)
        self.assertEqual(len(sessions), 2)
        # Verify descending order by start_time (s2 then s1)
        self.assertEqual(sessions[0]["session_id"], "session_20260922_110000")
        self.assertEqual(sessions[1]["session_id"], "session_20260922_100000")
        self.assertTrue(sessions[1]["has_manifest"])
        self.assertFalse(sessions[0]["has_manifest"])
        self.assertIn("quantum computing", sessions[1]["topics"])
        self.assertEqual(sessions[1]["turn_count"], 2)

        # 2. Get session details for s1
        detail = get_session_details("session_20260922_100000", chats_dir=self.chats_dir, db_path=self.db_path)
        self.assertEqual(detail["status"], "success")
        self.assertEqual(detail["session_id"], "session_20260922_100000")
        self.assertEqual(detail["turn_count"], 2)
        self.assertIsNotNone(detail["manifest"])
        self.assertEqual(detail["manifest"]["topics"], ["quantum computing", "qubits"])

        # 3. Nonexistent session details
        not_found = get_session_details("nonexistent_session", chats_dir=self.chats_dir, db_path=self.db_path)
        self.assertEqual(not_found["status"], "error")

    def test_delete_stored_session_and_clear_all(self):
        """Validates deletion of individual session files + index, and bulk purging."""
        init_manifest_db(self.db_path)

        s1_path = os.path.join(self.chats_dir, "session_del_1.json")
        with open(s1_path, "w", encoding="utf-8") as f:
            json.dump({
                "session_id": "session_del_1",
                "start_time": 100.0,
                "end_time": 110.0,
                "turns": [{"role": "user", "text": "Test 1"}]
            }, f)

        index_manifest_card({
            "session_id": "session_del_1",
            "date": "2026-09-22",
            "topics_discussed": ["deletion alpha"],
            "timestamp": 100.0
        }, transcript_path=s1_path, db_path=self.db_path)

        s2_path = os.path.join(self.chats_dir, "session_del_2.json")
        with open(s2_path, "w", encoding="utf-8") as f:
            json.dump({
                "session_id": "session_del_2",
                "start_time": 200.0,
                "end_time": 210.0,
                "turns": [{"role": "user", "text": "Test 2"}]
            }, f)

        index_manifest_card({
            "session_id": "session_del_2",
            "date": "2026-09-22",
            "topics_discussed": ["deletion beta"],
            "timestamp": 200.0
        }, transcript_path=s2_path, db_path=self.db_path)

        self.assertTrue(os.path.exists(s1_path))
        self.assertTrue(os.path.exists(s2_path))

        # Delete session 1
        res1 = delete_session_and_transcript("session_del_1", chats_dir=self.chats_dir, db_path=self.db_path)
        self.assertEqual(res1["status"], "success")
        self.assertFalse(os.path.exists(s1_path))
        self.assertEqual(len(search_manifest_index("deletion alpha", db_path=self.db_path)), 0)
        self.assertEqual(len(search_manifest_index("deletion beta", db_path=self.db_path)), 1)
        self.assertEqual(len(list_stored_sessions(chats_dir=self.chats_dir, db_path=self.db_path)), 1)

        # Delete nonexistent session returns not_found
        res_none = delete_session_and_transcript("session_del_1", chats_dir=self.chats_dir, db_path=self.db_path)
        self.assertEqual(res_none["status"], "not_found")

        # Clear all
        res_all = delete_all_stored_sessions(chats_dir=self.chats_dir, db_path=self.db_path)
        self.assertEqual(res_all["status"], "success")
        self.assertFalse(os.path.exists(s2_path))
        self.assertEqual(len(list_stored_sessions(chats_dir=self.chats_dir, db_path=self.db_path)), 0)

    def test_gui_bridge_rpc_methods(self):
        """Validates GuiBridge RPC wrappers for stored session browsing, querying, and deletion."""
        from core.gui_bridge import GuiBridge

        mock_engine = MagicMock()
        mock_engine.user_memory = MagicMock()

        # Instantiate GuiBridge with a dummy config
        dummy_cfg_path = os.path.join(self.test_dir, "config.json")
        with open(dummy_cfg_path, "w", encoding="utf-8") as f:
            json.dump({"audio": {}, "ui": {}}, f)

        with patch("core.gui_bridge.AetherEngine", return_value=mock_engine), \
             patch("core.gui_bridge.register_ui_log_callback"):
            bridge = GuiBridge(config_path=dummy_cfg_path)

        # Mock core.manifest_indexer calls
        sample_sessions = [
            {
                "session_id": "session_20260922_01",
                "preview": "Tell me about mortgages",
                "topics": ["mortgages"],
                "actions": ["rate calculation"],
                "entities": ["30-year fixed"]
            },
            {
                "session_id": "session_20260922_02",
                "preview": "How to optimize python code",
                "topics": ["python", "optimization"],
                "actions": ["benchmarking"],
                "entities": ["cProfile"]
            }
        ]

        with patch("core.manifest_indexer.list_stored_sessions", return_value=sample_sessions):
            # All sessions
            res_all = bridge.get_stored_sessions()
            self.assertTrue(res_all["success"])
            self.assertEqual(res_all["total"], 2)

            # Filtered by topic query
            res_filtered = bridge.get_stored_sessions("mortgages")
            self.assertTrue(res_filtered["success"])
            self.assertEqual(len(res_filtered["sessions"]), 1)
            self.assertEqual(res_filtered["sessions"][0]["session_id"], "session_20260922_01")

        with patch("core.manifest_indexer.get_session_details", return_value={"status": "success", "session_id": "s1"}):
            res_det = bridge.get_session_transcript("s1")
            self.assertTrue(res_det["success"])
            self.assertEqual(res_det["session"]["session_id"], "s1")

        with patch("core.manifest_indexer.delete_session_and_transcript", return_value={"status": "success"}):
            res_del = bridge.delete_stored_session("s1")
            self.assertTrue(res_del["success"])

        with patch("core.manifest_indexer.delete_all_stored_sessions", return_value={"status": "success", "deleted_files": 2}):
            res_clear = bridge.clear_all_stored_sessions()
            self.assertTrue(res_clear["success"])
            self.assertEqual(res_clear["result"]["deleted_files"], 2)


if __name__ == "__main__":
    unittest.main()

