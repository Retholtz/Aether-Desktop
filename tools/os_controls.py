"""
Aether Desktop - Tier 1: Win32 Native OS Window Management
Provides deterministic, handle-based (HWND) window state control (focus, maximize, minimize, restore, close)
without relying on simulated cursor clicks or pixel coordinates.
"""

import os
import re
import sys
import time
import urllib.parse
from typing import Optional, Dict, List, Tuple, Any

from core.screen_stream import ensure_thread_desktop
from security.whitelist import WhitelistValidator

import ctypes
from ctypes import wintypes

if sys.platform == "win32":
    import win32gui
    import win32con
    import win32process
    import win32clipboard
else:
    win32gui = None
    win32con = None
    win32process = None
    win32clipboard = None

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


def bring_hwnd_to_foreground(hwnd: int) -> bool:
    """
    Deterministically forces a window handle to the foreground across all Windows versions.
    Bypasses Windows LockSetForegroundWindow restrictions using:
    1. Un-minimizing / restoring window if iconic
    2. HWND_TOPMOST -> HWND_NOTOPMOST Z-order positioning
    3. Alt-key pulse (VK_MENU)
    4. AttachThreadInput synchronization between calling, foreground, and target threads
    5. BringWindowToTop, SwitchToThisWindow, and SetForegroundWindow
    6. Confirmation polling loop verifying GetForegroundWindow() == hwnd
    """
    ensure_thread_desktop()
    if not win32gui or not hwnd or not win32gui.IsWindow(hwnd):
        return False

    try:
        if win32gui.GetForegroundWindow() == hwnd:
            return True

        # 1. Restore if minimized, or show if hidden
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

        # 2. Force to top of Z-order via HWND_TOPMOST toggle
        try:
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
            )
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
            )
        except Exception:
            pass

        # 3. Pulse Alt key to grant foreground activation privilege
        try:
            ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
        except Exception:
            pass

        # 4. AttachThreadInput synchronization
        fore_hwnd = win32gui.GetForegroundWindow()
        fore_thread, _ = win32process.GetWindowThreadProcessId(fore_hwnd) if fore_hwnd else (0, 0)
        target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
        cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()

        attached_fore = False
        attached_target = False
        try:
            if fore_thread and cur_thread and fore_thread != cur_thread:
                win32process.AttachThreadInput(cur_thread, fore_thread, True)
                attached_fore = True
            if target_thread and cur_thread and target_thread != cur_thread:
                win32process.AttachThreadInput(cur_thread, target_thread, True)
                attached_target = True

            win32gui.BringWindowToTop(hwnd)
            try:
                ctypes.windll.user32.SwitchToThisWindow(hwnd, True)
            except Exception:
                pass
            win32gui.SetForegroundWindow(hwnd)
        finally:
            if attached_fore:
                try:
                    win32process.AttachThreadInput(cur_thread, fore_thread, False)
                except Exception:
                    pass
            if attached_target:
                try:
                    win32process.AttachThreadInput(cur_thread, target_thread, False)
                except Exception:
                    pass

        # 5. Polling verification loop (up to 300ms)
        for _ in range(15):
            if win32gui.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.02)

        return win32gui.GetForegroundWindow() == hwnd
    except Exception as e:
        print(f"[OS_CONTROLS] bring_hwnd_to_foreground note: {e}")
        return False


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
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        bring_hwnd_to_foreground(hwnd)
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
        bring_hwnd_to_foreground(hwnd)
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


