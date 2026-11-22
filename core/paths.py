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
    Returns the application directory (next to sys.executable when frozen,
    or the repository root when running from source).
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_user_data_dir() -> str:
    """
    Returns the guaranteed-writable per-user data directory:
    - Frozen builds: %LOCALAPPDATA%\\Aether (created on demand)
    - Source/dev builds: repository root
    """
    if is_frozen():
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        user_dir = os.path.join(base, "Aether")
        os.makedirs(user_dir, exist_ok=True)
        return user_dir
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(*parts: str) -> str:
    """Joins path components relative to the bundled read-only resource root."""
    return os.path.join(get_bundle_dir(), *parts)


def app_path(*parts: str) -> str:
    """Joins path components relative to the application directory."""
    return os.path.join(get_app_dir(), *parts)


def user_data_path(*parts: str) -> str:
    """Joins path components relative to the writable per-user data directory."""
    return os.path.join(get_user_data_dir(), *parts)
