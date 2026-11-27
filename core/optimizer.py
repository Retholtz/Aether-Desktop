"""
Aether Desktop - Tier 2 Background Worker (Reflexion Engine)
Asynchronously processes background cognitive synthesis tasks during engine idle windows:
1. Script Optimization & Refactoring (Task A)
2. Asynchronous Fact & Manifest Extraction from Session Transcripts (Task B)
3. Memory Reconciliation & Conflict Pruning (Task C)
"""

import os
import re
import json
import time
import threading
from typing import Dict, Any, List, Optional, Callable

from google import genai
from google.genai import types

from core.logger import get_logger
from security.ast_gatekeeper import format_bundled_runtime_prompt_guardrail

logger = get_logger("Reflexion")

OPTIMIZATION_PROMPT_TEMPLATE = """You are an expert systems and automation optimizer. 
A desktop automation script succeeded, but with performance or stability issues:
- Intent: {intent_description}
- Runtime: {execution_time_ms}ms
- Prior Errors: {traceback}

Original Code:
```python
{original_code}
```

Refactor this script into an optimal, production-grade snippet:
- Eliminate polling loops and replace them with native Win32/COM events or deterministic waits.
- Ensure all COM objects and window handles are strictly released in finally blocks.
- Parameterize hardcoded inputs.
- Preserve all safety constraints (no forbidden imports).
""" + format_bundled_runtime_prompt_guardrail() + "\nReturn only the clean Python code."

RECONCILIATION_PROMPT = """
Analyze the existing facts below. Reconcile duplicates, update outdated details, and produce a consolidated list of permanent user facts.

Allowed Categories: "family" | "dates" | "relationship" | "preference" | "project" | "system" | "general"

CRITICAL PRESERVATION RULES:
1. Always retain all entries in the "family" and "dates" categories.
2. Explicit profile keys (e.g., last_name, brother_name, wife_name, children, parents) must be preserved and never discarded or merged into generic hashes.
3. Consolidate only redundant or superseded facts within the same category.

Strict JSON format:
{{
  "reconciled_facts": [
    {{"category": "family|dates|relationship|preference|project|system|general", "key": "optional_explicit_key", "fact": "concise fact statement"}}
  ]
}}

Existing Facts:
{facts_text}
"""


def get_permissive_safety_settings() -> list:
    """Returns relaxed safety settings allowing biographical, historical, and genealogy lookups."""
    return [
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_CIVIC_INTEGRITY,
            threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH
        ),
    ]


