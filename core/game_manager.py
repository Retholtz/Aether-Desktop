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

from core.game_telemetry import EliteTelemetryWatcher

GAMES_PROFILES_PATH = os.path.join("data", "games_profiles.json")

DEFAULT_PROFILES = {
    "active_profile": "",
    "profiles": {}
}


class GameManager:
    """Manages game profiles, bindings synchronization, and voice/trigger macro execution."""

    def __init__(self, profiles_path: str = GAMES_PROFILES_PATH):
        self.profiles_path = profiles_path
        self.data = self._load_profiles()
        self.ed_watcher: Optional[EliteTelemetryWatcher] = None
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

    def _init_telemetry_watchers(self):
        """Initializes and starts background telemetry watchers for supported games."""
        ed_prof = self.get_profile("elite_dangerous")
        if ed_prof:
            j_dir = ed_prof.get("journal_dir") or ed_prof.get("telemetry_dir")
            if self.ed_watcher:
                if self.ed_watcher.is_running and (j_dir is None or self.ed_watcher.journal_dir == os.path.expandvars(j_dir)):
                    return
                try:
                    self.ed_watcher.stop()
                except Exception:
                    pass
            self.ed_watcher = EliteTelemetryWatcher(journal_dir=j_dir, on_milestone=self._on_telemetry_milestone)
            self.ed_watcher.start()

    def _on_telemetry_milestone(self, category: str, summary: str, location: str = "", details: str = ""):
        """Auto-records game milestone events into the Elite Dangerous Co-Pilot log."""
        self.add_copilot_log_entry(
            game_id="elite_dangerous",
            summary=summary,
            category=category,
            location=location,
            details=details
        )

    def _load_profiles(self) -> Dict[str, Any]:
        """Loads profiles from JSON disk cache or initializes defaults if not present."""
        if os.path.exists(self.profiles_path):
            try:
                with open(self.profiles_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[WARN] [GAME_MGR] Failed reading {self.profiles_path}: {e}")
        initial = copy.deepcopy(DEFAULT_PROFILES)
        self._save_profiles(initial)
        return initial

    def _save_profiles(self, payload: Dict[str, Any]):
        """Persists profile payload to disk."""
        dirname = os.path.dirname(self.profiles_path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        with open(self.profiles_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

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

        if game_id == "elite_dangerous":
            self._init_telemetry_watchers()

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
                self.data["active_profile"] = remaining[0] if remaining else ""
            if game_id == "elite_dangerous" and self.ed_watcher:
                try:
                    self.ed_watcher.stop()
                except Exception:
                    pass
                self.ed_watcher = None
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
            self._save_profiles(self.data)
            self._notify_mode_changed()
            return True
        if game_id in self.data.get("profiles", {}):
            self.data["active_profile"] = game_id
            if self.user_override_game_mode is not False:
                self.game_mode_enabled = True
                self.user_override_game_mode = True
            if game_id == "elite_dangerous" and (not self.ed_watcher or not self.ed_watcher.is_running):
                self._init_telemetry_watchers()
            self._save_profiles(self.data)
            self._notify_mode_changed()
            return True
        return False

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
            from core.screen_stream import ensure_thread_desktop
            ensure_thread_desktop()
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
        if not detected_id:
            detected_id = self.detect_running_game()

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
        if active_id == "elite_dangerous" and self.ed_watcher:
            telemetry_ctx = self.ed_watcher.get_summary_prompt_context()
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

        # 5. Co-Pilot Role Directive
        parts.append(
            "CO-PILOT ROLE DIRECTIVE:\n"
            "You are an active in-game co-pilot. Pay close attention to current telemetry, player surroundings, and past intel.\n"
            "If the player encounters or approaches a landmark, clue, point of interest, or mission destination that relates to something logged earlier, proactively prompt or remind the player (e.g., 'Hey, that cave might hold treasure, we should check that out!' or 'Heads up Commander, we have a mission destination in this system.').\n"
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
            f"   - If a quest or location is ambiguous on screen, ask the player for visual clarification rather than guessing."
        )

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
        if self.ed_watcher:
            try:
                self.ed_watcher.stop()
            except Exception:
                pass
            self.ed_watcher = None

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
        """Scans and extracts active keybinds from Elite Dangerous XML binds files."""
        ed_cfg = self.get_profile("elite_dangerous") or {}
        binds_dir = ed_cfg.get("bindings_dir")
        if not binds_dir:
            return {}

        binds_dir = os.path.expandvars(binds_dir)
        if not os.path.exists(binds_dir):
            return {}

        bind_files = glob.glob(os.path.join(binds_dir, "*.binds"))
        if not bind_files:
            return {}

        # Pick the most recently modified binds file
        latest_file = max(bind_files, key=os.path.getmtime)
        discovered = {}

        try:
            tree = ET.parse(latest_file)
            root = tree.getroot()

            # Target key mapping tags
            target_actions = {
                "DeployHeatSink": ("deploy heat sink", "Deploy Heat Sink"),
                "ToggleFlightAssist": ("toggle flight assist", "Toggle Flight Assist"),
                "ChargeECM": ("charge ecm", "Charge ECM"),
                "IncreaseEnginesPower": ("divert power to engines", "Power to Engines"),
                "IncreaseWeaponsPower": ("divert power to weapons", "Power to Weapons"),
                "IncreaseSystemsPower": ("divert power to systems", "Power to Systems"),
                "ResetPowerDistribution": ("balance power", "Balance Power"),
                "ToggleCargoScoop": ("toggle cargo scoop", "Toggle Cargo Scoop"),
                "LandingGearToggle": ("deploy landing gear", "Toggle Landing Gear"),
                "DeployHardpointToggle": ("deploy hardpoints", "Toggle Hardpoints"),
                "FireChaffLauncher": ("fire chaff", "Fire Chaff"),
                "SelectTarget": ("target ahead", "Target Ahead"),
                "CycleNextTarget": ("next target", "Target Next"),
                "SelectHighestThreat": ("target highest threat", "Target Highest Threat"),
                "HyperSuperCombination": ("frame shift drive", "Toggle Frame Shift Drive"),
            }

            for xml_elem, (phrase, desc) in target_actions.items():
                node = root.find(xml_elem)
                if node is not None:
                    # Prefer Primary binding, fallback to Secondary
                    slot = node.find("Primary")
                    if slot is None or slot.get("Device") != "Keyboard":
                        slot = node.find("Secondary")

                    if slot is not None and slot.get("Device") == "Keyboard":
                        raw_key = slot.get("Key", "").replace("Key_", "").lower()
                        mods = [m.get("Key", "").replace("Key_", "").lower() for m in slot.findall("Modifier")]
                        if raw_key:
                            discovered[phrase] = {
                                "key": raw_key,
                                "modifiers": mods,
                                "description": desc,
                                "source": "auto_detected"
                            }
            return discovered
        except Exception as e:
            print(f"[WARN] [GAME_MGR] Failed parsing Elite Dangerous binds: {e}")
            return {}

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

        # 1. Local file scan attempt
        if game_id == "elite_dangerous":
            detected = self.scan_elite_dangerous_binds()
            if detected:
                profile.setdefault("keybinds", {}).update(detected)
                self._save_profiles(self.data)
                return {"success": True, "count": len(detected), "source": "local_files", "keybinds": profile["keybinds"]}
        else:
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
        Supports DirectInput keyboard combos, mouse clicks, and virtual gamepad buttons/triggers.
        """
        from tools.os_controls import (
            send_directinput_combo,
            send_directinput_key,
            click_mouse,
            zoom_viewport,
            send_gamepad_button,
            send_gamepad_trigger,
        )
        active_id = self.data.get("active_profile")
        profile = self.get_profile(active_id)
        if not profile:
            return {"status": "error", "message": "No active game profile."}

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
