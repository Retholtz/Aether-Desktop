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
import time
import uuid
import sys
import threading
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional, List, Callable

from core.telemetry import create_telemetry_adapter, BaseTelemetryAdapter
from core.binds_scanners import scan_profile_bindings
from core.game_macro_runner import MacroRunner

GAMES_DIR = os.path.join("data", "games")
GAMES_STATE_FILE = "active_state.json"
GAMES_PROFILES_PATH = os.path.join("data", "games_profiles.json")

DEFAULT_PROFILES = {
    "active_profile": "",
    "profiles": {}
}


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

        profile = {
            "display_name": name_clean,
            "process_name": process_name.strip(),
            "telemetry_type": telemetry_type or "scratchpad_only",
            "guidance_level": "tactical",
            "navigation_mode": "controller",
            "map_controls": {
                "preferred_input": "gamepad",
                "pan_method": "left_drag",
                "marker_method": "right_click",
                "zoom_method": "mouse_wheel"
            },
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
        """Retrieves map navigation mode ('controller', 'co_pilot_spotter', 'search_bar', 'mouse_keyboard'). Defaults to 'controller'."""
        gid = game_id or self.data.get("active_profile")
        if not gid:
            return "controller"
        profile = self.get_profile(gid)
        if not profile:
            return "controller"
        mode = (profile.get("navigation_mode") or "controller").lower().strip()
        return mode if mode in ("controller", "co_pilot_spotter", "search_bar", "mouse_keyboard") else "controller"

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

        pname = (prof.get("process_name") or "").lower().strip()
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
                    if p_info_lower == pname or (pstem and pstem in p_info_lower):
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

        self._notify_mode_changed()
        active_id = self.data.get("active_profile", "")
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
                pname = (profile.get("process_name") or "").lower().strip()
                dname = (profile.get("display_name") or "").lower().strip()

                # A. Match process executable name (exact, stem, or substring)
                if pname and proc_name:
                    pname_stem = os.path.splitext(pname)[0]
                    if (proc_name == pname or
                        pname_stem in proc_name or
                        proc_name in pname or
                        pname in proc_name):
                        return gid

                # B. Match window title containing game display name
                if dname and len(dname) > 3 and dname in window_title:
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
                pname = (profile.get("process_name") or "").lower().strip()
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
                        if p_name_lower == pname or (pstem and pstem in p_name_lower):
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
        detected_id = self.detect_foreground_game()
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

    def get_active_game_context(self) -> str:
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

        # 2. Keybinds Reference (so the model knows which voice commands it can execute)
        keybinds = profile.get("keybinds", {})
        if keybinds:
            macro_lines = [f'- "{phrase}" -> {data.get("description", phrase)}' for phrase, data in keybinds.items()]
            parts.append("AVAILABLE IN-GAME VOICE MACROS (You can trigger these via the trigger_game_action tool):\n" + "\n".join(macro_lines))

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

        # Companion Flight-Deck Tools & MFD Consoles
        companions = profile.get("companion_apps", {})
        if companions:
            comp_lines = []
            for c_id, c_data in companions.items():
                dname = c_data.get("display_name", c_id)
                target = c_data.get("url") or c_data.get("executable", c_id)
                def_mon = c_data.get("default_monitor", 2)
                comp_lines.append(f'- "{dname}" ({c_id}) -> target: "{target}" (recommended display: Monitor {def_mon})')
            parts.append("COMPANION FLIGHT-DECK TOOLS & MFD CONSOLES (Available to launch/navigate on any monitor):\n" + "\n".join(comp_lines))

        # 5. Co-Pilot Role Directive
        parts.append(
            "CO-PILOT ROLE DIRECTIVE:\n"
            "You are an active in-game co-pilot. Pay close attention to current telemetry, player surroundings, and past intel.\n"
            "If the player encounters or approaches a landmark, clue, point of interest, or mission destination that relates to something logged earlier, you may acknowledge or remind the player (e.g., 'Hey, that cave might hold treasure, we should check that out!' or 'Heads up Commander, we have a mission destination in this system.').\n"
            "CRITICAL NON-PROACTIVE RULE FOR PUZZLES & WALKTHROUGHS:\n"
            "- NEVER PROACTIVELY SPOIL OR VOLUNTEER PUZZLE SOLUTIONS, DUNGEON MECHANICS, OR STEP-BY-STEP WALKTHROUGHS UNPROMPTED.\n"
            "- If the player mentions a destination, quest, or puzzle (e.g., 'We are going there', 'Heading to the ruins', 'Let's check out the cave'), acknowledge the journey in-character, but DO NOT explain how to solve or complete it.\n"
            "- ONLY provide puzzle solutions, walkthrough steps, or hints WHEN THE PLAYER EXPLICITLY ASKS FOR HELP (e.g., 'How do I do this?', 'What do I do here?', 'Give me a hint', 'I need help with this puzzle').\n"
            "Use the 'record_copilot_observation' tool whenever you or the player notice a notable landmark, rumor, clue, or hazard worth remembering.\n"
            "Use the 'update_game_scratchpad' tool when the player tells you to note down or remember something on their personal scratchpad."
        )

        # 6. Strict Game Scope & Search Locking Directive
        game_title = profile.get("display_name", active_id)
        universe_name = profile.get("universe_name") or profile.get("world_name") or f"the universe of {game_title}"
        parts.append(
            f"=== STRICT GAME SCOPE & SEARCH LOCK: {game_title.upper()} ===\n"
            f"1. IMMERSION & IN-CHARACTER MANDATE:\n"
            f"   - You are exclusively operating within {universe_name} in {game_title}.\n"
            f"   - Speak, react, and advise solely as the player's in-game companion. Stay 100% in-character at all times.\n"
            f"   - NEVER break character to discuss, explain, or troubleshoot real-world software, programming, Python code, system architecture, desktop bugs, or real-world events.\n"
            f"   - If the player asks about real-world software, code, bugs, or out-of-game matters: DO NOT ANSWER OR DISCUSS SOFTWARE. Deflect in-character and remind the player: 'My focus is on our journey in {game_title}. If you want to talk about real-world topics or code, tell me to \"disable Game Mode\" first.'\n"
            f"   - NEVER search for, reference, or import terminology from unrelated games (e.g. World of Warcraft, Black Desert, Dark Souls, Elder Scrolls, etc.).\n"
            f"   - When discussing game lore, builds, quests, or mechanics, provide rich, thorough, and detailed guidance grounded in {game_title}.\n"
            f"2. GOOGLE SEARCH LOCK: If you perform a Google Search to assist the player, your search query MUST be strictly scoped with \"{game_title}\" (e.g. '\"{game_title}\" <topic>'). Never execute un-scoped generic searches.\n"
            f"3. MAP & WAYPOINT DELEGATION:\n"
            f"   - When the user asks to mark, view, or find a location on their map, delegate directly to `pan_and_mark_map_location` or `assist_game_navigation`.\n"
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
                "- Instead, act as an observant human co-pilot/spotter: verbally guide the player on where to look on their map (e.g. 'Open your map and pan northeast past the river towards Silver Wolf Mountain.').\n"
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

    def scan_elite_dangerous_binds(self) -> Dict[str, dict]:
        """Backward-compatibility alias delegating to modular binds_scanners."""
        prof = self.get_profile("elite_dangerous") or {}
        return scan_profile_bindings({"binds_config": {"type": "elite_xml"}, **prof})

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

    def fetch_web_default_keybinds(self, game_name: str, client, model_endpoint: str = "gemini-2.5-flash") -> Dict[str, dict]:
        """
        Queries online reference knowledge to retrieve standard PC default
        keyboard and mouse bindings for titles without local XML/INI files.
        """
        if not client:
            return {}

        prompt = f"""
Provide the official default PC Keyboard and Mouse keybindings for the game: "{game_name}".
Focus on essential gameplay, UI, movement, and consumable shortcuts (e.g., call mount, open inventory, map, crouch, quick slots).

Return ONLY a JSON dictionary where keys are natural trigger phrases in lowercase (e.g. "call mount", "open map", "crouch"):
{{
    "voice trigger phrase": {{
        "key": "single key string (e.g. 'h', 'm', 'c', 'space', 'i', 'f1')",
        "modifiers": ["optional list of modifier strings like 'shift', 'ctrl' or empty list"],
        "description": "Short explanation of the action",
        "source": "web_defaults"
    }}
}}
Format strictly as valid JSON.
"""
        try:
            res = client.models.generate_content(
                model=model_endpoint,
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )
            raw_text = res.text.strip() if res and hasattr(res, "text") and res.text else ""
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                raw_text = re.sub(r"\s*```$", "", raw_text)

            parsed = json.loads(raw_text) if raw_text else {}
            if isinstance(parsed, dict) and parsed:
                clean_dict = {}
                for k, v in parsed.items():
                    if isinstance(v, dict):
                        v["source"] = "web_defaults"
                        v.setdefault("modifiers", [])
                        v.setdefault("description", k)
                        clean_dict[k.lower().strip()] = v
                return clean_dict
        except Exception as e:
            print(f"[ERROR] [GAME_MGR] Web keybind ingestion failed: {e}")
        return {}

    def sync_game_binds_with_fallback(self, game_id: str, client=None, model_endpoint: str = "gemini-2.5-flash") -> dict:
        """
        1. Tries local XML/INI directory scanning.
        2. If unavailable, fetches published PC defaults from online references.
        3. Merges into profile and persists to data/games_profiles.json.
        """
        profile = self.get_profile(game_id)
        if not profile:
            return {"success": False, "reason": "Profile not found"}

        # 1. Local file scan attempt (modular scanner or heuristic fallback)
        detected = scan_profile_bindings(profile)
        if not detected:
            detected = self.detect_heuristic_config(game_id)
        if detected:
            profile.setdefault("keybinds", {}).update(detected)
            self._save_profiles(self.data)
            return {"success": True, "count": len(detected), "source": "local_files", "keybinds": profile["keybinds"]}

        # 2. Web Reference Fallback
        if client:
            web_binds = self.fetch_web_default_keybinds(profile.get("display_name", game_id), client, model_endpoint)
            if web_binds:
                profile.setdefault("keybinds", {}).update(web_binds)
                self._save_profiles(self.data)
                return {"success": True, "count": len(web_binds), "source": "web_defaults", "keybinds": profile["keybinds"]}

        return {"success": False, "reason": "No local files found and web retrieval returned no valid bindings."}

    def sync_game_binds(self, game_id: str, client=None, model_endpoint: str = "gemini-2.5-flash") -> dict:
        """Attempts local scan or web fallback keybind synchronization."""
        return self.sync_game_binds_with_fallback(game_id, client=client, model_endpoint=model_endpoint)

    def trigger_action(self, phrase: str) -> dict:
        """
        Looks up spoken phrase in the active game profile and executes the action.
        Supports state-conditioned actions, composite sequences, DirectInput combos,
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
            telemetry_adapter=self.active_telemetry_adapter
        )
        if macro_res.get("status") in ("executed", "condition_skipped"):
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
