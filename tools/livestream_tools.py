"""
tools/livestream_tools.py - Agent Tools for Live Streaming Presence & Watchlist Management
Exposes tools for querying live streams and managing creator watchlists across Twitch and YouTube.
"""
from typing import Dict, Any, Optional, List
from core.livestream_service import LiveStreamService
from core.config import load_settings, save_settings


def register_livestream_tools(
    dispatcher,
    service: LiveStreamService,
    engine=None,
    watched_twitch: Optional[List[str]] = None,
    watched_youtube: Optional[List[Dict[str, str]]] = None
):
    default_twitch = list(watched_twitch) if watched_twitch is not None else []
    default_youtube = list(watched_youtube) if watched_youtube is not None else []

    def _get_current_settings() -> Dict[str, Any]:
        if engine and hasattr(engine, "settings") and isinstance(engine.settings, dict):
            return engine.settings
        return load_settings()

    def _persist_settings(settings_dict: Dict[str, Any]):
        save_settings(settings_dict)
        if engine and hasattr(engine, "notify"):
            try:
                engine.notify("streaming_config_updated", settings_dict.get("integrations", {}).get("streaming", {}))
            except Exception:
                pass

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

    # 1. Query live status
    async def get_live_stream_status(
        platform: Optional[str] = None,
        channel_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Checks if specified channels or watchlist creators are currently streaming live on Twitch or YouTube.
        """
        results = []
        platform_norm = (platform or "").lower().strip()

        # 1. Evaluate Twitch
        if platform_norm in ("", "twitch") and service.has_twitch():
            channels_to_query = [channel_name] if (channel_name and platform_norm == "twitch") else default_twitch
            if channels_to_query:
                twitch_res = await service.get_twitch_streams(channels_to_query)
                results.extend([t.to_dict() for t in twitch_res])

        # 2. Evaluate YouTube
        if platform_norm in ("", "youtube") and service.has_youtube():
            if channel_name and platform_norm == "youtube":
                yt_res = await service.check_youtube_channel_live(channel_id=channel_name)
                results.append(yt_res.to_dict())
            else:
                for entry in default_youtube:
                    c_id = entry.get("channel_id")
                    c_name = entry.get("name", "")
                    if c_id:
                        yt_res = await service.check_youtube_channel_live(channel_id=c_id, channel_name=c_name)
                        results.append(yt_res.to_dict())

        live_now = [r for r in results if r.get("is_live")]

        return {
            "status": "success",
            "total_queried": len(results),
            "live_count": len(live_now),
            "live_streams": live_now,
            "offline_streams": [r for r in results if not r.get("is_live")]
        }

    # 2. Add to watchlist
    async def add_to_stream_watchlist(
        platform: str,
        channel_name: str,
        display_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Adds a creator or broadcast channel to the user's Twitch or YouTube live stream watchlist.
        """
        plat = (platform or "").lower().strip()
        ch = (channel_name or "").strip()
        if not ch:
            return {"status": "error", "message": "channel_name cannot be empty."}
        if plat not in ("twitch", "youtube"):
            return {"status": "error", "message": "platform must be either 'twitch' or 'youtube'."}

        settings = _get_current_settings()
        stream_cfg = settings.setdefault("integrations", {}).setdefault("streaming", {})

        if plat == "twitch":
            tw_list = stream_cfg.setdefault("twitch", {}).setdefault("channels", [])
            clean_ch = ch.lower()
            if clean_ch not in [c.lower() for c in tw_list]:
                tw_list.append(clean_ch)
                if clean_ch not in default_twitch:
                    default_twitch.append(clean_ch)
                _persist_settings(settings)
                return {
                    "status": "success",
                    "message": f"Added Twitch channel '{clean_ch}' to live watchlist.",
                    "platform": "twitch",
                    "channel": clean_ch,
                    "watchlist": list(tw_list)
                }
            return {
                "status": "info",
                "message": f"Twitch channel '{clean_ch}' is already on your watchlist.",
                "watchlist": list(tw_list)
            }
        else:
            yt_list = stream_cfg.setdefault("youtube", {}).setdefault("channels", [])
            exists = any(
                entry.get("channel_id") == ch or (entry.get("name") and entry.get("name", "").lower() == ch.lower())
                for entry in yt_list
            )
            if not exists:
                new_entry = {"name": display_name or ch, "channel_id": ch}
                yt_list.append(new_entry)
                default_youtube.append(new_entry)
                _persist_settings(settings)
                return {
                    "status": "success",
                    "message": f"Added YouTube channel '{new_entry['name']}' ({ch}) to live watchlist.",
                    "platform": "youtube",
                    "channel": new_entry,
                    "watchlist": list(yt_list)
                }
            return {
                "status": "info",
                "message": f"YouTube channel '{ch}' is already on your watchlist.",
                "watchlist": list(yt_list)
            }

    # 3. Remove from watchlist
    async def remove_from_stream_watchlist(
        platform: str,
        channel_name: str
    ) -> Dict[str, Any]:
        """
        Removes a creator or broadcast channel from the user's Twitch or YouTube live stream watchlist.
        """
        plat = (platform or "").lower().strip()
        ch = (channel_name or "").strip()
        if not ch:
            return {"status": "error", "message": "channel_name cannot be empty."}
        if plat not in ("twitch", "youtube"):
            return {"status": "error", "message": "platform must be either 'twitch' or 'youtube'."}

        settings = _get_current_settings()
        stream_cfg = settings.setdefault("integrations", {}).setdefault("streaming", {})

        if plat == "twitch":
            tw_list = stream_cfg.setdefault("twitch", {}).setdefault("channels", [])
            initial_len = len(tw_list)
            tw_list[:] = [c for c in tw_list if c.lower() != ch.lower()]
            default_twitch[:] = [c for c in default_twitch if c.lower() != ch.lower()]
            if len(tw_list) < initial_len:
                _persist_settings(settings)
                return {
                    "status": "success",
                    "message": f"Removed Twitch channel '{ch}' from live watchlist.",
                    "watchlist": list(tw_list)
                }
            return {
                "status": "not_found",
                "message": f"Twitch channel '{ch}' was not found on your watchlist.",
                "watchlist": list(tw_list)
            }
        else:
            yt_list = stream_cfg.setdefault("youtube", {}).setdefault("channels", [])
            initial_len = len(yt_list)
            yt_list[:] = [
                entry for entry in yt_list
                if entry.get("channel_id") != ch and entry.get("name", "").lower() != ch.lower()
            ]
            default_youtube[:] = [
                entry for entry in default_youtube
                if entry.get("channel_id") != ch and entry.get("name", "").lower() != ch.lower()
            ]
            if len(yt_list) < initial_len:
                _persist_settings(settings)
                return {
                    "status": "success",
                    "message": f"Removed YouTube channel '{ch}' from live watchlist.",
                    "watchlist": list(yt_list)
                }
            return {
                "status": "not_found",
                "message": f"YouTube channel '{ch}' was not found on your watchlist.",
                "watchlist": list(yt_list)
            }

    # 4. List watchlist
    async def list_stream_watchlist(
        platform: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Lists all creators and channels currently monitored on the user's Twitch and YouTube live stream watchlist.
        """
        plat = (platform or "").lower().strip()
        settings = _get_current_settings()
        stream_cfg = settings.get("integrations", {}).get("streaming", {})

        tw = stream_cfg.get("twitch", {}).get("channels", default_twitch)
        yt = stream_cfg.get("youtube", {}).get("channels", default_youtube)

        res = {}
        if plat in ("", "twitch"):
            res["twitch"] = list(tw)
        if plat in ("", "youtube"):
            res["youtube"] = list(yt)

        return {
            "status": "success",
            "platforms": res,
            "total_channels": len(res.get("twitch", [])) + len(res.get("youtube", []))
        }

    _safe_register(
        name="get_live_stream_status",
        description="Checks which monitored creators or specified channels are currently streaming live on Twitch or YouTube.",
        parameters={
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "enum": ["twitch", "youtube"],
                    "description": "Optional platform filter ('twitch' or 'youtube'). Defaults to both."
                },
                "channel_name": {
                    "type": "string",
                    "description": "Optional specific channel username (Twitch) or channel ID (YouTube)."
                }
            }
        },
        handler=get_live_stream_status
    )

    _safe_register(
        name="add_to_stream_watchlist",
        description="Adds a creator or broadcast channel to the user's Twitch or YouTube live stream watchlist.",
        parameters={
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "enum": ["twitch", "youtube"],
                    "description": "Platform to monitor ('twitch' or 'youtube')."
                },
                "channel_name": {
                    "type": "string",
                    "description": "Twitch username or YouTube channel ID / handle."
                },
                "display_name": {
                    "type": "string",
                    "description": "Optional human-readable creator or channel name."
                }
            },
            "required": ["platform", "channel_name"]
        },
        handler=add_to_stream_watchlist
    )

    _safe_register(
        name="remove_from_stream_watchlist",
        description="Removes a creator or broadcast channel from the user's Twitch or YouTube live stream watchlist.",
        parameters={
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "enum": ["twitch", "youtube"],
                    "description": "Platform ('twitch' or 'youtube')."
                },
                "channel_name": {
                    "type": "string",
                    "description": "Twitch username or YouTube channel ID / name to remove."
                }
            },
            "required": ["platform", "channel_name"]
        },
        handler=remove_from_stream_watchlist
    )

    _safe_register(
        name="list_stream_watchlist",
        description="Lists all creators and channels currently monitored on the user's Twitch and YouTube live stream watchlist.",
        parameters={
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "enum": ["twitch", "youtube"],
                    "description": "Optional platform filter ('twitch' or 'youtube'). Defaults to both."
                }
            }
        },
        handler=list_stream_watchlist
    )
