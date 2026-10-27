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
        }
    }
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


def load_settings(settings_path: str = SETTINGS_PATH) -> Settings:
    """Loads settings from data/settings.json, falling back to defaults and env vars."""
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

    # Top-level helper accessors
    if "steam_api_key" not in settings_data:
        settings_data["steam_api_key"] = steam_cfg.get("api_key") or os.getenv("STEAM_API_KEY", "")
    if "steam_id64" not in settings_data:
        settings_data["steam_id64"] = steam_cfg.get("steam_id64") or os.getenv("STEAM_ID64", "")

    return Settings(settings_data)


def save_settings(settings: Dict[str, Any], settings_path: str = SETTINGS_PATH) -> None:
    """Safely saves settings dictionary to JSON file atomically."""
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

