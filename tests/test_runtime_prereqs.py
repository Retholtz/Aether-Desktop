"""
Unit tests for core.runtime_prereqs (Windows WebView2 & .NET runtime checks).
"""

import unittest
from unittest.mock import patch

from core.runtime_prereqs import check_windows_prerequisites, prompt_missing_prerequisites


class TestRuntimePrereqs(unittest.TestCase):
    @patch("core.runtime_prereqs.is_dotnet_desktop_runtime_installed", return_value=True)
    @patch("core.runtime_prereqs.is_dotnet_framework_installed", return_value=True)
    @patch("core.runtime_prereqs.is_webview2_installed", return_value=True)
    def test_all_prereqs_present(self, _wv2, _netfx, _netcore):
        res = check_windows_prerequisites()
        self.assertTrue(res["ok"])
        self.assertEqual(res["missing"], [])

    @patch("core.runtime_prereqs.is_dotnet_desktop_runtime_installed", return_value=False)
    @patch("core.runtime_prereqs.is_dotnet_framework_installed", return_value=False)
    @patch("core.runtime_prereqs.is_webview2_installed", return_value=False)
    def test_missing_webview2_and_dotnet_reported(self, _wv2, _netfx, _netcore):
        res = check_windows_prerequisites()
        self.assertFalse(res["ok"])
        self.assertFalse(res["webview2_installed"])
        self.assertFalse(res["dotnet_installed"])
        self.assertEqual(len(res["missing"]), 2)

    @patch("ctypes.windll.user32.MessageBoxW", return_value=7)  # IDNO
    def test_prompt_returns_false_when_webview2_missing(self, _msgbox):
        status = {
            "ok": False,
            "webview2_installed": False,
            "dotnet_installed": True,
            "missing": ["Microsoft Edge WebView2 Runtime (MicrosoftEdgeWebview2Setup.exe)"],
        }
        self.assertFalse(prompt_missing_prerequisites(status))


if __name__ == "__main__":
    unittest.main()
