"""
core/replay_service.py - Multi-Backend Gameplay Clip & Replay Trigger
Supports NVIDIA ShadowPlay, Xbox Game Bar, and OBS Studio WebSocket v5.
"""
import ctypes
import time
from typing import Dict, Any, Optional
import aiohttp
from core.logger import get_logger

logger = get_logger("ReplayService")

# Win32 Virtual Key Constants
VK_MENU = 0x12       # Alt key
VK_F10 = 0x79        # F10 key
VK_LWIN = 0x5B       # Left Windows key
VK_G = 0x47          # G key
KEYEVENTF_KEYUP = 0x0002

try:
    user32 = ctypes.windll.user32
except (AttributeError, Exception):
    user32 = None


class ReplayService:
    def __init__(
        self,
        backend: str = "shadowplay",
        obs_host: str = "localhost",
        obs_port: int = 4455,
        obs_password: str = "",
        spoken_confirmation: str = "Captured.",
        enabled: bool = True
    ):
        self.enabled = bool(enabled)
        self.backend = (backend or "shadowplay").lower().strip()
        self.obs_host = obs_host or "localhost"
        self.obs_port = int(obs_port or 4455)
        self.obs_password = obs_password or ""
        self.spoken_confirmation = spoken_confirmation or "Captured."

    # --- Hardware Scancode Emulation ---
    def _pulse_shadowplay(self) -> bool:
        """Sends Alt + F10 keystroke for GeForce Experience."""
        try:
            target_user32 = getattr(ctypes, "windll", None)
            target_user32 = target_user32.user32 if target_user32 else user32
            if not target_user32:
                logger.error("[REPLAY] user32 is unavailable on this platform.")
                return False

            # Press Alt, press F10, release F10, release Alt
            target_user32.keybd_event(VK_MENU, 0, 0, 0)
            time.sleep(0.05)
            target_user32.keybd_event(VK_F10, 0, 0, 0)
            time.sleep(0.08)
            target_user32.keybd_event(VK_F10, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.02)
            target_user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
            logger.info("[REPLAY] Sent Alt + F10 (ShadowPlay) shortcut.")
            return True
        except Exception as e:
            logger.error(f"[REPLAY] Failed to send ShadowPlay key combination: {e}")
            return False

    def _pulse_game_bar(self) -> bool:
        """Sends Win + Alt + G keystroke for Windows Game Bar."""
        try:
            target_user32 = getattr(ctypes, "windll", None)
            target_user32 = target_user32.user32 if target_user32 else user32
            if not target_user32:
                logger.error("[REPLAY] user32 is unavailable on this platform.")
                return False

            # Press Win + Alt + G, then release in reverse order
            target_user32.keybd_event(VK_LWIN, 0, 0, 0)
            time.sleep(0.03)
            target_user32.keybd_event(VK_MENU, 0, 0, 0)
            time.sleep(0.05)
            target_user32.keybd_event(VK_G, 0, 0, 0)
            time.sleep(0.08)
            target_user32.keybd_event(VK_G, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.02)
            target_user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
            target_user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
            logger.info("[REPLAY] Sent Win + Alt + G (Xbox Game Bar) shortcut.")
            return True
        except Exception as e:
            logger.error(f"[REPLAY] Failed to send Game Bar key combination: {e}")
            return False

    # --- OBS Studio WebSocket v5 ---
    async def _trigger_obs_replay(self) -> bool:
        """Connects to OBS WebSocket v5 and triggers SaveReplayBuffer."""
        url = f"ws://{self.obs_host}:{self.obs_port}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, timeout=2.0) as ws:
                    # OBS WebSocket v5 handshake: wait for 'Hello' (OpCode 0)
                    hello_msg = await ws.receive_json(timeout=2.0)
                    if hello_msg.get("op") != 0:
                        logger.error(f"[REPLAY] Unexpected OBS Hello response: {hello_msg}")
                        return False

                    # Identify (OpCode 1) - authentication omitted if password empty
                    identify_payload = {
                        "op": 1,
                        "d": {
                            "rpcVersion": 1
                        }
                    }
                    if self.obs_password and "authentication" in hello_msg.get("d", {}):
                        import base64
                        import hashlib
                        auth_info = hello_msg["d"]["authentication"]
                        salt = auth_info.get("salt", "")
                        challenge = auth_info.get("challenge", "")
                        secret = base64.b64encode(hashlib.sha256((self.obs_password + salt).encode('utf-8')).digest())
                        auth_resp = base64.b64encode(hashlib.sha256(secret + challenge.encode('utf-8')).digest()).decode('utf-8')
                        identify_payload["d"]["authentication"] = auth_resp

                    await ws.send_json(identify_payload)

                    # Wait for 'Identified' (OpCode 2)
                    identified_msg = await ws.receive_json(timeout=2.0)
                    if identified_msg.get("op") != 2:
                        logger.error(f"[REPLAY] OBS Identify failed: {identified_msg}")
                        return False

                    # Request SaveReplayBuffer (OpCode 6)
                    request_payload = {
                        "op": 6,
                        "d": {
                            "requestType": "SaveReplayBuffer",
                            "requestId": "aether_replay_clip"
                        }
                    }
                    await ws.send_json(request_payload)
                    response = await ws.receive_json(timeout=2.0)
                    
                    status = response.get("d", {}).get("requestStatus", {}).get("result", False)
                    logger.info(f"[REPLAY] OBS SaveReplayBuffer result: {status}")
                    return bool(status)
        except Exception as e:
            logger.error(f"[REPLAY] OBS WebSocket connection error: {e}")
            return False

    # --- Unified Trigger Execution ---
    async def trigger_clip(self) -> Dict[str, Any]:
        """Dispatches the clip command based on configured backend."""
        success = False
        backend_used = self.backend

        if self.backend == "shadowplay":
            import asyncio
            success = await asyncio.to_thread(self._pulse_shadowplay)
        elif self.backend == "xbox_game_bar":
            import asyncio
            success = await asyncio.to_thread(self._pulse_game_bar)
        elif self.backend == "obs_websocket":
            success = await self._trigger_obs_replay()
        else:
            logger.warning(f"[REPLAY] Unknown backend '{self.backend}'. Defaulting to ShadowPlay.")
            import asyncio
            success = await asyncio.to_thread(self._pulse_shadowplay)
            backend_used = "shadowplay"

        return {
            "status": "success" if success else "error",
            "backend": backend_used,
            "message": "Replay clip saved." if success else "Failed to trigger replay clip.",
            "spoken_confirmation": self.spoken_confirmation if success else "Replay capture failed."
        }

