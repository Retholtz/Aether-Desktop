"""
Test Central Tool Dispatcher
Tests routing to Tier 1, Tier 2, Tier 3, exception isolation, and event notification.
"""

import asyncio
import os
import sys

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.dispatcher import ToolDispatcher, get_all_tool_declarations

async def run_dispatcher_tests():
    print("=" * 60)
    print("           TEST CENTRAL TOOL DISPATCHER")
    print("=" * 60)

    events = []
    def on_event(ev_type, data):
        events.append((ev_type, data))

    whitelist = ["chrome.exe", "notepad.exe", "calc.exe"]
    dispatcher = ToolDispatcher(
        on_event=on_event,
        whitelist_getter=lambda: whitelist,
        whitelist_updater=lambda wl: {"status": "success"}
    )

    # 1. Verify declarations
    print("[1] Verifying all tool declarations...")
    decls = get_all_tool_declarations()
    decl_names = [d["name"] for d in decls]
    print(f"Loaded {len(decls)} tool declarations: {decl_names}")
    assert "maximize_window" in decl_names
    assert "minimize_window" in decl_names
    assert "restore_window" in decl_names
    assert "focus_window" in decl_names
    assert "type_text" in decl_names
    assert "execute_automation_script" in decl_names
    print("[PASS] Declarations verified.")

    # 2. Test dispatching maximize_window
    print("\n[2] Testing dispatch of 'maximize_window' on Chrome...")
    res_max = await dispatcher.dispatch("maximize_window", {"app_name": "chrome"})
    print("Dispatch result:", res_max)
    assert res_max["status"] == "success", f"maximize_window dispatch failed: {res_max}"

    # 3. Test dispatching type_text
    print("\n[3] Testing dispatch of 'type_text'...")
    res_type = await dispatcher.dispatch("type_text", {"text": "Dispatcher Test", "press_enter": False})
    print("Dispatch result:", res_type)
    assert res_type["status"] == "success"

    # 4. Test dispatching execute_automation_script
    print("\n[4] Testing dispatch of 'execute_automation_script'...")
    script_code = 'print("Hello from ToolDispatcher automation!")'
    res_script = await dispatcher.dispatch("execute_automation_script", {
        "script_code": script_code,
        "script_type": "python",
        "description": "Greeting"
    })
    print("Dispatch result:", res_script)
    assert res_script["status"] == "success"
    assert "Hello from ToolDispatcher" in res_script["stdout"]

    # 5. Test exception isolation on unknown tool
    print("\n[5] Testing exception isolation with unknown tool...")
    res_unknown = await dispatcher.dispatch("non_existent_tool_123", {})
    print("Dispatch result:", res_unknown)
    assert res_unknown["status"] == "error"
    print("[PASS] Unknown tool handled gracefully without throwing.")

    # 6. Verify event notification history
    print(f"\n[6] Verifying emitted events (Total: {len(events)})...")
    for ev_type, data in events:
        safe_content = str(data.get("content", "")).encode("ascii", "replace").decode("ascii")
        print(f" - [{ev_type}] {safe_content}")
    assert len(events) >= 3

    print("\n[PASS] All Central Tool Dispatcher tests passed successfully!")

if __name__ == "__main__":
    asyncio.run(run_dispatcher_tests())

