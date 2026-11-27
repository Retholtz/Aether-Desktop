"""
ui/vr_dot_overlay.py - Native OpenVR (SteamVR) Floating Status Dot Overlay.

Renders Aether's glowing status Dot directly inside VR headsets via OpenVR's
`IVROverlay` API (`VRApplication_Overlay`, zero game hooking required).

How the Dot determines its "corner" in VR:
- **HMD-Locked (`hmd`)**: Queries the headset's binocular overlapping FOV frustum
  (`GetProjectionRaw`), applies a user-configurable comfort safe-zone inset
  (`vr_dot_inset`, default 62%), projects to a comfortable focal depth
  (`vr_dot_depth`, default 1.2 m), and billboards the quad toward the eyes so
  it stays circular with zero peripheral skew.
- **Cockpit / Seated (`cockpit`)**: Anchors the Dot in `TrackingUniverseSeated`
  space at the same safe-FOV corner & depth — ideal for seated sims like
  Elite Dangerous and MSFS.
- **Wrist-Locked (`wrist_left` / `wrist_right`)**: Attaches a compact smartwatch-
  style status LED to the left or right VR controller.

If `openvr` (pyopenvr) is not installed or SteamVR is not running, this module
degrades gracefully with zero overhead and zero impact on desktop Dot Mode.
"""

from __future__ import annotations

import math
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from core.logger import get_logger
from ui.dot_overlay import (
    DEFAULT_HUD_STATUS_COLORS,
    hex_to_rgb,
    normalize_dot_position,
    normalize_dot_size,
    resolve_status_bucket,
    resolve_status_color,
    sanitize_hud_colors,
    _get_dot_geometry,
)

logger = get_logger("VRDotOverlay")

VALID_VR_DOT_ANCHORS = ("hmd", "cockpit", "wrist_left", "wrist_right")

# Conservative default binocular half-FOV tangents (~45 deg horiz, ~40 deg vert)
# used when no live headset projection frustum is available.
DEFAULT_FOV_TANGENTS: Tuple[float, float, float, float] = (1.0, 1.0, 0.8391, 0.8391)


def normalize_vr_dot_anchor(anchor: Any) -> str:
    """Normalizes VR dot anchor mode into one of VALID_VR_DOT_ANCHORS."""
    val = str(anchor or "hmd").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "head": "hmd",
        "head_locked": "hmd",
        "hmd_locked": "hmd",
        "view": "hmd",
        "seated": "cockpit",
        "world": "cockpit",
        "cockpit_locked": "cockpit",
        "left_wrist": "wrist_left",
        "left_hand": "wrist_left",
        "right_wrist": "wrist_right",
        "right_hand": "wrist_right",
    }
    val = aliases.get(val, val)
    return val if val in VALID_VR_DOT_ANCHORS else "hmd"


def normalize_vr_dot_inset(inset: Any) -> float:
    """Clamps VR safe-FOV corner inset ratio to [0.25, 0.90] (default 0.62)."""
    try:
        val = float(inset)
    except (TypeError, ValueError):
        val = 0.62
    return round(max(0.25, min(0.90, val)), 3)


def normalize_vr_dot_depth(depth_m: Any) -> float:
    """Clamps VR dot focal depth in meters to [0.30, 5.00] (default 1.20)."""
    try:
        val = float(depth_m)
    except (TypeError, ValueError):
        val = 1.20
    return round(max(0.30, min(5.00, val)), 2)


def normalize_vr_dot_scale(scale: Any) -> float:
    """Clamps VR dot visual scale multiplier to [0.50, 2.50] (default 1.00)."""
    try:
        val = float(scale)
    except (TypeError, ValueError):
        val = 1.00
    return round(max(0.50, min(2.50, val)), 2)


def normalize_vr_dot_x(offset_x: Any) -> float:
    """Clamps VR dot horizontal X offset in meters to [-3.50, 3.50] (default +0.25 m)."""
    try:
        val = float(offset_x)
    except (TypeError, ValueError):
        val = 0.25
    return round(max(-3.50, min(3.50, val)), 2)