def focus_window(app_name: str, auto_launch: bool = True) -> dict:
    """
    Brings a target window to the foreground so it receives user and keyboard focus.
    If the window is not currently open and auto_launch=True, attempts to launch it
    and waits for its window to appear before bringing it to the foreground.
    """
    ensure_thread_desktop()
    if not win32gui or not win32con:
        return {"status": "error", "message": "Win32 window management is only supported on Windows."}

    hwnd = find_hwnd_by_query(app_name)
    if not hwnd:
        if auto_launch:
            from security.whitelist import WhitelistValidator
            from core.config_manager import load_config

            cfg = load_config()
            user_whitelist = cfg.get("allowed_applications") or cfg.get("security", {}).get("app_whitelist", [])

            # Enforce active user whitelist instead of hardcoded wildcard ["*"]
            launch_res = WhitelistValidator.validate_and_launch(app_name, user_whitelist)
            if isinstance(launch_res, tuple):
                is_valid = launch_res[0]
                launch_err = launch_res[1]
            else:
                is_valid = launch_res.get("status") in ("success", "running")
                launch_err = launch_res.get("error", "Launch failed")

            if not is_valid:
                return {"status": "error", "message": f"App launch blocked by whitelist: {launch_err}"}

            for _ in range(25):
                time.sleep(0.1)
                hwnd = find_hwnd_by_query(app_name)
                if hwnd:
                    break

            if not hwnd:
                return {"status": "launched", "app": app_name}
        else:
            return {"status": "not_found", "app": app_name}


    try:
        title = win32gui.GetWindowText(hwnd) or app_name
        focused = bring_hwnd_to_foreground(hwnd)
        status_code = "success" if focused else "warning"
        return {
            "status": status_code,
            "action": "focus_window",
            "app_name": app_name,
            "title": title,
            "hwnd": hwnd,
            "focused": focused,
            "message": f"Focused '{title}'." if focused else f"Window '{title}' activated."
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


def register_background_monitor(
    task_id: str,
    description: str,
    python_code: str,
    interval_minutes: int = 60,
    workspace_root: Optional[str] = None
) -> str:
    """
    Registers a persistent background monitor script that runs periodically on boot.
    Args:
        task_id: Unique slug name for the task (e.g., 'real_estate_tracker')
        description: Plain text explanation of what the script checks
        python_code: The standalone Python code that executes the check
        interval_minutes: Polling frequency in minutes (e.g., 60, 240, 1440)
        workspace_root: Optional workspace root directory override
    """
    from core.startup_runner import STARTUP_DIR, StartupJobRunner
    from security.ast_gatekeeper import validate_python_script
    import re

    clean_task_id = re.sub(r"[^\w\-]", "_", str(task_id).strip().rstrip(".py"))
    if not clean_task_id:
        return "Error: task_id cannot be empty."

    raw_code = (python_code or "").strip()
    if not raw_code:
        return "Error: python_code cannot be empty."

    # Validate AST safety
    is_safe, error_msg = validate_python_script(raw_code)
    if not is_safe:
        return f"AST Security Gatekeeper Rejected: {error_msg}"

    effective_startup_dir = os.path.join(workspace_root, "scripts", "startup") if workspace_root else STARTUP_DIR
    os.makedirs(effective_startup_dir, exist_ok=True)

    filename = f"{clean_task_id}.py"
    target_path = os.path.join(effective_startup_dir, filename)

    if not raw_code.startswith("#"):
        code_to_write = f"# Auto-generated by Aether for: {description}\nimport sys\n\n{raw_code}\n"
    else:
        code_to_write = raw_code

    with open(target_path, "w", encoding="utf-8") as f:
        f.write(code_to_write)

    runner = StartupJobRunner(workspace_root=workspace_root)
    runner.register_task(
        task_id=clean_task_id,
        description=description,
        script_name=filename,
        interval_minutes=interval_minutes
    )

    return f"Monitor '{clean_task_id}' successfully saved and registered to run every {interval_minutes} minutes."


def register_monitoring_task(
    task_id: str,
    description: str,
    python_code: str,
    interval_minutes: int = 60,
    workspace_root: Optional[str] = None
) -> str:
    """Alias for register_background_monitor."""
    return register_background_monitor(
        task_id=task_id,
        description=description,
        python_code=python_code,
        interval_minutes=interval_minutes,
        workspace_root=workspace_root
    )


def list_background_monitors(workspace_root: Optional[str] = None) -> list:
    """Returns a list of all scheduled background monitors and their current statuses."""
    from core.startup_runner import StartupJobRunner
    runner = StartupJobRunner(workspace_root=workspace_root)
    data = runner.load_registry()
    return data.get("monitors", [])


def list_active_monitors(workspace_root: Optional[str] = None) -> list:
    """Alias for list_background_monitors."""
    return list_background_monitors(workspace_root=workspace_root)


def send_desktop_notification(title: str, message: str, play_chime: Optional[bool] = None) -> str:
    """
    Displays an ambient desktop notification/toast to the user with a chime.
    Use this when a long-running task completes or an alert needs user attention.
    """
    from ui.notifications import NotificationDispatcher

    if play_chime is None:
        play_chime = True
        try:
            import main
            eng = getattr(main, "active_engine", None)
            if eng and (getattr(eng, "is_speaking", False) or getattr(eng, "is_audio_streaming", False)):
                play_chime = False
        except Exception:
            pass

    dispatcher = NotificationDispatcher()
    dispatcher.notify(title=title, message=message, play_chime=bool(play_chime))
    return f"Desktop notification sent: '{title} - {message}'"


def inspect_screen_context(target: str = "active_window") -> dict:
    """
    Captures and inspects visual content currently displayed on the user's screen.
    Use this whenever the user asks you to look at, review, debug, read, or summarize 
    something visible on their display, desktop, browser, code editor, or active application.
    
    Args:
        target: 'active_window' (default, captures only focused app) or 'full_screen' (captures entire primary monitor)
    """
    from tools.screen_vision import capture_screen_image
    image_bytes, desc = capture_screen_image(target=target)
    return {
        "status": "success",
        "captured_target": desc,
        "image_bytes": image_bytes,
        "mime_type": "image/jpeg"
    }


class OSControls:
    """Provides high-level and native OS controls for window management and input."""

    def __init__(self):
        pass

    def maximize_window(self, app_name: str) -> dict:
        return maximize_window(app_name)

    def minimize_window(self, app_name: str) -> dict:
        return minimize_window(app_name)

    def restore_window(self, app_name: str) -> dict:
        return restore_window(app_name)

    def focus_window(self, app_name: str, auto_launch: bool = False) -> dict:
        return focus_window(app_name, auto_launch=auto_launch)

    def close_window(self, app_name: str) -> dict:
        return close_window(app_name)

    def navigate_browser(self, url: str) -> dict:
        return navigate_browser(url)

    def send_hotkey(self, *keys) -> dict:
        """Sends key combination, e.g. send_hotkey('ctrl', 'v') or send_hotkey('ctrl+v')."""
        from tools.gui_primitives import GuiPrimitivesController
        if not keys:
            return {"status": "error", "message": "No keys provided"}
        combo = "+".join(keys)
        controller = GuiPrimitivesController()
        return controller.press_key(combo)

    def send_directinput_key(self, key_name: str, duration_sec: float = 0.08) -> bool:
        """Sends a DirectInput hardware scancode pulse."""
        return send_directinput_key(key_name, duration_sec=duration_sec)

    def send_directinput_combo(self, modifiers: list[str], key_name: str, hold_duration: float = 0.08) -> bool:
        """Executes a modifier combo using DirectInput scancodes."""
        return send_directinput_combo(modifiers, key_name, hold_duration=hold_duration)

    def drag_mouse_relative(self, dx: int, dy: int, button: str = "right", steps: int = 12, step_delay: float = 0.015):
        """Relative mouse drag across screen for panning maps."""
        return drag_mouse_relative(dx, dy, button=button, steps=steps, step_delay=step_delay)

    def move_mouse_absolute(self, x: int, y: int) -> bool:
        """Moves the hardware cursor to screen coordinates (x, y)."""
        return move_mouse_absolute(x, y)

    def click_mouse_button(self, button: str = "right", hold_duration: float = 0.08) -> bool:
        """Clicks a mouse button (left or right) with hold duration."""
        return click_mouse_button(button=button, hold_duration=hold_duration)

    def send_gamepad_button(self, button_name: str, duration_sec: float = 0.1) -> bool:
        """Sends a hardware controller button press via ViGEmBus."""
        return send_gamepad_button(button_name, duration_sec=duration_sec)

    def pulse_key(self, key_name: str, duration_sec: float = 0.08) -> bool:
        """Universal DirectInput key pulse."""
        return pulse_key(key_name, duration_sec=duration_sec)

    def move_cursor_to_point(self, x: int, y: int, screen_width: int = 1920, screen_height: int = 1080):
        """Universal absolute cursor placement."""
        return move_cursor_to_point(x, y, screen_width=screen_width, screen_height=screen_height)

    def click_mouse(self, button: str = "left", duration_sec: float = 0.06):
        """Universal mouse click."""
        return click_mouse(button=button, duration_sec=duration_sec)

    def drag_viewport(self, dx: int, dy: int, button: str = "right", steps: int = 12):
        """Pans a viewport smoothly using relative mouse delta motions."""
        return drag_viewport(dx, dy, button=button, steps=steps)

    def zoom_viewport(self, notches: int):
        """Scrolls mouse wheel: negative = zoom out, positive = zoom in."""
        return zoom_viewport(notches=notches)

    def _type_keystrokes_direct(self, text: str) -> dict:
        """Types short text directly using standard keystroke simulation."""
        from tools.gui_primitives import GuiPrimitivesController
        controller = GuiPrimitivesController()
        return controller.type_text(text)

    def _get_clipboard_safe(self) -> Tuple[bool, Any, int]:
        """Attempts to read text from Windows clipboard. Returns (success, data, format)."""
        if not win32clipboard or not win32con:
            return False, None, 0
        try:
            win32clipboard.OpenClipboard()
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                win32clipboard.CloseClipboard()
                return True, data, win32con.CF_UNICODETEXT
            win32clipboard.CloseClipboard()
            return False, None, 0
        except Exception:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
            return False, None, 0

    def _set_clipboard_safe(self, text: str):
        """Safely sets unicode text onto Windows clipboard."""
        if not win32clipboard or not win32con:
            return False
        for _ in range(5):
            try:
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
                win32clipboard.CloseClipboard()
                return True
            except Exception:
                time.sleep(0.02)
        return False

    def type_text(self, text: str, fast_paste_threshold: int = 25) -> dict:
        """Types text with non-destructive clipboard preservation on large strings."""
        if not text:
            return {"status": "success", "length": 0}

        # For short strings, standard keystroke simulation is preferred
        if len(text) < fast_paste_threshold:
            self._type_keystrokes_direct(text)
            return {"status": "success", "method": "keystrokes", "length": len(text)}

        # Stash current clipboard
        had_clip, original_data, clip_format = self._get_clipboard_safe()

        try:
            # Set target text and execute paste
            self._set_clipboard_safe(text)
            time.sleep(0.05)
            self.send_hotkey("ctrl", "v")
            time.sleep(0.05)  # Allow target application to consume paste buffer
        finally:
            # Restore user's previous clipboard buffer
            if had_clip and original_data is not None:
                self._set_clipboard_safe(original_data)
            else:
                try:
                    win32clipboard.OpenClipboard()
                    win32clipboard.EmptyClipboard()
                    win32clipboard.CloseClipboard()
                except Exception:
                    pass

        return {"status": "success", "method": "clipboard_preserved", "length": len(text)}


def type_text(text: str, fast_paste_threshold: int = 25) -> dict:
    """Module-level type_text with non-destructive clipboard preservation."""
    return OSControls().type_text(text, fast_paste_threshold=fast_paste_threshold)


# DirectInput / Win32 Constants
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000

# Mapping common key names to DirectX / DirectInput hardware scancodes
SCANCODE_MAP = {
    # Alphanumeric
    "escape": 0x01, "esc": 0x01, "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05,
    "5": 0x06, "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B,
    "-": 0x0C, "=": 0x0D, "backspace": 0x0E, "tab": 0x0F,
    "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14, "y": 0x15,
    "u": 0x16, "i": 0x17, "o": 0x18, "p": 0x19, "[": 0x1A, "]": 0x1B,
    "enter": 0x1C, "return": 0x1C, "ctrl": 0x1D, "left_ctrl": 0x1D,
    "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22, "h": 0x23,
    "j": 0x24, "k": 0x25, "l": 0x26, ";": 0x27, "'": 0x28, "`": 0x29,
    "shift": 0x2A, "left_shift": 0x2A, "\\": 0x2B, "z": 0x2C, "x": 0x2D,
    "c": 0x2E, "v": 0x2F, "b": 0x30, "n": 0x31, "m": 0x32, ",": 0x33,
    ".": 0x34, "/": 0x35, "right_shift": 0x36, "alt": 0x38, "left_alt": 0x38,
    "space": 0x39, "spacebar": 0x39, "capslock": 0x3A,
    # Function keys
    "f1": 0x3B, "f2": 0x3C, "f3": 0x3D, "f4": 0x3E, "f5": 0x3F, "f6": 0x40,
    "f7": 0x41, "f8": 0x42, "f9": 0x43, "f10": 0x44, "f11": 0x57, "f12": 0x58,
    # Navigation / Arrow keys (Extended)
    "home": (0x47, True), "up": (0x48, True), "page_up": (0x49, True),
    "left": (0x4B, True), "right": (0x4D, True), "end": (0x4F, True),
    "down": (0x50, True), "page_down": (0x51, True), "insert": (0x52, True),
    "delete": (0x53, True),
    # Common aliases & extended keys
    "leftshift": 0x2A, "rightshift": 0x36, "leftctrl": 0x1D, "rightctrl": (0x1D, True),
    "control": 0x1D, "leftcontrol": 0x1D, "rightcontrol": (0x1D, True),
    "leftalt": 0x38, "rightalt": (0x38, True),
    "pageup": (0x49, True), "pagedown": (0x51, True),
    "uparrow": (0x48, True), "downarrow": (0x50, True),
    "leftarrow": (0x4B, True), "rightarrow": (0x4D, True),
}

# Win32 C Structs
ULONG_PTR = ctypes.c_size_t

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR)
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD)
    ]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR)
    ]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT)
    ]

