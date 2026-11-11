"""
core/briefing.py - Proactive Cold-Start Narrative Briefing Dispatcher
"""
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from core.logger import get_logger

logger = get_logger("BriefingDispatcher")


class ColdStartBriefing(BaseModel):
    campaign_name: str
    active_location: str
    atmospheric_framing: str
    prior_session_recap: str
    active_quest_objective: str
    party_vitals_summary: str
    spoken_intro: str


class ColdStartBriefingEngine:
    @classmethod
    def synthesize_cold_start_briefing(
        cls,
        manifest_data: Dict[str, Any],
        state_data: Dict[str, Any]
    ) -> ColdStartBriefing:
        campaign_name = manifest_data.get("name") or manifest_data.get("title", "The Adventure")
        active_loc_id = manifest_data.get("active_location_id") or state_data.get("current_location", "Unknown Location")

        # Resolve active location sensory details
        locations = manifest_data.get("locations", {})
        active_node = locations.get(active_loc_id, {})
        loc_name = active_node.get("name", active_loc_id.replace("_", " ").title())
        hazards = active_node.get("hazards", [])
        hazard_text = f" Environmental conditions: {', '.join(hazards)}." if hazards else ""
        loc_desc = active_node.get("description", "A quiet space.")

        # Resolve prior recap
        prior_recap = state_data.get("latest_recap") or "The party gathers their strength and prepares to advance."

        # Resolve active quest
        quests = manifest_data.get("quests", {})
        active_quest = next((q for q in quests.values() if str(q.get("status", "")).lower() in ("active", "queststatus.active")), None)
        if active_quest:
            q_title = active_quest.get("title", "Main Quest")
            mandatory_objs = [o.get("description") for o in active_quest.get("objectives", []) if o.get("is_mandatory") and not o.get("is_completed")]
            curr_obj = mandatory_objs[0] if mandatory_objs else "Explore and survive."
            quest_text = f"Current Priority: {q_title} — {curr_obj}"
        else:
            quest_text = "No active quest registered. The path ahead is open."

        # Resolve party condition
        raw_pcs = state_data.get("characters", [])
        pcs: List[Dict[str, Any]] = list(raw_pcs.values()) if isinstance(raw_pcs, dict) else (raw_pcs if isinstance(raw_pcs, list) else [])
        alive_count = sum(1 for c in pcs if c.get("vitals", {}).get("health", {}).get("current", 0) > 0)
        party_summary = f"{alive_count}/{len(pcs)} heroes standing" if pcs else "Party assembled"

        # Construct high-immersion 3-sentence spoken intro
        spoken_intro = (
            f"Welcome back to {campaign_name}. You are in {loc_name}. "
            f"Last session, {prior_recap.rstrip('.')} Ahead of you lies your objective: {quest_text.split('—')[-1].strip()} "
            f"How do you proceed?"
        )

        briefing = ColdStartBriefing(
            campaign_name=campaign_name,
            active_location=loc_name,
            atmospheric_framing=f"{loc_desc}{hazard_text}",
            prior_session_recap=prior_recap,
            active_quest_objective=quest_text,
            party_vitals_summary=party_summary,
            spoken_intro=spoken_intro
        )
        logger.info("Cold-start briefing generated for %s in %s", campaign_name, loc_name)
        return briefing


def synthesize_cold_start_briefing(manifest_data: Dict[str, Any], state_data: Dict[str, Any]) -> ColdStartBriefing:
    """Convenience functional interface for ColdStartBriefingEngine."""
    return ColdStartBriefingEngine.synthesize_cold_start_briefing(manifest_data, state_data)
