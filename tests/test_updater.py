"""
Unit tests for core.version, core.updater, and GuiBridge update integration.
"""

import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock, patch

from core.config_manager import sync_config_schema
from core.gui_bridge import GuiBridge
from core.updater import (
    EXE_NAME,
    build_updater_bat_script,
    check_for_updates,
    download_and_stage_update,
    extract_version_string,
    is_newer_version,
    is_trusted_github_url,
    launch_updater_bat,
    parse_version,
    select_release_asset,
)
from core.version import APP_VERSION


class TestVersionAndAssetHelpers(unittest.TestCase):
    def test_extract_and_parse_versions(self):
        self.assertEqual(APP_VERSION, "0.81")
        self.assertEqual(extract_version_string("Version 0.82"), "0.82")
        self.assertEqual(extract_version_string("v0.82"), "0.82")
        self.assertEqual(extract_version_string("Aether 0.82.zip"), "0.82")
        self.assertEqual(parse_version("0.81"), (0, 81))
        self.assertEqual(parse_version("0.81.0"), (0, 81))

    def test_is_newer_version(self):
        self.assertTrue(is_newer_version("0.82", "0.81"))
        self.assertTrue(is_newer_version("v0.82", "0.81"))
        self.assertTrue(is_newer_version("Version 0.82", "0.81"))
        self.assertTrue(is_newer_version("0.81.1", "0.81"))
        self.assertTrue(is_newer_version("1.0", "0.81"))
        self.assertFalse(is_newer_version("0.81", "0.81"))
        self.assertFalse(is_newer_version("0.81.0", "0.81"))
        self.assertFalse(is_newer_version("0.80", "0.81"))
        self.assertFalse(is_newer_version("0.9", "0.81"))

    def test_trusted_url_and_asset_selection(self):
        self.assertTrue(is_trusted_github_url("https://github.com/Retholtz/Aether-Desktop/releases/download/v0.82/Aether.0.82.zip"))
        self.assertTrue(is_trusted_github_url("https://objects.githubusercontent.com/some/path/Aether.0.82.zip"))
        self.assertFalse(is_trusted_github_url("http://github.com/Retholtz/Aether-Desktop/releases/download/v0.82/Aether.0.82.zip"))
        self.assertFalse(is_trusted_github_url("https://evil.example.com/Aether.0.82.zip"))

        assets = [
            {"name": "notes.txt", "browser_download_url": "https://github.com/Retholtz/Aether-Desktop/releases/download/v0.82/notes.txt"},
            {"name": "Aether 0.82.zip", "browser_download_url": "https://github.com/Retholtz/Aether-Desktop/releases/download/v0.82/Aether.0.82.zip", "size": 12345},
        ]
        chosen = select_release_asset(assets)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["name"], "Aether 0.82.zip")


