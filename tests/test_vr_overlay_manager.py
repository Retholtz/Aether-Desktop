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
        self.assertEqual(normalize_vr_hud_depth(0.1), 0.30)
        self.assertEqual(normalize_vr_hud_depth(9.0), 5.00)
        self.assertEqual(normalize_vr_hud_scale(0.1), 0.40)
        self.assertEqual(normalize_vr_hud_scale(9.0), 2.50)
        self.assertEqual(normalize_vr_hud_offset_x(-5.0), -3.50)
        self.assertEqual(normalize_vr_hud_offset_y(5.0), 2.50)
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
                "vr_hud_depth": 8.0,
                "vr_hud_scale": 0.1,
                "vr_hud_offset_x": 5.0,
                "vr_hud_offset_y": -4.0,
                "vr_hud_curvature": 0.9,
            }
        })
        ui = cfg["ui"]
        self.assertTrue(ui["vr_hud_enabled"])
        self.assertEqual(ui["vr_hud_anchor"], "hmd")
        self.assertEqual(ui["vr_hud_depth"], 5.00)
        self.assertEqual(ui["vr_hud_scale"], 0.40)
        self.assertEqual(ui["vr_hud_offset_x"], 3.50)
        self.assertEqual(ui["vr_hud_offset_y"], -2.50)
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

    def test_d3d11_uploader_pins_explicit_dxgi_adapter_and_fallback_hud_never_black(self):
        import sys
        import numpy as np
        from ui.vr_overlay_manager import _D3D11TextureUploader, render_vr_hud_fallback_rgba

        for mode in ("mini", "normal", "max"):
            raw, w, h = render_vr_hud_fallback_rgba(
                mode=mode,
                agent_name="Aether",
                state="listening",
                caption_lines=["You: Status report", "Aether: All systems nominal."],
            )
            self.assertEqual(len(raw), w * h * 4)
            arr = np.frombuffer(raw, dtype=np.uint8).reshape((h, w, 4))
            # Must contain visible bright text/LED pixels (never a solid black box!)
            self.assertGreater(int(arr[:, :, :3].max()), 200)
            # Outer rounded corners must be fully transparent (alpha == 0)
            self.assertEqual(int(arr[0, 0, 3]), 0)
            self.assertEqual(int(arr[0, w - 1, 3]), 0)
            self.assertEqual(int(arr[h - 1, 0, 3]), 0)
            self.assertEqual(int(arr[h - 1, w - 1, 3]), 0)
            # Center interior must be opaque
            self.assertGreater(int(arr[h // 2, w // 2, 3]), 200)

        from ui.vr_overlay_manager import _get_rounded_rect_alpha_mask
        mask = _get_rounded_rect_alpha_mask(440, 180, 14)
        self.assertEqual(int(mask[0, 0]), 0)
        self.assertEqual(int(mask[0, 439]), 0)
        self.assertEqual(int(mask[179, 0]), 0)
        self.assertEqual(int(mask[179, 439]), 0)
        self.assertEqual(int(mask[90, 220]), 255)

        if sys.platform == "win32":
            # Verify DXGI Factory1 + EnumAdapters1(0) + D3D11CreateDevice(adapter, UNKNOWN) works live
            uploader = _D3D11TextureUploader(adapter_index=0)
            try:
                ptr = uploader.upload_rgba("hud_pinned", raw, w, h)
                self.assertIsNotNone(ptr)
                self.assertGreater(ptr, 0)
            finally:
                uploader.release_all()

    def test_hud_normal_and_max_sizes_persist_across_sessions(self):
        import json
        import tempfile
        from core.gui_bridge import GuiBridge
        from ui.hud_window import HUDBridge

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = os.path.join(tmpdir, "config.json")
            bridge = GuiBridge.__new__(GuiBridge)
            bridge._config_lock = threading.Lock()
            bridge._config = sync_config_schema({"ui": {"hud_mode": "normal"}})
            bridge._config_path = cfg_path
            bridge.get_raw_config = lambda: bridge._config

            hud_b = HUDBridge(window=None, bridge=bridge)
            self.assertEqual(hud_b._normal_size, (440, 180))
            self.assertEqual(hud_b._max_size, (560, 480))

            hud_b.save_hud_size("normal", 620, 260)
            hud_b.save_hud_size("max", 740, 610)
            self.assertEqual(bridge._config["ui"]["hud_normal_size"], [620, 260])
            self.assertEqual(bridge._config["ui"]["hud_max_size"], [740, 610])

            # Verify written atomically to disk and loaded back cleanly on restart
            with open(cfg_path, "r", encoding="utf-8") as f:
                disk_cfg = sync_config_schema(json.load(f))
            bridge2 = GuiBridge.__new__(GuiBridge)
            bridge2._config_lock = threading.Lock()
            bridge2._config = disk_cfg
            bridge2._config_path = cfg_path
            bridge2.get_raw_config = lambda: bridge2._config

            hud_b2 = HUDBridge(window=None, bridge=bridge2)
            self.assertEqual(hud_b2._normal_size, (620, 260))
            self.assertEqual(hud_b2._max_size, (740, 610))

    def test_hud_per_mode_positions_persist_across_cycle_and_sessions(self):
        import json
        import tempfile
        from unittest.mock import patch
        from core.gui_bridge import GuiBridge
        from ui.hud_window import HUDBridge

        class _FakeOverlayWindow:
            def __init__(self):
                self.x = 0
                self.y = 0
                self.width = 176
                self.height = 56
                self.native = None
                self.events = None
                self.ops = []

            def resize(self, w, h):
                self.width = int(w)
                self.height = int(h)
                self.ops.append(("resize", self.width, self.height))

            def move(self, x, y):
                self.x = int(x)
                self.y = int(y)
                self.ops.append(("move", self.x, self.y))

            def show(self):
                pass

            def hide(self):
                pass

            def evaluate_js(self, _):
                pass

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("ui.hud_window._get_dpi_scale_for_window", return_value=1.0),
            patch("ui.hud_window._get_overlay_hwnd", return_value=None),
        ):
            cfg_path = os.path.join(tmpdir, "config.json")
            win = _FakeOverlayWindow()
            bridge = GuiBridge.__new__(GuiBridge)
            bridge._config_lock = threading.Lock()
            bridge._config = sync_config_schema({"ui": {"hud_mode": "mini"}})
            bridge._config_path = cfg_path
            bridge._current_hud_mode = "mini"
            bridge._overlay_window = win
            bridge._overlay_visible = True
            bridge.get_raw_config = lambda: bridge._config
            bridge._safe_hide_overlay_window = lambda: None
            bridge.on_hud_mode_changed = lambda _m: None

            hud_b = HUDBridge(window=win, bridge=bridge)
            bridge._hud_bridge = hud_b
            # Avoid creating real WinForms Dot or VR session during unit test
            hud_b.apply_dot_layout = lambda *a, **kw: {"success": True}
            hud_b.hide_dot = lambda: None
            hud_b.apply_vr_hud_layout = lambda *a, **kw: {"success": True}

            # User places Mini in upper-left, Normal mid-left, Max right-ish
            bridge.save_overlay_position(24, 24, mode="mini")
            bridge.save_overlay_position(280, 120, mode="normal")
            bridge.save_overlay_position(640, 160, mode="max")

            # Cycle Mini -> Normal -> Max -> Dot -> Mini
            for mode, exp_pos in (
                ("mini", (24, 24)),
                ("normal", (280, 120)),
                ("max", (640, 160)),
            ):
                win.ops.clear()
                hud_b.set_mode(mode)
                # Ensure resize happens BEFORE move so WinForms resize cannot snap back
                self.assertEqual(win.ops[0][0], "resize")
                self.assertEqual(win.ops[1][0], "move")
                self.assertEqual((win.x, win.y), exp_pos)

            # Cycle into Dot and back into Mini -> Mini MUST return to upper-left (24, 24)
            hud_b.set_mode("dot")
            hud_b.set_mode("mini")
            self.assertEqual((win.x, win.y), (24, 24))
            self.assertEqual((win.width, win.height), (176, 56))

            # Verify persisted on disk across restart
            with open(cfg_path, "r", encoding="utf-8") as f:
                disk_cfg = sync_config_schema(json.load(f))
            self.assertEqual(disk_cfg["ui"]["hud_positions"]["mini"], [24, 24])
            self.assertEqual(disk_cfg["ui"]["hud_positions"]["normal"], [280, 120])
            self.assertEqual(disk_cfg["ui"]["hud_positions"]["max"], [640, 160])

            # Legacy single overlay_x/overlay_y migration into active mode only
            migrated = sync_config_schema({"ui": {"hud_mode": "normal", "overlay_x": 111, "overlay_y": 222}})
            self.assertEqual(migrated["ui"]["hud_positions"]["normal"], [111, 222])
            self.assertIsNone(migrated["ui"]["hud_positions"]["mini"])
            self.assertIsNone(migrated["ui"]["hud_positions"]["max"])

            # Reset clears all saved card positions cleanly
            bridge.show_overlay = lambda: {"success": True}
            bridge.reset_overlay_position()
            self.assertEqual(bridge._config["ui"]["hud_positions"], {"mini": None, "normal": None, "max": None})

            # Verify Dot -> Mini pre-positions hidden window BEFORE show() (no 1-frame Max flash)
            bridge.save_overlay_position(24, 24, mode="mini")
            call_order = []
            orig_prepare = hud_b.prepare_overlay_bounds_before_show
            hud_b.prepare_overlay_bounds_before_show = lambda m: (call_order.append(("prepare", m)), orig_prepare(m))
            win.show = lambda: call_order.append(("show", win.x, win.y))
            bridge._on_engine_event = lambda *a, **kw: None
            bridge.set_mode("dot", persist=False)
            call_order.clear()
            bridge.set_mode("mini", persist=False)
            self.assertEqual(call_order[0], ("prepare", "mini"))
            self.assertEqual(call_order[1], ("show", 24, 24))

    def test_hmd_locked_stabilization_damps_head_tremor_and_supports_raw_zero(self):
        os.environ["AETHER_TESTING"] = "1"
        backend = _FakeVRBackend()
        # Start with upright HMD looking along -Z at origin
        backend.hmd_pose = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 1.2],
            [0.0, 0.0, 1.0, 0.0],
        ]
        mgr = VROverlayManager(backend=backend)
        mgr.configure_hud(enabled=True, anchor="hmd", hmd_smoothing=0.75, depth_m=1.25, offset_x=0.0, offset_y=0.0)
        mgr.show_hud("normal")

        # Tick 1: initializes smoothed HMD pose -> submits world-space ("cockpit") at 60Hz
        sleep_s = mgr.step_once(now=1.0)
        self.assertAlmostEqual(sleep_s, 0.016, places=3)
        anc1, mat1, _, _ = backend.transforms[VROverlayManager.HUD_KEY]
        self.assertEqual(anc1, "cockpit")

        # Tick 2: introduce 0.8 deg head tremor + 1.5mm translation jitter
        ang = math.radians(0.8)
        ca, sa = math.cos(ang), math.sin(ang)
        backend.hmd_pose = [
            [ca, 0.0, sa, 0.0015],
            [0.0, 1.0, 0.0, 1.2010],
            [-sa, 0.0, ca, 0.0],
        ]
        mgr.step_once(now=1.016)
        anc2, mat2, _, _ = backend.transforms[VROverlayManager.HUD_KEY]
        self.assertEqual(anc2, "cockpit")
        # Raw unsmoothed shift at 1.25m depth for 0.8 deg would be ~17.5mm; damped shift must be tiny
        shift_x = abs(mat2[0][3] - mat1[0][3])
        self.assertLess(shift_x, 0.003)

        # Setting hmd_smoothing = 0.0 switches back to raw hardware-locked "hmd" tracking
        mgr.configure_hud(hmd_smoothing=0.0)
        mgr.step_once(now=1.032)
        anc3, _, _, _ = backend.transforms[VROverlayManager.HUD_KEY]
        self.assertEqual(anc3, "hmd")

        # Reset defaults restores vr_hmd_smoothing = 0.60
        res = mgr.reset_defaults()
        self.assertEqual(res["defaults"]["vr_hmd_smoothing"], 0.60)
        self.assertEqual(mgr.get_status()["hmd_smoothing"], 0.60)
        mgr.destroy()

    def test_engine_stop_preserves_global_hotkey_hook_for_hud_cycle(self):
        from core.hotkey_manager import HotkeyManager

        hk = HotkeyManager()
        hk._running = True
        hk._is_key_down = True
        hk._is_hud_key_down = True
        hk._is_game_mode_key_down = True
        hk.reset_key_states()
        self.assertTrue(hk._running)
        self.assertFalse(hk._is_key_down)
        self.assertFalse(hk._is_hud_key_down)
        self.assertFalse(hk._is_game_mode_key_down)

    def test_vr_hud_position_persists_across_game_mode_toggle_and_re_show(self):
        import json
        import tempfile
        from core.gui_bridge import GuiBridge

        os.environ["AETHER_TESTING"] = "1"
        backend = _FakeVRBackend()
        backend.hmd_pose = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 1.2],
            [0.0, 0.0, 1.0, 0.0],
        ]
        mgr = VROverlayManager(
            backend=backend,
            hud_capture_fn=lambda: (b"\xff" * (16 * 16 * 4), 16, 16),
        )
        # Configure far-left X = -2.80 m (requires expanded [-3.5, 3.5] range!)
        mgr.configure_hud(
            enabled=True,
            anchor="cockpit",
            offset_x=-2.80,
            offset_y=0.40,
            depth_m=2.20,
        )
        mgr.show_hud("normal")
        mgr.step_once(now=1.0)
        self.assertIn(VROverlayManager.HUD_KEY, backend.shown)
        _, mat_before, _, _ = backend.transforms[VROverlayManager.HUD_KEY]
        self.assertAlmostEqual(mat_before[0][3], -2.80, places=3)

        # Simulate Game Mode hiding HUD and restarting it
        mgr.hide_hud()
        mgr.step_once(now=1.1)
        self.assertNotIn(VROverlayManager.HUD_KEY, backend.shown)

        # Re-show: must re-confirm transform + texture after show_overlay and restore exact X = -2.80 m
        backend.transforms.pop(VROverlayManager.HUD_KEY, None)
        mgr.show_hud("normal")
        sleep_s = mgr.step_once(now=1.2)
        self.assertAlmostEqual(sleep_s, 0.05, places=3)  # refresh burst active
        self.assertIn(VROverlayManager.HUD_KEY, backend.shown)
        _, mat_after, _, _ = backend.transforms[VROverlayManager.HUD_KEY]
        self.assertAlmostEqual(mat_after[0][3], -2.80, places=3)
        self.assertAlmostEqual(mat_after[1][3], 1.2 + 0.40, places=3)
        self.assertAlmostEqual(mat_after[2][3], -2.20, places=3)
        mgr.destroy()

        # Verify preview_hud_settings persists VR X/Y/Z straight to config.json
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = os.path.join(tmpdir, "config.json")
            bridge = GuiBridge.__new__(GuiBridge)
            bridge._config_lock = threading.Lock()
            bridge._config = sync_config_schema({"ui": {"hud_mode": "normal"}})
            bridge._config_path = cfg_path
            bridge._current_hud_mode = "normal"
            bridge._overlay_visible = False
            bridge._hud_bridge = None
            bridge.get_raw_config = lambda: bridge._config

            res = bridge.preview_hud_settings({
                "vr_hud_offset_x": -2.80,
                "vr_hud_offset_y": 1.60,
                "vr_hud_depth": 4.20,
                "vr_dot_x": -2.80,
                "vr_dot_y": 1.60,
                "vr_dot_depth": 4.20,
            })
            self.assertTrue(res["success"])
            with open(cfg_path, "r", encoding="utf-8") as f:
                disk_ui = sync_config_schema(json.load(f))["ui"]
            self.assertEqual(disk_ui["vr_hud_offset_x"], -2.80)
            self.assertEqual(disk_ui["vr_hud_offset_y"], 1.60)
            self.assertEqual(disk_ui["vr_hud_depth"], 4.20)
            self.assertEqual(disk_ui["vr_dot_x"], -2.80)
            self.assertEqual(disk_ui["vr_dot_y"], 1.60)
            self.assertEqual(disk_ui["vr_dot_depth"], 4.20)

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
