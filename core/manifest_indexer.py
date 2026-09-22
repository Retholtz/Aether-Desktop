"""
Aether Desktop - Session Manifest Indexer & Retrieval Engine
Persists structured session manifest cards extracted from raw conversation transcripts
into a SQLite repository (data/chat_index.db) and provides keyword/semantic search for Cortex.
"""

import json
import os
import sqlite3
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from core.logger import get_logger

logger = get_logger("ManifestIndexer")

DEFAULT_DB_REL_PATH = os.path.join("data", "chat_index.db")
_db_lock = threading.Lock()


def get_default_db_path() -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, DEFAULT_DB_REL_PATH)


def init_manifest_db(db_path: Optional[str] = None):
    """Initializes SQLite database and creates session_manifests table with indexes."""
    target_path = db_path or get_default_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)

    with _db_lock:
        conn = sqlite3.connect(target_path, timeout=10.0)
        try:
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
            except Exception:
                pass
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS session_manifests (
                        session_id TEXT PRIMARY KEY,
                        date TEXT,
                        topics TEXT,
                        actions TEXT,
                        unresolved TEXT,
                        entities TEXT,
                        transcript_path TEXT,
                        created_at REAL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_manifest_date ON session_manifests(date);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_manifest_created ON session_manifests(created_at);")
        finally:
            conn.close()


def index_manifest_card(card: Dict[str, Any], transcript_path: str, db_path: Optional[str] = None):
    """Inserts or updates a session manifest card into the SQLite database."""
    target_path = db_path or get_default_db_path()
    init_manifest_db(target_path)

    session_id = card.get("session_id") or f"session_{time.strftime('%Y%m%d_%H%M%S')}"
    date_str = card.get("date") or time.strftime("%Y-%m-%d")
    topics_json = json.dumps(card.get("topics_discussed") or card.get("topics") or [], default=str)
    actions_json = json.dumps(card.get("actions_executed") or card.get("actions") or [], default=str)
    unresolved_json = json.dumps(card.get("unresolved_questions") or card.get("unresolved") or [], default=str)
    entities_json = json.dumps(card.get("key_entities") or card.get("entities") or [], default=str)
    created_at = float(card.get("timestamp") or card.get("created_at") or time.time())

    with _db_lock:
        conn = sqlite3.connect(target_path, timeout=10.0)
        try:
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO session_manifests 
                    (session_id, date, topics, actions, unresolved, entities, transcript_path, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    session_id,
                    date_str,
                    topics_json,
                    actions_json,
                    unresolved_json,
                    entities_json,
                    transcript_path,
                    created_at
                ))
        finally:
            conn.close()

    logger.info(f"[MANIFEST] Indexed session card for '{session_id}' (date: {date_str})")


