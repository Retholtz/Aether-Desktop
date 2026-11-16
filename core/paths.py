"""
Aether Desktop - Frozen/Source Path Resolution Helpers

Separates read-only bundled resources (located in sys._MEIPASS when frozen)
from writable application data/config directories (located next to sys.executable
when frozen, or at the repository root when running from source).
"""

import os
import sys


def is_frozen() -> bool:
    """Returns True when running inside a PyInstaller/frozen executable."""
    return bool(getattr(sys, "frozen", False))


def get_bundle_dir() -> str:
    """
    Returns the root directory containing bundled read-only resources
    (ui/static, ui/sounds, assets/, models/).
    """
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_app_dir() -> str:
    """
    Returns the writable application directory for runtime data
    (config.json, .env, data/, logs/, profile/, scripts/cache/).
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(*parts: str) -> str:
    """Joins path components relative to the bundled read-only resource root."""
    return os.path.join(get_bundle_dir(), *parts)


def app_path(*parts: str) -> str:
    """Joins path components relative to the writable application directory."""
    return os.path.join(get_app_dir(), *parts)
