"""
Aether Desktop - Program Manager & Application Whitelist Subsystem
Manages whitelisted desktop programs, executable paths, launch arguments/extensions (e.g. --elevate),
administrative elevation via Windows UAC, deep process resolution across Steam libraries,
Start Menu shortcuts, Windows Registry, and running processes.
"""

import copy
import ctypes
import glob
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("Aether.ProgramManager")

PROGRAMS_PATH = os.path.join("data", "programs.json")
GAMES_DIR = os.path.join("data", "games")
GAMES_PROFILES_PATH = os.path.join("data", "games_profiles.json")

DEFAULT_PROGRAMS = {
    "active_program": "google_chrome",
    "programs": {
        "google_chrome": {
            "id": "google_chrome",
            "name": "Google Chrome",
            "path": "chrome.exe",
            "arguments": "",
            "elevate": False,
            "working_dir": ""
        },
        "notepad": {
            "id": "notepad",
            "name": "Notepad",
            "path": "notepad.exe",
            "arguments": "",
            "elevate": False,
            "working_dir": ""
        },
        "file_explorer": {
            "id": "file_explorer",
            "name": "File Explorer",
            "path": "explorer.exe",
            "arguments": "",
            "elevate": False,
            "working_dir": ""
        }
    }
}


class ProgramManager:
    """Manages whitelisted applications, execution arguments, elevation, and process lifecycle."""

    def __init__(self, programs_path: str = PROGRAMS_PATH, config_mgr=None):
        self.programs_path = programs_path
        self.config_mgr = config_mgr
        self._lock = threading.RLock()
        self.data = self._load_programs()

    def _load_programs(self) -> Dict[str, Any]:
        """Loads programs from disk or migrates from config.json and games_profiles.json."""
        with self._lock:
            if os.path.exists(self.programs_path):
                try:
                    with open(self.programs_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict) and "programs" in data:
                        self.data = data
                        synced = self.sync_from_games(target_payload=self.data)
                        deduped = self._deduplicate_programs(self.data)
                        if synced or deduped:
                            self._save_programs(self.data)
                        return self.data
                except Exception as e:
                    logger.warning(f"[PROGRAM_MGR] Failed reading {self.programs_path}: {e}")

            # Initialize defaults and migrate existing whitelist and games
            initial = copy.deepcopy(DEFAULT_PROGRAMS)
            self.data = initial
            self._import_from_config(initial)
            self.sync_from_games(target_payload=initial)
            self._deduplicate_programs(initial)
            self._save_programs(initial)
            return initial

    def _deduplicate_programs(self, payload: Dict[str, Any]) -> bool:
        """Removes duplicate program entries with identical normalized names or executable paths."""
        programs = payload.get("programs", {})
        seen_names = {}  # name_norm -> primary_pid
        seen_paths = {}  # path_norm -> primary_pid
        to_delete = []
        changed = False

        for pid, p in list(programs.items()):
            name = (p.get("name") or "").strip()
            path = (p.get("path") or "").strip()
            name_norm = re.sub(r'[^a-zA-Z0-9]+', '', name.lower())
            path_norm = os.path.normpath(path).lower() if (path and os.path.isabs(path)) else (os.path.basename(path).lower() if path else "")

            primary_pid = None
            if name_norm and name_norm in seen_names:
                primary_pid = seen_names[name_norm]
            elif path_norm and path_norm in seen_paths:
                primary_pid = seen_paths[path_norm]

            if primary_pid and primary_pid != pid:
                primary = programs[primary_pid]
                if not primary.get("path") and path:
                    primary["path"] = path
                if not primary.get("arguments") and p.get("arguments"):
                    primary["arguments"] = p["arguments"]
                if not primary.get("elevate") and p.get("elevate"):
                    primary["elevate"] = p["elevate"]
                if not primary.get("working_dir") and p.get("working_dir"):
                    primary["working_dir"] = p["working_dir"]

                # If current item has spaced/formatted name and primary does not, adopt it
                p_name = p.get("name", "")
                prim_name = primary.get("name", "")
                if " " in p_name and " " not in prim_name:
                    primary["name"] = p_name

                to_delete.append(pid)
                changed = True
            else:
                if name_norm:
                    seen_names[name_norm] = pid
                if path_norm:
                    seen_paths[path_norm] = pid

        for pid in to_delete:
            if pid in programs:
                del programs[pid]

        active = payload.get("active_program")
        if active in to_delete or not active or active not in programs:
            payload["active_program"] = next(iter(programs.keys())) if programs else ""
            changed = True

        return changed

    def _import_from_config(self, payload: Dict[str, Any]):
        """Imports existing items from config.json security.app_whitelist if available."""
        try:
            from core.config_manager import load_config
            cfg = load_config()
            existing_wl = cfg.get("security", {}).get("app_whitelist", [])
            for item in existing_wl:
                if not item or item == "*":
                    continue
                clean = item.strip()
                clean_no_ext = clean[:-4] if clean.lower().endswith(".exe") else clean
                clean_norm = re.sub(r'[^a-zA-Z0-9]+', '', clean_no_ext.lower())
                already_exists = False
                for p in payload["programs"].values():
                    p_path = os.path.basename(p.get("path", "")).lower()
                    p_name = p.get("name", "").lower()
                    p_norm = re.sub(r'[^a-zA-Z0-9]+', '', p_name)
                    if (clean.lower() in (p_path, p_name) or
                        clean_no_ext.lower() in (p_path.replace(".exe", ""), p_name) or
                        (clean_norm and clean_norm == p_norm)):
                        already_exists = True
                        break
                if already_exists:
                    continue

                pid = re.sub(r'[^a-zA-Z0-9]+', '_', clean_no_ext.lower()).strip('_')
                if pid and pid not in payload["programs"]:
                    display_name = clean_no_ext.replace("_", " ").title()
                    payload["programs"][pid] = {
                        "id": pid,
                        "name": display_name,
                        "path": clean,
                        "arguments": "",
                        "elevate": False,
                        "working_dir": ""
                    }
        except Exception as e:
            logger.debug(f"[PROGRAM_MGR] Could not import config whitelist: {e}")

    def sync_from_games(
        self,
        game_profiles: Optional[Dict[str, Any]] = None,
        target_payload: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Synchronizes all game profiles from data/games/*.json and data/games_profiles.json
        into the ProgramManager allowed programs list.
        Resolves executable paths and Steam AppIDs, preserves custom user launch arguments and elevation,
        and saves changes to programs.json and config.json.
        Returns True if changes were made, False otherwise.
        """
        with self._lock:
            payload = target_payload if target_payload is not None else getattr(self, "data", {})
            if not isinstance(payload, dict):
                return False
            programs = payload.setdefault("programs", {})

            profiles: Dict[str, Dict[str, Any]] = {}

            # 1. Directory mode is the primary source of truth for games
            has_dir_games = False
            if os.path.exists(GAMES_DIR):
                try:
                    for fname in os.listdir(GAMES_DIR):
                        if fname.endswith(".json") and fname != "active_state.json":
                            fpath = os.path.join(GAMES_DIR, fname)
                            try:
                                with open(fpath, "r", encoding="utf-8") as f:
                                    p = json.load(f)
                                    if isinstance(p, dict):
                                        gid = os.path.splitext(fname)[0]
                                        profiles[gid] = p
                                        has_dir_games = True
                            except Exception as e:
                                logger.debug(f"[PROGRAM_MGR] Could not read game profile {fname}: {e}")
                except Exception as e:
                    logger.debug(f"[PROGRAM_MGR] Could not scan games directory: {e}")

            # 2. Legacy fallback: ONLY if directory mode had no games
            if not has_dir_games and os.path.exists(GAMES_PROFILES_PATH):
                try:
                    with open(GAMES_PROFILES_PATH, "r", encoding="utf-8") as f:
                        gdata = json.load(f)
                    gp_dict = gdata.get("profiles", {}) if (isinstance(gdata, dict) and "profiles" in gdata) else gdata
                    if isinstance(gp_dict, dict):
                        for gid, p in gp_dict.items():
                            if isinstance(p, dict):
                                profiles[gid] = copy.deepcopy(p)
                except Exception as e:
                    logger.debug(f"[PROGRAM_MGR] Could not read games_profiles.json: {e}")

            # 3. Enrich steam_appid metadata for existing loaded profiles
            if os.path.exists(GAMES_PROFILES_PATH):
                try:
                    with open(GAMES_PROFILES_PATH, "r", encoding="utf-8") as f:
                        gdata = json.load(f)
                    gp_dict = gdata.get("profiles", {}) if (isinstance(gdata, dict) and "profiles" in gdata) else gdata
                    if isinstance(gp_dict, dict):
                        for gid, p in profiles.items():
                            if not p.get("steam_appid") and gid in gp_dict and gp_dict[gid].get("steam_appid"):
                                p["steam_appid"] = gp_dict[gid]["steam_appid"]
                except Exception:
                    pass

            # 3. Merge explicit game_profiles passed in (e.g. from GameManager or tests)
            if game_profiles and isinstance(game_profiles, dict):
                for gid, p in game_profiles.items():
                    if isinstance(p, dict):
                        if gid in profiles:
                            profiles[gid].update(p)
                        else:
                            profiles[gid] = copy.deepcopy(p)

            if not profiles:
                return False

            changed = False

            for gid, p in profiles.items():
                dname = (p.get("display_name") or gid).strip()
                pname = (p.get("process_name") or "").strip()
                appid = p.get("steam_appid")

                pid = re.sub(r'[^a-zA-Z0-9]+', '_', dname.lower()).strip('_') or gid
                resolved = ""
                if pname and os.path.isabs(pname) and os.path.exists(pname):
                    resolved = pname
                else:
                    resolved = self.resolve_program_path(pname or dname) or self.resolve_program_path(dname) or pname

                cwd = os.path.dirname(resolved) if resolved and os.path.isabs(resolved) else ""

                # Check if already present under another id or variant name
                matched_id = None
                extra_ids_to_remove = []
                dname_clean = re.sub(r'[^a-zA-Z0-9]+', '', dname.lower())
                pname_base = os.path.basename(pname).lower() if pname else ""
                pname_stem = pname_base[:-4] if pname_base.endswith(".exe") else pname_base

                for existing_id, ep in list(programs.items()):
                    e_path = (ep.get("path") or "").strip()
                    e_base = os.path.basename(e_path).lower() if e_path else ""
                    e_stem = e_base[:-4] if e_base.endswith(".exe") else e_base
                    e_name = (ep.get("name") or "").strip()
                    e_name_clean = re.sub(r'[^a-zA-Z0-9]+', '', e_name.lower())

                    is_match = False
                    if existing_id in (pid, gid):
                        is_match = True
                    elif e_name.lower() == dname.lower() or (dname_clean and e_name_clean == dname_clean):
                        is_match = True
                    elif pname_base and (pname_base == e_base or pname_stem == e_stem):
                        is_match = True
                    elif resolved and e_path and os.path.normcase(os.path.abspath(resolved)) == os.path.normcase(os.path.abspath(e_path)):
                        is_match = True
                    elif dname_clean and (dname_clean in e_name_clean or e_name_clean in dname_clean):
                        if not e_path or not os.path.exists(e_path) or (resolved and e_path and os.path.normcase(resolved) == os.path.normcase(e_path)):
                            is_match = True

                    if is_match:
                        if existing_id == pid:
                            matched_id = existing_id
                        elif matched_id is None:
                            matched_id = existing_id
                        else:
                            extra_ids_to_remove.append(existing_id)

                target_id = pid
                saved_args = ""
                saved_elevate = False
                saved_cwd = ""
                saved_path = ""

                if matched_id:
                    existing_entry = programs.get(matched_id, {})
                    saved_args = existing_entry.get("arguments", "")
                    saved_elevate = existing_entry.get("elevate", False)
                    saved_cwd = existing_entry.get("working_dir", "")
                    saved_path = existing_entry.get("path", "")

                    if matched_id != target_id:
                        del programs[matched_id]
                        changed = True
                        if payload.get("active_program") == matched_id:
                            payload["active_program"] = target_id

                for xid in extra_ids_to_remove:
                    x_entry = programs.get(xid, {})
                    if not saved_args and x_entry.get("arguments"):
                        saved_args = x_entry["arguments"]
                    if not saved_elevate and x_entry.get("elevate"):
                        saved_elevate = x_entry["elevate"]
                    if xid in programs:
                        del programs[xid]
                        changed = True
                    if payload.get("active_program") == xid:
                        payload["active_program"] = target_id

                final_path = resolved or saved_path or pname
                final_cwd = cwd or saved_cwd
                if not final_cwd and final_path and os.path.isabs(final_path):
                    final_cwd = os.path.dirname(final_path)

                prog_entry = {
                    "id": target_id,
                    "name": dname,
                    "path": final_path,
                    "arguments": saved_args,
                    "elevate": saved_elevate,
                    "working_dir": final_cwd
                }
                if appid:
                    prog_entry["steam_appid"] = appid

                current_entry = programs.get(target_id)
                if current_entry != prog_entry:
                    programs[target_id] = prog_entry
                    changed = True

            if changed and target_payload is None:
                self._deduplicate_programs(self.data)
                self._save_programs(self.data)

            return changed

    def _import_from_games(self, payload: Optional[Dict[str, Any]] = None):
        """Imports game titles (e.g. Crimson Desert, Elite Dangerous) from games directory into programs."""
        return self.sync_from_games(target_payload=payload)

    def _save_programs(self, payload: Dict[str, Any]):
        """Persists programs payload to disk and syncs with config.json whitelist."""
        with self._lock:
            dirname = os.path.dirname(self.programs_path)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
            with open(self.programs_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            self._sync_to_config_whitelist(payload)

    def _sync_to_config_whitelist(self, payload: Dict[str, Any]):
        """Synchronizes allowed executable names to config.json security.app_whitelist."""
        try:
            wl = []
            for p in payload.get("programs", {}).values():
                path = p.get("path", "").strip()
                name = p.get("name", "").strip()
                if path:
                    base = os.path.basename(path).lower()
                    if base not in wl:
                        wl.append(base)
                if name:
                    clean_name = name.lower()
                    if clean_name not in wl:
                        wl.append(clean_name)
                    if not clean_name.endswith(".exe") and f"{clean_name}.exe" not in wl:
                        wl.append(f"{clean_name}.exe")

            from core.config_manager import load_config, save_config
            cfg = load_config()
            cfg.setdefault("security", {})["app_whitelist"] = wl
            save_config(cfg)
        except Exception as e:
            logger.debug(f"[PROGRAM_MGR] Error syncing config whitelist: {e}")

    # -------------------------------------------------------------------------
    # CRUD API
    # -------------------------------------------------------------------------
    def get_whitelist_names(self) -> List[str]:
        """Returns the list of whitelisted program executable names, stems, and aliases."""
        with self._lock:
            wl: List[str] = []
            for p in self.data.get("programs", {}).values():
                path = (p.get("path") or "").strip()
                name = (p.get("name") or "").strip()
                if path:
                    base = os.path.basename(path).lower()
                    if base and base not in wl:
                        wl.append(base)
                    stem = base[:-4] if base.endswith(".exe") else base
                    if stem and stem not in wl:
                        wl.append(stem)
                if name:
                    clean_name = name.lower()
                    if clean_name and clean_name not in wl:
                        wl.append(clean_name)
                    if not clean_name.endswith(".exe") and f"{clean_name}.exe" not in wl:
                        wl.append(f"{clean_name}.exe")

            # Merge from config.json security whitelist if available
            try:
                from core.config_manager import load_config
                cfg = load_config()
                cfg_wl = cfg.get("security", {}).get("app_whitelist", [])
                for item in cfg_wl:
                    s_item = str(item).strip().lower()
                    if s_item and s_item not in wl:
                        wl.append(s_item)
            except Exception:
                pass
            return wl

    def list_programs(self) -> Dict[str, Any]:
        """Returns all configured programs (alphabetically sorted by name), active program, and process running status."""
        with self._lock:
            synced = self.sync_from_games()
            progs = copy.deepcopy(self.data.get("programs", {}))
            changed = False
            for pid, p in progs.items():
                curr_path = p.get("path", "").strip()
                if not curr_path or not os.path.exists(curr_path):
                    resolved = self.resolve_program_path(p.get("name", pid))
                    if resolved and os.path.exists(resolved):
                        p["path"] = resolved
                        if not p.get("working_dir"):
                            p["working_dir"] = os.path.dirname(resolved)
                        if pid in self.data.get("programs", {}):
                            self.data["programs"][pid]["path"] = resolved
                            self.data["programs"][pid]["working_dir"] = p["working_dir"]
                        changed = True
            if changed:
                self._save_programs(self.data)

            sorted_progs = dict(sorted(progs.items(), key=lambda kv: (kv[1].get("name") or kv[0]).lower()))
            return {
                "active_program": self.data.get("active_program", ""),
                "programs": sorted_progs,
                "running_status": self.get_running_status_map()
            }

    def get_program(self, prog_id_or_name: str) -> Optional[Dict[str, Any]]:
        """Looks up program by ID, display name, executable path, or alias."""
        if not prog_id_or_name:
            return None
        target = prog_id_or_name.strip().lower()
        target_no_ext = target[:-4] if target.endswith(".exe") else target
        target_norm = re.sub(r'[^a-zA-Z0-9]+', '', target)

        with self._lock:
            programs = self.data.get("programs", {})
            # 1. Exact ID
            if target in programs:
                return programs[target]

            # 2. Pass 1: Exact matches across all configured programs
            for pid, p in programs.items():
                p_name = (p.get("name") or "").strip().lower()
                p_name_norm = re.sub(r'[^a-zA-Z0-9]+', '', p_name)
                p_path = (p.get("path") or "").strip().lower()
                p_exe = os.path.basename(p_path)
                p_exe_stem = p_exe[:-4] if p_exe.endswith(".exe") else p_exe

                if (target == pid or
                    target == p_name or
                    target_no_ext == p_name or
                    target == p_exe or
                    target_no_ext == p_exe_stem or
                    (target_norm and target_norm == p_name_norm)):
                    return p

            # 3. Pass 2: Substring / Prefix / Conversational matches
            # e.g., "crimson" matches "Crimson Desert", "chrome" matches "Google Chrome"
            clean_conv = re.sub(r'^(open|launch|start|run|play|execute)\s+', '', target).strip()
            for pid, p in programs.items():
                p_name = (p.get("name") or "").strip().lower()
                p_name_norm = re.sub(r'[^a-zA-Z0-9]+', '', p_name)
                p_path = (p.get("path") or "").strip().lower()
                p_exe = os.path.basename(p_path)
                p_exe_stem = p_exe[:-4] if p_exe.endswith(".exe") else p_exe

                if (clean_conv and clean_conv == p_name) or (clean_conv and clean_conv == p_exe_stem):
                    return p
                if target_norm and (target_norm in p_name_norm or target_norm in p_exe_stem.lower()):
                    return p

            # 3. Check games profiles fallback
            games_profiles = {}
            if os.path.exists(GAMES_DIR):
                for fname in os.listdir(GAMES_DIR):
                    if fname.endswith(".json") and fname != "active_state.json":
                        try:
                            with open(os.path.join(GAMES_DIR, fname), "r", encoding="utf-8") as f:
                                gp = json.load(f)
                                if isinstance(gp, dict):
                                    games_profiles[os.path.splitext(fname)[0]] = gp
                        except Exception:
                            pass
            elif os.path.exists(GAMES_PROFILES_PATH):
                try:
                    with open(GAMES_PROFILES_PATH, "r", encoding="utf-8") as f:
                        gdata = json.load(f)
                    games_profiles = gdata.get("profiles", {})
                except Exception:
                    pass

            for gid, gp in games_profiles.items():
                g_name = (gp.get("display_name") or "").strip().lower()
                g_proc = (gp.get("process_name") or "").strip().lower()
                g_stem = g_proc[:-4] if g_proc.endswith(".exe") else g_proc
                if target in (gid, g_name, g_proc, g_stem) or (target_norm and target_norm in re.sub(r'[^a-zA-Z0-9]+', '', g_name)):
                    # Return pseudo program dict
                    resolved = self.resolve_program_path(g_proc or g_name) or g_proc
                    return {
                        "id": gid,
                        "name": gp.get("display_name", gid),
                        "path": resolved,
                        "arguments": "",
                        "elevate": False,
                        "working_dir": os.path.dirname(resolved) if resolved and os.path.isabs(resolved) else ""
                    }

        return None

    def add_program(
        self,
        name: str,
        path: str = "",
        arguments: str = "",
        elevate: bool = False,
        working_dir: str = ""
    ) -> Dict[str, Any]:
        """Creates a new whitelisted program entry."""
        name_clean = name.strip()
        if not name_clean:
            return {"success": False, "error": "Program name cannot be empty."}

        with self._lock:
            # Auto-resolve path if relative or missing
            resolved_path = path.strip()
            if not resolved_path or not os.path.isabs(resolved_path):
                detected = self.resolve_program_path(resolved_path or name_clean)
                if detected:
                    resolved_path = detected

            cwd = working_dir.strip()
            if not cwd and resolved_path and os.path.isabs(resolved_path):
                cwd = os.path.dirname(resolved_path)

            name_norm = re.sub(r'[^a-zA-Z0-9]+', '', name_clean.lower())
            path_norm = os.path.normpath(resolved_path).lower() if (resolved_path and os.path.isabs(resolved_path)) else (os.path.basename(resolved_path).lower() if resolved_path else "")

            # Check if this program already exists
            matched_id = None
            for pid, p in self.data.get("programs", {}).items():
                p_name_norm = re.sub(r'[^a-zA-Z0-9]+', '', (p.get("name") or "").lower())
                p_path = p.get("path") or ""
                p_path_norm = os.path.normpath(p_path).lower() if (p_path and os.path.isabs(p_path)) else (os.path.basename(p_path).lower() if p_path else "")

                if name_norm and name_norm == p_name_norm:
                    matched_id = pid
                    break

            if matched_id:
                existing = self.data["programs"][matched_id]
                if resolved_path and (not existing.get("path") or os.path.isabs(resolved_path)):
                    existing["path"] = resolved_path
                if arguments:
                    existing["arguments"] = arguments.strip()
                if elevate is not None:
                    existing["elevate"] = bool(elevate)
                if cwd:
                    existing["working_dir"] = cwd
                self.data["active_program"] = matched_id
                self._save_programs(self.data)
                return {
                    "success": True,
                    "program_id": matched_id,
                    "program": existing,
                    "active_program": self.data.get("active_program")
                }

            base_id = re.sub(r'[^a-zA-Z0-9]+', '_', name_clean.lower()).strip('_') or "program"
            prog_id = base_id
            counter = 2
            while prog_id in self.data.get("programs", {}):
                prog_id = f"{base_id}_{counter}"
                counter += 1

            program = {
                "id": prog_id,
                "name": name_clean,
                "path": resolved_path,
                "arguments": arguments.strip(),
                "elevate": bool(elevate),
                "working_dir": cwd
            }

            self.data.setdefault("programs", {})[prog_id] = program
            self.data["active_program"] = prog_id

            self._save_programs(self.data)
            return {
                "success": True,
                "program_id": prog_id,
                "program": program,
                "active_program": self.data.get("active_program")
            }

    def batch_add_programs(self, programs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Adds multiple programs to the whitelist in a single atomic transaction.
        Synchronizes with security.app_whitelist and disk persistence once.
        """
        if not programs or not isinstance(programs, list):
            return {"success": True, "added_count": 0, "added": []}

        added = []
        with self._lock:
            for item in programs:
                if not isinstance(item, dict):
                    continue
                name_clean = str(item.get("name") or item.get("display_name") or "").strip()
                if not name_clean:
                    continue

                path_val = str(item.get("path") or "").strip()
                args_val = str(item.get("arguments") or item.get("launch_args") or item.get("args") or "").strip()
                elev_val = bool(item.get("elevate", False))
                cwd_val = str(item.get("working_dir") or item.get("cwd") or "").strip()

                # Auto-resolve path if missing or relative
                resolved_path = path_val
                if not resolved_path or not os.path.isabs(resolved_path):
                    detected = self.resolve_program_path(resolved_path or name_clean)
                    if detected:
                        resolved_path = detected

                if not cwd_val and resolved_path and os.path.isabs(resolved_path):
                    cwd_val = os.path.dirname(resolved_path)

                name_norm = re.sub(r'[^a-zA-Z0-9]+', '', name_clean.lower())

                # Check if this program already exists
                matched_id = None
                for pid, p in self.data.get("programs", {}).items():
                    p_name_norm = re.sub(r'[^a-zA-Z0-9]+', '', (p.get("name") or "").lower())
                    if name_norm and name_norm == p_name_norm:
                        matched_id = pid
                        break

                if matched_id:
                    existing = self.data["programs"][matched_id]
                    if resolved_path and (not existing.get("path") or os.path.isabs(resolved_path)):
                        existing["path"] = resolved_path
                    if args_val:
                        existing["arguments"] = args_val
                    if elev_val:
                        existing["elevate"] = elev_val
                    if cwd_val:
                        existing["working_dir"] = cwd_val
                    added.append(existing)
                    continue

                base_id = re.sub(r'[^a-zA-Z0-9]+', '_', name_clean.lower()).strip('_') or "program"
                prog_id = base_id
                counter = 2
                while prog_id in self.data.get("programs", {}):
                    prog_id = f"{base_id}_{counter}"
                    counter += 1

                new_prog = {
                    "id": prog_id,
                    "name": name_clean,
                    "path": resolved_path,
                    "arguments": args_val,
                    "elevate": elev_val,
                    "working_dir": cwd_val
                }
                self.data.setdefault("programs", {})[prog_id] = new_prog
                added.append(new_prog)

            if added:
                self.data["active_program"] = added[-1]["id"]
                self._save_programs(self.data)

        return {"success": True, "added_count": len(added), "added": added}

    def update_program(
        self,
        prog_id: str,
        name: str,
        path: str,
        arguments: str = "",
        elevate: bool = False,
        working_dir: str = ""
    ) -> Dict[str, Any]:
        """Updates an existing whitelisted program configuration."""
        with self._lock:
            programs = self.data.get("programs", {})
            if prog_id not in programs:
                return {"success": False, "error": f"Program '{prog_id}' not found."}

            resolved_path = path.strip()
            if resolved_path and not os.path.isabs(resolved_path):
                detected = self.resolve_program_path(resolved_path)
                if detected:
                    resolved_path = detected

            cwd = working_dir.strip()
            if not cwd and resolved_path and os.path.isabs(resolved_path):
                cwd = os.path.dirname(resolved_path)

            programs[prog_id]["name"] = name.strip() or programs[prog_id]["name"]
            programs[prog_id]["path"] = resolved_path
            programs[prog_id]["arguments"] = arguments.strip()
            programs[prog_id]["elevate"] = bool(elevate)
            programs[prog_id]["working_dir"] = cwd

            self._save_programs(self.data)
            return {"success": True, "program": programs[prog_id]}

    def delete_program(self, prog_id: str) -> Dict[str, Any]:
        """Removes a program from the whitelist."""
        with self._lock:
            programs = self.data.get("programs", {})
            if prog_id in programs:
                del programs[prog_id]
                if self.data.get("active_program") == prog_id:
                    rem = list(programs.keys())
                    self.data["active_program"] = rem[0] if rem else ""
                self._save_programs(self.data)
                return {
                    "success": True,
                    "deleted_id": prog_id,
                    "active_program": self.data.get("active_program")
                }
            return {"success": False, "error": f"Program '{prog_id}' not found."}

    def set_active_program(self, prog_id: str) -> bool:
        """Sets the selected program in UI."""
        with self._lock:
            if not prog_id:
                self.data["active_program"] = ""
                self._save_programs(self.data)
                return True
            if prog_id in self.data.get("programs", {}):
                self.data["active_program"] = prog_id
                self._save_programs(self.data)
                return True
            return False

    def is_whitelisted(self, app_name_or_exe: str) -> bool:
        """Checks if the application is permitted by the program whitelist."""
        if not app_name_or_exe:
            return False
        clean = app_name_or_exe.strip().lower()
        if clean in ("*", "all"):
            return True
        return self.get_program(clean) is not None

    def get_installed_programs(self) -> List[Dict[str, Any]]:
        """
        Discovers installed desktop applications on Windows by scanning:
        1. Windows Start Menu application shortcuts (.lnk files)
        2. Windows Registry Uninstall keys (HKLM 64-bit, HKLM 32-bit WOW64, and HKCU)

        Filters out background noise, drivers, hotfixes, system updates, and runtime packages.
        Marks whether each program is currently whitelisted.
        """
        results: Dict[str, Dict[str, Any]] = {}
        if sys.platform != "win32":
            return []

        ignore_keywords = [
            'update for', 'security update', 'hotfix', 'redistributable',
            'language pack', 'driver package', 'realtek', 'nvidia graphics driver',
            'intel(r) management engine', 'microsoft visual c++', 'microsoft .net',
            'windows software development kit', 'vulkan run time libraries',
            'amd chipset software', 'installshield'
        ]

        bad_shortcut_terms = [
            'uninstall', 'help', 'readme', 'documentation', 'manual',
            'website', 'release notes', 'license', 'configuration tool',
            'diagnostics', 'crashreporter'
        ]

        # 1. Start Menu shortcuts
        try:
            import win32com.client
            wscript = win32com.client.Dispatch("WScript.Shell")
            start_dirs = [
                os.path.join(os.environ.get("PROGRAMDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
                os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")
            ]
            for sdir in start_dirs:
                if not os.path.exists(sdir):
                    continue
                for root, _, files in os.walk(sdir):
                    for f in files:
                        if not f.lower().endswith(".lnk"):
                            continue
                        s_name = f[:-4].strip()
                        lower_sname = s_name.lower()
                        if any(b in lower_sname for b in bad_shortcut_terms):
                            continue
                        fp = os.path.join(root, f)
                        try:
                            sc = wscript.CreateShortcut(fp)
                            target = sc.TargetPath
                            if target and target.lower().endswith(".exe") and os.path.exists(target):
                                t_base = os.path.basename(target).lower()
                                if any(b in t_base for b in ['unins', 'setup', 'update', 'crashreporter', 'helper', 'installer']):
                                    continue
                                norm_key = re.sub(r'[^a-zA-Z0-9]+', '', lower_sname)
                                if norm_key:
                                    results[norm_key] = {
                                        "name": s_name,
                                        "path": os.path.normpath(target),
                                        "publisher": "",
                                        "source": "Start Menu"
                                    }
                        except Exception:
                            pass
        except Exception as e:
            logger.debug(f"[PROGRAM_MGR] Start menu scan error: {e}")

        # 2. Windows Registry
        try:
            import winreg
            roots = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")
            ]
            for root, subkey in roots:
                try:
                    with winreg.OpenKey(root, subkey) as key:
                        count = winreg.QueryInfoKey(key)[0]
                        for i in range(count):
                            try:
                                kname = winreg.EnumKey(key, i)
                                with winreg.OpenKey(key, kname) as subk:
                                    def q(val_name):
                                        try:
                                            return winreg.QueryValueEx(subk, val_name)[0]
                                        except Exception:
                                            return None
                                    disp = q("DisplayName")
                                    if not disp or not str(disp).strip() or len(str(disp).strip()) < 2:
                                        continue
                                    disp = str(disp).strip()
                                    if q("SystemComponent") == 1 or q("ParentKeyName") or q("NoDisplay") == 1:
                                        continue
                                    ldisp = disp.lower()
                                    if any(bad in ldisp for bad in ignore_keywords) or re.search(r'kb\d{5,}', ldisp):
                                        continue
                                    publisher = str(q("Publisher") or "").strip()
                                    raw_icon = str(q("DisplayIcon") or "").strip().strip('\"')
                                    clean_icon = re.sub(r',\s*-?\d+$', '', raw_icon).strip('\"')
                                    exe_path = ""
                                    # Deep resolution prioritizes the actual runtime client (e.g. EliteDangerous64.exe over EDLaunch.exe)
                                    deep_exe = self.resolve_program_path(disp)
                                    if deep_exe and os.path.exists(deep_exe):
                                        exe_path = deep_exe
                                    elif clean_icon.lower().endswith(".exe") and os.path.exists(clean_icon):
                                        t_base = os.path.basename(clean_icon).lower()
                                        if not any(b in t_base for b in ['unins', 'setup', 'update', 'crashreporter', 'helper', 'installer']):
                                            exe_path = os.path.normpath(clean_icon)
                                    else:
                                        loc = str(q("InstallLocation") or "").strip().strip('\"')
                                        if loc and os.path.isdir(loc):
                                            try:
                                                for rf in sorted(os.listdir(loc)):
                                                    if rf.lower().endswith(".exe") and not any(u in rf.lower() for u in ['unins', 'setup', 'update', 'crash', 'reporter', 'identifier', 'dxsetup', 'helper']):
                                                        p = os.path.join(loc, rf)
                                                        if os.path.isfile(p):
                                                            exe_path = os.path.normpath(p)
                                                            break
                                                if not exe_path:
                                                    for root_dir, _, files in os.walk(loc):
                                                        depth = root_dir[len(loc):].count(os.sep)
                                                        if depth > 2:
                                                            continue
                                                        for fn in sorted(files):
                                                            if fn.lower().endswith(".exe"):
                                                                fn_low = fn.lower()
                                                                if not any(u in fn_low for u in ['unins', 'setup', 'update', 'crash', 'reporter', 'identifier', 'dxsetup', 'helper', 'crashpad']):
                                                                    p = os.path.join(root_dir, fn)
                                                                    if os.path.isfile(p):
                                                                        exe_path = os.path.normpath(p)
                                                                        break
                                                        if exe_path:
                                                            break
                                            except Exception:
                                                pass

                                    norm_key = re.sub(r'[^a-zA-Z0-9]+', '', ldisp)
                                    if not norm_key:
                                        continue

                                    if norm_key in results:
                                        if not results[norm_key]["publisher"] and publisher:
                                            results[norm_key]["publisher"] = publisher
                                        if not results[norm_key]["path"] and exe_path:
                                            results[norm_key]["path"] = exe_path
                                    else:
                                        results[norm_key] = {
                                            "name": disp,
                                            "path": exe_path,
                                            "publisher": publisher,
                                            "source": "Registry"
                                        }
                            except Exception:
                                pass
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"[PROGRAM_MGR] Registry scan error: {e}")

        # 3. Steam Libraries Discovery (across all library drives)
        try:
            steam_libs = self.find_steam_libraries()
            for lib in steam_libs:
                steamapps = os.path.join(lib, "steamapps")
                if not os.path.exists(steamapps):
                    continue
                for f in os.listdir(steamapps):
                    if f.startswith("appmanifest_") and f.endswith(".acf"):
                        try:
                            with open(os.path.join(steamapps, f), "r", encoding="utf-8", errors="ignore") as acf:
                                txt = acf.read()
                                name_m = re.search(r'"name"\s+"([^"]+)"', txt)
                                dir_m = re.search(r'"installdir"\s+"([^"]+)"', txt)
                                appid_m = re.search(r'"appid"\s+"([^"]+)"', txt)
                                name = name_m.group(1).strip() if name_m else ""
                                dir_name = dir_m.group(1).strip() if dir_m else ""
                                appid = appid_m.group(1).strip() if appid_m else ""
                                if not name or "steamworks" in name.lower() or appid == "228980":
                                    continue
                                full_dir = os.path.join(steamapps, "common", dir_name)
                                exe_path = self.resolve_program_path(name) or ""
                                if not exe_path or not os.path.exists(exe_path):
                                    if os.path.isdir(full_dir):
                                        for root_dir, _, files in os.walk(full_dir):
                                            depth = root_dir[len(full_dir):].count(os.sep)
                                            if depth > 2:
                                                continue
                                            for fn in sorted(files):
                                                if fn.lower().endswith(".exe"):
                                                    fn_low = fn.lower()
                                                    if not any(u in fn_low for u in ['unins', 'setup', 'update', 'crash', 'reporter', 'identifier', 'dxsetup', 'helper', 'crashpad']):
                                                        exe_path = os.path.normpath(os.path.join(root_dir, fn))
                                                        break
                                            if exe_path:
                                                break
                                norm_key = re.sub(r'[^a-zA-Z0-9]+', '', name.lower())
                                if norm_key:
                                    if norm_key in results:
                                        if exe_path and (not results[norm_key]["path"] or not os.path.exists(results[norm_key]["path"])):
                                            results[norm_key]["path"] = exe_path
                                        if appid:
                                            results[norm_key]["steam_appid"] = appid
                                    else:
                                        results[norm_key] = {
                                            "name": name,
                                            "path": exe_path,
                                            "publisher": "Steam",
                                            "source": "Steam",
                                            "steam_appid": appid
                                        }
                        except Exception:
                            pass
        except Exception as e:
            logger.debug(f"[PROGRAM_MGR] Steam manifest scan error: {e}")

        # 4. Epic Games Discovery
        epic_manifests = os.path.expandvars(r"%ProgramData%\Epic\EpicGamesLauncher\Data\Manifests")
        if os.path.exists(epic_manifests):
            try:
                for f in os.listdir(epic_manifests):
                    if f.endswith(".item"):
                        try:
                            with open(os.path.join(epic_manifests, f), "r", encoding="utf-8", errors="ignore") as ef:
                                item_data = json.load(ef)
                                disp_name = item_data.get("DisplayName", "").strip()
                                inst_loc = item_data.get("InstallLocation", "").strip()
                                launch_exe = item_data.get("LaunchExecutable", "").strip()
                                if disp_name and inst_loc and launch_exe:
                                    full_exe = os.path.normpath(os.path.join(inst_loc, launch_exe))
                                    if os.path.isfile(full_exe):
                                        norm_key = re.sub(r'[^a-zA-Z0-9]+', '', disp_name.lower())
                                        if norm_key and norm_key not in results:
                                            results[norm_key] = {
                                                "name": disp_name,
                                                "path": full_exe,
                                                "publisher": "Epic Games",
                                                "source": "Epic Games"
                                            }
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"[PROGRAM_MGR] Epic Games scan error: {e}")

        # 5. GOG Galaxy Games Discovery
        if sys.platform == "win32":
            try:
                import winreg
                for gog_root in [winreg.HKEY_LOCAL_MACHINE]:
                    for gog_sub in [r"SOFTWARE\GOG.com\Games", r"SOFTWARE\WOW6432Node\GOG.com\Games"]:
                        try:
                            with winreg.OpenKey(gog_root, gog_sub) as k:
                                for i in range(winreg.QueryInfoKey(k)[0]):
                                    try:
                                        game_key = winreg.EnumKey(k, i)
                                        with winreg.OpenKey(k, game_key) as gk:
                                            def gq(vn):
                                                try:
                                                    return winreg.QueryValueEx(gk, vn)[0]
                                                except Exception:
                                                    return None
                                            gname = gq("gameName")
                                            gpath = gq("path")
                                            gexe = gq("exe")
                                            if gname and gpath and gexe:
                                                full_exe = os.path.normpath(os.path.join(str(gpath), str(gexe)))
                                                if os.path.isfile(full_exe):
                                                    norm_key = re.sub(r'[^a-zA-Z0-9]+', '', str(gname).lower())
                                                    if norm_key and norm_key not in results:
                                                        results[norm_key] = {
                                                            "name": str(gname),
                                                            "path": full_exe,
                                                            "publisher": "GOG",
                                                            "source": "GOG"
                                                        }
                                    except Exception:
                                        pass
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"[PROGRAM_MGR] GOG scan error: {e}")

        # Check whitelist status and auto-resolve missing paths
        installed_list = []
        for item in results.values():
            name = item["name"]
            path = item["path"]

            # If no path found yet, try deep path resolver
            if not path:
                resolved = self.resolve_program_path(name)
                if resolved:
                    item["path"] = resolved
                    path = resolved

            # Check if whitelisted
            is_wl = self.is_whitelisted(name)
            if not is_wl and path:
                is_wl = self.is_whitelisted(os.path.basename(path))

            item["is_whitelisted"] = bool(is_wl)
            installed_list.append(item)

        installed_list.sort(key=lambda x: x["name"].lower())
        return installed_list

    # -------------------------------------------------------------------------
    # Deep Executable Path Resolution
    # -------------------------------------------------------------------------
    @classmethod
    def find_steam_libraries(cls) -> List[str]:
        """Discovers all Steam library paths across all drives."""
        paths = []
        if sys.platform != "win32":
            return paths

        try:
            import winreg
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(root, r"SOFTWARE\Valve\Steam") as k:
                        val, _ = winreg.QueryValueEx(k, "SteamPath")
                        if val and os.path.exists(val):
                            norm = os.path.normpath(val)
                            if norm not in paths:
                                paths.append(norm)
                            vdf = os.path.join(norm, "steamapps", "libraryfolders.vdf")
                            if os.path.exists(vdf):
                                with open(vdf, "r", encoding="utf-8", errors="ignore") as f:
                                    for line in f:
                                        m = re.search(r'"path"\s+"([^"]+)"', line)
                                        if m:
                                            p = os.path.normpath(m.group(1).replace("\\\\", "\\"))
                                            if os.path.exists(p) and p not in paths:
                                                paths.append(p)
                except OSError:
                    pass
        except Exception:
            pass

        # Standard fallbacks
        for fallback in [r"C:\Program Files (x86)\Steam", r"C:\Steam", r"D:\SteamLibrary", r"E:\SteamLibrary"]:
            if os.path.exists(fallback) and fallback not in paths:
                paths.append(fallback)

        return paths

    @classmethod
    def resolve_program_path(cls, path_or_name: str) -> Optional[str]:
        """
        Deep executable resolver:
        1. Direct file existence check
        2. Running processes (psutil.Process.exe())
        3. Steam libraries (steamapps/common)
        4. Windows Start Menu shortcuts (.lnk parsing)
        5. Windows Registry App Paths & Uninstall keys
        6. AppData Local Programs & WindowsApps
        7. PATH / shutil.which
        """
        if not path_or_name:
            return None

        clean = path_or_name.strip()
        # 1. Absolute path check
        if os.path.isabs(clean) and os.path.exists(clean):
            return os.path.normpath(clean)

        target_base = os.path.basename(clean).lower()
        target_stem = target_base[:-4] if target_base.endswith(".exe") else target_base
        target_norm = re.sub(r'[^a-zA-Z0-9]+', '', target_stem)

        # 2. Search running processes
        try:
            import psutil
            for proc in psutil.process_iter(['name', 'exe']):
                try:
                    pname = (proc.info.get('name') or '').lower()
                    pstem = pname[:-4] if pname.endswith('.exe') else pname
                    pexe = proc.info.get('exe')
                    if pexe and os.path.exists(pexe):
                        if pname == target_base or pstem == target_stem or (target_norm and target_norm == re.sub(r'[^a-zA-Z0-9]+', '', pstem)):
                            return os.path.normpath(pexe)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass

        # 3. Search Steam libraries
        steam_libs = cls.find_steam_libraries()
        for lib in steam_libs:
            common = os.path.join(lib, "steamapps", "common")
            if not os.path.exists(common):
                continue
            try:
                for game_folder in os.listdir(common):
                    gf_path = os.path.join(common, game_folder)
                    if not os.path.isdir(gf_path):
                        continue
                    gf_norm = re.sub(r'[^a-zA-Z0-9]+', '', game_folder.lower())
                    if target_norm in gf_norm or gf_norm in target_norm:
                        for root, _, files in os.walk(gf_path):
                            for f in sorted(files):
                                f_lower = f.lower()
                                if f_lower.endswith(".exe"):
                                    f_stem = f_lower[:-4]
                                    f_norm = re.sub(r'[^a-zA-Z0-9]+', '', f_stem)
                                    if (
                                        f_lower == target_base
                                        or f_stem == target_stem
                                        or f_norm == target_norm
                                        or f_norm == gf_norm
                                        or f_norm in target_norm
                                        or target_norm in f_norm
                                    ):
                                        if not any(bad in f_lower for bad in ['unins', 'setup', 'update', 'crash', 'reporter', 'identifier', 'dxsetup', 'helper', 'crashpad']):
                                            return os.path.normpath(os.path.join(root, f))
                            if root.count(os.sep) - gf_path.count(os.sep) > 3:
                                break
            except Exception:
                pass

        # 4. Search Windows Start Menu shortcuts
        if sys.platform == "win32":
            try:
                import win32com.client
                wscript = win32com.client.Dispatch("WScript.Shell")
                start_dirs = [
                    os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
                    os.path.join(os.environ.get("PROGRAMDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
                ]
                for sdir in start_dirs:
                    if not os.path.exists(sdir):
                        continue
                    for root, _, files in os.walk(sdir):
                        for f in files:
                            if f.lower().endswith(".lnk"):
                                f_stem = f[:-4].lower()
                                f_norm = re.sub(r'[^a-zA-Z0-9]+', '', f_stem)
                                if target_norm == f_norm or target_stem == f_stem or target_norm in f_norm:
                                    try:
                                        sc = wscript.CreateShortcut(os.path.join(root, f))
                                        t_path = sc.TargetPath
                                        if t_path and os.path.exists(t_path) and t_path.lower().endswith(".exe"):
                                            return os.path.normpath(t_path)
                                    except Exception:
                                        pass
            except Exception:
                pass

        # 5. Search Windows Registry App Paths
        if sys.platform == "win32":
            try:
                import winreg
                target_exe = target_base if target_base.endswith(".exe") else f"{target_base}.exe"
                for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                    for sub in (
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
                        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"
                    ):
                        try:
                            with winreg.OpenKey(root, f"{sub}\\{target_exe}") as k:
                                val, _ = winreg.QueryValueEx(k, "")
                                if val:
                                    clean_v = val.strip('"')
                                    if os.path.exists(clean_v):
                                        return os.path.normpath(clean_v)
                        except OSError:
                            pass
            except Exception:
                pass

        # 6. Check AppData Local Programs and WindowsApps
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            candidates = [
                os.path.join(local_appdata, "Programs", target_stem, f"{target_stem}.exe"),
                os.path.join(local_appdata, "Microsoft", "WindowsApps", f"{target_stem}.exe"),
            ]
            for c in candidates:
                if os.path.exists(c):
                    return os.path.normpath(c)

        # 7. Search PATH
        which_path = shutil.which(target_base if target_base.endswith(".exe") else f"{target_base}.exe")
        if which_path and os.path.exists(which_path):
            return os.path.normpath(which_path)

        return None

    # -------------------------------------------------------------------------
    # Launch & Process Control
    # -------------------------------------------------------------------------
    def launch_program(
        self,
        prog_id_or_name: str,
        target: Optional[str] = None,
        profile: Optional[str] = None,
        extra_args: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Validates whitelist, resolves executable path, binds arguments & extensions,
        and launches the application detached or elevated via Windows UAC.
        """
        target_str = str(target or "").lower()
        prog_clean = str(prog_id_or_name or "").lower().strip()
        if (
            prog_clean in ("mmsys.cpl", "msdt.exe", "devmgmt.msc", "mmsys_cpl", "control_exe")
            or (prog_clean == "control.exe" and any(k in target_str for k in ("sound", "mmsys", "troubleshoot", "audio")))
            or ("audio" in target_str and "diagnostic" in target_str)
        ):
            return {
                "status": "blocked",
                "app_name": prog_id_or_name,
                "error": (
                    "Security Blocked: Direct execution of Windows Audio Troubleshooter or Sound Control Panel is prohibited. "
                    "Modifying Windows audio endpoints or running audio troubleshooters disrupts active WASAPI hardware output streams."
                )
            }

        prog = self.get_program(prog_id_or_name)
        if not prog and not self.is_whitelisted(prog_id_or_name):
            return {
                "status": "blocked",
                "app_name": prog_id_or_name,
                "error": (
                    f"Security Blocked: '{prog_id_or_name}' is not in your allowed application whitelist. "
                    f"DO NOT attempt workaround hacks, hotkeys, or scripts to circumvent this block. "
                    f"Instead, STOP immediately and verbally inform the user: '{prog_id_or_name} is not currently on your allowed whitelist. Would you like me to add it so I can open it?' "
                    f"If the user grants permission (e.g. 'yes', 'add it', 'sure'), invoke add_to_whitelist('{prog_id_or_name}') and then launch it."
                )
            }

        display_name = prog.get("name", prog_id_or_name) if prog else prog_id_or_name
        raw_path = prog.get("path", "") if prog else prog_id_or_name
        config_args = prog.get("arguments", "") if prog else ""
        should_elevate = bool(prog.get("elevate", False)) if prog else False
        working_dir = (prog.get("working_dir") or "").strip() if prog else ""

        # Check for explicit --elevate / --admin in arguments
        all_args_check = f"{config_args} {extra_args or ''}".lower()
        if "--elevate" in all_args_check or "--admin" in all_args_check:
            should_elevate = True

        exe_path = self.resolve_program_path(raw_path) or raw_path

        # If not resolved to an absolute path, try resolving by display name
        if not os.path.isabs(exe_path) or not os.path.exists(exe_path):
            alt = self.resolve_program_path(display_name)
            if alt and os.path.exists(alt):
                exe_path = alt
                if prog and not prog.get("path"):
                    prog["path"] = alt
                    self._save_programs(self.data)

        # Build command-line arguments
        arg_parts = []
        if config_args:
            # Strip --elevate from args if elevated via runas
            clean_cargs = [a for a in shlex.split(config_args) if a not in ("--elevate", "--admin")] if should_elevate else shlex.split(config_args)
            arg_parts.extend(clean_cargs)

        if extra_args:
            clean_eargs = [a for a in shlex.split(extra_args) if a not in ("--elevate", "--admin")] if should_elevate else shlex.split(extra_args)
            arg_parts.extend(clean_eargs)

        # Handle browser URLs / search queries
        exe_lower = os.path.basename(exe_path).lower()
        if exe_lower in ("chrome.exe", "msedge.exe", "brave.exe", "firefox.exe"):
            arg_parts.append("--disable-session-crashed-bubble")
            if profile:
                arg_parts.append(f"--profile-directory={profile}")
            elif exe_lower in ("chrome.exe", "msedge.exe"):
                arg_parts.append("--profile-directory=Default")

        if target:
            clean_t = str(target).strip()
            if exe_lower in ("chrome.exe", "msedge.exe", "brave.exe", "firefox.exe"):
                if not (clean_t.startswith("http://") or clean_t.startswith("https://") or clean_t.startswith("file://")) and not os.path.exists(clean_t):
                    import urllib.parse
                    clean_t = f"https://www.google.com/search?q={urllib.parse.quote_plus(clean_t)}"
            arg_parts.append(clean_t)

        cwd = working_dir if (working_dir and os.path.exists(working_dir)) else (os.path.dirname(exe_path) if (exe_path and os.path.isabs(exe_path)) else None)

        try:
            # A. Elevated Launch via Windows UAC
            if should_elevate and sys.platform == "win32":
                args_str = " ".join([f'"{a}"' if " " in a else a for a in arg_parts])
                ret = ctypes.windll.shell32.ShellExecuteW(
                    None,
                    "runas",
                    exe_path,
                    args_str if args_str else None,
                    cwd,
                    1  # SW_SHOWNORMAL
                )
                if ret > 32:
                    return {
                        "status": "success",
                        "app_name": display_name,
                        "executable": exe_path,
                        "elevated": True,
                        "message": f"Successfully launched {display_name} with Administrator elevation."
                    }
                else:
                    return {
                        "status": "error",
                        "app_name": display_name,
                        "executable": exe_path,
                        "error": f"UAC elevation cancelled or failed (code: {ret})."
                    }

            # B. Standard Detached Process Launch
            if os.path.exists(exe_path):
                creation_flags = 0
                if sys.platform == "win32":
                    creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

                cmd = [exe_path] + arg_parts
                subprocess.Popen(cmd, cwd=cwd, creationflags=creation_flags, close_fds=True)
                return {
                    "status": "success",
                    "app_name": display_name,
                    "executable": exe_path,
                    "elevated": False,
                    "message": f"Successfully launched {display_name}."
                }
            else:
                # Check for Steam appid fallback before trying generic startfile
                steam_appid = (prog.get("steam_appid") if prog else None) or ""
                if not steam_appid and sys.platform == "win32":
                    try:
                        if os.path.exists(GAMES_PROFILES_PATH):
                            with open(GAMES_PROFILES_PATH, "r", encoding="utf-8") as f:
                                gp_data = json.load(f)
                            for gp in gp_data.get("profiles", {}).values():
                                if (gp.get("display_name", "").lower() == display_name.lower()) and gp.get("steam_appid"):
                                    steam_appid = str(gp["steam_appid"])
                                    break
                    except Exception:
                        pass

                if steam_appid and sys.platform == "win32":
                    try:
                        os.startfile(f"steam://rungameid/{steam_appid}")
                        return {
                            "status": "success",
                            "app_name": display_name,
                            "executable": f"steam://rungameid/{steam_appid}",
                            "elevated": False,
                            "message": f"Successfully launched {display_name} via Steam (AppID: {steam_appid})."
                        }
                    except Exception as se:
                        logger.debug(f"[PROGRAM_MGR] Steam URL launch error: {se}")

                # Windows Shell Fallback
                if sys.platform == "win32":
                    if target:
                        os.startfile(target)
                    elif exe_path and os.path.exists(exe_path):
                        os.startfile(exe_path)
                    else:
                        return {
                            "status": "error",
                            "app_name": display_name,
                            "executable": exe_path,
                            "error": f"Executable not found on disk: {exe_path or '(empty path)'}"
                        }
                    return {
                        "status": "success",
                        "app_name": display_name,
                        "executable": exe_path,
                        "elevated": False,
                        "message": f"Successfully launched {display_name} via Windows Shell."
                    }
                else:
                    return {
                        "status": "error",
                        "app_name": display_name,
                        "executable": exe_path,
                        "error": f"Executable not found: {exe_path}"
                    }
        except Exception as e:
            return {
                "status": "error",
                "app_name": display_name,
                "executable": exe_path,
                "error": f"Failed to launch {display_name}: {e}"
            }

    def close_program(self, prog_id_or_name: str) -> Dict[str, Any]:
        """Terminates running instances of the specified application."""
        prog = self.get_program(prog_id_or_name)
        display_name = prog.get("name", prog_id_or_name) if prog else prog_id_or_name
        path = prog.get("path", "") if prog else prog_id_or_name

        target_base = os.path.basename(path).lower() if path else prog_id_or_name.lower().strip()
        if not target_base.endswith(".exe"):
            target_base += ".exe"

        # Special case for File Explorer: close open folder windows without terminating desktop shell
        if target_base == "explorer.exe" or (prog and prog.get("id") == "file_explorer") or prog_id_or_name.lower() in ("file explorer", "file_explorer", "explorer"):
            if sys.platform == "win32":
                try:
                    from core.screen_stream import ensure_thread_desktop
                    ensure_thread_desktop()
                    import win32gui, win32con
                    closed_count = 0
                    def close_cb(hwnd, _):
                        nonlocal closed_count
                        if win32gui.IsWindowVisible(hwnd):
                            cls_name = win32gui.GetClassName(hwnd)
                            if cls_name in ("CabinetWClass", "ExploreWClass"):
                                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                                closed_count += 1
                        return True
                    try:
                        win32gui.EnumWindows(close_cb, None)
                    except Exception:
                        pass
                    if closed_count > 0:
                        return {
                            "status": "success",
                            "app_name": display_name,
                            "message": f"Successfully closed {closed_count} File Explorer window(s)."
                        }
                    else:
                        return {
                            "status": "warning",
                            "app_name": display_name,
                            "message": "No open File Explorer windows found."
                        }
                except Exception as e:
                    return {
                        "status": "error",
                        "app_name": display_name,
                        "error": f"Failed closing File Explorer: {e}"
                    }

        if sys.platform != "win32":
            return {"status": "error", "error": "Process termination only supported on Windows."}

        try:
            res = subprocess.run(
                ["taskkill", "/IM", target_base, "/F"],
                capture_output=True,
                text=True,
                check=False
            )
            if res.returncode == 0:
                return {
                    "status": "success",
                    "app_name": display_name,
                    "message": f"Successfully closed {display_name}."
                }
            else:
                return {
                    "status": "warning",
                    "app_name": display_name,
                    "message": f"{display_name} was not running or could not be terminated."
                }
        except Exception as e:
            return {
                "status": "error",
                "app_name": display_name,
                "error": f"Failed to close {display_name}: {e}"
            }

    @classmethod
    def is_file_explorer_open(cls) -> bool:
        """Checks if any actual File Explorer folder window is currently open (CabinetWClass/ExploreWClass)."""
        if sys.platform != "win32":
            return False
        try:
            from core.screen_stream import ensure_thread_desktop
            ensure_thread_desktop()
            import win32gui
            found = False
            def enum_cb(hwnd, _):
                nonlocal found
                if not found and win32gui.IsWindowVisible(hwnd):
                    try:
                        cls_name = win32gui.GetClassName(hwnd)
                        if cls_name in ("CabinetWClass", "ExploreWClass"):
                            found = True
                    except Exception:
                        pass
                return True

            try:
                win32gui.EnumWindows(enum_cb, None)
            except Exception:
                pass
            return found
        except Exception as e:
            logger.debug(f"[PROGRAM_MGR] is_file_explorer_open error: {e}")
            return False

    def is_program_running(self, prog_id_or_name: str) -> bool:
        """Checks if process for program is currently active."""
        prog = self.get_program(prog_id_or_name)
        path = prog.get("path", "") if prog else prog_id_or_name
        target = os.path.basename(path).lower() if path else prog_id_or_name.lower().strip()
        stem = target[:-4] if target.endswith(".exe") else target

        # Special case: Windows File Explorer (explorer.exe is the persistent shell process;
        # only report running when an actual folder window is open)
        if target == "explorer.exe" or (prog and prog.get("id") == "file_explorer") or prog_id_or_name.lower() in ("file explorer", "file_explorer", "explorer"):
            return self.is_file_explorer_open()

        try:
            import psutil
            for proc in psutil.process_iter(['name']):
                try:
                    pname = (proc.info.get('name') or '').lower()
                    if pname == target or pname == f"{stem}.exe" or stem in pname:
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass
        return False

    def get_running_status_map(self) -> Dict[str, bool]:
        """Returns map of prog_id -> is_running."""
        status = {}
        running_names = set()
        try:
            import psutil
            for proc in psutil.process_iter(['name']):
                try:
                    n = proc.info.get('name')
                    if n:
                        running_names.add(n.lower())
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass

        # Check File Explorer window state once
        explorer_open = self.is_file_explorer_open()

        with self._lock:
            for pid, p in self.data.get("programs", {}).items():
                path = p.get("path", "")
                p_exe = os.path.basename(path).lower() if path else f"{pid}.exe"
                p_stem = p_exe[:-4] if p_exe.endswith(".exe") else p_exe

                if p_exe == "explorer.exe" or pid == "file_explorer":
                    status[pid] = explorer_open
                else:
                    is_active = (p_exe in running_names or f"{p_stem}.exe" in running_names)
                    status[pid] = is_active

        return status
