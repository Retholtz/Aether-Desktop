"""
tools/steam_tools.py - Agent Tool Declarations for Steam Web API
Exposes methods for checking friend presence, achievements, and patch updates.
"""
from typing import Dict, Any, Optional
from core.steam_service import SteamService


def register_steam_tools(dispatcher, steam_service: SteamService, game_engine=None):
    """Registers Steam tools onto Aether's ToolDispatcher."""

    def _resolve_active_appid(explicit_appid: Optional[int] = None) -> Optional[int]:
        if explicit_appid:
            try:
                val = int(explicit_appid)
                if val > 0:
                    return val
            except (ValueError, TypeError):
                return explicit_appid
        if game_engine:
            mgr = getattr(game_engine, "manager", game_engine)
            data = getattr(mgr, "data", {})
            active_id = data.get("active_profile") if isinstance(data, dict) else None
            if active_id and hasattr(mgr, "get_profile"):
                prof = mgr.get_profile(active_id) or {}
                val = prof.get("steam_appid")
                if val:
                    try:
                        ival = int(val)
                        return ival if ival > 0 else None
                    except (ValueError, TypeError):
                        return val
        return None

    # 1. Friends presence tool
    async def get_steam_friends_activity() -> Dict[str, Any]:
        """Returns the list of Steam friends who are currently in-game or online."""
        if not steam_service.is_configured():
            return {"status": "error", "message": "Steam integration is not configured. Provide an API key and SteamID64."}

        friends = await steam_service.get_friends_activity()
        if not friends:
            return {"status": "success", "message": "No friends are currently active or friend list is private."}

        active_friends = [f for f in friends if f.game_title or f.status != "Offline"]
        in_game = [f for f in active_friends if f.game_title]
        online = [f for f in active_friends if not f.game_title]

        return {
            "status": "success",
            "total_friends_tracked": len(friends),
            "in_game": [{"name": f.persona_name, "game": f.game_title} for f in in_game],
            "online_other": [{"name": f.persona_name, "status": f.status} for f in online[:10]]
        }

    # 2. Achievement tracker tool
    async def get_steam_game_achievements(appid: Optional[int] = None) -> Dict[str, Any]:
        """Returns player achievement completion stats for the current or specified Steam game."""
        target_appid = _resolve_active_appid(appid)
        if not target_appid:
            return {"status": "error", "message": "No Steam AppID specified and no active Steam game detected."}

        stats = await steam_service.get_player_achievements(target_appid)
        if not stats:
            return {"status": "error", "message": f"Could not retrieve achievements for AppID {target_appid}."}

        return {
            "status": "success",
            "appid": target_appid,
            "completion_percentage": f"{stats.completion_percentage}%",
            "unlocked": f"{stats.unlocked_count}/{stats.total_achievements}",
            "next_to_unlock": [a.get("name", a.get("apiname")) for a in stats.locked[:3]]
        }

    # 3. Patch notes & news tool
    async def get_steam_patch_notes(appid: Optional[int] = None, count: int = 2) -> Dict[str, Any]:
        """Returns recent patch notes and updates for the active or specified game."""
        target_appid = _resolve_active_appid(appid)
        if not target_appid:
            return {"status": "error", "message": "No AppID specified and no active Steam game detected."}

        news = await steam_service.get_recent_news(target_appid, count=count)
        if not news:
            return {"status": "error", "message": f"No recent patch notes found for AppID {target_appid}."}

        return {
            "status": "success",
            "appid": target_appid,
            "updates": news
        }

    def _safe_register(name: str, description: str, parameters: dict, handler):
        try:
            dispatcher.register_tool(
                name=name,
                description=description,
                parameters=parameters,
                handler=handler
            )
        except TypeError:
            decl = {
                "name": name,
                "description": description,
                "parameters": parameters
            }
            dispatcher.register_tool(name, decl, handler)

    # Register handlers and schemas with dispatcher
    _safe_register(
        name="get_steam_friends_activity",
        description="Checks which Steam friends are currently online or playing games.",
        parameters={"type": "object", "properties": {}},
        handler=get_steam_friends_activity
    )

    _safe_register(
        name="get_steam_game_achievements",
        description="Gets achievement progress and remaining targets for the active game or specified Steam AppID.",
        parameters={
            "type": "object",
            "properties": {
                "appid": {"type": "integer", "description": "Optional Steam AppID. Inferred automatically if omitted."}
            }
        },
        handler=get_steam_game_achievements
    )

    _safe_register(
        name="get_steam_patch_notes",
        description="Retrieves the latest update notes or patch changelogs for the active game or specified Steam AppID.",
        parameters={
            "type": "object",
            "properties": {
                "appid": {"type": "integer", "description": "Optional Steam AppID. Inferred automatically if omitted."},
                "count": {"type": "integer", "description": "Number of news items to fetch (default 2)."}
            }
        },
        handler=get_steam_patch_notes
    )

    return {
        "get_steam_friends_activity": get_steam_friends_activity,
        "get_steam_game_achievements": get_steam_game_achievements,
        "get_steam_patch_notes": get_steam_patch_notes
    }

