"""
core/game_triggers.py - 100% Game-Agnostic Event Triggers (When/If -> Then) & Companion Chit-Chat Engine.

- GameTriggerEngine: Listens to live telemetry events, evaluates optional condition guards,
  respects Spotter-Only safety mode and per-rule cooldowns, and executes matching actions
  strictly sequentially in a background queue (keybind, macro, key_sequence, ui_task, speak).
- CompanionChitChatManager: Delivers organic, non-intrusive companion banter triggered by
  notable telemetry events or idle cruising, with optional Screen Peek and optional Casual Profanity.
"""

import asyncio
import queue
import random
import re
import threading
import time
from typing import Any, Dict, List, Optional

from core.logger import get_logger

logger = get_logger("GameTriggers")


def evaluate_trigger_condition(condition: str, state: Optional[Dict[str, Any]], flags: Optional[Dict[str, bool]] = None) -> bool:
    """
    Safely evaluates a simple boolean guard expression against telemetry state/flags.
    Supports:
      - `state.docked`, `!state.hardpoints_deployed`, `not state.in_combat`
      - `state.fuel_pct < 25`, `state.hull >= 80`, `state.star_system == Sol`
      - Compound `and` / `&&` clauses
    Returns True if condition is empty or satisfied.
    """
    cond = str(condition or "").strip()
    if not cond:
        return True

    st = dict(state or {})
    if isinstance(flags, dict):
        for fk, fv in flags.items():
            st.setdefault(fk, fv)

    clauses = [c.strip() for c in re.split(r"\s+(?:and|&&)\s+", cond, flags=re.IGNORECASE) if c.strip()]
    for clause in clauses:
        neg = False
        if clause.startswith("!"):
            neg = True
            clause = clause[1:].strip()
        elif clause.lower().startswith("not "):
            neg = True
            clause = clause[4:].strip()

        # Comparison operator?
        cmp_match = re.match(r"^(?:state\.)?([A-Za-z0-9_]+)\s*(==|!=|<=|>=|<|>)\s*(.+)$", clause)
        if cmp_match:
            key, op, raw_rhs = cmp_match.group(1), cmp_match.group(2), cmp_match.group(3).strip().strip("'\"")
            lhs = st.get(key)
            ok = False
            try:
                lhs_f = float(lhs)
                rhs_f = float(raw_rhs)
                if op == "==":
                    ok = lhs_f == rhs_f
                elif op == "!=":
                    ok = lhs_f != rhs_f
                elif op == "<":
                    ok = lhs_f < rhs_f
                elif op == "<=":
                    ok = lhs_f <= rhs_f
                elif op == ">":
                    ok = lhs_f > rhs_f
                elif op == ">=":
                    ok = lhs_f >= rhs_f
            except (TypeError, ValueError):
                lhs_s = str(lhs if lhs is not None else "").strip().lower()
                rhs_s = raw_rhs.lower()
                if op == "==":
                    ok = lhs_s == rhs_s
                elif op == "!=":
                    ok = lhs_s != rhs_s
            if neg:
                ok = not ok
            if not ok:
                return False
            continue

        # Simple truthy flag check
        key = clause[6:] if clause.lower().startswith("state.") else clause
        val = bool(st.get(key, False))
        if neg:
            val = not val
        if not val:
            return False

    return True


def _dispatch_engine_proactive_prompt(engine: Any, prompt: str) -> bool:
    """Safely schedules `engine.inject_tactical_alert(prompt)` on the engine's asyncio loop."""
    if engine is None or not prompt:
        return False
    if not getattr(engine, "is_running", False):
        return False
    if getattr(engine, "is_speaking", False) or getattr(engine, "_is_speaking", False):
        return False
    coro_fn = getattr(engine, "inject_tactical_alert", None)
    if not callable(coro_fn):
        return False
    loop = getattr(engine, "loop", None) or getattr(engine, "_loop", None)
    try:
        if loop is not None and getattr(loop, "is_running", lambda: False)():
            asyncio.run_coroutine_threadsafe(coro_fn(prompt), loop)
            return True
    except Exception as e:
        logger.debug("[TRIGGERS] Failed scheduling proactive prompt: %s", e)
    return False


