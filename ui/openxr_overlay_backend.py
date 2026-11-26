"""
ui/openxr_overlay_backend.py - Native OpenXR Implicit API Layer Backend & Multi-Runtime Selector.

Provides:
1. `detect_openxr_runtime()`: Inspects `XR_RUNTIME_JSON` and Windows Registry
   `HKLM\\SOFTWARE\\Khronos\\OpenXR\\1\\ActiveRuntime` to identify the active
   OpenXR runtime (SteamVR OpenXR, Meta Quest Link, Virtual Desktop VDXR, WMR, etc.).
2. `OpenXRLayerSharedMem`: Zero-dependency Windows shared-memory IPC client matching
   `native/openxr_layer/openxr_abi.h` (`Local\\AetherOpenXROverlayV1`, 256-byte header
   + up to 1024x1024 RGBA pixels) for `assets/openxr/aether_openxr_layer.dll`.
   Works across EVERY OpenXR runtime (Meta Quest Link, Virtual Desktop VDXR,
   SteamVR OpenXR, WMR, Varjo, Pimax) without requiring `pyopenxr` or `XR_EXTX_overlay`.
3. `register_openxr_layer()` / `unregister_openxr_layer()`: Per-user HKCU OpenXR
   Implicit API Layer registration (no Admin/UAC prompt required).
4. `OpenXROverlayBackend(VROverlayBackend)` & `CompositeVRBackend(VROverlayBackend)`:
   Auto-selects between SteamVR (OpenVR native D3D11) and OpenXR (Aether Implicit
   API Layer / `XR_EXTX_overlay`) based on user preference (`auto`, `openvr`, `openxr`).
"""

from __future__ import annotations

import ctypes
import math
import mmap
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from core.logger import get_logger
from ui.vr_dot_overlay import DEFAULT_FOV_TANGENTS
from ui.vr_overlay_manager import (
    OpenVROverlayBackend,
    VROverlayBackend,
    _mat33_to_quat,
    _quat_to_mat33,
)

logger = get_logger("OpenXRBackend")

VALID_VR_RUNTIMES = ("auto", "openvr", "openxr")

# Exact shared-memory IPC constants matching native/openxr_layer/openxr_abi.h
AETHER_XR_SHM_NAME = "Local\\AetherOpenXROverlayV1"
AETHER_XR_SHM_MAGIC = 0x41455852  # "AEXR"
AETHER_XR_SHM_VERSION = 1
AETHER_XR_MAX_WIDTH = 1024
AETHER_XR_MAX_HEIGHT = 1024
AETHER_XR_HEADER_SIZE = 256
AETHER_XR_SHM_TOTAL_BYTES = AETHER_XR_HEADER_SIZE + (AETHER_XR_MAX_WIDTH * AETHER_XR_MAX_HEIGHT * 4)

_ANCHOR_TO_INT = {
    "cockpit": 0,
    "hmd": 1,
    "wrist_left": 2,
    "wrist_right": 3,
}


class AetherVRSharedHeader(ctypes.Structure):
    """Exact 256-byte packed header matching `AetherVRSharedHeader` in `openxr_abi.h`."""
    _pack_ = 1
    _fields_ = [
        ("magic", ctypes.c_uint32),
        ("version", ctypes.c_uint32),
        ("frame_seq", ctypes.c_uint32),
        ("visible", ctypes.c_uint32),
        ("anchor", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("_pad0", ctypes.c_uint32),
        ("pos", ctypes.c_float * 3),
        ("quat", ctypes.c_float * 4),
        ("width_m", ctypes.c_float),
        ("height_m", ctypes.c_float),
        ("alpha", ctypes.c_float),
        ("heartbeat_ms", ctypes.c_uint64),
        ("layer_heartbeat_ms", ctypes.c_uint64),
        ("hmd_pose_valid", ctypes.c_uint32),
        ("hmd_pos", ctypes.c_float * 3),
        ("hmd_quat", ctypes.c_float * 4),
        ("reserved", ctypes.c_uint8 * 136),
    ]


assert ctypes.sizeof(AetherVRSharedHeader) == AETHER_XR_HEADER_SIZE


def normalize_vr_runtime(runtime: Any) -> str:
    """Normalizes VR runtime preference into one of ('auto', 'openvr', 'openxr')."""
    val = str(runtime or "auto").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "steamvr": "openvr",
        "steam": "openvr",
        "xr": "openxr",
        "automatic": "auto",
    }
    val = aliases.get(val, val)
    return val if val in VALID_VR_RUNTIMES else "auto"


