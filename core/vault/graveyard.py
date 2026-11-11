"""
core/vault/graveyard.py - Dead Character Archival & Graveyard Ledger
"""
import json
import os
from datetime import datetime, timezone
from typing import Dict, Any
from core.logger import get_logger

logger = get_logger("Graveyard")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def archive_to_graveyard(
    campaign_id: str,
    character_id: str,
    character_data: Dict[str, Any],
    cause_of_death: str = "Slain in combat",
    storage_root: str = "data/campaigns"
) -> str:
    """
    Archives a slain character to the campaign graveyard folder.
    Removes the character from the active campaign roster in state.json and campaign_state.json.
    Returns: The file path of the archived character.
    """
    graveyard_dir = os.path.join(storage_root, campaign_id, "graveyard")
    os.makedirs(graveyard_dir, exist_ok=True)

    archived_data = dict(character_data)
    archived_data["status"] = "slain_archived"
    archived_data["cause_of_death"] = cause_of_death
    archived_data["time_of_death"] = _utc_now_iso()

    target_file = os.path.join(graveyard_dir, f"{character_id}.json")
    with open(target_file, "w", encoding="utf-8") as f:
        json.dump(archived_data, f, indent=2)

    logger.info("Character %s archived to graveyard at %s (Cause: %s)", character_id, target_file, cause_of_death)

    # Clean from state.json and campaign_state.json if present
    for fname in ("state.json", "campaign_state.json"):
        fpath = os.path.join(storage_root, campaign_id, fname)
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                modified = False
                if "characters" in data and isinstance(data["characters"], dict) and character_id in data["characters"]:
                    del data["characters"][character_id]
                    modified = True
                if "state" in data and isinstance(data["state"], dict) and "characters" in data["state"]:
                    if character_id in data["state"]["characters"]:
                        del data["state"]["characters"][character_id]
                        modified = True
                if modified:
                    with open(fpath, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
            except Exception as e:
                logger.warning("Could not purge %s from %s: %s", character_id, fname, e)

    return target_file
