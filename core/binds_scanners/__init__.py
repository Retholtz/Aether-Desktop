"""
core/binds_scanners - Modular Keybinding Scanners Registry.
"""

from typing import Dict, Any, Callable
from core.binds_scanners.elite_xml import scan_elite_bindings

SCANNER_REGISTRY: Dict[str, Callable[[Dict[str, Any]], Dict[str, dict]]] = {
    "elite_xml": scan_elite_bindings,
    "elite_dangerous": scan_elite_bindings,
}


def scan_profile_bindings(profile: Dict[str, Any]) -> Dict[str, dict]:
    """
    Executes the appropriate keybind scanner configured for this profile.
    Falls back to name inference or returns empty dict if no scanner is configured.
    """
    binds_cfg = profile.get("binds_config") or {}
    scanner_type = binds_cfg.get("type") or profile.get("binds_scanner")
    if not scanner_type:
        dname = (profile.get("display_name") or "").lower()
        if "elite" in dname:
            scanner_type = "elite_xml"

    if not scanner_type:
        return {}

    scanner_fn = SCANNER_REGISTRY.get(scanner_type.lower())
    if scanner_fn:
        return scanner_fn(profile)
    return {}
