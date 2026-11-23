"""
core/game_modules.py - Declarative Pure-JSON Game Module Loader, Validator, Importer & Auto-Builder.

Loads bundled (assets/game_modules/) and user/community-imported (data/game_modules/)
`.aethermodule.json` files so Aether's telemetry, alerts, companion links, and safety rules
stay 100% game-agnostic and data-driven with zero executable code inside modules.
"""

import glob
import json
import os
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from core.logger import get_logger
from core.paths import resource_path, user_data_path
from core.telemetry.schema import GameTelemetryProfile, HudBarRegion

logger = get_logger("GameModules")

ALLOWED_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def is_safe_localhost_url(url: str) -> bool:
    """Returns True ONLY for http/https URLs targeting loopback (prevents SSRF)."""
    raw = str(url or "").strip()
    if not raw:
        return False
    try:
        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme not in ("http", "https"):
            return False
        return (parsed.hostname or "").strip().lower() in ALLOWED_LOOPBACK_HOSTS
    except Exception:
        return False

ALLOWED_PROVIDER_TYPES = {"vision_hud", "native_file", "localhost_http", "none"}
ALLOWED_INPUT_MODES = {"autonomous", "spotter_only"}

# Generic 3rd-party localhost telemetry bridges (game-specific companion processes live in `.aethermodule.json`)
KNOWN_COMPANION_BRIDGES: List[Dict[str, Any]] = [
    {
        "process": "SimHubWPF.exe",
        "kind": "localhost_http",
        "games": [],
        "http_endpoint": "http://127.0.0.1:8888/api/getgamedata",
    },
]

ANTI_CHEAT_KEYWORDS = (
    "easy anti-cheat",
    "easyanticheat",
    "battleye",
    "vanguard",
    "ricochet",
    "punkbuster",
    "nprotect",
    "vac",
    "kernel anti-cheat",
)


def _normalize_id(val: str) -> str:
    clean = re.sub(r"[^a-z0-9_]+", "_", (val or "").strip().lower()).strip("_")
    return clean


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
        user_data_path("data", "game_modules"),
    ):
        norm = os.path.normpath(candidate)
        if norm not in dirs and os.path.isdir(norm):
            dirs.append(norm)
    return dirs


def get_user_modules_dir() -> str:
    path = user_data_path("data", "game_modules")
    os.makedirs(path, exist_ok=True)
    return path


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
                mod_id = _normalize_id(
                    data.get("module_id")
                    or os.path.splitext(os.path.basename(fpath))[0].replace(".aethermodule", "")
                )
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


