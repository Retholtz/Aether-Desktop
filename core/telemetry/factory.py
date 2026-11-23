"""
core/telemetry/factory.py - Factory for instantiating game telemetry adapters.

Resolution order:
1. Trusted Tier 2 Python Game Extension (`extension.create_telemetry_adapter(...)`), if installed & trusted.
2. Explicit adapter in `profile["telemetry"]` or `profile["telemetry_type"]` (if non-placeholder).
3. Declarative `.aethermodule.json` `telemetry.adapter` (matched by game_id or executable).
4. Graceful fallback to `NoOpTelemetryAdapter`.
"""

from typing import Dict, Any, Optional, Callable, Type
from core.telemetry.base import BaseTelemetryAdapter, NoOpTelemetryAdapter
from core.telemetry.declarative import DeclarativeFileTelemetryAdapter
from core.logger import get_logger

logger = get_logger("TelemetryFactory")

ADAPTER_REGISTRY: Dict[str, Type[BaseTelemetryAdapter]] = {
    "declarative_file": DeclarativeFileTelemetryAdapter,
    "json_journal": DeclarativeFileTelemetryAdapter,
    "native_file": DeclarativeFileTelemetryAdapter,
    # Back-compat aliases for existing saved user profiles; all use the generic declarative engine
    "elite_dangerous": DeclarativeFileTelemetryAdapter,
    "elite_journal": DeclarativeFileTelemetryAdapter,
    "none": NoOpTelemetryAdapter,
    "scratchpad_only": NoOpTelemetryAdapter,
}

_PLACEHOLDER_TYPES = {"", "none", "scratchpad_only"}


def create_telemetry_adapter(
    game_id: str,
    profile: Optional[Dict[str, Any]] = None,
    on_milestone: Optional[Callable[[str, str, str, str], None]] = None
) -> BaseTelemetryAdapter:
    """
    Creates and returns a telemetry adapter based on game profile and matching `.aethermodule.json`.
    Falls back gracefully to NoOpTelemetryAdapter if no adapter is configured.
    """
    prof = profile or {}
    telemetry_cfg = prof.get("telemetry") if isinstance(prof.get("telemetry"), dict) else {}

    # Consult matching declarative game module (so legacy profiles with "scratchpad_only" still hydrate!)
    mod: Optional[Dict[str, Any]] = None
    try:
        from core.game_modules import get_module_for_game
        mod = get_module_for_game(game_id, profile=prof)
    except Exception:
        mod = None

    # 1. Optional Tier 2 trusted Python extension hook
    ext_mod_id = (mod.get("module_id") if isinstance(mod, dict) else "") or game_id
    if ext_mod_id:
        try:
            from core.game_extensions import load_trusted_extension
            ext_script = (mod.get("extension") if isinstance(mod, dict) else None) or "extension.py"
            ext = load_trusted_extension(ext_mod_id, script_name=str(ext_script))
            if ext is not None:
                custom_adapter = ext.create_telemetry_adapter(
                    game_id=game_id, profile=prof, on_milestone=on_milestone
                )
                if custom_adapter is not None:
                    logger.info("Using Tier 2 extension telemetry adapter for '%s'.", game_id)
                    return custom_adapter
        except Exception as e:
            logger.debug("Tier 2 telemetry extension skipped for '%s': %s", game_id, e)

    mod_tel = (mod.get("telemetry") if isinstance(mod, dict) else None) or {}
    raw_prof_key = str(
        telemetry_cfg.get("adapter")
        or telemetry_cfg.get("type")
        or prof.get("telemetry_type")
        or ""
    ).strip()

    if raw_prof_key.lower() not in _PLACEHOLDER_TYPES:
        adapter_key = raw_prof_key
    elif str(mod_tel.get("adapter") or "").strip().lower() not in _PLACEHOLDER_TYPES:
        adapter_key = str(mod_tel["adapter"]).strip()
    else:
        adapter_key = raw_prof_key or game_id

    # Merge module telemetry defaults into profile copy passed to adapter (without mutating caller profile)
    effective_prof = dict(prof)
    if mod_tel:
        merged_tel = dict(mod_tel)
        merged_tel.update({k: v for k, v in telemetry_cfg.items() if v is not None})
        effective_prof["telemetry"] = merged_tel

    adapter_cls = ADAPTER_REGISTRY.get(adapter_key.lower() if isinstance(adapter_key, str) else "")
    if adapter_cls is None:
        eff_tel = effective_prof.get("telemetry") if isinstance(effective_prof.get("telemetry"), dict) else {}
        if eff_tel.get("status_file") or eff_tel.get("native_file_path") or eff_tel.get("journal_glob"):
            adapter_cls = DeclarativeFileTelemetryAdapter
    if adapter_cls:
        logger.info("Instantiating %s for game '%s'.", adapter_cls.__name__, game_id)
        return adapter_cls(game_id=game_id, profile=effective_prof, on_milestone=on_milestone)

    logger.debug("No specific telemetry adapter found for '%s'. Using NoOpTelemetryAdapter.", game_id)
    return NoOpTelemetryAdapter(game_id=game_id, profile=effective_prof, on_milestone=on_milestone)
