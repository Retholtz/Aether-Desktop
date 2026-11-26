"""
Tests for Phase 1 VR Dot Overlay (OpenVR / SteamVR):
- Safe-FOV binocular frustum intersection
- 3D corner projection & eye-facing billboarding for HMD / Cockpit / Wrist anchors
- Straight RGBA rendering for OpenVR IVROverlay.setOverlayRaw
- Config schema normalization & HUDBridge / GuiBridge wiring
"""

import math
import os
import threading
import unittest
from unittest.mock import MagicMock

from core.config_manager import sync_config_schema
from ui.vr_dot_overlay import (
    DEFAULT_FOV_TANGENTS,
    VRDotOverlay,
    compute_safe_fov_tangents,
    normalize_vr_dot_anchor,
    normalize_vr_dot_depth,
    normalize_vr_dot_inset,
    normalize_vr_dot_scale,
    render_dot_rgba,
    resolve_vr_dot_transform,
)


class TestVRDotMathAndNormalization(unittest.TestCase):
    def test_normalizers_and_aliases(self):
        self.assertEqual(normalize_vr_dot_anchor("head_locked"), "hmd")
        self.assertEqual(normalize_vr_dot_anchor("seated"), "cockpit")
        self.assertEqual(normalize_vr_dot_anchor("left_hand"), "wrist_left")
        self.assertEqual(normalize_vr_dot_anchor("right_wrist"), "wrist_right")
        self.assertEqual(normalize_vr_dot_anchor("bogus"), "hmd")

        self.assertEqual(normalize_vr_dot_inset(0.1), 0.25)
        self.assertEqual(normalize_vr_dot_inset(1.5), 0.90)
        self.assertEqual(normalize_vr_dot_inset(0.62), 0.62)

        self.assertEqual(normalize_vr_dot_depth(0.1), 0.50)
        self.assertEqual(normalize_vr_dot_depth(10.0), 3.00)
        self.assertEqual(normalize_vr_dot_depth(1.4), 1.40)

        self.assertEqual(normalize_vr_dot_scale(0.1), 0.50)
        self.assertEqual(normalize_vr_dot_scale(5.0), 2.50)
        self.assertEqual(normalize_vr_dot_scale(1.25), 1.25)

    def test_binocular_safe_fov_overlap(self):
        # Asymmetric left/right eye frustums (typical VR canted/asymmetric lenses)
        left_proj = (-1.15, 0.85, -0.92, 0.90)
        right_proj = (-0.88, 1.18, -0.95, 0.88)
        tl, tr, tt, tb = compute_safe_fov_tangents(left_proj, right_proj)
        self.assertAlmostEqual(tl, 0.88, places=4)
        self.assertAlmostEqual(tr, 0.85, places=4)
        self.assertAlmostEqual(tt, 0.92, places=4)
        self.assertAlmostEqual(tb, 0.88, places=4)

        # Fallback when missing
        self.assertEqual(compute_safe_fov_tangents(None, None), DEFAULT_FOV_TANGENTS)

    def test_hmd_corner_positions_and_billboard_orthonormality(self):
        fov = (1.0, 1.0, 0.8, 0.8)
        depth = 1.2
        inset = 0.6

        expected_signs = {
            "top_left": (-1, +1),
            "top_right": (+1, +1),
            "bottom_left": (-1, -1),
            "bottom_right": (+1, -1),
        }
        for corner, (sx, sy) in expected_signs.items():
            mat, width_m = resolve_vr_dot_transform(
                position=corner,
                anchor="hmd",
                depth_m=depth,
                inset=inset,
                fov_tangents=fov,
                dot_size=22,
                vr_scale=1.0,
            )
            px, py, pz = mat[0][3], mat[1][3], mat[2][3]
            self.assertAlmostEqual(px, sx * depth * 1.0 * inset, places=5)
            self.assertAlmostEqual(py, sy * depth * 0.8 * inset, places=5)
            self.assertAlmostEqual(pz, -depth, places=5)
            self.assertGreater(width_m, 0.02)

            # Verify rotation columns form an orthonormal right-handed basis
            x_col = (mat[0][0], mat[1][0], mat[2][0])
            y_col = (mat[0][1], mat[1][1], mat[2][1])
            z_col = (mat[0][2], mat[1][2], mat[2][2])
            for col in (x_col, y_col, z_col):
                norm = math.sqrt(sum(c * c for c in col))
                self.assertAlmostEqual(norm, 1.0, places=5)
            # Local +Z must point directly back toward origin (0,0,0)
            to_origin = (-px, -py, -pz)
            dist = math.sqrt(sum(c * c for c in to_origin))
            expected_z = tuple(c / dist for c in to_origin)
            for a, b in zip(z_col, expected_z):
                self.assertAlmostEqual(a, b, places=5)

    def test_angular_width_scales_with_depth_and_scale(self):
        _, w1 = resolve_vr_dot_transform("top_right", "hmd", depth_m=1.0, dot_size=22, vr_scale=1.0)
        _, w2 = resolve_vr_dot_transform("top_right", "hmd", depth_m=2.0, dot_size=22, vr_scale=1.0)
        self.assertAlmostEqual(w2 / w1, 2.0, places=2)

        _, w_scaled = resolve_vr_dot_transform("top_right", "hmd", depth_m=1.0, dot_size=22, vr_scale=1.5)
        self.assertAlmostEqual(w_scaled / w1, 1.5, places=2)

    def test_wrist_anchors(self):
        mat_l, w_l = resolve_vr_dot_transform("top_right", "wrist_left", dot_size=22, vr_scale=1.0)
        mat_r, w_r = resolve_vr_dot_transform("top_right", "wrist_right", dot_size=22, vr_scale=1.0)
        self.assertGreater(mat_l[0][3], 0.0)
        self.assertLess(mat_r[0][3], 0.0)
        self.assertAlmostEqual(w_l, w_r, places=4)
        self.assertLess(w_l, 0.05)

    def test_render_dot_rgba_straight_alpha(self):
        rgba, canvas = render_dot_rgba(22, "#00d2ff", is_standby=False, pulse_phase=0.5)
        self.assertEqual(len(rgba), canvas * canvas * 4)
        # Corner pixel fully transparent
        self.assertEqual(rgba[3], 0)
        # Center pixel opaque cyan (R < 120, G > 180, B > 220, A > 240)
        mid = canvas // 2
        idx = (mid * canvas + mid) * 4
        r, g, b, a = rgba[idx], rgba[idx + 1], rgba[idx + 2], rgba[idx + 3]
        self.assertGreater(a, 240)
        self.assertGreater(b, 220)
        self.assertGreater(g, 180)
        self.assertLess(r, 120)


