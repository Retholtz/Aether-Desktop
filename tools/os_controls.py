"""
Aether Desktop - Tier 1: Win32 Native OS Window Management
Provides deterministic, handle-based (HWND) window state control (focus, maximize, minimize, restore, close)
without relying on simulated cursor clicks or pixel coordinates.
"""

import sys
import time
import urllib.parse
from typing import Optional, Dict, List

from core.screen_stream import ensure_thread_desktop
from security.whitelist import WhitelistValidator

if sys.platform == "win32":
    import win32gui
    import win32con
    import win32process
else:
    win32gui = None
    win32con = None
    win32process = None

try:
    import psutil
except ImportError:
    psutil = None


def find_hwnd_by_query(query: str) -> Optional[int]:
    """
    Finds the primary top-level HWND matching an executable name or window title substring.
    Uses win32gui.EnumWindows to inspect visible windows and checks both window titles
    and process image names via psutil with alias normalization.
    """
    ensure_thread_desktop()

    if not win32gui:
        return None

    raw_query = query.lower().strip()
    norm_exe = WhitelistValidator.normalize_app_name(raw_query)
    query_base = norm_exe[:-4] if norm_exe.endswith(".exe") else norm_exe
    query_tokens = [t for t in raw_query.split() if len(t) >= 3]

    title_matches = []
    process_matches = []

    def enum_windows_callback(hwnd, extra):
        try:
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd).strip()
                title_lower = title.lower()

                # 1. Match against window title (exact query, normalized base, or token)
                if title_lower:
                    if raw_query in title_lower or query_base in title_lower:
                        title_matches.append(hwnd)
                        return True

                # 2. Match against process name
                if psutil and win32process:
                    try:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        proc = psutil.Process(pid)
                        proc_name = proc.name().lower()
                        proc_base = proc_name[:-4] if proc_name.endswith(".exe") else proc_name

                        # Check if process matches normalized base, raw query, or any query token
                        proc_matched = (
                            query_base == proc_base or
                            proc_base in query_base or
                            query_base in proc_base or
                            raw_query in proc_name or
                            any(token in proc_name for token in query_tokens)
                        )

                        if proc_matched:
                            if title:
                                title_matches.append(hwnd)
                            else:
                                process_matches.append(hwnd)
                    except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
                        pass
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(enum_windows_callback, None)
    except Exception as e:
        if not title_matches and not process_matches:
            print(f"[OS_CONTROLS] EnumWindows note: {e}")

    # Prioritize windows that have a visible title bar over background/helper windows
    if title_matches:
        return title_matches[0]
    if process_matches:
        return process_matches[0]
    return None


