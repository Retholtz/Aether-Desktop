import asyncio
import json
import os
import sys
import time
import traceback
from typing import Callable, Optional

from google import genai
from google.genai import types

from core.screen_stream import ScreenCapturePipeline, ensure_thread_desktop
ensure_thread_desktop()

from core.audio_stream import AudioPipeline
from security.crypto import unprotect_secret
from tools.dispatcher import ToolDispatcher, get_all_tool_declarations

# The 30 Gemini Live prebuilt voices - Alphabetized
RAW_GEMINI_VOICES = [
    {"name": "Achernar", "trait": "Soft", "gender": "Female-sounding"},
    {"name": "Achird", "trait": "Friendly", "gender": "Male-sounding"},
    {"name": "Algenib", "trait": "Gravelly", "gender": "Male-sounding"},
    {"name": "Algieba", "trait": "Smooth", "gender": "Male-sounding"},
    {"name": "Alnilam", "trait": "Firm", "gender": "Male-sounding"},
    {"name": "Aoede", "trait": "Breezy", "gender": "Female-sounding"},
    {"name": "Autonoe", "trait": "Bright", "gender": "Female-sounding"},
    {"name": "Callirrhoe", "trait": "Easy-going", "gender": "Female-sounding"},
    {"name": "Charon", "trait": "Informative", "gender": "Male-sounding"},
    {"name": "Despina", "trait": "Smooth", "gender": "Female-sounding"},
    {"name": "Enceladus", "trait": "Breathy", "gender": "Male-sounding"},
    {"name": "Erinome", "trait": "Clear", "gender": "Female-sounding"},
    {"name": "Fenrir", "trait": "Excitable", "gender": "Male-sounding"},
    {"name": "Gacrux", "trait": "Mature", "gender": "Male-sounding"},
    {"name": "Iapetus", "trait": "Clear", "gender": "Male-sounding"},
    {"name": "Kore", "trait": "Firm", "gender": "Female-sounding"},
    {"name": "Laomedeia", "trait": "Upbeat", "gender": "Female-sounding"},
    {"name": "Leda", "trait": "Youthful", "gender": "Female-sounding"},
    {"name": "Orus", "trait": "Firm", "gender": "Male-sounding"},
    {"name": "Puck", "trait": "Upbeat", "gender": "Male-sounding"},
    {"name": "Pulcherrima", "trait": "Forward", "gender": "Female-sounding"},
    {"name": "Rasalgethi", "trait": "Informative", "gender": "Male-sounding"},
    {"name": "Sadachbia", "trait": "Lively", "gender": "Male-sounding"},
    {"name": "Sadaltager", "trait": "Knowledgeable", "gender": "Male-sounding"},
    {"name": "Schedar", "trait": "Even", "gender": "Male-sounding"},
    {"name": "Sulafat", "trait": "Warm", "gender": "Female-sounding"},
    {"name": "Umbriel", "trait": "Easy-going", "gender": "Male-sounding"},
    {"name": "Vindemiatrix", "trait": "Gentle", "gender": "Female-sounding"},
    {"name": "Zephyr", "trait": "Bright", "gender": "Female-sounding"},
    {"name": "Zubenelgenubi", "trait": "Casual", "gender": "Male-sounding"}
]
GEMINI_VOICES = sorted(RAW_GEMINI_VOICES, key=lambda x: x["name"])