def list_unique_game_modules(extra_dirs: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Returns deduplicated metadata summaries for all installed Game Modules."""
    raw = load_all_game_modules(extra_dirs=extra_dirs)
    seen = set()
    items: List[Dict[str, Any]] = []
    for mod in raw.values():
        mid = mod.get("module_id")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        tel = mod.get("telemetry") or {}
        items.append({
            "module_id": mid,
            "display_name": mod.get("display_name") or mid.replace("_", " ").title(),
            "universe_name": mod.get("universe_name") or "",
            "input_mode": mod.get("input_mode") or "autonomous",
            "provider_type": tel.get("provider_type") or "vision_hud",
            "suppress_alerts_when_running": list(mod.get("suppress_alerts_when_running") or []),
            "source_path": mod.get("_source_path") or "",
        })
    items.sort(key=lambda x: x["display_name"].lower())
    return items


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
        explicit_mod = _normalize_id(str(profile.get("module_id") or ""))
        if explicit_mod and explicit_mod in modules:
            return modules[explicit_mod]

        proc_raw = str(profile.get("process_name") or profile.get("executable") or "").strip().lower()
        proc_base = os.path.basename(proc_raw.replace("\\", "/")) if proc_raw else ""
        if proc_raw or proc_base:
            for mod in modules.values():
                exes = [os.path.basename(str(x).strip().replace("\\", "/")).lower() for x in (mod.get("executables") or [])]
                if proc_base in exes or proc_raw in exes:
                    return mod

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
    # Explicit profile list overrides module defaults when provided as a list
    if isinstance(profile, dict) and isinstance(profile.get("suppress_alerts_when_running"), list):
        for p in profile["suppress_alerts_when_running"]:
            if str(p).strip() and str(p).strip() not in suppress_list:
                suppress_list.append(str(p).strip())
    elif isinstance(mod, dict):
        for p in mod.get("suppress_alerts_when_running") or []:
            if str(p).strip() and str(p).strip() not in suppress_list:
                suppress_list.append(str(p).strip())

    # Also include any linked 3rd-party companion apps with `mute_aether_alerts` or `quiet_when_running`
    for src in (profile, mod):
        if isinstance(src, dict):
            raw_apps = src.get("companion_apps")
            app_iter = raw_apps if isinstance(raw_apps, list) else (list(raw_apps.values()) if isinstance(raw_apps, dict) else [])
            for app in app_iter:
                if isinstance(app, dict) and (app.get("mute_aether_alerts") or app.get("quiet_when_running")):
                    exe = os.path.basename(str(app.get("executable") or "").strip())
                    if exe and exe.lower() not in {s.lower() for s in suppress_list}:
                        suppress_list.append(exe)

    http_url = merged.get("http_endpoint")
    if http_url and not is_safe_localhost_url(str(http_url)):
        logger.warning("Ignoring non-localhost http_endpoint in module '%s': %s", game_id, http_url)
        http_url = None

    return GameTelemetryProfile(
        game_id=_normalize_id(game_id) or (mod.get("module_id") if mod else "generic"),
        provider_type=str(merged.get("provider_type") or "vision_hud"),
        poll_interval_sec=float(merged.get("poll_interval_sec") or 0.5),
        native_file_path=merged.get("native_file_path"),
        http_endpoint=http_url,
        http_timeout_sec=float(merged.get("http_timeout_sec") or 1.5),
        companion_files=list(merged.get("companion_files") or []),
        bitmask_mappings=dict(merged.get("bitmask_mappings") or {}),
        state_rules=list(merged.get("state_rules") or []),
        threshold_state_rules=list(merged.get("threshold_state_rules") or []),
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


def validate_game_module(raw_data: Any) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Validates and sanitizes an untrusted `.aethermodule.json` payload.
    Guarantees pure declarative JSON data, safe module_id slug, allowed input_mode,
    and localhost-only HTTP telemetry URLs.
    """
    if not isinstance(raw_data, dict):
        return False, None, "Module payload must be a JSON object."

    display_name = str(raw_data.get("display_name") or "").strip()
    mod_id = _normalize_id(str(raw_data.get("module_id") or display_name))
    if not mod_id:
        return False, None, "Module must include a valid 'module_id' or 'display_name'."
    if not display_name:
        display_name = mod_id.replace("_", " ").title()

    input_mode = str(raw_data.get("input_mode") or "autonomous").strip().lower()
    if input_mode not in ALLOWED_INPUT_MODES:
        input_mode = "autonomous"

    game_ids = [
        _normalize_id(str(g))
        for g in (raw_data.get("game_ids") or [mod_id])
        if _normalize_id(str(g))
    ]
    if mod_id not in game_ids:
        game_ids.insert(0, mod_id)

    executables = [
        os.path.basename(str(exe).strip())
        for exe in (raw_data.get("executables") or [])
        if str(exe).strip()
    ]

    suppress = [
        os.path.basename(str(p).strip())
        for p in (raw_data.get("suppress_alerts_when_running") or [])
        if str(p).strip()
    ]

    # Sanitize companion_apps (strip any untrusted local drive paths from shareable modules!)
    sanitized_companion_apps: List[Dict[str, Any]] = []
    raw_comp_apps = raw_data.get("companion_apps")
    if isinstance(raw_comp_apps, dict):
        raw_comp_iter = [{"id": k, **(v if isinstance(v, dict) else {})} for k, v in raw_comp_apps.items()]
    elif isinstance(raw_comp_apps, list):
        raw_comp_iter = [x for x in raw_comp_apps if isinstance(x, dict)]
    else:
        raw_comp_iter = []

    seen_app_ids = set()
    for app in raw_comp_iter:
        app_name = str(app.get("name") or app.get("display_name") or "").strip()
        app_exe = os.path.basename(str(app.get("executable") or "").strip())
        if not app_name and app_exe:
            app_name = os.path.splitext(app_exe)[0]
        if not app_name and not app_exe:
            continue
        app_id = _normalize_id(str(app.get("id") or app_name or app_exe))
        if not app_id or app_id in seen_app_ids:
            continue
        seen_app_ids.add(app_id)
        mute_flag = bool(app.get("mute_aether_alerts", False))
        quiet_flag = bool(app.get("quiet_when_running", False))
        sanitized_entry: Dict[str, Any] = {
            "id": app_id,
            "name": app_name,
            "executable": app_exe,
            "purpose": str(app.get("purpose") or "").strip(),
            "mute_aether_alerts": mute_flag,
            "quiet_when_running": quiet_flag,
            "prompt_to_launch": bool(app.get("prompt_to_launch", True)),
        }
        if app.get("url"):
            sanitized_entry["url"] = str(app["url"]).strip()
        sanitized_companion_apps.append(sanitized_entry)
        if (mute_flag or quiet_flag) and app_exe and app_exe.lower() not in {s.lower() for s in suppress}:
            suppress.append(app_exe)

    tel_in = raw_data.get("telemetry") if isinstance(raw_data.get("telemetry"), dict) else {}
    provider_type = str(tel_in.get("provider_type") or "vision_hud").strip().lower()
    if provider_type not in ALLOWED_PROVIDER_TYPES:
        return False, None, f"Unsupported telemetry provider_type '{provider_type}'."

    http_endpoint = tel_in.get("http_endpoint")
    if http_endpoint is not None and str(http_endpoint).strip():
        if not is_safe_localhost_url(str(http_endpoint)):
            return (
                False,
                None,
                "Security restriction: telemetry 'http_endpoint' must target localhost / 127.0.0.1 only.",
            )
        http_endpoint = str(http_endpoint).strip()
    else:
        http_endpoint = None

    sanitized_tel: Dict[str, Any] = {
        "adapter": str(tel_in.get("adapter") or "none"),
        "provider_type": provider_type,
        "poll_interval_sec": max(0.2, min(10.0, float(tel_in.get("poll_interval_sec") or 0.5))),
    }
    if tel_in.get("dir"):
        sanitized_tel["dir"] = str(tel_in["dir"]).strip()
    if tel_in.get("status_file"):
        sanitized_tel["status_file"] = str(tel_in["status_file"]).strip()
    if tel_in.get("native_file_path"):
        sanitized_tel["native_file_path"] = str(tel_in["native_file_path"]).strip()
    if tel_in.get("journal_glob"):
        sanitized_tel["journal_glob"] = str(tel_in["journal_glob"]).strip()
    if tel_in.get("event_field"):
        sanitized_tel["event_field"] = str(tel_in["event_field"]).strip()
    if tel_in.get("rehydrate_lines") is not None:
        sanitized_tel["rehydrate_lines"] = max(1, min(1000, int(tel_in["rehydrate_lines"])))
    if http_endpoint:
        sanitized_tel["http_endpoint"] = http_endpoint
        sanitized_tel["http_timeout_sec"] = max(0.2, min(5.0, float(tel_in.get("http_timeout_sec") or 1.5)))
    for list_key in (
        "companion_files",
        "companion_extractors",
        "flag_to_state",
        "event_handlers",
        "poi_trackers",
        "milestone_rules",
        "state_rules",
        "threshold_state_rules",
        "hud_bars",
    ):
        if isinstance(tel_in.get(list_key), list):
            sanitized_tel[list_key] = tel_in[list_key]
    for dict_key in (
        "initial_state",
        "initial_flags",
        "status_fields",
        "location_fields",
        "summary_config",
        "bitmask_mappings",
        "vital_mappings",
        "resource_mappings",
        "alert_rules",
    ):
        if isinstance(tel_in.get(dict_key), dict):
            sanitized_tel[dict_key] = tel_in[dict_key]

    # Validate optional declarative intel_endpoints (strictly public HTTPS URLs)
    sanitized_intel_endpoints: Dict[str, Any] = {}
    raw_intel = raw_data.get("intel_endpoints")
    if isinstance(raw_intel, dict):
        from core.game_intel import is_safe_public_https_url
        for cat_key, ep_spec in raw_intel.items():
            if not isinstance(ep_spec, dict):
                continue
            ep_url = str(ep_spec.get("url") or "").strip()
            if not ep_url or not is_safe_public_https_url(ep_url):
                return (
                    False,
                    None,
                    f"Security restriction: intel_endpoints['{cat_key}'].url must be a public HTTPS URL.",
                )
            clean_ep = dict(ep_spec)
            clean_ep["url"] = ep_url
            vr = clean_ep.get("value_resolver")
            if isinstance(vr, dict) and vr.get("url"):
                vr_url = str(vr["url"]).strip()
                if not is_safe_public_https_url(vr_url):
                    return (
                        False,
                        None,
                        f"Security restriction: intel_endpoints['{cat_key}'].value_resolver.url must be a public HTTPS URL.",
                    )
            sanitized_intel_endpoints[str(cat_key).strip().lower()] = clean_ep

    # Validate optional declarative binds_scanner block
    sanitized_binds_scanner = (
        dict(raw_data["binds_scanner"])
        if isinstance(raw_data.get("binds_scanner"), dict)
        else {}
    )

    # Optional Tier 2 Python extension filename (basename only; never auto-executed without SHA-256 trust)
    raw_ext = str(raw_data.get("extension") or "").strip()
    clean_ext = os.path.basename(raw_ext) if raw_ext.endswith(".py") else ""

    nav_mode_val = str(raw_data.get("navigation_mode") or "").strip().lower()
    if nav_mode_val not in ("controller", "co_pilot_spotter", "search_bar", "mouse_keyboard"):
        nav_mode_val = ""

    sanitized: Dict[str, Any] = {
        "schema_version": 1,
        "module_id": mod_id,
        "display_name": display_name,
        "game_ids": game_ids,
        "executables": executables,
        "universe_name": str(raw_data.get("universe_name") or "").strip(),
        "input_mode": input_mode,
        "suppress_alerts_when_running": suppress,
        "companion_apps": sanitized_companion_apps,
        "companion_resources": raw_data.get("companion_resources")
        if isinstance(raw_data.get("companion_resources"), dict)
        else {},
        "intel_endpoints": sanitized_intel_endpoints,
        "binds_scanner": sanitized_binds_scanner,
        "map_controls": raw_data.get("map_controls")
        if isinstance(raw_data.get("map_controls"), dict)
        else {},
        "keybinds": raw_data.get("keybinds") if isinstance(raw_data.get("keybinds"), dict) else {},
        "sequences": raw_data.get("sequences") if isinstance(raw_data.get("sequences"), dict) else {},
        "telemetry": sanitized_tel,
    }
    if nav_mode_val:
        sanitized["navigation_mode"] = nav_mode_val
    if raw_data.get("default_callsign"):
        sanitized["default_callsign"] = str(raw_data["default_callsign"]).strip()
    if isinstance(raw_data.get("default_pois"), list):
        clean_pois = []
        for item in raw_data["default_pois"]:
            if not isinstance(item, dict):
                continue
            p_name = str(item.get("name") or "").strip()
            p_sys = str(item.get("system") or item.get("location") or "").strip()
            if not p_name or not p_sys:
                continue
            p_id = _normalize_id(str(item.get("id") or p_name)) or "poi"
            clean_pois.append({
                "id": p_id,
                "name": p_name,
                "system": p_sys,
                "callsign": str(item.get("callsign") or "").strip(),
                "aliases": [str(a).strip() for a in (item.get("aliases") or []) if str(a).strip()],
            })
        if clean_pois:
            sanitized["default_pois"] = clean_pois
    if clean_ext:
        sanitized["extension"] = clean_ext
    if raw_data.get("steam_appid"):
        sanitized["steam_appid"] = raw_data["steam_appid"]

    return True, sanitized, "OK"


def save_user_game_module(module_data: Dict[str, Any], target_dir: Optional[str] = None) -> str:
    """Atomically saves a validated `.aethermodule.json` into the user modules directory."""
    out_dir = target_dir or get_user_modules_dir()
    os.makedirs(out_dir, exist_ok=True)
    mod_id = _normalize_id(module_data.get("module_id") or "custom_game")
    out_path = os.path.join(out_dir, f"{mod_id}.aethermodule.json")
    tmp_path = f"{out_path}.{os.getpid()}.tmp"
    clean_copy = {k: v for k, v in module_data.items() if not str(k).startswith("_")}
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(clean_copy, f, indent=2)
    os.replace(tmp_path, out_path)
    return out_path


def import_game_module(
    raw_or_path: Any,
    game_manager=None,
    target_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Imports a `.aethermodule.json` from a file path, JSON string, or dict.
    Saves it to `data/game_modules/` and assimilates it into `GameManager` if provided.
    """
    try:
        if isinstance(raw_or_path, str):
            stripped = raw_or_path.strip()
            if os.path.isfile(stripped):
                with open(stripped, "r", encoding="utf-8") as f:
                    parsed = json.load(f)
            else:
                parsed = json.loads(stripped)
        elif isinstance(raw_or_path, dict):
            parsed = raw_or_path
        else:
            return {"success": False, "error": "Unsupported module input format."}
    except Exception as e:
        return {"success": False, "error": f"Invalid JSON module: {e}"}

    ok, clean_mod, msg = validate_game_module(parsed)
    if not ok or not clean_mod:
        return {"success": False, "error": msg}

    saved_path = save_user_game_module(clean_mod, target_dir=target_dir)
    mod_id = clean_mod["module_id"]

    # Assimilate into GameManager profile (preserving user's personal callsign/scratchpad/logs)
    if game_manager is not None:
        try:
            existing = game_manager.get_profile(mod_id)
            exe_name = clean_mod["executables"][0] if clean_mod["executables"] else ""
            if not existing:
                game_manager.add_game(clean_mod["display_name"], exe_name)
                existing = game_manager.get_profile(mod_id)
            if isinstance(existing, dict):
                existing["module_id"] = mod_id
                if clean_mod.get("universe_name"):
                    existing["universe_name"] = clean_mod["universe_name"]
                if exe_name and not existing.get("process_name"):
                    existing["process_name"] = exe_name
                    existing["executable"] = exe_name
                existing["input_mode"] = clean_mod["input_mode"]
                existing["suppress_alerts_when_running"] = list(clean_mod["suppress_alerts_when_running"])
                if clean_mod.get("companion_apps"):
                    existing_apps = (
                        game_manager._normalize_companion_apps(existing.get("companion_apps"))
                        if hasattr(game_manager, "_normalize_companion_apps")
                        else (existing.get("companion_apps") if isinstance(existing.get("companion_apps"), list) else [])
                    )
                    by_id = {a["id"]: dict(a) for a in existing_apps if isinstance(a, dict) and a.get("id")}
                    by_exe = {
                        os.path.basename(str(a.get("executable") or "")).lower(): a["id"]
                        for a in existing_apps
                        if isinstance(a, dict) and a.get("executable")
                    }
                    for incoming in clean_mod["companion_apps"]:
                        inc_id = incoming.get("id")
                        inc_exe_low = os.path.basename(str(incoming.get("executable") or "")).lower()
                        match_id = inc_id if inc_id in by_id else by_exe.get(inc_exe_low)
                        if match_id and match_id in by_id:
                            # Preserve user's local .exe path while merging module metadata
                            merged_app = dict(incoming)
                            merged_app["id"] = match_id
                            merged_app["path"] = by_id[match_id].get("path", "")
                            by_id[match_id] = merged_app
                        elif inc_id:
                            new_app = dict(incoming)
                            new_app["path"] = ""
                            by_id[inc_id] = new_app
                    existing["companion_apps"] = list(by_id.values())
                if clean_mod.get("companion_resources"):
                    existing["companion_resources"] = dict(clean_mod["companion_resources"])
                if clean_mod.get("intel_endpoints"):
                    existing["intel_endpoints"] = dict(clean_mod["intel_endpoints"])
                if clean_mod.get("binds_scanner"):
                    existing["binds_scanner"] = dict(clean_mod["binds_scanner"])
                if clean_mod.get("navigation_mode"):
                    existing["navigation_mode"] = clean_mod["navigation_mode"]
                if clean_mod.get("extension"):
                    existing["extension"] = clean_mod["extension"]
                if clean_mod.get("map_controls"):
                    existing.setdefault("map_controls", {}).update(clean_mod["map_controls"])
                if clean_mod.get("keybinds"):
                    merged_binds = dict(clean_mod["keybinds"])
                    merged_binds.update(existing.get("keybinds") or {})
                    existing["keybinds"] = merged_binds
                if clean_mod.get("sequences"):
                    merged_seqs = dict(clean_mod["sequences"])
                    merged_seqs.update(existing.get("sequences") or {})
                    existing["sequences"] = merged_seqs
                existing["telemetry"] = dict(clean_mod["telemetry"])
                if hasattr(game_manager, "_save_profile"):
                    game_manager._save_profile(mod_id, existing)
                elif hasattr(game_manager, "save_profiles"):
                    game_manager.save_profiles()
        except Exception as e:
            logger.warning("Module '%s' saved, but profile merge warning: %s", mod_id, e)

    return {
        "success": True,
        "module_id": mod_id,
        "display_name": clean_mod["display_name"],
        "input_mode": clean_mod["input_mode"],
        "saved_path": saved_path,
        "module": clean_mod,
    }


def export_game_module(game_id: str, game_manager=None) -> Dict[str, Any]:
    """
    Exports a clean, shareable `.aethermodule.json` for `game_id`, stripping personal
    data (scratchpad notes, co-pilot logs, personal callsigns).
    """
    prof = game_manager.get_profile(game_id) if (game_manager and game_id) else None
    base_mod = get_module_for_game(game_id, profile=prof) or {}

    if not prof and not base_mod:
        return {"success": False, "error": f"No profile or module found for '{game_id}'."}

    prof = prof or {}
    norm_id = _normalize_id(game_id or base_mod.get("module_id") or "")
    exes: List[str] = list(base_mod.get("executables") or [])
    for k in ("process_name", "executable"):
        v = os.path.basename(str(prof.get(k) or "").strip())
        if v and v not in exes:
            exes.insert(0, v)

    merged_tel = dict(base_mod.get("telemetry") or {})
    if isinstance(prof.get("telemetry"), dict):
        merged_tel.update(prof["telemetry"])
    if "provider_type" not in merged_tel:
        merged_tel["provider_type"] = "vision_hud"

    raw_export = {
        "schema_version": 1,
        "module_id": norm_id,
        "display_name": prof.get("display_name") or base_mod.get("display_name") or norm_id.replace("_", " ").title(),
        "game_ids": list(dict.fromkeys([norm_id] + list(base_mod.get("game_ids") or []))),
        "executables": exes,
        "universe_name": prof.get("universe_name") or base_mod.get("universe_name") or "",
        "input_mode": prof.get("input_mode") or base_mod.get("input_mode") or "autonomous",
        "suppress_alerts_when_running": (
            list(prof["suppress_alerts_when_running"])
            if isinstance(prof.get("suppress_alerts_when_running"), list)
            else list(base_mod.get("suppress_alerts_when_running") or [])
        ),
        "companion_apps": (
            prof.get("companion_apps")
            if prof.get("companion_apps")
            else (base_mod.get("companion_apps") or [])
        ),
        "companion_resources": prof.get("companion_resources") or base_mod.get("companion_resources") or {},
        "intel_endpoints": prof.get("intel_endpoints") or base_mod.get("intel_endpoints") or {},
        "binds_scanner": prof.get("binds_scanner") or base_mod.get("binds_scanner") or {},
        "navigation_mode": prof.get("navigation_mode") or base_mod.get("navigation_mode") or "",
        "extension": prof.get("extension") or base_mod.get("extension") or "",
        "map_controls": prof.get("map_controls") or base_mod.get("map_controls") or {},
        "keybinds": prof.get("keybinds") or base_mod.get("keybinds") or {},
        "sequences": prof.get("sequences") or base_mod.get("sequences") or {},
        "telemetry": merged_tel,
    }
    ok, clean_mod, msg = validate_game_module(raw_export)
    if not ok or not clean_mod:
        return {"success": False, "error": msg}

    return {"success": True, "module_id": norm_id, "module": clean_mod}


def _detect_running_companions(game_id: str) -> Tuple[List[str], Optional[str]]:
    """Checks running processes for known 3rd-party co-pilots or localhost telemetry bridges."""
    suppress: List[str] = []
    detected_http: Optional[str] = None
    norm_id = _normalize_id(game_id)
    try:
        import psutil

        running_names = {(p.info.get("name") or "").strip().lower() for p in psutil.process_iter(["name"])}
    except Exception:
        running_names = set()

    for bridge in KNOWN_COMPANION_BRIDGES:
        proc_name = bridge["process"]
        if proc_name.lower() not in running_names:
            continue
        allowed_games = bridge.get("games") or []
        if allowed_games and norm_id not in allowed_games:
            continue
        if bridge.get("suppress_alerts") and proc_name not in suppress:
            suppress.append(proc_name)
        if bridge.get("http_endpoint") and not detected_http:
            detected_http = bridge["http_endpoint"]

    # Also check running companion processes declared in the matching `.aethermodule.json`
    mod = get_module_for_game(game_id)
    if isinstance(mod, dict):
        for proc_name in (mod.get("suppress_alerts_when_running") or []):
            if str(proc_name).lower() in running_names and proc_name not in suppress:
                suppress.append(proc_name)

    return suppress, detected_http


def auto_build_game_module(
    game_id: str,
    game_manager=None,
    genai_client=None,
    model_endpoint: str = "gemini-4-hydrogen",
    target_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Automatically drafts, validates, saves, and assimilates a `.aethermodule.json` for `game_id`:
    1. Starts from existing profile + any bundled module.
    2. Detects running 3rd-party companion apps / localhost telemetry bridges.
    3. Optionally uses Gemini with Google Search grounding to populate universe name,
       companion wiki/interactive map domains, and anti-cheat `spotter_only` recommendation.
    """
    prof = game_manager.get_profile(game_id) if (game_manager and game_id) else None
    existing_export = export_game_module(game_id, game_manager=game_manager)
    if existing_export.get("success"):
        draft = dict(existing_export["module"])
    else:
        norm_id = _normalize_id(game_id)
        draft = {
            "schema_version": 1,
            "module_id": norm_id,
            "display_name": (prof or {}).get("display_name") or norm_id.replace("_", " ").title(),
            "game_ids": [norm_id],
            "executables": [],
            "universe_name": "",
            "input_mode": "autonomous",
            "suppress_alerts_when_running": [],
            "companion_resources": {},
            "map_controls": {},
            "keybinds": {},
            "sequences": {},
            "telemetry": {"adapter": "none", "provider_type": "vision_hud", "poll_interval_sec": 0.5},
        }

    notes: List[str] = []

    # 1. Detect running 3rd-party companions / bridges
    found_suppress, found_http = _detect_running_companions(game_id)
    for proc_name in found_suppress:
        if proc_name not in draft["suppress_alerts_when_running"]:
            draft["suppress_alerts_when_running"].append(proc_name)
            notes.append(f"Detected running co-pilot {proc_name} (added to alert suppression).")
    if found_http and draft["telemetry"].get("provider_type") in ("vision_hud", "none", None):
        draft["telemetry"]["provider_type"] = "localhost_http"
        draft["telemetry"]["http_endpoint"] = found_http
        notes.append(f"Detected localhost telemetry bridge: {found_http}.")

    # 2. Optional web-grounded enrichment (universe, wiki/map domains, anti-cheat safety)
    if genai_client is not None:
        try:
            from google.genai import types
            from core.model_discovery import resolve_model_profile

            resolved_model = resolve_model_profile(model_endpoint, default_model="gemini-4-hydrogen")[0]
            prompt = (
                f'For the PC video game "{draft["display_name"]}", return strictly a JSON object with:\n'
                f'  "universe_name": short setting/world name (e.g. "Pywel", "Night City", "Milky Way"),\n'
                f'  "wiki_domain": primary wiki or interactive map domain (e.g. "fextralife.com", "inara.cz", "mapgenie.io"),\n'
                f'  "uses_kernel_anticheat": true if multiplayer with EAC/BattlEye/Vanguard/Ricochet else false,\n'
                f'  "anticheat_name": name of anti-cheat or ""\n'
                f"Return ONLY valid JSON."
            )
            cfg_kwargs: Dict[str, Any] = {
                "tools": [types.Tool(google_search=types.GoogleSearch())],
                "temperature": 0.0,
            }
            if hasattr(types, "ThinkingConfig"):
                try:
                    cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="low")
                except Exception:
                    pass
            resp = genai_client.models.generate_content(
                model=resolved_model,
                contents=prompt,
                config=types.GenerateContentConfig(**cfg_kwargs),
            )
            raw_txt = (resp.text or "").strip() if resp and hasattr(resp, "text") else ""
            if raw_txt.startswith("```"):
                raw_txt = re.sub(r"^```(?:json)?\s*", "", raw_txt)
                raw_txt = re.sub(r"\s*```$", "", raw_txt)
            # Extract JSON block if wrapped in prose
            m = re.search(r"\{[\s\S]*\}", raw_txt)
            info = json.loads(m.group(0)) if m else {}
            if info.get("universe_name") and not draft.get("universe_name"):
                draft["universe_name"] = str(info["universe_name"]).strip()
                notes.append(f"Universe set to '{draft['universe_name']}'.")
            wiki_dom = str(info.get("wiki_domain") or "").strip().replace("https://", "").replace("http://", "").split("/")[0]
            if wiki_dom and "lore" not in draft["companion_resources"]:
                draft["companion_resources"]["lore"] = {"name": wiki_dom, "domain": wiki_dom}
                notes.append(f"Linked companion resource '{wiki_dom}'.")
            ac_name = str(info.get("anticheat_name") or "").lower()
            if info.get("uses_kernel_anticheat") or any(k in ac_name for k in ANTI_CHEAT_KEYWORDS):
                draft["input_mode"] = "spotter_only"
                notes.append(
                    f"Kernel anti-cheat detected ({info.get('anticheat_name') or 'multiplayer'}): defaulted input_mode to 'spotter_only'."
                )
        except Exception as e:
            logger.debug("Auto-build web enrichment skipped for '%s': %s", game_id, e)

    res = import_game_module(draft, game_manager=game_manager, target_dir=target_dir)
    if res.get("success"):
        res["notes"] = notes
    return res


def build_module_authoring_prompt(game_name: str = "") -> str:
    """
    Generates a ready-to-paste LLM prompt that instructs any frontier AI (Gemini, ChatGPT, Claude)
    to output a strictly valid, schema-compliant `.aethermodule.json` file for Aether Desktop.
    """
    target = str(game_name or "").strip() or "<INSERT GAME NAME HERE>"
    norm_example = _normalize_id(target) if target != "<INSERT GAME NAME HERE>" else "my_game"
    return f"""You are creating a declarative Tier 1 Game Module (`.aethermodule.json`) for **Aether Desktop**, a 100% game-agnostic AI voice co-pilot.

TARGET GAME: **{target}**

Return ONLY a single valid JSON object (no markdown fences, no commentary before or after) that conforms strictly to Aether Desktop's `schema_version: 1` specification below:

### REQUIRED & OPTIONAL TOP-LEVEL FIELDS
- `"schema_version"`: `1` (required)
- `"module_id"`: lowercase snake_case ID, e.g. `"{norm_example}"` (required)
- `"display_name"`: exact human-readable game title, e.g. `"{target}"` (required)
- `"game_ids"`: array of lowercase ID aliases, e.g. `["{norm_example}"]`
- `"executables"`: array of PC `.exe` filenames (e.g. `["Game.exe"]`)
- `"universe_name"`: in-game setting/world name (e.g. `"Milky Way"`, `"Night City"`, `"Tamriel"`)
- `"default_callsign"`: optional default player title (e.g. `"Commander"`, `"Captain"`, or `""` for none)
- `"input_mode"`: `"autonomous"` for single-player / PvE / sim games, OR `"spotter_only"` for competitive multiplayer games with kernel anti-cheat (EAC, BattlEye, Vanguard, Ricochet)
- `"navigation_mode"`: `"search_bar"` | `"mouse_keyboard"` | `"controller"` | `"co_pilot_spotter"`
- `"suppress_alerts_when_running"`: optional list of 3rd-party co-pilot `.exe` names to avoid duplicate alerts
- `"companion_resources"`: optional dict of `{{category: {{"name": "...", "domain": "..."}}}}` (wiki/database domains)

### OPTIONAL `map_controls`
- `"preferred_input"`: `"mouse_keyboard"` or `"gamepad"`
- `"pan_method"`: `"left_drag"` | `"right_drag"` | `"wasd"`
- `"marker_method"`: `"right_click"` | `"left_click"`
- `"zoom_method"`: `"mouse_wheel"`
- `"search_routing"` (only if the game has a text-searchable map):
  - `"map_open_actions"`: list of keybind phrases that open the map
  - `"map_key"`: fallback single key (e.g. `"m"`)
  - `"open_wait_sec"`: float (e.g. `1.2`)
  - `"search_box_visual_hint"`: visual description of the search input box
  - `"input_method"`: `"clipboard_paste"` or `"directinput"`
  - `"submit_key"`: `"enter"` or `""`
  - `"post_input_keys"`: optional list of `{{"key": "down", "delay_ms": 300}}`
  - `"prefer_plot_key_first"`: boolean
  - `"pre_plot_move_center"`: boolean
  - `"pre_plot_mouse_nudge"`: `[dx, dy]` (e.g. `[15, 10]`)
  - `"plot_visual_hint"`: visual description of the Plot/Route button
  - `"plot_key"`: key to plot/confirm (e.g. `"space"`)
  - `"plot_hold_sec"`: float (e.g. `1.0`)
  - `"verify_telemetry_field"`: optional live telemetry state key to confirm route lock

### OPTIONAL `binds_scanner` (Declarative Local Keybinds Auto-Import)
- Supports `"format"`: `"xml_primary_secondary"` | `"ini_kv"` | `"json_kv"`
- `"dir"`: Windows config folder (supports `%LOCALAPPDATA%`, `%APPDATA%`, `%USERPROFILE%`)
- `"file_glob"`: e.g. `"*.binds"`, `"*.ini"`, `"*.json"`
- `"auto_import_all"`: `true`
- `"actions"`: optional curated voice phrases & aliases mapping raw action IDs to natural phrases

### OPTIONAL `keybinds` & `sequences` (Starter Default Binds & Multi-Step Macros)
- `"keybinds"`: `{{"voice phrase": {{"key": "m", "modifiers": [], "description": "Open Map", "aliases": ["map"]}}}}`
- `"sequences"`: `{{"macro voice phrase": {{"description": "...", "aliases": ["..."], "condition": "", "steps": [{{"action": "press", "key": "w", "repeat": 3, "interval_ms": 150}}, {{"action": "press", "key": "space"}}]}}}}`
  - Valid step actions: `"press"`, `"trigger_bind"`, `"vision_click"`, `"click"`, `"type"`, `"paste"`, `"wait"`

### OPTIONAL `intel_endpoints` (Public HTTPS REST APIs Only!)
- Security rule: EVERY `"url"` MUST be a public `https://` URL (no `http://`, no localhost, no private IPs).
- Each category (`"market"`, `"outfitting"`, `"services"`, `"navigation"`, etc.) defines `"method"` (`"GET"` or `"POST"`), `"url"`, `"body_template"` / `"query_params"`, `"results_path"`, and `"result_fields"`.

### OPTIONAL `telemetry` (100% Declarative — Zero Python Required!)
- `"adapter"`: `"declarative_file"` (for JSON status files + rolling JSONL logs) or `"none"`
- `"provider_type"`: `"native_file"` | `"localhost_http"` | `"vision_hud"` | `"none"`
  - Security rule: `"http_endpoint"` MUST be strictly `http://127.0.0.1` or `http://localhost`.
- For `"declarative_file"`, you may specify `"dir"`, `"status_file"`, `"journal_glob"`, `"bitmask_mappings"`, `"flag_to_state"`, `"status_fields"`, `"companion_extractors"`, `"event_handlers"`, `"milestone_rules"`, `"initial_state"`, `"initial_flags"`, `"location_fields"`, and `"alert_rules"`.

Now research **{target}** carefully and output ONLY the complete, valid `.aethermodule.json` object."""