class TestVRDotConfigAndBridgeWiring(unittest.TestCase):
    def test_config_schema_vr_dot_defaults_and_clamping(self):
        cfg = sync_config_schema({
            "ui": {
                "hud_mode": "dot",
                "vr_dot_enabled": False,
                "vr_dot_anchor": "seated",
                "vr_dot_inset": 0.99,
                "vr_dot_depth": 0.10,
                "vr_dot_scale": 4.0,
            }
        })
        ui = cfg["ui"]
        self.assertFalse(ui["vr_dot_enabled"])
        self.assertEqual(ui["vr_dot_anchor"], "cockpit")
        self.assertEqual(ui["vr_dot_inset"], 0.90)
        self.assertEqual(ui["vr_dot_depth"], 0.50)
        self.assertEqual(ui["vr_dot_scale"], 2.50)

    def test_vr_dot_overlay_state_and_bridge_preview(self):
        os.environ["AETHER_TESTING"] = "1"
        vr_dot = VRDotOverlay(enabled=True, position="bottom_left", anchor="cockpit", inset=0.55, depth_m=1.5, scale=1.2)
        vr_dot.set_state("thinking")
        vr_dot.show()
        st = vr_dot.get_status()
        self.assertTrue(st["enabled"])
        self.assertTrue(st["visible"])
        self.assertEqual(st["anchor"], "cockpit")
        self.assertEqual(st["position"], "bottom_left")
        self.assertEqual(st["state"], "thinking")
        vr_dot.hide()
        self.assertFalse(vr_dot.get_status()["visible"])
        vr_dot.destroy()

        from core.gui_bridge import GuiBridge

        bridge = GuiBridge.__new__(GuiBridge)
        bridge._config_lock = threading.Lock()
        bridge._config = sync_config_schema({"ui": {"hud_mode": "dot"}})
        bridge._current_hud_mode = "dot"
        bridge._overlay_visible = True
        bridge._hud_bridge = MagicMock()
        bridge._on_engine_event = MagicMock()
        bridge.get_raw_config = lambda: bridge._config

        res = bridge.preview_hud_settings({
            "dot_position": "bottom_left",
            "vr_dot_enabled": True,
            "vr_dot_anchor": "wrist_left",
            "vr_dot_inset": 0.70,
            "vr_dot_depth": 1.6,
            "vr_dot_scale": 1.3,
        })
        self.assertTrue(res["success"])
        bridge._hud_bridge.apply_dot_layout.assert_called_once()
        kwargs = bridge._hud_bridge.apply_dot_layout.call_args.kwargs
        self.assertEqual(kwargs["dot_position"], "bottom_left")
        self.assertEqual(kwargs["vr_dot_anchor"], "wrist_left")
        self.assertEqual(kwargs["vr_dot_inset"], 0.70)
        self.assertEqual(kwargs["vr_dot_depth"], 1.6)
        self.assertEqual(kwargs["vr_dot_scale"], 1.3)


if __name__ == "__main__":
    unittest.main()
