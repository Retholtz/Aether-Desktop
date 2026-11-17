"""
Unit tests for core.version, core.updater (shell-free), and GuiBridge update integration.
"""

import hashlib
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
    apply_staged_update_and_relaunch,
    check_for_updates,
    cleanup_old_update_artifacts,
    download_and_stage_update,
    extract_version_string,
    is_newer_version,
    is_trusted_github_url,
    parse_version,
    select_release_asset,
    strip_mark_of_the_web,
)
from core.version import APP_VERSION


def _make_mock_stream(zip_bytes: bytes, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"Content-Length": str(len(zip_bytes))}
    resp.iter_bytes.return_value = [zip_bytes]
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestVersionAndAssetHelpers(unittest.TestCase):
    def test_extract_and_parse_versions(self):
        self.assertEqual(APP_VERSION, "0.84.2")
        self.assertEqual(extract_version_string("Version 0.83"), "0.83")
        self.assertEqual(extract_version_string("v0.83"), "0.83")
        self.assertEqual(extract_version_string("Aether 0.83.zip"), "0.83")
        self.assertEqual(parse_version("0.83"), (0, 83))
        self.assertEqual(parse_version("0.83.0"), (0, 83))

    def test_is_newer_version(self):
        self.assertTrue(is_newer_version("0.83", "0.82"))
        self.assertTrue(is_newer_version("v0.83", "0.82"))
        self.assertTrue(is_newer_version("Version 0.83", "0.82"))
        self.assertTrue(is_newer_version("0.82.1", "0.82"))
        self.assertTrue(is_newer_version("1.0", "0.83"))
        self.assertFalse(is_newer_version("0.83", "0.83"))
        self.assertFalse(is_newer_version("0.83.0", "0.83"))
        self.assertFalse(is_newer_version("0.82", "0.83"))
        self.assertFalse(is_newer_version("0.9", "0.83"))

    def test_trusted_url_and_asset_selection(self):
        self.assertTrue(is_trusted_github_url("https://github.com/Retholtz/Aether-Desktop/releases/download/v0.83/Aether.0.83.zip"))
        self.assertTrue(is_trusted_github_url("https://objects.githubusercontent.com/some/path/Aether.0.83.zip"))
        self.assertFalse(is_trusted_github_url("http://github.com/Retholtz/Aether-Desktop/releases/download/v0.83/Aether.0.83.zip"))
        self.assertFalse(is_trusted_github_url("https://evil.example.com/Aether.0.83.zip"))

        assets = [
            {"name": "notes.txt", "browser_download_url": "https://github.com/Retholtz/Aether-Desktop/releases/download/v0.83/notes.txt"},
            {"name": "Aether 0.83.zip", "browser_download_url": "https://github.com/Retholtz/Aether-Desktop/releases/download/v0.83/Aether.0.83.zip", "size": 12345},
        ]
        chosen = select_release_asset(assets)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["name"], "Aether 0.83.zip")


