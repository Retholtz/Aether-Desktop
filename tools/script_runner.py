"""
Aether Desktop - Tier 3: Sandboxed Script Runner
Executes dynamically generated Python and PowerShell automation scripts in the background
with pre-execution AST safety gatekeeping and isolated subprocess containment.
"""

import datetime
import os
import subprocess
import sys
from typing import Dict, Optional

from security.ast_gatekeeper import validate_python_script


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

    def execute_script(
        self,
        script_code: str,
        script_type: str = "python",
        description: str = "",
        timeout: int = 30
    ) -> Dict:
        """
        Validates safety via AST gatekeeper (for Python) and executes script in a background subprocess.

        Args:
            script_code: The raw script source code.
            script_type: 'python' or 'powershell'. Default is 'python'.
            description: Brief summary of what the script accomplishes.
            timeout: Maximum execution time in seconds.

        Returns:
            Dict containing status, stdout, stderr, exit_code, and friendly summary.
        """
        script_clean = (script_code or "").strip()
        if not script_clean:
            return {
                "status": "error",
                "script_type": script_type,
                "error": "Script code cannot be empty.",
                "message": "Script execution aborted: No code provided."
            }

        st_lower = script_type.lower().strip()

        # 1. AST Security Gatekeeper validation for Python scripts
        if st_lower == "python":
            is_safe, error_msg = validate_python_script(script_clean)
            if not is_safe:
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
        except Exception as e:
            return {
                "status": "error",
                "script_type": script_type,
                "error": f"Failed to save script to cache: {e}"
            }

        # 3. Build execution command
        if st_lower == "python":
            cmd = [self.python_exe, script_file]
        elif st_lower in ("powershell", "ps1"):
            cmd = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-File", script_file
            ]
        else:
            return {
                "status": "error",
                "script_type": script_type,
                "error": f"Unsupported script type '{script_type}'. Must be 'python' or 'powershell'."
            }

        # 4. Execute in isolated subprocess
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(5, min(120, timeout)),
                cwd=self.workspace_root
            )

            stdout = (res.stdout or "").strip()
            stderr = (res.stderr or "").strip()
            exit_code = res.returncode
            success = (exit_code == 0)

            # Cap output lengths for Gemini context
            if len(stdout) > 2000:
                stdout = stdout[:2000] + "\n... [truncated]"
            if len(stderr) > 1000:
                stderr = stderr[:1000] + "\n... [truncated]"

            if success:
                msg = f"Script executed successfully." + (f" ({description})" if description else "")
                if stdout:
                    msg += f" Output: {stdout}"
            else:
                msg = f"Script exited with code {exit_code}."
                if stderr:
                    msg += f" Error: {stderr}"
                elif stdout:
                    msg += f" Output: {stdout}"

            return {
                "status": "success" if success else "error",
                "script_type": st_lower,
                "exit_code": exit_code,
                "description": description,
                "file": script_file,
                "stdout": stdout,
                "stderr": stderr,
                "message": msg
            }

        except subprocess.TimeoutExpired:
            return {
                "status": "timeout",
                "script_type": st_lower,
                "description": description,
                "file": script_file,
                "error": f"Execution timed out after {timeout} seconds.",
                "message": f"Script timed out after {timeout} seconds."
            }
        except Exception as e:
            return {
                "status": "error",
                "script_type": st_lower,
                "description": description,
                "file": script_file,
                "error": str(e),
                "message": f"Script failed to run: {e}"
            }

