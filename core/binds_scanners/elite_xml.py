"""
core/binds_scanners/elite_xml.py - Backward-Compatibility Wrapper for Elite Dangerous XML Keybinds.

Delegates to the universal pure-JSON declarative scanner (`core/binds_scanners/declarative.py`)
using the `binds_scanner` specification in `elite_dangerous.aethermodule.json`.
No hardcoded allowlist — imports all bound actions from the user's `.binds` file.
"""

from typing import Dict, Any
from core.binds_scanners.declarative import scan_declarative_bindings

DEFAULT_BINDS_DIR = r"%LOCALAPPDATA%\Frontier Developments\Elite Dangerous\Options\Bindings"

_FALLBACK_SPEC: Dict[str, Any] = {
    "format": "xml_primary_secondary",
    "dir": DEFAULT_BINDS_DIR,
    "file_glob": "*.binds",
    "preset_pointer_file": "StartPreset.4.start",
    "key_prefix": "Key_",
    "device_priority": ["Keyboard", "Mouse", "GamePad", "Controller"],
    "auto_import_all": True,
}


def scan_elite_bindings(profile: Dict[str, Any]) -> Dict[str, dict]:
    """Scans all active keybinds from Elite Dangerous XML binds files via the declarative scanner."""
    spec = None
    try:
        from core.game_modules import get_module_for_game
        mod = get_module_for_game("elite_dangerous", profile=profile if isinstance(profile, dict) else None)
        if isinstance(mod, dict) and isinstance(mod.get("binds_scanner"), dict):
            spec = mod["binds_scanner"]
    except Exception:
        spec = None

    return scan_declarative_bindings(profile if isinstance(profile, dict) else {}, spec or _FALLBACK_SPEC)
