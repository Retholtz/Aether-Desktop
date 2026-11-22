"""
core/binds_scanners - Universal Declarative & Extensible Keybinding Scanners Registry.

Resolution order:
1. Trusted Tier 2 Python Game Extension (`extension.scan_binds(profile)`), if installed & trusted.
2. Declarative pure-JSON `binds_scanner` spec from the profile or matching `.aethermodule.json`.
3. Legacy named scanner registry fallback (for backward compatibility).
"""

from typing import Dict, Any, Callable, Optional
from core.binds_scanners.declarative import scan_declarative_bindings
from core.binds_scanners.elite_xml import scan_elite_bindings

SCANNER_REGISTRY: Dict[str, Callable[[Dict[str, Any]], Dict[str, dict]]] = {
    "elite_xml": scan_elite_bindings,
    "elite_dangerous": scan_elite_bindings,
}


def scan_profile_bindings(profile: Dict[str, Any], game_id: str = "") -> Dict[str, dict]:
    """
    Executes the appropriate keybind scanner for `profile`:
    - Checks trusted Tier 2 `.py` extension first (if any).
    - Uses the declarative JSON `binds_scanner` block from the profile or its `.aethermodule.json`.
    - Falls back to legacy named scanners if configured.
    """
    if not isinstance(profile, dict):
        return {}

    gid = str(game_id or profile.get("module_id") or profile.get("game_id") or "").strip()
    mod: Optional[Dict[str, Any]] = None
    try:
        from core.game_modules import get_module_for_game
        mod = get_module_for_game(gid or profile.get("display_name", ""), profile=profile)
    except Exception:
        mod = None

    # 1. Optional Tier 2 trusted Python extension hook
    ext_mod_id = (mod.get("module_id") if isinstance(mod, dict) else "") or gid
    if ext_mod_id:
        try:
            from core.game_extensions import load_trusted_extension
            ext_script = (mod.get("extension") if isinstance(mod, dict) else None) or "extension.py"
            ext = load_trusted_extension(ext_mod_id, script_name=str(ext_script))
            if ext is not None:
                ext_res = ext.scan_binds(profile)
                if isinstance(ext_res, dict) and ext_res:
                    return ext_res
        except Exception:
            pass

    # 2. Declarative pure-JSON binds_scanner spec (profile override or bundled/user module)
    scanner_spec = None
    if isinstance(profile.get("binds_scanner"), dict) and profile["binds_scanner"]:
        scanner_spec = profile["binds_scanner"]
    elif isinstance(mod, dict) and isinstance(mod.get("binds_scanner"), dict) and mod["binds_scanner"]:
        scanner_spec = mod["binds_scanner"]

    if scanner_spec:
        res = scan_declarative_bindings(profile, scanner_spec)
        if res:
            return res

    # 3. Legacy string-named scanner fallback
    binds_cfg = profile.get("binds_config") or {}
    scanner_type = binds_cfg.get("type") or (
        profile.get("binds_scanner") if isinstance(profile.get("binds_scanner"), str) else None
    )
    if not scanner_type:
        dname = (profile.get("display_name") or "").lower()
        if "elite" in dname:
            scanner_type = "elite_xml"

    if not scanner_type:
        return {}

    scanner_fn = SCANNER_REGISTRY.get(str(scanner_type).lower())
    if scanner_fn:
        return scanner_fn(profile)
    return {}