class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _INPUT_UNION)
    ]


def _send_scancode_event(scancode: int, flags: int):
    """Low-level Win32 SendInput call."""
    if sys.platform != "win32":
        return
    ensure_thread_desktop()
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki = KEYBDINPUT(0, scancode, KEYEVENTF_SCANCODE | flags, 0, 0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

def send_directinput_key(key_name: str, duration_sec: float = 0.08) -> bool:
    """
    Sends a DirectInput hardware scancode pulse. 
    Maintains a hold duration (default 80ms) so 3D game engines register the frame tick.
    """
    normalized = key_name.lower().strip()
    mapping = SCANCODE_MAP.get(normalized)
    if not mapping:
        print(f"[WARN] [INPUT] Unknown key scancode for: {key_name}")
        return False

    if isinstance(mapping, tuple):
        scancode, is_extended = mapping
        base_flag = KEYEVENTF_EXTENDEDKEY if is_extended else 0
    else:
        scancode = mapping
        base_flag = 0

    # Key Down
    _send_scancode_event(scancode, base_flag)
    time.sleep(duration_sec)
    # Key Up
    _send_scancode_event(scancode, base_flag | KEYEVENTF_KEYUP)
    return True

def send_directinput_combo(modifiers: list[str], key_name: str, hold_duration: float = 0.08) -> bool:
    """Executes a modifier combo (e.g., Shift + Delete) using DirectInput scancodes."""
    active_mods = []
    try:
        # Press all modifiers down
        for mod in modifiers:
            m_norm = mod.lower().strip()
            mapping = SCANCODE_MAP.get(m_norm)
            if mapping:
                sc = mapping[0] if isinstance(mapping, tuple) else mapping
                ext = KEYEVENTF_EXTENDEDKEY if isinstance(mapping, tuple) and mapping[1] else 0
                _send_scancode_event(sc, ext)
                active_mods.append((sc, ext))
        
        time.sleep(0.02)
        # Pulse primary action key
        send_directinput_key(key_name, duration_sec=hold_duration)
        return True
    finally:
        # Release modifiers in reverse order
        for sc, ext in reversed(active_mods):
            _send_scancode_event(sc, ext | KEYEVENTF_KEYUP)


def drag_mouse_relative(dx: int, dy: int, button: str = "right", steps: int = 12, step_delay: float = 0.015):
    """
    Simulates clicking and dragging the mouse across the screen to pan game maps.
    Uses relative motion deltas (MOUSEEVENTF_MOVE).
    """
    if sys.platform != "win32":
        return
    ensure_thread_desktop()

    down_flag = MOUSEEVENTF_RIGHTDOWN if button.lower() == "right" else MOUSEEVENTF_LEFTDOWN
    up_flag = MOUSEEVENTF_RIGHTUP if button.lower() == "right" else MOUSEEVENTF_LEFTUP

    # 1. Mouse Button Down
    inp_down = INPUT()
    inp_down.type = INPUT_MOUSE
    inp_down.mi = MOUSEINPUT(0, 0, 0, down_flag, 0, 0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
    time.sleep(0.04)

    # 2. Smooth incremental drag steps
    steps = max(1, steps)
    step_x = int(dx / steps)
    step_y = int(dy / steps)
    for _ in range(steps):
        inp_move = INPUT()
        inp_move.type = INPUT_MOUSE
        inp_move.mi = MOUSEINPUT(step_x, step_y, 0, MOUSEEVENTF_MOVE, 0, 0)
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp_move), ctypes.sizeof(INPUT))
        time.sleep(step_delay)

    # 3. Mouse Button Up
    inp_up = INPUT()
    inp_up.type = INPUT_MOUSE
    inp_up.mi = MOUSEINPUT(0, 0, 0, up_flag, 0, 0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))
    time.sleep(0.04)


