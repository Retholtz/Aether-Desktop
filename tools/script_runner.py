"""
Aether Desktop - Tier 3: Sandboxed Script Runner with Telemetry Instrumentation
Executes dynamically generated Python and PowerShell automation scripts in the background
with pre-execution AST safety gatekeeping, isolated subprocess containment,
and runtime telemetry capture for the asynchronous Reflexion Engine.
"""

import ast
import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from typing import Dict, Optional, Any, List, Tuple

import psutil

from core.logger import get_logger
from core.telemetry_db import TelemetryDB
from security.ast_gatekeeper import validate_python_script

logger = get_logger("ScriptRunner")


def check_polling_loop(code: str) -> bool:
    """
    Detects repetitive polling loops (e.g. while loops with time.sleep or busy-wait).
    """
    if not code:
        return False
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.While):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        # Matches time.sleep(...) or sleep(...)
                        if isinstance(sub.func, ast.Attribute) and sub.func.attr == "sleep":
                            return True
                        if isinstance(sub.func, ast.Name) and sub.func.id == "sleep":
                            return True
    except Exception:
        # Fallback regex for syntax variants or PowerShell scripts
        if re.search(r"\bwhile\b[\s\S]*?\b(sleep|Start-Sleep)\b", code, re.IGNORECASE):
            return True
    return False


def check_unreleased_com(code: str) -> bool:
    """
    Detects COM automation usage (win32com.client.Dispatch, EnsureDispatch, etc.)
    that lacks proper cleanup (try...finally block calling Quit() or Close()).
    """
    if not code:
        return False
    if "win32com" not in code and "Dispatch" not in code:
        return False
    try:
        tree = ast.parse(code)
        uses_com = False
        has_finally_cleanup = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in ("Dispatch", "EnsureDispatch", "GetActiveObject"):
                    uses_com = True
            elif isinstance(node, ast.Try):
                if node.finalbody:
                    for f_node in node.finalbody:
                        for sub in ast.walk(f_node):
                            if isinstance(sub, ast.Call):
                                if isinstance(sub.func, ast.Attribute) and sub.func.attr in ("Quit", "quit", "Close", "close", "Release"):
                                    has_finally_cleanup = True
        if uses_com and not has_finally_cleanup:
            return True
    except Exception:
        # Fallback heuristic
        if ("Dispatch(" in code or "EnsureDispatch(" in code) and "finally:" not in code:
            return True
    return False


def prune_script_cache(cache_dir: Optional[str] = None, max_age_days: int = 7) -> int:
    """
    Deletes files in scripts/cache/ whose last modified time is older than max_age_days.
    Gracefully handles locked or in-use files. Returns the count of deleted files.
    """
    if cache_dir is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cache_dir = os.path.join(base_dir, "scripts", "cache")

    if not os.path.exists(cache_dir):
        return 0

    cutoff_time = time.time() - (max_age_days * 86400.0)
    pruned_count = 0

    for root, _, files in os.walk(cache_dir):
        for fname in files:
            if fname.startswith("auto_script_") or fname.endswith((".py", ".ps1", ".txt")):
                fpath = os.path.join(root, fname)
                try:
                    mtime = os.path.getmtime(fpath)
                    if mtime < cutoff_time:
                        os.remove(fpath)
                        pruned_count += 1
                        logger.debug(f"[CACHE PRUNE] Deleted expired cache file: {fname}")
                except (OSError, PermissionError) as e:
                    logger.debug(f"[CACHE PRUNE] Skipped locked file {fname}: {e}")
                except Exception as e:
                    logger.warning(f"[CACHE PRUNE] Error pruning {fname}: {e}")

    logger.info(f"[CACHE PRUNE] Cache maintenance complete. Pruned {pruned_count} files older than {max_age_days} days.")
    return pruned_count


