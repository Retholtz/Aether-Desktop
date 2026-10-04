"""
Aether Desktop - In-Game Helper Tools & Dynamic Dispatcher Registration
Provides game status/telemetry inspection, macro triggering, market lookup,
co-pilot observations, scratchpad management, and visual map navigation/waypoint setting.
"""

from typing import Optional, Dict, Any
from tools.dispatcher import register_tool


def register_game_tools(dispatcher, game_manager, engine=None):
    """
    Registers dedicated gaming tools on the provided ToolDispatcher, GameManager, and Engine:
    - get_current_game_telemetry: Real-time telemetry snapshot
    - get_current_game_status: Alias/companion for status inspection
    - trigger_game_action: DirectInput hardware macro trigger
    - lookup_inara_market: Market and trade commodity locator
    - record_copilot_observation: Log landmarks, clues, or points of interest
    - update_game_scratchpad: Verbal updates to player personal notes
    - pan_and_mark_map_location: Visual map drag-pan and waypoint placement
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

    @dispatcher.register(
        name="record_copilot_observation",
        description="Records an in-game point of interest, landmark, quest clue, rumor, or tactical note into the Co-Pilot log for future reference and proactive prompting.",
        parameters={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["landmark", "mission", "clue", "discovery", "intel", "warning"],
                    "description": "Category of the observation (e.g. landmark, clue, mission, discovery, intel, warning)."
                },
                "summary": {
                    "type": "string",
                    "description": "Concise summary of the landmark, clue, or observation (e.g. 'Cave behind waterfall may contain treasure chest', 'NPC mentioned smuggler hideout in Asteroid Belt B')."
                },
                "location": {
                    "type": "string",
                    "description": "In-game location, system, or area where observed (optional)."
                },
                "details": {
                    "type": "string",
                    "description": "Additional context or notes (optional)."
                }
            },
            "required": ["category", "summary"]
        }
    )
    def record_copilot_observation(
        category: str,
        summary: str,
        location: Optional[str] = None,
        details: Optional[str] = None
    ) -> dict:
        active_id = game_manager.data.get("active_profile")
        if not active_id:
            return {"success": False, "error": "No active game profile selected."}
        return game_manager.add_copilot_log_entry(
            game_id=active_id,
            summary=summary,
            category=category,
            location=location or "",
            details=details or ""
        )

    @dispatcher.register(
        name="update_game_scratchpad",
        description="Updates the player's personal scratchpad/notes (e.g. when user says 'note down that...', 'add to my scratchpad', or 'clear my scratchpad').",
        parameters={
            "type": "object",
            "properties": {
                "note": {
                    "type": "string",
                    "description": "The note text or checklist item to add or update."
                },
                "action": {
                    "type": "string",
                    "enum": ["append", "replace", "clear"],
                    "description": "Action to perform: 'append' (default, adds a new line), 'replace' (overwrites), or 'clear'."
                }
            },
            "required": ["note"]
        }
    )
    def update_game_scratchpad(note: str, action: str = "append") -> dict:
        active_id = game_manager.data.get("active_profile")
        if not active_id:
            return {"success": False, "error": "No active game profile selected."}
        profile = game_manager.get_profile(active_id)
        if not profile:
            return {"success": False, "error": "Profile not found."}

        act = (action or "append").lower().strip()
        if act == "clear":
            profile["scratchpad_raw"] = ""
        elif act == "replace":
            profile["scratchpad_raw"] = note.strip()
        else:  # append
            current = profile.get("scratchpad_raw", "")
            if current.strip():
                profile["scratchpad_raw"] = current.rstrip() + "\n" + note.strip()
            else:
                profile["scratchpad_raw"] = note.strip()
        game_manager._save_profiles(game_manager.data)
        return {
            "success": True,
            "action": act,
            "scratchpad": profile["scratchpad_raw"]
        }

    @dispatcher.register(
        name="pan_and_mark_map_location",
        description="Opens the in-game world map, pans the canvas if necessary, locates a landmark/boss visually, and sets a destination waypoint.",
        parameters={
            "type": "object",
            "properties": {
                "landmark": {
                    "type": "string",
                    "description": "Name or visual description of the destination (e.g., 'Legendary Bear Mount', 'Kweiden Outpost')."
                },
                "general_direction": {
                    "type": "string",
                    "enum": ["north", "south", "east", "west", "northeast", "northwest", "southeast", "southwest"],
                    "description": "Optional initial direction to pan the map viewport if known."
                }
            },
            "required": ["landmark"]
        }
    )
    def pan_and_mark_map_location(landmark: str, general_direction: Optional[str] = None) -> dict:
        from core.game_nav import GameNavigator
        client = (
            getattr(engine, "genai_client", None)
            or getattr(engine, "client", None)
            or getattr(dispatcher, "genai_client", None)
        )
        model_endpoint = "gemini-2.5-flash"
        if engine:
            if hasattr(engine, "config") and isinstance(engine.config, dict):
                model_endpoint = engine.config.get("primary_model_endpoint", "gemini-2.5-flash")
            elif hasattr(engine, "config_getter") and callable(engine.config_getter):
                cfg = engine.config_getter()
                model_endpoint = cfg.get("primary_model_endpoint", cfg.get("api", {}).get("model_id", "gemini-2.5-flash"))
        nav = GameNavigator(client=client, model_endpoint=model_endpoint)
        return nav.pan_and_place_marker(landmark, general_direction)

    @dispatcher.register(
        name="assist_game_navigation",
        description="Autonomous closed-loop vision navigation. Iteratively reads on-screen HUD prompts, zooms, pans, visually identifies target landmark or quest objective, and verifies waypoint marker placement.",
        parameters={
            "type": "object",
            "properties": {
                "target_description": {
                    "type": "string",
                    "description": "The destination, landmark, quest objective, or point of interest to navigate to."
                },
                "web_context": {
                    "type": "string",
                    "description": "Optional background hints or directions."
                }
            },
            "required": ["target_description"]
        }
    )
    def assist_game_navigation(target_description: str, web_context: str = "") -> dict:
        from core.game_nav import UniversalGameNavigator
        client = (
            getattr(engine, "genai_client", None)
            or getattr(engine, "client", None)
            or getattr(dispatcher, "genai_client", None)
        )
        model_endpoint = "gemini-2.5-flash"
        if engine:
            if hasattr(engine, "config") and isinstance(engine.config, dict):
                model_endpoint = engine.config.get("primary_model_endpoint", "gemini-2.5-flash")
            elif hasattr(engine, "config_getter") and callable(engine.config_getter):
                cfg = engine.config_getter()
                model_endpoint = cfg.get("primary_model_endpoint", cfg.get("api", {}).get("model_id", "gemini-2.5-flash"))
        nav = UniversalGameNavigator(client=client, model_endpoint=model_endpoint)
        return nav.run_vision_navigation_loop(target_description=target_description, web_context=web_context)

    return {
        "get_current_game_telemetry": get_current_game_telemetry,
        "get_current_game_status": get_current_game_status,
        "trigger_game_action": trigger_game_action,
        "lookup_inara_market": lookup_inara_market,
        "record_copilot_observation": record_copilot_observation,
        "update_game_scratchpad": update_game_scratchpad,
        "pan_and_mark_map_location": pan_and_mark_map_location,
        "assist_game_navigation": assist_game_navigation,
    }
