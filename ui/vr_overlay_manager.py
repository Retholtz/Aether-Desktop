"""
ui/vr_overlay_manager.py - Pluggable VR Overlay Manager (Dot + Mini/Normal/Max HUD).

Provides a runtime-agnostic `VROverlayBackend` interface and a native
`OpenVROverlayBackend` (SteamVR `IVROverlay`) that manages BOTH:
  - Aether's Status Dot (`aether.desktop.status_dot`) using safe-FOV corner math
  - Aether's Floating HUD (`aether.desktop.hud_panel`, Mini / Normal / Max) by
    capturing the live WebView2 overlay window into a curved 3D VR quad

Because the compositor backend is abstracted behind `VROverlayBackend`, Phase 3
(OpenXR `XR_EXTX_overlay` / OpenXR API Layer) can plug directly into this same
manager without duplicating any 3D math, capture, or HUD lifecycle logic.
"""

from __future__ import annotations

import math
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.logger import get_logger
from ui.dot_overlay import (
    normalize_dot_position,
    normalize_dot_size,
    resolve_status_bucket,
    resolve_status_color,
    sanitize_hud_colors,
)
from ui.vr_dot_overlay import (
    DEFAULT_FOV_TANGENTS,
    VALID_VR_DOT_ANCHORS,
    _build_billboard_matrix34,
    compute_safe_fov_tangents,
    is_openvr_available,
    normalize_vr_dot_anchor,
    normalize_vr_dot_depth,
    normalize_vr_dot_inset,
    normalize_vr_dot_scale,
    normalize_vr_dot_x,
    normalize_vr_dot_y,
    render_dot_rgba,
    resolve_vr_dot_transform,
)

logger = get_logger("VROverlayManager")

VALID_VR_HUD_ANCHORS = VALID_VR_DOT_ANCHORS  # ("hmd", "cockpit", "wrist_left", "wrist_right")

# Unified default VR overlay settings (shared across Dot, Mini, Normal, and Max)
DEFAULT_VR_SETTINGS: Dict[str, Any] = {
    "vr_enabled": True,
    "vr_anchor": "cockpit",
    "vr_x": 0.0,
    "vr_y": -0.12,
    "vr_depth": 1.25,
    "vr_scale": 1.00,
    "vr_curvature": 0.12,
    "vr_yaw": 0.0,
    "vr_pitch": 0.0,
    "vr_roll": 0.0,
    "vr_wrist_auto_hide": True,
    "vr_wrist_smoothing": 0.60,
    "vr_runtime": "auto",
}

# Base physical quad width (meters) at depth = 1.35 m and vr_hud_scale = 1.0
_HUD_BASE_WIDTH_M: Dict[str, float] = {
    "mini": 0.34,
    "normal": 0.62,
    "max": 0.78,
}


def normalize_vr_hud_anchor(anchor: Any) -> str:
    """Normalizes VR HUD anchor mode (defaults to 'cockpit' for comfortable reading)."""
    if anchor is None or str(anchor).strip() == "":
        return "cockpit"
    return normalize_vr_dot_anchor(anchor)


def normalize_vr_hud_depth(depth_m: Any) -> float:
    """Clamps VR HUD focal depth in meters to [0.50, 3.00] (default 1.35 m)."""
    try:
        val = float(depth_m)
    except (TypeError, ValueError):
        val = 1.35
    return round(max(0.50, min(3.00, val)), 2)


def normalize_vr_hud_scale(scale: Any) -> float:
    """Clamps VR HUD scale multiplier to [0.40, 2.50] (default 1.00)."""
    try:
        val = float(scale)
    except (TypeError, ValueError):
        val = 1.00
    return round(max(0.40, min(2.50, val)), 2)


def normalize_vr_hud_offset_x(offset_x: Any) -> float:
    """Clamps VR HUD horizontal offset in meters to [-1.50, 1.50] (default 0.00 m)."""
    try:
        val = float(offset_x)
    except (TypeError, ValueError):
        val = 0.0
    return round(max(-1.50, min(1.50, val)), 2)


def normalize_vr_hud_offset_y(offset_y: Any) -> float:
    """Clamps VR HUD vertical offset in meters to [-1.00, 1.00] (default -0.18 m)."""
    try:
        val = float(offset_y)
    except (TypeError, ValueError):
        val = -0.18
    return round(max(-1.00, min(1.00, val)), 2)


def normalize_vr_hud_curvature(curvature: Any) -> float:
    """Clamps VR HUD ergonomic quad curvature to [0.00, 0.50] (default 0.12)."""
    try:
        val = float(curvature)
    except (TypeError, ValueError):
        val = 0.12
    return round(max(0.0, min(0.50, val)), 2)


def normalize_vr_yaw(deg: Any) -> float:
    """Clamps VR overlay yaw (turn left/right) in degrees to [-60.0, 60.0] (default 0.0)."""
    try:
        val = float(deg)
    except (TypeError, ValueError):
        val = 0.0
    return round(max(-60.0, min(60.0, val)), 1)


def normalize_vr_pitch(deg: Any) -> float:
    """Clamps VR overlay pitch (tilt down/up) in degrees to [-45.0, 45.0] (default 0.0)."""
    try:
        val = float(deg)
    except (TypeError, ValueError):
        val = 0.0
    return round(max(-45.0, min(45.0, val)), 1)


def normalize_vr_roll(deg: Any) -> float:
    """Clamps VR overlay roll (bank/level left/right) in degrees to [-45.0, 45.0] (default 0.0)."""
    try:
        val = float(deg)
    except (TypeError, ValueError):
        val = 0.0
    return round(max(-45.0, min(45.0, val)), 1)


