"""
Aether Desktop - Main Application Entrypoint & Session Orchestrator
Initializes background asyncio loop for Gemini Live Multimodal WebSocket session,
attaches GUI bridge, and launches PyWebView HUD overlay.
"""

import asyncio
import os
import sys
import threading

from core.screen_stream import ensure_thread_desktop, init_dpi_awareness
init_dpi_awareness()
ensure_thread_desktop()

from core.gui_bridge import GuiBridge
from tools.script_runner import prune_script_cache
from ui.hud_window import HudWindow


def run_asyncio_loop(loop: asyncio.AbstractEventLoop):
    """Runs the background asyncio event loop for Gemini Live and audio tasks."""
    ensure_thread_desktop()
    asyncio.set_event_loop(loop)
    loop.run_forever()


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
        loop.call_soon_threadsafe(loop.stop)
        print("[SHUTDOWN] Clean exit.")


if __name__ == "__main__":
    main()