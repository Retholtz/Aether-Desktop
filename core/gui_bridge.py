import asyncio

import json

import os

import queue

import re

import sys

import threading

import time

import sounddevice as sd

from typing import Optional, Any, Dict, List

import psutil



from core.game_manager import GameManager

from core.audio_stream import (

    get_available_audio_devices,

    get_windows_audio_fingerprint,

    resolve_valid_audio_devices,

)

from core.engine import AetherEngine, GEMINI_VOICES

from core.logger import (

    get_logger,

    register_ui_log_callback,

    get_recent_logs,

    clear_memory_logs,

    open_logs_folder,

)

from security.crypto import protect_secret, unprotect_secret

from core.config_manager import sync_config_schema, save_config_atomic

from core.model_discovery import (

    load_cached_categorized_models,

    load_cached_models,

    fetch_available_gemini_models,

)



logger = get_logger("Bridge")



KOKORO_VOICES = [

    {"name": "af_heart", "trait": "Warm & Expressive (Quality Grade A)", "gender": "Female (US)"},

    {"name": "af_bella", "trait": "Bright & Crisp", "gender": "Female (US)"},

    {"name": "af_nicole", "trait": "Smooth & Professional", "gender": "Female (US)"},

    {"name": "af_aoede", "trait": "Breezy & Melodic", "gender": "Female (US)"},

    {"name": "af_kore", "trait": "Firm & Clear", "gender": "Female (US)"},

    {"name": "af_sarah", "trait": "Natural & Friendly", "gender": "Female (US)"},

    {"name": "af_nova", "trait": "Modern & Dynamic", "gender": "Female (US)"},

    {"name": "af_sky", "trait": "Light & Youthful", "gender": "Female (US)"},

    {"name": "am_adam", "trait": "Deep & Grounded", "gender": "Male (US)"},

    {"name": "am_michael", "trait": "Clear & Conversational", "gender": "Male (US)"},

    {"name": "am_puck", "trait": "Upbeat & Energetic", "gender": "Male (US)"},

    {"name": "am_echo", "trait": "Balanced & Informative", "gender": "Male (US)"},

    {"name": "am_eric", "trait": "Warm & Friendly", "gender": "Male (US)"},

    {"name": "am_fenrir", "trait": "Intense & Confident", "gender": "Male (US)"},

    {"name": "am_liam", "trait": "Casual & Easy-going", "gender": "Male (US)"},

    {"name": "am_onyx", "trait": "Authoritative & Deep", "gender": "Male (US)"},

    {"name": "bf_emma", "trait": "Polished & Articulate (UK)", "gender": "Female (GB)"},

    {"name": "bf_isabella", "trait": "Refined & Warm (UK)", "gender": "Female (GB)"},

    {"name": "bf_alice", "trait": "Clear & Expressive (UK)", "gender": "Female (GB)"},

    {"name": "bf_lily", "trait": "Gentle & Soft (UK)", "gender": "Female (GB)"},

    {"name": "bm_george", "trait": "Sophisticated & Measured (UK)", "gender": "Male (GB)"},

    {"name": "bm_fable", "trait": "Engaging & Upbeat (UK)", "gender": "Male (GB)"},

    {"name": "bm_lewis", "trait": "Deep & Resonant (UK)", "gender": "Male (GB)"},

    {"name": "bm_daniel", "trait": "Calm & Authoritative (UK)", "gender": "Male (GB)"},

    {"name": "alloy", "trait": "OpenAI Compatible", "gender": "Neutral"},

    {"name": "echo", "trait": "OpenAI Compatible", "gender": "Male"},

    {"name": "fable", "trait": "OpenAI Compatible", "gender": "British"},

    {"name": "onyx", "trait": "OpenAI Compatible", "gender": "Male"},

    {"name": "nova", "trait": "OpenAI Compatible", "gender": "Female"},

    {"name": "shimmer", "trait": "OpenAI Compatible", "gender": "Female"}

]



_CACHED_EDGE_VOICES = []

_CACHED_EDGE_CATALOG = None



def _get_edge_voice_catalog():

    global _CACHED_EDGE_CATALOG

    if _CACHED_EDGE_CATALOG is not None:

        return _CACHED_EDGE_CATALOG

    try:

        import edge_tts

        loop = asyncio.new_event_loop()

        try:

            raw = loop.run_until_complete(edge_tts.list_voices())

        finally:

            loop.close()



        priority_order = ["en-US", "en-GB", "en-AU", "en-CA", "en-IE", "en-NZ", "en-IN", "en-ZA", "en-SG"]

        locales = {}



        for v in raw:

            loc = v.get("Locale", "")

            loc_name = v.get("LocaleName", loc)

            short = v.get("ShortName", "")

            gender = v.get("Gender", "Unknown")



            parts = short.split("-")

            suffix = parts[-1] if len(parts) > 2 else short

            is_multi = "Multilingual" in suffix

            clean_name = suffix.replace("MultilingualNeural", "").replace("Neural", "")



            display_label = f"{clean_name} ({gender})"

            if is_multi:

                display_label = f"{clean_name} (Multilingual, {gender})"



            if loc not in locales:

                parts_loc = loc.split("-")

                region_code = parts_loc[1] if len(parts_loc) > 1 else loc

                country = loc_name

                if "(" in loc_name and ")" in loc_name:

                    country = loc_name[loc_name.find("(") + 1:loc_name.find(")")]

                    lang = loc_name[:loc_name.find("(")].strip()

                    region_label = f"{country} ({region_code}) — {lang}"

                else:

                    region_label = f"{loc_name} ({region_code})"

                locales[loc] = {

                    "id": loc,

                    "label": region_label,

                    "country": country,

                    "voices": []

                }

            locales[loc]["voices"].append({

                "id": short,

                "name": clean_name,

                "label": display_label,

                "gender": gender

            })



        for loc, data in locales.items():

            data["voices"].sort(key=lambda x: x["name"])



        sorted_keys = sorted(locales.keys(), key=lambda k: (

            0 if k in priority_order else 1,

            priority_order.index(k) if k in priority_order else locales[k]["country"].lower()

        ))



        regions_list = [{"id": k, "label": locales[k]["label"]} for k in sorted_keys]

        voices_map = {k: locales[k]["voices"] for k in sorted_keys}



        _CACHED_EDGE_CATALOG = {

            "regions": regions_list,

            "voices": voices_map

        }

        return _CACHED_EDGE_CATALOG

    except Exception as e:

        logger.warning(f"Failed to fetch Edge TTS voice catalog: {e}")

        return {

            "regions": [

                {"id": "en-US", "label": "United States (US) — English"},

                {"id": "en-GB", "label": "United Kingdom (GB) — English"},

                {"id": "en-AU", "label": "Australia (AU) — English"},

                {"id": "en-CA", "label": "Canada (CA) — English"}

            ],

            "voices": {

                "en-US": [

                    {"id": "en-US-GuyNeural", "name": "Guy", "label": "Guy (Male)", "gender": "Male"},

                    {"id": "en-US-JennyNeural", "name": "Jenny", "label": "Jenny (Female)", "gender": "Female"}

                ],

                "en-GB": [

                    {"id": "en-GB-LibbyNeural", "name": "Libby", "label": "Libby (Female)", "gender": "Female"},

                    {"id": "en-GB-RyanNeural", "name": "Ryan", "label": "Ryan (Male)", "gender": "Male"}

                ],

                "en-AU": [

                    {"id": "en-AU-NatashaNeural", "name": "Natasha", "label": "Natasha (Female)", "gender": "Female"},

                    {"id": "en-AU-WilliamMultilingualNeural", "name": "William", "label": "William (Multilingual, Male)", "gender": "Male"}

                ],

                "en-CA": [

                    {"id": "en-CA-ClaraNeural", "name": "Clara", "label": "Clara (Female)", "gender": "Female"},

                    {"id": "en-CA-LiamNeural", "name": "Liam", "label": "Liam (Male)", "gender": "Male"}

                ]

            }

        }



def _get_edge_voices():

    global _CACHED_EDGE_VOICES

    if _CACHED_EDGE_VOICES:

        return _CACHED_EDGE_VOICES

    catalog = _get_edge_voice_catalog()

    flat = []

    for reg_id, vlist in catalog.get("voices", {}).items():

        for v in vlist:

            flat.append({

                "name": v["id"],

                "trait": reg_id,

                "gender": v["gender"]

            })

    _CACHED_EDGE_VOICES = flat

    return _CACHED_EDGE_VOICES



def _get_sapi_voices():

    try:

        import pythoncom

        import win32com.client

        pythoncom.CoInitialize()

        try:

            speaker = win32com.client.Dispatch("SAPI.SpVoice")

            sapi_voices = []

            voices = speaker.GetVoices()

            for i in range(voices.Count):

                v = voices.Item(i)

                desc = v.GetDescription()

                sapi_voices.append({

                    "name": desc,

                    "trait": "Local SAPI5",

                    "gender": "System Voice"

                })

            voices = None

            speaker = None

            return sapi_voices if sapi_voices else [{"name": "Default Windows Voice", "trait": "Local SAPI5", "gender": "System Voice"}]

        finally:

            speaker = None

            try:

                pythoncom.CoUninitialize()

            except Exception:

                pass

    except Exception as e:

        logger.warning(f"Failed to fetch SAPI voices: {e}")

        return [{"name": "Default Windows Voice", "trait": "Local SAPI5", "gender": "System Voice"}]



