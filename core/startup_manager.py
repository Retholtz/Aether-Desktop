"""
Aether Desktop - Windows Startup Registry Manager
Manages auto-launch registry keys under HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.
"""

import os
import sys

try:
    import winreg
except ImportError:
    winreg = None

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_REG_NAME = "AetherDesktop"


def set_boot_on_startup(enable: bool) -> bool:
    """
    Adds or removes Aether Desktop from the current user's Windows Run registry key.
    Uses HKCU so no administrative elevation/UAC prompt is required.
    """
    if sys.platform != "win32" or winreg is None:
        return False

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            winreg.KEY_SET_VALUE
        )
    except OSError as err:
        print(f"[ERROR] [STARTUP] Failed to open Run registry key: {err}")
        return False

    with key:
        if enable:
            # Build target invocation pointing to current executable or script runner
            if getattr(sys, "frozen", False):
                # Packaged executable
                exe_path = f'"{sys.executable}"'
            else:
                # Python script execution
                python_exe = sys.executable
                script_path = os.path.abspath("main.py")
                if not os.path.exists(script_path):
                    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    alt_script = os.path.join(root_dir, "main.py")
                    if os.path.exists(alt_script):
                        script_path = alt_script
                exe_path = f'"{python_exe}" "{script_path}"'

            try:
                winreg.SetValueEx(key, APP_REG_NAME, 0, winreg.REG_SZ, exe_path)
                print(f"[INFO] [STARTUP] Enabled boot on startup: {exe_path}")
                return True
            except OSError as err:
                print(f"[ERROR] [STARTUP] Failed to write Run key: {err}")
                return False
        else:
            try:
                winreg.DeleteValue(key, APP_REG_NAME)
                print("[INFO] [STARTUP] Disabled boot on startup.")
                return True
            except FileNotFoundError:
                # Key already absent
                return True
            except OSError as err:
                print(f"[ERROR] [STARTUP] Failed to remove Run key: {err}")
                return False


def is_boot_on_startup_enabled() -> bool:
    """Checks whether the application registry entry currently exists in HKCU Run."""
    if sys.platform != "win32" or winreg is None:
        return False

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_REG_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False
