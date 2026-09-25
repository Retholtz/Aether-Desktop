import asyncio
import json
import os
import sys
import threading
import time
import sounddevice as sd
from typing import Optional

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
from core.security import protect_secret, unprotect_secret

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
    def __init__(self, config_path: str = "config.json", loop: Optional[asyncio.AbstractEventLoop] = None):
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
        self._config = self._load_config()
        self._current_hud_mode = self._config.get("ui", {}).get("hud_mode", "normal")
        self._engine = AetherEngine(
            config_getter=self.get_raw_config,
            on_event=self._on_engine_event,
            on_whitelist_update=self.update_whitelist
        )
        self.engine = self._engine
        register_ui_log_callback(self._on_log_record)
        self._last_audio_fp = get_windows_audio_fingerprint()
        self._start_audio_device_watcher()

    def _start_audio_device_watcher(self):
        """Monitors Windows System\\Sound endpoints for hotplugged earbuds/headsets and updates live streams & UI."""
        def _watch_loop():
            while True:
                try:
                    time.sleep(1.5)
                    fp = get_windows_audio_fingerprint()
                    if fp is None or fp == self._last_audio_fp:
                        continue

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
                        with open(self._config_path, "w", encoding="utf-8") as f:
                            json.dump(self._config, f, indent=2)
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

        t = threading.Thread(target=_watch_loop, name="AudioHardwareWatcher", daemon=True)
        t.start()

    @property
    def config(self) -> dict:
        return self._config

    def set_window(self, window):
        self._window = window

    def set_overlay_window(self, window):
        self._overlay_window = window

    def set_hud_bridge(self, hud_bridge):
        self._hud_bridge = hud_bridge

    def update_whitelist(self, new_whitelist: list) -> dict:
        """Updates security app_whitelist in config, persists to disk, and pushes config_updated event."""
        try:
            self._config.setdefault("security", {})["app_whitelist"] = new_whitelist
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2)
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
        if "vad_trailing_silence_ms" not in cfg:
            cfg["vad_trailing_silence_ms"] = cfg.get("audio", {}).get("vad_trailing_silence_ms", 1400)
        return cfg

    def get_raw_config(self) -> dict:
        return self._config

    def _on_engine_event(self, event_type: str, data: dict):
        """Pushes events to both the Main Window and Floating Overlay JavaScript runtimes."""
        try:
            if event_type == "hud_cycle_mode":
                self.cycle_hud_mode()
                return

            payload = json.dumps({"type": event_type, "data": data})
            
            with self._js_lock:
                # Dispatch to Main Window
                if self._window:
                    try:
                        js_code = f"if (window.aetherUI && window.aetherUI.handleEvent) {{ window.aetherUI.handleEvent({payload}); }}"
                        self._window.evaluate_js(js_code)
                    except Exception:
                        pass

                # Dispatch to Floating Overlay Window
                if self._overlay_window:
                    try:
                        overlay_js = f"if (window.aetherOverlay && window.aetherOverlay.handleEvent) {{ window.aetherOverlay.handleEvent({payload}); }}"
                        self._overlay_window.evaluate_js(overlay_js)
                    except Exception:
                        pass
        except Exception as err:
            logger.warning(f"[BRIDGE DISPATCH ERROR] {err}")

    # =========================================================================
    # Methods exposed to JavaScript (via window.pywebview.api)
    # =========================================================================

    def get_config(self) -> dict:
        """Returns application configuration for the UI (masking encrypted API key)."""
        cfg = json.loads(json.dumps(self._config))
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
        try:
            silence_ms = int(silence_ms)
            self._config["vad_trailing_silence_ms"] = silence_ms
            if "audio" in self._config and isinstance(self._config["audio"], dict):
                self._config["audio"]["vad_trailing_silence_ms"] = silence_ms

            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2)

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
        try:
            if new_config is None:
                with open(self._config_path, "w", encoding="utf-8") as f:
                    json.dump(self._config, f, indent=2)
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

            api_cfg = new_config.get("api", {})
            raw_key_input = api_cfg.get("new_api_key", "").strip()
            if raw_key_input and not raw_key_input.startswith("*") and not raw_key_input.startswith("•"):
                encrypted_key = protect_secret(raw_key_input)
                self._config.setdefault("api", {})["api_key_encrypted"] = encrypted_key
            
            if "agent_name" in api_cfg:
                self._config.setdefault("api", {})["agent_name"] = api_cfg["agent_name"].strip() or "Aether"
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
            if "pro_model_id" in api_cfg:
                self._config.setdefault("api", {})["pro_model_id"] = api_cfg["pro_model_id"]
            if "temperature" in api_cfg:
                self._config.setdefault("api", {})["temperature"] = float(api_cfg["temperature"])
            if "voice_name" in api_cfg:
                self._config.setdefault("api", {})["voice_name"] = api_cfg["voice_name"]
            if "voice_accent" in api_cfg:
                self._config.setdefault("api", {})["voice_accent"] = api_cfg["voice_accent"]
            if "voice_speed" in api_cfg:
                self._config.setdefault("api", {})["voice_speed"] = float(api_cfg["voice_speed"])
            if "local_tts_url" in api_cfg:
                self._config.setdefault("api", {})["local_tts_url"] = api_cfg["local_tts_url"]
            if "system_instruction" in api_cfg:
                self._config.setdefault("api", {})["system_instruction"] = api_cfg["system_instruction"]

            if "audio" in new_config:
                aud_cfg = new_config["audio"]
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
                if hasattr(self._engine, "hotkey_manager") and self._engine.hotkey_manager:
                    self._engine.hotkey_manager.update_config(self._config.get("audio", {}), self._config.get("ui", {}))
                if "hud_mode" in new_config["ui"]:
                    self.set_mode(new_config["ui"]["hud_mode"])

            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2)

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
            self._engine.stop()
            agent_name = self._config.get("api", {}).get("agent_name", "Aether")
            self._on_engine_event("status", {"state": "standby", "message": f"{agent_name} on standby."})
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
        """Sets the Push-To-Talk state (active = True/False)."""
        self._engine.set_ptt(active)
        return {"success": True}

    def toggle_ptt(self) -> dict:
        """Toggles active Push-to-Talk state."""
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
            asyncio.run_coroutine_threadsafe(self._engine.send_text(text), self._loop)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

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
                self._config.setdefault("audio", {}).setdefault("voice_biometrics", {})["enabled"] = enabled
                self._config["audio"]["voice_biometrics"]["threshold"] = float(threshold)
                with open(self._config_path, "w", encoding="utf-8") as f:
                    json.dump(self._config, f, indent=2)
                self._on_engine_event("voice_profile_updated", {"enrolled": True, "enabled": enabled, "threshold": threshold})
            return res
        except Exception as e:
            logger.error(f"[VOICE CALIBRATION FINALIZE ERROR] {e}")
            return {"success": False, "error": str(e)}

    def save_voice_biometrics_settings(self, enabled: bool, threshold: float) -> dict:
        """Updates biometric enabled toggle and sensitivity threshold."""
        try:
            self._config.setdefault("audio", {}).setdefault("voice_biometrics", {})["enabled"] = bool(enabled)
            self._config["audio"]["voice_biometrics"]["threshold"] = float(threshold)
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2)
            self._on_engine_event("voice_profile_updated", {"enrolled": self._engine.voice_verifier.is_enrolled(), "enabled": enabled, "threshold": threshold})
            return {"success": True, "enabled": enabled, "threshold": threshold}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_voice_profile(self) -> dict:
        """Removes enrolled voiceprint profile."""
        try:
            res = self._engine.voice_verifier.delete_profile()
            self._config.setdefault("audio", {}).setdefault("voice_biometrics", {})["enabled"] = False
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2)
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
            ui_cfg = self._config.setdefault("ui", {})
            if ui_cfg.get("overlay_x") == cx and ui_cfg.get("overlay_y") == cy:
                return {"success": True, "x": cx, "y": cy}
            ui_cfg["overlay_x"] = cx
            ui_cfg["overlay_y"] = cy
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2)
            return {"success": True, "x": cx, "y": cy}
        except Exception as e:
            logger.warning(f"[OVERLAY POSITION SAVE ERROR] {e}")
            return {"success": False, "error": str(e)}

    def move_overlay(self, x: int, y: int) -> dict:
        """Moves the floating HUD overlay window to screen coordinates (x, y)."""
        try:
            if getattr(self, "_overlay_window", None):
                self._overlay_window.move(int(x), int(y))
                self.save_overlay_position(x, y)
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
        self._config.setdefault("ui", {})["hud_mode"] = mode

        # Delegate resize to hud_bridge if registered, or direct to overlay_window
        if self._hud_bridge:
            try:
                self._hud_bridge.set_mode(mode)
            except Exception as ex:
                logger.warning(f"[HUD BRIDGE RESIZE ERROR] {ex}")
        elif getattr(self, "_overlay_window", None):
            try:
                if mode == "mini":
                    self._overlay_window.resize(180, 52)
                elif mode == "normal":
                    self._overlay_window.resize(440, 180)
                elif mode == "max":
                    self._overlay_window.resize(560, 480)
                from ui.hud_window import apply_hud_window_shape
                apply_hud_window_shape(self._overlay_window, mode)
            except Exception as ex:
                logger.warning(f"[OVERLAY RESIZE ERROR] {ex}")
            self.on_hud_mode_changed(mode)

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
        self._current_hud_mode = mode
        if getattr(self, "_overlay_window", None) and getattr(self, "_overlay_visible", False):
            try:
                js_code = f"if (window.aetherOverlay && window.aetherOverlay.setMode) {{ window.aetherOverlay.setMode('{mode}'); }}"
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
            self._config.setdefault("user", {})["name"] = clean
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2)
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
            self._config.setdefault("user", {})["callsign_frequency"] = clean
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2)
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






