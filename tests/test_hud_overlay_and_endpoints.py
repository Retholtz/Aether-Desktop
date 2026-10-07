"""
Unit tests for HUD Overlay Window movement/clamping, Model Endpoints dropdowns,
and frontend JavaScript asset integrity.
"""

import os
import unittest
from unittest.mock import MagicMock
from ui.hud_window import get_monitor_work_areas, clamp_window_position, HUDBridge


class TestHUDOverlayAndEndpoints(unittest.TestCase):

    def test_get_monitor_work_areas_structure(self):
        """Verifies get_monitor_work_areas returns valid logical geometry."""
        work_areas = get_monitor_work_areas()
        self.assertIsInstance(work_areas, list)
        self.assertGreater(len(work_areas), 0)
        for mon in work_areas:
            self.assertIn("left", mon)
            self.assertIn("top", mon)
            self.assertIn("right", mon)
            self.assertIn("bottom", mon)
            self.assertIn("is_primary", mon)
            self.assertGreater(mon["right"], mon["left"])
            self.assertGreater(mon["bottom"], mon["top"])
            self.assertIsInstance(mon["left"], int)
            self.assertIsInstance(mon["right"], int)

    def test_clamp_window_position_default(self):
        """None coordinates should snap to the top-right corner of the primary display."""
        work_areas = get_monitor_work_areas()
        primary = next((m for m in work_areas if m["is_primary"]), work_areas[0])

        cx, cy = clamp_window_position(None, None, width=440, height=180, margin=16)
        expected_x = primary["right"] - 440 - 24
        expected_y = primary["top"] + 24
        self.assertEqual(cx, int(expected_x))
        self.assertEqual(cy, int(expected_y))

    def test_clamp_window_position_out_of_bounds(self):
        """Coordinates pushed far beyond the display edges must be clamped within safe margins."""
        work_areas = get_monitor_work_areas()
        primary = next((m for m in work_areas if m["is_primary"]), work_areas[0])

        # Far beyond right/bottom
        cx, cy = clamp_window_position(99999, 99999, width=440, height=180, margin=16)
        self.assertLessEqual(cx, primary["right"] - 440 - 16)
        self.assertLessEqual(cy, primary["bottom"] - 180 - 16)

        # Far beyond left/top
        cx, cy = clamp_window_position(-99999, -99999, width=440, height=180, margin=16)
        self.assertGreaterEqual(cx, primary["left"] + 16)
        self.assertGreaterEqual(cy, primary["top"] + 16)

    def test_hud_bridge_move_overlay_clamping(self):
        """HUDBridge.move_overlay should clamp coordinates and move the window without disk writes."""
        mock_win = MagicMock()
        mock_win.width = 440
        mock_win.height = 180
        mock_bridge = MagicMock()

        hud = HUDBridge(mock_win, mock_bridge)
        res = hud.move_overlay(50000, 50000)

        self.assertTrue(res["success"])
        mock_win.move.assert_called_once()
        called_x, called_y = mock_win.move.call_args[0]
        # Should be clamped within bounds
        self.assertLess(called_x, 50000)
        self.assertLess(called_y, 50000)
        # Should NOT call save_overlay_position synchronously during move
        mock_bridge.save_overlay_position.assert_not_called()

    def test_hud_bridge_start_drag(self):
        """HUDBridge.start_drag returns a dict response without throwing."""
        mock_win = MagicMock()
        mock_bridge = MagicMock()
        hud = HUDBridge(mock_win, mock_bridge)

        res = hud.start_drag()
        self.assertIsInstance(res, dict)
        self.assertIn("success", res)

    def test_html_endpoints_populated(self):
        """Ensures index.html includes option elements for primary and heavy models."""
        path = "ui/static/index.html"
        self.assertTrue(os.path.exists(path), f"File {path} must exist")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn('id="select-primary-model"', content)
        self.assertIn('id="select-heavy-model"', content)
        self.assertIn('value="gemini-3.8-flash"', content)
        self.assertIn('value="gemini-3.1-pro-preview"', content)

    def test_settings_js_default_models_defined(self):
        """Ensures settings.js defines default fallback model arrays."""
        path = "ui/static/settings.js"
        self.assertTrue(os.path.exists(path), f"File {path} must exist")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("DEFAULT_CHAT_MODELS", content)
        self.assertIn("DEFAULT_HEAVY_MODELS", content)
        self.assertIn("gemini-3.8-flash", content)
        self.assertIn("gemini-3.1-pro-preview", content)

    def test_app_js_no_duplicate_declarations(self):
        """Ensures app.js does not redeclare block-scoped variables."""
        path = "ui/static/app.js"
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # Count declarations of heavySel to guarantee no SyntaxError: Identifier 'heavySel' has already been declared
        self.assertEqual(content.count("const heavySel ="), 1)
        self.assertEqual(content.count("let heavySel ="), 0)
        self.assertEqual(content.count("var heavySel ="), 0)


if __name__ == "__main__":
    unittest.main()