def normalize_vr_wrist_smoothing(val: Any) -> float:
    """Clamps VR wrist tremor stabilization factor to [0.00, 0.95] (default 0.60)."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        v = 0.60
    return round(max(0.0, min(0.95, v)), 2)


def _mat33_to_quat(m: List[List[float]]) -> Tuple[float, float, float, float]:
    """Converts upper-left 3x3 rotation matrix of a 3x4 affine matrix to unit quaternion (w, x, y, z)."""
    m00, m01, m02 = m[0][0], m[0][1], m[0][2]
    m10, m11, m12 = m[1][0], m[1][1], m[1][2]
    m20, m21, m22 = m[2][0], m[2][1], m[2][2]
    tr = m00 + m11 + m22
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        w = 0.25 * s
        x = (m21 - m12) / s
        y = (m02 - m20) / s
        z = (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(max(1e-12, 1.0 + m00 - m11 - m22)) * 2.0
        w = (m21 - m12) / s
        x = 0.25 * s
        y = (m01 + m10) / s
        z = (m02 + m20) / s
    elif m11 > m22:
        s = math.sqrt(max(1e-12, 1.0 + m11 - m00 - m22)) * 2.0
        w = (m02 - m20) / s
        x = (m01 + m10) / s
        y = 0.25 * s
        z = (m12 + m21) / s
    else:
        s = math.sqrt(max(1e-12, 1.0 + m22 - m00 - m11)) * 2.0
        w = (m10 - m01) / s
        x = (m02 + m20) / s
        y = (m12 + m21) / s
        z = 0.25 * s
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm < 1e-8:
        return (1.0, 0.0, 0.0, 0.0)
    return (w / norm, x / norm, y / norm, z / norm)


def _quat_to_mat33(q: Tuple[float, float, float, float]) -> List[List[float]]:
    w, x, y, z = q
    return [
        [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)],
        [2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)],
        [2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)],
    ]


def _quat_slerp(
    q1: Tuple[float, float, float, float],
    q2: Tuple[float, float, float, float],
    t: float,
) -> Tuple[float, float, float, float]:
    t = max(0.0, min(1.0, float(t)))
    dot = q1[0] * q2[0] + q1[1] * q2[1] + q1[2] * q2[2] + q1[3] * q2[3]
    if dot < 0.0:
        q2 = (-q2[0], -q2[1], -q2[2], -q2[3])
        dot = -dot
    if dot > 0.9995:
        res = tuple(q1[i] + t * (q2[i] - q1[i]) for i in range(4))
    else:
        theta_0 = math.acos(max(-1.0, min(1.0, dot)))
        sin_0 = math.sin(theta_0)
        s1 = math.sin((1.0 - t) * theta_0) / sin_0
        s2 = math.sin(t * theta_0) / sin_0
        res = tuple(s1 * q1[i] + s2 * q2[i] for i in range(4))
    norm = math.sqrt(sum(c * c for c in res))
    if norm < 1e-8:
        return (1.0, 0.0, 0.0, 0.0)
    return (res[0] / norm, res[1] / norm, res[2] / norm, res[3] / norm)


def smooth_matrix34(
    prev_mat: Optional[List[List[float]]],
    target_mat: List[List[float]],
    smoothing: float = 0.60,
    dt: float = 0.016,
) -> List[List[float]]:
    """
    Applies tremor-stabilizing position LERP + quaternion SLERP with a subtle
    micro-jitter deadzone from `prev_mat` toward `target_mat`.
    - `smoothing == 0.0`: instant 1:1 tracking (no smoothing).
    - `smoothing == 0.60`: absorbs minor hand tremor while following deliberate motion smoothly.
    - `smoothing == 0.95`: heavy stabilization for rock-steady reading.
    """
    s = normalize_vr_wrist_smoothing(smoothing)
    if prev_mat is None or s <= 0.0:
        return [list(row) for row in target_mat]

    dt_clamped = max(0.004, min(0.15, float(dt)))
    # Base time constant scales smoothly from ~0.02s (light) to ~0.35s (heavy)
    tau = 0.015 + (s ** 1.6) * 0.34
    base_alpha = 1.0 - math.exp(-dt_clamped / tau)

    # Micro-tremor deadzone: damp tiny <4mm / <2deg jitters even more strongly
    dx = target_mat[0][3] - prev_mat[0][3]
    dy = target_mat[1][3] - prev_mat[1][3]
    dz = target_mat[2][3] - prev_mat[2][3]
    pos_delta = math.sqrt(dx * dx + dy * dy + dz * dz)
    pos_deadzone = 0.004 * s
    pos_alpha = base_alpha * (0.18 if pos_delta < pos_deadzone else 1.0)

    q_prev = _mat33_to_quat(prev_mat)
    q_targ = _mat33_to_quat(target_mat)
    dot = abs(q_prev[0] * q_targ[0] + q_prev[1] * q_targ[1] + q_prev[2] * q_targ[2] + q_prev[3] * q_targ[3])
    ang_deg = math.degrees(2.0 * math.acos(max(-1.0, min(1.0, dot))))
    rot_deadzone = 2.2 * s
    rot_alpha = base_alpha * (0.15 if ang_deg < rot_deadzone else 1.0)

    q_out = _quat_slerp(q_prev, q_targ, rot_alpha)
    r33 = _quat_to_mat33(q_out)
    return [
        [r33[0][0], r33[0][1], r33[0][2], prev_mat[0][3] + pos_alpha * dx],
        [r33[1][0], r33[1][1], r33[1][2], prev_mat[1][3] + pos_alpha * dy],
        [r33[2][0], r33[2][1], r33[2][2], prev_mat[2][3] + pos_alpha * dz],
    ]


def _apply_euler_rad(
    mat_3x4: List[List[float]],
    yaw_rad: float = 0.0,
    pitch_rad: float = 0.0,
    roll_rad: float = 0.0,
) -> List[List[float]]:
    """Internal helper applying unclamped local intrinsic Yaw (Y), Pitch (X), Roll (Z) radians."""
    if abs(yaw_rad) < 1e-7 and abs(pitch_rad) < 1e-7 and abs(roll_rad) < 1e-7:
        return [list(row) for row in mat_3x4]

    cy, sy = math.cos(yaw_rad), math.sin(yaw_rad)
    cp, sp = math.cos(pitch_rad), math.sin(pitch_rad)
    cr, sr = math.cos(roll_rad), math.sin(roll_rad)

    ry = [[cy, 0.0, sy, 0.0], [0.0, 1.0, 0.0, 0.0], [-sy, 0.0, cy, 0.0]]
    rx = [[1.0, 0.0, 0.0, 0.0], [0.0, cp, -sp, 0.0], [0.0, sp, cp, 0.0]]
    rz = [[cr, -sr, 0.0, 0.0], [sr, cr, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
    rot = _mat34_mul(_mat34_mul(ry, rx), rz)
    return _mat34_mul(mat_3x4, rot)


def apply_local_euler_deg(
    mat_3x4: List[List[float]],
    yaw_deg: float = 0.0,
    pitch_deg: float = 0.0,
    roll_deg: float = 0.0,
) -> List[List[float]]:
    """
    Applies local intrinsic Yaw (Y), Pitch (X), and Roll (Z) rotation in degrees
    around the overlay's own center without altering its 3D translation.
    """
    return _apply_euler_rad(
        mat_3x4,
        yaw_rad=math.radians(normalize_vr_yaw(yaw_deg)),
        pitch_rad=math.radians(normalize_vr_pitch(pitch_deg)),
        roll_rad=math.radians(normalize_vr_roll(roll_deg)),
    )


def is_wrist_overlay_facing_user(
    hmd_pose: Optional[List[List[float]]],
    controller_pose: Optional[List[List[float]]],
    local_overlay_mat: List[List[float]],
    currently_visible: bool = False,
) -> bool:
    """
    Determines whether a wrist-docked overlay is turned palm-up / facing the user's
    eyes so the HUD automatically appears when glancing at your wrist and hides
    when your arm/wrist drops down. Uses hysteresis to prevent edge flicker.
    """
    if hmd_pose is None or controller_pose is None:
        return False
    try:
        world_ov = _mat34_mul(controller_pose, local_overlay_mat)
        ox, oy, oz = float(world_ov[0][3]), float(world_ov[1][3]), float(world_ov[2][3])
        nx, ny, nz = float(world_ov[0][2]), float(world_ov[1][2]), float(world_ov[2][2])
        n_len = math.sqrt(nx * nx + ny * ny + nz * nz)
        if n_len < 1e-6:
            return False
        nx, ny, nz = nx / n_len, ny / n_len, nz / n_len

        hx, hy, hz = float(hmd_pose[0][3]), float(hmd_pose[1][3]), float(hmd_pose[2][3])
        # HMD forward is local -Z
        fx, fy, fz = -float(hmd_pose[0][2]), -float(hmd_pose[1][2]), -float(hmd_pose[2][2])
        f_len = math.sqrt(fx * fx + fy * fy + fz * fz)
        if f_len < 1e-6:
            return False
        fx, fy, fz = fx / f_len, fy / f_len, fz / f_len

        # Vector from overlay to eyes
        vx, vy, vz = hx - ox, hy - oy, hz - oz
        dist = math.sqrt(vx * vx + vy * vy + vz * vz)
        if dist < 1e-4:
            return True
        vx, vy, vz = vx / dist, vy / dist, vz / dist

        # 1) Is the screen/palm side of the wrist facing the user's eyes?
        facing_dot = nx * vx + ny * vy + nz * vz
        # 2) Is the user's head looking roughly toward the wrist (not straight ahead with hand at side)?
        gaze_dot = fx * (-vx) + fy * (-vy) + fz * (-vz)

        if currently_visible:
            return facing_dot >= 0.35 and gaze_dot >= 0.30
        return facing_dot >= 0.50 and gaze_dot >= 0.45
    except Exception:
        return False


def _mat34_mul(a: List[List[float]], b: List[List[float]]) -> List[List[float]]:
    """Multiplies two 3x4 affine transform matrices (treating each as 4x4 with bottom row [0,0,0,1])."""
    out = [[0.0] * 4 for _ in range(3)]
    for r in range(3):
        for c in range(3):
            out[r][c] = a[r][0] * b[0][c] + a[r][1] * b[1][c] + a[r][2] * b[2][c]
        out[r][3] = a[r][0] * b[0][3] + a[r][1] * b[1][3] + a[r][2] * b[2][3] + a[r][3]
    return out


def _build_yaw_aligned_origin(hmd_pose: List[List[float]]) -> List[List[float]]:
    """
    Extracts the HMD's position and horizontal yaw (keeping the HUD perfectly
    level with the horizon even if the user tilts their head when recentering).
    """
    hx, hy, hz = float(hmd_pose[0][3]), float(hmd_pose[1][3]), float(hmd_pose[2][3])
    # HMD local forward is -Z -> column 2 negated
    fwd_x = -float(hmd_pose[0][2])
    fwd_z = -float(hmd_pose[2][2])
    horiz_len = math.hypot(fwd_x, fwd_z)
    if horiz_len < 1e-6:
        fwd_x, fwd_z = 0.0, -1.0
    else:
        fwd_x /= horiz_len
        fwd_z /= horiz_len
    # Local +Z points opposite to forward; local +X = world_up x local_Z
    zx, zz = -fwd_x, -fwd_z
    xx, xz = zz, -zx  # (0,1,0) x (zx, 0, zz) = (zz, 0, -zx)
    return [
        [xx, 0.0, zx, hx],
        [0.0, 1.0, 0.0, hy],
        [xz, 0.0, zz, hz],
    ]


def resolve_vr_hud_transform(
    anchor: str = "cockpit",
    depth_m: float = 1.35,
    offset_x: float = 0.0,
    offset_y: float = -0.18,
    hud_mode: str = "normal",
    vr_scale: float = 1.00,
    recenter_pose: Optional[List[List[float]]] = None,
    yaw_deg: float = 0.0,
    pitch_deg: float = 0.0,
    roll_deg: float = 0.0,
) -> Tuple[List[List[float]], float]:
    """
    Computes `(matrix_3x4, width_meters)` for the VR HUD (`mini`, `normal`, `max`).

    - `cockpit`: Anchored in seated space at `(offset_x, offset_y, -depth_m)`,
      optionally transformed by the user's yaw-aligned `recenter_pose`.
    - `hmd`: Head-locked at `(offset_x, offset_y, -depth_m)` billboarded toward eyes.
    - `wrist_left` / `wrist_right`: Compact forearm panel facing the viewer.
    """
    anc = normalize_vr_hud_anchor(anchor)
    d = normalize_vr_hud_depth(depth_m)
    ox = normalize_vr_hud_offset_x(offset_x)
    oy = normalize_vr_hud_offset_y(offset_y)
    scale = normalize_vr_hud_scale(vr_scale)
    mode = str(hud_mode or "normal").strip().lower()
    base_w = _HUD_BASE_WIDTH_M.get(mode, _HUD_BASE_WIDTH_M["normal"])

    if anc in ("wrist_left", "wrist_right"):
        x_off = 0.09 if anc == "wrist_left" else -0.09
        y_off = 0.05
        z_off = 0.08
        mat = _build_billboard_matrix34(
            pos=(x_off, y_off, z_off),
            target=(x_off * 2.0, y_off + 0.35, z_off + 0.25),
        )
        # Automatically align upright when user turns palm up:
        # 90 deg CW (-90) for left wrist, 90 deg CCW (+90) for right wrist.
        wrist_base_roll = -90.0 if anc == "wrist_left" else 90.0
        mat = _apply_euler_rad(
            mat,
            yaw_rad=math.radians(normalize_vr_yaw(yaw_deg)),
            pitch_rad=math.radians(normalize_vr_pitch(pitch_deg)),
            roll_rad=math.radians(wrist_base_roll + normalize_vr_roll(roll_deg)),
        )
        wrist_base = 0.16 if mode == "mini" else (0.24 if mode == "normal" else 0.30)
        width_m = round(wrist_base * scale, 4)
        return mat, max(0.10, min(0.60, width_m))

    local_mat = _build_billboard_matrix34(pos=(ox, oy, -d), target=(0.0, 0.0, 0.0))
    local_mat = apply_local_euler_deg(local_mat, yaw_deg=yaw_deg, pitch_deg=pitch_deg, roll_deg=roll_deg)
    if anc == "cockpit" and recenter_pose is not None:
        origin = _build_yaw_aligned_origin(recenter_pose)
        mat = _mat34_mul(origin, local_mat)
    else:
        mat = local_mat

    width_m = round(base_w * (d / 1.35) * scale, 4)
    return mat, max(0.15, min(2.50, width_m))


def capture_hwnd_rgba(hwnd: Optional[int]) -> Optional[Tuple[bytes, int, int]]:
    """
    Captures a live Win32/WebView2 window into a straight 32-bit RGBA byte buffer
    using `PrintWindow(..., PW_RENDERFULLCONTENT)` with GIL-releasing ctypes calls.
    Returns `(rgba_bytes, width, height)` or None if unavailable.
    """
    if sys.platform != "win32" or not hwnd:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        import numpy as np

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [
                ("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 3),
            ]

        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.ReleaseDC.restype = ctypes.c_int
        user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        user32.PrintWindow.restype = wintypes.BOOL

        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        gdi32.DeleteDC.restype = wintypes.BOOL
        gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(BITMAPINFO),
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteObject.restype = wintypes.BOOL

        h = int(hwnd)
        if not user32.IsWindow(h):
            return None

        rect = wintypes.RECT()
        if not user32.GetWindowRect(h, ctypes.byref(rect)):
            return None
        w = int(rect.right - rect.left)
        h_px = int(rect.bottom - rect.top)
        if w < 16 or h_px < 16 or w > 4096 or h_px > 4096:
            return None

        hdc_screen = user32.GetDC(None)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h_px  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0  # BI_RGB

        bits_ptr = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(bits_ptr), None, 0)
        if not hbmp or not bits_ptr.value:
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(None, hdc_screen)
            return None

        try:
            old_bmp = gdi32.SelectObject(hdc_mem, hbmp)
            PW_RENDERFULLCONTENT = 0x00000002
            ok = bool(user32.PrintWindow(h, hdc_mem, PW_RENDERFULLCONTENT))
            if not ok:
                ok = bool(user32.PrintWindow(h, hdc_mem, 0))
            if not ok:
                return None

            buf_size = w * h_px * 4
            raw = ctypes.string_at(bits_ptr.value, buf_size)
            bgra = np.frombuffer(raw, dtype=np.uint8).reshape((h_px, w, 4))
            # Convert BGRA -> RGBA and force opaque alpha for WebView2 surface
            rgba = np.empty_like(bgra)
            rgba[:, :, 0] = bgra[:, :, 2]
            rgba[:, :, 1] = bgra[:, :, 1]
            rgba[:, :, 2] = bgra[:, :, 0]
            rgba[:, :, 3] = 255
            return (rgba.tobytes(), w, h_px)
        finally:
            try:
                gdi32.SelectObject(hdc_mem, old_bmp)
            except Exception:
                pass
            gdi32.DeleteObject(hbmp)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(None, hdc_screen)
    except Exception:
        return None


class VROverlayBackend:
    """Runtime-agnostic interface for VR compositor overlay backends (OpenVR, OpenXR, etc.)."""

    name: str = "none"

    def is_available(self) -> bool:
        return False

    def is_runtime_active(self) -> bool:
        return False

    def connect(self) -> bool:
        return False

    def disconnect(self) -> None:
        pass

    def poll_events(self) -> bool:
        """Polls compositor events. Returns False if the VR runtime requested shutdown."""
        return True

    def get_fov_tangents(self) -> Tuple[float, float, float, float]:
        return DEFAULT_FOV_TANGENTS

    def get_hmd_pose(self) -> Optional[List[List[float]]]:
        return None

    def get_controller_pose(self, anchor: str) -> Optional[List[List[float]]]:
        return None

    def ensure_overlay(self, key: str, name: str, sort_order: int = 100) -> bool:
        return False

    def show_overlay(self, key: str) -> None:
        pass

    def hide_overlay(self, key: str) -> None:
        pass

    def set_overlay_rgba(self, key: str, rgba_bytes: bytes, width: int, height: int) -> None:
        pass

    def set_overlay_transform(
        self,
        key: str,
        anchor: str,
        mat_3x4: List[List[float]],
        width_m: float,
        curvature: float = 0.0,
    ) -> None:
        pass


class _D3D11TextureUploader:
    """
    Lightweight pure-ctypes Direct3D 11 GPU texture uploader for SteamVR's
    `IVROverlay.setOverlayTexture(TextureType_DirectX)`.

    Eliminates OpenVR `SetOverlayRaw` IPC size limits and `OverlayError_RequestFailed`
    when rendering full-resolution Normal / Max HUD panels or rapidly cycling modes.
    Verified against Windows SDK `d3d11.h` COM vtable indices:
      - IUnknown::Release = 2
      - ID3D11Device::CreateTexture2D = 5
      - ID3D11DeviceContext::UpdateSubresource = 48
      - ID3D11DeviceContext::Flush = 111
    """

    def __init__(self):
        self._device = None
        self._context = None
        self._textures: Dict[str, Tuple[Any, int, int]] = {}  # key -> (c_void_p, w, h)
        self._init_failed = False

    @staticmethod
    def _com_call(ptr, slot: int, restype, argtypes, *args):
        import ctypes
        vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[slot])
        return fn(ptr, *args)

    def _ensure_device(self) -> bool:
        if self._device is not None and self._context is not None:
            return True
        if self._init_failed or sys.platform != "win32":
            return False
        try:
            import ctypes
            from ctypes import wintypes

            d3d11 = ctypes.WinDLL("d3d11")
            dev = ctypes.c_void_p()
            ctx = ctypes.c_void_p()
            fl = wintypes.UINT()
            D3D_DRIVER_TYPE_HARDWARE = 1
            D3D11_SDK_VERSION = 7
            hr = d3d11.D3D11CreateDevice(
                None,
                D3D_DRIVER_TYPE_HARDWARE,
                None,
                0,
                None,
                0,
                D3D11_SDK_VERSION,
                ctypes.byref(dev),
                ctypes.byref(fl),
                ctypes.byref(ctx),
            )
            if hr != 0 or not dev.value or not ctx.value:
                self._init_failed = True
                logger.warning("D3D11CreateDevice failed (hr=0x%08x); falling back to SetOverlayRaw.", hr & 0xFFFFFFFF)
                return False
            self._device = dev
            self._context = ctx
            logger.info("Initialized native D3D11 GPU texture uploader for OpenVR (feature_level=0x%x).", fl.value)
            return True
        except Exception as exc:
            self._init_failed = True
            logger.warning("D3D11 uploader unavailable (%r); falling back to SetOverlayRaw.", exc)
            return False

    def upload_rgba(self, key: str, rgba_bytes: bytes, width: int, height: int) -> Optional[int]:
        """Uploads straight 32-bit RGBA bytes into a GPU `ID3D11Texture2D` and returns its pointer."""
        if not self._ensure_device():
            return None
        try:
            import ctypes
            from ctypes import wintypes

            w, h = int(width), int(height)
            expected = w * h * 4
            if w < 1 or h < 1 or len(rgba_bytes) < expected:
                return None

            c_buf = (ctypes.c_ubyte * expected).from_buffer_copy(rgba_bytes[:expected])
            existing = self._textures.get(key)
            if existing is not None and existing[1] == w and existing[2] == h:
                tex = existing[0]
                # ID3D11DeviceContext::UpdateSubresource (slot 48)
                self._com_call(
                    self._context,
                    48,
                    None,
                    [ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT],
                    tex,
                    0,
                    None,
                    ctypes.addressof(c_buf),
                    w * 4,
                    0,
                )
            else:
                if existing is not None:
                    try:
                        self._com_call(existing[0], 2, wintypes.ULONG, [])
                    except Exception:
                        pass
                    self._textures.pop(key, None)

                class DXGI_SAMPLE_DESC(ctypes.Structure):
                    _fields_ = [("Count", wintypes.UINT), ("Quality", wintypes.UINT)]

                class D3D11_TEXTURE2D_DESC(ctypes.Structure):
                    _fields_ = [
                        ("Width", wintypes.UINT),
                        ("Height", wintypes.UINT),
                        ("MipLevels", wintypes.UINT),
                        ("ArraySize", wintypes.UINT),
                        ("Format", wintypes.UINT),
                        ("SampleDesc", DXGI_SAMPLE_DESC),
                        ("Usage", wintypes.UINT),
                        ("BindFlags", wintypes.UINT),
                        ("CPUAccessFlags", wintypes.UINT),
                        ("MiscFlags", wintypes.UINT),
                    ]

                class D3D11_SUBRESOURCE_DATA(ctypes.Structure):
                    _fields_ = [
                        ("pSysMem", ctypes.c_void_p),
                        ("SysMemPitch", wintypes.UINT),
                        ("SysMemSlicePitch", wintypes.UINT),
                    ]

                DXGI_FORMAT_R8G8B8A8_UNORM = 28
                D3D11_USAGE_DEFAULT = 0
                D3D11_BIND_SHADER_RESOURCE = 8
                D3D11_BIND_RENDER_TARGET = 32

                desc = D3D11_TEXTURE2D_DESC(
                    w,
                    h,
                    1,
                    1,
                    DXGI_FORMAT_R8G8B8A8_UNORM,
                    DXGI_SAMPLE_DESC(1, 0),
                    D3D11_USAGE_DEFAULT,
                    D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET,
                    0,
                    0,
                )
                init = D3D11_SUBRESOURCE_DATA(ctypes.addressof(c_buf), w * 4, 0)
                tex = ctypes.c_void_p()
                # ID3D11Device::CreateTexture2D (slot 5)
                hr = self._com_call(
                    self._device,
                    5,
                    ctypes.c_long,
                    [ctypes.POINTER(D3D11_TEXTURE2D_DESC), ctypes.POINTER(D3D11_SUBRESOURCE_DATA), ctypes.POINTER(ctypes.c_void_p)],
                    ctypes.byref(desc),
                    ctypes.byref(init),
                    ctypes.byref(tex),
                )
                if hr != 0 or not tex.value:
                    logger.warning("D3D11 CreateTexture2D(%s, %dx%d) failed (hr=0x%08x).", key, w, h, hr & 0xFFFFFFFF)
                    return None
                self._textures[key] = (tex, w, h)

            # ID3D11DeviceContext::Flush (slot 111) so GPU texture is ready for SteamVR compositor
            self._com_call(self._context, 111, None, [])
            return int(tex.value)
        except Exception as exc:
            logger.warning("D3D11 upload_rgba(%s) error: %r", key, exc)
            return None

    def release_all(self) -> None:
        try:
            from ctypes import wintypes
            for tex, _, _ in list(self._textures.values()):
                try:
                    self._com_call(tex, 2, wintypes.ULONG, [])
                except Exception:
                    pass
            self._textures.clear()
            if self._context is not None:
                try:
                    self._com_call(self._context, 2, wintypes.ULONG, [])
                except Exception:
                    pass
                self._context = None
            if self._device is not None:
                try:
                    self._com_call(self._device, 2, wintypes.ULONG, [])
                except Exception:
                    pass
                self._device = None
        except Exception:
            pass


class OpenVROverlayBackend(VROverlayBackend):
    """Native SteamVR / OpenVR `IVROverlay` backend sharing one session for Dot & HUD."""

    name: str = "openvr"

    def __init__(self, use_d3d11: bool = True):
        self._openvr = None
        self._vr_sys = None
        self._vr_overlay = None
        self._handles: Dict[str, Any] = {}
        self._last_dims: Dict[str, Tuple[int, int]] = {}
        self._fov_tangents = DEFAULT_FOV_TANGENTS
        self._logged_errors: set = set()
        self._d3d11 = _D3D11TextureUploader() if (use_d3d11 and sys.platform == "win32") else None

    def _log_once(self, tag: str, msg: str, *args) -> None:
        if tag not in self._logged_errors:
            self._logged_errors.add(tag)
            logger.warning(msg, *args)

    def is_available(self) -> bool:
        return is_openvr_available()

    def is_runtime_active(self) -> bool:
        if not self.is_available():
            return False
        try:
            import openvr
            return bool(openvr.isRuntimeInstalled() and openvr.isHmdPresent())
        except Exception:
            return False

    def connect(self) -> bool:
        try:
            import openvr
            self._openvr = openvr
            self._vr_sys = openvr.init(openvr.VRApplication_Overlay)
            self._vr_overlay = openvr.IVROverlay()
            try:
                l_proj = self._vr_sys.getProjectionRaw(openvr.Eye_Left)
                r_proj = self._vr_sys.getProjectionRaw(openvr.Eye_Right)
                self._fov_tangents = compute_safe_fov_tangents(l_proj, r_proj)
            except Exception:
                self._fov_tangents = DEFAULT_FOV_TANGENTS
            logger.info("Connected to OpenVR overlay compositor (FOV tangents=%s).", self._fov_tangents)
            return True
        except Exception as exc:
            logger.debug("OpenVR connect skipped/retrying: %s", exc)
            self.disconnect()
            return False

    def disconnect(self) -> None:
        if self._vr_overlay is not None:
            for handle in list(self._handles.values()):
                try:
                    self._vr_overlay.hideOverlay(handle)
                except Exception:
                    pass
                try:
                    self._vr_overlay.destroyOverlay(handle)
                except Exception:
                    pass
        self._handles.clear()
        self._last_dims.clear()
        self._vr_overlay = None
        if self._d3d11 is not None:
            self._d3d11.release_all()
        if self._vr_sys is not None and self._openvr is not None:
            try:
                self._openvr.shutdown()
            except Exception:
                pass
        self._vr_sys = None

    def poll_events(self) -> bool:
        if self._vr_sys is None or self._openvr is None:
            return False
        try:
            ev = self._openvr.VREvent_t()
            while self._vr_sys.pollNextEvent(ev):
                if ev.eventType == self._openvr.VREvent_Quit:
                    logger.info("Received OpenVR quit event; releasing VR overlays.")
                    return False
            return True
        except Exception:
            return False

    def get_fov_tangents(self) -> Tuple[float, float, float, float]:
        return self._fov_tangents

    def get_hmd_pose(self) -> Optional[List[List[float]]]:
        if self._vr_sys is None or self._openvr is None:
            return None
        try:
            pose_array = (self._openvr.TrackedDevicePose_t * self._openvr.k_unMaxTrackedDeviceCount)()
            poses = self._vr_sys.getDeviceToAbsoluteTrackingPose(
                self._openvr.TrackingUniverseSeated,
                0.0,
                pose_array,
            )
            hmd = poses[self._openvr.k_unTrackedDeviceIndex_Hmd]
            if not getattr(hmd, "bPoseIsValid", False):
                return None
            m = hmd.mDeviceToAbsoluteTracking.m
            return [[float(m[r][c]) for c in range(4)] for r in range(3)]
        except Exception:
            return None

    def get_controller_pose(self, anchor: str) -> Optional[List[List[float]]]:
        if self._vr_sys is None or self._openvr is None or anchor not in ("wrist_left", "wrist_right"):
            return None
        try:
            role = (
                self._openvr.TrackedControllerRole_LeftHand
                if anchor == "wrist_left"
                else self._openvr.TrackedControllerRole_RightHand
            )
            ctrl_idx = self._vr_sys.getTrackedDeviceIndexForControllerRole(role)
            if ctrl_idx == self._openvr.k_unTrackedDeviceIndexInvalid:
                return None
            pose_array = (self._openvr.TrackedDevicePose_t * self._openvr.k_unMaxTrackedDeviceCount)()
            poses = self._vr_sys.getDeviceToAbsoluteTrackingPose(
                self._openvr.TrackingUniverseSeated,
                0.0,
                pose_array,
            )
            ctrl = poses[ctrl_idx]
            if not getattr(ctrl, "bPoseIsValid", False):
                return None
            m = ctrl.mDeviceToAbsoluteTracking.m
            return [[float(m[r][c]) for c in range(4)] for r in range(3)]
        except Exception:
            return None

    def ensure_overlay(self, key: str, name: str, sort_order: int = 100) -> bool:
        if key in self._handles:
            return True
        if self._vr_overlay is None:
            return False
        try:
            handle = self._vr_overlay.createOverlay(key, name)
            self._vr_overlay.setOverlayAlpha(handle, 1.0)
            self._vr_overlay.setOverlaySortOrder(handle, int(sort_order))
            self._handles[key] = handle
            logger.info("Created OpenVR overlay '%s' (handle=%s).", key, handle)
            return True
        except Exception as exc:
            self._log_once(f"create:{key}", "OpenVR createOverlay(%s) failed: %s", key, exc)
            return False

    def show_overlay(self, key: str) -> None:
        handle = self._handles.get(key)
        if handle is not None and self._vr_overlay is not None:
            try:
                self._vr_overlay.showOverlay(handle)
            except Exception as exc:
                self._log_once(f"show:{key}", "OpenVR showOverlay(%s) error: %s", key, exc)

    def hide_overlay(self, key: str) -> None:
        handle = self._handles.get(key)
        if handle is not None and self._vr_overlay is not None:
            try:
                self._vr_overlay.hideOverlay(handle)
            except Exception:
                pass

    def set_overlay_rgba(self, key: str, rgba_bytes: bytes, width: int, height: int) -> None:
        handle = self._handles.get(key)
        if handle is None or self._vr_overlay is None:
            return
        import ctypes

        w, h = int(width), int(height)
        data = rgba_bytes

        # Preferred path: Native D3D11 GPU texture via SetOverlayTexture (no IPC cap, crisp Normal/Max HUD)
        if self._d3d11 is not None and self._openvr is not None and hasattr(self._vr_overlay, "setOverlayTexture"):
            tex_ptr = self._d3d11.upload_rgba(key, data, w, h)
            if tex_ptr:
                try:
                    vr_tex = self._openvr.Texture_t()
                    vr_tex.handle = tex_ptr
                    vr_tex.eType = self._openvr.TextureType_DirectX
                    vr_tex.eColorSpace = self._openvr.ColorSpace_Auto
                    self._vr_overlay.setOverlayTexture(handle, vr_tex)
                    self._last_dims[key] = (w, h)
                    return
                except Exception as exc:
                    self._log_once(
                        f"d3d11:{key}",
                        "OpenVR setOverlayTexture(%s, %dx%d) error: %r; falling back to SetOverlayRaw",
                        key, w, h, exc,
                    )

        # Fallback path: SetOverlayRaw (used if D3D11 is unavailable or mocked in tests)
        prev_dims = self._last_dims.get(key)
        if prev_dims is not None and prev_dims != (w, h):
            try:
                if hasattr(self._vr_overlay, "clearOverlayTexture"):
                    self._vr_overlay.clearOverlayTexture(handle)
            except Exception:
                pass

        try:
            c_buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
            self._vr_overlay.setOverlayRaw(handle, c_buf, w, h, 4)
            self._last_dims[key] = (w, h)
        except Exception as exc:
            self._log_once(
                f"raw:{key}",
                "OpenVR setOverlayRaw(%s, %dx%d) error: %r",
                key, w, h, exc,
            )

    def _to_hmd_mat34(self, mat_3x4: List[List[float]]):
        hmd_mat = self._openvr.HmdMatrix34_t()
        for r in range(3):
            for c in range(4):
                hmd_mat.m[r][c] = float(mat_3x4[r][c])
        return hmd_mat

    def set_overlay_transform(
        self,
        key: str,
        anchor: str,
        mat_3x4: List[List[float]],
        width_m: float,
        curvature: float = 0.0,
    ) -> None:
        handle = self._handles.get(key)
        if handle is None or self._vr_overlay is None or self._vr_sys is None or self._openvr is None:
            return
        try:
            self._vr_overlay.setOverlayWidthInMeters(handle, float(width_m))
            if hasattr(self._vr_overlay, "setOverlayCurvature"):
                self._vr_overlay.setOverlayCurvature(handle, float(max(0.0, min(1.0, curvature))))

            hmd_mat = self._to_hmd_mat34(mat_3x4)
            if anchor == "cockpit":
                self._vr_overlay.setOverlayTransformAbsolute(
                    handle,
                    self._openvr.TrackingUniverseSeated,
                    hmd_mat,
                )
            elif anchor in ("wrist_left", "wrist_right"):
                role = (
                    self._openvr.TrackedControllerRole_LeftHand
                    if anchor == "wrist_left"
                    else self._openvr.TrackedControllerRole_RightHand
                )
                ctrl_idx = self._vr_sys.getTrackedDeviceIndexForControllerRole(role)
                if ctrl_idx != self._openvr.k_unTrackedDeviceIndexInvalid:
                    self._vr_overlay.setOverlayTransformTrackedDeviceRelative(handle, ctrl_idx, hmd_mat)
                else:
                    self._vr_overlay.setOverlayTransformTrackedDeviceRelative(
                        handle,
                        self._openvr.k_unTrackedDeviceIndex_Hmd,
                        hmd_mat,
                    )
            else:
                self._vr_overlay.setOverlayTransformTrackedDeviceRelative(
                    handle,
                    self._openvr.k_unTrackedDeviceIndex_Hmd,
                    hmd_mat,
                )
        except Exception as exc:
            self._log_once(f"xform:{key}", "OpenVR transform update(%s) error: %s", key, exc)


class VROverlayManager:
    """
    Unified thread-safe VR Overlay Manager for both the Status Dot and the
    Mini/Normal/Max HUD panel, backed by a pluggable `VROverlayBackend`.
    """

    DOT_KEY = "aether.desktop.status_dot"
    DOT_NAME = "Aether Status Dot"
    HUD_KEY = "aether.desktop.hud_panel"
    HUD_NAME = "Aether HUD"

    def __init__(
        self,
        backend: Optional[VROverlayBackend] = None,
        hud_capture_fn: Optional[Callable[[], Optional[Tuple[bytes, int, int]]]] = None,
        vr_runtime: str = "auto",
    ):
        self._lock = threading.Lock()
        if backend is not None:
            self._backend: VROverlayBackend = backend
        else:
            from ui.openxr_overlay_backend import CompositeVRBackend
            self._backend = CompositeVRBackend(preference=vr_runtime)
        self._hud_capture_fn = hud_capture_fn

        # Dot configuration & state (shares unified defaults with HUD so all 4 tiers sit together)
        self._dot_enabled = DEFAULT_VR_SETTINGS["vr_enabled"]
        self._dot_visible = False
        self._dot_size = 22
        self._dot_position = "top_right"
        self._dot_anchor = DEFAULT_VR_SETTINGS["vr_anchor"]
        self._dot_inset = 0.62
        self._dot_x = DEFAULT_VR_SETTINGS["vr_x"]
        self._dot_y = DEFAULT_VR_SETTINGS["vr_y"]
        self._dot_depth = DEFAULT_VR_SETTINGS["vr_depth"]
        self._dot_scale = DEFAULT_VR_SETTINGS["vr_scale"]
        self._dot_colors = sanitize_hud_colors(None)
        self._dot_state = "standby"
        self._dot_dirty = True
        self._dot_pose_dirty = True
        self._dot_recenter_requested = False
        self._dot_recenter_pose: Optional[List[List[float]]] = None

        # HUD configuration & state
        self._hud_enabled = DEFAULT_VR_SETTINGS["vr_enabled"]
        self._hud_visible = False
        self._hud_mode = "normal"
        self._hud_anchor = DEFAULT_VR_SETTINGS["vr_anchor"]
        self._hud_depth = DEFAULT_VR_SETTINGS["vr_depth"]
        self._hud_scale = DEFAULT_VR_SETTINGS["vr_scale"]
        self._hud_offset_x = DEFAULT_VR_SETTINGS["vr_x"]
        self._hud_offset_y = DEFAULT_VR_SETTINGS["vr_y"]
        self._hud_curvature = DEFAULT_VR_SETTINGS["vr_curvature"]
        self._hud_yaw = DEFAULT_VR_SETTINGS["vr_yaw"]
        self._hud_pitch = DEFAULT_VR_SETTINGS["vr_pitch"]
        self._hud_roll = DEFAULT_VR_SETTINGS["vr_roll"]
        self._wrist_auto_hide = DEFAULT_VR_SETTINGS["vr_wrist_auto_hide"]
        self._wrist_smoothing = DEFAULT_VR_SETTINGS["vr_wrist_smoothing"]
        self._hud_pose_dirty = True
        self._hud_recenter_requested = False
        self._hud_recenter_pose: Optional[List[List[float]]] = None
        self._last_dot_local_mat: Optional[List[List[float]]] = None
        self._last_hud_local_mat: Optional[List[List[float]]] = None
        self._last_dot_width_m: float = 0.04
        self._last_hud_width_m: float = 0.62
        self._smoothed_dot_world_mat: Optional[List[List[float]]] = None
        self._smoothed_hud_world_mat: Optional[List[List[float]]] = None
        self._last_step_t: Optional[float] = None
        self._dot_refresh_ticks: int = 0

        # Runtime state
        self._connected = False
        self._hmd_present = False
        self._destroyed = False
        self._dot_shown_in_vr = False
        self._hud_shown_in_vr = False
        self._dot_pulse_phase = 0.0
        self._last_dot_frame_t = 0.0
        self._last_hud_frame_t = 0.0

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def set_hud_capture_fn(self, fn: Optional[Callable[[], Optional[Tuple[bytes, int, int]]]]) -> None:
        with self._lock:
            self._hud_capture_fn = fn

    def get_status(self) -> Dict[str, Any]:
        diag = (
            self._backend.get_diagnostics()
            if hasattr(self._backend, "get_diagnostics")
            else {
                "vr_runtime_pref": "auto",
                "active_backend": self._backend.name,
                "openvr_installed": self._backend.is_available(),
                "openxr_installed": False,
                "openxr_runtime_configured": False,
                "openxr_runtime_name": "None",
                "openxr_extx_overlay": False,
            }
        )
        with self._lock:
            return {
                **diag,
                "connected": self._connected,
                "hmd_present": self._hmd_present,
                "enabled": self._dot_enabled,
                "visible": self._dot_visible,
                "anchor": self._dot_anchor,
                "position": self._dot_position,
                "inset": self._dot_inset,
                "dot_x": self._dot_x,
                "dot_y": self._dot_y,
                "depth_m": self._dot_depth,
                "scale": self._dot_scale,
                "state": self._dot_state,
                "hud_enabled": self._hud_enabled,
                "hud_visible": self._hud_visible,
                "hud_mode": self._hud_mode,
                "hud_anchor": self._hud_anchor,
                "hud_depth_m": self._hud_depth,
                "hud_scale": self._hud_scale,
                "hud_offset_x": self._hud_offset_x,
                "hud_offset_y": self._hud_offset_y,
                "hud_curvature": self._hud_curvature,
                "hud_yaw": self._hud_yaw,
                "hud_pitch": self._hud_pitch,
                "hud_roll": self._hud_roll,
                "wrist_auto_hide": self._wrist_auto_hide,
                "wrist_smoothing": self._wrist_smoothing,
            }

    def configure_dot(
        self,
        enabled: Optional[bool] = None,
        size: Optional[int] = None,
        position: Optional[str] = None,
        anchor: Optional[str] = None,
        inset: Optional[float] = None,
        offset_x: Optional[float] = None,
        offset_y: Optional[float] = None,
        depth_m: Optional[float] = None,
        scale: Optional[float] = None,
        colors: Optional[Dict[str, str]] = None,
    ) -> None:
        should_start = False
        with self._lock:
            if enabled is not None:
                self._dot_enabled = bool(enabled)
            if size is not None:
                self._dot_size = normalize_dot_size(size)
                self._dot_pose_dirty = True
            if position is not None:
                self._dot_position = normalize_dot_position(position)
                self._dot_pose_dirty = True
            if anchor is not None:
                new_anc = normalize_vr_dot_anchor(anchor)
                if new_anc != self._dot_anchor and new_anc == "cockpit":
                    self._dot_recenter_requested = True
                self._dot_anchor = new_anc
                self._dot_pose_dirty = True
            if inset is not None:
                self._dot_inset = normalize_vr_dot_inset(inset)
                self._dot_pose_dirty = True
            if offset_x is not None:
                self._dot_x = normalize_vr_dot_x(offset_x)
                self._dot_pose_dirty = True
            if offset_y is not None:
                self._dot_y = normalize_vr_dot_y(offset_y)
                self._dot_pose_dirty = True
            if depth_m is not None:
                self._dot_depth = normalize_vr_dot_depth(depth_m)
                self._dot_pose_dirty = True
            if scale is not None:
                self._dot_scale = normalize_vr_dot_scale(scale)
                self._dot_pose_dirty = True
            if colors is not None:
                self._dot_colors = sanitize_hud_colors(colors)
            self._dot_dirty = True
            should_start = (self._dot_enabled and self._dot_visible) and not self._destroyed
        if should_start and os.environ.get("AETHER_TESTING") != "1":
            self._ensure_thread_started()

    def configure_hud(
        self,
        enabled: Optional[bool] = None,
        mode: Optional[str] = None,
        anchor: Optional[str] = None,
        depth_m: Optional[float] = None,
        scale: Optional[float] = None,
        offset_x: Optional[float] = None,
        offset_y: Optional[float] = None,
        curvature: Optional[float] = None,
        yaw_deg: Optional[float] = None,
        pitch_deg: Optional[float] = None,
        roll_deg: Optional[float] = None,
        wrist_auto_hide: Optional[bool] = None,
        wrist_smoothing: Optional[float] = None,
        vr_runtime: Optional[str] = None,
    ) -> None:
        should_start = False
        with self._lock:
            if enabled is not None:
                self._hud_enabled = bool(enabled)
            if mode is not None:
                clean_m = str(mode or "normal").strip().lower()
                if clean_m in ("mini", "normal", "max"):
                    self._hud_mode = clean_m
                    self._hud_pose_dirty = True
            if anchor is not None:
                new_anc = normalize_vr_hud_anchor(anchor)
                if new_anc != self._hud_anchor and new_anc == "cockpit":
                    self._hud_recenter_requested = True
                self._hud_anchor = new_anc
                self._hud_pose_dirty = True
            if depth_m is not None:
                self._hud_depth = normalize_vr_hud_depth(depth_m)
                self._hud_pose_dirty = True
            if scale is not None:
                self._hud_scale = normalize_vr_hud_scale(scale)
                self._hud_pose_dirty = True
            if offset_x is not None:
                self._hud_offset_x = normalize_vr_hud_offset_x(offset_x)
                self._hud_pose_dirty = True
            if offset_y is not None:
                self._hud_offset_y = normalize_vr_hud_offset_y(offset_y)
                self._hud_pose_dirty = True
            if curvature is not None:
                self._hud_curvature = normalize_vr_hud_curvature(curvature)
                self._hud_pose_dirty = True
            if yaw_deg is not None:
                self._hud_yaw = normalize_vr_yaw(yaw_deg)
                self._hud_pose_dirty = True
                self._dot_pose_dirty = True
            if pitch_deg is not None:
                self._hud_pitch = normalize_vr_pitch(pitch_deg)
                self._hud_pose_dirty = True
                self._dot_pose_dirty = True
            if roll_deg is not None:
                self._hud_roll = normalize_vr_roll(roll_deg)
                self._hud_pose_dirty = True
                self._dot_pose_dirty = True
            if wrist_auto_hide is not None:
                self._wrist_auto_hide = bool(wrist_auto_hide)
            if wrist_smoothing is not None:
                self._wrist_smoothing = normalize_vr_wrist_smoothing(wrist_smoothing)
                self._hud_pose_dirty = True
                self._dot_pose_dirty = True
            if vr_runtime is not None and hasattr(self._backend, "set_preference"):
                if self._backend.set_preference(vr_runtime):
                    self._connected = False
                    self._dot_shown_in_vr = False
                    self._hud_shown_in_vr = False
                    self._dot_pose_dirty = True
                    self._hud_pose_dirty = True
            should_start = (self._hud_enabled and self._hud_visible) and not self._destroyed
        if should_start and os.environ.get("AETHER_TESTING") != "1":
            self._ensure_thread_started()

    def set_dot_state(self, state: str) -> None:
        clean = str(state or "standby").strip().lower()
        with self._lock:
            if self._dot_state != clean:
                self._dot_state = clean
                self._dot_dirty = True

    def show_dot(self) -> None:
        with self._lock:
            if self._destroyed:
                return
            self._dot_visible = True
            self._dot_dirty = True
            self._dot_pose_dirty = True
            should_start = self._dot_enabled
        if should_start and os.environ.get("AETHER_TESTING") != "1":
            self._ensure_thread_started()

    def hide_dot(self) -> None:
        with self._lock:
            self._dot_visible = False
            self._dot_dirty = True

    def show_hud(self, mode: Optional[str] = None) -> None:
        with self._lock:
            if self._destroyed:
                return
            if mode in ("mini", "normal", "max"):
                self._hud_mode = mode
            self._hud_visible = True
            self._hud_pose_dirty = True
            should_start = self._hud_enabled
        if should_start and os.environ.get("AETHER_TESTING") != "1":
            self._ensure_thread_started()

    def hide_hud(self) -> None:
        with self._lock:
            self._hud_visible = False

    def recenter_hud(self) -> dict:
        """Recenters cockpit-anchored VR HUD and VR Dot to the user's current gaze direction."""
        with self._lock:
            self._hud_recenter_requested = True
            self._hud_pose_dirty = True
            self._dot_recenter_requested = True
            self._dot_pose_dirty = True
            connected = self._connected
        return {"success": True, "connected": connected}

    def reset_defaults(self) -> dict:
        """Resets unified VR Overlay settings (Dot + Mini/Normal/Max) to safe defaults and recenters."""
        with self._lock:
            d = DEFAULT_VR_SETTINGS
            self._dot_enabled = d["vr_enabled"]
            self._dot_anchor = d["vr_anchor"]
            self._dot_x = d["vr_x"]
            self._dot_y = d["vr_y"]
            self._dot_depth = d["vr_depth"]
            self._dot_scale = d["vr_scale"]
            self._dot_dirty = True
            self._dot_pose_dirty = True
            self._dot_recenter_requested = True

            self._hud_enabled = d["vr_enabled"]
            self._hud_anchor = d["vr_anchor"]
            self._hud_offset_x = d["vr_x"]
            self._hud_offset_y = d["vr_y"]
            self._hud_depth = d["vr_depth"]
            self._hud_scale = d["vr_scale"]
            self._hud_curvature = d["vr_curvature"]
            self._hud_yaw = d["vr_yaw"]
            self._hud_pitch = d["vr_pitch"]
            self._hud_roll = d["vr_roll"]
            self._wrist_auto_hide = d["vr_wrist_auto_hide"]
            self._wrist_smoothing = d["vr_wrist_smoothing"]
            self._smoothed_dot_world_mat = None
            self._smoothed_hud_world_mat = None
            self._hud_pose_dirty = True
            self._hud_recenter_requested = True
            connected = self._connected
        return {"success": True, "connected": connected, "defaults": dict(DEFAULT_VR_SETTINGS)}

    def destroy(self) -> None:
        with self._lock:
            self._dot_visible = False
            self._hud_visible = False
            self._destroyed = True
        self._stop_event.set()
        t = self._thread
        if t is not None and t.is_alive() and threading.current_thread() is not t:
            try:
                t.join(timeout=1.5)
            except Exception:
                pass
        try:
            self._backend.disconnect()
        except Exception:
            pass
        with self._lock:
            self._connected = False

    def _ensure_thread_started(self) -> None:
        if not self._backend.is_available():
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="AetherVROverlayManager",
        )
        self._thread.start()

    def step_once(self, now: Optional[float] = None) -> float:
        """
        Executes a single VR overlay pump tick (useful for deterministic unit tests
        as well as the background loop). Returns suggested sleep duration in seconds.
        """
        now_t = time.monotonic() if now is None else float(now)

        with self._lock:
            want_dot = self._dot_enabled and self._dot_visible
            want_hud = self._hud_enabled and self._hud_visible
            dot_dirty = self._dot_dirty
            dot_pose_dirty = self._dot_pose_dirty
            dot_size = self._dot_size
            dot_pos = self._dot_position
            dot_anchor = self._dot_anchor
            dot_inset = self._dot_inset
            dot_x = self._dot_x
            dot_y = self._dot_y
            dot_depth = self._dot_depth
            dot_scale = self._dot_scale
            dot_colors = dict(self._dot_colors)
            dot_state = self._dot_state
            dot_recenter_req = self._dot_recenter_requested
            dot_recenter_pose = self._dot_recenter_pose

            hud_mode = self._hud_mode
            hud_anchor = self._hud_anchor
            hud_depth = self._hud_depth
            hud_scale = self._hud_scale
            hud_ox = self._hud_offset_x
            hud_oy = self._hud_offset_y
            hud_curv = self._hud_curvature
            hud_yaw = self._hud_yaw
            hud_pitch = self._hud_pitch
            hud_roll = self._hud_roll
            wrist_auto_hide = self._wrist_auto_hide
            wrist_smoothing = self._wrist_smoothing
            hud_pose_dirty = self._hud_pose_dirty
            recenter_req = self._hud_recenter_requested
            recenter_pose = self._hud_recenter_pose
            capture_fn = self._hud_capture_fn

            self._dot_dirty = False
            self._dot_pose_dirty = False
            self._dot_recenter_requested = False
            self._hud_pose_dirty = False
            self._hud_recenter_requested = False

        if not want_dot and not want_hud:
            if self._connected:
                if self._dot_shown_in_vr:
                    self._backend.hide_overlay(self.DOT_KEY)
                    self._dot_shown_in_vr = False
                if self._hud_shown_in_vr:
                    self._backend.hide_overlay(self.HUD_KEY)
                    self._hud_shown_in_vr = False
            return 0.25

        if not self._connected:
            active = self._backend.is_runtime_active()
            with self._lock:
                self._hmd_present = active
            if not active:
                return 2.5
            if not self._backend.connect():
                return 3.0
            with self._lock:
                self._connected = True
            dot_dirty = True
            dot_pose_dirty = True
            hud_pose_dirty = True

        if not self._backend.poll_events():
            self._backend.disconnect()
            with self._lock:
                self._connected = False
            self._dot_shown_in_vr = False
            self._hud_shown_in_vr = False
            return 2.0

        dt = 0.016 if self._last_step_t is None else max(0.004, min(0.15, now_t - self._last_step_t))
        self._last_step_t = now_t

        # --- 1. Status Dot Overlay ---
        if want_dot:
            if self._backend.ensure_overlay(self.DOT_KEY, self.DOT_NAME, sort_order=100):
                # Auto-anchor cockpit Dot to current HMD gaze on first show (or explicit recenter)
                if dot_recenter_req or (dot_anchor == "cockpit" and dot_recenter_pose is None):
                    pose = self._backend.get_hmd_pose()
                    if pose is not None:
                        dot_recenter_pose = pose
                        recenter_pose = pose
                        with self._lock:
                            self._dot_recenter_pose = pose
                            self._hud_recenter_pose = pose
                        dot_pose_dirty = True

                if dot_pose_dirty or self._last_dot_local_mat is None:
                    fov = self._backend.get_fov_tangents()
                    mat, width_m = resolve_vr_dot_transform(
                        position=dot_pos,
                        anchor=dot_anchor,
                        depth_m=dot_depth,
                        inset=dot_inset,
                        fov_tangents=fov,
                        dot_size=dot_size,
                        vr_scale=dot_scale,
                        offset_x=dot_x,
                        offset_y=dot_y,
                        recenter_pose=dot_recenter_pose,
                    )
                    mat = apply_local_euler_deg(mat, yaw_deg=hud_yaw, pitch_deg=hud_pitch, roll_deg=hud_roll)
                    self._last_dot_local_mat = mat
                    self._last_dot_width_m = width_m
                    if not (dot_anchor in ("wrist_left", "wrist_right") and wrist_smoothing > 0.0):
                        self._smoothed_dot_world_mat = None
                        self._backend.set_overlay_transform(self.DOT_KEY, dot_anchor, mat, width_m, curvature=0.0)

                dot_should_show = True
                ctrl_p_dot = None
                if dot_anchor in ("wrist_left", "wrist_right"):
                    ctrl_p_dot = self._backend.get_controller_pose(dot_anchor)
                    if wrist_auto_hide:
                        hmd_p = self._backend.get_hmd_pose()
                        dot_should_show = is_wrist_overlay_facing_user(
                            hmd_p,
                            ctrl_p_dot,
                            self._last_dot_local_mat,
                            currently_visible=self._dot_shown_in_vr,
                        )

                if dot_should_show:
                    if dot_anchor in ("wrist_left", "wrist_right") and wrist_smoothing > 0.0 and ctrl_p_dot is not None:
                        target_world = _mat34_mul(ctrl_p_dot, self._last_dot_local_mat)
                        self._smoothed_dot_world_mat = smooth_matrix34(
                            self._smoothed_dot_world_mat, target_world, smoothing=wrist_smoothing, dt=dt
                        )
                        self._backend.set_overlay_transform(
                            self.DOT_KEY, "cockpit", self._smoothed_dot_world_mat, self._last_dot_width_m, curvature=0.0
                        )

                    bucket = resolve_status_bucket(dot_state)
                    is_standby = (bucket == "standby")
                    hex_col = resolve_status_color(dot_state, dot_colors)

                    need_initial_show = not self._dot_shown_in_vr
                    if need_initial_show:
                        self._backend.show_overlay(self.DOT_KEY)
                        self._dot_shown_in_vr = True
                        self._dot_refresh_ticks = 3
                        dot_dirty = True

                    if self._dot_refresh_ticks > 0:
                        self._dot_refresh_ticks -= 1
                        dot_dirty = True

                    if not is_standby and (now_t - self._last_dot_frame_t >= 0.085):
                        self._dot_pulse_phase = (self._dot_pulse_phase + 0.24) % (2.0 * math.pi)
                        dot_dirty = True

                    # Upload texture AFTER show_overlay (and for 3 initial ticks) so SteamVR
                    # latches the D3D11 texture reliably on first boot even in standby mode.
                    if dot_dirty:
                        rgba_bytes, canvas = render_dot_rgba(
                            dot_size,
                            hex_col,
                            is_standby=is_standby,
                            pulse_phase=self._dot_pulse_phase,
                        )
                        self._backend.set_overlay_rgba(self.DOT_KEY, rgba_bytes, canvas, canvas)
                        self._last_dot_frame_t = now_t
                elif self._dot_shown_in_vr:
                    self._backend.hide_overlay(self.DOT_KEY)
                    self._dot_shown_in_vr = False
                    self._smoothed_dot_world_mat = None
        elif self._dot_shown_in_vr:
            self._backend.hide_overlay(self.DOT_KEY)
            self._dot_shown_in_vr = False
            self._smoothed_dot_world_mat = None

        # --- 2. HUD Panel Overlay (Mini / Normal / Max) ---
        if want_hud:
            if self._backend.ensure_overlay(self.HUD_KEY, self.HUD_NAME, sort_order=90):
                # Auto-anchor cockpit HUD to current HMD gaze on first show (or explicit recenter)
                if recenter_req or (hud_anchor == "cockpit" and recenter_pose is None):
                    pose = self._backend.get_hmd_pose()
                    if pose is not None:
                        recenter_pose = pose
                        with self._lock:
                            self._hud_recenter_pose = pose
                            self._dot_recenter_pose = pose
                        hud_pose_dirty = True

                eff_curv = hud_curv if hud_mode in ("normal", "max") else 0.0
                if hud_pose_dirty or self._last_hud_local_mat is None:
                    mat, width_m = resolve_vr_hud_transform(
                        anchor=hud_anchor,
                        depth_m=hud_depth,
                        offset_x=hud_ox,
                        offset_y=hud_oy,
                        hud_mode=hud_mode,
                        vr_scale=hud_scale,
                        recenter_pose=recenter_pose,
                        yaw_deg=hud_yaw,
                        pitch_deg=hud_pitch,
                        roll_deg=hud_roll,
                    )
                    self._last_hud_local_mat = mat
                    self._last_hud_width_m = width_m
                    if not (hud_anchor in ("wrist_left", "wrist_right") and wrist_smoothing > 0.0):
                        self._smoothed_hud_world_mat = None
                        self._backend.set_overlay_transform(self.HUD_KEY, hud_anchor, mat, width_m, curvature=eff_curv)

                hud_should_show = True
                ctrl_p_hud = None
                if hud_anchor in ("wrist_left", "wrist_right"):
                    ctrl_p_hud = self._backend.get_controller_pose(hud_anchor)
                    if wrist_auto_hide:
                        hmd_p = self._backend.get_hmd_pose()
                        hud_should_show = is_wrist_overlay_facing_user(
                            hmd_p,
                            ctrl_p_hud,
                            self._last_hud_local_mat,
                            currently_visible=self._hud_shown_in_vr,
                        )

                if hud_should_show:
                    if hud_anchor in ("wrist_left", "wrist_right") and wrist_smoothing > 0.0 and ctrl_p_hud is not None:
                        target_world = _mat34_mul(ctrl_p_hud, self._last_hud_local_mat)
                        self._smoothed_hud_world_mat = smooth_matrix34(
                            self._smoothed_hud_world_mat, target_world, smoothing=wrist_smoothing, dt=dt
                        )
                        self._backend.set_overlay_transform(
                            self.HUD_KEY, "cockpit", self._smoothed_hud_world_mat, self._last_hud_width_m, curvature=eff_curv
                        )

                    need_initial_hud_show = not self._hud_shown_in_vr
                    if need_initial_hud_show:
                        self._backend.show_overlay(self.HUD_KEY)
                        self._hud_shown_in_vr = True

                    if capture_fn is not None and (need_initial_hud_show or (now_t - self._last_hud_frame_t >= 0.08)):
                        frame = capture_fn()
                        if frame is not None:
                            rgba_bytes, fw, fh = frame
                            self._backend.set_overlay_rgba(self.HUD_KEY, rgba_bytes, fw, fh)
                            self._last_hud_frame_t = now_t
                elif self._hud_shown_in_vr:
                    self._backend.hide_overlay(self.HUD_KEY)
                    self._hud_shown_in_vr = False
                    self._smoothed_hud_world_mat = None
        elif self._hud_shown_in_vr:
            self._backend.hide_overlay(self.HUD_KEY)
            self._hud_shown_in_vr = False
            self._smoothed_hud_world_mat = None

        wrist_smoothed_active = (
            wrist_smoothing > 0.0
            and (
                (self._dot_shown_in_vr and dot_anchor in ("wrist_left", "wrist_right"))
                or (self._hud_shown_in_vr and hud_anchor in ("wrist_left", "wrist_right"))
            )
        )
        if wrist_smoothed_active:
            return 0.016  # ~60 Hz buttery-smooth wrist stabilization
        wrist_active = (
            (want_dot and dot_anchor in ("wrist_left", "wrist_right") and wrist_auto_hide)
            or (want_hud and hud_anchor in ("wrist_left", "wrist_right") and wrist_auto_hide)
        )
        if self._dot_refresh_ticks > 0:
            return 0.05
        return 0.06 if (want_hud or wrist_active or (want_dot and resolve_status_bucket(dot_state) != "standby")) else 0.15

    def _loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                wait_s = self.step_once()
                self._stop_event.wait(wait_s)
        finally:
            try:
                self._backend.disconnect()
            except Exception:
                pass
            with self._lock:
                self._connected = False
            self._dot_shown_in_vr = False
            self._hud_shown_in_vr = False
