"""
core/telemetry - Unified Telemetry Subsystem for Aether
"""

from core.telemetry.base import BaseTelemetryAdapter, NoOpTelemetryAdapter
from core.telemetry.elite import EliteTelemetryAdapter
from core.telemetry.factory import create_telemetry_adapter, ADAPTER_REGISTRY

__all__ = [
    "BaseTelemetryAdapter",
    "NoOpTelemetryAdapter",
    "EliteTelemetryAdapter",
    "create_telemetry_adapter",
    "ADAPTER_REGISTRY",
]

