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
                        deduped = self._deduplicate_programs(data)
                        if deduped:
                            self._save_programs(data)
                        return data
                except Exception as e:
                    logger.warning(f"[PROGRAM_MGR] Failed reading {self.programs_path}: {e}")

            # Initialize defaults and migrate existing whitelist and games
            initial = copy.deepcopy(DEFAULT_PROGRAMS)
            self._import_from_config(initial)
            self._import_from_games(initial)
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
                already_exists = False
                for p in payload["programs"].values():
                    p_path = os.path.basename(p.get("path", "")).lower()
                    p_name = p.get("name", "").lower()
                    if clean.lower() in (p_path, p_name) or clean_no_ext.lower() in (p_path.replace(".exe", ""), p_name):
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

    def _import_from_games(self, payload: Dict[str, Any]):
        """Imports game titles (e.g. Crimson Desert) from games_profiles.json into programs."""
        if os.path.exists(GAMES_PROFILES_PATH):
            try:
                with open(GAMES_PROFILES_PATH, "r", encoding="utf-8") as f:
                    gdata = json.load(f)
                profiles = gdata.get("profiles", {})
                for gid, p in profiles.items():
                    pname = p.get("process_name") or ""
                    dname = p.get("display_name") or gid
                    pid = re.sub(r'[^a-zA-Z0-9]+', '_', dname.lower()).strip('_') or gid
                    resolved = self.resolve_program_path(pname or dname) or pname
                    cwd = os.path.dirname(resolved) if resolved and os.path.isabs(resolved) else ""

                    # Check if already present under another id (e.g. crimsondesert)
                    matched_id = None
                    for existing_id, ep in list(payload["programs"].items()):
                        e_path = os.path.basename(ep.get("path", "")).lower()
                        e_name = ep.get("name", "").lower()
                        if existing_id == pid or existing_id == gid or (pname and pname.lower() == e_path) or (dname and dname.lower() == e_name) or (re.sub(r'[^a-zA-Z0-9]+', '', dname.lower()) == re.sub(r'[^a-zA-Z0-9]+', '', existing_id)):
                            matched_id = existing_id
                            break

                    target_id = pid
                    if matched_id and matched_id != pid and matched_id in payload["programs"]:
                        del payload["programs"][matched_id]
                    payload["programs"][target_id] = {
                        "id": target_id,
                        "name": dname,
                        "path": resolved or pname,
                        "arguments": payload["programs"].get(target_id, {}).get("arguments", ""),
                        "elevate": payload["programs"].get(target_id, {}).get("elevate", False),
                        "working_dir": cwd or payload["programs"].get(target_id, {}).get("working_dir", "")
                    }
            except Exception as e:
                logger.debug(f"[PROGRAM_MGR] Could not import from games profiles: {e}")

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
    def list_programs(self) -> Dict[str, Any]:
        """Returns all configured programs, active program, and process running status."""
        with self._lock:
            return {
                "active_program": self.data.get("active_program", ""),
                "programs": copy.deepcopy(self.data.get("programs", {})),
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
            if os.path.exists(GAMES_PROFILES_PATH):
                try:
                    with open(GAMES_PROFILES_PATH, "r", encoding="utf-8") as f:
                        gdata = json.load(f)
                    for gid, gp in gdata.get("profiles", {}).items():
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
                except Exception:
                    pass

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
                            for f in files:
                                f_lower = f.lower()
                                if f_lower.endswith(".exe"):
                                    f_stem = f_lower[:-4]
                                    f_norm = re.sub(r'[^a-zA-Z0-9]+', '', f_stem)
                                    if f_lower == target_base or f_stem == target_stem or f_norm == target_norm:
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
                # Windows Shell Fallback
                if sys.platform == "win32":
                    if target:
                        os.startfile(target)
                    else:
                        os.startfile(exe_path)
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
