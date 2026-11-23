"""
Aether Desktop - Game Profile Infrastructure & DirectInput Hardware Action Dispatcher
Manages game profiles, metadata, trigger phrases, custom macros, active game profile switching,
and automatic keybind discovery (including Elite Dangerous XML binds scanner and heuristic config sniffer).
"""

import os
import glob
import json
import copy
import re
import random
import time
import uuid
import sys
import threading
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional, List, Callable

from core.telemetry import create_telemetry_adapter, BaseTelemetryAdapter
from core.binds_scanners import scan_profile_bindings
from core.game_macro_runner import MacroRunner
from core.paths import user_data_path

GAMES_DIR = user_data_path("data", "games")
GAMES_STATE_FILE = "active_state.json"
GAMES_PROFILES_PATH = user_data_path("data", "games_profiles.json")


def _get_paired_executables(proc_name: str) -> List[str]:
    """
    Dynamically resolves sibling launcher/client executables from all installed `.aethermodule.json`
    `executables` lists (zero hardcoded game/launcher pairs in Python).
    """
    clean = os.path.basename(str(proc_name or "").strip()).lower()
    if not clean:
        return []
    stem = os.path.splitext(clean)[0]
    peers: List[str] = []
    try:
        from core.game_modules import load_all_game_modules
        seen_mods = set()
        for mod in load_all_game_modules().values():
            mid = mod.get("module_id")
            if not mid or mid in seen_mods:
                continue
            seen_mods.add(mid)
            exes = [os.path.basename(str(e)).lower() for e in (mod.get("executables") or []) if str(e).strip()]
            stems = [os.path.splitext(e)[0] for e in exes]
            if clean in exes or (stem and stem in stems):
                for e in exes:
                    if e != clean and e not in peers:
                        peers.append(e)
    except Exception:
        pass
    return peers

DEFAULT_PROFILES = {
    "active_profile": "",
    "profiles": {}
}


