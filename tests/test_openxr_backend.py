"""
Tests for Phase 3 OpenXR Overlay Backend & Multi-Runtime Composite Selector:
- OpenXR runtime classification (SteamVR, Meta Quest Link, VDXR, WMR, Varjo, Monado)
- 3x4 affine matrix -> OpenXR XrPosef (quaternion xyzw + position xyz) conversion
- CompositeVRBackend auto-selection, failover, and forced runtime preference
- Config schema normalization & VROverlayManager diagnostics
"""

import math
import os
import unittest
from unittest.mock import MagicMock

from core.config_manager import sync_config_schema
from ui.openxr_overlay_backend import (
    AETHER_XR_HEADER_SIZE,
    AETHER_XR_SHM_MAGIC,
    AetherVRSharedHeader,
    CompositeVRBackend,
    OpenXRLayerSharedMem,
    OpenXROverlayBackend,
    classify_openxr_runtime_path,
    detect_openxr_runtime,
    get_openxr_layer_paths,
    is_openxr_layer_installed,
    mat34_to_xr_pose,
    normalize_vr_runtime,
)
from ui.vr_overlay_manager import (
    VROverlayBackend,
    VROverlayManager,
    _quat_to_mat33,
    apply_local_euler_deg,
)


class _StubBackend(VROverlayBackend):
    def __init__(self, name: str, available: bool = True, active: bool = False):
        self.name = name
        self._available = available
        self._active = active
        self.connected = False
        self.shown = set()

    def is_available(self) -> bool:
        return self._available

    def is_runtime_active(self) -> bool:
        return self._available and self._active

    def connect(self) -> bool:
        if not self.is_runtime_active():
            return False
        self.connected = True
        return True

    def disconnect(self) -> None:
        self.connected = False
        self.shown.clear()

    def poll_events(self) -> bool:
        return self.connected

    def ensure_overlay(self, key: str, name: str, sort_order: int = 100) -> bool:
        return self.connected

    def show_overlay(self, key: str) -> None:
        self.shown.add(key)

    def hide_overlay(self, key: str) -> None:
        self.shown.discard(key)


