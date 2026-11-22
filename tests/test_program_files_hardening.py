import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import paths
from ui.hud_window import HudWindow


class TestProgramFilesHardening(unittest.TestCase):
    def test_frozen_paths_redirect_to_localappdata(self):
        with tempfile.TemporaryDirectory() as tmp_local, tempfile.TemporaryDirectory() as tmp_install:
            with patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "_MEIPASS", os.path.join(tmp_install, "_internal"), create=True), \
                 patch.object(sys, "executable", os.path.join(tmp_install, "AetherDesktop.exe")), \
                 patch.dict(os.environ, {"LOCALAPPDATA": tmp_local}):
                self.assertEqual(paths.get_resource_dir(), os.path.join(tmp_install, "_internal"))
                self.assertEqual(paths.get_user_data_dir(), os.path.join(tmp_local, "Aether"))
                self.assertEqual(paths.get_webview_cache_dir(), os.path.join(tmp_local, "Aether", "WebView2"))
                self.assertEqual(paths.get_logs_dir(), os.path.join(tmp_local, "Aether", "logs"))
                self.assertTrue(os.path.isdir(os.path.join(tmp_local, "Aether")))
                self.assertTrue(os.path.isdir(os.path.join(tmp_local, "Aether", "WebView2")))
                self.assertTrue(os.path.isdir(os.path.join(tmp_local, "Aether", "logs")))

    def test_hud_window_sets_webview2_env_and_encodes_spaces_and_parens(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            spaced_bundle = os.path.join(tmp_root, "Program Files (x86)", "Aether", "_internal")
            static_dir = os.path.join(spaced_bundle, "ui", "static")
            os.makedirs(static_dir, exist_ok=True)
            index_html = os.path.join(static_dir, "index.html")
            overlay_html = os.path.join(static_dir, "overlay.html")
            Path(index_html).write_text("<html></html>", encoding="utf-8")
            Path(overlay_html).write_text("<html></html>", encoding="utf-8")

            local_app = os.path.join(tmp_root, "LocalAppData")
            expected_cache = os.path.join(local_app, "Aether", "WebView2")

            bridge = MagicMock()
            bridge.get_raw_config.return_value = {"ui": {"hud_mode": "normal"}}
            main_win = MagicMock()
            overlay_win = MagicMock()
            overlay_win.events = MagicMock()

            env_seen_at_create = {}

            def _fake_create_window(**kwargs):
                env_seen_at_create["WEBVIEW2_USER_DATA_FOLDER"] = os.environ.get("WEBVIEW2_USER_DATA_FOLDER")
                return main_win if kwargs.get("title") == "Aether Desktop" else overlay_win

            with patch.dict(os.environ, {"LOCALAPPDATA": local_app}, clear=False), \
                 patch("ui.hud_window.get_resource_dir", return_value=spaced_bundle), \
                 patch("ui.hud_window.SystemTrayManager", return_value=MagicMock()), \
                 patch("ui.hud_window.webview.create_window", side_effect=_fake_create_window) as mock_create, \
                 patch("ui.hud_window.webview.start") as mock_start:
                hud = HudWindow(bridge=bridge, title="Aether Desktop")
                self.assertEqual(env_seen_at_create.get("WEBVIEW2_USER_DATA_FOLDER"), expected_cache)

                urls = [call.kwargs["url"] for call in mock_create.call_args_list]
                self.assertTrue(all(u.startswith("file:///") for u in urls))
                self.assertTrue(all("%20" in u for u in urls))
                self.assertTrue(all(" " not in u for u in urls))
                self.assertEqual(urls[0], Path(index_html).resolve().as_uri())
                self.assertEqual(urls[1], Path(overlay_html).resolve().as_uri())

                hud.start(debug=False)
                mock_start.assert_called_once()
                _, start_kwargs = mock_start.call_args
                self.assertEqual(start_kwargs.get("gui"), "edgechromium")
                self.assertFalse(start_kwargs.get("private_mode"))
                self.assertEqual(start_kwargs.get("storage_path"), expected_cache)

    def test_fatal_startup_error_writes_startup_log(self):
        import main as app_main

        with tempfile.TemporaryDirectory() as tmp_local:
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp_local}):
                try:
                    raise RuntimeError("Simulated Program Files startup failure")
                except RuntimeError as exc:
                    app_main._log_fatal_startup_error(exc, show_dialog=False)

                log_file = os.path.join(tmp_local, "Aether", "logs", "startup.log")
                self.assertTrue(os.path.isfile(log_file))
                content = Path(log_file).read_text(encoding="utf-8")
                self.assertIn("FATAL STARTUP ERROR", content)
                self.assertIn("Simulated Program Files startup failure", content)


if __name__ == "__main__":
    unittest.main()
