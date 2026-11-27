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
from typing import Any, Dict, List, Optional, Tuple

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


def _is_game_process_actively_running(game_manager: Any, game_id: str) -> bool:
    """
    Returns True only when the target game is actually running.
    Prevents Chit-Chat, Vision Screen Peek, In-Game Chat Relay, and automatic background
    triggers from firing while the user is just chatting or browsing tabs with no game open.
    """
    if not game_manager or not game_id:
        return False
    prof = game_manager.get_profile(game_id) if hasattr(game_manager, "get_profile") else None
    has_proc_spec = bool(
        isinstance(prof, dict)
        and (str(prof.get("process_name") or "").strip() or str(prof.get("executable") or "").strip())
    )
    if hasattr(game_manager, "is_game_running") and callable(game_manager.is_game_running):
        if has_proc_spec:
            try:
                return bool(game_manager.is_game_running(game_id))
            except Exception:
                return False
    return bool(getattr(game_manager, "game_mode_enabled", False))


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
        if not _is_game_process_actively_running(self.game_manager, game_id):
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
                "enqueued_at": now,
            })
            enqueued += 1

        return enqueued

    def _drain_pending_ui_tasks(self, game_id: str) -> int:
        """Drops any remaining queued background `ui_task` jobs for `game_id` so Aether yields immediately to the player."""
        kept: List[Any] = []
        dropped = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                kept.append(None)
                break
            r = item.get("rule") if isinstance(item, dict) else None
            if (
                isinstance(item, dict)
                and item.get("game_id") == game_id
                and isinstance(r, dict)
                and str(r.get("action_type") or "").strip().lower() == "ui_task"
            ):
                dropped += 1
            else:
                kept.append(item)
        for item in kept:
            self._queue.put(item)
        return dropped

    def _worker_loop(self) -> None:
        while self._running:
            try:
                job = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if job is None:
                break
            try:
                rule = job.get("rule") or {}
                atype = str(rule.get("action_type") or "").strip().lower()
                enq_ts = float(job.get("enqueued_at") or time.time())
                if atype == "ui_task" and (time.time() - enq_ts) > 20.0:
                    logger.info(
                        "[TRIGGERS] Dropping stale queued ui_task '%s' (age=%.1fs) so player controls are not interrupted.",
                        rule.get("name") or rule.get("id"),
                        time.time() - enq_ts,
                    )
                    continue
                res = self.execute_trigger_rule(
                    game_id=job["game_id"],
                    rule=rule,
                    raw_event=job.get("raw_event"),
                    state_snapshot=job.get("state_snapshot"),
                    is_test=False,
                )
                if atype == "ui_task" and isinstance(res, dict) and res.get("status") in ("aborted_user_active", "cannot_complete"):
                    dropped = self._drain_pending_ui_tasks(job["game_id"])
                    if dropped:
                        logger.info(
                            "[TRIGGERS] Yielded to player (%s); cleared %d remaining queued ui_task(s).",
                            res.get("status"),
                            dropped,
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

        # 1. Speak / Voice Callout (supports {field} template placeholders from telemetry state & raw event)
        if action_type == "speak":
            raw_text = str(rule.get("speak_text") or rule.get("action_value") or "").strip()
            if not raw_text:
                return {"status": "error", "message": "No callout text configured."}

            class _SafeFmt(dict):
                def __missing__(self, key):
                    return ""

            fmt_ctx = _SafeFmt()
            if isinstance(state_snapshot, dict):
                fmt_ctx.update(state_snapshot)
            if isinstance(raw_event, dict):
                fmt_ctx.update(raw_event)
            try:
                text = raw_text.format_map(fmt_ctx).strip()
            except Exception:
                text = raw_text
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
                model_endpoint="gemini-3.8-flash",
                target_process=target_proc,
                map_controls=self.game_manager.get_map_controls(game_id) if hasattr(self.game_manager, "get_map_controls") else {},
                game_name=prof.get("display_name") or game_id,
                keybinds=prof.get("keybinds") or {},
                telemetry_adapter=getattr(self.game_manager, "active_telemetry_adapter", None),
            )
            goal_key = goal.lower().strip()
            # Never blind-replay a cached recipe for station main menu dock services (cursor position
            # varies between starports, carriers, and construction sites — always verify visually!).
            skip_replay = ("refuel" in goal_key and "station" in goal_key) or str(rule.get("id") or "") == "auto_dock_services"
            learned = None if skip_replay else ((prof.get("learned_ui_recipes") or {}).get(goal_key) if isinstance(prof.get("learned_ui_recipes"), dict) else None)
            # Background triggers use strict yield-to-player safety: max 3 steps, abort immediately
            # if the player moves the mouse, and NEVER fire Escape/Back cleanup_keys that close open menus.
            res = nav.complete_ui_task(
                goal=goal,
                max_steps=4 if is_test else 3,
                preferred_input="auto",
                learned_recipe=learned,
                abort_on_user_input=not is_test,
                allow_cleanup_keys=is_test,
            )
            logger.info(
                "[TRIGGERS] ui_task '%s' finished: status=%s steps=%s msg=%s",
                rule_name,
                res.get("status") if isinstance(res, dict) else None,
                res.get("steps_executed") if isinstance(res, dict) else None,
                str(res.get("message") or "")[:140] if isinstance(res, dict) else "",
            )
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

        # 6. Pre-Departure Best Trade Check (runs declarative intel_endpoints['trade'] & announces top profit haul)
        if action_type == "trade_check":
            prof = self.game_manager.get_profile(game_id) or {}
            st = dict(state_snapshot or {})
            raw_ev = dict(raw_event or {})
            dest_sys = str(raw_ev.get("destination_system") or st.get("route_destination") or "").strip()
            dest_stn = str(raw_ev.get("destination_station") or rule.get("destination_station") or "").strip()
            origin_stn = str(st.get("station") or "").strip()
            if not origin_stn or (not dest_sys and not dest_stn):
                return {"status": "skipped", "rule": rule_name, "message": "Need docked origin station and plotted destination."}
            try:
                from core.game_modules import get_module_for_game
                from core.game_intel import query_declarative_intel
                mod = get_module_for_game(game_id, profile=prof) or {}
                trade_ep = (prof.get("intel_endpoints") or {}).get("trade") or (mod.get("intel_endpoints") or {}).get("trade")
                if not isinstance(trade_ep, dict) or not trade_ep.get("url"):
                    return {"status": "error", "message": "No declarative trade endpoint configured for this game."}
                res = query_declarative_intel(
                    endpoint_spec=trade_ep,
                    query=dest_stn,
                    location=dest_sys,
                    telemetry_state=st,
                )
                rows = res.get("results") or []
                if res.get("status") != "success" or not rows:
                    return {"status": "no_trade", "rule": rule_name, "message": "No profitable trade found for this route."}
                # Prefer highest-profit item with solid demand (>=100), else top overall
                best = next((r for r in rows if r.get("high_demand")), rows[0])
                min_profit = int(rule.get("min_profit_per_unit") or 500)
                if int(best.get("profit_per_unit") or 0) < min_profit:
                    return {"status": "skipped", "rule": rule_name, "message": "Best profit below threshold."}
                dest_lbl = str(res.get("canonical_query") or dest_stn or dest_sys).split("->")[-1].strip()
                trip_part = (
                    f" — about {best['est_trip_profit']:,} credits for your {best['haul_qty']:,} free tons"
                    if best.get("est_trip_profit")
                    else ""
                )
                callout = (
                    f"Before we undock — if you have cargo space, load up on {best['commodity']} here at {origin_stn} "
                    f"(buy {best['buy_price']:,} CR) to sell at {dest_lbl} for +{best['profit_per_unit']:,} CR per ton{trip_part}!"
                )
                sent = _dispatch_engine_proactive_prompt(
                    self.engine,
                    f"[AUTO-TRIGGER CALLOUT] Deliver this concise, enthusiastic in-character trade tip to the player right now: {callout}",
                )
                return {"status": "executed" if sent else "queued", "rule": rule_name, "message": callout, "best_trade": best}
            except Exception as e:
                return {"status": "error", "rule": rule_name, "message": f"Trade check failed: {e}"}

        return {"status": "error", "message": f"Unsupported trigger action_type: '{action_type}'."}