class GameTriggerEngine:
    """
    Executes declarative `When / If -> Then` automation rules sequentially in a worker thread.
    """

    def __init__(self, game_manager: Any, engine: Any = None):
        self.game_manager = game_manager
        self.engine = engine
        self._queue: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self._cooldowns: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._running = True
        self._worker = threading.Thread(target=self._worker_loop, daemon=True, name="GameTriggerWorker")
        self._worker.start()

    def stop(self) -> None:
        self._running = False
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass

    def handle_event(
        self,
        game_id: str,
        event_type: str,
        raw_event: Optional[Dict[str, Any]] = None,
        state_snapshot: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Evaluates all enabled triggers for `game_id` against `event_type` and enqueues matches.
        Returns the number of rules enqueued.
        """
        if not self._running or not game_id or not event_type:
            return 0
        if not self.game_manager or not hasattr(self.game_manager, "get_triggers"):
            return 0

        triggers = self.game_manager.get_triggers(game_id)
        if not triggers:
            return 0

        ev_lower = str(event_type).strip().lower()
        adapter = getattr(self.game_manager, "active_telemetry_adapter", None)
        flags = adapter.get_state_flags() if (adapter and hasattr(adapter, "get_state_flags")) else {}
        now = time.time()
        enqueued = 0

        for rule in triggers:
            if not isinstance(rule, dict) or not rule.get("enabled", False):
                continue
            rule_ev = str(rule.get("event") or "").strip().lower()
            if not rule_ev or rule_ev != ev_lower:
                continue

            # Condition guard
            cond = str(rule.get("condition") or "").strip()
            if cond and not evaluate_trigger_condition(cond, state_snapshot, flags):
                continue

            # Per-rule cooldown
            tid = str(rule.get("id") or rule.get("name") or "")
            cd_key = f"{game_id}:{tid}"
            cd_sec = max(0.0, float(rule.get("cooldown_sec") or 5.0))
            with self._lock:
                last_ts = self._cooldowns.get(cd_key, 0.0)
                if (now - last_ts) < cd_sec:
                    continue
                self._cooldowns[cd_key] = now

            self._queue.put({
                "game_id": game_id,
                "rule": dict(rule),
                "raw_event": dict(raw_event or {}),
                "state_snapshot": dict(state_snapshot or {}),
            })
            enqueued += 1

        return enqueued

    def _worker_loop(self) -> None:
        while self._running:
            try:
                job = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if job is None:
                break
            try:
                self.execute_trigger_rule(
                    game_id=job["game_id"],
                    rule=job["rule"],
                    raw_event=job.get("raw_event"),
                    state_snapshot=job.get("state_snapshot"),
                    is_test=False,
                )
            except Exception as e:
                logger.warning("[TRIGGERS] Rule execution error: %s", e)

    def execute_trigger_rule(
        self,
        game_id: str,
        rule: Dict[str, Any],
        raw_event: Optional[Dict[str, Any]] = None,
        state_snapshot: Optional[Dict[str, Any]] = None,
        is_test: bool = False,
    ) -> Dict[str, Any]:
        """Executes a single trigger rule's THEN action (respects Spotter-Only safety mode)."""
        if not isinstance(rule, dict):
            return {"status": "error", "message": "Invalid trigger rule."}

        action_type = str(rule.get("action_type") or "keybind").strip().lower()
        rule_name = str(rule.get("name") or rule.get("id") or "Trigger")

        # Anti-cheat Spotter-Only safety enforcement
        input_mode = (
            self.game_manager.get_input_mode(game_id)
            if (self.game_manager and hasattr(self.game_manager, "get_input_mode"))
            else "autonomous"
        )
        if input_mode == "spotter_only" and action_type != "speak":
            return {
                "status": "blocked_by_spotter_only",
                "rule": rule_name,
                "message": f"Trigger '{rule_name}' blocked in Spotter-Only (Anti-Cheat Safe) mode.",
            }

        delay_ms = max(0, int(rule.get("delay_ms") or 0))
        if delay_ms > 0 and not is_test:
            time.sleep(min(delay_ms, 15000) / 1000.0)

        logger.info("[TRIGGERS] Executing '%s' (type=%s, test=%s)", rule_name, action_type, is_test)

        # 1. Speak / Voice Callout
        if action_type == "speak":
            text = str(rule.get("speak_text") or rule.get("action_value") or "").strip()
            if not text:
                return {"status": "error", "message": "No callout text configured."}
            sent = _dispatch_engine_proactive_prompt(
                self.engine,
                f"[AUTO-TRIGGER CALLOUT] Deliver this concise in-character update to the player right now: {text}",
            )
            return {"status": "executed" if sent else "queued", "rule": rule_name, "message": f"Callout: {text}"}

        # 2. Keybind (with optional hold_ms)
        if action_type == "keybind":
            phrase = str(rule.get("phrase") or rule.get("action_value") or "").strip()
            hold_ms = int(rule.get("hold_ms") or 0)
            if not phrase:
                return {"status": "error", "message": "No keybind phrase configured."}
            if hold_ms > 150:
                from core.game_macro_runner import MacroRunner
                prof = self.game_manager.get_profile(game_id) or {}
                runner = MacroRunner()
                err = runner._execute_single_step({"action": "trigger_bind", "phrase": phrase, "hold_ms": hold_ms}, prof)
                return err or {"status": "executed", "rule": rule_name, "message": f"Triggered '{phrase}' (hold {hold_ms}ms)."}
            return self.game_manager.execute_keybind(phrase, game_id=game_id)

        # 3. Saved Macro
        if action_type == "macro":
            macro_name = str(rule.get("macro_name") or rule.get("action_value") or "").strip()
            if not macro_name:
                return {"status": "error", "message": "No macro name configured."}
            return self.game_manager.execute_keybind(macro_name, game_id=game_id)

        # 4. Raw Key / Click Sequence
        if action_type == "key_sequence":
            steps = rule.get("steps")
            if not isinstance(steps, list) or not steps:
                return {"status": "error", "message": "No key sequence steps configured."}
            from core.game_macro_runner import MacroRunner
            prof = self.game_manager.get_profile(game_id) or {}
            client = None
            if self.engine is not None:
                client = getattr(self.engine, "genai_client", None) or getattr(self.engine, "client", None)
            runner = MacroRunner()
            for st in steps:
                if isinstance(st, dict):
                    err = runner._execute_single_step(st, prof, vision_client=client)
                    if err:
                        return err
            return {"status": "executed", "rule": rule_name, "message": f"Executed {len(steps)} step(s) for '{rule_name}'."}

        # 5. Closed-loop Vision UI Task (e.g., Refuel ship, Repair ship, Restock ship)
        if action_type == "ui_task":
            goal = str(rule.get("goal") or rule.get("action_value") or "").strip()
            if not goal:
                return {"status": "error", "message": "No UI task goal configured."}
            client = None
            if self.engine is not None:
                client = getattr(self.engine, "genai_client", None) or getattr(self.engine, "client", None)
            if client is None:
                client = getattr(self.game_manager, "vision_client", None)
            if client is None:
                return {"status": "error", "message": "Vision client unavailable for UI task trigger."}
            from core.game_nav import UniversalGameNavigator
            prof = self.game_manager.get_profile(game_id) or {}
            target_proc = prof.get("process_name") or ""
            nav = UniversalGameNavigator(
                client=client,
                target_process=target_proc,
                map_controls=self.game_manager.get_map_controls(game_id) if hasattr(self.game_manager, "get_map_controls") else {},
                game_name=prof.get("display_name") or game_id,
                keybinds=prof.get("keybinds") or {},
                telemetry_adapter=getattr(self.game_manager, "active_telemetry_adapter", None),
            )
            goal_key = goal.lower().strip()
            learned = (prof.get("learned_ui_recipes") or {}).get(goal_key) if isinstance(prof.get("learned_ui_recipes"), dict) else None
            res = nav.complete_ui_task(goal=goal, max_steps=6, preferred_input="auto", learned_recipe=learned)
            if (
                isinstance(res, dict)
                and res.get("status") == "verified_done"
                and isinstance(res.get("learned_recipe"), dict)
                and res["learned_recipe"].get("actions")
            ):
                try:
                    prof.setdefault("learned_ui_recipes", {})[goal_key] = res["learned_recipe"]
                    self.game_manager._save_profiles(self.game_manager.data, target_game_id=game_id)
                except Exception:
                    pass
            return res

        return {"status": "error", "message": f"Unsupported trigger action_type: '{action_type}'."}


class CompanionChitChatManager:
    """
    100% game-agnostic proactive companion banter engine.
    Reacts organically to telemetry events & quiet cruising, with optional Screen Peek
    and optional Casual Profanity.
    """

    FREQUENCY_PROFILES: Dict[str, Dict[str, float]] = {
        "off": {"cooldown": 999999.0, "event_prob": 0.0, "idle_sec": 999999.0},
        "rare": {"cooldown": 300.0, "event_prob": 0.22, "idle_sec": 480.0},
        "occasional": {"cooldown": 150.0, "event_prob": 0.42, "idle_sec": 300.0},
        "chatty": {"cooldown": 75.0, "event_prob": 0.65, "idle_sec": 180.0},
    }

    def __init__(self, game_manager: Any, engine: Any = None):
        self.game_manager = game_manager
        self.engine = engine
        self._last_chitchat_ts: float = 0.0
        self._last_event_ts: float = time.time()
        self._recent_topics: List[str] = []
        self._visited_locations: set = set()
        self._banter_angles = [
            "scenic_observation",
            "goal_checkin",
            "recent_discovery_callback",
            "in_universe_musing",
        ]
        self._banter_idx: int = 0
        self._lock = threading.Lock()
        self._running = True
        self._idle_thread = threading.Thread(target=self._idle_loop, daemon=True, name="CompanionChitChatLoop")
        self._idle_thread.start()

    def stop(self) -> None:
        self._running = False

    def _get_settings(self, game_id: str) -> Dict[str, Any]:
        prof = self.game_manager.get_profile(game_id) if (self.game_manager and game_id) else None
        if not isinstance(prof, dict):
            return {"frequency": "off", "screen_peek": False, "allow_profanity": False, "emotive_voice": False}
        freq = str(prof.get("chitchat_frequency") or "off").strip().lower()
        if freq not in self.FREQUENCY_PROFILES:
            freq = "off"
        return {
            "frequency": freq,
            "screen_peek": bool(prof.get("chitchat_screen_peek", False)),
            "allow_profanity": bool(prof.get("chitchat_allow_profanity", False)),
            "emotive_voice": bool(prof.get("chitchat_emotive_voice", False)),
        }

    def _is_suppressed_by_companion(self, game_id: str) -> bool:
        if not self.game_manager or not hasattr(self.game_manager, "should_suppress_alerts"):
            return False
        try:
            return bool(self.game_manager.should_suppress_alerts(game_id))
        except Exception:
            return False

    def _can_chitchat_now(self, game_id: str, settings: Dict[str, Any]) -> bool:
        if settings["frequency"] == "off":
            return False
        if not self.engine or not getattr(self.engine, "is_running", False):
            return False
        if not getattr(self.game_manager, "game_mode_enabled", False):
            return False
        if getattr(self.engine, "is_speaking", False) or getattr(self.engine, "_is_speaking", False):
            return False
        if getattr(self.engine, "is_user_speaking", False) or getattr(self.engine, "_user_speaking", False):
            return False
        if self._is_suppressed_by_companion(game_id):
            return False
        cfg = self.FREQUENCY_PROFILES[settings["frequency"]]
        with self._lock:
            if (time.time() - self._last_chitchat_ts) < cfg["cooldown"]:
                return False
        return True

    def _describe_screen_peek(self, game_id: str) -> Dict[str, Any]:
        """
        100% genre-universal viewport glance for Companion Chit-Chat.
        Returns `{"notable": bool, "urgent": bool, "scene": str}`.
        Works for space sims, survival/horror (e.g. enemy hordes, stealth, weather),
        RPGs, shooters, and open-world games alike; ignores pause/inventory menus.
        """
        empty = {"notable": False, "urgent": False, "mood": "calm", "scene": ""}
        client = None
        if self.engine is not None:
            client = getattr(self.engine, "genai_client", None) or getattr(self.engine, "client", None)
        if client is None:
            return empty
        try:
            import json as _json
            from core.game_nav import UniversalGameNavigator
            from google.genai import types as genai_types
            prof = self.game_manager.get_profile(game_id) or {}
            game_name = prof.get("display_name") or game_id
            nav = UniversalGameNavigator(client=client, target_process=prof.get("process_name") or "")
            img_bytes, _, _ = nav.capture_viewport()
            if not img_bytes:
                return empty
            from core.model_discovery import get_configured_game_model
            cfg = self.engine.config if (self.engine is not None and isinstance(getattr(self.engine, "config", None), dict)) else None
            model, _ = get_configured_game_model(cfg, prefer_fast=True)
            peek_prompt = (
                f"You are glancing at a live gameplay frame of '{game_name}'. "
                "Is this active gameplay (not a pause menu, settings screen, or loading screen) with something visually "
                "notable, atmospheric, tense, or dangerous (e.g., enemy horde, ambush, stealth tension, boss, combat, "
                "dramatic weather/scenery, multiple bright stars, neutron star jet cones, docking bay, cool landmark, low health)?\n"
                "Return ONLY JSON: "
                '{"notable": true/false, "urgent": true/false, "mood": "stealth"|"combat"|"awe"|"calm"|"celebration", "scene": "1 vivid short sentence describing what is visible, or empty string if menu/boring"}'
            )
            resp = client.models.generate_content(
                model=model,
                contents=[
                    genai_types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                    peek_prompt,
                ],
            )
            raw = (getattr(resp, "text", None) or "").strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
            try:
                parsed = _json.loads(raw)
                if isinstance(parsed, dict):
                    scene = str(parsed.get("scene") or "").strip()[:240]
                    mood = str(parsed.get("mood") or "calm").strip().lower()
                    if mood not in ("stealth", "combat", "awe", "calm", "celebration"):
                        mood = "calm"
                    return {
                        "notable": bool(parsed.get("notable", bool(scene))),
                        "urgent": bool(parsed.get("urgent", False)),
                        "mood": mood,
                        "scene": scene,
                    }
            except Exception:
                if raw:
                    return {"notable": True, "urgent": False, "mood": "calm", "scene": raw[:240]}
            return empty
        except Exception as e:
            logger.debug("[CHITCHAT] Screen peek skipped: %s", e)
            return empty

    @staticmethod
    def _resolve_emotive_cue(
        event_type: str = "",
        state_snapshot: Optional[Dict[str, Any]] = None,
        peek_mood: str = "calm",
        urgent: bool = False,
    ) -> str:
        """Maps live gameplay context/mood to a native-audio vocal delivery stage direction."""
        ev = str(event_type or "").lower()
        st = state_snapshot or {}
        mood = str(peek_mood or "calm").lower()

        if mood == "stealth":
            return " [VOCAL DELIVERY: Drop your voice into a hushed, low, tense whisper — quiet and cautious, like whispering 'Psst, be careful...' right next to them.]"
        if (
            mood == "combat"
            or urgent
            or ev in ("underattack", "interdicted", "shieldstate", "hulldamage", "heatwarning", "heatdamage", "fighterdestroyed", "died")
            or st.get("in_combat")
            or st.get("overheating")
        ):
            return " [VOCAL DELIVERY: Speak at a brisk, slightly frantic, adrenaline-charged combat pace — sharp and urgent.]"
        if mood == "awe" or ev in ("jetconeboost", "codexentry", "fssallbodiesfound", "saascancomplete", "scanorganic"):
            return " [VOCAL DELIVERY: Sound genuinely awestruck, warm, and slightly breathless at the sight/discovery.]"
        if mood == "celebration" or ev in ("bounty", "missioncompleted", "escapeinterdiction", "marketsell"):
            return " [VOCAL DELIVERY: Upbeat, confident, and celebratory energy.]"
        return " [VOCAL DELIVERY: Relaxed, warm, conversational co-pilot pace.]"

    def _build_chitchat_prompt(
        self,
        game_id: str,
        context_note: str,
        settings: Dict[str, Any],
        screen_note: str = "",
        urgent: bool = False,
        banter_angle: str = "",
        event_type: str = "",
        state_snapshot: Optional[Dict[str, Any]] = None,
        peek_mood: str = "calm",
    ) -> str:
        callsign = self.game_manager.get_callsign(game_id) if hasattr(self.game_manager, "get_callsign") else ""
        profanity_rule = (
            " Casual PG-13/R-rated profanity (e.g. 'holy shit', 'bloody hell', 'damn', 'what the hell') is allowed and encouraged "
            "when reacting to startling, intense, or awe-inspiring moments — keep it organic and never forced."
            if settings.get("allow_profanity")
            else " Keep language clean (no profanity)."
        )
        recent_avoid = ""
        with self._lock:
            if self._recent_topics:
                recent_avoid = f" Avoid repeating recent topics: {', '.join(self._recent_topics[-5:])}."
        screen_part = f" Live visual sight: {screen_note}." if screen_note else ""
        emotive_part = (
            self._resolve_emotive_cue(event_type=event_type, state_snapshot=state_snapshot, peek_mood=peek_mood, urgent=urgent)
            if settings.get("emotive_voice")
            else (
                " Tone: hushed/urgent wingman warning (e.g. 'Pssst, watch out...' or 'Heads up!')."
                if urgent
                else ""
            )
        )
        angle_hints = {
            "scenic_observation": " Focus on the atmosphere, surroundings, or current journey.",
            "goal_checkin": " Casually riff on the player's active goal or checklist if present, otherwise make an organic travel remark.",
            "recent_discovery_callback": " Optionally call back to a recent discovery or milestone from this session.",
            "in_universe_musing": " Share a brief, grounded in-universe companion thought or witty observation during the quiet stretch.",
        }
        angle_part = angle_hints.get(banter_angle, "")
        addr = f" (you may address them as {callsign.split(';')[0].strip()})" if callsign else ""
        return (
            f"[COMPANION CHIT-CHAT] Make a spontaneous, natural 1-2 sentence in-character co-pilot remark{addr} "
            f"reacting to what's happening right now. Context: {context_note}.{screen_part}{emotive_part}{angle_part}"
            f"{profanity_rule}{recent_avoid} Sound like a real wingman/co-pilot, NOT an AI assistant."
        )

    def _enrich_event_context(
        self,
        event_type: str,
        raw_event: Optional[Dict[str, Any]],
        state_snapshot: Optional[Dict[str, Any]],
    ) -> str:
        ev = raw_event or {}
        st = state_snapshot or {}
        loc = st.get("star_system") or st.get("zone") or st.get("region") or ev.get("StarSystem") or ""
        stn = st.get("station") or ev.get("StationName") or ""
        bits = [f"Event={event_type}"]
        if loc:
            bits.append(f"Location={loc}")
        if stn:
            bits.append(f"Port/Site={stn}")
            with self._lock:
                stn_key = f"{loc}:{stn}".lower()
                if stn_key not in self._visited_locations:
                    self._visited_locations.add(stn_key)
                    if str(event_type).lower() in ("docked", "approachsettlement", "touchdown"):
                        bits.append(f"First time arriving at '{stn}' this session")
        sec = str(ev.get("SystemSecurity_Localised") or ev.get("SystemSecurity") or "").strip()
        if sec and ("anarchy" in sec.lower() or "lawless" in sec.lower()):
            bits.append("Dangerous Anarchy/Lawless territory")
        if ev.get("TotalSale"):
            bits.append(f"Trade sale payout={ev.get('TotalSale')}")
        if ev.get("Message_Localised") and ev.get("From"):
            bits.append(f"Incoming hail from {ev.get('From')}: {str(ev.get('Message_Localised'))[:90]}")
        return ", ".join(bits)

    def on_telemetry_event(
        self,
        game_id: str,
        event_type: str,
        raw_event: Optional[Dict[str, Any]] = None,
        state_snapshot: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Potentially emits an organic event-triggered chit-chat remark."""
        self._last_event_ts = time.time()
        settings = self._get_settings(game_id)
        if not self._can_chitchat_now(game_id, settings):
            return False

        # Check module's declarative chitchat_events list (if defined)
        allowed_events = None
        try:
            from core.game_modules import get_module_for_game
            mod = get_module_for_game(game_id, profile=self.game_manager.get_profile(game_id))
            if isinstance(mod, dict) and isinstance(mod.get("chitchat_events"), list) and mod["chitchat_events"]:
                allowed_events = {str(e).lower() for e in mod["chitchat_events"]}
        except Exception:
            pass
        if allowed_events is not None and str(event_type).lower() not in allowed_events:
            return False

        cfg = self.FREQUENCY_PROFILES[settings["frequency"]]
        if random.random() > cfg["event_prob"]:
            return False

        with self._lock:
            self._last_chitchat_ts = time.time()
            self._recent_topics.append(str(event_type))
            if len(self._recent_topics) > 10:
                self._recent_topics = self._recent_topics[-10:]

        def _run_bg():
            ctx_note = self._enrich_event_context(event_type, raw_event, state_snapshot)
            peek = self._describe_screen_peek(game_id) if settings["screen_peek"] else {"notable": False, "urgent": False, "mood": "calm", "scene": ""}
            prompt = self._build_chitchat_prompt(
                game_id,
                ctx_note,
                settings,
                screen_note=peek.get("scene", ""),
                urgent=bool(peek.get("urgent", False)),
                event_type=event_type,
                state_snapshot=state_snapshot,
                peek_mood=peek.get("mood", "calm"),
            )
            _dispatch_engine_proactive_prompt(self.engine, prompt)

        threading.Thread(target=_run_bg, daemon=True, name="ChitChatEventWorker").start()
        return True

    def _idle_loop(self) -> None:
        while self._running:
            time.sleep(30.0)
            if not self._running or not self.game_manager:
                continue
            active_id = self.game_manager.data.get("active_profile", "")
            if not active_id:
                continue
            settings = self._get_settings(active_id)
            if not self._can_chitchat_now(active_id, settings):
                continue

            cfg = self.FREQUENCY_PROFILES[settings["frequency"]]
            adapter = getattr(self.game_manager, "active_telemetry_adapter", None)
            has_live_tel = bool(adapter and hasattr(adapter, "is_active") and adapter.is_active())

            # Vision-only games (e.g. survival/open-world games with no telemetry file) use the faster
            # cooldown cadence when Screen Peek is enabled so Aether can spot threats/hordes in time.
            if not has_live_tel and settings["screen_peek"]:
                peek = self._describe_screen_peek(active_id)
                if not peek.get("notable"):
                    continue
                with self._lock:
                    self._last_chitchat_ts = time.time()
                prof = self.game_manager.get_profile(active_id) or {}
                gname = prof.get("display_name") or active_id
                prompt = self._build_chitchat_prompt(
                    active_id,
                    f"Live visual observation while playing {gname}",
                    settings,
                    screen_note=peek.get("scene", ""),
                    urgent=bool(peek.get("urgent", False)),
                    peek_mood=peek.get("mood", "calm"),
                )
                _dispatch_engine_proactive_prompt(self.engine, prompt)
                continue

            # Quiet cruising / deep-space ambient banter
            if (time.time() - max(self._last_chitchat_ts, self._last_event_ts)) < cfg["idle_sec"]:
                continue

            with self._lock:
                self._last_chitchat_ts = time.time()
                angle = self._banter_angles[self._banter_idx % len(self._banter_angles)]
                self._banter_idx += 1

            prof = self.game_manager.get_profile(active_id) or {}
            st = adapter.get_state() if (adapter and hasattr(adapter, "get_state")) else {}
            loc = st.get("star_system") or st.get("zone") or st.get("region") or prof.get("display_name") or "current area"
            goals = str(prof.get("scratchpad_raw") or "").strip()[:160]
            log_entries = prof.get("copilot_log") or []
            recent_log = str(log_entries[-1].get("summary") or "")[:140] if (log_entries and isinstance(log_entries[-1], dict)) else ""
            ctx_parts = [f"Quiet stretch in {loc}"]
            if goals:
                ctx_parts.append(f"Player goals: {goals}")
            if recent_log:
                ctx_parts.append(f"Recent discovery: {recent_log}")
            peek = self._describe_screen_peek(active_id) if settings["screen_peek"] else {"notable": False, "urgent": False, "mood": "calm", "scene": ""}
            prompt = self._build_chitchat_prompt(
                active_id,
                " | ".join(ctx_parts),
                settings,
                screen_note=peek.get("scene", ""),
                urgent=bool(peek.get("urgent", False)),
                banter_angle=angle,
                state_snapshot=st,
                peek_mood=peek.get("mood", "calm"),
            )
            _dispatch_engine_proactive_prompt(self.engine, prompt)
