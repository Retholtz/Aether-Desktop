"""
tools/notification_tools.py - Agent Tools for Querying Desktop Notifications
Exposes tools for checking recent incoming Discord messages and system toasts.
"""
from typing import Dict, Any, Optional
from core.notification_watcher import DesktopNotificationWatcher


def register_notification_tools(dispatcher, watcher: DesktopNotificationWatcher):
    """Registers notification management tools onto Aether's ToolDispatcher."""

    async def get_recent_desktop_notifications(
        app_name: Optional[str] = None,
        limit: int = 5
    ) -> Dict[str, Any]:
        """
        Retrieves recent desktop notifications and messages (Discord, Slack, Teams, etc.).
        Useful when the user asks 'did anyone message me on Discord?' or 'what notifications do I have?'.
        """
        if not watcher or not watcher.is_supported():
            return {
                "status": "error",
                "message": "Desktop notification listener is unavailable on this platform."
            }

        notifs = watcher.get_recent(app_filter=app_name, limit=limit)
        if not notifs:
            target = f"from {app_name}" if app_name else "across all monitored apps"
            return {
                "status": "success",
                "message": f"No recent notifications found {target}.",
                "notifications": []
            }

        return {
            "status": "success",
            "count": len(notifs),
            "notifications": notifs
        }

    async def clear_recent_notifications() -> Dict[str, Any]:
        """Clears the cached desktop notification list in Aether's memory."""
        if watcher:
            watcher.clear_history()
        return {"status": "success", "message": "Notification history cleared."}

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
        name="get_recent_desktop_notifications",
        description="Reads recent incoming desktop notifications (e.g. Discord DMs, mentions, Slack, Teams).",
        parameters={
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "Optional app name filter (e.g., 'Discord', 'Slack', 'Teams')."
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of notifications to return (default 5)."
                }
            }
        },
        handler=get_recent_desktop_notifications
    )

    _safe_register(
        name="clear_recent_notifications",
        description="Clears Aether's recent notification log.",
        parameters={"type": "object", "properties": {}},
        handler=clear_recent_notifications
    )

