"""
Aether Desktop - Windows Runtime Prerequisite Checker
Checks for Microsoft Edge WebView2 Runtime and .NET Runtime before launching
the PyWebView/WinForms GUI so missing dependencies produce a clear prompt
instead of an opaque crash.
"""

from __future__ import annotations

import os
import sys
import webbrowser
from typing import Any, Dict, List

WEBVIEW2_CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_DOWNLOAD_URL = "https://developer.microsoft.com/en-us/microsoft-edge/webview2/#download-section"
DOTNET_DOWNLOAD_URL = "https://dotnet.microsoft.com/en-us/download/dotnet"

# .NET Framework 4.7.2 minimum release key (4.8 = 528040, 4.8.1 = 533320)
NETFX_472_MIN_RELEASE = 461808


def is_webview2_installed() -> bool:
    """Returns True if Microsoft Edge WebView2 Runtime is registered in Windows Registry."""
    if sys.platform != "win32":
        return True

    import winreg

    reg_locations = (
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}"),
    )
    for root, subkey in reg_locations:
        try:
            with winreg.OpenKey(root, subkey) as key:
                pv, _ = winreg.QueryValueEx(key, "pv")
                if str(pv or "").strip() and str(pv).strip() != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def is_dotnet_framework_installed(min_release: int = NETFX_472_MIN_RELEASE) -> bool:
    """Returns True if .NET Framework 4.7.2+ is installed in Windows Registry."""
    if sys.platform != "win32":
        return True

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full") as key:
            release, _ = winreg.QueryValueEx(key, "Release")
            return int(release) >= int(min_release)
    except OSError:
        return False


def is_dotnet_desktop_runtime_installed() -> bool:
    """Returns True if .NET 6/7/8/9+ Windows Desktop Runtime is installed."""
    if sys.platform != "win32":
        return True

    import winreg

    reg_paths = (
        r"SOFTWARE\dotnet\Setup\InstalledVersions\x64\sharedfx\Microsoft.WindowsDesktop.App",
        r"SOFTWARE\WOW6432Node\dotnet\Setup\InstalledVersions\x64\sharedfx\Microsoft.WindowsDesktop.App",
    )
    for subkey in reg_paths:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as key:
                num_vals = winreg.QueryInfoKey(key)[1]
                if num_vals > 0:
                    return True
        except OSError:
            continue

    shared_dir = os.path.join(
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        "dotnet",
        "shared",
        "Microsoft.WindowsDesktop.App",
    )
    if os.path.isdir(shared_dir):
        try:
            if any(os.path.isdir(os.path.join(shared_dir, entry)) for entry in os.listdir(shared_dir)):
                return True
        except OSError:
            pass
    return False


def check_windows_prerequisites() -> Dict[str, Any]:
    """
    Checks whether required Windows runtimes (WebView2 and .NET) are installed.
    Returns a structured status dictionary.
    """
    webview2 = is_webview2_installed()
    netfx = is_dotnet_framework_installed()
    dotnet_desktop = is_dotnet_desktop_runtime_installed()
    dotnet_ok = netfx or dotnet_desktop

    missing: List[str] = []
    if not webview2:
        missing.append("Microsoft Edge WebView2 Runtime (MicrosoftEdgeWebview2Setup.exe)")
    if not dotnet_ok:
        missing.append(".NET Runtime (.NET Framework 4.7.2+ or .NET Desktop Runtime)")

    return {
        "ok": webview2 and dotnet_ok,
        "webview2_installed": webview2,
        "dotnet_framework_installed": netfx,
        "dotnet_desktop_installed": dotnet_desktop,
        "dotnet_installed": dotnet_ok,
        "missing": missing,
    }


def prompt_missing_prerequisites(status: Dict[str, Any]) -> bool:
    """
    Displays a native Windows MessageBox listing missing runtimes and offers to open
    Microsoft's download page. Returns True if startup can continue, False if fatal.
    """
    if status.get("ok", True) or sys.platform != "win32":
        return True

    missing_list = "\n".join(f"• {item}" for item in status.get("missing", []))
    message = (
        "Aether Desktop is missing required Windows runtime component(s):\n\n"
        f"{missing_list}\n\n"
        "Would you like to open the official Microsoft download page now?"
    )

    try:
        import ctypes
        MB_YESNO = 0x00000004
        MB_ICONWARNING = 0x00000030
        MB_TOPMOST = 0x00040000
        IDYES = 6
        res = ctypes.windll.user32.MessageBoxW(
            None,
            message,
            "Aether Desktop - Missing Prerequisites",
            MB_YESNO | MB_ICONWARNING | MB_TOPMOST,
        )
        if res == IDYES:
            if not status.get("webview2_installed", True):
                webbrowser.open(WEBVIEW2_DOWNLOAD_URL)
            if not status.get("dotnet_installed", True):
                webbrowser.open(DOTNET_DOWNLOAD_URL)
    except Exception:
        pass

    # WebView2 is strictly required for PyWebView to create any window
    return bool(status.get("webview2_installed", True))
