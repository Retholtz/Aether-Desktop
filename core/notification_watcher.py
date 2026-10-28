"""
core/notification_watcher.py - WinRT Desktop Notification Listener
Monitors Windows OS toast notifications for Discord, Slack, and other desktop apps
using UserNotificationListener without violating application Terms of Service.
"""
import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable, Set

from core.logger import get_logger

logger = get_logger("NotificationWatcher")

# Lazy-import winsdk components to permit mock testing on non-Windows environments
try:
    from winsdk.windows.ui.notifications.management import (
        UserNotificationListener,
        UserNotificationListenerAccessStatus
    )
    from winsdk.windows.ui.notifications import NotificationKinds, KnownNotificationBindings
    WINSDK_AVAILABLE = True
except ImportError:
    try:
        from winrt.windows.ui.notifications.management import (
            UserNotificationListener,
            UserNotificationListenerAccessStatus
        )
        from winrt.windows.ui.notifications import NotificationKinds, KnownNotificationBindings
        WINSDK_AVAILABLE = True
    except ImportError:
        WINSDK_AVAILABLE = False
        UserNotificationListener = None
        UserNotificationListenerAccessStatus = None
        NotificationKinds = None
        KnownNotificationBindings = None

if NotificationKinds is None:
    class _MockNotificationKinds:
        TOAST = 1
    NotificationKinds = _MockNotificationKinds

if KnownNotificationBindings is None:
    class _MockKnownNotificationBindings:
        toast_generic = "toastGeneric"
    KnownNotificationBindings = _MockKnownNotificationBindings

if UserNotificationListenerAccessStatus is None:
    class _MockUserNotificationListenerAccessStatus:
        ALLOWED = 1
        DENIED = 2
        UNSPECIFIED = 0
    UserNotificationListenerAccessStatus = _MockUserNotificationListenerAccessStatus


@dataclass
class DesktopNotification:
    notification_id: int
    app_name: str
    title: str
    body: str
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.notification_id,
            "app_name": self.app_name,
            "title": self.title,
            "body": self.body,
            "timestamp": self.timestamp.isoformat()
        }


