"""
tools/lore_tools.py - Role-Gated Lore & Clue Retrieval
"""
import re
from typing import Dict, Any, List, Optional
from core.logger import get_logger

logger = get_logger("LoreTools")


def tool_query_campaign_lore(
    query: str,
    role: str = "player",
    manifest_data: Optional[Dict[str, Any]] = None,
    state_data: Optional[Dict[str, Any]] = None,
    campaign_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Retrieves lore, clues, and location descriptions with strict spoiler protection.
    Player role omits hidden secrets and unrevealed clues.
    DM role returns unredacted records.
    """
    manifest = manifest_data or (campaign_data.get("manifest", campaign_data) if campaign_data else {}) or {}
    state = state_data or (campaign_data.get("state", campaign_data) if campaign_data else {}) or {}
    q_low = query.strip().lower()

    matched_clues = []
    # Search clues (manifest clues dict or list)
    clues_source = manifest.get("clues", {})
    clues_list = clues_source.values() if isinstance(clues_source, dict) else (clues_source if isinstance(clues_source, list) else [])

    for clue in clues_list:
        c_dict = clue if isinstance(clue, dict) else (clue.__dict__ if hasattr(clue, "__dict__") else {})
        if role == "player" and not c_dict.get("is_revealed", False):
            continue  # Strictly hide unrevealed secrets from players

        title = c_dict.get("title", "")
        content = c_dict.get("content", "")
        if q_low in title.lower() or q_low in content.lower():
            matched_clues.append({"title": title, "content": content})

    matched_locations = []
    # Search locations
    locs_source = manifest.get("locations", {})
    locs_list = locs_source.values() if isinstance(locs_source, dict) else (locs_source if isinstance(locs_source, list) else [])

    for loc in locs_list:
        l_dict = loc if isinstance(loc, dict) else (loc.__dict__ if hasattr(loc, "__dict__") else {})
        name = l_dict.get("name", "")
        desc = l_dict.get("description", "")
        if q_low in name.lower() or q_low in desc.lower():
            # If player, sanitize GM-only annotations from description
            cleaned_desc = desc
            if role == "player":
                cleaned_desc = re.sub(
                    r'(?i)(?:dm notes|traps & secrets|monster tactics):.*?(?=(\n\n|\Z))',
                    '',
                    cleaned_desc
                ).strip()

            loc_entry = {
                "name": name,
                "description": cleaned_desc,
                "hazards": l_dict.get("hazards", [])
            }
            if role == "dm":
                loc_entry["hidden_secrets"] = l_dict.get("hidden_secrets", [])
            matched_locations.append(loc_entry)

    matched_journal = []
    # Search persistent session journal
    for j in state.get("journal", []):
        j_dict = j if isinstance(j, dict) else (j.__dict__ if hasattr(j, "__dict__") else {})
        text = f"{j_dict.get('summary', '')} {j_dict.get('last_action', '')} {j_dict.get('text', '')}"
        if q_low in text.lower():
            matched_journal.append({
                "timestamp": j_dict.get("timestamp"),
                "summary": j_dict.get("summary"),
                "location": j_dict.get("location")
            })

    total = len(matched_clues) + len(matched_locations) + len(matched_journal)
    logger.info("Lore query '%s' by role '%s' returned %d results", query, role, total)
    return {
        "query": query,
        "role": role,
        "clues": matched_clues,
        "locations": matched_locations,
        "journal_entries": matched_journal,
        "total_results": total
    }
