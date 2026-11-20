"""
tools/media_tools.py - Agent Tools for Controlling Windows Media Playback
Exposes tools for checking what song is playing, issuing transport controls,
and directly playing music tracks on YouTube Music / YouTube without slow vision clicking.
"""
import asyncio
import re
import urllib.parse
from typing import Dict, Any, Optional

import requests

from core.media_controller_service import MediaControllerService
from tools.os_controls import navigate_browser


def _resolve_youtube_video_id(query: str, prefer_music: bool = True) -> Optional[str]:
    """Resolves the top YouTube videoId for a song/artist search query in <1s via HTTP."""
    clean_q = str(query or "").strip()
    if not clean_q:
        return None
    search_q = f"{clean_q} song" if (prefer_music and "song" not in clean_q.lower() and "audio" not in clean_q.lower() and "music" not in clean_q.lower()) else clean_q
    try:
        url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(search_q)
        resp = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=6,
        )
        if not resp.ok or not resp.text:
            return None
        ids = list(dict.fromkeys(re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', resp.text)))
        if ids:
            return ids[0]
    except Exception:
        pass
    return None


def register_media_tools(dispatcher, service: MediaControllerService):
    """Registers media control tools on Aether's ToolDispatcher."""

    async def get_current_playing_media() -> Dict[str, Any]:
        """
        Retrieves metadata about the currently playing song or video across Spotify,
        YouTube Music, browsers, or desktop media players.
        """
        info = await service.get_current_media_info()
        if not info:
            return {
                "status": "idle",
                "message": "No media player is currently active or reporting track information."
            }

        return {
            "status": "success",
            "track": info.to_dict(),
            "summary": f"'{info.title}' by {info.artist} ({info.playback_status})"
        }

    async def control_media_playback(action: str) -> Dict[str, Any]:
        """
        Controls active media playback on Windows.
        Action must be one of: 'play', 'pause', 'toggle', 'next', 'previous'.
        """
        success, message = await service.execute_transport_command(action, fallback_to_media_key=True)
        return {
            "status": "success" if success else "error",
            "action": action,
            "message": message
        }

    async def play_music(
        query: str,
        service_name: str = "youtube_music",
        app_name: str = "chrome",
        monitor: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Deterministically searches and starts playing a song, artist, or video directly on
        YouTube Music or YouTube in ~2 seconds without brittle vision clicking.
        """
        clean_q = str(query or "").strip()
        if not clean_q:
            return {"status": "error", "message": "A song, artist, or video query is required."}

        svc = str(service_name or "youtube_music").strip().lower().replace(" ", "_")
        video_id = None
        if svc in ("youtube_music", "ytmusic", "yt_music", "music", "youtube", "yt"):
            prefer_music = svc not in ("youtube", "yt")
            video_id = await asyncio.to_thread(_resolve_youtube_video_id, clean_q, prefer_music)

        if video_id:
            if svc in ("youtube", "yt"):
                target_url = f"https://www.youtube.com/watch?v={video_id}"
            else:
                target_url = f"https://music.youtube.com/watch?v={video_id}"
        elif svc == "spotify":
            target_url = f"https://open.spotify.com/search/{urllib.parse.quote(clean_q)}"
        elif svc in ("youtube", "yt"):
            target_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(clean_q)}"
        else:
            target_url = f"https://music.youtube.com/search?q={urllib.parse.quote_plus(clean_q)}"

        nav_res = await asyncio.to_thread(
            navigate_browser,
            target_url,
            app_name=app_name or "chrome",
            monitor=monitor,
        )
        if nav_res.get("status") == "error":
            return nav_res

        # Wait briefly for direct watch page to start playback and verify via SMTC
        playing_info = None
        if video_id:
            for attempt in range(6):
                await asyncio.sleep(0.5)
                info = await service.get_current_media_info()
                if info and str(getattr(info, "playback_status", "")).lower() == "playing":
                    playing_info = info
                    break
                # If browser autoplay paused the video after ~1.5s, issue a play transport command once
                if attempt == 2:
                    try:
                        await service.execute_transport_command("play", fallback_to_media_key=False)
                    except Exception:
                        pass

        if playing_info:
            return {
                "status": "success",
                "action": "play_music",
                "query": clean_q,
                "service": svc,
                "video_id": video_id,
                "url": target_url,
                "playback_verified": True,
                "track": playing_info.to_dict(),
                "message": f"Started playing '{playing_info.title}' by {playing_info.artist} on {svc.replace('_', ' ').title()}.",
            }

        return {
            "status": "success" if video_id else "opened_search",
            "action": "play_music",
            "query": clean_q,
            "service": svc,
            "video_id": video_id,
            "url": target_url,
            "playback_verified": False,
            "message": (
                f"Opened direct track URL '{target_url}' for '{clean_q}' in {app_name}. "
                "Playback should begin momentarily; call control_media_playback('play') if paused."
                if video_id
                else f"Opened search results for '{clean_q}' at '{target_url}'."
            ),
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

    _safe_register(
        name="get_current_playing_media",
        description="Checks what track, song, or video is currently playing on Windows.",
        parameters={"type": "object", "properties": {}},
        handler=get_current_playing_media
    )

    _safe_register(
        name="control_media_playback",
        description="Controls media playback (play, pause, toggle, next, previous) for active players.",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play", "pause", "toggle", "next", "previous"],
                    "description": "Transport action to send to the active media player."
                }
            },
            "required": ["action"]
        },
        handler=control_media_playback
    )

    _safe_register(
        name="play_music",
        description=(
            "Fast, deterministic tool to immediately search and play a specific song, artist, album, "
            "or music video directly on YouTube Music or YouTube without slow screen clicking. "
            "ALWAYS use this tool when asked to play a song or artist (e.g. 'Open YouTube Music and play Nyra Noir', "
            "'Play Sting on YouTube')."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Song title, artist name, or music search query (e.g. 'Nyra Noir', 'Sting Fields of Gold')."
                },
                "service_name": {
                    "type": "string",
                    "enum": ["youtube_music", "youtube", "spotify"],
                    "description": "Target music platform. Default is 'youtube_music'."
                },
                "app_name": {
                    "type": "string",
                    "description": "Browser to open the track in. Default is 'chrome'."
                },
                "monitor": {
                    "type": "integer",
                    "description": "Optional 1-based monitor number to place the browser window on."
                }
            },
            "required": ["query"]
        },
        handler=play_music
    )

