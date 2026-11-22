"""
core/session_manager.py - Persistent Multi-Workspace Session Manager
Manages instant context rehydration and disk serialization across Aether modes.
"""
import os
import json
import tempfile
import threading
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from core.paths import user_data_path
from core.logger import get_logger

logger = get_logger("SessionManager")


def _utc_now_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat()
    except Exception:
        return datetime.utcnow().isoformat()


class SessionManager:
    def __init__(self, sessions_dir: Optional[str] = None):
        self._lock = threading.RLock()
        if sessions_dir is None:
            self.sessions_dir = user_data_path("data", "sessions")
        else:
            self.sessions_dir = os.path.abspath(sessions_dir)
        os.makedirs(self.sessions_dir, exist_ok=True)
        self.active_session_id: str = "general_default"
        self.history: List[Dict[str, Any]] = []
        self.metadata: Dict[str, Any] = {}
        # Load existing default session on boot so the first switch_session() or append_turn()
        # does not overwrite persisted general history with an empty list.
        self._load_session_file_locked(self.active_session_id, create_if_missing=False)

    def _load_session_file_locked(
        self,
        session_id: str,
        display_name: Optional[str] = None,
        create_if_missing: bool = True,
    ) -> None:
        session_file = self.get_session_path(session_id)
        if os.path.exists(session_file):
            try:
                with open(session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    raw_hist = data.get("history", [])
                    self.history = list(raw_hist) if isinstance(raw_hist, list) else []
                    raw_meta = data.get("metadata", {})
                    self.metadata = dict(raw_meta) if isinstance(raw_meta, dict) else {}
                    logger.info(f"[SESSION] Rehydrated session '{session_id}' ({len(self.history)} turns).")
            except Exception as e:
                logger.error(f"[SESSION] Failed loading session '{session_id}': {e}")
                self.history = []
                self.metadata = {}
        else:
            self.history = []
            self.metadata = {
                "total_turns": 0,
                "created_at": _utc_now_iso(),
                "display_name": display_name or session_id
            }
            if create_if_missing:
                logger.info(f"[SESSION] Initialized new session '{session_id}'.")
                self.save_current_session()

    def get_session_path(self, session_id: str) -> str:
        safe_id = "".join(c for c in session_id if c.isalnum() or c in ("_", "-"))
        return os.path.join(self.sessions_dir, f"{safe_id}.json")

    def switch_session(
        self,
        workspace_type: str,
        context_id: str,
        display_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Persists the current active session and rehydrates or creates the target session.
        Returns the loaded conversation history.
        """
        with self._lock:
            target_id = f"{workspace_type}_{context_id}".lower().strip()
            # 1. Save current session before swapping to another session
            if self.active_session_id and self.active_session_id != target_id:
                self.save_current_session()

            self.active_session_id = target_id
            self._load_session_file_locked(target_id, display_name=display_name, create_if_missing=True)
            return list(self.history)

    def save_current_session(self):
        """Atomically writes the current active session to disk."""
        with self._lock:
            if not self.active_session_id:
                return

            session_file = self.get_session_path(self.active_session_id)
            parts = self.active_session_id.split("_", 1)
            ws_type = parts[0]
            ctx_id = parts[1] if len(parts) > 1 else "default"

            payload = {
                "session_id": self.active_session_id,
                "workspace_type": ws_type,
                "context_id": ctx_id,
                "display_name": self.metadata.get("display_name", self.active_session_id),
                "updated_at": _utc_now_iso(),
                "history": list(self.history),
                "metadata": {
                    **self.metadata,
                    "total_turns": len(self.history)
                }
            }

            temp_name = None
            try:
                os.makedirs(self.sessions_dir, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    "w", dir=self.sessions_dir, delete=False, suffix=".tmp", encoding="utf-8"
                ) as tf:
                    temp_name = tf.name
                    json.dump(payload, tf, indent=2)
                os.replace(temp_name, session_file)
                temp_name = None
            except Exception as e:
                logger.error(f"[SESSION] Error writing session file '{session_file}': {e}")
            finally:
                if temp_name and os.path.exists(temp_name):
                    try:
                        os.remove(temp_name)
                    except Exception:
                        pass

    def append_turn(self, role: str, content: str):
        """Appends a turn ('user' or 'model') and triggers persistence."""
        with self._lock:
            self.history.append({
                "role": role,
                "parts": [{"text": content}]
            })
            self.save_current_session()

    def clear_active_history(self):
        """Clears memory for the active session without deleting the file."""
        with self._lock:
            self.history.clear()
            self.save_current_session()

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Lists all persistent sessions on disk."""
        with self._lock:
            sessions = []
            if not os.path.exists(self.sessions_dir):
                return sessions
            for fname in os.listdir(self.sessions_dir):
                if fname.endswith(".json"):
                    fpath = os.path.join(self.sessions_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            sessions.append({
                                "session_id": data.get("session_id", fname[:-5]),
                                "workspace_type": data.get("workspace_type", "general"),
                                "context_id": data.get("context_id", "default"),
                                "display_name": data.get("display_name", data.get("session_id", fname[:-5])),
                                "updated_at": data.get("updated_at"),
                                "total_turns": len(data.get("history", []))
                            })
                    except Exception:
                        pass
            return sessions
