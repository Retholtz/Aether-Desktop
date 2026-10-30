"""
core/media_controller_service.py - Windows System Media Transport Controls (SMTC) Client
Provides non-blocking playback controls and track metadata inspection across media players.
"""
import asyncio
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple
from core.logger import get_logger

logger = get_logger("MediaController")

# Import PyWinRT components to permit mock testing on non-Windows environments
try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as SMTCManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus
    )
    WINRT_MEDIA_AVAILABLE = True
except ImportError:
    WINRT_MEDIA_AVAILABLE = False
    SMTCManager = None
    PlaybackStatus = None


@dataclass
class MediaTrackInfo:
    title: str = "Unknown Title"
    artist: str = "Unknown Artist"
    album_title: str = ""
    playback_status: str = "Unknown"  # "Playing" | "Paused" | "Stopped" | "Closed"
    source_app_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "artist": self.artist,
            "album_title": self.album_title,
            "playback_status": self.playback_status,
            "source_app_id": self.source_app_id
        }


class MediaControllerService:
    def __init__(self, verbal_ack: bool = True):
        self.verbal_ack = verbal_ack
        self._manager = None
        self._lock = asyncio.Lock()

    def is_supported(self) -> bool:
        return WINRT_MEDIA_AVAILABLE

    async def _get_manager(self):
        """Retrieves or initializes the WinRT SMTC session manager."""
        if not self.is_supported():
            return None
        try:
            return await SMTCManager.request_async()
        except Exception as e:
            logger.error(f"[MEDIA] Failed to request SMTC Session Manager: {e}")
            return None

    async def _resolve_session(self):
        """Gets current focused media session, falling back to any active session."""
        mgr = await self._get_manager()
        if not mgr:
            return None

        # 1. Primary: check all sessions and prioritize any actively playing session
        try:
            sessions = mgr.get_sessions()
            if sessions:
                for s in sessions:
                    try:
                        p_info = s.get_playback_info()
                        if p_info and getattr(p_info, "playback_status", None) == 4:  # 4 = Playing
                            return s
                    except Exception:
                        pass
        except Exception:
            pass

        # 2. Secondary: current focused session if it has valid playback info
        try:
            cur = mgr.get_current_session()
            if cur:
                p_info = cur.get_playback_info()
                if p_info and getattr(p_info, "playback_status", None) != 0:  # != Closed
                    return cur
        except Exception:
            pass

        # 3. Tertiary: any session with status != Closed
        try:
            sessions = mgr.get_sessions()
            if sessions:
                for s in sessions:
                    try:
                        p_info = s.get_playback_info()
                        if p_info and getattr(p_info, "playback_status", None) != 0:
                            return s
                    except Exception:
                        pass
                if len(sessions) > 0:
                    return sessions[0]
        except Exception:
            pass

        return None

    def _parse_playback_status(self, raw_status: Any) -> str:
        # Standard WinRT GlobalSystemMediaTransportControlsSessionPlaybackStatus
        status_map = {
            0: "Closed",
            1: "Opened",
            2: "Changing",
            3: "Stopped",
            4: "Playing",
            5: "Paused"
        }
        try:
            val = getattr(raw_status, "value", int(raw_status))
            return status_map.get(val, "Unknown")
        except Exception:
            return "Unknown"

    def _parse_media_title(self, title: str, proc_name: str = "") -> Optional[Tuple[MediaTrackInfo, str]]:
        """
        Parses track and artist from a window title across YouTube Music, Spotify, YouTube, etc.
        Returns (MediaTrackInfo, source_type).
        """
        title = title.strip()
        if not title:
            return None

        proc_lower = proc_name.lower()
        title_lower = title.lower()

        # 1. YouTube Music with bullet (e.g. "Song • Artist - YouTube Music - Google Chrome" or "Song • Artist")
        if "•" in title:
            parts = title.split("•")
            track_title = parts[0].strip()
            rest = parts[1].strip()
            for suffix in [" - youtube music", " - google chrome", " - microsoft edge", " - brave"]:
                idx = rest.lower().find(suffix)
                if idx != -1:
                    rest = rest[:idx].strip()
            if track_title and rest:
                return MediaTrackInfo(
                    title=track_title,
                    artist=rest,
                    playback_status="Playing",
                    source_app_id=proc_name or "YouTube Music"
                ), "youtube_music"

        # 2. YouTube Music with hyphen
        if "youtube music" in title_lower:
            clean = title
            for suffix in [" - google chrome", " - microsoft edge", " - brave"]:
                idx = clean.lower().find(suffix)
                if idx != -1:
                    clean = clean[:idx].strip()
            idx = clean.lower().find(" - youtube music")
            if idx != -1:
                clean = clean[:idx].strip()
                parts = clean.split(" - ")
                if len(parts) >= 2:
                    return MediaTrackInfo(
                        title=parts[0].strip(),
                        artist=parts[1].strip(),
                        playback_status="Playing",
                        source_app_id=proc_name or "YouTube Music"
                    ), "youtube_music"
                elif clean.lower() != "youtube music":
                    return MediaTrackInfo(
                        title=clean,
                        artist="YouTube Music",
                        playback_status="Playing",
                        source_app_id=proc_name or "YouTube Music"
                    ), "youtube_music"

            # Check if title is e.g. "Artist - YouTube Music"
            if clean.lower().endswith("youtube music"):
                prefix = clean[:clean.lower().rfind("youtube music")].rstrip(" -").strip()
                if prefix:
                    return MediaTrackInfo(
                        title=prefix,
                        artist="YouTube Music",
                        playback_status="Playing",
                        source_app_id=proc_name or "YouTube Music"
                    ), "youtube_music"
            return None

        # 3. Regular YouTube tab (e.g. "Song Title - YouTube - Google Chrome")
        if " - youtube" in title_lower:
            clean = title
            for suffix in [" - google chrome", " - microsoft edge", " - brave"]:
                idx = clean.lower().find(suffix)
                if idx != -1:
                    clean = clean[:idx].strip()
            idx = clean.lower().find(" - youtube")
            if idx != -1:
                clean = clean[:idx].strip()
            return MediaTrackInfo(
                title=clean,
                artist="YouTube",
                playback_status="Playing",
                source_app_id=proc_name or "YouTube"
            ), "youtube"

        # 4. Spotify
        if "spotify" in proc_lower or "spotify" in title_lower:
            if title_lower not in ("spotify", "spotify free", "spotify premium"):
                parts = title.split(" - ")
                if len(parts) >= 2:
                    return MediaTrackInfo(
                        title=" - ".join(parts[1:]).strip(),
                        artist=parts[0].strip(),
                        playback_status="Playing",
                        source_app_id="Spotify.exe"
                    ), "spotify"

        return None

    def _find_media_window(self) -> Tuple[Optional[int], Optional[MediaTrackInfo]]:
        """Scans open windows to find an active media player window handle (HWND) and track info."""
        try:
            from core.screen_stream import ensure_thread_desktop
            ensure_thread_desktop()
        except Exception:
            pass

        try:
            import win32gui
            import win32process
            import psutil

            candidates = []

            def enum_cb(hwnd, _):
                try:
                    if win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                        title = win32gui.GetWindowText(hwnd) or ""
                        if title.strip():
                            _, pid = win32process.GetWindowThreadProcessId(hwnd)
                            proc_name = ""
                            try:
                                proc_name = psutil.Process(pid).name()
                            except Exception:
                                pass

                            res = self._parse_media_title(title, proc_name)
                            if res:
                                info, kind = res
                                candidates.append((hwnd, info, kind, 0))
                            elif "youtube music" in title.lower() or "spotify" in title.lower():
                                kind = "youtube_music" if "youtube music" in title.lower() else "spotify"
                                candidates.append((hwnd, None, kind, 1))
                except Exception:
                    pass
                return True  # Crucial: Always return True to avoid pywintypes error 122

            try:
                win32gui.EnumWindows(enum_cb, None)
            except Exception:
                pass

            if candidates:
                # Prioritize entries with full track info first, then YouTube Music / Spotify
                candidates.sort(key=lambda c: (c[3], 0 if c[2] in ("youtube_music", "spotify") else 1))
                best = candidates[0]
                return best[0], best[1]

            return None, None
        except Exception as e:
            logger.debug(f"[MEDIA] Failed to scan window media: {e}")
            return None, None

    def _get_window_title_media_info(self) -> Optional[MediaTrackInfo]:
        """Fallback to inspect window titles (e.g. Chrome YouTube Music, Spotify desktop)."""
        _, info = self._find_media_window()
        return info

    def _send_media_key(self, action: str) -> bool:
        """
        Sends hardware Windows multimedia extended keystroke via SendInput (matching physical
        multimedia keyboards like Razer/Logitech with KEYEVENTF_EXTENDEDKEY).
        """
        try:
            import ctypes
            from ctypes import wintypes

            try:
                from core.screen_stream import ensure_thread_desktop
                ensure_thread_desktop()
            except Exception:
                pass

            # Virtual-Key codes
            VK_MEDIA_NEXT_TRACK = 0xB0
            VK_MEDIA_PREV_TRACK = 0xB1
            VK_MEDIA_STOP = 0xB2
            VK_MEDIA_PLAY_PAUSE = 0xB3

            key_map = {
                "play": VK_MEDIA_PLAY_PAUSE,
                "pause": VK_MEDIA_PLAY_PAUSE,
                "toggle": VK_MEDIA_PLAY_PAUSE,
                "toggle_play_pause": VK_MEDIA_PLAY_PAUSE,
                "resume": VK_MEDIA_PLAY_PAUSE,
                "next": VK_MEDIA_NEXT_TRACK,
                "skip": VK_MEDIA_NEXT_TRACK,
                "next_track": VK_MEDIA_NEXT_TRACK,
                "previous": VK_MEDIA_PREV_TRACK,
                "prev": VK_MEDIA_PREV_TRACK,
                "previous_track": VK_MEDIA_PREV_TRACK,
                "stop": VK_MEDIA_STOP,
            }
            vk = key_map.get(action.lower().strip())
            if not vk:
                return False

            ULONG_PTR = ctypes.c_size_t
            INPUT_KEYBOARD = 1
            KEYEVENTF_EXTENDEDKEY = 0x0001
            KEYEVENTF_KEYUP = 0x0002

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [
                    ("wVk", wintypes.WORD),
                    ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR),
                ]

            class _INPUT_UNION(ctypes.Union):
                _fields_ = [
                    ("ki", KEYBDINPUT),
                    ("padding", ctypes.c_byte * 32),
                ]

            class INPUT(ctypes.Structure):
                _anonymous_ = ("_u",)
                _fields_ = [
                    ("type", wintypes.DWORD),
                    ("_u", _INPUT_UNION),
                ]

            scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)

            # Key Down with EXTENDEDKEY flag
            down = INPUT()
            down.type = INPUT_KEYBOARD
            down.ki = KEYBDINPUT(vk, scan, KEYEVENTF_EXTENDEDKEY, 0, 0)

            # Key Up with EXTENDEDKEY | KEYUP flags
            up = INPUT()
            up.type = INPUT_KEYBOARD
            up.ki = KEYBDINPUT(vk, scan, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0, 0)

            inputs = (INPUT * 2)(down, up)
            sent = ctypes.windll.user32.SendInput(2, inputs, ctypes.sizeof(INPUT))
            if sent == 2:
                logger.info(f"[MEDIA] SendInput dispatched extended media key 0x{vk:X} (scan 0x{scan:X})")
                return True
            else:
                logger.warning(f"[MEDIA] SendInput failed (sent={sent}, err={ctypes.GetLastError()})")
                return False
        except Exception as e:
            logger.warning(f"[MEDIA] Hardware media key dispatch failed: {e}")
            return False

    def _send_media_command(self, action: str, target_hwnd: Optional[int] = None) -> bool:
        """
        Sends transport command directly to target media window using WM_APPCOMMAND,
        and broadcasts multimedia virtual key.
        """
        action = action.lower().strip()
        WM_APPCOMMAND = 0x0319
        APPCOMMAND_MEDIA_NEXTTRACK = 11
        APPCOMMAND_MEDIA_PREVTRACK = 12
        APPCOMMAND_MEDIA_STOP = 13
        APPCOMMAND_MEDIA_PLAY_PAUSE = 14

        app_cmd_map = {
            "play": APPCOMMAND_MEDIA_PLAY_PAUSE,
            "pause": APPCOMMAND_MEDIA_PLAY_PAUSE,
            "toggle": APPCOMMAND_MEDIA_PLAY_PAUSE,
            "toggle_play_pause": APPCOMMAND_MEDIA_PLAY_PAUSE,
            "resume": APPCOMMAND_MEDIA_PLAY_PAUSE,
            "next": APPCOMMAND_MEDIA_NEXTTRACK,
            "skip": APPCOMMAND_MEDIA_NEXTTRACK,
            "next_track": APPCOMMAND_MEDIA_NEXTTRACK,
            "previous": APPCOMMAND_MEDIA_PREVTRACK,
            "prev": APPCOMMAND_MEDIA_PREVTRACK,
            "previous_track": APPCOMMAND_MEDIA_PREVTRACK,
            "stop": APPCOMMAND_MEDIA_STOP,
        }
        cmd = app_cmd_map.get(action)

        dispatched = False
        # 1. Direct window messaging via WM_APPCOMMAND if window handle is known
        if target_hwnd and cmd:
            try:
                import win32gui
                l_param = cmd << 16
                win32gui.PostMessage(target_hwnd, WM_APPCOMMAND, target_hwnd, l_param)
                logger.info(f"[MEDIA] Dispatched WM_APPCOMMAND (0x{cmd:X}) to HWND {target_hwnd}")
                dispatched = True
            except Exception as e:
                logger.warning(f"[MEDIA] Direct WM_APPCOMMAND to HWND {target_hwnd} failed: {e}")

        # 2. Always broadcast hardware media virtual key
        key_sent = self._send_media_key(action)
        return dispatched or key_sent

    def _get_action_success_msg(self, action: str) -> str:
        action = action.lower().strip()
        if action in ("play", "resume"):
            return "Resumed playback."
        elif action == "pause":
            return "Paused playback."
        elif action in ("toggle", "toggle_play_pause"):
            return "Toggled playback."
        elif action in ("next", "skip", "next_track"):
            return "Skipped to next track."
        elif action in ("previous", "prev", "previous_track"):
            return "Returned to previous track."
        return f"Executed {action}."

    # --- Metadata Query ---
    async def get_current_media_info(self) -> Optional[MediaTrackInfo]:
        """Retrieves current track title, artist, album, and playback status."""
        # 1. Primary: Try WinRT SMTC session
        if self.is_supported():
            async with self._lock:
                try:
                    session = await self._resolve_session()
                    if session:
                        playback_info = session.get_playback_info()
                        status_str = "Unknown"
                        if playback_info:
                            status_str = self._parse_playback_status(playback_info.playback_status)

                        media_props = await session.try_get_media_properties_async()
                        if media_props and (media_props.title or "").strip():
                            app_id = ""
                            try:
                                app_id = session.source_app_user_model_id or ""
                            except Exception:
                                pass

                            return MediaTrackInfo(
                                title=media_props.title or "Unknown Title",
                                artist=media_props.artist or "Unknown Artist",
                                album_title=media_props.album_title or "",
                                playback_status=status_str,
                                source_app_id=app_id
                            )
                except Exception as e:
                    logger.debug(f"[MEDIA] WinRT query failed: {e}")

        # 2. Fallback: Inspect open window titles (YouTube Music PWA/Chrome, Spotify, etc.)
        window_info = self._get_window_title_media_info()
        if window_info:
            return window_info

        return None

    # --- Transport Commands ---
    async def execute_transport_command(self, action: str, fallback_to_media_key: bool = True) -> Tuple[bool, str]:
        """
        Executes a playback transport action:
        action in ('play', 'pause', 'toggle', 'next', 'previous')
        Returns (success: bool, status_message: str)
        """
        action = action.lower().strip()
        msg = self._get_action_success_msg(action)

        if not self.is_supported() and not fallback_to_media_key:
            return False, "WinRT media controls are not available on this platform."

        # 1. Try WinRT SMTC session if available
        if self.is_supported():
            async with self._lock:
                session = await self._resolve_session()
                if session:
                    success = False
                    try:
                        if action in ("play", "resume"):
                            success = await session.try_play_async()
                        elif action == "pause":
                            success = await session.try_pause_async()
                        elif action in ("toggle", "toggle_play_pause"):
                            success = await session.try_toggle_play_pause_async()
                        elif action in ("next", "skip", "next_track"):
                            success = await session.try_skip_next_async()
                        elif action in ("previous", "prev", "previous_track"):
                            success = await session.try_skip_previous_async()
                        else:
                            return False, f"Unsupported media command: '{action}'."

                        if success:
                            logger.info(f"[MEDIA] Command '{action}' succeeded via WinRT SMTC.")
                            return True, msg
                    except Exception as e:
                        logger.debug(f"[MEDIA] SMTC transport failed: {e}")

        # 2. Fallback: Direct WM_APPCOMMAND to target media window + hardware media key
        if fallback_to_media_key:
            target_hwnd, _ = self._find_media_window()
            if self._send_media_command(action, target_hwnd=target_hwnd):
                logger.info(f"[MEDIA] Command '{action}' succeeded via direct media command (HWND: {target_hwnd}).")
                return True, msg

        return False, "No active media playback session detected."