def maximize_window(app_name: str) -> dict:
    """
    Natively maximizes a target window using Win32 API handles.
    Restores first (SW_RESTORE) to ensure clean state transition if minimized,
    then maximizes (SW_MAXIMIZE) and brings to the foreground.
    """
    ensure_thread_desktop()
    if not win32gui or not win32con:
        return {"status": "error", "message": "Win32 window management is only supported on Windows."}

    hwnd = find_hwnd_by_query(app_name)
    if not hwnd:
        return {"status": "error", "message": f"No active window found for '{app_name}'."}

    try:
        title = win32gui.GetWindowText(hwnd) or app_name
        # Restore first to ensure clean state transition if minimized, then maximize
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        return {
            "status": "success",
            "action": "maximize_window",
            "app_name": app_name,
            "title": title,
            "hwnd": hwnd,
            "message": f"Successfully maximized '{title}'."
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to maximize '{app_name}': {str(e)}"}


def minimize_window(app_name: str) -> dict:
    """Natively minimizes a target window using Win32 API handles."""
    ensure_thread_desktop()
    if not win32gui or not win32con:
        return {"status": "error", "message": "Win32 window management is only supported on Windows."}

    hwnd = find_hwnd_by_query(app_name)
    if not hwnd:
        return {"status": "error", "message": f"No active window found for '{app_name}'."}

    try:
        title = win32gui.GetWindowText(hwnd) or app_name
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        return {
            "status": "success",
            "action": "minimize_window",
            "app_name": app_name,
            "title": title,
            "hwnd": hwnd,
            "message": f"Successfully minimized '{title}'."
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to minimize '{app_name}': {str(e)}"}


def restore_window(app_name: str) -> dict:
    """Restores a target window to its normal non-maximized, non-minimized state."""
    ensure_thread_desktop()
    if not win32gui or not win32con:
        return {"status": "error", "message": "Win32 window management is only supported on Windows."}

    hwnd = find_hwnd_by_query(app_name)
    if not hwnd:
        return {"status": "error", "message": f"No active window found for '{app_name}'."}

    try:
        title = win32gui.GetWindowText(hwnd) or app_name
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        return {
            "status": "success",
            "action": "restore_window",
            "app_name": app_name,
            "title": title,
            "hwnd": hwnd,
            "message": f"Successfully restored '{title}'."
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to restore '{app_name}': {str(e)}"}


def focus_window(app_name: str) -> dict:
    """Brings a target window to the foreground so it receives user and keyboard focus."""
    ensure_thread_desktop()
    if not win32gui or not win32con:
        return {"status": "error", "message": "Win32 window management is only supported on Windows."}

    hwnd = find_hwnd_by_query(app_name)
    if not hwnd:
        return {"status": "error", "message": f"No active window found for '{app_name}'."}

    try:
        title = win32gui.GetWindowText(hwnd) or app_name
        # If minimized, restore it first so it becomes visible
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.05)
        return {
            "status": "success",
            "action": "focus_window",
            "app_name": app_name,
            "title": title,
            "hwnd": hwnd,
            "message": f"Focused '{title}'."
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to focus '{app_name}': {str(e)}"}


def close_window(app_name: str) -> dict:
    """Natively closes the top-level window by posting WM_CLOSE message."""
    ensure_thread_desktop()
    if not win32gui or not win32con:
        return {"status": "error", "message": "Win32 window management is only supported on Windows."}

    hwnd = find_hwnd_by_query(app_name)
    if not hwnd:
        return {"status": "error", "message": f"No active window found for '{app_name}'."}

    try:
        title = win32gui.GetWindowText(hwnd) or app_name
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        return {
            "status": "success",
            "action": "close_window",
            "app_name": app_name,
            "title": title,
            "hwnd": hwnd,
            "message": f"Closed window '{title}'."
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to close '{app_name}': {str(e)}"}


def navigate_browser(query_or_url: str, app_name: str = "chrome", whitelist: Optional[List[str]] = None) -> dict:
    """
    Deterministically navigates an open browser (or launches it if not open) to a URL or search query.
    If the browser window is open:
        1. Brings the browser to the foreground (focus_window).
        2. Presses Ctrl+L to activate and highlight the omnibox address bar.
        3. Types the formatted URL/query and presses Enter.
    If the browser window is not open:
        Launches the browser with the target URL via WhitelistValidator.
    """
    ensure_thread_desktop()
    clean = str(query_or_url).strip()
    if not clean:
        return {"status": "error", "message": "query_or_url is required."}

    # Format URL / search query
    is_url = (
        clean.startswith("http://")
        or clean.startswith("https://")
        or clean.startswith("file://")
    )
    if not is_url:
        parts = clean.split()
        if len(parts) == 1 and ("." in clean or clean.startswith("localhost")):
            formatted_target = f"https://{clean}"
        else:
            formatted_target = f"https://www.google.com/search?q={urllib.parse.quote_plus(clean)}"
    else:
        formatted_target = clean

    hwnd = find_hwnd_by_query(app_name)
    if hwnd:
        try:
            focus_window(app_name)
            time.sleep(0.1)

            from tools.gui_primitives import GuiPrimitivesController
            gui = GuiPrimitivesController()
            gui.press_key("ctrl+l")
            time.sleep(0.08)
            gui.type_text(formatted_target, press_enter=True)

            return {
                "status": "success",
                "action": "navigate_browser",
                "app_name": app_name,
                "target": formatted_target,
                "method": "omnibox_ctrl_l",
                "message": f"Navigated {app_name} to '{formatted_target}'."
            }
        except Exception as e:
            return {"status": "error", "message": f"Failed to navigate {app_name}: {str(e)}"}
    else:
        wl = whitelist if whitelist is not None else ["*"]
        launch_res = WhitelistValidator.validate_and_launch(app_name, wl, target=formatted_target)
        return {
            "status": launch_res.get("status", "success"),
            "action": "navigate_browser",
            "app_name": app_name,
            "target": formatted_target,
            "method": "launch_process",
            "details": launch_res,
            "message": launch_res.get("message", f"Launched {app_name} with target '{formatted_target}'.")
        }


