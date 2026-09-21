"""
Aether Desktop - Core Desktop Control Proxy
Re-exports GuiPrimitivesController as DesktopController for backward compatibility.
"""

from tools.gui_primitives import (
    GuiPrimitivesController as DesktopController,
    MOUSEINPUT,
    KEYBDINPUT,
    HARDWAREINPUT,
    INPUT,
    ULONG_PTR,
)
from tools.os_controls import focus_window

__all__ = [
    "DesktopController",
    "MOUSEINPUT",
    "KEYBDINPUT",
    "HARDWAREINPUT",
    "INPUT",
    "ULONG_PTR",
    "focus_window",
]