def move_mouse_absolute(x: int, y: int) -> bool:
    """Moves the hardware cursor to screen coordinates (x, y)."""
    if sys.platform != "win32":
        return False
    ensure_thread_desktop()
    return bool(ctypes.windll.user32.SetCursorPos(int(x), int(y)))


def click_mouse_button(button: str = "right", hold_duration: float = 0.08) -> bool:
    """Clicks a mouse button (left or right) with hold duration for 3D game engines."""
    if sys.platform != "win32":
        return False
    ensure_thread_desktop()
    down_flag = MOUSEEVENTF_RIGHTDOWN if button.lower() == "right" else MOUSEEVENTF_LEFTDOWN
    up_flag = MOUSEEVENTF_RIGHTUP if button.lower() == "right" else MOUSEEVENTF_LEFTUP

    inp_down = INPUT()
    inp_down.type = INPUT_MOUSE
    inp_down.mi = MOUSEINPUT(0, 0, 0, down_flag, 0, 0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
    time.sleep(hold_duration)

    inp_up = INPUT()
    inp_up.type = INPUT_MOUSE
    inp_up.mi = MOUSEINPUT(0, 0, 0, up_flag, 0, 0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))
    time.sleep(0.04)
    return True


def pulse_key(key_name: str, duration_sec: float = 0.08) -> bool:
    """Universal DirectInput key pulse."""
    return send_directinput_key(key_name, duration_sec=duration_sec)


