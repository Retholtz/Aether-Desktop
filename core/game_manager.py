"""
Aether Desktop - Game Profile Infrastructure & DirectInput Hardware Action Dispatcher
Manages game profiles, metadata, trigger phrases, custom macros, active game profile switching,
and automatic keybind discovery (including Elite Dangerous XML binds scanner and heuristic config sniffer).
"""

import os
import glob
import json
import copy
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional, List

GAMES_PROFILES_PATH = os.path.join("data", "games_profiles.json")

DEFAULT_PROFILES = {
    "active_profile": "crimson_desert",
    "profiles": {
        "crimson_desert": {
            "display_name": "Crimson Desert",
            "process_name": "CrimsonDesert.exe",
            "telemetry_type": "scratchpad_only",
            "scratchpad": {
                "active_quests": [],
                "crafting_materials": [],
                "general_notes": []
            },
            "keybinds": {
                "open inventory": {"key": "i", "modifiers": [], "description": "Open Inventory"},
                "use health potion": {"key": "h", "modifiers": [], "description": "Quick Health Consumable"},
                "open world map": {"key": "m", "modifiers": [], "description": "Toggle Map"}
            }
        },
        "elite_dangerous": {
            "display_name": "Elite Dangerous",
            "process_name": "EliteDangerous64.exe",
            "telemetry_type": "journal_tail",
            "journal_dir": os.path.expandvars(r"%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous"),
            "bindings_dir": os.path.expandvars(r"%LOCALAPPDATA%\Frontier Developments\Elite Dangerous\Options\Bindings"),
            "scratchpad": {
                "targets": [],
                "trade_notes": []
            },
            "keybinds": {
                "silent running": {"key": "delete", "modifiers": ["shift"], "description": "Toggle Silent Running"},
                "deploy heat sink": {"key": "v", "modifiers": [], "description": "Deploy Heat Sink"},
                "toggle flight assist": {"key": "z", "modifiers": [], "description": "Toggle Flight Assist"}
            }
        }
    }
}


class GameManager:
    """Manages game profiles, bindings synchronization, and voice/trigger macro execution."""

    def __init__(self, profiles_path: str = GAMES_PROFILES_PATH):
        self.profiles_path = profiles_path
        self.data = self._load_profiles()

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

    def get_profile(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves profile dictionary for specified game ID."""
        return self.data.get("profiles", {}).get(game_id)

    def get_active_profile(self) -> Optional[Dict[str, Any]]:
        """Returns the profile dictionary for the currently active game."""
        active_id = self.data.get("active_profile")
        return self.get_profile(active_id)

    def set_active_profile(self, game_id: str) -> bool:
        """Switches the currently active game profile."""
        if game_id in self.data.get("profiles", {}):
            self.data["active_profile"] = game_id
            self._save_profiles(self.data)
            return True
        return False

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

    def sync_game_binds(self, game_id: str) -> dict:
        """Attempts auto-detection; merges into profile while preserving manual overrides."""
        profile = self.get_profile(game_id)
        if not profile:
            return {"success": False, "reason": f"Profile '{game_id}' not found."}

        detected = {}
        if game_id == "elite_dangerous":
            detected = self.scan_elite_dangerous_binds()
        else:
            detected = self.detect_heuristic_config(game_id)

        if detected:
            # Merge: detected keys update base, existing non-conflicting stay
            profile.setdefault("keybinds", {}).update(detected)
            self._save_profiles(self.data)
            return {"success": True, "count": len(detected), "keybinds": profile["keybinds"]}

        return {"success": False, "reason": "No auto-parser available or no files found. Use manual overrides."}

    def trigger_action(self, phrase: str) -> dict:
        """Looks up spoken phrase in the active game profile and executes the DirectInput combo."""
        from tools.os_controls import send_directinput_combo, send_directinput_key
        active_id = self.data.get("active_profile")
        profile = self.get_profile(active_id)
        if not profile:
            return {"status": "error", "message": "No active game profile."}

        keybinds = profile.get("keybinds", {})
        phrase_clean = phrase.lower().strip()

        # Match trigger phrase
        action = keybinds.get(phrase_clean)
        if not action:
            return {"status": "ignored", "message": f"No macro mapped to '{phrase}' in {profile['display_name']}."}

        key = action.get("key")
        mods = action.get("modifiers", [])
        if mods:
            send_directinput_combo(mods, key)
        else:
            send_directinput_key(key)

        return {"status": "executed", "game": profile["display_name"], "action": action.get("description", phrase)}
