"""
Aether Desktop - Rolling Session Compactor & Lifecycle Manager (Layer B)
Tracks session age, turn telemetry, and tool token weight.
Generates asynchronous background conversation state summaries and orchestrates
silent WebSocket session re-anchoring without audio stream interruption.
"""

import asyncio
import os
import time
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
from core.logger import get_logger

logger = get_logger("SessionLifecycle")

# Default rotation trigger thresholds
DEFAULT_MAX_TURNS = 25
DEFAULT_MAX_DURATION_SECONDS = 1800.0  # 30 minutes
DEFAULT_MAX_TOOL_CHARS = 50000         # 50k characters of tool stdout/tracebacks

COMPACTOR_PROMPT = """Summarize the active desktop context and conversation state into exactly 4-6 concise bullet points:
- Active user tasks or pending desktop workflows.
- Key facts established in this session.
- Any active window states or working files currently in focus.
Omit greetings and small talk. Keep it purely operational."""


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
        rotation_threshold_tool_chars: int = DEFAULT_MAX_TOOL_CHARS
    ):
        self.rotation_threshold_turns = rotation_threshold_turns
        self.rotation_threshold_duration = rotation_threshold_duration
        self.rotation_threshold_tool_chars = rotation_threshold_tool_chars

        self.turn_count: int = 0
        self.session_start_time: float = time.time()
        self.tool_execution_chars: int = 0
        self.turn_history: List[Dict[str, str]] = []
        self.last_summary: str = ""
        self.rotation_in_progress: bool = False
        self.total_rotations: int = 0

    def record_turn(self, role: str, content: str):
        """Records a user utterance or model response in turn history."""
        clean_content = str(content).strip()
        if not clean_content:
            return

        self.turn_history.append({
            "role": role,
            "content": clean_content,
            "timestamp": time.time()
        })

        # Cap retained turns in memory to avoid unbounded growth
        if len(self.turn_history) > 60:
            self.turn_history = self.turn_history[-40:]

        if role in ("user", "assistant", "model"):
            self.turn_count += 1
            logger.debug(f"[LIFECYCLE] Turn #{self.turn_count} ({role}): {clean_content[:80]}...")

    def record_tool_chars(self, char_count: int):
        """Accumulates tool payload character count to gauge token consumption."""
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
        if self.rotation_in_progress:
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

    async def generate_session_summary(
        self,
        client: Optional[genai.Client],
        model: str = "gemini-2.5-flash"
    ) -> str:
        """
        Asynchronously compacts the active conversation history into 4-6 operational bullet points
        using a fast, non-blocking call to gemini-2.5-flash.
        """
        if not self.turn_history:
            return "- Session newly initialized; no prior actions."

        # Format transcript lines for the compactor
        transcript_lines = []
        for t in self.turn_history[-30:]:
            role_label = "User" if t["role"] == "user" else "Assistant"
            transcript_lines.append(f"{role_label}: {t['content']}")
        transcript_text = "\n".join(transcript_lines)

        full_prompt = (
            f"Below is the recent conversation and desktop task history:\n"
            f"---\n"
            f"{transcript_text}\n"
            f"---\n\n"
            f"{COMPACTOR_PROMPT}"
        )

        logger.info(f"[LIFECYCLE COMPACTOR] Generating state summary via {model} ({len(self.turn_history)} turns)...")

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
            recent_turns = self.turn_history[-6:]
            bullets = []
            for t in recent_turns:
                r = "User asked" if t["role"] == "user" else "Assistant performed"
                snippet = t["content"].replace("\n", " ")[:120]
                bullets.append(f"- {r}: {snippet}")
            summary_text = "\n".join(bullets[:5]) if bullets else "- Ongoing user session continuing without prior tasks."

        self.last_summary = summary_text
        logger.info(f"[LIFECYCLE COMPACTOR] State summary produced ({len(summary_text)} chars):\n{summary_text}")
        return summary_text

    def build_compacted_instruction(self, base_instruction: str, summary: Optional[str] = None) -> str:
        """
        Appends the 4-6 bullet state summary into the system instruction for the newly rotated session.
        """
        active_summary = summary or self.last_summary
        if not active_summary:
            return base_instruction

        compacted_block = (
            "\n\nACTIVE DESKTOP CONTEXT & CONVERSATION STATE (ROLLING CONTEXT COMPACTOR):\n"
            "The following bullet points summarize the active state and pending workflows from the previous session turns:\n"
            f"{active_summary}\n\n"
            "DIRECTIVE: Seamlessly continue assisting the user without re-introducing yourself, greeting them again, or losing context of the above state."
        )

        return base_instruction + compacted_block

    def reset_metrics(self):
        """Resets turn count, elapsed time, and tool chars after successful silent reconnect."""
        self.turn_count = 0
        self.session_start_time = time.time()
        self.tool_execution_chars = 0
        # Retain last 2 turns to bridge the prompt continuity
        self.turn_history = self.turn_history[-2:]
        self.total_rotations += 1
        self.rotation_in_progress = False
        logger.info(f"[LIFECYCLE] Metrics reset. Total lifetime rotations: {self.total_rotations}")