class AetherEngine:
    """
    Manages the Gemini Multimodal Live API session, Audio Pipeline,
    speech/text streaming, barge-in, voice kill phrase detection, and GUI events.
    Delegates all tool executions to the modular ToolDispatcher.
    """
    def __init__(
        self,
        config_getter: Callable[[], dict],
        on_event: Callable[[str, dict], None],
        on_whitelist_update: Optional[Callable[[list], dict]] = None,
        config_path: str = "config.json"
    ):
        self.config_getter = config_getter
        self.on_event = on_event
        self.on_whitelist_update = on_whitelist_update
        self.config_path = config_path
        self.is_running = False
        self.audio: Optional[AudioPipeline] = None
        self.session = None
        self._main_task = None
        self._text_queue = asyncio.Queue()
        self.current_turn_text = ""
        self.current_user_speech = ""

        # Screen capture pipeline
        self.screen_pipeline = ScreenCapturePipeline()

        # Modular Tool Dispatcher (Tier 1 OS, Tier 2 GUI, Tier 3 Scripts, Security)
        self.dispatcher = ToolDispatcher(
            screen_pipeline=self.screen_pipeline,
            on_event=self.notify,
            whitelist_getter=self._get_whitelist,
            whitelist_updater=self._update_whitelist
        )

    def _get_whitelist(self) -> list:
        cfg = self.config_getter()
        return cfg.get("security", {}).get("app_whitelist", [])

    def _update_whitelist(self, new_whitelist: list) -> dict:
        if self.on_whitelist_update:
            return self.on_whitelist_update(new_whitelist)
        else:
            try:
                cfg = self.config_getter()
                cfg.setdefault("security", {})["app_whitelist"] = new_whitelist
                with open(self.config_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2)
                self.notify("config_updated", cfg)
                return {"success": True}
            except Exception as e:
                print(f"[WHITELIST PERSIST ERROR] {e}")
                return {"success": False, "error": str(e)}

    def notify(self, event_type: str, data: dict):
        if self.on_event:
            try:
                self.on_event(event_type, data)
            except Exception as e:
                print(f"[ENGINE EVENT ERROR] {e}")

    def kill_audio(self):
        """Immediately halts audio playback and drains all output buffers."""
        if self.audio:
            self.audio.kill_output()
        self.notify("status", {"state": "speaking_interrupted", "message": "Playback killed."})
        self.notify("chat_event", {
            "type": "system",
            "content": "Playback halted by Voice Playback Kill Phrase."
        })

    def set_ptt(self, active: bool):
        if self.audio:
            self.audio.set_ptt(active)
            self.notify("mic_status", {"ptt_active": active, "is_speaking": self.audio.is_speaking})

    async def send_text(self, text: str):
        """Queues a typed message to be sent into the active Gemini Live session."""
        if not text.strip():
            return
        await self._text_queue.put(text.strip())
        self.notify("chat_event", {
            "type": "user",
            "content": text.strip(),
            "source": "text"
        })

    async def _send_loop(self, session):
        """Streams microphone PCM audio, real-time desktop vision frames, and typed messages to the Live session."""
        last_vision_time = 0.0
        try:
            while self.is_running:
                current_cfg = self.config_getter()

                # 1. Check for any queued typed text messages
                while not self._text_queue.empty():
                    typed_text = self._text_queue.get_nowait()
                    content = types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=typed_text)]
                    )
                    await session.send_client_content(turns=[content], turn_complete=True)

                # 2. Check for microphone audio
                try:
                    pcm_data = await asyncio.wait_for(self.audio.input_queue.get(), timeout=0.02)
                    await session.send_realtime_input(
                        audio=types.Blob(
                            data=pcm_data,
                            mime_type="audio/pcm;rate=16000"
                        )
                    )
                except asyncio.TimeoutError:
                    pass

                # 3. Stream real-time desktop vision frames if enabled
                vision_cfg = current_cfg.get("vision", {})
                if vision_cfg.get("enabled", True):
                    vision_fps = float(vision_cfg.get("fps", 1.0))
                    vision_interval = 1.0 / max(0.2, min(5.0, vision_fps))
                    now = time.time()
                    if (now - last_vision_time) >= vision_interval:
                        last_vision_time = now
                        try:
                            target_mon = vision_cfg.get("monitor", "auto")
                            jpeg_bytes, meta = await self.screen_pipeline.capture_frame(target=target_mon)
                            await session.send_realtime_input(
                                video=types.Blob(
                                    data=jpeg_bytes,
                                    mime_type="image/jpeg"
                                )
                            )
                        except Exception:
                            pass

                await asyncio.sleep(0.001)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[SEND LOOP ERROR] {e}")

    async def _receive_loop(self, session, kill_phrase: str, agent_name: str):
        """Processes incoming model audio, speech transcriptions, and interruption events across multiple turns."""
        try:
            self.current_user_speech = ""
            self.current_turn_text = ""

            while self.is_running:
                async for response in session.receive():
                    if not self.is_running:
                        break

                    # 0. Handle Tool Calls from Model via Modular Dispatcher
                    tool_call = getattr(response, "tool_call", None)
                    if tool_call is not None and tool_call.function_calls:
                        function_responses = []

                        for fc in tool_call.function_calls:
                            fn_name = fc.name
                            fn_args = fc.args or {}
                            fc_id = fc.id
                            print(f"[TOOL CALL] Function: {fn_name}, Args: {fn_args}, ID: {fc_id}")

                            result = await self.dispatcher.dispatch(fn_name, fn_args)

                            # If a snapshot was requested, stream the JPEG frame back to the live session
                            if fn_name == "capture_screen_snapshot" and "_jpeg_bytes" in result:
                                raw_jpeg = result.pop("_jpeg_bytes")
                                try:
                                    await session.send_realtime_input(
                                        video=types.Blob(data=raw_jpeg, mime_type="image/jpeg")
                                    )
                                except Exception as e:
                                    print(f"[SNAPSHOT SEND ERROR] {e}")

                            function_responses.append(types.FunctionResponse(
                                id=fc_id,
                                name=fn_name,
                                response=result
                            ))

                        if function_responses:
                            await session.send_tool_response(function_responses=function_responses)

                    server_content = getattr(response, "server_content", None)
                    if server_content is None:
                        continue

                    # 1. User Input Transcription (Recognized user spoken words)
                    input_trans = getattr(server_content, "input_transcription", None)
                    if input_trans and input_trans.text:
                        self.current_user_speech += input_trans.text

                        clean_user_speech = self.current_user_speech.strip()
                        self.notify("user_speech_stream", {
                            "text": clean_user_speech
                        })

                        # Check Voice Playback Kill Phrase
                        if kill_phrase and kill_phrase.lower() in clean_user_speech.lower():
                            print(f"\n[KILL PHRASE DETECTED: '{clean_user_speech}'] -> Halting audio!")
                            self.kill_audio()

                    # 2. Model Audio Generation
                    model_turn = getattr(server_content, "model_turn", None)
                    if model_turn is not None:
                        # Finalize user speech turn when model starts responding
                        if self.current_user_speech.strip():
                            self.notify("chat_event", {
                                "type": "user",
                                "content": self.current_user_speech.strip(),
                                "source": "voice"
                            })
                            self.current_user_speech = ""

                        for part in model_turn.parts or []:
                            # Feed raw audio chunks directly to playback buffer
                            inline_data = getattr(part, "inline_data", None)
                            if inline_data is not None and inline_data.data:
                                self.audio.write_output_chunk(inline_data.data)
                                self.notify("status", {"state": "speaking", "message": f"{agent_name} is speaking..."})

                            text_part = getattr(part, "text", None)
                            if text_part:
                                self.current_turn_text += text_part

                    # 3. Model Output Transcription
                    output_trans = getattr(server_content, "output_transcription", None)
                    if output_trans and output_trans.text:
                        self.current_turn_text += output_trans.text

                    # 4. Search Grounding / Tool Events
                    grounding = getattr(server_content, "grounding_metadata", None)
                    if grounding:
                        queries = getattr(grounding, "web_search_queries", None)
                        if queries:
                            for q in queries:
                                self.notify("chat_event", {
                                    "type": "tool",
                                    "name": "Google Search",
                                    "content": f"Searching: {q}"
                                })

                    # 5. Native Interruption Handling (Barge-in)
                    if server_content.interrupted:
                        print("\n[USER INTERRUPTED - BARGE-IN]")
                        self.kill_audio()
                        self.current_turn_text = ""
                        if self.current_user_speech.strip():
                            self.notify("chat_event", {
                                "type": "user",
                                "content": self.current_user_speech.strip(),
                                "source": "voice"
                            })
                            self.current_user_speech = ""

                    # 6. Turn Complete
                    if server_content.turn_complete:
                        # Finalize user speech if not already finalized
                        if self.current_user_speech.strip():
                            self.notify("chat_event", {
                                "type": "user",
                                "content": self.current_user_speech.strip(),
                                "source": "voice"
                            })
                            self.current_user_speech = ""

                        if self.current_turn_text.strip():
                            self.notify("chat_event", {
                                "type": "assistant",
                                "agent_name": agent_name,
                                "content": self.current_turn_text.strip()
                            })
                        self.current_turn_text = ""

                        # Wait for hardware buffer to drain
                        wait_count = 0
                        while self.audio and not self.audio.is_output_empty() and wait_count < 40 and self.is_running:
                            await asyncio.sleep(0.05)
                            wait_count += 1

                        if self.audio:
                            self.audio.clear_output_buffer()
                        if self.is_running:
                            self.notify("status", {"state": "listening", "message": f"{agent_name} is listening..."})

                # Avoid busy-loop if session closes
                if self.is_running:
                    await asyncio.sleep(0.01)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            err_str = str(e)
            if not self.is_running or "1000" in err_str or "normal" in err_str.lower():
                pass
            else:
                print(f"\n[RECEIVE LOOP ERROR] {e}")
                if self.audio:
                    self.audio.kill_output()
                self.notify("status", {"state": "error", "message": f"Receive error: {e}"})
                self.notify("chat_event", {
                    "type": "error",
                    "content": f"Audio receive error: {e}"
                })

    async def _run(self):
        self.is_running = True
        config = self.config_getter()
        api_cfg = config.get("api", {})
        audio_cfg = config.get("audio", {})

        # Resolve Agent Name
        agent_name = api_cfg.get("agent_name", "Aether").strip() or "Aether"

        # Resolve API Key
        encrypted_key = api_cfg.get("api_key_encrypted", "")
        api_key = unprotect_secret(encrypted_key) if encrypted_key else os.environ.get("GEMINI_API_KEY", "")

        if not api_key:
            err_msg = "Gemini API Key is not configured! Please open Settings and enter your API Key."
            self.notify("status", {"state": "error", "message": err_msg})
            self.notify("chat_event", {"type": "error", "content": err_msg})
            self.is_running = False
            return

        # Use the verified working Multimodal Live model endpoint
        model_id = api_cfg.get("model_id", "gemini-2.5-flash-native-audio-latest")
        if model_id == "gemini-2.0-flash-exp":
            model_id = "gemini-2.5-flash-native-audio-latest"

        voice_name = api_cfg.get("voice_name", "Aoede")
        temperature = float(api_cfg.get("temperature", 1.0))

        # System instructions with strict identity directive and [Agent Name] template support
        raw_instruction = api_cfg.get(
            "system_instruction",
            "You are [Agent Name], a fast, knowledgeable desktop voice assistant. "
            "You have Google Search available to check real-time facts, weather, news, and details. "
            "When asked for live information, search the web and answer conversationally and concisely. "
            "If the user says 'stop', '[Agent Name] stop', or tells you to stop speaking, halt immediately. "
            "Always keep answers brief (1-3 sentences) unless asked to elaborate."
        )
        templated_instruction = (
            raw_instruction
            .replace("[Agent Name]", agent_name)
            .replace("[agent name]", agent_name)
            .replace("[AGENT NAME]", agent_name)
            .replace("{agent_name}", agent_name)
            .replace("{Agent Name}", agent_name)
        )

        strict_identity = (
            f"CRITICAL SYSTEM DIRECTIVE ON IDENTITY:\n"
            f"Your name is {agent_name}. You are {agent_name}.\n"
            f"Under NO circumstances should you ever call yourself 'Aether', 'Gemini', or any name other than '{agent_name}'.\n"
            f"Whenever asked about your identity, name, or who you are, you must ALWAYS explicitly state that your name is {agent_name}.\n\n"
        )
        desktop_tools_directive = (
            "DESKTOP VISION, DETERMINISTIC WIN32 CONTROL & SCRIPT AUTOMATION:\n"
            "You have real-time vision of the user's desktop screen and powerful tools to interact directly with applications:\n"
            "- Real-Time Vision: You continuously receive video frames of the user's desktop (with multi-monitor tracking). You can see open windows, buttons, search bars, and text on screen.\n"
            "- Launching Applications & Browser Profiles: When asked to open/launch an application (e.g. 'Open Chrome', 'Launch Notepad', 'Open Word', 'Open Excel'), call `launch_application(app_name=...)`. When asked to sign in or use a specific browser user/profile (e.g. 'Open Chrome and sign in as Michael', 'Launch Chrome as Traci', 'Open Chrome as Noah'), call `launch_application(app_name='chrome', profile='Michael')` (or 'Traci', 'Noah', etc.). This launches directly into that user profile natively without showing the profile picker dialog.\n"
            "- Deterministic In-Browser Search & Omnibox Navigation: When asked to search or navigate in an open browser (e.g. 'search for News today', 'open MSN.com', 'go to YouTube', 'look up weather in Chrome'), ALWAYS call `navigate_browser(query_or_url=..., app_name='chrome')`. NEVER attempt to guess or click mouse coordinates on the search bar or address bar—`navigate_browser` uses deterministic Win32 window focus and Ctrl+L omnibox navigation!\n"
            "- Deterministic Win32 Window Management: When asked to maximize, minimize, restore, or focus a window (e.g. 'Maximize Chrome', 'Minimize Spotify', 'Restore Notepad', 'Focus Excel', 'Bring Chrome to front'), ALWAYS call `maximize_window(app_name)`, `minimize_window(app_name)`, `restore_window(app_name)`, or `focus_window(app_name)`. NEVER attempt to click title bars, top-right corners, or maximize icons with mouse coordinates.\n"
            "- Mouse Clicking: When asked to click on a button, icon, link, or coordinate inside an application or screen, call `mouse_click(x, y, app_name=optional)` using normalized 0-1000 coordinates. When clicking inside an application (such as Chrome or Notepad), always pass `app_name` so coordinates map accurately relative to the window's physical bounds.\n"
            "- Typing: When asked to type text into a focused field or application, call `type_text(text, press_enter=True/False, app_name=optional)`.\n"
            "- Keyboard Shortcuts: When asked to press keys or shortcuts (e.g. new tab, enter, escape, select all), call `press_key(key_combo)`.\n"
            "- Scrolling: When asked to scroll a page or list, call `scroll_page(direction, amount)`.\n"
            "- Screen Inspection: When you need a high-detail snapshot of a specific display, call `capture_screen_snapshot(monitor)`.\n"
            "- On-The-Fly Script Automation: For complex operations (such as creating/formatting tables in Word or Excel, populating spreadsheets, calculating data, or automating multi-step document tasks), write and run an on-the-fly Python script via `execute_automation_script(script_code, description)`. Python scripts have full access to native Microsoft Office COM via `win32com.client` (e.g. `win32com.client.Dispatch('Excel.Application')` or `Dispatch('Word.Application')`) so you can manipulate documents, rows, columns, and cells live in the background!\n"
            "- Whitelist: If an app is blocked by the security whitelist and the user asks to add or allow it, call `add_to_whitelist(app_name)`.\n"
            "- After executing actions, provide a brief, polite verbal confirmation (1-2 sentences). You can chain multiple actions smoothly.\n\n"
        )
        system_instruction_text = strict_identity + desktop_tools_directive + templated_instruction

        audio_mode = audio_cfg.get("mode", "always_on")
        kill_phrase = audio_cfg.get("safe_phrase", f"{agent_name} stop").strip()
        kill_phrase = (
            kill_phrase
            .replace("[Agent Name]", agent_name)
            .replace("[agent name]", agent_name)
            .replace("[AGENT NAME]", agent_name)
            .replace("{agent_name}", agent_name)
        )

        software_gate = audio_cfg.get("software_gate", False)
        in_idx = audio_cfg.get("input_device_index", 0)
        out_idx = audio_cfg.get("output_device_index", 0)

        self.notify("status", {"state": "connecting", "message": f"Opening audio devices ({in_idx}, {out_idx})..."})

        try:
            self.audio = AudioPipeline(
                input_device=in_idx,
                output_device=out_idx,
                mode=audio_mode,
                software_gate=software_gate
            )
            await self.audio.start()
        except Exception as e:
            err_msg = f"Failed to initialize audio devices (Input #{in_idx}, Output #{out_idx}): {e}"
            self.notify("status", {"state": "error", "message": err_msg})
            self.notify("chat_event", {"type": "error", "content": err_msg})
            self.is_running = False
            return

        self.notify("status", {"state": "connecting", "message": f"Connecting to Gemini Live ({model_id}, Voice: {voice_name})..."})

        try:
            client = genai.Client(api_key=api_key)

            if kill_phrase:
                system_instruction_text += f"\nImportant: If the user says '{kill_phrase}' or 'stop', halt speaking immediately."

            live_tools = [
                types.Tool(google_search=types.GoogleSearch()),
                types.Tool(function_declarations=get_all_tool_declarations())
            ]

            live_config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                temperature=temperature,
                tools=live_tools,
                input_audio_transcription=types.AudioTranscriptionConfig(),
                output_audio_transcription=types.AudioTranscriptionConfig(),
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice_name
                        )
                    )
                ),
                system_instruction=types.Content(
                    parts=[types.Part.from_text(text=system_instruction_text)]
                )
            )

            async with client.aio.live.connect(model=model_id, config=live_config) as session:
                self.session = session
                self.notify("status", {"state": "connected", "message": f"Connected to Gemini Live. {agent_name} is listening."})
                self.notify("chat_event", {
                    "type": "system",
                    "content": f"Connected to Gemini Live ({model_id}, Voice: {voice_name}). {agent_name} is listening..."
                })

                send_task = asyncio.create_task(self._send_loop(session))
                recv_task = asyncio.create_task(self._receive_loop(session, kill_phrase, agent_name))
                tasks = {send_task, recv_task}

                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

                for task in done:
                    try:
                        task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        if self.is_running and "1000" not in str(e):
                            print(f"[TASK FINISHED WITH ERROR] {e}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            err_str = str(e)
            if self.is_running and "1000" not in err_str and "normal" not in err_str.lower():
                traceback.print_exc()
                err_msg = f"Connection failed: {e}"
                self.notify("status", {"state": "error", "message": err_msg})
                self.notify("chat_event", {
                    "type": "error",
                    "content": f"Failed to connect to Gemini Live: {e}\n\nPlease check your Gemini API key and network connection."
                })
        finally:
            if self.audio:
                self.audio.stop()
                self.audio = None
            self.session = None
            self.is_running = False
            self.notify("status", {"state": "disconnected", "message": "Assistant stopped."})

    def start(self, loop: asyncio.AbstractEventLoop):
        """Starts the assistant engine on the provided event loop."""
        if self.is_running:
            return
        self.is_running = True
        self._main_task = asyncio.run_coroutine_threadsafe(self._run(), loop)

    def stop(self):
        """Stops the assistant engine and closes connections."""
        self.is_running = False
        if self.audio:
            self.audio.stop()
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
        self.notify("status", {"state": "disconnected", "message": "Assistant stopped."})
