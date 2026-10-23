"""
Aether Desktop - Rolling Session Compactor & Lifecycle Manager (Layer B)
Tracks session age, turn telemetry, and tool token weight.
Generates asynchronous background conversation state summaries and orchestrates
silent WebSocket session re-anchoring without audio stream interruption.
"""

import asyncio
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
from core.logger import get_logger
from core.context_sanitizer import sanitize_turn_history

logger = get_logger("SessionLifecycle")

# Default rotation trigger thresholds
DEFAULT_MAX_TURNS = 25
DEFAULT_MAX_DURATION_SECONDS = 450.0   # 7.5 minutes (preempts 9m / 540s Gemini Live server GoAway cutoff)
DEFAULT_MAX_TOOL_CHARS = 50000         # 50k characters of tool stdout/tracebacks

COMPACTOR_PROMPT = """Summarize the active desktop context and conversation state into exactly 4-6 concise bullet points:
- Active user tasks or pending desktop workflows.
- Key facts established in this session.
- Any active window states or working files currently in focus.
Omit greetings and small talk. Keep it purely operational."""

GAME_COMPACTOR_PROMPT = """Summarize the active in-game co-pilot context and tactical conversation state into the following structured facets (keep each concise and under 25 words):
- ACTIVE GAME & UNIVERSE: Current game title, universe setting, and player role.
- PRIMARY OBJECTIVE: Current quest, mission, or high-level player goal.
- IMMEDIATE SUB-TASK: The exact action or sub-goal being tackled right now.
- LOCATION & VESSEL/STATUS: Current coordinates, star system, zone, ship/character health, or vehicle condition.
- KEY RECENT DECISIONS & TACTICAL DIRECTIVES: Critical rules, navigation choices, or commands given in the last 10 minutes.
- NOTABLE DISCOVERIES/INTEL: Landmarks, items, or clues mentioned recently.

Omit conversational pleasantries, greetings, and system metadata. Output only the structured facets above."""


