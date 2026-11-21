"""
core/game_modules.py - Declarative Pure-JSON Game Module Loader & Resolver.

Loads bundled and community-shared `.aethermodule.json` files from `data/game_modules/`
so Aether's telemetry, alerts, companion links, and safety rules remain 100% game-agnostic
and data-driven with zero game-specific Python code required for new games or DLCs.
"""

import glob
import json
import os
from typing import Any, Dict, List, Optional

from core.logger import get_logger
from core.paths import app_path, resource_path
from core.telemetry.schema import GameTelemetryProfile, HudBarRegion

logger = get_logger("GameModules")


def _normalize_id(val: str) -> str:
    return (val or "").strip().lower().replace(" ", "_")


def get_game_modules_dirs() -> List[str]:
    """
    Returns candidate module directories in load order:
    1. Bundled read-only defaults (`assets/game_modules`, tracked in Git & PyInstaller)
    2. User-writable / community-imported overrides (`data/game_modules`)
    """
    dirs: List[str] = []
    for candidate in (
        resource_path("assets", "game_modules"),
        resource_path("data", "game_modules"),
        app_path("data", "game_modules"),
    ):
        norm = os.path.normpath(candidate)
        if norm not in dirs and os.path.isdir(norm):
            dirs.append(norm)
    return dirs


def load_all_game_modules(extra_dirs: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    """
    Loads all `*.aethermodule.json` and `*.json` module definitions, indexing them
    by `module_id` and every alias listed in `game_ids`.
    Later directories (e.g. user-writable overrides) take precedence over bundled defaults.
    """
    modules_by_key: Dict[str, Dict[str, Any]] = {}
    search_dirs = list(get_game_modules_dirs())
    if extra_dirs:
        for d in extra_dirs:
            if d and os.path.isdir(d) and os.path.normpath(d) not in search_dirs:
                search_dirs.append(os.path.normpath(d))

    for mod_dir in search_dirs:
        pattern = os.path.join(mod_dir, "*.json")
        for fpath in sorted(glob.glob(pattern)):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    continue
                mod_id = _normalize_id(data.get("module_id") or os.path.splitext(os.path.basename(fpath))[0].replace(".aethermodule", ""))
                if not mod_id:
                    continue
                data["_source_path"] = fpath
                data["module_id"] = mod_id
                modules_by_key[mod_id] = data
                for alias in data.get("game_ids") or []:
                    norm_alias = _normalize_id(str(alias))
                    if norm_alias:
                        modules_by_key[norm_alias] = data
            except Exception as e:
                logger.warning("Failed loading game module %s: %s", fpath, e)

    return modules_by_key


def get_module_for_game(
    game_id: str,
    profile: Optional[Dict[str, Any]] = None,
    extra_dirs: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Finds the best-matching declarative Game Module for a game_id or profile."""
    modules = load_all_game_modules(extra_dirs=extra_dirs)
    norm_id = _normalize_id(game_id)
    if norm_id and norm_id in modules:
        return modules[norm_id]

    if isinstance(profile, dict):
        # Match by explicit module_id or executable name
        explicit_mod = _normalize_id(str(profile.get("module_id") or ""))
        if explicit_mod and explicit_mod in modules:
            return modules[explicit_mod]

        proc = str(profile.get("process_name") or profile.get("executable") or "").strip().lower()
        if proc:
            for mod in modules.values():
                exes = [str(x).strip().lower() for x in (mod.get("executables") or [])]
                if proc in exes:
                    return mod

    # Prefix/substring fallback (e.g. "elite_dangerous_horizons" -> "elite_dangerous")
    if norm_id:
        for mod_key, mod in modules.items():
            if mod_key and (norm_id.startswith(mod_key) or mod_key.startswith(norm_id)):
                return mod
    return None


def build_telemetry_profile_from_module(
    game_id: str,
    profile: Optional[Dict[str, Any]] = None,
    extra_dirs: Optional[List[str]] = None,
) -> Optional[GameTelemetryProfile]:
    """
    Constructs a `GameTelemetryProfile` by merging a declarative Game Module's
    `telemetry` block with any inline overrides in `profile["telemetry"]`.
    """
    mod = get_module_for_game(game_id, profile=profile, extra_dirs=extra_dirs)
    mod_tel = (mod.get("telemetry") if isinstance(mod, dict) else None) or {}
    prof_tel = (profile.get("telemetry") if isinstance(profile, dict) else None) or {}

    if not mod_tel and not prof_tel:
        return None

    merged: Dict[str, Any] = dict(mod_tel)
    merged.update({k: v for k, v in prof_tel.items() if v is not None})

    raw_bars = merged.get("hud_bars") or []
    hud_bars: List[HudBarRegion] = []
    for bar in raw_bars:
        if isinstance(bar, HudBarRegion):
            hud_bars.append(bar)
        elif isinstance(bar, dict) and bar.get("name") and bar.get("bbox_pct"):
            bbox = tuple(float(x) for x in bar["bbox_pct"])
            if len(bbox) == 4:
                hud_bars.append(
                    HudBarRegion(
                        name=str(bar["name"]),
                        bbox_pct=bbox,
                        color_profile=str(bar.get("color_profile") or "red"),
                    )
                )

    suppress_list: List[str] = []
    for src in (mod, profile):
        if isinstance(src, dict):
            for p in src.get("suppress_alerts_when_running") or []:
                if str(p).strip() and str(p).strip() not in suppress_list:
                    suppress_list.append(str(p).strip())

    return GameTelemetryProfile(
        game_id=_normalize_id(game_id) or (mod.get("module_id") if mod else "generic"),
        provider_type=str(merged.get("provider_type") or "vision_hud"),
        poll_interval_sec=float(merged.get("poll_interval_sec") or 0.5),
        native_file_path=merged.get("native_file_path"),
        companion_files=list(merged.get("companion_files") or []),
        bitmask_mappings=dict(merged.get("bitmask_mappings") or {}),
        state_rules=list(merged.get("state_rules") or []),
        vital_mappings=dict(merged.get("vital_mappings") or {}),
        resource_mappings=dict(merged.get("resource_mappings") or {}),
        alert_rules=dict(merged.get("alert_rules") or {}),
        suppress_alerts_when_running=suppress_list,
        hud_bars=hud_bars,
    )


def load_all_telemetry_profiles(extra_dirs: Optional[List[str]] = None) -> Dict[str, GameTelemetryProfile]:
    """Builds a game_id -> GameTelemetryProfile registry from all installed Game Modules."""
    modules = load_all_game_modules(extra_dirs=extra_dirs)
    profiles: Dict[str, GameTelemetryProfile] = {}
    for key in modules:
        tp = build_telemetry_profile_from_module(key, extra_dirs=extra_dirs)
        if tp is not None:
            profiles[key] = tp
    return profiles
