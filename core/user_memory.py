"""
Aether Desktop - Dynamic User Knowledge Store & Preferences
Manages personalized user facts, toggleable proactive notifications,
and alert deduplication history in data/user_profile.db.
"""

import datetime
import json
import os
import random
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

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS custom_dictionary (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        term TEXT NOT NULL UNIQUE,
                        phonetic_guide TEXT NOT NULL,
                        category TEXT DEFAULT 'name',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_custom_dict_term ON custom_dictionary(term);")

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
        """Retrieves the user's preferred name / callsign raw string if stored in user memory."""
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

    def get_user_names(self) -> List[str]:
        """
        Retrieves the user's preferred names / callsigns as a parsed list.
        Supports semicolon-separated names (e.g. 'Michael; Mike; Buddy').
        """
        raw = self.get_user_name(default="")
        if not raw:
            return []
        names = [n.strip() for n in raw.split(";") if n.strip()]
        return names

    def get_random_user_name(self, default: str = "User") -> str:
        """Returns a single random name/callsign from the user's preferred names list."""
        names = self.get_user_names()
        if names:
            return random.choice(names)
        return default

    def set_user_name(self, name: str) -> dict:
        """Saves or updates the user's preferred name(s) / callsign(s)."""
        raw = str(name).strip()
        parts = [p.strip() for p in raw.split(";") if p.strip()]
        clean = "; ".join(parts) if parts else raw
        return self.remember_fact(category="general", key="user_name", value=clean, data_type="string")

    def get_callsign_frequency(self, default: str = "often") -> str:
        """Retrieves the user's preferred callsign usage frequency ('never', 'seldom', 'often', 'always')."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute("""
                SELECT value FROM user_facts
                WHERE key = 'callsign_frequency'
                ORDER BY last_updated DESC LIMIT 1;
            """).fetchone()
            if row and row["value"]:
                val = str(row["value"]).strip().lower()
                if val in ("never", "seldom", "often", "always"):
                    return val
        return default

    def set_callsign_frequency(self, frequency: str) -> dict:
        """Saves or updates the user's preferred callsign usage frequency."""
        val = str(frequency).strip().lower()
        if val not in ("never", "seldom", "often", "always"):
            val = "often"
        return self.remember_fact(category="general", key="callsign_frequency", value=val, data_type="string")

    def build_user_identity_directive(self, frequency: Optional[str] = None) -> str:
        """Constructs a structured directive for addressing the user according to their configured frequency."""
        names = self.get_user_names()
        if not names:
            return ""

        freq = (frequency or self.get_callsign_frequency()).strip().lower()
        if freq not in ("never", "seldom", "often", "always"):
            freq = "often"

        if len(names) == 1:
            name_display = f"'{names[0]}'"
            sample_name = names[0]
            variation_instruction = (
                f"- CALLSIGN USAGE:\n"
                f"  * When addressing the user, use {name_display}."
            )
        else:
            name_display = ", ".join(f"'{n}'" for n in names)
            sample_name = names[0]
            sample_nick = names[1] if len(names) > 1 else names[0]
            variation_instruction = (
                f"- CALLSIGN / NAME VARIATION:\n"
                f"  * The user has multiple preferred callsigns: {name_display}.\n"
                f"  * When you do address the user, randomly alternate between their preferred callsigns ({name_display}). Never use more than one callsign in a single response."
            )

        if freq == "never":
            frequency_guideline = (
                f"- CONVERSATIONAL ADDRESS DIRECTIVE (NEVER ADDRESS BY NAME):\n"
                f"  * Do NOT address the user by their name or callsign in spoken or written conversation.\n"
                f"  * Always answer and converse directly without inserting their name or callsign."
            )
            return (
                f"USER IDENTITY & ADDRESS DIRECTIVE:\n"
                f"The user's registered name(s) / callsign(s): {name_display}.\n"
                f"{frequency_guideline}\n"
            )

        elif freq == "seldom":
            frequency_guideline = (
                f"- NATURAL FREQUENCY (SELDOM / ~15-25% OF RESPONSES):\n"
                f"  * Address the user by their name or callsign only seldomly (roughly 1 in every 4 to 5 responses, about 15% to 25% of turns).\n"
                f"  * Most responses (about 75-85%) should simply answer or execute instructions directly without using their name.\n"
                f"  * Only occasionally weave their callsign in when it feels especially fitting or natural."
            )
        elif freq == "always":
            frequency_guideline = (
                f"- FREQUENCY (ALWAYS / EVERY RESPONSE):\n"
                f"  * Always address the user by their name or callsign in every response when speaking to them.\n"
                f"  * Weave their callsign naturally into every answer."
            )
        else:  # "often" (default)
            frequency_guideline = (
                f"- NATURAL & BALANCED FREQUENCY (OFTEN / ~40-50% OF RESPONSES):\n"
                f"  * Do NOT use the user's name in every single response back-to-back, as that feels forced and robotic.\n"
                f"  * You should address the user by their name or callsign roughly every 2 to 3 responses (about 40% to 50% of the time across the conversation).\n"
                f"  * Weave their callsigns naturally into answers, explanations, remarks, and confirmations (for example: \"That makes sense, {sample_name}\", \"Here is the latest data, {sample_name}\", \"You're spot on, {sample_name}\")."
            )

        directive = (
            f"USER IDENTITY & ADDRESS DIRECTIVE:\n"
            f"The user goes by the following preferred name(s) / callsign(s): {name_display}.\n"
            f"{frequency_guideline}\n"
            f"{variation_instruction}\n"
        )
        return directive

    def add_dictionary_term(self, term: str, phonetic_guide: str, category: str = "name") -> Dict[str, Any]:
        """Inserts or updates a custom lexicon entry for STT/TTS phonetic biasing."""
        term_clean = str(term).strip()
        phonetic_clean = str(phonetic_guide).strip()
        cat_clean = (category or "name").strip().lower()

        if not term_clean or not phonetic_clean:
            return {
                "status": "error",
                "message": "Both term and phonetic guide must be non-empty."
            }

        with self._lock:
            conn = self._get_connection()
            with conn:
                cursor = conn.execute("""
                    INSERT INTO custom_dictionary (term, phonetic_guide, category, created_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(term) DO UPDATE SET
                        phonetic_guide = excluded.phonetic_guide,
                        category = excluded.category;
                """, (term_clean, phonetic_clean, cat_clean))
                term_id = cursor.lastrowid

            if not term_id or cursor.rowcount == 0:
                row = conn.execute("SELECT id FROM custom_dictionary WHERE term = ? COLLATE NOCASE", (term_clean,)).fetchone()
                term_id = row["id"] if row else 0

        logger.info(f"[USER MEMORY] Stored lexicon term '{term_clean}' -> '{phonetic_clean}' ({cat_clean})")
        return {
            "status": "success",
            "id": term_id,
            "term": term_clean,
            "phonetic_guide": phonetic_clean,
            "category": cat_clean,
            "message": f"Added term '{term_clean}' with phonetic guide '{phonetic_clean}'."
        }

    def remove_dictionary_term(self, term: str) -> Dict[str, Any]:
        """Removes a term from the custom lexicon dictionary."""
        term_clean = str(term).strip()
        with self._lock:
            conn = self._get_connection()
            with conn:
                cursor = conn.execute("DELETE FROM custom_dictionary WHERE term = ? COLLATE NOCASE;", (term_clean,))
                deleted = cursor.rowcount > 0

        logger.info(f"[USER MEMORY] Removed lexicon term '{term_clean}' (deleted={deleted})")
        return {
            "status": "success" if deleted else "not_found",
            "deleted": deleted,
            "term": term_clean,
            "message": f"Removed dictionary term '{term_clean}'." if deleted else f"Term '{term_clean}' was not found in dictionary."
        }

    def get_all_dictionary_terms(self) -> List[Dict[str, Any]]:
        """Returns all custom lexicon terms ordered alphabetically by term."""
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute("""
                SELECT id, term, phonetic_guide, category, created_at
                FROM custom_dictionary
                ORDER BY term COLLATE NOCASE ASC;
            """).fetchall()
        return [dict(r) for r in rows]

    def build_lexicon_instruction(self) -> str:
        """Constructs a structured pronunciation and transcription guide block for system instructions."""
        terms = self.get_all_dictionary_terms()
        if not terms:
            return ""

        lines = ["\n[CUSTOM USER LEXICON & PRONUNCIATION GUIDE]"]
        lines.append("Use this lexicon to resolve ambiguous audio input (STT) and guide phonetic speech output (TTS):")
        for item in terms:
            lines.append(f"- Term: '{item['term']}' | Phonetic Sound: '{item['phonetic_guide']}' (Type: {item['category']})")
        lines.append("Always use the canonical spelling in text/tool calls, and adhere to the phonetic syllable stress when speaking.\n")
        return "\n".join(lines)

    def close(self):
        """Closes database connection safely."""
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None


# Default user memory singleton reference
_default_memory: Optional[UserMemory] = None


def get_user_memory() -> UserMemory:
    global _default_memory
    if _default_memory is None:
        _default_memory = UserMemory()
    return _default_memory


def add_dictionary_term(term: str, phonetic_guide: str, category: str = "name") -> Dict[str, Any]:
    return get_user_memory().add_dictionary_term(term=term, phonetic_guide=phonetic_guide, category=category)


def remove_dictionary_term(term: str) -> Dict[str, Any]:
    return get_user_memory().remove_dictionary_term(term=term)


def get_all_dictionary_terms() -> List[Dict[str, Any]]:
    return get_user_memory().get_all_dictionary_terms()


def build_lexicon_instruction() -> str:
    return get_user_memory().build_lexicon_instruction()

