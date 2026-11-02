"""
tools/vault_tools.py - Agent Tools for Character Progression and Vault Queries
Allows Aether to query heroes, execute in-world level-ups, and manage cross-genre gear.
"""
from typing import Dict, Any, Optional, List
from core.character_vault_service import CharacterVaultService


def register_vault_tools(dispatcher, vault_service: CharacterVaultService, get_current_genre_cb):

    def emit_update(event_name: str, payload: dict):
        if callable(getattr(dispatcher, "notify", None)):
            try:
                dispatcher.notify(event_name, payload)
            except Exception:
                pass
        if callable(getattr(dispatcher, "on_event", None)):
            try:
                dispatcher.on_event(event_name, payload)
            except Exception:
                pass

    async def level_up_character(
        character_id: str,
        new_abilities: Optional[List[str]] = None,
        stat_increases: Optional[Dict[str, int]] = None
    ) -> Dict[str, Any]:
        """
        Advances a character's level by 1. Automatically enforces genre-safe leveling:
        updates canonical root if in the native realm, or updates the variant branch if in a crossover.
        """
        campaign_genre = get_current_genre_cb() if callable(get_current_genre_cb) else "heroic_fantasy"
        formatted_traits = [{"name": a, "description": "Granted on level up."} for a in (new_abilities or [])]

        res = vault_service.level_up_character(
            char_id=character_id,
            current_campaign_genre=campaign_genre,
            level_increment=1,
            stat_boosts=stat_increases,
            new_traits=formatted_traits
        )
        if res.get("status") == "success":
            emit_update("vault_character_updated", res)
        return res

    async def inspect_vault_roster() -> Dict[str, Any]:
        """Lists all persistent heroes stored across the Character Vault, showing level and lineage."""
        characters = vault_service.list_characters()
        return {
            "status": "success",
            "count": len(characters),
            "characters": characters
        }

    dispatcher.register_tool(
        name="level_up_character",
        description="Levels up a character sheet, updating their permanent vault entry or crossover branch.",
        parameters={
            "type": "object",
            "properties": {
                "character_id": {"type": "string", "description": "Vault ID of character."},
                "new_abilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Names of newly unlocked traits or feats."
                },
                "stat_increases": {
                    "type": "object",
                    "description": "Key-value deltas for attributes (e.g. {'Agility': 1})."
                }
            },
            "required": ["character_id"]
        },
        handler=level_up_character
    )

    dispatcher.register_tool(
        name="inspect_vault_roster",
        description="Inspects the Character Vault to list heroes, levels, and crossover branches.",
        parameters={"type": "object", "properties": {}},
        handler=inspect_vault_roster
    )