def normalize_vr_dot_y(offset_y: Any) -> float:
    """Clamps VR dot vertical Y offset in meters to [-2.50, 2.50] (default +0.18 m)."""
    try:
        val = float(offset_y)
    except (TypeError, ValueError):
        val = 0.18
    return round(max(-2.50, min(2.50, val)), 2)


def compute_safe_fov_tangents(
    left_eye_proj: Optional[Tuple[float, float, float, float]] = None,
    right_eye_proj: Optional[Tuple[float, float, float, float]] = None,
) -> Tuple[float, float, float, float]:
    """
    Computes positive half-FOV tangents `(tan_left, tan_right, tan_top, tan_bottom)`
    representing the comfortable binocular overlapping frustum from OpenVR
    `GetProjectionRaw(eye)` tuples `(left, right, top, bottom)`.

    In OpenVR, `GetProjectionRaw` returns raw frustum tangents where typically
    `left < 0`, `right > 0`, and `top`/`bottom` have opposite signs depending on
    API convention. Taking `abs()` and intersecting the left/right eye bounds
    ensures the corner lies inside *both* eyes' clear viewing region.
    """
    if not left_eye_proj or not right_eye_proj:
        return DEFAULT_FOV_TANGENTS
    try:
        ll, lr, lt, lb = [abs(float(v)) for v in left_eye_proj]
        rl, rr, rt, rb = [abs(float(v)) for v in right_eye_proj]
        # Binocular overlap: use the smaller outward extent of both eyes
        tan_left = min(ll, rl)
        tan_right = min(lr, rr)
        tan_top = min(lt, rt)
        tan_bottom = min(lb, rb)
        # Clamp to sane headset frustum bounds (~17 deg .. ~68 deg)
        tan_left = max(0.30, min(2.50, tan_left))
        tan_right = max(0.30, min(2.50, tan_right))
        tan_top = max(0.25, min(2.00, tan_top))
        tan_bottom = max(0.25, min(2.00, tan_bottom))
        return (tan_left, tan_right, tan_top, tan_bottom)
    except Exception:
        return DEFAULT_FOV_TANGENTS


def _vec_normalize(v: Tuple[float, float, float]) -> Tuple[float, float, float]:
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if length < 1e-8:
        return (0.0, 0.0, 1.0)
    return (v[0] / length, v[1] / length, v[2] / length)