class TestOpenXRBackendAndCompositeSelector(unittest.TestCase):
    def test_runtime_classification_and_normalization(self):
        self.assertEqual(normalize_vr_runtime("steamvr"), "openvr")
        self.assertEqual(normalize_vr_runtime("xr"), "openxr")
        self.assertEqual(normalize_vr_runtime("automatic"), "auto")
        self.assertEqual(normalize_vr_runtime("invalid"), "auto")

        self.assertEqual(classify_openxr_runtime_path(r"C:\Steam\SteamVR\steamxr_win64.json"), "SteamVR OpenXR")
        self.assertEqual(classify_openxr_runtime_path(r"C:\Program Files\Virtual Desktop Streamer\OpenXR\vdxr_win64.json"), "Virtual Desktop (VDXR)")
        self.assertEqual(classify_openxr_runtime_path(r"C:\Program Files\Oculus\Support\oculus-runtime\oculus_openxr_64.json"), "Meta Quest Link")
        self.assertEqual(classify_openxr_runtime_path(r"C:\Windows\System32\MixedRealityRuntime.json"), "Windows Mixed Reality")
        self.assertEqual(classify_openxr_runtime_path(r"/usr/share/openxr/1/openxr_monado.json"), "Monado OpenXR")
        self.assertEqual(classify_openxr_runtime_path(""), "None")

        # detect_openxr_runtime always returns well-formed dict
        info = detect_openxr_runtime()
        self.assertIn("configured", info)
        self.assertIn("name", info)
        self.assertIn("path", info)

    def test_mat34_to_xr_pose_roundtrip(self):
        base = [
            [1.0, 0.0, 0.0, 0.25],
            [0.0, 1.0, 0.0, -0.15],
            [0.0, 0.0, 1.0, -1.25],
        ]
        rotated = apply_local_euler_deg(base, yaw_deg=25.0, pitch_deg=-15.0, roll_deg=10.0)
        (qx, qy, qz, qw), (px, py, pz) = mat34_to_xr_pose(rotated)

        self.assertAlmostEqual(px, 0.25, places=5)
        self.assertAlmostEqual(py, -0.15, places=5)
        self.assertAlmostEqual(pz, -1.25, places=5)
        self.assertAlmostEqual(math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw), 1.0, places=5)

        # Round-trip quaternion back to 3x3 matrix and verify match
        r33 = _quat_to_mat33((qw, qx, qy, qz))
        for r in range(3):
            for c in range(3):
                self.assertAlmostEqual(r33[r][c], rotated[r][c], places=4)

    def test_composite_auto_selection_failover_and_preference_switch(self):
        # 1. Both active in auto -> prefers OpenVR
        ovr = _StubBackend("openvr", available=True, active=True)
        oxr = _StubBackend("openxr", available=True, active=True)
        comp = CompositeVRBackend(preference="auto", openvr_backend=ovr, openxr_backend=oxr)
        self.assertTrue(comp.connect())
        self.assertEqual(comp.name, "openvr")
        self.assertTrue(ovr.connected)
        self.assertFalse(oxr.connected)

        # 2. Switch preference to 'openxr' -> disconnects OpenVR and reconnects OpenXR
        needs_reconnect = comp.set_preference("openxr")
        self.assertTrue(needs_reconnect)
        self.assertFalse(ovr.connected)
        self.assertTrue(comp.connect())
        self.assertEqual(comp.name, "openxr")
        self.assertTrue(oxr.connected)

        # 3. Auto mode when only OpenXR is active -> picks OpenXR seamlessly
        ovr2 = _StubBackend("openvr", available=True, active=False)
        oxr2 = _StubBackend("openxr", available=True, active=True)
        comp2 = CompositeVRBackend(preference="auto", openvr_backend=ovr2, openxr_backend=oxr2)
        self.assertTrue(comp2.connect())
        self.assertEqual(comp2.name, "openxr")
        comp2.disconnect()

    def test_openxr_backend_graceful_when_pyopenxr_missing(self):
        backend = OpenXROverlayBackend()
        diag = backend.get_diagnostics()
        self.assertIn("openxr_installed", diag)
        self.assertIn("openxr_runtime_name", diag)
        if not diag["openxr_installed"]:
            self.assertFalse(backend.is_runtime_active())
            self.assertFalse(backend.connect())

    def test_config_schema_vr_runtime_and_manager_diagnostics(self):
        os.environ["AETHER_TESTING"] = "1"
        cfg = sync_config_schema({"ui": {"vr_runtime": "steamvr"}})
        self.assertEqual(cfg["ui"]["vr_runtime"], "openvr")

        cfg2 = sync_config_schema({"ui": {"vr_runtime": "openxr"}})
        self.assertEqual(cfg2["ui"]["vr_runtime"], "openxr")

        mgr = VROverlayManager(vr_runtime="auto")
        st = mgr.get_status()
        self.assertEqual(st["vr_runtime_pref"], "auto")
        self.assertIn("openxr_runtime_name", st)
        self.assertIn("openxr_installed", st)
        self.assertIn("openvr_installed", st)
        mgr.destroy()

    def test_compiled_openxr_layer_dll_manifest_and_shared_mem_ipc(self):
        import ctypes
        import json
        import sys
        import time

        self.assertEqual(ctypes.sizeof(AetherVRSharedHeader), AETHER_XR_HEADER_SIZE)
        self.assertTrue(is_openxr_layer_installed())
        manifest_path, dll_path = get_openxr_layer_paths()
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["api_layer"]["name"], "XR_APILAYER_AETHER_overlay")
        self.assertEqual(manifest["api_layer"]["library_path"], ".\\aether_openxr_layer.dll")

        if sys.platform != "win32":
            return

        # Verify DLL loads and exports Khronos entry point
        dll = ctypes.WinDLL(dll_path)
        self.assertTrue(hasattr(dll, "xrNegotiateLoaderApiLayerInterface"))

        # Verify live shared-memory IPC round-trip with OpenXROverlayBackend
        tag = f"Local\\AetherOpenXRTest_{os.getpid()}"
        shm = OpenXRLayerSharedMem(tagname=tag)
        try:
            self.assertTrue(shm.open())
            self.assertEqual(shm._hdr.magic, AETHER_XR_SHM_MAGIC)
            self.assertFalse(shm.is_layer_active())

            # Simulate C++ layer writing live heartbeat + HMD pose during xrEndFrame
            shm._hdr.layer_heartbeat_ms = int(time.time() * 1000)
            shm._hdr.hmd_pose_valid = 1
            shm._hdr.hmd_pos[0], shm._hdr.hmd_pos[1], shm._hdr.hmd_pos[2] = 0.1, 1.5, -0.2
            shm._hdr.hmd_quat[0], shm._hdr.hmd_quat[1], shm._hdr.hmd_quat[2], shm._hdr.hmd_quat[3] = 0.0, 0.0, 0.0, 1.0

            backend = OpenXROverlayBackend(shm=shm)
            self.assertTrue(backend.is_runtime_active())
            self.assertTrue(backend.connect())

            hmd_pose = backend.get_hmd_pose()
            self.assertIsNotNone(hmd_pose)
            self.assertAlmostEqual(hmd_pose[0][3], 0.1, places=4)
            self.assertAlmostEqual(hmd_pose[1][3], 1.5, places=4)
            self.assertAlmostEqual(hmd_pose[2][3], -0.2, places=4)

            self.assertTrue(backend.ensure_overlay("hud", "Aether HUD"))
            backend.set_overlay_transform(
                "hud",
                "cockpit",
                [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, -0.1], [0.0, 0.0, 1.0, -1.25]],
                0.60,
            )
            backend.show_overlay("hud")
            self.assertEqual(shm._hdr.visible, 1)

            px = bytes([10, 20, 30, 255] * 64)
            backend.set_overlay_rgba("hud", px, 8, 8)
            self.assertEqual(shm._hdr.width, 8)
            self.assertEqual(shm._hdr.height, 8)
            self.assertGreater(shm._hdr.frame_seq, 0)

            backend.hide_overlay("hud")
            self.assertEqual(shm._hdr.visible, 0)
            backend.disconnect()
        finally:
            shm.close()


if __name__ == "__main__":
    unittest.main()
