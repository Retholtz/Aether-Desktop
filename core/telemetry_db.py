"""
Aether Desktop - Telemetry Database & Optimization Queue Store
Persists script execution telemetry, error tracebacks, and queues sub-optimal
scripts for asynchronous background refinement by the Reflexion Engine.
"""

import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Dict, List, Optional, Any

from core.logger import get_logger

logger = get_logger("TelemetryDB")

DEFAULT_DB_REL_PATH = os.path.join("data", "telemetry.db")


class _TelemetryConnection(sqlite3.Connection):
    """SQLite connection subclass that commits and closes cleanly when exiting outer context manager."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ctx_depth = 0

    def __enter__(self):
        self._ctx_depth += 1
        return super().__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._ctx_depth -= 1
        res = super().__exit__(exc_type, exc_val, exc_tb)
        if self._ctx_depth <= 0:
            try:
                self.close()
            except Exception:
                pass
        return res


def get_default_telemetry_db_path() -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, DEFAULT_DB_REL_PATH)


def configure_telemetry_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Opens a thread-safe SQLite connection configured with WAL mode and busy timeout."""
    target_path = db_path or get_default_telemetry_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
    conn = sqlite3.connect(
        target_path,
        check_same_thread=False,
        timeout=10.0,
        factory=_TelemetryConnection
    )
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
    except Exception as e:
        logger.warning(f"[TELEMETRY DB] Could not set WAL/busy_timeout mode: {e}")
    return conn


def init_telemetry_db(db_path: Optional[str] = None):
    """Initializes telemetry database tables including script_runs and script_usage_stats."""
    with configure_telemetry_connection(db_path) as conn:
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS script_usage_stats (
                    script_hash TEXT PRIMARY KEY,
                    script_path TEXT,
                    intent_label TEXT,
                    execution_count INTEGER DEFAULT 1,
                    consecutive_clean_runs INTEGER DEFAULT 1,
                    last_execution_ts REAL,
                    is_promoted INTEGER DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_promoted ON script_usage_stats(is_promoted)")


def record_script_execution(
    script_path: str,
    intent_label: str,
    success: bool,
    returncode: int,
    duration_ms: float,
    db_path: Optional[str] = None
) -> dict:
    """
    Records script run, computes code hash (excluding transient headers/comments),
    and increments the clean execution counter.
    Returns status dictionary indicating if threshold was reached.
    """
    if not script_path or not os.path.exists(script_path):
        return {"should_promote": False}

    with open(script_path, "r", encoding="utf-8") as f:
        code_content = f.read()

    # Normalize code for hashing (strip comments, docstrings, whitespace)
    normalized = "\n".join(
        [line.strip() for line in code_content.splitlines() if line.strip() and not line.strip().startswith("#")]
    )
    script_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    now = time.time()
    should_promote = False

    init_telemetry_db(db_path)
    with configure_telemetry_connection(db_path) as conn:
        with conn:
            cur = conn.execute(
                "SELECT execution_count, consecutive_clean_runs, is_promoted, script_path FROM script_usage_stats WHERE script_hash = ?",
                (script_hash,)
            )
            row = cur.fetchone()

            # If previous recorded script path was deleted, or if marked promoted but removed from library/catalog, reset stats
            if row:
                prev_path = row[3]
                is_prom = row[2]
                if prev_path and not os.path.exists(prev_path):
                    row = None
                elif is_prom:
                    catalog_path = os.path.join("scripts", "skills_catalog.json")
                    still_in_catalog = False
                    if os.path.exists(catalog_path):
                        try:
                            with open(catalog_path, "r", encoding="utf-8") as cf:
                                cat_data = json.load(cf)
                            for entry in cat_data.get("skills", []):
                                if entry.get("hash") == script_hash and entry.get("path") and os.path.exists(entry.get("path")):
                                    still_in_catalog = True
                                    break
                        except Exception:
                            pass
                    if not still_in_catalog:
                        row = None

            if row:
                exec_count, clean_runs, is_promoted = row[0], row[1], row[2]
                if is_promoted:
                    return {
                        "should_promote": False,
                        "is_promoted": True,
                        "script_hash": script_hash,
                        "clean_runs": clean_runs
                    }

                if success and returncode == 0:
                    clean_runs += 1
                else:
                    clean_runs = 0

                exec_count += 1
                conn.execute("""
                    UPDATE script_usage_stats
                    SET execution_count = ?, consecutive_clean_runs = ?, last_execution_ts = ?, script_path = ?, intent_label = ?
                    WHERE script_hash = ?
                """, (exec_count, clean_runs, now, script_path, intent_label, script_hash))

                # Auto-promote threshold: 3 consecutive clean executions
                if clean_runs >= 3 and not is_promoted:
                    should_promote = True
            else:
                clean_runs = 1 if (success and returncode == 0) else 0
                conn.execute("""
                    INSERT OR REPLACE INTO script_usage_stats (script_hash, script_path, intent_label, execution_count, consecutive_clean_runs, last_execution_ts, is_promoted)
                    VALUES (?, ?, ?, 1, ?, ?, 0)
                """, (script_hash, script_path, intent_label, clean_runs, now))

    return {
        "should_promote": should_promote,
        "script_hash": script_hash,
        "clean_runs": clean_runs
    }


def mark_script_as_promoted(script_hash: str, db_path: Optional[str] = None):
    """Marks a script hash as promoted in script_usage_stats."""
    init_telemetry_db(db_path)
    with configure_telemetry_connection(db_path) as conn:
        with conn:
            conn.execute("UPDATE script_usage_stats SET is_promoted = 1 WHERE script_hash = ?", (script_hash,))


class TelemetryDB:
    """Lightweight, thread-safe SQLite database for script telemetry and optimization queuing."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            self.db_path = get_default_telemetry_db_path()
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
                self._conn.execute("PRAGMA busy_timeout=5000;")
            except Exception as e:
                logger.warning(f"[TELEMETRY DB] Could not set WAL mode: {e}")
                logger.warning(f"[TELEMETRY DB] Could not set WAL/busy_timeout mode: {e}")
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
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS script_usage_stats (
                        script_hash TEXT PRIMARY KEY,
                        script_path TEXT,
                        intent_label TEXT,
                        execution_count INTEGER DEFAULT 1,
                        consecutive_clean_runs INTEGER DEFAULT 1,
                        last_execution_ts REAL,
                        is_promoted INTEGER DEFAULT 0
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_promoted ON script_usage_stats(is_promoted)")
            logger.info(f"[TELEMETRY DB] Initialized database at {self.db_path}")

    def record_script_execution(
        self,
        script_path: str,
        intent_label: str,
        success: bool,
        returncode: int,
        duration_ms: float
    ) -> dict:
        """Instance method delegating to record_script_execution against self.db_path."""
        return record_script_execution(
            script_path=script_path,
            intent_label=intent_label,
            success=success,
            returncode=returncode,
            duration_ms=duration_ms,
            db_path=self.db_path
        )

    def mark_script_as_promoted(self, script_hash: str):
        """Instance method delegating to mark_script_as_promoted against self.db_path."""
        mark_script_as_promoted(script_hash=script_hash, db_path=self.db_path)

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