def _vec_cross(
    a: Tuple[float, float, float],
    b: Tuple[float, float, float],
) -> Tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _build_billboard_matrix34(
    pos: Tuple[float, float, float],
    target: Tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> List[List[float]]:
    """
    Builds a 3x4 row-major OpenVR transform matrix positioned at `pos` whose
    local +Z normal points toward `target` (the viewer's eyes), keeping local +Y
    aligned with world up `(0, 1, 0)`.
    """
    px, py, pz = pos
    tx, ty, tz = target
    z_axis = _vec_normalize((tx - px, ty - py, tz - pz))
    world_up = (0.0, 1.0, 0.0)
    # Guard against degenerate pole alignment
    if abs(z_axis[1]) > 0.995:
        world_up = (0.0, 0.0, -1.0)
    x_axis = _vec_normalize(_vec_cross(world_up, z_axis))
    y_axis = _vec_cross(z_axis, x_axis)
    return [
        [x_axis[0], y_axis[0], z_axis[0], px],
        [x_axis[1], y_axis[1], z_axis[1], py],
        [x_axis[2], y_axis[2], z_axis[2], pz],
    ]


def _mat34_mul_dot(a: List[List[float]], b: List[List[float]]) -> List[List[float]]:
    out = [[0.0] * 4 for _ in range(3)]
    for r in range(3):
        for c in range(3):
            out[r][c] = a[r][0] * b[0][c] + a[r][1] * b[1][c] + a[r][2] * b[2][c]
        out[r][3] = a[r][0] * b[0][3] + a[r][1] * b[1][3] + a[r][2] * b[2][3] + a[r][3]
    return out


def _yaw_aligned_origin_dot(hmd_pose: List[List[float]]) -> List[List[float]]:
    hx, hy, hz = float(hmd_pose[0][3]), float(hmd_pose[1][3]), float(hmd_pose[2][3])
    fwd_x = -float(hmd_pose[0][2])
    fwd_z = -float(hmd_pose[2][2])
    horiz_len = math.hypot(fwd_x, fwd_z)
    if horiz_len < 1e-6:
        fwd_x, fwd_z = 0.0, -1.0
    else:
        fwd_x /= horiz_len
        fwd_z /= horiz_len
    zx, zz = -fwd_x, -fwd_z
    xx, xz = zz, -zx
    return [
        [xx, 0.0, zx, hx],
        [0.0, 1.0, 0.0, hy],
        [xz, 0.0, zz, hz],
    ]


def resolve_vr_dot_transform(
    position: str = "top_right",
    anchor: str = "hmd",
    depth_m: float = 1.20,
    inset: float = 0.62,
    fov_tangents: Optional[Tuple[float, float, float, float]] = None,
    dot_size: int = 22,
    vr_scale: float = 1.00,
    offset_x: Optional[float] = None,
    offset_y: Optional[float] = None,
    recenter_pose: Optional[List[List[float]]] = None,
) -> Tuple[List[List[float]], float]:
    """
    Computes `(matrix_3x4, width_meters)` for the VR Dot overlay.

    For `hmd` and `cockpit` anchors:
    - When explicit `offset_x` / `offset_y` (meters) are provided, places the Dot
      directly at `(offset_x, offset_y, -depth_m)` so the user has exact X/Y/Z
      control in VR space (with `(0, 0)` dead center).
    - Otherwise falls back to safe-FOV corner projection from `position` + `inset`.
    - In `cockpit` mode, if `recenter_pose` is supplied, transforms into the
      user's yaw-aligned seated facing direction.

    For `wrist_left` and `wrist_right` anchors, places a compact smartwatch-style
    LED on the inner wrist facing upward/inward toward the user.
    """
    pos = normalize_dot_position(position)
    anc = normalize_vr_dot_anchor(anchor)
    d = normalize_vr_dot_depth(depth_m)
    ins = normalize_vr_dot_inset(inset)
    d_size = normalize_dot_size(dot_size)
    v_scale = normalize_vr_dot_scale(vr_scale)
    tan_l, tan_r, tan_t, tan_b = fov_tangents or DEFAULT_FOV_TANGENTS

    if anc in ("wrist_left", "wrist_right"):
        # Inner-wrist smartwatch offset relative to controller pose
        x_off = 0.036 if anc == "wrist_left" else -0.036
        y_off = 0.022
        z_off = 0.065
        # Face slightly up & inward toward the viewer
        mat = _build_billboard_matrix34(
            pos=(x_off, y_off, z_off),
            target=(x_off * 2.5, y_off + 0.25, z_off + 0.18),
        )
        width_m = round(0.022 * (d_size / 22.0) * v_scale, 4)
        return mat, max(0.010, min(0.10, width_m))

    # HMD or Cockpit X/Y/Z placement (or legacy safe-FOV corner fallback)
    if offset_x is not None and offset_y is not None:
        x = normalize_vr_dot_x(offset_x)
        y = normalize_vr_dot_y(offset_y)
    elif pos == "top_left":
        x = -d * tan_l * ins
        y = +d * tan_t * ins
    elif pos == "bottom_left":
        x = -d * tan_l * ins
        y = -d * tan_b * ins
    elif pos == "bottom_right":
        x = +d * tan_r * ins
        y = -d * tan_b * ins
    else:  # top_right
        x = +d * tan_r * ins
        y = +d * tan_t * ins

    z = -d
    local_mat = _build_billboard_matrix34(pos=(x, y, z), target=(0.0, 0.0, 0.0))
    if anc == "cockpit" and recenter_pose is not None:
        origin = _yaw_aligned_origin_dot(recenter_pose)
        mat = _mat34_mul_dot(origin, local_mat)
    else:
        mat = local_mat

    # Maintain constant apparent angular size (~2.0 deg at default dot_size=22, scale=1.0)
    width_m = round(d * 0.036 * (d_size / 22.0) * v_scale, 4)
    return mat, max(0.015, min(0.35, width_m))


def render_dot_rgba(
    dot_size: int,
    hex_color: str,
    is_standby: bool = False,
    pulse_phase: float = 0.0,
) -> Tuple[bytes, int]:
    """
    Renders a smooth anti-aliased glowing dot into a straight (non-premultiplied)
    32-bit RGBA buffer suitable for OpenVR `IVROverlay.setOverlayRaw`.
    Returns `(rgba_bytes, canvas_size)`.
    """
    import numpy as np

    # Render at 2x resolution for crisp VR sampling without aliasing
    diameter = normalize_dot_size(dot_size) * 2
    dist, core_alpha, rim_mask, hl_boost, radius, glow_pad, canvas = _get_dot_geometry(diameter)

    r_col, g_col, b_col = hex_to_rgb(hex_color)
    pulse = 0.5 + 0.5 * math.sin(pulse_phase) if not is_standby else 0.0

    glow_radius = radius + glow_pad * (0.65 + 0.25 * pulse)
    glow_strength = 0.16 if is_standby else (0.45 + 0.25 * pulse)
    glow_norm = np.clip(
        1.0 - np.maximum(0.0, dist - radius * 0.85) / max(1.0, (glow_radius - radius * 0.85)),
        0.0,
        1.0,
    )
    glow_alpha = (glow_norm ** 2.2) * glow_strength

    total_alpha = np.clip(np.maximum(core_alpha, glow_alpha), 0.0, 1.0)
    total_alpha = np.clip(np.maximum(total_alpha, rim_mask), 0.0, 1.0)
    if is_standby:
        total_alpha = total_alpha * 0.82

    color_weight = np.where(
        core_alpha > 0.01,
        1.0,
        np.clip(glow_alpha / np.maximum(total_alpha, 1e-5), 0.0, 1.0),
    )
    r_u8 = np.clip(np.round(r_col * color_weight + 255.0 * hl_boost), 0, 255).astype(np.uint8)
    g_u8 = np.clip(np.round(g_col * color_weight + 255.0 * hl_boost), 0, 255).astype(np.uint8)
    b_u8 = np.clip(np.round(b_col * color_weight + 255.0 * hl_boost), 0, 255).astype(np.uint8)
    a_u8 = np.clip(np.round(total_alpha * 255.0), 0, 255).astype(np.uint8)

    rgba = np.stack([r_u8, g_u8, b_u8, a_u8], axis=-1)
    return rgba.tobytes(), canvas


def is_openvr_available() -> bool:
    """Returns True if the optional `openvr` (pyopenvr) Python package is installed."""
    try:
        import openvr  # noqa: F401
        return True
    except Exception:
        return False


class VRDotOverlay:
    """
    Thread-safe OpenVR background overlay controller for Aether's Status Dot.

    Automatically detects when SteamVR / OpenVR is active, attaches as a
    lightweight `VRApplication_Overlay` client, and projects the Dot into the
    user's chosen VR anchor (`hmd`, `cockpit`, `wrist_left`, `wrist_right`).
    """

    OVERLAY_KEY = "aether.desktop.status_dot"
    OVERLAY_NAME = "Aether Status Dot"

    def __init__(
        self,
        enabled: bool = True,
        size: int = 22,
        position: str = "top_right",
        anchor: str = "hmd",
        inset: float = 0.62,
        depth_m: float = 1.20,
        scale: float = 1.00,
        colors: Optional[Dict[str, str]] = None,
    ):
        self._lock = threading.Lock()
        self._enabled = bool(enabled)
        self._size = normalize_dot_size(size)
        self._position = normalize_dot_position(position)
        self._anchor = normalize_vr_dot_anchor(anchor)
        self._inset = normalize_vr_dot_inset(inset)
        self._depth_m = normalize_vr_dot_depth(depth_m)
        self._scale = normalize_vr_dot_scale(scale)
        self._colors = sanitize_hud_colors(colors)
        self._state = "standby"
        self._visible = False
        self._destroyed = False
        self._dirty = True
        self._pose_dirty = True

        self._connected = False
        self._hmd_present = False
        self._fov_tangents = DEFAULT_FOV_TANGENTS

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def get_status(self) -> Dict[str, Any]:
        """Returns current VR Dot runtime and configuration state."""
        with self._lock:
            return {
                "openvr_installed": is_openvr_available(),
                "enabled": self._enabled,
                "connected": self._connected,
                "hmd_present": self._hmd_present,
                "visible": self._visible,
                "anchor": self._anchor,
                "position": self._position,
                "inset": self._inset,
                "depth_m": self._depth_m,
                "scale": self._scale,
                "state": self._state,
            }

    def configure(
        self,
        enabled: Optional[bool] = None,
        size: Optional[int] = None,
        position: Optional[str] = None,
        anchor: Optional[str] = None,
        inset: Optional[float] = None,
        depth_m: Optional[float] = None,
        scale: Optional[float] = None,
        colors: Optional[Dict[str, str]] = None,
    ) -> None:
        should_start = False
        with self._lock:
            if enabled is not None:
                self._enabled = bool(enabled)
            if size is not None:
                self._size = normalize_dot_size(size)
                self._pose_dirty = True
            if position is not None:
                self._position = normalize_dot_position(position)
                self._pose_dirty = True
            if anchor is not None:
                self._anchor = normalize_vr_dot_anchor(anchor)
                self._pose_dirty = True
            if inset is not None:
                self._inset = normalize_vr_dot_inset(inset)
                self._pose_dirty = True
            if depth_m is not None:
                self._depth_m = normalize_vr_dot_depth(depth_m)
                self._pose_dirty = True
            if scale is not None:
                self._scale = normalize_vr_dot_scale(scale)
                self._pose_dirty = True
            if colors is not None:
                self._colors = sanitize_hud_colors(colors)
            self._dirty = True
            should_start = self._enabled and self._visible and not self._destroyed

        if should_start and os.environ.get("AETHER_TESTING") != "1":
            self._ensure_thread_started()

    def set_state(self, state: str) -> None:
        clean = str(state or "standby").strip().lower()
        with self._lock:
            if self._state != clean:
                self._state = clean
                self._dirty = True

    def show(self) -> None:
        with self._lock:
            if self._destroyed:
                return
            self._visible = True
            self._dirty = True
            self._pose_dirty = True
            should_start = self._enabled
        if should_start and os.environ.get("AETHER_TESTING") != "1":
            self._ensure_thread_started()

    def hide(self) -> None:
        with self._lock:
            self._visible = False
            self._dirty = True

    def destroy(self) -> None:
        with self._lock:
            self._visible = False
            self._destroyed = True
            self._dirty = True
        self._stop_event.set()
        t = self._thread
        if t is not None and t.is_alive() and threading.current_thread() is not t:
            try:
                t.join(timeout=1.5)
            except Exception:
                pass

    def _ensure_thread_started(self) -> None:
        if not is_openvr_available():
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._vr_loop,
            daemon=True,
            name="AetherVRDotOverlay",
        )
        self._thread.start()

    @staticmethod
    def _to_hmd_matrix34(openvr_mod, mat_3x4: List[List[float]]):
        hmd_mat = openvr_mod.HmdMatrix34_t()
        for r in range(3):
            for c in range(4):
                hmd_mat.m[r][c] = float(mat_3x4[r][c])
        return hmd_mat

    def _vr_loop(self) -> None:
        """Background OpenVR overlay lifecycle & texture/pose pump."""
        try:
            import openvr
        except Exception:
            return

        vr_sys = None
        vr_overlay = None
        overlay_handle = None
        currently_shown = False
        pulse_phase = 0.0
        last_frame_t = time.monotonic()

        def _cleanup_openvr():
            nonlocal vr_sys, vr_overlay, overlay_handle, currently_shown
            try:
                if vr_overlay is not None and overlay_handle is not None:
                    try:
                        vr_overlay.hideOverlay(overlay_handle)
                    except Exception:
                        pass
                    try:
                        vr_overlay.destroyOverlay(overlay_handle)
                    except Exception:
                        pass
            finally:
                overlay_handle = None
                vr_overlay = None
                currently_shown = False
            if vr_sys is not None:
                try:
                    openvr.shutdown()
                except Exception:
                    pass
                vr_sys = None
            with self._lock:
                self._connected = False

        try:
            while not self._stop_event.is_set():
                with self._lock:
                    enabled = self._enabled
                    visible = self._visible
                    dirty = self._dirty
                    pose_dirty = self._pose_dirty
                    size = self._size
                    pos = self._position
                    anchor = self._anchor
                    inset = self._inset
                    depth_m = self._depth_m
                    scale = self._scale
                    colors = dict(self._colors)
                    state = self._state
                    self._dirty = False
                    self._pose_dirty = False

                if not enabled or not visible:
                    if vr_overlay is not None and overlay_handle is not None and currently_shown:
                        try:
                            vr_overlay.hideOverlay(overlay_handle)
                        except Exception:
                            pass
                        currently_shown = False
                    self._stop_event.wait(0.25)
                    continue

                # Connect to OpenVR only when runtime + HMD are actually active
                if vr_sys is None:
                    try:
                        hmd_ok = bool(openvr.isRuntimeInstalled() and openvr.isHmdPresent())
                    except Exception:
                        hmd_ok = False
                    with self._lock:
                        self._hmd_present = hmd_ok
                    if not hmd_ok:
                        self._stop_event.wait(2.5)
                        continue
                    try:
                        vr_sys = openvr.init(openvr.VRApplication_Overlay)
                        vr_overlay = openvr.IVROverlay()
                        overlay_handle = vr_overlay.createOverlay(self.OVERLAY_KEY, self.OVERLAY_NAME)
                        vr_overlay.setOverlayAlpha(overlay_handle, 1.0)
                        vr_overlay.setOverlaySortOrder(overlay_handle, 100)

                        # Query live headset binocular frustum for safe-FOV corner math
                        try:
                            l_proj = vr_sys.getProjectionRaw(openvr.Eye_Left)
                            r_proj = vr_sys.getProjectionRaw(openvr.Eye_Right)
                            fov = compute_safe_fov_tangents(l_proj, r_proj)
                        except Exception:
                            fov = DEFAULT_FOV_TANGENTS
                        with self._lock:
                            self._fov_tangents = fov
                            self._connected = True
                        dirty = True
                        pose_dirty = True
                        logger.info("Connected to OpenVR overlay compositor (FOV tangents=%s).", fov)
                    except Exception as exc:
                        logger.debug("OpenVR overlay init skipped/retrying: %s", exc)
                        _cleanup_openvr()
                        self._stop_event.wait(3.0)
                        continue

                # Poll OpenVR quit events so we release cleanly when SteamVR shuts down
                try:
                    ev = openvr.VREvent_t()
                    while vr_sys.pollNextEvent(ev):
                        if ev.eventType == openvr.VREvent_Quit:
                            logger.info("Received OpenVR quit event; releasing VR Dot overlay.")
                            _cleanup_openvr()
                            break
                except Exception:
                    _cleanup_openvr()
                    self._stop_event.wait(2.0)
                    continue

                if vr_sys is None or vr_overlay is None or overlay_handle is None:
                    continue

                # Apply 3D transform & width whenever corner/anchor/depth/inset/scale changes
                if pose_dirty:
                    with self._lock:
                        fov_t = self._fov_tangents
                    mat_3x4, width_m = resolve_vr_dot_transform(
                        position=pos,
                        anchor=anchor,
                        depth_m=depth_m,
                        inset=inset,
                        fov_tangents=fov_t,
                        dot_size=size,
                        vr_scale=scale,
                    )
                    hmd_mat = self._to_hmd_matrix34(openvr, mat_3x4)
                    try:
                        vr_overlay.setOverlayWidthInMeters(overlay_handle, float(width_m))
                        if anchor == "cockpit":
                            vr_overlay.setOverlayTransformAbsolute(
                                overlay_handle,
                                openvr.TrackingUniverseSeated,
                                hmd_mat,
                            )
                        elif anchor in ("wrist_left", "wrist_right"):
                            role = (
                                openvr.TrackedControllerRole_LeftHand
                                if anchor == "wrist_left"
                                else openvr.TrackedControllerRole_RightHand
                            )
                            ctrl_idx = vr_sys.getTrackedDeviceIndexForControllerRole(role)
                            if ctrl_idx != openvr.k_unTrackedDeviceIndexInvalid:
                                vr_overlay.setOverlayTransformTrackedDeviceRelative(
                                    overlay_handle,
                                    ctrl_idx,
                                    hmd_mat,
                                )
                            else:
                                # Fallback to HMD corner if controller is off/sleeping
                                fallback_mat, fallback_w = resolve_vr_dot_transform(
                                    position=pos,
                                    anchor="hmd",
                                    depth_m=depth_m,
                                    inset=inset,
                                    fov_tangents=fov_t,
                                    dot_size=size,
                                    vr_scale=scale,
                                )
                                vr_overlay.setOverlayWidthInMeters(overlay_handle, float(fallback_w))
                                vr_overlay.setOverlayTransformTrackedDeviceRelative(
                                    overlay_handle,
                                    openvr.k_unTrackedDeviceIndex_Hmd,
                                    self._to_hmd_matrix34(openvr, fallback_mat),
                                )
                        else:
                            vr_overlay.setOverlayTransformTrackedDeviceRelative(
                                overlay_handle,
                                openvr.k_unTrackedDeviceIndex_Hmd,
                                hmd_mat,
                            )
                    except Exception as exc:
                        logger.debug("OpenVR overlay transform update error: %s", exc)

                bucket = resolve_status_bucket(state)
                is_standby = (bucket == "standby")
                hex_col = resolve_status_color(state, colors)
                now = time.monotonic()

                if not currently_shown:
                    try:
                        vr_overlay.showOverlay(overlay_handle)
                        currently_shown = True
                        dirty = True
                    except Exception:
                        pass

                if not is_standby and (now - last_frame_t >= 0.085):
                    pulse_phase = (pulse_phase + 0.24) % (2.0 * math.pi)
                    dirty = True

                if dirty:
                    try:
                        import ctypes
                        rgba_bytes, canvas = render_dot_rgba(
                            size,
                            hex_col,
                            is_standby=is_standby,
                            pulse_phase=pulse_phase,
                        )
                        c_buf = (ctypes.c_ubyte * len(rgba_bytes)).from_buffer_copy(rgba_bytes)
                        vr_overlay.setOverlayRaw(overlay_handle, c_buf, canvas, canvas, 4)
                        last_frame_t = now
                    except Exception as exc:
                        logger.warning("OpenVR overlay texture upload error: %s", exc)

                self._stop_event.wait(0.06 if not is_standby else 0.15)
        finally:
            _cleanup_openvr()
