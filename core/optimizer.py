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
Return only the clean Python code."""

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
        self.config = getattr(engine, "config", {}) if engine else {}
        if not isinstance(self.config, dict):
            self.config = {}

        self.heavy_model = self.config.get("tier2_heavy_model", "gemini-3.8-pro")
        self.heavy_model = self.config.get("tier2_heavy_model", "gemini-3.1-pro-preview")
        self.thinking_budget = self.config.get("tier2_thinking_budget", 2048)

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
        self._last_memory_reconciliation = 0.0

        # Optional callbacks for legacy/extensibility
        self.on_optimized = kwargs.get("on_optimized")

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

        idle_delay = self.config.get("reflexion_idle_delay_seconds", 15)
        time_since_turn = time.time() - getattr(self.engine, "last_user_turn_timestamp", 0.0)
        is_audio_active = getattr(self.engine, "is_audio_streaming", False) or getattr(self.engine, "is_speaking", False)
        is_tool_active = getattr(self.engine, "is_tool_running", False)

        return (time_since_turn >= idle_delay) and not is_audio_active and not is_tool_active

    def _reflexion_loop(self):
        """Continuous background polling loop evaluating tasks during idle periods."""
        poll_interval = self.config.get("reflexion_poll_interval_seconds", 30)
        while self._running:
            try:
                if self._is_engine_idle():
                    # Task A: Refactor sub-optimal scripts
                    self._process_script_optimization_queue()

                    # Task B: Process queued chat transcripts for facts & manifests
                    self._process_unindexed_sessions()

                    # Task C: Periodic memory reconciliation (runs at most once every 6 hours)
                    if time.time() - self._last_memory_reconciliation > 21600:
                        self._reconcile_and_prune_memory()
                        self._last_memory_reconciliation = time.time()

            except Exception as e:
                print(f"[ERROR] [REFLEXION] Loop exception: {e}")
                logger.error(f"[REFLEXION] Loop exception: {e}")

            time.sleep(poll_interval)

    def _process_script_optimization_queue(self):
        """Task A: Refactor sub-optimal scripts from data/telemetry.db."""
        db = getattr(self.engine, "telemetry_db", None)
        if not db:
            from core.telemetry_db import TelemetryDB
            db = TelemetryDB()

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
            config = types.GenerateContentConfig(
                temperature=0.2,
                safety_settings=get_permissive_safety_settings(),
                thinking_config=types.ThinkingConfig(thinking_budget=self.thinking_budget)
            )
            response = active_client.models.generate_content(
                model=self.heavy_model,
                contents=prompt,
                config=config
            )
            if not self._is_engine_idle():
                logger.info(f"[REFLEXION] Engine became active during script optimization for '{run_id}'; yielding without committing.")
                return

            raw_resp = getattr(response, "text", "") or ""
            clean_code = extract_python_code(raw_resp)

            if not clean_code:
                return

            from security.ast_gatekeeper import validate_python_script
            is_safe, error_msg = validate_python_script(clean_code)
            if not is_safe:
                logger.warning(f"[REFLEXION] Generated code for '{run_id}' failed AST safety validation: {error_msg}")
                return

            promoted_name = None
            skill_library = getattr(self.engine, "skill_library", None)
            if not skill_library and hasattr(self.engine, "dispatcher") and self.engine.dispatcher:
                skill_library = getattr(self.engine.dispatcher, "skill_library", None)

            if skill_library:
                try:
                    existing_skill = skill_library.find_matching_skill_name(intent)
                    if existing_skill:
                        up_res = skill_library.update_skill_code(existing_skill, clean_code)
                        if up_res.get("status") == "success":
                            promoted_name = existing_skill
                            logger.info(f"[PROMOTION] Updated skill '{existing_skill}' with optimized code.")
                    else:
                        raw_tokens = re.findall(r"[a-zA-Z0-9]+", intent.lower())
                        norm_name = "_".join(raw_tokens[:5]) if raw_tokens else f"auto_skill_{run_id[-6:]}"
                        save_res = skill_library.save_skill(norm_name, intent, clean_code)
                        if save_res.get("status") == "success":
                            promoted_name = norm_name
                            logger.info(f"[PROMOTION] Auto-promoted novel skill '{norm_name}'.")
                except Exception as promo_err:
                    logger.warning(f"[PROMOTION ERROR] Could not auto-promote '{run_id}': {promo_err}")

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
            if len(turns) < 2:
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

            # Tier 2 Reasoning Prompt
            prompt = f"""
Analyze the following conversation session. Extract two things:
1. "manifest": High-level indexing card.
2. "facts": Concrete new facts learned about the user, their workflows, system preferences, relationships, family, dates, or ongoing projects. Do not include transient requests (e.g., "what's the weather"), only durable facts.

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
  ]
}}

Conversation:
{turns_text[-8000:]}
"""
            try:
                active_client = self._get_genai_client()
                if not active_client:
                    continue
                config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    safety_settings=get_permissive_safety_settings(),
                    thinking_config=types.ThinkingConfig(thinking_budget=self.thinking_budget)
                )
                response = active_client.models.generate_content(
                    model=self.heavy_model,
                    contents=prompt,
                    config=config
                )
                if not self._is_engine_idle():
                    logger.info(f"[REFLEXION] Engine became active during processing of {session_data.get('session_id')}; yielding immediately.")
                    break

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

            except Exception as err:
                print(f"[ERROR] [REFLEXION] Failed processing session {filepath}: {err}")
                logger.error(f"[REFLEXION] Failed processing session {filepath}: {err}")

    def _reconcile_and_prune_memory(self):
        """Uses the heavy model to deduplicate, resolve contradictions, and clean memory."""
        from core.user_memory import get_all_facts, replace_facts

        snapshot_ts = time.time()
        memory_db = getattr(getattr(self.engine, "user_memory", None), "db_path", None)
        existing_facts = get_all_facts(db_path=memory_db)
        if len(existing_facts) < 5:
            return

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
                return
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                safety_settings=get_permissive_safety_settings(),
                thinking_config=types.ThinkingConfig(thinking_budget=self.thinking_budget)
            )
            response = active_client.models.generate_content(
                model=self.heavy_model,
                contents=prompt,
                config=config
            )
            if not self._is_engine_idle():
                logger.info("[REFLEXION] Engine became active during memory reconciliation; deferring write.")
                return

            result = json.loads(response.text)
            cleaned_facts = result.get("reconciled_facts", [])

            if cleaned_facts:
                replace_facts(cleaned_facts, snapshot_ts=snapshot_ts, db_path=memory_db)
                print(f"[INFO] [REFLEXION] Memory reconciled: {len(existing_facts)} facts pruned down to {len(cleaned_facts)}.")
                logger.info(f"[REFLEXION] Memory reconciled: {len(existing_facts)} facts pruned down to {len(cleaned_facts)}.")
        except Exception as e:
            print(f"[ERROR] [REFLEXION] Reconciliation failed: {e}")
            logger.error(f"[REFLEXION] Reconciliation failed: {e}")


# Backward compatibility alias
ScriptOptimizer = ReflexionEngine
