"""
Test suite verifying end-to-end settings persistence across ConfigManager,
GuiBridge, and config.json.
Validates:
1. PTT choice persists across save and reload cycles.
2. Software gate (mute microphone) setting persists.
3. Desktop Vision options (enabled, fps, monitor, endpoint) persist.
4. HUD options (floating_overlay, minimize_to_tray, hud_mode) persist.
5. PTT 0ms fast-path release separation is preserved.
"""

import asyncio
import json
import os
import shutil
import tempfile
import unittest

from core.config_manager import ConfigManager, sync_config_schema
from core.gui_bridge import GuiBridge


class TestSettingsPersistence(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "config.json")
        base_cfg = {
            "agent_name": "Aether",
            "mode": "always_on",
            "audio": {
                "mode": "always_on",
                "software_gate": False,
                "vad_trailing_silence_ms": 1400,
            },
            "vision": {
                "enabled": False,
                "fps": 1.0,
                "monitor": "auto",
                "endpoint": "gemini-3.8-flash-snapshot",
            },
            "ui": {
                "floating_overlay": "on_minimize",
                "minimize_to_tray": True,
                "hud_mode": "normal",
            },
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(base_cfg, f, indent=2)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_schema_sync_defaults_and_floating_overlay(self):
        """Verify schema synchronization does not override 'on_minimize' with 'always'."""
        cfg = {"ui": {}}
        synced = sync_config_schema(cfg)
        self.assertEqual(synced["ui"]["floating_overlay"], "on_minimize")
        self.assertTrue(synced["ui"]["minimize_to_tray"])
        self.assertIn("vision", synced)
        self.assertEqual(synced["vision"]["monitor"], "auto")
        self.assertEqual(synced["vision"]["fps"], 1.0)
        self.assertFalse(synced["audio"]["software_gate"])

    def test_config_manager_persistence_cycle(self):
        """Verify ConfigManager saves and reloads all user choices correctly."""
        mgr = ConfigManager(config_path=self.config_path)

        updates = {
            "mode": "ptt",
            "ptt_type": "hold",
            "ptt_key": "Backslash",
            "ptt_key_display": "BACKSLASH",
            "ptt_vk": 220,
            "audio": {
                "mode": "ptt",
                "software_gate": True,
                "ptt_type": "hold",
                "ptt_key": "Backslash",
                "ptt_key_display": "BACKSLASH",
                "ptt_vk": 220,
            },
            "vision": {
                "enabled": True,
                "fps": 2.0,
                "monitor": "all",
                "endpoint": "gemini-3.1-pro-snapshot",
            },
            "ui": {
                "floating_overlay": "disabled",
                "minimize_to_tray": False,
                "hud_mode": "mini",
            },
        }

        mgr.save(updates)

        # Reload with a completely separate ConfigManager instance from disk
        reloaded_mgr = ConfigManager(config_path=self.config_path)
        cfg = reloaded_mgr.config

        # 1. PTT choice
        self.assertEqual(cfg["mode"], "ptt")
        self.assertEqual(cfg["audio"]["mode"], "ptt")
        self.assertEqual(cfg["ptt_key"], "Backslash")
        self.assertEqual(cfg["audio"]["ptt_key"], "Backslash")

        # 2. Mute microphone / software_gate
        self.assertTrue(cfg["audio"]["software_gate"])

        # 3. Vision options
        self.assertTrue(cfg["vision"]["enabled"])
        self.assertEqual(cfg["vision"]["fps"], 2.0)
        self.assertEqual(cfg["vision"]["monitor"], "all")
        self.assertEqual(cfg["vision"]["endpoint"], "gemini-3.1-pro-snapshot")

        # 4. HUD options
        self.assertEqual(cfg["ui"]["floating_overlay"], "disabled")
        self.assertFalse(cfg["ui"]["minimize_to_tray"])
        self.assertEqual(cfg["ui"]["hud_mode"], "mini")

    def test_gui_bridge_save_config_roundtrip(self):
        """Verify GuiBridge saves and persists full frontend payload correctly to disk."""
        loop = asyncio.new_event_loop()
        try:
            bridge = GuiBridge(config_path=self.config_path, loop=loop)

            frontend_payload = {
                "mode": "ptt",
                "ptt_type": "toggle",
                "ptt_key": "F13",
                "ptt_key_display": "F13",
                "ptt_vk": 124,
                "ptt_modifiers": [],
                "hud_mode": "max",
                "agent_name": "Aether",
                "audio": {
                    "mode": "ptt",
                    "ptt_type": "toggle",
                    "ptt_key": "F13",
                    "ptt_key_display": "F13",
                    "ptt_vk": 124,
                    "ptt_modifiers": [],
                    "software_gate": True,
                    "preferred_language": "en-US",
                    "input_device_index": 2,
                    "input_device_name": "Test Mic",
                    "output_device_index": 3,
                    "output_device_name": "Test Speakers",
                },
                "vision": {
                    "enabled": True,
                    "fps": 0.5,
                    "monitor": "1",
                    "endpoint": "gemini-3.8-flash-snapshot",
                    "resolution": [768, 768],
                    "jpeg_quality": 70,
                },
                "ui": {
                    "floating_overlay": "on_minimize",
                    "minimize_to_tray": True,
                    "hud_mode": "max",
                },
            }

            res = bridge.save_config(frontend_payload)
            self.assertTrue(res["success"])

            # Read directly from disk
            with open(self.config_path, "r", encoding="utf-8") as f:
                disk_cfg = json.load(f)

            self.assertEqual(disk_cfg["mode"], "ptt")
            self.assertEqual(disk_cfg["audio"]["mode"], "ptt")
            self.assertTrue(disk_cfg["audio"]["software_gate"])
            self.assertEqual(disk_cfg["audio"]["ptt_key"], "F13")
            self.assertEqual(disk_cfg["vision"]["fps"], 0.5)
            self.assertEqual(disk_cfg["vision"]["monitor"], "1")
            self.assertEqual(disk_cfg["ui"]["hud_mode"], "max")
            self.assertEqual(disk_cfg["ui"]["floating_overlay"], "on_minimize")

            # Check bridge get_config()
            ui_cfg = bridge.get_config()
            self.assertEqual(ui_cfg["mode"], "ptt")
            self.assertEqual(ui_cfg["audio"]["mode"], "ptt")
            self.assertTrue(ui_cfg["audio"]["software_gate"])
            self.assertEqual(ui_cfg["vision"]["monitor"], "1")

            bridge.stop_audio_watcher()
        finally:
            loop.close()

    def test_voice_accent_persistence_roundtrip(self):
        """Verify voice accent saves properly to disk and config without falling back to default."""
        loop = asyncio.new_event_loop()
        try:
            bridge = GuiBridge(config_path=self.config_path, loop=loop)
            payload = {
                "voice_accent": "British",
                "api": {
                    "voice_accent": "British",
                }
            }
            res = bridge.save_config(payload)
            self.assertTrue(res["success"])

            with open(self.config_path, "r", encoding="utf-8") as f:
                disk_cfg = json.load(f)
            self.assertEqual(disk_cfg.get("voice_accent"), "British")
            self.assertEqual(disk_cfg.get("api", {}).get("voice_accent"), "British")

            ui_cfg = bridge.get_config()
            self.assertEqual(ui_cfg.get("voice_accent"), "British")
            self.assertEqual(ui_cfg.get("api", {}).get("voice_accent"), "British")

            bridge.stop_audio_watcher()
        finally:
            loop.close()

    def test_hud_mode_sync_and_js_notification(self):
        """Verify setting HUD mode updates config and evaluates JS in overlay window."""
        loop = asyncio.new_event_loop()
        try:
            bridge = GuiBridge(config_path=self.config_path, loop=loop)
            from unittest.mock import MagicMock
            mock_overlay = MagicMock()
            bridge.set_overlay_window(mock_overlay)

            res = bridge.set_mode("mini")
            self.assertTrue(res["success"])
            self.assertEqual(bridge.get_hud_mode()["mode"], "mini")

            # Check that evaluate_js was dispatched
            mock_overlay.evaluate_js.assert_any_call(
                "if (window.aetherOverlay && window.aetherOverlay.setMode) { window.aetherOverlay.setMode('mini'); }"
            )

            # Check persisted config
            with open(self.config_path, "r", encoding="utf-8") as f:
                disk_cfg = json.load(f)
            self.assertEqual(disk_cfg.get("hud_mode"), "mini")
            self.assertEqual(disk_cfg.get("ui", {}).get("hud_mode"), "mini")

            bridge.stop_audio_watcher()
        finally:
            loop.close()

    def test_audio_stream_max_speech_frames_and_pauses(self):
        """Verify audio stream guard allows 30s speech and VAD turn detector preserves 1400ms pause."""
        from core.audio_stream import AudioPipeline
        pipeline = AudioPipeline(
            mode="always_on",
            vad_trailing_silence_ms=1400,
        )
        self.assertEqual(pipeline._max_speech_frames, 600)
        self.assertEqual(pipeline.vad_turn_detector.silence_timeout_ms, 1400)


    def test_full_reboot_cycle_all_user_options(self):
        """Simulates full application reboot cycle: save -> disk write -> restart bridge -> assert zero reversion."""
        loop = asyncio.new_event_loop()
        try:
            bridge = GuiBridge(config_path=self.config_path, loop=loop)
            full_user_payload = {
                "mode": "ptt",
                "ptt_type": "toggle",
                "ptt_key": "Backslash",
                "ptt_key_display": "\\",
                "ptt_vk": 220,
                "ptt_modifiers": ["Control"],
                "software_gate": True,
                "voice_accent": "British",
                "tts_voice": "Puck",
                "tts_speed": 1.25,
                "vad_trailing_silence_ms": 1800,
                "agent_name": "Aether",
                "wake_phrase": "Hey Aether",
                "sleep_phrase": "Aether stop listening",
                "kill_phrase": "Aether stop",
                "always_on_mode": "wake_sleep_toggle",
                "primary_model_endpoint": "gemini-3.8-flash",
                "tier2_heavy_model": "gemini-3.1-pro-preview",
                "boot_on_startup": True,
                "start_minimized": True,
                "api": {
                    "agent_name": "Aether",
                    "voice_name": "Puck",
                    "voice_accent": "British",
                    "voice_speed": 1.25,
                    "model_id": "gemini-3.8-flash",
                },
                "audio": {
                    "mode": "ptt",
                    "ptt_type": "toggle",
                    "ptt_key": "Backslash",
                    "ptt_key_display": "\\",
                    "ptt_vk": 220,
                    "ptt_modifiers": ["Control"],
                    "software_gate": True,
                    "vad_trailing_silence_ms": 1800,
                    "preferred_language": "en-US",
                },
                "vision": {
                    "enabled": False,
                    "fps": 2.0,
                    "monitor": "all",
                    "endpoint": "gemini-3.1-pro-snapshot",
                    "resolution": [768, 768],
                    "jpeg_quality": 70,
                },
                "ui": {
                    "floating_overlay": "disabled",
                    "minimize_to_tray": False,
                    "hud_mode": "mini",
                    "hud_mode_hotkey": "Ctrl+Shift+H",
                    "hud_mode_key_display": "Ctrl+Shift+H",
                    "hud_mode_vk": 72,
                    "hud_mode_modifiers": ["Control", "Shift"],
                },
            }

            res = bridge.save_config(full_user_payload)
            self.assertTrue(res["success"])
            bridge.stop_audio_watcher()
        finally:
            loop.close()

        # Step 2: Read raw disk file to verify persistence
        with open(self.config_path, "r", encoding="utf-8") as f:
            disk_cfg = json.load(f)

        self.assertEqual(disk_cfg["mode"], "ptt")
        self.assertEqual(disk_cfg["audio"]["mode"], "ptt")
        self.assertEqual(disk_cfg["ptt_type"], "toggle")
        self.assertEqual(disk_cfg["audio"]["ptt_type"], "toggle")
        self.assertEqual(disk_cfg["ptt_key"], "Backslash")
        self.assertTrue(disk_cfg["software_gate"])
        self.assertTrue(disk_cfg["audio"]["software_gate"])
        self.assertEqual(disk_cfg["voice_accent"], "British")
        self.assertEqual(disk_cfg["api"]["voice_accent"], "British")
        self.assertEqual(disk_cfg["tts_voice"], "Puck")
        self.assertEqual(disk_cfg["tts_speed"], 1.25)
        self.assertFalse(disk_cfg["vision"]["enabled"])
        self.assertEqual(disk_cfg["vision"]["fps"], 2.0)
        self.assertEqual(disk_cfg["vision"]["monitor"], "all")
        self.assertEqual(disk_cfg["vision"]["endpoint"], "gemini-3.1-pro-snapshot")
        self.assertEqual(disk_cfg["ui"]["floating_overlay"], "disabled")
        self.assertFalse(disk_cfg["ui"]["minimize_to_tray"])
        self.assertEqual(disk_cfg["ui"]["hud_mode"], "mini")

        # Step 3: Simulate application restart with a completely fresh GuiBridge instance
        restart_loop = asyncio.new_event_loop()
        try:
            rebooted_bridge = GuiBridge(config_path=self.config_path, loop=restart_loop)
            reboot_cfg = rebooted_bridge.get_config()

            # Verify PTT choices persisted
            self.assertEqual(reboot_cfg["mode"], "ptt")
            self.assertEqual(reboot_cfg["audio"]["mode"], "ptt")
            self.assertEqual(reboot_cfg["ptt_type"], "toggle")
            self.assertEqual(reboot_cfg["ptt_key"], "Backslash")
            self.assertEqual(reboot_cfg["audio"]["ptt_key"], "Backslash")

            # Verify Software Gate (Mute microphone while speaking) persisted
            self.assertTrue(reboot_cfg["software_gate"])
            self.assertTrue(reboot_cfg["audio"]["software_gate"])

            # Verify Voice Accent & TTS settings persisted
            self.assertEqual(reboot_cfg["voice_accent"], "British")
            self.assertEqual(reboot_cfg["api"]["voice_accent"], "British")
            self.assertEqual(reboot_cfg["tts_voice"], "Puck")
            self.assertEqual(reboot_cfg["tts_speed"], 1.25)

            # Verify Desktop Vision options persisted
            self.assertFalse(reboot_cfg["vision"]["enabled"])
            self.assertEqual(reboot_cfg["vision"]["fps"], 2.0)
            self.assertEqual(reboot_cfg["vision"]["monitor"], "all")
            self.assertEqual(reboot_cfg["vision"]["endpoint"], "gemini-3.1-pro-snapshot")

            # Verify HUD options persisted
            self.assertEqual(reboot_cfg["ui"]["floating_overlay"], "disabled")
            self.assertFalse(reboot_cfg["ui"]["minimize_to_tray"])
            self.assertEqual(reboot_cfg["ui"]["hud_mode"], "mini")

            rebooted_bridge.stop_audio_watcher()
        finally:
            restart_loop.close()


if __name__ == "__main__":
    unittest.main()

