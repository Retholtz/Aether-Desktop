"""
Aether Desktop - Tier 3: Sandboxed Script Runner
Executes dynamically generated Python and PowerShell automation scripts in the background
with pre-execution AST safety gatekeeping and isolated subprocess containment.
"""

import datetime
import json
import os
import subprocess
import sys
import time
from typing import Dict, Optional, Any

from core.logger import get_logger
from security.ast_gatekeeper import validate_python_script

logger = get_logger("ScriptRunner")


class ScriptRunner:
    """Manages secure generation and background execution of automation scripts."""

    def __init__(self, workspace_root: Optional[str] = None):
        self.workspace_root = workspace_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.cache_dir = os.path.join(self.workspace_root, "scripts", "cache")
        os.makedirs(self.cache_dir, exist_ok=True)

        # Locate active virtual environment Python interpreter
        venv_python = os.path.join(self.workspace_root, ".venv", "Scripts", "python.exe")
        if os.path.exists(venv_python):
            self.python_exe = venv_python
        else:
            self.python_exe = sys.executable

    def _get_execution_env(self, args: Optional[dict] = None) -> dict:
        env = os.environ.copy()
        python_path = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (self.workspace_root + os.pathsep + python_path) if python_path else self.workspace_root
        if args is not None:
            env["SKILL_ARGS"] = json.dumps(args)
        return env

    def execute_script(
        self,
        script_code: str,
        script_type: str = "python",
        description: str = "",
        args: Optional[dict] = None,
        timeout: int = 30
    ) -> Dict:
        """
        Validates safety via AST gatekeeper (for Python) and executes script in a background subprocess.

        Args:
            script_code: The raw script source code.
            script_type: 'python' or 'powershell'. Default is 'python'.
            description: Brief summary of what the script accomplishes.
            args: Optional dictionary of arguments passed to the script via SKILL_ARGS and CLI JSON.
            timeout: Maximum execution time in seconds.

        Returns:
            Dict containing status, stdout, stderr, traceback, exit_code, and friendly summary.
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
        logger.info(f"[SCRIPT INVOKE] Type: {st_lower} | Desc: '{description}' | Args: {json.dumps(args or {}, default=str)}")
        logger.debug(f"[SCRIPT CODE]\n{script_clean}")

        # 1. AST Security Gatekeeper validation for Python scripts
        if st_lower == "python":
            is_safe, error_msg = validate_python_script(script_clean)
            if not is_safe:
                logger.warning(f"[SCRIPT BLOCKED] AST Gatekeeper rejected script: {error_msg}")
                return {
                    "status": "blocked",
                    "script_type": "python",
                    "description": description,
                    "error": f"AST Gatekeeper Rejected: {error_msg}",
                    "message": f"Script execution was blocked by the safety gatekeeper: {error_msg}"
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

        # 4. Execute in isolated subprocess with workspace environment
        t0 = time.perf_counter()
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(5, min(120, timeout)),
                cwd=self.workspace_root,
                env=self._get_execution_env(args)
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000

            stdout = (res.stdout or "").strip()
            stderr = (res.stderr or "").strip()
            exit_code = res.returncode
            success = (exit_code == 0)

            # Cap output lengths for Gemini context
            if len(stdout) > 2500:
                stdout = stdout[:2500] + "\n... [truncated]"
            if len(stderr) > 1500:
                stderr = stderr[:1500] + "\n... [truncated]"

            if success:
                logger.info(f"[SCRIPT SUCCESS] Duration: {elapsed_ms:.1f}ms | ExitCode: 0 | Output: {stdout[:300] if stdout else '(none)'}")
                msg = f"Script executed successfully." + (f" ({description})" if description else "")
                if stdout:
                    msg += f" Output: {stdout}"
            else:
                logger.error(f"[SCRIPT FAILED] Duration: {elapsed_ms:.1f}ms | ExitCode: {exit_code} | Stderr: {stderr or stdout}")
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
            return {
                "status": "timeout",
                "script_type": st_lower,
                "duration_ms": round(elapsed_ms, 1),
                "description": description,
                "file": script_file,
                "error": f"Execution timed out after {timeout} seconds.",
                "message": f"Script timed out after {timeout} seconds."
            }
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.error(f"[SCRIPT ERROR] Duration: {elapsed_ms:.1f}ms | Error: {e}")
            return {
                "status": "error",
                "script_type": st_lower,
                "duration_ms": round(elapsed_ms, 1),
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
        Executes an existing script file from the Skill Library with arguments.
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

        cmd = [self.python_exe, script_file]
        if args is not None:
            cmd.append(json.dumps(args))

        t0 = time.perf_counter()
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(5, min(120, timeout)),
                cwd=self.workspace_root,
                env=self._get_execution_env(args)
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000

            stdout = (res.stdout or "").strip()
            stderr = (res.stderr or "").strip()
            exit_code = res.returncode
            success = (exit_code == 0)

            if len(stdout) > 2500:
                stdout = stdout[:2500] + "\n... [truncated]"
            if len(stderr) > 1500:
                stderr = stderr[:1500] + "\n... [truncated]"

            if success:
                logger.info(f"[SKILL SUCCESS] {file_name} | Duration: {elapsed_ms:.1f}ms | ExitCode: 0 | Output: {stdout[:300] if stdout else '(none)'}")
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
            elapsed_ms = (time.perf_counter() - t0) * 1000
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


