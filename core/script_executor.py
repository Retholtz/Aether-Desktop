"""
Aether Desktop - Core Script Executor Proxy
Re-exports ScriptRunner as ScriptExecutor for backward compatibility.
"""

from tools.script_runner import ScriptRunner as ScriptExecutor

__all__ = ["ScriptExecutor"]
