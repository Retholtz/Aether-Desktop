import asyncio
import json
import os
import sys
import sounddevice as sd
from typing import Optional

from core.audio_stream import get_available_audio_devices
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
        self._config = self._load_config()
        self._engine = AetherEngine(
            config_getter=self.get_raw_config,
            on_event=self._on_engine_event,
            on_whitelist_update=self.update_whitelist
        )
        register_ui_log_callback(self._on_log_record)

    def set_window(self, window):
        self._window = window

    def set_overlay_window(self, window):
        self._overlay_window = window

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
        if os.path.exists(self._config_path):
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[CONFIG LOAD ERROR] {e}")
        return {}

    def get_raw_config(self) -> dict:
        return self._config

    def _on_engine_event(self, event_type: str, data: dict):
        """Pushes events to both the Main Window and Floating Overlay JavaScript runtimes."""
        try:
            payload = json.dumps({"type": event_type, "data": data})
            
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
            print(f"[BRIDGE DISPATCH ERROR] {err}")

    # =========================================================================
    # Methods exposed to JavaScript (via window.pywebview.api)
    # =========================================================================

    def get_config(self) -> dict:
        """Returns application configuration for the UI (masking encrypted API key)."""
        cfg = json.loads(json.dumps(self._config))
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

    def save_config(self, new_config: dict) -> dict:
        """Encrypts new API key if provided and saves updated configuration."""
        try:
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
            if "system_instruction" in api_cfg:
                self._config.setdefault("api", {})["system_instruction"] = api_cfg["system_instruction"]

            if "audio" in new_config:
                self._config["audio"] = new_config["audio"]
                if self._engine and self._engine.audio:
                    self._engine.audio.set_mode(new_config["audio"].get("mode", "always_on"))
                    self._engine.audio.set_software_gate(new_config["audio"].get("software_gate", False))

            if "vision" in new_config:
                self._config["vision"] = new_config["vision"]
            if "security" in new_config:
                self._config["security"] = new_config["security"]
            if "ui" in new_config:
                self._config["ui"] = new_config["ui"]

            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2)

            return {"success": True, "message": "Settings saved successfully."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_audio_devices(self) -> dict:
        """Enumerates truly active, available microphones and speakers on the system."""
        return get_available_audio_devices()

    def get_available_voices(self) -> list:
        """Returns list of the 30 Google Gemini official TTS voices."""
        return GEMINI_VOICES

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
            return {"success": True, "status": "stopped"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def kill_audio(self) -> dict:
        """Safe-word / manual trigger: immediately silences audio playback."""
        try:
            self._engine.kill_audio()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def set_ptt(self, active: bool) -> dict:
        """Sets the Push-To-Talk state (active = True/False)."""
        self._engine.set_ptt(active)
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
    # Window & Floating Overlay Controls
    # =========================================================================

    def restore_main_window(self) -> dict:
        """Restores the main window from minimized or hidden state and focuses it."""
        try:
            if self._window:
                self._window.restore()
                self._window.show()
                cfg = self._config.get("ui", {})
                mode = cfg.get("floating_overlay", "on_minimize")
                if mode == "on_minimize" and getattr(self, "_overlay_window", None):
                    self._overlay_window.hide()
                    self._overlay_visible = False
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def show_overlay(self) -> dict:
        """Explicitly displays the floating overlay window."""
        try:
            if getattr(self, "_overlay_window", None):
                self._overlay_window.show()
                self._overlay_visible = True
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
            return {"success": True, "visible": getattr(self, "_overlay_visible", False)}
        except Exception as e:
            return {"success": False, "error": str(e)}

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



