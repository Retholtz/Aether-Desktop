"""
tools/campaign_tools.py - LLM Function Bindings for Campaign & World State Mutation
"""
from typing import Dict, Any, Optional
from core.campaign.campaign_manager import CampaignManager
from core.campaign.schema import QuestStatus, FactionDisposition

# Module-level singleton tied to active campaign session
_CAMPAIGN = CampaignManager(campaign_id="active_campaign")


def tool_navigate_location(location_id: str) -> Dict[str, Any]:
    """Moves the party to a new location node, updating description, hazards, and visibility."""
    return _CAMPAIGN.set_active_location(location_id)


def tool_reveal_clue(clue_id: str) -> Dict[str, Any]:
    """Reveals an investigated clue or lore fragment to the party."""
    return _CAMPAIGN.reveal_clue(clue_id)


def tool_update_quest_status(quest_id: str, new_status: str) -> Dict[str, Any]:
    """Updates high-level quest status: 'active', 'completed', 'failed', or 'unassigned'."""
    status_enum = QuestStatus(new_status.lower().strip())
    return _CAMPAIGN.update_quest_status(quest_id, status_enum)


def tool_complete_quest_objective(quest_id: str, objective_id: str) -> Dict[str, Any]:
    """Marks a specific objective within a quest as completed."""
    return _CAMPAIGN.complete_objective(quest_id, objective_id)


def tool_adjust_faction_reputation(faction_id: str, delta: int, disposition: Optional[str] = None) -> Dict[str, Any]:
    """Modifies a faction's influence (0-100) and optional disposition."""
    disp_enum = FactionDisposition(disposition.lower().strip()) if disposition else None
    return _CAMPAIGN.adjust_faction_reputation(faction_id, delta, disp_enum)


def tool_get_campaign_context() -> str:
    """Returns the current formatted campaign prompt injection string."""
    return _CAMPAIGN.generate_prompt_context()
