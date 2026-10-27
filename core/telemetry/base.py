"""
core/telemetry/base.py - Abstract Base Contract for Game Telemetry Adapters.

Defines the standard interface for receiving in-game state, event milestones,
and situational context across any game (log streaming, memory, UDP, or scratchpad).
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Callable


class BaseTelemetryAdapter(ABC):
    """Abstract base class that all game telemetry adapters must implement."""

    def __init__(
        self,
        game_id: str,
        profile: Optional[Dict[str, Any]] = None,
        on_milestone: Optional[Callable[[str, str, str, str], None]] = None
    ):
        self.game_id = game_id
        self.profile = profile or {}
        self.on_milestone = on_milestone
        self.is_running = False
        self.state: Dict[str, Any] = {"game_id": game_id, "active": False}

    @abstractmethod
    def start(self) -> None:
        """Start listening, tailing, or polling for telemetry."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop telemetry monitoring and cleanly release resources."""
        pass

    @abstractmethod
    def is_active(self) -> bool:
        """Returns True if the telemetry source is currently actively reporting data."""
        pass

    @abstractmethod
    def get_state(self) -> Dict[str, Any]:
        """Returns the full telemetry state snapshot."""
        pass

    @abstractmethod
    def get_state_flags(self) -> Dict[str, bool]:
        """
        Returns boolean state flags for condition verification.
        (e.g., {'landing_gear_down': True, 'docked': False, 'shields_up': True})
        """
        pass

    @abstractmethod
    def get_summary_prompt_context(self) -> str:
        """Returns a formatted telemetry summary string for LLM system context."""
        pass

    def get_location(self) -> Dict[str, Any]:
        """
        Returns normalized location dict:
        {'system': ..., 'body': ..., 'station': ..., 'coordinates': ...}
        """
        st = self.get_state()
        return {
            "system": st.get("star_system") or st.get("zone") or st.get("region") or "Unknown",
            "body": st.get("body") or st.get("sub_area"),
            "station": st.get("station") or st.get("settlement"),
            "docked": st.get("docked", False)
        }


class NoOpTelemetryAdapter(BaseTelemetryAdapter):
    """Fallback telemetry adapter for games without live telemetry support."""

    def start(self) -> None:
        self.is_running = True

    def stop(self) -> None:
        self.is_running = False

    def is_active(self) -> bool:
        return False

    def get_state(self) -> Dict[str, Any]:
        return {"game_id": self.game_id, "active": False}

    def get_state_flags(self) -> Dict[str, bool]:
        return {}

    def get_summary_prompt_context(self) -> str:
        return ""