def move_cursor_to_point(x: int, y: int, screen_width: int = 1920, screen_height: int = 1080):
    """Universal absolute cursor placement."""
    if sys.platform != "win32":
        return
    ensure_thread_desktop()
    norm_x = int(x * (65535.0 / screen_width))
    norm_y = int(y * (65535.0 / screen_height))
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(norm_x, norm_y, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, 0, 0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def click_mouse(button: str = "left", duration_sec: float = 0.06):
    """Universal mouse click."""
    if sys.platform != "win32":
        return
    ensure_thread_desktop()
    btn = button.lower().strip()
    down_flag = MOUSEEVENTF_RIGHTDOWN if btn == "right" else MOUSEEVENTF_LEFTDOWN
    up_flag = MOUSEEVENTF_RIGHTUP if btn == "right" else MOUSEEVENTF_LEFTUP

    inp_d = INPUT(type=INPUT_MOUSE)
    inp_d.mi = MOUSEINPUT(0, 0, 0, down_flag, 0, 0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp_d), ctypes.sizeof(INPUT))
    time.sleep(duration_sec)

    inp_u = INPUT(type=INPUT_MOUSE)
    inp_u.mi = MOUSEINPUT(0, 0, 0, up_flag, 0, 0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp_u), ctypes.sizeof(INPUT))