class ScriptRunner:
    """Manages secure generation, background execution, and telemetry instrumentation of automation scripts."""

    def __init__(
        self,
        workspace_root: Optional[str] = None,
        telemetry_db: Optional[TelemetryDB] = None
    ):
        self.workspace_root = workspace_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.cache_dir = os.path.join(self.workspace_root, "scripts", "cache")
        os.makedirs(self.cache_dir, exist_ok=True)

        self.telemetry_db = telemetry_db or TelemetryDB()

        # Locate active virtual environment Python interpreter
        venv_python = os.path.join(self.workspace_root, ".venv", "Scripts", "python.exe")
        if os.path.exists(venv_python):
            self.python_exe = venv_python
        else:
            self.python_exe = sys.executable

        # Tracks recent failures per intent for traceback-driven self-correction tracking
        # Schema: {intent_key: {"count": int, "tracebacks": List[str], "last_time": float}}
        self._failure_history: Dict[str, Dict[str, Any]] = {}
        self._history_lock = threading.Lock()

    def _get_execution_env(self, args: Optional[dict] = None) -> dict:
        env = os.environ.copy()
        python_path = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (self.workspace_root + os.pathsep + python_path) if python_path else self.workspace_root
        if args is not None:
            env["SKILL_ARGS"] = json.dumps(args)
        return env

    def _run_subprocess_monitored(
        self,
        cmd: List[str],
        timeout: int,
        env: dict
    ) -> Tuple[int, str, str, float, float]:
        """
        Executes subprocess while sampling peak memory usage (MB) and wall-clock duration.
        Returns: (exit_code, stdout, stderr, elapsed_ms, peak_mb)
        """
        t0 = time.perf_counter()
        peak_mb = [0.0]
        stop_monitor = threading.Event()

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self.workspace_root,
            env=env
        )

        def monitor_memory():
            try:
                ps_proc = psutil.Process(proc.pid)
                while not stop_monitor.is_set():
                    try:
                        mem = ps_proc.memory_info().rss / (1024 * 1024)
                        if mem > peak_mb[0]:
                            peak_mb[0] = mem
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        break
                    time.sleep(0.01)
            except Exception:
                pass

        monitor_thread = threading.Thread(target=monitor_memory, daemon=True)
        monitor_thread.start()

        try:
            stdout, stderr = proc.communicate(timeout=max(5, min(120, timeout)))
            elapsed_ms = (time.perf_counter() - t0) * 1000
            stop_monitor.set()
            monitor_thread.join(timeout=0.1)
            exit_code = proc.returncode
            return exit_code, stdout or "", stderr or "", elapsed_ms, peak_mb[0]
        except subprocess.TimeoutExpired:
            stop_monitor.set()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
            monitor_thread.join(timeout=0.1)
            raise

    def execute_script(
        self,
        script_code: str,
        script_type: str = "python",
        description: str = "",
        args: Optional[dict] = None,
        timeout: int = 30,
        run_id: Optional[str] = None
    ) -> Dict:
        """
        Validates safety via AST gatekeeper (for Python), executes script in a background subprocess,
        records runtime telemetry, evaluates optimization queue rules, and returns immediately.

        Args:
            script_code: The raw script source code.
            script_type: 'python' or 'powershell'. Default is 'python'.
            description: Brief summary of what the script accomplishes.
            args: Optional dictionary of arguments passed to the script via SKILL_ARGS and CLI JSON.
            timeout: Maximum execution time in seconds.
            run_id: Optional explicit run identifier for tracing.

        Returns:
            Dict containing status, stdout, stderr, traceback, exit_code, duration_ms, and telemetry metrics.
        """
        script_clean = (script_code or "").strip()
        if not script_clean:
            logger.error("[SCRIPT ERROR] Script code cannot be empty.")
            return {
                "status": "error",
                "script_type": script_type,
                "error": "Script code cannot be empty.",
                "message": "Script execution aborted: No code provided."
            }

        st_lower = script_type.lower().strip()
        intent_key = description.strip().lower() or "unspecified_script_task"
        active_run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"

        logger.info(f"[SCRIPT INVOKE] Type: {st_lower} | Desc: '{description}' | Args: {json.dumps(args or {}, default=str)}")
        logger.debug(f"[SCRIPT CODE]\n{script_clean}")

        # 1. AST Security Gatekeeper validation for Python scripts
        if st_lower == "python":
            is_safe, error_msg = validate_python_script(script_clean)
            if not is_safe:
                logger.warning(f"[SCRIPT BLOCKED] AST Gatekeeper rejected script: {error_msg}")
                # Record blocked attempt
                self.telemetry_db.record_run(
                    run_id=active_run_id,
                    intent_description=description,
                    original_code=script_clean,
                    execution_time_ms=0.0,
                    retry_count=0,
                    status="failed",
                    traceback=f"AST Gatekeeper Rejected: {error_msg}",
                    exit_code=1,
                    memory_peak_mb=0.0
                )
                return {
                    "status": "blocked",
                    "script_type": "python",
                    "description": description,
                    "error": f"AST Gatekeeper Rejected: {error_msg}",
                    "message": f"Script execution was blocked by the safety gatekeeper: {error_msg}",
                    "execution_time_ms": 0.0,
                    "retry_count": 0,
                    "exit_code": 1,
                    "traceback_history": [f"AST Gatekeeper Rejected: {error_msg}"],
                    "memory_peak_mb": 0.0,
                    "run_id": active_run_id
                }

        ext = ".py" if st_lower == "python" else (".ps1" if st_lower in ("powershell", "ps1") else ".txt")

        # 2. Cache script for logging and auditability
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        script_file = os.path.join(self.cache_dir, f"auto_script_{ts}{ext}")

        try:
            with open(script_file, "w", encoding="utf-8") as f:
                f.write(script_clean)
            logger.info(f"[SCRIPT CACHED] Saved to: {script_file}")
        except Exception as e:
            logger.error(f"[SCRIPT ERROR] Failed to save script to cache: {e}")
            return {
                "status": "error",
                "script_type": script_type,
                "error": f"Failed to save script to cache: {e}"
            }

        # 3. Build execution command
        if st_lower == "python":
            cmd = [self.python_exe, script_file]
            if args is not None:
                cmd.append(json.dumps(args))
        elif st_lower in ("powershell", "ps1"):
            cmd = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-File", script_file
            ]
        else:
            logger.error(f"[SCRIPT ERROR] Unsupported script type '{script_type}'.")
            return {
                "status": "error",
                "script_type": script_type,
                "error": f"Unsupported script type '{script_type}'. Must be 'python' or 'powershell'."
            }

        # 4. Execute with runtime metrics instrumentation
        try:
            exit_code, stdout_raw, stderr_raw, elapsed_ms, peak_mb = self._run_subprocess_monitored(
                cmd=cmd,
                timeout=timeout,
                env=self._get_execution_env(args)
            )

            stdout = (stdout_raw or "").strip()
            stderr = (stderr_raw or "").strip()
            success = (exit_code == 0)

            # Cap output lengths for LLM context
            if len(stdout) > 2500:
                stdout = stdout[:2500] + "\n... [truncated]"
            if len(stderr) > 1500:
                stderr = stderr[:1500] + "\n... [truncated]"

            # Track traceback-driven self-correction history
            now = time.perf_counter()
            with self._history_lock:
                if not success:
                    # Record failure attempt for this intent
                    hist = self._failure_history.get(intent_key, {"count": 0, "tracebacks": [], "last_time": now})
                    hist["count"] += 1
                    err_entry = stderr if stderr else f"Exit code {exit_code}"
                    hist["tracebacks"].append(err_entry)
                    hist["last_time"] = now
                    self._failure_history[intent_key] = hist

                    retry_count = hist["count"]
                    traceback_history = list(hist["tracebacks"])
                    status = "failed"
                else:
                    # Successful recovery: check if this was a retry
                    hist = self._failure_history.pop(intent_key, None)
                    if hist and (now - hist.get("last_time", 0) < 180.0):
                        retry_count = hist.get("count", 0)
                        traceback_history = list(hist.get("tracebacks", []))
                    else:
                        retry_count = 0
                        traceback_history = []

                    # 5. Queuing Rule Evaluation
                    has_loop = check_polling_loop(script_clean)
                    has_unreleased = check_unreleased_com(script_clean)
                    is_slow = (elapsed_ms > 1500.0)
                    has_prior_retries = (retry_count >= 1)

                    if is_slow or has_prior_retries or has_loop or has_unreleased:
                        status = "queued_for_optimization"
                        reason = []
                        if is_slow: reason.append(f"Runtime > 1500ms ({elapsed_ms:.1f}ms)")
                        if has_prior_retries: reason.append(f"Retries >= 1 ({retry_count})")
                        if has_loop: reason.append("Polling loop detected")
                        if has_unreleased: reason.append("Unreleased COM handle detected")
                        logger.info(f"[REFLEXION QUEUE] Script '{active_run_id}' flagged for optimization: {', '.join(reason)}")
                    else:
                        status = "success"

            # 6. Record run in local telemetry database
            combined_traceback = "\n---\n".join(traceback_history) if traceback_history else stderr
            self.telemetry_db.record_run(
                run_id=active_run_id,
                intent_description=description,
                original_code=script_clean,
                execution_time_ms=elapsed_ms,
                retry_count=retry_count,
                status=status,
                traceback=combined_traceback,
                exit_code=exit_code,
                memory_peak_mb=peak_mb
            )

            if success:
                logger.info(
                    f"[SCRIPT SUCCESS] Duration: {elapsed_ms:.1f}ms | PeakMem: {peak_mb:.1f}MB | "
                    f"Retries: {retry_count} | Queue: {status} | Output: {stdout[:300] if stdout else '(none)'}"
                )
                msg = f"Script executed successfully." + (f" ({description})" if description else "")
                if stdout:
                    msg += f" Output: {stdout}"
            else:
                logger.error(
                    f"[SCRIPT FAILED] Duration: {elapsed_ms:.1f}ms | ExitCode: {exit_code} | "
                    f"Retries: {retry_count} | Stderr: {stderr or stdout}"
                )
                msg = f"Script failed (exit code {exit_code})."
                if stderr:
                    msg += f" Traceback: {stderr}"
                elif stdout:
                    msg += f" Output: {stdout}"

            return {
                "status": "success" if success else "error",
                "script_type": st_lower,
                "exit_code": exit_code,
                "duration_ms": round(elapsed_ms, 1),
                "execution_time_ms": round(elapsed_ms, 1),
                "retry_count": retry_count,
                "traceback_history": traceback_history,
                "memory_peak_mb": round(peak_mb, 2),
                "run_id": active_run_id,
                "telemetry_status": status,
                "description": description,
                "file": script_file,
                "stdout": stdout,
                "stderr": stderr,
                "traceback": stderr if not success else "",
                "error": stderr if not success else "",
                "message": msg
            }

        except subprocess.TimeoutExpired:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.error(f"[SCRIPT TIMEOUT] Duration: {elapsed_ms:.1f}ms (Limit: {timeout}s) | File: {script_file}")
            self.telemetry_db.record_run(
                run_id=active_run_id,
                intent_description=description,
                original_code=script_clean,
                execution_time_ms=elapsed_ms,
                retry_count=0,
                status="failed",
                traceback=f"Execution timed out after {timeout} seconds.",
                exit_code=1,
                memory_peak_mb=peak_mb[0]
            )
            return {
                "status": "timeout",
                "script_type": st_lower,
                "exit_code": 1,
                "duration_ms": round(elapsed_ms, 1),
                "execution_time_ms": round(elapsed_ms, 1),
                "retry_count": 0,
                "traceback_history": [f"Execution timed out after {timeout} seconds."],
                "memory_peak_mb": round(peak_mb[0], 2),
                "run_id": active_run_id,
                "telemetry_status": "failed",
                "description": description,
                "file": script_file,
                "error": f"Execution timed out after {timeout} seconds.",
                "message": f"Script timed out after {timeout} seconds."
            }
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.error(f"[SCRIPT ERROR] Duration: {elapsed_ms:.1f}ms | Error: {e}")
            self.telemetry_db.record_run(
                run_id=active_run_id,
                intent_description=description,
                original_code=script_clean,
                execution_time_ms=elapsed_ms,
                retry_count=0,
                status="failed",
                traceback=str(e),
                exit_code=1,
                memory_peak_mb=peak_mb[0]
            )
            return {
                "status": "error",
                "script_type": st_lower,
                "exit_code": 1,
                "duration_ms": round(elapsed_ms, 1),
                "execution_time_ms": round(elapsed_ms, 1),
                "retry_count": 0,
                "traceback_history": [str(e)],
                "memory_peak_mb": round(peak_mb[0], 2),
                "run_id": active_run_id,
                "telemetry_status": "failed",
                "description": description,
                "file": script_file,
                "error": str(e),
                "traceback": str(e),
                "message": f"Script failed to run: {e}"
            }

    def execute_script_file(
        self,
        script_file: str,
        args: Optional[dict] = None,
        description: str = "",
        timeout: int = 30
    ) -> Dict:
        """
        Executes an existing script file from the Skill Library with arguments and telemetry tracking.
        """
        if not os.path.exists(script_file):
            logger.error(f"[SKILL ERROR] Script file not found: {script_file}")
            return {
                "status": "error",
                "error": f"Script file not found: {script_file}",
                "message": f"Skill execution failed: '{script_file}' does not exist."
            }

        file_name = os.path.basename(script_file)
        logger.info(f"[SKILL INVOKE] File: {file_name} | Desc: '{description}' | Args: {json.dumps(args or {}, default=str)}")

        # Safety validation for Python scripts
        if script_file.endswith(".py"):
            try:
                with open(script_file, "r", encoding="utf-8") as f:
                    code = f.read()
                is_safe, error_msg = validate_python_script(code)
                if not is_safe:
                    logger.warning(f"[SKILL BLOCKED] AST Gatekeeper rejected {file_name}: {error_msg}")
                    return {
                        "status": "blocked",
                        "error": f"AST Gatekeeper Rejected: {error_msg}",
                        "message": f"Skill execution was blocked by the safety gatekeeper: {error_msg}"
                    }
            except Exception as e:
                logger.error(f"[SKILL ERROR] Failed reading script {file_name}: {e}")
                return {"status": "error", "error": f"Failed reading script: {e}"}
        else:
            code = ""

        cmd = [self.python_exe, script_file]
        if args is not None:
            cmd.append(json.dumps(args))

        try:
            exit_code, stdout_raw, stderr_raw, elapsed_ms, peak_mb = self._run_subprocess_monitored(
                cmd=cmd,
                timeout=timeout,
                env=self._get_execution_env(args)
            )

            stdout = (stdout_raw or "").strip()
            stderr = (stderr_raw or "").strip()
            success = (exit_code == 0)

            if len(stdout) > 2500:
                stdout = stdout[:2500] + "\n... [truncated]"
            if len(stderr) > 1500:
                stderr = stderr[:1500] + "\n... [truncated]"

            if success:
                logger.info(f"[SKILL SUCCESS] {file_name} | Duration: {elapsed_ms:.1f}ms | PeakMem: {peak_mb:.1f}MB | ExitCode: 0")
                msg = f"Skill '{file_name}' executed successfully." + (f" ({description})" if description else "")
                if stdout:
                    msg += f" Output: {stdout}"
            else:
                logger.error(f"[SKILL FAILED] {file_name} | Duration: {elapsed_ms:.1f}ms | ExitCode: {exit_code} | Stderr: {stderr or stdout}")
                msg = f"Skill '{file_name}' failed (exit code {exit_code})."
                if stderr:
                    msg += f" Traceback: {stderr}"
                elif stdout:
                    msg += f" Output: {stdout}"

            return {
                "status": "success" if success else "error",
                "exit_code": exit_code,
                "duration_ms": round(elapsed_ms, 1),
                "execution_time_ms": round(elapsed_ms, 1),
                "memory_peak_mb": round(peak_mb, 2),
                "description": description,
                "file": script_file,
                "stdout": stdout,
                "stderr": stderr,
                "traceback": stderr if not success else "",
                "error": stderr if not success else "",
                "message": msg
            }

        except subprocess.TimeoutExpired:
            elapsed_ms = (time.perf_counter() - t0) * 1000 if 't0' in locals() else timeout * 1000
            logger.error(f"[SKILL TIMEOUT] {file_name} | Duration: {elapsed_ms:.1f}ms (Limit: {timeout}s)")
            return {
                "status": "timeout",
                "duration_ms": round(elapsed_ms, 1),
                "description": description,
                "file": script_file,
                "error": f"Execution timed out after {timeout} seconds.",
                "message": f"Skill timed out after {timeout} seconds."
            }
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000 if 't0' in locals() else 0.0
            logger.error(f"[SKILL ERROR] {file_name} | Duration: {elapsed_ms:.1f}ms | Error: {e}")
            return {
                "status": "error",
                "duration_ms": round(elapsed_ms, 1),
                "description": description,
                "file": script_file,
                "error": str(e),
                "traceback": str(e),
                "message": f"Skill failed to run: {e}"
            }
