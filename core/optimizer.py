"""
Aether Desktop - Background Optimization Worker (Reflexion Engine)
Asynchronously monitors data/telemetry.db for queued_for_optimization scripts,
refines sub-optimal code via Gemini during idle periods, validates safety with
the AST Gatekeeper, and persists production-grade snippets.
"""

import asyncio
import re
import time
from typing import Callable, Dict, Optional, Any

from google import genai
from google.genai import types

from core.logger import get_logger
from core.telemetry_db import TelemetryDB
from security.ast_gatekeeper import validate_python_script
from tools.skill_library import SkillLibrary

logger = get_logger("Optimizer")

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


def extract_python_code(raw_text: str) -> str:
    """Extracts raw python code from an LLM response, stripping markdown fences."""
    text = (raw_text or "").strip()
    match = re.search(r"```(?:python|py)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text


class ScriptOptimizer:
    """
    Asynchronous background worker that refines queued scripts during engine idle periods.
    """

    def __init__(
        self,
        telemetry_db: Optional[TelemetryDB] = None,
        client_getter: Optional[Callable[[], Optional[genai.Client]]] = None,
        model_id: str = "gemini-3.8-flash",
        is_idle_callback: Optional[Callable[[], bool]] = None,
        on_optimized: Optional[Callable[[dict], None]] = None,
        skill_library: Optional[Any] = None,
        check_interval: float = 5.0
    ):
        self.db = telemetry_db or TelemetryDB()
        self.client_getter = client_getter
        self.model_id = model_id
        self.is_idle_callback = is_idle_callback
        self.on_optimized = on_optimized
        self.skill_library = skill_library
        self.check_interval = check_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def set_client_getter(self, getter: Callable[[], Optional[genai.Client]]):
        self.client_getter = getter

    def set_idle_callback(self, callback: Callable[[], bool]):
        self.is_idle_callback = callback

    def is_idle(self) -> bool:
        if self.is_idle_callback:
            try:
                return bool(self.is_idle_callback())
            except Exception as e:
                logger.warning(f"[OPTIMIZER] Error evaluating idle callback: {e}")
                return False
        return True

    async def optimize_run(self, run: Dict[str, Any], client: Optional[genai.Client] = None) -> bool:
        """
        Executes a single optimization pass for a queued script run.
        Returns True if optimization succeeded and was persisted.
        """
        run_id = run.get("run_id")
        intent = run.get("intent_description") or "Unspecified intent"
        orig_code = run.get("original_code") or ""
        exec_ms = run.get("execution_time_ms", 0.0)
        traceback_info = run.get("traceback") or "None"

        active_client = client or (self.client_getter() if self.client_getter else None)
        if not active_client:
            logger.warning("[OPTIMIZER] No Gemini client available for optimization. Skipping run.")
            return False

        prompt = OPTIMIZATION_PROMPT_TEMPLATE.format(
            intent_description=intent,
            execution_time_ms=f"{exec_ms:.1f}",
            traceback=traceback_info,
            original_code=orig_code
        )

        logger.info(f"[OPTIMIZER] Refactoring queued script '{run_id}' (Intent: '{intent}', Prior Time: {exec_ms:.1f}ms)...")
        t0 = time.perf_counter()

        try:
            response = await asyncio.to_thread(
                active_client.models.generate_content,
                model=self.model_id,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2
                )
            )
            opt_duration = (time.perf_counter() - t0) * 1000

            raw_resp = getattr(response, "text", "") or ""
            clean_code = extract_python_code(raw_resp)

            if not clean_code:
                logger.warning(f"[OPTIMIZER] Empty code returned for '{run_id}'")
                return False

            # Strict security gatekeeping on optimized code
            is_safe, error_msg = validate_python_script(clean_code)
            if not is_safe:
                logger.warning(f"[OPTIMIZER] Generated code for '{run_id}' failed AST safety validation: {error_msg}")
                return False

            # Auto-Promotion from Optimizer to Permanent Skill Library
            promoted_name = None
            if self.skill_library:
                try:
                    existing_skill = self.skill_library.find_matching_skill_name(intent)
                    if existing_skill:
                        up_res = self.skill_library.update_skill_code(existing_skill, clean_code)
                        if up_res.get("status") == "success":
                            promoted_name = existing_skill
                            logger.info(f"[PROMOTION] Updated existing skill '{existing_skill}' with optimized code.")
                    else:
                        # Novel, generalizable intent: synthesize a valid identifier name
                        raw_tokens = re.findall(r"[a-zA-Z0-9]+", intent.lower())
                        norm_name = "_".join(raw_tokens[:5]) if raw_tokens else f"auto_skill_{run_id[-6:]}"
                        save_res = self.skill_library.save_skill(norm_name, intent, clean_code)
                        if save_res.get("status") == "success":
                            promoted_name = norm_name
                            logger.info(f"[PROMOTION] Auto-promoted novel skill '{norm_name}' to permanent library.")
                except Exception as promo_err:
                    logger.warning(f"[PROMOTION ERROR] Could not auto-promote '{run_id}': {promo_err}")

            final_status = "promoted_to_library" if promoted_name else "optimized"
            self.db.update_optimized_code(run_id, clean_code, status=final_status)
            logger.info(f"[OPTIMIZER SUCCESS] Script '{run_id}' refined in {opt_duration:.1f}ms and status='{final_status}'.")

            if self.on_optimized:
                try:
                    self.on_optimized({
                        "run_id": run_id,
                        "intent_description": intent,
                        "optimized_code": clean_code,
                        "duration_ms": opt_duration,
                        "status": final_status,
                        "promoted_skill": promoted_name
                    })
                except Exception as cb_err:
                    logger.warning(f"[OPTIMIZER] Error in on_optimized callback: {cb_err}")

            return True

        except Exception as e:
            logger.error(f"[OPTIMIZER ERROR] Optimization failed for '{run_id}': {e}")
            return False

    async def run_loop(self):
        """Main background loop polling for queued optimization jobs during idle periods."""
        self._running = True
        logger.info("[OPTIMIZER] Background Reflexion worker started.")

        while self._running:
            try:
                await asyncio.sleep(self.check_interval)

                if not self._running:
                    break

                # 1. Idle Trigger Condition: Must be completely idle
                if not self.is_idle():
                    continue

                # 2. Check for queued optimization jobs
                queued = self.db.get_queued_runs(limit=1)
                if not queued:
                    continue

                job = queued[0]
                # Re-verify idle state immediately before calling LLM
                if not self.is_idle():
                    continue

                await self.optimize_run(job)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[OPTIMIZER LOOP ERROR] {e}")
                await asyncio.sleep(self.check_interval)

        logger.info("[OPTIMIZER] Background Reflexion worker stopped.")

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> asyncio.Task:
        if self._task and not self._task.done():
            return self._task
        self._running = True
        active_loop = loop or asyncio.get_event_loop()
        self._task = active_loop.create_task(self.run_loop())
        return self._task

    def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