def drag_viewport(dx: int, dy: int, button: str = "right", steps: int = 12):
    """Pans a viewport smoothly using relative mouse delta motions."""
    if sys.platform != "win32":
        return
    ensure_thread_desktop()
    btn = button.lower().strip()
    down_flag = MOUSEEVENTF_RIGHTDOWN if btn == "right" else MOUSEEVENTF_LEFTDOWN
    up_flag = MOUSEEVENTF_RIGHTUP if btn == "right" else MOUSEEVENTF_LEFTUP

    inp_d = INPUT(type=INPUT_MOUSE)
    inp_d.mi = MOUSEINPUT(0, 0, 0, down_flag, 0, 0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp_d), ctypes.sizeof(INPUT))
    time.sleep(0.04)

    steps = max(1, steps)
    sx = int(dx / steps)
    sy = int(dy / steps)
    for _ in range(steps):
        inp_m = INPUT(type=INPUT_MOUSE)
        inp_m.mi = MOUSEINPUT(sx, sy, 0, MOUSEEVENTF_MOVE, 0, 0)
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp_m), ctypes.sizeof(INPUT))
        time.sleep(0.012)

    inp_u = INPUT(type=INPUT_MOUSE)
    inp_u.mi = MOUSEINPUT(0, 0, 0, up_flag, 0, 0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp_u), ctypes.sizeof(INPUT))
    time.sleep(0.04)


