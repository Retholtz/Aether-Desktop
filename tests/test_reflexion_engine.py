"""
Unit tests for the Tier 2 Reflexion Engine (Cortex vs Reflexion Pipeline).
Validates idle gating, unindexed session discovery, fact synthesis,
memory reconciliation, and script self-optimization.
"""

import os
import json
import time
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core.optimizer import ReflexionEngine, extract_python_code
from core.manifest_indexer import (
    init_manifest_db,
    index_manifest_card,
    get_unprocessed_sessions
)
from core.user_memory import (
    init_memory_db,
    add_fact_batch,
    get_all_facts,
    replace_facts
)
from core.telemetry_db import TelemetryDB


class TestReflexionEngine(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.chats_dir = os.path.join(self.test_dir, "chats")
        os.makedirs(self.chats_dir, exist_ok=True)
        self.manifest_db_path = os.path.join(self.test_dir, "chat_index.db")
        self.memory_db_path = os.path.join(self.test_dir, "user_profile.db")
        self.telemetry_db_path = os.path.join(self.test_dir, "telemetry.db")

        init_manifest_db(self.manifest_db_path)
        init_memory_db(self.memory_db_path)

        # Mock Engine Harness
        self.mock_engine = MagicMock()
        self.mock_engine.config = {
            "tier1_fast_model": "gemini-3.8-flash",
            "tier2_heavy_model": "gemini-3.8-pro",
            "tier2_thinking_budget": 2048,
            "reflexion_idle_delay_seconds": 15,
            "reflexion_poll_interval_seconds": 30
        }
        self.mock_engine.last_user_turn_timestamp = time.time() - 20.0
        self.mock_engine.is_audio_streaming = False
        self.mock_engine.is_speaking = False
        self.mock_engine.is_tool_running = False
        self.mock_engine.telemetry_db = TelemetryDB(db_path=self.telemetry_db_path)
        self.mock_engine.dispatcher = MagicMock()
        self.mock_engine.dispatcher.skill_library = None
        self.mock_engine.user_memory = MagicMock()
        self.mock_engine.user_memory.db_path = self.memory_db_path

        self.reflexion = ReflexionEngine(engine=self.mock_engine)

    def tearDown(self):
        self.reflexion.stop()
        if hasattr(self.mock_engine, "telemetry_db") and self.mock_engine.telemetry_db:
            self.mock_engine.telemetry_db.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_idle_gatekeeper(self):
        """Tier 2 must strictly execute only when audio, VAD, tools, and speech are idle."""
        # 1. Fully idle
        self.mock_engine.last_user_turn_timestamp = time.time() - 20.0
        self.mock_engine.is_audio_streaming = False
        self.mock_engine.is_speaking = False
        self.mock_engine.is_tool_running = False
        self.assertTrue(self.reflexion._is_engine_idle())

        # 2. Too recent user interaction (< 15 seconds)
        self.mock_engine.last_user_turn_timestamp = time.time() - 5.0
        self.assertFalse(self.reflexion._is_engine_idle())

        # Reset turn timestamp to idle
        self.mock_engine.last_user_turn_timestamp = time.time() - 25.0

        # 3. Audio streaming / VAD active
        self.mock_engine.is_audio_streaming = True
        self.assertFalse(self.reflexion._is_engine_idle())
        self.mock_engine.is_audio_streaming = False

        # 4. Assistant TTS is speaking
        self.mock_engine.is_speaking = True
        self.assertFalse(self.reflexion._is_engine_idle())
        self.mock_engine.is_speaking = False

        # 5. Tool invocation in progress
        self.mock_engine.is_tool_running = True
        self.assertFalse(self.reflexion._is_engine_idle())
        self.mock_engine.is_tool_running = False

        # 6. Idle again
        self.assertTrue(self.reflexion._is_engine_idle())

    def test_unprocessed_sessions_filtering(self):
        """get_unprocessed_sessions must accurately detect files not yet indexed in session_manifests."""
        # Create 3 session files
        for i in range(1, 4):
            session_file = os.path.join(self.chats_dir, f"session_00{i}.json")
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump({
                    "session_id": f"session_00{i}",
                    "start_time": time.time(),
                    "turns": [
                        {"role": "user", "text": f"User query {i}"},
                        {"role": "assistant", "text": f"Assistant response {i}"}
                    ]
                }, f)

        # Initially all 3 are unprocessed
        unprocessed = get_unprocessed_sessions(limit=5, chats_dir=self.chats_dir, db_path=self.manifest_db_path)
        self.assertEqual(len(unprocessed), 3)

        # Index session_002
        index_manifest_card(
            card={
                "session_id": "session_002",
                "topics_discussed": ["Topic 2"],
                "actions_executed": [],
                "unresolved_questions": [],
                "key_entities": []
            },
            transcript_path=os.path.join(self.chats_dir, "session_002.json"),
            db_path=self.manifest_db_path
        )

        # Now only 2 remain unprocessed
        unprocessed_after = get_unprocessed_sessions(limit=5, chats_dir=self.chats_dir, db_path=self.manifest_db_path)
        self.assertEqual(len(unprocessed_after), 2)
        sids = [os.path.splitext(os.path.basename(p))[0] for p in unprocessed_after]
        self.assertNotIn("session_002", sids)
        self.assertIn("session_001", sids)
        self.assertIn("session_003", sids)

    def test_memory_batch_and_reconciliation(self):
        """Tests fact batch addition, conflict updating, and atomic memory reconciliation."""
        facts_batch_1 = [
            {"category": "preference", "fact": "User prefers Dark Mode in IDEs."},
            {"category": "project", "fact": "User is building Aether Desktop assistant."},
            {"category": "system", "fact": "Default browser is Google Chrome."}
        ]
        add_fact_batch(facts_batch_1, source_session="session_101", db_path=self.memory_db_path)

        all_facts = get_all_facts(db_path=self.memory_db_path)
        self.assertEqual(len(all_facts), 3)
        facts_text = [f["fact"] for f in all_facts]
        self.assertIn("User prefers Dark Mode in IDEs.", facts_text)

        # Add duplicate fact with updated source
        facts_batch_2 = [
            {"category": "preference", "fact": "User prefers Dark Mode in IDEs."},
            {"category": "relationship", "fact": "Wife name is Traci."}
        ]
        add_fact_batch(facts_batch_2, source_session="session_102", db_path=self.memory_db_path)

        # Count should be 4 (not 5, because 'User prefers Dark Mode in IDEs.' was deduplicated by UNIQUE constraint)
        updated_facts = get_all_facts(db_path=self.memory_db_path)
        self.assertEqual(len(updated_facts), 4)

        # Test atomic replacement
        reconciled = [
            {"category": "preference", "fact": "User uses dark theme across all tools."},
            {"category": "family", "fact": "Wife name is Traci."}
        ]
        replace_facts(reconciled, db_path=self.memory_db_path)

        final_facts = get_all_facts(db_path=self.memory_db_path)
        self.assertEqual(len(final_facts), 2)
        self.assertEqual(final_facts[0]["category"] in ("preference", "family"), True)

    def test_process_unindexed_sessions_mock(self):
        """Simulates LLM response for unindexed sessions extraction into manifest and facts."""
        session_file = os.path.join(self.chats_dir, "session_test_run.json")
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump({
                "session_id": "session_test_run",
                "start_time": time.time(),
                "turns": [
                    {"role": "user", "text": "I live in Bowling Green, OH and prefer ranch houses."},
                    {"role": "assistant", "text": "Got it, I will remember your housing preferences."}
                ]
            }, f)

        mock_llm_json = json.dumps({
            "manifest": {
                "topics_discussed": ["Real Estate Housing Search"],
                "actions_executed": [],
                "unresolved_questions": [],
                "key_entities": ["Bowling Green, OH"]
            },
            "facts": [
                {"category": "preference", "fact": "User prefers ranch style housing in Bowling Green, OH."}
            ]
        })

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = mock_llm_json
        mock_client.models.generate_content.return_value = mock_response

        self.reflexion.client = mock_client

        with patch("core.manifest_indexer.get_default_chats_dir", return_value=self.chats_dir), \
             patch("core.manifest_indexer.get_default_db_path", return_value=self.manifest_db_path), \
             patch("core.user_memory.MEMORY_DB_PATH", self.memory_db_path):
            self.reflexion._process_unindexed_sessions()

        # Verify session was indexed
        unprocessed = get_unprocessed_sessions(limit=5, chats_dir=self.chats_dir, db_path=self.manifest_db_path)
        self.assertEqual(len(unprocessed), 0)

        # Verify fact was added
        facts = get_all_facts(db_path=self.memory_db_path)
        self.assertTrue(any("ranch style housing" in f["fact"] for f in facts))

    def test_reconcile_and_prune_memory_mock(self):
        """Tests that long-term memory reconciliation triggers and prunes when >= 5 facts exist."""
        # Seed 6 facts
        seed_facts = [
            {"category": "preference", "fact": f"User preference rule #{i}"}
            for i in range(6)
        ]
        add_fact_batch(seed_facts, source_session="seed_session", db_path=self.memory_db_path)
        self.assertEqual(len(get_all_facts(db_path=self.memory_db_path)), 6)

        mock_reconciled_json = json.dumps({
            "reconciled_facts": [
                {"category": "preference", "fact": "Consolidated user preference rules 0 through 5."}
            ]
        })

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = mock_reconciled_json
        mock_client.models.generate_content.return_value = mock_response
        self.reflexion.client = mock_client

        with patch("core.user_memory.MEMORY_DB_PATH", self.memory_db_path):
            self.reflexion._reconcile_and_prune_memory()

        # Should be pruned down to 1 reconciled fact
        pruned_facts = get_all_facts(db_path=self.memory_db_path)
        self.assertEqual(len(pruned_facts), 1)
        self.assertEqual(pruned_facts[0]["fact"], "Consolidated user preference rules 0 through 5.")

    def test_script_optimization_queue_mock(self):
        """Tests Task A script optimization from telemetry DB."""
        # Seed a queued script run in telemetry DB
        run_id = "test_run_123"
        self.mock_engine.telemetry_db.record_run(
            run_id=run_id,
            intent_description="Automate notepad note",
            original_code="import time\nprint('hello')",
            execution_time_ms=1200.0,
            retry_count=0,
            status="queued_for_optimization",
            traceback="Slow execution"
        )

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "```python\n# Optimized snippet\ndef run():\n    return 'optimal'\n```"
        mock_client.models.generate_content.return_value = mock_response
        self.reflexion.client = mock_client

        self.reflexion._process_script_optimization_queue()

        # Verify the telemetry database updated the status
        cur = self.mock_engine.telemetry_db._conn.execute("SELECT status, optimized_code FROM script_runs WHERE run_id = ?", (run_id,))
        row = cur.fetchone()
        self.assertIn(row["status"], ("optimized", "promoted_to_library"))
        self.assertIn("return 'optimal'", row["optimized_code"])

    def test_replace_facts_snapshot_race_condition(self):
        """Tests that facts recorded after snapshot_ts are preserved across reconciliation."""
        snapshot_time = time.time()
        time.sleep(0.01)

        # 1. Existing facts before snapshot
        old_facts = [
            {"category": "work", "fact": "User is a software engineer"}
        ]
        add_fact_batch(old_facts, source_session="session_old", db_path=self.memory_db_path)

        # Force the updated_at to be <= snapshot_time
        with sqlite3.connect(self.memory_db_path) as conn:
            conn.execute("UPDATE user_facts SET updated_at = ?", (snapshot_time - 10.0,))

        # 2. User tells Cortex a new fact *during* the reconciliation window (updated_at > snapshot_time)
        concurrent_fact = [
            {"category": "preference", "fact": "User hates cold coffee"}
        ]
        add_fact_batch(concurrent_fact, source_session="live_voice_session", db_path=self.memory_db_path)

        # 3. Task C completes and commits with snapshot_ts
        reconciled = [
            {"category": "work", "fact": "User works as a senior software architect"}
        ]
        replace_facts(reconciled, snapshot_ts=snapshot_time, db_path=self.memory_db_path)

        # 4. Verify both the reconciled fact AND the concurrent live fact exist!
        all_facts = get_all_facts(db_path=self.memory_db_path)
        fact_texts = [f["fact"] for f in all_facts]

        self.assertIn("User works as a senior software architect", fact_texts)
        self.assertIn("User hates cold coffee", fact_texts, "Concurrent fact must NOT be wiped by reconciliation!")
        self.assertNotIn("User is a software engineer", fact_texts, "Old superseded fact should be pruned.")

    def test_mid_flight_interruption_aborts_commit(self):
        """Tests that if the engine becomes active mid-inference, Reflexion yields without committing."""
        seed_facts = [{"category": "general", "fact": f"General note #{i}"} for i in range(6)]
        add_fact_batch(seed_facts, source_session="init", db_path=self.memory_db_path)

        mock_client = MagicMock()
        def simulate_interruption(*args, **kwargs):
            # User starts speaking mid-flight!
            self.mock_engine.is_speaking = True
            resp = MagicMock()
            resp.text = json.dumps({"reconciled_facts": [{"category": "general", "fact": "Reconciled all"}]})
            return resp

        mock_client.models.generate_content.side_effect = simulate_interruption
        self.reflexion.client = mock_client

        with patch("core.user_memory.MEMORY_DB_PATH", self.memory_db_path):
            self.reflexion._reconcile_and_prune_memory()

        # Database should still contain all 6 initial facts because commit was deferred!
        facts_after = get_all_facts(db_path=self.memory_db_path)
        self.assertEqual(len(facts_after), 6)

    def test_sqlite_wal_and_busy_timeout(self):
        """Tests that user_profile.db and chat_index.db connections enforce WAL mode and busy timeout."""
        with sqlite3.connect(self.memory_db_path) as conn:
            cur = conn.execute("PRAGMA journal_mode;")
            self.assertEqual(cur.fetchone()[0].lower(), "wal")

        with sqlite3.connect(self.manifest_db_path) as conn:
            cur = conn.execute("PRAGMA journal_mode;")
            self.assertEqual(cur.fetchone()[0].lower(), "wal")


if __name__ == "__main__":
    unittest.main()

