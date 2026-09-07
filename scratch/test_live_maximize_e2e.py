"""
End-to-End Test: Gemini Multimodal Live with Window State Dispatcher
Verifies that when asked to maximize Visual Studio Code, Gemini Live calls maximize_window natively
and the ToolDispatcher successfully executes it via Win32 HWND.
"""

import asyncio
import json
import os
import sys

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google import genai
from google.genai import types

from security.crypto import unprotect_secret
from tools.dispatcher import ToolDispatcher, get_all_tool_declarations
from tools.os_controls import restore_window

async def test_live_maximize_e2e():
    print("=" * 60)
    print("    TEST GEMINI LIVE E2E: NATIVE MAXIMIZE_WINDOW")
    print("=" * 60)

    # Load config
    with open("config.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    enc_key = cfg.get("api", {}).get("api_key_encrypted", "")
    api_key = unprotect_secret(enc_key) if enc_key else os.environ.get("GEMINI_API_KEY", "")
    assert api_key, "Gemini API Key is required!"

    model_id = "gemini-2.5-flash-native-audio-latest"
    print(f"[1] Connecting to Gemini Live ({model_id})...")

    dispatcher = ToolDispatcher(
        whitelist_getter=lambda: ["chrome.exe", "notepad.exe", "code.exe"]
    )

    client = genai.Client(api_key=api_key)

    live_tools = [
        types.Tool(google_search=types.GoogleSearch()),
        types.Tool(function_declarations=get_all_tool_declarations())
    ]

    system_instruction = (
        "You are Aether, a desktop assistant. When asked to maximize, minimize, restore, or focus windows, "
        "ALWAYS use the native Win32 window management tools (`maximize_window`, `minimize_window`, `restore_window`, `focus_window`). "
        "NEVER simulate mouse clicks on title bars."
    )

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        tools=live_tools,
        system_instruction=types.Content(
            parts=[types.Part.from_text(text=system_instruction)]
        )
    )

    async with client.aio.live.connect(model=model_id, config=config) as session:
        print("[CONNECTED] Sending prompt: 'Please maximize Visual Studio Code'...")

        prompt = types.Content(
            role="user",
            parts=[types.Part.from_text(text="Please maximize Visual Studio Code")]
        )
        await session.send_client_content(turns=[prompt], turn_complete=True)

        found_maximize_call = False
        async for response in session.receive():
            tool_call = getattr(response, "tool_call", None)
            if tool_call and tool_call.function_calls:
                for fc in tool_call.function_calls:
                    print(f"\n[GEMINI TOOL CALL] Function: '{fc.name}', Args: {fc.args}")
                    if fc.name == "maximize_window":
                        found_maximize_call = True
                        print("[MATCH] Gemini correctly called maximize_window!")
                        # Execute via dispatcher
                        res = await dispatcher.dispatch(fc.name, fc.args or {})
                        print("Execution result:", res)
                        assert res["status"] == "success", f"Dispatcher execution failed: {res}"

                        # Send tool response back to complete the turn
                        await session.send_tool_response(function_responses=[
                            types.FunctionResponse(id=fc.id, name=fc.name, response=res)
                        ])

                        # Restore back for normal desktop testing
                        restore_window("code")
                        break

            server_content = getattr(response, "server_content", None)
            if server_content and server_content.turn_complete:
                break

            if found_maximize_call:
                break

        assert found_maximize_call, "Gemini Live did not invoke maximize_window!"
        print("\n[PASS] Gemini Live correctly invoked maximize_window and Win32 HWND executed successfully!")

if __name__ == "__main__":
    asyncio.run(test_live_maximize_e2e())