def zoom_viewport(notches: int):
    """Scrolls mouse wheel: negative = zoom out, positive = zoom in."""
    if sys.platform != "win32":
        return
    ensure_thread_desktop()
    WHEEL_DELTA = 120
    direction = 1 if notches > 0 else -1
    for _ in range(abs(notches)):
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(0, 0, direction * WHEEL_DELTA, MOUSEEVENTF_WHEEL, 0, 0)
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.03)


_VIRTUAL_GAMEPAD = None


def get_virtual_gamepad():
    """
    Lazy initialization for ViGEmBus virtual Xbox 360 controller.
    Gracefully falls back to None if vgamepad is not installed or ViGEmBus driver is absent.
    """
    global _VIRTUAL_GAMEPAD
    if _VIRTUAL_GAMEPAD is None:
        try:
            import vgamepad as vg
            _VIRTUAL_GAMEPAD = vg.VX360Gamepad()
            print("[INFO] [INPUT] ViGEmBus Virtual Xbox 360 Gamepad initialized.")
        except Exception as e:
            print(f"[WARN] [INPUT] Virtual gamepad unavailable: {e}")
            _VIRTUAL_GAMEPAD = False
    return _VIRTUAL_GAMEPAD if _VIRTUAL_GAMEPAD is not False else None


def send_gamepad_button(button_name: str, duration_sec: float = 0.1) -> bool:
    """
    Simulates pressing a button on a virtual Xbox 360 controller via ViGEmBus.
    Bypasses Windows LLKHF_INJECTED keyboard hook filters in protected game engines.
    """
    pad = get_virtual_gamepad()
    if not pad:
        return False

    try:
        import vgamepad as vg
        btn_key = button_name.lower().strip()
        if btn_key.startswith("gamepad_") or btn_key.startswith("btn_"):
            btn_key = btn_key.split("_", 1)[1]

        # Trigger routing if trigger name passed as button
        if btn_key in ("lt", "left_trigger"):
            return send_gamepad_trigger("lt", 1.0, duration_sec)
        elif btn_key in ("rt", "right_trigger"):
            return send_gamepad_trigger("rt", 1.0, duration_sec)

        btn_map = {
            "a": vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
            "b": vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
            "x": vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
            "y": vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
            "view": vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
            "back": vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
            "select": vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
            "menu": vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
            "start": vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
            "lb": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
            "left_shoulder": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
            "rb": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
            "right_shoulder": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
            "ls": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
            "left_thumb": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
            "rs": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
            "right_thumb": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
            "dpad_up": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
            "up": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
            "dpad_down": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
            "down": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
            "dpad_left": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
            "left": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
            "dpad_right": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
            "right": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
            "guide": vg.XUSB_BUTTON.XUSB_GAMEPAD_GUIDE,
            "xbox": vg.XUSB_BUTTON.XUSB_GAMEPAD_GUIDE,
        }
        btn = btn_map.get(btn_key)
        if not btn:
            print(f"[WARN] [INPUT] Unknown gamepad button: {button_name}")
            return False

        pad.press_button(button=btn)
        pad.update()
        time.sleep(duration_sec)
        pad.release_button(button=btn)
        pad.update()
        return True
    except Exception as e:
        print(f"[ERROR] [INPUT] Failed sending gamepad button '{button_name}': {e}")
        return False