def is_openxr_available() -> bool:
    """Returns True if the optional `pyopenxr` (`xr`) Python package is installed."""
    try:
        import xr  # noqa: F401
        return True
    except Exception:
        return False


def get_openxr_layer_paths() -> Tuple[str, str]:
    """Returns `(manifest_json_path, dll_path)` for Aether's bundled OpenXR Implicit API Layer."""
    try:
        from core.paths import get_resource_dir
        base = str(get_resource_dir())
    except Exception:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    layer_dir = os.path.join(base, "assets", "openxr")
    return (
        os.path.join(layer_dir, "XR_APILAYER_AETHER_overlay.json"),
        os.path.join(layer_dir, "aether_openxr_layer.dll"),
    )


def is_openxr_layer_installed() -> bool:
    """Returns True if both the OpenXR manifest JSON and compiled DLL exist on disk."""
    manifest, dll = get_openxr_layer_paths()
    return os.path.isfile(manifest) and os.path.isfile(dll)


def is_openxr_layer_registered() -> bool:
    """
    Returns True if Aether's OpenXR Implicit API Layer manifest is registered and
    enabled (DWORD value == 0 per Khronos spec) in HKCU or HKLM.
    """
    if sys.platform != "win32":
        return False
    manifest, _ = get_openxr_layer_paths()
    norm_manifest = os.path.normcase(os.path.abspath(manifest))
    try:
        import winreg
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, r"SOFTWARE\Khronos\OpenXR\1\ApiLayers\Implicit") as key:
                    idx = 0
                    while True:
                        try:
                            name, val, _ = winreg.EnumValue(key, idx)
                            idx += 1
                            if os.path.normcase(os.path.abspath(name)) == norm_manifest and int(val) == 0:
                                return True
                        except OSError:
                            break
            except OSError:
                continue
    except Exception:
        pass
    return False


def register_openxr_layer(enable: bool = True) -> Dict[str, Any]:
    """
    Registers (enable=True) or unregisters (enable=False) Aether's OpenXR Implicit
    API Layer in `HKCU\\SOFTWARE\\Khronos\\OpenXR\\1\\ApiLayers\\Implicit` (no Admin required).
    """
    if sys.platform != "win32":
        return {"success": False, "error": "OpenXR layer registration is Windows-only."}
    manifest, dll = get_openxr_layer_paths()
    if enable and (not os.path.isfile(manifest) or not os.path.isfile(dll)):
        return {
            "success": False,
            "error": "aether_openxr_layer.dll not found in assets/openxr.",
            "registered": False,
        }
    try:
        import winreg
        subkey = r"SOFTWARE\Khronos\OpenXR\1\ApiLayers\Implicit"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE) as key:
            abs_manifest = os.path.abspath(manifest)
            if enable:
                # Khronos OpenXR spec: DWORD 0 = enabled, non-zero = disabled
                winreg.SetValueEx(key, abs_manifest, 0, winreg.REG_DWORD, 0)
                logger.info("Registered OpenXR Implicit API Layer (HKCU): %s", abs_manifest)
            else:
                try:
                    winreg.DeleteValue(key, abs_manifest)
                    logger.info("Unregistered OpenXR Implicit API Layer (HKCU): %s", abs_manifest)
                except FileNotFoundError:
                    pass
        return {
            "success": True,
            "registered": is_openxr_layer_registered(),
            "manifest": os.path.abspath(manifest),
        }
    except Exception as exc:
        logger.warning("Failed to update OpenXR layer registry: %s", exc)
        return {"success": False, "error": str(exc), "registered": is_openxr_layer_registered()}


