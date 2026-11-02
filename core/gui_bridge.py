import asyncio

import json

import os

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

    def __init__(self, config_path: Any = "config.json", loop: Optional[asyncio.AbstractEventLoop] = None, engine: Any = None):

        if not isinstance(config_path, str):

            engine = config_path

            config_path = "config.json"



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

        self._current_hud_mode = "normal"

        self._js_lock = threading.Lock()

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



            # Health Watchdog: Revive audio engine streams if invalidated by OS

            if os.environ.get("AETHER_TESTING") != "1":

                try:

                    eng = getattr(self, "_engine", None)

                    aud = getattr(eng, "audio", None) if eng else None

                    if aud and getattr(aud, "_running", False):

                        out_s = getattr(aud, "out_stream", None)

                        if out_s is not None and not getattr(out_s, "active", False):

                            if hasattr(aud, "ensure_output_stream_healthy"):

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

        if hasattr(win, "events") and hasattr(win.events, "loaded"):

            return win.events.loaded.is_set()

        return True



    def set_hud_bridge(self, hud_bridge):

        self._hud_bridge = hud_bridge



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



    def _on_engine_event(self, event_type: str, data: dict):

        """Pushes events to both the Main Window and Floating Overlay JavaScript runtimes."""

        try:

            if event_type == "hud_cycle_mode":

                self.cycle_hud_mode()

                return



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

            if event_type == "config_updated" and isinstance(data, dict):

                from core.config_manager import mask_sensitive_config

                data = mask_sensitive_config(data)



            payload = json.dumps({"type": event_type, "data": data})

            def _dispatch():
                with self._js_lock:
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

            # Execute dispatch asynchronously in a daemon worker thread to prevent
            # deadlocking with PyWebView synchronous bridge call message pumping on Windows
            threading.Thread(target=_dispatch, daemon=True).start()

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

            for top_key in ("agent_name", "pipeline_mode", "wake_phrase", "kill_phrase", "tts_endpoint", "tts_voice", "tts_speed", "voice_accent", "wake_word_enabled", "idle_timeout_seconds", "boot_on_startup", "start_minimized", "primary_model_endpoint", "tier1_fast_model", "tier2_heavy_model", "tier2_thinking_level", "tier2_thinking_budget", "stt_model_endpoint", "tts_model_endpoint", "stt_endpoint", "always_on_mode", "mode", "ptt_type", "ptt_key", "ptt_key_display", "ptt_vk", "ptt_modifiers", "hud_mode"):

                if top_key in new_config:

                    self._config[top_key] = new_config[top_key]



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



            api_cfg = new_config.get("api", {})

            raw_key_input = api_cfg.get("new_api_key", "").strip()

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

            if "pipeline_mode" in api_cfg:

                self._config.setdefault("api", {})["pipeline_mode"] = api_cfg["pipeline_mode"]

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

            if "local_tts_url" in api_cfg:

                self._config.setdefault("api", {})["local_tts_url"] = api_cfg["local_tts_url"]

            if "system_instruction" in api_cfg:

                self._config.setdefault("api", {})["system_instruction"] = api_cfg["system_instruction"]



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

                    if self._engine.audio:

                        self._engine.audio.set_mode(self._config["audio"].get("mode", "always_on"))

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

                                if wake_en and self._engine.audio.wake_detector.state.value == "IDLE_LISTENING":

                                    self._on_engine_event("status", {

                                        "state": "standby",

                                        "message": f"Standby — Say '{self._engine.audio.wake_detector.wake_phrase}' to wake {ag_name}."

                                    })

                                else:

                                    self._on_engine_event("status", {

                                        "state": "listening",

                                        "message": f"{ag_name} is listening..."

                                    })

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



            if "vision" in new_config:

                self._config["vision"] = new_config["vision"]

            if "security" in new_config:

                self._config["security"] = new_config["security"]

            if "ui" in new_config:

                self._config.setdefault("ui", {}).update(new_config["ui"])

                if "hud_mode" in new_config["ui"]:

                    target_mode = str(new_config["ui"]["hud_mode"]).lower()

                    if target_mode in ("mini", "normal", "max"):

                        self._current_hud_mode = target_mode

                        self._config["hud_mode"] = target_mode

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



            accent_changed = (old_accent != new_accent)

            voice_changed = (old_voice != new_voice)

            model_changed = (old_model != new_model or old_live_model != new_live_model)

            prompt_changed = (old_prompt != new_prompt or old_agent_name != new_agent_name)

            temp_changed = (old_temp != new_temp)



            live_config_changed = bool(accent_changed or voice_changed or model_changed or prompt_changed or temp_changed)



            sync_config_schema(self._config)

            save_config_atomic(self._config, self._config_path)



            engine_ref = getattr(self, "_engine", None) or getattr(self, "engine", None)

            if engine_ref:

                if hasattr(engine_ref, "update_voice_accent_directive"):

                    engine_ref.update_voice_accent_directive(new_accent)

                if live_config_changed:

                    changes = []

                    if accent_changed: changes.append(f"accent: {old_accent} -> {new_accent}")

                    if voice_changed: changes.append(f"voice: {old_voice} -> {new_voice}")

                    if model_changed: changes.append("model")

                    if prompt_changed: changes.append("prompt/name")

                    if temp_changed: changes.append("temp")

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

            from ui.hud_window import clamp_window_position

            cur_mode = getattr(self, "_current_hud_mode", "normal")

            w, h = (180, 52) if cur_mode == "mini" else ((560, 480) if cur_mode == "max" else (440, 180))

            cx, cy = clamp_window_position(None, None, width=w, height=h)

            if getattr(self, "_overlay_window", None):

                self._overlay_window.move(cx, cy)

                self.show_overlay()

            self.save_overlay_position(cx, cy)

            return {"success": True, "x": cx, "y": cy}

        except Exception as e:

            logger.warning(f"[RESET OVERLAY ERROR] {e}")

            return {"success": False, "error": str(e)}



    def show_overlay(self) -> dict:

        """Explicitly displays the floating overlay window."""

        try:

            if getattr(self, "_overlay_window", None):

                self._overlay_window.show()

                self._overlay_visible = True

                cur_mode = getattr(self, "_current_hud_mode", "normal")

                self.set_mode(cur_mode)

            return {"success": True}

        except Exception as e:

            return {"success": False, "error": str(e)}



    def hide_overlay(self) -> dict:

        """Hides the floating overlay window."""

        try:

            if getattr(self, "_overlay_window", None):

                self._overlay_window.hide()

                self._overlay_visible = False

            return {"success": True}

        except Exception as e:

            return {"success": False, "error": str(e)}



    def toggle_overlay(self) -> dict:

        """Toggles visibility of the floating overlay."""

        try:

            if getattr(self, "_overlay_window", None):

                if getattr(self, "_overlay_visible", False):

                    self._overlay_window.hide()

                    self._overlay_visible = False

                else:

                    self._overlay_window.show()

                    self._overlay_visible = True

                    try:

                        from ui.hud_window import apply_hud_window_shape

                        cur_mode = getattr(self, "_current_hud_mode", "normal")

                        apply_hud_window_shape(self._overlay_window, cur_mode)

                    except Exception:

                        pass

            return {"success": True, "visible": getattr(self, "_overlay_visible", False)}

        except Exception as e:

            return {"success": False, "error": str(e)}



    def save_overlay_position(self, x: int, y: int) -> dict:

        """Persists the floating overlay window coordinates to config."""

        try:

            from ui.hud_window import clamp_window_position

            cur_mode = getattr(self, "_current_hud_mode", "normal")

            w, h = (180, 52) if cur_mode == "mini" else ((560, 480) if cur_mode == "max" else (440, 180))

            cx, cy = clamp_window_position(x, y, width=w, height=h)

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



    def set_mode(self, mode: str) -> dict:

        """Sets the HUD overlay view mode (mini, normal, max) and resizes window."""

        mode = mode.lower() if mode else "normal"

        if mode not in ("mini", "normal", "max"):

            mode = "normal"

        self._current_hud_mode = mode

        with self._config_lock:

            self._config.setdefault("ui", {})["hud_mode"] = mode

            self._config["hud_mode"] = mode

            sync_config_schema(self._config)

            save_config_atomic(self._config, self._config_path)



        # Delegate resize to hud_bridge if registered, or direct to overlay_window

        if self._hud_bridge:

            try:

                self._hud_bridge.set_mode(mode)

            except Exception as ex:

                logger.warning(f"[HUD BRIDGE RESIZE ERROR] {ex}")

        elif getattr(self, "_overlay_window", None):

            try:

                if mode == "mini":

                    self._overlay_window.resize(210, 56)

                elif mode == "normal":

                    self._overlay_window.resize(440, 180)

                elif mode == "max":

                    self._overlay_window.resize(560, 480)

                from ui.hud_window import apply_hud_window_shape

                apply_hud_window_shape(self._overlay_window, mode)

            except Exception as ex:

                logger.warning(f"[OVERLAY RESIZE ERROR] {ex}")



        self.on_hud_mode_changed(mode)

        self._on_engine_event("hud_mode_changed", {"mode": mode})

        return {"success": True, "mode": mode}



    def cycle_hud_mode(self) -> str:

        """Cycles through HUD modes: mini -> normal -> max -> mini."""

        order = ["mini", "normal", "max"]

        cur = getattr(self, "_current_hud_mode", "normal")

        try:

            next_idx = (order.index(cur) + 1) % len(order)

            next_mode = order[next_idx]

        except ValueError:

            next_mode = "normal"



        if not getattr(self, "_overlay_visible", False):

            self.show_overlay()



        self.set_mode(next_mode)

        return next_mode



    def on_hud_mode_changed(self, mode: str):

        """Notifies JavaScript in the floating overlay window of mode change."""

        clean_mode = str(mode or "normal").lower().strip()

        if clean_mode not in ("mini", "normal", "max"):

            clean_mode = "normal"

        self._current_hud_mode = clean_mode

        if getattr(self, "_overlay_window", None):

            with self._js_lock:

                try:

                    js_code = f"if (window.aetherOverlay && window.aetherOverlay.setMode) {{ window.aetherOverlay.setMode({json.dumps(clean_mode)}); }}"

                    self._overlay_window.evaluate_js(js_code)

                except Exception:

                    pass



    def get_hud_mode(self) -> dict:

        """Returns the current HUD mode."""

        return {"success": True, "mode": getattr(self, "_current_hud_mode", "normal")}



    def toggle_mic_mute(self) -> dict:

        """Toggles microphone mute state."""

        try:

            if self._engine and self._engine.audio:

                new_mode = "ptt" if self._engine.audio.mode == "always_on" else "always_on"

                self._engine.audio.set_mode(new_mode)

                muted = (new_mode == "ptt")

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

            "user_override": self.game_mgr.user_override_game_mode

        }

    def get_hud_telemetry(self) -> dict:
        """Returns real-time HUD overlay state including running game status and live FPS."""
        active_id = self.game_mgr.data.get("active_profile", "")
        detected_id = getattr(self.game_mgr, "_last_detected_game", "UNSET")
        if detected_id == "UNSET":
            is_running = self.game_mgr.is_game_running(active_id) if active_id else False
            if not is_running and hasattr(self.game_mgr, "detect_running_game"):
                running_gid = self.game_mgr.detect_running_game()
                if running_gid:
                    active_id = running_gid
                    is_running = True
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

        """Scans local directories or retrieves web defaults if files are absent."""

        client = getattr(self.engine, "client", None) or getattr(self._engine, "client", None)

        model = "gemini-2.5-flash"

        if hasattr(self, "_config") and isinstance(self._config, dict):

            model = self._config.get("primary_model_endpoint", "gemini-2.5-flash")

        elif hasattr(self, "engine") and hasattr(self.engine, "config"):

            model = getattr(self.engine.config, "get", lambda k, d: d)("primary_model_endpoint", "gemini-2.5-flash")

        return self.game_mgr.sync_game_binds_with_fallback(game_id, client=client, model_endpoint=model)



    def test_game_macro(self, phrase: str) -> dict:

        """Tests execution of a keybind via DirectInput hardware scancode."""

        return self.game_mgr.trigger_action(phrase)



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

        self.game_mgr._save_profiles(self.game_mgr.data)

        return {"success": True, "keybinds": profile["keybinds"]}



    def delete_game_keybind(self, game_id: str, phrase: str) -> dict:

        """Removes a voice macro from a game profile."""

        profile = self.game_mgr.get_profile(game_id)

        if profile and phrase in profile.get("keybinds", {}):

            del profile["keybinds"][phrase]

            self.game_mgr._save_profiles(self.game_mgr.data)

            return {"success": True, "keybinds": profile["keybinds"]}

        return {"success": False, "error": "Binding not found"}



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

            games_dir = getattr(self.game_mgr, "games_dir", None) or os.path.join(getattr(self.game_mgr, "base_dir", "."), "data", "games")

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

            profiles_json = os.path.join(getattr(self.game_mgr, "base_dir", "."), "data", "games_profiles.json")

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



        try:

            for p in psutil.process_iter(['pid', 'name']):

                try:

                    pname = p.info.get('name') or ''

                    pid = p.info.get('pid')

                    if not pname or pname.lower() in system_procs:

                        continue



                    # If has a visible window, treat as primary application

                    if pid in window_titles:

                        w_title = window_titles[pid]

                        applications.append({

                            "pid": pid,

                            "name": pname,

                            "title": w_title

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

            "processes": user_processes

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
        vram_threshold_pct: int
    ) -> dict:
        """Persists and hot-updates CPU/GPU thermal and VRAM warning settings."""
        with self._config_lock:
            hw_cfg = self._config.setdefault("hardware_telemetry", {})
            hw_cfg["alerts_enabled"] = bool(alerts_enabled)
            hw_cfg["verbal_warnings"] = bool(alerts_enabled)
            hw_cfg["cpu_temp_alert_threshold_c"] = int(float(cpu_temp_threshold_c))
            hw_cfg["gpu_temp_alert_threshold_c"] = int(float(gpu_temp_threshold_c))
            hw_cfg["vram_alert_threshold_pct"] = int(float(vram_threshold_pct))
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
                                asyncio.run(self.notebooklm_service.batch_create_sources(nb_id, sources))
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
                nb_resp = asyncio.run(self.notebooklm_service.create_notebook(proj.get("name", project_id)))
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
                    cloud_synced = bool(asyncio.run(self.notebooklm_service.batch_create_sources(nb_id, sources)))
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
                asyncio.run(self.notebooklm_service.delete_notebook(nb_id))
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
                    asyncio.run(
                        self.notebooklm_service.batch_create_sources(
                            nb_id,
                            [{"displayName": saved_fname, "filePath": fpath}]
                        )
                    )
                except Exception as e:
                    logger.debug(f"[GUI_BRIDGE] Cloud file sync warning: {e}")
        return {"status": "success", "file": meta}

    def delete_project_file(self, project_id: str, filename: str) -> Dict[str, Any]:
        ok = self.project_service.delete_file(project_id, filename)
        return {"status": "success" if ok else "error", "deleted": ok}

    def open_project_file(self, project_id: str, filename: str) -> Dict[str, Any]:
        """Opens a local project document using the operating system's default viewer."""
        p_dir = self.project_service.get_project_dir(project_id)
        target = os.path.join(p_dir, filename)
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
        target = os.path.join(p_dir, filename)
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
        """Opens an external website URL in the user's default browser."""
        import webbrowser
        clean_url = (url or "").strip()
        if not clean_url:
            return {"status": "error", "message": "No URL provided."}
        if not clean_url.startswith(("http://", "https://")):
            clean_url = "https://" + clean_url
        try:
            if hasattr(os, "startfile"):
                os.startfile(clean_url)
            else:
                webbrowser.open(clean_url)
            logger.info(f"[GUI_BRIDGE] Opened external URL: {clean_url}")
            return {"status": "success", "url": clean_url}
        except Exception as e:
            try:
                webbrowser.open(clean_url)
                return {"status": "success", "url": clean_url}
            except Exception as e2:
                logger.error(f"[GUI_BRIDGE] Failed to open external URL '{clean_url}': {e2}")
                return {"status": "error", "message": str(e2)}

    def read_project_file(self, project_id: str, filename: str) -> Dict[str, Any]:
        p_dir = self.project_service.get_project_dir(project_id)
        target = os.path.join(p_dir, filename)
        if not os.path.exists(target):
            return {"status": "error", "message": "File not found"}
        try:
            content = self.project_service.extract_file_text(target)
            return {"status": "success", "filename": filename, "content": content}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def import_project_url(self, project_id: str, url: str) -> Dict[str, Any]:
        import asyncio
        res = asyncio.run(self.project_service.import_url(project_id, url))
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
                        asyncio.run(
                            self.notebooklm_service.batch_create_sources(
                                nb_id,
                                [{"displayName": saved_fname, "filePath": fpath}]
                            )
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
        return {
            "active_campaign": active_id,
            "campaigns": campaigns,
            "active_campaign_meta": campaigns.get(active_id) if active_id else None,
            "dice_mode": campaigns.get(active_id, {}).get("dice_mode", "digital") if active_id else "digital",
            "state": self.ttrpg_service.active_state,
            "is_adventure_active": is_adv,
            "projects": self.project_service.data.get("projects", {}) if hasattr(self, "project_service") and self.project_service else {}
        }

    def set_active_campaign(self, campaign_id: str) -> Dict[str, Any]:
        res = self.ttrpg_service.set_active_campaign(campaign_id)
        if res.get("status") == "success":
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
        initial_files: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        res = self.ttrpg_service.create_campaign(
            title=title,
            system_preset=system_preset,
            gm_mode=gm_mode,
            setting_overview=setting_overview,
            tone=tone,
            difficulty=difficulty,
            linked_project_id=linked_project_id,
            initial_files=initial_files
        )
        if res.get("status") == "success":
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
        linked_project_id: Optional[str] = None
    ) -> Dict[str, Any]:
        res = self.ttrpg_service.update_campaign(
            campaign_id=campaign_id,
            title=title,
            system_preset=system_preset,
            gm_mode=gm_mode,
            setting_overview=setting_overview,
            tone=tone,
            difficulty=difficulty,
            linked_project_id=linked_project_id
        )
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
        return res

    def delete_ttrpg_campaign(self, campaign_id: str) -> Dict[str, Any]:
        res = self.ttrpg_service.delete_campaign(campaign_id)
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
        return res

    def upload_ttrpg_document(
        self,
        filename: str,
        base64_content: str,
        doc_category: str = "ruleset",
        campaign_id: Optional[str] = None
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
            campaign_id=campaign_id
        )
        if res.get("status") == "success":
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
        return res

    def refresh_ttrpg_roster_and_files(self, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Scans the campaign files directory for newly added files, extracts and imports
        any missing character sheets into the Party Roster, and reloads campaign state.
        """
        res = self.ttrpg_service.refresh_campaign_files_and_characters(campaign_id=campaign_id)
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
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
                if eng and hasattr(eng, "session_lifecycle") and eng.session_lifecycle:
                    try:
                        raw_turns = eng.session_lifecycle.get_raw_turns()
                        if raw_turns:
                            self.ttrpg_service.auto_summarize_session_to_journal(raw_turns, session_id=eng.session_lifecycle.session_id)
                            if hasattr(self, "_on_engine_event"):
                                self._on_engine_event("ttrpg_state_updated", {"status": "success", "session_id": eng.session_lifecycle.session_id})
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
            f"First, call `read_campaign_document(filename='all')` to review our uploaded documents ({doc_names}). "
            f"If there are character sheets among the documents, add or update them on the Party Roster using `upsert_ttrpg_character`. "
            f"Then give us a brief summary of the ruleset, campaign premise, and party, and set the opening scene!"
        )
        if active_id:
            return self.send_workspace_message("campaign", active_id, prompt)
        return self.send_text_message(prompt)

    def start_or_resume_ttrpg_campaign(self) -> Dict[str, Any]:
        """
        Starts a new campaign with opening narration or resumes an existing campaign seamlessly
        from the persistent session recap and journal notes.
        Auto-starts Aether voice engine if not already running.
        """
        if not self._engine.is_running:
            self.start_assistant()
        self.set_ttrpg_session_active(True)
        active_id = self.ttrpg_service.manifest.get("active_campaign")
        camp = self.ttrpg_service.manifest.get("campaigns", {}).get(active_id, {}) if active_id else {}
        camp_title = camp.get("title", active_id or "the campaign")
        state = self.ttrpg_service.active_state or {}
        recap = state.get("latest_recap")
        loc = state.get("current_location")
        chars = state.get("characters", {})
        char_names = ", ".join(c.get("name", "hero") for c in chars.values()) if chars else "the party"
        difficulty = (camp.get("difficulty") or "helpful").lower()

        diff_note = ""
        if difficulty == "helpful":
            diff_note = " Remember we are playing in Helpful / Beginner mode: please suggest 2-3 specific actions we can take, tell us what dice to roll with our exact modifiers, and remind us of helpful abilities!"

        docs = state.get("documents", {})
        module_files = [f for f in docs.keys() if docs[f].get("category") in ("campaign", "module") or "elf" in f.lower() or "module" in f.lower()]
        doc_hint = ""
        if module_files:
            doc_hint = f" Consult the adventure module '{module_files[0]}' using `read_campaign_document` (e.g. Scene 1) to accurately set the opening scene, Johan Petry/NPC dialogue, and quest hook from the module."

        if recap or state.get("journal"):
            prompt = (
                f"Aether, please act as our Game Master and resume our campaign '{camp_title}' for {char_names}! "
                f"Provide a brief 2-sentence recap of where we left off (at {loc or 'our last location'}), "
                f"describe the immediate scene and what is happening right now, and ask {char_names} what we do next!{diff_note}"
            )
        else:
            prompt = (
                f"Aether, please act as our Game Master and begin our campaign '{camp_title}'! "
                f"Introduce the setting and our characters ({char_names}), and set the opening scene from the adventure module.{doc_hint}{diff_note} "
                f"What do we see and what are our immediate choices?"
            )
        if active_id:
            return self.send_workspace_message("campaign", active_id, prompt)
        return self.send_text_message(prompt)

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
        return res

    def roll_ttrpg_dice(self, formula: str, roll_type: str = "normal", purpose: Optional[str] = None) -> Dict[str, Any]:
        res = self.ttrpg_service.parse_and_roll(formula, roll_type)
        if res.get("status") == "success" and purpose:
            res["purpose"] = purpose
        if res.get("status") == "success":
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
        return res

    def add_ttrpg_initiative(self, name: str, roll_val: int, is_player: bool = True) -> Dict[str, Any]:
        res = self.ttrpg_service.add_to_initiative(name, int(roll_val), bool(is_player))
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
        return res

    def remove_ttrpg_initiative(self, name_or_id: str) -> Dict[str, Any]:
        res = self.ttrpg_service.remove_from_initiative(name_or_id)
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
        return res

    def add_ttrpg_character(self, char_payload: Dict[str, Any]) -> Dict[str, Any]:
        res = self.ttrpg_service.add_character(char_payload)
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
        return res

    def delete_ttrpg_character(self, character_id_or_name: str) -> Dict[str, Any]:
        res = self.ttrpg_service.delete_character(character_id_or_name)
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
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
        return res

    def update_ttrpg_condition(self, character_name: str, condition: str, active: bool) -> Dict[str, Any]:
        res = self.ttrpg_service.update_condition(character_name, condition, bool(active))
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
        return res

    def manage_ttrpg_character_inventory(
        self,
        character_name: str,
        action: str = "add",
        items: Optional[List[str]] = None,
        weapons: Optional[List[Dict[str, str]]] = None,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """Adds or removes weapons and items for a character in the active campaign."""
        res = self.ttrpg_service.manage_character_inventory(
            character_name=character_name,
            action=action,
            items=items,
            weapons=weapons,
            reason=reason
        )
        if res.get("status") == "success":
            self._on_engine_event("ttrpg_state_updated", res)
        return res

    def reformat_ttrpg_weapons_or_items(self, raw_text: str, ruleset: str = "dnd_5e") -> Dict[str, Any]:
        """Parses and reformats raw unformatted weapon or item text into structured character sheet records."""
        return self.ttrpg_service.reformat_weapons_or_items(raw_text=raw_text, ruleset=ruleset)

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

    def save_campaign_character_to_vault(
        self,
        character_name_or_id: str,
        campaign_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Exports a character from the active or specified campaign party roster
        into the Character Vault as a permanent Canonical Root hero.
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

        res = svc.import_from_campaign(target_char, campaign_meta=camp)
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

        # Add character to campaign party roster
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
        if getattr(eng, "active_workspace_type", "") != workspace_type or getattr(eng, "active_workspace_context_id", "") != context_id:
            eng.set_active_workspace(workspace_type, context_id)

        try:
            if not eng.is_running:
                logger.info(f"[GUI_BRIDGE] Auto-starting engine from workspace message ({workspace_type}:{context_id})")
                self.start_assistant()
            asyncio.run_coroutine_threadsafe(eng.send_text(clean_text), self._loop)
            return {"success": True, "session_id": sid}
        except Exception as e:
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

GUIBridge = GuiBridge













