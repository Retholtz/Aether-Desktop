"""
tools/replay_tools.py - Agent Tool Declarations for Replay Capture
Allows the LLM to trigger a gameplay capture if requested through conversation.
"""
from typing import Dict, Any
from core.replay_service import ReplayService


def register_replay_tools(dispatcher, replay_service: ReplayService):
    """Registers replay tools on Aether's ToolDispatcher."""

    async def trigger_instant_replay() -> Dict[str, Any]:
        """
        Triggers an instant replay capture of recent gameplay using ShadowPlay, Xbox Game Bar, or OBS.
        Use this when the user asks to save a video clip or record recent game action.
        """
        return await replay_service.trigger_clip()

    try:
        dispatcher.register_tool(
            name="trigger_instant_replay",
            description="Captures and saves a video clip of the last 30-60 seconds of gameplay.",
            parameters={"type": "object", "properties": {}},
            handler=trigger_instant_replay
        )
    except TypeError:
        decl = {
            "name": "trigger_instant_replay",
            "description": "Captures and saves a video clip of the last 30-60 seconds of gameplay.",
            "parameters": {"type": "object", "properties": {}}
        }
        dispatcher.register_tool("trigger_instant_replay", decl, trigger_instant_replay)