def classify_openxr_runtime_path(runtime_path: Optional[str]) -> str:
    """Maps an OpenXR runtime JSON manifest path to a human-readable runtime name."""
    if not runtime_path:
        return "None"
    low = str(runtime_path).lower()
    if "steamxr" in low or "steamvr" in low:
        return "SteamVR OpenXR"
    if "vdxr" in low or "virtualdesktop" in low:
        return "Virtual Desktop (VDXR)"
    if "oculus" in low or "meta" in low:
        return "Meta Quest Link"
    if "mixedreality" in low or "wmr" in low:
        return "Windows Mixed Reality"
    if "varjo" in low:
        return "Varjo OpenXR"
    if "pimax" in low:
        return "Pimax OpenXR"
    if "monado" in low:
        return "Monado OpenXR"
    return os.path.basename(str(runtime_path)) or "OpenXR Runtime"


def detect_openxr_runtime() -> Dict[str, Any]:
    """
    Detects the configured system OpenXR runtime without initializing a VR session.
    Checks `XR_RUNTIME_JSON` env var first, then Windows Registry
    `HKLM\\SOFTWARE\\Khronos\\OpenXR\\1\\ActiveRuntime`.
    """
    env_path = os.environ.get("XR_RUNTIME_JSON", "").strip()
    if env_path:
        return {
            "configured": True,
            "path": env_path,
            "name": classify_openxr_runtime_path(env_path),
            "source": "env",
        }

    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Khronos\OpenXR\1") as key:
                val, _ = winreg.QueryValueEx(key, "ActiveRuntime")
                if val:
                    path_str = str(val).strip()
                    return {
                        "configured": True,
                        "path": path_str,
                        "name": classify_openxr_runtime_path(path_str),
                        "source": "registry",
                    }
        except Exception:
            pass

    return {"configured": False, "path": "", "name": "None", "source": "none"}


def mat34_to_xr_pose(mat_3x4: List[List[float]]) -> Tuple[Tuple[float, float, float, float], Tuple[float, float, float]]:
    """
    Converts a 3x4 row-major affine matrix into OpenXR pose tuples:
      `(quat_xyzw, pos_xyz)` where `quat_xyzw = (x, y, z, w)` matches `XrQuaternionf`
      and `pos_xyz = (px, py, pz)` matches `XrVector3f`.
    """
    w, x, y, z = _mat33_to_quat(mat_3x4)
    pos = (float(mat_3x4[0][3]), float(mat_3x4[1][3]), float(mat_3x4[2][3]))
    return ((x, y, z, w), pos)


