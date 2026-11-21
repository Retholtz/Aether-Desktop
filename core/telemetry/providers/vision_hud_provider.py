"""
core/telemetry/providers/vision_hud_provider.py - Agnostic CPU HSV HUD Bar Sampler
"""
import json
import re
import threading
from typing import Any, Dict, List, Optional
import numpy as np
from core.telemetry.schema import TelemetryFrame, GameTelemetryProfile, HudBarRegion
from core.logger import get_logger

logger = get_logger("VisionHudProvider")

VALID_HUD_COLORS = {"red", "green", "blue", "cyan", "yellow"}


def calibrate_hud_bars_from_frame(
    img_bytes: bytes,
    genai_client: Any,
    model_endpoint: str = "gemini-4-hydrogen",
    game_name: str = "",
) -> List[HudBarRegion]:
    """
    One-shot vision calibration: inspects a live gameplay screenshot and returns
    normalized `HudBarRegion` boxes (0.0-1.0 ratios) for any visible vital/resource bars
    (health, stamina, mana, spirit, shield, oxygen, etc.) at the player's exact resolution.
    """
    if not img_bytes or not genai_client:
        return []

    from core.model_discovery import resolve_model_profile

    resolved_model = resolve_model_profile(model_endpoint, default_model="gemini-4-hydrogen")[0]
    game_ctx = f' in the game "{game_name}"' if game_name else ""
    prompt = f"""
Inspect this live gameplay screenshot{game_ctx}. Locate any visible HUD vital or resource bars
(such as health, stamina, mana, spirit, shield, armor, oxygen, energy, or posture).

For each bar clearly visible on screen, return its bounding box in normalized 0-1000 coordinates
([left, top, width, height]) and its primary fill color ("red", "green", "blue", "cyan", or "yellow").
Return strictly a JSON object:
{{
  "bars": [
    {{"name": "health", "bbox_1000": [left, top, width, height], "color_profile": "red"}}
  ]
}}
If no HUD bars are visible (e.g. menu, cutscene, or map screen), return {{"bars": []}}.
"""
    try:
        try:
            from google.genai import types

            part = types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
            contents = [part, prompt]
            cfg_kwargs: Dict[str, Any] = {
                "response_mime_type": "application/json",
                "temperature": 0.0,
            }
            if hasattr(types, "ThinkingConfig"):
                try:
                    cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="low")
                except Exception:
                    pass
            cfg = types.GenerateContentConfig(**cfg_kwargs)
        except Exception:
            contents = [
                {
                    "parts": [
                        {"inline_data": {"mime_type": "image/jpeg", "data": img_bytes}},
                        {"text": prompt},
                    ]
                }
            ]
            cfg = {"response_mime_type": "application/json", "temperature": 0.0}

        resp = genai_client.models.generate_content(
            model=resolved_model,
            contents=contents,
            config=cfg,
        )
        raw = (resp.text or "").strip() if resp and hasattr(resp, "text") else ""
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        parsed = json.loads(raw) if raw else {}

        calibrated: List[HudBarRegion] = []
        seen_names = set()
        for item in parsed.get("bars") or []:
            if not isinstance(item, dict):
                continue
            name = re.sub(r"[^a-z0-9_]+", "_", str(item.get("name") or "").strip().lower()).strip("_")
            bbox = item.get("bbox_1000") or item.get("bbox_pct")
            if not name or name in seen_names or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            try:
                vals = [float(v) for v in bbox]
            except (TypeError, ValueError):
                continue
            # Convert 0-1000 to 0.0-1.0 if needed
            if max(vals) > 1.0:
                vals = [v / 1000.0 for v in vals]
            x, y, w, h = vals
            x = max(0.0, min(0.99, x))
            y = max(0.0, min(0.99, y))
            w = max(0.01, min(1.0 - x, w))
            h = max(0.004, min(0.25, h))
            color = str(item.get("color_profile") or "red").strip().lower()
            if color not in VALID_HUD_COLORS:
                color = "red"
            seen_names.add(name)
            calibrated.append(
                HudBarRegion(
                    name=name,
                    bbox_pct=(round(x, 4), round(y, 4), round(w, 4), round(h, 4)),
                    color_profile=color,
                )
            )
        return calibrated
    except Exception as e:
        logger.warning("Vision HUD calibration failed: %s", e)
        return []


def calibrate_hud_bars_live(
    genai_client: Any,
    target_process: str = "",
    model_endpoint: str = "gemini-4-hydrogen",
    game_name: str = "",
) -> List[HudBarRegion]:
    """Captures the active game viewport (multi-monitor aware) and calibrates HUD bars."""
    from core.game_nav import UniversalGameNavigator

    nav = UniversalGameNavigator(
        client=genai_client,
        model_endpoint=model_endpoint,
        target_process=target_process,
        game_name=game_name,
    )
    img_bytes, _, _ = nav.capture_viewport(max_dim=1280)
    if not img_bytes:
        return []
    return calibrate_hud_bars_from_frame(
        img_bytes=img_bytes,
        genai_client=genai_client,
        model_endpoint=model_endpoint,
        game_name=game_name,
    )

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
