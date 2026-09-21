"""
Aether Desktop - Tools Subsystem
Tier 1: OS native window management (os_controls.py)
Tier 2: Low-level GUI primitives (gui_primitives.py)
Tier 3: Sandboxed script runner (script_runner.py)
Central Router: ToolDispatcher (dispatcher.py)
"""

from tools.dispatcher import ToolDispatcher, get_all_tool_declarations
from tools.os_controls import (
    find_hwnd_by_query,
    maximize_window,
    minimize_window,
    restore_window,
    focus_window,
    close_window,
)
from tools.gui_primitives import GuiPrimitivesController
from tools.script_runner import ScriptRunner

__all__ = [
    "ToolDispatcher",
    "get_all_tool_declarations",
    "find_hwnd_by_query",
    "maximize_window",
    "minimize_window",
    "restore_window",
    "focus_window",
    "close_window",
    "GuiPrimitivesController",
    "ScriptRunner",
]