class OpenXRLayerSharedMem:
    """
    Manages the `Local\\AetherOpenXROverlayV1` Windows shared-memory channel read by
    `aether_openxr_layer.dll` during OpenXR games' `xrEndFrame`.
    """

    def __init__(self, tagname: str = AETHER_XR_SHM_NAME):
        self._tagname = tagname
        self._mm: Optional[mmap.mmap] = None
        self._hdr: Optional[AetherVRSharedHeader] = None
        self._seq: int = 0

    def open(self) -> bool:
        if self._mm is not None and self._hdr is not None:
            return True
        if sys.platform != "win32":
            return False
        try:
            self._mm = mmap.mmap(-1, AETHER_XR_SHM_TOTAL_BYTES, tagname=self._tagname)
            self._hdr = AetherVRSharedHeader.from_buffer(self._mm)
            self._hdr.magic = AETHER_XR_SHM_MAGIC
            self._hdr.version = AETHER_XR_SHM_VERSION
            self._hdr.alpha = 1.0
            self._hdr.heartbeat_ms = int(time.time() * 1000)
            return True
        except Exception as exc:
            logger.debug("OpenXR shared memory open failed: %s", exc)
            self.close()
            return False

    def close(self) -> None:
        if self._hdr is not None:
            try:
                self._hdr.visible = 0
                self._hdr.heartbeat_ms = 0
            except Exception:
                pass
            self._hdr = None
        if self._mm is not None:
            try:
                self._mm.close()
            except Exception:
                pass
            self._mm = None

    def touch_heartbeat(self) -> None:
        if self._hdr is not None:
            self._hdr.heartbeat_ms = int(time.time() * 1000)

    def is_layer_active(self, max_age_ms: int = 3000) -> bool:
        """Returns True if a live OpenXR game has loaded `aether_openxr_layer.dll` and is submitting frames."""
        if self._hdr is None and not self.open():
            return False
        if self._hdr is None:
            return False
        self.touch_heartbeat()
        layer_ts = int(self._hdr.layer_heartbeat_ms)
        if layer_ts <= 0:
            return False
        now_ms = int(time.time() * 1000)
        return abs(now_ms - layer_ts) <= max_age_ms

    def get_hmd_pose(self) -> Optional[List[List[float]]]:
        """Reads live HMD pose in LOCAL space published by `aether_openxr_layer.dll`."""
        if self._hdr is None or not self._hdr.hmd_pose_valid:
            return None
        qx, qy, qz, qw = (
            float(self._hdr.hmd_quat[0]),
            float(self._hdr.hmd_quat[1]),
            float(self._hdr.hmd_quat[2]),
            float(self._hdr.hmd_quat[3]),
        )
        px, py, pz = (
            float(self._hdr.hmd_pos[0]),
            float(self._hdr.hmd_pos[1]),
            float(self._hdr.hmd_pos[2]),
        )
        r33 = _quat_to_mat33((qw, qx, qy, qz))
        return [
            [r33[0][0], r33[0][1], r33[0][2], px],
            [r33[1][0], r33[1][1], r33[1][2], py],
            [r33[2][0], r33[2][1], r33[2][2], pz],
        ]

    def set_visible(self, visible: bool) -> None:
        if self._hdr is not None:
            self._hdr.visible = 1 if visible else 0
            self.touch_heartbeat()

    def write_transform(self, anchor: str, mat_3x4: List[List[float]], width_m: float, height_m: float) -> None:
        if self._hdr is None:
            return
        (qx, qy, qz, qw), (px, py, pz) = mat34_to_xr_pose(mat_3x4)
        self._hdr.anchor = _ANCHOR_TO_INT.get(anchor, 0)
        self._hdr.pos[0] = px
        self._hdr.pos[1] = py
        self._hdr.pos[2] = pz
        self._hdr.quat[0] = qx
        self._hdr.quat[1] = qy
        self._hdr.quat[2] = qz
        self._hdr.quat[3] = qw
        self._hdr.width_m = float(width_m)
        self._hdr.height_m = float(height_m)
        self._hdr.alpha = 1.0
        self.touch_heartbeat()

    def write_rgba(self, rgba_bytes: bytes, width: int, height: int) -> None:
        if self._hdr is None or self._mm is None:
            return
        w, h = int(width), int(height)
        data = rgba_bytes
        if w > AETHER_XR_MAX_WIDTH or h > AETHER_XR_MAX_HEIGHT:
            try:
                from PIL import Image
                scale = min(AETHER_XR_MAX_WIDTH / float(w), AETHER_XR_MAX_HEIGHT / float(h))
                nw = max(4, int(w * scale))
                nh = max(4, int(h * scale))
                img = Image.frombytes("RGBA", (w, h), data)
                img = img.resize((nw, nh), Image.Resampling.BILINEAR)
                data = img.tobytes()
                w, h = nw, nh
            except Exception:
                return
        expected = w * h * 4
        if expected <= 0 or len(data) < expected:
            return
        self._mm[AETHER_XR_HEADER_SIZE : AETHER_XR_HEADER_SIZE + expected] = data[:expected]
        self._hdr.width = w
        self._hdr.height = h
        self._seq += 1
        self._hdr.frame_seq = self._seq
        self.touch_heartbeat()