class TestCheckAndStageUpdate(unittest.TestCase):
    def setUp(self):
        self.app_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.app_dir, ignore_errors=True)

    @patch("core.updater.httpx.get")
    def test_check_for_updates_404_no_releases(self, mock_get):
        resp = MagicMock()
        resp.status_code = 404
        mock_get.return_value = resp

        res = check_for_updates("0.82")
        self.assertTrue(res["success"])
        self.assertFalse(res["update_available"])
        self.assertTrue(res["no_releases"])

    @patch("core.updater.httpx.get")
    def test_check_for_updates_newer_release_available(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "tag_name": "v0.83",
            "name": "Version 0.83",
            "body": "- Shell-free auto-updater",
            "html_url": "https://github.com/Retholtz/Aether-Desktop/releases/tag/v0.83",
            "published_at": "2026-11-16T17:00:00Z",
            "assets": [
                {
                    "name": "Aether 0.83.zip",
                    "browser_download_url": "https://github.com/Retholtz/Aether-Desktop/releases/download/v0.83/Aether.0.83.zip",
                    "size": 4096,
                    "digest": "sha256:" + ("a" * 64),
                }
            ],
        }
        mock_get.return_value = resp

        res = check_for_updates("0.82")
        self.assertTrue(res["success"])
        self.assertTrue(res["update_available"])
        self.assertEqual(res["latest_version"], "0.83")
        self.assertEqual(res["asset_name"], "Aether 0.83.zip")
        self.assertEqual(res["expected_sha256"], "a" * 64)

    @patch("core.updater.httpx.get")
    def test_check_for_updates_same_version(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "tag_name": "0.83",
            "name": "Version 0.83",
            "body": "Current release",
            "html_url": "https://github.com/Retholtz/Aether-Desktop/releases/tag/0.83",
            "assets": [],
        }
        mock_get.return_value = resp

        res = check_for_updates("0.83")
        self.assertTrue(res["success"])
        self.assertFalse(res["update_available"])

    @patch("core.updater.httpx.stream")
    def test_download_and_stage_nested_zip_verifies_sha256_and_strips_config(self, mock_stream):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("AetherDesktop/AetherDesktop.exe", b"NEW_EXE_BYTES")
            zf.writestr("AetherDesktop/_internal/version.txt", b"0.83")
            zf.writestr("AetherDesktop/config.json", b'{"should_not": "overwrite"}')
            zf.writestr("AetherDesktop/.env", b"SECRET=NO")
        zip_bytes = buf.getvalue()
        expected_sha = hashlib.sha256(zip_bytes).hexdigest()

        mock_stream.return_value = _make_mock_stream(zip_bytes)

        res = download_and_stage_update(
            "https://github.com/Retholtz/Aether-Desktop/releases/download/v0.83/Aether.0.83.zip",
            expected_sha256=expected_sha,
            app_dir=self.app_dir,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["sha256"], expected_sha)
        self.assertEqual(res["stage_dir"], os.path.join(self.app_dir, "_update_staging"))
        payload_dir = res["payload_dir"]
        self.assertTrue(os.path.isfile(os.path.join(payload_dir, EXE_NAME)))
        self.assertTrue(os.path.isfile(os.path.join(payload_dir, "_internal", "version.txt")))
        self.assertFalse(os.path.exists(os.path.join(payload_dir, "config.json")))
        self.assertFalse(os.path.exists(os.path.join(payload_dir, ".env")))

    @patch("core.updater.httpx.stream")
    def test_download_and_stage_rejects_sha256_mismatch(self, mock_stream):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("AetherDesktop/AetherDesktop.exe", b"NEW_EXE_BYTES")
        zip_bytes = buf.getvalue()

        mock_stream.return_value = _make_mock_stream(zip_bytes)

        res = download_and_stage_update(
            "https://github.com/Retholtz/Aether-Desktop/releases/download/v0.83/Aether.0.83.zip",
            expected_sha256="0" * 64,
            app_dir=self.app_dir,
        )
        self.assertFalse(res["success"])
        self.assertIn("SHA-256 checksum mismatch", res["error"])
        self.assertFalse(os.path.exists(os.path.join(self.app_dir, "_update_staging")))

    @patch("core.updater.httpx.stream")
    def test_download_and_stage_blocks_zip_slip(self, mock_stream):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../../evil.txt", b"bad")
        zip_bytes = buf.getvalue()

        mock_stream.return_value = _make_mock_stream(zip_bytes)

        res = download_and_stage_update(
            "https://github.com/Retholtz/Aether-Desktop/releases/download/v0.83/Aether.0.83.zip",
            app_dir=self.app_dir,
        )
        self.assertFalse(res["success"])
        self.assertIn("Unsafe zip entry", res["error"])


class TestAtomicSwapCleanupAndBridgeIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "config.json")
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"agent_name": "Aether"}, f)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_cleanup_old_update_artifacts_sweeps_old_files_and_folders(self):
        old_exe = os.path.join(self.test_dir, "AetherDesktop.exe.old")
        old_internal = os.path.join(self.test_dir, "_internal.old")
        old_staging = os.path.join(self.test_dir, "_update_staging")
        keep_exe = os.path.join(self.test_dir, "AetherDesktop.exe")

        with open(old_exe, "wb") as f:
            f.write(b"old")
        os.makedirs(old_internal, exist_ok=True)
        with open(os.path.join(old_internal, "dummy.dll"), "wb") as f:
            f.write(b"dll")
        os.makedirs(old_staging, exist_ok=True)
        with open(keep_exe, "wb") as f:
            f.write(b"current")

        res = cleanup_old_update_artifacts(self.test_dir)
        self.assertIn("AetherDesktop.exe.old", res["removed"])
        self.assertIn("_internal.old", res["removed"])
        self.assertIn("_update_staging", res["removed"])
        self.assertFalse(os.path.exists(old_exe))
        self.assertFalse(os.path.exists(old_internal))
        self.assertFalse(os.path.exists(old_staging))
        self.assertTrue(os.path.isfile(keep_exe))

    def test_strip_mark_of_the_web_removes_zone_identifier(self):
        if os.name != "nt":
            return
        dll_path = os.path.join(self.test_dir, "Microsoft.Web.WebView2.Core.dll")
        with open(dll_path, "wb") as f:
            f.write(b"MZ_FAKE_DLL")
        ads_path = f"{dll_path}:Zone.Identifier"
        with open(ads_path, "w", encoding="utf-8") as f:
            f.write("[ZoneTransfer]\nZoneId=3\n")
        self.assertTrue(os.path.exists(ads_path))
        cleared = strip_mark_of_the_web(self.test_dir)
        self.assertEqual(cleared, 1)
        self.assertFalse(os.path.exists(ads_path))
        self.assertTrue(os.path.isfile(dll_path))

    @patch("core.updater.subprocess.Popen")
    def test_atomic_rename_swap_and_shell_free_relaunch(self, mock_popen):
        # Create current live files
        with open(os.path.join(self.test_dir, EXE_NAME), "wb") as f:
            f.write(b"OLD_EXE")
        os.makedirs(os.path.join(self.test_dir, "_internal"), exist_ok=True)
        with open(os.path.join(self.test_dir, "_internal", "lib.dll"), "wb") as f:
            f.write(b"OLD_DLL")

        # Create staged update payload
        stage_dir = os.path.join(self.test_dir, "_update_staging")
        payload_dir = os.path.join(stage_dir, "extracted", "AetherDesktop")
        os.makedirs(os.path.join(payload_dir, "_internal"), exist_ok=True)
        with open(os.path.join(payload_dir, EXE_NAME), "wb") as f:
            f.write(b"NEW_EXE")
        with open(os.path.join(payload_dir, "_internal", "lib.dll"), "wb") as f:
            f.write(b"NEW_DLL")

        res = apply_staged_update_and_relaunch(
            payload_dir=payload_dir,
            stage_dir=stage_dir,
            exe_name=EXE_NAME,
            app_dir=self.test_dir,
            relaunch=True,
            allow_dev_mode=True,
        )
        self.assertTrue(res["success"])
        with open(os.path.join(self.test_dir, EXE_NAME), "rb") as f:
            self.assertEqual(f.read(), b"NEW_EXE")
        with open(os.path.join(self.test_dir, "_internal", "lib.dll"), "rb") as f:
            self.assertEqual(f.read(), b"NEW_DLL")
        with open(os.path.join(self.test_dir, f"{EXE_NAME}.old"), "rb") as f:
            self.assertEqual(f.read(), b"OLD_EXE")
        with open(os.path.join(self.test_dir, "_internal", "lib.dll.old"), "rb") as f:
            self.assertEqual(f.read(), b"OLD_DLL")
        self.assertFalse(os.path.exists(stage_dir))

        mock_popen.assert_called_once()
        call_args, call_kwargs = mock_popen.call_args
        self.assertEqual(call_args[0][0], os.path.abspath(os.path.join(self.test_dir, EXE_NAME)))
        self.assertFalse(call_kwargs["shell"])
        self.assertTrue(call_kwargs["close_fds"])

    def test_apply_staged_update_refuses_in_dev_mode(self):
        res = apply_staged_update_and_relaunch(self.test_dir, self.test_dir)
        self.assertFalse(res["success"])
        self.assertTrue(res.get("dev_mode"))

    def test_schema_and_bridge_skip_update_persistence(self):
        synced = sync_config_schema({})
        self.assertTrue(synced["auto_check_updates"])
        self.assertEqual(synced["skipped_update_version"], "")

        bridge = GuiBridge(config_path=self.config_path)
        ver_info = bridge.get_app_version()
        self.assertEqual(ver_info["version"], APP_VERSION)

        bridge.skip_update_version("0.83")
        with patch("core.updater.check_for_updates") as mock_check:
            mock_check.return_value = {
                "success": True,
                "update_available": True,
                "current_version": "0.82",
                "latest_version": "0.83",
            }
            auto_res = bridge.check_for_updates(force=False)
            self.assertTrue(auto_res["skipped"])
            manual_res = bridge.check_for_updates(force=True)
            self.assertFalse(manual_res["skipped"])


if __name__ == "__main__":
    unittest.main()
