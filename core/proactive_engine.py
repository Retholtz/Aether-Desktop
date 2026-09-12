"""
Aether Desktop - Proactive Briefing Engine
Evaluates user facts, dates, and interests against notification preferences
at session startup to synthesize personalized proactive spoken briefings.
"""

import datetime
import os
import re
from typing import Dict, List, Optional, Any

from core.logger import get_logger
from core.user_memory import UserMemory

logger = get_logger("ProactiveEngine")


def parse_date_event(date_str: str) -> Optional[tuple]:
    """Extracts (month, day) from various date formats."""
    s = str(date_str).strip()
    m = re.match(r"^\d{4}[-/](\d{1,2})[-/](\d{1,2})$", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^(\d{1,2})[-/](\d{1,2})$", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    for fmt in ["%c %d", "%b %d", "%B %d, ", "%b %d, %Y", "%Y-%m-%d"]:
        try:
            dt = datetime.datetime.strptime(s, fmt)
            return dt.month, dt.day
        except ValueError:
            pass
    return None


def days_until_annual_event(month: int, day: int, today: Optional[datetime.date] = None) -> int:
    """Calculates days remaining until the next annual occurrence of a date."""
    today = today or datetime.date.today()
    try:
        candidate = datetime.date(today.year, month, day)
    except ValueError:
        candidate = datetime.date(today.year, month, day - 1)

    delta = (candidate - today).days
    if delta < 0:
        try:
            candidate = datetime.date(today.year + 1, month, day)
        except ValueError:
            candidate = datetime.date(today.year + 1, month, day - 1)
        delta = (candidate - today).days
    return delta


class ProactiveEngine:
    """Evaluates stored user memory and triggers proactive alerts upon startup."""

    def __init__(self, memory: Optional[UserMemory] = None):
        self.memory = memory or UserMemory()

    def evaluate_candidates(self, today: Optional[datetime.date] = None) -> List[Dict[str, Any]]:
        """Scans active facts and preferences to identify any candidate proactive alerts."""
        today = today or datetime.date.today()
        prefs = {p["category"]: p for p in self.memory.get_preferences()}
        candidates = []

        # 1. Date-based alerts (Birthdays, Anniversaries, Deadlines)
        dates_pref = prefs.get("dates", {})
        if dates_pref.get("enabled", 1):
            lead_days = dates_pref.get("lead_time_days", 7)
            facts = self.memory.get_facts()

            for f in facts:
                in_date = (f.get("data_type") == "date") or (f.get("category") == "dates") or ("birthday" in f.get("key", "")) or ("anniversary" in f.get("key", ""))
                if not in_date:
                    continue

                val = f.get("value", "")
                parsed = parse_date_event(val)
                if not parsed:
                    continue

                month, day = parsed
                days_away = days_until_annual_event(month, day, today)

                if 0 <= days_away <= lead_days:
                    # Check if already mentioned today
                    if self.memory.has_prompt_fired_today(fact_id=f["id"]):
                        logger.info(f"[PROACTIVE] Skipping fact #{f['id']} ('{f['key']}'): already notified today.")
                        continue

                    # Human-readable event description
                    key_human = f["key"].replace("_", " ")
                    if days_away == 0:
                        timing = "is today"
                    elif days_away == 1:
                        timing = "is tomorrow"
                    else:
                        timing = f"is in {days_away} days"

                    candidates.append({
                        "type": "date",
                        "fact_id": f["id"],
                        "category": f['category'],
                        "key": f["key"],
                        "value": f['value'],
                        "days_away": days_away,
                        "timing_text": timing,
                        "description": f"{key_human} ({f['value']}) {timing}"
                    })

        # Sort candidates: closest events first
        candidates.sort(key=lambda c: c.get("days_away", 999))
        return candidates

    def _generate_fallback_greeting(self, candidates: List[Dict[str, Any]], agent_name: str = "Aether", user_name: str = "Michael") -> str:
        """Generates a polite, natural fallback proactive briefing without LLM dependency."""
        if not candidates:
            return f"Hello {user_name}, {agent_name} is online and ready to assist."
        primary = candidates[0]
        desc = primary.get("description", "")
        key_human = primary.get("key", "").replace("_", " ")
        timing = primary.get("timing_text", "soon")
        return f"Good day {user_name}, this is {agent_name}. Just a heads up that your {key_human} {timing}."

    async def check_proactive_briefing(
        self,
        user_name: Optional[str] = None,
        genai_client = None,
        cortex_model: str = "gemini-3.8-flash",
        client = None,
        model_id: Optional[str] = None,
        agent_name: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Runs startup evaluation and synthesizes a proactive spoken greeting if candidates exist.
        Logs fired alert to prompt_history.
        """
        genai_client = client or genai_client
        cortex_model = model_id or cortex_model
        agent_name = agent_name or "Aether"
        user_name = (user_name.strip() if user_name else "") or self.memory.get_user_name(default="User")

        candidates = self.evaluate_candidates()
        if not candidates:
            return None

        primary = candidates[0]
        desc = primary["description"]
        timing = primary.get("timing_text", "soon")
        key_human = primary["key"].replace("_", " ")

        greeting_text = ""
        # 1. If Gemini client is available, synthesize a warm personalized greeting
        if genai_client:
            try:
                from google.genai import types
                prompt = (
                    f"You are {agent_name}, {user_name}'s proactive AI desktop assistant. "
                    f"A calendar reminder was triggered: {desc}. "
                    f"Synthesize a warm, brief 1-2 sentence spoken greeting welcoming {user_name} "
                    f"and proactively mentioning this event conversationally. Ask if they need anything prepared."
                )
                resp = await genai_client.aio.models.generate_content(
                    model=cortex_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.7,
                        max_output_tokens=100
                    )
                )
                if resp and resp.text:
                    greeting_text = resp.text.strip()
            except Exception as e:
                logger.warning(f"[PROACTIVE] LMM greeting synthesis failed: {e}")

        # Fallback greeting if offline or synthesis failed
        if not greeting_text:
            greeting_text = self._generate_fallback_greeting(candidates, agent_name=agent_name, user_name=user_name)

        # Record in prompt_history so it does not repeat today
        self.memory.record_prompt(fact_id=primary.get("fact_id"), prompt_text=greeting_text)
        logger.info(f"[PROACTIVE] Briefing generated for {user_name}: '{greeting_text}'")

        return {
            "greeting_text": greeting_text,
            "candidate": primary,
            "total_candidates": len(candidates)
        }
