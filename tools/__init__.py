"""
Aether Desktop - Tools Subsystem
Tier 1: OS native window management (os_controls.py)
Tier 2: Low-level GUI primitives (gui_primitives.py)
Tier 3: Sandboxed script runner (script_runner.py)
Central Router: ToolDispatcher (dispatcher.py)
"""

from tools.dispatcher import ToolDispatcher, get_all_tool_declarations, register_tool
from tools.os_controls import (
    find_hwnd_by_query,
    maximize_window,
    minimize_window,
    restore_window,
    focus_window,
    close_window,
    navigate_browser,
    move_window_to_monitor,
    get_monitors_info,
    OSControls,
    type_text,
    send_directinput_key,
    send_directinput_combo,
    SCANCODE_MAP,
)
from tools.gui_primitives import GuiPrimitivesController
from tools.script_runner import ScriptRunner

from tools.clipboard import (
    set_clipboard_html,
    set_clipboard_text,
    get_clipboard_text,
    format_windows_html_clipboard,
    markdown_to_html_for_clipboard,
    clean_markdown_typography,
    paste_markdown_to_target_app,
)

__all__ = [
    "ToolDispatcher",
    "get_all_tool_declarations",
    "register_tool",
    "find_hwnd_by_query",
    "maximize_window",
    "minimize_window",
    "restore_window",
    "focus_window",
    "close_window",
    "navigate_browser",
    "move_window_to_monitor",
    "get_monitors_info",
    "OSControls",
    "type_text",
    "send_directinput_key",
    "send_directinput_combo",
    "SCANCODE_MAP",
    "GuiPrimitivesController",
    "ScriptRunner",
    "set_clipboard_html",
    "set_clipboard_text",
    "get_clipboard_text",
    "format_windows_html_clipboard",
    "markdown_to_html_for_clipboard",
    "paste_markdown_to_target_app",
]

