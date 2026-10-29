"""
core/config.py - Settings and Credentials Management for Aether
Provides persistence and retrieval of application settings and integration
credentials (e.g. Steam Web API) with environment variable fallbacks.
"""

import copy
import json
import os
from typing import Any, Dict, Optional

# Re-export config_manager symbols for compatibility
try:
    from core.config_manager import (
        DEFAULT_CURATED_MODELS,
        DEFAULT_VOICE_CONFIG,
        ConfigManager,
        load_config,
        save_config,
        save_config_atomic,
        sync_config_schema,
    )
except ImportError:
    pass

SETTINGS_PATH = os.path.join("data", "settings.json")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "integrations": {
        "steam": {
            "enabled": True,
            "api_key": "",
            "steam_id64": "",
            "auto_detect_active_appid": True,
            "poll_friends_status": False,
            "poll_interval_minutes": 5,
        },
        "desktop_notifications": {
            "enabled": True,
            "poll_interval_seconds": 1.5,
            "target_apps": [
                "Discord",
                "Slack",
                "Microsoft Teams",
                "WhatsApp",
                "Telegram",
            ],
            "include_whitelist_apps": True,
            "verbal_announcement": False,
            "ignore_empty": True,
        },
        "streaming": {
            "enabled": True,
            "poll_interval_seconds": 120,
            "verbal_alerts": False,
            "twitch": {
                "enabled": True,
                "client_id": "",
                "client_secret": "",
                "channels": ["shroud", "cohhcarnage"],
            },
            "youtube": {
                "enabled": True,
                "api_key": "",
                "channels": [
                    {
                        "name": "Team YouTube",
                        "channel_id": "UCBR8-60-B28hp2BmDPdntcQ",
                    }
                ],
            },
        },
        "replay_capture": {
            "enabled": True,
            "backend": "shadowplay",
            "spoken_confirmation": "Captured.",
            "obs": {
                "host": "localhost",
                "port": 4455,
                "password": "",
            },
        },
    },
    "audio_ducking": {
        "enabled": True,
        "duck_percentage": 60,
        "target_mode": "all_except_aether",
        "restore_delay_ms": 150,
    },
}


class Settings(dict):
    """
    Settings dictionary wrapper with fallback to environment variables
    and convenient accessors for nested integration credentials.
    """

    def get(self, key: str, default: Any = None) -> Any:
        val = super().get(key, None)
        if val is not None and val != "":
            return val

        # Dynamic fallback for Steam credentials
        if key == "steam_api_key":
            steam_cfg = self.get("integrations", {}).get("steam", {})
            api_key = steam_cfg.get("api_key", "")
            return api_key or os.getenv("STEAM_API_KEY", "") or default
        elif key == "steam_id64":
            steam_cfg = self.get("integrations", {}).get("steam", {})
            steam_id64 = steam_cfg.get("steam_id64", "")
            return steam_id64 or os.getenv("STEAM_ID64", "") or default

        return default if val is None else val


def load_settings(settings_path: Optional[str] = None) -> Settings:
    """Loads settings from data/settings.json, falling back to defaults and env vars."""
    if settings_path is None:
        settings_path = SETTINGS_PATH
    settings_data: Dict[str, Any] = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings_data = json.load(f)
        except Exception as e:
            print(f"[WARN] [CONFIG] Failed reading {settings_path}: {e}")
            settings_data = {}
    else:
        settings_data = copy.deepcopy(DEFAULT_SETTINGS)
        save_settings(settings_data, settings_path)

    # Ensure nested integrations.steam structure is populated
    integrations = settings_data.setdefault("integrations", {})
    steam_cfg = integrations.setdefault("steam", {})
    steam_cfg.setdefault("enabled", True)
    steam_cfg.setdefault("api_key", os.getenv("STEAM_API_KEY", ""))
    steam_cfg.setdefault("steam_id64", os.getenv("STEAM_ID64", ""))
    steam_cfg.setdefault("auto_detect_active_appid", True)
    steam_cfg.setdefault("poll_friends_status", False)
    steam_cfg.setdefault("poll_interval_minutes", 5)

    # Top-level helper accessors
    if "steam_api_key" not in settings_data:
        settings_data["steam_api_key"] = steam_cfg.get("api_key") or os.getenv("STEAM_API_KEY", "")
    if "steam_id64" not in settings_data:
        settings_data["steam_id64"] = steam_cfg.get("steam_id64") or os.getenv("STEAM_ID64", "")

    # Ensure nested integrations.desktop_notifications structure is populated
    notif_cfg = integrations.setdefault("desktop_notifications", {})
    notif_cfg.setdefault("enabled", True)
    notif_cfg.setdefault("poll_interval_seconds", 1.5)
    notif_cfg.setdefault("target_apps", [
        "Discord",
        "Slack",
        "Microsoft Teams",
        "WhatsApp",
        "Telegram",
    ])
    notif_cfg.setdefault("include_whitelist_apps", True)
    notif_cfg.setdefault("verbal_announcement", False)
    notif_cfg.setdefault("ignore_empty", True)

    # Ensure nested integrations.streaming structure is populated
    stream_cfg = integrations.setdefault("streaming", {})
    stream_cfg.setdefault("enabled", True)
    stream_cfg.setdefault("poll_interval_seconds", 120)
    stream_cfg.setdefault("verbal_alerts", False)
    tw_cfg = stream_cfg.setdefault("twitch", {})
    tw_cfg.setdefault("enabled", True)
    tw_cfg.setdefault("client_id", os.getenv("TWITCH_CLIENT_ID", ""))
    tw_cfg.setdefault("client_secret", os.getenv("TWITCH_CLIENT_SECRET", ""))
    tw_cfg.setdefault("channels", ["shroud", "cohhcarnage"])
    yt_cfg = stream_cfg.setdefault("youtube", {})
    yt_cfg.setdefault("enabled", True)
    yt_cfg.setdefault("api_key", os.getenv("YOUTUBE_API_KEY", ""))
    yt_cfg.setdefault("channels", [
        {
            "name": "Team YouTube",
            "channel_id": "UCBR8-60-B28hp2BmDPdntcQ"
        }
    ])

    # Ensure nested integrations.replay_capture structure is populated
    replay_cfg = integrations.setdefault("replay_capture", {})
    replay_cfg.setdefault("enabled", True)
    replay_cfg.setdefault("backend", "shadowplay")
    replay_cfg.setdefault("spoken_confirmation", "Captured.")
    obs_cfg = replay_cfg.setdefault("obs", {})
    obs_cfg.setdefault("host", "localhost")
    obs_cfg.setdefault("port", 4455)
    obs_cfg.setdefault("password", "")

    # Ensure audio_ducking structure is populated
    duck_cfg = settings_data.setdefault("audio_ducking", {})
    duck_cfg.setdefault("enabled", True)
    duck_cfg.setdefault("duck_percentage", 60)
    duck_cfg.setdefault("target_mode", "all_except_aether")
    duck_cfg.setdefault("restore_delay_ms", 150)

    return Settings(settings_data)


def save_settings(settings: Dict[str, Any], settings_path: Optional[str] = None) -> None:
    """Safely saves settings dictionary to JSON file atomically."""
    if settings_path is None:
        settings_path = SETTINGS_PATH
    dirname = os.path.dirname(settings_path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    tmp_path = f"{settings_path}.{os.getpid()}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        os.replace(tmp_path, settings_path)
    except Exception as e:
        print(f"[WARN] [CONFIG] Failed saving {settings_path}: {e}")
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

