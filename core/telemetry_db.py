"""
Aether Desktop - Telemetry Database & Optimization Queue Store
Persists script execution telemetry, error tracebacks, and queues sub-optimal
scripts for asynchronous background refinement by the Reflexion Engine.
"""

import os
import sqlite3
import threading
from typing import Dict, List, Optional, Any

from core.logger import get_logger

logger = get_logger("TelemetryDB")

DEFAULT_DB_REL_PATH = os.path.join("data", "telemetry.db")


class TelemetryDB:
    """Lightweight, thread-safe SQLite database for script telemetry and optimization queuing."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.db_path = os.path.join(base_dir, DEFAULT_DB_REL_PATH)
        else:
            self.db_path = db_path

        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
            self._conn.row_factory = sqlite3.Row
            # Enable Write-Ahead Logging (WAL) for concurrent read/write throughput
            try:
                self._conn.execute("PRAGMA journal_mode=WAL;")
                self._conn.execute("PRAGMA synchronous=NORMAL;")
            except Exception as e:
                logger.warning(f"[TELEMETRY DB] Could not set WAL mode: {e}")
        return self._conn

    def _init_db(self):
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS script_runs (
                        run_id TEXT PRIMARY KEY,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        intent_description TEXT,
                        original_code TEXT,
                        optimized_code TEXT,
                        execution_time_ms REAL,
                        retry_count INTEGER,
                        status TEXT,
                        traceback TEXT,
                        exit_code INTEGER DEFAULT 0,
                        memory_peak_mb REAL DEFAULT 0.0
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_script_runs_status ON script_runs(status);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_script_runs_timestamp ON script_runs(timestamp);")
            logger.info(f"[TELEMETRY DB] Initialized database at {self.db_path}")

    def record_run(
        self,
        run_id: str,
        intent_description: str,
        original_code: str,
        execution_time_ms: float,
        retry_count: int,
        status: str,
        traceback: str = "",
        optimized_code: Optional[str] = None,
        exit_code: int = 0,
        memory_peak_mb: float = 0.0
    ) -> str:
        """
        Inserts or replaces a script run telemetry record in the database.
        Returns the run_id.
        """
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute("""
                    INSERT INTO script_runs (
                        run_id, intent_description, original_code, optimized_code,
                        execution_time_ms, retry_count, status, traceback,
                        exit_code, memory_peak_mb
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET
                        intent_description=excluded.intent_description,
                        original_code=excluded.original_code,
                        optimized_code=COALESCE(excluded.optimized_code, script_runs.optimized_code),
                        execution_time_ms=excluded.execution_time_ms,
                        retry_count=excluded.retry_count,
                        status=excluded.status,
                        traceback=excluded.traceback,
                        exit_code=excluded.exit_code,
                        memory_peak_mb=excluded.memory_peak_mb;
                """, (
                    run_id,
                    intent_description,
                    original_code,
                    optimized_code,
                    float(execution_time_ms),
                    int(retry_count),
                    status,
                    traceback or "",
                    int(exit_code),
                    float(memory_peak_mb)
                ))
            logger.debug(f"[TELEMETRY DB] Recorded run '{run_id}' | Status: {status} | Time: {execution_time_ms:.1f}ms")
            return run_id

    def get_queued_runs(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Retrieves scripts awaiting background optimization, ordered by insertion time."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT run_id, timestamp, intent_description, original_code, optimized_code,
                       execution_time_ms, retry_count, status, traceback, exit_code, memory_peak_mb
                FROM script_runs
                WHERE status = 'queued_for_optimization'
                ORDER BY timestamp ASC
                LIMIT ?;
            """, (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def update_optimized_code(
        self,
        run_id: str,
        optimized_code: str,
        status: str = "optimized"
    ) -> bool:
        """Updates a script record with the refined, optimized source code."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                cursor = conn.execute("""
                    UPDATE script_runs
                    SET optimized_code = ?, status = ?
                    WHERE run_id = ?;
                """, (optimized_code, status, run_id))
                updated = cursor.rowcount > 0
            if updated:
                logger.info(f"[TELEMETRY DB] Updated run '{run_id}' to status '{status}'")
            return updated

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single run by its unique ID."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT run_id, timestamp, intent_description, original_code, optimized_code,
                       execution_time_ms, retry_count, status, traceback, exit_code, memory_peak_mb
                FROM script_runs
                WHERE run_id = ?;
            """, (run_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_recent_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Retrieves recent script runs for monitoring and debugging."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT run_id, timestamp, intent_description, original_code, optimized_code,
                       execution_time_ms, retry_count, status, traceback, exit_code, memory_peak_mb
                FROM script_runs
                ORDER BY timestamp DESC
                LIMIT ?;
            """, (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def close(self):
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

