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
    async def get_steam_friends_activity(include_offline: bool = False) -> Dict[str, Any]:
        """Returns the list of Steam friends who are currently in-game, online, or (optionally) offline."""
        if not steam_service.is_configured():
            return {"status": "error", "message": "Steam integration is not configured. Provide an API key and SteamID64."}

        friends = await steam_service.get_friends_activity()
        if not friends:
            return {"status": "success", "message": "No friends are currently active or friend list is private."}

        active_friends = [f for f in friends if f.game_title or f.status != "Offline"]
        in_game = [f for f in active_friends if f.game_title]
        online = [f for f in active_friends if not f.game_title]
        offline = [f for f in friends if f.status == "Offline"]

        payload: Dict[str, Any] = {
            "status": "success",
            "total_friends_tracked": len(friends),
            "in_game": [{"name": f.persona_name, "game": f.game_title, "steamid": f.steamid} for f in in_game],
            "online_other": [{"name": f.persona_name, "status": f.status, "steamid": f.steamid} for f in online]
        }
        if include_offline:
            payload["offline"] = [{"name": f.persona_name, "status": f.status, "steamid": f.steamid} for f in offline]

        return payload

    # 2. Friend library and games query tool
    async def get_steam_friend_games(friend: str) -> Dict[str, Any]:
        """Looks up a specific Steam friend by their display name or SteamID, returning their current status and owned/recently played game library."""
        if not steam_service.is_configured():
            return {"status": "error", "message": "Steam integration is not configured. Provide an API key and SteamID64."}

        friend_obj = await steam_service.resolve_friend(friend)
        if not friend_obj:
            all_friends = await steam_service.get_friends_activity()
            tracked_names = [f.persona_name for f in all_friends]
            return {
                "status": "error",
                "message": f"Could not find a friend matching '{friend}'. Tracked friends: {tracked_names}"
            }

        owned = await steam_service.get_owned_games(friend_obj.steamid)
        recent = await steam_service.get_recently_played_games(friend_obj.steamid)

        return {
            "status": "success",
            "friend_name": friend_obj.persona_name,
            "steamid": friend_obj.steamid,
            "current_status": friend_obj.status,
            "current_game": friend_obj.game_title,
            "recently_played_games": [
                {"name": g["name"], "playtime_2weeks_hours": g.get("playtime_2weeks_hours", 0)}
                for g in recent
            ],
            "total_owned_games": len(owned),
            "owned_games": [
                {"name": g["name"], "appid": g["appid"], "playtime_hours": g.get("playtime_hours", 0)}
                for g in owned[:50]
            ],
            "note": "Friend's game details are private in their Steam privacy settings." if (not owned and not recent) else None
        }

    # 3. User's own owned games library
    async def get_steam_owned_games(filter_name: Optional[str] = None) -> Dict[str, Any]:
        """Lists the games in YOUR OWN Steam library. Use this to check what games you own (not a friend's)."""
        if not steam_service.is_configured():
            return {"status": "error", "message": "Steam integration is not configured. Provide an API key and SteamID64."}

        games = await steam_service.get_owned_games(steam_service.steam_id64)
        if filter_name:
            fn = str(filter_name).lower()
            filtered = [g for g in games if fn in g.get("name", "").lower()]
        else:
            filtered = games

        return {
            "status": "success",
            "total_games": len(games),
            "matching_games": [
                {"name": g["name"], "appid": g["appid"], "playtime_hours": g.get("playtime_hours", 0)}
                for g in filtered[:50]
            ]
        }

    # 4. Achievement tracker tool
    async def get_steam_game_achievements(appid: Optional[int] = None, friend: Optional[str] = None) -> Dict[str, Any]:
        """Returns player achievement completion stats for the current or specified game, for yourself or for a Steam friend."""
        target_steamid = None
        target_name = "You"
        if friend:
            friend_obj = await steam_service.resolve_friend(friend)
            if not friend_obj:
                return {"status": "error", "message": f"Could not find friend '{friend}'."}
            target_steamid = friend_obj.steamid
            target_name = friend_obj.persona_name
            if not appid and friend_obj.game_id:
                try:
                    appid = int(friend_obj.game_id)
                except (ValueError, TypeError):
                    pass

        target_appid = _resolve_active_appid(appid)
        if not target_appid:
            return {"status": "error", "message": "No Steam AppID specified and no active Steam game detected."}

        stats = await steam_service.get_player_achievements(target_appid, steamid=target_steamid)
        if not stats:
            return {
                "status": "partial",
                "appid": target_appid,
                "message": f"Could not retrieve achievements for {target_name} on AppID {target_appid}. (Profile game details may be Private, or this game has no Steam achievements)."
            }

        return {
            "status": "success",
            "target": target_name,
            "appid": target_appid,
            "completion_percentage": f"{stats.completion_percentage}%",
            "unlocked": f"{stats.unlocked_count}/{stats.total_achievements}",
            "recent_unlocked": [a.get("name", a.get("apiname")) for a in stats.recent_unlocked[:3]],
            "next_to_unlock": [a.get("name", a.get("apiname")) for a in stats.locked[:3]]
        }

    # 5. Patch notes & news tool
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
        description="Checks which Steam friends are currently online, in-game, or on your friend list.",
        parameters={
            "type": "object",
            "properties": {
                "include_offline": {"type": "boolean", "description": "Set true to list offline friends as well."}
            }
        },
        handler=get_steam_friends_activity
    )

    _safe_register(
        name="get_steam_friend_games",
        description="Queries what games a specific Steam friend owns, has recently played, or is currently playing.",
        parameters={
            "type": "object",
            "properties": {
                "friend": {"type": "string", "description": "Friend display name or SteamID (e.g. 'styx', 'Rainmaker', 'Traci', '76561198970487331')."}
            },
            "required": ["friend"]
        },
        handler=get_steam_friend_games
    )

    _safe_register(
        name="get_steam_owned_games",
        description="Queries YOUR OWN Steam library to see what games you own. Do NOT use this to check a friend's games.",
        parameters={
            "type": "object",
            "properties": {
                "filter_name": {"type": "string", "description": "Optional search term to filter game titles in your library."}
            }
        },
        handler=get_steam_owned_games
    )

    _safe_register(
        name="get_steam_game_achievements",
        description="Gets achievement progress for the active game or specified Steam AppID, either for yourself or for a specified Steam friend.",
        parameters={
            "type": "object",
            "properties": {
                "appid": {"type": "integer", "description": "Optional Steam AppID. Inferred automatically if omitted."},
                "friend": {"type": "string", "description": "Optional friend name or SteamID to check their achievements instead of yours."}
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
        "get_steam_friend_games": get_steam_friend_games,
        "get_steam_owned_games": get_steam_owned_games,
        "get_steam_game_achievements": get_steam_game_achievements,
        "get_steam_patch_notes": get_steam_patch_notes
    }

