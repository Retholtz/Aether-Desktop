"""
core/livestream_service.py - Non-blocking Twitch Helix & YouTube Live Watcher
Monitors broadcast statuses and detects stream state transitions.
"""
import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable
import aiohttp

from core.logger import get_logger

logger = get_logger("LiveStreamService")

TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_STREAMS_URL = "https://api.twitch.tv/helix/streams"
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"


@dataclass
class LiveStreamInfo:
    platform: str                    # "twitch" | "youtube"
    channel_name: str
    channel_id: str
    is_live: bool
    title: str = ""
    game_or_category: str = ""
    viewer_count: int = 0
    url: str = ""
    started_at: Optional[str] = None
    last_checked: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform,
            "channel_name": self.channel_name,
            "channel_id": self.channel_id,
            "is_live": self.is_live,
            "title": self.title,
            "game_or_category": self.game_or_category,
            "viewer_count": self.viewer_count,
            "url": self.url,
            "started_at": self.started_at,
            "last_checked": self.last_checked.isoformat() if hasattr(self.last_checked, "isoformat") else str(self.last_checked)
        }


class LiveStreamService:
    def __init__(
        self,
        twitch_client_id: str = "",
        twitch_client_secret: str = "",
        youtube_api_key: str = "",
        on_stream_went_live: Optional[Callable[[LiveStreamInfo], None]] = None,
        google_token_provider: Optional[Callable[[], Optional[str]]] = None,
        twitch_token_provider: Optional[Callable[[], Optional[str]]] = None,
    ):
        self.twitch_client_id = twitch_client_id.strip() if twitch_client_id else ""
        self.twitch_client_secret = twitch_client_secret.strip() if twitch_client_secret else ""
        self.youtube_api_key = youtube_api_key.strip() if youtube_api_key else ""
        self.on_stream_went_live = on_stream_went_live
        self.google_token_provider = google_token_provider
        self.twitch_token_provider = twitch_token_provider

        self._session: Optional[aiohttp.ClientSession] = None
        self._twitch_access_token: Optional[str] = None
        self._twitch_token_expires_at: float = 0.0

        # Channel state tracking: (platform, channel_id/name) -> is_live bool
        self._channel_states: Dict[str, bool] = {}

    def configure(
        self,
        twitch_client_id: Optional[str] = None,
        twitch_client_secret: Optional[str] = None,
        youtube_api_key: Optional[str] = None,
        google_token_provider: Optional[Callable[[], Optional[str]]] = None,
        twitch_token_provider: Optional[Callable[[], Optional[str]]] = None,
    ):
        """Updates API credentials and token providers at runtime."""
        if twitch_client_id is not None:
            self.twitch_client_id = twitch_client_id.strip()
            self._twitch_access_token = None
        if twitch_client_secret is not None:
            self.twitch_client_secret = twitch_client_secret.strip()
            self._twitch_access_token = None
        if youtube_api_key is not None:
            self.youtube_api_key = youtube_api_key.strip()
        if google_token_provider is not None:
            self.google_token_provider = google_token_provider
        if twitch_token_provider is not None:
            self.twitch_token_provider = twitch_token_provider

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0))
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # --- Twitch Helix Pipeline ---
    def has_twitch(self) -> bool:
        if self.twitch_client_id and self.twitch_client_secret:
            return True
        if self.twitch_client_id and callable(self.twitch_token_provider) and self.twitch_token_provider():
            return True
        return False

    async def _ensure_twitch_token(self) -> Optional[str]:
        """Obtains or refreshes Twitch OAuth2 token (user token via OAuth or app access token)."""
        if not self.has_twitch():
            return None

        # Check if an OAuth user access token is provided
        if callable(self.twitch_token_provider):
            tok = self.twitch_token_provider()
            if tok:
                return tok

        if self._twitch_access_token and time.time() < (self._twitch_token_expires_at - 60):
            return self._twitch_access_token

        if not self.twitch_client_secret:
            return None

        session = await self._get_session()
        params = {
            "client_id": self.twitch_client_id,
            "client_secret": self.twitch_client_secret,
            "grant_type": "client_credentials"
        }

        try:
            req = session.post(TWITCH_TOKEN_URL, params=params)
            async with req as resp:
                actual = req if hasattr(req, "status") and isinstance(req.status, int) and not isinstance(resp.status, int) else resp
                if actual.status != 200:
                    logger.error(f"[STREAM] Twitch token request failed: HTTP {actual.status}")
                    return None
                data = await actual.json()
                self._twitch_access_token = data.get("access_token")
                expires_in = data.get("expires_in", 3600)
                self._twitch_token_expires_at = time.time() + expires_in
                return self._twitch_access_token
        except Exception as e:
            logger.error(f"[STREAM] Error requesting Twitch app token: {e}")
            return None

    async def get_twitch_streams(self, channels: List[str]) -> List[LiveStreamInfo]:
        """Queries status for up to 100 Twitch channel logins simultaneously."""
        token = await self._ensure_twitch_token()
        if not token or not channels:
            return []

        session = await self._get_session()
        headers = {
            "Client-Id": self.twitch_client_id,
            "Authorization": f"Bearer {token}"
        }

        # Query batch
        query_params = [("user_login", ch.lower().strip()) for ch in channels[:100]]

        try:
            req = session.get(TWITCH_STREAMS_URL, headers=headers, params=query_params)
            async with req as resp:
                actual = req if hasattr(req, "status") and isinstance(req.status, int) and not isinstance(resp.status, int) else resp
                if actual.status != 200:
                    logger.warning(f"[STREAM] Twitch streams endpoint returned HTTP {actual.status}")
                    return []
                data = await actual.json()
                stream_list = data.get("data", [])
        except Exception as e:
            logger.error(f"[STREAM] Twitch streams fetch exception: {e}")
            return []

        live_by_login = {s["user_login"].lower(): s for s in stream_list}
        results = []

        for ch in channels:
            login = ch.lower().strip()
            if login in live_by_login:
                s = live_by_login[login]
                info = LiveStreamInfo(
                    platform="twitch",
                    channel_name=s.get("user_name", login),
                    channel_id=s.get("user_id", ""),
                    is_live=True,
                    title=s.get("title", ""),
                    game_or_category=s.get("game_name", ""),
                    viewer_count=s.get("viewer_count", 0),
                    url=f"https://twitch.tv/{login}",
                    started_at=s.get("started_at")
                )
            else:
                info = LiveStreamInfo(
                    platform="twitch",
                    channel_name=login,
                    channel_id="",
                    is_live=False,
                    url=f"https://twitch.tv/{login}"
                )

            self._process_state_transition(f"twitch:{login}", info)
            results.append(info)

        return results

    # --- YouTube Data API v3 Pipeline ---
    def has_youtube(self) -> bool:
        if self.youtube_api_key:
            return True
        if callable(self.google_token_provider) and self.google_token_provider():
            return True
        return False

    async def check_youtube_channel_live(self, channel_id: str, channel_name: str = "") -> LiveStreamInfo:
        """Queries whether a specific YouTube channel is currently broadcasting live."""
        if not self.has_youtube() or not channel_id:
            return LiveStreamInfo(
                platform="youtube",
                channel_name=channel_name or channel_id,
                channel_id=channel_id,
                is_live=False
            )

        session = await self._get_session()
        headers = {}
        params = {
            "part": "snippet",
            "channelId": channel_id,
            "eventType": "live",
            "type": "video"
        }
        if self.youtube_api_key:
            params["key"] = self.youtube_api_key
        elif callable(self.google_token_provider):
            g_tok = self.google_token_provider()
            if g_tok:
                headers["Authorization"] = f"Bearer {g_tok}"

        try:
            req = session.get(YOUTUBE_SEARCH_URL, headers=headers, params=params)
            async with req as resp:
                actual = req if hasattr(req, "status") and isinstance(req.status, int) and not isinstance(resp.status, int) else resp
                if actual.status != 200:
                    logger.warning(f"[STREAM] YouTube API check returned HTTP {actual.status}")
                    return LiveStreamInfo(platform="youtube", channel_name=channel_name, channel_id=channel_id, is_live=False)

                data = await actual.json()
                items = data.get("items", [])
                if items:
                    item = items[0]
                    video_id = item.get("id", {}).get("videoId", "")
                    snippet = item.get("snippet", {})
                    info = LiveStreamInfo(
                        platform="youtube",
                        channel_name=snippet.get("channelTitle") or channel_name or channel_id,
                        channel_id=channel_id,
                        is_live=True,
                        title=snippet.get("title", ""),
                        game_or_category="Live Stream",
                        viewer_count=0,
                        url=f"https://youtube.com/watch?v={video_id}" if video_id else f"https://youtube.com/channel/{channel_id}",
                        started_at=snippet.get("publishedAt")
                    )
                else:
                    info = LiveStreamInfo(
                        platform="youtube",
                        channel_name=channel_name or channel_id,
                        channel_id=channel_id,
                        is_live=False,
                        url=f"https://youtube.com/channel/{channel_id}"
                    )
        except Exception as e:
            logger.error(f"[STREAM] YouTube API check failed for {channel_id}: {e}")
            info = LiveStreamInfo(platform="youtube", channel_name=channel_name, channel_id=channel_id, is_live=False)

        self._process_state_transition(f"youtube:{channel_id}", info)
        return info

    # --- Edge Transition Logic ---
    def _process_state_transition(self, tracker_key: str, current_info: LiveStreamInfo):
        previous_state = self._channel_states.get(tracker_key)

        # Detect edge: was Offline (or unrecorded initial startup), now Live
        if previous_state is False and current_info.is_live:
            logger.info(f"[STREAM] Edge detected: {current_info.platform} channel {current_info.channel_name} went LIVE.")
            if callable(self.on_stream_went_live):
                try:
                    self.on_stream_went_live(current_info)
                except Exception as ex:
                    logger.error(f"[STREAM] Callback error on live transition: {ex}")

        # Update cache
        self._channel_states[tracker_key] = current_info.is_live