def search_manifest_index(query: str, limit: int = 5, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Performs case-insensitive substring search over topics, actions, unresolved questions,
    and key entities in the session manifest index.
    """
    target_path = db_path or get_default_db_path()
    init_manifest_db(target_path)

    clean_query = (query or "").strip().lower()
    if not clean_query:
        return []

    terms = f"%{clean_query}%"
    results: List[Dict[str, Any]] = []

    with _db_lock:
        conn = sqlite3.connect(target_path, timeout=10.0)
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("""
                SELECT session_id, date, topics, actions, unresolved, entities, transcript_path, created_at
                FROM session_manifests
                WHERE LOWER(topics) LIKE ? 
                   OR LOWER(actions) LIKE ? 
                   OR LOWER(entities) LIKE ? 
                   OR LOWER(unresolved) LIKE ?
                ORDER BY created_at DESC 
                LIMIT ?;
            """, (terms, terms, terms, terms, max(1, limit)))

            for row in cur.fetchall():
                try:
                    topics = json.loads(row["topics"]) if row["topics"] else []
                except Exception:
                    topics = []
                try:
                    actions = json.loads(row["actions"]) if row["actions"] else []
                except Exception:
                    actions = []
                try:
                    unresolved = json.loads(row["unresolved"]) if row["unresolved"] else []
                except Exception:
                    unresolved = []
                try:
                    entities = json.loads(row["entities"]) if row["entities"] else []
                except Exception:
                    entities = []

                results.append({
                    "session_id": row["session_id"],
                    "date": row["date"],
                    "topics": topics,
                    "actions": actions,
                    "unresolved": unresolved,
                    "entities": entities,
                    "path": row["transcript_path"],
                    "created_at": row["created_at"]
                })
        finally:
            conn.close()

    return results


def resolve_gemini_client(explicit_client: Optional[Any] = None) -> Optional[Any]:
    """
    Resolves a google-genai Client instance:
    1. Uses explicit client if provided.
    2. Uses GEMINI_API_KEY environment variable.
    3. Reads config.json and decrypts DPAPI api_key_encrypted via unprotect_secret.
    """
    if explicit_client is not None:
        return explicit_client

    try:
        from google import genai
    except ImportError:
        logger.warning("[MANIFEST] google-genai package not available for extraction.")
        return None

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        try:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cfg_path = os.path.join(base_dir, "config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                enc_key = cfg.get("api", {}).get("api_key_encrypted", "")
                if enc_key:
                    from core.security import unprotect_secret
                    api_key = unprotect_secret(enc_key)
        except Exception as e:
            logger.debug(f"[MANIFEST] Could not decrypt API key from config.json: {e}")

    if not api_key:
        logger.warning("[MANIFEST] No GEMINI_API_KEY found or decrypted. Manifest extraction unavailable.")
        return None

    try:
        return genai.Client(api_key=api_key)
    except Exception as e:
        logger.error(f"[MANIFEST] Failed to initialize genai.Client: {e}")
        return None


def _extract_and_store_manifest(
    transcript_path: str,
    client: Optional[Any] = None,
    db_path: Optional[str] = None,
    model: str = "gemini-3.8-flash"
):
    """
    Reads a persisted session JSON file, builds conversation text, prompts Gemini
    for a structured Manifest Card, and stores it in the index database.
    """
    if not os.path.exists(transcript_path):
        logger.warning(f"[MANIFEST] Transcript file does not exist: {transcript_path}")
        return

    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            session_data = json.load(f)
    except Exception as e:
        logger.error(f"[MANIFEST ERROR] Failed to read transcript {transcript_path}: {e}")
        return

    turns = session_data.get("turns", [])
    if not turns:
        logger.debug(f"[MANIFEST] Session {transcript_path} contains no turns; skipping.")
        return

    # Condense raw turns into formatted dialogue
    dialogue_lines = []
    for t in turns:
        role = str(t.get("role", "user")).upper()
        text = str(t.get("text", "")).strip()
        tools = t.get("tools_used") or []
        tools_str = f" [Tools: {', '.join(tools)}]" if tools else ""
        if text or tools_str:
            dialogue_lines.append(f"{role}: {text}{tools_str}")

    turns_text = "\n".join(dialogue_lines)
    if len(turns_text.strip()) < 20:
        logger.debug(f"[MANIFEST] Session {transcript_path} dialogue too brief; skipping extraction.")
        return

    active_client = resolve_gemini_client(client)
    if not active_client:
        # Construct deterministic fallback card if no LLM client is available
        fallback_card = {
            "session_id": session_data.get("session_id", os.path.splitext(os.path.basename(transcript_path))[0]),
            "date": time.strftime("%Y-%m-%d", time.localtime(session_data.get("start_time", time.time()))),
            "timestamp": session_data.get("start_time", time.time()),
            "topics_discussed": [turns[0].get("text", "")[:80]] if turns else [],
            "actions_executed": [t for t in (turns[0].get("tools_used") or [])],
            "unresolved_questions": [],
            "key_entities": []
        }
        index_manifest_card(fallback_card, transcript_path, db_path=db_path)
        logger.info(f"[MANIFEST] Stored deterministic fallback card for {fallback_card['session_id']}")
        return

    from google.genai import types

    prompt = f"""Analyze the following desktop conversation turns and produce a concise session manifest card.
Strictly adhere to the following JSON structure:
{{
  "topics_discussed": ["string"],
  "actions_executed": ["string"],
  "unresolved_questions": ["string"],
  "key_entities": ["string"]
}}

Rules:
- topics_discussed: 1 to 4 concise phrases summarizing key user requests or discussion topics.
- actions_executed: 0 to 5 distinct actions, automation tools, web searches, or files manipulated.
- unresolved_questions: any questions asked that were left pending, deferred, or requested for later.
- key_entities: specific names, URLs, applications, dates, or financial/technical terms mentioned.
Keep each list item short and specific.

Conversation:
{turns_text[-6000:]}
"""

    candidate_models = [model, "gemini-3.8-flash"]
    card_data = None

    for candidate in candidate_models:
        try:
            resp = active_client.models.generate_content(
                model=candidate,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2
                )
            )
            raw_text = getattr(resp, "text", "") or ""
            if raw_text:
                card_data = json.loads(raw_text)
                break
        except Exception as err:
            logger.warning(f"[MANIFEST] Extraction with model '{candidate}' failed: {err}")

    if not card_data or not isinstance(card_data, dict):
        logger.warning(f"[MANIFEST] Could not parse valid JSON manifest card for {transcript_path}")
        return

    session_id = session_data.get("session_id") or os.path.splitext(os.path.basename(transcript_path))[0]
    start_time = session_data.get("start_time", time.time())
    card_data["session_id"] = session_id
    card_data["date"] = time.strftime("%Y-%m-%d", time.localtime(start_time))
    card_data["timestamp"] = start_time

    index_manifest_card(card_data, transcript_path, db_path=db_path)
    logger.info(f"[MANIFEST] Successfully extracted and indexed session manifest for {session_id}")


def queue_manifest_extraction(
    transcript_path: str,
    client: Optional[Any] = None,
    db_path: Optional[str] = None,
    model: str = "gemini-3.8-flash"
) -> threading.Thread:
    """
    Spawns an asynchronous background daemon thread to extract and index
    the session manifest card without blocking the caller.
    """
    t = threading.Thread(
        target=_extract_and_store_manifest,
        args=(transcript_path, client, db_path, model),
        daemon=True,
        name="ManifestExtractor"
    )
    t.start()
    return t


def get_default_chats_dir() -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "data", "chats")


def list_stored_sessions(
    chats_dir: Optional[str] = None,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Scans data/chats for session transcripts, queries the manifest database for
    extracted metadata, and returns a unified list of sessions sorted by newest first.
    """
    target_chats = chats_dir or get_default_chats_dir()
    target_db = db_path or get_default_db_path()
    init_manifest_db(target_db)

    # 1. Load all manifests from SQLite into memory map
    manifest_map: Dict[str, Dict[str, Any]] = {}
    with _db_lock:
        conn = sqlite3.connect(target_db, timeout=10.0)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT session_id, date, topics, actions, unresolved, entities, transcript_path, created_at
                FROM session_manifests
                ORDER BY created_at DESC;
            """).fetchall()
            for r in rows:
                sid = r["session_id"]
                try:
                    topics = json.loads(r["topics"]) if r["topics"] else []
                except Exception:
                    topics = []
                try:
                    actions = json.loads(r["actions"]) if r["actions"] else []
                except Exception:
                    actions = []
                try:
                    unresolved = json.loads(r["unresolved"]) if r["unresolved"] else []
                except Exception:
                    unresolved = []
                try:
                    entities = json.loads(r["entities"]) if r["entities"] else []
                except Exception:
                    entities = []

                manifest_map[sid] = {
                    "date": r["date"],
                    "topics": topics,
                    "actions": actions,
                    "unresolved": unresolved,
                    "entities": entities,
                    "created_at": r["created_at"],
                    "path": r["transcript_path"]
                }
        finally:
            conn.close()

    sessions: List[Dict[str, Any]] = []

    # 2. Scan chats directory
    if os.path.exists(target_chats):
        for fname in os.listdir(target_chats):
            if not fname.endswith(".json"):
                continue

            fpath = os.path.join(target_chats, fname)
            sid = os.path.splitext(fname)[0]
            try:
                stat = os.stat(fpath)
                mtime = stat.st_mtime
                fsize = stat.st_size
                with open(fpath, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)

                turns = raw_data.get("turns", [])
                start_t = raw_data.get("start_time", mtime)
                end_t = raw_data.get("end_time", mtime)
                turn_count = len(turns)

                # Extract first user query for preview
                first_query = ""
                for t in turns:
                    if t.get("role") == "user" and t.get("text"):
                        first_query = t["text"]
                        break

                m = manifest_map.get(sid, {})
                date_str = m.get("date") or time.strftime("%Y-%m-%d", time.localtime(start_t))
                time_str = time.strftime("%H:%M:%S", time.localtime(start_t))

                sessions.append({
                    "session_id": sid,
                    "date": date_str,
                    "time": time_str,
                    "start_time": start_t,
                    "end_time": end_t,
                    "duration_seconds": round(max(0, end_t - start_t), 1),
                    "turn_count": turn_count,
                    "file_size": fsize,
                    "file_path": fpath,
                    "preview": first_query[:100] + ("..." if len(first_query) > 100 else ""),
                    "topics": m.get("topics", []),
                    "actions": m.get("actions", []),
                    "unresolved": m.get("unresolved", []),
                    "entities": m.get("entities", []),
                    "has_manifest": bool(sid in manifest_map)
                })
            except Exception as e:
                logger.debug(f"[MANIFEST] Error reading session file {fpath}: {e}")

    # Sort descending by start_time
    sessions.sort(key=lambda s: s.get("start_time", 0), reverse=True)
    return sessions


def get_session_details(
    session_id: str,
    chats_dir: Optional[str] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Loads full transcript turns and manifest card details for a specific session_id.
    """
    target_chats = chats_dir or get_default_chats_dir()
    target_db = db_path or get_default_db_path()

    clean_sid = os.path.basename(session_id.strip()).replace(".json", "")
    fpath = os.path.join(target_chats, f"{clean_sid}.json")

    if not os.path.exists(fpath):
        return {
            "status": "error",
            "message": f"Session file '{clean_sid}.json' not found on disk."
        }

    try:
        with open(fpath, "r", encoding="utf-8") as f:
            session_data = json.load(f)
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to parse session file: {e}"
        }

    # Fetch manifest card from SQLite
    manifest_card = None
    with _db_lock:
        conn = sqlite3.connect(target_db, timeout=10.0)
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT session_id, date, topics, actions, unresolved, entities, created_at
                FROM session_manifests
                WHERE session_id = ?
                LIMIT 1;
            """, (clean_sid,)).fetchone()
            if row:
                try:
                    topics = json.loads(row["topics"]) if row["topics"] else []
                except Exception:
                    topics = []
                try:
                    actions = json.loads(row["actions"]) if row["actions"] else []
                except Exception:
                    actions = []
                try:
                    unresolved = json.loads(row["unresolved"]) if row["unresolved"] else []
                except Exception:
                    unresolved = []
                try:
                    entities = json.loads(row["entities"]) if row["entities"] else []
                except Exception:
                    entities = []

                manifest_card = {
                    "session_id": row["session_id"],
                    "date": row["date"],
                    "topics": topics,
                    "actions": actions,
                    "unresolved": unresolved,
                    "entities": entities,
                    "created_at": row["created_at"]
                }
        finally:
            conn.close()

    start_t = session_data.get("start_time", 0)
    end_t = session_data.get("end_time", 0)
    turns = session_data.get("turns", [])

    return {
        "status": "success",
        "session_id": clean_sid,
        "date": time.strftime("%Y-%m-%d", time.localtime(start_t)) if start_t else "Unknown",
        "time": time.strftime("%H:%M:%S", time.localtime(start_t)) if start_t else "Unknown",
        "start_time": start_t,
        "end_time": end_t,
        "duration_seconds": round(max(0, end_t - start_t), 1),
        "turn_count": len(turns),
        "turns": turns,
        "manifest": manifest_card
    }


def delete_session_and_transcript(
    session_id: str,
    chats_dir: Optional[str] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Deletes the session JSON transcript from data/chats/ and removes its
    indexed metadata from SQLite session_manifests.
    """
    target_chats = chats_dir or get_default_chats_dir()
    target_db = db_path or get_default_db_path()

    clean_sid = os.path.basename(session_id.strip()).replace(".json", "")
    fpath = os.path.join(target_chats, f"{clean_sid}.json")

    file_deleted = False
    if os.path.exists(fpath):
        try:
            os.remove(fpath)
            file_deleted = True
            logger.info(f"[SESSION] Deleted transcript file {fpath}")
        except Exception as e:
            logger.warning(f"[SESSION] Error removing {fpath}: {e}")

    db_deleted = False
    with _db_lock:
        conn = sqlite3.connect(target_db, timeout=10.0)
        try:
            with conn:
                cur = conn.execute("DELETE FROM session_manifests WHERE session_id = ?;", (clean_sid,))
                db_deleted = cur.rowcount > 0
        finally:
            conn.close()

    if file_deleted or db_deleted:
        logger.info(f"[SESSION] Successfully deleted session '{clean_sid}' (file={file_deleted}, db={db_deleted})")
        return {
            "status": "success",
            "session_id": clean_sid,
            "file_deleted": file_deleted,
            "db_deleted": db_deleted,
            "message": f"Successfully deleted session '{clean_sid}'."
        }
    else:
        return {
            "status": "not_found",
            "session_id": clean_sid,
            "message": f"Session '{clean_sid}' was not found on disk or in the database."
        }


def delete_all_stored_sessions(
    chats_dir: Optional[str] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Deletes all session transcripts in data/chats/ and wipes session_manifests table.
    """
    target_chats = chats_dir or get_default_chats_dir()
    target_db = db_path or get_default_db_path()

    deleted_files = 0
    if os.path.exists(target_chats):
        for fname in os.listdir(target_chats):
            if fname.endswith(".json"):
                fpath = os.path.join(target_chats, fname)
                try:
                    os.remove(fpath)
                    deleted_files += 1
                except Exception as e:
                    logger.warning(f"[SESSION] Failed to delete {fpath}: {e}")

    deleted_rows = 0
    with _db_lock:
        conn = sqlite3.connect(target_db, timeout=10.0)
        try:
            with conn:
                cur = conn.execute("DELETE FROM session_manifests;")
                deleted_rows = cur.rowcount
        finally:
            conn.close()

    logger.info(f"[SESSION] Purged all stored sessions (files={deleted_files}, db_rows={deleted_rows})")
    return {
        "status": "success",
        "deleted_files": deleted_files,
        "deleted_rows": deleted_rows,
        "message": f"Purged {deleted_files} session transcripts and {deleted_rows} index records."
    }

