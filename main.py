"""
Aether Desktop - Main Application Entrypoint & Session Orchestrator
Initializes background asyncio loop for Gemini Live Multimodal WebSocket session,
attaches GUI bridge, and launches PyWebView HUD overlay.
"""

import asyncio
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional


def strip_mark_of_the_web(target_dir: Path) -> int:
    """Removes NTFS Zone.Identifier alternate data streams from extracted binaries."""
    if os.name != "nt" or not target_dir.exists():
        return 0
    cleared = 0
    for root, _, files in os.walk(target_dir):
        for filename in files:
            ads_path = f"{os.path.join(root, filename)}:Zone.Identifier"
            try:
                if os.path.exists(ads_path):
                    os.remove(ads_path)
                    cleared += 1
            except OSError:
                pass
    return cleared


# Run immediately on frozen builds before pythonnet / pywebview loads .NET DLLs
if getattr(sys, "frozen", False):
    strip_mark_of_the_web(Path(sys.executable).resolve().parent)

# Fast-path staged self-updater helper mode (--apply-update): runs outside the live
# install directory without initializing WebView2, audio, or the single-instance mutex.
if "--apply-update" in sys.argv:
    from core.updater import apply_update_from_helper_cli
    sys.exit(apply_update_from_helper_cli(sys.argv[1:]))

# Fast-path sandboxed Python script runner mode (--run-script <path> [args_json]):
# allows frozen PyInstaller builds (where sys.executable is AetherDesktop.exe) to execute
# AST-validated automation/skill scripts in an isolated subprocess without launching the GUI.
if len(sys.argv) >= 3 and sys.argv[1] == "--run-script":
    import runpy
    _script_path = sys.argv[2]
    _script_args = sys.argv[3:]
    sys.argv = [_script_path] + _script_args
    try:
        runpy.run_path(_script_path, run_name="__main__")
        sys.exit(0)
    except SystemExit as _se:
        sys.exit(_se.code if isinstance(_se.code, int) else 0)
    except Exception as _exc:
        import traceback
        traceback.print_exc()
        sys.exit(1)

from core.screen_stream import ensure_thread_desktop, init_dpi_awareness
init_dpi_awareness()
ensure_thread_desktop()

from core.config_manager import load_config
from core.gui_bridge import GuiBridge
from core.paths import get_app_dir, is_frozen
from tools.script_runner import prune_script_cache
from ui.hud_window import HudWindow
from core.user_memory import (
    build_lexicon_instruction,
    add_dictionary_term,
    remove_dictionary_term,
    get_all_dictionary_terms,
)


def initialize_window(window):
    """
    Checks start_minimized upon application boot to hide or minimize the
    main application window immediately.
    """
    cfg = load_config(os.path.join(get_app_dir(), "config.json"))
    should_minimize = cfg.get("start_minimized", False)

    if should_minimize:
        # If running via pywebview or standard GUI window:
        print("[INFO] [GUI] Launching directly to minimized state per user preferences.")
        try:
            window.minimize()  # or window.hide() if system tray is active
        except Exception as e:
            print(f"[WARN] [GUI] Could not minimize window on start: {e}")
            window.show()
    else:
        window.show()

# ---------------------------------------------------------------------------
# Session Telemetry & Lifecycle State (Layer B Context Optimization)
# ---------------------------------------------------------------------------
turn_count: int = 0
session_start_time: float = 0.0
active_session: Any = None
active_engine: Any = None


def update_session_state(session: Any = None, turns: Optional[int] = None, start_time: Optional[float] = None):
    """Updates global session reference and telemetry metrics."""
    global active_session, turn_count, session_start_time
    if session is not None:
        active_session = session
    if turns is not None:
        turn_count = turns
    if start_time is not None:
        session_start_time = start_time


async def send_audio_loop(*args, **kwargs):
    """Module-level alias delegating to the active engine's send loop."""
    if active_engine and hasattr(active_engine, "_send_loop"):
        return await active_engine._send_loop(*args, **kwargs)