def extract_python_code(raw_text: str) -> str:
    """Extracts raw python code from an LLM response, stripping markdown fences."""
    text = (raw_text or "").strip()
    match = re.search(r"```(?:python|py)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text


class ReflexionEngine:
    """
    Tier 2 Asynchronous Engine.
    Executes deep reasoning models (gemini-3.8-pro with thinking budget) strictly
    during idle periods when user interaction, audio streaming, and tools are quiet.
    """

    def __init__(self, engine=None, **kwargs):
        self.engine = engine
        self.memory_db = kwargs.get("memory_db")
        self.config = getattr(engine, "config", {}) if engine else {}
        if not isinstance(self.config, dict):
            self.config = {}

        from core.model_discovery import get_configured_tier2_model
        # Background Reflexion strictly uses Settings Tier 2 Moderate (Hydrogen) with low thinking
        # — never Tier 3 flagship/Neon — so idle maintenance cannot cause runaway API costs.
        base_t2, _ = get_configured_tier2_model(self.config if self.config else None)
        if "neon" in str(base_t2).lower():
            base_t2 = "gemini-4-hydrogen"
        self.heavy_model = base_t2
        self.thinking_level = "low"
        self.thinking_budget = min(int(self.config.get("tier2_thinking_budget", 1024) or 1024), 1024)

        # Gemini Client Resolution: env var -> engine client -> decrypted config
        self.client: Optional[genai.Client] = None
        api_key = os.environ.get("GEMINI_API_KEY")
        if api_key:
            try:
                self.client = genai.Client(api_key=api_key)
            except Exception:
                self.client = None
        elif engine and hasattr(engine, "genai_client") and engine.genai_client:
            self.client = engine.genai_client
        else:
            try:
                from core.manifest_indexer import resolve_gemini_client
                self.client = resolve_gemini_client()
            except Exception:
                self.client = None

        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._state_path = self._resolve_state_path()
        self._last_memory_reconciliation = self._load_last_reconciliation_ts()

        # Optional callbacks for legacy/extensibility
        self.on_optimized = kwargs.get("on_optimized")

    @staticmethod
    def _resolve_state_path() -> str:
        try:
            from core.paths import user_data_path
            return user_data_path("reflexion_state.json")
        except Exception:
            return os.path.join("data", "reflexion_state.json")

    def _load_last_reconciliation_ts(self) -> float:
        try:
            if os.path.exists(self._state_path):
                with open(self._state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return float(data.get("last_memory_reconciliation", 0.0) or 0.0)
        except Exception:
            pass
        return 0.0

    def _save_last_reconciliation_ts(self, ts: float) -> None:
        try:
            os.makedirs(os.path.dirname(self._state_path) or ".", exist_ok=True)
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump({"last_memory_reconciliation": float(ts)}, f)
        except Exception:
            pass

    def _build_generate_config(
        self,
        response_mime_type: Optional[str] = None,
        temperature: Optional[float] = None
    ) -> types.GenerateContentConfig:
        """Constructs GenerateContentConfig adapting thinking parameters for Gemini 4 vs older models."""
        from core.model_discovery import build_thinking_config
        thinking_cfg = build_thinking_config(
            self.heavy_model,
            thinking_level=self.thinking_level,
            thinking_budget=self.thinking_budget,
        )
        is_gemini4 = any(k in str(self.heavy_model).lower() for k in ("neon", "argon", "hydrogen", "carbon", "gemini-4"))

        kwargs: Dict[str, Any] = {
            "safety_settings": get_permissive_safety_settings(),
            "thinking_config": thinking_cfg,
        }
        if response_mime_type:
            kwargs["response_mime_type"] = response_mime_type
        if temperature is not None:
            kwargs["temperature"] = temperature
        if is_gemini4:
            kwargs["max_output_tokens"] = 65536
        return types.GenerateContentConfig(**kwargs)

    def _get_genai_client(self) -> Optional[genai.Client]:
        """Dynamically retrieves or resolves the Google GenAI client if not initialized."""
        if self.client is not None:
            return self.client
        if self.engine and hasattr(self.engine, "genai_client") and self.engine.genai_client is not None:
            self.client = self.engine.genai_client
            return self.client
        api_key = os.environ.get("GEMINI_API_KEY")
        if api_key:
            try:
                self.client = genai.Client(api_key=api_key)
                return self.client
            except Exception:
                pass
        from core.manifest_indexer import resolve_gemini_client
        self.client = resolve_gemini_client()
        return self.client

    def start(self):
        """Starts the background worker thread for Tier 2 reflexion tasks."""
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._reflexion_loop,
            daemon=True,
            name="Aether-ReflexionWorker"
        )
        self._worker_thread.start()
        print(f"[INFO] [REFLEXION] Tier 2 Engine started using {self.heavy_model}.")
        logger.info(f"[REFLEXION] Tier 2 Engine started using {self.heavy_model}.")

    def stop(self):
        """Signals the background worker thread to terminate."""
        self._running = False

    def _is_engine_idle(self) -> bool:
        """
        Ensures Tier 2 never competes for network or CPU during active interaction.
        Requires:
        1. Microphone/VAD is idle.
        2. No active assistant TTS audio is playing.
        3. No active tool invocation is executing.
        4. User has been inactive for at least reflexion_idle_delay_seconds.
        """
        if not self.engine:
            return True

        if not hasattr(self.engine, "last_user_turn_timestamp"):
            return True

        # Pause background Reflexion completely while Game Mode is active
        ge = getattr(self.engine, "game_engine", None)
        gm = getattr(ge, "manager", None) if ge else getattr(self.engine, "game_manager", None)
        if gm is not None and hasattr(gm, "is_game_mode_active"):
            try:
                if gm.is_game_mode_active():
                    return False
            except Exception:
                pass

        idle_delay = self.config.get("reflexion_idle_delay_seconds", 45)
        time_since_turn = time.time() - getattr(self.engine, "last_user_turn_timestamp", 0.0)
        is_audio_active = getattr(self.engine, "is_audio_streaming", False) or getattr(self.engine, "is_speaking", False)
        is_tool_active = getattr(self.engine, "is_tool_running", False)

        return (time_since_turn >= idle_delay) and not is_audio_active and not is_tool_active

    def _reflexion_loop(self):
        """Continuous background polling loop evaluating tasks during idle periods."""
        poll_interval = self.config.get("reflexion_poll_interval_seconds", 30)
        reconcile_interval = float(self.config.get("reflexion_reconcile_interval_seconds", 86400))
        while self._running:
            try:
                if self._is_engine_idle():
                    # Task A: Refactor sub-optimal scripts
                    self._process_script_optimization_queue()

                    # Task B: Process queued chat transcripts for facts & manifests
                    self._process_unindexed_sessions()

                    # Task C: Periodic memory reconciliation (persisted across restarts; at most once per 24h)
                    if time.time() - self._last_memory_reconciliation > reconcile_interval:
                        if self._reconcile_and_prune_memory():
                            self._last_memory_reconciliation = time.time()
                            self._save_last_reconciliation_ts(self._last_memory_reconciliation)

            except Exception as e:
                print(f"[ERROR] [REFLEXION] Loop exception: {e}")
                logger.error(f"[REFLEXION] Loop exception: {e}")

            time.sleep(poll_interval)

    def _process_script_optimization_queue(self):
        """Task A: Refactor sub-optimal scripts from data/telemetry.db."""
        db = getattr(self.engine, "telemetry_db", None)
        owns_db = False
        if not db:
            from core.telemetry_db import TelemetryDB
            db = TelemetryDB()
            owns_db = True

        try:
            queued = db.get_queued_runs(limit=1)
            if not queued:
                return

            if not self._is_engine_idle():
                return

            run = queued[0]
            run_id = run.get("run_id")
            intent = run.get("intent_description") or "Unspecified intent"
            orig_code = run.get("original_code") or ""
            exec_ms = run.get("execution_time_ms", 0.0)
            traceback_info = run.get("traceback") or "None"

            active_client = self._get_genai_client()
            if not active_client:
                return

            prompt = OPTIMIZATION_PROMPT_TEMPLATE.format(
                intent_description=intent,
                execution_time_ms=f"{exec_ms:.1f}",
                traceback=traceback_info,
                original_code=orig_code
            )

            try:
                from core.api_telemetry import api_feature_context
                config = self._build_generate_config(temperature=0.2)
                with api_feature_context("script_gen_optimizer", "reflexion_optimize"):
                    response = active_client.models.generate_content(
                        model=self.heavy_model,
                        contents=prompt,
                        config=config
                    )
                # Always commit already-paid API results so we never discard tokens and re-bill on retry
                raw_resp = getattr(response, "text", "") or ""
                clean_code = extract_python_code(raw_resp)

                if not clean_code:
                    db.update_optimized_code(run_id, "", status="optimization_failed")
                    return

                from security.ast_gatekeeper import validate_python_script
                is_safe, error_msg = validate_python_script(clean_code)
                if not is_safe:
                    logger.warning(f"[REFLEXION] Generated code for '{run_id}' failed AST safety validation: {error_msg}")
                    db.update_optimized_code(run_id, "", status="blocked_by_ast")
                    return

                promoted_name = None
                skill_library = getattr(self.engine, "skill_library", None)
                if not skill_library and hasattr(self.engine, "dispatcher") and self.engine.dispatcher:
                    skill_library = getattr(self.engine.dispatcher, "skill_library", None)

                # Only update an already-promoted skill in the library; do NOT auto-promote
                # one-off user task scripts on their first run (promotion requires 3 clean runs via telemetry_db).
                if skill_library:
                    try:
                        existing_skill = skill_library.find_matching_skill_name(intent, threshold=0.80)
                        if existing_skill:
                            up_res = skill_library.update_skill_code(existing_skill, clean_code)
                            if up_res.get("status") == "success":
                                promoted_name = existing_skill
                                logger.info(f"[PROMOTION] Updated existing skill '{existing_skill}' with optimized code.")
                    except Exception as promo_err:
                        logger.warning(f"[PROMOTION ERROR] Could not update skill for '{run_id}': {promo_err}")

                final_status = "promoted_to_library" if promoted_name else "optimized"
                db.update_optimized_code(run_id, clean_code, status=final_status)
                logger.info(f"[REFLEXION SUCCESS] Script '{run_id}' refined with status='{final_status}'.")

                if self.on_optimized:
                    try:
                        self.on_optimized({
                            "run_id": run_id,
                            "intent_description": intent,
                            "optimized_code": clean_code,
                            "status": final_status,
                            "promoted_skill": promoted_name
                        })
                    except Exception as cb_err:
                        logger.warning(f"[REFLEXION] Error in on_optimized callback: {cb_err}")

                if self.engine and hasattr(self.engine, "notify"):
                    self.engine.notify("chat_event", {
                        "type": "tool",
                        "name": "Reflexion Self-Optimization",
                        "content": f"✨ [OPTIMIZATION] Self-refined script for '{intent}'."
                    })

            except Exception as e:
                print(f"[ERROR] [REFLEXION] Script optimization failed for '{run_id}': {e}")
                logger.error(f"[REFLEXION] Script optimization failed for '{run_id}': {e}")
                try:
                    db.update_optimized_code(run_id, "", status="optimization_error")
                except Exception:
                    pass
        finally:
            if owns_db and hasattr(db, "close"):
                try:
                    db.close()
                except Exception:
                    pass

    def _process_unindexed_sessions(self):
        """Scans data/chats for sessions requiring manifest extraction or memory synthesis."""
        from core.manifest_indexer import get_unprocessed_sessions, index_manifest_card
        from core.user_memory import add_fact_batch

        unprocessed = get_unprocessed_sessions(limit=2)
        for filepath in unprocessed:
            if not self._is_engine_idle():
                break  # Yield immediately if the user speaks

            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    session_data = json.load(f)
            except Exception as e:
                print(f"[ERROR] [REFLEXION] Failed reading session {filepath}: {e}")
                logger.error(f"[REFLEXION] Failed reading session {filepath}: {e}")
                continue

            turns = session_data.get("turns", [])
            has_substantial_single_turn = (
                len(turns) == 1 and (
                    bool(turns[0].get("tools_used"))
                    or len(str(turns[0].get("text") or turns[0].get("content") or "")) >= 60
                )
            )
            if len(turns) < 2 and not has_substantial_single_turn:
                # Mark minimal session as indexed so it is not repeatedly re-scanned
                minimal_card = {
                    "session_id": session_data.get("session_id", os.path.splitext(os.path.basename(filepath))[0]),
                    "date": time.strftime("%Y-%m-%d", time.localtime(session_data.get("start_time", time.time()))),
                    "timestamp": session_data.get("start_time", time.time()),
                    "topics_discussed": [],
                    "actions_executed": [],
                    "unresolved_questions": [],
                    "key_entities": []
                }
                index_manifest_card(minimal_card, filepath)
                continue

            turns_text = "\n".join([f"{t.get('role', 'USER').upper()}: {t.get('text') or t.get('content', '')}" for t in turns])

            session_game_id = str(session_data.get("game_id") or "").strip()
            game_fact_inst = (
                f'3. "game_facts": Durable in-game facts, faction allegiances, rivalries, character stances, or playstyle preferences specific to the active game ({session_game_id}). '
                f'Keep in-game faction/lore preferences in "game_facts" (NOT in global "facts").\n'
                if session_game_id
                else '3. "game_facts": If the session clearly discussed a specific video game\'s player faction allegiances, rivalries, or playstyle preferences, list them here (otherwise empty list).\n'
            )

            # Tier 2 Reasoning Prompt
            prompt = f"""
Analyze the following conversation session. Extract three things:
1. "manifest": High-level indexing card.
2. "facts": Concrete new real-world facts learned about the user, their workflows, system preferences, relationships, family, dates, or ongoing projects. Do not include transient requests (e.g., "what's the weather"), only durable facts.
{game_fact_inst}
Strict JSON format:
{{
  "manifest": {{
    "topics_discussed": ["string"],
    "actions_executed": ["string"],
    "unresolved_questions": ["string"],
    "key_entities": ["string"]
  }},
  "facts": [
    {{"category": "family|dates|relationship|preference|project|system|general", "fact": "string"}}
  ],
  "game_facts": [
    {{"category": "allegiance|npc|preference|playstyle|rivalry|lore|asset|general", "fact": "string"}}
  ]
}}

Conversation:
{turns_text[-8000:]}
"""
            try:
                active_client = self._get_genai_client()
                if not active_client:
                    continue
                config = self._build_generate_config(response_mime_type="application/json")
                from core.api_telemetry import api_feature_context
                with api_feature_context("script_gen_optimizer", "reflexion_session_memory"):
                    response = active_client.models.generate_content(
                        model=self.heavy_model,
                        contents=prompt,
                        config=config
                    )
                # Always persist already-paid session indexing/facts before yielding to active speech
                result = json.loads(response.text)

                # Store Manifest Card
                manifest_card = result.get("manifest", {})
                manifest_card["session_id"] = session_data.get("session_id", os.path.splitext(os.path.basename(filepath))[0])
                manifest_card["date"] = time.strftime("%Y-%m-%d", time.localtime(session_data.get("start_time", time.time())))
                manifest_card["timestamp"] = session_data.get("start_time", time.time())
                index_manifest_card(manifest_card, filepath)

                # Store Extracted Facts
                new_facts = result.get("facts", [])
                if new_facts:
                    memory_db = getattr(getattr(self.engine, "user_memory", None), "db_path", None)
                    add_fact_batch(new_facts, source_session=manifest_card["session_id"], db_path=memory_db)
                    print(f"[INFO] [REFLEXION] Synthesized {len(new_facts)} facts from {manifest_card['session_id']}")
                    logger.info(f"[REFLEXION] Synthesized {len(new_facts)} facts from {manifest_card['session_id']}")

                # Store Per-Game Cross-Session Memory Facts (Allegiances, NPCs, Rivalries, Playstyle)
                game_facts = result.get("game_facts", [])
                if game_facts and session_game_id:
                    gm = getattr(self.engine, "game_mgr", None) or getattr(getattr(self.engine, "game_engine", None), "manager", None)
                    if gm is not None and hasattr(gm, "add_game_memory") and (not hasattr(gm, "get_game_memory_enabled") or gm.get_game_memory_enabled(session_game_id)):
                        added_gm = 0
                        for gf in game_facts:
                            if isinstance(gf, dict) and str(gf.get("fact") or "").strip():
                                res_gm = gm.add_game_memory(
                                    game_id=session_game_id,
                                    fact=str(gf["fact"]).strip(),
                                    category=str(gf.get("category") or "preference"),
                                    source="reflexion",
                                )
                                if res_gm.get("success") and not res_gm.get("duplicate"):
                                    added_gm += 1
                        if added_gm:
                            logger.info(f"[REFLEXION] Synthesized {added_gm} per-game memories for '{session_game_id}' from {manifest_card['session_id']}")

            except Exception as err:
                print(f"[ERROR] [REFLEXION] Failed processing session {filepath}: {err}")
                logger.error(f"[REFLEXION] Failed processing session {filepath}: {err}")

    def _reconcile_and_prune_memory(self):
        """Uses the heavy model to deduplicate, resolve contradictions, and clean memory."""
        from core.user_memory import get_all_facts, replace_facts

        snapshot_ts = time.time()
        memory_db = getattr(self, "memory_db", None) or getattr(getattr(self.engine, "user_memory", None), "db_path", None)
        existing_facts = get_all_facts(db_path=memory_db)
        if len(existing_facts) < 5:
            return False

        print("[INFO] [REFLEXION] Running long-term memory reconciliation...")
        logger.info("[REFLEXION] Running long-term memory reconciliation...")
        facts_lines = []
        for f in existing_facts:
            k = f.get("key")
            key_prefix = f" (key={k})" if k and not str(k).startswith("fact_") else ""
            facts_lines.append(f"- [{f['category']}]{key_prefix} {f['fact']}")
        facts_text = "\n".join(facts_lines)

        prompt = RECONCILIATION_PROMPT.format(facts_text=facts_text)
        try:
            active_client = self._get_genai_client()
            if not active_client:
                return False
            config = self._build_generate_config(response_mime_type="application/json")
            from core.api_telemetry import api_feature_context
            with api_feature_context("script_gen_optimizer", "reflexion_memory_reconcile"):
                response = active_client.models.generate_content(
                    model=self.heavy_model,
                    contents=prompt,
                    config=config
                )
            # Always commit already-paid reconciliation results (atomic local SQLite write <5ms)
            result = json.loads(response.text)
            cleaned_facts = result.get("reconciled_facts", []) if isinstance(result, dict) else (result if isinstance(result, list) else [])

            if cleaned_facts:
                replace_facts(cleaned_facts, snapshot_ts=snapshot_ts, db_path=memory_db)
                print(f"[INFO] [REFLEXION] Memory reconciled: {len(existing_facts)} facts pruned down to {len(cleaned_facts)}.")
                logger.info(f"[REFLEXION] Memory reconciled: {len(existing_facts)} facts pruned down to {len(cleaned_facts)}.")
            return True
        except Exception as e:
            print(f"[ERROR] [REFLEXION] Reconciliation failed: {e}")
            logger.error(f"[REFLEXION] Reconciliation failed: {e}")
            return False


# Backward compatibility alias
ScriptOptimizer = ReflexionEngine
