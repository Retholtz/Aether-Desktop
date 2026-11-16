"""
core/telemetry/providers/vision_hud_provider.py - Agnostic CPU HSV HUD Bar Sampler
"""
import threading
from typing import Optional
import numpy as np
from core.telemetry.schema import TelemetryFrame, GameTelemetryProfile, HudBarRegion
from core.logger import get_logger

logger = get_logger("VisionHudProvider")

try:
    import mss
except ImportError:
    mss = None


class VisionHudTelemetryProvider:
    # RGB dominant channel heuristics for sub-2ms CPU bar fill calculation
    COLOR_RULES = {
        "red": lambda r, g, b: (r > 130) & (r > g * 1.6) & (r > b * 1.6),
        "green": lambda r, g, b: (g > 120) & (g > r * 1.4) & (g > b * 1.4),
        "blue": lambda r, g, b: (b > 130) & (b > r * 1.5) & (b > g * 1.2),
        "cyan": lambda r, g, b: (g > 130) & (b > 130) & (r < 100),
        "yellow": lambda r, g, b: (r > 150) & (g > 140) & (b < 90),
    }

    def __init__(self, profile: GameTelemetryProfile):
        self.profile = profile
        self._sct = None
        self._thread_local = threading.local()

    def _get_sct(self):
        if self._sct is not None:
            return self._sct
        if not mss:
            return None
        sct = getattr(self._thread_local, "sct", None)
        if sct is None:
            mss_cls = getattr(mss, "MSS", None) or mss.mss
            sct = mss_cls()
            self._thread_local.sct = sct
        return sct

    def poll(self) -> Optional[TelemetryFrame]:
        if not self.profile.hud_bars:
            return None

        sct = self._get_sct()
        if not sct:
            return None

        try:
            monitors = sct.monitors
            monitor = monitors[1] if len(monitors) > 1 else monitors[0]
            mon_w = monitor["width"]
            mon_h = monitor["height"]
            mon_left = monitor["left"]
            mon_top = monitor["top"]

            vitals = {}
            for bar in self.profile.hud_bars:
                pct = self._sample_bar_fill(sct, bar, mon_left, mon_top, mon_w, mon_h)
                if pct is not None:
                    vitals[f"{bar.name}_pct"] = round(pct, 3)

            if not vitals:
                return None

            return TelemetryFrame(
                game_id=self.profile.game_id,
                vitals=vitals
            )
        except Exception as e:
            logger.debug("HUD bar sample skipped: %s", e)
            return None

    def _sample_bar_fill(
        self,
        sct,
        bar: HudBarRegion,
        mon_left: int,
        mon_top: int,
        mon_w: int,
        mon_h: int
    ) -> Optional[float]:
        x_pct, y_pct, w_pct, h_pct = bar.bbox_pct
        region = {
            "left": int(mon_left + x_pct * mon_w),
            "top": int(mon_top + y_pct * mon_h),
            "width": max(4, int(w_pct * mon_w)),
            "height": max(2, int(h_pct * mon_h)),
        }

        raw_img = sct.grab(region)
        # Convert BGRA buffer to numpy array (H, W, 3) BGR
        arr = np.asarray(raw_img, dtype=np.uint8)[:, :, :3]
        b = arr[:, :, 0].astype(np.int16)
        g = arr[:, :, 1].astype(np.int16)
        r = arr[:, :, 2].astype(np.int16)

        rule_fn = self.COLOR_RULES.get(bar.color_profile.lower(), self.COLOR_RULES["red"])
        mask = rule_fn(r, g, b)

        # Collapse vertically: a column counts as filled if >= 35% of its vertical pixels match the bar color
        col_fill = mask.mean(axis=0) >= 0.35
        fill_ratio = float(col_fill.mean())

        # HUD presence validation: if < 3% of columns match (e.g. Title Screen, Map Screen, or HUD hidden),
        # treat the HUD bar as not visible rather than 0% health.
        if fill_ratio < 0.03:
            return None

        return fill_ratio