class OpenXROverlayBackend(VROverlayBackend):
    """
    OpenXR backend implementing `VROverlayBackend` via Aether's native C++
    Implicit API Layer (`aether_openxr_layer.dll`) over shared memory.

    Works across all OpenXR runtimes (Meta Quest Link, Virtual Desktop VDXR,
    SteamVR OpenXR, WMR, Varjo, Pimax) with zero `pyopenxr` dependency.
    """

    name: str = "openxr"

    def __init__(self, shm: Optional[OpenXRLayerSharedMem] = None):
        self._shm = shm if shm is not None else OpenXRLayerSharedMem()
        self._runtime_info = detect_openxr_runtime()
        self._connected = False
        self._overlays: Dict[str, Dict[str, Any]] = {}
        self._active_key: Optional[str] = None

    def get_diagnostics(self) -> Dict[str, Any]:
        self._runtime_info = detect_openxr_runtime()
        return {
            "openxr_installed": is_openxr_available(),
            "openxr_layer_installed": is_openxr_layer_installed(),
            "openxr_layer_registered": is_openxr_layer_registered(),
            "openxr_layer_active": self._connected,
            "openxr_runtime_configured": self._runtime_info["configured"],
            "openxr_runtime_name": self._runtime_info["name"],
            "openxr_extx_overlay": is_openxr_layer_installed(),
        }

    def is_available(self) -> bool:
        return is_openxr_layer_installed() or is_openxr_available()

    def is_runtime_active(self) -> bool:
        if not is_openxr_layer_installed():
            return False
        return self._shm.is_layer_active()

    def connect(self) -> bool:
        if not self._shm.open():
            return False
        if not self._shm.is_layer_active():
            return False
        self._connected = True
        self._runtime_info = detect_openxr_runtime()
        logger.info(
            "Connected to Aether OpenXR Implicit API Layer (runtime='%s').",
            self._runtime_info.get("name", "OpenXR"),
        )
        return True

    def disconnect(self) -> None:
        self._connected = False
        self._active_key = None
        self._overlays.clear()
        self._shm.set_visible(False)

    def poll_events(self) -> bool:
        if not self._connected:
            return False
        if not self._shm.is_layer_active(max_age_ms=4000):
            logger.info("OpenXR game session ended; releasing OpenXR layer overlay.")
            return False
        return True

    def get_fov_tangents(self) -> Tuple[float, float, float, float]:
        return DEFAULT_FOV_TANGENTS

    def get_hmd_pose(self) -> Optional[List[List[float]]]:
        if not self._connected:
            return None
        return self._shm.get_hmd_pose()

    def ensure_overlay(self, key: str, name: str, sort_order: int = 100) -> bool:
        if not self._connected:
            return False
        if key not in self._overlays:
            self._overlays[key] = {
                "name": name,
                "anchor": "cockpit",
                "mat": [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, -1.25]],
                "width_m": 0.4,
                "aspect": 1.0,
            }
        return True

    def show_overlay(self, key: str) -> None:
        if key in self._overlays:
            self._active_key = key
            ov = self._overlays[key]
            self._shm.write_transform(ov["anchor"], ov["mat"], ov["width_m"], ov["width_m"] * ov["aspect"])
            self._shm.set_visible(True)

    def hide_overlay(self, key: str) -> None:
        if self._active_key == key:
            self._active_key = None
            self._shm.set_visible(False)

    def set_overlay_rgba(self, key: str, rgba_bytes: bytes, width: int, height: int) -> None:
        ov = self._overlays.get(key)
        if ov is None:
            return
        w, h = max(1, int(width)), max(1, int(height))
        ov["aspect"] = float(h) / float(w)
        if self._active_key is None or self._active_key == key:
            self._shm.write_transform(ov["anchor"], ov["mat"], ov["width_m"], ov["width_m"] * ov["aspect"])
            self._shm.write_rgba(rgba_bytes, w, h)

    def set_overlay_transform(
        self,
        key: str,
        anchor: str,
        mat_3x4: List[List[float]],
        width_m: float,
        curvature: float = 0.0,
    ) -> None:
        ov = self._overlays.get(key)
        if ov is None:
            return
        ov["anchor"] = anchor
        ov["mat"] = mat_3x4
        ov["width_m"] = float(width_m)
        if self._active_key is None or self._active_key == key:
            self._shm.write_transform(anchor, mat_3x4, float(width_m), float(width_m) * ov["aspect"])


