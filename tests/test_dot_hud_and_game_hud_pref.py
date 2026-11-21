"""
Tests for Dot HUD Mode, Dot Size/Corner/Monitor positioning, 5 Custom HUD Status Colors,
and Per-Game HUD Preference ('default', 'none', 'dot', 'mini', 'normal', 'max').
"""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from core.config_manager import sync_config_schema
from core.game_manager import GameManager
from ui.dot_overlay import (
    DEFAULT_HUD_STATUS_COLORS,
    DotOverlayWindow,
    normalize_dot_position,
    normalize_dot_size,
    render_dot_bgra,
    resolve_dot_position,
    resolve_status_color,
    sanitize_hud_colors,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestDotOverlayGeometryAndRendering(unittest.TestCase):
    def test_colors_sanitization_and_buckets(self):
        self.assertEqual(
            set(DEFAULT_HUD_STATUS_COLORS.keys()),
            {"standby", "listening", "thinking", "executing", "speaking"},
        )
        custom = sanitize_hud_colors({
            "standby": "#112233",
            "listening": "#00ff00",
            "thinking": "not-a-hex",
            "executing": "#ffaa00",
            "speaking": "#abcdef",
        })
        self.assertEqual(custom["standby"], "#112233")
        self.assertEqual(custom["listening"], "#00ff00")
        self.assertEqual(custom["thinking"], DEFAULT_HUD_STATUS_COLORS["thinking"])
        self.assertEqual(custom["executing"], "#ffaa00")
        self.assertEqual(custom["speaking"], "#abcdef")

        self.assertEqual(resolve_status_color("idle", custom), "#112233")
        self.assertEqual(resolve_status_color("hearing", custom), "#00ff00")
        self.assertEqual(resolve_status_color("executing", custom), "#ffaa00")
        self.assertEqual(resolve_status_color("speaking", custom), "#abcdef")
        self.assertEqual(resolve_status_color("error", custom), "#ef476f")

    def test_dot_size_clamping_and_multi_monitor_corners(self):
        self.assertEqual(normalize_dot_size(4), 12)
        self.assertEqual(normalize_dot_size(120), 48)
        self.assertEqual(normalize_dot_size(24), 24)
        self.assertEqual(normalize_dot_position("upper_left"), "top_left")
        self.assertEqual(normalize_dot_position("lower_right"), "bottom_right")

        monitors = [
            {"left": 0, "top": 0, "right": 2560, "bottom": 1400, "is_primary": True},
            {"left": 2560, "top": 0, "right": 4480, "bottom": 1040, "is_primary": False},
        ]
        # Primary top_left
        self.assertEqual(resolve_dot_position("top_left", "1", 40, monitors=monitors, margin=16), (16, 16))
        # Monitor 2 bottom_right
        self.assertEqual(
            resolve_dot_position("bottom_right", "2", 40, monitors=monitors, margin=16),
            (4480 - 40 - 16, 1040 - 40 - 16),
        )
        # Auto monitor follows active game window on Monitor 2
        self.assertEqual(
            resolve_dot_position(
                "top_right",
                "auto",
                40,
                monitors=monitors,
                active_window_rect=(2600, 50, 4400, 1000),
                margin=16,
            ),
            (4480 - 40 - 16, 16),
        )

    def test_render_dot_bgra_transparent_corners_and_opaque_core(self):
        bgra, canvas = render_dot_bgra(22, "#00d2ff", is_standby=False, pulse_phase=0.5)
        self.assertGreater(canvas, 22)
        self.assertEqual(len(bgra), canvas * canvas * 4)
        # Top-left corner pixel must be fully transparent (zero dark box!)
        self.assertEqual(bgra[3], 0)
        # Center pixel must be nearly opaque and cyan-tinted (B > 200, G > 180)
        mid = canvas // 2
        idx = (mid * canvas + mid) * 4
        b, g, r, a = bgra[idx], bgra[idx + 1], bgra[idx + 2], bgra[idx + 3]
        self.assertGreater(a, 240)
        self.assertGreater(b, 200)
        self.assertGreater(g, 180)


class TestConfigAndGameHudPreference(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.profiles_path = os.path.join(self._tmp.name, "games_profiles.json")
        self.gm = GameManager(profiles_path=self.profiles_path)
        self.gm.add_game("Elite Dangerous", "EliteDangerous64.exe")

    def tearDown(self):
        self._tmp.cleanup()

    def test_config_schema_defaults_and_dot_mode(self):
        cfg = {"ui": {"hud_mode": "dot", "dot_size": 30, "dot_position": "bottom_left", "dot_monitor": "2"}}
        synced = sync_config_schema(cfg)
        self.assertEqual(synced["ui"]["hud_mode"], "dot")
        self.assertEqual(synced["ui"]["dot_size"], 30)
        self.assertEqual(synced["ui"]["dot_position"], "bottom_left")
        self.assertEqual(synced["ui"]["dot_monitor"], "2")
        self.assertEqual(synced["ui"]["hud_colors"], DEFAULT_HUD_STATUS_COLORS)

    def test_game_manager_hud_preference_crud(self):
        self.assertEqual(self.gm.get_hud_preference("elite_dangerous"), "default")
        for pref in ("none", "dot", "mini", "normal", "max", "default"):
            res = self.gm.set_hud_preference("elite_dangerous", pref)
            self.assertTrue(res["success"])
            self.assertEqual(self.gm.get_hud_preference("elite_dangerous"), pref)
        bad = self.gm.set_hud_preference("elite_dangerous", "invalid_tier")
        self.assertFalse(bad["success"])

    def test_gui_bridge_dot_cycle_and_per_game_override_restore(self):
        from core.gui_bridge import GuiBridge

        os.environ["AETHER_TESTING"] = "1"
        bridge = GuiBridge.__new__(GuiBridge)
        bridge._config_lock = threading.Lock()
        bridge._config = sync_config_schema({"ui": {"hud_mode": "normal", "floating_overlay": "always"}})
        bridge._config_path = os.path.join(self._tmp.name, "config.json")
        bridge._current_hud_mode = "normal"
        bridge._overlay_visible = True
        bridge._overlay_window = MagicMock()
        bridge._hud_bridge = MagicMock()
        bridge._dot_overlay = None
        bridge._engine = None
        bridge.game_mgr = self.gm
        bridge._is_shutting_down = False
        bridge.on_hud_mode_changed = MagicMock()
        bridge._on_engine_event = MagicMock()
        bridge.get_raw_config = lambda: bridge._config

        # Cycle order: normal -> max -> dot -> mini -> normal
        self.assertEqual(bridge.cycle_hud_mode(), "max")
        self.assertEqual(bridge.cycle_hud_mode(), "dot")
        bridge._hud_bridge.set_mode.assert_called_with("dot")
        self.assertEqual(bridge.cycle_hud_mode(), "mini")
        bridge._hud_bridge.set_mode.assert_called_with("mini")
        self.assertEqual(bridge.cycle_hud_mode(), "normal")

        # Per-game HUD preference: 'dot' while running -> restores 'normal' when game closes
        self.gm.set_hud_preference("elite_dangerous", "dot")
        bridge._apply_game_hud_preference("elite_dangerous", is_running=True)
        self.assertEqual(bridge._current_hud_mode, "dot")
        bridge._hud_bridge.set_mode.assert_called_with("dot")
        # Global saved config hud_mode must NOT have been overwritten by the temporary game override
        self.assertEqual(bridge._config["ui"]["hud_mode"], "normal")

        bridge._apply_game_hud_preference("elite_dangerous", is_running=False)
        self.assertEqual(bridge._current_hud_mode, "normal")
        self.assertTrue(bridge._overlay_visible)

        # Per-game HUD preference: 'none' hides overlay while running -> restores when game exits
        self.gm.set_hud_preference("elite_dangerous", "none")
        bridge._apply_game_hud_preference("elite_dangerous", is_running=True)
        self.assertFalse(bridge._overlay_visible)
        bridge._apply_game_hud_preference("elite_dangerous", is_running=False)
        self.assertTrue(bridge._overlay_visible)

    def test_startup_dot_mode_does_not_call_hide_on_unshown_pywebview_window(self):
        from core.gui_bridge import GuiBridge
        from types import SimpleNamespace

        os.environ["AETHER_TESTING"] = "1"
        unshown_ev = threading.Event()  # NOT set -> mimics hidden=True pywebview window at startup!
        unloaded_ev = threading.Event()
        unshown_overlay = MagicMock()
        unshown_overlay.native = MagicMock()
        unshown_overlay.events = SimpleNamespace(shown=unshown_ev, loaded=unloaded_ev)
        unshown_overlay.hide.side_effect = AssertionError("Must never call hide() on an unshown pywebview window!")
        unshown_overlay.evaluate_js.side_effect = AssertionError("Must never call evaluate_js() on an unshown/unloaded overlay!")

        bridge = GuiBridge.__new__(GuiBridge)
        bridge._config_lock = threading.RLock()
        bridge._config = sync_config_schema({"ui": {"hud_mode": "dot", "floating_overlay": "always"}})
        bridge._config_path = os.path.join(self._tmp.name, "config.json")
        bridge._current_hud_mode = "dot"
        bridge._overlay_visible = False
        bridge._overlay_window = unshown_overlay
        bridge._hud_bridge = MagicMock()
        bridge._dot_overlay = None
        bridge._engine = None
        bridge.game_mgr = self.gm
        bridge._is_shutting_down = False
        bridge._js_lock = threading.Lock()
        bridge._on_engine_event = MagicMock()
        bridge.get_raw_config = lambda: bridge._config

        # Startup applyWindowsTheme() calls set_overlay_theme() before overlay is shown — must not deadlock!
        theme_res = bridge.set_overlay_theme("dark")
        self.assertTrue(theme_res["success"])
        unshown_overlay.evaluate_js.assert_not_called()

        # hide_overlay() before shown must NOT call overlay.hide()
        bridge.hide_overlay()
        unshown_overlay.hide.assert_not_called()

        # show_overlay() in dot mode displays the circular WinForms Dot without flashing/hiding unshown WebView2 overlay
        res = bridge.show_overlay()
        self.assertTrue(res["success"])
        self.assertTrue(bridge._overlay_visible)
        unshown_overlay.show.assert_not_called()
        unshown_overlay.hide.assert_not_called()
        bridge._hud_bridge.set_mode.assert_called_with("dot")

    def test_frontend_hooks_present(self):
        html = (REPO_ROOT / "ui" / "static" / "index.html").read_text(encoding="utf-8")
        overlay = (REPO_ROOT / "ui" / "static" / "overlay.html").read_text(encoding="utf-8")
        app_js = (REPO_ROOT / "ui" / "static" / "app.js").read_text(encoding="utf-8")
        games_js = (REPO_ROOT / "ui" / "static" / "games_tab.js").read_text(encoding="utf-8")

        for el_id in (
            "dotPositionSelect",
            "dotMonitorSelect",
            "dotSizeSlider",
            "hudColorStandby",
            "hudColorListening",
            "hudColorThinking",
            "hudColorExecuting",
            "hudColorSpeaking",
            "btnResetHudColors",
            "select-game-hud-pref",
        ):
            self.assertIn(el_id, html, f"Missing element id '{el_id}' in index.html")

        self.assertIn("applyCustomStatusColors", overlay)
        self.assertIn("modeDotBtn", overlay)
        self.assertIn("preview_hud_settings", app_js)
        self.assertIn("set_game_hud_preference", games_js)


if __name__ == "__main__":
    unittest.main()
