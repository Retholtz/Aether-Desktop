import asyncio
import base64
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

from core.audio_stream import AudioPipeline, resolve_valid_audio_devices
from core.logger import get_logger
from security.crypto import unprotect_secret
from tools.dispatcher import ToolDispatcher, get_all_tool_declarations

logger = get_logger("Engine")

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

try:
    import edge_tts
    import miniaudio
except ImportError:
    edge_tts = None
    miniaudio = None

GEMINI_TO_EDGE_VOICE = {
    # Female-sounding voices
    "Aoede": "en-US-JennyNeural",
    "Autonoe": "en-US-AriaNeural",
    "Callirrhoe": "en-US-JennyNeural",
    "Despina": "en-US-AriaNeural",
    "Erinome": "en-US-JennyNeural",
    "Kore": "en-US-AriaNeural",
    "Laomedeia": "en-GB-SoniaNeural",
    "Leda": "en-US-JennyNeural",
    "Pulcherrima": "en-US-AriaNeural",
    "Sulafat": "en-US-JennyNeural",
    "Vindemiatrix": "en-US-AriaNeural",
    "Zephyr": "en-US-JennyNeural",
    "Achernar": "en-US-AriaNeural",
    # Male-sounding voices
    "Achird": "en-US-GuyNeural",
    "Algenib": "en-US-ChristopherNeural",
    "Algieba": "en-US-GuyNeural",
    "Alnilam": "en-US-ChristopherNeural",
    "Charon": "en-US-ChristopherNeural",
    "Enceladus": "en-US-GuyNeural",
    "Fenrir": "en-US-GuyNeural",
    "Gacrux": "en-US-ChristopherNeural",
    "Iapetus": "en-US-GuyNeural",
    "Orus": "en-US-ChristopherNeural",
    "Puck": "en-US-GuyNeural",
    "Rasalgethi": "en-US-ChristopherNeural",
    "Sadachbia": "en-US-GuyNeural",
    "Sadaltager": "en-US-ChristopherNeural",
    "Schedar": "en-US-GuyNeural",
    "Umbriel": "en-US-GuyNeural",
    "Zubenelgenubi": "en-US-GuyNeural",
}

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
        self.is_tool_executing = False
        self._reset_context_flag = False

        # Screen capture pipeline
        self.screen_pipeline = ScreenCapturePipeline()

        # Modular Tool Dispatcher (Tier 1 OS, Tier 2 GUI, Tier 3 Scripts, Security)
        self.dispatcher = ToolDispatcher(
            screen_pipeline=self.screen_pipeline,
            on_event=self.notify,
            whitelist_getter=self._get_whitelist,
            whitelist_updater=self._update_whitelist,
            config_getter=self.config_getter
        )

    def reset_chat_context(self):
        """Signals the active pipeline loop to reset chat session state / memory."""
        self._reset_context_flag = True
        logger.info("[SESSION] Reset context requested by user/UI.")
        self.notify("chat_event", {
            "type": "system",
            "content": "✨ AI conversation context reset. Starting fresh task."
        })


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
        if event_type == "status":
            logger.debug(f"[STATUS] {data.get('state')}: {data.get('message')}")
        if self.on_event:
            try:
                self.on_event(event_type, data)
            except Exception as e:
                logger.error(f"[ENGINE EVENT ERROR] {e}")

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

    def _on_speech_state(self, state: str):
        if not self.is_running:
            return
        if state == "speech_detected":
            self.notify("status", {"state": "hearing", "message": "Hearing speech..."})
        elif state == "speech_finalized":
            self.notify("status", {"state": "transcribing", "message": "Transcribing speech..."})

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

                # If a tool call is actively executing, pause streaming to prevent 1011 state conflicts
                if self.is_tool_executing:
                    if self.audio and self.audio.input_queue:
                        while not self.audio.input_queue.empty():
                            try:
                                self.audio.input_queue.get_nowait()
                            except Exception:
                                break
                    await asyncio.sleep(0.01)
                    continue

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
                        self.is_tool_executing = True
                        try:
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
                                    response={"result": result} if not isinstance(result, dict) else result
                                ))

                            if function_responses:
                                await session.send_tool_response(function_responses=function_responses)
                        finally:
                            self.is_tool_executing = False

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
        skills_summary = self.dispatcher.skill_library.get_manifest_summary()

        desktop_tools_directive = (
            "DESKTOP VISION, DETERMINISTIC WIN32 CONTROL & AUTONOMOUS SCRIPT AUTOMATION:\n"
            "You have real-time vision of the user's desktop screen and powerful tools to interact directly with applications:\n"
            "- Real-Time Vision: You continuously receive video frames of the user's desktop (with multi-monitor tracking). You can see open windows, buttons, search bars, and text on screen.\n"
            "- Atomic Reflex Tools (Low-Latency): Use built-in tools for instant actions:\n"
            "  * `launch_application(app_name, target, profile)`: Launches allowed desktop apps and browser profiles directly.\n"
            "  * `focus_window(app_name)`, `maximize_window(app_name)`, `minimize_window(app_name)`, `restore_window(app_name)`: Fast Win32 window management.\n"
            "  * `navigate_browser(query_or_url, app_name='chrome')`: Deterministic browser search and URL navigation via omnibox.\n"
            "  * `find_and_click_element(target_description, app_name)`: Visual grounding element locator and clicker.\n"
            "  * `type_text(text, app_name, press_enter)`: Foreground window typing with formatting.\n"
            "  * `press_key(key_combo)`: Keyboard hotkeys and shortcuts.\n"
            "  * `mouse_click(x, y, app_name)`: 0-1000 normalized desktop coordinate clicks.\n"
            "  * `capture_screen_snapshot(monitor)`: High-detail screen capture.\n\n"
            "PERMANENT SKILL LIBRARY (REUSABLE AUTOMATIONS):\n"
            "You possess a persistent, version-controlled library of tested automation scripts. Before writing new code from scratch, ALWAYS check if a matching skill is available and invoke `run_saved_script(skill_name, args)`:\n"
            f"{skills_summary}\n\n"
            "CRITICAL RULE FOR CREATING TABLES & DOCUMENT COMPARISONS:\n"
            "- When asked to create, compare, or insert a table into an open document (e.g. 'Within this document, create a table comparing X and Y', 'Create a comparison table in Google Docs / Word', 'Compare these items with pros and cons in a table'):\n"
            "  1. Do NOT type queries, headers, or titles into the document or browser with `type_text`.\n"
            "  2. Synthesize or look up the required comparison facts, specs, and pros/cons.\n"
            "  3. Immediately invoke `run_saved_script(skill_name='create_table_google_docs', args={'headers': ['Feature / Spec', 'Vehicle A', 'Vehicle B'], 'rows': [['Pros', '...', '...'], ['Cons', '...', '...']], 'title': 'Comparison Title'})` for Google Docs (or `create_table_word` for Word).\n"
            "  4. The saved skill automatically loads the clipboard with formatted HTML and pastes it cleanly into the active document.\n\n"
            "DYNAMIC EXECUTION & SELF-EXTENDING LEARNING (CORTEX):\n"
            "- Dynamic Python Generation: For novel, multi-step, document, spreadsheet, data processing (pandas), Microsoft Office COM (Word/Excel via win32com.client), or window layout tasks without a pre-existing skill, write and execute Python code dynamically via `execute_automation_script(script_code, description)`.\n"
            "- Self-Correction on Error: If a script fails, you will receive the full Python stack trace / traceback in the tool response. Inspect the error, adjust your script or logic, and retry.\n"
            "- Learning & Memory: Once you have dynamically generated and verified a successful script for a task that might be reused (e.g. formatting a report, manipulating files, custom layouts), save it permanently to the Skill Library via `save_script_to_library(skill_name, description, script_code, parameters)`. This persists it to disk and syncs it via Git so you never have to generate it again!\n"
            "- Whitelist: If an app is blocked by the security whitelist and the user asks to add or allow it, call `add_to_whitelist(app_name)`.\n"
            "- After executing actions, provide a brief, polite verbal confirmation (1-2 sentences). You can chain multiple actions smoothly.\n\n"
            "ATOMIC NAVIGATION & COMMAND ISOLATION (CRITICAL):\n"
            "- Treat standalone application navigation commands (e.g. 'Open Gmail', 'Open Chrome', 'Open Word', 'Open YouTube', 'Go to Google Docs') as fresh, generic actions. Always navigate to the default clean home page or root inbox (e.g. https://mail.google.com). NEVER inject keywords, queries, search terms, or filters from previous tasks into a generic navigation command.\n"
            "- Only carry over search terms or context if the user explicitly uses referential words such as 'those', 'them', 'that search', 'it', 'again', or 'continue'.\n"
            "- When the user switches topics or applications, completely purge prior task context.\n\n"
            "MULTI-STEP WORKFLOW AUTONOMY:\n"
            "- When given a multi-step instruction (e.g. 'Find X in Gmail, calculate the total, and export it into a table in Google Docs'), do NOT stop prematurely after the first step to ask if you should proceed. Autonomously continue executing subsequent steps through to the final deliverable unless you encounter an unresolvable error or need user credentials.\n\n"
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

        # Validate and resolve to available devices in case configured device was unplugged or invalid
        in_idx, out_idx = resolve_valid_audio_devices(in_idx, out_idx)

        self.notify("status", {"state": "connecting", "message": f"Opening audio devices ({in_idx}, {out_idx})..."})

        try:
            self.audio = AudioPipeline(
                input_device=in_idx,
                output_device=out_idx,
                mode=audio_mode,
                software_gate=software_gate,
                on_speech_state=self._on_speech_state
            )
            await self.audio.start()
        except Exception as e:
            err_msg = f"Failed to initialize audio devices (Input #{in_idx}, Output #{out_idx}): {e}"
            self.notify("status", {"state": "error", "message": err_msg})
            self.notify("chat_event", {"type": "error", "content": err_msg})
            self.is_running = False
            return

        if kill_phrase:
            system_instruction_text += f"\nImportant: If the user says '{kill_phrase}' or 'stop', halt speaking immediately."

        pipeline_mode = api_cfg.get("pipeline_mode", "modular").lower()

        try:
            if pipeline_mode == "modular":
                await self._run_modular_pipeline(
                    api_key=api_key,
                    agent_name=agent_name,
                    kill_phrase=kill_phrase,
                    api_cfg=api_cfg,
                    system_instruction_text=system_instruction_text,
                    voice_name=voice_name,
                    temperature=temperature
                )
            else:
                await self._run_live_pipeline(
                    api_key=api_key,
                    agent_name=agent_name,
                    kill_phrase=kill_phrase,
                    api_cfg=api_cfg,
                    system_instruction_text=system_instruction_text,
                    voice_name=voice_name,
                    temperature=temperature
                )
        finally:
            if self.audio:
                self.audio.stop()
                self.audio = None
            self.session = None
            self.is_running = False
            self.notify("status", {"state": "disconnected", "message": "Assistant stopped."})

    async def _run_modular_pipeline(
        self,
        api_key: str,
        agent_name: str,
        kill_phrase: str,
        api_cfg: dict,
        system_instruction_text: str,
        voice_name: str,
        temperature: float
    ):
        """
        Executes Option #2: High-Reasoning Modular 3-Stage Pipeline.
        - Stage 1 (STT): gemini-3.5-transcribe
        - Stage 2 (Cortex): gemini-3.8-flash (Reasoning, Google Search, & Function Calling)
        - Stage 3 (TTS): gemini-3.1-flash-tts-preview
        Immunizes the assistant against WebSocket 1011 errors during complex tool runs.
        """
        stt_model = api_cfg.get("stt_model_id", "gemini-3.5-transcribe")
        cortex_model = api_cfg.get("model_id", "gemini-3.8-flash")
        tts_model = api_cfg.get("tts_model_id", "gemini-live-native")
        audio_cfg = self.config_getter().get("audio", {})
        preferred_language = audio_cfg.get("preferred_language", "en-US")

        client = genai.Client(api_key=api_key)
        self.dispatcher.genai_client = client

        self.notify("status", {
            "state": "connected",
            "message": f"Modular Pipeline active ({cortex_model}). {agent_name} is listening."
        })
        self.notify("chat_event", {
            "type": "system",
            "content": f"Ready in Modular Pipeline mode.\n- Cortex: {cortex_model}\n- STT: {stt_model} (Language: {preferred_language})\n- TTS: {tts_model} (Voice: {voice_name})\n{agent_name} is listening..."
        })

        # Multi-turn Cortex chat session factory with Google Search & Function Calling
        def create_fresh_chat():
            logger.info(f"[SESSION] Initializing fresh Cortex chat session ({cortex_model})")
            return client.chats.create(
                model=cortex_model,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction_text,
                    tools=[
                        types.Tool(google_search=types.GoogleSearch()),
                        types.Tool(function_declarations=get_all_tool_declarations())
                    ],
                    tool_config=types.ToolConfig(
                        include_server_side_tool_invocations=True
                    ),
                    temperature=temperature
                )
            )

        chat = create_fresh_chat()

        turn_counter = 0
        while self.is_running:
            try:
                t_turn_start = time.perf_counter()
                stt_ms = 0.0
                llm_ms = 0.0
                tools_ms = 0.0
                tts_ms = 0.0

                # Check if UI / external trigger requested a context reset
                if self._reset_context_flag:
                    self._reset_context_flag = False
                    chat = create_fresh_chat()
                    logger.info("[SESSION] Conversational context reset to clean slate via flag.")

                # 1. Wait concurrently for either typed input or speech utterance from VAD
                text_task = asyncio.create_task(self._text_queue.get())
                audio_task = asyncio.create_task(self.audio.utterance_queue.get())

                done, pending = await asyncio.wait(
                    [text_task, audio_task],
                    return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()

                user_prompt = ""
                source = "voice"

                if text_task in done:
                    user_prompt = text_task.result().strip()
                    source = "text"
                    t_turn_start = time.perf_counter()
                elif audio_task in done:
                    wav_bytes = audio_task.result()
                    if not wav_bytes or len(wav_bytes) < 1000:
                        continue

                    t_turn_start = time.perf_counter()
                    self.notify("status", {"state": "transcribing", "message": f"Transcribing speech ({preferred_language})..."})
                    t_stt_0 = time.perf_counter()
                    try:
                        stt_prompt = (
                            f"You are a verbatim speech-to-text transcriber for {preferred_language}. "
                            f"Transcribe only clear, audible speech spoken in {preferred_language}. "
                            f"Output only the verbatim spoken words without commentary. "
                            f"If the audio contains background chatter, noise, sighs, breathing, non-speech vocalizations, "
                            f"or speech in another language, output NOTHING (empty string). Do not guess or translate."
                        )
                        stt_resp = await asyncio.to_thread(
                            client.models.generate_content,
                            model=stt_model,
                            contents=[
                                types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav"),
                                stt_prompt
                            ],
                            config=types.GenerateContentConfig(
                                temperature=0.0
                            )
                        )
                        stt_ms = (time.perf_counter() - t_stt_0) * 1000
                        logger.info(f"[LATENCY] STT ({stt_model}): {stt_ms:.1f}ms")

                        if stt_resp.candidates and stt_resp.candidates[0].content and stt_resp.candidates[0].content.parts:
                            for part in stt_resp.candidates[0].content.parts:
                                if getattr(part, "audio_transcription", None) and getattr(part.audio_transcription, "text", None):
                                    user_prompt += part.audio_transcription.text
                                elif getattr(part, "text", None):
                                    user_prompt += part.text
                        user_prompt = user_prompt.strip()

                        # Language / Script validation filter
                        if user_prompt:
                            # If preferred language is English, reject non-Latin scripts (Devanagari, Gurmukhi/Punjabi, Arabic, Asian scripts)
                            if preferred_language.lower().startswith("en"):
                                import re
                                if re.search(r'[\u0600-\u06FF\u0900-\u097F\u0A00-\u0A7F\u4E00-\u9FFF\u3040-\u30FF]', user_prompt):
                                    logger.warning(f"[STT FILTER] Discarded non-English transcription hallucination: '{user_prompt}'")
                                    user_prompt = ""
                                    continue
                            # Reject pure filler/noise vocalizations like "hmm", "...", "uhm"
                            filler_words = {"hmm", "hmmm", "uh", "um", "ah", "uhm", "huh", "mhm"}
                            cleaned_lower = "".join(c for c in user_prompt.lower() if c.isalnum() or c.isspace()).strip()
                            if cleaned_lower in filler_words or len(cleaned_lower) <= 1:
                                logger.info(f"[STT FILTER] Discarded vocal filler or ambient sound: '{user_prompt}'")
                                user_prompt = ""
                                continue
                    except Exception as stt_err:
                        stt_ms = (time.perf_counter() - t_stt_0) * 1000
                        logger.error(f"[STT TRANSCRIPTION ERROR] ({stt_ms:.1f}ms) {stt_err}")
                        self.notify("chat_event", {"type": "error", "content": f"STT error: {stt_err}"})
                        continue

                if not user_prompt:
                    continue

                # Voice-driven Context Reset Triggers
                reset_phrases = [
                    "new task", "clear context", "reset context", "start over",
                    "forget that", "shifting gears", "new session", "clean slate"
                ]
                lower_prompt = user_prompt.lower().strip()

                # Standalone reset command
                if any(lower_prompt == p or lower_prompt == f"{agent_name.lower()} {p}" for p in reset_phrases):
                    chat = create_fresh_chat()
                    logger.info(f"[SESSION] Context reset triggered by voice command: '{user_prompt}'")
                    self.notify("chat_event", {
                        "type": "system",
                        "content": "✨ Conversation context cleared. Ready for your next command."
                    })
                    if tts_model:
                        await self._stream_synthesize_speech(
                            client=client,
                            voice_name=voice_name,
                            text="Context cleared. What would you like to do next?"
                        )
                    continue

                # Leading reset phrase (e.g. "New task, open gmail" or "Shifting gears, open my email")
                for p in reset_phrases:
                    matched_prefix = None
                    for prefix in [f"{p},", f"{p} -", f"{p}:", f"{p} "]:
                        if lower_prompt.startswith(prefix):
                            matched_prefix = prefix
                            break
                    if matched_prefix:
                        chat = create_fresh_chat()
                        logger.info(f"[SESSION] Context reset with leading phrase '{p}'. Fresh chat session initialized.")
                        user_prompt = user_prompt[len(matched_prefix):].strip()
                        self.notify("chat_event", {
                            "type": "system",
                            "content": "✨ Prior context cleared for new task."
                        })
                        break

                turn_counter += 1
                logger.info(f"[TURN START #{turn_counter}] Prompt ({source}): {user_prompt}")

                # Check kill phrase
                if kill_phrase and kill_phrase.lower() in user_prompt.lower():
                    self.kill_audio()

                # Emit user speech to chat if from voice (text is already emitted in send_text)
                if source == "voice":
                    self.notify("chat_event", {
                        "type": "user",
                        "content": user_prompt,
                        "source": source
                    })

                self.notify("status", {"state": "thinking", "message": f"{agent_name} is thinking...", "user_prompt": user_prompt})

                # 2. Send prompt to Cortex (gemini-3.8-flash)
                self.is_tool_executing = True
                t_llm_0 = time.perf_counter()
                try:
                    response = await asyncio.to_thread(chat.send_message, user_prompt)
                    llm_ms = (time.perf_counter() - t_llm_0) * 1000
                    logger.info(f"[LATENCY] Cortex LLM ({cortex_model}): {llm_ms:.1f}ms")
                finally:
                    self.is_tool_executing = False

                # Handle Search Grounding queries if present
                if response.candidates and getattr(response.candidates[0], "grounding_metadata", None):
                    queries = getattr(response.candidates[0], "grounding_metadata", "web_search_queries", None)
                    if queries:
                        for q in queries:
                            logger.info(f"[SEARCH GROUNDING] Query: {q}")
                            self.notify("chat_event", {
                                "type": "tool",
                                "name": "Google Search",
                                "content": f"Searching: {q}"
                            })
                            self.notify("status", {
                                "state": "executing",
                                "message": f"Searching Google: {q}...",
                                "action": "Google Search",
                                "user_prompt": user_prompt
                            })

                # 3. Handle Tool Calls / Dynamic Scripts Execution Loop
                loop_count = 0
                max_tool_turns = 25  # Extended from 10 to allow complex multi-stage workflows (e.g. Gmail search + Google Docs export) to finish
                t_tools_0 = time.perf_counter()
                while self.is_running and getattr(response, "function_calls", None) and loop_count < max_tool_turns:
                    loop_count += 1
                    self.is_tool_executing = True
                    tool_parts = []
                    try:
                        for fc in response.function_calls:
                            fn_name = fc.name
                            fn_args = fc.args or {}

                            self.notify("chat_event", {
                                "type": "tool",
                                "name": fn_name,
                                "content": f"Executing: {fn_name}({json.dumps(fn_args, default=str)})"
                            })
                            self.notify("status", {
                                "state": "executing",
                                "message": f"Executing: {fn_name}...",
                                "action": fn_name,
                                "user_prompt": user_prompt
                            })

                            result = await self.dispatcher.dispatch(fn_name, fn_args)

                            # Handle screen snapshot JPEG if returned
                            raw_jpeg = None
                            if fn_name == "capture_screen_snapshot" and isinstance(result, dict) and "_jpeg_bytes" in result:
                                raw_jpeg = result.pop("_jpeg_bytes")

                            func_resp = types.Part.from_function_response(
                                name=fn_name,
                                response={"result": result} if not isinstance(result, dict) else result
                            )
                            tool_parts.append(func_resp)
                            if raw_jpeg:
                                tool_parts.append(types.Part.from_bytes(data=raw_jpeg, mime_type="image/jpeg"))

                        self.notify("status", {
                            "state": "thinking",
                            "message": f"{agent_name} is reasoning with tool output...",
                            "user_prompt": user_prompt
                        })
                        response = await asyncio.to_thread(chat.send_message, tool_parts)
                    finally:
                        self.is_tool_executing = False

                if loop_count > 0:
                    tools_ms = (time.perf_counter() - t_tools_0) * 1000
                    logger.info(f"[LATENCY] Tool Calls ({loop_count} turns): {tools_ms:.1f}ms")

                # 4. Extract Assistant Response Text
                assistant_text = getattr(response, "text", "") or ""
                if not assistant_text and response.candidates and response.candidates[0].content:
                    for part in response.candidates[0].content.parts or []:
                        if getattr(part, "text", None):
                            assistant_text += part.text

                # If loop reached max turns and model is still proposing tools without text, request verbal answer
                if not assistant_text.strip() and getattr(response, "function_calls", None):
                    try:
                        summary_resp = await asyncio.to_thread(chat.send_message, "Please provide your concise verbal response and summary to the user now.")
                        assistant_text = getattr(summary_resp, "text", "") or ""
                        if not assistant_text and summary_resp.candidates and summary_resp.candidates[0].content:
                            for part in summary_resp.candidates[0].content.parts or []:
                                if getattr(part, "text", None):
                                    assistant_text += part.text
                    except Exception as summary_err:
                        logger.error(f"[SUMMARY ERROR] {summary_err}")

                assistant_text = assistant_text.strip()
                if assistant_text:
                    self.notify("chat_event", {
                        "type": "assistant",
                        "agent_name": agent_name,
                        "content": assistant_text
                    })

                    # 5. Synthesize & Stream Speech (Gemini Live WebSocket / Edge TTS / SAPI)
                    self.notify("status", {
                        "state": "speaking",
                        "message": f"{agent_name} is speaking...",
                        "user_prompt": user_prompt
                    })
                    t_tts_0 = time.perf_counter()
                    first_audio_ms = None
                    try:
                        def _on_pcm_chunk(chunk: bytes):
                            nonlocal first_audio_ms
                            if chunk and self.audio and self.is_running:
                                if first_audio_ms is None:
                                    first_audio_ms = (time.perf_counter() - t_tts_0) * 1000
                                self.audio.write_output_chunk(chunk)

                        await self._stream_synthesize_speech(
                            text=assistant_text,
                            tts_engine=tts_model,
                            voice_name=voice_name,
                            client=client,
                            on_pcm_chunk=_on_pcm_chunk
                        )
                        tts_ms = (time.perf_counter() - t_tts_0) * 1000
                        ttfb_str = f" | First sound: {first_audio_ms:.0f}ms" if first_audio_ms is not None else ""
                        logger.info(f"[LATENCY] TTS ({tts_model}): {tts_ms:.1f}ms{ttfb_str}")

                        # Drain output buffer while listening for interruptions
                        while self.audio and not self.audio.is_output_empty() and self.is_running:
                            if not self._text_queue.empty():
                                self.kill_audio()
                                break
                            await asyncio.sleep(0.05)
                    except Exception as tts_err:
                        tts_ms = (time.perf_counter() - t_tts_0) * 1000
                        logger.error(f"[TTS SYNTHESIS ERROR] ({tts_ms:.1f}ms) {tts_err}")

                # Calculate total end-to-end turnaround latency
                total_turn_ms = (time.perf_counter() - t_turn_start) * 1000
                logger.info(
                    f"[LATENCY SUMMARY] Turn #{turn_counter} Completed in {total_turn_ms:.0f}ms | "
                    f"STT: {stt_ms:.0f}ms | LLM: {llm_ms:.0f}ms | Tools: {tools_ms:.0f}ms | TTS: {tts_ms:.0f}ms"
                )

                # Push real-time latency telemetry to the UI dashboard
                self.notify("telemetry_update", {
                    "stt_ms": round(stt_ms),
                    "llm_ms": round(llm_ms),
                    "tools_ms": round(tools_ms),
                    "tts_ms": round(tts_ms),
                    "total_ms": round(total_turn_ms)
                })

                if self.audio:
                    self.audio.clear_output_buffer()
                if self.is_running:
                    self.notify("status", {"state": "listening", "message": f"{agent_name} is listening..."})

            except asyncio.CancelledError:
                break
            except Exception as turn_err:
                if self.is_running:
                    logger.error(f"[MODULAR TURN ERROR] {turn_err}", exc_info=True)
                    self.notify("chat_event", {"type": "error", "content": f"Pipeline error: {turn_err}"})
                    self.notify("status", {"state": "error", "message": f"Error: {turn_err}"})
                    await asyncio.sleep(1.0)

                    self.notify("status", {"state": "error", "message": f"Error: {turn_err}"})
                    await asyncio.sleep(1.0)

    def _resolve_edge_voice(self, voice_name: str) -> str:
        """Maps Gemini or generic voice names to Microsoft Edge Neural voices."""
        if not voice_name:
            return "en-US-JennyNeural"
        if "-" in voice_name and "Neural" in voice_name:
            return voice_name
        return GEMINI_TO_EDGE_VOICE.get(voice_name, "en-US-JennyNeural")

    async def _stream_synthesize_speech(
        self,
        text: str,
        tts_engine: str,
        voice_name: str,
        client: genai.Client,
        on_pcm_chunk: Callable[[bytes], None]
    ):
        """
        Streams synthesized speech directly to the audio playback buffer in real time.
        Supported engines:
        - "gemini-live-native" / "gemini" / "multimodal": Real-Time WebSocket streaming (~500ms TTFB)
        - "edge-tts" / "edge": Ultra-fast Microsoft Neural TTS (~300ms TTFB)
        - "windows-local" / "sapi": Offline local Windows SAPI5 voice (<50ms)
        - "gemini-2.5-flash-preview-tts" / "gemini-3.1-flash-tts-preview": REST fallback
        """
        tts_lower = (tts_engine or "").lower()

        # 1. Gemini Multimodal Live WebSocket Streaming (Fast ~500ms TTFB, 30 Native Gemini Voices)
        if "live" in tts_lower or tts_lower == "gemini-live-native" or ("gemini" in tts_lower and "preview" not in tts_lower and "2.5" not in tts_lower):
            try:
                live_model = "gemini-3.1-flash-live-preview"
                config = types.LiveConnectConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice_name or "Aoede"
                            )
                        )
                    ),
                    system_instruction=types.Content(
                        parts=[types.Part.from_text(
                            text="You are a vocal speech synthesis engine. Read the user input aloud directly with natural, pleasant expression. Do not add any commentary, greetings, or conversational filler. Only vocalize the exact text provided."
                        )]
                    )
                )

                async with client.aio.live.connect(model=live_model, config=config) as session:
                    await session.send_realtime_input(text=text)
                    async for response in session.receive():
                        if not self.is_running:
                            break
                        if not self._text_queue.empty():
                            self.kill_audio()
                            break
                        sc = getattr(response, "server_content", None)
                        if sc and sc.model_turn:
                            for part in sc.model_turn.parts:
                                inline_data = getattr(part, "inline_data", None)
                                if inline_data and inline_data.data:
                                    on_pcm_chunk(inline_data.data)
                        if sc and sc.turn_complete:
                            break
                return
            except Exception as live_tts_err:
                logger.warning(f"[GEMINI LIVE TTS STREAM ERROR] {live_tts_err}, falling back...")

        # 2. Edge Neural TTS (Ultra-Fast ~300ms TTFB)
        if "edge" in tts_lower:
            if edge_tts is not None and miniaudio is not None:
                try:
                    edge_voice = self._resolve_edge_voice(voice_name)
                    comm = edge_tts.Communicate(text, edge_voice)
                    async for chunk in comm.stream():
                        if not self.is_running:
                            break
                        if not self._text_queue.empty():
                            self.kill_audio()
                            break
                        if chunk["type"] == "audio":
                            decoded = miniaudio.decode(chunk["data"], nchannels=1, sample_rate=24000)
                            if decoded and decoded.samples:
                                on_pcm_chunk(decoded.samples.tobytes())
                    return
                except Exception as edge_err:
                    logger.warning(f"[EDGE TTS ERROR] {edge_err}, falling back...")

        # 3. Windows Local SAPI5 (Instantaneous < 50ms)
        if "windows" in tts_lower or "sapi" in tts_lower or "local" in tts_lower:
            def _speak_sapi():
                try:
                    import win32com.client
                    speaker = win32com.client.Dispatch("SAPI.SpVoice")
                    if voice_name:
                        for i in range(speaker.GetVoices().Count):
                            v = speaker.GetVoices().Item(i)
                            if voice_name.lower() in v.GetDescription().lower():
                                speaker.Voice = v
                                break
                    stream = win32com.client.Dispatch("SAPI.SpMemoryStream")
                    stream.Format.Type = 30  # SAFT24kHz16BitMono
                    speaker.AudioOutputStream = stream
                    speaker.Speak(text)
                    return bytes(stream.GetData())
                except Exception as sapi_err:
                    logger.error(f"[SAPI ERROR] {sapi_err}")
                    return None

            pcm = await asyncio.to_thread(_speak_sapi)
            if pcm:
                on_pcm_chunk(pcm)
                return

        # 4. Fallback / REST Non-streaming Gemini TTS
        model = tts_engine if "gemini" in tts_lower else "gemini-3.1-flash-tts-preview"
        try:
            tts_resp = await asyncio.to_thread(
                client.interactions.create,
                model=model,
                input=text,
                response_format={"type": "audio"},
                generation_config={"speech_config": [{"voice": voice_name}]}
            )
            if getattr(tts_resp, "output_audio", None) and getattr(tts_resp.output_audio, "data", None):
                pcm = base64.b64decode(tts_resp.output_audio.data)
                on_pcm_chunk(pcm)
        except Exception as gem_err:
            logger.error(f"[GEMINI REST TTS ERROR] {gem_err}")

    async def _synthesize_speech(
        self,
        text: str,
        tts_engine: str,
        voice_name: str,
        client: genai.Client
    ) -> Optional[bytes]:
        """Convenience helper collecting all chunks into a single byte buffer."""
        chunks = []
        await self._stream_synthesize_speech(
            text=text,
            tts_engine=tts_engine,
            voice_name=voice_name,
            client=client,
            on_pcm_chunk=lambda c: chunks.append(c)
        )
        return b"".join(chunks) if chunks else None

    async def _run_live_pipeline(
        self,
        api_key: str,
        agent_name: str,
        kill_phrase: str,
        api_cfg: dict,
        system_instruction_text: str,
        voice_name: str,
        temperature: float
    ):
        """Executes the original Gemini Multimodal Live WebSocket session (Fallback/Live Mode)."""
        model_id = api_cfg.get("live_model_id", "gemini-3.1-flash-live-preview")
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

        reconnect_delay = 1.0

        while self.is_running:
            self.notify("status", {"state": "connecting", "message": f"Connecting to Gemini Live ({model_id}, Voice: {voice_name})..."})

            try:
                client = genai.Client(api_key=api_key)
                self.dispatcher.genai_client = client

                async with client.aio.live.connect(model=model_id, config=live_config) as session:
                    self.session = session
                    reconnect_delay = 1.0
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
                break
            except Exception as e:
                err_str = str(e)
                if not self.is_running or "1000" in err_str or "normal" in err_str.lower():
                    break
                print(f"\n[LIVE SESSION DISCONNECTED: {e}] -> Auto-reconnecting in {reconnect_delay:.1f}s...")
                self.notify("status", {"state": "reconnecting", "message": f"Connection lost ({e}). Reconnecting in {reconnect_delay:.1f}s..."})
                self.notify("chat_event", {
                    "type": "system",
                    "content": f"⚠️ Connection interrupted ({e}). Reconnecting in {reconnect_delay:.1f}s..."
                })
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, 8.0)

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
