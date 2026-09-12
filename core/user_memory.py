"""
Aether Desktop - Dynamic User Knowledge Store & Preferences
Manages personalized user facts, toggleable proactive notifications,
and alert deduplication history in data/user_profile.db.
"""

import datetime
import json
import os
import sqlite3
import threading
from typing import Dict, List, Optional, Any

from core.logger import get_logger

logger = get_logger("UserMemory")

DEFAULT_DB_REL_PATH = os.path.join("data", "user_profile.db")


class UserMemory:
    """Thread-safe SQLite store for arbitrary user facts, notification preferences, and proactive prompt history."""

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
            try:
                self._conn.execute("PRAGMA journal_mode=WAL;")
                self._conn.execute("PRAGMA synchronous=NORMAL;")
            except Exception as e:
                logger.warning(f"[USER MEMORY] Could not set WAL mode: {e}")
        return self._conn

    def _init_db(self):
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS user_facts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        category TEXT NOT NULL,
                        key TEXT NOT NULL,
                        value TEXT NOT NULL,
                        data_type TEXT DEFAULT 'string',
                        last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(category, key)
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_category ON user_facts(category);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_key ON user_facts(key);")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS notification_preferences (
                        category TEXT PRIMARY KEY,
                        enabled INTEGER DEFAULT 1,
                        lead_time_days INTEGER DEFAULT 7
                    );
                """)

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS prompt_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        fact_id INTEGER,
                        prompt_text TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_history_fact ON prompt_history(fact_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_history_time ON prompt_history(timestamp);")

                # Seed default notification categories
                default_categories = [
                    ('dates', 1, 7),
                    ('interests', 1, 7),
                    ('work', 1, 7),
                    ('general', 1, 7),
                ]
                for cat, en, lead in default_categories:
                    conn.execute("""
                        INSERT OR IGNORE INTO notification_preferences (category, enabled, lead_time_days)
                        VALUES (?, ?, ?);
                    """, (cat, en, lead))

    def remember_fact(self, category: str, key: str, value: str, data_type: str = "string") -> Dict[str, Any]:
        """Inserts or updates a user fact into user_facts."""
        cat_clean = category.strip().lower() or "general"
        key_clean = key.strip().lower().replace(" ", "_")
        val_clean = str(value).strip()
        type_clean = (data_type or "string").strip().lower()

        with self._lock:
            conn = self._get_connection()
            with conn:
                cursor = conn.execute("""
                    INSERT INTO user_facts (category, key, value, data_type, last_updated)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(category, key) DO UPDATE SET
                        value = excluded.value,
                        data_type = excluded.data_type,
                        last_updated = CURRENT_TIMESTAMP;
                """, (cat_clean, key_clean, val_clean, type_clean))
                fact_id = cursor.lastrowid

            if not fact_id or cursor.rowcount == 0:
                row = conn.execute("SELECT id FROM user_facts WHERE category = ? AND key = ?", (cat_clean, key_clean)).fetchone()
                fact_id = row["id"] if row else 0

        logger.info(f"[USER MEMORY] Stored fact [{cat_clean}] {key_clean} = '{val_clean}' ({type_clean})")
        return {
            "status": "success",
            "id": fact_id,
            "category": cat_clean,
            "key": key_clean,
            "value": val_clean,
            "data_type": type_clean,
            "message": f"Remembered: [{cat_clean}] {key_clean} = {val_clean}"
        }

    def forget_fact(self, category: str, key: str) -> Dict[str, Any]:
        """Removes the specified key from user_facts."""
        cat_clean = category.strip().lower()
        key_clean = key.strip().lower().replace(" ", "_")

        with self._lock:
            conn = self._get_connection()
            with conn:
                cursor = conn.execute("""
                    DELETE FROM user_facts WHERE category = ? AND key = ?;
                """, (cat_clean, key_clean))
                deleted = cursor.rowcount > 0

        logger.info(f"[USER MEMORY] Forgot fact [{cat_clean}] {key_clean} (deleted={deleted})")
        return {
            "status": "success" if deleted else "not_found",
            "deleted": deleted,
            "category": cat_clean,
            "key": key_clean,
            "message": f"Forgot fact [{cat_clean}] {key_clean}" if deleted else f"Fact [{cat_clean}] {key_clean} was not found."
        }

    def delete_fact_by_id(self, fact_id: int) -> Dict[str, Any]:
        """Removes a fact by its integer primary key."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                cursor = conn.execute("DELETE FROM user_facts WHERE id = ?;", (fact_id,))
                deleted = cursor.rowcount > 0

        return {"status": "success" if deleted else "not_found", "deleted": deleted}

    def get_facts(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns list of stored facts, optionally filtered by category."""
        with self._lock:
            conn = self._get_connection()
            if category and category.strip():
                rows = conn.execute("""
                    SELECT id, category, key, value, data_type, last_updated
                    FROM user_facts
                    WHERE category = ?
                    ORDER BY category ASC, key ASC;
                """, (category.strip().lower(),)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT id, category, key, value, data_type, last_updated
                    FROM user_facts
                    ORDER BY category ASC, key ASC;
                """).fetchall()

        return [dict(r) for r in rows]

    def query_facts(
        self,
        search_term: str,
        category: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Performs an indexed text search (LIKE %search_term%) against user_facts.
        Searches key, value, and category, and returns a compact list of matching
        key-value pairs up to the specified limit (default 5).
        """
        clean_term = search_term.strip().lower()
        if not clean_term:
            return []

        pattern = f"%{clean_term}%"
        with self._lock:
            conn = self._get_connection()
            if category and category.strip():
                cat_clean = category.strip().lower()
                rows = conn.execute("""
                    SELECT category, key, value, data_type
                    FROM user_facts
                    WHERE category = ? AND (key LIKE ? OR value LIKE ?)
                    ORDER BY last_updated DESC
                    LIMIT ?;
                """, (cat_clean, pattern, pattern, max(1, limit))).fetchall()
            else:
                rows = conn.execute("""
                    SELECT category, key, value, data_type
                    FROM user_facts
                    WHERE key LIKE ? OR value LIKE ? OR category LIKE ?
                    ORDER BY last_updated DESC
                    LIMIT ?;
                """, (pattern, pattern, pattern, max(1, limit))).fetchall()

        return [
            {
                "category": r["category"],
                "key": r["key"],
                "value": r["value"]
            }
            for r in rows
        ]

    def get_preferences(self) -> List[Dict[str, Any]]:
        """Returns all notification preference rows."""
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute("""
                SELECT category, enabled, lead_time_days
                FROM notification_preferences
                ORDER BY category ASC;
            """).fetchall()
        return [dict(r) for r in rows]

    def update_preference(self, category: str, enabled: int, lead_time_days: Optional[int] = None) -> Dict[str, Any]:
        """Updates notification toggle and optional lead time for a category."""
        cat_clean = category.strip().lower()
        en_val = 1 if enabled else 0

        with self._lock:
            conn = self._get_connection()
            with conn:
                if lead_time_days is not None:
                    conn.execute("""
                        INSERT INTO notification_preferences (category, enabled, lead_time_days)
                        VALUES (?, ?, ?)
                        ON CONFLICT(category) DO UPDATE SET
                            enabled = excluded.enabled,
                            lead_time_days = excluded.lead_time_days;
                    """, (cat_clean, en_val, int(lead_time_days)))
                else:
                    conn.execute("""
                        INSERT INTO notification_preferences (category, enabled, lead_time_days)
                        VALUES (?, ?, 7)
                        ON CONFLICT(category) DO UPDATE SET
                            enabled = excluded.enabled;
                    """, (cat_clean, en_val))

        logger.info(f"[USER MEMORY] Updated preference [{cat_clean}]: enabled={en_val}, lead_time={lead_time_days}")
        return {"status": "success", "category": cat_clean, "enabled": en_val}

    def has_prompt_fired_today(self, fact_id: Optional[int] = None, prompt_text: Optional[str] = None) -> bool:
        """Checks if an alert for this fact or text was already logged today."""
        with self._lock:
            conn = self._get_connection()
            if fact_id is not None:
                row = conn.execute("""
                    SELECT id FROM prompt_history
                    WHERE fact_id = ? AND date(timestamp, 'localtime') = date('now', 'localtime')
                    LIMIT 1;
                """, (fact_id,)).fetchone()
                if row:
                    return True

            if prompt_text:
                row = conn.execute("""
                    SELECT id FROM prompt_history
                    WHERE prompt_text = ? AND date(timestamp, 'localtime') = date('now', 'localtime')
                    LIMIT 1;
                """, (prompt_text.strip(),)).fetchone()
                if row:
                    return True

        return False

    def record_prompt(self, fact_id: Optional[int], prompt_text: str) -> int:
        """Records a fired proactive prompt in prompt_history to prevent same-day duplicates."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                cursor = conn.execute("""
                    INSERT INTO prompt_history (fact_id, prompt_text, timestamp)
                    VALUES (?, ?, CURRENT_TIMESTAMP);
                """, (fact_id, prompt_text.strip()))
                return cursor.lastrowid or 0

    def get_memory_summary(self, max_facts: int = 50) -> str:
        """Builds a compact bulleted summary of stored user facts grouped by category for LLM injection."""
        facts = self.get_facts()
        if not facts:
            return "No specific user profile facts recorded yet."

        by_category: Dict[str, List[str]] = {}
        for f in facts[:max_facts]:
            cat = f['category'].capitalize()
            k = f['key'].replace('_', ' ')
            v = f['value']
            by_category.setdefault(cat, []).append(f"{k}: {v}")

        lines = []
        for cat, items in by_category.items():
            lines.append(f"- {cat}: {', '.join(items)}")

        return "\n".join(lines)

    def get_user_name(self, default: str = "") -> str:
        """Retrieves the user's preferred name if stored in user memory."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute("""
                SELECT value FROM user_facts
                WHERE key = 'user_name'
                ORDER BY last_updated DESC LIMIT 1;
            """).fetchone()
            if row and row["value"]:
                return str(row["value"]).strip()
        return default

    def set_user_name(self, name: str) -> dict:
        """Saves or updates the user's preferred name."""
        clean = str(name).strip()
        return self.remember_fact(category="general", key="user_name", value=clean, data_type="string")

    def close(self):
        """Closes database connection safely."""
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
