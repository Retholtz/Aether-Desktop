"""
core/telemetry - Unified Telemetry Subsystem for Aether
"""

from core.telemetry.base import BaseTelemetryAdapter, NoOpTelemetryAdapter
from core.telemetry.elite import EliteTelemetryAdapter
from core.telemetry.factory import create_telemetry_adapter, ADAPTER_REGISTRY
from core.telemetry.schema import TelemetryFrame, GameTelemetryProfile, HudBarRegion, HudBarCalibration
from core.telemetry.copilot_gate import CopilotEventGate
from core.telemetry.telemetry_service import TelemetryService

__all__ = [
    "BaseTelemetryAdapter",
    "NoOpTelemetryAdapter",
    "EliteTelemetryAdapter",
    "create_telemetry_adapter",
    "ADAPTER_REGISTRY",
    "TelemetryFrame",
    "GameTelemetryProfile",
    "HudBarRegion",
    "HudBarCalibration",
    "CopilotEventGate",
    "TelemetryService",
]

