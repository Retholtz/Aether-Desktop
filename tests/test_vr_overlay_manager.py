"""
Tests for Phase 2 Pluggable VR Overlay Manager (Dot + Mini/Normal/Max HUD):
- VR HUD normalizers & 3D transform math (cockpit, HMD, wrist, yaw-aligned recenter)
- Unified VROverlayManager lifecycle with a mock VROverlayBackend
- Config schema defaults & GuiBridge preview / recenter / hide wiring
"""

import math
import os
import threading
import unittest
from unittest.mock import MagicMock

from core.config_manager import sync_config_schema
from ui.vr_overlay_manager import (
    VROverlayBackend,
    VROverlayManager,
    apply_local_euler_deg,
    is_wrist_overlay_facing_user,
    normalize_vr_hud_anchor,
    normalize_vr_hud_curvature,
    normalize_vr_hud_depth,
    normalize_vr_hud_offset_x,
    normalize_vr_hud_offset_y,
    normalize_vr_hud_scale,
    normalize_vr_pitch,
    normalize_vr_roll,
    normalize_vr_wrist_smoothing,
    normalize_vr_yaw,
    resolve_vr_hud_transform,
    smooth_matrix34,
)


class _FakeVRBackend(VROverlayBackend):
    name = "fake_vr"

    def __init__(self):
        self.available = True
        self.runtime_active = True
        self.connected = False
        self.alive = True
        self.overlays = {}
        self.shown = set()
        self.transforms = {}
        self.textures = {}
        self.hmd_pose = [
            [0.0, 0.0, 1.0, 0.2],
            [0.0, 1.0, 0.0, 1.1],
            [-1.0, 0.0, 0.0, -0.3],
        ]  # Looking 90 deg left (-X)
        self.controller_pose = None

    def get_controller_pose(self, anchor: str):
        return self.controller_pose

    def is_available(self) -> bool:
        return self.available

    def is_runtime_active(self) -> bool:
        return self.runtime_active

    def connect(self) -> bool:
        self.connected = True
        return True

    def disconnect(self) -> None:
        self.connected = False
        self.shown.clear()
        self.overlays.clear()

    def poll_events(self) -> bool:
        return self.alive

    def get_hmd_pose(self):
        return self.hmd_pose

    def ensure_overlay(self, key: str, name: str, sort_order: int = 100) -> bool:
        self.overlays[key] = (name, sort_order)
        return True

    def show_overlay(self, key: str) -> None:
        self.shown.add(key)

    def hide_overlay(self, key: str) -> None:
        self.shown.discard(key)

    def set_overlay_rgba(self, key: str, rgba_bytes: bytes, width: int, height: int) -> None:
        self.textures[key] = (len(rgba_bytes), width, height)

    def set_overlay_transform(self, key: str, anchor: str, mat_3x4, width_m: float, curvature: float = 0.0) -> None:
        self.transforms[key] = (anchor, mat_3x4, width_m, curvature)


