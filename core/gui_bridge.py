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
                aud_cfg = new_config["audio"]
                if "voice_biometrics" in aud_cfg:
                    self._config.setdefault("audio", {}).setdefault("voice_biometrics", {}).update(aud_cfg["voice_biometrics"])
                    aud_copy = dict(aud_cfg)
                    del aud_copy["voice_biometrics"]
                    self._config["audio"].update(aud_copy)
                else:
                    self._config.setdefault("audio", {}).update(aud_cfg)
                if self._engine and self._engine.audio:
                    self._engine.audio.set_mode(self._config["audio"].get("mode", "always_on"))
                    self._engine.audio.set_software_gate(self._config["audio"].get("software_gate", False))

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
        """Sets the user's preferred name in memory and configuration."""
        try:
            clean = str(name).strip()
            self._engine.user_memory.set_user_name(clean)
            self._config.setdefault("user", {})["name"] = clean
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2)
            self._on_engine_event("config_updated", self._config)
            return {"success": True, "name": clean}
        except Exception as e:
            logger.error(f"[BRIDGE ERROR] set_user_name: {e}")
            return {"success": False, "error": str(e)}




