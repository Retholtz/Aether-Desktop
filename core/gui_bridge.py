import asyncio
import json
import os
import sys
import sounddevice as sd
from typing import Optional

from core.engine import AetherEngine, GEMINI_VOICES
from core.security import protect_secret, unprotect_secret

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
        self._config = self._load_config()
        self._engine = AetherEngine(
            config_getter=self.get_raw_config,
            on_event=self._on_engine_event
        )

    def set_window(self, window):
        self._window = window

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
        """Pushes events to the WebView frontend JavaScript runtime."""
        if not self._window:
            return
        try:
            payload = json.dumps({"type": event_type, "data": data})
            js_code = f"if (window.aetherUI && window.aetherUI.handleEvent) {{ window.aetherUI.handleEvent({payload}); }}"
            self._window.evaluate_js(js_code)
        except Exception:
            pass

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

            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2)

            return {"success": True, "message": "Settings saved successfully."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_audio_devices(self) -> dict:
        """Enumerates active microphones and speakers on the Windows system."""
        try:
            devices = sd.query_devices()
            hostapis = sd.query_hostapis()
            
            inputs = []
            outputs = []

            for idx, dev in enumerate(devices):
                api_name = hostapis[dev['hostapi']]['name']
                label = f"{dev['name']} ({api_name})"
                
                if dev['max_input_channels'] > 0:
                    inputs.append({
                        "index": idx,
                        "name": dev['name'],
                        "api": api_name,
                        "label": label,
                        "channels": dev['max_input_channels'],
                        "samplerate": int(dev['default_samplerate'])
                    })
                
                if dev['max_output_channels'] > 0:
                    outputs.append({
                        "index": idx,
                        "name": dev['name'],
                        "api": api_name,
                        "label": label,
                        "channels": dev['max_output_channels'],
                        "samplerate": int(dev['default_samplerate'])
                    })

            return {"inputs": inputs, "outputs": outputs}
        except Exception as e:
            return {"inputs": [], "outputs": [], "error": str(e)}

    def get_available_voices(self) -> list:
        """Returns list of the 30 Google Gemini official TTS voices."""
        return GEMINI_VOICES

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