class TestVRHudMathAndManager(unittest.TestCase):
    def test_hud_normalizers(self):
        self.assertEqual(normalize_vr_hud_anchor(""), "cockpit")
        self.assertEqual(normalize_vr_hud_anchor("head_locked"), "hmd")
        self.assertEqual(normalize_vr_hud_anchor("left_wrist"), "wrist_left")
        self.assertEqual(normalize_vr_hud_depth(0.1), 0.50)
        self.assertEqual(normalize_vr_hud_depth(9.0), 3.00)
        self.assertEqual(normalize_vr_hud_scale(0.1), 0.40)
        self.assertEqual(normalize_vr_hud_scale(9.0), 2.50)
        self.assertEqual(normalize_vr_hud_offset_x(-5.0), -1.50)
        self.assertEqual(normalize_vr_hud_offset_y(5.0), 1.00)
        self.assertEqual(normalize_vr_hud_curvature(-0.5), 0.0)
        self.assertEqual(normalize_vr_hud_curvature(0.9), 0.50)

    def test_hud_widths_by_mode_and_recenter_yaw_alignment(self):
        _, w_mini = resolve_vr_hud_transform("cockpit", depth_m=1.35, hud_mode="mini", vr_scale=1.0)
        _, w_norm = resolve_vr_hud_transform("cockpit", depth_m=1.35, hud_mode="normal", vr_scale=1.0)
        _, w_max = resolve_vr_hud_transform("cockpit", depth_m=1.35, hud_mode="max", vr_scale=1.0)
        self.assertLess(w_mini, w_norm)
        self.assertLess(w_norm, w_max)

        # HMD turned 90 deg left (-X forward), at position (0.2, 1.1, -0.3)
        hmd_pose = [
            [0.0, 0.0, 1.0, 0.2],
            [0.0, 1.0, 0.0, 1.1],
            [-1.0, 0.0, 0.0, -0.3],
        ]
        mat, _ = resolve_vr_hud_transform(
            anchor="cockpit",
            depth_m=1.50,
            offset_x=0.0,
            offset_y=-0.20,
            hud_mode="normal",
            recenter_pose=hmd_pose,
        )
        # Should be 1.50 m in front of HMD along -X, 0.20 m below HMD Y, same Z
        self.assertAlmostEqual(mat[0][3], 0.2 - 1.50, places=4)
        self.assertAlmostEqual(mat[1][3], 1.1 - 0.20, places=4)
        self.assertAlmostEqual(mat[2][3], -0.3, places=4)
        # Local +X must remain horizontal (y component == 0)
        self.assertAlmostEqual(mat[1][0], 0.0, places=5)

    def test_manager_dot_to_hud_transition_recenter_and_quit(self):
        os.environ["AETHER_TESTING"] = "1"
        backend = _FakeVRBackend()
        fake_frame = (bytes(440 * 180 * 4), 440, 180)
        mgr = VROverlayManager(backend=backend, hud_capture_fn=lambda: fake_frame)

        # 1. Show Dot -> connects & uploads dot texture
        mgr.configure_dot(enabled=True, position="top_left", anchor="hmd", inset=0.65, depth_m=1.2)
        mgr.set_dot_state("listening")
        mgr.show_dot()
        mgr.step_once(now=1.0)

        self.assertTrue(backend.connected)
        self.assertIn(VROverlayManager.DOT_KEY, backend.shown)
        self.assertNotIn(VROverlayManager.HUD_KEY, backend.shown)
        self.assertIn(VROverlayManager.DOT_KEY, backend.textures)

        # 2. Switch to Normal HUD -> hides Dot, shows HUD with curvature, uploads captured frame
        mgr.hide_dot()
        mgr.configure_hud(enabled=True, mode="normal", anchor="cockpit", curvature=0.15)
        mgr.show_hud(mode="normal")
        mgr.step_once(now=2.0)

        self.assertNotIn(VROverlayManager.DOT_KEY, backend.shown)
        self.assertIn(VROverlayManager.HUD_KEY, backend.shown)
        self.assertEqual(backend.textures[VROverlayManager.HUD_KEY], (440 * 180 * 4, 440, 180))
        _, _, _, curv = backend.transforms[VROverlayManager.HUD_KEY]
        self.assertAlmostEqual(curv, 0.15, places=3)

        # 3. Recenter HUD -> snaps to HMD gaze pose
        res = mgr.recenter_hud()
        self.assertTrue(res["success"])
        mgr.step_once(now=2.2)
        _, mat, _, _ = backend.transforms[VROverlayManager.HUD_KEY]
        self.assertLess(mat[0][3], 0.0)  # Facing -X from fake HMD pose

        # 4. Mini mode -> flat curvature (0.0)
        mgr.configure_hud(mode="mini")
        mgr.step_once(now=2.4)
        _, _, _, mini_curv = backend.transforms[VROverlayManager.HUD_KEY]
        self.assertEqual(mini_curv, 0.0)

        # 5. SteamVR quit event -> disconnects cleanly
        backend.alive = False
        mgr.step_once(now=2.6)
        self.assertFalse(backend.connected)
        self.assertFalse(mgr.get_status()["connected"])

        mgr.destroy()


