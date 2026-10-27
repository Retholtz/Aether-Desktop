"""
core/telemetry/factory.py - Factory for instantiating game telemetry adapters.
"""

from typing import Dict, Any, Optional, Callable, Type
from core.telemetry.base import BaseTelemetryAdapter, NoOpTelemetryAdapter
from core.telemetry.elite import EliteTelemetryAdapter
from core.logger import get_logger

logger = get_logger("TelemetryFactory")

ADAPTER_REGISTRY: Dict[str, Type[BaseTelemetryAdapter]] = {
    "elite_dangerous": EliteTelemetryAdapter,
    "elite_journal": EliteTelemetryAdapter,
    "none": NoOpTelemetryAdapter,
    "scratchpad_only": NoOpTelemetryAdapter,
}


def create_telemetry_adapter(
    game_id: str,
    profile: Optional[Dict[str, Any]] = None,
    on_milestone: Optional[Callable[[str, str, str, str], None]] = None
) -> BaseTelemetryAdapter:
    """
    Creates and returns a telemetry adapter based on game profile configuration.
    Falls back gracefully to NoOpTelemetryAdapter if no adapter is configured.
    """
    prof = profile or {}
    telemetry_cfg = prof.get("telemetry") or {}
    adapter_key = (
        telemetry_cfg.get("adapter")
        or telemetry_cfg.get("type")
        or prof.get("telemetry_type")
        or game_id
    )

    adapter_cls = ADAPTER_REGISTRY.get(adapter_key.lower() if isinstance(adapter_key, str) else "")
    if adapter_cls:
        logger.info(f"Instantiating {adapter_cls.__name__} for game '{game_id}'.")
        return adapter_cls(game_id=game_id, profile=prof, on_milestone=on_milestone)

    logger.debug(f"No specific telemetry adapter found for '{game_id}'. Using NoOpTelemetryAdapter.")
    return NoOpTelemetryAdapter(game_id=game_id, profile=prof, on_milestone=on_milestone)

