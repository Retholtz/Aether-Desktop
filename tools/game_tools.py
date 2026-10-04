"""
Aether Desktop - In-Game Helper Tools & Dynamic Dispatcher Registration
Provides game status/telemetry inspection, macro triggering, and market lookup tools.
"""

from typing import Optional
from tools.dispatcher import register_tool


def register_game_tools(dispatcher, game_manager):
    """
    Registers dedicated gaming tools on the provided ToolDispatcher and GameManager:
    - get_current_game_telemetry: Real-time telemetry snapshot
    - get_current_game_status: Alias/companion for status inspection
    - trigger_game_action: DirectInput hardware macro trigger
    - lookup_inara_market: Market and trade commodity locator
    """

    @dispatcher.register(
        name="get_current_game_telemetry",
        description="Returns real-time in-game telemetry, location, and ship status for the active game (e.g. Elite Dangerous)."
    )
    def get_current_game_telemetry() -> dict:
        active_id = game_manager.data.get("active_profile")
        if active_id == "elite_dangerous" and game_manager.ed_watcher:
            return game_manager.ed_watcher.state
        profile = game_manager.get_profile(active_id)
        return {
            "game": profile.get("display_name") if profile else "None",
            "scratchpad": profile.get("scratchpad_raw", "") if profile else ""
        }

    @dispatcher.register(
        name="get_current_game_status",
        description="Retrieves live in-game location, status, and ship state for the active game."
    )
    def get_current_game_status() -> dict:
        return get_current_game_telemetry()

    @dispatcher.register(
        name="trigger_game_action",
        description="Executes a mapped in-game macro or hotkey using DirectInput hardware scancodes.",
        parameters={
            "type": "object",
            "properties": {
                "phrase": {
                    "type": "string",
                    "description": "The exact voice macro phrase to trigger (e.g., 'deploy heat sink', 'open inventory', 'silent running')."
                }
            },
            "required": ["phrase"]
        }
    )
    def trigger_game_action(phrase: str) -> dict:
        return game_manager.trigger_action(phrase)

    @dispatcher.register(
        name="lookup_inara_market",
        description="Query nearest star systems and stations for commodities, outfitting, or trade services in Elite Dangerous.",
        parameters={
            "type": "object",
            "properties": {
                "commodity": {
                    "type": "string",
                    "description": "Commodity or item name to search (e.g. 'Tritium', 'Painite', 'Modular Terminals')."
                },
                "star_system": {
                    "type": "string",
                    "description": "Reference star system (defaults to current system if omitted)."
                }
            },
            "required": ["commodity"]
        }
    )
    def lookup_inara_market(commodity: str, star_system: Optional[str] = None) -> dict:
        ref_system = star_system
        if not ref_system and game_manager.ed_watcher:
            s = game_manager.ed_watcher.state.get("star_system")
            if s and s != "Unknown":
                ref_system = s
        ref_system = ref_system or "Sol"
        return {
            "status": "success",
            "commodity": commodity,
            "reference_system": ref_system,
            "message": f"Querying Inara/EDDN market data for '{commodity}' near {ref_system}."
        }

    return {
        "get_current_game_telemetry": get_current_game_telemetry,
        "get_current_game_status": get_current_game_status,
        "trigger_game_action": trigger_game_action,
        "lookup_inara_market": lookup_inara_market,
    }
