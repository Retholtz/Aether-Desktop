import asyncio
import os
import sys
import threading
import webview

from core.gui_bridge import GuiBridge

def run_asyncio_loop(loop: asyncio.AbstractEventLoop):
    """Runs the background asyncio event loop for Gemini Live and audio tasks."""
    asyncio.set_event_loop(loop)
    loop.run_forever()

def main():
    print("=" * 60)
    print("           AETHER DESKTOP - INITIALIZING")
    print("=" * 60)

    # 1. Create a dedicated asyncio event loop on a background thread
    loop = asyncio.new_event_loop()
    bg_thread = threading.Thread(target=run_asyncio_loop, args=(loop,), daemon=True)
    bg_thread.start()

    # 2. Initialize GUI bridge
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "config.json")
    html_path = os.path.join(base_dir, "gui", "index.html")

    bridge = GuiBridge(config_path=config_path, loop=loop)

    # 3. Create pywebview window
    window = webview.create_window(
        title="Aether Desktop",
        url=html_path,
        js_api=bridge,
        width=1040,
        height=760,
        min_size=(880, 620),
        background_color="#202020"
    )
    bridge.set_window(window)

    print("[GUI] WebView window initialized. Launching Aether Desktop...")

    try:
        # Start GUI main loop (blocks until window is closed)
        webview.start(debug=False)
    finally:
        print("\n[SHUTDOWN] Window closed. Stopping assistant engine...")
        bridge.stop_assistant()
        loop.call_soon_threadsafe(loop.stop)
        print("[SHUTDOWN] Clean exit.")

if __name__ == "__main__":
    main()