class GuiBridge:

    """

    Exposes a clean JSON-RPC interface to pywebview's JavaScript runtime,

    handling configuration, hardware enumeration, engine lifecycle, and telemetry.

    All internal non-RPC fields are prefixed with '_' to prevent pywebview reflection loops.

    """

    def __init__(self, config_path: Any = None, loop: Optional[asyncio.AbstractEventLoop] = None, engine: Any = None):
        from core.paths import is_frozen, user_data_path
        if config_path is None:
            config_path = user_data_path("config.json")
        elif not isinstance(config_path, str):
            engine = config_path
            config_path = user_data_path("config.json")
        elif config_path == "config.json" and is_frozen():
            config_path = user_data_path("config.json")



        self._config_path = config_path

        if loop:

            self._loop = loop

        else:

            try:

                self._loop = asyncio.get_running_loop()

            except RuntimeError:

                self._loop = asyncio.new_event_loop()

        self._window = None

        self._overlay_window = None

        self._overlay_visible = False

        self._hud_bridge = None
        self._hud_window = None
        self._update_in_progress = False

        self._current_hud_mode = "normal"

        self._js_lock = threading.Lock()
        self._js_event_queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._js_dispatcher_thread: Optional[threading.Thread] = None
        self._is_shutting_down = False

        self._config_lock = threading.RLock()

        self._stop_audio_watcher = threading.Event()

        self._audio_watcher_thread = None

        self._config = self._load_config()

        self._current_hud_mode = self._config.get("ui", {}).get("hud_mode", "normal")

        if engine is not None:

            self._engine = engine

            self.engine = engine

        else:

            self._engine = AetherEngine(

                config_getter=self.get_raw_config,

                on_event=self._on_engine_event,

                on_whitelist_update=self.update_whitelist

            )

            self._engine._loop = self._loop

            self.engine = self._engine

        if hasattr(self.engine, "game_mgr") and self.engine.game_mgr is not None:

            self.game_mgr = self.engine.game_mgr

        else:

            self.game_mgr = GameManager()

            if self.engine is not None:

                try:

                    self.engine.game_mgr = self.game_mgr

                except Exception:

                    pass



        from core.program_manager import ProgramManager

        if hasattr(self.engine, "program_mgr") and self.engine.program_mgr is not None:

            self.program_mgr = self.engine.program_mgr

        else:

            self.program_mgr = ProgramManager()

            if self.engine is not None:

                try:

                    self.engine.program_mgr = self.program_mgr

                except Exception:

                    pass

        from core.project_service import ProjectService
        from core.notebooklm_service import NotebookLMService

        if hasattr(self.engine, "project_service") and self.engine.project_service is not None and isinstance(self.engine.project_service, ProjectService):
            self.project_service = self.engine.project_service
        else:
            self.project_service = ProjectService()
            if self.engine is not None:
                try:
                    self.engine.project_service = self.project_service
                except Exception:
                    pass

        if hasattr(self.engine, "notebooklm_service") and self.engine.notebooklm_service is not None and isinstance(self.engine.notebooklm_service, NotebookLMService):
            self.notebooklm_service = self.engine.notebooklm_service
        else:
            self.notebooklm_service = NotebookLMService()
            if self.engine is not None:
                try:
                    self.engine.notebooklm_service = self.notebooklm_service
                except Exception:
                    pass

        from core.ttrpg_service import TTRPGService
        if hasattr(self.engine, "ttrpg_service") and self.engine.ttrpg_service is not None and isinstance(self.engine.ttrpg_service, TTRPGService):
            self.ttrpg_service = self.engine.ttrpg_service
        else:
            self.ttrpg_service = TTRPGService(client_getter=lambda: getattr(self.engine, "client", None))
            if self.engine is not None:
                try:
                    self.engine.ttrpg_service = self.ttrpg_service
                except Exception:
                    pass



        orig_cb = self.game_mgr.on_game_mode_changed

        def _bridge_on_game_mode_changed(enabled: bool, game_id: str, game_name: str, is_running: bool = False):

            if callable(orig_cb):

                try:

                    orig_cb(enabled, game_id, game_name, is_running)

                    return

                except TypeError:

                    try:

                        orig_cb(enabled, game_id, game_name)

                        return

                    except Exception:

                        pass

                except Exception:

                    pass

            prof = self.game_mgr.get_profile(game_id) if game_id else None
            fps_mon = bool(prof.get("fps_monitoring", True)) if prof else True
            self._on_engine_event("game_mode_changed", {

                "enabled": enabled,

                "game_mode_enabled": enabled,

                "active_profile": game_id,

                "game_id": game_id,

                "display_name": game_name,

                "game_name": game_name,

                "is_running": is_running,

                "fps_monitoring": fps_mon

            })

        self.game_mgr.on_game_mode_changed = _bridge_on_game_mode_changed



        def _bridge_on_game_added(game_id: str, profile: dict):

            if hasattr(self, "program_mgr") and self.program_mgr:

                try:

                    self.program_mgr.sync_from_games(game_profiles={game_id: profile})

                except Exception as e:

                    logger.debug(f"[GUI_BRIDGE] on_game_added program sync error: {e}")

        self.game_mgr.on_game_added = _bridge_on_game_added

        from core.oauth_manager import get_oauth_manager

        self._oauth_mgr = get_oauth_manager()

        self._oauth_mgr.on_status_change = lambda status: self._on_engine_event("oauth_status_updated", status)

        register_ui_log_callback(self._on_log_record)

        self._last_audio_fp = get_windows_audio_fingerprint()

        self.start_audio_watcher()



    def start_audio_watcher(self, force: bool = False):

        """Starts background audio hotplug watcher thread."""

        # Skip background hardware threads during test discovery/runs

        if not force and (os.environ.get("AETHER_TESTING") == "1" or "unittest" in sys.modules):

            return



        self._stop_audio_watcher.clear()

        self._audio_watcher_thread = threading.Thread(

            target=self._audio_hotplug_watcher_loop,

            daemon=True,

            name="AudioHotplugWatcher"

        )

        self._audio_watcher_thread.start()



    def stop_audio_watcher(self):

        """Signals background thread to terminate before shutdown."""

        self._stop_audio_watcher.set()

        if self._audio_watcher_thread and self._audio_watcher_thread.is_alive():

            self._audio_watcher_thread.join(timeout=2.0)



    # Backwards compatibility aliases

    _start_audio_device_watcher = start_audio_watcher

    _stop_audio_device_watcher = stop_audio_watcher



    def _audio_hotplug_watcher_loop(self):

        while not self._stop_audio_watcher.is_set():

            # Check devices without blocking exit

            if self._stop_audio_watcher.wait(timeout=2.0):

                break



            # Synchronize configuration updates

            with self._config_lock:

                # Safely inspect and mutate device preferences

                self._sync_active_audio_devices()



            # Health Watchdog: Revive audio engine streams if invalidated by OS or stalled

            if os.environ.get("AETHER_TESTING") != "1":

                try:

                    eng = getattr(self, "_engine", None)

                    aud = getattr(eng, "audio", None) if eng else None

                    if aud and getattr(aud, "_running", False):

                        out_s = getattr(aud, "out_stream", None)

                        if out_s is not None and hasattr(aud, "ensure_output_stream_healthy"):

                            aud.ensure_output_stream_healthy()

                except Exception:

                    pass



    def _sync_active_audio_devices(self):

        try:

            fp = get_windows_audio_fingerprint()

            if fp is None or fp == self._last_audio_fp:

                return



            prev_fp = self._last_audio_fp

            self._last_audio_fp = fp



            avail = get_available_audio_devices(force_refresh=True)

            inputs = avail.get("inputs", [])

            outputs = avail.get("outputs", [])



            # Check if the system default input or output changed compared to previous fingerprint

            prev_def_in_id = next((d[0] for d in (prev_fp[0] if prev_fp else ()) if d[2]), None)

            prev_def_out_id = next((d[0] for d in (prev_fp[1] if prev_fp else ()) if d[2]), None)

            curr_def_in = next((d for d in inputs if d.get("is_default")), inputs[0] if inputs else None)

            curr_def_out = next((d for d in outputs if d.get("is_default")), outputs[0] if outputs else None)



            aud_cfg = self._config.setdefault("audio", {})

            cfg_in_idx = aud_cfg.get("input_device_index", 0)

            cfg_out_idx = aud_cfg.get("output_device_index", 0)

            cfg_in_name = aud_cfg.get("input_device_name", "")

            cfg_out_name = aud_cfg.get("output_device_name", "")



            # If Windows default input device changed (e.g. earbud plugged in/unplugged), switch to new default

            if curr_def_in and (prev_def_in_id != curr_def_in.get("device_id") or "[default]" in cfg_in_name.lower()):

                selected_in = curr_def_in

            else:

                resolved_in_idx, _ = resolve_valid_audio_devices(cfg_in_idx, cfg_out_idx, cfg_in_name, cfg_out_name)

                selected_in = next((d for d in inputs if d["index"] == resolved_in_idx), curr_def_in)



            # If Windows default output device changed (e.g. earbud plugged in/unplugged), switch to new default

            if curr_def_out and (prev_def_out_id != curr_def_out.get("device_id") or "[default]" in cfg_out_name.lower()):

                selected_out = curr_def_out

            else:

                _, resolved_out_idx = resolve_valid_audio_devices(cfg_in_idx, cfg_out_idx, cfg_in_name, cfg_out_name)

                selected_out = next((d for d in outputs if d["index"] == resolved_out_idx), curr_def_out)



            if selected_in:

                aud_cfg["input_device_index"] = selected_in["index"]

                aud_cfg["input_device_name"] = selected_in["label"]

            if selected_out:

                aud_cfg["output_device_index"] = selected_out["index"]

                aud_cfg["output_device_name"] = selected_out["label"]



            try:

                save_config_atomic(self._config, self._config_path)

            except Exception:

                pass



            # Hot-swap active AudioPipeline streams if the assistant is currently running

            if self._engine and self._engine.audio and selected_in and selected_out:

                self._engine.audio.switch_devices(selected_in["index"], selected_out["index"])



            logger.info(

                f"[AUDIO HOTPLUG] Active hardware updated -> Mic: {aud_cfg.get('input_device_name')} | "

                f"Speaker: {aud_cfg.get('output_device_name')}"

            )



            self._on_engine_event(

                "audio_devices_updated",

                {

                    "inputs": inputs,

                    "outputs": outputs,

                    "selected_input_index": aud_cfg.get("input_device_index"),

                    "selected_input_name": aud_cfg.get("input_device_name"),

                    "selected_output_index": aud_cfg.get("output_device_index"),

                    "selected_output_name": aud_cfg.get("output_device_name"),

                },

            )

        except Exception as e:

            logger.debug(f"Audio hardware watcher error: {e}")



    def update_config_value(self, key: str, value: any):

        with self._config_lock:

            self._config[key] = value

            save_config_atomic(self._config, self._config_path)





    @property

    def config(self) -> dict:

        with self._config_lock:

            return self._config



    def set_window(self, window):

        self._window = window



    def set_overlay_window(self, window):

        self._overlay_window = window

        if self._engine:

            self._engine.hud_window = window



    def _is_window_ready(self, win) -> bool:

        """Returns True only if the PyWebView window has a native handle and has finished loading."""

        if not win:

            return False

        if not getattr(win, "native", None):

            return False

        if hasattr(win, "events"):
            if hasattr(win.events, "shown") and hasattr(win.events.shown, "is_set"):
                if not win.events.shown.is_set():
                    return False
            if hasattr(win.events, "loaded") and hasattr(win.events.loaded, "is_set"):
                return win.events.loaded.is_set()

        return True



    def set_hud_bridge(self, hud_bridge):

        self._hud_bridge = hud_bridge

    def set_hud_window(self, hud_window):

        self._hud_window = hud_window



    def update_whitelist(self, new_whitelist: list) -> dict:

        """Updates security app_whitelist in config, persists to disk, and pushes config_updated event."""

        with self._config_lock:

            try:

                self._config.setdefault("security", {})["app_whitelist"] = new_whitelist

                save_config_atomic(self._config, self._config_path)

                self._on_engine_event("config_updated", self._config)

                return {"success": True}

            except Exception as e:

                print(f"[WHITELIST UPDATE ERROR] {e}")

                return {"success": False, "error": str(e)}





    def _load_config(self) -> dict:

        cfg = {}

        if os.path.exists(self._config_path):

            try:

                with open(self._config_path, "r", encoding="utf-8") as f:

                    cfg = json.load(f)

            except Exception as e:

                print(f"[CONFIG LOAD ERROR] {e}")

        return sync_config_schema(cfg)



    def get_raw_config(self) -> dict:

        with self._config_lock:

            return self._config



    def _ensure_js_dispatcher(self):
        """Lazily starts the single FIFO UI event dispatcher thread."""
        if not hasattr(self, "_js_event_queue") or self._js_event_queue is None:
            self._js_event_queue = queue.Queue()
        worker = getattr(self, "_js_dispatcher_thread", None)
        if worker is not None and worker.is_alive():
            return
        with self._js_lock:
            worker = getattr(self, "_js_dispatcher_thread", None)
            if worker is not None and worker.is_alive():
                return

            def _worker_loop():
                while True:
                    payload = self._js_event_queue.get()
                    if payload is None:
                        break
                    if getattr(self, "_is_shutting_down", False):
                        continue
                    with self._js_lock:
                        if getattr(self, "_is_shutting_down", False):
                            continue
                        # Dispatch to Main Window
                        if self._is_window_ready(self._window):
                            try:
                                js_code = f"if (window.aetherUI && window.aetherUI.handleEvent) {{ window.aetherUI.handleEvent({payload}); }}"
                                self._window.evaluate_js(js_code)
                            except Exception as e:
                                logger.debug(f"[BRIDGE DISPATCH MAIN ERROR] {e}")

                        # Dispatch to Floating Overlay Window
                        if self._is_window_ready(self._overlay_window):
                            try:
                                overlay_js = f"if (window.aetherOverlay && window.aetherOverlay.handleEvent) {{ window.aetherOverlay.handleEvent({payload}); }}"
                                self._overlay_window.evaluate_js(overlay_js)
                            except Exception as e:
                                logger.debug(f"[BRIDGE DISPATCH OVERLAY ERROR] {e}")

            t = threading.Thread(target=_worker_loop, daemon=True, name="Aether-UIEventDispatcher")
            self._js_dispatcher_thread = t
            t.start()

    def _run_async_coro(self, coro, close_services=(), timeout: float = 30.0):
        """
        Executes an async coroutine safely from synchronous GuiBridge methods without
        leaking aiohttp.ClientSession instances across ephemeral event loops.
        """
        loop = getattr(self, "_loop", None)
        if loop is not None and loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(coro, loop)
                return fut.result(timeout=timeout)
            except Exception:
                try:
                    coro.close()
                except Exception:
                    pass
                raise

        async def _runner():
            try:
                return await coro
            finally:
                for svc in close_services:
                    if svc is not None and hasattr(svc, "close"):
                        try:
                            await svc.close()
                        except Exception:
                            pass

        return asyncio.run(_runner())

    def _on_engine_event(self, event_type: str, data: dict):

        """Pushes events to both the Main Window and Floating Overlay JavaScript runtimes in strict FIFO order."""

        if getattr(self, "_is_shutting_down", False):
            return

        try:

            if event_type == "hud_cycle_mode":

                self.cycle_hud_mode()

                return



            if event_type == "status" and isinstance(data, dict):
                st_str = str(data.get("state") or "standby")
                hud_b = getattr(self, "_hud_bridge", None)
                if hud_b is not None and hasattr(hud_b, "set_dot_status"):
                    try:
                        hud_b.set_dot_status(state=st_str)
                    except Exception:
                        pass

            if event_type == "mic_status" and isinstance(data, dict):
                hud_b = getattr(self, "_hud_bridge", None)
                if hud_b is not None and hasattr(hud_b, "set_dot_status"):
                    try:
                        if data.get("ptt_active"):
                            hud_b.set_dot_status(state="hearing")
                        else:
                            eng_running = bool(getattr(self._engine, "is_running", False)) if self._engine else False
                            hud_b.set_dot_status(state="listening" if eng_running else "standby")
                    except Exception:
                        pass

            if event_type == "game_mode_changed" and isinstance(data, dict):
                gid = data.get("game_id") or data.get("active_profile") or ""
                prof = self.game_mgr.get_profile(gid) if (gid and hasattr(self, "game_mgr") and self.game_mgr) else None
                if "fps_monitoring" not in data:
                    data["fps_monitoring"] = bool(prof.get("fps_monitoring", True)) if prof else True
                show_fps = bool(data.get("is_running") and data.get("fps_monitoring", True))
                if hasattr(self, "_hud_bridge") and self._hud_bridge and hasattr(self._hud_bridge, "set_mini_fps_visible"):
                    try:
                        self._hud_bridge.set_mini_fps_visible(show_fps)
                    except Exception:
                        pass
                try:
                    self._apply_game_hud_preference(
                        game_id=gid,
                        is_running=bool(data.get("is_running")),
                        profile=prof,
                    )
                except Exception as hud_pref_err:
                    logger.debug(f"[GAME HUD PREF] Error applying game HUD preference: {hud_pref_err}")

            if event_type == "config_updated" and isinstance(data, dict):

                from core.config_manager import mask_sensitive_config

                data = mask_sensitive_config(data)
                try:
                    self._sync_dot_overlay_config()
                except Exception:
                    pass



            payload = json.dumps({"type": event_type, "data": data})
            self._ensure_js_dispatcher()
            self._js_event_queue.put_nowait(payload)

        except Exception as err:
            logger.warning(f"[BRIDGE DISPATCH ERROR] {err}")



    def notify(self, event_type: str, data: dict):

        """Dispatches an event payload to attached pywebview windows."""

        self._on_engine_event(event_type, data)



    # =========================================================================

    # Methods exposed to JavaScript (via window.pywebview.api)

    # =========================================================================



    def get_config(self) -> dict:

        """Returns application configuration for the UI (masking encrypted API key)."""

        with self._config_lock:

            cfg = json.loads(json.dumps(self._config))

            cfg["boot_on_startup"] = self._config.get("boot_on_startup", False)

            cfg["start_minimized"] = self._config.get("start_minimized", False)

            cfg["auto_check_updates"] = self._config.get("auto_check_updates", True)

            cfg["skipped_update_version"] = self._config.get("skipped_update_version", "")

            from core.version import APP_VERSION, APP_CHANNEL
            cfg["app_version"] = APP_VERSION
            cfg["app_channel"] = APP_CHANNEL

            cfg["vad_trailing_silence_ms"] = self._config.get("vad_trailing_silence_ms", 1400)

            api_cfg = cfg.get("api", {})

            enc_key = api_cfg.get("api_key_encrypted", "")

            decrypted = unprotect_secret(enc_key) if enc_key else os.environ.get("GEMINI_API_KEY", "")

            

            if decrypted:

                if len(decrypted) > 8:

                    masked = decrypted[:4] + "************" + decrypted[-4:]

                else:

                    masked = "********"

                api_cfg["api_key_display"] = masked

                api_cfg["has_key"] = True

            else:

                api_cfg["api_key_display"] = ""

                api_cfg["has_key"] = False



            cfg["api"] = api_cfg

            return cfg



    def update_vad_silence(self, silence_ms: int) -> bool:

        """Saves setting to disk and applies immediately to the audio pipeline."""

        with self._config_lock:

            try:

                silence_ms = int(silence_ms)

                self._config["vad_trailing_silence_ms"] = silence_ms

                if "audio" in self._config and isinstance(self._config["audio"], dict):

                    self._config["audio"]["vad_trailing_silence_ms"] = silence_ms



                save_config_atomic(self._config, self._config_path)



                # Hot-update audio stream detector

                if hasattr(self, "engine") and hasattr(self.engine, "audio_stream") and self.engine.audio_stream:

                    self.engine.audio_stream.set_vad_trailing_silence(silence_ms)

                elif hasattr(self._engine, "audio") and self._engine.audio:

                    self._engine.audio.set_vad_trailing_silence(silence_ms)



                self._on_engine_event("config_updated", self.get_config())

                print(f"[INFO] [GUI_BRIDGE] VAD trailing silence updated to {silence_ms}ms")

                return True

            except Exception as e:

                print(f"[ERROR] [GUI_BRIDGE] Failed to update VAD silence: {e}")

                return False





    def save_config(self, new_config: Optional[dict] = None) -> dict:

        """Encrypts new API key if provided and saves updated configuration."""

        with self._config_lock:

            return self._save_config_locked(new_config)



    def _save_config_locked(self, new_config: Optional[dict] = None) -> dict:

        try:

            old_accent = self._config.get("voice_accent") or self._config.get("api", {}).get("voice_accent")

            old_voice = self._config.get("tts_voice") or self._config.get("api", {}).get("voice_name")

            old_model = self._config.get("primary_model_endpoint") or self._config.get("tier1_fast_model") or self._config.get("api", {}).get("model_id")

            old_live_model = self._config.get("api", {}).get("live_model_id")

            old_prompt = self._config.get("api", {}).get("system_instruction")

            old_agent_name = self._config.get("agent_name") or self._config.get("api", {}).get("agent_name")

            old_temp = self._config.get("api", {}).get("temperature")

            old_preferred_language = (
                (self._config.get("audio") or {}).get("preferred_language")
                or self._config.get("preferred_language")
                or "en-US"
            )



            if new_config is None:

                sync_config_schema(self._config)

                save_config_atomic(self._config, self._config_path)

                self._on_engine_event("config_updated", self.get_config())

                return {"success": True, "message": "Settings saved successfully."}







            if "vad_trailing_silence_ms" in new_config:

                silence_ms = int(new_config["vad_trailing_silence_ms"])

                self._config["vad_trailing_silence_ms"] = silence_ms

                if "audio" in self._config and isinstance(self._config["audio"], dict):

                    self._config["audio"]["vad_trailing_silence_ms"] = silence_ms

                if hasattr(self, "engine") and hasattr(self.engine, "audio_stream") and self.engine.audio_stream:

                    self.engine.audio_stream.set_vad_trailing_silence(silence_ms)

                elif hasattr(self._engine, "audio") and self._engine.audio:

                    self._engine.audio.set_vad_trailing_silence(silence_ms)



            # Top-level wake_phrase / kill_phrase / agent_name / startup updates

            for top_key in ("agent_name", "pipeline_mode", "wake_phrase", "kill_phrase", "tts_endpoint", "tts_voice", "tts_speed", "tts_pace", "voice_accent", "wake_word_enabled", "idle_timeout_seconds", "boot_on_startup", "start_minimized", "auto_check_updates", "skipped_update_version", "primary_model_endpoint", "ttrpg_model_endpoint", "ttrpg_thinking_level", "tier1_fast_model", "tier2_moderate_model", "tier2_moderate_level", "tier2_heavy_model", "tier3_heavy_model", "tier2_thinking_level", "tier3_thinking_level", "tier2_thinking_budget", "stt_model_endpoint", "tts_model_endpoint", "stt_endpoint", "always_on_mode", "mode", "ptt_type", "ptt_key", "ptt_key_display", "ptt_vk", "ptt_modifiers", "hud_mode"):

                if top_key in new_config:

                    self._config[top_key] = new_config[top_key]

            if "tier2_moderate_model" in new_config and new_config["tier2_moderate_model"]:
                mod_val = str(new_config["tier2_moderate_model"]).strip()
                self._config["tier2_moderate_model"] = mod_val
                self._config.setdefault("api", {})["moderate_model_id"] = mod_val
                if ":" in mod_val:
                    m_lvl = mod_val.split(":", 1)[1].strip().lower()
                    if m_lvl == "extra":
                        m_lvl = "xhigh"
                    if m_lvl in ("low", "medium", "high", "xhigh"):
                        self._config["tier2_moderate_level"] = m_lvl
                        self._config.setdefault("api", {})["tier2_moderate_level"] = m_lvl

            if "tier3_heavy_model" in new_config or "tier2_heavy_model" in new_config:
                h_val = str(new_config.get("tier3_heavy_model") or new_config.get("tier2_heavy_model") or "").strip()
                if h_val:
                    self._config["tier3_heavy_model"] = h_val
                    self._config["tier2_heavy_model"] = h_val
                    self._config.setdefault("api", {})["pro_model_id"] = h_val
                    if ":" in h_val:
                        h_lvl = h_val.split(":", 1)[1].strip().lower()
                        if h_lvl == "extra":
                            h_lvl = "xhigh"
                        if h_lvl in ("low", "medium", "high", "xhigh"):
                            self._config["tier2_thinking_level"] = h_lvl
                            self._config["tier3_thinking_level"] = h_lvl
                            self._config.setdefault("api", {})["tier2_thinking_level"] = h_lvl
                            self._config.setdefault("api", {})["tier3_thinking_level"] = h_lvl

            if "primary_model_endpoint" in new_config and new_config["primary_model_endpoint"]:
                pm_val = str(new_config["primary_model_endpoint"]).strip()
                self._config["primary_model_endpoint"] = pm_val
                self._config["tier1_fast_model"] = pm_val
                self._config.setdefault("api", {})["model_id"] = pm_val
                if "pipeline_mode" not in new_config and "pipeline_mode" not in (new_config.get("api") or {}):
                    inferred_pipe = "live" if "live" in pm_val.lower() else "modular"
                    self._config["pipeline_mode"] = inferred_pipe
                    self._config.setdefault("api", {})["pipeline_mode"] = inferred_pipe
                    if inferred_pipe == "live":
                        self._config["api"]["live_model_id"] = pm_val

            if "ttrpg_model_endpoint" in new_config and new_config["ttrpg_model_endpoint"]:
                t_val = str(new_config["ttrpg_model_endpoint"]).strip()
                self._config["ttrpg_model_endpoint"] = t_val
                self._config.setdefault("api", {})["ttrpg_model_id"] = t_val
                if ":" in t_val:
                    t_lvl = t_val.split(":", 1)[1].strip().lower()
                    if t_lvl == "extra":
                        t_lvl = "xhigh"
                    if t_lvl in ("low", "medium", "high", "xhigh"):
                        self._config["ttrpg_thinking_level"] = t_lvl
                        self._config.setdefault("api", {})["ttrpg_thinking_level"] = t_lvl



            if "pipeline_mode" in new_config:

                self._config["pipeline_mode"] = str(new_config["pipeline_mode"]).lower()

                self._config.setdefault("api", {})["pipeline_mode"] = str(new_config["pipeline_mode"]).lower()



            if "mode" in new_config:

                self._config["mode"] = new_config["mode"]

                self._config.setdefault("audio", {})["mode"] = new_config["mode"]

            if "ptt_type" in new_config:

                self._config["ptt_type"] = new_config["ptt_type"]

                self._config.setdefault("audio", {})["ptt_type"] = new_config["ptt_type"]

            if "ptt_key" in new_config:

                self._config["ptt_key"] = new_config["ptt_key"]

                self._config.setdefault("audio", {})["ptt_key"] = new_config["ptt_key"]

            if "ptt_key_display" in new_config:

                self._config["ptt_key_display"] = new_config["ptt_key_display"]

                self._config.setdefault("audio", {})["ptt_key_display"] = new_config["ptt_key_display"]

            if "ptt_vk" in new_config:

                self._config["ptt_vk"] = new_config["ptt_vk"]

                self._config.setdefault("audio", {})["ptt_vk"] = new_config["ptt_vk"]

            if "ptt_modifiers" in new_config:

                self._config["ptt_modifiers"] = new_config["ptt_modifiers"]

                self._config.setdefault("audio", {})["ptt_modifiers"] = new_config["ptt_modifiers"]

            if "hud_mode" in new_config:

                self._config["hud_mode"] = new_config["hud_mode"]

                self._config.setdefault("ui", {})["hud_mode"] = new_config["hud_mode"]

                self._current_hud_mode = new_config["hud_mode"]

            if "software_gate" in new_config:

                self._config["software_gate"] = bool(new_config["software_gate"])

                self._config.setdefault("audio", {})["software_gate"] = bool(new_config["software_gate"])



            if "primary_model_endpoint" in new_config:

                self._config["primary_model_endpoint"] = new_config["primary_model_endpoint"]

                self._config["tier1_fast_model"] = new_config["primary_model_endpoint"]

                self._config.setdefault("api", {})["model_id"] = new_config["primary_model_endpoint"]



            if "tier2_heavy_model" in new_config:

                self._config["tier2_heavy_model"] = new_config["tier2_heavy_model"]

                self._config.setdefault("api", {})["pro_model_id"] = new_config["tier2_heavy_model"]

                if ":" in str(new_config["tier2_heavy_model"]):

                    lvl = str(new_config["tier2_heavy_model"]).split(":", 1)[1].strip().lower()
                    if lvl == "extra":
                        lvl = "xhigh"

                    if lvl in ("low", "medium", "high", "xhigh"):

                        self._config["tier2_thinking_level"] = lvl
                        self._config["tier3_thinking_level"] = lvl

                        self._config.setdefault("api", {})["tier2_thinking_level"] = lvl
                        self._config.setdefault("api", {})["tier3_thinking_level"] = lvl



            if "tier2_thinking_level" in new_config:

                self._config["tier2_thinking_level"] = new_config["tier2_thinking_level"]

                self._config.setdefault("api", {})["tier2_thinking_level"] = new_config["tier2_thinking_level"]



            if "stt_endpoint" in new_config and new_config["stt_endpoint"]:

                self._config["stt_endpoint"] = new_config["stt_endpoint"]

                self._config["stt_model_endpoint"] = new_config["stt_endpoint"]

                self._config.setdefault("api", {})["stt_endpoint"] = new_config["stt_endpoint"]

                self._config.setdefault("api", {})["stt_model_id"] = new_config["stt_endpoint"]



            if "stt_model_endpoint" in new_config and new_config["stt_model_endpoint"]:

                self._config["stt_model_endpoint"] = new_config["stt_model_endpoint"]

                self._config["stt_endpoint"] = new_config["stt_model_endpoint"]

                self._config.setdefault("api", {})["stt_model_id"] = new_config["stt_model_endpoint"]

                self._config.setdefault("api", {})["stt_endpoint"] = new_config["stt_model_endpoint"]



            if "tts_model_endpoint" in new_config and new_config["tts_model_endpoint"]:

                self._config["tts_model_endpoint"] = new_config["tts_model_endpoint"]

                self._config.setdefault("api", {})["tts_model_id"] = new_config["tts_model_endpoint"]

                self._config.setdefault("api", {})["tts_endpoint"] = new_config["tts_model_endpoint"]

                self._config["tts_endpoint"] = new_config["tts_model_endpoint"]



            if "models" in new_config and isinstance(new_config["models"], dict):

                self._config.setdefault("models", {}).update(new_config["models"])



            if "boot_on_startup" in new_config:

                self._config["boot_on_startup"] = bool(new_config["boot_on_startup"])

            elif "ui" in new_config and "boot_on_startup" in new_config["ui"]:

                self._config["boot_on_startup"] = bool(new_config["ui"]["boot_on_startup"])



            if "start_minimized" in new_config:

                self._config["start_minimized"] = bool(new_config["start_minimized"])

            elif "ui" in new_config and "start_minimized" in new_config["ui"]:

                self._config["start_minimized"] = bool(new_config["ui"]["start_minimized"])



            api_cfg = new_config.get("api") or {}

            raw_key_input = str(api_cfg.get("new_api_key") or "").strip()

            if raw_key_input and not raw_key_input.startswith("*") and not raw_key_input.startswith("•"):

                encrypted_key = protect_secret(raw_key_input)

                self._config.setdefault("api", {})["api_key_encrypted"] = encrypted_key

            

            if "agent_name" in api_cfg:

                agent_name_val = api_cfg["agent_name"].strip() or "Aether"

                self._config.setdefault("api", {})["agent_name"] = agent_name_val

                self._config["agent_name"] = agent_name_val

            if "wake_phrase" in api_cfg:

                wp_val = api_cfg["wake_phrase"].strip()

                if wp_val:

                    self._config.setdefault("api", {})["wake_phrase"] = wp_val

                    self._config.setdefault("audio", {})["wake_phrase"] = wp_val

                    self._config["wake_phrase"] = wp_val

            if "model_id" in api_cfg:

                self._config.setdefault("api", {})["model_id"] = api_cfg["model_id"]

            if "moderate_model_id" in api_cfg:

                self._config.setdefault("api", {})["moderate_model_id"] = api_cfg["moderate_model_id"]

                self._config["tier2_moderate_model"] = api_cfg["moderate_model_id"]

            if "pipeline_mode" in api_cfg:

                pipe_val = str(api_cfg["pipeline_mode"]).lower()

                self._config.setdefault("api", {})["pipeline_mode"] = pipe_val

                self._config["pipeline_mode"] = pipe_val

            if "stt_model_id" in api_cfg:

                self._config.setdefault("api", {})["stt_model_id"] = api_cfg["stt_model_id"]

            if "tts_model_id" in api_cfg:

                self._config.setdefault("api", {})["tts_model_id"] = api_cfg["tts_model_id"]

            if "live_model_id" in api_cfg:

                self._config.setdefault("api", {})["live_model_id"] = api_cfg["live_model_id"]

            if "stt_endpoint" in api_cfg:

                self._config.setdefault("api", {})["stt_endpoint"] = api_cfg["stt_endpoint"]

            if "tts_endpoint" in api_cfg:

                self._config.setdefault("api", {})["tts_endpoint"] = api_cfg["tts_endpoint"]

                self._config["tts_endpoint"] = api_cfg["tts_endpoint"]

            if "pro_model_id" in api_cfg:

                self._config.setdefault("api", {})["pro_model_id"] = api_cfg["pro_model_id"]

            if "temperature" in api_cfg:

                self._config.setdefault("api", {})["temperature"] = float(api_cfg["temperature"])

            if "voice_name" in api_cfg:

                self._config.setdefault("api", {})["voice_name"] = api_cfg["voice_name"]

                self._config["tts_voice"] = api_cfg["voice_name"]

            if "voice_accent" in api_cfg:

                self._config.setdefault("api", {})["voice_accent"] = api_cfg["voice_accent"]

                self._config["voice_accent"] = api_cfg["voice_accent"]

            if "voice_speed" in api_cfg:

                self._config.setdefault("api", {})["voice_speed"] = float(api_cfg["voice_speed"])

                self._config["tts_speed"] = float(api_cfg["voice_speed"])

            if "tts_pace" in api_cfg:

                pace_val = str(api_cfg["tts_pace"]).strip().lower()

                if pace_val in ("measured", "natural", "brisk"):

                    self._config.setdefault("api", {})["tts_pace"] = pace_val

                    self._config["tts_pace"] = pace_val

            if "local_tts_url" in api_cfg:

                self._config.setdefault("api", {})["local_tts_url"] = api_cfg["local_tts_url"]

            if "system_instruction" in api_cfg:

                self._config.setdefault("api", {})["system_instruction"] = api_cfg["system_instruction"]



            if "preferred_language" in new_config and new_config["preferred_language"]:

                self._config.setdefault("audio", {})["preferred_language"] = str(new_config["preferred_language"]).strip()

                self._config["preferred_language"] = self._config["audio"]["preferred_language"]

            if "audio" in new_config:

                aud_cfg = new_config["audio"]

                if "wake_phrase" in aud_cfg and aud_cfg["wake_phrase"]:

                    self._config["wake_phrase"] = aud_cfg["wake_phrase"].strip()

                    self._config.setdefault("api", {})["wake_phrase"] = self._config["wake_phrase"]

                if "sleep_phrase" in aud_cfg and aud_cfg["sleep_phrase"]:

                    self._config["sleep_phrase"] = aud_cfg["sleep_phrase"].strip()

                elif "stop_listening_phrase" in aud_cfg and aud_cfg["stop_listening_phrase"]:

                    self._config["sleep_phrase"] = aud_cfg["stop_listening_phrase"].strip()

                    aud_cfg["sleep_phrase"] = self._config["sleep_phrase"]

                if "mode" in aud_cfg and aud_cfg["mode"]:

                    self._config["mode"] = aud_cfg["mode"]

                if "ptt_type" in aud_cfg and aud_cfg["ptt_type"]:

                    self._config["ptt_type"] = aud_cfg["ptt_type"]

                if "ptt_key" in aud_cfg and aud_cfg["ptt_key"]:

                    self._config["ptt_key"] = aud_cfg["ptt_key"]

                if "ptt_key_display" in aud_cfg and aud_cfg["ptt_key_display"]:

                    self._config["ptt_key_display"] = aud_cfg["ptt_key_display"]

                if "ptt_vk" in aud_cfg:

                    self._config["ptt_vk"] = aud_cfg["ptt_vk"]

                if "ptt_modifiers" in aud_cfg:

                    self._config["ptt_modifiers"] = aud_cfg["ptt_modifiers"]

                if "always_on_mode" in aud_cfg and aud_cfg["always_on_mode"]:

                    self._config["always_on_mode"] = aud_cfg["always_on_mode"].strip()

                    self._config["wake_word_enabled"] = (self._config["always_on_mode"] != "always_on")

                    aud_cfg["wake_word_enabled"] = self._config["wake_word_enabled"]

                if "kill_phrase" in aud_cfg and aud_cfg["kill_phrase"]:

                    self._config["kill_phrase"] = aud_cfg["kill_phrase"].strip()

                    aud_cfg["safe_phrase"] = self._config["kill_phrase"]

                elif "safe_phrase" in aud_cfg and aud_cfg["safe_phrase"]:

                    self._config["kill_phrase"] = aud_cfg["safe_phrase"].strip()

                    aud_cfg["kill_phrase"] = self._config["kill_phrase"]

                if "vad_trailing_silence_ms" in aud_cfg:

                    silence_ms = int(aud_cfg["vad_trailing_silence_ms"])

                    self._config["vad_trailing_silence_ms"] = silence_ms

                    self._config.setdefault("audio", {})["vad_trailing_silence_ms"] = silence_ms

                    if hasattr(self, "engine") and hasattr(self.engine, "audio_stream") and self.engine.audio_stream:

                        self.engine.audio_stream.set_vad_trailing_silence(silence_ms)

                    elif hasattr(self._engine, "audio") and self._engine.audio:

                        self._engine.audio.set_vad_trailing_silence(silence_ms)

                if "voice_biometrics" in aud_cfg:

                    self._config.setdefault("audio", {}).setdefault("voice_biometrics", {}).update(aud_cfg["voice_biometrics"])

                    aud_copy = dict(aud_cfg)

                    del aud_copy["voice_biometrics"]

                    self._config["audio"].update(aud_copy)

                else:

                    self._config.setdefault("audio", {}).update(aud_cfg)

                if self._engine:

                    audio_mode = self._config["audio"].get("mode", "always_on")

                    if audio_mode != "ptt" and (
                        getattr(self._engine, "_ptt_active", False)
                        or (self._engine.audio and getattr(self._engine.audio, "ptt_active", False))
                    ):
                        try:
                            self._engine.set_ptt(False)
                        except Exception:
                            pass

                    if self._engine.audio:

                        self._engine.audio.set_mode(audio_mode)

                        self._engine.audio.set_software_gate(self._config["audio"].get("software_gate", False))

                        ao_mode = self._config.get("always_on_mode") or self._config["audio"].get("always_on_mode", "wake_word")

                        wake_en = (ao_mode != "always_on")

                        self._engine.audio.wake_word_enabled = wake_en

                        self._engine.audio.config["wake_word_enabled"] = wake_en

                        self._engine.audio.config["always_on_mode"] = ao_mode

                        if hasattr(self._engine.audio, "wake_detector") and self._engine.audio.wake_detector:

                            wp = self._config.get("wake_phrase") or self._config["audio"].get("wake_phrase")

                            if wp:

                                self._engine.audio.wake_detector.update_wake_phrase(wp)

                            sp = self._config.get("sleep_phrase") or self._config["audio"].get("sleep_phrase")

                            if sp:

                                self._engine.audio.wake_detector.update_sleep_phrase(sp)

                            self._engine.audio.wake_detector.update_listening_mode(ao_mode)

                            if self._engine.is_running:

                                ag_name = self._config.get("api", {}).get("agent_name", "Aether").strip() or "Aether"

                                if audio_mode == "ptt":

                                    self._on_engine_event("status", {

                                        "state": "standby",

                                        "message": f"{ag_name} is ready (Push-to-Talk)."

                                    })

                                elif wake_en and self._engine.audio.wake_detector.state.value == "IDLE_LISTENING":

                                    self._on_engine_event("status", {

                                        "state": "standby",

                                        "message": f"Standby — Say '{self._engine.audio.wake_detector.wake_phrase}' to wake {ag_name}."

                                    })

                                else:

                                    self._on_engine_event("status", {

                                        "state": "listening",

                                        "message": f"{ag_name} is listening..."

                                    })

                                    if not self._engine.is_on_demand_mode():

                                        self._engine.request_live_session_connect(reason="Switched to Always-On Open Mic")

                        new_in = self._config["audio"].get("input_device_index")

                        new_out = self._config["audio"].get("output_device_index")

                        if (

                            new_in is not None

                            and new_out is not None

                            and (new_in != self._engine.audio.input_device or new_out != self._engine.audio.output_device)

                        ):

                            self._engine.audio.switch_devices(int(new_in), int(new_out))

                    if hasattr(self._engine, "hotkey_manager") and self._engine.hotkey_manager:

                        self._engine.hotkey_manager.update_config(self._config.get("audio", {}), self._config.get("ui", {}))



            if "vision" in new_config and isinstance(new_config["vision"], dict):

                self._config.setdefault("vision", {}).update(new_config["vision"])

            if "security" in new_config and isinstance(new_config["security"], dict):

                self._config.setdefault("security", {}).update(new_config["security"])

            if "ui" in new_config:

                ui_in = dict(new_config["ui"]) if isinstance(new_config["ui"], dict) else {}
                from ui.dot_overlay import (
                    normalize_dot_position,
                    normalize_dot_size,
                    sanitize_hud_colors,
                )
                if "dot_size" in ui_in:
                    ui_in["dot_size"] = normalize_dot_size(ui_in["dot_size"])
                if "dot_position" in ui_in:
                    ui_in["dot_position"] = normalize_dot_position(ui_in["dot_position"])
                if "dot_monitor" in ui_in:
                    ui_in["dot_monitor"] = str(ui_in["dot_monitor"] or "auto")
                if "hud_colors" in ui_in:
                    ui_in["hud_colors"] = sanitize_hud_colors(ui_in["hud_colors"])

                self._config.setdefault("ui", {}).update(ui_in)

                if "hud_mode" in ui_in:

                    target_mode = str(ui_in["hud_mode"]).lower()

                    if target_mode in ("dot", "mini", "normal", "max"):

                        self._current_hud_mode = target_mode

                        self._config["hud_mode"] = target_mode

                self._sync_dot_overlay_config()

                if hasattr(self._engine, "hotkey_manager") and self._engine.hotkey_manager:

                    self._engine.hotkey_manager.update_config(self._config.get("audio", {}), self._config.get("ui", {}))

            if "hardware_telemetry" in new_config and isinstance(new_config["hardware_telemetry"], dict):
                hw_in = new_config["hardware_telemetry"]
                hw_cfg = self._config.setdefault("hardware_telemetry", {})
                if "alerts_enabled" in hw_in:
                    hw_cfg["alerts_enabled"] = bool(hw_in["alerts_enabled"])
                    hw_cfg["verbal_warnings"] = bool(hw_in["alerts_enabled"])
                if "verbal_warnings" in hw_in:
                    hw_cfg["verbal_warnings"] = bool(hw_in["verbal_warnings"])
                if "cpu_temp_alert_threshold_c" in hw_in:
                    hw_cfg["cpu_temp_alert_threshold_c"] = int(float(hw_in["cpu_temp_alert_threshold_c"]))
                if "gpu_temp_alert_threshold_c" in hw_in:
                    hw_cfg["gpu_temp_alert_threshold_c"] = int(float(hw_in["gpu_temp_alert_threshold_c"]))
                if "vram_alert_threshold_pct" in hw_in:
                    hw_cfg["vram_alert_threshold_pct"] = int(float(hw_in["vram_alert_threshold_pct"]))
                if "alert_sustained_seconds" in hw_in:
                    hw_cfg["alert_sustained_seconds"] = max(5, min(30, int(float(hw_in["alert_sustained_seconds"]))))

                try:
                    from core.config import load_settings, save_settings
                    engine_inst = getattr(self, "_engine", None) or getattr(self, "engine", None)
                    if engine_inst and hasattr(engine_inst, "settings") and isinstance(engine_inst.settings, dict):
                        engine_inst.settings.setdefault("hardware_telemetry", {}).update(hw_cfg)
                        save_settings(engine_inst.settings)
                    else:
                        s_data = load_settings()
                        s_data.setdefault("hardware_telemetry", {}).update(hw_cfg)
                        save_settings(s_data)
                    if engine_inst and hasattr(engine_inst, "hardware_service") and engine_inst.hardware_service:
                        engine_inst.hardware_service.update_alert_settings(
                            alerts_enabled=hw_cfg.get("alerts_enabled", True),
                            cpu_temp_threshold_c=hw_cfg.get("cpu_temp_alert_threshold_c", 95),
                            gpu_temp_threshold_c=hw_cfg.get("gpu_temp_alert_threshold_c", 85),
                            vram_threshold_pct=hw_cfg.get("vram_alert_threshold_pct", 95),
                            sustained_seconds=hw_cfg.get("alert_sustained_seconds", 15),
                        )
                except Exception as e:
                    logger.debug(f"[GUI_BRIDGE] Failed syncing hardware_telemetry settings: {e}")



            new_accent = self._config.get("voice_accent") or self._config.get("api", {}).get("voice_accent")

            new_voice = self._config.get("tts_voice") or self._config.get("api", {}).get("voice_name")

            new_model = self._config.get("primary_model_endpoint") or self._config.get("tier1_fast_model") or self._config.get("api", {}).get("model_id")

            new_live_model = self._config.get("api", {}).get("live_model_id")

            new_prompt = self._config.get("api", {}).get("system_instruction")

            new_agent_name = self._config.get("agent_name") or self._config.get("api", {}).get("agent_name")

            new_temp = self._config.get("api", {}).get("temperature")

            new_preferred_language = (
                (self._config.get("audio") or {}).get("preferred_language")
                or self._config.get("preferred_language")
                or "en-US"
            )



            accent_changed = (old_accent != new_accent)

            voice_changed = (old_voice != new_voice)

            model_changed = (old_model != new_model or old_live_model != new_live_model)

            prompt_changed = (old_prompt != new_prompt or old_agent_name != new_agent_name)

            temp_changed = (old_temp != new_temp)

            language_changed = (str(old_preferred_language).strip() != str(new_preferred_language).strip())



            live_config_changed = bool(accent_changed or voice_changed or model_changed or prompt_changed or temp_changed or language_changed)



            sync_config_schema(self._config)

            save_config_atomic(self._config, self._config_path)



            engine_ref = getattr(self, "_engine", None) or getattr(self, "engine", None)

            if engine_ref:

                if hasattr(engine_ref, "update_voice_accent_directive"):

                    engine_ref.update_voice_accent_directive(new_accent)

                if prompt_changed or language_changed:

                    if hasattr(engine_ref, "rebuild_base_system_instruction"):

                        engine_ref.rebuild_base_system_instruction()

                    if getattr(engine_ref, "_active_pipeline_mode", "live") == "modular":

                        engine_ref._reset_context_flag = True

                if live_config_changed:

                    changes = []

                    if accent_changed: changes.append(f"accent: {old_accent} -> {new_accent}")

                    if voice_changed: changes.append(f"voice: {old_voice} -> {new_voice}")

                    if model_changed: changes.append("model")

                    if prompt_changed: changes.append("prompt/name")

                    if temp_changed: changes.append("temp")

                    if language_changed: changes.append(f"language: {old_preferred_language} -> {new_preferred_language}")

                    reason_str = f"Settings: {', '.join(changes)}"

                    logger.info(f"[GUI BRIDGE] Live configuration updated ({reason_str}). Invalidating live session.")

                    if hasattr(engine_ref, "invalidate_live_session"):

                        engine_ref.invalidate_live_session(reason=reason_str)

                    elif getattr(engine_ref, "is_running", False):

                        conn_mgr = getattr(engine_ref, "conn_mgr", None)

                        if conn_mgr and str(getattr(getattr(conn_mgr, "state", None), "value", getattr(conn_mgr, "state", ""))).upper() == "CONNECTED":

                            engine_ref.request_live_session_rotation(reason=reason_str)



            self._on_engine_event("config_updated", self.get_config())



            return {"success": True, "message": "Settings saved successfully."}



        except Exception as e:

            return {"success": False, "error": str(e)}



    def get_audio_devices(self, force_refresh: bool = False) -> dict:

        """Enumerates truly active, available microphones and speakers on the system matching Windows System\\Sound."""

        return get_available_audio_devices(force_refresh=force_refresh)



    def get_edge_voice_catalog(self) -> dict:

        """Returns structured Edge TTS catalog with priority sorted regions and clean voice names."""

        return _get_edge_voice_catalog()



    def get_available_voices(self, engine: str = "gemini-live-native", region: Optional[str] = None) -> list:

        """Returns list of voices tailored to the selected TTS engine (Gemini, Edge, SAPI5, or Local TTS)."""

        eng = (engine or "").lower()

        if "edge" in eng:

            if region:

                catalog = _get_edge_voice_catalog()

                reg_voices = catalog.get("voices", {}).get(region, [])

                if reg_voices:

                    return [{"name": v["id"], "trait": region, "gender": v["gender"], "clean_name": v["name"], "label": v["label"]} for v in reg_voices]

            return _get_edge_voices()

        elif "windows" in eng or "sapi" in eng:

            return _get_sapi_voices()

        elif "local" in eng or "kokoro" in eng:

            return KOKORO_VOICES

        else:

            return GEMINI_VOICES



    def get_available_accents(self) -> list:

        """Returns list of natural language voice accents for Gemini speech."""

        return [

            {"id": "default", "label": "Default (Native / Standard)"},

            {"id": "British", "label": "British (Received Pronunciation / BBC English)"},

            {"id": "Cockney", "label": "British (Cockney / East London)"},

            {"id": "Scottish", "label": "Scottish"},

            {"id": "Irish", "label": "Irish"},

            {"id": "Australian", "label": "Australian"},

            {"id": "American Southern", "label": "American (Southern / Texan)"},

            {"id": "American New York", "label": "American (New York)"},

            {"id": "French", "label": "French Accent"},

            {"id": "German", "label": "German Accent"},

            {"id": "Italian", "label": "Italian Accent"},

            {"id": "Spanish", "label": "Spanish Accent"},

            {"id": "Indian", "label": "Indian Accent"},

            {"id": "Japanese", "label": "Japanese Accent"}

        ]



    def get_discovered_models(self) -> dict:

        """Returns cached models grouped by chat, tts, and stt."""

        return load_cached_categorized_models()



    def refresh_discovered_models(self) -> dict:

        """Forces a live model query and updates all three categories."""

        try:

            client = getattr(self.engine, "client", None)

            if not client:

                return {"success": False, "error": "API client not initialized."}

            data = fetch_available_gemini_models(client)

            return {

                "success": True,

                "categorized_models": data,

                "models": data.get("chat_models", [])

            }

        except Exception as e:

            return {"success": False, "error": str(e)}



    def get_monitors(self) -> list:

        """Enumerates connected monitors for the frontend settings."""

        try:

            return self._engine.screen_pipeline.get_monitor_list_for_ui()

        except Exception as e:

            return [{"id": "auto", "name": "Auto (Follow Active Window)", "details": str(e)}]



    def start_assistant(self) -> dict:

        """Starts the Aether voice assistant engine."""

        try:

            if not self._engine.is_running:

                self._engine.start(self._loop)

                return {"success": True, "status": "starting"}

            return {"success": True, "status": "already_running"}

        except Exception as e:

            return {"success": False, "error": str(e)}



    def stop_assistant(self) -> dict:

        """Stops the Aether assistant engine."""

        try:

            if hasattr(self, "set_ttrpg_session_active"):
                try:
                    self.set_ttrpg_session_active(False)
                except Exception:
                    pass

            self._engine.stop()

            agent_name = self._config.get("api", {}).get("agent_name", "Aether")

            self._on_engine_event("status", {"state": "disconnected", "message": f"{agent_name} stopped."})

            return {"success": True, "status": "stopped"}

        except Exception as e:

            logger.error(f"[STOP ASSISTANT ERROR] {e}", exc_info=True)

            return {"success": False, "error": str(e)}



    def kill_audio(self) -> dict:

        """Safe-word / manual trigger: immediately silences audio playback."""

        try:

            self._engine.kill_audio()

            return {"success": True}

        except Exception as e:

            return {"success": False, "error": str(e)}

    def refresh_connection(self) -> dict:
        """Stops and restarts the assistant engine while clearing hung audio/session buffers."""
        try:
            logger.info("[GUI BRIDGE] Refreshing Aether connection and clearing buffers...")
            try:
                self._engine.kill_audio()
            except Exception:
                pass

            # Clear any stuck state flags or stale resumption tokens
            for attr, val in (
                ("_session_resumption_handle", None),
                ("is_tool_running", False),
                ("is_speaking", False),
                ("is_audio_streaming", False),
                ("_ptt_active", False),
            ):
                if hasattr(self._engine, attr):
                    try:
                        setattr(self._engine, attr, val)
                    except Exception:
                        pass
            if hasattr(self._engine, "hotkey_manager") and self._engine.hotkey_manager:
                try:
                    self._engine.hotkey_manager.set_input_focused(False)
                except Exception:
                    pass

            if getattr(self._engine, "is_running", False):
                try:
                    self._engine.stop()
                except Exception as stop_err:
                    logger.warning(f"[REFRESH CONNECTION] Stop warning: {stop_err}")
                time.sleep(0.35)

            if not getattr(self._engine, "is_running", False):
                self._engine.start(self._loop)

            return {"success": True, "status": "refreshed"}
        except Exception as e:
            logger.error(f"[REFRESH CONNECTION ERROR] {e}", exc_info=True)
            return {"success": False, "error": str(e)}



    def reset_chat_context(self) -> dict:

        """Resets the active conversational context / chat history in the engine."""

        try:

            if self._engine:

                self._engine.reset_chat_context()

                return {"success": True, "message": "Context reset successfully."}

            return {"success": False, "error": "Engine not running"}

        except Exception as e:

            return {"success": False, "error": str(e)}



    def set_ptt(self, active: bool) -> dict:

        """Sets the Push-To-Talk state (active = True/False), auto-starting engine if idle."""

        if active and not self._engine.is_running:

            self.start_assistant()

        self._engine.set_ptt(active)

        return {"success": True}



    def toggle_ptt(self) -> dict:

        """Toggles active Push-to-Talk state, auto-starting engine if idle."""

        if not self._engine.is_running:

            self.start_assistant()

        if self._engine:

            new_state = self._engine.toggle_ptt()

            return {"success": True, "active": new_state}

        return {"success": False, "error": "Engine not running"}



    def set_input_focused(self, focused: bool) -> dict:

        """Informs engine whether a text input in the UI has focus (to suppress hotkeys)."""

        if self._engine and hasattr(self._engine, "hotkey_manager") and self._engine.hotkey_manager:

            self._engine.hotkey_manager.set_input_focused(focused)

        return {"success": True}



    def send_text_message(self, text: str) -> dict:

        """Sends a typed chat prompt to Gemini Live."""

        try:

            if not self._engine.is_running:
                logger.info("[GUI_BRIDGE] Auto-starting engine from typed text message")
                self.start_assistant()

            asyncio.run_coroutine_threadsafe(self._engine.send_text(text), self._loop)

            return {"success": True}

        except Exception as e:

            return {"success": False, "error": str(e)}



    def send_chat_message(self, text: str) -> dict:

        """Alias for send_text_message to send typed prompt to Gemini Live."""

        return self.send_text_message(text)



    def get_telemetry(self) -> dict:

        """Returns real-time telemetry (mic level, status, speaking state)."""

        mic_lvl = self._engine.audio.current_mic_level if (self._engine and self._engine.audio) else 0.0

        is_spk = self._engine.audio.is_speaking if (self._engine and self._engine.audio) else False

        return {

            "is_running": self._engine.is_running if self._engine else False,

            "is_speaking": is_spk,

            "mic_level": mic_lvl

        }



    def get_windows_theme(self) -> dict:

        """Reads Windows system dark/light theme and accent color from the registry."""

        is_dark = True

        accent_hex = "#0078d4"



        if sys.platform == "win32":

            try:

                import winreg

                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:

                    apps_use_light = winreg.QueryValueEx(key, 'AppsUseLightTheme')[0]

                    is_dark = (apps_use_light == 0)

            except Exception:

                pass



            try:

                import winreg

                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\DWM') as key:

                    accent = winreg.QueryValueEx(key, 'AccentColor')[0]

                    r = accent & 0xFF

                    g = (accent >> 8) & 0xFF

                    b = (accent >> 16) & 0xFF

                    # Avoid extremely dark or black accent fallback

                    if r + g + b > 50:

                        accent_hex = f"#{r:02x}{g:02x}{b:02x}"

            except Exception:

                pass



        return {

            "is_dark": is_dark,

            "accent_color": accent_hex

        }

    def set_overlay_theme(self, theme: str) -> dict:
        """Pushes the resolved Light/Dark theme to the floating HUD overlay window."""
        mode = "light" if str(theme).lower() == "light" else "dark"
        overlay_win = getattr(self, "_overlay_window", None)
        if self._is_window_ready(overlay_win):
            try:
                overlay_win.evaluate_js(
                    f"window.applyOverlayTheme && window.applyOverlayTheme({json.dumps(mode)})"
                )
            except Exception:
                pass
        return {"success": True, "theme": mode}



    # =========================================================================

    # Target Speaker Verification & Voice Profile Onboarding

    # =========================================================================



    def get_voice_profile_status(self) -> dict:

        """Returns the enrollment status, active threshold, and staged samples."""

        try:

            status = self._engine.voice_verifier.get_status()

            bio_cfg = self._config.get("audio", {}).get("voice_biometrics", {})

            status["enabled"] = bio_cfg.get("enabled", False)

            status["threshold"] = bio_cfg.get("threshold", 0.45)

            return {"success": True, **status}

        except Exception as e:

            return {"success": False, "error": str(e)}



    def start_voice_calibration(self) -> dict:

        """Clears any previous staged calibration samples to begin fresh onboarding."""

        try:

            self._engine.voice_verifier.clear_staged_samples()

            return {"success": True, "message": "Ready to record calibration samples."}

        except Exception as e:

            return {"success": False, "error": str(e)}



    def record_calibration_sample(self, prompt_index: int) -> dict:

        """

        Records a 3.5-second calibration utterance from the configured microphone

        at its native hardware sample rate, resamples to 16kHz 16-bit mono,

        and extracts a 512-dim CAM++ embedding vector.

        """

        was_calibrating = False

        try:

            # Temporarily mute assistant speech intake while recording calibration

            if self._engine and self._engine.audio and self._engine.is_running:

                self._engine.audio.is_calibrating = True

                was_calibrating = True



            audio_cfg = self._config.get("audio", {})

            in_idx = audio_cfg.get("input_device_index", None)



            # Query hardware device details to get native supported samplerate

            try:

                in_info = sd.query_devices(in_idx) if in_idx is not None else sd.query_devices(kind='input')

                hw_sr = int(in_info.get('default_samplerate', 48000))

                dev_idx = in_info.get('index', in_idx)

            except Exception:

                in_info = sd.query_devices(kind='input')

                hw_sr = int(in_info.get('default_samplerate', 48000))

                dev_idx = None



            duration = 3.5

            target_sr = 16000



            logger.info(f"[VOICE CALIBRATION] Recording prompt #{prompt_index} for {duration}s from mic #{dev_idx} ({hw_sr}Hz)...")

            

            # Record float32 at native hardware rate

            import numpy as np

            recording = sd.rec(

                int(duration * hw_sr),

                samplerate=hw_sr,

                channels=1,

                dtype='float32',

                device=dev_idx

            )

            sd.wait()



            # Resample to 16,000 Hz if hardware rate differs

            mono = recording.flatten() if recording.ndim == 1 else np.mean(recording, axis=1)

            if hw_sr == 48000 and target_sr == 16000:

                rem = len(mono) % 3

                if rem > 0:

                    mono = mono[:-rem]

                resampled = mono.reshape(-1, 3).mean(axis=1)

            elif hw_sr != target_sr:

                target_length = int(round(len(mono) * target_sr / hw_sr))

                if target_length > 0:

                    resampled = np.interp(

                        np.linspace(0.0, 1.0, target_length, endpoint=False),

                        np.linspace(0.0, 1.0, len(mono), endpoint=False),

                        mono

                    )

                else:

                    resampled = mono

            else:

                resampled = mono



            # Convert to 16-bit PCM integer

            audio_int16 = (np.clip(resampled, -1.0, 1.0) * 32767).astype(np.int16)



            import io, wave

            buf = io.BytesIO()

            with wave.open(buf, "wb") as wf:

                wf.setnchannels(1)

                wf.setsampwidth(2)

                wf.setframerate(target_sr)

                wf.writeframes(audio_int16.tobytes())

            wav_bytes = buf.getvalue()



            res = self._engine.voice_verifier.add_calibration_sample(wav_bytes)

            res["prompt_index"] = prompt_index

            return res

        except Exception as e:

            logger.error(f"[VOICE CALIBRATION ERROR] {e}")

            return {"success": False, "error": str(e)}

        finally:

            if was_calibrating and self._engine and self._engine.audio:

                self._engine.audio.is_calibrating = False



    def finalize_voice_profile(self, threshold: float = 0.45, enabled: bool = True) -> dict:

        """Averages calibration samples, saves voiceprint, and enables biometric gating."""

        try:

            res = self._engine.voice_verifier.finalize_calibration()

            if res.get("success"):

                with self._config_lock:

                    self._config.setdefault("audio", {}).setdefault("voice_biometrics", {})["enabled"] = enabled

                    self._config["audio"]["voice_biometrics"]["threshold"] = float(threshold)

                    save_config_atomic(self._config, self._config_path)

                self._on_engine_event("voice_profile_updated", {"enrolled": True, "enabled": enabled, "threshold": threshold})

            return res

        except Exception as e:

            logger.error(f"[VOICE CALIBRATION FINALIZE ERROR] {e}")

            return {"success": False, "error": str(e)}



    def save_voice_biometrics_settings(self, enabled: bool, threshold: float) -> dict:

        """Updates biometric enabled toggle and sensitivity threshold."""

        try:

            with self._config_lock:

                self._config.setdefault("audio", {}).setdefault("voice_biometrics", {})["enabled"] = bool(enabled)

                self._config["audio"]["voice_biometrics"]["threshold"] = float(threshold)

                save_config_atomic(self._config, self._config_path)

            self._on_engine_event("voice_profile_updated", {"enrolled": self._engine.voice_verifier.is_enrolled(), "enabled": enabled, "threshold": threshold})

            return {"success": True, "enabled": enabled, "threshold": threshold}

        except Exception as e:

            return {"success": False, "error": str(e)}



    def delete_voice_profile(self) -> dict:

        """Removes enrolled voiceprint profile."""

        try:

            res = self._engine.voice_verifier.delete_profile()

            with self._config_lock:

                self._config.setdefault("audio", {}).setdefault("voice_biometrics", {})["enabled"] = False

                save_config_atomic(self._config, self._config_path)

            self._on_engine_event("voice_profile_updated", {"enrolled": False, "enabled": False})

            return res

        except Exception as e:



            return {"success": False, "error": str(e)}



    # =========================================================================

    # Window & Floating Overlay Controls

    # =========================================================================



    def restore_main_window(self) -> dict:

        """Restores the main window from minimized or hidden state and focuses it."""

        try:

            if self._window:

                self._window.show()

                self._window.restore()

                if sys.platform == "win32":

                    try:

                        import win32gui

                        import win32con

                        hwnd = None

                        if hasattr(self._window, "native") and self._window.native and hasattr(self._window.native, "Handle"):

                            try:

                                hwnd = self._window.native.Handle.ToInt64()

                            except Exception:

                                hwnd = None

                        if not hwnd:

                            hwnd = win32gui.FindWindow(None, "Aether Desktop")

                        if hwnd and win32gui.IsWindow(hwnd):

                            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

                            win32gui.SetForegroundWindow(hwnd)

                    except Exception as ex:

                        logger.warning(f"[RESTORE MAIN WIN32 ERROR] {ex}")

                cfg = self._config.get("ui", {})

                mode = cfg.get("floating_overlay", "on_minimize")

                if mode == "on_minimize" and getattr(self, "_overlay_window", None):

                    self._overlay_window.hide()

                    self._overlay_visible = False

            return {"success": True}

        except Exception as e:

            return {"success": False, "error": str(e)}



    def reset_overlay_position(self) -> dict:

        """Snaps the floating overlay window back to the top-right of the primary display."""

        try:

            from ui.hud_window import (
                clamp_window_position,
                move_overlay_physical,
                _get_dpi_scale_for_window,
            )

            cur_mode = getattr(self, "_current_hud_mode", "normal")

            w, h = (180, 56) if cur_mode == "mini" else ((560, 480) if cur_mode == "max" else (440, 180))

            overlay_win = getattr(self, "_overlay_window", None)
            scale = _get_dpi_scale_for_window(overlay_win)
            cx, cy = clamp_window_position(None, None, width=int(round(w * scale)), height=int(round(h * scale)))

            if overlay_win:

                move_overlay_physical(overlay_win, cx, cy)

                self.show_overlay()

            self.save_overlay_position(cx, cy)

            return {"success": True, "x": cx, "y": cy}

        except Exception as e:

            logger.warning(f"[RESET OVERLAY ERROR] {e}")

            return {"success": False, "error": str(e)}



    def set_dot_overlay(self, dot_overlay) -> None:
        """Attaches the native click-through DotOverlayWindow managed by HudWindow."""
        self._dot_overlay = dot_overlay
        self._sync_dot_overlay_config()

    def _sync_dot_overlay_config(self, active_game_process: Optional[str] = None) -> None:
        """Applies latest dot size, corner position, monitor, and colors when Dot Mode is active."""
        self._active_game_process_for_dot = active_game_process if active_game_process is not None else getattr(self, "_active_game_process_for_dot", "")
        hud_b = getattr(self, "_hud_bridge", None)
        if hud_b is not None and hasattr(hud_b, "set_dot_status"):
            try:
                ui_cfg = self.get_raw_config().get("ui", {}) or {}
                if isinstance(ui_cfg.get("hud_colors"), dict):
                    hud_b.set_dot_status(colors=ui_cfg["hud_colors"])
            except Exception:
                pass
        if getattr(self, "_current_hud_mode", "normal") == "dot" and getattr(self, "_overlay_visible", False):
            if hud_b is not None and hasattr(hud_b, "apply_dot_layout"):
                try:
                    hud_b.apply_dot_layout(active_game_process=self._active_game_process_for_dot)
                except Exception:
                    pass

    def preview_hud_settings(self, ui_updates: dict) -> dict:
        """Live-previews Dot size/position/monitor and custom HUD status colors across all HUD tiers."""
        try:
            from ui.dot_overlay import (
                normalize_dot_position,
                normalize_dot_size,
                sanitize_hud_colors,
            )
            updates = ui_updates if isinstance(ui_updates, dict) else {}
            hud_b = getattr(self, "_hud_bridge", None)
            if "hud_colors" in updates:
                clean_colors = sanitize_hud_colors(updates["hud_colors"])
                if hud_b is not None and hasattr(hud_b, "set_dot_status"):
                    hud_b.set_dot_status(colors=clean_colors)
                self._on_engine_event("hud_colors_preview", {"hud_colors": clean_colors})
            if getattr(self, "_current_hud_mode", "normal") == "dot" and getattr(self, "_overlay_visible", False):
                if hud_b is not None and hasattr(hud_b, "apply_dot_layout"):
                    hud_b.apply_dot_layout(
                        dot_size=normalize_dot_size(updates["dot_size"]) if "dot_size" in updates else None,
                        dot_position=normalize_dot_position(updates["dot_position"]) if "dot_position" in updates else None,
                        dot_monitor=str(updates["dot_monitor"]) if "dot_monitor" in updates else None,
                        active_game_process=getattr(self, "_active_game_process_for_dot", ""),
                    )
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _apply_game_hud_preference(
        self,
        game_id: str,
        is_running: bool,
        profile: Optional[dict] = None,
    ) -> None:
        """
        Applies per-game HUD preference ('default', 'none', 'dot', 'mini', 'normal', 'max')
        when a game launches and restores the user's prior global HUD state when the game exits.
        """
        if not hasattr(self, "_game_hud_override_state"):
            self._game_hud_override_state = None
        if not hasattr(self, "_game_hud_override_game_id"):
            self._game_hud_override_game_id = None

        prof = profile or (self.game_mgr.get_profile(game_id) if (game_id and hasattr(self, "game_mgr") and self.game_mgr) else None)
        proc_name = str((prof or {}).get("process_name") or (prof or {}).get("executable") or "") if is_running else ""
        self._sync_dot_overlay_config(active_game_process=proc_name)

        if not is_running:
            saved = self._game_hud_override_state
            if saved is not None:
                self._game_hud_override_state = None
                self._game_hud_override_game_id = None
                prev_mode = saved.get("mode", "normal")
                prev_vis = bool(saved.get("visible", False))
                self.set_mode(prev_mode, persist=False)
                if prev_vis:
                    self.show_overlay()
                else:
                    self.hide_overlay()
            return

        pref = (
            self.game_mgr.get_hud_preference(game_id)
            if (game_id and hasattr(self, "game_mgr") and self.game_mgr and hasattr(self.game_mgr, "get_hud_preference"))
            else "default"
        )

        if pref == "default":
            # If user changed preference back to 'default' mid-game, restore saved global state
            if self._game_hud_override_state is not None and self._game_hud_override_game_id == game_id:
                saved = self._game_hud_override_state
                self._game_hud_override_state = None
                self._game_hud_override_game_id = None
                self.set_mode(saved.get("mode", "normal"), persist=False)
                if saved.get("visible"):
                    self.show_overlay()
                else:
                    self.hide_overlay()
            return

        # Save user's prior global HUD state once before applying game override
        if self._game_hud_override_state is None:
            self._game_hud_override_state = {
                "mode": getattr(self, "_current_hud_mode", self.get_raw_config().get("ui", {}).get("hud_mode", "normal")),
                "visible": bool(getattr(self, "_overlay_visible", False)),
            }
        self._game_hud_override_game_id = game_id

        if pref == "none":
            self.hide_overlay()
        elif pref in ("dot", "mini", "normal", "max"):
            self._current_hud_mode = pref
            self.show_overlay()
            self.set_mode(pref, persist=False)

    @staticmethod
    def _is_webview_window_shown(win_obj) -> bool:
        """
        Returns True only if a pywebview Window is safe to call `.hide()` on without
        blocking forever inside pywebview's `@_shown_call` (`events.shown.wait()`).
        """
        if win_obj is None:
            return False
        events = getattr(win_obj, "events", None)
        shown_ev = getattr(events, "shown", None) if events is not None else None
        if shown_ev is not None and hasattr(shown_ev, "is_set"):
            try:
                return bool(shown_ev.is_set())
            except Exception:
                return False
        # Mock windows in unit tests don't have a real threading.Event on events.shown
        return True

    def _safe_hide_overlay_window(self) -> None:
        """Safely hides the WebView2 overlay window without deadlocking if it was created hidden=True."""
        overlay_win = getattr(self, "_overlay_window", None)
        if overlay_win is None:
            return
        if not self._is_webview_window_shown(overlay_win):
            return
        try:
            overlay_win.hide()
        except Exception:
            pass

    def show_overlay(self) -> dict:

        """Explicitly displays the floating overlay (circular WinForms Dot in dot mode, or WebView2 card in mini/normal/max)."""

        try:

            cur_mode = getattr(self, "_current_hud_mode", "normal")
            overlay_win = getattr(self, "_overlay_window", None)
            hud_b = getattr(self, "_hud_bridge", None)

            if cur_mode == "dot":
                self._safe_hide_overlay_window()
                self._overlay_visible = True
                if hud_b is not None and hasattr(hud_b, "set_mode"):
                    hud_b.set_mode("dot")
                return {"success": True}

            if hud_b is not None and hasattr(hud_b, "hide_dot"):
                try:
                    hud_b.hide_dot()
                except Exception:
                    pass

            if overlay_win:

                from ui.hud_window import (
                    clamp_window_position,
                    move_overlay_physical,
                    _get_overlay_physical_rect,
                    _get_dpi_scale_for_window,
                )

                w, h = (180, 56) if cur_mode == "mini" else ((560, 480) if cur_mode == "max" else (440, 180))
                rect = _get_overlay_physical_rect(overlay_win)
                if rect:
                    cur_x, cur_y, cur_w, cur_h = rect
                    cx, cy = clamp_window_position(cur_x, cur_y, width=cur_w, height=cur_h)
                    if cx != cur_x or cy != cur_y:
                        move_overlay_physical(overlay_win, cx, cy)
                else:
                    ui_cfg = self.get_raw_config().get("ui", {})
                    scale = _get_dpi_scale_for_window(overlay_win)
                    cx, cy = clamp_window_position(
                        ui_cfg.get("overlay_x"),
                        ui_cfg.get("overlay_y"),
                        width=int(round(w * scale)),
                        height=int(round(h * scale)),
                    )
                    move_overlay_physical(overlay_win, cx, cy)

                overlay_win.show()

                self._overlay_visible = True

                self.set_mode(cur_mode, persist=False)

            return {"success": True}

        except Exception as e:

            return {"success": False, "error": str(e)}



    def hide_overlay(self) -> dict:

        """Hides both the floating WebView2 overlay window and the circular Dot indicator."""

        try:

            self._safe_hide_overlay_window()

            hud_b = getattr(self, "_hud_bridge", None)
            if hud_b is not None and hasattr(hud_b, "hide_dot"):
                try:
                    hud_b.hide_dot()
                except Exception:
                    pass

            if getattr(self, "_dot_overlay", None):

                self._dot_overlay.hide()

            self._overlay_visible = False

            return {"success": True}

        except Exception as e:

            return {"success": False, "error": str(e)}



    def toggle_overlay(self) -> dict:

        """Toggles visibility of the floating overlay (including Dot mode)."""

        try:

            if getattr(self, "_overlay_visible", False):

                self.hide_overlay()

            else:

                self.show_overlay()

            return {"success": True, "visible": getattr(self, "_overlay_visible", False)}

        except Exception as e:

            return {"success": False, "error": str(e)}



    def save_overlay_position(self, x: int, y: int) -> dict:

        """Persists the floating overlay window physical coordinates to config."""

        try:

            from ui.hud_window import clamp_window_position, _get_dpi_scale_for_window

            cur_mode = getattr(self, "_current_hud_mode", "normal")

            w, h = (180, 56) if cur_mode == "mini" else ((560, 480) if cur_mode == "max" else (440, 180))
            scale = _get_dpi_scale_for_window(getattr(self, "_overlay_window", None))

            cx, cy = clamp_window_position(x, y, width=int(round(w * scale)), height=int(round(h * scale)))

            with self._config_lock:

                ui_cfg = self._config.setdefault("ui", {})

                if ui_cfg.get("overlay_x") == cx and ui_cfg.get("overlay_y") == cy:

                    return {"success": True, "x": cx, "y": cy}

                ui_cfg["overlay_x"] = cx

                ui_cfg["overlay_y"] = cy

                save_config_atomic(self._config, self._config_path)

            return {"success": True, "x": cx, "y": cy}



        except Exception as e:

            logger.warning(f"[OVERLAY POSITION SAVE ERROR] {e}")

            return {"success": False, "error": str(e)}



    def move_overlay(self, x: int, y: int) -> dict:

        """Moves the floating HUD overlay window to screen coordinates (x, y) with boundary clamping."""

        try:

            if getattr(self, "_overlay_window", None):

                from ui.hud_window import clamp_window_position

                cur_mode = getattr(self, "_current_hud_mode", "normal")

                w, h = (180, 52) if cur_mode == "mini" else ((560, 480) if cur_mode == "max" else (440, 180))

                cx, cy = clamp_window_position(x, y, width=w, height=h)

                self._overlay_window.move(cx, cy)

                return {"success": True}

        except Exception as e:

            return {"success": False, "error": str(e)}

        return {"success": False, "error": "Overlay window not initialized"}



    def set_mode(self, mode: str, persist: bool = True) -> dict:

        """Sets the HUD overlay view mode (dot, mini, normal, max) and updates the active overlay window."""

        mode = mode.lower() if mode else "normal"

        if mode not in ("dot", "mini", "normal", "max"):

            mode = "normal"

        self._current_hud_mode = mode

        if persist:
            with self._config_lock:

                self._config.setdefault("ui", {})["hud_mode"] = mode

                self._config["hud_mode"] = mode

                sync_config_schema(self._config)

                save_config_atomic(self._config, self._config_path)

        overlay_win = getattr(self, "_overlay_window", None)
        is_vis = bool(getattr(self, "_overlay_visible", False))

        if mode == "dot":
            self._safe_hide_overlay_window()
            if self._hud_bridge:
                try:
                    if is_vis:
                        self._hud_bridge.set_mode("dot")
                    elif hasattr(self._hud_bridge, "hide_dot"):
                        self._hud_bridge.hide_dot()
                except Exception as ex:
                    logger.warning(f"[HUD BRIDGE DOT ERROR] {ex}")
            self.on_hud_mode_changed("dot")
            self._on_engine_event("hud_mode_changed", {"mode": "dot"})
            return {"success": True, "mode": "dot"}

        if self._hud_bridge and hasattr(self._hud_bridge, "hide_dot"):
            try:
                self._hud_bridge.hide_dot()
            except Exception:
                pass

        if is_vis and overlay_win:
            try:
                overlay_win.show()
            except Exception:
                pass

        # Delegate resize/positioning to hud_bridge if registered, or direct to overlay_window
        if self._hud_bridge:

            try:

                self._hud_bridge.set_mode(mode)

            except Exception as ex:

                logger.warning(f"[HUD BRIDGE RESIZE ERROR] {ex}")

        elif overlay_win:

            try:

                if mode == "mini":

                    overlay_win.resize(210, 56)

                elif mode == "normal":

                    overlay_win.resize(440, 180)

                elif mode == "max":

                    overlay_win.resize(560, 480)

                from ui.hud_window import apply_hud_window_shape

                apply_hud_window_shape(overlay_win, mode)

            except Exception as ex:

                logger.warning(f"[OVERLAY RESIZE ERROR] {ex}")

        self.on_hud_mode_changed(mode)

        self._on_engine_event("hud_mode_changed", {"mode": mode})

        return {"success": True, "mode": mode}



    def cycle_hud_mode(self) -> str:

        """Cycles through HUD modes: dot -> mini -> normal -> max -> dot."""

        order = ["dot", "mini", "normal", "max"]

        cur = getattr(self, "_current_hud_mode", "normal")

        try:

            next_idx = (order.index(cur) + 1) % len(order)

            next_mode = order[next_idx]

        except ValueError:

            next_mode = "normal"

        self._overlay_visible = True
        self.set_mode(next_mode)

        return next_mode



    def on_hud_mode_changed(self, mode: str):

        """Notifies JavaScript in the floating overlay window of mode change."""

        clean_mode = str(mode or "normal").lower().strip()

        if clean_mode not in ("dot", "mini", "normal", "max"):

            clean_mode = "normal"

        self._current_hud_mode = clean_mode

        if clean_mode == "dot":
            return

        if self._is_window_ready(getattr(self, "_overlay_window", None)):

            with self._js_lock:

                try:

                    js_code = f"if (window.aetherOverlay && window.aetherOverlay.setMode) {{ window.aetherOverlay.setMode('{clean_mode}'); }}"

                    self._overlay_window.evaluate_js(js_code)

                except Exception:

                    pass



    def get_hud_mode(self) -> dict:

        """Returns the current HUD mode."""

        return {"success": True, "mode": getattr(self, "_current_hud_mode", "normal")}



    def toggle_mic_mute(self) -> dict:

        """Toggles microphone mute state by switching between Hands-Free (always_on) and Push-to-Talk (ptt)."""

        try:

            if self._engine and self._engine.audio:

                new_mode = "ptt" if self._engine.audio.mode == "always_on" else "always_on"

                if new_mode != "ptt" and (
                    getattr(self._engine, "_ptt_active", False)
                    or getattr(self._engine.audio, "ptt_active", False)
                ):
                    self._engine.set_ptt(False)

                self._engine.audio.set_mode(new_mode)
                self._config["mode"] = new_mode
                self._config.setdefault("audio", {})["mode"] = new_mode

                if hasattr(self._engine, "hotkey_manager") and self._engine.hotkey_manager:
                    self._engine.hotkey_manager.update_config(
                        self._config.get("audio", {}),
                        self._config.get("ui", {}),
                    )

                muted = (new_mode == "ptt")
                ag_name = self._config.get("api", {}).get("agent_name", "Aether").strip() or "Aether"
                if muted:
                    self._on_engine_event("status", {
                        "state": "standby",
                        "message": f"{ag_name} is ready (Push-to-Talk)."
                    })
                else:
                    wake_en = bool(self._engine.audio.config.get("wake_word_enabled", self._engine.audio.wake_word_enabled))
                    wd = getattr(self._engine.audio, "wake_detector", None)
                    if wake_en and wd and getattr(wd.state, "value", "") == "IDLE_LISTENING":
                        self._on_engine_event("status", {
                            "state": "standby",
                            "message": f"Standby — Say '{wd.wake_phrase}' to wake {ag_name}."
                        })
                    else:
                        self._on_engine_event("status", {
                            "state": "listening",
                            "message": f"{ag_name} is listening..."
                        })
                        if self._engine.is_running and not self._engine.is_on_demand_mode():
                            self._engine.request_live_session_connect(reason="Mic unmuted (Always-On)")

                self._on_engine_event("mic_muted", {"muted": muted})

                return {"success": True, "muted": muted}

            return {"success": False, "error": "Audio pipeline not active"}

        except Exception as e:

            return {"success": False, "error": str(e)}



    # =========================================================================

    # Diagnostic Logs & Latency Telemetry

    # =========================================================================



    def _on_log_record(self, log_entry: dict):

        """Pushes real-time log records to pywebview."""

        if log_entry.get("level") == "DEBUG":

            return

        self._on_engine_event("log_event", log_entry)



    def get_recent_logs(self) -> list:

        """Returns buffered recent log entries for the UI log console."""

        return get_recent_logs()



    def clear_log_console(self) -> dict:

        """Clears buffered in-memory logs."""

        clear_memory_logs()

        return {"success": True}



    def open_logs_folder(self) -> dict:

        """Opens the logs directory in Windows Explorer."""

        ok = open_logs_folder()

        return {"success": ok}



    # =========================================================================

    # Dynamic User Knowledge & Notification Preferences (Concept #3)

    # =========================================================================



    def get_user_preferences(self) -> dict:

        """Retrieves notification preferences from user memory."""

        try:

            prefs = self._engine.user_memory.get_preferences()

            return {"success": True, "preferences": prefs}

        except Exception as e:

            logger.error(f"[BRIDGE ERROR] get_user_preferences: {e}")

            return {"success": False, "error": str(e), "preferences": {}}



    def update_user_preference(self, category: str, enabled: bool, lead_time_days: Optional[int] = None) -> dict:

        """Updates notification preference category toggle and lead time."""

        try:

            self._engine.user_memory.update_preference(category, enabled, lead_time_days)

            return {"success": True}

        except Exception as e:

            logger.error(f"[BRIDGE ERROR] update_user_preference: {e}")

            return {"success": False, "error": str(e)}



    def get_user_facts(self, category: Optional[str] = None) -> dict:

        """Retrieves stored user facts optionally filtered by category."""

        try:

            facts = self._engine.user_memory.get_facts(category=category if category else None)

            return {"success": True, "facts": facts}

        except Exception as e:

            logger.error(f"[BRIDGE ERROR] get_user_facts: {e}")

            return {"success": False, "error": str(e), "facts": []}



    def add_user_fact(self, category: str, key: str, value: str, data_type: str = "string") -> dict:

        """Adds or updates a user fact in the profile database."""

        try:

            res = self._engine.user_memory.remember_fact(category, key, value, data_type)

            return {"success": True, "fact": res}

        except Exception as e:

            logger.error(f"[BRIDGE ERROR] add_user_fact: {e}")

            return {"success": False, "error": str(e)}



    def delete_user_fact(self, fact_id: int) -> dict:

        """Deletes a fact from the user profile database by ID."""

        try:

            deleted = self._engine.user_memory.delete_fact_by_id(fact_id)

            return {"success": True, "deleted": deleted}

        except Exception as e:

            logger.error(f"[BRIDGE ERROR] delete_user_fact: {e}")

            return {"success": False, "error": str(e)}



    def get_user_name(self) -> dict:

        """Retrieves the user's preferred name from memory and config."""

        try:

            name = self._engine.user_memory.get_user_name(default="")

            if not name:

                name = self._config.get("user", {}).get("name", "")

            return {"success": True, "name": name}

        except Exception as e:

            logger.error(f"[BRIDGE ERROR] get_user_name: {e}")

            return {"success": False, "error": str(e), "name": ""}



    def set_user_name(self, name: str) -> dict:

        """Sets the user's preferred name(s) / callsign(s) in memory and configuration."""

        try:

            raw = str(name).strip()

            parts = [p.strip() for p in raw.split(";") if p.strip()]

            clean = "; ".join(parts) if parts else raw

            self._engine.user_memory.set_user_name(clean)

            with self._config_lock:

                self._config.setdefault("user", {})["name"] = clean

                save_config_atomic(self._config, self._config_path)

            if hasattr(self._engine, "update_user_identity_directive"):

                self._engine.update_user_identity_directive()

            if hasattr(self._engine, "invalidate_live_session"):

                self._engine.invalidate_live_session(reason="User Callsign Updated")

            self._on_engine_event("config_updated", self._config)

            return {"success": True, "name": clean}

        except Exception as e:

            logger.error(f"[BRIDGE ERROR] set_user_name: {e}")

            return {"success": False, "error": str(e)}



    def get_callsign_frequency(self) -> dict:

        """Retrieves the user's preferred callsign frequency ('never', 'seldom', 'often', 'always')."""

        try:

            freq = self._engine.user_memory.get_callsign_frequency(default="")

            if not freq:

                with self._config_lock:

                    freq = self._config.get("user", {}).get("callsign_frequency", "often")

            freq = str(freq).strip().lower()

            if freq not in ("never", "seldom", "often", "always"):

                freq = "often"

            levels = {"never": 0, "seldom": 1, "often": 2, "always": 3}

            return {"success": True, "frequency": freq, "level": levels.get(freq, 2)}

        except Exception as e:

            logger.error(f"[BRIDGE ERROR] get_callsign_frequency: {e}")

            return {"success": False, "error": str(e), "frequency": "often", "level": 2}



    def set_callsign_frequency(self, frequency: str) -> dict:

        """Sets the user's preferred callsign frequency and updates the engine prompt directive."""

        try:

            clean = str(frequency).strip().lower()

            if clean not in ("never", "seldom", "often", "always"):

                clean = "often"

            self._engine.user_memory.set_callsign_frequency(clean)

            with self._config_lock:

                self._config.setdefault("user", {})["callsign_frequency"] = clean

                save_config_atomic(self._config, self._config_path)

            if hasattr(self._engine, "update_user_identity_directive"):

                self._engine.update_user_identity_directive()

            if hasattr(self._engine, "invalidate_live_session"):

                self._engine.invalidate_live_session(reason="User Callsign Updated")

            self._on_engine_event("config_updated", self._config)

            levels = {"never": 0, "seldom": 1, "often": 2, "always": 3}

            return {"success": True, "frequency": clean, "level": levels.get(clean, 2)}



        except Exception as e:

            logger.error(f"[BRIDGE ERROR] set_callsign_frequency: {e}")

            return {"success": False, "error": str(e)}



    def get_dictionary_terms(self) -> dict:

        """Retrieves all custom dictionary / lexicon terms."""

        try:

            terms = self._engine.user_memory.get_all_dictionary_terms()

            return {"success": True, "terms": terms}

        except Exception as e:

            logger.error(f"[BRIDGE ERROR] get_dictionary_terms: {e}")

            return {"success": False, "error": str(e), "terms": []}



    def add_dictionary_term(self, term: str, phonetic_guide: str, category: str = "name") -> dict:

        """Adds or updates a custom lexicon entry and refreshes session prompt context."""

        try:

            res = self._engine.user_memory.add_dictionary_term(term, phonetic_guide, category)

            if hasattr(self._engine, "update_lexicon_context"):

                self._engine.update_lexicon_context()

            return {"success": True, "result": res}

        except Exception as e:

            logger.error(f"[BRIDGE ERROR] add_dictionary_term: {e}")

            return {"success": False, "error": str(e)}



    def remove_dictionary_term(self, term: str) -> dict:

        """Removes a custom lexicon entry and refreshes session prompt context."""

        try:

            res = self._engine.user_memory.remove_dictionary_term(term)

            if hasattr(self._engine, "update_lexicon_context"):

                self._engine.update_lexicon_context()

            return {"success": True, "result": res}

        except Exception as e:

            logger.error(f"[BRIDGE ERROR] remove_dictionary_term: {e}")

            return {"success": False, "error": str(e)}



    def get_stored_sessions(self, query: Optional[str] = None) -> dict:

        """

        Lists stored conversation sessions with manifest card metadata.

        Optionally filters by query keyword.

        """

        try:

            from core.manifest_indexer import list_stored_sessions

            sessions = list_stored_sessions()

            if query and query.strip():

                q = query.strip().lower()

                sessions = [

                    s for s in sessions

                    if q in s.get("session_id", "").lower()

                    or q in s.get("preview", "").lower()

                    or any(q in str(t).lower() for t in s.get("topics", []))

                    or any(q in str(a).lower() for a in s.get("actions", []))

                    or any(q in str(e).lower() for e in s.get("entities", []))

                ]

            return {"success": True, "sessions": sessions, "total": len(sessions)}

        except Exception as e:

            logger.error(f"[BRIDGE ERROR] get_stored_sessions: {e}")

            return {"success": False, "error": str(e), "sessions": []}



    def get_session_transcript(self, session_id: str) -> dict:

        """

        Retrieves full turn transcript and manifest card for a given session.

        """

        try:

            from core.manifest_indexer import get_session_details

            res = get_session_details(session_id)

            if res.get("status") == "success":

                return {"success": True, "session": res}

            else:

                return {"success": False, "error": res.get("message", "Failed to retrieve session")}

        except Exception as e:

            logger.error(f"[BRIDGE ERROR] get_session_transcript: {e}")

            return {"success": False, "error": str(e)}



    def delete_stored_session(self, session_id: str) -> dict:

        """

        Deletes a specific session transcript file and its SQLite index entry.

        """

        try:

            from core.manifest_indexer import delete_session_and_transcript

            res = delete_session_and_transcript(session_id)

            if res.get("status") == "success":

                return {"success": True, "result": res}

            else:

                return {"success": False, "error": res.get("message", "Session not found")}

        except Exception as e:

            logger.error(f"[BRIDGE ERROR] delete_stored_session: {e}")

            return {"success": False, "error": str(e)}



    def clear_all_stored_sessions(self) -> dict:

        """

        Deletes all stored session transcripts and clears the manifest index.

        """

        try:

            from core.manifest_indexer import delete_all_stored_sessions

            res = delete_all_stored_sessions()

            return {"success": True, "result": res}

        except Exception as e:

            logger.error(f"[BRIDGE ERROR] clear_all_stored_sessions: {e}")

            return {"success": False, "error": str(e)}

    # --- DOSSIER VIEWER BRIDGES ---

    def get_dossiers(self, query: Optional[str] = None) -> dict:
        """Lists saved Markdown dossiers in data/dossiers/."""
        try:
            from tools.dossier_exporter import list_dossiers
            dossiers = list_dossiers(query=query)
            return {"success": True, "dossiers": dossiers, "total": len(dossiers)}
        except Exception as e:
            logger.error(f"[BRIDGE ERROR] get_dossiers: {e}")
            return {"success": False, "error": str(e), "dossiers": []}

    def get_dossier_content(self, filename: str) -> dict:
        """Retrieves metadata, raw Markdown, and Word/Docs-styled HTML for a dossier."""
        try:
            from tools.dossier_exporter import get_dossier_details
            res = get_dossier_details(filename)
            if res.get("status") == "success":
                return {"success": True, "dossier": res["dossier"]}
            return {"success": False, "error": res.get("message", "Dossier not found.")}
        except Exception as e:
            logger.error(f"[BRIDGE ERROR] get_dossier_content: {e}")
            return {"success": False, "error": str(e)}

    def delete_dossier(self, filename: str) -> dict:
        """Deletes a saved dossier file from data/dossiers/."""
        try:
            from tools.dossier_exporter import delete_dossier_file
            res = delete_dossier_file(filename)
            if res.get("status") == "success":
                return {"success": True, "result": res}
            return {"success": False, "error": res.get("message", "Dossier not found.")}
        except Exception as e:
            logger.error(f"[BRIDGE ERROR] delete_dossier: {e}")
            return {"success": False, "error": str(e)}

    def copy_dossier_rich_html(self, filename: str) -> dict:
        """Copies a dossier as rich CF_HTML + plain text for pasting into Word or Google Docs."""
        try:
            from tools.dossier_exporter import get_dossier_details
            from tools.clipboard import copy_markdown_as_rich_html, set_clipboard_text
            res = get_dossier_details(filename)
            if res.get("status") != "success":
                return {"success": False, "error": res.get("message", "Dossier not found.")}
            md = res["dossier"].get("content_markdown", "")
            ok = copy_markdown_as_rich_html(md)
            if not ok:
                ok = set_clipboard_text(md)
            return {"success": bool(ok)}
        except Exception as e:
            logger.error(f"[BRIDGE ERROR] copy_dossier_rich_html: {e}")
            return {"success": False, "error": str(e)}

    def open_dossier_file(self, filename: str) -> dict:
        """Opens a dossier file in the OS default viewer."""
        try:
            from tools.dossier_exporter import _safe_dossier_path
            fpath = _safe_dossier_path(filename)
            if not fpath or not os.path.isfile(fpath):
                return {"success": False, "error": "Dossier file not found."}
            os.startfile(fpath)
            return {"success": True}
        except Exception as e:
            logger.error(f"[BRIDGE ERROR] open_dossier_file: {e}")
            return {"success": False, "error": str(e)}

    def save_dossier_theme(self, theme: str) -> dict:
        """Persists the user's preferred Dossier Viewer reading theme ('dark' or 'paper')."""
        try:
            clean_theme = "dark" if str(theme).strip().lower() == "dark" else "paper"
            with self._config_lock:
                ui_cfg = self._config.setdefault("ui", {})
                if ui_cfg.get("dossier_theme") == clean_theme:
                    return {"success": True, "theme": clean_theme}
                ui_cfg["dossier_theme"] = clean_theme
                save_config_atomic(self._config, self._config_path)
            return {"success": True, "theme": clean_theme}
        except Exception as e:
            logger.warning(f"[DOSSIER THEME SAVE ERROR] {e}")
            return {"success": False, "error": str(e)}

    # --- GAMES SUBSYSTEM BRIDGES ---



    def get_game_profiles(self) -> dict:

        """Returns all game profiles, active profile ID, and live process detection status."""

        profiles_data = self.game_mgr.data

        running_processes = set()

        try:

            for p in psutil.process_iter(['name']):

                try:

                    name = p.name()

                    if name:

                        running_processes.add(name.lower())

                except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):

                    pass

        except Exception:

            pass



        # Annotate whether each game's process is currently detected

        status_map = {}

        for gid, pdata in profiles_data.get("profiles", {}).items():

            proc_name = (pdata.get("process_name") or pdata.get("executable") or "").lower()
            proc_name = os.path.basename(proc_name)

            status_map[gid] = proc_name in running_processes if proc_name else False



        sorted_profiles = dict(sorted(

            profiles_data.get("profiles", {}).items(),

            key=lambda kv: (kv[1].get("display_name") or kv[0]).lower()

        ))



        return {

            "active_profile": profiles_data.get("active_profile", ""),

            "profiles": sorted_profiles,

            "running_status": status_map,

            "game_mode_enabled": self.game_mgr.game_mode_enabled,

            "is_game_mode_active": self.game_mgr.is_game_mode_active(),

            "user_override": self.game_mgr.user_override_game_mode

        }



    def add_game_profile(self, display_name: str, process_name: str = "") -> dict:

        """Creates a new game profile and switches to it if no active game is set."""

        if not display_name or not display_name.strip():

            return {"success": False, "error": "Game name cannot be empty."}

        res = self.game_mgr.add_game(display_name=display_name.strip(), process_name=process_name.strip())

        if res.get("success"):

            if hasattr(self, "program_mgr") and self.program_mgr:

                try:

                    self.program_mgr.sync_from_games(game_profiles={res["game_id"]: res.get("profile", {})})

                except Exception as e:

                    logger.warning(f"[GUI_BRIDGE] Auto-sync to programs failed after add_game_profile: {e}")

        return res



    def delete_game_profile(self, game_id: str) -> dict:

        """Deletes a game profile from memory and disk, and removes from program whitelist."""

        prof = self.game_mgr.get_profile(game_id)

        dname = (prof.get("display_name") or game_id).strip() if prof else game_id

        res = self.game_mgr.delete_game(game_id)

        if res.get("success"):

            if hasattr(self, "program_mgr") and self.program_mgr:

                try:

                    dname_norm = re.sub(r'[^a-zA-Z0-9]+', '', dname.lower())

                    to_del = []

                    for pid, p in self.program_mgr.data.get("programs", {}).items():

                        p_norm = re.sub(r'[^a-zA-Z0-9]+', '', p.get("name", "").lower())

                        if pid == game_id or (dname_norm and (p_norm == dname_norm or pid == dname_norm)):

                            to_del.append(pid)

                    for pid in to_del:

                        self.program_mgr.delete_program(pid)

                except Exception as e:

                    logger.debug(f"[GUI_BRIDGE] Auto-delete from programs failed: {e}")

        return res



    def set_active_game_profile(self, game_id: str) -> dict:

        """Switches the active game profile in memory and disk."""

        if not game_id:

            self.game_mgr.set_active_profile("")

            return {"success": True, "active_profile": "", "game_mode_enabled": self.game_mgr.game_mode_enabled}

        success = self.game_mgr.set_active_profile(game_id)

        if success:

            if self.game_mgr.user_override_game_mode is not False:

                self.game_mgr.set_game_mode(True)

            return {

                "success": True,

                "active_profile": game_id,

                "game_mode_enabled": self.game_mgr.game_mode_enabled

            }

        return {"success": False, "error": f"Unknown game profile: {game_id}"}



    def get_game_mode(self) -> dict:

        """Returns the current Game Mode status and active profile."""

        active_id = self.game_mgr.data.get("active_profile", "")

        prof = self.game_mgr.get_profile(active_id) if active_id else None

        is_running = self.game_mgr.is_game_running(active_id) if active_id else False

        is_active = self.game_mgr.is_game_mode_active()

        return {

            "game_mode_enabled": self.game_mgr.game_mode_enabled,

            "is_active": is_active,

            "is_running": is_running,

            "active_profile": active_id,

            "display_name": prof.get("display_name", active_id) if prof else "",

            "fps_monitoring": bool(prof.get("fps_monitoring", True)) if prof else True,

            "telemetry_enabled": bool(prof.get("telemetry_enabled", False)) if prof else False,

            "user_override": self.game_mgr.user_override_game_mode

        }

    def get_hud_telemetry(self) -> dict:
        """Returns real-time HUD overlay state including running game status and live FPS."""
        active_id = self.game_mgr.data.get("active_profile", "")
        detected_id = getattr(self.game_mgr, "_last_detected_game", "UNSET")
        if detected_id == "UNSET" or (not detected_id and self.game_mgr.game_mode_enabled):
            is_running = self.game_mgr.is_game_running(active_id) if active_id else False
            if not is_running and hasattr(self.game_mgr, "detect_running_game"):
                running_gid = self.game_mgr.detect_running_game()
                if running_gid:
                    active_id = running_gid
                    is_running = True
            if is_running and active_id:
                self.game_mgr._last_detected_game = active_id
        else:
            if detected_id:
                active_id = detected_id
                is_running = True
            else:
                is_running = False

        prof = self.game_mgr.get_profile(active_id) if active_id else None
        fps_monitoring = bool(prof.get("fps_monitoring", True)) if prof else False
        display_name = prof.get("display_name", active_id) if prof else ""
        fps_val = None
        fps_1pct = None

        engine = getattr(self, "_engine", None) or getattr(self, "engine", None)
        hw_svc = getattr(engine, "hardware_service", None) if engine else None
        if hw_svc and is_running and fps_monitoring and prof:
            raw_exe = prof.get("executable") or prof.get("process_name") or f"{active_id}.exe"
            exe_name = os.path.basename(raw_exe)
            if not getattr(hw_svc, "_monitored_process_name", None) and hasattr(hw_svc, "start_fps_monitoring_sync"):
                hw_svc.start_fps_monitoring_sync(exe_name)
            if hasattr(hw_svc, "get_fps_metrics"):
                metrics = hw_svc.get_fps_metrics()
                fps_val = metrics.get("fps_avg") or metrics.get("fps_current")
                fps_1pct = metrics.get("fps_1pct_low")

        return {
            "game_mode_enabled": self.game_mgr.game_mode_enabled,
            "enabled": self.game_mgr.game_mode_enabled,
            "is_active": self.game_mgr.is_game_mode_active(),
            "is_running": is_running,
            "active_profile": active_id,
            "game_id": active_id,
            "display_name": display_name,
            "fps_monitoring": fps_monitoring,
            "fps": fps_val,
            "fps_1pct_low": fps_1pct
        }



    def set_game_mode(self, enabled: bool) -> dict:

        """Explicitly enables or disables Game Mode."""

        res = self.game_mgr.set_game_mode(enabled, user_explicit=True)

        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "_reset_context_flag"):

            self._engine._reset_context_flag = True

        elif hasattr(self, "engine") and self.engine and hasattr(self.engine, "_reset_context_flag"):

            self.engine._reset_context_flag = True

        return res



    def toggle_game_mode(self) -> dict:

        """Toggles Game Mode state."""

        res = self.game_mgr.set_game_mode(not self.game_mgr.game_mode_enabled, user_explicit=True)

        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "_reset_context_flag"):

            self._engine._reset_context_flag = True

        elif hasattr(self, "engine") and self.engine and hasattr(self.engine, "_reset_context_flag"):

            self.engine._reset_context_flag = True

        return res



    def get_agent_name(self) -> dict:

        """Returns the configured assistant agent name."""

        api_cfg = self._config.get("api", {}) if hasattr(self, "_config") and self._config else {}

        return {"agent_name": api_cfg.get("agent_name", "Aether")}



    def sync_game_bindings(self, game_id: str) -> dict:

        """Scans local directories or retrieves web defaults via Hydrogen - Low if files are absent."""

        client = getattr(self.engine, "client", None) or getattr(self._engine, "client", None)
        model = "gemini-4-hydrogen:low"
        res = self.game_mgr.sync_game_binds_with_fallback(game_id, client=client, model_endpoint=model)
        if res.get("success"):
            eng = getattr(self, "_engine", None) or getattr(self, "engine", None)
            if eng is not None:
                eng._reset_context_flag = True
                if hasattr(eng, "invalidate_live_session"):
                    eng.invalidate_live_session(reason="Game Keybinds Synced")
        return res



    def test_game_macro(self, phrase: str) -> dict:

        """Tests execution of a keybind or visual/keystroke macro."""

        eng = getattr(self, "_engine", None) or getattr(self, "engine", None)
        client = getattr(eng, "genai_client", None) or getattr(eng, "client", None) if eng else None
        return self.game_mgr.trigger_action(phrase, vision_client=client)

    def get_game_macros(self, game_id: str) -> dict:
        """Returns merged module-default and user-created custom macros (sequences) for a game."""
        profile = self.game_mgr.get_profile(game_id)
        if not profile:
            return {"success": False, "error": "Profile not found", "macros": {}}

        merged: Dict[str, Any] = {}
        try:
            from core.game_modules import get_module_for_game
            mod = get_module_for_game(game_id, profile=profile)
            if isinstance(mod, dict) and isinstance(mod.get("sequences"), dict):
                for sname, sdef in mod["sequences"].items():
                    if isinstance(sdef, dict):
                        entry = dict(sdef)
                        entry.setdefault("source", "module_default")
                        merged[str(sname).lower().strip()] = entry
        except Exception:
            pass

        disabled = set(str(x).lower().strip() for x in (profile.get("disabled_module_sequences") or []))
        for d_name in disabled:
            merged.pop(d_name, None)

        if isinstance(profile.get("sequences"), dict):
            for sname, sdef in profile["sequences"].items():
                if isinstance(sdef, dict):
                    entry = dict(sdef)
                    entry.setdefault("source", "user_macro")
                    merged[str(sname).lower().strip()] = entry

        return {"success": True, "game_id": game_id, "macros": merged}

    def save_game_macro(
        self,
        game_id: str,
        name: str,
        steps: Any,
        description: str = "",
        condition: str = "",
        ack: str = "",
        aliases: Optional[list] = None,
        old_name: str = "",
    ) -> dict:
        """Creates or updates a user-generated visual/keystroke macro (sequence) in a game profile."""
        profile = self.game_mgr.get_profile(game_id)
        if not profile:
            return {"success": False, "error": "Profile not found"}

        clean_name = str(name or "").lower().strip()
        if not clean_name:
            return {"success": False, "error": "Macro name cannot be empty."}

        parsed_steps = steps
        if isinstance(steps, str):
            try:
                parsed_steps = json.loads(steps)
            except Exception as e:
                return {"success": False, "error": f"Invalid JSON in steps: {e}"}
        if not isinstance(parsed_steps, list) or len(parsed_steps) == 0:
            return {"success": False, "error": "Macro must include at least one step."}

        from core.game_macro_runner import normalize_and_validate_macro_steps
        norm_steps, val_errors = normalize_and_validate_macro_steps(parsed_steps, profile=profile)
        if val_errors:
            return {"success": False, "error": " | ".join(val_errors)}
        parsed_steps = norm_steps

        clean_aliases = []
        if isinstance(aliases, str):
            clean_aliases = [a.lower().strip() for a in aliases.split(",") if a.strip()]
        elif isinstance(aliases, list):
            clean_aliases = [str(a).lower().strip() for a in aliases if str(a).strip()]

        entry: Dict[str, Any] = {
            "description": str(description or clean_name.title()).strip(),
            "steps": parsed_steps,
            "source": "user_macro",
        }
        if str(condition or "").strip():
            entry["condition"] = str(condition).strip()
        if str(ack or "").strip():
            entry["ack"] = str(ack).strip()
        if clean_aliases:
            entry["aliases"] = clean_aliases

        seqs = profile.setdefault("sequences", {})
        clean_old = str(old_name or "").lower().strip()
        if clean_old and clean_old != clean_name and clean_old in seqs:
            del seqs[clean_old]
        # Re-enable if previously disabled as a module default
        disabled = [x for x in (profile.get("disabled_module_sequences") or []) if str(x).lower().strip() != clean_name]
        if "disabled_module_sequences" in profile:
            profile["disabled_module_sequences"] = disabled

        seqs[clean_name] = entry
        self.game_mgr._save_profiles(self.game_mgr.data, target_game_id=game_id)

        eng = getattr(self, "_engine", None) or getattr(self, "engine", None)
        if eng is not None:
            eng._reset_context_flag = True
            if hasattr(eng, "invalidate_live_session"):
                eng.invalidate_live_session(reason="Game Macro Saved")

        return self.get_game_macros(game_id)

    def delete_game_macro(self, game_id: str, name: str) -> dict:
        """Deletes a user macro (or hides a bundled module-default macro) for a game profile."""
        profile = self.game_mgr.get_profile(game_id)
        if not profile:
            return {"success": False, "error": "Profile not found"}

        clean_name = str(name or "").lower().strip()
        seqs = profile.get("sequences") or {}
        removed = False
        if clean_name in seqs:
            del seqs[clean_name]
            removed = True

        # Check if a bundled module default has the same name; if so, record in disabled_module_sequences
        try:
            from core.game_modules import get_module_for_game
            mod = get_module_for_game(game_id, profile=profile)
            mod_seqs = {str(k).lower().strip() for k in ((mod or {}).get("sequences") or {}).keys()}
            if clean_name in mod_seqs:
                disabled = list(profile.get("disabled_module_sequences") or [])
                if clean_name not in disabled:
                    disabled.append(clean_name)
                profile["disabled_module_sequences"] = disabled
                removed = True
        except Exception:
            pass

        if not removed:
            return {"success": False, "error": f"Macro '{clean_name}' not found."}

        self.game_mgr._save_profiles(self.game_mgr.data, target_game_id=game_id)
        eng = getattr(self, "_engine", None) or getattr(self, "engine", None)
        if eng is not None:
            eng._reset_context_flag = True
            if hasattr(eng, "invalidate_live_session"):
                eng.invalidate_live_session(reason="Game Macro Deleted")

        return self.get_game_macros(game_id)

    def generate_game_macro_from_text(
        self,
        game_id: str,
        instruction: str,
        include_screenshot: bool = False,
    ) -> dict:
        """
        Compiles a plain-English user request into a structured declarative macro JSON via Gemini Hydrogen.
        When `include_screenshot=True` and the game is running, captures a live frame of the game monitor
        (briefly focusing the game window only if needed on a single-monitor setup, then restoring Aether)
        so Gemini can see exact visible UI labels, menus, and buttons.
        """
        raw_inst = str(instruction or "").strip()
        if not raw_inst:
            return {"success": False, "error": "Please describe what the macro should do."}

        profile = self.game_mgr.get_profile(game_id) if game_id else {}
        game_name = (profile or {}).get("display_name") or game_id or "PC Game"
        target_proc = (profile or {}).get("process_name") or (profile or {}).get("executable") or ""
        sample_binds = list(((profile or {}).get("keybinds") or {}).keys())[:60]

        eng = getattr(self, "_engine", None) or getattr(self, "engine", None)
        client = getattr(eng, "genai_client", None) or getattr(eng, "client", None) if eng else None
        if not client:
            return {"success": False, "error": "Gemini client unavailable to generate macro steps."}

        screenshot_bytes: Optional[bytes] = None
        if include_screenshot and target_proc:
            try:
                import time as _time
                from core.game_nav import UniversalGameNavigator
                prev_hwnd = None
                switched_focus = False
                try:
                    import win32gui
                    from tools.os_controls import find_window_hwnd, focus_window
                    prev_hwnd = win32gui.GetForegroundWindow()
                    game_hwnd = find_window_hwnd(target_proc)
                    if game_hwnd and win32gui.IsWindow(game_hwnd) and game_hwnd != prev_hwnd:
                        # Only switch focus if Aether & game overlap on the same monitor
                        same_monitor = True
                        try:
                            import mss
                            gl, gt, gr, gb = win32gui.GetWindowRect(game_hwnd)
                            al, at, ar, ab = win32gui.GetWindowRect(prev_hwnd) if prev_hwnd else (0, 0, 0, 0)
                            gcx, gcy = (gl + gr) // 2, (gt + gb) // 2
                            acx, acy = (al + ar) // 2, (at + ab) // 2
                            with mss.mss() as sct:
                                g_mon = next((i for i, m in enumerate(sct.monitors[1:]) if m["left"] <= gcx < m["left"] + m["width"] and m["top"] <= gcy < m["top"] + m["height"]), 0)
                                a_mon = next((i for i, m in enumerate(sct.monitors[1:]) if m["left"] <= acx < m["left"] + m["width"] and m["top"] <= acy < m["top"] + m["height"]), 0)
                                same_monitor = (g_mon == a_mon)
                        except Exception:
                            same_monitor = True
                        if same_monitor:
                            focus_window(game_hwnd)
                            switched_focus = True
                            _time.sleep(0.35)
                except Exception:
                    pass

                try:
                    nav = UniversalGameNavigator(target_process=target_proc, game_name=game_name)
                    img_bytes, _, _ = nav.capture_viewport(max_dim=1280)
                    if img_bytes:
                        screenshot_bytes = img_bytes
                finally:
                    if switched_focus and prev_hwnd:
                        try:
                            from tools.os_controls import focus_window
                            focus_window(prev_hwnd)
                        except Exception:
                            pass
            except Exception as e:
                print(f"[DEBUG] [MACRO_GEN] Live game screenshot skipped: {e}")

        try:
            from google.genai import types
            from core.model_discovery import resolve_model_profile, build_thinking_config

            base_model, level = resolve_model_profile("gemini-4-hydrogen:low", default_model="gemini-4-hydrogen", default_level="low")
            thinking_cfg = build_thinking_config(base_model, thinking_level=level or "low")

            vision_hint = (
                "A live screenshot of the game is attached. Inspect the exact visible UI labels, menus, "
                "weapon wheels, and button text to craft precise `vision_click` target descriptions.\n"
                if screenshot_bytes
                else ""
            )
            prompt = (
                f'You are a macro compiler for the PC game "{game_name}".\n'
                f'{vision_hint}'
                f'Available synced keybind phrases include: {sample_binds}\n'
                f'Convert this user instruction into a clean Aether declarative macro JSON object:\n'
                f'"{raw_inst}"\n\n'
                f'Supported step actions:\n'
                f'1. {{"action": "vision_click", "target": "UI button/label description", "button": "left", "hold_ms": 100, "abort_if_not_found": true, "delay_ms": 200}}\n'
                f'2. {{"action": "press", "key": "x", "modifiers": [], "hold_ms": 80, "repeat": 5, "interval_ms": 20, "delay_ms": 100}}\n'
                f'3. {{"action": "trigger_bind", "phrase": "<synced keybind phrase>", "repeat": 1, "interval_ms": 50, "delay_ms": 100}}\n'
                f'4. {{"action": "wait", "delay_ms": 500}}\n'
                f'5. {{"action": "paste", "text": "...", "delay_ms": 150}} or {{"action": "type", "text": "...", "delay_ms": 150}}\n'
                f'6. {{"action": "click", "button": "left", "delay_ms": 100}}\n\n'
                f'Return ONLY valid JSON with keys:\n'
                f'{{\n'
                f'  "name": "short lowercase trigger name",\n'
                f'  "aliases": ["optional natural alternate phrases"],\n'
                f'  "description": "plain-English intent describing when Aether should trigger this macro",\n'
                f'  "condition": "optional telemetry condition like state.docked or empty string",\n'
                f'  "ack": "short in-character spoken confirmation",\n'
                f'  "steps": [ ... ]\n'
                f'}}'
            )
            contents: Any = (
                [types.Part.from_bytes(data=screenshot_bytes, mime_type="image/jpeg"), prompt]
                if screenshot_bytes
                else prompt
            )
            res = client.models.generate_content(
                model=base_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                    thinking_config=thinking_cfg,
                ),
            )
            raw_txt = (getattr(res, "text", None) or "").strip()
            if raw_txt.startswith("```"):
                raw_txt = re.sub(r"^```(?:json)?\s*", "", raw_txt)
                raw_txt = re.sub(r"\s*```$", "", raw_txt)
            parsed = json.loads(raw_txt) if raw_txt else {}
            if not isinstance(parsed, dict) or not isinstance(parsed.get("steps"), list):
                return {"success": False, "error": "Model returned an invalid macro structure."}
            return {"success": True, "macro": parsed, "used_screenshot": bool(screenshot_bytes)}
        except Exception as e:
            return {"success": False, "error": f"Macro generation failed: {e}"}



    def save_game_keybind(self, game_id: str, phrase: str, key: str, modifiers: list, description: str) -> dict:

        """Adds or updates a voice-to-scancode macro in a game profile."""

        profile = self.game_mgr.get_profile(game_id)

        if not profile:

            return {"success": False, "error": "Profile not found"}



        clean_phrase = phrase.lower().strip()

        profile.setdefault("keybinds", {})[clean_phrase] = {

            "key": key.lower().strip(),

            "modifiers": [m.lower().strip() for m in (modifiers or []) if isinstance(m, str) and m.strip()],

            "description": description.strip() or phrase.title()

        }

        self.game_mgr._save_profiles(self.game_mgr.data, target_game_id=game_id)
        eng = getattr(self, "_engine", None) or getattr(self, "engine", None)
        if eng is not None:
            eng._reset_context_flag = True
            if hasattr(eng, "invalidate_live_session"):
                eng.invalidate_live_session(reason="Game Keybind Saved")

        return {"success": True, "keybinds": profile["keybinds"]}



    def delete_game_keybind(self, game_id: str, phrase: str) -> dict:

        """Removes a voice macro from a game profile."""

        profile = self.game_mgr.get_profile(game_id)

        if profile and phrase in profile.get("keybinds", {}):

            del profile["keybinds"][phrase]

            self.game_mgr._save_profiles(self.game_mgr.data, target_game_id=game_id)
            eng = getattr(self, "_engine", None) or getattr(self, "engine", None)
            if eng is not None:
                eng._reset_context_flag = True
                if hasattr(eng, "invalidate_live_session"):
                    eng.invalidate_live_session(reason="Game Keybind Deleted")

            return {"success": True, "keybinds": profile["keybinds"]}

        return {"success": False, "error": "Binding not found"}

    def get_game_callsign(self, game_id: Optional[str] = None) -> dict:
        """Returns the configured player callsign(s) and multi-character mode for a game profile."""
        gid = game_id or self.game_mgr.data.get("active_profile", "")
        callsign = self.game_mgr.get_callsign(gid) if gid else ""
        multi = self.game_mgr.get_multiple_main_characters(gid) if gid else False
        return {"success": True, "game_id": gid, "callsign": callsign, "multiple_main_characters": multi}

    def set_game_callsign(self, game_id: str, callsign: str) -> dict:
        """Updates the player callsign(s) for a game profile (supports ';' separated list or blank)."""
        ok = self.game_mgr.set_callsign(game_id, callsign)
        if not ok:
            return {"success": False, "error": f"Game profile '{game_id}' not found."}
        saved = self.game_mgr.get_callsign(game_id)
        eng = getattr(self, "_engine", None) or getattr(self, "engine", None)
        if eng is not None:
            eng._reset_context_flag = True
            if hasattr(eng, "invalidate_live_session"):
                eng.invalidate_live_session(reason=f"Game Callsign Updated ({game_id})")
        return {"success": True, "game_id": game_id, "callsign": saved}

    def get_game_copilot_vibe(self, game_id: Optional[str] = None) -> dict:
        """Returns the co-pilot vibe preset and custom free-text personality for a game profile."""
        gid = game_id or self.game_mgr.data.get("active_profile", "")
        if not gid:
            return {"success": False, "error": "No active game profile."}
        info = self.game_mgr.get_copilot_vibe(gid)
        return {"success": True, "game_id": gid, **info}

    def set_game_copilot_vibe(self, game_id: str, vibe: str = "witty", custom_personality: Optional[str] = None) -> dict:
        """Updates the per-game co-pilot vibe preset and unrestricted free-text personality."""
        res = self.game_mgr.set_copilot_vibe(game_id, vibe=vibe, custom_personality=custom_personality)
        if res.get("success"):
            eng = getattr(self, "_engine", None) or getattr(self, "engine", None)
            if eng is not None:
                eng._reset_context_flag = True
                if hasattr(eng, "invalidate_live_session"):
                    eng.invalidate_live_session(reason="Co-Pilot Personality Updated")
        return res

    def set_game_multiple_characters(self, game_id: str, enabled: Any) -> dict:
        """Toggles vision-matched multiple main character callsign mode for a game profile."""
        if isinstance(enabled, str):
            enabled_bool = enabled.lower() not in ("false", "0", "off", "")
        else:
            enabled_bool = bool(enabled)
        ok = self.game_mgr.set_multiple_main_characters(game_id, enabled_bool)
        if not ok:
            return {"success": False, "error": f"Game profile '{game_id}' not found."}
        eng = getattr(self, "_engine", None) or getattr(self, "engine", None)
        if eng is not None:
            eng._reset_context_flag = True
            if hasattr(eng, "invalidate_live_session"):
                eng.invalidate_live_session(reason=f"Game Multiple Main Characters Updated ({game_id})")
        return {"success": True, "game_id": game_id, "multiple_main_characters": enabled_bool}



    def update_game_scratchpad(self, game_id: str, scratchpad_text: str) -> dict:

        """Saves freeform or structured game notes to the profile."""

        profile = self.game_mgr.get_profile(game_id)

        if profile:

            profile["scratchpad_raw"] = scratchpad_text

            self.game_mgr._save_profiles(self.game_mgr.data)

            return {"success": True}

        return {"success": False, "error": "Profile not found"}



    def get_copilot_log(self, game_id: Optional[str] = None) -> dict:

        """Returns the Co-Pilot intel and milestone observations for the specified or active game."""

        gid = game_id or self.game_mgr.data.get("active_profile", "")

        entries = self.game_mgr.get_copilot_log(gid) if gid else []

        return {"success": True, "game_id": gid, "entries": entries}



    def clear_copilot_log(self, game_id: Optional[str] = None) -> dict:

        """Clears all Co-Pilot observations for the specified or active game."""

        gid = game_id or self.game_mgr.data.get("active_profile", "")

        success = self.game_mgr.clear_copilot_log(gid) if gid else False

        return {"success": success, "game_id": gid}



    def delete_copilot_log_entry(self, game_id: str, entry_id: str) -> dict:

        """Deletes a single Co-Pilot observation by ID."""

        gid = game_id or self.game_mgr.data.get("active_profile", "")

        success = self.game_mgr.delete_copilot_log_entry(gid, entry_id)

        return {"success": success}



    def add_copilot_log_entry(self, game_id: str, summary: str, category: str = "intel", location: str = "") -> dict:

        """Adds an entry to the Co-Pilot observation log."""

        gid = game_id or self.game_mgr.data.get("active_profile", "")

        return self.game_mgr.add_copilot_log_entry(gid, summary=summary, category=category, location=location)



    def get_guidance_level(self, game_id: Optional[str] = None) -> dict:

        """Returns the current guidance level for the game (companion, tactical, walkthrough)."""

        level = self.game_mgr.get_guidance_level(game_id)

        return {"success": True, "guidance_level": level}



    def set_guidance_level(self, game_id: str, level: str) -> dict:

        """Sets the guidance level for the game and triggers session prompt rotation if needed."""

        res = self.game_mgr.set_guidance_level(game_id, level)

        if res.get("success"):

            if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "_reset_context_flag"):

                self._engine._reset_context_flag = True

            elif hasattr(self, "engine") and self.engine and hasattr(self.engine, "_reset_context_flag"):

                self.engine._reset_context_flag = True

        return res



    def get_navigation_mode(self, game_id: Optional[str] = None) -> dict:

        """Returns the current map navigation mode for the game (controller, co_pilot_spotter, search_bar, mouse_keyboard)."""

        mode = self.game_mgr.get_navigation_mode(game_id)

        return {"success": True, "navigation_mode": mode}



    def set_navigation_mode(self, game_id: str, mode: str) -> dict:

        """Sets the map navigation mode for the game and triggers session prompt rotation if needed."""

        res = self.game_mgr.set_navigation_mode(game_id, mode)

        if res.get("success"):

            if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "_reset_context_flag"):

                self._engine._reset_context_flag = True

            elif hasattr(self, "engine") and self.engine and hasattr(self.engine, "_reset_context_flag"):

                self.engine._reset_context_flag = True

        return res



    def toggle_game_fps_monitoring(self, game_id: str, enabled: Any) -> dict:

        """Toggles FPS monitoring for a specific game profile and updates background watcher."""

        logger.info(f"[HARDWARE] toggle_game_fps_monitoring called for game_id='{game_id}' with enabled={enabled}")

        prof = self.game_mgr.get_profile(game_id)

        alt_id = ""

        if not prof:

            alt_id = game_id.replace("_64", "") if "_64" in game_id else f"{game_id}_64"

            prof = self.game_mgr.get_profile(alt_id)

            if prof:

                game_id = alt_id

            else:

                logger.warning(f"[HARDWARE] Game profile '{game_id}' not found.")

                return {"status": "error", "success": False, "message": f"Profile '{game_id}' not found."}



        if isinstance(enabled, str):

            enabled_bool = enabled.lower() not in ("false", "0", "off", "")

        else:

            enabled_bool = bool(enabled)



        prof["fps_monitoring"] = enabled_bool
        if os.environ.get("AETHER_TESTING") == "1":
            return {"status": "success", "success": True, "game_id": game_id, "fps_monitoring": enabled_bool}

        if hasattr(self.game_mgr, "save_profile"):

            self.game_mgr.save_profile(game_id)

        if hasattr(self.game_mgr, "save_profiles"):

            self.game_mgr.save_profiles()



        # Direct file sync for individual game JSON in data/games/

        try:

            from core.paths import get_user_data_dir
            games_dir = getattr(self.game_mgr, "games_dir", None) or os.path.join(getattr(self.game_mgr, "base_dir", get_user_data_dir()), "data", "games")

            gpath = os.path.join(games_dir, f"{game_id}.json")

            if os.path.exists(gpath):

                with open(gpath, "r", encoding="utf-8") as f:

                    file_data = json.load(f)

                file_data["fps_monitoring"] = enabled_bool

                with open(gpath, "w", encoding="utf-8") as f:

                    json.dump(file_data, f, indent=2)

        except Exception as e:

            logger.debug(f"[HARDWARE] Non-fatal error syncing individual profile JSON: {e}")



        try:

            from core.paths import get_user_data_dir
            profiles_json = os.path.join(getattr(self.game_mgr, "base_dir", get_user_data_dir()), "data", "games_profiles.json")

            if os.path.exists(profiles_json):

                with open(profiles_json, "r", encoding="utf-8") as f:

                    all_profs = json.load(f)

                if game_id in all_profs:

                    all_profs[game_id]["fps_monitoring"] = enabled_bool

                    with open(profiles_json, "w", encoding="utf-8") as f:

                        json.dump(all_profs, f, indent=2)

        except Exception as e:

            logger.debug(f"[HARDWARE] Non-fatal error syncing games_profiles.json: {e}")



        active_id = self.game_mgr.data.get("active_profile")

        engine = getattr(self, "engine", None) or getattr(self, "_engine", None)

        if (active_id == game_id or (alt_id and active_id == alt_id)) and hasattr(engine, "hardware_service") and engine.hardware_service:
            raw_exe = prof.get("executable") or prof.get("process_name") or f"{game_id}.exe"
            exe_name = os.path.basename(raw_exe)
            is_running = self.game_mgr.is_game_running(game_id)
            if enabled_bool and is_running and hasattr(engine.hardware_service, "start_fps_monitoring_sync"):
                engine.hardware_service.start_fps_monitoring_sync(exe_name)
            elif not enabled_bool and hasattr(engine.hardware_service, "stop_fps_monitoring_sync"):
                engine.hardware_service.stop_fps_monitoring_sync()

        try:
            self._on_engine_event("game_mode_changed", self.get_game_mode())
        except Exception:
            pass

        logger.info(f"[HARDWARE] Successfully updated fps_monitoring={enabled_bool} for '{game_id}'.")

        return {"status": "success", "success": True, "game_id": game_id, "fps_monitoring": enabled_bool}



    def get_game_fps_monitoring(self, game_id: str) -> dict:

        """Returns the current FPS monitoring status for the given game profile."""

        prof = self.game_mgr.get_profile(game_id)

        if not prof:

            return {"status": "error", "message": f"Profile '{game_id}' not found."}

        return {"status": "success", "game_id": game_id, "fps_monitoring": bool(prof.get("fps_monitoring", True))}

    def toggle_game_telemetry(self, game_id: str, enabled: Any) -> dict:
        """Toggles real-time game telemetry co-pilot monitoring for a specific game."""
        logger.info(f"[TELEMETRY] toggle_game_telemetry called for game_id='{game_id}' with enabled={enabled}")
        if isinstance(enabled, str):
            enabled_bool = enabled.lower() not in ("false", "0", "off", "")
        else:
            enabled_bool = bool(enabled)

        prof = self.game_mgr.get_profile(game_id)
        if not prof and game_id:
            alt_id = game_id.replace("_64", "") if "_64" in game_id else f"{game_id}_64"
            prof = self.game_mgr.get_profile(alt_id)
            if prof:
                game_id = alt_id

        if prof is not None:
            prof["telemetry_enabled"] = enabled_bool
            if os.environ.get("AETHER_TESTING") != "1":
                if hasattr(self.game_mgr, "save_profile"):
                    self.game_mgr.save_profile(game_id)
                if hasattr(self.game_mgr, "save_profiles"):
                    self.game_mgr.save_profiles()

        engine = getattr(self, "_engine", None) or getattr(self, "engine", None)
        telemetry_svc = getattr(engine, "telemetry_service", None) if engine else None
        if telemetry_svc is None:
            if not hasattr(self, "_telemetry_service") or self._telemetry_service is None:
                from core.telemetry.telemetry_service import TelemetryService
                cb = getattr(engine, "inject_tactical_alert", None) if engine else None
                self._telemetry_service = TelemetryService(on_copilot_trigger=cb)
            telemetry_svc = self._telemetry_service

        loop = getattr(engine, "_loop", None) or getattr(self, "_loop", None)
        telemetry_svc.set_enabled(game_id, enabled_bool, loop=loop, profile_override=prof)

        return {
            "status": "success",
            "success": True,
            "game_id": game_id,
            "telemetry_enabled": enabled_bool,
        }

    def set_game_input_mode(self, game_id: str, input_mode: str) -> dict:
        """Sets per-game input safety mode ('autonomous' or 'spotter_only')."""
        res = self.game_mgr.set_input_mode(game_id, input_mode)
        if res.get("success"):
            eng = getattr(self, "_engine", None) or getattr(self, "engine", None)
            if eng is not None:
                eng._reset_context_flag = True
        return res

    def set_game_hud_preference(self, game_id: str, hud_pref: str) -> dict:
        """Sets per-game HUD tier preference ('default', 'none', 'dot', 'mini', 'normal', 'max')."""
        res = self.game_mgr.set_hud_preference(game_id, hud_pref)
        if res.get("success"):
            try:
                is_running = bool(self.game_mgr.is_game_running(game_id))
                if is_running:
                    self._apply_game_hud_preference(game_id=game_id, is_running=True)
            except Exception:
                pass
        return res

    def _sync_telemetry_profile_override(self, game_id: str) -> None:
        """Refreshes active telemetry service profile override after profile edits."""
        eng = getattr(self, "_engine", None) or getattr(self, "engine", None)
        tel = getattr(eng, "telemetry_service", None) if eng else getattr(self, "_telemetry_service", None)
        prof = self.game_mgr.get_profile(game_id)
        norm_gid = (game_id or "").strip().lower().replace(" ", "_")
        if tel is not None and tel.enabled and tel.active_game_id == norm_gid:
            tel._profile_override = prof

    def set_game_suppress_alerts(self, game_id: str, processes_csv: Any) -> dict:
        """Sets 3rd-party companion processes (e.g. EDCoPilot.exe) that suppress duplicate voice alerts."""
        res = self.game_mgr.set_suppress_alerts(game_id, processes_csv)
        if res.get("success"):
            self._sync_telemetry_profile_override(game_id)
        return res

    def browse_companion_executable(self) -> dict:
        """Opens a native Windows file picker for selecting a 3rd-party companion program executable."""
        selected_path = self._open_native_file_dialog("Select 3rd-Party Companion Program")
        if selected_path:
            file_name = os.path.basename(selected_path)
            base_name = os.path.splitext(file_name)[0]
            cleaned = re.sub(r'([a-z])([A-Z0-9])', r'\1 \2', base_name)
            cleaned = cleaned.replace('_', ' ').replace('-', ' ').strip()
            suggested_name = cleaned.title() if cleaned else base_name
            return {
                "success": True,
                "file_path": selected_path,
                "file_name": file_name,
                "suggested_name": suggested_name,
                "directory": os.path.dirname(selected_path),
            }
        return {"success": False, "cancelled": True}

    def get_game_companion_apps(self, game_id: str) -> dict:
        """Returns linked 3rd-party companion apps for `game_id` with live running status."""
        if not game_id:
            return {"success": False, "apps": [], "error": "No game_id specified."}
        apps = self.game_mgr.get_companion_apps_with_status(game_id)
        return {"success": True, "game_id": game_id, "apps": apps}

    def add_game_companion_app(
        self,
        game_id: str,
        name: str,
        executable_or_path: str = "",
        purpose: str = "",
        mute_aether_alerts: bool = False,
        prompt_to_launch: bool = True,
        quiet_when_running: bool = False,
    ) -> dict:
        """Links a 3rd-party companion program to a game profile."""
        res = self.game_mgr.add_companion_app(
            game_id=game_id,
            name=name,
            executable_or_path=executable_or_path,
            purpose=purpose,
            mute_aether_alerts=bool(mute_aether_alerts),
            prompt_to_launch=bool(prompt_to_launch),
            quiet_when_running=bool(quiet_when_running),
        )
        if res.get("success"):
            self._sync_telemetry_profile_override(game_id)
            eng = getattr(self, "_engine", None) or getattr(self, "engine", None)
            if eng is not None:
                eng._reset_context_flag = True
        return res

    def update_game_companion_app(self, game_id: str, app_id: str, updates: dict) -> dict:
        """Updates a linked 3rd-party companion program (mute toggle, proactive launch toggle, etc.)."""
        res = self.game_mgr.update_companion_app(game_id, app_id, updates if isinstance(updates, dict) else {})
        if res.get("success"):
            self._sync_telemetry_profile_override(game_id)
            eng = getattr(self, "_engine", None) or getattr(self, "engine", None)
            if eng is not None:
                eng._reset_context_flag = True
        return res

    def remove_game_companion_app(self, game_id: str, app_id: str) -> dict:
        """Removes a linked 3rd-party companion program from a game profile."""
        res = self.game_mgr.remove_companion_app(game_id, app_id)
        if res.get("success"):
            self._sync_telemetry_profile_override(game_id)
            eng = getattr(self, "_engine", None) or getattr(self, "engine", None)
            if eng is not None:
                eng._reset_context_flag = True
        return res

    def launch_game_companion_app(self, game_id: str, app_id_or_name: str) -> dict:
        """Starts a linked 3rd-party companion program directly from the Games tab."""
        return self.game_mgr.launch_companion_app(app_id_or_name, game_id=game_id)

    def list_installed_game_modules(self) -> dict:
        """Returns metadata for all installed bundled + user Game Modules."""
        from core.game_modules import list_unique_game_modules
        return {"success": True, "modules": list_unique_game_modules()}

    def import_game_module_json(self, raw_json: Any) -> dict:
        """Imports a declarative `.aethermodule.json` string or dict and assimilates it."""
        from core.game_modules import import_game_module
        return import_game_module(raw_json, game_manager=self.game_mgr)

    def _reload_game_extension_tools(self) -> None:
        eng = getattr(self, "_engine", None) or getattr(self, "engine", None)
        if eng is None:
            return
        try:
            ge = getattr(eng, "game_engine", None)
            disp = getattr(eng, "dispatcher", None)
            if ge and disp and hasattr(ge, "register_tools"):
                ge.register_tools(disp)
            eng._reset_context_flag = True
        except Exception as exc:
            logger.debug(f"[GAME EXTENSIONS] Hot-reload warning: {exc}")

    def list_game_extensions(self) -> dict:
        """Lists installed Tier 2 community `.py` game extensions and their SHA-256 trust status."""
        from core.game_extensions import list_game_extensions
        return {"success": True, "extensions": list_game_extensions()}

    def import_game_extension_dialog(self, game_id: str = "") -> dict:
        """Opens a native file picker to import a Tier 2 `.py` community game extension."""
        try:
            import webview
            win = getattr(self, "_window", None)
            if not win:
                return {"success": False, "error": "Main window not available for file picker."}
            selected = win.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("Python Game Extensions (*.py)", "All files (*.*)"),
            )
            if not selected:
                return {"success": False, "cancelled": True}
            fpath = selected[0] if isinstance(selected, (list, tuple)) else str(selected)
            target_id = str(game_id or self.game_mgr.data.get("active_profile", "")).strip()
            if not target_id:
                target_id = os.path.splitext(os.path.basename(fpath))[0]
            else:
                # Prefer canonical module_id when linked to a declarative game module
                try:
                    from core.game_modules import get_module_for_game
                    prof = self.game_mgr.get_profile(target_id)
                    mod = get_module_for_game(target_id, profile=prof)
                    if isinstance(mod, dict) and mod.get("module_id"):
                        target_id = str(mod["module_id"])
                except Exception:
                    pass
            from core.game_extensions import import_game_extension
            return import_game_extension(target_id, fpath)
        except Exception as exc:
            return {"success": False, "error": f"Extension import failed: {exc}"}

    def trust_game_extension(self, module_id: str, script_name: str = "extension.py") -> dict:
        """Approves a Tier 2 `.py` game extension by recording its SHA-256 hash and hot-reloading tools."""
        from core.game_extensions import trust_game_extension
        res = trust_game_extension(module_id, script_name=script_name)
        if res.get("success"):
            self._reload_game_extension_tools()
        return res

    def revoke_game_extension(self, module_id: str) -> dict:
        """Revokes trust for a Tier 2 `.py` game extension and refreshes active tools."""
        from core.game_extensions import revoke_game_extension
        res = revoke_game_extension(module_id)
        if res.get("success"):
            self._reload_game_extension_tools()
        return res

    def delete_game_extension(self, module_id: str) -> dict:
        """Deletes an installed Tier 2 `.py` game extension and refreshes active tools."""
        from core.game_extensions import delete_game_extension
        res = delete_game_extension(module_id)
        if res.get("success"):
            self._reload_game_extension_tools()
        return res

    def import_game_module_dialog(self) -> dict:
        """Opens a native file picker to import a `.aethermodule.json` community module."""
        try:
            import webview
            win = getattr(self, "_window", None)
            if not win:
                return {"success": False, "error": "Main window not available for file picker."}
            selected = win.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("Aether Game Modules (*.aethermodule.json;*.json)", "All files (*.*)"),
            )
            if not selected:
                return {"success": False, "cancelled": True}
            fpath = selected[0] if isinstance(selected, (list, tuple)) else str(selected)
            from core.game_modules import import_game_module
            return import_game_module(fpath, game_manager=self.game_mgr)
        except Exception as e:
            return {"success": False, "error": f"Import dialog failed: {e}"}

    def export_game_module_dialog(self, game_id: str) -> dict:
        """Exports the active game profile as a clean, shareable `.aethermodule.json` file."""
        from core.game_modules import export_game_module
        exp = export_game_module(game_id, game_manager=self.game_mgr)
        if not exp.get("success"):
            return exp
        try:
            import webview
            win = getattr(self, "_window", None)
            if not win:
                return exp
            default_name = f"{exp['module_id']}.aethermodule.json"
            dest = win.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=default_name,
                file_types=("Aether Game Modules (*.aethermodule.json;*.json)",),
            )
            if not dest:
                return {"success": False, "cancelled": True}
            out_path = dest[0] if isinstance(dest, (list, tuple)) else str(dest)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(exp["module"], f, indent=2)
            return {"success": True, "module_id": exp["module_id"], "exported_path": out_path}
        except Exception as e:
            return {"success": False, "error": f"Export dialog failed: {e}"}

    def auto_build_game_module(self, game_id: str) -> dict:
        """
        Auto-detects companion bridges, wiki/universe, and anti-cheat safety to draft a Game Module.
        If Aether is not running yet, starts Aether automatically so the web-grounded Gemini lookup executes.
        """
        import time as _time
        from core.game_modules import auto_build_game_module as _auto_build
        from core.model_discovery import get_configured_tier2_model

        eng = getattr(self, "_engine", None) or getattr(self, "engine", None)
        started_aether = False

        if eng is not None and not getattr(eng, "is_running", False):
            logger.info("[GAME MODULE] Aether not running; starting Aether before Auto-Build Gemini lookup.")
            try:
                start_res = self.start_assistant()
                if isinstance(start_res, dict) and start_res.get("success"):
                    started_aether = True
            except Exception as e:
                logger.warning(f"[GAME MODULE] Could not auto-start Aether before Auto-Build: {e}")

        # Resolve (or wait briefly for) the live GenAI client
        client = None
        if eng is not None:
            deadline = _time.monotonic() + (1.5 if started_aether else 0.0)
            while True:
                client = getattr(eng, "genai_client", None) or getattr(eng, "client", None)
                if client is not None or _time.monotonic() >= deadline:
                    break
                _time.sleep(0.15)

        cfg = self.get_raw_config() if hasattr(self, "get_raw_config") else {}
        model_ep = get_configured_tier2_model(cfg)[0]
        res = _auto_build(
            game_id=game_id,
            game_manager=self.game_mgr,
            genai_client=client,
            model_endpoint=model_ep,
        )
        if isinstance(res, dict):
            res["started_aether"] = started_aether
            res["used_gemini_lookup"] = bool(client is not None)
            if started_aether:
                notes = list(res.get("notes") or [])
                notes.insert(0, "Started Aether to run web-grounded Gemini module lookup.")
                res["notes"] = notes
        return res

    def calibrate_game_hud_bars(
        self,
        game_id: str,
        focus_game: bool = True,
        countdown_sec: float = 3.0,
    ) -> dict:
        """
        One-click Vision HUD bar calibration for the active game's resolution & aspect ratio.
        Automatically starts Aether if needed, brings the game window back to the foreground,
        waits a brief countdown (so the player can unpause if needed), captures the HUD,
        and announces completion verbally so you don't have to Alt+Tab back.
        """
        import time as _time
        from core.model_discovery import get_configured_tier2_model
        from core.telemetry.providers.vision_hud_provider import calibrate_and_persist_game_hud

        prof = self.game_mgr.get_profile(game_id)
        if not prof:
            return {"success": False, "error": f"Game profile '{game_id}' not found."}

        eng = getattr(self, "_engine", None) or getattr(self, "engine", None)
        started_aether = False
        if eng is not None and not getattr(eng, "is_running", False):
            logger.info("[HUD CALIBRATION] Aether not running; starting Aether before HUD calibration.")
            try:
                start_res = self.start_assistant()
                if isinstance(start_res, dict) and start_res.get("success"):
                    started_aether = True
            except Exception as e:
                logger.warning(f"[HUD CALIBRATION] Could not auto-start Aether: {e}")

        client = None
        if eng is not None:
            deadline = _time.monotonic() + (1.5 if started_aether else 0.0)
            while True:
                client = getattr(eng, "genai_client", None) or getattr(eng, "client", None)
                if client is not None or _time.monotonic() >= deadline:
                    break
                _time.sleep(0.15)

        if not client:
            return {"success": False, "error": "Gemini client not initialized yet. Check your API key or click Start Aether first."}

        cfg = self.get_raw_config() if hasattr(self, "get_raw_config") else {}
        model_ep = get_configured_tier2_model(cfg)[0]
        tel_svc = getattr(eng, "telemetry_service", None) if eng else getattr(self, "_telemetry_service", None)

        res = calibrate_and_persist_game_hud(
            game_id=game_id,
            game_manager=self.game_mgr,
            genai_client=client,
            model_endpoint=model_ep,
            telemetry_service=tel_svc,
            focus_game=bool(focus_game),
            countdown_sec=float(countdown_sec or 0.0),
        )
        res["started_aether"] = started_aether

        # Speak a brief confirmation while the player is focused back in the game
        if eng is not None and getattr(eng, "is_running", False) and hasattr(eng, "_speak_feedback"):
            eng_loop = getattr(eng, "_loop", None) or getattr(self, "_loop", None)
            if eng_loop and getattr(eng_loop, "is_running", lambda: False)():
                spoken = (
                    f"HUD calibration complete. Locked onto {', '.join(b['name'] for b in res['bars'])}."
                    if res.get("success")
                    else "HUD calibration didn't spot any bars. Make sure you're unpaused in live gameplay and say calibrate my HUD."
                )
                try:
                    asyncio.run_coroutine_threadsafe(eng._speak_feedback(spoken), eng_loop)
                except Exception:
                    pass

        return res

    def check_game_elevation_status(self, game_id: Optional[str] = None) -> dict:
        """Returns Admin/UIPI elevation status for the active or specified game."""
        return self.game_mgr.check_game_elevation(game_id)



    def get_map_controls(self, game_id: Optional[str] = None) -> dict:

        """Returns the configured map controls for the game."""

        gid = game_id or self.game_mgr.data.get("active_profile", "")

        prof = self.game_mgr.get_profile(gid) if gid else None

        controls = prof.get("map_controls", {}) if prof else {}

        return {"success": True, "map_controls": controls}



    def get_game_telemetry(self) -> dict:

        """Returns live in-game telemetry snapshot for the active profile."""

        active_id = self.game_mgr.data.get("active_profile", "")

        copilot_entries = self.game_mgr.get_copilot_log(active_id) if active_id else []

        adapter = getattr(self.game_mgr, "active_telemetry_adapter", None) or getattr(self.game_mgr, "ed_watcher", None)

        if adapter and hasattr(adapter, "state") and getattr(adapter, "state", None):

            res = dict(adapter.state)

            res["copilot_entries"] = copilot_entries

            return res

        profile = self.game_mgr.get_profile(active_id)

        return {

            "game": profile.get("display_name") if profile else active_id,

            "active": False,

            "star_system": "Unknown",

            "docked": False,

            "supercruise": False,

            "station": None,

            "scratchpad": profile.get("scratchpad_raw", "") if profile else "",

            "copilot_entries": copilot_entries

        }



    def _open_native_file_dialog(self, title: str = "Select Executable") -> Optional[str]:

        """

        Opens a native Windows open file dialog for choosing an executable.

        Uses pywebview native modal dialog, Win32 GetOpenFileNameW via ctypes, or Tkinter fallback.

        """

        # Method 1: pywebview Window create_file_dialog (Native WinForms dialog parented to main window)

        win = getattr(self, "_window", None)

        if win:

            try:

                import webview

                file_types = (

                    'Executable files (*.exe)',

                    'Batch files (*.bat;*.cmd;*.ps1)',

                    'All files (*.*)'

                )

                dialog_mode = getattr(webview.FileDialog, "OPEN", 10)

                res = win.create_file_dialog(

                    dialog_type=dialog_mode,

                    allow_multiple=False,

                    file_types=file_types

                )

                if res and len(res) > 0 and res[0]:

                    logger.info(f"[BRIDGE] pywebview file dialog selected: {res[0]}")

                    return os.path.normpath(res[0])

                if res is not None and len(res) == 0:

                    # User explicitly cancelled

                    return None

            except Exception as e:

                logger.debug(f"[BRIDGE] pywebview file dialog error: {e}")



        # Method 2: Win32 GetOpenFileNameW via ctypes

        if sys.platform == "win32":

            try:

                import ctypes

                from ctypes import wintypes

                class OPENFILENAMEW(ctypes.Structure):

                    _fields_ = [

                        ('lStructSize', wintypes.DWORD),

                        ('hwndOwner', wintypes.HWND),

                        ('hInstance', wintypes.HINSTANCE),

                        ('lpstrFilter', wintypes.LPCWSTR),

                        ('lpstrCustomFilter', wintypes.LPWSTR),

                        ('nMaxCustFilter', wintypes.DWORD),

                        ('nFilterIndex', wintypes.DWORD),

                        ('lpstrFile', wintypes.LPWSTR),

                        ('nMaxFile', wintypes.DWORD),

                        ('lpstrFileTitle', wintypes.LPWSTR),

                        ('nMaxFileTitle', wintypes.DWORD),

                        ('lpstrInitialDir', wintypes.LPCWSTR),

                        ('lpstrTitle', wintypes.LPCWSTR),

                        ('Flags', wintypes.DWORD),

                        ('nFileOffset', wintypes.WORD),

                        ('nFileExtension', wintypes.WORD),

                        ('lpstrDefExt', wintypes.LPCWSTR),

                        ('lCustData', wintypes.LPARAM),

                        ('lpfnHook', wintypes.LPVOID),

                        ('lpTemplateName', wintypes.LPCWSTR),

                        ('pvReserved', wintypes.LPVOID),

                        ('dwReserved', wintypes.DWORD),

                        ('FlagsEx', wintypes.DWORD)

                    ]

                buf = ctypes.create_unicode_buffer(1024)

                ofn = OPENFILENAMEW()

                ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)

                ofn.lpstrFilter = "Executable files (*.exe)\0*.exe\0Batch files (*.bat;*.cmd;*.ps1)\0*.bat;*.cmd;*.ps1\0All files (*.*)\0*.*\0\0"

                ofn.nFilterIndex = 1

                ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR)

                ofn.nMaxFile = 1024

                ofn.lpstrTitle = title

                ofn.Flags = 0x00001000 | 0x00000800 | 0x00080000 | 0x00000008

                if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):

                    if buf.value and os.path.exists(buf.value):

                        logger.info(f"[BRIDGE] GetOpenFileNameW selected: {buf.value}")

                        return os.path.normpath(buf.value)

            except Exception as e:

                logger.debug(f"[BRIDGE] GetOpenFileNameW fallback error: {e}")



        # Method 3: Tkinter Topmost File Dialog fallback

        try:

            import tkinter as tk

            from tkinter import filedialog

            root = tk.Tk()

            root.withdraw()

            root.attributes('-topmost', True)

            root.focus_force()

            selected = filedialog.askopenfilename(

                parent=root,

                title=title,

                filetypes=[

                    ("Executable files (*.exe)", "*.exe"),

                    ("Batch files (*.bat;*.cmd;*.ps1)", "*.bat;*.cmd;*.ps1"),

                    ("All files (*.*)", "*.*")

                ]

            )

            root.destroy()

            if selected and os.path.exists(selected):

                logger.info(f"[BRIDGE] Tkinter file dialog selected: {selected}")

                return os.path.normpath(selected)

        except Exception as e:

            logger.debug(f"[BRIDGE] tkinter file dialog error: {e}")



        return None



    def browse_game_executable(self) -> dict:

        """

        Opens a native Windows file selection dialog for choosing a game executable.

        Returns file path, clean suggested name, and process basename.

        """

        selected_path = self._open_native_file_dialog("Select Game Executable")

        if selected_path:

            file_name = os.path.basename(selected_path)

            base_name = os.path.splitext(file_name)[0]

            cleaned = re.sub(r'([a-z])([A-Z0-9])', r'\1 \2', base_name)

            cleaned = cleaned.replace('_', ' ').replace('-', ' ').strip()

            suggested_name = cleaned.title() if cleaned else base_name

            directory = os.path.dirname(selected_path)



            logger.info(f"[BRIDGE] browse_game_executable successfully resolved: {selected_path} (dir: {directory})")

            return {

                "success": True,

                "file_path": selected_path,

                "file_name": file_name,

                "suggested_name": suggested_name,

                "directory": directory

            }



        logger.info("[BRIDGE] browse_game_executable cancelled or no file selected.")

        return {"success": False, "cancelled": True}



    def get_running_processes(self) -> dict:

        """

        Scans currently active processes and open windows using psutil and win32gui.

        Returns running applications (with window titles) and user processes to select as game executables.

        """

        import psutil

        system_procs = {

            "system", "system idle process", "registry", "smss.exe", "csrss.exe", "wininit.exe",

            "services.exe", "lsass.exe", "svchost.exe", "fontdrvhost.exe", "winlogon.exe",

            "dwm.exe", "sihost.exe", "taskhostw.exe", "runtimebroker.exe", "shellexperiencehost.exe",

            "searchindexer.exe", "searchhost.exe", "startmenuexperiencehost.exe", "ctfmon.exe",

            "explorer.exe", "conhost.exe", "wudfhost.exe", "spoolsv.exe"

        }



        # 1. Map window titles to processes if on Windows

        window_titles = {}

        if sys.platform == "win32":

            try:

                from core.screen_stream import ensure_thread_desktop

                ensure_thread_desktop()

                import win32gui

                import win32process



                def enum_cb(hwnd, _):

                    if win32gui.IsWindowVisible(hwnd):

                        txt = win32gui.GetWindowText(hwnd).strip()

                        if txt and len(txt) > 1:

                            try:

                                _, pid = win32process.GetWindowThreadProcessId(hwnd)

                                if pid not in window_titles:

                                    window_titles[pid] = txt

                            except Exception:

                                pass

                    return True



                win32gui.EnumWindows(enum_cb, None)

            except Exception:

                pass



        applications = []
        seen_names = set()
        user_processes = []
        process_paths = {}

        try:
            for p in psutil.process_iter(['pid', 'name', 'exe']):
                try:
                    pname = p.info.get('name') or ''
                    pid = p.info.get('pid')
                    pexe = p.info.get('exe') or ''
                    if not pname or pname.lower() in system_procs:
                        continue

                    if pexe and pname.lower() not in process_paths:
                        process_paths[pname.lower()] = os.path.normpath(pexe)

                    # If has a visible window, treat as primary application
                    if pid in window_titles:
                        w_title = window_titles[pid]
                        applications.append({
                            "pid": pid,
                            "name": pname,
                            "title": w_title,
                            "exe_path": os.path.normpath(pexe) if pexe else ""
                        })

                    if pname.lower() not in seen_names:
                        seen_names.add(pname.lower())
                        user_processes.append(pname)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception as e:
            logger.debug(f"[BRIDGE] Error scanning processes: {e}")

        applications.sort(key=lambda a: a.get("title", "").lower())
        user_processes.sort(key=lambda s: s.lower())

        return {
            "success": True,
            "applications": applications,
            "processes": user_processes,
            "process_paths": process_paths
        }



    def open_task_manager(self) -> dict:

        """Launches the native Windows Task Manager."""

        if sys.platform == "win32":

            try:

                import subprocess

                subprocess.Popen(["taskmgr.exe"])

                return {"success": True}

            except Exception as e:

                return {"success": False, "error": str(e)}

        return {"success": False, "error": "Task Manager is only available on Windows."}



    # -------------------------------------------------------------------------

    # Whitelisted Programs & Application Subsystem RPCs

    # -------------------------------------------------------------------------

    def get_programs(self) -> dict:

        """Returns all whitelisted programs, active program, and process running status."""

        return self.program_mgr.list_programs()



    def get_installed_programs(self) -> dict:

        """Scans the Windows Registry and Start Menu for installed desktop applications."""

        try:

            programs = self.program_mgr.get_installed_programs()

            return {"success": True, "programs": programs, "total": len(programs)}

        except Exception as e:

            logger.error(f"[BRIDGE ERROR] get_installed_programs: {e}")

            return {"success": False, "error": str(e), "programs": [], "total": 0}



    def batch_add_programs(self, programs: Any = None) -> dict:

        """Adds a batch of programs to the whitelist en masse in an atomic transaction."""

        try:

            if not isinstance(programs, list):

                return {"success": False, "error": "Programs argument must be a list"}

            res = self.program_mgr.batch_add_programs(programs)

            if res.get("success"):

                try:

                    self.notify("oauth_status_updated", self.get_oauth_status())

                    self.notify("desktop_notifications_status_changed", self.get_desktop_notifications_status())

                except Exception:

                    pass

            return res

        except Exception as e:

            logger.error(f"[BRIDGE ERROR] batch_add_programs: {e}")

            return {"success": False, "error": str(e)}



    def add_program(

        self,

        display_name: Any = "",

        path: str = "",

        launch_args: str = "",

        elevate: bool = False,

        working_dir: str = "",

        *args,

        **kwargs

    ) -> dict:

        """Adds a new program to the whitelist."""

        # Support dict argument e.g. add_program({"name": "...", "path": "..."})

        if isinstance(display_name, dict):

            data = display_name

            name_val = str(data.get("name") or data.get("display_name") or "").strip()

            path_val = str(data.get("path") or "").strip()

            args_val = str(data.get("launch_args") or data.get("arguments") or data.get("args") or "").strip()

            elev_val = bool(data.get("elevate", False))

            cwd_val = str(data.get("working_dir") or data.get("cwd") or "").strip()

            res = self.program_mgr.add_program(

                name=name_val,

                path=path_val,

                arguments=args_val,

                elevate=elev_val,

                working_dir=cwd_val

            )

            if res.get("success"):

                try:

                    self.notify("oauth_status_updated", self.get_oauth_status())

                    self.notify("desktop_notifications_status_changed", self.get_desktop_notifications_status())

                except Exception:

                    pass

            return res



        name_val = str(display_name or "").strip()

        path_val = str(path or "").strip()

        args_val = str(launch_args or kwargs.get("arguments") or kwargs.get("launch_args") or kwargs.get("args") or "").strip()

        elev_val = bool(elevate or kwargs.get("elevate", False))

        cwd_val = str(working_dir or kwargs.get("working_dir") or kwargs.get("cwd") or "").strip()



        if not name_val:

            return {"success": False, "error": "Program display name cannot be empty."}



        res = self.program_mgr.add_program(

            name=name_val,

            path=path_val,

            arguments=args_val,

            elevate=elev_val,

            working_dir=cwd_val

        )

        if res.get("success"):

            try:

                self.notify("oauth_status_updated", self.get_oauth_status())

                self.notify("desktop_notifications_status_changed", self.get_desktop_notifications_status())

            except Exception:

                pass

        return res



    def update_program(

        self,

        prog_id: Any = "",

        display_name: str = "",

        path: str = "",

        launch_args: str = "",

        elevate: bool = False,

        working_dir: str = "",

        *args,

        **kwargs

    ) -> dict:

        """Updates an existing whitelisted program configuration."""

        if isinstance(prog_id, dict):

            data = prog_id

            pid_val = str(data.get("id") or data.get("prog_id") or "").strip()

            name_val = str(data.get("name") or data.get("display_name") or "").strip()

            path_val = str(data.get("path") or "").strip()

            args_val = str(data.get("launch_args") or data.get("arguments") or data.get("args") or "").strip()

            elev_val = bool(data.get("elevate", False))

            cwd_val = str(data.get("working_dir") or data.get("cwd") or "").strip()

            return self.program_mgr.update_program(

                prog_id=pid_val,

                name=name_val,

                path=path_val,

                arguments=args_val,

                elevate=elev_val,

                working_dir=cwd_val

            )



        pid_val = str(prog_id or "").strip()

        name_val = str(display_name or "").strip()

        path_val = str(path or "").strip()

        args_val = str(launch_args or kwargs.get("arguments") or kwargs.get("launch_args") or kwargs.get("args") or "").strip()

        elev_val = bool(elevate or kwargs.get("elevate", False))

        cwd_val = str(working_dir or kwargs.get("working_dir") or kwargs.get("cwd") or "").strip()



        return self.program_mgr.update_program(

            prog_id=pid_val,

            name=name_val,

            path=path_val,

            arguments=args_val,

            elevate=elev_val,

            working_dir=cwd_val

        )



    def delete_program(self, prog_id: str) -> dict:

        """Deletes a program from the whitelist."""

        res = self.program_mgr.delete_program(prog_id)

        if res.get("success"):

            try:

                self.notify("oauth_status_updated", self.get_oauth_status())

                self.notify("desktop_notifications_status_changed", self.get_desktop_notifications_status())

            except Exception:

                pass

        return res



    def set_active_program(self, prog_id: str) -> dict:

        """Sets the currently selected program in the UI."""

        success = self.program_mgr.set_active_program(prog_id)

        return {"success": success, "active_program": prog_id}



    def launch_program(self, prog_id: str) -> dict:

        """Launches the specified program detached or elevated."""

        return self.program_mgr.launch_program(prog_id)



    def close_program(self, prog_id: str) -> dict:

        """Terminates the specified program's process."""

        return self.program_mgr.close_program(prog_id)



    def browse_program_executable(self) -> dict:

        """

        Opens a Windows file browser dialog for the user to navigate to and select any program executable (.exe).

        Returns the selected file name, path, and suggested clean display name.

        """

        selected_path = self._open_native_file_dialog("Select Program Executable")



        if selected_path:

            file_name = os.path.basename(selected_path)

            base_name = os.path.splitext(file_name)[0]

            cleaned = re.sub(r'([a-z])([A-Z0-9])', r'\1 \2', base_name)

            cleaned = cleaned.replace('_', ' ').replace('-', ' ').strip()

            suggested_name = cleaned.title() if cleaned else base_name

            directory = os.path.dirname(selected_path)



            logger.info(f"[BRIDGE] browse_program_executable successfully resolved: {selected_path} (dir: {directory})")

            return {

                "success": True,

                "file_path": selected_path,

                "file_name": file_name,

                "suggested_name": suggested_name,

                "directory": directory

            }



        logger.info("[BRIDGE] browse_program_executable cancelled or no file selected.")

        return {"success": False, "cancelled": True}



    # =========================================================================

    # OAuth 2.0 Connected Accounts (Gmail / Outlook / Hotmail / IMAP) RPC

    # =========================================================================



    def get_oauth_status(self) -> dict:

        """Returns safe connection status for Google, Microsoft (Outlook/Hotmail), IMAP, Steam, and Desktop Notifications."""

        res = self._oauth_mgr.get_status()

        res["steam"] = self.get_steam_status()

        res["desktop_notifications"] = self.get_desktop_notifications_status()

        res["email_monitoring"] = self.get_email_monitor_status()

        res["streaming"] = self.get_streaming_status()

        res["replay_capture"] = self.get_replay_capture_config()

        res["media_controller"] = self.get_media_controller_config()

        return res



    def save_oauth_client_config(self, provider: str, config_data: Optional[dict] = None) -> dict:

        """Saves OAuth 2.0 Client ID / Secret or IMAP/SMTP credentials to the DPAPI Token Vault."""

        if isinstance(provider, dict) and config_data is None:

            config_data = provider

            provider = str(config_data.get("provider", "google"))

        return self._oauth_mgr.save_client_config(str(provider), config_data or {})



    def start_oauth_flow(self, provider: str) -> dict:

        """Initiates the system browser OAuth 2.0 PKCE flow for 'google' or 'microsoft'."""

        return self._oauth_mgr.start_oauth_flow(str(provider))



    def disconnect_oauth_provider(self, provider: str) -> dict:

        """Disconnects and revokes stored OAuth tokens for the specified provider."""

        return self._oauth_mgr.disconnect_provider(str(provider))



    def confirm_pending_email_send(self, draft_id: str, provider: str = "auto") -> dict:

        """Confirms and dispatches a pending email draft from the UI."""

        return self._oauth_mgr.send_draft(draft_id=str(draft_id), provider=str(provider), user_confirmed=True)



    # =========================================================================

    # Steam Web API Integration RPC

    # =========================================================================



    def get_steam_status(self) -> dict:

        """Returns the current connection and configuration status of Steam Web API."""

        from core.config import load_settings

        settings = load_settings()

        steam_cfg = settings.get("integrations", {}).get("steam", {})

        api_key = steam_cfg.get("api_key") or os.getenv("STEAM_API_KEY", "")

        steam_id64 = steam_cfg.get("steam_id64") or os.getenv("STEAM_ID64", "")

        enabled = steam_cfg.get("enabled", True)

        auto_detect = steam_cfg.get("auto_detect_active_appid", True)

        persona_name = steam_cfg.get("persona_name", "")

        poll_friends_status = bool(steam_cfg.get("poll_friends_status", False))

        poll_interval_minutes = int(steam_cfg.get("poll_interval_minutes", 5) or 5)



        connected = bool(api_key and steam_id64)

        masked_key = ""

        if api_key:

            masked_key = api_key[:4] + "••••••••" + api_key[-4:] if len(api_key) > 8 else "••••••••"



        return {

            "success": True,

            "connected": connected,

            "enabled": enabled,

            "api_key_masked": masked_key,

            "has_api_key": bool(api_key),

            "steam_id64": steam_id64,

            "auto_detect_active_appid": auto_detect,

            "persona_name": persona_name,

            "poll_friends_status": poll_friends_status,

            "poll_interval_minutes": poll_interval_minutes,

            "account_label": f"Connected as {persona_name} (SteamID: {steam_id64})" if persona_name else (f"SteamID: {steam_id64}" if steam_id64 else "Not connected")

        }



    def save_steam_config(

        self,

        api_key: str = "",

        steam_id64: str = "",

        auto_detect: bool = True,

        poll_friends_status: bool = False,

        poll_interval_minutes: int = 5

    ) -> dict:

        """Saves Steam API credentials, fetches persona name, and updates the running SteamService."""

        from core.config import load_settings, save_settings

        settings = load_settings()

        steam_cfg = settings.setdefault("integrations", {}).setdefault("steam", {})



        clean_key = str(api_key or "").strip()

        clean_id = str(steam_id64 or "").strip()



        # If user left the masked key unchanged, keep existing key

        if "••••" in clean_key:

            clean_key = steam_cfg.get("api_key") or os.getenv("STEAM_API_KEY", "")



        # Try to resolve Steam persona name if credentials provided

        persona_name = steam_cfg.get("persona_name", "")

        if clean_key and clean_id:

            try:

                import urllib.request

                import json

                url = f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?key={clean_key}&steamids={clean_id}"

                req = urllib.request.Request(url, headers={"User-Agent": "Aether-Desktop"})

                with urllib.request.urlopen(req, timeout=4.0) as resp:

                    if resp.status == 200:

                        data = json.loads(resp.read().decode("utf-8"))

                        players = data.get("response", {}).get("players", [])

                        if players:

                            persona_name = players[0].get("personaname", "")

            except Exception as e:

                logger.warning(f"[STEAM] Could not verify persona name: {e}")



        steam_cfg["enabled"] = True

        steam_cfg["api_key"] = clean_key

        steam_cfg["steam_id64"] = clean_id

        steam_cfg["auto_detect_active_appid"] = bool(auto_detect)

        steam_cfg["poll_friends_status"] = bool(poll_friends_status)

        steam_cfg["poll_interval_minutes"] = max(1, int(poll_interval_minutes or 5))

        if persona_name:

            steam_cfg["persona_name"] = persona_name



        settings["steam_api_key"] = clean_key

        settings["steam_id64"] = clean_id



        save_settings(settings)



        # Update running engine steam service if available

        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "steam_service"):

            self._engine.steam_service.configure(api_key=clean_key, steam_id64=clean_id)

            if hasattr(self._engine, "set_steam_friend_polling"):

                self._engine.set_steam_friend_polling(poll_friends_status, poll_interval_minutes)



        status = self.get_steam_status()

        self.notify("steam_status_changed", status)

        self.notify("oauth_status_updated", self.get_oauth_status())

        return status



    def set_steam_friend_polling(self, enabled: bool, interval_minutes: int = 5) -> dict:

        """Enables or disables periodic Steam friend status polling and saves preference."""

        from core.config import load_settings, save_settings

        settings = load_settings()

        steam_cfg = settings.setdefault("integrations", {}).setdefault("steam", {})

        steam_cfg["poll_friends_status"] = bool(enabled)

        steam_cfg["poll_interval_minutes"] = max(1, int(interval_minutes or 5))

        save_settings(settings)



        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "set_steam_friend_polling"):

            self._engine.set_steam_friend_polling(bool(enabled), int(interval_minutes or 5))

        elif hasattr(self, "_engine") and self._engine and hasattr(self._engine, "steam_service"):

            self._engine.steam_service.set_friend_polling(

                enabled=bool(enabled),

                interval_minutes=int(interval_minutes or 5),

                callback=getattr(self._engine, "_on_steam_friend_status_change", None),

                loop=getattr(self._engine, "_loop", None)

            )



        status = self.get_steam_status()

        self.notify("steam_status_changed", status)

        self.notify("oauth_status_updated", self.get_oauth_status())

        return status



    def disconnect_steam(self) -> dict:

        """Disconnects and clears Steam Web API credentials."""

        from core.config import load_settings, save_settings

        settings = load_settings()

        steam_cfg = settings.setdefault("integrations", {}).setdefault("steam", {})

        steam_cfg["api_key"] = ""

        steam_cfg["steam_id64"] = ""

        steam_cfg["persona_name"] = ""

        steam_cfg["poll_friends_status"] = False

        settings["steam_api_key"] = ""

        settings["steam_id64"] = ""

        save_settings(settings)



        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "steam_service"):

            self._engine.steam_service.configure(api_key="", steam_id64="")

            self._engine.steam_service.stop_friend_polling()



        status = self.get_steam_status()

        self.notify("steam_status_changed", status)

        self.notify("oauth_status_updated", self.get_oauth_status())

        return status



    # =========================================================================

    # Desktop Notification Watcher (Discord, Slack, Teams, etc.) RPC

    # =========================================================================



    def get_desktop_notifications_status(self) -> dict:

        """Returns the current configuration and operational status of the desktop notification watcher."""

        from core.config import load_settings

        settings = load_settings()

        notif_cfg = settings.get("integrations", {}).get("desktop_notifications", {})

        enabled = bool(notif_cfg.get("enabled", True))

        poll_interval = float(notif_cfg.get("poll_interval_seconds", 1.5))

        target_apps = list(notif_cfg.get("target_apps", ["Discord", "Slack", "Microsoft Teams", "WhatsApp", "Telegram"]))

        verbal = bool(notif_cfg.get("verbal_announcement", False))

        include_wl = bool(notif_cfg.get("include_whitelist_apps", True))



        watcher = getattr(self._engine, "notification_watcher", None) if hasattr(self, "_engine") and self._engine else None

        supported = watcher.is_supported() if watcher else True

        running = bool(watcher and getattr(watcher, "_running", False))



        whitelist_apps = []

        if include_wl:

            try:

                if hasattr(self, "program_mgr") and self.program_mgr and hasattr(self.program_mgr, "get_whitelist_names"):

                    raw_wl = self.program_mgr.get_whitelist_names()

                    whitelist_apps = sorted(list({

                        name.title() for name in raw_wl

                        if name and name != "*" and not name.lower().endswith(".exe")

                    }))

            except Exception:

                pass



        extra_str = f" + {len(whitelist_apps)} whitelisted apps" if (include_wl and whitelist_apps) else ""

        status_label = f"Monitoring {', '.join(target_apps[:3])}{extra_str}" if enabled and supported else ("Disabled" if not enabled else "Unavailable")



        return {

            "success": True,

            "enabled": enabled,

            "supported": supported,

            "running": running,

            "verbal_announcement": verbal,

            "poll_interval_seconds": poll_interval,

            "target_apps": target_apps,

            "include_whitelist_apps": include_wl,

            "whitelist_apps": whitelist_apps,

            "effective_targets": list(watcher.get_effective_targets()) if watcher else target_apps,

            "status_label": status_label

        }



    def save_desktop_notifications_config(

        self,

        enabled: bool = True,

        verbal_announcement: bool = False,

        poll_interval_seconds: float = 1.5,

        target_apps: Optional[Any] = None,

        include_whitelist_apps: bool = True

    ) -> dict:

        """Updates and persists desktop notification watcher configuration."""

        from core.config import load_settings, save_settings

        settings = load_settings()

        notif_cfg = settings.setdefault("integrations", {}).setdefault("desktop_notifications", {})



        notif_cfg["enabled"] = bool(enabled)

        notif_cfg["verbal_announcement"] = bool(verbal_announcement)

        notif_cfg["poll_interval_seconds"] = max(0.5, float(poll_interval_seconds or 1.5))

        notif_cfg["include_whitelist_apps"] = bool(include_whitelist_apps)

        if target_apps is not None:

            if isinstance(target_apps, str):

                target_apps = [a.strip() for a in target_apps.split(",") if a.strip()]

            notif_cfg["target_apps"] = list(target_apps)



        save_settings(settings)



        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "set_desktop_notifications_config"):

            self._engine.set_desktop_notifications_config(

                enabled=bool(enabled),

                verbal_announcement=bool(verbal_announcement),

                poll_interval_seconds=max(0.5, float(poll_interval_seconds or 1.5)),

                target_apps=notif_cfg["target_apps"],

                include_whitelist_apps=bool(include_whitelist_apps)

            )



        status = self.get_desktop_notifications_status()

        self.notify("desktop_notifications_status_changed", status)

        self.notify("oauth_status_updated", self.get_oauth_status())

        return status



    def get_recent_desktop_notifications(self, app_name: Optional[str] = None, limit: int = 10) -> dict:

        """Returns captured notifications from memory."""

        watcher = getattr(self._engine, "notification_watcher", None) if hasattr(self, "_engine") and self._engine else None

        if not watcher:

            return {"success": True, "count": 0, "notifications": []}

        notifs = watcher.get_recent(app_filter=app_name, limit=int(limit or 10))

        return {"success": True, "count": len(notifs), "notifications": notifs}



    def clear_desktop_notifications(self) -> dict:

        """Clears recent notification log from watcher memory."""

        watcher = getattr(self._engine, "notification_watcher", None) if hasattr(self, "_engine") and self._engine else None

        if watcher:

            watcher.clear_history()

        return {"success": True, "message": "Notification history cleared."}

    def get_email_monitor_status(self) -> dict:

        """Returns configuration and operational status of background OAuth/IMAP email monitoring."""

        from core.config import load_settings

        settings = load_settings()

        email_cfg = settings.get("integrations", {}).get("email_monitoring", {})

        enabled = bool(email_cfg.get("enabled", True))

        verbal = bool(email_cfg.get("verbal_announcement", True))

        poll_interval = float(email_cfg.get("poll_interval_seconds", 60.0))

        monitor = getattr(self._engine, "email_monitor", None) if hasattr(self, "_engine") and self._engine else None

        running = bool(monitor and getattr(monitor, "_running", False))

        return {

            "enabled": enabled,

            "verbal_announcement": verbal,

            "poll_interval_seconds": poll_interval,

            "running": running,

        }

    def save_email_monitor_config(

        self,

        enabled: bool = True,

        verbal_announcement: bool = True,

        poll_interval_seconds: float = 60.0,

    ) -> dict:

        """Updates and persists background OAuth/IMAP email monitoring settings."""

        from core.config import load_settings, save_settings

        settings = load_settings()

        email_cfg = settings.setdefault("integrations", {}).setdefault("email_monitoring", {})

        email_cfg["enabled"] = bool(enabled)

        email_cfg["verbal_announcement"] = bool(verbal_announcement)

        email_cfg["poll_interval_seconds"] = max(15.0, float(poll_interval_seconds or 60.0))

        save_settings(settings)

        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "set_email_monitor_config"):

            self._engine.set_email_monitor_config(

                enabled=email_cfg["enabled"],

                verbal_announcement=email_cfg["verbal_announcement"],

                poll_interval_seconds=email_cfg["poll_interval_seconds"],

            )

        status = self.get_email_monitor_status()

        self.notify("oauth_status_updated", self.get_oauth_status())

        return status

    def get_recent_email_alerts(self, limit: int = 10) -> dict:

        """Returns recently captured background email monitor alerts."""

        monitor = getattr(self._engine, "email_monitor", None) if hasattr(self, "_engine") and self._engine else None

        if not monitor:

            return {"success": True, "count": 0, "alerts": []}

        alerts = monitor.get_recent(limit=int(limit or 10))

        return {"success": True, "count": len(alerts), "alerts": alerts}

    def clear_email_alerts(self) -> dict:

        """Clears recent background email alert history."""

        monitor = getattr(self._engine, "email_monitor", None) if hasattr(self, "_engine") and self._engine else None

        if monitor:

            monitor.clear_history()

        return {"success": True, "message": "Email alert history cleared."}



    # =========================================================================

    # Live Streaming & Broadcaster Watchlist RPC

    # =========================================================================



    def get_streaming_status(self) -> dict:

        """Returns the current connection, configuration, and watchlist status of the LiveStream service."""

        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "settings") and isinstance(self._engine.settings, dict):

            settings = self._engine.settings

        else:

            from core.config import load_settings

            settings = load_settings()

        stream_cfg = settings.get("integrations", {}).get("streaming", {})

        enabled = bool(stream_cfg.get("enabled", True))

        poll_interval = int(stream_cfg.get("poll_interval_seconds", 120))

        verbal = bool(stream_cfg.get("verbal_alerts", False))



        tw_cfg = stream_cfg.get("twitch", {})

        tw_enabled = bool(tw_cfg.get("enabled", True))

        tw_id = str(tw_cfg.get("client_id", "") or os.getenv("TWITCH_CLIENT_ID", "")).strip()

        tw_sec = str(tw_cfg.get("client_secret", "") or os.getenv("TWITCH_CLIENT_SECRET", "")).strip()

        tw_channels = list(tw_cfg.get("channels", []))

        tw_tokens = self._oauth_mgr.vault.get_provider_tokens("twitch") if self._oauth_mgr else None

        tw_oauth_connected = bool(tw_tokens and tw_tokens.get("access_token"))

        tw_account_name = tw_tokens.get("account_name", "") if tw_tokens else ""

        tw_connected = bool((tw_id and tw_sec) or tw_oauth_connected)



        tw_id_masked = ""

        if tw_id:

            tw_id_masked = tw_id[:3] + "••••" + tw_id[-3:] if len(tw_id) > 6 else "••••••••"



        yt_cfg = stream_cfg.get("youtube", {})

        yt_enabled = bool(yt_cfg.get("enabled", True))

        yt_key = str(yt_cfg.get("api_key", "") or os.getenv("YOUTUBE_API_KEY", "")).strip()

        yt_channels = list(yt_cfg.get("channels", []))

        google_tokens = self._oauth_mgr.vault.get_provider_tokens("google") if self._oauth_mgr else None

        google_has_yt = bool(google_tokens and any("youtube" in s for s in google_tokens.get("scopes", [])))

        yt_oauth_connected = bool(google_tokens and google_has_yt)

        yt_connected = bool(yt_key or yt_oauth_connected)



        yt_key_masked = ""

        if yt_key:

            yt_key_masked = yt_key[:4] + "••••" + yt_key[-4:] if len(yt_key) > 8 else "••••••••"



        running = bool(hasattr(self._engine, "livestream_service") and self._engine.livestream_service)



        return {

            "success": True,

            "enabled": enabled,

            "poll_interval_seconds": poll_interval,

            "verbal_alerts": verbal,

            "running": running,

            "twitch_enabled": tw_enabled,

            "twitch_connected": tw_connected,

            "twitch_oauth_connected": tw_oauth_connected,

            "twitch_account_name": tw_account_name,

            "twitch_client_id": tw_id,

            "twitch_client_id_masked": tw_id_masked,

            "has_twitch_id": bool(tw_id),

            "has_twitch_secret": bool(tw_sec),

            "twitch_channels": tw_channels,

            "youtube_enabled": yt_enabled,

            "youtube_connected": yt_connected,

            "youtube_oauth_connected": yt_oauth_connected,

            "youtube_api_key": yt_key,

            "youtube_api_key_masked": yt_key_masked,

            "has_youtube_key": bool(yt_key),

            "youtube_channels": yt_channels,

            "total_channels": len(tw_channels) + len(yt_channels)

        }



    def save_streaming_config(

        self,

        enabled: Optional[bool] = None,

        poll_interval_seconds: Optional[int] = None,

        verbal_alerts: Optional[bool] = None,

        twitch_client_id: Optional[str] = None,

        twitch_client_secret: Optional[str] = None,

        youtube_api_key: Optional[str] = None

    ) -> dict:

        """Saves Live Streaming configuration and credentials."""

        from core.config import save_settings

        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "settings") and isinstance(self._engine.settings, dict):

            settings = self._engine.settings

        else:

            from core.config import load_settings

            settings = load_settings()

        stream_cfg = settings.setdefault("integrations", {}).setdefault("streaming", {})



        if enabled is not None:

            stream_cfg["enabled"] = bool(enabled)

        if poll_interval_seconds is not None:

            stream_cfg["poll_interval_seconds"] = max(10, int(poll_interval_seconds or 120))

        if verbal_alerts is not None:

            stream_cfg["verbal_alerts"] = bool(verbal_alerts)



        tw_cfg = stream_cfg.setdefault("twitch", {})

        if twitch_client_id is not None:

            clean_tw_id = str(twitch_client_id).strip()

            if "••••" not in clean_tw_id:

                tw_cfg["client_id"] = clean_tw_id



        if twitch_client_secret is not None:

            clean_tw_sec = str(twitch_client_secret).strip()

            if "••••" not in clean_tw_sec:

                tw_cfg["client_secret"] = clean_tw_sec



        yt_cfg = stream_cfg.setdefault("youtube", {})

        if youtube_api_key is not None:

            clean_yt_key = str(youtube_api_key).strip()

            if "••••" not in clean_yt_key:

                yt_cfg["api_key"] = clean_yt_key



        save_settings(settings)



        if hasattr(self, "_engine") and self._engine:

            if hasattr(self._engine, "set_streaming_config"):

                self._engine.set_streaming_config(

                    enabled=stream_cfg.get("enabled"),

                    poll_interval_seconds=stream_cfg.get("poll_interval_seconds"),

                    verbal_alerts=stream_cfg.get("verbal_alerts"),

                    twitch=tw_cfg,

                    youtube=yt_cfg

                )

            if hasattr(self._engine, "settings") and isinstance(self._engine.settings, dict):

                self._engine.settings.setdefault("integrations", {})["streaming"] = stream_cfg



        status = self.get_streaming_status()

        self.notify("streaming_status_changed", status)

        self.notify("oauth_status_updated", self.get_oauth_status())

        return status



    def add_stream_channel(self, platform: str, channel_name: str, display_name: str = "") -> dict:

        """Adds a channel or creator to the streaming watchlist."""

        plat = (platform or "").lower().strip()

        ch = (channel_name or "").strip()

        if not ch or plat not in ("twitch", "youtube"):

            return {"success": False, "error": "Invalid platform or channel name."}



        from core.config import save_settings

        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "settings") and isinstance(self._engine.settings, dict):

            settings = self._engine.settings

        else:

            from core.config import load_settings

            settings = load_settings()

        stream_cfg = settings.setdefault("integrations", {}).setdefault("streaming", {})



        if plat == "twitch":

            tw_channels = stream_cfg.setdefault("twitch", {}).setdefault("channels", [])

            clean_ch = ch.lower()

            if clean_ch not in [c.lower() for c in tw_channels]:

                tw_channels.append(clean_ch)

        else:

            yt_channels = stream_cfg.setdefault("youtube", {}).setdefault("channels", [])

            exists = any(

                entry.get("channel_id") == ch or (entry.get("name") and entry.get("name", "").lower() == ch.lower())

                for entry in yt_channels

            )

            if not exists:

                yt_channels.append({"name": display_name or ch, "channel_id": ch})



        save_settings(settings)



        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "settings"):

            self._engine.settings.setdefault("integrations", {})["streaming"] = stream_cfg



        status = self.get_streaming_status()

        self.notify("streaming_status_changed", status)

        self.notify("oauth_status_updated", self.get_oauth_status())

        return {"success": True, "status": status}



    def remove_stream_channel(self, platform: str, channel_name: str) -> dict:

        """Removes a channel or creator from the streaming watchlist."""

        plat = (platform or "").lower().strip()

        ch = (channel_name or "").strip()

        if not ch or plat not in ("twitch", "youtube"):

            return {"success": False, "error": "Invalid platform or channel name."}



        from core.config import save_settings

        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "settings") and isinstance(self._engine.settings, dict):

            settings = self._engine.settings

        else:

            from core.config import load_settings

            settings = load_settings()

        stream_cfg = settings.setdefault("integrations", {}).setdefault("streaming", {})



        if plat == "twitch":

            tw_channels = stream_cfg.setdefault("twitch", {}).setdefault("channels", [])

            tw_channels[:] = [c for c in tw_channels if c.lower() != ch.lower()]

        else:

            yt_channels = stream_cfg.setdefault("youtube", {}).setdefault("channels", [])

            yt_channels[:] = [

                entry for entry in yt_channels

                if entry.get("channel_id") != ch and entry.get("name", "").lower() != ch.lower()

            ]



        save_settings(settings)



        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "settings"):

            self._engine.settings.setdefault("integrations", {})["streaming"] = stream_cfg



        status = self.get_streaming_status()

        self.notify("streaming_status_changed", status)

        self.notify("oauth_status_updated", self.get_oauth_status())

        return {"success": True, "status": status}



    def check_live_streams_now(self, platform: Optional[str] = None) -> dict:

        """Polls current live status across watchlist creators immediately."""

        service = getattr(self._engine, "livestream_service", None) if hasattr(self, "_engine") and self._engine else None

        if not service:

            return {"success": False, "error": "Live streaming service is not initialized."}



        import asyncio

        loop = getattr(self._engine, "_loop", None) or getattr(self, "_loop", None)



        settings = self.get_streaming_status()

        plat = (platform or "").lower().strip()



        async def _query():

            results = []

            if plat in ("", "twitch") and service.has_twitch():

                channels = settings.get("twitch_channels", [])

                if channels:

                    tw_res = await service.get_twitch_streams(channels)

                    results.extend([t.to_dict() for t in tw_res])

            if plat in ("", "youtube") and service.has_youtube():

                for entry in settings.get("youtube_channels", []):

                    cid = entry.get("channel_id")

                    cname = entry.get("name", "")

                    if cid:

                        yt_res = await service.check_youtube_channel_live(cid, cname)

                        results.append(yt_res.to_dict())

            return results



        try:

            if isinstance(loop, asyncio.AbstractEventLoop) and loop.is_running():

                future = asyncio.run_coroutine_threadsafe(_query(), loop)

                res = future.result(timeout=10.0)

            else:

                res = asyncio.run(_query())

            return {

                "success": True,

                "streams": res,

                "live_streams": [r for r in res if r.get("is_live")],

                "offline_streams": [r for r in res if not r.get("is_live")]

            }

        except Exception as e:

            return {"success": False, "error": str(e)}



    def sync_twitch_follows(self) -> dict:

        """Syncs creators followed on Twitch into Aether's watchlist."""

        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "sync_twitch_follows_to_watchlist"):

            res = self._engine.sync_twitch_follows_to_watchlist()

        else:

            from core.oauth_manager import get_oauth_manager

            mgr = get_oauth_manager()

            channels = mgr.fetch_twitch_followed_channels()

            if not channels:

                return {"success": False, "error": "No followed channels found or Twitch not connected."}

            from core.config import load_settings, save_settings

            settings = load_settings()

            stream_cfg = settings.setdefault("integrations", {}).setdefault("streaming", {})

            tw_cfg = stream_cfg.setdefault("twitch", {})

            current = list(tw_cfg.get("channels", []))

            current_lower = {c.lower() for c in current}

            added = 0

            for ch in channels:

                login = ch.get("login") or ch.get("name", "")

                if login and login.lower() not in current_lower:

                    current.append(login)

                    current_lower.add(login.lower())

                    added += 1

            tw_cfg["channels"] = current

            save_settings(settings)

            res = {"success": True, "added_count": added, "total_count": len(current)}



        status = self.get_streaming_status()

        self.notify("streaming_status_changed", status)

        self.notify("oauth_status_updated", self.get_oauth_status())

        res["status"] = status

        return res



    def sync_youtube_subscriptions(self) -> dict:

        """Syncs subscribed YouTube channels into Aether's watchlist."""

        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "sync_youtube_subscriptions_to_watchlist"):

            res = self._engine.sync_youtube_subscriptions_to_watchlist()

        else:

            from core.oauth_manager import get_oauth_manager

            mgr = get_oauth_manager()

            channels = mgr.fetch_youtube_subscriptions()

            if not channels:

                return {"success": False, "error": "No subscribed channels found or Google YouTube not connected."}

            from core.config import load_settings, save_settings

            settings = load_settings()

            stream_cfg = settings.setdefault("integrations", {}).setdefault("streaming", {})

            yt_cfg = stream_cfg.setdefault("youtube", {})

            current = list(yt_cfg.get("channels", []))

            existing_ids = {c.get("channel_id") for c in current if isinstance(c, dict)}

            added = 0

            for ch in channels:

                cid = ch.get("channel_id")

                if cid and cid not in existing_ids:

                    current.append({"name": ch.get("name", cid), "channel_id": cid})

                    existing_ids.add(cid)

                    added += 1

            yt_cfg["channels"] = current

            save_settings(settings)

            res = {"success": True, "added_count": added, "total_count": len(current)}



        status = self.get_streaming_status()

        self.notify("streaming_status_changed", status)

        self.notify("oauth_status_updated", self.get_oauth_status())

        res["status"] = status

        return res



    def get_replay_capture_config(self) -> dict:

        """Returns the current replay capture configuration."""

        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "settings") and isinstance(self._engine.settings, dict):

            settings = self._engine.settings

        else:

            from core.config import load_settings

            settings = load_settings()

        replay_cfg = settings.get("integrations", {}).get("replay_capture", {})

        obs_cfg = replay_cfg.get("obs", {})

        return {

            "success": True,

            "enabled": bool(replay_cfg.get("enabled", True)),

            "backend": str(replay_cfg.get("backend", "shadowplay")),

            "spoken_confirmation": str(replay_cfg.get("spoken_confirmation", "Captured.")),

            "obs": {

                "host": str(obs_cfg.get("host", "localhost")),

                "port": int(obs_cfg.get("port", 4455)),

                "password": str(obs_cfg.get("password", ""))

            }

        }



    def save_replay_capture_config(

        self,

        enabled: Optional[bool] = None,

        backend: Optional[str] = None,

        spoken_confirmation: Optional[str] = None,

        obs_host: Optional[str] = None,

        obs_port: Optional[int] = None,

        obs_password: Optional[str] = None

    ) -> dict:

        """Saves and applies replay capture settings."""

        from core.config import save_settings

        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "settings") and isinstance(self._engine.settings, dict):

            settings = self._engine.settings

        else:

            from core.config import load_settings

            settings = load_settings()

        replay_cfg = settings.setdefault("integrations", {}).setdefault("replay_capture", {})

        if enabled is not None:

            replay_cfg["enabled"] = bool(enabled)

        if backend is not None:

            replay_cfg["backend"] = backend.lower().strip()

        if spoken_confirmation is not None:

            replay_cfg["spoken_confirmation"] = spoken_confirmation



        obs_cfg = replay_cfg.setdefault("obs", {})

        if obs_host is not None:

            obs_cfg["host"] = obs_host.strip()

        if obs_port is not None:

            obs_cfg["port"] = int(obs_port)

        if obs_password is not None and "••••" not in obs_password:

            obs_cfg["password"] = obs_password



        save_settings(settings)

        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "set_replay_capture_config"):

            self._engine.set_replay_capture_config(

                enabled=replay_cfg.get("enabled"),

                backend=replay_cfg.get("backend"),

                spoken_confirmation=replay_cfg.get("spoken_confirmation"),

                obs=obs_cfg

            )

        return self.get_replay_capture_config()



    def get_media_controller_config(self) -> dict:

        """Returns the current media controller configuration."""

        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "settings") and isinstance(self._engine.settings, dict):

            settings = self._engine.settings

        elif hasattr(self, "engine") and self.engine and hasattr(self.engine, "settings") and isinstance(self.engine.settings, dict):

            settings = self.engine.settings

        else:

            from core.config import load_settings

            settings = load_settings()

        media_cfg = settings.get("integrations", {}).get("media_controller", {})

        return {

            "success": True,

            "enabled": bool(media_cfg.get("enabled", True)),

            "verbal_acknowledgement": bool(media_cfg.get("verbal_acknowledgement", True)),

            "supported_commands": list(media_cfg.get("supported_commands", ["play", "pause", "toggle", "next", "previous", "query"]))

        }



    def save_media_controller_config(

        self,

        enabled: Optional[bool] = None,

        verbal_acknowledgement: Optional[bool] = None

    ) -> dict:

        """Updates and persists media controller settings."""

        settings = None

        if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "settings") and isinstance(self._engine.settings, dict):

            settings = self._engine.settings

        elif hasattr(self, "engine") and self.engine and hasattr(self.engine, "settings") and isinstance(self.engine.settings, dict):

            settings = self.engine.settings

        else:

            from core.config import load_settings

            settings = load_settings()



        media_cfg = settings.setdefault("integrations", {}).setdefault("media_controller", {})

        if enabled is not None:

            media_cfg["enabled"] = bool(enabled)

        if verbal_acknowledgement is not None:

            media_cfg["verbal_acknowledgement"] = bool(verbal_acknowledgement)



        from core.config import save_settings

        save_settings(settings)



        engine_inst = getattr(self, "_engine", None) or getattr(self, "engine", None)

        if engine_inst and hasattr(engine_inst, "media_service") and engine_inst.media_service:

            if verbal_acknowledgement is not None:

                engine_inst.media_service.verbal_ack = bool(verbal_acknowledgement)



        return self.get_media_controller_config()



    def get_audio_ducking_settings(self) -> dict:

        """Retrieves current audio ducking settings."""

        settings = getattr(self.engine, "settings", None)

        if settings is None or not isinstance(settings, dict):

            from core.config import load_settings

            settings = load_settings()

        cfg = settings.get("audio_ducking", {})

        return {

            "enabled": cfg.get("enabled", True),

            "duck_percentage": cfg.get("duck_percentage", 60)

        }



    def update_audio_ducking_settings(self, enabled: bool, duck_percentage: int) -> dict:

        """Persists and updates audio ducking parameters in engine settings and runtime service."""

        if not hasattr(self.engine, "settings") or not isinstance(self.engine.settings, dict):

            from core.config import load_settings

            self.engine.settings = load_settings()



        if "audio_ducking" not in self.engine.settings:

            self.engine.settings["audio_ducking"] = {}



        self.engine.settings["audio_ducking"]["enabled"] = bool(enabled)

        self.engine.settings["audio_ducking"]["duck_percentage"] = int(duck_percentage)



        if hasattr(self.engine, "save_settings") and callable(self.engine.save_settings):

            self.engine.save_settings()

        else:

            from core.config import save_settings

            save_settings(self.engine.settings)



        if hasattr(self.engine, "audio_ducking_service") and self.engine.audio_ducking_service:

            self.engine.audio_ducking_service.update_settings(

                enabled=bool(enabled),

                duck_percentage=int(duck_percentage)

            )



        return {"status": "success", "settings": self.engine.settings["audio_ducking"]}

    def save_hardware_telemetry_settings(
        self,
        alerts_enabled: bool,
        cpu_temp_threshold_c: int,
        gpu_temp_threshold_c: int,
        vram_threshold_pct: int,
        sustained_seconds: Optional[int] = None
    ) -> dict:
        """Persists and hot-updates CPU/GPU thermal, VRAM, and sustained-duration warning settings."""
        with self._config_lock:
            hw_cfg = self._config.setdefault("hardware_telemetry", {})
            hw_cfg["alerts_enabled"] = bool(alerts_enabled)
            hw_cfg["verbal_warnings"] = bool(alerts_enabled)
            hw_cfg["cpu_temp_alert_threshold_c"] = int(float(cpu_temp_threshold_c))
            hw_cfg["gpu_temp_alert_threshold_c"] = int(float(gpu_temp_threshold_c))
            hw_cfg["vram_alert_threshold_pct"] = int(float(vram_threshold_pct))
            if sustained_seconds is not None:
                hw_cfg["alert_sustained_seconds"] = max(5, min(30, int(float(sustained_seconds))))
            else:
                hw_cfg.setdefault("alert_sustained_seconds", 15)
            save_config_atomic(self._config, self._config_path)

        engine_inst = getattr(self, "_engine", None) or getattr(self, "engine", None)
        try:
            from core.config import load_settings, save_settings
            if engine_inst and hasattr(engine_inst, "settings") and isinstance(engine_inst.settings, dict):
                engine_inst.settings.setdefault("hardware_telemetry", {}).update(hw_cfg)
                save_settings(engine_inst.settings)
            else:
                s_data = load_settings()
                s_data.setdefault("hardware_telemetry", {}).update(hw_cfg)
                save_settings(s_data)
            if engine_inst and hasattr(engine_inst, "hardware_service") and engine_inst.hardware_service:
                engine_inst.hardware_service.update_alert_settings(
                    alerts_enabled=hw_cfg["alerts_enabled"],
                    cpu_temp_threshold_c=hw_cfg["cpu_temp_alert_threshold_c"],
                    gpu_temp_threshold_c=hw_cfg["gpu_temp_alert_threshold_c"],
                    vram_threshold_pct=hw_cfg["vram_alert_threshold_pct"],
                    sustained_seconds=hw_cfg["alert_sustained_seconds"],
                )
        except Exception as e:
            logger.debug(f"[GUI_BRIDGE] save_hardware_telemetry_settings error: {e}")

        return {"success": True, "hardware_telemetry": hw_cfg}

    # ------------------------------------------------------------------
    # Projects Workspace & NotebookLM Cloud RAG Endpoints
    # ------------------------------------------------------------------
    def get_projects_data(self) -> Dict[str, Any]:
        projects = self.project_service.data.get("projects", {})
        for pid, proj in list(projects.items()):
            nb = proj.get("notebooklm", {})
            if nb.get("sync_enabled"):
                try:
                    self.project_service.sync_with_google_drive(pid)
                except Exception as e:
                    logger.debug(f"[GUI_BRIDGE] Google Drive sync warning for '{pid}': {e}")
                if not nb.get("notebook_id"):
                    nb_id = self._ensure_project_cloud_store(pid)
                    if nb_id:
                        try:
                            p_dir = self.project_service.get_project_dir(pid)
                            files = self.project_service.list_files(pid)
                            sources = [
                                {"displayName": f["filename"], "filePath": os.path.join(p_dir, f["filename"])}
                                for f in files
                                if f.get("filename") and os.path.exists(os.path.join(p_dir, f["filename"]))
                            ]
                            if sources:
                                self._run_async_coro(
                                    self.notebooklm_service.batch_create_sources(nb_id, sources),
                                    close_services=(self.notebooklm_service,),
                                )
                        except Exception as e:
                            logger.debug(f"[GUI_BRIDGE] Auto-heal cloud sync warning for '{pid}': {e}")
        return {
            "active_project": self.project_service.data.get("active_project"),
            "projects": self.project_service.data.get("projects", {}),
            "google_drive_root": self.project_service.find_google_drive_root(),
        }

    def set_active_project(self, project_id: str) -> Dict[str, Any]:
        if project_id in self.project_service.data["projects"]:
            self.project_service.data["active_project"] = project_id
            self.project_service.save_manifest()
            proj_name = self.project_service.data["projects"][project_id].get("name", project_id)
            history = []
            if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "set_active_workspace"):
                try:
                    history = self._engine.set_active_workspace("project", project_id, proj_name)
                except Exception as e:
                    logger.debug(f"[GUI_BRIDGE] set_active_workspace error: {e}")
            elif hasattr(self, "_engine") and self._engine and hasattr(self._engine, "invalidate_live_session"):
                try:
                    self._engine.invalidate_live_session(reason=f"Active Project Changed: {project_id}")
                except Exception:
                    pass
            formatted = self._format_history_turns(history)
            return {"status": "success", "active_project": project_id, "history": formatted}
        return {"status": "error", "message": "Project not found"}

    def create_project(self, project_id: str, name: str, description: str = "", instructions: str = "") -> Dict[str, Any]:
        res = self.project_service.create_project(project_id, name, description, instructions)
        if res.get("status") == "success":
            pid = res["project"]["id"]
            self.project_service.data["active_project"] = pid
            self.project_service.save_manifest()
        return res

    def _ensure_project_cloud_store(self, project_id: str) -> Optional[str]:
        """Ensures the project has a linked Gemini Cloud FileSearchStore and returns its store name."""
        if not hasattr(self, "notebooklm_service") or not self.notebooklm_service:
            return None
        if not self.notebooklm_service.is_configured():
            try:
                enc = self._config.get("api", {}).get("api_key_encrypted", "")
                if enc:
                    self.notebooklm_service.api_key = unprotect_secret(enc) or ""
            except Exception:
                pass
        if not self.notebooklm_service.is_configured():
            return None

        proj = self.project_service.data.get("projects", {}).get(project_id, {})
        nb = proj.get("notebooklm", {})
        nb_id = nb.get("notebook_id")
        if not nb_id:
            try:
                nb_resp = self._run_async_coro(
                    self.notebooklm_service.create_notebook(proj.get("name", project_id)),
                    close_services=(self.notebooklm_service,),
                )
                if isinstance(nb_resp, dict):
                    nb_id = nb_resp.get("name") or nb_resp.get("notebookId") or nb_resp.get("id")
                    if nb_id:
                        self.project_service.update_project(project_id, notebook_id=nb_id)
            except Exception as e:
                logger.debug(f"[GUI_BRIDGE] Auto-create cloud store warning: {e}")
        return nb_id

    def update_project(
        self,
        project_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        custom_instructions: Optional[str] = None,
        sync_enabled: Optional[bool] = None,
        notebook_id: Optional[str] = None
    ) -> Dict[str, Any]:
        res = self.project_service.update_project(
            project_id=project_id,
            name=name,
            description=description,
            custom_instructions=custom_instructions,
            sync_enabled=sync_enabled,
            notebook_id=notebook_id
        )
        if res.get("status") == "success" and sync_enabled:
            self.sync_project(project_id)
            res["project"] = self.project_service.data.get("projects", {}).get(project_id, res.get("project"))
        return res

    def sync_project(self, project_id: str) -> Dict[str, Any]:
        """
        Triggers an immediate two-way Google Drive sync + Gemini Cloud RAG sync for the project.
        """
        if project_id not in self.project_service.data.get("projects", {}):
            return {"status": "error", "message": f"Project '{project_id}' not found."}

        drive_res = self.project_service.sync_with_google_drive(project_id)
        nb_id = self._ensure_project_cloud_store(project_id)
        cloud_synced = False
        if nb_id:
            try:
                p_dir = self.project_service.get_project_dir(project_id)
                files = self.project_service.list_files(project_id)
                sources = []
                for fmeta in files:
                    fname = fmeta.get("filename")
                    fpath = os.path.join(p_dir, fname) if fname else ""
                    if fpath and os.path.exists(fpath):
                        sources.append({"displayName": fname, "filePath": fpath})
                if sources:
                    cloud_synced = bool(
                        self._run_async_coro(
                            self.notebooklm_service.batch_create_sources(nb_id, sources),
                            close_services=(self.notebooklm_service,),
                        )
                    )
                else:
                    cloud_synced = True
            except Exception as e:
                logger.debug(f"[GUI_BRIDGE] sync_project cloud sync warning: {e}")

        proj = self.project_service.data.get("projects", {}).get(project_id, {})
        return {
            "status": "success",
            "project": proj,
            "files": self.project_service.list_files(project_id),
            "drive_sync": drive_res,
            "cloud_synced": cloud_synced,
        }

    def open_project_drive_folder(self, project_id: str) -> Dict[str, Any]:
        """Opens the project's synced Google Drive folder (or local folder fallback) in Windows File Explorer."""
        drive_dir = self.project_service.get_drive_project_dir(project_id)
        target_dir = drive_dir if drive_dir else self.project_service.get_project_dir(project_id)
        try:
            os.makedirs(target_dir, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(target_dir)
            return {"status": "success", "path": target_dir}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def open_project_notebooklm(self, project_id: Optional[str] = None) -> Dict[str, Any]:
        """Opens Google NotebookLM web interface in the default browser."""
        import webbrowser
        url = "https://notebooklm.google.com"
        if project_id and project_id in self.project_service.data.get("projects", {}):
            custom_url = self.project_service.data["projects"][project_id].get("notebooklm", {}).get("web_url")
            if custom_url:
                url = custom_url
        try:
            webbrowser.open(url)
            return {"status": "success", "url": url}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def delete_project(self, project_id: str) -> Dict[str, Any]:
        proj = self.project_service.data.get("projects", {}).get(project_id, {})
        nb_id = proj.get("notebooklm", {}).get("notebook_id")
        if nb_id and hasattr(self, "notebooklm_service") and self.notebooklm_service and self.notebooklm_service.is_configured():
            try:
                self._run_async_coro(
                    self.notebooklm_service.delete_notebook(nb_id),
                    close_services=(self.notebooklm_service,),
                )
            except Exception:
                pass
        ok = self.project_service.delete_project(project_id)
        return {
            "status": "success" if ok else "error",
            "deleted": ok,
            "active_project": self.project_service.data.get("active_project")
        }

    def list_project_files(self, project_id: str) -> List[Dict[str, Any]]:
        return self.project_service.list_files(project_id)

    def upload_project_file(self, project_id: str, filename: str, base64_content: str) -> Dict[str, Any]:
        import base64
        raw_bytes = base64.b64decode(base64_content)
        meta = self.project_service.add_file(project_id, filename, raw_bytes)
        p_data = self.project_service.data.get("projects", {}).get(project_id, {})
        nb_cfg = p_data.get("notebooklm", {})
        if nb_cfg.get("sync_enabled"):
            nb_id = self._ensure_project_cloud_store(project_id)
            if nb_id:
                try:
                    p_dir = self.project_service.get_project_dir(project_id)
                    saved_fname = meta.get("filename", filename)
                    fpath = os.path.join(p_dir, saved_fname)
                    self._run_async_coro(
                        self.notebooklm_service.batch_create_sources(
                            nb_id,
                            [{"displayName": saved_fname, "filePath": fpath}]
                        ),
                        close_services=(self.notebooklm_service,),
                    )
                except Exception as e:
                    logger.debug(f"[GUI_BRIDGE] Cloud file sync warning: {e}")
        return {"status": "success", "file": meta}

    def delete_project_file(self, project_id: str, filename: str) -> Dict[str, Any]:
        ok = self.project_service.delete_file(project_id, filename)
        return {"status": "success" if ok else "error", "deleted": ok}

    def open_project_file(self, project_id: str, filename: str) -> Dict[str, Any]:
        """Opens a local project document using the operating system's default viewer."""
        try:
            target = self.project_service.resolve_project_file_path(project_id, filename)
        except PermissionError:
            logger.warning(f"[GUI_BRIDGE SECURITY] Blocked path traversal in open_project_file: {filename}")
            return {"status": "error", "message": "Access denied: invalid project file path."}
        if not os.path.exists(target):
            return {"status": "error", "message": f"File '{filename}' does not exist on disk."}
        try:
            if hasattr(os, "startfile"):
                os.startfile(target)
            else:
                import subprocess, sys
                if sys.platform == "darwin":
                    subprocess.Popen(["open", target])
                else:
                    subprocess.Popen(["xdg-open", target])
            logger.info(f"[GUI_BRIDGE] Opened local project file: {target}")
            return {"status": "success", "path": target}
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] Failed to open project file '{filename}': {e}")
            return {"status": "error", "message": str(e)}

    def open_project_file_location(self, project_id: str, filename: str) -> Dict[str, Any]:
        """Reveals the project file in Windows File Explorer with the item selected."""
        p_dir = self.project_service.get_project_dir(project_id)
        try:
            target = self.project_service.resolve_project_file_path(project_id, filename)
        except PermissionError:
            logger.warning(f"[GUI_BRIDGE SECURITY] Blocked path traversal in open_project_file_location: {filename}")
            return {"status": "error", "message": "Access denied: invalid project file path."}
        try:
            if sys.platform == "win32" and os.path.exists(target):
                import subprocess
                subprocess.Popen(["explorer.exe", f"/select,{os.path.normpath(target)}"])
            elif os.path.exists(p_dir):
                if hasattr(os, "startfile"):
                    os.startfile(p_dir)
                else:
                    import subprocess, sys
                    if sys.platform == "darwin":
                        subprocess.Popen(["open", p_dir])
                    else:
                        subprocess.Popen(["xdg-open", p_dir])
            return {"status": "success"}
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] Failed to reveal file '{filename}': {e}")
            return {"status": "error", "message": str(e)}

    def open_external_url(self, url: str) -> Dict[str, Any]:
        """Opens an external HTTP/HTTPS website URL in the user's default browser."""
        import urllib.parse
        import webbrowser
        clean_url = (url or "").strip()
        if not clean_url:
            return {"status": "error", "message": "No URL provided."}
        if "://" not in clean_url and not clean_url.lower().startswith(("mailto:", "javascript:", "file:", "data:", "vbscript:")):
            clean_url = "https://" + clean_url
        parsed = urllib.parse.urlparse(clean_url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            logger.warning(f"[GUI_BRIDGE SECURITY] Blocked non-HTTP(S) external URL: {clean_url!r}")
            return {"status": "error", "message": "Only http:// and https:// URLs are permitted."}
        try:
            webbrowser.open(clean_url)
            logger.info(f"[GUI_BRIDGE] Opened external URL: {clean_url}")
            return {"status": "success", "url": clean_url}
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] Failed to open external URL '{clean_url}': {e}")
            return {"status": "error", "message": str(e)}

    def read_project_file(self, project_id: str, filename: str) -> Dict[str, Any]:
        try:
            target = self.project_service.resolve_project_file_path(project_id, filename)
        except PermissionError:
            logger.warning(f"[GUI_BRIDGE SECURITY] Blocked path traversal in read_project_file: {filename}")
            return {"status": "error", "message": "Access denied: invalid project file path."}
        if not os.path.exists(target):
            return {"status": "error", "message": "File not found"}
        try:
            content = self.project_service.extract_file_text(target)
            return {"status": "success", "filename": filename, "content": content}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def import_project_url(self, project_id: str, url: str) -> Dict[str, Any]:
        res = self._run_async_coro(
            self.project_service.import_url(project_id, url),
            close_services=(self.project_service,),
        )
        if res.get("status") == "success":
            p_data = self.project_service.data.get("projects", {}).get(project_id, {})
            if p_data.get("notebooklm", {}).get("sync_enabled"):
                nb_id = self._ensure_project_cloud_store(project_id)
                meta = res.get("metadata") or res.get("file") or {}
                saved_fname = meta.get("filename")
                if nb_id and saved_fname:
                    try:
                        p_dir = self.project_service.get_project_dir(project_id)
                        fpath = os.path.join(p_dir, saved_fname)
                        self._run_async_coro(
                            self.notebooklm_service.batch_create_sources(
                                nb_id,
                                [{"displayName": saved_fname, "filePath": fpath}]
                            ),
                            close_services=(self.notebooklm_service,),
                        )
                    except Exception as e:
                        logger.debug(f"[GUI_BRIDGE] Cloud URL sync warning: {e}")
        return res

    # ------------------------------------------------------------------
    # Universal Tabletop Roleplaying Engine (Role Play Tab) Endpoints
    # ------------------------------------------------------------------
    def get_ttrpg_data(self) -> Dict[str, Any]:
        self.ttrpg_service.load_manifest()
        self.ttrpg_service.load_active_campaign_state()
        active_id = self.ttrpg_service.manifest.get("active_campaign")
        campaigns = self.ttrpg_service.manifest.get("campaigns", {})
        eng = getattr(self, "_engine", getattr(self, "engine", None))
        is_adv = bool(getattr(eng, "is_ttrpg_session_active", lambda: False)())

        world_state = None
        world_context = ""
        if active_id:
            try:
                from core.campaign.campaign_manager import CampaignManager
                c_mgr = CampaignManager(campaign_id=active_id, storage_root=self.ttrpg_service.data_dir)
                world_state = c_mgr.serialize_manifest()
                world_context = c_mgr.generate_prompt_context()
            except Exception as e:
                logger.debug(f"[GUIBridge] World state load error for '{active_id}': {e}")

        from core.model_discovery import get_ttrpg_model_options
        with self._config_lock:
            ttrpg_ep = (
                self._config.get("ttrpg_model_endpoint")
                or self._config.get("api", {}).get("ttrpg_model_id")
                or "gemini-4-hydrogen:medium"
            )

        return {
            "active_campaign": active_id,
            "campaigns": campaigns,
            "active_campaign_meta": campaigns.get(active_id) if active_id else None,
            "dice_mode": campaigns.get(active_id, {}).get("dice_mode", "digital") if active_id else "digital",
            "state": self.ttrpg_service.active_state,
            "is_adventure_active": is_adv,
            "ttrpg_model_endpoint": ttrpg_ep,
            "ttrpg_model_options": get_ttrpg_model_options(),
            "projects": self.project_service.data.get("projects", {}) if hasattr(self, "project_service") and self.project_service else {},
            "world_state": world_state,
            "world_context": world_context
        }

    def save_ttrpg_model_endpoint(self, model_endpoint: str) -> Dict[str, Any]:
        """Persists the user's preferred Cortex model/thinking level for TTRPG sessions."""
        chosen = str(model_endpoint or "").strip() or "gemini-4-hydrogen:medium"
        with self._config_lock:
            self._config["ttrpg_model_endpoint"] = chosen
            self._config.setdefault("api", {})["ttrpg_model_id"] = chosen
            save_config_atomic(self._config, self._config_path)
        eng = getattr(self, "_engine", getattr(self, "engine", None))
        if eng and hasattr(eng, "is_ttrpg_session_active") and eng.is_ttrpg_session_active():
            eng._reset_context_flag = True
        logger.info(f"[TTRPG] Saved preferred DM Engine profile: {chosen}")
        return {"success": True, "ttrpg_model_endpoint": chosen}

    def set_active_campaign(self, campaign_id: str) -> Dict[str, Any]:
        res = self.ttrpg_service.set_active_campaign(campaign_id)
        if res.get("status") == "success":
            try:
                from core.campaign.campaign_manager import CampaignManager
                import tools.campaign_tools as ct
                ct._CAMPAIGN = CampaignManager(campaign_id=campaign_id, storage_root=self.ttrpg_service.data_dir)
            except Exception as e:
                logger.debug(f"[GUIBridge] Failed to update campaign_tools singleton: {e}")

            camp_title = (res.get("campaign") or {}).get("title", campaign_id) if isinstance(res.get("campaign"), dict) else campaign_id
            history = []
            if hasattr(self, "_engine") and self._engine and hasattr(self._engine, "set_active_workspace"):
                try:
                    history = self._engine.set_active_workspace("campaign", campaign_id, camp_title)
                except Exception as e:
                    logger.debug(f"[GUI_BRIDGE] set_active_workspace error: {e}")
            self._on_engine_event("ttrpg_state_updated", res)
            res["history"] = self._format_history_turns(history)
        return res

    def create_ttrpg_campaign(
        self,
        title: str,
        system_preset: str = "dnd_5e",
        gm_mode: str = "interactive_narrator",
        setting_overview: str = "",
        tone: str = "Gritty, tense, grounded, cinematic",
        difficulty: str = "helpful",
        linked_project_id: str = "",
        initial_files: Optional[List[Dict[str, Any]]] = None,
        byoc_data: Optional[Dict[str, Any]] = None,
        module_adherence: str = "strict"
    ) -> Dict[str, Any]:
        res = self.ttrpg_service.create_campaign(
            title=title,
            system_preset=system_preset,
            gm_mode=gm_mode,
            setting_overview=setting_overview,
            tone=tone,
            difficulty=difficulty,
            linked_project_id=linked_project_id,
            initial_files=initial_files,
            module_adherence=module_adherence
        )
        if res.get("status") == "success":
            cid = (res.get("campaign") or {}).get("id")
            if cid:
                try:
                    from core.campaign.campaign_manager import CampaignManager
                    from core.campaign.schema import (
                        LocationNode, LocationType, FactionRecord,
                        FactionDisposition, QuestRecord, QuestObjective, QuestStatus
                    )
                    import re
                    c_mgr = CampaignManager(campaign_id=cid, storage_root=self.ttrpg_service.data_dir)
                    c_mgr.manifest.title = (res.get("campaign") or {}).get("title") or title
                    c_mgr.manifest.ruleset = system_preset or "dnd_5e"
                    c_mgr.manifest.premise = setting_overview or f"The adventure begins in {c_mgr.manifest.title}."
                    c_mgr.manifest.difficulty = difficulty or "helpful"
                    c_mgr.manifest.module_adherence = module_adherence or "strict"

                    if byoc_data and isinstance(byoc_data, dict):
                        if byoc_data.get("genre"):
                            c_mgr.manifest.genre = byoc_data["genre"]
                        sliders = byoc_data.get("tone_sliders") or {}
                        if sliders:
                            c_mgr.manifest.tone_sliders.update(sliders)

                        # Starting / Configured Locations
                        loc_list = byoc_data.get("locations") if isinstance(byoc_data.get("locations"), list) else []
                        if byoc_data.get("starting_location"):
                            loc_list.append(byoc_data["starting_location"])
                        for loc_data in loc_list:
                            if not loc_data or not loc_data.get("name"):
                                continue
                            loc_type_str = str(loc_data.get("type", "region")).lower().strip()
                            try:
                                loc_type = LocationType(loc_type_str)
                            except Exception:
                                loc_type = LocationType.REGION
                            raw_hazards = loc_data.get("hazards", "")
                            hazards = [h.strip() for h in raw_hazards.split(",") if h.strip()] if isinstance(raw_hazards, str) else (raw_hazards or [])
                            loc_id = loc_data.get("id") or f"loc_{re.sub(r'[^a-z0-9_]+', '_', loc_data['name'].lower()).strip('_')}"
                            loc_node = LocationNode(
                                id=loc_id,
                                name=loc_data["name"],
                                type=loc_type,
                                description=loc_data.get("description", ""),
                                is_discovered=True,
                                hazards=hazards
                            )
                            c_mgr.add_location(loc_node)

                        # Starting / Configured Factions
                        fac_list = byoc_data.get("factions") if isinstance(byoc_data.get("factions"), list) else []
                        if byoc_data.get("starting_faction"):
                            fac_list.append(byoc_data["starting_faction"])
                        for fac_data in fac_list:
                            if not fac_data or not fac_data.get("name"):
                                continue
                            disp_str = str(fac_data.get("disposition", "neutral")).lower().strip()
                            try:
                                f_disp = FactionDisposition(disp_str)
                            except Exception:
                                f_disp = FactionDisposition.NEUTRAL
                            fac_id = fac_data.get("id") or f"fac_{re.sub(r'[^a-z0-9_]+', '_', fac_data['name'].lower()).strip('_')}"
                            fac_node = FactionRecord(
                                id=fac_id,
                                name=fac_data["name"],
                                disposition=f_disp,
                                influence_score=int(fac_data.get("influence_score", 50)),
                                agenda=fac_data.get("agenda", ""),
                                public_reputation=fac_data.get("public_reputation", fac_data.get("agenda", ""))
                            )
                            c_mgr.manifest.factions[fac_node.id] = fac_node

                        # Starting / Configured Quests
                        quest_list = byoc_data.get("quests") if isinstance(byoc_data.get("quests"), list) else []
                        if byoc_data.get("starting_quest"):
                            quest_list.append(byoc_data["starting_quest"])
                        for quest_data in quest_list:
                            if not quest_data or not quest_data.get("title"):
                                continue
                            q_objs = []
                            for idx, od in enumerate(quest_data.get("objectives", [])):
                                if isinstance(od, str):
                                    q_objs.append(QuestObjective(id=f"obj_{idx+1}", description=od))
                                elif isinstance(od, dict):
                                    q_objs.append(QuestObjective(
                                        id=od.get("id", f"obj_{idx+1}"),
                                        description=od.get("description", ""),
                                        is_completed=bool(od.get("is_completed", False)),
                                        is_mandatory=bool(od.get("is_mandatory", True))
                                    ))
                            if not q_objs and quest_data.get("objective"):
                                q_objs.append(QuestObjective(id="obj_1", description=quest_data["objective"]))
                            q_id = quest_data.get("id") or f"quest_{re.sub(r'[^a-z0-9_]+', '_', quest_data['title'].lower()).strip('_')}"
                            q_node = QuestRecord(
                                id=q_id,
                                title=quest_data["title"],
                                status=QuestStatus.ACTIVE,
                                giver_npc=quest_data.get("giver_npc", "The Patron"),
                                summary=quest_data.get("summary", ""),
                                objectives=q_objs
                            )
                            c_mgr.add_quest(q_node)

                        # Starting / Configured Clues
                        clue_list = byoc_data.get("clues") if isinstance(byoc_data.get("clues"), list) else []
                        if byoc_data.get("starting_clue"):
                            clue_list.append(byoc_data["starting_clue"])
                        for clue_data in clue_list:
                            if not clue_data or not clue_data.get("title"):
                                continue
                            clue_id = clue_data.get("id") or f"clue_{re.sub(r'[^a-z0-9_]+', '_', clue_data['title'].lower()).strip('_')}"
                            from core.campaign.schema import ClueRecord
                            clue_node = ClueRecord(
                                id=clue_id,
                                title=clue_data["title"],
                                content=clue_data.get("content", ""),
                                is_revealed=bool(clue_data.get("is_revealed", False))
                            )
                            c_mgr.register_clue(clue_node)

                    c_mgr.save()
                    self.ttrpg_service.load_active_campaign_state()
                    res["state"] = self.ttrpg_service.active_state
                    try:
                        import tools.campaign_tools as ct
                        ct._CAMPAIGN = c_mgr
                    except Exception:
                        pass
                except Exception as ex:
                    logger.warning(f"[GUIBridge] BYOC setup failed for '{cid}': {ex}")

            if cid and hasattr(self, "_engine") and self._engine and hasattr(self._engine, "set_active_workspace"):
                try:
                    camp_title = (res.get("campaign") or {}).get("title", cid) if isinstance(res.get("campaign"), dict) else cid
                    self._engine.set_active_workspace("campaign", cid, camp_title)
                except Exception as ex_ws:
                    logger.debug(f"[GUIBridge] set_active_workspace on create error: {ex_ws}")

            self._on_engine_event("ttrpg_state_updated", res)
        return res

    def update_ttrpg_campaign(
        self,
        campaign_id: Optional[str] = None,
        title: Optional[str] = None,
        system_preset: Optional[str] = None,
        gm_mode: Optional[str] = None,
        setting_overview: Optional[str] = None,
        tone: Optional[str] = None,
        difficulty: Optional[str] = None,
        linked_project_id: Optional[str] = None,
        dice_mode: Optional[str] = None,
        module_adherence: Optional[str] = None
    ) -> Dict[str, Any]:
        res = self.ttrpg_service.update_campaign(
            campaign_id=campaign_id,
            title=title,
            system_preset=system_preset,
            gm_mode=gm_mode,
            setting_overview=setting_overview,
            tone=tone,
            difficulty=difficulty,
            linked_project_id=linked_project_id,
            dice_mode=dice_mode,
            module_adherence=module_adherence
        )
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
            if res.get("level_up_ready"):
                self._on_engine_event("level_up_ready", res)
        return res

    def set_ttrpg_difficulty(self, difficulty: str, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        """Sets the DM difficulty/guidance level ('helpful', 'easy', 'medium', 'hard') for a campaign."""
        cid = campaign_id or self.ttrpg_service.manifest.get("active_campaign")
        clean_diff = (difficulty or "helpful").strip().lower()
        if clean_diff not in ("helpful", "easy", "medium", "hard"):
            clean_diff = "helpful"
        res = self.ttrpg_service.update_campaign(campaign_id=cid, difficulty=clean_diff)
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
            self._on_engine_event("campaign_updated", res.get("campaign", {}))
            try:
                eng = getattr(self, "_engine", getattr(self, "engine", None))
                if eng and getattr(eng, "active_workspace_type", None) == "campaign" and getattr(eng, "active_context_id", None) == cid:
                    eng.active_system_prompt = self.ttrpg_service.build_gm_context_prompt()
                    logger.info(f"[TTRPG] Refreshed active engine system prompt with difficulty: {clean_diff}")
            except Exception as e:
                logger.warning(f"[TTRPG] Could not refresh engine prompt on difficulty change: {e}")
        return res

    def delete_ttrpg_campaign(self, campaign_id: str) -> Dict[str, Any]:
        res = self.ttrpg_service.delete_campaign(campaign_id)
        if res.get("status") == "success":
            new_active = res.get("active_campaign")
            try:
                eng = getattr(self, "_engine", getattr(self, "engine", None))
                if eng and getattr(eng, "active_workspace_type", None) == "campaign" and getattr(eng, "active_context_id", None) == campaign_id:
                    if new_active:
                        camp_title = (self.ttrpg_service.manifest.get("campaigns", {}).get(new_active) or {}).get("title", new_active)
                        eng.set_active_workspace("campaign", new_active, camp_title)
                    else:
                        eng.set_active_workspace("general", "default", "General")
            except Exception as e:
                logger.warning(f"[TTRPG] Error switching engine workspace after deleting campaign: {e}")

            self._on_engine_event("active_campaign_changed", {"campaign_id": new_active, "campaign": self.ttrpg_service.manifest.get("campaigns", {}).get(new_active)})
            self._on_engine_event("campaign_updated", {"deleted": campaign_id, "active_campaign": new_active})
            self._on_engine_event("ttrpg_state_updated", res)
        return res

    # ------------------------------------------------------------------
    # BYOC & World State Ledger (Phase 2 Integration)
    # ------------------------------------------------------------------
    def get_ttrpg_world_state(self, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        cid = campaign_id or self.ttrpg_service.manifest.get("active_campaign")
        if not cid:
            return {"status": "error", "message": "No active campaign"}
        try:
            from core.campaign.campaign_manager import CampaignManager
            c_mgr = CampaignManager(campaign_id=cid, storage_root=self.ttrpg_service.data_dir)
            return {
                "status": "success",
                "campaign_id": cid,
                "manifest": c_mgr.serialize_manifest(),
                "context": c_mgr.generate_prompt_context()
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def byoc_set_active_location(self, location_id: str, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        cid = campaign_id or self.ttrpg_service.manifest.get("active_campaign")
        if not cid:
            return {"status": "error", "message": "No active campaign"}
        try:
            from core.campaign.campaign_manager import CampaignManager
            c_mgr = CampaignManager(campaign_id=cid, storage_root=self.ttrpg_service.data_dir)
            res = c_mgr.set_active_location(location_id)
            self._on_engine_event("world_state_updated", {"campaign_id": cid})
            return {"status": "success", "result": res}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def byoc_add_location(self, location_data: Dict[str, Any], campaign_id: Optional[str] = None) -> Dict[str, Any]:
        cid = campaign_id or self.ttrpg_service.manifest.get("active_campaign")
        if not cid:
            return {"status": "error", "message": "No active campaign"}
        try:
            import re
            from core.campaign.campaign_manager import CampaignManager
            from core.campaign.schema import LocationNode, LocationType
            c_mgr = CampaignManager(campaign_id=cid, storage_root=self.ttrpg_service.data_dir)
            loc_id = location_data.get("id") or f"loc_{re.sub(r'[^a-z0-9_]+', '_', location_data.get('name', 'node').lower()).strip('_')}"
            try:
                ltype = LocationType(location_data.get("type", "point_of_interest").lower())
            except Exception:
                ltype = LocationType.POINT_OF_INTEREST
            raw_h = location_data.get("hazards", "")
            hazards = [h.strip() for h in raw_h.split(",") if h.strip()] if isinstance(raw_h, str) else (raw_h or [])
            node = LocationNode(
                id=loc_id,
                name=location_data.get("name", "Unknown Location"),
                type=ltype,
                parent_id=location_data.get("parent_id"),
                description=location_data.get("description", ""),
                hazards=hazards,
                hidden_secrets=location_data.get("hidden_secrets", []),
                connected_location_ids=location_data.get("connected_location_ids", [])
            )
            c_mgr.add_location(node)
            self._on_engine_event("world_state_updated", {"campaign_id": cid})
            return {"status": "success", "location": loc_id}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def byoc_add_faction(self, faction_data: Dict[str, Any], campaign_id: Optional[str] = None) -> Dict[str, Any]:
        cid = campaign_id or self.ttrpg_service.manifest.get("active_campaign")
        if not cid:
            return {"status": "error", "message": "No active campaign"}
        try:
            import re
            from core.campaign.campaign_manager import CampaignManager
            from core.campaign.schema import FactionRecord, FactionDisposition
            c_mgr = CampaignManager(campaign_id=cid, storage_root=self.ttrpg_service.data_dir)
            fac_id = faction_data.get("id") or f"fac_{re.sub(r'[^a-z0-9_]+', '_', faction_data.get('name', 'faction').lower()).strip('_')}"
            try:
                fdisp = FactionDisposition(faction_data.get("disposition", "neutral").lower())
            except Exception:
                fdisp = FactionDisposition.NEUTRAL
            fac = FactionRecord(
                id=fac_id,
                name=faction_data.get("name", "Unknown Faction"),
                disposition=fdisp,
                influence_score=int(faction_data.get("influence_score", 50)),
                agenda=faction_data.get("agenda", ""),
                public_reputation=faction_data.get("public_reputation", ""),
                hidden_motives=faction_data.get("hidden_motives", "")
            )
            c_mgr.manifest.factions[fac_id] = fac
            c_mgr.save()
            self._on_engine_event("world_state_updated", {"campaign_id": cid})
            return {"status": "success", "faction": fac_id}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def byoc_adjust_faction(self, faction_id: str, delta: int, disposition: Optional[str] = None, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        cid = campaign_id or self.ttrpg_service.manifest.get("active_campaign")
        if not cid:
            return {"status": "error", "message": "No active campaign"}
        try:
            from core.campaign.campaign_manager import CampaignManager
            from core.campaign.schema import FactionDisposition
            c_mgr = CampaignManager(campaign_id=cid, storage_root=self.ttrpg_service.data_dir)
            disp_enum = FactionDisposition(disposition.lower().strip()) if disposition else None
            res = c_mgr.adjust_faction_reputation(faction_id, delta, disp_enum)
            self._on_engine_event("world_state_updated", {"campaign_id": cid})
            return {"status": "success", "result": res}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def byoc_add_quest(self, quest_data: Dict[str, Any], campaign_id: Optional[str] = None) -> Dict[str, Any]:
        cid = campaign_id or self.ttrpg_service.manifest.get("active_campaign")
        if not cid:
            return {"status": "error", "message": "No active campaign"}
        try:
            import re
            from core.campaign.campaign_manager import CampaignManager
            from core.campaign.schema import QuestRecord, QuestObjective, QuestStatus
            c_mgr = CampaignManager(campaign_id=cid, storage_root=self.ttrpg_service.data_dir)
            q_id = quest_data.get("id") or f"quest_{re.sub(r'[^a-z0-9_]+', '_', quest_data.get('title', 'quest').lower()).strip('_')}"
            objs = []
            for idx, od in enumerate(quest_data.get("objectives", [])):
                if isinstance(od, str):
                    objs.append(QuestObjective(id=f"obj_{idx+1}", description=od))
                elif isinstance(od, dict):
                    objs.append(QuestObjective(
                        id=od.get("id", f"obj_{idx+1}"),
                        description=od.get("description", ""),
                        is_completed=bool(od.get("is_completed", False)),
                        is_mandatory=bool(od.get("is_mandatory", True))
                    ))
            q = QuestRecord(
                id=q_id,
                title=quest_data.get("title", "Untitled Quest"),
                status=QuestStatus.ACTIVE,
                giver_npc=quest_data.get("giver_npc", "The Patron"),
                summary=quest_data.get("summary", ""),
                objectives=objs
            )
            c_mgr.add_quest(q)
            self._on_engine_event("world_state_updated", {"campaign_id": cid})
            return {"status": "success", "quest": q_id}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def byoc_complete_objective(self, quest_id: str, objective_id: str, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        cid = campaign_id or self.ttrpg_service.manifest.get("active_campaign")
        if not cid:
            return {"status": "error", "message": "No active campaign"}
        try:
            from core.campaign.campaign_manager import CampaignManager
            c_mgr = CampaignManager(campaign_id=cid, storage_root=self.ttrpg_service.data_dir)
            res = c_mgr.complete_objective(quest_id, objective_id)
            self._on_engine_event("world_state_updated", {"campaign_id": cid})
            return {"status": "success", "result": res}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def byoc_add_clue(self, clue_data: Dict[str, Any], campaign_id: Optional[str] = None) -> Dict[str, Any]:
        cid = campaign_id or self.ttrpg_service.manifest.get("active_campaign")
        if not cid:
            return {"status": "error", "message": "No active campaign"}
        try:
            import re
            from core.campaign.campaign_manager import CampaignManager
            from core.campaign.schema import ClueRecord
            c_mgr = CampaignManager(campaign_id=cid, storage_root=self.ttrpg_service.data_dir)
            c_id = clue_data.get("id") or f"clue_{re.sub(r'[^a-z0-9_]+', '_', clue_data.get('title', 'clue').lower()).strip('_')}"
            clue = ClueRecord(
                id=c_id,
                title=clue_data.get("title", "Mysterious Clue"),
                content=clue_data.get("content", ""),
                is_revealed=bool(clue_data.get("is_revealed", False)),
                source_location_id=clue_data.get("source_location_id"),
                tags=clue_data.get("tags", [])
            )
            c_mgr.register_clue(clue)
            self._on_engine_event("world_state_updated", {"campaign_id": cid})
            return {"status": "success", "clue": c_id}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def byoc_reveal_clue(self, clue_id: str, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        cid = campaign_id or self.ttrpg_service.manifest.get("active_campaign")
        if not cid:
            return {"status": "error", "message": "No active campaign"}
        try:
            from core.campaign.campaign_manager import CampaignManager
            c_mgr = CampaignManager(campaign_id=cid, storage_root=self.ttrpg_service.data_dir)
            res = c_mgr.reveal_clue(clue_id)
            self._on_engine_event("world_state_updated", {"campaign_id": cid})
            return {"status": "success", "result": res}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def byoc_update_tone_sliders(self, sliders: Dict[str, int], campaign_id: Optional[str] = None) -> Dict[str, Any]:
        cid = campaign_id or self.ttrpg_service.manifest.get("active_campaign")
        if not cid:
            return {"status": "error", "message": "No active campaign"}
        try:
            from core.campaign.campaign_manager import CampaignManager
            c_mgr = CampaignManager(campaign_id=cid, storage_root=self.ttrpg_service.data_dir)
            c_mgr.manifest.tone_sliders.update(sliders)
            c_mgr.save()
            self._on_engine_event("world_state_updated", {"campaign_id": cid})
            return {"status": "success", "sliders": c_mgr.manifest.tone_sliders}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def upload_ttrpg_document(
        self,
        filename: str,
        base64_content: str,
        doc_category: str = "ruleset",
        campaign_id: Optional[str] = None,
        ruleset_override: Optional[str] = None
    ) -> Dict[str, Any]:
        import base64
        try:
            b64_clean = base64_content.split(",", 1)[1] if "," in base64_content else base64_content
            raw_bytes = base64.b64decode(b64_clean)
        except Exception as e:
            return {"status": "error", "message": f"Invalid file encoding: {e}"}

        res = self.ttrpg_service.add_campaign_document(
            filename=filename,
            content_bytes=raw_bytes,
            doc_category=doc_category,
            campaign_id=campaign_id,
            ruleset_override=ruleset_override
        )
        if res.get("status") in ("success", "requires_ruleset_selection"):
            self._on_engine_event("ttrpg_state_updated", res)
        return res

    def list_ttrpg_documents(self, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        return self.ttrpg_service.list_campaign_documents(campaign_id=campaign_id)

    def read_ttrpg_document(self, filename: str, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        return self.ttrpg_service.read_campaign_document(filename=filename, campaign_id=campaign_id)

    def delete_ttrpg_document(self, filename: str, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        res = self.ttrpg_service.delete_campaign_document(filename=filename, campaign_id=campaign_id)
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
            if res.get("level_up_ready"):
                self._on_engine_event("level_up_ready", res)
        return res

    def refresh_ttrpg_roster_and_files(self, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Scans the campaign files directory for newly added files, extracts and imports
        any missing character sheets into the Party Roster, and reloads campaign state.
        """
        res = self.ttrpg_service.refresh_campaign_files_and_characters(campaign_id=campaign_id)
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
            if res.get("level_up_ready"):
                self._on_engine_event("level_up_ready", res)
        return res

    def record_ttrpg_notes(
        self,
        summary: str,
        location: Optional[str] = None,
        key_clues: Optional[List[str]] = None,
        active_quest: Optional[str] = None,
        last_action: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Appends a journal entry and updates live session recap in the persistent campaign memory.
        """
        res = self.ttrpg_service.record_campaign_notes(
            summary=summary,
            location=location,
            key_clues=key_clues,
            active_quest=active_quest,
            last_action=last_action
        )
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
            if res.get("level_up_ready"):
                self._on_engine_event("level_up_ready", res)
        return res

    def get_ttrpg_notes(self, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        return self.ttrpg_service.get_campaign_notes(campaign_id=campaign_id)

    def set_ttrpg_session_active(self, active: bool) -> Dict[str, Any]:
        """
        Auto-manages Target Speaker Filter and engine mode for Tabletop RPG DM sessions.
        When active=True (adventure starts/resumes):
            - Puts AetherEngine into Tabletop Role Play mode (is_ttrpg_session_active = True).
            - If Target Speaker Filter is currently ON, saves that it was ON, then turns it OFF
              so all players around the microphone can speak.
        When active=False (adventure ends / Aether stops):
            - Deactivates Tabletop Role Play mode on AetherEngine (is_ttrpg_session_active = False).
            - If Target Speaker Filter was ON prior to the adventure session, restores it back to ON.
            - Auto-summarizes turns to the campaign journal.
        """
        try:
            eng = getattr(self, "_engine", getattr(self, "engine", None))
            if eng and hasattr(eng, "set_ttrpg_session_active"):
                eng.set_ttrpg_session_active(active)

            with self._config_lock:
                bio_cfg = self._config.get("audio", {}).get("voice_biometrics", {})
                current_bio = bool(bio_cfg.get("enabled", False))
                threshold = float(bio_cfg.get("threshold", 0.40))

            if active:
                if getattr(self, "_ttrpg_prev_target_speaker_filter", None) is None:
                    self._ttrpg_prev_target_speaker_filter = current_bio
                if current_bio:
                    self.save_voice_biometrics_settings(False, threshold)
                    logger.info("[TTRPG] Auto-toggled Target Speaker Filter OFF for tabletop session (multi-player party mode).")
                if hasattr(self, "_on_engine_event"):
                    self._on_engine_event("ttrpg_session_mode_changed", {"active": True})
                return {"success": True, "filter_toggled": current_bio, "current_filter": False, "is_adventure_active": True}
            else:
                prev = getattr(self, "_ttrpg_prev_target_speaker_filter", None)
                self._ttrpg_prev_target_speaker_filter = None
                if prev is True:
                    self.save_voice_biometrics_settings(True, threshold)
                    logger.info("[TTRPG] Tabletop session concluded: Restored Target Speaker Filter to ON.")

                # Auto-summarize session turns into campaign journal & recap on conclusion
                if eng:
                    try:
                        raw_turns = []
                        if hasattr(eng, "session_lifecycle") and eng.session_lifecycle:
                            raw_turns = eng.session_lifecycle.get_raw_turns()
                        sess_mgr = getattr(eng, "session_mgr", None) or getattr(eng, "session_manager", None)
                        if (not raw_turns or len(raw_turns) < 2) and sess_mgr:
                            raw_turns = getattr(sess_mgr, "history", [])
                        if raw_turns:
                            sid = (
                                getattr(eng.session_lifecycle, "session_id", None)
                                or (getattr(sess_mgr, "active_session_id", "campaign") if sess_mgr else "campaign")
                            )
                            self.ttrpg_service.auto_summarize_session_to_journal(raw_turns, session_id=sid)
                            if hasattr(self, "_on_engine_event"):
                                self._on_engine_event("ttrpg_state_updated", {"status": "success", "session_id": sid})
                    except Exception as e_rp:
                        logger.warning(f"[TTRPG] Auto session notes error on session end: {e_rp}")

                if hasattr(self, "_on_engine_event"):
                    self._on_engine_event("ttrpg_session_mode_changed", {"active": False})
                return {"success": True, "filter_restored": bool(prev is True), "current_filter": True if prev is True else current_bio, "is_adventure_active": False}
        except Exception as e:
            logger.error(f"[TTRPG] Error in set_ttrpg_session_active: {e}")
            return {"success": False, "error": str(e)}

    def ask_aether_dm_review(self, custom_prompt: Optional[str] = None) -> Dict[str, Any]:
        """
        Sends an instruction to Aether to review the uploaded campaign rulesets, modules,
        and character sheets, populate any characters into the Party Roster, and assume the role of DM/GM.
        """
        if not self._engine.is_running:
            self.start_assistant()
        self.set_ttrpg_session_active(True)
        active_id = self.ttrpg_service.manifest.get("active_campaign")
        camp = self.ttrpg_service.manifest.get("campaigns", {}).get(active_id, {}) if active_id else {}
        camp_title = camp.get("title", active_id or "the active campaign")
        docs = (self.ttrpg_service.active_state or {}).get("documents", {})
        doc_names = ", ".join(f"'{k}' ({v.get('category', 'doc')})" for k, v in docs.items()) if docs else "the campaign setting"

        prompt = custom_prompt or (
            f"Please act as our Dungeon Master (DM) / Game Master for the campaign '{camp_title}'. "
            f"First, call `read_campaign_document(filename='all')` to review our uploaded campaign adventure and ruleset documents ({doc_names}). "
            f"Ground your narration in the adventure module and rules, consult the Party Roster for our active characters, "
            f"give us a brief summary of the campaign premise and party, and set the opening scene!"
        )
        if active_id:
            return self.send_workspace_message("campaign", active_id, prompt)
        return self.send_text_message(prompt)

    def start_or_resume_ttrpg_campaign(self, model_endpoint: Optional[str] = None) -> Dict[str, Any]:
        """
        Starts a new campaign with opening narration or resumes an existing campaign seamlessly
        from the persistent session recap and journal notes.
        Auto-starts Aether voice engine if not already running and applies the saved TTRPG Cortex engine.
        """
        active_id = self.ttrpg_service.manifest.get("active_campaign")
        if not active_id and self.ttrpg_service.manifest.get("campaigns"):
            active_id = next(iter(self.ttrpg_service.manifest["campaigns"]))
            self.ttrpg_service.manifest["active_campaign"] = active_id
            self.ttrpg_service.save_manifest()

        if not active_id:
            return {"success": False, "error": "No campaign selected or available to resume"}

        if model_endpoint:
            self.save_ttrpg_model_endpoint(model_endpoint)

        # Ensure campaign state and workspace context are bound BEFORE starting/switching the modular DM engine
        if hasattr(self.ttrpg_service, "load_active_campaign_state"):
            self.ttrpg_service.load_active_campaign_state()

        self.set_ttrpg_session_active(True)
        eng = getattr(self, "_engine", None)
        if eng and hasattr(eng, "set_active_workspace"):
            if (
                getattr(eng, "active_workspace_type", "") != "campaign"
                or getattr(eng, "active_workspace_context_id", "") != active_id
            ):
                eng.set_active_workspace("campaign", active_id)

        if eng and eng.is_running and getattr(eng, "_active_pipeline_mode", None) == "live":
            # Switching from fast Live front-end into dedicated TTRPG Modular reasoning engine
            eng.stop()
            deadline_stop = time.monotonic() + 1.5
            while eng.is_running and time.monotonic() < deadline_stop:
                time.sleep(0.05)

        if eng and not eng.is_running:
            start_res = self.start_assistant()
            if isinstance(start_res, dict) and not start_res.get("success", True):
                self.set_ttrpg_session_active(False)
                return start_res

        if eng:
            # Wait briefly for the Modular DM pipeline to finish audio init and open its Cortex chat session
            deadline_ready = time.monotonic() + 8.0
            while eng.is_running and time.monotonic() < deadline_ready:
                conn_state = str(getattr(getattr(getattr(eng, "conn_mgr", None), "state", None), "value", "")).upper()
                if (
                    getattr(eng, "_active_pipeline_mode", None) == "modular"
                    and (conn_state == "CONNECTED" or getattr(eng, "chat", None) is not None)
                ):
                    break
                time.sleep(0.05)
            if not eng.is_running:
                self.set_ttrpg_session_active(False)
                return {"success": False, "error": "Assistant engine failed to start. Check API key and audio devices."}

        # Synchronize location and recap from recent session turns if available so resume location is never stale
        eng = getattr(self, "_engine", None)
        sess_mgr = (getattr(eng, "session_mgr", None) or getattr(eng, "session_manager", None)) if eng else None
        if sess_mgr:
            active_sid = f"campaign_{active_id}".lower().strip()
            hist = getattr(sess_mgr, "history", [])
            if (not hist or getattr(sess_mgr, "active_session_id", "") != active_sid) and hasattr(sess_mgr, "switch_session"):
                hist = sess_mgr.switch_session("campaign", active_id)
            if hist and len(hist) >= 2:
                try:
                    self.ttrpg_service.auto_summarize_session_to_journal(hist, session_id=active_sid)
                except Exception as e_sync:
                    logger.debug(f"[TTRPG] Resume turn auto-sync check error: {e_sync}")

        camp = self.ttrpg_service.manifest.get("campaigns", {}).get(active_id, {}) if active_id else {}
        camp_title = camp.get("title", active_id or "the campaign")
        state = self.ttrpg_service.active_state or {}
        recap = state.get("latest_recap")
        loc = state.get("current_location")
        difficulty = (camp.get("difficulty") or "helpful").lower()
        dice_mode = self.ttrpg_service.get_dice_mode(active_id)

        diff_note = ""
        if difficulty == "helpful":
            if dice_mode == "physical":
                diff_note = " Remember we are playing in Helpful / Beginner mode with physical dice: please suggest 2-3 specific actions we can take, educate us on what physical dice to roll with our exact modifiers, and remind us of helpful abilities!"
            else:
                diff_note = " Remember we are playing in Helpful / Beginner mode with digital dice: please suggest 2-3 specific actions we can take, remind us of helpful abilities, and roll digital dice for us automatically when actions require checks!"

        docs = state.get("documents", {})
        module_files = [
            f for f, d in docs.items()
            if d.get("category") in ("campaign", "module")
            or (any(k in f.lower() for k in ("module", "adventure", "campaign", "quest", "act"))
                and not any(k in f.lower() for k in ("basicrules", "rule", "sheet", "char")))
        ]
        doc_hint = ""
        if module_files:
            doc_hint = f" Consult the adventure module '{module_files[0]}' using `read_campaign_document` (e.g. Scene 1 or Chapter 1) to accurately set the opening scene, introductory NPC dialogue, and quest hook from the module."

        if recap or state.get("journal"):
            prompt = (
                f"Aether, please act as our Game Master and resume our campaign '{camp_title}' for our party from the party roster! "
                f"Provide a brief 2-sentence recap of where we left off (at {loc or 'our last location'}), "
                f"describe the immediate scene and what is happening right now, and ask the party what we do next!{diff_note}"
            )
        else:
            prompt = (
                f"Aether, please act as our Game Master and begin our campaign '{camp_title}'! "
                f"Introduce the setting and our characters from the party roster, and set the opening scene from the adventure module.{doc_hint}{diff_note} "
                f"What do we see and what are our immediate choices?"
            )
        return self.send_workspace_message("campaign", active_id, prompt)

    def manage_ttrpg_progression(
        self,
        character_name: str,
        action: str = "award_xp",
        amount_or_level: int = 0,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        res = self.ttrpg_service.manage_character_progression(
            character_name=character_name,
            action=action,
            amount_or_level=amount_or_level,
            notes=notes
        )
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
            if res.get("level_up_ready"):
                self._on_engine_event("level_up_ready", res)
        return res

    def roll_ttrpg_dice(
        self,
        formula: str,
        roll_type: str = "normal",
        purpose: Optional[str] = None,
        character_name: Optional[str] = None
    ) -> Dict[str, Any]:
        res = self.ttrpg_service.parse_and_roll(formula, roll_type, character_name=character_name)
        if res.get("status") == "success":
            if purpose:
                res["purpose"] = purpose
            if character_name:
                res["character_name"] = character_name

            p_low = (purpose or "").lower()
            is_death_save = "death save" in p_low or "death saving" in p_low or (formula.strip() == "1d20" and "death" in p_low)
            char_id, cdata = self.ttrpg_service._find_character(character_name) if character_name else (None, None)

            if is_death_save and cdata:
                d20_roll = res.get("rolls", [10])[0] if res.get("rolls") else res.get("total", 10)
                ds = cdata.setdefault("death_saves", {"successes": 0, "failures": 0, "status": "dying"})
                char_conds = cdata.setdefault("conditions", [])
                msg = ""
                if d20_roll == 20:
                    self.ttrpg_service.modify_character_stat(character_name, "health", 1)
                    msg = f"NATURAL 20! {character_name} regains 1 HP, revives, and is no longer unconscious!"
                elif d20_roll == 1:
                    ds["failures"] = ds.get("failures", 0) + 2
                    if ds["failures"] >= 3:
                        ds["status"] = "dead"
                        if "Dead" not in char_conds:
                            char_conds.append("Dead")
                        msg = f"NATURAL 1! Critical failure! {character_name} suffers 2 death save failures ({ds['failures']}/3) and has DIED."
                    else:
                        msg = f"NATURAL 1! Critical failure! {character_name} suffers 2 death save failures ({ds['failures']}/3)."
                elif d20_roll >= 10:
                    ds["successes"] = ds.get("successes", 0) + 1
                    if ds["successes"] >= 3:
                        ds["status"] = "stable"
                        if "Stable" not in char_conds:
                            char_conds.append("Stable")
                        msg = f"SUCCESS (Roll: {d20_roll})! {character_name} records a death save success ({ds['successes']}/3). STABLE at 0 HP!"
                    else:
                        msg = f"SUCCESS (Roll: {d20_roll})! {character_name} records a death save success ({ds['successes']}/3)."
                else:
                    ds["failures"] = ds.get("failures", 0) + 1
                    if ds["failures"] >= 3:
                        ds["status"] = "dead"
                        if "Dead" not in char_conds:
                            char_conds.append("Dead")
                        msg = f"FAILURE (Roll: {d20_roll})! {character_name} suffers a death save failure ({ds['failures']}/3) and has DIED."
                    else:
                        msg = f"FAILURE (Roll: {d20_roll})! {character_name} suffers a death save failure ({ds['failures']}/3)."

                self.ttrpg_service.save_active_campaign_state()
                res["death_save_result"] = msg
                res["death_saves"] = ds
                self._on_engine_event("roster_updated", self.ttrpg_service.get_campaign_roster())
                if self.ttrpg_service.active_state and self.ttrpg_service.active_state.get("initiative_order"):
                    self._on_engine_event("initiative_updated", {"initiative_order": self.ttrpg_service.active_state.get("initiative_order", [])})

            self._on_engine_event("dice_roll_event", res)
        return res

    def set_ttrpg_dice_mode(self, mode: str, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        """Sets dice rolling mode ('physical' or 'digital') for tabletop roleplay."""
        res = self.ttrpg_service.set_dice_mode(mode, campaign_id)
        if res.get("status") == "success":
            eng = getattr(self, "_engine", getattr(self, "engine", None))
            if eng and hasattr(eng, "set_active_workspace") and getattr(eng, "active_workspace_type", "") == "campaign":
                active_id = self.ttrpg_service.manifest.get("active_campaign")
                if active_id:
                    eng.set_active_workspace("campaign", active_id)
            self._on_engine_event("dice_mode_updated", res)
        return res

    def get_ttrpg_dice_mode(self, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        """Returns active dice rolling mode ('physical' or 'digital')."""
        mode = self.ttrpg_service.get_dice_mode(campaign_id)
        return {"status": "success", "dice_mode": mode}

    def advance_ttrpg_turn(self) -> Dict[str, Any]:
        res = self.ttrpg_service.advance_turn()
        if res.get("status") == "success":
            self._on_engine_event("initiative_updated", res)
            self._on_engine_event("ttrpg_state_updated", res)
            if res.get("level_up_ready_characters"):
                self._on_engine_event("level_up_ready", res)
        return res

    def start_ttrpg_combat(self, enemies: Optional[List[Any]] = None, round_number: int = 1) -> Dict[str, Any]:
        res = self.ttrpg_service.start_combat(enemies=enemies, round_number=round_number)
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
            self._on_engine_event("initiative_updated", res)
        return res

    def end_ttrpg_combat(self) -> Dict[str, Any]:
        res = self.ttrpg_service.end_combat()
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
            self._on_engine_event("initiative_updated", res)
            if res.get("level_up_ready_characters"):
                self._on_engine_event("level_up_ready", res)
        return res

    def add_ttrpg_initiative(self, name: str, roll_val: int, is_player: bool = True) -> Dict[str, Any]:
        res = self.ttrpg_service.add_to_initiative(name, int(roll_val), bool(is_player))
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
            self._on_engine_event("initiative_updated", res)
        return res

    def remove_ttrpg_initiative(self, name_or_id: str) -> Dict[str, Any]:
        res = self.ttrpg_service.remove_from_initiative(name_or_id)
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
            self._on_engine_event("initiative_updated", res)
        return res

    def clear_ttrpg_defeated_combatants(self) -> Dict[str, Any]:
        res = self.ttrpg_service.clear_defeated_combatants()
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
            self._on_engine_event("initiative_updated", res)
            if res.get("level_up_ready_characters"):
                self._on_engine_event("level_up_ready", res)
        return res

    def add_ttrpg_character(self, char_payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            logger.info(f"[TTRPG] add_ttrpg_character requested for: {char_payload.get('name')} (ID: {char_payload.get('id')})")
            if "universal_core" in char_payload or "target_system_attributes" in char_payload:
                res = self.ttrpg_service.import_character_to_active_campaign(char_payload)
            else:
                res = self.ttrpg_service.add_character(char_payload)
            if res.get("status") in ("success", "already_loaded"):
                self._on_engine_event("ttrpg_state_updated", res)
            else:
                logger.warning(f"[TTRPG] add_ttrpg_character error: {res.get('message')}")
            return res
        except Exception as e:
            logger.error(f"[TTRPG] Exception in add_ttrpg_character: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    def get_ttrpg_campaign_roster(self) -> List[Dict[str, Any]]:
        """Authoritative live campaign party roster query."""
        return self.ttrpg_service.get_campaign_roster()

    def import_character_to_active_campaign(self, vault_char_data: Dict[str, Any]) -> Dict[str, Any]:
        """Once-and-done state clone from vault template into active campaign."""
        res = self.ttrpg_service.import_character_to_active_campaign(vault_char_data)
        self._on_engine_event("ttrpg_state_updated", res)
        return res

    def delete_ttrpg_character(self, character_id_or_name: str) -> Dict[str, Any]:
        res = self.ttrpg_service.delete_character(character_id_or_name)
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
            if res.get("level_up_ready"):
                self._on_engine_event("level_up_ready", res)
        return res

    def modify_ttrpg_character_stat(
        self,
        character_name: str,
        stat_key: str,
        delta: int,
        is_custom_meter: bool = False
    ) -> Dict[str, Any]:
        res = self.ttrpg_service.modify_character_stat(
            character_name=character_name,
            stat_key=stat_key,
            delta=int(delta),
            is_custom_meter=bool(is_custom_meter)
        )
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
            if self.ttrpg_service.active_state and self.ttrpg_service.active_state.get("initiative_order"):
                self._on_engine_event("initiative_updated", {"initiative_order": self.ttrpg_service.active_state.get("initiative_order", [])})
        return res

    def update_ttrpg_condition(self, character_name: str, condition: str, active: bool) -> Dict[str, Any]:
        res = self.ttrpg_service.update_condition(character_name, condition, bool(active))
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
            self._on_engine_event("roster_updated", self.ttrpg_service.get_campaign_roster())
            if self.ttrpg_service.active_state and self.ttrpg_service.active_state.get("initiative_order"):
                self._on_engine_event("initiative_updated", {"initiative_order": self.ttrpg_service.active_state.get("initiative_order", [])})
        return res

    def update_ttrpg_entity_location(
        self,
        name: str,
        location: str,
        is_separated: Optional[bool] = None,
        reunite_party: bool = False
    ) -> Dict[str, Any]:
        res = self.ttrpg_service.update_entity_location(
            name=name,
            location=location,
            is_separated=is_separated,
            reunite_party=reunite_party
        )
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
            self._on_engine_event("roster_updated", self.ttrpg_service.get_campaign_roster())
        return res

    def manage_ttrpg_character_inventory(
        self,
        character_name: str,
        action: str = "add",
        items: Optional[List[str]] = None,
        weapons: Optional[List[Dict[str, str]]] = None,
        spells: Optional[List[Any]] = None,
        reason: Optional[str] = None,
        storage_location: Optional[str] = None,
        location_knowledge: Optional[str] = None,
        status: Optional[str] = None,
        holder_or_creature: Optional[str] = None,
        salvageable: Optional[bool] = None,
        discovery_clue: Optional[str] = None
    ) -> Dict[str, Any]:
        """Adds, removes, disarms, discovers, destroys, or retrieves weapons, items, and spells for a character or party in the active campaign."""
        res = self.ttrpg_service.manage_character_inventory(
            character_name=character_name,
            action=action,
            items=items,
            weapons=weapons,
            spells=spells,
            reason=reason,
            storage_location=storage_location,
            location_knowledge=location_knowledge,
            status=status,
            holder_or_creature=holder_or_creature,
            salvageable=salvageable,
            discovery_clue=discovery_clue
        )
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
            if res.get("level_up_ready"):
                self._on_engine_event("level_up_ready", res)
        return res

    def manage_ttrpg_inventory(
        self,
        character_name: str,
        action: str = "add",
        items: Optional[List[str]] = None,
        weapons: Optional[List[Dict[str, str]]] = None,
        spells: Optional[List[Any]] = None,
        reason: Optional[str] = None,
        storage_location: Optional[str] = None,
        location_knowledge: Optional[str] = None,
        status: Optional[str] = None,
        holder_or_creature: Optional[str] = None,
        salvageable: Optional[bool] = None,
        discovery_clue: Optional[str] = None
    ) -> Dict[str, Any]:
        """Convenience alias for manage_ttrpg_character_inventory supporting party-wide disarm/retrieve."""
        return self.manage_ttrpg_character_inventory(
            character_name=character_name,
            action=action,
            items=items,
            weapons=weapons,
            spells=spells,
            reason=reason,
            storage_location=storage_location,
            location_knowledge=location_knowledge,
            status=status,
            holder_or_creature=holder_or_creature,
            salvageable=salvageable,
            discovery_clue=discovery_clue
        )

    def reformat_ttrpg_weapons_or_items(self, raw_text: str, ruleset: str = "dnd_5e") -> Dict[str, Any]:
        """Parses and reformats raw unformatted weapon or item text into structured character sheet records."""
        return self.ttrpg_service.reformat_weapons_or_items(raw_text=raw_text, ruleset=ruleset)

    def set_active_speaker_focus(self, character_id: str) -> Dict[str, Any]:
        """Sets the active speaker fallback for single-mic tabletop setups."""
        res = self.ttrpg_service.set_focused_speaker(character_id)
        if res.get("status") == "success":
            self._on_engine_event("speaker_focused", {"character_id": res.get("focused_speaker_id"), "name": res.get("name")})
            self._on_engine_event("ttrpg_state_updated", res)
        return res

    def set_party_caller(self, character_name: str) -> Dict[str, Any]:
        """Sets the designated player who resolves group decisions in exploration mode."""
        res = self.ttrpg_service.set_party_caller(character_name)
        if res.get("status") == "success":
            self._on_engine_event("party_caller_updated", res)
            self._on_engine_event("ttrpg_state_updated", res)
        return res

    def adjust_character_spell_slot(self, character_id: str, level: int, delta: int) -> Dict[str, Any]:
        """Directly adjust spell slots via card pips."""
        res = self.ttrpg_service.adjust_spell_slot(character_id, int(level), int(delta))
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
            self._on_engine_event("roster_updated", self.ttrpg_service.get_campaign_roster())
        return res

    def invoke_ttrpg_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Invokes a registered TTRPG tool directly (e.g. cast_spell)."""
        eng = getattr(self, "_engine", getattr(self, "engine", None))
        if eng and hasattr(eng, "dispatcher"):
            coro = eng.dispatcher.dispatch(tool_name, params)
            if asyncio.iscoroutine(coro):
                target_loop = (
                    getattr(self, "_loop", None)
                    or getattr(self, "loop", None)
                    or getattr(eng, "_loop", None)
                )
                try:
                    if target_loop is not None and target_loop.is_running():
                        future = asyncio.run_coroutine_threadsafe(coro, target_loop)
                        res = future.result(timeout=10.0)
                    else:
                        res = asyncio.run(coro)
                    self._on_engine_event("roster_updated", self.ttrpg_service.get_campaign_roster())
                    self._on_engine_event("ttrpg_state_updated", res)
                    return res
                except Exception as e:
                    try:
                        coro.close()
                    except Exception:
                        pass
                    return {"status": "error", "message": str(e)}
            return coro

        # Direct fallback for cast_spell if dispatcher is not ready
        if tool_name == "cast_spell":
            res = self.ttrpg_service.cast_character_spell(
                character_name=params.get("character_name", ""),
                spell_name=params.get("spell_name", ""),
                spell_level=int(params.get("spell_level", 0)),
                slot_level=int(params.get("slot_level")) if params.get("slot_level") is not None else None
            )
            if res.get("status") == "success":
                self._on_engine_event("roster_updated", self.ttrpg_service.get_campaign_roster())
                self._on_engine_event("ttrpg_state_updated", res)
            return res

        return {"status": "error", "message": f"Tool '{tool_name}' could not be dispatched."}

    # ------------------------------------------------------------------
    # Character Vault & Cross-Genre Branching Endpoints
    # ------------------------------------------------------------------
    def list_vault_characters(self) -> List[Dict[str, Any]]:
        """Lists all persistent characters in the Character Vault."""
        eng = getattr(self, "_engine", getattr(self, "engine", None))
        if eng and hasattr(eng, "vault_service") and eng.vault_service:
            return eng.vault_service.list_characters()
        from core.character_vault_service import CharacterVaultService
        return CharacterVaultService().list_characters()

    def get_vault_character(self, char_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves full character data from the Character Vault."""
        eng = getattr(self, "_engine", getattr(self, "engine", None))
        if eng and hasattr(eng, "vault_service") and eng.vault_service:
            return eng.vault_service.get_character(char_id)
        from core.character_vault_service import CharacterVaultService
        return CharacterVaultService().get_character(char_id)

    def save_vault_character(self, char_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Creates or updates a character in the Character Vault."""
        eng = getattr(self, "_engine", getattr(self, "engine", None))
        svc = getattr(eng, "vault_service", None)
        if not svc:
            from core.character_vault_service import CharacterVaultService
            svc = CharacterVaultService()
        res = svc.save_character(char_payload)
        if res.get("status") == "success":
            self._on_engine_event("vault_character_updated", res)
        return res

    def delete_vault_character(self, char_id: str) -> Dict[str, Any]:
        """Deletes a character from the Character Vault."""
        eng = getattr(self, "_engine", getattr(self, "engine", None))
        svc = getattr(eng, "vault_service", None)
        if not svc:
            from core.character_vault_service import CharacterVaultService
            svc = CharacterVaultService()
        success = svc.delete_character(char_id)
        if success:
            self._on_engine_event("vault_character_updated", {"character_id": char_id, "deleted": True})
        return {"status": "success" if success else "error"}

    def upload_vault_character_document(
        self,
        filename: str,
        base64_content: str,
        ruleset_override: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Uploads a character sheet document (PDF AcroForm, Gemini Multimodal OCR, JSON)
        directly into the Character Vault as a permanent Canonical Root hero.
        """
        import base64
        try:
            b64_clean = base64_content.split(",", 1)[1] if "," in base64_content else base64_content
            raw_bytes = base64.b64decode(b64_clean)
        except Exception as e:
            return {"status": "error", "message": f"Invalid file encoding: {e}"}

        eng = getattr(self, "_engine", getattr(self, "engine", None))
        svc = getattr(eng, "vault_service", None)
        if not svc:
            from core.character_vault_service import CharacterVaultService
            svc = CharacterVaultService()

        res = svc.import_from_document(
            filename=filename,
            content_bytes=raw_bytes,
            ttrpg_service=self.ttrpg_service,
            ruleset_override=ruleset_override
        )
        if res.get("status") == "success":
            self._on_engine_event("vault_character_updated", res)
        return res

    def check_vault_character_exists(self, character_name_or_id: str) -> Dict[str, Any]:
        """Checks if a character with the given name or id already exists in the Character Vault."""
        eng = getattr(self, "_engine", getattr(self, "engine", None))
        svc = getattr(eng, "vault_service", None)
        if not svc:
            from core.character_vault_service import CharacterVaultService
            svc = CharacterVaultService()

        clean_id_name = re.sub(r'[^a-zA-Z0-9_]', '', (character_name_or_id or "").lower().replace(" ", "_"))
        root_id = f"char_{clean_id_name}_root"
        existing = svc.get_character(root_id)
        if not existing and character_name_or_id:
            for c in svc.list_characters():
                if (c.get("name") or "").lower() == character_name_or_id.lower() or c.get("id") == character_name_or_id:
                    return {"exists": True, "root_id": c.get("id"), "character": c}
            return {"exists": False, "root_id": root_id, "character": None}
        return {"exists": True, "root_id": root_id, "character": existing}

    def save_campaign_character_to_vault(
        self,
        character_name_or_id: str,
        campaign_id: Optional[str] = None,
        save_mode: str = "overwrite",
        branch_label: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Exports a character from the active or specified campaign party roster
        into the Character Vault as a permanent Canonical Root hero or a Branch variant.
        """
        eng = getattr(self, "_engine", getattr(self, "engine", None))
        svc = getattr(eng, "vault_service", None)
        if not svc:
            from core.character_vault_service import CharacterVaultService
            svc = CharacterVaultService()

        cid = campaign_id or self.ttrpg_service.manifest.get("active_campaign")
        camp = self.ttrpg_service.manifest.get("campaigns", {}).get(cid, {}) if cid else {}
        chars = (self.ttrpg_service.active_state or {}).get("characters", {})

        target_char = None
        if character_name_or_id in chars:
            target_char = chars[character_name_or_id]
        else:
            for c in chars.values():
                if c.get("name", "").lower() == character_name_or_id.lower() or c.get("id") == character_name_or_id:
                    target_char = c
                    break

        if not target_char:
            return {"status": "error", "message": f"Character '{character_name_or_id}' not found in active campaign roster."}

        res = svc.import_from_campaign(target_char, campaign_meta=camp, save_mode=save_mode, branch_label=branch_label)
        if res.get("status") == "success":
            self._on_engine_event("vault_character_updated", res)
        return res

    def list_campaign_characters_for_vault(self, campaign_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists characters currently in the active campaign available for export to the Vault."""
        chars = (self.ttrpg_service.active_state or {}).get("characters", {})
        results = []
        for cid_key, c in chars.items():
            results.append({
                "id": c.get("id") or cid_key,
                "name": c.get("name", "Hero"),
                "level": c.get("level", 1),
                "archetype": c.get("archetype", "Adventurer"),
                "player_name": c.get("player_name", "Player")
            })
        return results

    def import_vault_character_to_campaign(
        self,
        char_id: str,
        campaign_id: Optional[str] = None,
        quarantine_unmatched: bool = True,
        adjudications: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Imports a character from the vault into the active campaign.
        If cross-genre, forks a variant branch, applies item adjudications,
        and adds the character to the active party roster.
        """
        eng = getattr(self, "_engine", getattr(self, "engine", None))
        svc = getattr(eng, "vault_service", None)
        if not svc:
            from core.character_vault_service import CharacterVaultService
            svc = CharacterVaultService()

        # Determine target campaign genre
        cid = campaign_id or self.ttrpg_service.manifest.get("active_campaign")
        camp = self.ttrpg_service.manifest.get("campaigns", {}).get(cid, {}) if cid else {}
        target_genre = camp.get("system_preset") or camp.get("genre") or "heroic_fantasy"

        import_res = svc.import_to_campaign(char_id, target_genre=target_genre, quarantine_unmatched=quarantine_unmatched)
        if import_res.get("status") != "success":
            return import_res

        char_data = import_res["character"]

        # Apply specific item adjudications if provided
        if adjudications and isinstance(adjudications, dict):
            for item_name, res_choice in adjudications.items():
                svc.adjudicate_item(
                    char_id=char_data["id"],
                    item_name=item_name,
                    resolution=res_choice,
                    target_genre=target_genre
                )
            char_data = svc.get_character(char_data["id"]) or char_data

        # Add character to campaign party roster (using Once and Done state isolation if schema 2.0 or universal core)
        if "universal_core" in char_data or "target_system_attributes" in char_data:
            self.ttrpg_service.import_character_to_active_campaign(char_data, overwrite_existing=True)
        else:
            self.ttrpg_service.add_character(char_data)
        self._on_engine_event("ttrpg_state_updated", {"status": "success", "imported_vault_char": char_data["id"]})
        self._on_engine_event("vault_character_updated", {"character_id": char_data["id"]})

        return {
            "status": "success",
            "character": char_data,
            "is_crossover": import_res.get("is_crossover", False),
            "message": f"Successfully imported '{char_data.get('name')}' to campaign party roster."
        }

    def level_up_vault_character(
        self,
        char_id: str,
        level_increment: int = 1,
        stat_boosts: Optional[Dict[str, int]] = None,
        new_traits: Optional[List[Dict[str, str]]] = None
    ) -> Dict[str, Any]:
        """
        Levels up character via Vault Service and syncs party roster if present.
        """
        eng = getattr(self, "_engine", getattr(self, "engine", None))
        svc = getattr(eng, "vault_service", None)
        if not svc:
            from core.character_vault_service import CharacterVaultService
            svc = CharacterVaultService()

        active_genre = eng._get_current_campaign_genre() if hasattr(eng, "_get_current_campaign_genre") else "heroic_fantasy"
        res = svc.level_up_character(
            char_id=char_id,
            current_campaign_genre=active_genre,
            level_increment=int(level_increment),
            stat_boosts=stat_boosts,
            new_traits=new_traits
        )

        if res.get("status") == "success":
            # Sync with campaign party roster if character is present
            chars = (self.ttrpg_service.active_state or {}).get("characters", {})
            updated_char = svc.get_character(char_id)
            if updated_char and (char_id in chars or any(c.get("name") == updated_char.get("name") for c in chars.values())):
                self.ttrpg_service.add_character(updated_char)
                self._on_engine_event("ttrpg_state_updated", {"status": "success", "level_up": char_id})
            self._on_engine_event("vault_character_updated", res)

        return res

    def _format_history_turns(self, history: list) -> list:
        formatted = []
        if not history:
            return formatted
        for t in history:
            if not isinstance(t, dict):
                continue
            role = t.get("role", "user")
            text = ""
            parts = t.get("parts", [])
            if parts and isinstance(parts, list):
                first = parts[0]
                if isinstance(first, dict):
                    text = first.get("text", "")
                elif isinstance(first, str):
                    text = str(first)
            elif "content" in t:
                text = t.get("content", "")

            if not text:
                continue

            # Strip any GM framing wrapper from displayed history turns
            if text.startswith("[TABLETOP ROLEPLAY ACTION"):
                match = re.search(r"Player Action / Dialogue:\s*(.*?)(?:\n\[GAME MASTER DIRECTIVE|\Z)", text, re.DOTALL)
                if match:
                    text = match.group(1).strip()

            msg_type = "user" if role == "user" else "assistant"
            formatted.append({
                "type": msg_type,
                "role": role,
                "content": text,
                "timestamp": t.get("timestamp", ""),
                "source": t.get("source", "text")
            })
        return formatted

    def get_workspace_history(self, workspace_type: str, context_id: str) -> Dict[str, Any]:
        """Fetches persistent conversation bubbles for a given workspace and context."""
        eng = getattr(self, "_engine", getattr(self, "engine", None))
        if eng and hasattr(eng, "session_mgr") and eng.session_mgr:
            sid = f"{workspace_type}_{context_id}".lower().strip()
            if eng.session_mgr.active_session_id == sid:
                return {
                    "status": "success",
                    "session_id": sid,
                    "history": self._format_history_turns(eng.session_mgr.history)
                }
            spath = eng.session_mgr.get_session_path(sid)
            if os.path.exists(spath):
                try:
                    with open(spath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        raw = data.get("history", [])
                        return {
                            "status": "success",
                            "session_id": sid,
                            "history": self._format_history_turns(raw)
                        }
                except Exception as e:
                    logger.debug(f"[GUI_BRIDGE] get_workspace_history read error: {e}")
            return {"status": "success", "session_id": sid, "history": []}
        return {"status": "error", "message": "Engine not initialized"}

    def clear_workspace_history(self, workspace_type: str, context_id: str) -> Dict[str, Any]:
        """Clears persistent history for a workspace and resets AI turn memory."""
        eng = getattr(self, "_engine", getattr(self, "engine", None))
        sid = f"{workspace_type}_{context_id}".lower().strip()
        if eng and hasattr(eng, "session_mgr") and eng.session_mgr:
            if eng.session_mgr.active_session_id == sid:
                eng.session_mgr.clear_active_history()
            else:
                spath = eng.session_mgr.get_session_path(sid)
                if os.path.exists(spath):
                    try:
                        os.remove(spath)
                    except Exception:
                        pass
            if hasattr(eng, "invalidate_live_session"):
                try:
                    eng.invalidate_live_session(reason=f"User cleared workspace history: {sid}")
                except Exception:
                    pass
            return {"status": "success", "session_id": sid}
        return {"status": "error", "message": "Engine not initialized"}

    def send_workspace_message(self, workspace_type: str, context_id: str, text: str) -> Dict[str, Any]:
        """Sends a text message routed to a specific workspace context."""
        clean_text = (text or "").strip()
        if not clean_text:
            return {"success": False, "error": "Empty message"}
        eng = getattr(self, "_engine", getattr(self, "engine", None))
        if not eng:
            return {"success": False, "error": "Engine not running"}

        sid = f"{workspace_type}_{context_id}".lower().strip()

        # Enforce TTRPG engine locking when routing to campaign workspace
        if workspace_type == "campaign":
            self.set_ttrpg_session_active(True)
            if hasattr(eng, "set_ttrpg_session_active"):
                eng.set_ttrpg_session_active(True)
            try:
                from core.campaign.campaign_manager import CampaignManager
                import tools.campaign_tools as ct
                ct._CAMPAIGN = CampaignManager(campaign_id=context_id, storage_root=self.ttrpg_service.data_dir)
            except Exception as e:
                logger.debug(f"[GUIBridge] Failed to update campaign_tools singleton: {e}")

        needs_switch = (
            getattr(eng, "active_workspace_type", "") != workspace_type
            or getattr(eng, "active_workspace_context_id", "") != context_id
        )
        if needs_switch:
            eng.set_active_workspace(workspace_type, context_id)
        elif workspace_type == "campaign":
            if hasattr(self, "ttrpg_service") and self.ttrpg_service:
                self.ttrpg_service.manifest["active_campaign"] = context_id
                self.ttrpg_service.active_campaign_id = context_id
                try:
                    eng.active_system_prompt = self.ttrpg_service.build_gm_context_prompt()
                except Exception as e:
                    logger.error(f"[GUI_BRIDGE] Error refreshing TTRPG GM prompt: {e}", exc_info=True)

        try:
            if not eng.is_running:
                logger.info(f"[GUI_BRIDGE] Auto-starting engine from workspace message ({workspace_type}:{context_id})")
                self.start_assistant()

            prompt_to_send = clean_text
            display_text = clean_text
            if workspace_type == "campaign":
                camp = self.ttrpg_service.manifest.get("campaigns", {}).get(context_id, {}) if hasattr(self, "ttrpg_service") and self.ttrpg_service else {}
                camp_title = camp.get("title", context_id or "Active Campaign")
                if not clean_text.startswith("Aether, please act as our Game Master") and not clean_text.startswith("[TABLETOP ROLEPLAY"):
                    active_chars = (
                        (self.ttrpg_service.active_state or {}).get("characters", {})
                        if hasattr(self, "ttrpg_service") and self.ttrpg_service else {}
                    )
                    pc_names = [
                        str(c.get("name", "")).strip()
                        for c in active_chars.values()
                        if isinstance(c, dict) and str(c.get("name", "")).strip()
                    ]
                    party_label = ", ".join(pc_names) if pc_names else "the active party roster"
                    if len(pc_names) >= 2:
                        ask_party_example = f"'Who in the party steps forward to do this? {', '.join(pc_names[:-1])}, or {pc_names[-1]}?'"
                    elif len(pc_names) == 1:
                        ask_party_example = f"'Does {pc_names[0]} step forward to attempt this?'"
                    else:
                        ask_party_example = "'Which character steps forward to attempt this?'"
                    prompt_to_send = (
                        f"[TABLETOP ROLEPLAY ACTION — CAMPAIGN: {camp_title}]\n"
                        f"Player Action / Dialogue: {clean_text}\n"
                        f"[GAME MASTER DIRECTIVE: Adjudicate this action strictly within the active adventure '{camp_title}'.\n"
                        f"- MANDATORY CHARACTER SELECTION & PLAYER AGENCY:\n"
                        f"  * Active Party Roster: {party_label}. YOU DO NOT CHOOSE WHO ACTS!\n"
                        f"  * If the player specifies which character acts: resolve immediately with that character.\n"
                        f"  * If the player did NOT name a character (e.g. '{clean_text}'):\n"
                        f"    - If this is dialogue, asking an NPC questions, or movement: DO NOT ROLL DICE! Answer directly in-character as the NPC or describe the travel.\n"
                        f"    - If an action genuinely requires a skill check: ASK THE TABLE: {ask_party_example} DO NOT arbitrarily pick an actor or call roll_dice until the player specifies who acts!\n"
                        f"- STRICT BAN ON UNSOLICITED DIALOGUE ROLLS: Routine conversation with NPCs (asking questions, pressing for clues, talking to guards) is NORMAL DIALOGUE, NOT a Persuasion check! Moving to an area (e.g. heading to the western gate) is MOVEMENT, NOT a Survival check! Never roll dice for ordinary conversation or travel!\n"
                        f"- ACTOR ATTRIBUTION & CLEAN NARRATION: Every dice roll MUST be explicitly attributed to a character or monster/DM. Always pass their name in `character_name` when calling `roll_dice`. In your narration, state the character and the final total (e.g. '<Character> rolled a 16')—DO NOT read off or append dice formulas like '(1d20+4)'.\n"
                        f"- AUTONOMOUS REWARD & LOOT BESTOWAL: When the player asks you to grant class-appropriate weapons or gear upon a successful check without naming exact items, DO NOT ask them which items to add—call `grant_loot(mode='class_reward', character_name='all', ...)` (or `complete_quest` at quest end, or `manage_party_inventory` for exact items) immediately in this same turn using tier-scaled, proficiency-legal gear for each hero.\n"
                        f"- Do NOT answer as a general desktop assistant.]"
                    )

            coro = eng.send_text(prompt_to_send, display_text=display_text)
            asyncio.run_coroutine_threadsafe(coro, self._loop)
            return {"success": True, "session_id": sid}
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] send_workspace_message error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def set_active_workspace(
        self,
        workspace_type: str,
        context_id: str,
        display_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Switches workspace context and rehydrates history."""
        eng = getattr(self, "_engine", getattr(self, "engine", None))
        if eng and hasattr(eng, "set_active_workspace"):
            history = eng.set_active_workspace(workspace_type, context_id, display_name)
            formatted = self._format_history_turns(history)
            return {"status": "success", "history": formatted, "session_id": f"{workspace_type}_{context_id}"}
        return {"status": "error", "message": "Engine not initialized"}

    # --- D&D 5e (2014 & 2024) SRD Compendium & Custom Homebrew Endpoints ---

    def search_dnd_compendium(
        self,
        category: str = "all",
        query: str = "",
        edition: str = "all",
        filters: Optional[Dict[str, Any]] = None,
        homebrew_only: bool = False,
        limit: int = 200,
    ) -> Dict[str, Any]:
        """Searches the offline D&D 5e (2014/2024 SRD + Homebrew) compendium."""
        try:
            from core.compendiums import search_compendium, get_compendium_stats
            items = search_compendium(
                category=category,
                query=query,
                edition=edition,
                filters=filters,
                homebrew_only=bool(homebrew_only),
                limit=int(limit) if limit else 200,
            )
            stats = get_compendium_stats()
            return {"status": "success", "items": items, "count": len(items), "stats": stats}
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] search_dnd_compendium error: {e}", exc_info=True)
            return {"status": "error", "message": str(e), "items": [], "count": 0}

    def get_dnd_compendium_item(
        self,
        category: str,
        index_or_name: str,
        edition: str = "2024",
    ) -> Dict[str, Any]:
        """Retrieves full details (including pack contents or 2014/2024 notes) for a compendium item."""
        try:
            from core.compendiums.dnd_weapons import get_weapon
            from core.compendiums.dnd_armor import get_armor
            from core.compendiums.dnd_items import get_item, expand_pack_contents
            from core.compendiums.dnd_spells import get_spell

            cat = (category or "").strip().lower()
            entry = None
            if cat in ("weapons", "weapon"):
                entry = get_weapon(index_or_name)
            elif cat in ("armor", "armors", "shield", "shields"):
                entry = get_armor(index_or_name)
            elif cat in ("items", "item", "gear", "magic_items"):
                entry = get_item(index_or_name)
                if entry and entry.get("contents"):
                    entry["expanded_contents"] = expand_pack_contents(entry.get("index") or entry["name"])
            elif cat in ("spells", "spell"):
                entry = get_spell(index_or_name, edition=edition)

            if not entry:
                return {"status": "error", "message": f"'{index_or_name}' not found in '{category}'."}
            return {"status": "success", "item": entry}
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] get_dnd_compendium_item error: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    def get_dnd_compendium_stats(self) -> Dict[str, Any]:
        """Returns entry counts across Weapons, Armor, Items, Spells, and Homebrew."""
        try:
            from core.compendiums import get_compendium_stats
            return {"status": "success", "stats": get_compendium_stats()}
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] get_dnd_compendium_stats error: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    def list_weapon_masteries(self) -> Dict[str, Any]:
        """Returns 2024 D&D 5e Weapon Mastery properties (Cleave, Graze, Nick, Push, Sap, Slow, Topple, Vex)."""
        try:
            from core.compendiums.dnd_weapons import list_weapon_masteries
            return {"status": "success", "masteries": list_weapon_masteries()}
        except Exception as e:
            return {"status": "error", "message": str(e), "masteries": []}

    def list_homebrew_entries(self, category: str = "all") -> Dict[str, Any]:
        """Lists all custom homebrew weapons, armor, items, and spells."""
        try:
            from core.compendiums.homebrew_manager import get_default_homebrew_manager
            entries = get_default_homebrew_manager().list_entries(category=category)
            return {"status": "success", "entries": entries, "count": len(entries)}
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] list_homebrew_entries error: {e}", exc_info=True)
            return {"status": "error", "message": str(e), "entries": []}

    def save_homebrew_entry(self, category: str, entry_data: Dict[str, Any]) -> Dict[str, Any]:
        """Creates or updates a custom homebrew weapon, armor, item, or spell."""
        try:
            from core.compendiums.homebrew_manager import get_default_homebrew_manager
            res = get_default_homebrew_manager().save_entry(category=category, entry=entry_data or {})
            if res.get("status") == "success":
                self._on_engine_event("homebrew_updated", res)
            return res
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] save_homebrew_entry error: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    def delete_homebrew_entry(self, category: str, entry_id: str) -> Dict[str, Any]:
        """Deletes a custom homebrew entry from persistent JSON storage."""
        try:
            from core.compendiums.homebrew_manager import get_default_homebrew_manager
            res = get_default_homebrew_manager().delete_entry(category=category, entry_id=entry_id)
            if res.get("status") == "success":
                self._on_engine_event("homebrew_updated", res)
            return res
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] delete_homebrew_entry error: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    def add_compendium_entry_to_character(
        self,
        character_name: str,
        category: str,
        index_or_name: str,
        edition: str = "2024",
        expand_pack: bool = True,
        qty: int = 1,
        equip: bool = False,
        bypass_class_check: bool = False,
        prepare_spell: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Grants a compendium item/weapon/armor/spell directly to a character in the active campaign."""
        try:
            res = self.ttrpg_service.add_compendium_entry_to_character(
                character_name=character_name,
                category=category,
                index_or_name=index_or_name,
                edition=edition,
                expand_pack=bool(expand_pack),
                qty=max(1, int(qty or 1)),
                equip=bool(equip),
                bypass_class_check=bool(bypass_class_check),
                prepare_spell=prepare_spell,
            )
            if res.get("status") == "success":
                self._on_engine_event("ttrpg_state_updated", res)
                self._on_engine_event("roster_updated", self.ttrpg_service.get_campaign_roster())
            return res
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] add_compendium_entry_to_character error: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    def check_character_spell_legality(
        self,
        character_name: str,
        edition: str = "2024",
    ) -> Dict[str, Any]:
        """Checks if a campaign character's prepared and known spells adhere to D&D 5e limits."""
        try:
            res = self.ttrpg_service.check_character_spell_legality(character_name, edition=edition)
            res["status"] = "success"
            return res
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] check_character_spell_legality error: {e}", exc_info=True)
            return {"status": "error", "message": str(e), "is_legal": True}

    def check_spell_class_eligibility(
        self,
        character_name: str,
        spell_name: str,
        edition: str = "2024",
    ) -> Dict[str, Any]:
        """Validates if a campaign character is eligible to learn/prepare a spell according to D&D 5e class rules."""
        try:
            if not self.ttrpg_service._ensure_active_state():
                return {"status": "error", "message": "No active campaign loaded.", "can_learn": True}
            cid, char_data = self.ttrpg_service._find_character(character_name)
            if not char_data:
                return {"status": "error", "message": f"Character '{character_name}' not found.", "can_learn": True}
            from core.compendiums.dnd_spells import validate_character_spell_assignment
            val = validate_character_spell_assignment(char_data, spell_name, edition=edition)
            val["status"] = "success"
            return val
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] check_spell_class_eligibility error: {e}", exc_info=True)
            return {"status": "error", "message": str(e), "can_learn": True}

    # --- Phase 2: Location-Based Merchant Shop & Interactive Short / Long Rest ---

    def get_merchant_catalog(
        self,
        specialty: str = "all",
        query: str = "",
        edition: Optional[str] = None,
        price_multiplier: float = 1.0,
        include_magic: bool = False,
    ) -> Dict[str, Any]:
        """Returns priced shop items/weapons/armor with location markup/discount multiplier."""
        try:
            return self.ttrpg_service.get_merchant_catalog(
                specialty=specialty,
                query=query,
                edition=edition,
                price_multiplier=float(price_multiplier or 1.0),
                include_magic=bool(include_magic),
            )
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] get_merchant_catalog error: {e}", exc_info=True)
            return {"status": "error", "message": str(e), "items": []}

    def open_merchant_shop(
        self,
        shop_name: str = "Local Merchant",
        location_name: str = "",
        price_multiplier: float = 1.0,
        specialty: str = "general",
        description: str = "",
        include_magic: bool = False,
    ) -> Dict[str, Any]:
        """Sets active shop metadata and emits shop_opened event."""
        try:
            res = self.ttrpg_service.set_active_shop(
                shop_name=shop_name,
                location_name=location_name,
                price_multiplier=float(price_multiplier or 1.0),
                specialty=specialty,
                description=description,
                include_magic=bool(include_magic),
            )
            if res.get("status") == "success":
                self._on_engine_event("shop_opened", res["shop"])
                self._on_engine_event("ttrpg_state_updated", res)
            return res
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] open_merchant_shop error: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    def purchase_merchant_item(
        self,
        character_name: str,
        category: str = "auto",
        index_or_name: str = "",
        qty: int = 1,
        price_multiplier: float = 1.0,
        expand_pack: bool = True,
        edition: Optional[str] = None,
        equip: bool = False,
    ) -> Dict[str, Any]:
        """Purchases an item from the active shop using character currency."""
        try:
            res = self.ttrpg_service.purchase_merchant_item(
                character_name=character_name,
                category=category,
                index_or_name=index_or_name,
                qty=max(1, int(qty or 1)),
                price_multiplier=float(price_multiplier or 1.0),
                expand_pack=bool(expand_pack),
                edition=edition,
                equip=bool(equip),
            )
            if res.get("status") == "success":
                self._on_engine_event("ttrpg_state_updated", res)
                self._on_engine_event("roster_updated", self.ttrpg_service.get_campaign_roster())
            return res
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] purchase_merchant_item error: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    def sell_merchant_item(
        self,
        character_name: str,
        item_name: str,
        sell_rate: float = 0.5,
        qty: int = 1,
        source: str = "auto",
        override_value_gp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Sells an owned inventory item or weapon to a merchant."""
        try:
            res = self.ttrpg_service.sell_merchant_item(
                character_name=character_name,
                item_name=item_name,
                sell_rate=float(sell_rate or 0.5),
                qty=max(1, int(qty or 1)),
                source=source,
                override_value_gp=override_value_gp,
            )
            if res.get("status") == "success":
                self._on_engine_event("ttrpg_state_updated", res)
                self._on_engine_event("roster_updated", self.ttrpg_service.get_campaign_roster())
            return res
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] sell_merchant_item error: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    def get_rest_modal_state(self, rest_type: str = "short", edition: Optional[str] = None) -> Dict[str, Any]:
        """Returns interactive Short/Long Rest state across all campaign characters."""
        try:
            return self.ttrpg_service.get_rest_modal_state(rest_type=rest_type, edition=edition)
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] get_rest_modal_state error: {e}", exc_info=True)
            return {"status": "error", "message": str(e), "characters": []}

    def commit_party_rest(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Applies interactive Short/Long Rest choices (Hit Dice, consumables, gear, attunement, spells)."""
        try:
            res = self.ttrpg_service.commit_interactive_rest(payload or {})
            if res.get("status") == "success":
                self._on_engine_event("ttrpg_state_updated", res)
                self._on_engine_event("roster_updated", self.ttrpg_service.get_campaign_roster())
            return res
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] commit_party_rest error: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    # --- Phase 3: D&D 5e (2014 & 2024) Guided Level-Up & Multiclass Wizard ---

    @staticmethod
    def _is_dnd_character_dict(char_data: Dict[str, Any]) -> bool:
        ruleset = str(char_data.get("ruleset") or char_data.get("native_system") or "").strip().lower()
        if ruleset in ("coc_7e", "coc", "call_of_cthulhu", "cyberpunk_red", "cyberpunk", "pf2e", "pathfinder", "pathfinder_2e"):
            return False
        attrs = char_data.get("attributes") or {}
        vitals = char_data.get("vitals") or {}
        if "SAN" in vitals or "sanity" in vitals:
            return False
        if any(k in ruleset for k in ("dnd", "5e", "dungeons", "heroic_d20", "fantasy")):
            return True
        if attrs.get("Strength") is not None:
            return True
        return True if not ruleset else False

    def get_character_advancement_options(
        self,
        char_id_or_name: str,
        edition: Optional[str] = None,
        target_class: Optional[str] = None,
        source: str = "campaign",
        bypass_prereqs: bool = False,
    ) -> Dict[str, Any]:
        """Returns Guided Level-Up & Multiclass options for a Campaign or Vault character."""
        try:
            src = str(source or "campaign").strip().lower()
            if src == "vault":
                eng = getattr(self, "_engine", getattr(self, "engine", None))
                svc = getattr(eng, "vault_service", None)
                if not svc:
                    from core.character_vault_service import CharacterVaultService
                    svc = CharacterVaultService()
                char_obj = svc.get_character(char_id_or_name)
                if not char_obj:
                    return {"status": "error", "message": f"Character '{char_id_or_name}' not found in Vault."}
                if not self._is_dnd_character_dict(char_obj):
                    return {
                        "status": "success",
                        "is_dnd": False,
                        "source": "vault",
                        "ruleset": char_obj.get("ruleset") or char_obj.get("native_system") or "generic",
                        "ruleset_label": char_obj.get("ruleset_label") or char_obj.get("ruleset") or "Tabletop RPG",
                        "character_id": char_obj.get("id", char_id_or_name),
                        "character_name": char_obj.get("name", "Adventurer"),
                        "archetype": char_obj.get("archetype", ""),
                        "current_total_level": int(char_obj.get("level", 1) or 1),
                        "next_total_level": int(char_obj.get("level", 1) or 1) + 1,
                        "experience_points": int(char_obj.get("experience_points", 0) or 0),
                        "attributes": char_obj.get("attributes") or {},
                    }
                ed = edition if str(edition or "").strip() in ("2014", "2024") else (
                    char_obj.get("ruleset_edition")
                    or (self.ttrpg_service._get_campaign_edition() if hasattr(self.ttrpg_service, "_get_campaign_edition") else "2024")
                )
                res = svc.get_dnd_advancement_options(
                    char_id=char_obj["id"],
                    edition=ed,
                    target_class=target_class,
                    bypass_prereqs=bool(bypass_prereqs),
                )
                res["is_dnd"] = True
                res["ruleset"] = char_obj.get("ruleset") or "dnd_5e"
                return res

            # Campaign source
            if not self.ttrpg_service._ensure_active_state():
                return {"status": "error", "message": "No active campaign loaded."}
            cid, char_obj = self.ttrpg_service._find_character(char_id_or_name)
            if not char_obj:
                return {"status": "error", "message": f"Character '{char_id_or_name}' not found."}
            if not self._is_dnd_character_dict(char_obj):
                return {
                    "status": "success",
                    "is_dnd": False,
                    "source": "campaign",
                    "ruleset": char_obj.get("ruleset") or "generic",
                    "ruleset_label": char_obj.get("ruleset_label") or char_obj.get("ruleset") or "Tabletop RPG",
                    "character_id": cid,
                    "character_name": char_obj.get("name", char_id_or_name),
                    "archetype": char_obj.get("archetype", ""),
                    "current_total_level": int(char_obj.get("level", 1) or 1),
                    "next_total_level": int(char_obj.get("level", 1) or 1) + 1,
                    "experience_points": int(char_obj.get("experience_points", 0) or 0),
                    "attributes": char_obj.get("attributes") or {},
                }
            res = self.ttrpg_service.get_character_advancement_options(
                character_name_or_id=char_id_or_name,
                edition=edition,
                target_class=target_class,
                bypass_prereqs=bool(bypass_prereqs),
            )
            res["is_dnd"] = True
            res["ruleset"] = char_obj.get("ruleset") or "dnd_5e"
            return res
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] get_character_advancement_options error: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    def apply_character_dnd_level_up(
        self,
        char_id_or_name: str,
        payload: Optional[Dict[str, Any]] = None,
        source: str = "campaign",
    ) -> Dict[str, Any]:
        """Applies guided D&D 5e (2014 or 2024) single-class or multiclass level-up for Campaign or Vault."""
        try:
            src = str(source or "campaign").strip().lower()
            data = dict(payload or {})
            if src == "vault":
                eng = getattr(self, "_engine", getattr(self, "engine", None))
                svc = getattr(eng, "vault_service", None)
                if not svc:
                    from core.character_vault_service import CharacterVaultService
                    svc = CharacterVaultService()
                active_genre = eng._get_current_campaign_genre() if (eng and hasattr(eng, "_get_current_campaign_genre")) else None
                res = svc.apply_dnd_level_up(
                    char_id=char_id_or_name,
                    payload=data,
                    current_campaign_genre=active_genre,
                )
                if res.get("status") == "success":
                    updated_char = res.get("character")
                    chars = (self.ttrpg_service.active_state or {}).get("characters", {})
                    if updated_char and (
                        char_id_or_name in chars
                        or any(c.get("name") == updated_char.get("name") for c in chars.values())
                    ):
                        self.ttrpg_service.add_character(updated_char)
                        self._on_engine_event("ttrpg_state_updated", res)
                        self._on_engine_event("roster_updated", self.ttrpg_service.get_campaign_roster())
                    self._on_engine_event("vault_character_updated", res)
                return res

            res = self.ttrpg_service.apply_dnd_level_up(
                character_name_or_id=char_id_or_name,
                payload=data,
            )
            if res.get("status") == "success":
                self._on_engine_event("ttrpg_state_updated", res)
                self._on_engine_event("roster_updated", self.ttrpg_service.get_campaign_roster())
            return res
        except Exception as e:
            logger.error(f"[GUI_BRIDGE] apply_character_dnd_level_up error: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    # =========================================================================
    # Application Version & GitHub Releases Auto-Updater
    # =========================================================================

    def get_app_version(self) -> Dict[str, Any]:
        """Returns the current application version, channel, and packaging mode."""
        from core.paths import is_frozen
        from core.version import APP_CHANNEL, APP_VERSION, GITHUB_RELEASES_PAGE, GITHUB_REPO
        return {
            "version": APP_VERSION,
            "channel": APP_CHANNEL,
            "repo": GITHUB_REPO,
            "releases_page": GITHUB_RELEASES_PAGE,
            "is_frozen": is_frozen(),
        }

    def check_for_updates(self, force: bool = False) -> Dict[str, Any]:
        """
        Checks GitHub Releases for a newer version than the local APP_VERSION.
        When force=False (auto-check on load), respects skipped_update_version.
        """
        from core.updater import check_for_updates as _check_updates
        result = _check_updates()
        with self._config_lock:
            skipped = str(self._config.get("skipped_update_version") or "").strip()
        latest = str(result.get("latest_version") or "").strip()
        result["skipped"] = bool(
            not force
            and result.get("update_available")
            and skipped
            and latest
            and skipped == latest
        )
        return result

    def skip_update_version(self, version: str) -> Dict[str, Any]:
        """Persists a version string to skip automatic startup update prompts for it."""
        clean_ver = str(version or "").strip()
        with self._config_lock:
            self._config["skipped_update_version"] = clean_ver
            save_config_atomic(self._config, self._config_path)
        return {"success": True, "skipped_update_version": clean_ver}

    def open_release_page(self, url: str = "") -> Dict[str, Any]:
        """Opens the GitHub release URL in the user's default browser."""
        import webbrowser
        from core.updater import is_trusted_github_url
        from core.version import GITHUB_RELEASES_PAGE
        target = str(url or "").strip() or GITHUB_RELEASES_PAGE
        if not is_trusted_github_url(target):
            target = GITHUB_RELEASES_PAGE
        try:
            webbrowser.open(target)
            return {"success": True, "url": target}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def download_and_install_update(self, download_url: str = "", expected_sha256: str = "") -> Dict[str, Any]:
        """
        Downloads, SHA-256 verifies, and stages the release .zip on a background thread,
        pushes progress events ('update_progress') to the UI, performs an atomic
        pure-Python .old rename swap, relaunches AetherDesktop.exe, and exits cleanly.
        """
        from core.paths import is_frozen
        from core.updater import (
            apply_staged_update_and_relaunch,
            check_for_updates as _check_updates,
            download_and_stage_update,
        )

        if getattr(self, "_update_in_progress", False):
            return {"success": False, "error": "An update download is already in progress."}

        if not is_frozen():
            return {
                "success": False,
                "dev_mode": True,
                "error": "Auto-install only runs in the packaged AetherDesktop.exe build. Use Git pull or download from GitHub Releases.",
            }

        target_url = str(download_url or "").strip()
        target_sha256 = str(expected_sha256 or "").strip()
        if not target_url or not target_sha256:
            check_res = _check_updates()
            if not target_url:
                target_url = str(check_res.get("download_url") or "").strip()
            if not target_sha256:
                target_sha256 = str(check_res.get("expected_sha256") or "").strip()
        if not target_url:
            return {
                "success": False,
                "error": "No downloadable .zip asset found on the latest GitHub release.",
            }

        self._update_in_progress = True

        def _worker():
            last_pct = -1
            try:
                def _on_progress(downloaded: int, total: int, stage: str):
                    nonlocal last_pct
                    if stage == "downloading":
                        pct = int((downloaded * 100) / total) if total > 0 else 0
                        if pct == last_pct and downloaded < total:
                            return
                        last_pct = pct
                        mb_done = downloaded / (1024 * 1024)
                        mb_tot = total / (1024 * 1024) if total > 0 else 0
                        msg = (
                            f"Downloading update... {mb_done:.1f} / {mb_tot:.1f} MB ({pct}%)"
                            if total > 0
                            else f"Downloading update... {mb_done:.1f} MB"
                        )
                        self._on_engine_event("update_progress", {
                            "stage": "downloading",
                            "percent": pct,
                            "downloaded": downloaded,
                            "total": total,
                            "message": msg,
                        })
                    elif stage == "extracting":
                        self._on_engine_event("update_progress", {
                            "stage": "extracting",
                            "percent": 100,
                            "message": "Verifying and extracting update package...",
                        })

                staged = download_and_stage_update(
                    target_url,
                    progress_cb=_on_progress,
                    expected_sha256=target_sha256,
                )
                if not staged.get("success"):
                    self._on_engine_event("update_progress", {
                        "stage": "error",
                        "message": staged.get("error") or "Update staging failed.",
                    })
                    return

                self._on_engine_event("update_progress", {
                    "stage": "applying",
                    "percent": 100,
                    "message": "Applying update and restarting Aether Desktop...",
                })

                launched = apply_staged_update_and_relaunch(
                    payload_dir=staged["payload_dir"],
                    stage_dir=staged["stage_dir"],
                    exe_name=staged.get("exe_name", "AetherDesktop.exe"),
                    app_dir=staged.get("app_dir"),
                )
                if not launched.get("success"):
                    self._on_engine_event("update_progress", {
                        "stage": "error",
                        "message": launched.get("error") or "Failed to apply update.",
                    })
                    return

                # Brief pause so UI renders the 'restarting' state before closing windows
                time.sleep(0.45)
                if getattr(self, "_engine", None) and getattr(self._engine, "audio_ducking_service", None):
                    try:
                        self._engine.audio_ducking_service.restore_all()
                    except Exception:
                        pass
                if getattr(self, "_hud_window", None) and hasattr(self._hud_window, "close_all"):
                    self._hud_window.close_all()
                elif getattr(self, "_window", None):
                    try:
                        self._window.destroy()
                    except Exception:
                        os._exit(0)
                else:
                    os._exit(0)
            except Exception as exc:
                logger.error(f"[UPDATER] Background update worker error: {exc}", exc_info=True)
                self._on_engine_event("update_progress", {
                    "stage": "error",
                    "message": str(exc),
                })
            finally:
                self._update_in_progress = False

        threading.Thread(target=_worker, daemon=True).start()
        return {"success": True, "started": True}


GUIBridge = GuiBridge













