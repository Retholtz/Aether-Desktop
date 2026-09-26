"""
Aether Desktop - Batch 5 Unit Test Suite
Validates Windows Startup Registry Manager, Configuration Persistence,
Window Lifecycle (start_minimized), and GUI Bridge Startup Settings.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from core.startup_manager import set_boot_on_startup, is_boot_on_startup_enabled
from core.config_manager import (
    load_config,
    save_config,
    sync_config_schema,
    apply_startup_configuration,
)
from main import initialize_window


@unittest.skipUnless(sys.platform == "win32", "Windows Registry tests require Win32")
class TestBatch5Startup(unittest.TestCase):
    def setUp(self):
        # Save previous registry state to restore after test
        self.original_enabled = is_boot_on_startup_enabled()

    def tearDown(self):
        # Ensure test cleanup: restore original registry state
        set_boot_on_startup(self.original_enabled)

    def test_boot_on_startup_toggle(self):
        # 1. Enable startup
        success = set_boot_on_startup(True)
        self.assertTrue(success)
        self.assertTrue(is_boot_on_startup_enabled())

        # 2. Disable startup
        success = set_boot_on_startup(False)
        self.assertTrue(success)
        self.assertFalse(is_boot_on_startup_enabled())

    def test_schema_defaults(self):
        schema = sync_config_schema({})
        self.assertIn("boot_on_startup", schema)
        self.assertIn("start_minimized", schema)
        self.assertFalse(schema["boot_on_startup"])
        self.assertFalse(schema["start_minimized"])

    def test_apply_startup_configuration(self):
        # Test enabling via config dict
        apply_startup_configuration({"boot_on_startup": True})
        self.assertTrue(is_boot_on_startup_enabled())

        # Test disabling via config dict
        apply_startup_configuration({"boot_on_startup": False})
        self.assertFalse(is_boot_on_startup_enabled())

    def test_initialize_window_minimized(self):
        mock_window = MagicMock()
        with patch("main.load_config", return_value={"start_minimized": True}):
            initialize_window(mock_window)
            mock_window.minimize.assert_called_once()
            mock_window.show.assert_not_called()

    def test_initialize_window_normal(self):
        mock_window = MagicMock()
        with patch("main.load_config", return_value={"start_minimized": False}):
            initialize_window(mock_window)
            mock_window.show.assert_called_once()
            mock_window.minimize.assert_not_called()

    def test_initialize_window_minimize_fallback_to_show(self):
        mock_window = MagicMock()
        mock_window.minimize.side_effect = RuntimeError("Mock window minimize failure")
        with patch("main.load_config", return_value={"start_minimized": True}):
            initialize_window(mock_window)
            mock_window.minimize.assert_called_once()
            mock_window.show.assert_called_once()


if __name__ == "__main__":
    unittest.main()
