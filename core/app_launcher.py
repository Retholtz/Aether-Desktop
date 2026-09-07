"""
Aether Desktop - Core App Launcher Proxy
Re-exports WhitelistValidator as DesktopAppLauncher for backwards compatibility.
"""

from security.whitelist import WhitelistValidator as DesktopAppLauncher, COMMON_ALIASES, PROCESS_KILL_TARGETS

__all__ = ["DesktopAppLauncher", "COMMON_ALIASES", "PROCESS_KILL_TARGETS"]