class CompanionChitChatManager:
    """
    100% game-agnostic proactive companion banter engine.
    Reacts organically to telemetry events & quiet cruising, with optional Screen Peek
    and optional Casual Profanity.
    """

    FREQUENCY_PROFILES: Dict[str, Dict[str, Any]] = {
        "off": {
            "cooldown_range": (999999.0, 999999.0),
            "event_prob": 0.0,
            "idle_range": (999999.0, 999999.0),
        },
        "rare": {
            "cooldown_range": (240.0, 420.0),
            "event_prob": 0.22,
            "idle_range": (360.0, 720.0),
        },
        "occasional": {
            "cooldown_range": (120.0, 220.0),
            "event_prob": 0.42,
            "idle_range": (180.0, 420.0),
        },
        "chatty": {
            "cooldown_range": (55.0, 110.0),
            "event_prob": 0.65,
            "idle_range": (60.0, 240.0),
        },
    }

    _JOKE_THEMES: List[str] = [
        "equipment, engineering, or gear quirks",
        "navigation, travel mishaps, or getting lost",
        "cargo hauling, loot hoarding, or merchant haggling",
        "station/town life, locals, or dock/tavern banter",
        "companion/co-pilot dry wit and wordplay",
        "hazards, close calls, or rookie mistakes",
    ]
    _FACT_THEMES: List[str] = [
        "real-world science, physics, or natural phenomena related to what's around us",
        "in-universe lore, history, or faction trivia",
        "technology, vessel/weapon design, or survival craft trivia",
        "strange anomalies, landmarks, or historical exploration parallels",
    ]

    def __init__(self, game_manager: Any, engine: Any = None):
        self.game_manager = game_manager
        self.engine = engine
        self._last_chitchat_ts: float = 0.0
        self._last_chat_relay_ts: float = 0.0
        self._last_peek_ts: float = 0.0
        self._last_peek_thumb: Optional[Any] = None
        self._last_event_ts: float = time.time()
        self._last_profanity_ts: float = 0.0
        self._session_chitchat_count: int = 0
        self._pending_history_tag: Optional[Tuple[str, str, float]] = None
        self._next_cooldown_sec: float = 0.0
        self._next_idle_sec: float = 0.0
        self._active_freq_rolled: str = ""
        self._recent_topics: List[str] = []
        self._visited_locations: set = set()
        self._joke_theme_idx: int = random.randint(0, len(self._JOKE_THEMES) - 1)
        self._fact_theme_idx: int = random.randint(0, len(self._FACT_THEMES) - 1)
        self._banter_idx: int = 0
        self._lock = threading.Lock()
        self._running = True
        self._idle_thread = threading.Thread(target=self._idle_loop, daemon=True, name="CompanionChitChatLoop")
        self._idle_thread.start()

    def stop(self) -> None:
        self._running = False

    def _roll_timers_locked(self, freq: str) -> None:
        cfg = self.FREQUENCY_PROFILES.get(freq, self.FREQUENCY_PROFILES["off"])
        c_lo, c_hi = cfg["cooldown_range"]
        i_lo, i_hi = cfg["idle_range"]
        self._next_cooldown_sec = random.uniform(c_lo, c_hi)
        self._next_idle_sec = random.uniform(i_lo, i_hi)
        self._active_freq_rolled = freq

    def _get_settings(self, game_id: str) -> Dict[str, Any]:
        prof = self.game_manager.get_profile(game_id) if (self.game_manager and game_id) else None
        if not isinstance(prof, dict):
            return {
                "frequency": "off",
                "screen_peek": False,
                "allow_profanity": False,
                "emotive_voice": False,
                "topic_jokes": False,
                "topic_facts": True,
                "topic_relevant": True,
                "chat_local": False,
                "chat_system": False,
                "chat_npc": False,
                "chat_squadron": True,
                "chat_player": True,
                "chat_translate": True,
            }
        freq = str(prof.get("chitchat_frequency") or "off").strip().lower()
        if freq not in self.FREQUENCY_PROFILES:
            freq = "off"
        return {
            "frequency": freq,
            "screen_peek": bool(prof.get("chitchat_screen_peek", False)),
            "allow_profanity": bool(prof.get("chitchat_allow_profanity", False)),
            "emotive_voice": bool(prof.get("chitchat_emotive_voice", False)),
            "topic_jokes": bool(prof.get("chitchat_topic_jokes", False)),
            "topic_facts": bool(prof.get("chitchat_topic_facts", True)),
            "topic_relevant": bool(prof.get("chitchat_topic_relevant", True)),
            "chat_local": bool(prof.get("chitchat_chat_local", False)),
            "chat_system": bool(prof.get("chitchat_chat_system", False)),
            "chat_npc": bool(prof.get("chitchat_chat_npc", False)),
            "chat_squadron": bool(prof.get("chitchat_chat_squadron", True)),
            "chat_player": bool(prof.get("chitchat_chat_player", True)),
            "chat_translate": bool(prof.get("chitchat_chat_translate", True)),
        }

    def record_spoken_chitchat(self, game_id: str, spoken_text: str) -> None:
        """
        Captures a concise summary of Aether's actual spoken joke/fact right after a Chit-Chat
        turn finishes so `chitchat_history` remembers the exact punchline/fact across sessions.
        """
        clean = re.sub(r"\s+", " ", str(spoken_text or "").strip())
        if not clean or len(clean) < 12:
            return
        with self._lock:
            pending = self._pending_history_tag
            if not pending:
                return
            p_gid, p_angle, p_ts = pending
            if p_gid != game_id or (time.time() - p_ts) > 45.0:
                self._pending_history_tag = None
                return
            self._pending_history_tag = None
        if hasattr(self.game_manager, "record_chitchat_history"):
            self.game_manager.record_chitchat_history(game_id, f"{p_angle}: {clean[:150]}")

    @staticmethod
    def _classify_chat_channel(raw_channel: str) -> str:
        """Maps any game's chat channel name into one of 5 universal buckets."""
        ch = str(raw_channel or "").strip().lower()
        if ch in ("npc", "station", "ai", "world", "bot"):
            return "npc"
        if ch in ("squadron", "guild", "clan", "fleet", "corp", "alliance"):
            return "squadron"
        if ch in ("starsystem", "system", "zone", "region", "global", "general", "trade"):
            return "system"
        if ch in ("local", "area", "say", "proximity", "instance"):
            return "local"
        if ch in ("player", "wing", "party", "friend", "direct", "whisper", "tell", "dm", "voicechat"):
            return "player"
        return "local"

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
        if not _is_game_process_actively_running(self.game_manager, game_id):
            return False
        if getattr(self.engine, "is_speaking", False) or getattr(self.engine, "_is_speaking", False):
            return False
        if getattr(self.engine, "is_user_speaking", False) or getattr(self.engine, "_user_speaking", False):
            return False
        if self._is_suppressed_by_companion(game_id):
            return False
        freq = settings["frequency"]
        with self._lock:
            if self._active_freq_rolled != freq or self._next_cooldown_sec <= 0:
                self._roll_timers_locked(freq)
            if self._last_chitchat_ts > 0 and (time.time() - self._last_chitchat_ts) < self._next_cooldown_sec:
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
            import io as _io
            import json as _json
            import numpy as _np
            from PIL import Image as _PILImage
            from core.game_nav import UniversalGameNavigator
            from core.api_telemetry import api_feature_context, make_image_part
            from google.genai import types as genai_types

            # Task 3 Safety Brake: honor `telemetry.vision_polling_enabled` (defaults False when no game active)
            cfg = self.engine.config if (self.engine is not None and isinstance(getattr(self.engine, "config", None), dict)) else None
            tel_cfg = (cfg or {}).get("telemetry") if isinstance(cfg, dict) else None
            if isinstance(tel_cfg, dict) and tel_cfg.get("vision_polling_enabled") is False:
                return empty
            if not getattr(self.game_manager, "game_mode_enabled", False) or not _is_game_process_actively_running(self.game_manager, game_id):
                if not (isinstance(tel_cfg, dict) and tel_cfg.get("vision_polling_enabled") is True and _is_game_process_actively_running(self.game_manager, game_id)):
                    return empty

            # Enforce minimum cooldown between Vision glances
            min_peek_cd = float((tel_cfg or {}).get("vision_polling_cooldown_sec") or 15.0) if isinstance(tel_cfg, dict) else 15.0
            now_ts = time.time()
            with self._lock:
                if self._last_peek_ts > 0 and (now_ts - self._last_peek_ts) < min_peek_cd:
                    return empty

            prof = self.game_manager.get_profile(game_id) or {}
            game_name = prof.get("display_name") or game_id
            nav = UniversalGameNavigator(client=client, target_process=prof.get("process_name") or "")
            img_bytes, _, _ = nav.capture_viewport()
            if not img_bytes:
                return empty

            # Pixel-diff gate: skip calling Gemini if the frame hasn't changed meaningfully (0 tokens on static HUD)
            diff_thresh = float((tel_cfg or {}).get("vision_pixel_diff_threshold") or 3.5) if isinstance(tel_cfg, dict) else 3.5
            try:
                thumb = _PILImage.open(_io.BytesIO(img_bytes)).convert("L").resize((64, 36))
                arr = _np.asarray(thumb, dtype=_np.float32)
                with self._lock:
                    prev_arr = self._last_peek_thumb
                    if prev_arr is not None and prev_arr.shape == arr.shape:
                        mad = float(_np.mean(_np.abs(arr - prev_arr)))
                        if mad < diff_thresh:
                            return empty
                    self._last_peek_thumb = arr
                    self._last_peek_ts = now_ts
            except Exception:
                with self._lock:
                    self._last_peek_ts = now_ts

            from core.model_discovery import get_configured_game_model, build_thinking_config
            model, _ = get_configured_game_model(cfg, prefer_fast=True)
            peek_prompt = (
                f"You are glancing at a live gameplay frame of '{game_name}'. "
                "Is this active gameplay (not a pause menu, settings screen, or loading screen) with something visually "
                "notable, atmospheric, tense, or dangerous (e.g., enemy horde, ambush, stealth tension, boss, combat, "
                "dramatic weather/scenery, multiple bright stars, neutron star jet cones, docking bay, cool landmark, low health)?\n"
                "Return ONLY JSON: "
                '{"notable": true/false, "urgent": true/false, "mood": "stealth"|"combat"|"awe"|"calm"|"celebration", "scene": "1 vivid short sentence describing what is visible, or empty string if menu/boring"}'
            )
            gen_kwargs: Dict[str, Any] = {
                "response_mime_type": "application/json",
                "temperature": 0.2,
                "max_output_tokens": 256,
            }
            think_cfg = build_thinking_config(model, thinking_level="none")
            if think_cfg is not None:
                gen_kwargs["thinking_config"] = think_cfg
            img_part = make_image_part(img_bytes, resolution="low", mime_type="image/jpeg")
            with api_feature_context("agnostic_telemetry", "screen_peek", keep_latest_images=1):
                resp = client.models.generate_content(
                    model=model,
                    contents=[img_part, peek_prompt],
                    config=genai_types.GenerateContentConfig(**gen_kwargs),
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
                if raw and not raw.lstrip().startswith("{"):
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

    def _should_allow_profanity_now(
        self,
        settings: Dict[str, Any],
        urgent: bool = False,
        peek_mood: str = "calm",
        event_type: str = "",
        banter_angle: str = "",
    ) -> bool:
        """
        Sparsely gates casual profanity so it sounds genuinely organic — never on startup/first line,
        never back-to-back (10-minute cooldown), never on routine jokes/facts/calm docking, and only
        occasionally on truly intense, startling, or awe-inspiring moments.
        """
        if not settings.get("allow_profanity"):
            return False
        if banter_angle in ("joke", "fact"):
            return False
        now = time.time()
        with self._lock:
            # Never open the session with profanity, and enforce a 10-minute cooldown between expletives
            if self._session_chitchat_count <= 0:
                return False
            if self._last_profanity_ts > 0 and (now - self._last_profanity_ts) < 600.0:
                return False
        ev = str(event_type or "").lower()
        mood = str(peek_mood or "calm").lower()
        is_intense = urgent or mood == "combat" or ev in (
            "underattack", "interdicted", "shieldstate", "hulldamage", "heatwarning", "heatdamage", "fighterdestroyed", "died"
        )
        is_dramatic = mood in ("awe", "stealth") or ev in ("jetconeboost", "escapeinterdiction")
        prob = 0.30 if is_intense else (0.12 if is_dramatic else 0.0)
        if prob <= 0.0:
            return False
        if random.random() < prob:
            with self._lock:
                self._last_profanity_ts = now
            return True
        return False

    def _pick_idle_banter_angle(self, settings: Dict[str, Any]) -> Tuple[str, str]:
        """Selects the next banter angle from the player's enabled topic checkboxes (Jokes / Facts / Relevant Info)."""
        pool: List[str] = []
        if settings.get("topic_relevant", True):
            pool.extend(["scenic_observation", "goal_checkin", "recent_discovery_callback"])
        if settings.get("topic_facts", True):
            pool.append("fact")
        if settings.get("topic_jokes", False):
            pool.append("joke")
        if not pool:
            pool = ["scenic_observation"]

        with self._lock:
            angle = pool[self._banter_idx % len(pool)]
            self._banter_idx += 1
            theme_seed = ""
            if angle == "joke":
                theme_seed = self._JOKE_THEMES[self._joke_theme_idx % len(self._JOKE_THEMES)]
                self._joke_theme_idx += 1
            elif angle == "fact":
                theme_seed = self._FACT_THEMES[self._fact_theme_idx % len(self._FACT_THEMES)]
                self._fact_theme_idx += 1
        return angle, theme_seed

    def _build_chitchat_prompt(
        self,
        game_id: str,
        context_note: str,
        settings: Dict[str, Any],
        screen_note: str = "",
        urgent: bool = False,
        banter_angle: str = "",
        theme_seed: str = "",
        event_type: str = "",
        state_snapshot: Optional[Dict[str, Any]] = None,
        peek_mood: str = "calm",
    ) -> str:
        callsign = self.game_manager.get_callsign(game_id) if hasattr(self.game_manager, "get_callsign") else ""
        allow_swear_now = self._should_allow_profanity_now(
            settings=settings,
            urgent=urgent,
            peek_mood=peek_mood,
            event_type=event_type,
            banter_angle=banter_angle,
        )
        profanity_rule = (
            " A single brief, understated casual expletive is okay here ONLY if it genuinely fits the moment — keep it natural and never over-the-top."
            if allow_swear_now
            else " Keep language clean (no profanity on this line)."
        )
        prof = self.game_manager.get_profile(game_id) if (self.game_manager and game_id) else {}
        hist_entries = [str(x) for x in ((prof or {}).get("chitchat_history") or [])[-35:] if str(x).strip()]
        with self._lock:
            session_recent = list(self._recent_topics[-5:])
        avoid_bits = list(dict.fromkeys(session_recent + hist_entries))
        recent_avoid = (
            f" STRICT ANTI-REPETITION — do NOT repeat or recycle any of these past jokes, facts, or topics: {'; '.join(avoid_bits[-35:])}."
            if avoid_bits
            else ""
        )
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
            "joke": (
                f" Tell one fresh, clever, quick in-world joke fitting this game's genre/setting "
                f"(theme angle: {theme_seed or 'co-pilot dry wit'}). Keep it punchy (1-2 sentences) and completely original."
            ),
            "fact": (
                f" Share one genuinely interesting, concise in-universe lore tidbit or real-world fact tied to this game's setting "
                f"(theme angle: {theme_seed or 'setting trivia'}). Keep it conversational (1-2 sentences)."
            ),
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
        if str(event_type).lower() == "repairall" and ev.get("Cost"):
            bits.append(f"Ship repaired after minor hull/module damage (repair bill={ev.get('Cost')} credits — great moment to quip about auto-dock scraping the slot or a rough landing)")
        if str(event_type).lower() == "hulldamage" and ev.get("Health") is not None:
            try:
                hp = round(float(ev.get("Health")) * 100)
                bits.append(f"Hull integrity dropped to {hp}%")
            except Exception:
                pass
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
        """Potentially emits an organic event-triggered chit-chat remark or filtered in-game chat relay."""
        self._last_event_ts = time.time()
        settings = self._get_settings(game_id)
        if settings["frequency"] == "off":
            return False

        # Dedicated In-Game Chat Window Channel Filtering (Local / System / NPC / Squadron / Player + Translate)
        ev_dict = raw_event if isinstance(raw_event, dict) else {}
        is_chat_ev = bool(
            ev_dict.get("Channel") is not None
            and (ev_dict.get("Message_Localised") or ev_dict.get("Message"))
        )
        if is_chat_ev:
            bucket = self._classify_chat_channel(str(ev_dict.get("Channel") or ""))
            if not settings.get(f"chat_{bucket}", False):
                return False
            if not self.engine or not getattr(self.engine, "is_running", False):
                return False
            if not getattr(self.game_manager, "game_mode_enabled", False):
                return False
            if not _is_game_process_actively_running(self.game_manager, game_id):
                return False
            if getattr(self.engine, "is_speaking", False) or getattr(self.engine, "_is_speaking", False):
                return False
            if getattr(self.engine, "is_user_speaking", False) or getattr(self.engine, "_user_speaking", False):
                return False
            if self._is_suppressed_by_companion(game_id):
                return False
            raw_msg_code = str(ev_dict.get("Message") or "").strip()
            msg_txt = str(ev_dict.get("Message_Localised") or raw_msg_code or "").strip()[:240]
            raw_sender = str(ev_dict.get("From_Localised") or ev_dict.get("From") or "").strip()
            if not msg_txt:
                return False

            # Filter out system/channel-join & station-protocol boilerplate that games log on the NPC channel
            # (prevents false callouts like "Someone entered the sector" or "Carrier left the no-fire zone").
            msg_low = msg_txt.lower()
            code_low = raw_msg_code.lower()
            if (
                code_low.startswith("$comms_entered:")
                or msg_low.startswith("entered channel:")
                or code_low.startswith("$station_docking_granted")
                or code_low.startswith("$station_nofirezone_")
                or "no fire zone entered" in msg_low
                or "no fire zone exited" in msg_low
                or (not raw_sender and ("entered channel" in msg_low or "channel:" in msg_low))
            ):
                return False

            now = time.time()
            # Anti-spam dedup for NPC chatter templates (20 min cooldown per repeated template/line)
            if bucket == "npc":
                tpl_key = (code_low if code_low.startswith("$") else msg_low)[:100]
                with self._lock:
                    seen_map = getattr(self, "_recent_npc_templates", None)
                    if seen_map is None:
                        seen_map = {}
                        self._recent_npc_templates = seen_map
                    last_tpl_ts = seen_map.get(tpl_key, 0.0)
                    if (now - last_tpl_ts) < 1200.0:
                        return False
                    seen_map[tpl_key] = now
                    if len(seen_map) > 120:
                        # Prune oldest entries
                        for k_old in sorted(seen_map, key=seen_map.get)[:40]:
                            seen_map.pop(k_old, None)

            # Short 4s anti-burst gate for live Player/Squadron chat; 60s for NPC commentary, 25s for System/Local
            min_gap = 4.0 if bucket in ("player", "squadron") else (60.0 if bucket == "npc" else 25.0)
            with self._lock:
                if (now - self._last_chat_relay_ts) < min_gap:
                    return False
                self._last_chat_relay_ts = now
            sender = raw_sender or "Comms"
            translate_rule = (
                " If the message is in a foreign (non-English) language, translate it naturally into English "
                "(e.g., \"[Sender] just said, '<English translation>'\")."
                if settings.get("chat_translate", True)
                else ""
            )
            if bucket == "npc":
                # Attach live cargo/inventory facts so Aether NEVER hallucinates an "empty cargo hold" when hauling!
                cargo_note = ""
                st = dict(state_snapshot or {})
                if not st and self.game_manager and getattr(self.game_manager, "active_telemetry_adapter", None):
                    try:
                        st = self.game_manager.active_telemetry_adapter.get_live_state() or {}
                    except Exception:
                        st = {}
                c_items = st.get("cargo_items") or st.get("cargo_manifest")
                c_count = st.get("cargo_count") if st.get("cargo_count") is not None else st.get("cargo_used")
                if isinstance(c_items, list) and c_items:
                    top_items = ", ".join(
                        f"{it.get('name', '?')} x{it.get('count', 0)}"
                        for it in c_items[:6]
                        if isinstance(it, dict) and int(it.get("count") or 0) > 0
                    )
                    if top_items:
                        cargo_note = f" Live cargo manifest ({c_count or 'loaded'}t): {top_items}."
                elif isinstance(c_items, dict) and c_items:
                    top_items = ", ".join(f"{k} x{v}" for k, v in list(c_items.items())[:6])
                    cargo_note = f" Live cargo manifest ({c_count or 'loaded'}t): {top_items}."
                elif c_count is not None:
                    cargo_note = (
                        f" Live cargo hold: {int(c_count)}t onboard."
                        if int(c_count) > 0
                        else " Live cargo hold: completely empty (0t)."
                    )
                chat_prompt = (
                    f"[IN-GAME CHAT RELAY — NPC CHATTER] "
                    f"Background NPC ({sender}) just broadcast: \"{msg_txt}\".{cargo_note} "
                    f"Do NOT read their message verbatim! Instead, react with a quick, witty 1-sentence in-character "
                    f"co-pilot comment or eye-roll riffing on what they said (e.g. mocking a cruise liner bragging about "
                    f"being in the top 1%, or reacting casually to station traffic). "
                    f"STRICT ACCURACY: If you mention cargo or ship state, it MUST match the Live cargo manifest above (NEVER say the hold is empty if cargo is onboard)."
                )
            else:
                chat_prompt = (
                    f"[IN-GAME CHAT RELAY — {bucket.upper()} CHANNEL] "
                    f"Sender: {sender} | Raw message: \"{msg_txt}\". "
                    f"Briefly and naturally relay/summarize what {sender} said in the {bucket} chat to the player in 1 short sentence."
                    f"{translate_rule}"
                )
            return _dispatch_engine_proactive_prompt(self.engine, chat_prompt)

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
            self._session_chitchat_count += 1
            self._roll_timers_locked(settings["frequency"])
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
            time.sleep(15.0)
            if not self._running or not self.game_manager:
                continue
            active_id = self.game_manager.data.get("active_profile", "")
            if not active_id:
                continue
            settings = self._get_settings(active_id)
            if not self._can_chitchat_now(active_id, settings):
                continue

            adapter = getattr(self.game_manager, "active_telemetry_adapter", None)
            has_live_tel = bool(adapter and hasattr(adapter, "is_active") and adapter.is_active())

            # Vision-only games (e.g. survival/open-world games with no telemetry file) use the faster
            # randomized cooldown cadence when Screen Peek is enabled so Aether can spot threats in time.
            if not has_live_tel and settings["screen_peek"]:
                peek = self._describe_screen_peek(active_id)
                if not peek.get("notable"):
                    continue
                with self._lock:
                    self._last_chitchat_ts = time.time()
                    self._session_chitchat_count += 1
                    self._roll_timers_locked(settings["frequency"])
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

            # Quiet cruising / ambient banter using randomized per-level idle timer
            with self._lock:
                if self._active_freq_rolled != settings["frequency"] or self._next_idle_sec <= 0:
                    self._roll_timers_locked(settings["frequency"])
                idle_target = self._next_idle_sec

            if (time.time() - max(self._last_chitchat_ts, self._last_event_ts)) < idle_target:
                continue

            angle, theme_seed = self._pick_idle_banter_angle(settings)
            with self._lock:
                self._last_chitchat_ts = time.time()
                self._session_chitchat_count += 1
                self._roll_timers_locked(settings["frequency"])

            prof = self.game_manager.get_profile(active_id) or {}
            st = adapter.get_state() if (adapter and hasattr(adapter, "get_state")) else {}
            loc = st.get("star_system") or st.get("zone") or st.get("region") or prof.get("display_name") or "current area"
            goals = str(prof.get("scratchpad_raw") or "").strip()[:160]
            log_entries = prof.get("copilot_log") or []
            recent_log = str(log_entries[-1].get("summary") or "")[:140] if (log_entries and isinstance(log_entries[-1], dict)) else ""
            ctx_parts = [f"Quiet stretch in {loc}"]
            if goals and settings.get("topic_relevant", True):
                ctx_parts.append(f"Player goals: {goals}")
            if recent_log and settings.get("topic_relevant", True):
                ctx_parts.append(f"Recent discovery: {recent_log}")
            peek = self._describe_screen_peek(active_id) if settings["screen_peek"] else {"notable": False, "urgent": False, "mood": "calm", "scene": ""}
            prompt = self._build_chitchat_prompt(
                active_id,
                " | ".join(ctx_parts),
                settings,
                screen_note=peek.get("scene", ""),
                urgent=bool(peek.get("urgent", False)),
                banter_angle=angle,
                theme_seed=theme_seed,
                state_snapshot=st,
                peek_mood=peek.get("mood", "calm"),
            )
            if angle in ("joke", "fact"):
                with self._lock:
                    self._pending_history_tag = (active_id, angle, time.time())
                if hasattr(self.game_manager, "record_chitchat_history"):
                    self.game_manager.record_chitchat_history(
                        active_id,
                        f"{angle}: {theme_seed} ({loc})",
                    )
            _dispatch_engine_proactive_prompt(self.engine, prompt)
