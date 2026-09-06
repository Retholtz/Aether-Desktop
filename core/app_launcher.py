"""
Aether Desktop - Desktop Application Launcher & Security Whitelist Hook
Handles resolving, verifying against the whitelist, and launching Windows applications.
"""

import os
import shutil
import subprocess
import sys
from typing import List, Optional

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



class DesktopAppLauncher:
    """Manages secure application execution on Windows."""

    @staticmethod
    def normalize_app_name(raw_name: str) -> str:
        """Normalizes user speech input to a canonical .exe name."""
        cleaned = raw_name.strip().lower()
        if cleaned in COMMON_ALIASES:
            return COMMON_ALIASES[cleaned]
        if not cleaned.endswith(".exe") and "." not in cleaned:
            return cleaned + ".exe"
        return cleaned

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
    def launch_app(cls, app_name: str, whitelist: List[str]) -> dict:
        """
        Validates whitelist and launches the requested application.
        Returns a structured status dictionary.
        """
        target_exe = cls.normalize_app_name(app_name)

        # 1. Validate against security whitelist
        if not cls.is_whitelisted(target_exe, whitelist):
            return {
                "status": "blocked",
                "app_name": app_name,
                "executable": target_exe,
                "error": f"Security Blocked: '{target_exe}' is not in your allowed application whitelist. You can add it in Settings > Desktop Vision & Automation Hook."
            }

        # 2. Resolve executable path
        exe_path = cls.resolve_app_path(target_exe)

        # 3. Launch detached process
        try:
            if exe_path and os.path.exists(exe_path):
                creation_flags = 0
                if sys.platform == "win32":
                    creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                
                subprocess.Popen([exe_path], creationflags=creation_flags, close_fds=True)
                return {
                    "status": "success",
                    "app_name": app_name,
                    "executable": target_exe,
                    "path": exe_path,
                    "message": f"Successfully launched {app_name}."
                }
            else:
                if sys.platform == "win32":
                    os.startfile(target_exe)
                    return {
                        "status": "success",
                        "app_name": app_name,
                        "executable": target_exe,
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
    def close_app(cls, app_name: str, whitelist: List[str]) -> dict:
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

            # 1. Check known process mappings (e.g. calc.exe -> CalculatorApp.exe)
            if target_lower in PROCESS_KILL_TARGETS:
                candidates.extend(PROCESS_KILL_TARGETS[target_lower])

            # 2. Add raw app_name variants if applicable
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

            # 3. Fallback to image name wildcard filter if not yet terminated
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
        """
        Normalizes app_name to canonical executable and adds it to the whitelist.
        Returns result dict with updated whitelist.
        """
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
        """
        Normalizes app_name and removes matching executable from the whitelist.
        """
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