def is_current_process_elevated() -> bool:
    """Returns True if Aether itself is running with Administrator privileges on Windows."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def is_pid_elevated(pid: int) -> Optional[bool]:
    """
    Checks whether a target process PID is running elevated (Admin).
    Returns True/False, or None if indeterminate. When Aether is non-elevated,
    OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) returning Access Denied (5)
    against a user-session game process also indicates higher privilege / UIPI isolation.
    """
    if sys.platform != "win32" or not pid:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        TOKEN_QUERY = 0x0008
        TokenElevation = 20

        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32

        h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h_proc:
            err = kernel32.GetLastError()
            # Access Denied from non-elevated Aether -> target process is elevated/protected
            if err == 5 and not is_current_process_elevated():
                return True
            return None

        try:
            h_tok = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(h_proc, TOKEN_QUERY, ctypes.byref(h_tok)):
                err = kernel32.GetLastError()
                if err == 5 and not is_current_process_elevated():
                    return True
                return None
            try:
                elev = wintypes.DWORD(0)
                ret_len = wintypes.DWORD(0)
                ok = advapi32.GetTokenInformation(
                    h_tok, TokenElevation, ctypes.byref(elev), ctypes.sizeof(elev), ctypes.byref(ret_len)
                )
                return bool(elev.value) if ok else None
            finally:
                kernel32.CloseHandle(h_tok)
        finally:
            kernel32.CloseHandle(h_proc)
    except Exception:
        return None


def _save_atomic(file_path: str, data: Any):
    """Atomically writes JSON data to disk using process-specific temp file."""
    dirname = os.path.dirname(file_path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    tmp_path = f"{file_path}.{os.getpid()}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, file_path)
    except Exception as e:
        print(f"[WARN] [GAME_MGR] Failed saving {file_path}: {e}")
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


class GameManager:
    """Manages game profiles, bindings synchronization, and voice/trigger macro execution."""

    def __init__(self, profiles_path: Optional[str] = None, games_dir: Optional[str] = None):
        self._lock = threading.RLock()
        if profiles_path and profiles_path != GAMES_PROFILES_PATH:
            self.is_dir_mode = False
            self.profiles_path = profiles_path
            self.games_dir = ""
        else:
            self.is_dir_mode = True
            self.games_dir = games_dir or GAMES_DIR
            self.profiles_path = profiles_path or GAMES_PROFILES_PATH

        self.data = self._load_profiles()
        self.active_telemetry_adapter: Optional[BaseTelemetryAdapter] = None
        self.macro_runner = MacroRunner()
        self._init_telemetry_watchers()

        # Dedicated Game Mode state & automatic foreground window watcher
        self.game_mode_enabled: bool = False
        self.user_override_game_mode: Optional[bool] = None  # None = auto, True = explicit on, False = explicit off
        self.on_game_mode_changed: Optional[Callable[..., None]] = None
        self.on_game_added: Optional[Callable[[str, Dict[str, Any]], None]] = None
        self._foreground_watcher_thread: Optional[threading.Thread] = None
        self._stop_foreground_watcher = threading.Event()
        self._last_detected_game: Optional[str] = None

        # Check if a configured game is already running at startup
        detected = self.detect_foreground_game() or self.detect_running_game()
        if detected:
            self.data["active_profile"] = detected
            self.game_mode_enabled = True
            self._last_detected_game = detected
        else:
            self.game_mode_enabled = False
            self._last_detected_game = None

    @property
    def ed_watcher(self) -> Optional[BaseTelemetryAdapter]:
        """Backward-compatibility alias for self.active_telemetry_adapter."""
        return self.active_telemetry_adapter

    @ed_watcher.setter
    def ed_watcher(self, val: Optional[BaseTelemetryAdapter]):
        """Backward-compatibility setter for self.active_telemetry_adapter."""
        self.active_telemetry_adapter = val

    def _init_telemetry_watchers(self):
        """Initializes and starts background telemetry adapter for the currently active game."""
        active_id = self.data.get("active_profile", "")
        self._update_telemetry_adapter(active_id)

    def _update_telemetry_adapter(self, game_id: str):
        """Switches or restarts the telemetry adapter for the specified game."""
        if self.active_telemetry_adapter:
            try:
                self.active_telemetry_adapter.stop()
            except Exception:
                pass
            self.active_telemetry_adapter = None

        if not game_id:
            return

        prof = self.get_profile(game_id)
        if not prof:
            return

        def on_milestone(cat: str, summary: str, loc: str = "", det: str = ""):
            self.add_copilot_log_entry(
                game_id=game_id,
                summary=summary,
                category=cat,
                location=loc,
                details=det
            )

        self.active_telemetry_adapter = create_telemetry_adapter(
            game_id=game_id,
            profile=prof,
            on_milestone=on_milestone
        )
        self.active_telemetry_adapter.start()

    def _migrate_monolithic_to_dir(self) -> Dict[str, Any]:
        """Migrates monolithic games_profiles.json to individual data/games/<id>.json files."""
        os.makedirs(self.games_dir, exist_ok=True)
        try:
            with open(self.profiles_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[WARN] [GAME_MGR] Migration read error for {self.profiles_path}: {e}")
            data = copy.deepcopy(DEFAULT_PROFILES)

        active = data.get("active_profile", "")
        profiles = data.get("profiles", {})

        state_path = os.path.join(self.games_dir, GAMES_STATE_FILE)
        _save_atomic(state_path, {"active_profile": active})

        for gid, prof in profiles.items():
            gpath = os.path.join(self.games_dir, f"{gid}.json")
            _save_atomic(gpath, prof)

        print(f"[INFO] [GAME_MGR] Successfully migrated {len(profiles)} game profile(s) to {self.games_dir}/")
        return {"active_profile": active, "profiles": profiles}

    def _load_profiles(self) -> Dict[str, Any]:
        """Loads profiles from data/games/*.json or migrates from games_profiles.json."""
        if not self.is_dir_mode:
            if os.path.exists(self.profiles_path):
                try:
                    with open(self.profiles_path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception as e:
                    print(f"[WARN] [GAME_MGR] Failed reading {self.profiles_path}: {e}")
            initial = copy.deepcopy(DEFAULT_PROFILES)
            _save_atomic(self.profiles_path, initial)
            return initial

        # Directory mode: check if migration needed
        if not os.path.exists(self.games_dir) or not any(f.endswith(".json") and f != GAMES_STATE_FILE for f in os.listdir(self.games_dir)):
            if os.path.exists(self.profiles_path):
                return self._migrate_monolithic_to_dir()

        os.makedirs(self.games_dir, exist_ok=True)
        profiles = {}
        active_profile = ""
        state_path = os.path.join(self.games_dir, GAMES_STATE_FILE)
        if os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    sdata = json.load(f)
                    active_profile = sdata.get("active_profile", "")
            except Exception as e:
                print(f"[WARN] [GAME_MGR] Failed reading {state_path}: {e}")

        for fname in os.listdir(self.games_dir):
            if fname.endswith(".json") and fname != GAMES_STATE_FILE:
                fpath = os.path.join(self.games_dir, fname)
                gid = os.path.splitext(fname)[0]
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        prof = json.load(f)
                        if isinstance(prof, dict):
                            profiles[gid] = prof
                except Exception as e:
                    print(f"[WARN] [GAME_MGR] Failed loading profile {fpath}: {e}")

        if not active_profile and profiles:
            active_profile = list(profiles.keys())[0]

        return {
            "active_profile": active_profile,
            "profiles": profiles
        }

    def save_profiles(self):
        """Persists all profiles to disk."""
        with self._lock:
            self._save_profiles(self.data)

    def save_profile(self, game_id: str):
        """Persists a single game's profile to disk."""
        with self._lock:
            prof = self.get_profile(game_id)
            if not prof:
                return
            if self.is_dir_mode:
                gpath = os.path.join(self.games_dir, f"{game_id}.json")
                _save_atomic(gpath, prof)
            else:
                self._save_profiles(self.data)

    def _save_profiles(self, payload: Dict[str, Any], target_game_id: Optional[str] = None):
        """Persists profiles to disk. In dir mode, writes individual files."""
        with self._lock:
            if not self.is_dir_mode:
                _save_atomic(self.profiles_path, payload)
                return

            os.makedirs(self.games_dir, exist_ok=True)
            state_path = os.path.join(self.games_dir, GAMES_STATE_FILE)
            _save_atomic(state_path, {"active_profile": payload.get("active_profile", "")})

            profiles = payload.get("profiles", {})
            if target_game_id and target_game_id in profiles:
                gpath = os.path.join(self.games_dir, f"{target_game_id}.json")
                _save_atomic(gpath, profiles[target_game_id])
            else:
                for gid, prof in profiles.items():
                    gpath = os.path.join(self.games_dir, f"{gid}.json")
                    _save_atomic(gpath, prof)

    def add_game(
        self,
        display_name: str,
        process_name: str = "",
        telemetry_type: str = "scratchpad_only",
        keybinds: Optional[Dict] = None,
        scratchpad: Optional[Dict] = None,
        bindings_dir: str = "",
        config_dir: str = ""
    ) -> dict:
        """Creates a new game profile and switches to it if no active game is set."""
        name_clean = display_name.strip()
        if not name_clean:
            return {"success": False, "error": "Game name cannot be empty."}

        base_id = re.sub(r'[^a-zA-Z0-9]+', '_', name_clean.lower()).strip('_') or "game"
        game_id = base_id
        counter = 2
        while game_id in self.data.get("profiles", {}):
            game_id = f"{base_id}_{counter}"
            counter += 1

        default_nav_mode = "controller"
        default_map_controls = {
            "preferred_input": "gamepad",
            "pan_method": "left_drag",
            "marker_method": "right_click",
            "zoom_method": "mouse_wheel"
        }
        try:
            from core.game_modules import get_module_for_game
            mod = get_module_for_game(game_id, profile={"process_name": process_name.strip()})
            if isinstance(mod, dict):
                if mod.get("navigation_mode") in ("controller", "co_pilot_spotter", "search_bar", "mouse_keyboard"):
                    default_nav_mode = mod["navigation_mode"]
                if isinstance(mod.get("map_controls"), dict) and mod["map_controls"]:
                    default_map_controls.update(mod["map_controls"])
        except Exception:
            pass

        profile = {
            "display_name": name_clean,
            "process_name": process_name.strip(),
            "telemetry_type": telemetry_type or "scratchpad_only",
            "guidance_level": "tactical",
            "navigation_mode": default_nav_mode,
            "map_controls": default_map_controls,
            "scratchpad": scratchpad if scratchpad is not None else {
                "active_quests": [],
                "crafting_materials": [],
                "general_notes": []
            },
            "scratchpad_raw": "",
            "copilot_log": [],
            "keybinds": keybinds if keybinds is not None else {}
        }
        if bindings_dir:
            profile["bindings_dir"] = bindings_dir
        if config_dir:
            profile["config_dir"] = config_dir

        self.data.setdefault("profiles", {})[game_id] = profile
        if not self.data.get("active_profile"):
            self.data["active_profile"] = game_id

        if self.data.get("active_profile") == game_id:
            self._update_telemetry_adapter(game_id)

        self._save_profiles(self.data)
        if callable(self.on_game_added):
            try:
                self.on_game_added(game_id, profile)
            except Exception as e:
                print(f"[WARN] [GAME_MGR] on_game_added callback error: {e}")

        return {
            "success": True,
            "game_id": game_id,
            "profile": profile,
            "active_profile": self.data.get("active_profile")
        }

    def delete_game(self, game_id: str) -> dict:
        """Deletes a game profile from memory and disk."""
        profiles = self.data.get("profiles", {})
        if game_id in profiles:
            del profiles[game_id]
            if self.data.get("active_profile") == game_id:
                remaining = list(profiles.keys())
                new_active = remaining[0] if remaining else ""
                self.data["active_profile"] = new_active
                self._update_telemetry_adapter(new_active)

            if self.is_dir_mode:
                gpath = os.path.join(self.games_dir, f"{game_id}.json")
                if os.path.exists(gpath):
                    try:
                        os.remove(gpath)
                    except Exception as e:
                        print(f"[WARN] [GAME_MGR] Failed removing {gpath}: {e}")
                state_path = os.path.join(self.games_dir, GAMES_STATE_FILE)
                _save_atomic(state_path, {"active_profile": self.data.get("active_profile", "")})
                if os.path.exists(self.profiles_path):
                    try:
                        with open(self.profiles_path, "r", encoding="utf-8") as pf:
                            gp = json.load(pf)
                        if isinstance(gp, dict) and game_id in gp:
                            del gp[game_id]
                            _save_atomic(self.profiles_path, gp)
                    except Exception:
                        pass
            else:
                self._save_profiles(self.data)

            return {
                "success": True,
                "deleted_id": game_id,
                "active_profile": self.data.get("active_profile"),
                "remaining_count": len(profiles)
            }
        return {"success": False, "error": f"Game profile '{game_id}' not found."}

    def get_profile(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves profile dictionary for specified game ID."""
        return self.data.get("profiles", {}).get(game_id)

    def get_active_profile(self) -> Optional[Dict[str, Any]]:
        """Returns the profile dictionary for the currently active game."""
        active_id = self.data.get("active_profile")
        if not active_id:
            return None
        return self.get_profile(active_id)

    def set_active_profile(self, game_id: str) -> bool:
        """Switches the currently active game profile."""
        if not game_id:
            self.data["active_profile"] = ""
            self.game_mode_enabled = False
            self.user_override_game_mode = None
            self._update_telemetry_adapter("")
            self._save_profiles(self.data)
            self._notify_mode_changed()
            return True
        if game_id in self.data.get("profiles", {}):
            self.data["active_profile"] = game_id
            if self.user_override_game_mode is not False:
                self.game_mode_enabled = True
                self.user_override_game_mode = True
            self._update_telemetry_adapter(game_id)
            self._save_profiles(self.data)
            self._notify_mode_changed()
            return True
        return False

    def get_guidance_level(self, game_id: Optional[str] = None) -> str:
        """Retrieves guidance level for the game ('companion', 'tactical', or 'walkthrough'). Defaults to 'tactical'."""
        gid = game_id or self.data.get("active_profile")
        if not gid:
            return "tactical"
        profile = self.get_profile(gid)
        if not profile:
            return "tactical"
        level = (profile.get("guidance_level") or "tactical").lower().strip()
        return level if level in ("companion", "tactical", "walkthrough") else "tactical"

    def set_guidance_level(self, game_id: str, level: str) -> dict:
        """Sets the guidance level for the specified game profile."""
        norm_level = (level or "").lower().strip()
        if norm_level not in ("companion", "tactical", "walkthrough"):
            return {
                "success": False,
                "error": f"Invalid guidance level '{level}'. Must be 'companion', 'tactical', or 'walkthrough'."
            }
        profile = self.get_profile(game_id)
        if not profile:
            return {"success": False, "error": f"Game profile '{game_id}' not found."}
        profile["guidance_level"] = norm_level
        self._save_profiles(self.data)
        return {"success": True, "game_id": game_id, "guidance_level": norm_level}

    def get_navigation_mode(self, game_id: Optional[str] = None) -> str:
        """Retrieves map navigation mode ('controller', 'co_pilot_spotter', 'search_bar', 'mouse_keyboard'). Checks profile then module."""
        gid = game_id or self.data.get("active_profile")
        if not gid:
            return "controller"
        profile = self.get_profile(gid)
        if not profile:
            return "controller"
        valid_modes = ("controller", "co_pilot_spotter", "search_bar", "mouse_keyboard")
        raw_prof_mode = str(profile.get("navigation_mode") or "").lower().strip()
        if raw_prof_mode in valid_modes:
            return raw_prof_mode
        try:
            from core.game_modules import get_module_for_game
            mod = get_module_for_game(gid, profile=profile)
            mod_mode = str((mod or {}).get("navigation_mode") or "").lower().strip()
            if mod_mode in valid_modes:
                return mod_mode
        except Exception:
            pass
        return "controller"

    def set_navigation_mode(self, game_id: str, mode: str) -> dict:
        """Sets the map navigation mode for the specified game profile."""
        norm_mode = (mode or "").lower().strip()
        valid_modes = ("controller", "co_pilot_spotter", "search_bar", "mouse_keyboard")
        if norm_mode not in valid_modes:
            return {
                "success": False,
                "error": f"Invalid navigation mode '{mode}'. Must be one of: {', '.join(valid_modes)}."
            }
        profile = self.get_profile(game_id)
        if not profile:
            return {"success": False, "error": f"Game profile '{game_id}' not found."}
        profile["navigation_mode"] = norm_mode
        map_ctrl = profile.setdefault("map_controls", {})
        if norm_mode == "controller":
            map_ctrl["preferred_input"] = "gamepad"
        elif norm_mode == "mouse_keyboard":
            map_ctrl["preferred_input"] = "mouse_keyboard"
        self._save_profiles(self.data)
        return {"success": True, "game_id": game_id, "navigation_mode": norm_mode}

    def get_input_mode(self, game_id: Optional[str] = None) -> str:
        """Retrieves input safety mode ('autonomous' or 'spotter_only'). Checks profile then module."""
        gid = game_id or self.data.get("active_profile")
        if not gid:
            return "autonomous"
        profile = self.get_profile(gid) or {}
        mode = str(profile.get("input_mode") or "").lower().strip()
        if mode in ("autonomous", "spotter_only"):
            return mode
        try:
            from core.game_modules import get_module_for_game
            mod = get_module_for_game(gid, profile=profile)
            if mod and str(mod.get("input_mode") or "").lower().strip() == "spotter_only":
                return "spotter_only"
        except Exception:
            pass
        return "autonomous"

    def set_input_mode(self, game_id: str, mode: str) -> dict:
        """Sets input safety mode ('autonomous' or 'spotter_only') for a game profile."""
        norm = (mode or "").lower().strip()
        if norm not in ("autonomous", "spotter_only"):
            return {"success": False, "error": f"Invalid input mode '{mode}'. Must be 'autonomous' or 'spotter_only'."}
        profile = self.get_profile(game_id)
        if not profile:
            return {"success": False, "error": f"Game profile '{game_id}' not found."}
        profile["input_mode"] = norm
        if norm == "spotter_only":
            profile["navigation_mode"] = "co_pilot_spotter"
        self._save_profiles(self.data)
        return {"success": True, "game_id": game_id, "input_mode": norm}

    ALLOWED_HUD_PREFERENCES = ("default", "none", "dot", "mini", "normal", "max")

    def get_hud_preference(self, game_id: Optional[str] = None) -> str:
        """Returns per-game HUD preference ('default', 'none', 'dot', 'mini', 'normal', 'max')."""
        gid = game_id or self.data.get("active_profile")
        if not gid:
            return "default"
        profile = self.get_profile(gid) or {}
        pref = str(profile.get("hud_preference") or "default").strip().lower()
        return pref if pref in self.ALLOWED_HUD_PREFERENCES else "default"

    def set_hud_preference(self, game_id: str, hud_pref: str) -> dict:
        """Sets per-game HUD preference ('default', 'none', 'dot', 'mini', 'normal', 'max')."""
        norm = str(hud_pref or "default").strip().lower()
        if norm not in self.ALLOWED_HUD_PREFERENCES:
            return {
                "success": False,
                "error": f"Invalid HUD preference '{hud_pref}'. Allowed: {', '.join(self.ALLOWED_HUD_PREFERENCES)}.",
            }
        profile = self.get_profile(game_id)
        if not profile:
            return {"success": False, "error": f"Game profile '{game_id}' not found."}
        profile["hud_preference"] = norm
        self._save_profiles(self.data)
        return {"success": True, "game_id": game_id, "hud_preference": norm}

    def get_suppress_alerts(self, game_id: Optional[str] = None) -> List[str]:
        """Returns 3rd-party co-pilot process names that suppress duplicate Aether voice alerts."""
        gid = game_id or self.data.get("active_profile")
        if not gid:
            return []
        profile = self.get_profile(gid) or {}
        if isinstance(profile.get("suppress_alerts_when_running"), list):
            return list(profile["suppress_alerts_when_running"])
        try:
            from core.game_modules import get_module_for_game
            mod = get_module_for_game(gid, profile=profile)
            if mod and isinstance(mod.get("suppress_alerts_when_running"), list):
                return list(mod["suppress_alerts_when_running"])
        except Exception:
            pass
        return []

    def set_suppress_alerts(self, game_id: str, processes: Any) -> dict:
        """Sets 3rd-party companion process names (list or comma/semicolon string) that suppress duplicate alerts."""
        profile = self.get_profile(game_id)
        if not profile:
            return {"success": False, "error": f"Game profile '{game_id}' not found."}
        if isinstance(processes, str):
            raw_list = [p.strip() for p in re.split(r"[,;]+", processes) if p.strip()]
        elif isinstance(processes, list):
            raw_list = [str(p).strip() for p in processes if str(p).strip()]
        else:
            raw_list = []
        clean_list = []
        seen_lower = set()
        for p in raw_list:
            base = os.path.basename(p)
            if base and base.lower() not in seen_lower:
                clean_list.append(base)
                seen_lower.add(base.lower())
        profile["suppress_alerts_when_running"] = clean_list

        # Keep companion_apps mute_aether_alerts flags in sync with manual edits
        apps = self._normalize_companion_apps(profile.get("companion_apps"))
        if apps:
            for app in apps:
                exe_low = os.path.basename(str(app.get("executable") or "")).lower()
                if exe_low:
                    app["mute_aether_alerts"] = exe_low in seen_lower
            profile["companion_apps"] = apps

        self._save_profiles(self.data)
        return {
            "success": True,
            "game_id": game_id,
            "suppress_alerts_when_running": clean_list,
            "companion_apps": apps,
        }

    # --- 3rd-Party Companion Apps Management (Game-Agnostic) ---

    @staticmethod
    def _normalize_companion_app_id(raw_id: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", str(raw_id or "").strip().lower()).strip("_")
        return slug

    def _normalize_companion_apps(self, raw: Any) -> List[Dict[str, Any]]:
        """
        Normalizes `profile['companion_apps']` into a clean list of dicts while
        remaining backward-compatible with legacy dict-based flight-deck entries.
        """
        items: List[Dict[str, Any]] = []
        seen_ids = set()

        def _append_entry(raw_key: str, entry: Dict[str, Any]):
            if not isinstance(entry, dict):
                return
            name = str(entry.get("name") or entry.get("display_name") or raw_key or "").strip()
            raw_path = str(entry.get("path") or "").strip()
            raw_exe = str(entry.get("executable") or "").strip()
            if not raw_path and raw_exe and (os.sep in raw_exe or "/" in raw_exe or ":" in raw_exe):
                raw_path = os.path.normpath(raw_exe)
            exe = os.path.basename(raw_exe or raw_path).strip()
            if not name and exe:
                name = os.path.splitext(exe)[0]
            if not name and not exe:
                return
            app_id = self._normalize_companion_app_id(entry.get("id") or raw_key or name or exe)
            if not app_id:
                app_id = f"app_{len(items) + 1}"
            base_id = app_id
            suffix = 2
            while app_id in seen_ids:
                app_id = f"{base_id}_{suffix}"
                suffix += 1
            seen_ids.add(app_id)

            norm: Dict[str, Any] = {
                "id": app_id,
                "name": name,
                "executable": exe,
                "path": raw_path,
                "purpose": str(entry.get("purpose") or "").strip(),
                "mute_aether_alerts": bool(entry.get("mute_aether_alerts", False)),
                "quiet_when_running": bool(entry.get("quiet_when_running", False)),
                "prompt_to_launch": bool(entry.get("prompt_to_launch", True)),
            }
            if entry.get("url"):
                norm["url"] = str(entry["url"]).strip()
            if entry.get("default_monitor") is not None:
                norm["default_monitor"] = entry["default_monitor"]
            items.append(norm)

        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, dict):
                    _append_entry("", entry)
        elif isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(v, dict):
                    _append_entry(str(k), v)

        return items

    def _sync_companion_mute_alerts(
        self,
        profile: Dict[str, Any],
        removed_muted_exes: Optional[List[str]] = None,
    ) -> List[str]:
        """Synchronizes companion app `mute_aether_alerts` toggles with `suppress_alerts_when_running`."""
        apps = self._normalize_companion_apps(profile.get("companion_apps"))
        profile["companion_apps"] = apps

        current = list(profile.get("suppress_alerts_when_running") or [])
        muted_by_apps = {
            os.path.basename(a["executable"]).lower(): os.path.basename(a["executable"])
            for a in apps
            if (a.get("mute_aether_alerts") or a.get("quiet_when_running")) and a.get("executable")
        }
        unmuted_by_apps = {
            os.path.basename(a["executable"]).lower()
            for a in apps
            if not (a.get("mute_aether_alerts") or a.get("quiet_when_running")) and a.get("executable")
        }
        for r_exe in (removed_muted_exes or []):
            r_low = os.path.basename(str(r_exe or "")).lower()
            if r_low:
                unmuted_by_apps.add(r_low)

        result: List[str] = []
        seen = set()
        for proc in current:
            base = os.path.basename(str(proc or "").strip())
            low = base.lower()
            if not base or low in seen:
                continue
            if low in unmuted_by_apps and low not in muted_by_apps:
                continue
            result.append(base)
            seen.add(low)

        for low, base in muted_by_apps.items():
            if low not in seen:
                result.append(base)
                seen.add(low)

        profile["suppress_alerts_when_running"] = result
        return result

    @staticmethod
    def _get_running_process_names_lower() -> set:
        """Returns a lowercase set of currently running process basenames."""
        try:
            import psutil

            return {
                (p.info.get("name") or "").strip().lower()
                for p in psutil.process_iter(["name"])
                if p.info.get("name")
            }
        except Exception:
            return set()

    def get_companion_apps(self, game_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns normalized 3rd-party companion apps configured for `game_id`."""
        gid = game_id or self.data.get("active_profile")
        if not gid:
            return []
        profile = self.get_profile(gid) or {}
        apps = self._normalize_companion_apps(profile.get("companion_apps"))
        if not apps:
            try:
                from core.game_modules import get_module_for_game
                mod = get_module_for_game(gid, profile=profile)
                if mod and mod.get("companion_apps"):
                    apps = self._normalize_companion_apps(mod.get("companion_apps"))
            except Exception:
                pass
        return apps

    def get_companion_apps_with_status(
        self,
        game_id: Optional[str] = None,
        running_procs: Optional[set] = None,
    ) -> List[Dict[str, Any]]:
        """Returns linked 3rd-party companion apps enriched with live `running: bool` status."""
        apps = self.get_companion_apps(game_id)
        if not apps:
            return []
        running_set = (
            {str(p).lower() for p in running_procs}
            if running_procs is not None
            else self._get_running_process_names_lower()
        )
        enriched = []
        for app in apps:
            item = dict(app)
            exe_low = os.path.basename(str(item.get("executable") or "")).lower()
            exe_stem = os.path.splitext(exe_low)[0] if exe_low else ""
            is_running = False
            if exe_low:
                is_running = (
                    exe_low in running_set
                    or (f"{exe_low}.exe" in running_set)
                    or (exe_stem and f"{exe_stem}.exe" in running_set)
                )
            item["running"] = bool(is_running)
            enriched.append(item)
        return enriched

    def get_missing_promptable_companion_apps(
        self,
        game_id: Optional[str] = None,
        running_procs: Optional[set] = None,
    ) -> List[Dict[str, Any]]:
        """Returns linked companion apps that have `prompt_to_launch=True` and are currently NOT running."""
        apps = self.get_companion_apps_with_status(game_id, running_procs=running_procs)
        return [
            a for a in apps
            if a.get("prompt_to_launch", True) and not a.get("running", False)
        ]

    def is_quiet_companion_running(
        self,
        game_id: Optional[str] = None,
        running_procs: Optional[set] = None,
    ) -> Optional[str]:
        """Returns the name of any running companion app with `quiet_when_running=True`, or None."""
        for app in self.get_companion_apps_with_status(game_id, running_procs=running_procs):
            if app.get("quiet_when_running") and app.get("running"):
                return str(app.get("name") or app.get("executable") or app.get("id"))
        return None

    def add_companion_app(
        self,
        game_id: str,
        name: str,
        executable_or_path: str = "",
        purpose: str = "",
        mute_aether_alerts: bool = False,
        prompt_to_launch: bool = True,
        quiet_when_running: bool = False,
    ) -> dict:
        """Links a 3rd-party companion program to a game profile."""
        profile = self.get_profile(game_id)
        if not profile:
            return {"success": False, "error": f"Game profile '{game_id}' not found."}

        raw_target = str(executable_or_path or "").strip().strip('"')
        full_path = ""
        if raw_target and (os.sep in raw_target or "/" in raw_target or ":" in raw_target):
            full_path = os.path.normpath(raw_target)
        exe_name = os.path.basename(raw_target).strip()
        if exe_name and "." not in exe_name and not raw_target.startswith(("http://", "https://")):
            exe_name = f"{exe_name}.exe"

        clean_name = str(name or "").strip()
        if not clean_name and exe_name:
            clean_name = os.path.splitext(exe_name)[0]
        if not clean_name:
            return {"success": False, "error": "Companion app name or executable is required."}

        apps = self._normalize_companion_apps(profile.get("companion_apps"))
        app_id = self._normalize_companion_app_id(clean_name or exe_name)
        existing_idx = None
        for idx, existing in enumerate(apps):
            if existing["id"] == app_id or (exe_name and existing.get("executable", "").lower() == exe_name.lower()):
                existing_idx = idx
                break

        entry = {
            "id": apps[existing_idx]["id"] if existing_idx is not None else app_id,
            "name": clean_name,
            "executable": exe_name,
            "path": full_path or (apps[existing_idx].get("path", "") if existing_idx is not None else ""),
            "purpose": str(purpose or "").strip(),
            "mute_aether_alerts": bool(mute_aether_alerts),
            "quiet_when_running": bool(quiet_when_running),
            "prompt_to_launch": bool(prompt_to_launch),
        }
        if existing_idx is not None:
            apps[existing_idx] = entry
        else:
            apps.append(entry)

        profile["companion_apps"] = apps
        suppress_list = self._sync_companion_mute_alerts(profile)
        self._save_profiles(self.data)
        return {
            "success": True,
            "game_id": game_id,
            "app": entry,
            "companion_apps": self.get_companion_apps_with_status(game_id),
            "suppress_alerts_when_running": suppress_list,
        }

    def update_companion_app(self, game_id: str, app_id: str, updates: Dict[str, Any]) -> dict:
        """Updates properties (mute_aether_alerts, prompt_to_launch, purpose, path, etc.) on a linked companion app."""
        profile = self.get_profile(game_id)
        if not profile:
            return {"success": False, "error": f"Game profile '{game_id}' not found."}
        apps = self._normalize_companion_apps(profile.get("companion_apps"))
        target = None
        norm_lookup = self._normalize_companion_app_id(app_id)
        for app in apps:
            if app["id"] == app_id or app["id"] == norm_lookup:
                target = app
                break
        if target is None:
            return {"success": False, "error": f"Companion app '{app_id}' not found."}

        if isinstance(updates, dict):
            if "name" in updates and str(updates["name"]).strip():
                target["name"] = str(updates["name"]).strip()
            if "purpose" in updates:
                target["purpose"] = str(updates["purpose"] or "").strip()
            if "path" in updates:
                raw_p = str(updates["path"] or "").strip().strip('"')
                target["path"] = os.path.normpath(raw_p) if raw_p else ""
                if raw_p and not target.get("executable"):
                    target["executable"] = os.path.basename(raw_p)
            if "executable" in updates and str(updates["executable"]).strip():
                target["executable"] = os.path.basename(str(updates["executable"]).strip())
            if "mute_aether_alerts" in updates:
                target["mute_aether_alerts"] = bool(updates["mute_aether_alerts"])
            if "quiet_when_running" in updates:
                target["quiet_when_running"] = bool(updates["quiet_when_running"])
            if "prompt_to_launch" in updates:
                target["prompt_to_launch"] = bool(updates["prompt_to_launch"])

        profile["companion_apps"] = apps
        suppress_list = self._sync_companion_mute_alerts(profile)
        self._save_profiles(self.data)
        return {
            "success": True,
            "game_id": game_id,
            "app": target,
            "companion_apps": self.get_companion_apps_with_status(game_id),
            "suppress_alerts_when_running": suppress_list,
        }

    def remove_companion_app(self, game_id: str, app_id: str) -> dict:
        """Removes a linked 3rd-party companion program from a game profile."""
        profile = self.get_profile(game_id)
        if not profile:
            return {"success": False, "error": f"Game profile '{game_id}' not found."}
        apps = self._normalize_companion_apps(profile.get("companion_apps"))
        norm_lookup = self._normalize_companion_app_id(app_id)
        removed_muted = []
        kept = []
        removed = False
        for app in apps:
            if app["id"] == app_id or app["id"] == norm_lookup:
                removed = True
                if (app.get("mute_aether_alerts") or app.get("quiet_when_running")) and app.get("executable"):
                    removed_muted.append(app["executable"])
            else:
                kept.append(app)
        if not removed:
            return {"success": False, "error": f"Companion app '{app_id}' not found."}

        profile["companion_apps"] = kept
        suppress_list = self._sync_companion_mute_alerts(profile, removed_muted_exes=removed_muted)
        self._save_profiles(self.data)
        return {
            "success": True,
            "game_id": game_id,
            "companion_apps": self.get_companion_apps_with_status(game_id),
            "suppress_alerts_when_running": suppress_list,
        }

    def launch_companion_app(self, app_id_or_name: str, game_id: Optional[str] = None) -> dict:
        """
        Safely launches a user-configured 3rd-party companion app linked to the game profile.
        Only launches apps explicitly present in `companion_apps` for security.
        """
        gid = game_id or self.data.get("active_profile")
        if not gid:
            return {"success": False, "error": "No active game profile selected."}
        apps = self.get_companion_apps_with_status(gid)
        if not apps:
            return {"success": False, "error": f"No 3rd-party companion apps configured for '{gid}'."}

        q_raw = str(app_id_or_name or "").strip().lower()
        q_slug = self._normalize_companion_app_id(q_raw)
        match = None
        for app in apps:
            a_id = app["id"].lower()
            a_name = app["name"].lower()
            a_exe = os.path.basename(app.get("executable") or "").lower()
            a_stem = os.path.splitext(a_exe)[0] if a_exe else ""
            if q_raw in (a_id, a_name, a_exe, a_stem) or (q_slug and q_slug == a_id):
                match = app
                break
        if match is None:
            for app in apps:
                if q_raw and (q_raw in app["name"].lower() or q_raw in app.get("executable", "").lower()):
                    match = app
                    break

        if match is None:
            available = ", ".join(a["name"] for a in apps)
            return {
                "success": False,
                "error": f"Companion app '{app_id_or_name}' is not linked to this game. Linked apps: {available}.",
            }

        if match.get("running"):
            return {
                "success": True,
                "already_running": True,
                "app": match,
                "message": f"{match['name']} is already running.",
            }

        import shutil
        import subprocess

        launch_target = ""
        saved_path = str(match.get("path") or "").strip()
        if saved_path and os.path.isfile(saved_path):
            launch_target = saved_path
        elif match.get("executable"):
            resolved = shutil.which(match["executable"])
            if resolved:
                launch_target = resolved

        if not launch_target:
            return {
                "success": False,
                "app": match,
                "error": (
                    f"No executable path is saved for {match['name']} ({match.get('executable') or 'unknown'}). "
                    f"Use Browse in the Games tab to link its .exe path so Aether can launch it."
                ),
            }

        try:
            cwd = os.path.dirname(launch_target) or None
            if sys.platform == "win32":
                subprocess.Popen(
                    [launch_target],
                    cwd=cwd,
                    creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                    close_fds=True,
                )
            else:
                subprocess.Popen([launch_target], cwd=cwd, close_fds=True)
            return {
                "success": True,
                "launched": True,
                "app": match,
                "path": launch_target,
                "message": f"Started {match['name']}.",
            }
        except Exception as e:
            return {"success": False, "app": match, "error": f"Failed to launch {match['name']}: {e}"}

    def check_game_elevation(self, game_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Checks whether the specified (or active) running game is elevated (Administrator)
        while Aether is non-elevated, which causes Windows UIPI to block hotkeys & macros.
        """
        gid = game_id or self.data.get("active_profile")
        aether_admin = is_current_process_elevated()
        result = {
            "game_id": gid or "",
            "aether_elevated": aether_admin,
            "game_elevated": False,
            "uipi_blocked": False,
            "warning": "",
        }
        if not gid or sys.platform != "win32" or aether_admin:
            return result

        prof = self.get_profile(gid)
        if not prof:
            return result
        target_proc = str(prof.get("process_name") or prof.get("executable") or "").strip().lower()
        if not target_proc:
            return result

        try:
            import psutil

            for proc in psutil.process_iter(["pid", "name"]):
                pname = (proc.info.get("name") or "").strip().lower()
                if pname == target_proc or pname == f"{target_proc}.exe":
                    if is_pid_elevated(proc.info["pid"]) is True:
                        g_title = prof.get("display_name") or gid
                        result["game_elevated"] = True
                        result["uipi_blocked"] = True
                        result["warning"] = (
                            f"{g_title} is running as Administrator while Aether is not. "
                            f"Windows UIPI may block Push-to-Talk hotkeys and DirectInput macros — "
                            f"restart Aether as Administrator if inputs don't register."
                        )
                        return result
        except Exception:
            pass
        return result

    def is_game_running(self, game_id: Optional[str] = None) -> bool:
        """
        Checks if a configured game (or the active/specified game) is currently running on the system.
        """
        target = game_id or self.data.get("active_profile")
        if not target:
            return False

        # 1. Quick check against foreground window
        fg = self.detect_foreground_game()
        if fg == target:
            return True

        # 2. Check running system processes
        if sys.platform != "win32":
            return False

        prof = self.get_profile(target)
        if not prof:
            return False

        raw_pname = (prof.get("executable") or prof.get("process_name") or "").strip()
        pname = os.path.basename(raw_pname).lower()
        if not pname:
            return False

        pstem = os.path.splitext(pname)[0]
        try:
            import psutil
            for proc in psutil.process_iter(['name']):
                try:
                    p_info_name = proc.info.get('name')
                    if not p_info_name:
                        continue
                    p_info_lower = p_info_name.lower().strip()
                    paired_exes = _get_paired_executables(pname)
                    if (p_info_lower == pname or
                        (pstem and pstem in p_info_lower) or
                        any(p_info_lower == pe or (os.path.splitext(pe)[0] and os.path.splitext(pe)[0] in p_info_lower) for pe in paired_exes)):
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            pass

        return False

    def is_game_mode_active(self) -> bool:
        """Returns True if Game Mode is active."""
        if not self.game_mode_enabled:
            return False
        active_id = self.data.get("active_profile")
        if not active_id:
            return False
        if self.user_override_game_mode is True:
            return True
        if self.user_override_game_mode is False:
            return False
        if self._last_detected_game and self._last_detected_game == active_id:
            return True
        return self.is_game_running(active_id)

    def set_game_mode(self, enabled: bool, user_explicit: bool = False) -> dict:
        """
        Enables or disables Game Mode.
        When Game Mode is True: LLM is strictly fenced inside the game universe (preventing hallucinations).
        When Game Mode is False: LLM can talk about real-world events / open topics,
        while in-game macros remain standing by and functional.
        """
        self.game_mode_enabled = bool(enabled)
        if user_explicit:
            self.user_override_game_mode = bool(enabled)

        active_id = self.data.get("active_profile", "")
        if self.game_mode_enabled:
            if active_id and self.is_game_running(active_id):
                self._last_detected_game = active_id
            else:
                running_gid = self.detect_running_game()
                if running_gid:
                    self.data["active_profile"] = running_gid
                    active_id = running_gid
                    self._last_detected_game = running_gid

        self._notify_mode_changed()
        active_name = self.get_profile(active_id).get("display_name", active_id) if active_id else ""
        return {
            "success": True,
            "game_mode_enabled": self.game_mode_enabled,
            "active_profile": active_id,
            "display_name": active_name,
            "user_override": self.user_override_game_mode
        }

    def _notify_mode_changed(self):
        """Dispatches game mode change event to callback if registered."""
        if callable(self.on_game_mode_changed):
            try:
                active_id = self.data.get("active_profile") or ""
                active_name = ""
                if active_id:
                    prof = self.get_profile(active_id)
                    if prof:
                        active_name = prof.get("display_name", active_id)
                is_running = self.is_game_running(active_id) if active_id else False
                active_state = self.is_game_mode_active()
                try:
                    self.on_game_mode_changed(active_state, active_id, active_name, is_running)
                except TypeError:
                    self.on_game_mode_changed(active_state, active_id, active_name)
            except Exception as e:
                print(f"[WARN] [GAME_MGR] Failed notifying game mode changed: {e}")

    def detect_foreground_game(self) -> Optional[str]:
        """
        Detects if the current active foreground window belongs to any configured game profile.
        Returns the matching game_id or None.
        """
        if sys.platform != "win32":
            return None
        try:
            import win32gui
            import win32process
            import psutil

            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return None

            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if not pid:
                return None

            try:
                proc = psutil.Process(pid)
                proc_name = proc.name().lower().strip()
            except Exception:
                proc_name = ""

            try:
                window_title = win32gui.GetWindowText(hwnd).lower().strip()
            except Exception:
                window_title = ""

            for gid, profile in self.data.get("profiles", {}).items():
                raw_pname = (profile.get("executable") or profile.get("process_name") or "").strip()
                pname = os.path.basename(raw_pname).lower()
                dname = (profile.get("display_name") or "").lower().strip()

                # A. Match process executable name (exact, stem, substring, or paired launcher/runtime)
                if pname and proc_name:
                    pname_stem = os.path.splitext(pname)[0]
                    paired_exes = _get_paired_executables(pname)
                    if (proc_name == pname or
                        pname_stem in proc_name or
                        proc_name in pname or
                        pname in proc_name or
                        any(proc_name == pe or os.path.splitext(pe)[0] in proc_name or proc_name in pe for pe in paired_exes)):
                        return gid

                # B. Match window title containing game display name or title words
                if dname and len(dname) > 3:
                    if dname in window_title:
                        return gid
                    words = [w for w in re.split(r'[^a-zA-Z0-9]+', dname) if len(w) > 2]
                    if len(words) >= 2 and all(w in window_title for w in words):
                        return gid
        except Exception:
            pass
        return None

    def detect_running_game(self) -> Optional[str]:
        """
        Checks running system processes to see if any configured game executable is loaded.
        Returns the matching game_id or None.
        """
        if sys.platform != "win32":
            return None
        try:
            import psutil
            profiles = self.data.get("profiles", {})
            if not profiles:
                return None

            targets = {}
            for gid, profile in profiles.items():
                raw_pname = (profile.get("executable") or profile.get("process_name") or "").strip()
                pname = os.path.basename(raw_pname).lower()
                if pname:
                    targets[gid] = (pname, os.path.splitext(pname)[0])

            if not targets:
                return None

            for proc in psutil.process_iter(['name']):
                try:
                    p_name = proc.info.get('name')
                    if not p_name:
                        continue
                    p_name_lower = p_name.lower().strip()
                    for gid, (pname, pstem) in targets.items():
                        paired_exes = _get_paired_executables(pname)
                        if (p_name_lower == pname or
                            (pstem and pstem in p_name_lower) or
                            any(p_name_lower == pe or (os.path.splitext(pe)[0] and os.path.splitext(pe)[0] in p_name_lower) for pe in paired_exes)):
                            return gid
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            pass
        return None

    def check_and_update_foreground_game(self) -> dict:
        """
        Checks if a configured game is currently focused in the foreground or loaded.
        If a game is loaded or focused:
        1. Automatically sets active profile to detected game.
        2. Automatically sets game_mode_enabled = True (unless explicit user override).
        3. Dispatches _notify_mode_changed() so the overlay updates immediately.
        """
        detected_id = self.detect_foreground_game() or self.detect_running_game()
        # Avoid hammering psutil.process_iter() across the whole OS every 2 seconds.
        # Only check background running process if game mode was already active to verify it didn't close.
        if not detected_id and self.game_mode_enabled:
            active_id = self.data.get("active_profile")
            if active_id and self.is_game_running(active_id):
                detected_id = active_id

        active_id = self.data.get("active_profile")

        if detected_id:
            if detected_id != active_id:
                self.set_active_profile(detected_id)
            else:
                if self.user_override_game_mode is not False and not self.game_mode_enabled:
                    self.game_mode_enabled = True
                    self._notify_mode_changed()
                elif getattr(self, "_last_detected_game", None) != detected_id:
                    self._notify_mode_changed()
        else:
            if getattr(self, "_last_detected_game", None) is not None:
                # Game just closed/terminated
                self.user_override_game_mode = None
                if self.game_mode_enabled:
                    self.game_mode_enabled = False
                    self._notify_mode_changed()
            elif self.user_override_game_mode is None and self.game_mode_enabled:
                self.game_mode_enabled = False
                self._notify_mode_changed()

        self._last_detected_game = detected_id

        return {
            "detected_game": detected_id,
            "active_profile": self.data.get("active_profile"),
            "game_mode_enabled": self.game_mode_enabled,
            "is_game_mode_active": self.is_game_mode_active(),
            "user_override": self.user_override_game_mode
        }

    def start_foreground_watcher(self, interval: float = 2.0):
        """Starts background daemon thread checking foreground game window."""
        if self._foreground_watcher_thread and self._foreground_watcher_thread.is_alive():
            return
        self._stop_foreground_watcher.clear()
        self._foreground_watcher_thread = threading.Thread(
            target=self._foreground_watcher_loop,
            args=(interval,),
            daemon=True,
            name="GameForegroundWatcher"
        )
        self._foreground_watcher_thread.start()

    def stop_foreground_watcher(self):
        """Stops background foreground game window polling thread."""
        self._stop_foreground_watcher.set()
        if self._foreground_watcher_thread:
            try:
                self._foreground_watcher_thread.join(timeout=1.0)
            except Exception:
                pass
            self._foreground_watcher_thread = None

    def _foreground_watcher_loop(self, interval: float = 2.0):
        while not self._stop_foreground_watcher.is_set():
            try:
                self.check_and_update_foreground_game()
            except Exception:
                pass
            self._stop_foreground_watcher.wait(interval)

    def get_active_game_context(self, include_personality: bool = True) -> str:
        """
        Gathers live context from the active game profile, including scratchpad
        notes and real-time telemetry if available.
        When Game Mode is disabled, returns an empty string so the LLM is not locked
        to the game universe and can freely discuss real-world topics while macros stay ready.
        """
        if not self.is_game_mode_active():
            return ""

        active_id = self.data.get("active_profile")
        if not active_id:
            return ""

        profile = self.get_profile(active_id)
        if not profile:
            return ""

        parts = [f"=== ACTIVE GAME COMPANION: {profile.get('display_name', active_id)} ==="]

        # 1. Scratchpad / Objectives / Checklists
        raw_notes = profile.get("scratchpad_raw", "").strip()
        if raw_notes:
            parts.append(f"PLAYER GOALS & OBJECTIVES:\n{raw_notes}")
        elif profile.get("scratchpad"):
            sp = profile["scratchpad"]
            sp_lines = []
            if sp.get("active_quests"):
                sp_lines.append("Quests: " + ", ".join(sp["active_quests"]))
            if sp.get("general_notes"):
                sp_lines.append("Notes: " + ", ".join(sp["general_notes"]))
            if sp_lines:
                parts.append("PLAYER GOALS & OBJECTIVES:\n" + "\n".join(sp_lines))

        # 2. Keybinds & Saved Sequences Reference (supports 1 to 1,000+ binds without prompt bloat)
        keybinds = profile.get("keybinds", {})
        if keybinds:
            def _bind_priority(item):
                phrase_k, data_v = item
                src = str(data_v.get("source") or "").lower()
                is_manual = 0 if src == "manual" else 1
                has_curated = 0 if (data_v.get("aliases") or data_v.get("condition")) else 1
                dev_rank = 0 if str(data_v.get("device") or "keyboard").lower() in ("keyboard", "mouse") else 1
                return (is_manual, has_curated, dev_rank, phrase_k)

            ordered = sorted(keybinds.items(), key=_bind_priority)
            max_prompt_binds = 80
            shown = ordered[:max_prompt_binds]
            macro_lines = [f'- "{phrase}" -> {data.get("description", phrase)}' for phrase, data in shown]
            overflow = len(ordered) - len(shown)
            if overflow > 0:
                macro_lines.append(
                    f"...plus {overflow} additional synced keybinds active and triggerable via `trigger_game_action`."
                )
            parts.append(
                f"AVAILABLE IN-GAME VOICE MACROS ({len(ordered)} total — trigger via `trigger_game_action`):\n"
                + "\n".join(macro_lines)
            )

        # Saved Multi-Step / Visual Macros (profile overrides + module defaults)
        merged_seqs: Dict[str, Any] = {}
        try:
            from core.game_modules import get_module_for_game
            mod = get_module_for_game(active_id, profile=profile)
            if isinstance(mod, dict) and isinstance(mod.get("sequences"), dict):
                merged_seqs.update(mod["sequences"])
        except Exception:
            pass
        if isinstance(profile.get("sequences"), dict):
            merged_seqs.update(profile["sequences"])
        if merged_seqs:
            disabled_seqs = {str(x).lower().strip() for x in (profile.get("disabled_module_sequences") or [])}
            seq_lines = []
            for sname, sdef in merged_seqs.items():
                if str(sname).lower().strip() in disabled_seqs:
                    continue
                if isinstance(sdef, dict):
                    sdesc = sdef.get("description") or sdef.get("ack") or f"Custom macro ({len(sdef.get('steps', []))} steps)"
                    saliases = sdef.get("aliases") or []
                    alias_note = f" (aliases: {', '.join(saliases)})" if saliases else ""
                    cond_note = f" [requires: {sdef['condition']}]" if sdef.get("condition") else ""
                    seq_lines.append(f'- "{sname}"{alias_note}{cond_note} -> {sdesc}')
                else:
                    seq_lines.append(f'- "{sname}" -> Custom macro')
            if seq_lines:
                parts.append(
                    "CUSTOM VISUAL & MULTI-STEP MACROS (trigger via `trigger_game_action`):\n"
                    "- FLEXIBLE INTENT MATCHING: The player does NOT need to say the exact macro name! "
                    "Whenever the player's natural request matches the intent of a macro below "
                    "(e.g. 'Let's get outta this station' or 'Undock us' -> 'take us out'), "
                    "immediately invoke `trigger_game_action(phrase='<exact macro name>')`.\n"
                    + "\n".join(seq_lines)
                )

        # 3. Live Telemetry
        if self.active_telemetry_adapter and self.active_telemetry_adapter.is_active():
            telemetry_ctx = self.active_telemetry_adapter.get_summary_prompt_context()
            if telemetry_ctx:
                parts.append(telemetry_ctx)

        # 4. Co-Pilot Intel & Observed Landmarks / Clues (Past Events & Points of Interest)
        copilot_entries = profile.get("copilot_log", [])
        if copilot_entries:
            recent_entries = copilot_entries[-12:]
            log_lines = []
            for entry in recent_entries:
                loc = f" (at {entry['location']})" if entry.get("location") else ""
                cat = entry.get("category", "INTEL").upper()
                log_lines.append(f"- [{cat}]{loc}: {entry.get('summary', '')}")
            parts.append("CO-PILOT INTEL & OBSERVED LANDMARKS / CLUES (PAST DISCOVERIES):\n" + "\n".join(log_lines))

        # Linked 3rd-Party Companion Apps & Flight-Deck Tools (Live Running Status)
        companions = self.get_companion_apps_with_status(active_id)
        if companions:
            comp_lines = []
            active_quiet_apps = []
            for c_data in companions:
                dname = c_data.get("name", c_data.get("id", "Companion App"))
                target = c_data.get("url") or c_data.get("executable") or c_data.get("id", "")
                status_tag = "[RUNNING]" if c_data.get("running") else "[NOT RUNNING]"
                purpose_str = f" — {c_data['purpose']}" if c_data.get("purpose") else ""
                mute_note = " (Mutes duplicate Aether alerts while running)" if c_data.get("mute_aether_alerts") else ""
                quiet_note = " (Quiet when running)" if c_data.get("quiet_when_running") else ""
                mon_note = f" (Monitor {c_data['default_monitor']})" if c_data.get("default_monitor") is not None else ""
                if c_data.get("quiet_when_running") and c_data.get("running"):
                    active_quiet_apps.append(dname)
                comp_lines.append(
                    f'- "{dname}" ({target}) {status_tag}{purpose_str}{mute_note}{quiet_note}{mon_note}'
                )
            quiet_directive = ""
            if active_quiet_apps:
                quiet_directive = (
                    f"\n- QUIET MODE ACTIVE ({', '.join(active_quiet_apps)} running): "
                    f"Stay quiet and passive — do NOT volunteer unsolicited commentary or alerts; respond only when directly addressed by the player."
                )
            parts.append(
                "LINKED 3RD-PARTY COMPANION APPS & FLIGHT-DECK TOOLS:\n"
                + "\n".join(comp_lines)
                + "\n- For [RUNNING] apps: actively leverage or reference them in-game when helpful (and defer duplicate telemetry callouts if marked muted).\n"
                + "- For [NOT RUNNING] apps: if the player asks to start one (or accepts an offer to start it), call `launch_game_companion_app(app_name)` directly."
                + quiet_directive
            )

        # Input Safety Mode Reminder
        if self.get_input_mode(active_id) == "spotter_only":
            parts.append(
                "INPUT SAFETY MODE: SPOTTER-ONLY (ANTI-CHEAT SAFE)\n"
                "- Synthetic keyboard/mouse/gamepad macros are STRICTLY DISABLED for this game.\n"
                "- Provide read-only voice guidance, visual HUD/map spotting, and companion-app assistance only."
            )

        # 5. Co-Pilot Role Directive
        parts.append(
            "CO-PILOT ROLE DIRECTIVE:\n"
            "You are an active in-game co-pilot and companion. Pay close attention to current telemetry, player surroundings, and past intel.\n"
            "If the player encounters or approaches a landmark, clue, point of interest, or destination that relates to something logged earlier, you may acknowledge or remind the player (e.g., 'That cave might hold treasure, we should check it out!' or 'We have a marked destination nearby.').\n"
            "CRITICAL NON-PROACTIVE RULE FOR PUZZLES & WALKTHROUGHS:\n"
            "- NEVER PROACTIVELY SPOIL OR VOLUNTEER PUZZLE SOLUTIONS, DUNGEON MECHANICS, OR STEP-BY-STEP WALKTHROUGHS UNPROMPTED.\n"
            "- If the player mentions a destination, quest, or puzzle (e.g., 'We are going there', 'Heading to the ruins', 'Let's check out the cave'), acknowledge the journey in-character, but DO NOT explain how to solve or complete it.\n"
            "- ONLY provide puzzle solutions, walkthrough steps, or hints WHEN THE PLAYER EXPLICITLY ASKS FOR HELP (e.g., 'How do I do this?', 'What do I do here?', 'Give me a hint', 'I need help with this puzzle').\n"
            "Use the 'record_copilot_observation' tool whenever you or the player notice a notable landmark, rumor, clue, or hazard worth remembering.\n"
            "Use the 'update_game_scratchpad' tool when the player tells you to note down or remember something on their personal scratchpad."
        )

        # 5b. Per-Game Player Callsign Directive (supports ';' separated random callsigns or blank = no callout)
        parts.append(self.build_game_callsign_directive(profile, game_id=active_id))

        # 5c. Co-Pilot Vibe, Free-Text Personality, Anti-Rote Variety & Adaptive Conversational Pacing
        if include_personality:
            parts.append(self.build_copilot_personality_directive(profile))

        # 6. Strict Game Scope & Search Locking Directive
        game_title = profile.get("display_name", active_id)
        universe_name = profile.get("universe_name") or profile.get("world_name") or f"the universe of {game_title}"
        parts.append(
            f"=== STRICT GAME SCOPE & SEARCH LOCK: {game_title.upper()} ===\n"
            f"1. IMMERSION & IN-CHARACTER MANDATE:\n"
            f"   - You are exclusively operating within {universe_name} in {game_title}.\n"
            f"   - Speak, react, and advise solely as the player's in-game companion. Stay 100% in-character at all times.\n"
            f"   - NEVER break character to discuss, explain, or troubleshoot real-world software, programming, Python code, system architecture, desktop bugs, or real-world events.\n"
            f"   - HARDWARE, THERMALS & FPS EXCEPTION: Real-time gaming rig performance metrics (CPU and GPU thermals/temperatures, VRAM allocation, and live FPS/frametime) ARE EXPLICITLY ALLOWED during Game Mode. If the player asks about thermals, temps, FPS, frame rates, or hardware status, DO NOT deflect. Invoke `get_hardware_telemetry` immediately and report the metrics directly.\n"
            f"   - If the player asks about other unrelated real-world software, code, bugs, or out-of-game matters: DO NOT ANSWER OR DISCUSS SOFTWARE. Deflect in-character and remind the player: 'My focus is on our journey in {game_title}. If you want to talk about real-world topics or code, tell me to \"disable Game Mode\" first.'\n"
            f"   - NEVER search for, reference, or import terminology from unrelated games (e.g. World of Warcraft, Black Desert, Dark Souls, Elder Scrolls, etc.).\n"
            f"   - When discussing game lore, builds, quests, or mechanics, provide rich, thorough, and detailed guidance grounded in {game_title}.\n"
            f"2. GOOGLE SEARCH LOCK: If you perform a Google Search to assist the player, your search query MUST be strictly scoped with \"{game_title}\" (e.g. '\"{game_title}\" <topic>'). Never execute un-scoped generic searches.\n"
            f"3. LIVE INTEL, MARKET LOOKUP & COURSE PLOTTING (CO-PILOT DATABASE MANDATE):\n"
            f"   - YOU ARE THE CO-PILOT & TACTICAL DATABASE. NEVER tell the player to 'go look it up on a website', 'check a wiki', or 'search online yourself'.\n"
            f"   - Whenever the player asks where to buy/sell a commodity, item, gear, module, outfitting, or service: IMMEDIATELY call `query_game_intel(query=..., category=...)`.\n"
            f"   - Report the concrete results naturally in-character (exact location/zone, vendor/station name, distance, access constraints, stock/supply, and price) and offer to plot the course.\n"
            f"   - When the player asks to plot a course, route to a system, or mark a location on their map, delegate directly to `search_and_route_destination` (for search-bar maps) or `pan_and_mark_map_location` / `assist_game_navigation`.\n"
            f"   - IN-GAME MENUS, MAPS & UI BUTTONS: NEVER use `close_window` or `add_to_whitelist` for in-game screens, maps, or menus — close them via `trigger_game_action` (e.g. `'close menu'`, `'escape'`, or the map toggle). To click a visible in-game UI button (e.g. `'Auto Launch'`, `'Starport Services'`), use `click_game_ui_element` or a saved macro. NEVER claim success if a tool returns `failed` or `element_not_found`.\n"
            f"   - NEVER attempt to blindly click raw screen coordinates with generic `mouse_click` or send random `escape`, `Close`, or `t` keystrokes.\n"
            f"4. SCREEN-FIRST GROUNDING:\n"
            f"   - If web wiki information is incomplete, unreleased, or conflicting, call `inspect_screen_context` to read the active quest tracker, map labels, or HUD directly from the user's game screen.\n"
            f"   - If a quest or location is ambiguous on screen, ask the player for visual clarification rather than guessing.\n"
            f"5. AUXILIARY CONSOLES & COMPANION FLIGHT-DECK TOOLS:\n"
            f"   - Auxiliary displays and companion apps (tactical maps, database terminals, companion web tools) are treated in-universe as auxiliary flight-deck terminals or cockpit MFDs.\n"
            f"   - You can launch, position, or navigate them across connected monitors (1 to N) using `launch_application`, `navigate_browser`, and `move_window_to_monitor` without breaking character.\n"
            f"   - Maintain player immersion while fulfilling display requests.\n"
            f"   - Game Shield: You are strictly prevented from closing the active game itself, but you may manage auxiliary windows and companion consoles."
        )

        # 7. Guidance Level & Helpful Assistance Directive
        guidance_level = profile.get("guidance_level", "tactical").lower().strip()
        guidance_descriptions = {
            "companion": (
                "MODE: COMPANION (LOW / IMMERSIVE HINTS ONLY)\n"
                "- Act as an atmospheric, observant fellow traveler.\n"
                "- Provide lore context, subtle observations, and gentle thematic hints ONLY WHEN ASKED FOR HELP.\n"
                "- Do NOT spoil or give away the direct puzzle sequence, answer, or riddle solution outright unless the player triggers the breakthrough override.\n"
                "- Never volunteer puzzle hints unprompted."
            ),
            "tactical": (
                "MODE: TACTICAL (MEDIUM / MECHANICS & CLUES - DEFAULT)\n"
                "- Focus on game mechanics, key clues, interactive elements in the area, and logical deductions ONLY WHEN ASKED FOR HELP.\n"
                "- Point out what the player might be missing, levers, symbols, or environmental patterns to empower them to solve it without spoiling every exact step immediately.\n"
                "- Never volunteer puzzle mechanics or clues unprompted."
            ),
            "walkthrough": (
                "MODE: WALKTHROUGH (HIGH / DIRECT STEP-BY-STEP SOLUTIONS)\n"
                "- Provide clear, direct, step-by-step walkthrough instructions and exact solutions to puzzles, riddles, or navigation challenges ONLY WHEN THE PLAYER ASKS FOR HELP (e.g. 'How do I do this?', 'What's the solution?', 'Help me with this puzzle', 'Walk me through it').\n"
                "- DO NOT BE OVERZEALOUS OR PROACTIVE: If the player simply says they are going somewhere, mentions a location, or arrives at a puzzle, DO NOT explain how to solve it unprompted. Acknowledge their movement in-character, and wait until they ask for help before offering any walkthrough steps or solutions.\n"
                "- When asked, deliver the exact solution clearly, concisely, and completely in-character."
            )
        }
        active_guidance_desc = guidance_descriptions.get(guidance_level, guidance_descriptions["tactical"])

        parts.append(
            f"=== GUIDANCE LEVEL: {guidance_level.upper()} ===\n"
            f"{active_guidance_desc}\n\n"
            "DYNAMIC BREAKTHROUGH OVERRIDE:\n"
            "If the player explicitly demands the direct answer, solution, or asks 'how do I do this' / 'just tell me the solution' / 'give me the answer' / 'no hints':\n"
            "TEMPORARILY ESCALATE YOUR ASSISTANCE TO WALKTHROUGH MODE FOR THIS TURN! Immediately give the exact, step-by-step answer or puzzle solution directly, while remaining in character (e.g., 'Alright, let's cut to the chase...', 'Understood, here is the exact sequence...'). Do not withhold the solution or force more riddles upon them when they ask directly."
        )

        # 8. In-Game Map Navigation Directive
        nav_mode = profile.get("navigation_mode", "controller").lower().strip()
        nav_directives = {
            "controller": (
                "MAP NAVIGATION MODE: VIRTUAL CONTROLLER (ViGEmBus)\n"
                "- When navigating or placing map markers, the system communicates directly with the game using virtual Xbox 360 controller hardware events (ViGEmBus).\n"
                "- You can execute `pan_and_mark_map_location`, `pan_game_map`, and `place_map_marker` autonomously."
            ),
            "co_pilot_spotter": (
                "MAP NAVIGATION MODE: CO-PILOT GUIDED SPOTTER (VOICE & VISION)\n"
                "- Do NOT attempt to blindly move the player's physical mouse cursor.\n"
                "- When the player says 'Open map' (or any bound macro), Aether automatically injects the DirectInput keystroke to open the map. NEVER ask the player to open their map for you.\n"
                "- When the player asks where a destination is on the map, act as an observant co-pilot/spotter: verbally guide them on where to pan on the open map (e.g. 'Pan northeast past the river towards Silver Wolf Mountain.').\n"
                "- Use `inspect_screen_context` or `spot_and_guide_map_landmark` to look at their map screen in real time, call out when they are near the destination ('Right there, place your marker near that ruin icon!'), and verify once they place their pin."
            ),
            "search_bar": (
                "MAP NAVIGATION MODE: SEARCH BAR & KEYBOARD ROUTING (e.g. Elite Dangerous / Systems)\n"
                "- Use text search entry to plot courses rather than manual viewport dragging.\n"
                "- When the player requests navigation, focus the navigation search box, enter the system/destination name via `search_and_route_destination`, and plot the route."
            ),
            "mouse_keyboard": (
                "MAP NAVIGATION MODE: MOUSE & KEYBOARD DRAG\n"
                "- The map is navigated via left-click canvas dragging and DirectInput keystrokes."
            )
        }
        active_nav_desc = nav_directives.get(nav_mode, nav_directives["controller"])
        parts.append(f"=== MAP NAVIGATION PROTOCOL: {nav_mode.upper()} ===\n{active_nav_desc}")

        return "\n\n".join(parts)

    def add_copilot_log_entry(
        self,
        game_id: str,
        summary: str,
        category: str = "intel",
        location: str = "",
        details: str = ""
    ) -> dict:
        """Adds a milestone, landmark, or intel observation to the game's Co-Pilot log."""
        profile = self.get_profile(game_id)
        if not profile:
            return {"success": False, "error": f"Game profile '{game_id}' not found."}

        log = profile.setdefault("copilot_log", [])
        entry_id = f"copilot_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        now = time.time()
        time_str = time.strftime("%H:%M:%S", time.localtime(now))

        entry = {
            "id": entry_id,
            "timestamp": now,
            "time_str": time_str,
            "category": category.lower().strip() or "intel",
            "summary": summary.strip(),
            "location": location.strip(),
            "details": details.strip()
        }
        log.append(entry)
        if len(log) > 100:
            profile["copilot_log"] = log[-100:]

        self._save_profiles(self.data)
        return {"success": True, "entry": entry, "total": len(profile["copilot_log"])}

    def get_copilot_log(self, game_id: str) -> List[dict]:
        """Returns the list of co-pilot log entries for the given game."""
        profile = self.get_profile(game_id)
        if not profile:
            return []
        return profile.get("copilot_log", [])

    def clear_copilot_log(self, game_id: str) -> bool:
        """Clears all co-pilot log entries for the given game."""
        profile = self.get_profile(game_id)
        if not profile:
            return False
        profile["copilot_log"] = []
        self._save_profiles(self.data)
        return True

    def delete_copilot_log_entry(self, game_id: str, entry_id: str) -> bool:
        """Deletes a specific co-pilot log entry by ID."""
        profile = self.get_profile(game_id)
        if not profile:
            return False
        log = profile.get("copilot_log", [])
        new_log = [e for e in log if e.get("id") != entry_id]
        if len(new_log) != len(log):
            profile["copilot_log"] = new_log
            self._save_profiles(self.data)
            return True
        return False

    def append_to_scratchpad(self, game_id: str, note: str) -> str:
        """Appends a new note or checklist item to the player's personal scratchpad."""
        profile = self.get_profile(game_id)
        if not profile:
            return ""
        current = profile.get("scratchpad_raw", "")
        cleaned_note = note.strip()
        if not cleaned_note:
            return current
        if current.strip():
            new_text = current.rstrip() + "\n" + cleaned_note
        else:
            new_text = cleaned_note
        profile["scratchpad_raw"] = new_text
        self._save_profiles(self.data)
        return new_text

    def close(self):
        """Shuts down background telemetry watchers and foreground process watcher."""
        self.stop_foreground_watcher()
        if self.active_telemetry_adapter:
            try:
                self.active_telemetry_adapter.stop()
            except Exception:
                pass
            self.active_telemetry_adapter = None

    def list_profiles(self) -> Dict[str, Any]:
        """Returns metadata for all configured game profiles and the active profile indicator."""
        return {
            "active_profile": self.data.get("active_profile"),
            "profiles": self.data.get("profiles", {})
        }

    def set_keybind(
        self,
        game_id: str,
        phrase: str,
        key: str,
        modifiers: Optional[List[str]] = None,
        description: str = ""
    ) -> bool:
        """Adds or updates a custom macro keybinding for a specific game."""
        profile = self.get_profile(game_id)
        if not profile:
            return False
        if "keybinds" not in profile:
            profile["keybinds"] = {}
        profile["keybinds"][phrase.lower().strip()] = {
            "key": key.lower().strip(),
            "modifiers": [m.lower().strip() for m in (modifiers or [])],
            "description": description or phrase,
            "source": "manual"
        }
        self._save_profiles(self.data)
        return True

    def update_scratchpad(self, game_id: str, section: str, item: Any) -> bool:
        """Appends an entry into a scratchpad section (e.g. quests, notes)."""
        profile = self.get_profile(game_id)
        if not profile:
            return False
        scratchpad = profile.setdefault("scratchpad", {})
        if section not in scratchpad:
            scratchpad[section] = []
        if isinstance(scratchpad[section], list):
            scratchpad[section].append(item)
            self._save_profiles(self.data)
            return True
        return False

    def list_game_extensions(self) -> List[Dict[str, Any]]:
        """Lists discovered Tier 2 community `.py` game extensions and their SHA-256 trust state."""
        from core.game_extensions import list_game_extensions
        return list_game_extensions()

    def trust_game_extension(self, module_id: str, script_name: str = "extension.py") -> Dict[str, Any]:
        """Explicitly approves a Tier 2 `.py` game extension by recording its SHA-256 hash."""
        from core.game_extensions import trust_game_extension
        return trust_game_extension(module_id, script_name=script_name)

    def revoke_game_extension(self, module_id: str) -> Dict[str, Any]:
        """Revokes trust for a Tier 2 `.py` game extension."""
        from core.game_extensions import revoke_game_extension
        return revoke_game_extension(module_id)

    def detect_heuristic_config(self, game_id: str) -> Dict[str, dict]:
        """
        Heuristically searches for game config / keybinding files in standard directories
        (AppData, Saved Games, Documents) or custom profile paths and extracts mappings.
        """
        profile = self.get_profile(game_id) or {}
        search_dirs = []
        if profile.get("config_dir"):
            search_dirs.append(os.path.expandvars(profile["config_dir"]))
        if profile.get("bindings_dir"):
            search_dirs.append(os.path.expandvars(profile["bindings_dir"]))

        display_name = profile.get("display_name", game_id)
        # Standard user locations
        for base_env in [r"%LOCALAPPDATA%", r"%APPDATA%", r"%USERPROFILE%\Saved Games", r"%USERPROFILE%\Documents\My Games"]:
            base = os.path.expandvars(base_env)
            if os.path.exists(base):
                search_dirs.append(os.path.join(base, display_name))
                search_dirs.append(os.path.join(base, game_id))

        discovered = {}
        for sdir in search_dirs:
            if not os.path.exists(sdir):
                continue
            # Look for JSON configs first
            for jfile in glob.glob(os.path.join(sdir, "*.json")):
                try:
                    with open(jfile, "r", encoding="utf-8") as f:
                        jdata = json.load(f)
                    for key in ["keybinds", "bindings", "controls", "keys"]:
                        if isinstance(jdata, dict) and key in jdata and isinstance(jdata[key], dict):
                            for act, bind in jdata[key].items():
                                if isinstance(bind, dict) and "key" in bind:
                                    discovered[act.lower()] = {
                                        "key": str(bind["key"]).lower(),
                                        "modifiers": [m.lower() for m in bind.get("modifiers", [])],
                                        "description": bind.get("description", act),
                                        "source": "heuristic_detected"
                                    }
                                elif isinstance(bind, str):
                                    discovered[act.lower()] = {
                                        "key": bind.lower(),
                                        "modifiers": [],
                                        "description": act,
                                        "source": "heuristic_detected"
                                    }
                except Exception:
                    pass

            # Look for INI / CFG files
            for cfile in glob.glob(os.path.join(sdir, "*.ini")) + glob.glob(os.path.join(sdir, "*.cfg")):
                try:
                    with open(cfile, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            line = line.strip()
                            if "=" in line and not line.startswith(("#", ";", "[")):
                                k, v = line.split("=", 1)
                                k_clean = k.strip().lower()
                                v_clean = v.strip().lower()
                                from tools.os_controls import SCANCODE_MAP
                                if v_clean in SCANCODE_MAP:
                                    act_phrase = k_clean.replace("action_", "").replace("bind_", "").replace("_", " ")
                                    discovered[act_phrase] = {
                                        "key": v_clean,
                                        "modifiers": [],
                                        "description": k.strip(),
                                        "source": "heuristic_detected"
                                    }
                except Exception:
                    pass

        return discovered

    ALLOWED_COPILOT_VIBES = ("witty", "tactical", "cinematic", "terse")

    def get_copilot_vibe(self, game_id: str) -> Dict[str, str]:
        """Returns the co-pilot vibe preset and free-text custom personality for a game profile."""
        profile = self.get_profile(game_id) or {}
        vibe = str(profile.get("copilot_vibe") or "witty").strip().lower()
        if vibe not in self.ALLOWED_COPILOT_VIBES:
            vibe = "witty"
        custom = str(profile.get("copilot_personality") or "")
        return {"vibe": vibe, "custom_personality": custom}

    def set_copilot_vibe(self, game_id: str, vibe: str = "witty", custom_personality: Optional[str] = None) -> dict:
        """Sets the per-game co-pilot vibe preset and optional unrestricted free-text personality."""
        profile = self.get_profile(game_id)
        if not profile:
            return {"success": False, "error": f"Game profile '{game_id}' not found."}
        norm_vibe = str(vibe or "witty").strip().lower()
        if norm_vibe not in self.ALLOWED_COPILOT_VIBES:
            return {
                "success": False,
                "error": f"Invalid vibe '{vibe}'. Allowed: {', '.join(self.ALLOWED_COPILOT_VIBES)}.",
            }
        profile["copilot_vibe"] = norm_vibe
        if custom_personality is not None:
            profile["copilot_personality"] = str(custom_personality)
        self._save_profiles(self.data, target_game_id=game_id)
        return {
            "success": True,
            "game_id": game_id,
            "vibe": norm_vibe,
            "custom_personality": str(profile.get("copilot_personality") or ""),
        }

    def build_copilot_personality_directive(self, profile: Dict[str, Any]) -> str:
        """Builds an engaging, non-rote personality & adaptive-pacing directive for Game Mode."""
        vibe = str((profile or {}).get("copilot_vibe") or "witty").strip().lower()
        custom = str((profile or {}).get("copilot_personality") or "").strip()

        vibe_descriptions = {
            "witty": (
                "VOICE & VIBE — WITTY & ENGAGING:\n"
                "- Sound like a quick-witted, warm, genuinely fun companion riding shotgun — NOT a polite butler or corporate AI.\n"
                "- Use natural spoken contractions (\"we're\", \"that's\", \"let's\", \"got it\"), playful banter, dry humor, and lively reactions to the moment.\n"
                "- Example rhythm (improvise fresh lines, never copy verbatim): \"Engines dead — parked and waiting on you.\", "
                "\"Spooling up the drive, hang onto your coffee.\", \"Map's up — where are we headed?\", "
                "\"Careful, the local faction runs this turf and they aren't exactly rolling out the welcome mat.\""
            ),
            "tactical": (
                "VOICE & VIBE — TACTICAL / FLIGHT-DECK PRO:\n"
                "- Sharp, seasoned, confident co-pilot with crisp situational awareness, calm focus, and authentic crew-chief cadence.\n"
                "- Example rhythm: \"All stop. Holding position.\", \"Drive's hot — punch it on your mark.\", \"Contacts up, requesting clearance.\""
            ),
            "cinematic": (
                "VOICE & VIBE — CINEMATIC / DRAMATIC:\n"
                "- Evocative, immersive, and rich with atmosphere — lean into the scale, tension, and wonder of the world around you.\n"
                "- Example rhythm: \"Engines cut — dead quiet out here.\", \"Charging the drive... let's tear open the dark.\""
            ),
            "terse": (
                "VOICE & VIBE — TERSE / MINIMAL:\n"
                "- Ultra-brief, razor-sharp 2-to-5-word callouts on routine actions; expand naturally only when the player asks an open question.\n"
                "- Example rhythm: \"Full stop.\", \"Gear down.\", \"Map open.\", \"Heat sink out.\""
            ),
        }
        vibe_block = vibe_descriptions.get(vibe, vibe_descriptions["witty"])
        custom_block = (
            f"\nCUSTOM USER PERSONALITY INSTRUCTIONS (HIGHEST PRIORITY — ALWAYS HONOR THIS VOICE):\n{custom}\n"
            if custom
            else ""
        )

        return (
            "=== CO-PILOT VOICE, ANTI-ROTE VARIETY & CONVERSATIONAL FLOW ===\n"
            f"{vibe_block}{custom_block}\n"
            "ANTI-ROTE VARIETY RULE & BANNED ASSISTANT CLICHÉS (NEVER SAY THESE):\n"
            "- NEVER say \"I have initiated ... for you\", \"Initiating ... now.\", \"Accessing ...\", \"Opening ... now.\", "
            "\"I'm afraid I can't...\", or echo raw keybind names.\n"
            "- NEVER end every sentence with the player's callsign — drop it in only occasionally when it feels natural.\n"
            "- NEVER reuse the exact same confirmation template twice in a row; vary your phrasing, energy, and rhythm every turn.\n"
            "ADAPTIVE PACING & LIVE CONVERSATION:\n"
            "- Quick cockpit/combat/action commands: keep acknowledgments punchy, lively, and varied (1 natural spoken line).\n"
            "- News, lore, world events, faction politics, strategy, or open questions: DO NOT cut yourself off at 2–3 lines! "
            "Answer directly on the Live WebSocket (using your built-in Google Search when needed — reserve `query_game_intel` "
            "for structured live database lookups like markets, commodities, gear/outfitting, vendors, services, or system intel) "
            "and enjoy a rich, natural, multi-sentence conversational exchange."
        )

    def get_callsign(self, game_id: str) -> str:
        """Returns the configured player callsign string (supports ';' separated values or blank)."""
        profile = self.get_profile(game_id)
        if not profile:
            return ""
        if "callsign" in profile and profile["callsign"] is not None:
            return str(profile["callsign"]).strip()
        # Fall back to declarative module default_callsign (zero hardcoded game names in Python)
        try:
            from core.game_modules import get_module_for_game
            mod = get_module_for_game(game_id, profile=profile)
            if isinstance(mod, dict) and mod.get("default_callsign"):
                return str(mod["default_callsign"]).strip()
        except Exception:
            pass
        return ""

    def set_callsign(self, game_id: str, callsign: str) -> bool:
        """Sets the player callsign(s) for a game profile (supports ';' separated list or blank)."""
        profile = self.get_profile(game_id)
        if not profile:
            return False
        raw = str(callsign or "").strip()
        parts = [p.strip() for p in raw.split(";") if p.strip()]
        clean = "; ".join(parts) if parts else ""
        profile["callsign"] = clean
        self._save_profiles(self.data, target_game_id=game_id)
        return True

    def get_multiple_main_characters(self, game_id: str) -> bool:
        """Returns True if this game has multiple distinct playable protagonists."""
        profile = self.get_profile(game_id)
        if not profile:
            return False
        return bool(profile.get("multiple_main_characters", False))

    def set_multiple_main_characters(self, game_id: str, enabled: bool) -> bool:
        """Enables or disables vision-matched multiple protagonist callsigns for a game."""
        profile = self.get_profile(game_id)
        if not profile:
            return False
        profile["multiple_main_characters"] = bool(enabled)
        self._save_profiles(self.data, target_game_id=game_id)
        return True

    def build_game_callsign_directive(self, profile: Dict[str, Any], game_id: str = "") -> str:
        """Builds an explicit directive governing how Aether addresses the player in this game."""
        if "callsign" in profile and profile["callsign"] is not None:
            raw = str(profile["callsign"]).strip()
        elif "elite" in (game_id or "").lower() or "elite" in str(profile.get("display_name", "")).lower():
            raw = "Commander"
        else:
            raw = ""

        callsigns = [c.strip() for c in raw.split(";") if c.strip()]
        if not callsigns:
            return (
                "=== PLAYER CALLSIGN & ADDRESS DIRECTIVE (BLANK / NO CALLSIGN) ===\n"
                "- The player's callsign for this game is BLANK.\n"
                "- Do NOT address the player by any name, callsign, rank, or title (NEVER call them 'Commander', 'Pilot', 'Captain', 'Chief', 'Boss', etc.).\n"
                "- Speak to the player directly and naturally without inserting any name or title."
            )

        if len(callsigns) == 1:
            only_name = callsigns[0]
            return (
                f"=== PLAYER CALLSIGN & ADDRESS DIRECTIVE ===\n"
                f"- The player's callsign in this game is '{only_name}'.\n"
                f"- Address the player naturally as '{only_name}' (roughly every 2 to 3 responses). Never use any other title."
            )

        formatted_list = ", ".join(f"'{c}'" for c in callsigns)
        if bool(profile.get("multiple_main_characters", False)):
            return (
                f"=== PLAYER CALLSIGN & ADDRESS DIRECTIVE (MULTIPLE MAIN CHARACTERS — VISION MATCHED) ===\n"
                f"- This game has multiple playable protagonists: {formatted_list}.\n"
                f"- These are DIFFERENT CHARACTERS, NOT interchangeable nicknames for the same character.\n"
                f"- ALWAYS inspect the live screen (active character model, HUD portrait/icon, equipment/combat style, or dialogue) to identify which protagonist from {formatted_list} is currently active before addressing them by name.\n"
                f"- ONLY address the player as the character currently active on screen. Do NOT randomly pick a different protagonist.\n"
                f"- If the active character is not visible or cannot be determined (e.g., title screen, full-screen map, or loading screen), speak naturally WITHOUT using any character name."
            )

        chosen = random.choice(callsigns)
        return (
            f"=== PLAYER CALLSIGN & ADDRESS DIRECTIVE (MULTIPLE CALLSIGNS) ===\n"
            f"- The player uses the following callsigns in this game: {formatted_list}.\n"
            f"- Randomly alternate between {formatted_list} when addressing the player (for example, use '{chosen}' on this turn).\n"
            f"- Never use more than one callsign in a single response, and NEVER use any title outside {formatted_list}."
        )

    def fetch_web_default_keybinds(self, game_name: str, client, model_endpoint: str = "gemini-4-hydrogen:low") -> Dict[str, dict]:
        """
        Queries online reference knowledge to retrieve standard PC default
        keyboard and mouse bindings for titles without local XML/INI files.
        Routes through Gemini 4 Hydrogen - Low by default.
        """
        if not client:
            return {}

        from google.genai import types
        from core.model_discovery import resolve_model_profile, build_thinking_config

        raw_ep = (model_endpoint or "gemini-4-hydrogen:low").strip()
        if "live" in raw_ep.lower():
            raw_ep = "gemini-4-hydrogen:low"
        base_model, level = resolve_model_profile(raw_ep, default_model="gemini-4-hydrogen", default_level="low")
        thinking_cfg = build_thinking_config(base_model, thinking_level=level or "low")

        prompt = f"""
Provide the official default PC Keyboard and Mouse keybindings for the game: "{game_name}".
Focus on essential gameplay, UI, movement, combat, and consumable shortcuts (e.g., call mount, open inventory, open map, crouch, dodge, guard, interact, quick slots).

Return ONLY a JSON dictionary where keys are natural trigger phrases in lowercase (e.g. "call mount", "open map", "crouch"):
{{
    "voice trigger phrase": {{
        "key": "single key string (e.g. 'h', 'm', 'c', 'space', 'i', 'f1', 'mouse_left', 'mouse_right')",
        "modifiers": ["optional list of modifier strings like 'shift', 'ctrl' or empty list"],
        "description": "Short explanation of the action",
        "source": "web_defaults"
    }}
}}
Format strictly as valid JSON.
"""
        try:
            gen_cfg = types.GenerateContentConfig(
                response_mime_type="application/json",
                thinking_config=thinking_cfg,
            )
            res = client.models.generate_content(
                model=base_model,
                contents=prompt,
                config=gen_cfg,
            )
            raw_text = ""
            if res and getattr(res, "candidates", None):
                cand = res.candidates[0]
                if cand and getattr(cand, "content", None) and getattr(cand.content, "parts", None):
                    raw_text = "".join(
                        p.text for p in cand.content.parts
                        if getattr(p, "text", None) and getattr(p, "thought", False) is not True
                    ).strip()
            if not raw_text and res and hasattr(res, "text") and res.text:
                raw_text = res.text.strip()

            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                raw_text = re.sub(r"\s*```$", "", raw_text)

            parsed = json.loads(raw_text) if raw_text else {}
            if isinstance(parsed, dict) and parsed:
                clean_dict = {}
                for k, v in parsed.items():
                    if isinstance(v, dict) and v.get("key"):
                        v["source"] = "web_defaults"
                        v.setdefault("modifiers", [])
                        v.setdefault("description", k)
                        clean_dict[k.lower().strip()] = v
                return clean_dict
        except Exception as e:
            print(f"[ERROR] [GAME_MGR] Web keybind ingestion failed ({base_model}): {e}")
        return {}

    def sync_game_binds_with_fallback(self, game_id: str, client=None, model_endpoint: str = "gemini-4-hydrogen:low") -> dict:
        """
        1. Tries local XML/INI directory scanning (or built-in profile presets).
        2. If unavailable, fetches published PC defaults via Hydrogen - Low.
        3. Merges into profile and persists to data/games/<game_id>.json.
        """
        profile = self.get_profile(game_id)
        if not profile:
            return {"success": False, "reason": "Profile not found"}

        # 1. Local file scan attempt (declarative module scanner, trusted extension, or heuristic fallback)
        detected = scan_profile_bindings(profile, game_id=game_id)
        if not detected:
            detected = self.detect_heuristic_config(game_id)
        if detected:
            profile.setdefault("keybinds", {}).update(detected)
            self._save_profiles(self.data, target_game_id=game_id)
            return {"success": True, "count": len(detected), "source": "local_files", "keybinds": profile["keybinds"]}

        # 2. Web Reference Fallback (Hydrogen - Low)
        if client:
            web_binds = self.fetch_web_default_keybinds(profile.get("display_name", game_id), client, model_endpoint)
            if web_binds:
                profile.setdefault("keybinds", {}).update(web_binds)
                self._save_profiles(self.data, target_game_id=game_id)
                return {"success": True, "count": len(web_binds), "source": "web_defaults", "keybinds": profile["keybinds"]}

        # 3. Curated Built-in Preset Fallback (when local files and web retrieval are unavailable)
        builtin_binds = self._get_builtin_default_keybinds(game_id, profile.get("display_name", ""))
        if builtin_binds:
            profile.setdefault("keybinds", {}).update(builtin_binds)
            self._save_profiles(self.data, target_game_id=game_id)
            return {"success": True, "count": len(builtin_binds), "source": "builtin_defaults", "keybinds": profile["keybinds"]}

        return {"success": False, "reason": "No local files found and web retrieval returned no valid bindings."}

    def _get_builtin_default_keybinds(self, game_id: str, display_name: str = "") -> Dict[str, dict]:
        """Returns curated PC default keybindings for known titles when offline or unconfigured."""
        target = f"{game_id} {display_name}".lower()
        if "crimson" in target and "desert" in target:
            return {
                "open inventory": {"key": "i", "modifiers": [], "description": "Open equipment and inventory screen", "source": "web_defaults"},
                "open map": {"key": "m", "modifiers": [], "description": "Open world map", "source": "web_defaults"},
                "move forward": {"key": "w", "modifiers": [], "description": "Move character forward", "source": "web_defaults"},
                "move backward": {"key": "s", "modifiers": [], "description": "Move character backward", "source": "web_defaults"},
                "move left": {"key": "a", "modifiers": [], "description": "Move character left", "source": "web_defaults"},
                "move right": {"key": "d", "modifiers": [], "description": "Move character right", "source": "web_defaults"},
                "sprint": {"key": "shift", "modifiers": [], "description": "Sprint or dash", "source": "web_defaults"},
                "jump": {"key": "space", "modifiers": [], "description": "Jump or vault over obstacles", "source": "web_defaults"},
                "crouch": {"key": "c", "modifiers": [], "description": "Crouch or enter stealth posture", "source": "web_defaults"},
                "dodge": {"key": "e", "modifiers": [], "description": "Perform an evasive roll or step", "source": "web_defaults"},
                "primary attack": {"key": "mouse_left", "modifiers": [], "description": "Perform light or primary melee attack", "source": "web_defaults"},
                "secondary attack": {"key": "mouse_right", "modifiers": [], "description": "Perform heavy attack or special combat action", "source": "web_defaults"},
                "guard": {"key": "q", "modifiers": [], "description": "Block incoming attacks or ready shield", "source": "web_defaults"},
                "kick": {"key": "f", "modifiers": [], "description": "Execute a kick to break guard or stagger enemies", "source": "web_defaults"},
                "interact": {"key": "r", "modifiers": [], "description": "Interact with objects, NPCs, or loot", "source": "web_defaults"},
                "lock on": {"key": "mouse_middle", "modifiers": [], "description": "Lock camera onto targeted enemy", "source": "web_defaults"},
                "call mount": {"key": "h", "modifiers": [], "description": "Whistle to call mount", "source": "web_defaults"},
                "pause menu": {"key": "escape", "modifiers": [], "description": "Open main system menu or pause game", "source": "web_defaults"},
                "use potion": {"key": "1", "modifiers": [], "description": "Consume primary healing item", "source": "web_defaults"},
                "quick slot 1": {"key": "1", "modifiers": [], "description": "Use assigned item in quick slot 1", "source": "web_defaults"},
                "quick slot 2": {"key": "2", "modifiers": [], "description": "Use assigned item in quick slot 2", "source": "web_defaults"},
                "quick slot 3": {"key": "3", "modifiers": [], "description": "Use assigned item in quick slot 3", "source": "web_defaults"},
                "quick slot 4": {"key": "4", "modifiers": [], "description": "Use assigned item in quick slot 4", "source": "web_defaults"},
            }
        return {}

    def sync_game_binds(self, game_id: str, client=None, model_endpoint: str = "gemini-4-hydrogen:low") -> dict:
        """Attempts local scan or web fallback keybind synchronization."""
        return self.sync_game_binds_with_fallback(game_id, client=client, model_endpoint=model_endpoint)

    def trigger_action(self, phrase: str, vision_client: Any = None, model_endpoint: str = "gemini-4-hydrogen") -> dict:
        """
        Looks up spoken phrase in the active game profile and executes the action.
        Supports state-conditioned actions, composite visual/keystroke sequences, DirectInput combos,
        and virtual gamepad/mouse inputs.
        """
        active_id = self.data.get("active_profile")
        profile = self.get_profile(active_id)
        if not profile:
            return {"status": "error", "message": "No active game profile."}

        # 1. State-Aware MacroRunner (Checks preconditions, executes sequences or keybinds)
        macro_res = self.macro_runner.execute_action(
            phrase=phrase,
            profile=profile,
            telemetry_adapter=self.active_telemetry_adapter,
            vision_client=vision_client or getattr(self, "vision_client", None),
            model_endpoint=model_endpoint,
        )
        if macro_res.get("status") in ("executed", "condition_skipped", "failed", "element_not_found", "aborted"):
            return macro_res

        from tools.os_controls import (
            click_mouse,
            zoom_viewport,
            send_gamepad_button,
            send_gamepad_trigger,
        )

        keybinds = profile.get("keybinds", {})
        phrase_clean = phrase.lower().strip()

        # Match trigger phrase (exact or normalized)
        action = keybinds.get(phrase_clean)
        if not action:
            for k, val in keybinds.items():
                if phrase_clean == k.lower().strip():
                    action = val
                    break
        if not action:
            return {"status": "ignored", "message": f"No macro mapped to '{phrase}' in {profile['display_name']}."}

        key = str(action.get("key", "")).lower().strip()
        mods = action.get("modifiers", [])

        # 1. Mouse Action Mapping
        mouse_map = {
            "mouse_left": "left",
            "left_click": "left",
            "mouse_right": "right",
            "right_click": "right",
            "mouse_middle": "middle",
            "middle_click": "middle",
        }
        if key in mouse_map:
            click_mouse(button=mouse_map[key])
            return {"status": "executed", "game": profile["display_name"], "action": action.get("description", phrase), "type": "mouse"}

        if key in ("scroll_up", "wheel_up"):
            zoom_viewport(1)
            return {"status": "executed", "game": profile["display_name"], "action": action.get("description", phrase), "type": "mouse_wheel"}
        elif key in ("scroll_down", "wheel_down"):
            zoom_viewport(-1)
            return {"status": "executed", "game": profile["display_name"], "action": action.get("description", phrase), "type": "mouse_wheel"}

        # 2. Virtual Gamepad Mapping
        if key.startswith("gamepad_") or key.startswith("btn_") or key in ("lt", "rt", "left_trigger", "right_trigger"):
            if key in ("lt", "rt", "left_trigger", "right_trigger"):
                send_gamepad_trigger(key, 1.0)
            else:
                send_gamepad_button(key)
            return {"status": "executed", "game": profile["display_name"], "action": action.get("description", phrase), "type": "gamepad"}

        # 3. DirectInput Keyboard Mapping
        if mods:
            send_directinput_combo(mods, key)
        else:
            send_directinput_key(key)

        return {"status": "executed", "game": profile["display_name"], "action": action.get("description", phrase), "type": "keyboard"}
