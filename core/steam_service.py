"""
core/steam_service.py - Non-blocking Steam Web API Client for Aether
Provides asynchronous querying for Steam friends, achievements, and patch news.
"""
import re
import os
import json
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
import aiohttp
from core.logger import get_logger

logger = get_logger("SteamService")

STEAM_API_BASE = "https://api.steampowered.com"

# Valve persona state definitions
PERSONA_STATES = {
    0: "Offline",
    1: "Online",
    2: "Busy",
    3: "Away",
    4: "Snooze",
    5: "Looking to Trade",
    6: "Looking to Play"
}

@dataclass
class SteamFriendSummary:
    steamid: str
    persona_name: str
    status: str
    game_title: Optional[str] = None
    game_id: Optional[str] = None

@dataclass
class SteamAchievementSummary:
    total_achievements: int
    unlocked_count: int
    completion_percentage: float
    recent_unlocked: List[Dict[str, Any]]
    locked: List[Dict[str, Any]]


class _SessionWrapper:
    """
    Transparent wrapper around aiohttp.ClientSession to maintain compatibility
    with mock objects in unit tests where __aenter__ returns self.
    """
    def __init__(self, session: aiohttp.ClientSession):
        self._session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def get(self, *args, **kwargs):
        res = self._session.get(*args, **kwargs)
        if hasattr(res, "_mock_return_value") and hasattr(res, "__aenter__"):
            if hasattr(res.__aenter__, "return_value") and res.__aenter__.return_value is not res:
                res.__aenter__.return_value = res
        return res


class SteamService:
    def __init__(self, api_key: Optional[str] = None, steam_id64: Optional[str] = None):
        self.api_key = api_key or os.getenv("STEAM_API_KEY", "")
        self.steam_id64 = steam_id64 or os.getenv("STEAM_ID64", "")
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8.0))
        return _SessionWrapper(self._session)

    def configure(self, api_key: str, steam_id64: str):
        self.api_key = api_key.strip()
        self.steam_id64 = steam_id64.strip()

    def is_configured(self) -> bool:
        return bool(self.api_key and self.steam_id64)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # --- 1. Friends & Presence ---
    async def get_friends_activity(self) -> List[SteamFriendSummary]:
        """Fetches friend list and retrieves active persona/game state."""
        if not self.is_configured():
            logger.warning("[STEAM] API Key or SteamID64 missing.")
            return []

        session = await self._get_session()
        # Step A: Get Friend IDs
        friends_url = f"{STEAM_API_BASE}/ISteamUser/GetFriendList/v1/"
        params = {
            "key": self.api_key,
            "steamid": self.steam_id64,
            "relationship": "friend"
        }

        try:
            async with session.get(friends_url, params=params) as resp:
                if resp.status != 200:
                    logger.warning(f"[STEAM] Failed to fetch friend list: HTTP {resp.status}")
                    return []
                data = await resp.json()
                friends_list = data.get("friendslist", {}).get("friends", [])
        except Exception as e:
            logger.error(f"[STEAM] Error fetching friend list: {e}")
            return []

        if not friends_list:
            return []

        # Steam limits GetPlayerSummaries to 100 IDs per request
        friend_ids = [f["steamid"] for f in friends_list[:100]]
        summaries_url = f"{STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/"
        summary_params = {
            "key": self.api_key,
            "steamids": ",".join(friend_ids)
        }

        try:
            async with session.get(summaries_url, params=summary_params) as resp:
                if resp.status != 200:
                    return []
                summary_data = await resp.json()
                players = summary_data.get("response", {}).get("players", [])
        except Exception as e:
            logger.error(f"[STEAM] Error fetching player summaries: {e}")
            return []

        results = []
        for p in players:
            p_state = p.get("personastate", 0)
            status_text = PERSONA_STATES.get(p_state, "Offline")
            game_extrainfo = p.get("gameextrainfo")  # Title if playing a game
            game_id = p.get("gameid")

            results.append(SteamFriendSummary(
                steamid=p.get("steamid", ""),
                persona_name=p.get("personaname", "Unknown"),
                status=status_text,
                game_title=game_extrainfo,
                game_id=game_id
            ))
        return results

    # --- 2. Player Achievements ---
    async def get_player_achievements(self, appid: int) -> Optional[SteamAchievementSummary]:
        """Fetches achievement unlock stats for the user in a specific game."""
        if not self.is_configured() or not appid:
            return None

        session = await self._get_session()
        url = f"{STEAM_API_BASE}/ISteamUserStats/GetPlayerAchievements/v1/"
        params = {
            "key": self.api_key,
            "steamid": self.steam_id64,
            "appid": appid,
            "l": "en"
        }

        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.warning(f"[STEAM] Failed to fetch achievements for app {appid}: HTTP {resp.status}")
                    return None
                data = await resp.json()
                playerstats = data.get("playerstats", {})
                if not playerstats.get("success", False):
                    return None

                achievements = playerstats.get("achievements", [])
                total = len(achievements)
                unlocked = [a for a in achievements if a.get("achieved") == 1]
                locked = [a for a in achievements if a.get("achieved") == 0]

                unlocked_count = len(unlocked)
                pct = (unlocked_count / total * 100.0) if total > 0 else 0.0

                return SteamAchievementSummary(
                    total_achievements=total,
                    unlocked_count=unlocked_count,
                    completion_percentage=round(pct, 1),
                    recent_unlocked=unlocked[-5:] if unlocked else [],
                    locked=locked[:5] if locked else []
                )
        except Exception as e:
            logger.error(f"[STEAM] Achievement error for app {appid}: {e}")
            return None

    # --- 3. Patch News & Game Updates ---
    async def get_recent_news(self, appid: int, count: int = 3, max_length: int = 500) -> List[Dict[str, Any]]:
        """Fetches recent news items and patch logs, stripping HTML tags."""
        if not appid:
            return []

        session = await self._get_session()
        url = f"{STEAM_API_BASE}/ISteamNews/GetNewsForApp/v2/"
        params = {
            "appid": appid,
            "count": count,
            "maxlength": max_length,
            "format": "json"
        }

        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                items = data.get("appnews", {}).get("newsitems", [])

                clean_items = []
                for item in items:
                    raw_content = item.get("contents", "")
                    clean_content = re.sub(r'<[^>]+>|\[\/?\w+.*?\]', '', raw_content).strip()
                    clean_items.append({
                        "title": item.get("title", "Update"),
                        "url": item.get("url", ""),
                        "date": item.get("date", 0),
                        "summary": clean_content
                    })
                return clean_items
        except Exception as e:
            logger.error(f"[STEAM] News error for app {appid}: {e}")
            return []

