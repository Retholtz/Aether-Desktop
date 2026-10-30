"""
tools/media_tools.py - Agent Tools for Controlling Windows Media Playback
Exposes tools for checking what song is playing and issuing transport controls.
"""
from typing import Dict, Any, Optional
from core.media_controller_service import MediaControllerService


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

