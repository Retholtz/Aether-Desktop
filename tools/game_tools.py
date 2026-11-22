"""
Aether Desktop - In-Game Helper Tools & Dynamic Dispatcher Registration
Provides game status/telemetry inspection, macro triggering, market lookup,
co-pilot observations, scratchpad management, and visual map navigation/waypoint setting.
"""

import time
from typing import Optional, Dict, Any
from tools.dispatcher import register_tool


def _resolve_tier2_game_model(engine=None) -> str:
    """Resolves the configured Tier 2 Moderate REST model ID from Settings."""
    from core.model_discovery import get_configured_tier2_model
    cfg = None
    if engine:
        if hasattr(engine, "config") and isinstance(engine.config, dict):
            cfg = engine.config
        elif hasattr(engine, "config_getter") and callable(engine.config_getter):
            try:
                cfg = engine.config_getter()
            except Exception:
                cfg = None
    return get_configured_tier2_model(cfg)[0]


def register_game_tools(dispatcher, game_manager, engine=None):
    """
    Registers dedicated gaming tools on the provided ToolDispatcher, GameManager, and Engine:
    - get_current_game_telemetry: Real-time telemetry snapshot
    - get_current_game_status: Alias/companion for status inspection
    - trigger_game_action: DirectInput hardware macro trigger
    - query_game_intel: Generalized in-game market, outfitting, route, and database intel locator
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

        adapter = getattr(game_manager, "active_telemetry_adapter", None) or getattr(game_manager, "ed_watcher", None)
        profile = game_manager.get_profile(active_id) if active_id else None
        if adapter and (adapter.is_active() or (hasattr(adapter, "state") and adapter.state.get("active"))):
            res = dict(adapter.get_state() if hasattr(adapter, "get_state") else adapter.state)
            res["active_profile"] = active_id
            res["game_mode_enabled"] = enabled
            res["is_game_mode_active"] = is_active
            res["is_game_running"] = is_running
            res["scratchpad"] = profile.get("scratchpad_raw", "") if profile else ""
            res["recent_copilot_observations"] = recent_obs
            if hasattr(adapter, "get_status_summary"):
                res["status_summary"] = adapter.get_status_summary()
            return res
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
        active_id = game_manager.data.get("active_profile", "") if game_manager else ""
        if active_id and hasattr(game_manager, "get_input_mode") and game_manager.get_input_mode(active_id) == "spotter_only":
            return {
                "status": "blocked_by_spotter_only",
                "message": "Synthetic key/mouse macros are disabled because this game is in Spotter-Only (Anti-Cheat Safe) mode.",
            }
        client = (
            getattr(engine, "genai_client", None)
            or getattr(engine, "client", None)
            or getattr(dispatcher, "genai_client", None)
        )
        model_endpoint = _resolve_tier2_game_model(engine)
        return game_manager.trigger_action(phrase, vision_client=client, model_endpoint=model_endpoint)

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
        name="query_game_intel",
        description=(
            "Queries live in-game databases and APIs for commodities, market buy/sell stations, prices, "
            "outfitting/gear, station/NPC services, navigation/routes, crafting, or lore near the player's "
            "current telemetry location. Always call this tool directly instead of telling the player to "
            "check an external website."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Item, commodity, module, service, route, station, or intel subject to look up (e.g. 'Liquid Oxygen', 'Tritium', 'Material Trader', 'Frame Shift Drive (SCO)')."
                },
                "category": {
                    "type": "string",
                    "enum": ["market", "outfitting", "services", "navigation", "lore", "crafting"],
                    "description": "Category of intel to look up (default: 'market')."
                },
                "location": {
                    "type": "string",
                    "description": "Reference star system or in-game zone (defaults to player's current telemetry location if omitted)."
                }
            },
            "required": ["query"]
        }
    )
    def query_game_intel(query: str, category: str = "market", location: Optional[str] = None) -> dict:
        active_id = game_manager.data.get("active_profile", "") if game_manager else ""
        profile = game_manager.get_active_profile() if game_manager else {}
        game_title = (profile.get("display_name") if profile else None) or "Active Game"
        cat_norm = str(category or "market").strip().lower()

        # Resolve matching declarative game module (so bundled/user module intel_endpoints & companion_resources always hydrate)
        mod = None
        try:
            from core.game_modules import get_module_for_game
            mod = get_module_for_game(active_id or game_title, profile=profile)
        except Exception:
            mod = None

        # 1. Resolve Live Telemetry Snapshot & Reference Location
        telemetry_state: Dict[str, Any] = {}
        ref_location = location
        if game_manager:
            adapter = getattr(game_manager, "active_telemetry_adapter", None) or getattr(game_manager, "ed_watcher", None)
            if adapter:
                if hasattr(adapter, "get_state"):
                    try:
                        telemetry_state = dict(adapter.get_state() or {})
                    except Exception:
                        telemetry_state = {}
                elif hasattr(adapter, "state") and isinstance(adapter.state, dict):
                    telemetry_state = dict(adapter.state)
                if not ref_location:
                    loc_dict = adapter.get_location() if hasattr(adapter, "get_location") else {}
                    ref_location = loc_dict.get("system") or loc_dict.get("station")
                    if not ref_location:
                        sys_val = telemetry_state.get("star_system")
                        if sys_val and str(sys_val).strip().lower() != "unknown":
                            ref_location = sys_val

        # 2. Tier 2 Trusted Python Game Extension hook (if installed & explicitly trusted by user)
        ext_mod_id = (mod.get("module_id") if isinstance(mod, dict) else "") or active_id
        if ext_mod_id:
            try:
                from core.game_extensions import load_trusted_extension
                ext_script = (mod.get("extension") if isinstance(mod, dict) else None) or "extension.py"
                ext = load_trusted_extension(ext_mod_id, script_name=str(ext_script))
                if ext is not None:
                    ext_res = ext.query_intel(
                        query=query,
                        category=cat_norm,
                        location=ref_location,
                        telemetry=telemetry_state,
                        profile=profile,
                    )
                    if isinstance(ext_res, dict) and ext_res.get("status") == "success":
                        ext_res.setdefault("game", game_title)
                        ext_res.setdefault("category", cat_norm)
                        ext_res.setdefault("query", query)
                        ext_res.setdefault("reference_location", ref_location or "Unknown")
                        return ext_res
            except Exception:
                pass

        # 3. Tier 1 Declarative REST Intel Engine (from profile override or .aethermodule.json `intel_endpoints`)
        intel_endpoints: Dict[str, Any] = {}
        if isinstance(mod, dict) and isinstance(mod.get("intel_endpoints"), dict):
            intel_endpoints.update(mod["intel_endpoints"])
        if isinstance(profile, dict) and isinstance(profile.get("intel_endpoints"), dict):
            intel_endpoints.update(profile["intel_endpoints"])

        ep_spec = intel_endpoints.get(cat_norm)
        if isinstance(ep_spec, dict) and ep_spec.get("url"):
            from core.game_intel import query_declarative_intel
            live_res = query_declarative_intel(
                endpoint_spec=ep_spec,
                query=query,
                location=ref_location or "",
                telemetry_state=telemetry_state,
            )
            if live_res.get("status") == "success" and (
                live_res.get("count", 0) > 0 or not ep_spec.get("fallback_to_web_search", True)
            ):
                return {
                    "status": "success",
                    "game": game_title,
                    "category": cat_norm,
                    "query": query,
                    "canonical_query": live_res.get("canonical_query", query),
                    "reference_location": live_res.get("reference_location") or ref_location or "Unknown",
                    "source": live_res.get("source"),
                    "count": live_res.get("count", 0),
                    "results": live_res.get("results", []),
                    "intel": live_res.get("intel", ""),
                }

        if not ref_location:
            ref_location = "Current Location"

        # 4. Extract Configured Companion Domain (profile -> module fallback)
        companion_resources: Dict[str, Any] = {}
        if isinstance(mod, dict) and isinstance(mod.get("companion_resources"), dict):
            companion_resources.update(mod["companion_resources"])
        if isinstance(profile, dict) and isinstance(profile.get("companion_resources"), dict):
            companion_resources.update(profile["companion_resources"])

        cat_resource = companion_resources.get(cat_norm, {}) if isinstance(companion_resources, dict) else {}
        domain = cat_resource.get("domain") or cat_resource.get("url") if isinstance(cat_resource, dict) else None
        if not domain and profile:
            comp_apps = profile.get("companion_apps")
            if isinstance(comp_apps, dict) and comp_apps:
                first_app = next(iter(comp_apps.values()))
                if isinstance(first_app, dict):
                    domain = first_app.get("url") or first_app.get("domain")
            elif isinstance(comp_apps, list) and comp_apps:
                for entry in comp_apps:
                    if isinstance(entry, dict) and (entry.get("url") or entry.get("domain")):
                        domain = entry.get("url") or entry.get("domain")
                        break

        client = (
            getattr(engine, "genai_client", None)
            or getattr(engine, "client", None)
            or getattr(dispatcher, "genai_client", None)
        )
        if client:
            try:
                from google.genai import types
                model_endpoint = _resolve_tier2_game_model(engine)

                search_scope = f"'{domain}' and {game_title}" if domain else f"'{game_title}'"
                prompt_content = (
                    f"Search {search_scope} for {cat_norm} intel on '{query}' near reference location '{ref_location}'. "
                    f"Return concrete buying/selling stations, vendors, or locations, exact prices or requirements, "
                    f"landing pad or zone constraints, and distance from '{ref_location}' if applicable. "
                    f"Do NOT tell the user to check a website manually; give direct actionable results."
                )

                resp = client.models.generate_content(
                    model=model_endpoint,
                    contents=prompt_content,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        temperature=0.1,
                    ),
                )
                return {
                    "status": "success",
                    "game": game_title,
                    "category": cat_norm,
                    "query": query,
                    "reference_location": ref_location,
                    "intel": (resp.text or "").strip(),
                }
            except Exception as e:
                return {
                    "status": "partial",
                    "game": game_title,
                    "category": cat_norm,
                    "query": query,
                    "reference_location": ref_location,
                    "message": f"Live intel search encountered an error ({e}). Use Google Search grounding for '{query}' near {ref_location} in {game_title}."
                }

        return {
            "status": "info",
            "game": game_title,
            "category": cat_norm,
            "query": query,
            "reference_location": ref_location,
            "message": f"Reference location resolved to '{ref_location}'. Querying {game_title} databases for '{query}'."
        }

    @dispatcher.register(
        name="lookup_inara_market",
        description="Backward-compatibility alias delegating to query_game_intel.",
        parameters={
            "type": "object",
            "properties": {
                "commodity": {"type": "string", "description": "Commodity name to search."},
                "star_system": {"type": "string", "description": "Reference star system."}
            },
            "required": ["commodity"]
        }
    )
    def lookup_inara_market(commodity: str, star_system: Optional[str] = None) -> dict:
        res = query_game_intel(query=commodity, category="market", location=star_system)
        res["commodity"] = commodity
        res["reference_system"] = star_system or res.get("reference_location")
        if res.get("status") == "info":
            res["status"] = "success"
        return res

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

    def _build_game_navigator(nav_cls):
        """Constructs a navigator enriched with merged module+profile map_controls, keybinds, and telemetry."""
        client = (
            getattr(engine, "genai_client", None)
            or getattr(engine, "client", None)
            or getattr(dispatcher, "genai_client", None)
        )
        model_endpoint = _resolve_tier2_game_model(engine)
        active_id = ""
        target_process = ""
        game_name = ""
        merged_map_controls: Dict[str, Any] = {}
        keybinds: Dict[str, Any] = {}
        tel_adapter = None
        if game_manager:
            active_id = game_manager.data.get("active_profile", "")
            prof = game_manager.get_profile(active_id) if active_id else None
            tel_adapter = getattr(game_manager, "active_telemetry_adapter", None) or getattr(game_manager, "ed_watcher", None)
            try:
                from core.game_modules import get_module_for_game
                mod = get_module_for_game(active_id, profile=prof) if active_id else None
                if isinstance(mod, dict) and isinstance(mod.get("map_controls"), dict):
                    merged_map_controls.update(mod["map_controls"])
            except Exception:
                pass
            if prof:
                target_process = prof.get("process_name") or prof.get("display_name") or ""
                game_name = prof.get("display_name") or active_id
                if isinstance(prof.get("map_controls"), dict):
                    merged_map_controls.update(prof["map_controls"])
                if isinstance(prof.get("keybinds"), dict):
                    keybinds = prof["keybinds"]
        return active_id, nav_cls(
            client=client,
            model_endpoint=model_endpoint,
            target_process=target_process,
            map_controls=merged_map_controls or None,
            game_name=game_name,
            keybinds=keybinds,
            telemetry_adapter=tel_adapter,
        )

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
        active_id, nav = _build_game_navigator(GameNavigator)
        if active_id and game_manager:
            if hasattr(game_manager, "get_input_mode") and game_manager.get_input_mode(active_id) == "spotter_only":
                return nav.spot_and_guide_map_location(landmark_description=landmark)
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
        active_id = game_manager.data.get("active_profile", "") if game_manager else ""
        if active_id and hasattr(game_manager, "get_input_mode") and game_manager.get_input_mode(active_id) == "spotter_only":
            return {
                "status": "blocked_by_spotter_only",
                "message": "Map panning inputs are disabled in Spotter-Only (Anti-Cheat Safe) mode.",
            }
        from core.game_nav import GameNavigator
        client = (
            getattr(engine, "genai_client", None)
            or getattr(engine, "client", None)
            or getattr(dispatcher, "genai_client", None)
        )
        model_endpoint = _resolve_tier2_game_model(engine)
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
        active_id = game_manager.data.get("active_profile", "") if game_manager else ""
        if active_id and hasattr(game_manager, "get_input_mode") and game_manager.get_input_mode(active_id) == "spotter_only":
            return {
                "status": "blocked_by_spotter_only",
                "message": "Synthetic mouse clicks are disabled in Spotter-Only (Anti-Cheat Safe) mode.",
            }
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
        active_id = game_manager.data.get("active_profile", "") if game_manager else ""
        if active_id and hasattr(game_manager, "get_input_mode") and game_manager.get_input_mode(active_id) == "spotter_only":
            return {
                "status": "blocked_by_spotter_only",
                "message": "Synthetic keystrokes are disabled in Spotter-Only (Anti-Cheat Safe) mode.",
            }
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
        model_endpoint = _resolve_tier2_game_model(engine)
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
        description="Search Bar Navigation (Keyboard & Mouse): Opens the in-game navigation map, pastes the destination into the search bar, and plots the course.",
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
        active_id = game_manager.data.get("active_profile", "") if game_manager else ""
        if active_id and hasattr(game_manager, "get_input_mode") and game_manager.get_input_mode(active_id) == "spotter_only":
            return {
                "status": "blocked_by_spotter_only",
                "message": "Synthetic search-bar typing is disabled in Spotter-Only (Anti-Cheat Safe) mode.",
            }
        from core.game_nav import GameNavigator
        _, nav = _build_game_navigator(GameNavigator)
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
        active_id, nav = _build_game_navigator(UniversalGameNavigator)
        nav_mode = game_manager.get_navigation_mode(active_id) if (game_manager and active_id) else "controller"
        if game_manager and active_id and hasattr(game_manager, "get_input_mode") and game_manager.get_input_mode(active_id) == "spotter_only":
            return nav.spot_and_guide_map_location(landmark_description=target_description)
        if nav_mode == "co_pilot_spotter":
            return nav.spot_and_guide_map_location(landmark_description=target_description)
        elif nav_mode == "search_bar":
            return nav.search_and_route_destination(destination_name=target_description)
        return nav.run_vision_navigation_loop(target_description=target_description, web_context=web_context)

    @dispatcher.register(
        name="launch_game_companion_app",
        description="Launches a 3rd-party companion/helper program linked to the active game profile (e.g. EDMC, EDDiscovery, SRV Survey, EDCoPilot, Little Navmap). Safe to use in both Autonomous and Spotter-Only modes.",
        parameters={
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "Display name, ID, or executable name of the linked 3rd-party companion program to start."
                }
            },
            "required": ["app_name"]
        }
    )
    def launch_game_companion_app(app_name: str) -> dict:
        if not game_manager or not hasattr(game_manager, "launch_companion_app"):
            return {"success": False, "error": "Game manager unavailable."}
        return game_manager.launch_companion_app(app_name)

    @dispatcher.register(
        name="calibrate_hud_bars",
        description="Captures the current live gameplay screen immediately (without Alt+Tabbing) and auto-calibrates HUD vital/resource bar positions (health, stamina, mana, shield, etc.) for the active game's exact resolution and aspect ratio. Call this whenever the player says 'calibrate my HUD', 'calibrate HUD bars', or 'detect my health bar'. 100% read-only and safe in both Autonomous and Spotter-Only modes.",
        parameters={
            "type": "object",
            "properties": {},
            "required": []
        }
    )
    def calibrate_hud_bars() -> dict:
        if not game_manager:
            return {"success": False, "error": "Game manager unavailable."}
        active_id = game_manager.data.get("active_profile", "")
        if not active_id:
            return {"success": False, "error": "No active game profile selected."}
        client = (
            getattr(engine, "genai_client", None)
            or getattr(engine, "client", None)
            or getattr(dispatcher, "genai_client", None)
        )
        if not client:
            return {"success": False, "error": "Gemini vision client unavailable."}
        from core.telemetry.providers.vision_hud_provider import calibrate_and_persist_game_hud
        model_endpoint = _resolve_tier2_game_model(engine)
        tel_svc = getattr(engine, "telemetry_service", None) if engine else None
        return calibrate_and_persist_game_hud(
            game_id=active_id,
            game_manager=game_manager,
            genai_client=client,
            model_endpoint=model_endpoint,
            telemetry_service=tel_svc,
            focus_game=False,
            countdown_sec=0.0,
        )

    @dispatcher.register(
        name="click_game_ui_element",
        description=(
            "Captures the active game screen, visually locates a specific UI button, menu item, or label "
            "(e.g. 'Auto Launch', 'Starport Services', 'Confirm'), and clicks it with the mouse. "
            "Returns 'element_not_found' if the button is not visible on screen."
        ),
        parameters={
            "type": "object",
            "properties": {
                "target_description": {
                    "type": "string",
                    "description": "Clear description of the on-screen UI button or text to click (e.g. 'Auto Launch button')."
                },
                "button": {
                    "type": "string",
                    "enum": ["left", "right"],
                    "description": "Mouse button to click (default: 'left')."
                },
                "hold_ms": {
                    "type": "integer",
                    "description": "Hold duration in milliseconds (default: 80)."
                }
            },
            "required": ["target_description"]
        }
    )
    def click_game_ui_element(target_description: str, button: str = "left", hold_ms: int = 80) -> dict:
        active_id = game_manager.data.get("active_profile", "") if game_manager else ""
        if active_id and hasattr(game_manager, "get_input_mode") and game_manager.get_input_mode(active_id) == "spotter_only":
            return {
                "status": "blocked_by_spotter_only",
                "message": "Synthetic mouse clicks are disabled in Spotter-Only (Anti-Cheat Safe) mode.",
            }
        client = (
            getattr(engine, "genai_client", None)
            or getattr(engine, "client", None)
            or getattr(dispatcher, "genai_client", None)
        )
        if not client:
            return {"status": "error", "message": "Vision client unavailable."}
        from core.game_nav import UniversalGameNavigator
        from tools.os_controls import focus_window, move_mouse_absolute, click_mouse_button
        _, nav = _build_game_navigator(UniversalGameNavigator)
        if nav.target_process:
            try:
                focus_window(nav.target_process)
                time.sleep(0.12)
            except Exception:
                pass
        pt = nav.locate_ui_element_point(target_description)
        if not pt:
            return {
                "status": "element_not_found",
                "target": target_description,
                "message": f"Could not find '{target_description}' on the active game screen.",
            }
        move_mouse_absolute(pt[0], pt[1])
        time.sleep(0.1)
        click_mouse_button(str(button or "left").lower(), hold_duration=max(0.04, float(hold_ms or 80) / 1000.0))
        return {
            "status": "executed",
            "target": target_description,
            "clicked_at": [pt[0], pt[1]],
            "button": str(button or "left").lower(),
            "message": f"Located and clicked '{target_description}' at ({pt[0]}, {pt[1]}).",
        }

    @dispatcher.register(
        name="save_game_macro",
        description=(
            "Creates or updates a user-generated multi-step visual/keystroke macro for the active game. "
            "Supported step actions: 'press' (key, modifiers, hold_ms, repeat, interval_ms), "
            "'trigger_bind' (phrase, repeat, interval_ms), 'vision_click' (target, button, hold_ms, abort_if_not_found), "
            "'click' (button, x, y), 'type' (text), 'paste' (text), 'wait' (delay_ms)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Voice trigger phrase for the macro (e.g. 'take us out', 'full power to engines')."
                },
                "steps": {
                    "type": "array",
                    "description": "Ordered list of step objects.",
                    "items": {"type": "object"}
                },
                "description": {
                    "type": "string",
                    "description": "Short description of what the macro does."
                },
                "condition": {
                    "type": "string",
                    "description": "Optional telemetry precondition (e.g. 'state.docked', '!state.landing_gear_down')."
                },
                "ack": {
                    "type": "string",
                    "description": "Optional spoken confirmation text."
                },
                "aliases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional alternative voice phrases."
                }
            },
            "required": ["name", "steps"]
        }
    )
    def save_game_macro(
        name: str,
        steps: list,
        description: str = "",
        condition: str = "",
        ack: str = "",
        aliases: Optional[list] = None,
    ) -> dict:
        if not game_manager:
            return {"status": "error", "message": "Game manager unavailable."}
        active_id = game_manager.data.get("active_profile", "")
        profile = game_manager.get_profile(active_id) if active_id else None
        if not profile:
            return {"status": "error", "message": "No active game profile selected."}
        clean_name = str(name or "").lower().strip()
        if not clean_name:
            return {"status": "error", "message": "Macro name cannot be empty."}
        from core.game_macro_runner import normalize_and_validate_macro_steps
        norm_steps, val_errors = normalize_and_validate_macro_steps(steps, profile=profile)
        if val_errors:
            return {
                "status": "error",
                "message": "Macro validation failed: " + " | ".join(val_errors),
                "errors": val_errors,
            }

        entry: Dict[str, Any] = {
            "description": str(description or clean_name.title()).strip(),
            "steps": norm_steps,
            "source": "user_macro",
        }
        if condition:
            entry["condition"] = str(condition).strip()
        if ack:
            entry["ack"] = str(ack).strip()
        if isinstance(aliases, list) and aliases:
            entry["aliases"] = [str(a).lower().strip() for a in aliases if str(a).strip()]

        profile.setdefault("sequences", {})[clean_name] = entry
        game_manager._save_profiles(game_manager.data, target_game_id=active_id)
        if engine is not None:
            if hasattr(engine, "_reset_context_flag"):
                engine._reset_context_flag = True
            if hasattr(engine, "notify") and callable(engine.notify):
                try:
                    engine.notify("game_macros_updated", {"game_id": active_id, "macro_name": clean_name})
                except Exception:
                    pass
        return {
            "status": "success",
            "game": profile.get("display_name", active_id),
            "macro_name": clean_name,
            "step_count": len(steps),
            "message": f"Saved custom macro '{clean_name}' ({len(steps)} steps) for {profile.get('display_name', active_id)}.",
        }

    @dispatcher.register(
        name="list_game_macros",
        description="Lists all user-created and module-provided custom macros (sequences) for the active game.",
        parameters={"type": "object", "properties": {}}
    )
    def list_game_macros() -> dict:
        if not game_manager:
            return {"status": "error", "message": "Game manager unavailable."}
        active_id = game_manager.data.get("active_profile", "")
        profile = game_manager.get_profile(active_id) if active_id else None
        if not profile:
            return {"status": "error", "message": "No active game profile selected."}
        merged: Dict[str, Any] = {}
        try:
            from core.game_modules import get_module_for_game
            mod = get_module_for_game(active_id, profile=profile)
            if isinstance(mod, dict) and isinstance(mod.get("sequences"), dict):
                merged.update(mod["sequences"])
        except Exception:
            pass
        if isinstance(profile.get("sequences"), dict):
            merged.update(profile["sequences"])
        return {
            "status": "success",
            "game": profile.get("display_name", active_id),
            "count": len(merged),
            "macros": merged,
        }

    @dispatcher.register(
        name="delete_game_macro",
        description="Deletes a user-created custom macro (sequence) from the active game profile.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the custom macro to delete."}
            },
            "required": ["name"]
        }
    )
    def delete_game_macro(name: str) -> dict:
        if not game_manager:
            return {"status": "error", "message": "Game manager unavailable."}
        active_id = game_manager.data.get("active_profile", "")
        profile = game_manager.get_profile(active_id) if active_id else None
        if not profile:
            return {"status": "error", "message": "No active game profile selected."}
        clean_name = str(name or "").lower().strip()
        seqs = profile.get("sequences") or {}
        if clean_name in seqs:
            del seqs[clean_name]
            game_manager._save_profiles(game_manager.data, target_game_id=active_id)
            if engine is not None:
                if hasattr(engine, "_reset_context_flag"):
                    engine._reset_context_flag = True
                if hasattr(engine, "notify") and callable(engine.notify):
                    try:
                        engine.notify("game_macros_updated", {"game_id": active_id, "deleted": clean_name})
                    except Exception:
                        pass
            return {"status": "success", "deleted": clean_name, "message": f"Deleted custom macro '{clean_name}'."}
        return {"status": "not_found", "message": f"No custom macro named '{clean_name}' found in profile."}

    return {
        "get_current_game_telemetry": get_current_game_telemetry,
        "get_current_game_status": get_current_game_status,
        "trigger_game_action": trigger_game_action,
        "toggle_game_mode": toggle_game_mode,
        "query_game_intel": query_game_intel,
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
        "launch_game_companion_app": launch_game_companion_app,
        "calibrate_hud_bars": calibrate_hud_bars,
        "click_game_ui_element": click_game_ui_element,
        "save_game_macro": save_game_macro,
        "list_game_macros": list_game_macros,
        "delete_game_macro": delete_game_macro,
    }
