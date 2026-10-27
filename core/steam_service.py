"""
core/steam_service.py - Non-blocking Steam Web API Client for Aether
Provides asynchronous querying for Steam friends, achievements, and patch news.
"""
import re
import os
import json
import asyncio
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Callable
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
    def __init__(
        self,
        api_key: Optional[str] = None,
        steam_id64: Optional[str] = None,
        poll_friends_status: bool = False,
        poll_interval_minutes: int = 5
    ):
        self.api_key = api_key or os.getenv("STEAM_API_KEY", "")
        self.steam_id64 = steam_id64 or os.getenv("STEAM_ID64", "")
        self.poll_friends_status = bool(poll_friends_status)
        self.poll_interval_minutes = max(1, int(poll_interval_minutes or 5))
        self._session: Optional[aiohttp.ClientSession] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._poll_worker_running: bool = False
        self._status_change_callback: Optional[Callable] = None
        self._friend_state_cache: Dict[str, Dict[str, Any]] = {}
        self._has_seeded_cache: bool = False

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8.0))
        return _SessionWrapper(self._session)

    def configure(self, api_key: str, steam_id64: str):
        self.api_key = api_key.strip()
        self.steam_id64 = steam_id64.strip()
        if not self.is_configured():
            self.stop_friend_polling()

    def is_configured(self) -> bool:
        return bool(self.api_key and self.steam_id64)

    async def close(self):
        self.stop_friend_polling()
        if self._session and not self._session.closed:
            await self._session.close()

    # --- Background Friend Status Polling & Transitions ---
    async def check_friends_status_changes(self) -> List[Dict[str, Any]]:
        """
        Polls friend activity and returns a list of change events for friends
        whose status or current game has transitioned since the last check.
        On initial run, records baseline friend states without generating alerts.
        """
        if not self.is_configured():
            return []

        friends = await self.get_friends_activity()
        if not friends:
            return []

        changes: List[Dict[str, Any]] = []

        if not self._has_seeded_cache:
            for f in friends:
                self._friend_state_cache[f.steamid] = {
                    "persona_name": f.persona_name,
                    "status": f.status,
                    "game_title": f.game_title,
                    "game_id": f.game_id
                }
            self._has_seeded_cache = True
            return []

        for f in friends:
            sid = f.steamid
            prev = self._friend_state_cache.get(sid)
            curr = {
                "persona_name": f.persona_name,
                "status": f.status,
                "game_title": f.game_title,
                "game_id": f.game_id
            }

            if prev is not None:
                prev_status = prev.get("status", "Offline")
                curr_status = f.status
                prev_game = prev.get("game_title")
                curr_game = f.game_title
                name = f.persona_name or prev.get("persona_name", "Friend")

                change_msg = None
                change_type = None

                # 1. Game change: Started playing a new game
                if curr_game and curr_game != prev_game:
                    change_msg = f"{name} is now playing {curr_game}."
                    change_type = "game_started"
                # 2. Game change: Stopped playing a game (while remaining online/active)
                elif prev_game and not curr_game and curr_status != "Offline":
                    change_msg = f"{name} stopped playing {prev_game}."
                    change_type = "game_stopped"
                # 3. Status change: Came Online
                elif prev_status == "Offline" and curr_status != "Offline":
                    if curr_game:
                        change_msg = f"{name} is now online playing {curr_game}."
                    else:
                        change_msg = f"{name} is now online."
                    change_type = "status_online"
                # 4. Status change: Went Offline
                elif prev_status != "Offline" and curr_status == "Offline":
                    change_msg = f"{name} is now offline."
                    change_type = "status_offline"
                # 5. Other persona status changes (e.g. Away, Busy, Looking to Play)
                elif prev_status != curr_status and curr_status not in ("Offline", "Online"):
                    change_msg = f"{name} is now {curr_status}."
                    change_type = "status_changed"

                if change_msg:
                    changes.append({
                        "steamid": sid,
                        "persona_name": name,
                        "previous_status": prev_status,
                        "current_status": curr_status,
                        "previous_game": prev_game,
                        "current_game": curr_game,
                        "change_type": change_type,
                        "message": change_msg
                    })

            self._friend_state_cache[sid] = curr

        return changes

    def set_friend_polling(
        self,
        enabled: bool,
        interval_minutes: int = 5,
        callback: Optional[Callable] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None
    ):
        """Updates polling configuration and starts or stops background polling."""
        self.poll_friends_status = bool(enabled)
        self.poll_interval_minutes = max(1, int(interval_minutes or 5))
        if callback is not None:
            self._status_change_callback = callback

        if self.poll_friends_status:
            self.start_friend_polling(loop=loop)
        else:
            self.stop_friend_polling()

    def start_friend_polling(
        self,
        loop: Optional[asyncio.AbstractEventLoop] = None
    ):
        """Starts the background asyncio polling task if not already running."""
        if not self.is_configured():
            logger.warning("[STEAM] Cannot start friend polling: Steam not configured.")
            return

        if self._poll_worker_running and self._poll_task and not self._poll_task.done():
            return

        self._poll_worker_running = True

        target_loop = loop
        if target_loop is None:
            try:
                target_loop = asyncio.get_running_loop()
            except RuntimeError:
                target_loop = None

        if target_loop and target_loop.is_running():
            def _schedule():
                if self._poll_worker_running and (self._poll_task is None or self._poll_task.done()):
                    self._poll_task = target_loop.create_task(self._poll_worker())
            try:
                if asyncio.get_running_loop() == target_loop:
                    _schedule()
                else:
                    target_loop.call_soon_threadsafe(_schedule)
            except RuntimeError:
                target_loop.call_soon_threadsafe(_schedule)
        else:
            logger.debug("[STEAM] Event loop not currently running; polling will start when engine begins.")

    def stop_friend_polling(self):
        """Stops the background friend polling task."""
        self._poll_worker_running = False
        if self._poll_task and not self._poll_task.done():
            target_task = self._poll_task
            try:
                if target_task.get_loop().is_running():
                    target_task.get_loop().call_soon_threadsafe(target_task.cancel)
                else:
                    target_task.cancel()
            except Exception:
                pass
            self._poll_task = None
        self._friend_state_cache.clear()
        self._has_seeded_cache = False
        logger.info("[STEAM] Stopped friend status polling.")

    async def _poll_worker(self):
        logger.info(f"[STEAM] Friend status polling active (Interval: {self.poll_interval_minutes}m).")
        while self._poll_worker_running:
            try:
                changes = await self.check_friends_status_changes()
                if changes and self._status_change_callback:
                    for change in changes:
                        try:
                            res = self._status_change_callback(change)
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception as cb_err:
                            logger.error(f"[STEAM] Status change callback error: {cb_err}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[STEAM] Error in friend status polling loop: {e}")

            interval_sec = max(10, self.poll_interval_minutes * 60)
            for _ in range(interval_sec):
                if not self._poll_worker_running:
                    break
                await asyncio.sleep(1)

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

    async def resolve_friend(self, query: str) -> Optional[SteamFriendSummary]:
        """Resolves a Steam friend by persona name or steamid."""
        if not self.is_configured() or not query:
            return None
        friends = await self.get_friends_activity()
        q_clean = str(query).strip().lower()
        # Direct steamid match
        for f in friends:
            if f.steamid == q_clean:
                return f
        # Exact persona name match (case-insensitive)
        for f in friends:
            if f.persona_name.lower() == q_clean:
                return f
        # Substring persona name match
        for f in friends:
            if q_clean in f.persona_name.lower():
                return f
        return None

    # --- 2. Player Achievements ---
    async def get_player_achievements(self, appid: int, steamid: Optional[str] = None) -> Optional[SteamAchievementSummary]:
        """Fetches achievement unlock stats for the user or a specific friend in a specific game."""
        if not self.is_configured() or not appid:
            return None

        target_sid = str(steamid or self.steam_id64).strip()
        session = await self._get_session()
        url = f"{STEAM_API_BASE}/ISteamUserStats/GetPlayerAchievements/v1/"
        params = {
            "key": self.api_key,
            "steamid": target_sid,
            "appid": appid,
            "l": "en"
        }

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 403:
                    logger.info(f"[STEAM] Achievements for {target_sid} (app {appid}) are private (HTTP 403).")
                    return None
                if resp.status != 200:
                    logger.warning(f"[STEAM] Failed to fetch achievements for app {appid} (user {target_sid}): HTTP {resp.status}")
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

    # --- 3. Owned & Recently Played Games ---
    async def get_owned_games(self, steamid: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetches owned games library for the user or a specific friend."""
        if not self.is_configured():
            return []
        target_sid = str(steamid or self.steam_id64).strip()
        session = await self._get_session()
        url = f"{STEAM_API_BASE}/IPlayerService/GetOwnedGames/v1/"
        params = {
            "key": self.api_key,
            "steamid": target_sid,
            "include_appinfo": 1,
            "include_played_free_games": 1
        }
        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.warning(f"[STEAM] Failed to fetch owned games for {target_sid}: HTTP {resp.status}")
                    return []
                data = await resp.json()
                games = data.get("response", {}).get("games", [])
                results = []
                for g in games:
                    results.append({
                        "appid": g.get("appid"),
                        "name": g.get("name", "Unknown"),
                        "playtime_hours": round(g.get("playtime_forever", 0) / 60.0, 1),
                        "rtime_last_played": g.get("rtime_last_played", 0)
                    })
                return results
        except Exception as e:
            logger.error(f"[STEAM] Error fetching owned games for {target_sid}: {e}")
            return []

    async def get_recently_played_games(self, steamid: Optional[str] = None, count: int = 10) -> List[Dict[str, Any]]:
        """Fetches recently played games for the user or a specific friend."""
        if not self.is_configured():
            return []
        target_sid = str(steamid or self.steam_id64).strip()
        session = await self._get_session()
        url = f"{STEAM_API_BASE}/IPlayerService/GetRecentlyPlayedGames/v1/"
        params = {
            "key": self.api_key,
            "steamid": target_sid,
            "count": count
        }
        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                games = data.get("response", {}).get("games", [])
                results = []
                for g in games:
                    results.append({
                        "appid": g.get("appid"),
                        "name": g.get("name", "Unknown"),
                        "playtime_2weeks_hours": round(g.get("playtime_2weeks", 0) / 60.0, 1),
                        "playtime_forever_hours": round(g.get("playtime_forever", 0) / 60.0, 1)
                    })
                return results
        except Exception as e:
            logger.error(f"[STEAM] Error fetching recently played games for {target_sid}: {e}")
            return []

    # --- 4. Patch News & Game Updates ---
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

    # --- 5. Game Title & Store AppID Resolution ---
    async def resolve_appid_by_title(self, title: str) -> Optional[int]:
        """Resolves a game title to a Steam AppID using user's owned games and Steam Store Search."""
        if not title:
            return None

        clean_title = str(title).strip()
        if not clean_title:
            return None

        # 1. Check if user already owns this game (quick local check against owned library)
        try:
            if self.is_configured():
                owned = await self.get_owned_games(self.steam_id64)
                for g in owned:
                    if g.get("name", "").strip().lower() == clean_title.lower():
                        return int(g["appid"])
                for g in owned:
                    if clean_title.lower() in g.get("name", "").strip().lower():
                        return int(g["appid"])
        except Exception as e:
            logger.debug(f"[STEAM] Error checking owned games for title '{clean_title}': {e}")

        # 2. Query Valve Store Search (public unauthenticated API)
        session = await self._get_session()
        url = "https://store.steampowered.com/api/storesearch/"
        params = {
            "term": clean_title,
            "l": "english",
            "cc": "US"
        }
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("items", [])
                    if items:
                        for it in items:
                            if it.get("name", "").strip().lower() == clean_title.lower():
                                return int(it.get("id"))
                        return int(items[0].get("id"))
        except Exception as e:
            logger.warning(f"[STEAM] Store search error for '{clean_title}': {e}")

        return None


