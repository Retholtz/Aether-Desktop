"""
Aether Desktop - Main Application Entrypoint & Session Orchestrator
Initializes background asyncio loop for Gemini Live Multimodal WebSocket session,
attaches GUI bridge, and launches PyWebView HUD overlay.
"""

import asyncio
import os
import sys
import threading
from typing import Any, Optional

from core.screen_stream import ensure_thread_desktop, init_dpi_awareness
init_dpi_awareness()
ensure_thread_desktop()

from core.gui_bridge import GuiBridge
from tools.script_runner import prune_script_cache
from ui.hud_window import HudWindow
from core.user_memory import (
    build_lexicon_instruction,
    add_dictionary_term,
    remove_dictionary_term,
    get_all_dictionary_terms,
)

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
    base_dir = os.path.dirname(os.path.abspath(__file__))
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
    )

    print("[HUD] WebView window initialized. Launching Aether Desktop...")

    try:
        # Start GUI main loop (blocks until window is closed)
        hud.start(debug=False)
    finally:
        print("\n[SHUTDOWN] Window closed. Stopping assistant engine...")
        bridge.stop_assistant()
        if hasattr(bridge._engine, "hotkey_manager") and bridge._engine.hotkey_manager:
            try:
                bridge._engine.hotkey_manager.stop()
            except Exception:
                pass
        if hasattr(bridge._engine, "telemetry_db") and bridge._engine.telemetry_db:
            try:
                bridge._engine.telemetry_db.close()
            except Exception:
                pass
        loop.call_soon_threadsafe(loop.stop)
        print("[SHUTDOWN] Clean exit.")


if __name__ == "__main__":
    main()