def send_gamepad_stick(stick: str = "left", x: float = 0.0, y: float = 0.0, duration_sec: float = 0.2) -> bool:
    """
    Deflects a virtual gamepad analog thumbstick (x, y normalized from -1.0 to 1.0).
    stick: 'left' or 'right'.
    Duration holds the stick position before returning it to center (0.0, 0.0).
    """
    pad = get_virtual_gamepad()
    if not pad:
        return False
    try:
        stick_clean = stick.lower().strip()
        clamped_x = max(-1.0, min(1.0, float(x)))
        clamped_y = max(-1.0, min(1.0, float(y)))

        if stick_clean in ("left", "ls", "l"):
            pad.left_joystick_float(clamped_x, clamped_y)
        elif stick_clean in ("right", "rs", "r"):
            pad.right_joystick_float(clamped_x, clamped_y)
        else:
            print(f"[WARN] [INPUT] Unknown gamepad stick: {stick}")
            return False

        pad.update()
        time.sleep(duration_sec)

        # Center stick back
        if stick_clean in ("left", "ls", "l"):
            pad.left_joystick_float(0.0, 0.0)
        else:
            pad.right_joystick_float(0.0, 0.0)
        pad.update()
        return True
    except Exception as e:
        print(f"[ERROR] [INPUT] Failed moving gamepad stick '{stick}': {e}")
        return False


def send_gamepad_trigger(trigger: str = "rt", value: float = 1.0, duration_sec: float = 0.1) -> bool:
    """
    Simulates depressing a virtual gamepad analog trigger (0.0 to 1.0).
    trigger: 'left'/'lt' or 'right'/'rt'.
    """
    pad = get_virtual_gamepad()
    if not pad:
        return False
    try:
        trig_clean = trigger.lower().strip()
        clamped_val = max(0.0, min(1.0, float(value)))

        if trig_clean in ("left", "lt", "left_trigger"):
            pad.left_trigger_float(clamped_val)
        elif trig_clean in ("right", "rt", "right_trigger"):
            pad.right_trigger_float(clamped_val)
        else:
            print(f"[WARN] [INPUT] Unknown gamepad trigger: {trigger}")
            return False

        pad.update()
        time.sleep(duration_sec)

        # Release trigger
        if trig_clean in ("left", "lt", "left_trigger"):
            pad.left_trigger_float(0.0)
        else:
            pad.right_trigger_float(0.0)
        pad.update()
        return True
    except Exception as e:
        print(f"[ERROR] [INPUT] Failed sending gamepad trigger '{trigger}': {e}")
        return False


def send_gamepad_combo(buttons: list, duration_sec: float = 0.1) -> bool:
    """
    Simulates pressing multiple virtual gamepad buttons simultaneously (e.g. ['lb', 'a']).
    """
    pad = get_virtual_gamepad()
    if not pad:
        return False
    try:
        import vgamepad as vg
        btn_map = {
            "a": vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
            "b": vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
            "x": vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
            "y": vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
            "view": vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
            "back": vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
            "select": vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
            "menu": vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
            "start": vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
            "lb": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
            "left_shoulder": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
            "rb": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
            "right_shoulder": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
            "ls": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
            "left_thumb": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
            "rs": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
            "right_thumb": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
            "dpad_up": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
            "up": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
            "dpad_down": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
            "down": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
            "dpad_left": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
            "left": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
            "dpad_right": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
            "right": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
            "guide": vg.XUSB_BUTTON.XUSB_GAMEPAD_GUIDE,
            "xbox": vg.XUSB_BUTTON.XUSB_GAMEPAD_GUIDE,
        }
        resolved = []
        for b in buttons:
            k = b.lower().strip()
            if k.startswith("gamepad_") or k.startswith("btn_"):
                k = k.split("_", 1)[1]
            mapped = btn_map.get(k)
            if mapped:
                resolved.append(mapped)

        if not resolved:
            return False

        for btn in resolved:
            pad.press_button(button=btn)
        pad.update()
        time.sleep(duration_sec)
        for btn in resolved:
            pad.release_button(button=btn)
        pad.update()
        return True
    except Exception as e:
        print(f"[ERROR] [INPUT] Failed sending gamepad combo: {e}")
        return False


def is_running_as_admin() -> bool:
    """Checks if the current process is running with elevated Administrator privileges."""
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin() != 0)
    except Exception:
        return False
