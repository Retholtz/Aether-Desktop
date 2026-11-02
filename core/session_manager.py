"""
core/session_manager.py - Persistent Multi-Workspace Session Manager
Manages instant context rehydration and disk serialization across Aether modes.
"""
import os
import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from core.logger import get_logger

logger = get_logger("SessionManager")


def _utc_now_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat()
    except Exception:
        return datetime.utcnow().isoformat()


class SessionManager:
    def __init__(self, sessions_dir: str = "data/sessions"):
        self.sessions_dir = os.path.abspath(sessions_dir)
        os.makedirs(self.sessions_dir, exist_ok=True)
        self.active_session_id: str = "general_default"
        self.history: List[Dict[str, Any]] = []
        self.metadata: Dict[str, Any] = {}

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
        # 1. Save current session before swapping
        if self.active_session_id:
            self.save_current_session()

        target_id = f"{workspace_type}_{context_id}".lower().strip()
        self.active_session_id = target_id
        session_file = self.get_session_path(target_id)

        if os.path.exists(session_file):
            try:
                with open(session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.history = data.get("history", [])
                    self.metadata = data.get("metadata", {})
                    logger.info(f"[SESSION] Rehydrated session '{target_id}' ({len(self.history)} turns).")
            except Exception as e:
                logger.error(f"[SESSION] Failed loading session '{target_id}': {e}")
                self.history = []
                self.metadata = {}
        else:
            self.history = []
            self.metadata = {
                "total_turns": 0,
                "created_at": _utc_now_iso(),
                "display_name": display_name or target_id
            }
            logger.info(f"[SESSION] Initialized new session '{target_id}'.")
            self.save_current_session()

        return self.history

    def save_current_session(self):
        """Asynchronously writes the current active session to disk."""
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
            "history": self.history,
            "metadata": {
                **self.metadata,
                "total_turns": len(self.history)
            }
        }

        try:
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.error(f"[SESSION] Error writing session file '{session_file}': {e}")

    def append_turn(self, role: str, content: str):
        """Appends a turn ('user' or 'model') and triggers persistence."""
        self.history.append({
            "role": role,
            "parts": [{"text": content}]
        })
        self.save_current_session()

    def clear_active_history(self):
        """Clears memory for the active session without deleting the file."""
        self.history.clear()
        self.save_current_session()

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Lists all persistent sessions on disk."""
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