class TestVRHudConfigAndBridge(unittest.TestCase):
    def test_config_schema_vr_hud_defaults_and_clamping(self):
        cfg = sync_config_schema({
            "ui": {
                "vr_hud_enabled": True,
                "vr_hud_anchor": "head",
                "vr_hud_depth": 5.0,
                "vr_hud_scale": 0.1,
                "vr_hud_offset_x": 3.0,
                "vr_hud_offset_y": -2.0,
                "vr_hud_curvature": 0.9,
            }
        })
        ui = cfg["ui"]
        self.assertTrue(ui["vr_hud_enabled"])
        self.assertEqual(ui["vr_hud_anchor"], "hmd")
        self.assertEqual(ui["vr_hud_depth"], 3.00)
        self.assertEqual(ui["vr_hud_scale"], 0.40)
        self.assertEqual(ui["vr_hud_offset_x"], 1.50)
        self.assertEqual(ui["vr_hud_offset_y"], -1.00)
        self.assertEqual(ui["vr_hud_curvature"], 0.50)

    def test_gui_bridge_vr_hud_preview_recenter_and_hide(self):
        from core.gui_bridge import GuiBridge

        bridge = GuiBridge.__new__(GuiBridge)
        bridge._config_lock = threading.Lock()
        bridge._config = sync_config_schema({"ui": {"hud_mode": "normal"}})
        bridge._current_hud_mode = "normal"
        bridge._overlay_visible = True
        bridge._overlay_window = MagicMock()
        bridge._hud_bridge = MagicMock()
        bridge._hud_bridge.recenter_vr_hud.return_value = {"success": True, "connected": True}
        bridge._on_engine_event = MagicMock()
        bridge.get_raw_config = lambda: bridge._config

        res = bridge.preview_hud_settings({
            "vr_hud_enabled": True,
            "vr_hud_anchor": "cockpit",
            "vr_hud_depth": 1.6,
            "vr_hud_scale": 1.2,
            "vr_hud_offset_y": -0.25,
            "vr_hud_curvature": 0.20,
        })
        self.assertTrue(res["success"])
        bridge._hud_bridge.apply_vr_hud_layout.assert_called_once()
        kw = bridge._hud_bridge.apply_vr_hud_layout.call_args.kwargs
        self.assertEqual(kw["mode"], "normal")
        self.assertEqual(kw["vr_hud_depth"], 1.6)
        self.assertEqual(kw["vr_hud_curvature"], 0.20)

        rec = bridge.recenter_vr_hud()
        self.assertTrue(rec["success"])
        bridge._hud_bridge.recenter_vr_hud.assert_called_once()

        bridge.hide_overlay()
        bridge._hud_bridge.hide_vr_hud.assert_called_once()

    def test_openvr_backend_set_overlay_rgba_passes_byref_compatible_ctypes_array(self):
        import ctypes
        from ui.vr_overlay_manager import OpenVROverlayBackend

        backend = OpenVROverlayBackend(use_d3d11=False)
        captured = {}

        def _fake_set_overlay_raw(handle, buf, w, h, bpp):
            # Must not raise TypeError: byref() argument must be a ctypes instance
            ptr = ctypes.byref(buf)
            captured["ptr"] = ptr
            captured["len"] = len(buf)
            captured["dims"] = (w, h, bpp)

        fake_ov = MagicMock()
        fake_ov.setOverlayRaw.side_effect = _fake_set_overlay_raw
        backend._vr_overlay = fake_ov
        backend._handles["dot"] = 12345

        rgba = bytes([10, 20, 30, 255] * 16)
        backend.set_overlay_rgba("dot", rgba, 4, 4)
        self.assertEqual(captured["len"], len(rgba))
        self.assertEqual(captured["dims"], (4, 4, 4))

        # Dimension change (e.g. Mini -> Normal) must call clearOverlayTexture first
        rgba_big = bytes([10, 20, 30, 255] * 64)
        backend.set_overlay_rgba("dot", rgba_big, 8, 8)
        fake_ov.clearOverlayTexture.assert_called_once_with(12345)

    def test_d3d11_texture_uploader_handles_dot_mini_normal_max_cycle(self):
        import sys
        if sys.platform != "win32":
            return
        from ui.vr_overlay_manager import _D3D11TextureUploader

        uploader = _D3D11TextureUploader()
        try:
            # 1. Create Dot (68x68)
            p1 = uploader.upload_rgba("dot", bytes(68 * 68 * 4), 68, 68)
            self.assertIsNotNone(p1)
            # 2. Update same dimensions via UpdateSubresource (must reuse same texture pointer!)
            p1_again = uploader.upload_rgba("dot", bytes(68 * 68 * 4), 68, 68)
            self.assertEqual(p1, p1_again)
            # 3. Cycle Mini (264x84) -> Normal (660x270) -> Max (840x720) -> Mini
            for (w, h) in ((264, 84), (660, 270), (840, 720), (264, 84)):
                ptr = uploader.upload_rgba("hud", bytes(w * h * 4), w, h)
                self.assertIsNotNone(ptr)
                self.assertGreater(ptr, 0)
        finally:
            uploader.release_all()

    def test_reset_vr_settings_restores_unified_defaults(self):
        from core.gui_bridge import GuiBridge
        from ui.vr_overlay_manager import DEFAULT_VR_SETTINGS

        os.environ["AETHER_TESTING"] = "1"
        backend = _FakeVRBackend()
        mgr = VROverlayManager(backend=backend)
        mgr.configure_dot(anchor="hmd", offset_x=0.9, offset_y=-0.8, depth_m=2.5, scale=2.0)
        mgr.configure_hud(anchor="hmd", offset_x=0.9, offset_y=-0.8, depth_m=2.5, scale=2.0, curvature=0.45)

        res = mgr.reset_defaults()
        self.assertTrue(res["success"])
        st = mgr.get_status()
        self.assertEqual(st["anchor"], DEFAULT_VR_SETTINGS["vr_anchor"])
        self.assertEqual(st["hud_anchor"], DEFAULT_VR_SETTINGS["vr_anchor"])
        self.assertEqual(st["dot_x"], DEFAULT_VR_SETTINGS["vr_x"])
        self.assertEqual(st["hud_offset_x"], DEFAULT_VR_SETTINGS["vr_x"])
        self.assertEqual(st["dot_y"], DEFAULT_VR_SETTINGS["vr_y"])
        self.assertEqual(st["hud_offset_y"], DEFAULT_VR_SETTINGS["vr_y"])
        self.assertEqual(st["depth_m"], DEFAULT_VR_SETTINGS["vr_depth"])
        self.assertEqual(st["hud_depth_m"], DEFAULT_VR_SETTINGS["vr_depth"])
        self.assertEqual(st["hud_curvature"], DEFAULT_VR_SETTINGS["vr_curvature"])
        mgr.destroy()

        bridge = GuiBridge.__new__(GuiBridge)
        bridge._config_lock = threading.Lock()
        bridge._config = sync_config_schema({"ui": {"vr_hud_offset_x": 1.2, "vr_hud_depth": 2.9}})
        bridge._config_path = None
        bridge._hud_bridge = MagicMock()
        r2 = bridge.reset_vr_settings()
        self.assertTrue(r2["success"])
        self.assertEqual(bridge._config["ui"]["vr_hud_offset_x"], DEFAULT_VR_SETTINGS["vr_x"])
        self.assertEqual(bridge._config["ui"]["vr_dot_x"], DEFAULT_VR_SETTINGS["vr_x"])
        bridge._hud_bridge.reset_vr_settings.assert_called_once()

    def test_vr_euler_rotation_preserves_position_and_applies_roll(self):
        self.assertEqual(normalize_vr_yaw(120), 60.0)
        self.assertEqual(normalize_vr_pitch(-90), -45.0)
        self.assertEqual(normalize_vr_roll(90), 45.0)

        base_mat, _ = resolve_vr_hud_transform("cockpit", depth_m=1.25, offset_x=0.0, offset_y=0.0)
        rolled = apply_local_euler_deg(base_mat, yaw_deg=0.0, pitch_deg=0.0, roll_deg=30.0)
        # Translation unchanged
        self.assertAlmostEqual(rolled[0][3], base_mat[0][3], places=5)
        self.assertAlmostEqual(rolled[1][3], base_mat[1][3], places=5)
        self.assertAlmostEqual(rolled[2][3], base_mat[2][3], places=5)
        # Local +X tilted upward by sin(30 deg) = 0.5
        self.assertAlmostEqual(rolled[1][0], math.sin(math.radians(30.0)), places=4)
        self.assertAlmostEqual(rolled[0][0], math.cos(math.radians(30.0)), places=4)

    def test_wrist_palm_up_auto_hide_and_reveal(self):
        os.environ["AETHER_TESTING"] = "1"
        # HMD at origin looking straight ahead along -Z
        hmd_id = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
        ]
        # Simple local overlay facing +Z
        local_ov = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
        ]
        # Palm-up wrist held 0.4 m directly in front of eyes (z = -0.4), facing +Z toward eyes
        ctrl_palm_up = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, -0.05],
            [0.0, 0.0, 1.0, -0.40],
        ]
        # Wrist dropped at side / palm facing down (-Y) or away (-Z)
        ctrl_palm_down = [
            [1.0, 0.0, 0.0, -0.30],
            [0.0, -1.0, 0.0, -0.60],
            [0.0, 0.0, -1.0, -0.15],
        ]
        self.assertTrue(is_wrist_overlay_facing_user(hmd_id, ctrl_palm_up, local_ov, currently_visible=False))
        self.assertFalse(is_wrist_overlay_facing_user(hmd_id, ctrl_palm_down, local_ov, currently_visible=True))

        # End-to-end manager test with wrist_left + wrist_auto_hide=True
        backend = _FakeVRBackend()
        backend.hmd_pose = hmd_id
        capture_count = {"n": 0}

        def _cap():
            capture_count["n"] += 1
            return (bytes(200 * 60 * 4), 200, 60)

        mgr = VROverlayManager(backend=backend, hud_capture_fn=_cap)
        mgr.configure_hud(enabled=True, mode="mini", anchor="wrist_left", wrist_auto_hide=True)
        mgr.show_hud(mode="mini")

        # Controller dropped down -> HUD stays hidden & capture is skipped!
        backend.controller_pose = ctrl_palm_down
        mgr.step_once(now=1.0)
        self.assertNotIn(VROverlayManager.HUD_KEY, backend.shown)
        self.assertEqual(capture_count["n"], 0)

        # Now orient controller so the wrist_left local overlay normal faces directly toward HMD origin
        # Compute controller pose that places wrist overlay in front of eyes facing +Z
        local_mat, _ = resolve_vr_hud_transform("wrist_left", hud_mode="mini")
        # Transpose of 3x3 rotation of local_mat cancels local rotation -> world normal = (0, 0, +1)
        ctrl_facing_eyes = [
            [local_mat[0][0], local_mat[1][0], local_mat[2][0], 0.0],
            [local_mat[0][1], local_mat[1][1], local_mat[2][1], -0.05],
            [local_mat[0][2], local_mat[1][2], local_mat[2][2], -0.45],
        ]
        backend.controller_pose = ctrl_facing_eyes
        mgr.step_once(now=1.2)
        self.assertIn(VROverlayManager.HUD_KEY, backend.shown)
        self.assertEqual(capture_count["n"], 1)

        # Drop wrist down again -> HUD hides automatically!
        backend.controller_pose = ctrl_palm_down
        mgr.step_once(now=1.4)
        self.assertNotIn(VROverlayManager.HUD_KEY, backend.shown)
        mgr.destroy()

    def test_wrist_stabilization_damps_micro_tremor_and_preserves_orthonormality(self):
        self.assertEqual(normalize_vr_wrist_smoothing(-0.5), 0.0)
        self.assertEqual(normalize_vr_wrist_smoothing(1.5), 0.95)

        prev = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, -0.5],
        ]
        # 2 mm hand tremor + 1 deg micro-jitter
        tremor = apply_local_euler_deg(
            [
                [1.0, 0.0, 0.0, 0.002],
                [0.0, 1.0, 0.0, -0.001],
                [0.0, 0.0, 1.0, -0.5],
            ],
            roll_deg=1.0,
        )
        # 0% smoothing -> exact 1:1 tracking
        raw = smooth_matrix34(prev, tremor, smoothing=0.0, dt=0.016)
        self.assertAlmostEqual(raw[0][3], 0.002, places=6)

        # 75% smoothing -> micro-tremor heavily damped (< 5% of tremor passes in one 60Hz tick)
        damped = smooth_matrix34(prev, tremor, smoothing=0.75, dt=0.016)
        self.assertLess(abs(damped[0][3]), 0.0002)
        # Rotation columns remain unit-length
        for col_idx in range(3):
            norm = math.sqrt(sum(damped[r][col_idx] ** 2 for r in range(3)))
            self.assertAlmostEqual(norm, 1.0, places=5)

    def test_dot_first_load_uploads_after_show_and_refreshes_initial_ticks(self):
        os.environ["AETHER_TESTING"] = "1"
        events = []

        class _OrderTrackingBackend(_FakeVRBackend):
            def show_overlay(self, key: str) -> None:
                events.append(("show", key))
                super().show_overlay(key)

            def set_overlay_rgba(self, key: str, rgba_bytes: bytes, width: int, height: int) -> None:
                events.append(("rgba", key))
                super().set_overlay_rgba(key, rgba_bytes, width, height)

        backend = _OrderTrackingBackend()
        mgr = VROverlayManager(backend=backend)
        mgr.configure_dot(enabled=True, anchor="hmd")
        mgr.set_dot_state("standby")
        mgr.show_dot()

        # Tick 1: show_overlay MUST happen before set_overlay_rgba
        mgr.step_once(now=1.0)
        self.assertEqual(events[0], ("show", VROverlayManager.DOT_KEY))
        self.assertEqual(events[1], ("rgba", VROverlayManager.DOT_KEY))

        # Subsequent 2 ticks still refresh texture so SteamVR latches D3D11 even in standby
        mgr.step_once(now=1.06)
        mgr.step_once(now=1.12)
        rgba_count = sum(1 for ev in events if ev == ("rgba", VROverlayManager.DOT_KEY))
        self.assertEqual(rgba_count, 3)
        mgr.destroy()

    def test_dot_direct_xyz_and_cockpit_auto_center(self):
        os.environ["AETHER_TESTING"] = "1"
        backend = _FakeVRBackend()
        mgr = VROverlayManager(backend=backend)
        mgr.configure_dot(enabled=True, anchor="cockpit", offset_x=0.0, offset_y=0.0, depth_m=1.2)
        mgr.show_dot()
        mgr.step_once(now=1.0)

        anc, mat, _, _ = backend.transforms[VROverlayManager.DOT_KEY]
        self.assertEqual(anc, "cockpit")
        # Fake HMD is at (0.2, 1.1, -0.3) looking along -X -> (0,0) dot should sit at (0.2 - 1.2, 1.1, -0.3)
        self.assertAlmostEqual(mat[0][3], 0.2 - 1.2, places=4)
        self.assertAlmostEqual(mat[1][3], 1.1, places=4)
        self.assertAlmostEqual(mat[2][3], -0.3, places=4)
        mgr.destroy()


if __name__ == "__main__":
    unittest.main()
