import asyncio
import base64
import json
import os
import sys
import time
import traceback
from typing import Any, Callable, Optional

from google import genai
from google.genai import types, errors
import httpx

from core.screen_stream import ScreenCapturePipeline, ensure_thread_desktop
ensure_thread_desktop()

from core.audio_stream import AudioPipeline, resolve_valid_audio_devices
from core.logger import get_logger
from core.voice_verifier import VoiceProfileVerifier
from core.optimizer import ScriptOptimizer
from core.telemetry_db import TelemetryDB
from core.user_memory import UserMemory
from core.proactive_engine import ProactiveEngine
from core.session_lifecycle import SessionLifecycleManager
from security.crypto import unprotect_secret
from tools.dispatcher import ToolDispatcher, get_all_tool_declarations

logger = get_logger("Engine")

# Transient socket/transport errors eligible for automatic retry
TRANSIENT_NETWORK_ERRORS = (
    httpx.TransportError,
    httpx.NetworkError,
    ConnectionError,
    OSError,
    errors.ServerError,
)

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
        config_getter: Optional[Callable[[], dict]] = None,
        on_event: Optional[Callable[[str, dict], None]] = None,
        on_whitelist_update: Optional[Callable[[list], dict]] = None,
        config_path: str = "config.json"
    ):
        self.config_getter = config_getter or (lambda: {})
        self.on_event = on_event or (lambda ev, data: None)
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
        self._kill_playback_flag = False
        self._playback_cancel_event = asyncio.Event()

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

        # Target Speaker Verification (CAM++ Offline Biometrics)
        self.voice_verifier = VoiceProfileVerifier()

        # Telemetry & Asynchronous Self-Optimization (Reflexion Engine)
        self.telemetry_db = TelemetryDB()
        if hasattr(self.dispatcher, "script_runner") and self.dispatcher.script_runner:
            self.dispatcher.script_runner.telemetry_db = self.telemetry_db
        self.last_user_turn_time = time.perf_counter()
        self.genai_client: Optional[genai.Client] = None
        self.optimizer = ScriptOptimizer(
            telemetry_db=self.telemetry_db,
            client_getter=lambda: self.genai_client,
            model_id=self.config_getter().get("api", {}).get("model_id", "gemini-3.8-flash"),
            is_idle_callback=self._is_engine_idle,
            on_optimized=self._on_script_optimized,
            skill_library=self.dispatcher.skill_library
        )
        self._optimizer_task: Optional[asyncio.Task] = None

        # Dynamic User Memory & Proactive Briefing Engine
        self.user_memory = UserMemory()
        self.dispatcher.user_memory = self.user_memory
        self.proactive_engine = ProactiveEngine(self.user_memory)

        # Rolling Session Lifecycle & Compactor (Layer B Context Optimization)
        self.session_lifecycle = SessionLifecycleManager()
        self._active_cm = None
        self._active_recv_task: Optional[asyncio.Task] = None
        self._base_system_instruction: str = ""

        try:
            import main
            main.active_engine = self
            main.update_session_state(
                session=None,
                turns=0,
                start_time=self.session_lifecycle.session_start_time
            )
        except Exception:
            pass

    def _is_audio_idle_for_rotation(self) -> bool:
        """
        Determines if the audio pipeline is safe for silent session reconnection:
        not audio.is_speaking and not user currently speaking and not tool executing.
        """
        if not self.is_running:
            return False
        if self.audio and self.audio.is_speaking:
            return False
        if self.audio and getattr(self.audio, "_is_in_speech", False):
            return False
        if self.is_tool_executing:
            return False
        return True

    def _is_engine_idle(self) -> bool:
        """
        Trigger Conditions: Run only when the audio pipeline is idle
        (not audio.is_speaking and no active user turns for > 15 seconds, and no active tool execution).
        """
        if not self.is_running:
            return False
        if self.audio and self.audio.is_speaking:
            return False
        if self.is_tool_executing:
            return False
        if not self._text_queue.empty():
            return False
        idle_duration = time.perf_counter() - self.last_user_turn_time
        return idle_duration >= 15.0

    def _on_script_optimized(self, event_data: dict):
        """Notifies UI of background script self-optimization."""
        desc = event_data.get("intent_description", "Script")
        dur = event_data.get("duration_ms", 0.0)
        logger.info(f"[REFLEXION] Background optimization completed: '{desc}' ({dur:.0f}ms)")
        self.notify("chat_event", {
            "type": "tool",
            "name": "Reflexion Self-Optimization",
            "content": f"✨ [OPTIMIZATION] Self-refined script for '{desc}' ({dur:.0f}ms)."
        })

    def _is_kill_command(self, text: str, kill_phrase: str = "", agent_name: str = "") -> bool:
        """
        Determines whether the provided text is an intentional stop/kill command.
        Matches:
        - Configured safe/kill phrase (e.g. "Aether stop")
        - Natural stop commands: "stop", "halt", "cancel", "shut up", "be quiet", "quiet", "silence", "stop talking", "stop speaking", "stop it"
        - Prefix / suffix variants with agent name (e.g. "Aether stop", "Stop Aether")
        - Natural polite/urgency decorators (e.g. "please stop", "stop please", "stop now", "aether please stop")
        """
        if not text:
            return False
        clean = "".join(c for c in text.lower() if c.isalnum() or c.isspace()).strip()
        if not clean:
            return False

        # Configured kill phrase
        if kill_phrase:
            clean_kp = "".join(c for c in kill_phrase.lower() if c.isalnum() or c.isspace()).strip()
            if clean_kp and (clean == clean_kp or clean_kp in clean):
                return True

        # Common core stop commands
        core_stops = {
            "stop", "halt", "cancel", "shut up", "be quiet", "quiet", "silence",
            "stop talking", "stop speaking", "stop it"
        }

        # Check raw clean match
        if clean in core_stops:
            return True

        # Strip polite / urgency decorators ("please", "now", "kindly", "hey", "just")
        filler = {"please", "now", "kindly", "hey", "just", "can", "you"}
        words = clean.split()
        stripped_words = [w for w in words if w not in filler]
        stripped = " ".join(stripped_words)
        if stripped in core_stops:
            return True

        # Check with agent name removed or prefixed/suffixed
        if agent_name:
            name_clean = "".join(c for c in agent_name.lower() if c.isalnum()).strip()
            if name_clean:
                without_name = " ".join([w for w in stripped_words if w != name_clean])
                if without_name in core_stops:
                    return True

        return False

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
        """Immediately halts audio playback, cancels in-flight synthesis, and purges all output buffers."""
        self._kill_playback_flag = True
        if hasattr(self, "_playback_cancel_event") and self._playback_cancel_event:
            self._playback_cancel_event.set()
        if self.audio:
            self.audio.kill_output()
        self.notify("status", {"state": "listening", "message": "Playback killed."})
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
        self.last_user_turn_time = time.perf_counter()
        await self._text_queue.put(text.strip())
        self.notify("chat_event", {
            "type": "user",
            "content": text.strip(),
            "source": "text"
        })

    async def _send_loop(self, session=None):
        """Streams microphone PCM audio, real-time desktop vision frames, and typed messages to the Live session."""
        last_vision_time = 0.0
        try:
            while self.is_running:
                active_session = self.session or session
                if active_session is None:
                    await asyncio.sleep(0.01)
                    continue

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
                    await active_session.send_client_content(turns=[content], turn_complete=True)

                # 2. Check for microphone audio
                try:
                    pcm_data = await asyncio.wait_for(self.audio.input_queue.get(), timeout=0.02)
                    await active_session.send_realtime_input(
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
                            await active_session.send_realtime_input(
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

    async def _receive_loop(
        self,
        session,
        kill_phrase: str,
        agent_name: str,
        client: Optional[genai.Client] = None,
        model_id: Optional[str] = None,
        voice_name: Optional[str] = None,
        temperature: Optional[float] = None
    ):
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
                                try:
                                    res_chars = len(json.dumps(result, default=str))
                                    self.session_lifecycle.record_tool_chars(res_chars)
                                except Exception:
                                    pass

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
                        if self._is_kill_command(clean_user_speech, kill_phrase, agent_name):
                            logger.info(f"[KILL PHRASE DETECTED: '{clean_user_speech}'] -> Halting audio!")
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
                        clean_user_speech = self.current_user_speech.strip()
                        if clean_user_speech:
                            self.notify("chat_event", {
                                "type": "user",
                                "content": clean_user_speech,
                                "source": "voice"
                            })
                            self.session_lifecycle.record_turn("user", clean_user_speech)
                            self.current_user_speech = ""

                        clean_turn_text = self.current_turn_text.strip()
                        if clean_turn_text:
                            self.notify("chat_event", {
                                "type": "assistant",
                                "agent_name": agent_name,
                                "content": clean_turn_text
                            })
                            self.session_lifecycle.record_turn("assistant", clean_turn_text)
                        self.current_turn_text = ""

                        # Sync telemetry with main.py
                        try:
                            import main
                            main.update_session_state(turns=self.session_lifecycle.turn_count)
                        except Exception:
                            pass

                        # Wait for hardware buffer to drain
                        wait_count = 0
                        while self.audio and not self.audio.is_output_empty() and wait_count < 40 and self.is_running:
                            await asyncio.sleep(0.05)
                            wait_count += 1

                        if self.audio:
                            self.audio.clear_output_buffer()
                        if self.is_running:
                            self.last_user_turn_time = time.perf_counter()
                            self.notify("status", {"state": "listening", "message": f"{agent_name} is listening..."})

                        # Layer B: Check if session needs rolling compaction & silent reconnect
                        if (
                            self.session_lifecycle.needs_rotation()
                            and not self.session_lifecycle.rotation_in_progress
                            and client is not None
                            and model_id is not None
                        ):
                            logger.info("[LIFECYCLE] Rotation threshold reached after turn completion. Launching silent reconnect...")
                            asyncio.create_task(
                                self.rotate_live_session(
                                    client=client,
                                    model_id=model_id,
                                    kill_phrase=kill_phrase,
                                    agent_name=agent_name,
                                    voice_name=voice_name or "Aoede",
                                    temperature=temperature if temperature is not None else 0.7
                                )
                            )

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
        raw_instruction = api_cfg.get("system_instruction") or (
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
            "  * `capture_screen_snapshot(monitor)`: High-detail screen capture.\n"
            "  * `read_saved_skill(skill_name)`: Inspects and returns full source code of any saved skill from library.\n\n"
            "PERMANENT SKILL LIBRARY (REUSABLE AUTOMATIONS):\n"
            "You possess a persistent, version-controlled library of tested automation scripts. Before writing new code from scratch, ALWAYS check if a matching skill is available and invoke `run_saved_script(skill_name, args)`. If you need to see how a saved skill was implemented, invoke `read_saved_skill(skill_name)`:\n"
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
            "- ANTI-INTROSPECTION & CODE INTEGRITY DIRECTIVE (CRITICAL):\n"
            "  * NEVER execute scripts via `execute_automation_script` to read source files from disk, scan directories (`os.walk`), or inspect internal Python classes/methods (`inspect.getsource`).\n"
            "  * If you need to see how an existing skill handles a task (e.g. images, tables, formatting), simply call `read_saved_skill(skill_name)` to view its code instantly in a single step.\n"
            "  * In dynamic scripts, NEVER call bare `win32gui.SetForegroundWindow(hwnd)` directly (which Windows blocks with a lock timeout error); instead, always import and use `from tools.os_controls import bring_hwnd_to_foreground`.\n"
            "- WEB EDITORS & GOOGLE DOCS CANVAS STABILITY:\n"
            "  * When opening or interacting with web-based editors like Google Docs (`https://docs.new`), Sheets, or Office 365, web applications take 3–5 seconds to initialize WebAssembly/Canvas components and autofocus the editing surface.\n"
            "  * If a paste (`Ctrl+V`) or text entry does not immediately appear, DO NOT enter a diagnostic loop. Simply click into the white document canvas using `find_and_click_element(target_description='document canvas')` or wait 1.5 seconds and re-dispatch `press_key('ctrl+v')`.\n\n"
            "SECURITY WHITELIST & EASY-BUTTON PERMISSION PROTOCOL (CRITICAL):\n"
            "- Desktop applications and processes are governed by the user's security whitelist.\n"
            "- When you call `launch_application` or `close_application` and it returns `status: 'blocked'`:\n"
            "  1. STOP IMMEDIATELY. Do NOT call any further tools, and NEVER attempt workaround hacks (such as pressing Win+E, Alt+F4, hotkey shortcuts, or running arbitrary scripts) to bypass the whitelist block.\n"
            "  2. Adding the program to the whitelist with user permission is the 'EASY BUTTON'. Immediately inform the user verbally in one clear, concise sentence that the program is not in their allowed whitelist, and ask if they would like you to add it:\n"
            "     Example: 'File Explorer is not currently on your allowed whitelist. Would you like me to add it so I can open it for you?'\n"
            "  3. When the user confirms (e.g. 'Yes', 'Yes please', 'Add it', 'Sure', 'Go ahead', 'Allow it'):\n"
            "     * Immediately call `add_to_whitelist(app_name=...)`.\n"
            "     * In the same turn, call `launch_application(app_name=..., target=...)` to seamlessly fulfill their original request without making them ask again!\n"
            "     * Give a brief, pleasant verbal confirmation: 'I've added File Explorer to your whitelist and opened your documents.'\n\n"
            "DETERMINISTIC DIRECT NAVIGATION PREFERENCE (SPEED & ACCURACY):\n"
            "- Always prefer direct URL navigation or direct OS commands over multi-turn visual clicking:\n"
            "  * To create a new Google Doc, Sheet, or Slide, directly navigate to `https://docs.new`, `https://sheets.new`, or `https://slides.new` via `navigate_browser` or `launch_application` (instant 50ms) instead of hunting for template buttons.\n"
            "  * To open standard folders, pass the target directly (e.g. `shell:Personal` for Documents, `shell:My Pictures\\Screenshots` for Screenshots) to `launch_application(app_name='explorer', target=...)`.\n"
            "  * Avoid calling `capture_screen_snapshot` after routine atomic actions unless visual verification is strictly necessary or requested by the user.\n\n"
            "- Whitelist: If an app is blocked by the security whitelist and the user asks to add or allow it, call `add_to_whitelist(app_name)`.\n"
            "- After executing actions, provide a brief, polite verbal confirmation (1-2 sentences). You can chain multiple actions smoothly.\n\n"
            "ATOMIC NAVIGATION & COMMAND ISOLATION (CRITICAL):\n"
            "- Treat standalone application navigation commands (e.g. 'Open Gmail', 'Open Chrome', 'Open Word', 'Open YouTube', 'Go to Google Docs') as fresh, generic actions. Always navigate to the default clean home page or root inbox (e.g. https://mail.google.com). NEVER inject keywords, queries, search terms, or filters from previous tasks into a generic navigation command.\n"
            "- Only carry over search terms or context if the user explicitly uses referential words such as 'those', 'them', 'that search', 'it', 'again', or 'continue'.\n"
            "- When the user switches topics or applications, completely purge prior task context.\n\n"
            "MULTI-STEP WORKFLOW AUTONOMY:\n"
            "- When given a multi-step instruction (e.g. 'Find X in Gmail, calculate the total, and export it into a table in Google Docs'), do NOT stop prematurely after the first step to ask if you should proceed. Autonomously continue executing subsequent steps through to the final deliverable unless you encounter an unresolvable error or need user credentials.\n\n"
        )
        user_name = self.user_memory.get_user_name(default="")
        user_name_directive = f"USER IDENTITY DIRECTIVE:\nThe user's name is {user_name}. Always address the user by their name ({user_name}) when speaking to them.\n\n" if user_name else ""

        # Time-sensitive startup alerts flagged by ProactiveEngine (lean, no full database dump)
        try:
            startup_alerts = self.proactive_engine.evaluate_candidates()
            alerts_directive = ""
            if startup_alerts:
                alert_lines = [f"- {a['description']}" for a in startup_alerts[:3]]
                alerts_directive = "TIME-SENSITIVE STARTUP ALERTS:\n" + "\n".join(alert_lines) + "\n\n"
        except Exception:
            alerts_directive = ""

        # Layer C: Dynamic On-Demand Knowledge Retrieval Directive
        user_memory_directive = (
            "USER PROFILE & PERSONAL KNOWLEDGE RETRIEVAL (MEMORY HYGIENE):\n"
            "- You have a persistent local database of user facts, family details, dates, preferences, and projects. "
            "If the user refers to personal context, family members, or past setups not currently present in your active conversation, "
            "call `query_user_memory` with a concise keyword to inspect their profile before answering.\n"
            "- PROACTIVE MEMORY LEARNING DIRECTIVE:\n"
            "  * When the user shares personal facts, details about family, spouse, children, birthdays, anniversaries, hobbies, vehicles, or requests 'remember this' / 'remember that' (e.g. 'My wife\\'s birthday is 5-1-1977. Her name is Traci, please remember it'):\n"
            "    1. Immediately invoke `remember_user_fact` for each distinct fact revealed in their message.\n"
            "    2. For calendar dates (birthdays, anniversaries), always convert to standard ISO 'YYYY-MM-DD' format (e.g. '1977-05-01') under category 'dates' with data_type='date'.\n"
            "    3. For family and relationship names, store under category 'family' (e.g. key='wife_name', value='Traci').\n"
            "    4. After executing the tool(s), confirm warmly and concisely in 1 sentence that you have saved their facts.\n"
            "  * If the user asks you to forget or delete information, invoke `forget_user_fact`.\n\n"
        )

        system_instruction_text = strict_identity + user_name_directive + alerts_directive + desktop_tools_directive + user_memory_directive + templated_instruction
        self._base_system_instruction = system_instruction_text

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
        self.genai_client = client
        self.dispatcher.genai_client = client

        # Launch background Reflexion self-optimization worker
        if self._optimizer_task is None or self._optimizer_task.done():
            self._optimizer_task = asyncio.create_task(self.optimizer.run_loop())

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

        # Proactive Startup Briefing Evaluation
        try:
            current_user_name = self.user_memory.get_user_name(default="User")
            briefing_res = await self.proactive_engine.check_proactive_briefing(
                user_name=current_user_name,
                client=client,
                model_id=cortex_model,
                agent_name=agent_name
            )
            briefing_text = briefing_res.get("greeting_text") if isinstance(briefing_res, dict) else (briefing_res or "")
            if briefing_text and self.is_running:
                logger.info(f"[PROACTIVE] Triggering startup briefing: '{briefing_text}'")
                self.notify("chat_event", {
                    "type": "assistant",
                    "agent_name": agent_name,
                    "content": briefing_text
                })
                stop_briefing_event = asyncio.Event()

                def _on_briefing_chunk(chunk: bytes):
                    if not self._kill_playback_flag and not stop_briefing_event.is_set():
                        if chunk and self.audio and self.is_running:
                            self.audio.write_output_chunk(chunk)

                self.notify("status", {
                    "state": "speaking",
                    "message": f"{agent_name} is delivering briefing...",
                    "user_prompt": ""
                })
                await self._stream_synthesize_speech(
                    text=briefing_text,
                    tts_engine=tts_model,
                    voice_name=voice_name,
                    client=client,
                    on_pcm_chunk=_on_briefing_chunk,
                    stop_event=stop_briefing_event
                )
                while self.audio and not self.audio.is_output_empty() and self.is_running:
                    if self._kill_playback_flag:
                        break
                    await asyncio.sleep(0.04)

                self.notify("status", {"state": "listening", "message": f"{agent_name} is listening..."})
        except Exception as proactive_err:
            logger.warning(f"[PROACTIVE BRIEFING ERROR] {proactive_err}")

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
                    if self.audio:
                        self.audio.reset_vad()
                elif audio_task in done:
                    wav_bytes = audio_task.result()
                    if not wav_bytes or len(wav_bytes) < 1000:
                        continue

                    # Target Speaker Verification Gate (CAM++ Offline Biometrics)
                    live_audio_cfg = self.config_getter().get("audio", {})
                    live_bio_cfg = live_audio_cfg.get("voice_biometrics", {})
                    if live_bio_cfg.get("enabled", False) and self.voice_verifier.is_enrolled():
                        thresh = float(live_bio_cfg.get("threshold", 0.40))
                        is_user, score = self.voice_verifier.verify(wav_bytes, threshold=thresh)
                        if not is_user:
                            logger.info(f"[VOICE GATE] Utterance discarded: non-user voice (score: {score:.3f} < {thresh:.2f})")
                            self.notify("status", {
                                "state": "voice_gated",
                                "message": f"Ignored background voice (score: {score:.2f})"
                            })
                            continue
                        else:
                            logger.info(f"[VOICE GATE] Authorized user verified (score: {score:.3f} >= {thresh:.2f})")

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
                        stt_resp = await self._generate_content_resilient(
                            client=client,
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

                # Check kill phrase: immediately halt and do not send to Cortex
                if self._is_kill_command(user_prompt, kill_phrase, agent_name):
                    logger.info(f"[KILL PHRASE] Standalone kill phrase received: '{user_prompt}'. Halting audio.")
                    self.kill_audio()
                    if self.audio:
                        self.audio.clear_output_buffer()
                        self.audio.reset_vad()
                    self.notify("status", {"state": "listening", "message": f"{agent_name} is listening..."})
                    continue

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
                    response = await self._send_chat_message_resilient(chat, user_prompt)
                    llm_ms = (time.perf_counter() - t_llm_0) * 1000
                    logger.info(f"[LATENCY] Cortex LLM ({cortex_model}): {llm_ms:.1f}ms")
                finally:
                    self.is_tool_executing = False

                # Handle Search Grounding queries if present
                if response.candidates:
                    grounding = getattr(response.candidates[0], "grounding_metadata", None)
                    if grounding:
                        queries = getattr(grounding, "web_search_queries", None)
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
                blocked_by_whitelist = False
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
                            try:
                                res_chars = len(json.dumps(result, default=str))
                                self.session_lifecycle.record_tool_chars(res_chars)
                            except Exception:
                                pass
                            if isinstance(result, dict) and result.get("status") == "blocked":
                                blocked_by_whitelist = True

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
                        response = await self._send_chat_message_resilient(chat, tool_parts)

                        # Latency optimization: prune older image parts from chat._curated_history so subsequent turns don't re-upload stale multi-megabyte screenshots
                        if hasattr(chat, "_curated_history") and chat._curated_history:
                            image_parts_found = []
                            for turn in chat._curated_history:
                                if getattr(turn, "parts", None):
                                    for idx, p in enumerate(turn.parts):
                                        if getattr(p, "inline_data", None) and getattr(p.inline_data, "mime_type", "").startswith("image/"):
                                            image_parts_found.append((turn, idx))
                            if len(image_parts_found) > 1:
                                for turn_obj, p_idx in image_parts_found[:-1]:
                                    turn_obj.parts[p_idx] = types.Part.from_text(text="[Prior desktop screen snapshot omitted to optimize latency]")
                    finally:
                        self.is_tool_executing = False

                    if blocked_by_whitelist:
                        logger.info("[WHITELIST GATE] Application blocked by whitelist. Halting tool loop to deliver verbal permission request.")
                        break

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
                        summary_resp = await self._send_chat_message_resilient(chat, "Please provide your concise verbal response and summary to the user now.")
                        assistant_text = getattr(summary_resp, "text", "") or ""
                        if not assistant_text and summary_resp.candidates and summary_resp.candidates[0].content:
                            for part in summary_resp.candidates[0].content.parts or []:
                                if getattr(part, "text", None):
                                    assistant_text += part.text
                    except Exception as summary_err:
                        logger.error(f"[SUMMARY ERROR] {summary_err}")

                assistant_text = assistant_text.strip()
                if assistant_text:
                    # Check if user said kill phrase or typed text while Cortex was thinking/tool-executing
                    halt_turn = False
                    if not self._text_queue.empty():
                        queued_input = self._text_queue.get_nowait().strip()
                        if self._is_kill_command(queued_input, kill_phrase, agent_name):
                            logger.info(f"[KILL PHRASE] Received kill phrase while thinking: '{queued_input}' -> Aborting speech.")
                            self.kill_audio()
                            halt_turn = True
                        else:
                            # Re-insert into queue for next turn
                            await self._text_queue.put(queued_input)

                    if not halt_turn and self.audio and not self.audio.utterance_queue.empty():
                        queued_wav = self.audio.utterance_queue.get_nowait()
                        if queued_wav and len(queued_wav) >= 1000:
                            live_audio_cfg = self.config_getter().get("audio", {})
                            live_bio_cfg = live_audio_cfg.get("voice_biometrics", {})
                            is_auth = True
                            if live_bio_cfg.get("enabled", False) and self.voice_verifier.is_enrolled():
                                thresh = float(live_bio_cfg.get("threshold", 0.40))
                                is_auth, _ = self.voice_verifier.verify(queued_wav, threshold=thresh)
                            if is_auth:
                                try:
                                    q_resp = await self._generate_content_resilient(
                                        client=client,
                                        model=stt_model,
                                        contents=[
                                            types.Part.from_bytes(data=queued_wav, mime_type="audio/wav"),
                                            stt_prompt
                                        ],
                                        config=types.GenerateContentConfig(temperature=0.0)
                                    )
                                    q_text = ""
                                    if q_resp.candidates and q_resp.candidates[0].content and q_resp.candidates[0].content.parts:
                                        for p in q_resp.candidates[0].content.parts:
                                            if getattr(p, "audio_transcription", None) and getattr(p.audio_transcription, "text", None):
                                                q_text += p.audio_transcription.text
                                            elif getattr(p, "text", None):
                                                q_text += p.text
                                    q_text = q_text.strip()
                                    if q_text:
                                        if self._is_kill_command(q_text, kill_phrase, agent_name):
                                            logger.info(f"[KILL PHRASE] Interrupted during thinking by voice: '{q_text}'")
                                            self.kill_audio()
                                            halt_turn = True
                                        else:
                                            logger.info(f"[BARGE-IN] Queued voice prompt received during thinking: '{q_text}'")
                                            self.notify("chat_event", {"type": "user", "content": q_text, "source": "voice"})
                                            await self._text_queue.put(q_text)
                                            halt_turn = True
                                except Exception as q_err:
                                    logger.warning(f"[QUEUED TRANSCRIPTION ERROR] {q_err}")

                    if halt_turn:
                        if self.audio:
                            self.audio.clear_output_buffer()
                            self.audio.reset_vad()
                        self.notify("status", {"state": "listening", "message": f"{agent_name} is listening..."})
                        continue

                    self.notify("chat_event", {
                        "type": "assistant",
                        "agent_name": agent_name,
                        "content": assistant_text
                    })

                    # 5. Synthesize & Stream Speech with Concurrent Interruption Monitoring
                    self._kill_playback_flag = False
                    stop_playback_event = asyncio.Event()

                    self.notify("status", {
                        "state": "speaking",
                        "message": f"{agent_name} is speaking...",
                        "user_prompt": user_prompt
                    })
                    t_tts_0 = time.perf_counter()
                    first_audio_ms = None
                    barge_in_prompt = None

                    def _on_pcm_chunk(chunk: bytes):
                        nonlocal first_audio_ms
                        if self._kill_playback_flag or stop_playback_event.is_set():
                            return
                        if chunk and self.audio and self.is_running:
                            if first_audio_ms is None:
                                first_audio_ms = (time.perf_counter() - t_tts_0) * 1000
                            self.audio.write_output_chunk(chunk)

                    async def _run_playback():
                        try:
                            await self._stream_synthesize_speech(
                                text=assistant_text,
                                tts_engine=tts_model,
                                voice_name=voice_name,
                                client=client,
                                on_pcm_chunk=_on_pcm_chunk,
                                stop_event=stop_playback_event
                            )
                            # Drain output buffer while listening for interruptions
                            while self.audio and not self.audio.is_output_empty() and self.is_running:
                                if self._kill_playback_flag or stop_playback_event.is_set():
                                    break
                                await asyncio.sleep(0.04)
                        except asyncio.CancelledError:
                            pass
                        except Exception as p_err:
                            logger.error(f"[TTS SYNTHESIS ERROR] {p_err}")

                    playback_task = asyncio.create_task(_run_playback())

                    # Concurrent Interruption Supervisor
                    while not playback_task.done() and self.is_running and not stop_playback_event.is_set():
                        # A. Check text queue (instant typed barge-in)
                        if not self._text_queue.empty():
                            typed_text = self._text_queue.get_nowait().strip()
                            stop_playback_event.set()
                            self.kill_audio()
                            playback_task.cancel()
                            if self._is_kill_command(typed_text, kill_phrase, agent_name):
                                logger.info(f"[KILL PHRASE] Typed kill command during playback: '{typed_text}'")
                            else:
                                barge_in_prompt = typed_text
                                logger.info(f"[TEXT BARGE-IN] Interrupted with new prompt: '{typed_text}'")
                            break

                        # B. Check voice utterance queue (voice kill phrase or voice barge-in)
                        if self.audio and not self.audio.utterance_queue.empty():
                            raw_wav = self.audio.utterance_queue.get_nowait()
                            if raw_wav and len(raw_wav) >= 1000:
                                live_audio_cfg = self.config_getter().get("audio", {})
                                live_bio_cfg = live_audio_cfg.get("voice_biometrics", {})
                                is_authorized = True
                                if live_bio_cfg.get("enabled", False) and self.voice_verifier.is_enrolled():
                                    thresh = float(live_bio_cfg.get("threshold", 0.40))
                                    is_user, score = self.voice_verifier.verify(raw_wav, threshold=thresh)
                                    if not is_user:
                                        logger.info(f"[VOICE GATE] Interruption discarded: non-user voice ({score:.3f} < {thresh:.2f})")
                                        is_authorized = False

                                if is_authorized:
                                    try:
                                        t_int_0 = time.perf_counter()
                                        stt_resp = await self._generate_content_resilient(
                                            client=client,
                                            model=stt_model,
                                            contents=[
                                                types.Part.from_bytes(data=raw_wav, mime_type="audio/wav"),
                                                stt_prompt
                                            ],
                                            config=types.GenerateContentConfig(temperature=0.0)
                                        )
                                        int_text = ""
                                        if stt_resp.candidates and stt_resp.candidates[0].content and stt_resp.candidates[0].content.parts:
                                            for p in stt_resp.candidates[0].content.parts:
                                                if getattr(p, "audio_transcription", None) and getattr(p.audio_transcription, "text", None):
                                                    int_text += p.audio_transcription.text
                                                elif getattr(p, "text", None):
                                                    int_text += p.text
                                        int_text = int_text.strip()
                                        int_ms = (time.perf_counter() - t_int_0) * 1000
                                        logger.info(f"[INTERRUPTION STT] ({int_ms:.0f}ms): '{int_text}'")

                                        if int_text:
                                            if self._is_kill_command(int_text, kill_phrase, agent_name):
                                                logger.info(f"[KILL PHRASE] Voice kill phrase detected during playback: '{int_text}' -> Halting audio!")
                                                stop_playback_event.set()
                                                self.kill_audio()
                                                playback_task.cancel()
                                                break
                                            else:
                                                logger.info(f"[VOICE BARGE-IN] Spoken command detected during playback: '{int_text}'")
                                                self.notify("chat_event", {
                                                    "type": "user",
                                                    "content": int_text,
                                                    "source": "voice"
                                                })
                                                stop_playback_event.set()
                                                self.kill_audio()
                                                playback_task.cancel()
                                                barge_in_prompt = int_text
                                                break
                                    except Exception as int_err:
                                        logger.warning(f"[INTERRUPTION STT ERROR] {int_err}")

                        # C. Check if UI kill button set flag
                        if self._kill_playback_flag:
                            stop_playback_event.set()
                            playback_task.cancel()
                            break

                        await asyncio.sleep(0.04)

                    try:
                        await playback_task
                    except asyncio.CancelledError:
                        pass
                    except Exception as tts_err:
                        logger.error(f"[TTS SYNTHESIS ERROR] {tts_err}")

                    tts_ms = (time.perf_counter() - t_tts_0) * 1000
                    ttfb_str = f" | First sound: {first_audio_ms:.0f}ms" if first_audio_ms is not None else ""
                    logger.info(f"[LATENCY] TTS ({tts_model}): {tts_ms:.1f}ms{ttfb_str}")

                    # If barge-in captured a new prompt, queue it so the next turn starts immediately
                    if barge_in_prompt:
                        await self._text_queue.put(barge_in_prompt)

                # Calculate total end-to-end turnaround latency
                total_turn_ms = (time.perf_counter() - t_turn_start) * 1000
                logger.info(
                    f"[LATENCY SUMMARY] Turn #{turn_counter} Completed in {total_turn_ms:.0f}ms | "
                    f"STT: {stt_ms:.0f}ms | LLM: {llm_ms:.0f}ms | Tools: {tools_ms:.0f}ms | TTS: {tts_ms:.0f}ms"
                )

                # Record turn in lifecycle telemetry
                self.session_lifecycle.record_turn("user", user_prompt)
                if assistant_text:
                    self.session_lifecycle.record_turn("assistant", assistant_text)

                try:
                    import main
                    main.update_session_state(turns=self.session_lifecycle.turn_count)
                except Exception:
                    pass

                # Layer B: Check if modular session needs context compaction
                if self.session_lifecycle.needs_rotation():
                    logger.info("[LIFECYCLE] Modular rotation threshold reached. Compacting context via background compactor...")
                    summary = await self.session_lifecycle.generate_session_summary(client)
                    updated_instruction = self.session_lifecycle.build_compacted_instruction(
                        self._base_system_instruction,
                        summary=summary
                    )
                    chat = client.chats.create(
                        model=cortex_model,
                        config=types.GenerateContentConfig(
                            system_instruction=updated_instruction,
                            tools=[
                                types.Tool(google_search=types.GoogleSearch()),
                                types.Tool(function_declarations=get_all_tool_declarations())
                            ],
                            tool_config=types.ToolConfig(include_server_side_tool_invocations=True),
                            temperature=temperature
                        )
                    )
                    self.session_lifecycle.reset_metrics()
                    try:
                        import main
                        main.update_session_state(turns=0, start_time=time.time())
                    except Exception:
                        pass
                    self.notify("chat_event", {
                        "type": "system",
                        "content": "🔄 Context compacted into 4-6 operational state bullets."
                    })

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
                    self.audio.reset_vad()
                if self.is_running:
                    self.last_user_turn_time = time.perf_counter()
                    self.notify("status", {"state": "listening", "message": f"{agent_name} is listening..."})

            except asyncio.CancelledError:
                break
            except Exception as turn_err:
                if self.is_running:
                    logger.error(f"[MODULAR TURN ERROR] {turn_err}", exc_info=True)
                    self.notify("chat_event", {"type": "error", "content": f"Pipeline error: {turn_err}"})
                    self.notify("status", {"state": "error", "message": f"Error: {turn_err}"})
                    await asyncio.sleep(1.0)

    async def _send_chat_message_resilient(
        self,
        chat,
        message: Any,
        max_retries: int = 3,
        initial_delay: float = 0.4,
        backoff_factor: float = 1.5
    ):
        """
        Sends a message to the Cortex Gemini chat session with automatic retry on transient
        network/socket drops (e.g. WinError 10054, httpx.ReadError, idle keep-alive disconnects).
        """
        delay = initial_delay
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                return await asyncio.to_thread(chat.send_message, message)
            except TRANSIENT_NETWORK_ERRORS as exc:
                last_err = exc
                err_msg = str(exc)
                if attempt < max_retries:
                    logger.warning(
                        f"[CORTEX RESILIENCE] Transient network/socket error in chat.send_message "
                        f"(attempt {attempt}/{max_retries}): {type(exc).__name__}: {err_msg}. "
                        f"Retrying in {delay:.2f}s with fresh socket..."
                    )
                    await asyncio.sleep(delay)
                    delay *= backoff_factor
                else:
                    logger.error(
                        f"[CORTEX RESILIENCE] chat.send_message failed after {max_retries} attempts: "
                        f"{type(exc).__name__}: {err_msg}"
                    )
                    raise last_err
            except Exception as exc:
                exc_str = str(exc).lower()
                if "10054" in exc_str or "forcibly closed" in exc_str or "connection reset" in exc_str:
                    last_err = exc
                    if attempt < max_retries:
                        logger.warning(
                            f"[CORTEX RESILIENCE] Detected socket reset in chat.send_message "
                            f"(attempt {attempt}/{max_retries}): {exc}. "
                            f"Retrying in {delay:.2f}s with fresh socket..."
                        )
                        await asyncio.sleep(delay)
                        delay *= backoff_factor
                        continue
                raise exc

    async def _generate_content_resilient(
        self,
        client,
        model: str,
        contents: Any,
        config: Optional[Any] = None,
        max_retries: int = 2,
        initial_delay: float = 0.3
    ):
        """
        Calls client.models.generate_content with transient socket drop retry.
        """
        delay = initial_delay
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                return await asyncio.to_thread(
                    client.models.generate_content,
                    model=model,
                    contents=contents,
                    config=config
                )
            except TRANSIENT_NETWORK_ERRORS as exc:
                last_err = exc
                if attempt < max_retries:
                    logger.warning(
                        f"[STT RESILIENCE] Transient socket drop in generate_content "
                        f"(attempt {attempt}/{max_retries}): {type(exc).__name__}: {exc}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    await asyncio.sleep(delay)
                    delay *= 2.0
                else:
                    raise last_err
            except Exception as exc:
                exc_str = str(exc).lower()
                if "10054" in exc_str or "forcibly closed" in exc_str or "connection reset" in exc_str:
                    last_err = exc
                    if attempt < max_retries:
                        logger.warning(
                            f"[STT RESILIENCE] Socket reset in generate_content "
                            f"(attempt {attempt}/{max_retries}): {exc}. "
                            f"Retrying in {delay:.2f}s..."
                        )
                        await asyncio.sleep(delay)
                        delay *= 2.0
                        continue
                raise exc


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
        on_pcm_chunk: Callable[[bytes], None],
        stop_event: Optional[asyncio.Event] = None
    ):
        """
        Streams synthesized speech directly to the audio playback buffer in real time.
        Supported engines:
        - "gemini-live-native" / "gemini" / "multimodal": Real-Time WebSocket streaming (~500ms TTFB)
        - "edge-tts" / "edge": Ultra-fast Microsoft Neural TTS (~300ms TTFB)
        - "windows-local" / "sapi": Offline local Windows SAPI5 voice (<50ms)
        - "gemini-2.5-flash-preview-tts" / "gemini-3.1-flash-tts-preview": REST fallback
        """
        if not self.is_running or self._kill_playback_flag or (stop_event and stop_event.is_set()):
            return

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
                        if not self.is_running or self._kill_playback_flag or (stop_event and stop_event.is_set()):
                            break
                        if not self._text_queue.empty():
                            self.kill_audio()
                            break
                        sc = getattr(response, "server_content", None)
                        if sc and sc.model_turn:
                            for part in sc.model_turn.parts:
                                inline_data = getattr(part, "inline_data", None)
                                if inline_data and inline_data.data:
                                    if not self._kill_playback_flag and not (stop_event and stop_event.is_set()):
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
                        if not self.is_running or self._kill_playback_flag or (stop_event and stop_event.is_set()):
                            break
                        if not self._text_queue.empty():
                            self.kill_audio()
                            break
                        if chunk["type"] == "audio":
                            decoded = miniaudio.decode(chunk["data"], nchannels=1, sample_rate=24000)
                            if decoded and decoded.samples:
                                if not self._kill_playback_flag and not (stop_event and stop_event.is_set()):
                                    on_pcm_chunk(decoded.samples.tobytes())
                    return
                except Exception as edge_err:
                    logger.warning(f"[EDGE TTS ERROR] {edge_err}, falling back...")

        # 3. Windows Local SAPI5 (Instantaneous < 50ms)
        if "windows" in tts_lower or "sapi" in tts_lower or "local" in tts_lower:
            if not self.is_running or self._kill_playback_flag or (stop_event and stop_event.is_set()):
                return
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
            if pcm and not self._kill_playback_flag and not (stop_event and stop_event.is_set()):
                on_pcm_chunk(pcm)
                return

        # 4. Fallback / REST Non-streaming Gemini TTS
        if not self.is_running or self._kill_playback_flag or (stop_event and stop_event.is_set()):
            return
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
                if not self._kill_playback_flag and not (stop_event and stop_event.is_set()):
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

    def _build_live_config(
        self,
        system_instruction_text: str,
        voice_name: str,
        temperature: float
    ) -> types.LiveConnectConfig:
        """Constructs types.LiveConnectConfig with Google Search, tool declarations, and voice."""
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            temperature=temperature,
            tools=[
                types.Tool(google_search=types.GoogleSearch()),
                types.Tool(function_declarations=get_all_tool_declarations())
            ],
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

    async def rotate_live_session(
        self,
        client: genai.Client,
        model_id: str,
        kill_phrase: str,
        agent_name: str,
        voice_name: str = "Aoede",
        temperature: float = 0.7
    ) -> bool:
        """
        Executes a seamless silent reconnection of the Gemini Live WebSocket session:
        1. Waits until audio pipeline is idle.
        2. Non-blocking call to gemini-2.5-flash to summarize conversation state into 4-6 bullet points.
        3. Pre-seeds new connection's system_instruction with the state summary.
        4. Opens new WebSocket connection (client.aio.live.connect).
        5. Atomically swaps self.session reference used by _send_loop.
        6. Spawns new _receive_loop on the new session and cancels old receive task.
        7. Gracefully closes the old session.
        8. Audio hardware streams remain active and unaffected.
        """
        if self.session_lifecycle.rotation_in_progress or not self.is_running:
            return False

        self.session_lifecycle.rotation_in_progress = True
        logger.info("[ROTATION] Commencing silent session rotation & context compaction...")

        try:
            # 1. Wait until audio pipeline is idle (max 5 seconds)
            for _ in range(50):
                if self._is_audio_idle_for_rotation():
                    break
                await asyncio.sleep(0.1)

            # 2. Asynchronous background summarization via gemini-2.5-flash
            summary = await self.session_lifecycle.generate_session_summary(client)

            # 3. Pre-seed new connection system instruction with the 4-6 bullet state summary
            new_instruction = self.session_lifecycle.build_compacted_instruction(
                self._base_system_instruction,
                summary=summary
            )
            new_config = self._build_live_config(new_instruction, voice_name, temperature)

            # 4. Open new WebSocket connection
            logger.info(f"[ROTATION] Connecting new live session ({model_id})...")
            new_cm = client.aio.live.connect(model=model_id, config=new_config)
            new_session = await new_cm.__aenter__()

            # 5. Atomically swap active session references
            old_session = self.session
            old_cm = self._active_cm
            old_recv_task = self._active_recv_task

            self.session = new_session
            self._active_cm = new_cm

            # Sync with main.py
            try:
                import main
                main.update_session_state(
                    session=new_session,
                    turns=0,
                    start_time=time.time()
                )
            except Exception:
                pass

            # 6. Start new receive loop
            self._active_recv_task = asyncio.create_task(
                self._receive_loop(new_session, kill_phrase, agent_name, client, model_id, voice_name, temperature)
            )

            # 7. Cancel previous receive loop and close old session
            if old_recv_task and not old_recv_task.done():
                old_recv_task.cancel()

            if old_session:
                try:
                    await old_session.close()
                except Exception:
                    pass
            if old_cm:
                try:
                    await old_cm.__aexit__(None, None, None)
                except Exception:
                    pass

            # 8. Reset lifecycle metrics
            self.session_lifecycle.reset_metrics()

            self.notify("chat_event", {
                "type": "system",
                "content": "🔄 Context compacted seamlessly. Active session re-anchored with zero audio disruption."
            })
            logger.info("[ROTATION] Silent live session rotation completed successfully!")
            return True
        except Exception as rot_err:
            logger.error(f"[ROTATION ERROR] Failed silent reconnect: {rot_err}", exc_info=True)
            self.session_lifecycle.rotation_in_progress = False
            return False

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
        """Executes Gemini Multimodal Live WebSocket session with Silent Reconnect Lifecycle."""
        model_id = api_cfg.get("live_model_id", "gemini-3.1-flash-live-preview")
        self._base_system_instruction = system_instruction_text
        reconnect_delay = 1.0

        while self.is_running:
            self.notify("status", {"state": "connecting", "message": f"Connecting to Gemini Live ({model_id}, Voice: {voice_name})..."})

            try:
                client = genai.Client(api_key=api_key)
                self.genai_client = client
                self.dispatcher.genai_client = client

                live_config = self._build_live_config(system_instruction_text, voice_name, temperature)
                self._active_cm = client.aio.live.connect(model=model_id, config=live_config)
                session = await self._active_cm.__aenter__()
                self.session = session

                try:
                    import main
                    main.update_session_state(
                        session=session,
                        turns=0,
                        start_time=time.time()
                    )
                except Exception:
                    pass

                reconnect_delay = 1.0
                self.notify("status", {"state": "connected", "message": f"Connected to Gemini Live. {agent_name} is listening."})
                self.notify("chat_event", {
                    "type": "system",
                    "content": f"Connected to Gemini Live ({model_id}, Voice: {voice_name}). {agent_name} is listening..."
                })

                send_task = asyncio.create_task(self._send_loop())
                self._active_recv_task = asyncio.create_task(
                    self._receive_loop(session, kill_phrase, agent_name, client, model_id, voice_name, temperature)
                )

                while self.is_running:
                    tasks = {send_task, self._active_recv_task}
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    if send_task in done:
                        break
                    if self._active_recv_task in done:
                        try:
                            self._active_recv_task.result()
                        except asyncio.CancelledError:
                            # Old receive loop was cancelled during rotation; wait for new active_recv_task
                            continue
                        except Exception as e:
                            if self.is_running and "1000" not in str(e):
                                print(f"[TASK FINISHED WITH ERROR] {e}")
                            break

                # Teardown current connection
                if send_task and not send_task.done():
                    send_task.cancel()
                if self._active_recv_task and not self._active_recv_task.done():
                    self._active_recv_task.cancel()
                if self.session:
                    try:
                        await self.session.close()
                    except Exception:
                        pass
                if self._active_cm:
                    try:
                        await self._active_cm.__aexit__(None, None, None)
                    except Exception:
                        pass

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
        """Stops the assistant engine, background workers, and closes connections."""
        self.is_running = False
        if self.audio:
            self.audio.stop()
        if self._optimizer_task and not self._optimizer_task.done():
            self._optimizer_task.cancel()
        if self.optimizer:
            self.optimizer.stop()
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
        if self.telemetry_db:
            self.telemetry_db.close()
        self.notify("status", {"state": "disconnected", "message": "Assistant stopped."})
