"""
Aether Desktop - In-Game Helper Tools & Dynamic Dispatcher Registration
Provides game status/telemetry inspection, macro triggering, market lookup,
co-pilot observations, scratchpad management, and visual map navigation/waypoint setting.
"""

import time
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
        description="Returns real-time in-game telemetry, location, active profile, and Game Mode focus status for the active game."
    )
    def get_current_game_telemetry() -> dict:
        active_id = game_manager.data.get("active_profile") or ""
        is_active = game_manager.is_game_mode_active()
        enabled = game_manager.game_mode_enabled
        is_running = game_manager.is_game_running(active_id) if active_id else False
        copilot_entries = game_manager.get_copilot_log(active_id) if active_id else []
        recent_obs = copilot_entries[-5:] if copilot_entries else []

        if active_id == "elite_dangerous" and game_manager.ed_watcher:
            res = dict(game_manager.ed_watcher.state)
            res["active_profile"] = active_id
            res["game_mode_enabled"] = enabled
            res["is_game_mode_active"] = is_active
            res["is_game_running"] = is_running
            prof = game_manager.get_profile(active_id) if active_id else None
            res["scratchpad"] = prof.get("scratchpad_raw", "") if prof else ""
            res["recent_copilot_observations"] = recent_obs
            return res
        profile = game_manager.get_profile(active_id) if active_id else None
        return {
            "game": profile.get("display_name") if profile else "None",
            "active_profile": active_id,
            "game_mode_enabled": enabled,
            "is_game_mode_active": is_active,
            "is_game_running": is_running,
            "scratchpad": profile.get("scratchpad_raw", "") if profile else "",
            "recent_copilot_observations": recent_obs
        }

    @dispatcher.register(
        name="get_current_game_status",
        description="Retrieves live in-game location, status, active profile, and whether Game Mode is currently engaged."
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
        name="toggle_game_mode",
        description="Enables or disables Aether's dedicated in-game focus mode. In game mode, Aether strictly locks into the active game universe and mechanics. When disabled, Aether can freely discuss real-world topics while in-game macros remain standing by.",
        parameters={
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "True to enable Game Mode (strict game focus), False to disable (open conversation)."
                }
            },
            "required": ["enabled"]
        }
    )
    def toggle_game_mode(enabled: bool) -> dict:
        res = game_manager.set_game_mode(enabled, user_explicit=True)
        if engine and hasattr(engine, "_reset_context_flag"):
            engine._reset_context_flag = True
        return res

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
        name="get_game_scratchpad",
        description="Reads the active game profile's scratchpad notes, active quest lists, crafting materials, and personal game notes.",
        parameters={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["all", "active_quests", "crafting_materials", "general_notes", "raw"],
                    "description": "Optional category of scratchpad notes to retrieve (default: 'all')."
                }
            }
        }
    )
    def get_game_scratchpad(category: str = "all") -> dict:
        active_id = game_manager.data.get("active_profile")
        if not active_id:
            return {"success": False, "error": "No active game profile selected."}
        profile = game_manager.get_profile(active_id)
        if not profile:
            return {"success": False, "error": "Profile not found."}

        raw = profile.get("scratchpad_raw", "")
        structured = profile.get("scratchpad", {})
        cat = (category or "all").lower().strip()
        if cat == "raw":
            return {"game": profile.get("display_name", active_id), "scratchpad_raw": raw}
        elif cat in structured:
            return {"game": profile.get("display_name", active_id), category: structured[cat]}
        return {
            "game": profile.get("display_name", active_id),
            "scratchpad_raw": raw,
            "structured_notes": structured
        }

    @dispatcher.register(
        name="search_copilot_log",
        description="Searches historical co-pilot observations, landmarks, rumors, and tactical milestones logged for the active game.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keyword or text to filter observations (optional)."
                },
                "category": {
                    "type": "string",
                    "enum": ["all", "landmark", "mission", "clue", "discovery", "intel", "warning"],
                    "description": "Optional category filter (e.g. landmark, clue, mission, intel, warning)."
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of recent matching entries to return (default: 10)."
                }
            }
        }
    )
    def search_copilot_log(query: Optional[str] = None, category: str = "all", limit: int = 10) -> dict:
        active_id = game_manager.data.get("active_profile")
        if not active_id:
            return {"success": False, "error": "No active game profile selected."}
        entries = game_manager.get_copilot_log(active_id)
        if not entries:
            return {"game": active_id, "entries": [], "count": 0, "message": "No co-pilot observations recorded yet."}

        filtered = entries
        cat = (category or "all").lower().strip()
        if cat != "all":
            filtered = [e for e in filtered if e.get("category", "").lower() == cat]

        if query:
            q = query.lower().strip()
            filtered = [
                e for e in filtered
                if q in str(e.get("summary", "")).lower()
                or q in str(e.get("location", "")).lower()
                or q in str(e.get("details", "")).lower()
            ]

        capped = filtered[-max(1, int(limit)):] if limit else filtered
        return {
            "game": active_id,
            "entries": capped,
            "total_matches": len(filtered),
            "returned_count": len(capped)
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
        active_id = ""
        target_process = ""
        map_controls = None
        if game_manager:
            active_id = game_manager.data.get("active_profile", "")
            prof = game_manager.get_profile(active_id) if active_id else None
            if prof:
                target_process = prof.get("process_name") or prof.get("display_name") or ""
                map_controls = prof.get("map_controls")
        nav = GameNavigator(client=client, model_endpoint=model_endpoint, target_process=target_process, map_controls=map_controls)
        if active_id and game_manager:
            nav_mode = game_manager.get_navigation_mode(active_id)
            if nav_mode == "co_pilot_spotter":
                return nav.spot_and_guide_map_location(landmark_description=landmark)
            elif nav_mode == "search_bar":
                return nav.search_and_route_destination(destination_name=landmark)
            elif nav_mode == "controller":
                return nav.pan_and_place_marker(landmark, general_direction, use_gamepad=True)
            elif nav_mode == "mouse_keyboard":
                return nav.pan_and_place_marker(landmark, general_direction, use_gamepad=False)
        return nav.pan_and_place_marker(landmark, general_direction)

    @dispatcher.register(
        name="pan_game_map",
        description="Pans the in-game world map viewport toward a cardinal direction (e.g. north, south, east, west) using relative mouse dragging or gamepad stick.",
        parameters={
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["north", "south", "east", "west", "northeast", "northwest", "southeast", "southwest"],
                    "description": "Cardinal direction to pan the map viewport."
                },
                "distance": {
                    "type": "integer",
                    "description": "Distance in pixels or drag magnitude (default: 350)."
                }
            },
            "required": ["direction"]
        }
    )
    def pan_game_map(direction: str, distance: int = 350) -> dict:
        from core.game_nav import GameNavigator
        client = (
            getattr(engine, "genai_client", None)
            or getattr(engine, "client", None)
            or getattr(dispatcher, "genai_client", None)
        )
        model_endpoint = "gemini-2.5-flash"
        target_process = ""
        map_controls = None
        if game_manager:
            active_id = game_manager.data.get("active_profile", "")
            prof = game_manager.get_profile(active_id) if active_id else None
            if prof:
                target_process = prof.get("process_name") or prof.get("display_name") or ""
                map_controls = prof.get("map_controls")
        nav = GameNavigator(client=client, model_endpoint=model_endpoint, target_process=target_process, map_controls=map_controls)
        nav.pan_map(direction=direction, distance=distance)
        return {"status": "success", "direction": direction, "distance": distance}

    @dispatcher.register(
        name="place_map_marker",
        description="Places a destination waypoint marker on the in-game world map at specific screen coordinates or at the current cursor position.",
        parameters={
            "type": "object",
            "properties": {
                "x": {
                    "type": "integer",
                    "description": "Optional X screen coordinate (in pixels). If omitted, clicks at current cursor position."
                },
                "y": {
                    "type": "integer",
                    "description": "Optional Y screen coordinate (in pixels). If omitted, clicks at current cursor position."
                }
            }
        }
    )
    def place_map_marker(x: Optional[int] = None, y: Optional[int] = None) -> dict:
        from tools.os_controls import move_mouse_absolute, click_mouse_button
        ctrl = {}
        if game_manager:
            active_id = game_manager.data.get("active_profile", "")
            prof = game_manager.get_profile(active_id) if active_id else None
            if prof:
                ctrl = prof.get("map_controls") or {}
        marker_method = ctrl.get("marker_method")
        btn = "left" if marker_method == "left_click" else "right"
        if x is not None and y is not None:
            move_mouse_absolute(int(x), int(y))
            time.sleep(0.1)
        click_mouse_button(btn, hold_duration=0.08)
        return {"status": "success", "placed_at": [x, y] if x is not None and y is not None else "cursor_pos", "button": btn}

    @dispatcher.register(
        name="center_map_on_player",
        description="Snaps or centers the in-game world map back to the player character or origin.",
        parameters={
            "type": "object",
            "properties": {}
        }
    )
    def center_map_on_player() -> dict:
        from tools.os_controls import pulse_key
        ctrl = {}
        if game_manager:
            active_id = game_manager.data.get("active_profile", "")
            prof = game_manager.get_profile(active_id) if active_id else None
            if prof:
                ctrl = prof.get("map_controls") or {}
        center_key = ctrl.get("center_player_key", "r")
        pulse_key(center_key)
        return {"status": "success", "key": center_key, "message": f"Centered map using key '{center_key}'."}

    @dispatcher.register(
        name="spot_and_guide_map_landmark",
        description="Co-Pilot Guided Spotter mode: Observes the in-game world map via computer vision, locates destination bearings or landmarks, and returns spoken guidance for the player without artificial cursor movement.",
        parameters={
            "type": "object",
            "properties": {
                "landmark": {
                    "type": "string",
                    "description": "Name or visual description of the destination (e.g. 'Silver Wolf Mountain', 'Kweiden Outpost')."
                }
            },
            "required": ["landmark"]
        }
    )
    def spot_and_guide_map_landmark(landmark: str) -> dict:
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
        target_process = ""
        map_controls = None
        if game_manager:
            active_id = game_manager.data.get("active_profile", "")
            prof = game_manager.get_profile(active_id) if active_id else None
            if prof:
                target_process = prof.get("process_name") or prof.get("display_name") or ""
                map_controls = prof.get("map_controls")
        nav = UniversalGameNavigator(client=client, model_endpoint=model_endpoint, target_process=target_process, map_controls=map_controls)
        return nav.spot_and_guide_map_location(landmark_description=landmark)

    @dispatcher.register(
        name="search_and_route_destination",
        description="Search Bar Navigation: Focuses in-game navigation search bar (e.g. Elite Dangerous galaxy map), types destination name via DirectInput, and plots route.",
        parameters={
            "type": "object",
            "properties": {
                "destination_name": {
                    "type": "string",
                    "description": "Star system, sector, or destination name to search and plot."
                }
            },
            "required": ["destination_name"]
        }
    )
    def search_and_route_destination(destination_name: str) -> dict:
        from core.game_nav import UniversalGameNavigator
        target_process = ""
        map_controls = None
        if game_manager:
            active_id = game_manager.data.get("active_profile", "")
            prof = game_manager.get_profile(active_id) if active_id else None
            if prof:
                target_process = prof.get("process_name") or prof.get("display_name") or ""
                map_controls = prof.get("map_controls")
        nav = UniversalGameNavigator(target_process=target_process, map_controls=map_controls)
        return nav.search_and_route_destination(destination_name=destination_name)

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
        target_process = ""
        map_controls = None
        nav_mode = "controller"
        if game_manager:
            active_id = game_manager.data.get("active_profile", "")
            prof = game_manager.get_profile(active_id) if active_id else None
            if prof:
                target_process = prof.get("process_name") or prof.get("display_name") or ""
                map_controls = prof.get("map_controls")
            nav_mode = game_manager.get_navigation_mode(active_id)
        nav = UniversalGameNavigator(client=client, model_endpoint=model_endpoint, target_process=target_process, map_controls=map_controls)
        if nav_mode == "co_pilot_spotter":
            return nav.spot_and_guide_map_location(landmark_description=target_description)
        elif nav_mode == "search_bar":
            return nav.search_and_route_destination(destination_name=target_description)
        return nav.run_vision_navigation_loop(target_description=target_description, web_context=web_context)

    return {
        "get_current_game_telemetry": get_current_game_telemetry,
        "get_current_game_status": get_current_game_status,
        "trigger_game_action": trigger_game_action,
        "toggle_game_mode": toggle_game_mode,
        "lookup_inara_market": lookup_inara_market,
        "record_copilot_observation": record_copilot_observation,
        "update_game_scratchpad": update_game_scratchpad,
        "get_game_scratchpad": get_game_scratchpad,
        "search_copilot_log": search_copilot_log,
        "pan_and_mark_map_location": pan_and_mark_map_location,
        "assist_game_navigation": assist_game_navigation,
        "spot_and_guide_map_landmark": spot_and_guide_map_landmark,
        "search_and_route_destination": search_and_route_destination,
        "pan_game_map": pan_game_map,
        "place_map_marker": place_map_marker,
        "center_map_on_player": center_map_on_player,
    }