class TestCheckAndStageUpdate(unittest.TestCase):
    @patch("core.updater.requests.get")
    def test_check_for_updates_404_no_releases(self, mock_get):
        resp = MagicMock()
        resp.status_code = 404
        resp.ok = False
        mock_get.return_value = resp

        res = check_for_updates("0.81")
        self.assertTrue(res["success"])
        self.assertFalse(res["update_available"])
        self.assertTrue(res["no_releases"])

    @patch("core.updater.requests.get")
    def test_check_for_updates_newer_release_available(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.ok = True
        resp.json.return_value = {
            "tag_name": "v0.82",
            "name": "Version 0.82",
            "body": "- Added auto-updater",
            "html_url": "https://github.com/Retholtz/Aether-Desktop/releases/tag/v0.82",
            "published_at": "2026-11-16T14:00:00Z",
            "assets": [
                {
                    "name": "Aether 0.82.zip",
                    "browser_download_url": "https://github.com/Retholtz/Aether-Desktop/releases/download/v0.82/Aether.0.82.zip",
                    "size": 4096,
                }
            ],
        }
        mock_get.return_value = resp

        res = check_for_updates("0.81")
        self.assertTrue(res["success"])
        self.assertTrue(res["update_available"])
        self.assertEqual(res["latest_version"], "0.82")
        self.assertEqual(res["asset_name"], "Aether 0.82.zip")

    @patch("core.updater.requests.get")
    def test_check_for_updates_same_version(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.ok = True
        resp.json.return_value = {
            "tag_name": "0.81",
            "name": "Version 0.81",
            "body": "Initial release",
            "html_url": "https://github.com/Retholtz/Aether-Desktop/releases/tag/0.81",
            "assets": [],
        }
        mock_get.return_value = resp

        res = check_for_updates("0.81")
        self.assertTrue(res["success"])
        self.assertFalse(res["update_available"])

    @patch("core.updater.requests.get")
    def test_download_and_stage_nested_zip_strips_config(self, mock_get):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("AetherDesktop/AetherDesktop.exe", b"NEW_EXE_BYTES")
            zf.writestr("AetherDesktop/_internal/version.txt", b"0.82")
            zf.writestr("AetherDesktop/config.json", b'{"should_not": "overwrite"}')
            zf.writestr("AetherDesktop/.env", b"SECRET=NO")
        zip_bytes = buf.getvalue()

        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.headers = {"Content-Length": str(len(zip_bytes))}
        resp.iter_content.return_value = [zip_bytes]
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_get.return_value = resp

        res = download_and_stage_update("https://github.com/Retholtz/Aether-Desktop/releases/download/v0.82/Aether.0.82.zip")
        try:
            self.assertTrue(res["success"])
            payload_dir = res["payload_dir"]
            self.assertTrue(os.path.isfile(os.path.join(payload_dir, EXE_NAME)))
            self.assertTrue(os.path.isfile(os.path.join(payload_dir, "_internal", "version.txt")))
            self.assertFalse(os.path.exists(os.path.join(payload_dir, "config.json")))
            self.assertFalse(os.path.exists(os.path.join(payload_dir, ".env")))
        finally:
            if res.get("stage_dir") and os.path.exists(res["stage_dir"]):
                shutil.rmtree(res["stage_dir"], ignore_errors=True)

    @patch("core.updater.requests.get")
    def test_download_and_stage_blocks_zip_slip(self, mock_get):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../../evil.txt", b"bad")
        zip_bytes = buf.getvalue()

        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.headers = {"Content-Length": str(len(zip_bytes))}
        resp.iter_content.return_value = [zip_bytes]
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_get.return_value = resp

        res = download_and_stage_update("https://github.com/Retholtz/Aether-Desktop/releases/download/v0.82/Aether.0.82.zip")
        self.assertFalse(res["success"])
        self.assertIn("Unsafe zip entry", res["error"])


class TestBatBuilderAndBridgeIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "config.json")
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"agent_name": "Aether"}, f)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_bat_script_contains_delete_replace_and_reboot(self):
        bat = build_updater_bat_script(
            old_pid=4242,
            app_dir=r"C:\Apps\AetherDesktop",
            payload_dir=r"C:\Temp\aether_desktop_update\extracted\AetherDesktop",
            stage_dir=r"C:\Temp\aether_desktop_update",
        )
        self.assertIn("OLD_PID=4242", bat)
        self.assertIn('del /F /Q "%APP_DIR%\\%EXE_NAME%"', bat)
        self.assertIn("robocopy", bat)
        self.assertIn("_internal_backup", bat)
        self.assertIn('start "" /D "%APP_DIR%" "%APP_DIR%\\%EXE_NAME%"', bat)
        self.assertIn('del /F /Q "%~f0"', bat)

    def test_launch_updater_bat_refuses_in_dev_mode(self):
        res = launch_updater_bat(self.test_dir, self.test_dir)
        self.assertFalse(res["success"])
        self.assertTrue(res.get("dev_mode"))

    def test_schema_and_bridge_skip_update_persistence(self):
        synced = sync_config_schema({})
        self.assertTrue(synced["auto_check_updates"])
        self.assertEqual(synced["skipped_update_version"], "")

        bridge = GuiBridge(config_path=self.config_path)
        ver_info = bridge.get_app_version()
        self.assertEqual(ver_info["version"], APP_VERSION)

        bridge.skip_update_version("0.82")
        with patch("core.updater.check_for_updates") as mock_check:
            mock_check.return_value = {
                "success": True,
                "update_available": True,
                "current_version": "0.81",
                "latest_version": "0.82",
            }
            auto_res = bridge.check_for_updates(force=False)
            self.assertTrue(auto_res["skipped"])
            manual_res = bridge.check_for_updates(force=True)
            self.assertFalse(manual_res["skipped"])


if __name__ == "__main__":
    unittest.main()