class CompositeVRBackend(VROverlayBackend):
    """
    Smart multi-runtime VR backend that automatically selects between
    `OpenVROverlayBackend` (SteamVR) and `OpenXROverlayBackend` (OpenXR)
    according to user preference (`auto`, `openvr`, `openxr`).
    """

    def __init__(
        self,
        preference: str = "auto",
        openvr_backend: Optional[VROverlayBackend] = None,
        openxr_backend: Optional[VROverlayBackend] = None,
    ):
        self._preference = normalize_vr_runtime(preference)
        self._openvr = openvr_backend if openvr_backend is not None else OpenVROverlayBackend()
        self._openxr = openxr_backend if openxr_backend is not None else OpenXROverlayBackend()
        self._active: Optional[VROverlayBackend] = None

    @property
    def name(self) -> str:
        if self._active is not None:
            return self._active.name
        return self._preference

    def set_preference(self, preference: str) -> bool:
        """Updates runtime preference. Returns True if active backend needs reconnect."""
        new_pref = normalize_vr_runtime(preference)
        if new_pref == self._preference:
            return False
        self._preference = new_pref
        if self._active is not None and new_pref != "auto" and self._active.name != new_pref:
            self.disconnect()
            return True
        return False

    def get_preference(self) -> str:
        return self._preference

    def get_diagnostics(self) -> Dict[str, Any]:
        xr_diag = (
            self._openxr.get_diagnostics()
            if hasattr(self._openxr, "get_diagnostics")
            else {
                "openxr_installed": self._openxr.is_available(),
                "openxr_layer_installed": False,
                "openxr_layer_registered": False,
                "openxr_layer_active": False,
                "openxr_runtime_configured": False,
                "openxr_runtime_name": "None",
                "openxr_extx_overlay": False,
            }
        )
        return {
            "vr_runtime_pref": self._preference,
            "active_backend": self._active.name if self._active is not None else "none",
            "openvr_installed": self._openvr.is_available(),
            **xr_diag,
        }

    def _candidate_order(self) -> List[VROverlayBackend]:
        if self._preference == "openvr":
            return [self._openvr]
        if self._preference == "openxr":
            return [self._openxr]
        # Auto mode: prefer OpenVR (full native D3D11 overlay support in SteamVR),
        # and automatically attach via Aether's OpenXR Implicit API Layer for
        # native OpenXR games (Meta Quest Link, Virtual Desktop VDXR, WMR, etc.).
        return [self._openvr, self._openxr]

    def is_available(self) -> bool:
        return any(b.is_available() for b in self._candidate_order())

    def is_runtime_active(self) -> bool:
        for b in self._candidate_order():
            if b.is_runtime_active():
                return True
        return False

    def connect(self) -> bool:
        for b in self._candidate_order():
            if b.is_runtime_active() and b.connect():
                self._active = b
                return True
        self._active = None
        return False

    def disconnect(self) -> None:
        if self._active is not None:
            try:
                self._active.disconnect()
            except Exception:
                pass
            self._active = None

    def poll_events(self) -> bool:
        if self._active is None:
            return False
        return self._active.poll_events()

    def get_fov_tangents(self) -> Tuple[float, float, float, float]:
        if self._active is None:
            return DEFAULT_FOV_TANGENTS
        return self._active.get_fov_tangents()

    def get_hmd_pose(self) -> Optional[List[List[float]]]:
        if self._active is None:
            return None
        return self._active.get_hmd_pose()

    def get_controller_pose(self, anchor: str) -> Optional[List[List[float]]]:
        if self._active is None:
            return None
        return self._active.get_controller_pose(anchor)

    def ensure_overlay(self, key: str, name: str, sort_order: int = 100) -> bool:
        if self._active is None:
            return False
        return self._active.ensure_overlay(key, name, sort_order=sort_order)

    def show_overlay(self, key: str) -> None:
        if self._active is not None:
            self._active.show_overlay(key)

    def hide_overlay(self, key: str) -> None:
        if self._active is not None:
            self._active.hide_overlay(key)

    def set_overlay_rgba(self, key: str, rgba_bytes: bytes, width: int, height: int) -> None:
        if self._active is not None:
            self._active.set_overlay_rgba(key, rgba_bytes, width, height)

    def set_overlay_transform(
        self,
        key: str,
        anchor: str,
        mat_3x4: List[List[float]],
        width_m: float,
        curvature: float = 0.0,
    ) -> None:
        if self._active is not None:
            self._active.set_overlay_transform(key, anchor, mat_3x4, width_m, curvature=curvature)