async def receive_audio_loop(*args, **kwargs):
    """Module-level alias delegating to the active engine's receive loop."""
    if active_engine and hasattr(active_engine, "_receive_loop"):
        return await active_engine._receive_loop(*args, **kwargs)


def run_asyncio_loop(loop: asyncio.AbstractEventLoop):
    """Runs the background asyncio event loop for Gemini Live and audio tasks."""
    ensure_thread_desktop()
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0)
        except Exception:
            pass
    asyncio.set_event_loop(loop)
    try:
        loop.run_forever()
    finally:
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass


def main():
    base_dir = get_app_dir()

    # Check for elevation argument (--elevate or --admin)
    if "--elevate" in sys.argv or "--admin" in sys.argv:
        from tools.os_controls import is_running_as_admin
        if not is_running_as_admin():
            import ctypes
            print("[INFO] [ELEVATE] Elevating to Administrator privileges via Windows UAC...")
            clean_args = [a for a in sys.argv[1:] if a not in ("--elevate", "--admin")]
            if is_frozen():
                target_args = [f'"{a}"' if " " in a else a for a in clean_args]
            else:
                main_script = os.path.abspath(__file__)
                target_args = [f'"{main_script}"']
                for a in clean_args:
                    if not a.endswith("main.py"):
                        target_args.append(f'"{a}"' if " " in a else a)
            params = " ".join(target_args)
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, base_dir, 1)
            if ret > 32:
                sys.exit(0)
            else:
                print(f"[WARN] [ELEVATE] User declined UAC prompt or elevation failed (code: {ret}). Continuing normal startup.")

    # Sweep leftover *.old / _internal.old / _update_staging artifacts from prior auto-updates
    try:
        from core.updater import cleanup_old_update_artifacts
        cleanup_old_update_artifacts(base_dir)
        if "--post-update" in sys.argv:
            def _deferred_cleanup():
                import time
                time.sleep(2.5)
                try:
                    cleanup_old_update_artifacts(base_dir)
                except Exception:
                    pass
            threading.Thread(target=_deferred_cleanup, daemon=True).start()
    except Exception:
        pass

    # Enforce Single-Instance application lock on Windows to prevent duplicate engines/voices
    _single_instance_mutex = None
    _kernel32 = None
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        import time
        ERROR_ALREADY_EXISTS = 183
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        _kernel32.CreateMutexW.restype = wintypes.HANDLE
        _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        _kernel32.CloseHandle.restype = wintypes.BOOL
        mutex_name = "Local\\AetherDesktop_SingleInstance_Mutex"
        post_update = "--post-update" in sys.argv
        max_attempts = 25 if post_update else 1
        for attempt in range(max_attempts):
            ctypes.set_last_error(0)
            _single_instance_mutex = _kernel32.CreateMutexW(None, False, mutex_name)
            last_err = ctypes.get_last_error()
            if last_err != ERROR_ALREADY_EXISTS:
                break
            if attempt + 1 < max_attempts:
                if _single_instance_mutex:
                    _kernel32.CloseHandle(_single_instance_mutex)
                    _single_instance_mutex = None
                time.sleep(0.2)
                continue
            print("[INFO] [SINGLE_INSTANCE] Aether Desktop is already running. Focusing existing window...")
            try:
                hwnd = ctypes.windll.user32.FindWindowW(None, "Aether Desktop")
                if hwnd:
                    ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
            sys.exit(0)

    # Check Windows runtime prerequisites (WebView2 & .NET) before launching GUI
    if sys.platform == "win32":
        try:
            from core.runtime_prereqs import check_windows_prerequisites, prompt_missing_prerequisites
            prereq_status = check_windows_prerequisites()
            if not prereq_status.get("ok", True):
                print(f"[WARN] [PREREQS] Missing runtime(s): {', '.join(prereq_status.get('missing', []))}")
                if not prompt_missing_prerequisites(prereq_status):
                    sys.exit(1)
        except SystemExit:
            raise
        except Exception as exc:
            print(f"[WARN] [PREREQS] Could not verify Windows runtimes: {exc}")

    print("=" * 60)
    print("           AETHER DESKTOP - MODULAR ARCHITECTURE")
    print("=" * 60)

    # 0. Fire-and-forget background cache maintenance (prunes scripts > 7 days old)
    threading.Thread(target=prune_script_cache, kwargs={"max_age_days": 7}, daemon=True).start()

    # 1. Create a dedicated asyncio event loop on a background worker thread
    loop = asyncio.new_event_loop()
    bg_thread = threading.Thread(target=run_asyncio_loop, args=(loop,), daemon=True)
    bg_thread.start()

    # 2. Initialize JSON-RPC GUI bridge
    config_path = os.path.join(base_dir, "config.json")
    bridge = GuiBridge(config_path=config_path, loop=loop)
    if hasattr(bridge._engine, "hotkey_manager") and bridge._engine.hotkey_manager:
        bridge._engine.hotkey_manager.start()

    # 3. Create PyWebView HUD window runner (loading from ui/static/)
    hud = HudWindow(
        bridge=bridge,
        title="Aether Desktop",
        width=1040,
        height=760,
        min_width=880,
        min_height=620,
        on_init=initialize_window,
    )

    print("[HUD] WebView window initialized. Launching Aether Desktop...")

    try:
        # Start GUI main loop (blocks until window is closed)
        hud.start(debug=False)
    finally:
        import logging
        print("\n[SHUTDOWN] Window closed. Stopping assistant engine...")
        shutdown_watchdog = threading.Timer(4.0, os._exit, args=(0,))
        shutdown_watchdog.daemon = True
        shutdown_watchdog.start()
        try:
            bridge.stop_audio_watcher()
        except Exception:
            pass
        try:
            bridge.stop_assistant()
        except Exception:
            pass
        if hasattr(bridge._engine, "hotkey_manager") and bridge._engine.hotkey_manager:
            try:
                bridge._engine.hotkey_manager.stop()
            except Exception:
                pass
        if hasattr(bridge._engine, "session_mgr") and bridge._engine.session_mgr:
            try:
                bridge._engine.session_mgr.save_current_session()
            except Exception:
                pass
        if hasattr(bridge._engine, "session_lifecycle") and bridge._engine.session_lifecycle:
            try:
                bridge._engine.session_lifecycle.flush_session_to_disk()
            except Exception:
                pass
        if hasattr(bridge._engine, "user_memory") and bridge._engine.user_memory:
            try:
                bridge._engine.user_memory.close()
            except Exception:
                pass
        if hasattr(bridge._engine, "telemetry_db") and bridge._engine.telemetry_db:
            try:
                bridge._engine.telemetry_db.close()
            except Exception:
                pass

        async def _graceful_drain_and_cancel():
            # Give in-flight cleanup tasks (e.g. close/disconnect coroutines scheduled by stop_assistant)
            # a short window to finish before force-cancelling remaining background tasks.
            pending = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
            if pending:
                done, still_pending = await asyncio.wait(pending, timeout=1.0)
                for t in still_pending:
                    t.cancel()
                if still_pending:
                    await asyncio.gather(*still_pending, return_exceptions=True)

        if loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(_graceful_drain_and_cancel(), loop)
                fut.result(timeout=2.5)
            except Exception:
                pass
            loop.call_soon_threadsafe(loop.stop)

        if _single_instance_mutex and sys.platform == "win32":
            try:
                if _kernel32 is not None:
                    _kernel32.CloseHandle(_single_instance_mutex)
                else:
                    import ctypes
                    ctypes.windll.kernel32.CloseHandle(_single_instance_mutex)
            except Exception:
                pass
        try:
            logging.shutdown()
        except Exception:
            pass
        print("[SHUTDOWN] Clean exit.")
        sys.stdout.flush()
        sys.stderr.flush()
        shutdown_watchdog.cancel()
        os._exit(0)


if __name__ == "__main__":
    main()