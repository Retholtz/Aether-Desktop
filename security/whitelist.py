"""
Aether Desktop - Security Whitelist & Execution Validator
Validates application launch and termination requests against the user's security whitelist,
resolves executable paths across Windows Registry and system folders, and manages process aliases and browser profiles.
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.parse
from typing import Dict, List, Optional

if sys.platform == "win32":
    import winreg
else:
    winreg = None

COMMON_ALIASES = {
    "google chrome": "chrome.exe",
    "chrome": "chrome.exe",
    "chrome browser": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "notepad": "notepad.exe",
    "text editor": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "word": "winword.exe",
    "microsoft word": "winword.exe",
    "winword": "winword.exe",
    "excel": "excel.exe",
    "microsoft excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "ppt": "powerpnt.exe",
    "powerpnt": "powerpnt.exe",
    "spotify": "spotify.exe",
    "blender": "blender.exe",
    "file explorer": "explorer.exe",
    "explorer": "explorer.exe",
    "files": "explorer.exe",
    "terminal": "wt.exe",
    "windows terminal": "wt.exe",
    "powershell": "powershell.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "paint": "mspaint.exe",
    "steam": "steam.exe",
    "discord": "discord.exe",
    "vlc": "vlc.exe",
    "vs code": "code.exe",
    "vscode": "code.exe",
    "visual studio code": "code.exe"
}

# Windows App package mappings where the running process name differs from the launcher stub
PROCESS_KILL_TARGETS = {
    "calc.exe": ["CalculatorApp.exe", "Calculator.exe", "calc.exe"],
    "calculator.exe": ["CalculatorApp.exe", "Calculator.exe", "calc.exe"],
    "wt.exe": ["WindowsTerminal.exe", "wt.exe"],
    "windowsterminal.exe": ["WindowsTerminal.exe", "wt.exe"],
    "notepad.exe": ["notepad.exe", "Notepad.exe"],
    "mspaint.exe": ["mspaint.exe", "Paint.exe"],
}


class WhitelistValidator:
    """Manages application authorization and execution validation."""

    @staticmethod
    def normalize_app_name(raw_name: str) -> str:
        """Normalizes user speech/text input to a canonical .exe name."""
        cleaned = raw_name.strip().lower()
        if cleaned in COMMON_ALIASES:
            return COMMON_ALIASES[cleaned]
        if not cleaned.endswith(".exe") and "." not in cleaned:
            return cleaned + ".exe"
        return cleaned

    @classmethod
    def get_chrome_profiles(cls) -> Dict[str, str]:
        """
        Reads Chrome's Local State file to discover installed user profiles.
        Returns a dictionary mapping lowercase names, user emails, and directory names
        to the exact profile directory string (e.g. 'Default', 'Profile 1').
        """
        mapping = {}
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if not local_appdata:
            return mapping

        local_state_file = os.path.join(local_appdata, "Google", "Chrome", "User Data", "Local State")
        if not os.path.exists(local_state_file):
            return mapping

        try:
            with open(local_state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
            info_cache = state.get("profile", {}).get("info_cache", {})
            for prof_dir, pinfo in info_cache.items():
                mapping[prof_dir.lower()] = prof_dir
                name = pinfo.get("name", "")
                if name:
                    mapping[name.lower()] = prof_dir
                uname = pinfo.get("user_name", "")
                if uname:
                    mapping[uname.lower()] = prof_dir
                gaia = pinfo.get("gaia_name", "")
                if gaia:
                    mapping[gaia.lower()] = prof_dir
        except Exception as e:
            print(f"[SECURITY] Failed to read Chrome profiles: {e}")

        return mapping

    @classmethod
    def is_whitelisted(cls, target_exe: str, whitelist: List[str]) -> bool:
        """Checks whether the application is permitted by the security whitelist."""
        if not whitelist:
            return False

        target_clean = target_exe.lower().strip()
        target_base = target_clean[:-4] if target_clean.endswith(".exe") else target_clean

        for item in whitelist:
            item_clean = item.lower().strip()
            item_base = item_clean[:-4] if item_clean.endswith(".exe") else item_clean

            if item_clean == "*" or item_clean == target_clean or item_base == target_base:
                return True
        return False

    @classmethod
    def resolve_app_path(cls, target_exe: str) -> Optional[str]:
        """Resolves the full executable file path on Windows."""
        # 1. Search PATH via shutil.which
        which_path = shutil.which(target_exe)
        if which_path and os.path.exists(which_path):
            return which_path

        if not winreg:
            return None

        # 2. Check Windows Registry App Paths
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for sub in (
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"
            ):
                key_path = f"{sub}\\{target_exe}"
                try:
                    with winreg.OpenKey(root, key_path) as k:
                        val, _ = winreg.QueryValueEx(k, "")
                        if val:
                            clean_val = val.strip('"')
                            if os.path.exists(clean_val):
                                return clean_val
                except OSError:
                    pass

        # 3. Check AppData Local Programs and WindowsApps
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            base_no_ext = target_exe[:-4] if target_exe.endswith(".exe") else target_exe
            candidates = [
                os.path.join(local_appdata, "Programs", base_no_ext, target_exe),
                os.path.join(local_appdata, "Microsoft", "WindowsApps", target_exe),
            ]
            for candidate in candidates:
                if os.path.exists(candidate):
                    return candidate

        return None

    @classmethod
    def validate_and_launch(
        cls,
        app_name: str,
        whitelist: List[str],
        target: Optional[str] = None,
        profile: Optional[str] = None
    ) -> dict:
        """
        Validates the requested application against the whitelist and launches it detached.
        Supports native browser profile selection (e.g. profile='Michael' -> --profile-directory="Default").
        Returns a structured JSON status dictionary.
        """
        target_exe = cls.normalize_app_name(app_name)

        # 1. Security whitelist validation
        if not cls.is_whitelisted(target_exe, whitelist):
            return {
                "status": "blocked",
                "app_name": app_name,
                "executable": target_exe,
                "error": f"Security Blocked: '{target_exe}' is not in your allowed application whitelist. You can add it in Settings > Desktop Vision & Automation Hook."
            }

        # 2. Resolve executable path
        exe_path = cls.resolve_app_path(target_exe)

        # 3. Format target for browsers if it is a search query
        final_target = target
        if final_target and target_exe.lower() in ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"):
            clean_t = str(final_target).strip()
            if clean_t and not (clean_t.startswith("http://") or clean_t.startswith("https://") or clean_t.startswith("file://")) and not os.path.exists(clean_t):
                final_target = f"https://www.google.com/search?q={urllib.parse.quote_plus(clean_t)}"

        # 4. Handle browser profile flags & clean session flags
        extra_args = []
        if target_exe.lower() in ("chrome.exe", "msedge.exe", "brave.exe"):
            extra_args.append("--disable-session-crashed-bubble")

        resolved_profile_name = None
        if target_exe.lower() in ("chrome.exe", "msedge.exe"):
            if profile:
                prof_clean = str(profile).strip().lower()
                profiles_map = cls.get_chrome_profiles()
                if prof_clean in profiles_map:
                    prof_dir = profiles_map[prof_clean]
                    extra_args.append(f"--profile-directory={prof_dir}")
                    resolved_profile_name = f"{profile} ({prof_dir})"
                else:
                    extra_args.append(f"--profile-directory={profile}")
                    resolved_profile_name = profile
            else:
                # Default to primary user profile to bypass the "Who's using Chrome?" profile picker dialog
                extra_args.append("--profile-directory=Default")
                resolved_profile_name = "Default"

        # 5. Launch process
        try:
            if exe_path and os.path.exists(exe_path):
                creation_flags = 0
                if sys.platform == "win32":
                    creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

                cmd = [exe_path]
                if extra_args:
                    cmd.extend(extra_args)
                if final_target:
                    cmd.append(str(final_target))

                subprocess.Popen(cmd, creationflags=creation_flags, close_fds=True)
                msg = f"Successfully launched {app_name}"
                if resolved_profile_name:
                    msg += f" with profile '{resolved_profile_name}'"
                if final_target:
                    msg += f" -> '{final_target}'"
                msg += "."

                return {
                    "status": "success",
                    "app_name": app_name,
                    "executable": target_exe,
                    "path": exe_path,
                    "profile": resolved_profile_name,
                    "target": final_target,
                    "message": msg
                }
            else:
                if sys.platform == "win32":
                    if final_target:
                        os.startfile(final_target)
                    else:
                        os.startfile(target_exe)
                    return {
                        "status": "success",
                        "app_name": app_name,
                        "executable": target_exe,
                        "target": final_target,
                        "message": f"Successfully launched {app_name} via Windows Shell."
                    }
                else:
                    return {
                        "status": "error",
                        "app_name": app_name,
                        "executable": target_exe,
                        "error": f"Could not find executable path for {target_exe}."
                    }
        except Exception as e:
            return {
                "status": "error",
                "app_name": app_name,
                "executable": target_exe,
                "error": f"Failed to launch {app_name}: {e}"
            }

    @classmethod
    def validate_and_close(cls, app_name: str, whitelist: List[str]) -> dict:
        """Validates whitelist and terminates the requested application."""
        target_exe = cls.normalize_app_name(app_name)

        if not cls.is_whitelisted(target_exe, whitelist):
            return {
                "status": "blocked",
                "app_name": app_name,
                "executable": target_exe,
                "error": f"Security Blocked: Cannot close '{target_exe}' because it is not in your allowed application whitelist."
            }

        if sys.platform != "win32":
            return {"status": "error", "error": "Application closing is only supported on Windows."}

        try:
            target_lower = target_exe.lower()
            candidates = []

            # 1. Known process mappings
            if target_lower in PROCESS_KILL_TARGETS:
                candidates.extend(PROCESS_KILL_TARGETS[target_lower])

            # 2. Add raw app_name variants
            raw_clean = app_name.strip().lower()
            if raw_clean:
                raw_base = raw_clean[:-4] if raw_clean.endswith(".exe") else raw_clean
                candidates.extend([
                    f"{raw_base}.exe",
                    f"{raw_base}App.exe",
                    f"{raw_base.capitalize()}App.exe",
                    f"{raw_base.capitalize()}.exe"
                ])

            if target_exe not in candidates:
                candidates.append(target_exe)

            # Deduplicate while preserving order
            seen = set()
            unique_candidates = []
            for c in candidates:
                c_low = c.lower()
                if c_low not in seen:
                    seen.add(c_low)
                    unique_candidates.append(c)

            terminated = False
            for cand in unique_candidates:
                res = subprocess.run(
                    ["taskkill", "/IM", cand, "/F"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if res.returncode == 0:
                    terminated = True

            # 3. Fallback to image name wildcard
            if not terminated:
                for base in [target_exe[:-4] if target_exe.endswith(".exe") else target_exe, raw_clean]:
                    if len(base) >= 3:
                        res = subprocess.run(
                            ["taskkill", "/FI", f"IMAGENAME eq {base}*", "/F"],
                            capture_output=True,
                            text=True,
                            check=False
                        )
                        if res.returncode == 0 and "SUCCESS" in res.stdout:
                            terminated = True
                            break

            if terminated:
                return {
                    "status": "success",
                    "app_name": app_name,
                    "executable": target_exe,
                    "message": f"Successfully closed {app_name}."
                }
            else:
                return {
                    "status": "warning",
                    "app_name": app_name,
                    "executable": target_exe,
                    "message": f"{app_name} was not running or could not be terminated."
                }
        except Exception as e:
            return {
                "status": "error",
                "app_name": app_name,
                "executable": target_exe,
                "error": f"Failed to close {app_name}: {e}"
            }

    @classmethod
    def add_to_whitelist(cls, app_name: str, whitelist: List[str]) -> dict:
        """Adds normalized executable to the whitelist."""
        target_exe = cls.normalize_app_name(app_name)
        current_list = list(whitelist) if whitelist else []

        if cls.is_whitelisted(target_exe, current_list):
            return {
                "status": "already_allowed",
                "app_name": app_name,
                "executable": target_exe,
                "whitelist": current_list,
                "message": f"'{target_exe}' is already on the application whitelist."
            }

        current_list.append(target_exe)
        return {
            "status": "success",
            "app_name": app_name,
            "executable": target_exe,
            "whitelist": current_list,
            "message": f"Successfully added '{target_exe}' to the application whitelist."
        }

    @classmethod
    def remove_from_whitelist(cls, app_name: str, whitelist: List[str]) -> dict:
        """Removes normalized executable from the whitelist."""
        target_exe = cls.normalize_app_name(app_name)
        target_clean = target_exe.lower().strip()
        target_base = target_clean[:-4] if target_clean.endswith(".exe") else target_clean

        current_list = list(whitelist) if whitelist else []
        new_list = []
        found = False

        for item in current_list:
            item_clean = item.lower().strip()
            item_base = item_clean[:-4] if item_clean.endswith(".exe") else item_clean
            if item_clean == target_clean or item_base == target_base:
                found = True
            else:
                new_list.append(item)

        if not found:
            return {
                "status": "not_found",
                "app_name": app_name,
                "executable": target_exe,
                "whitelist": current_list,
                "message": f"'{target_exe}' was not found on the application whitelist."
            }

        return {
            "status": "success",
            "app_name": app_name,
            "executable": target_exe,
            "whitelist": new_list,
            "message": f"Successfully removed '{target_exe}' from the application whitelist."
        }