class DesktopNotificationWatcher:
    def __init__(
        self,
        poll_interval: float = 1.5,
        target_apps: Optional[List[str]] = None,
        on_notification: Optional[Callable[[DesktopNotification], None]] = None,
        cache_size: int = 300,
        whitelist_getter: Optional[Callable[[], List[str]]] = None
    ):
        self.poll_interval = poll_interval
        self.target_apps: Set[str] = {app.lower() for app in (target_apps or ["Discord"])}
        self.on_notification = on_notification
        self.whitelist_getter = whitelist_getter

        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._listener = None
        self._seen_ids: Set[int] = set()
        self._seen_queue: deque = deque(maxlen=cache_size)
        self._recent_notifications: deque = deque(maxlen=50)

    def is_supported(self) -> bool:
        return WINSDK_AVAILABLE

    def get_effective_targets(self) -> Set[str]:
        """
        Returns combined set of monitored app names:
        Configured target apps + active programs from the Security Whitelist (if whitelist_getter provided).
        """
        targets = set(self.target_apps)
        if callable(self.whitelist_getter):
            try:
                wl = self.whitelist_getter() or []
                for item in wl:
                    s = str(item).strip().lower()
                    if not s or s == "*":
                        continue
                    stem = s[:-4] if s.endswith(".exe") else s
                    if stem and len(stem) >= 2:
                        targets.add(stem)
                    if len(s) >= 2:
                        targets.add(s)
            except Exception as e:
                logger.debug(f"[NOTIFICATIONS] Error resolving whitelist targets: {e}")
        return targets

    async def initialize(self) -> bool:
        """Requests WinRT notification permissions from the Windows shell."""
        if not self.is_supported():
            logger.warning("[NOTIFICATIONS] winsdk is not installed. Notification watching disabled.")
            return False

        try:
            if hasattr(UserNotificationListener, "get_current") and callable(getattr(UserNotificationListener, "get_current")):
                self._listener = UserNotificationListener.get_current()
            elif hasattr(UserNotificationListener, "current"):
                self._listener = UserNotificationListener.current
            else:
                self._listener = None

            if not self._listener:
                logger.warning("[NOTIFICATIONS] Could not acquire UserNotificationListener instance.")
                return False

            access_status = await self._listener.request_access_async()
            if access_status != UserNotificationListenerAccessStatus.ALLOWED:
                logger.warning(
                    f"[NOTIFICATIONS] WinRT Access Denied ({access_status}). "
                    "Ensure Notifications are enabled in Windows Settings > Privacy."
                )
                return False

            # Prime the deduplication cache with current notifications so we don't spam on startup
            current_toasts = await self._listener.get_notifications_async(NotificationKinds.TOAST)
            for notif in current_toasts:
                self._record_seen(notif.id)

            logger.info(f"[NOTIFICATIONS] Subsystem initialized. Tracking apps: {list(self.target_apps)}")
            return True
        except Exception as e:
            logger.error(f"[NOTIFICATIONS] Failed to initialize UserNotificationListener: {e}")
            return False

    def _record_seen(self, notif_id: int):
        if notif_id not in self._seen_ids:
            if len(self._seen_queue) >= self._seen_queue.maxlen:
                oldest = self._seen_queue.popleft()
                self._seen_ids.discard(oldest)
            self._seen_ids.add(notif_id)
            self._seen_queue.append(notif_id)

    async def start(self):
        """Starts the background polling task."""
        if self._running:
            return

        initialized = await self.initialize()
        if not initialized:
            return

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("[NOTIFICATIONS] Background polling loop active.")

    async def stop(self):
        """Cleanly stops the watcher."""
        self._running = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("[NOTIFICATIONS] Background polling loop stopped.")

    async def _poll_loop(self):
        while self._running:
            try:
                await self._poll_notifications()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[NOTIFICATIONS] Error polling notifications: {e}")
            await asyncio.sleep(self.poll_interval)

    async def _poll_notifications(self):
        if not self._listener:
            return

        notifications = await self._listener.get_notifications_async(NotificationKinds.TOAST)
        for notif in notifications:
            notif_id = notif.id
            if notif_id in self._seen_ids:
                continue

            self._record_seen(notif_id)

            # Extract Application Name
            app_display_name = "Unknown"
            try:
                if notif.app_info and notif.app_info.display_info:
                    app_display_name = notif.app_info.display_info.display_name or "Unknown"
            except Exception:
                pass

            # Filter by configured targets and whitelist
            effective_targets = self.get_effective_targets()
            if effective_targets:
                app_lower = app_display_name.lower()
                matched = any(
                    target in app_lower or app_lower in target
                    for target in effective_targets
                )
                if not matched:
                    continue

            # Extract Text Content
            title, body = self._extract_text(notif)
            if not title and not body:
                continue

            parsed_item = DesktopNotification(
                notification_id=notif_id,
                app_name=app_display_name,
                title=title,
                body=body
            )

            self._recent_notifications.appendleft(parsed_item)

            if callable(self.on_notification):
                try:
                    self.on_notification(parsed_item)
                except Exception as ex:
                    logger.error(f"[NOTIFICATIONS] Error in callback handler: {ex}")

    def _extract_text(self, notif) -> tuple[str, str]:
        """Safely parses toast title and content elements from visual bindings."""
        try:
            visual = notif.notification.visual
            binding = visual.get_binding(KnownNotificationBindings.toast_generic)
            if not binding:
                return "", ""

            elements = list(binding.get_text_elements())
            texts = [el.text.strip() for el in elements if el.text]

            if not texts:
                return "", ""

            title = texts[0]
            body = "\n".join(texts[1:]) if len(texts) > 1 else ""
            return title, body
        except Exception:
            return "", ""

    def get_recent(self, app_filter: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Returns captured notifications stored in memory."""
        results = []
        filter_lower = app_filter.lower() if app_filter else None

        for n in self._recent_notifications:
            if filter_lower and filter_lower not in n.app_name.lower():
                continue
            results.append(n.to_dict())
            if len(results) >= limit:
                break
        return results

    def clear_history(self):
        self._recent_notifications.clear()