class SessionLifecycleManager:
    """
    Monitors session metrics (turns, elapsed time, tool payload size).
    Executes non-blocking background LLM compaction and coordinates
    seamless WebSocket rotation.
    """

    def __init__(
        self,
        rotation_threshold_turns: int = DEFAULT_MAX_TURNS,
        rotation_threshold_duration: float = DEFAULT_MAX_DURATION_SECONDS,
        rotation_threshold_tool_chars: int = DEFAULT_MAX_TOOL_CHARS,
        chats_dir: Optional[str] = None
    ):
        self.rotation_threshold_turns = rotation_threshold_turns
        self.rotation_threshold_duration = rotation_threshold_duration
        self.rotation_threshold_tool_chars = rotation_threshold_tool_chars

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.chats_dir = chats_dir or os.path.join(base_dir, "data", "chats")
        os.makedirs(self.chats_dir, exist_ok=True)

        self._lock = threading.RLock()
        self.session_id: str = f"session_{time.strftime('%Y%m%d_%H%M%S')}"
        self.raw_transcript: List[Dict[str, Any]] = []

        self.turn_count: int = 0
        self.session_start_time: float = time.time()
        self.tool_execution_chars: int = 0
        self.turn_history: List[Dict[str, str]] = []
        self.last_summary: str = ""
        self._rotation_in_progress: bool = False
        self.rotation_started_at: float = 0.0
        self.total_rotations: int = 0

    @property
    def rotation_in_progress(self) -> bool:
        if self._rotation_in_progress:
            if time.time() - self.rotation_started_at > 120.0:
                print("[WARN] [SESSION] Stale rotation lock expired (>120s). Resetting lock.")
                logger.warning("[SESSION] Stale rotation lock expired (>120s). Resetting lock.")
                self._rotation_in_progress = False
        return self._rotation_in_progress

    @rotation_in_progress.setter
    def rotation_in_progress(self, value: bool):
        self._rotation_in_progress = bool(value)
        if value:
            self.rotation_started_at = time.time()
        else:
            self.rotation_started_at = 0.0

    def record_turn(self, role: str, content: str, tools_used: Optional[List[str]] = None):
        """Records a user utterance or model response in turn history and raw transcript."""
        clean_content = str(content).strip()
        if not clean_content:
            return

        turn_timestamp = time.time()
        with self._lock:
            turn_data = {
                "turn_id": len(self.raw_transcript) + 1,
                "timestamp": turn_timestamp,
                "role": role,
                "text": clean_content,
                "tools_used": list(tools_used) if tools_used else []
            }
            self.raw_transcript.append(turn_data)

            self.turn_history.append({
                "role": role,
                "content": clean_content,
                "timestamp": turn_timestamp
            })

            # Cap retained turns in memory to avoid unbounded growth
            if len(self.turn_history) > 60:
                self.turn_history = self.turn_history[-40:]

            if role in ("user", "assistant", "model"):
                self.turn_count += 1
                logger.debug(f"[LIFECYCLE] Turn #{self.turn_count} ({role}): {clean_content[:80]}...")

    def get_raw_turns(self) -> List[Dict[str, Any]]:
        """Returns the raw in-memory turn history for the current session."""
        with self._lock:
            return list(self.turn_history)

    def get_active_context_turns(self) -> List[Dict[str, Any]]:
        """
        Retrieves the in-memory turns for the current session,
        running the surgical refusal cleansing pass before handoff to the LLM.
        """
        raw_turns = self.get_raw_turns()
        return sanitize_turn_history(raw_turns)

    def record_tool_chars(self, char_count: int):
        """Accumulates tool payload character count to gauge token consumption."""
        with self._lock:
            self.tool_execution_chars += max(0, char_count)

    def get_elapsed_seconds(self) -> float:
        """Returns seconds elapsed since current session started."""
        return time.time() - self.session_start_time

    def needs_rotation(self) -> bool:
        """
        Checks whether the session has reached the rotation threshold:
          - turn_count >= 25 (or configured limit)
          - duration >= 30 minutes (1800s)
          - tool execution chars >= 50,000
        """
        # Reset stuck rotation locks older than 120 seconds
        if self.rotation_in_progress:
            if time.time() - self.rotation_started_at > 120.0:
                print("[WARN] [SESSION] Stale rotation lock expired (>120s). Resetting lock.")
                logger.warning("[SESSION] Stale rotation lock expired (>120s). Resetting lock.")
                self.rotation_in_progress = False
            else:
                return False

        if self.turn_count >= self.rotation_threshold_turns:
            logger.info(f"[LIFECYCLE] NEEDS_ROTATION: turn_count ({self.turn_count}) >= threshold ({self.rotation_threshold_turns})")
            return True

        elapsed = self.get_elapsed_seconds()
        if elapsed >= self.rotation_threshold_duration:
            logger.info(f"[LIFECYCLE] NEEDS_ROTATION: elapsed ({elapsed:.0f}s) >= threshold ({self.rotation_threshold_duration:.0f}s)")
            return True

        if self.tool_execution_chars >= self.rotation_threshold_tool_chars:
            logger.info(f"[LIFECYCLE] NEEDS_ROTATION: tool_chars ({self.tool_execution_chars}) >= threshold ({self.rotation_threshold_tool_chars})")
            return True

        return False

    def rotate_session(self):
        """Executes session rotation with concurrency flag protection."""
        self.rotation_in_progress = True
        self.rotation_started_at = time.time()
        try:
            self._execute_session_rotation()
        finally:
            self.rotation_in_progress = False

    def _execute_session_rotation(self):
        self.flush_session_to_disk()
        self.reset_metrics()


    async def generate_session_summary(
        self,
        client: Optional[genai.Client],
        model: str = "gemini-3.8-flash",
        is_game_mode: bool = False,
        game_title: str = "",
        active_game_context: str = ""
    ) -> str:
        """
        Asynchronously compacts the active conversation history using a fast, non-blocking call to gemini-3.8-flash.
        When is_game_mode is True, uses the structured Two-Tier Game Compactor schema with game facets.
        When is_game_mode is False, compacts into 4-6 operational desktop bullet points.
        """
        active_turns = self.get_active_context_turns()
        if not active_turns:
            if is_game_mode:
                game_label = game_title or "Active Game"
                return (
                    f"- ACTIVE GAME & UNIVERSE: {game_label}\n"
                    f"- PRIMARY OBJECTIVE: Standing by for player orders.\n"
                    f"- IMMEDIATE SUB-TASK: In-game co-pilot initialized.\n"
                    f"- LOCATION & VESSEL/STATUS: Operational.\n"
                    f"- KEY RECENT DECISIONS & TACTICAL DIRECTIVES: Follow player direct instructions.\n"
                    f"- NOTABLE DISCOVERIES/INTEL: None logged yet."
                )
            return "- Session newly initialized; no prior actions."

        # Format transcript lines for the compactor
        transcript_lines = []
        for t in active_turns[-30:]:
            role_label = "User" if t["role"] == "user" else "Assistant"
            transcript_lines.append(f"{role_label}: {t['content']}")
        transcript_text = "\n".join(transcript_lines)

        if is_game_mode:
            game_header = f"Active Game: {game_title}\n" if game_title else ""
            if active_game_context:
                game_header += f"Current In-Game Context:\n{active_game_context}\n"
            full_prompt = (
                f"Below is the recent in-game dialogue and tactical action history:\n"
                f"---\n"
                f"{game_header}"
                f"{transcript_text}\n"
                f"---\n\n"
                f"{GAME_COMPACTOR_PROMPT}"
            )
        else:
            full_prompt = (
                f"Below is the recent conversation and desktop task history:\n"
                f"---\n"
                f"{transcript_text}\n"
                f"---\n\n"
                f"{COMPACTOR_PROMPT}"
            )

        logger.info(f"[LIFECYCLE COMPACTOR] Generating state summary via {model} (is_game_mode={is_game_mode}, {len(active_turns)} turns)...")

        summary_text = ""
        if client:
            try:
                # Use client.aio for non-blocking async generation
                resp = await client.aio.models.generate_content(
                    model=model,
                    contents=full_prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.2,
                        max_output_tokens=600,
                    )
                )
                if resp.candidates and resp.candidates[0].content and resp.candidates[0].content.parts:
                    summary_text = "".join(p.text for p in resp.candidates[0].content.parts if getattr(p, "text", None)).strip()
            except Exception as compactor_err:
                logger.warning(f"[LIFECYCLE COMPACTOR] {model} failed: {compactor_err}. Attempting fallback...")
                try:
                    fallback_model = "gemini-3.8-flash"
                    resp = await client.aio.models.generate_content(
                        model=fallback_model,
                        contents=full_prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.2,
                            max_output_tokens=600,
                        )
                    )
                    if resp.candidates and resp.candidates[0].content and resp.candidates[0].content.parts:
                        summary_text = "".join(p.text for p in resp.candidates[0].content.parts if getattr(p, "text", None)).strip()
                except Exception as fb_err:
                    logger.error(f"[LIFECYCLE COMPACTOR] Fallback LLM also failed: {fb_err}")

        # If LLM generation failed or client was None, construct deterministic operational summary
        if not summary_text:
            if is_game_mode:
                game_label = game_title or "Active Game"
                recent_user = [t["content"] for t in active_turns if t["role"] == "user"]
                last_order = recent_user[-1].replace("\n", " ")[:100] if recent_user else "Standing by for player orders."
                summary_text = (
                    f"- ACTIVE GAME & UNIVERSE: {game_label}\n"
                    f"- PRIMARY OBJECTIVE: In-game co-pilot support for {game_label}.\n"
                    f"- IMMEDIATE SUB-TASK: {last_order}\n"
                    f"- LOCATION & VESSEL/STATUS: Operational.\n"
                    f"- KEY RECENT DECISIONS & TACTICAL DIRECTIVES: Seamlessly continuing gameplay.\n"
                    f"- NOTABLE DISCOVERIES/INTEL: Retrieved from active telemetry."
                )
            else:
                recent_turns = active_turns[-6:]
                bullets = []
                for t in recent_turns:
                    r = "User asked" if t["role"] == "user" else "Assistant performed"
                    snippet = t["content"].replace("\n", " ")[:120]
                    bullets.append(f"- {r}: {snippet}")
                summary_text = "\n".join(bullets[:5]) if bullets else "- Ongoing user session continuing without prior tasks."

        self.last_summary = summary_text
        logger.info(f"[LIFECYCLE COMPACTOR] State summary produced ({len(summary_text)} chars):\n{summary_text}")
        return summary_text

    def build_compacted_instruction(
        self,
        base_instruction: str,
        summary: Optional[str] = None,
        is_game_mode: bool = False
    ) -> str:
        """
        Appends the state summary into the system instruction for the newly rotated session.
        In Game Mode, applies co-pilot continuous immersion framing.
        In Desktop Mode, applies desktop operational continuity framing.
        """
        active_summary = summary or self.last_summary
        if not active_summary:
            return base_instruction

        if is_game_mode:
            compacted_block = (
                "\n\n================================================================================\n"
                "ACTIVE IN-GAME TACTICAL STATE & CO-PILOT WORKING MEMORY (TWO-TIER ROTATION):\n"
                "The following structured facets represent the continuous state of our gameplay from previous session turns:\n"
                f"{active_summary}\n\n"
                "DIRECTIVE: Stay in-character as the dedicated co-pilot. Seamlessly continue assisting without greeting the player again or dropping active mission context.\n"
                "================================================================================\n"
            )
        else:
            compacted_block = (
                "\n\nACTIVE DESKTOP CONTEXT & CONVERSATION STATE (ROLLING CONTEXT COMPACTOR):\n"
                "The following bullet points summarize the active state and pending workflows from the previous session turns:\n"
                f"{active_summary}\n\n"
                "DIRECTIVE: Seamlessly continue assisting the user without re-introducing yourself, greeting them again, or losing context of the above state."
            )

        return base_instruction + compacted_block

    def flush_session_to_disk(self) -> Optional[str]:
        """
        Writes current session turns to raw JSON transcript in data/chats/
        and dispatches background manifest card extraction and indexing.
        Returns the flushed session_id, or None if no turns were recorded.
        """
        with self._lock:
            if not self.raw_transcript:
                return None

            filepath = os.path.join(self.chats_dir, f"{self.session_id}.json")
            start_t = self.raw_transcript[0]["timestamp"] if self.raw_transcript else self.session_start_time
            end_t = self.raw_transcript[-1]["timestamp"] if self.raw_transcript else time.time()
            payload = {
                "session_id": self.session_id,
                "start_time": start_t,
                "end_time": end_t,
                "turns": list(self.raw_transcript)
            }

            try:
                tmp_path = f"{filepath}.{os.getpid()}.tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
                os.replace(tmp_path, filepath)
                logger.info(f"[SESSION] Persisted raw session transcript to {filepath}")
            except Exception as e:
                logger.error(f"[SESSION ERROR] Failed to save session transcript to {filepath}: {e}")
                return None

            # Session transcript is persisted; the idle-aware ReflexionEngine processes manifest extraction
            logger.debug(f"[MANIFEST] Session {filepath} queued for idle Reflexion extraction.")

            old_id = self.session_id
            new_id = f"session_{time.strftime('%Y%m%d_%H%M%S')}"
            if new_id == old_id:
                import datetime
                new_id = f"session_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
            self.session_id = new_id
            self.raw_transcript = []
            return old_id

    def reset_metrics(self):
        """Resets turn count, elapsed time, and tool chars after successful silent reconnect."""
        with self._lock:
            # Ensure any unpersisted transcript turns are flushed to disk before resetting
            if self.raw_transcript:
                self.flush_session_to_disk()

            self.turn_count = 0
            self.session_start_time = time.time()
            self.tool_execution_chars = 0
            # Retain last 2 turns to bridge the prompt continuity (sanitized of any refusals)
            self.turn_history = sanitize_turn_history(self.turn_history[-2:])
            self.total_rotations += 1
            self.rotation_in_progress = False
            logger.info(f"[LIFECYCLE] Metrics reset. Total lifetime rotations: {self.total_rotations}")


