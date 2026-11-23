"""
core/telemetry/declarative.py - 100% Game-Agnostic Declarative File & JSONL Telemetry Adapter.

Reads any game's live status JSON file, sibling companion JSON files, and rolling JSONL
event logs using purely declarative mappings defined in the game's `.aethermodule.json`.
Zero game-specific logic, bitmasks, ship lists, or event names are hardcoded in Python.
"""

import glob
import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from core.logger import get_logger
from core.telemetry.base import BaseTelemetryAdapter

logger = get_logger("DeclarativeTelemetry")


def _extract_path(obj: Any, path_spec: Any) -> Any:
    """
    Resolves a dotted path (supporting negative integer list indices like `Route.-1.StarSystem`)
    or a fallback list of candidate paths (`["SystemSecurity_Localised", "SystemSecurity"]`).
    """
    if isinstance(path_spec, list):
        for candidate in path_spec:
            val = _extract_path(obj, candidate)
            if val is not None and val != "":
                return val
        return None
    if not isinstance(path_spec, str) or not path_spec.strip():
        return None

    cur = obj
    for part in path_spec.split("."):
        if isinstance(cur, dict):
            if part in cur:
                cur = cur[part]
            else:
                return None
        elif isinstance(cur, list):
            try:
                idx = int(part)
                cur = cur[idx]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


class DeclarativeFileTelemetryAdapter(BaseTelemetryAdapter):
    """
    Universal file-backed telemetry adapter driven entirely by `.aethermodule.json`.

    Supported `profile["telemetry"]` keys:
    - `dir`: directory containing status/companion/journal files (env vars expanded)
    - `status_file` or `native_file_path`: live polled JSON status file
    - `journal_glob`: glob pattern for rolling JSONL event logs (e.g. `"Journal.*.log"`)
    - `event_field`: event name field in JSONL lines (default: `"event"`)
    - `rehydrate_lines`: number of trailing lines to replay on initial sync (default: 150)
    - `poll_interval_sec`: status file poll interval (default: 0.25)
    - `initial_state`: default state dict seeded before events arrive
    - `initial_flags`: default boolean flags dict
    - `bitmask_mappings`: `{field_name: {flag_name: int_mask}}`
    - `flag_to_state`: list of flag names to mirror into `self.state`
    - `status_fields`: `{state_key: path_or_spec}` extracted from the status JSON
    - `companion_extractors`: list of sibling JSON file extraction rules
    - `event_handlers`: list of JSONL event rules (`events`, `set_state`, `set_flags`, `constants`, `clear_state`, `derived_rules`)
    - `milestone_rules`: list of JSONL milestone notification rules
    - `location_fields`: `{system, station, body, star_pos}` mapping into `self.state`
    - `summary_config`: declarative formatting options for `get_summary_prompt_context()`
    """

    def __init__(
        self,
        game_id: str,
        profile: Optional[Dict[str, Any]] = None,
        on_milestone: Optional[Callable[[str, str, str, str], None]] = None,
        journal_dir: Optional[str] = None,
    ):
        super().__init__(game_id, profile=profile, on_milestone=on_milestone)
        self._tel_cfg: Dict[str, Any] = self._resolve_telemetry_config(game_id, profile or {})

        raw_dir = (
            journal_dir
            or self._tel_cfg.get("dir")
            or (self.profile.get("telemetry") or {}).get("dir")
            or self.profile.get("journal_dir")
            or self.profile.get("telemetry_dir")
            or ""
        )
        if not raw_dir and self._tel_cfg.get("native_file_path"):
            raw_dir = os.path.dirname(os.path.expandvars(str(self._tel_cfg["native_file_path"])))
        self.telemetry_dir = os.path.expandvars(str(raw_dir)) if raw_dir else ""
        # Back-compat attribute alias
        self.journal_dir = self.telemetry_dir

        status_name = str(self._tel_cfg.get("status_file") or "").strip()
        if not status_name and self._tel_cfg.get("native_file_path"):
            status_name = os.path.basename(os.path.expandvars(str(self._tel_cfg["native_file_path"])))
        self.status_file_path = (
            os.path.join(self.telemetry_dir, status_name)
            if (self.telemetry_dir and status_name)
            else ""
        )

        self._journal_glob = str(self._tel_cfg.get("journal_glob") or "").strip()
        self._event_field = str(self._tel_cfg.get("event_field") or "event").strip()
        self._rehydrate_lines = max(1, int(self._tel_cfg.get("rehydrate_lines") or 150))
        self._poll_interval = max(0.05, float(self._tel_cfg.get("poll_interval_sec") or 0.25))

        self._thread: Optional[threading.Thread] = None
        self._current_file: Optional[str] = None
        self._file_handle = None
        self._lock = threading.Lock()

        display_name = (
            self.profile.get("display_name")
            or self._tel_cfg.get("display_name")
            or game_id.replace("_", " ").title()
        )
        self._display_name = str(display_name)

        self.state: Dict[str, Any] = {
            "game": self._display_name,
            "game_id": game_id,
            "active": False,
            "last_event": None,
            "last_updated": 0.0,
        }
        init_state = self._tel_cfg.get("initial_state")
        if isinstance(init_state, dict):
            for k, v in init_state.items():
                self.state[k] = list(v) if isinstance(v, list) else (dict(v) if isinstance(v, dict) else v)

        self.flags: Dict[str, bool] = {}
        init_flags = self._tel_cfg.get("initial_flags")
        if isinstance(init_flags, dict):
            for k, v in init_flags.items():
                self.flags[str(k)] = bool(v)

    @staticmethod
    def _resolve_telemetry_config(game_id: str, profile: Dict[str, Any]) -> Dict[str, Any]:
        prof_tel = profile.get("telemetry") if isinstance(profile.get("telemetry"), dict) else {}
        if (
            prof_tel.get("bitmask_mappings")
            or prof_tel.get("event_handlers")
            or prof_tel.get("journal_glob")
            or prof_tel.get("status_fields")
        ):
            return dict(prof_tel)
        try:
            from core.game_modules import get_module_for_game
            mod = get_module_for_game(game_id, profile=profile)
            mod_tel = mod.get("telemetry") if isinstance(mod, dict) and isinstance(mod.get("telemetry"), dict) else {}
            if mod_tel:
                merged = dict(mod_tel)
                merged.update({k: v for k, v in prof_tel.items() if v is not None})
                if isinstance(mod, dict) and mod.get("display_name") and not merged.get("display_name"):
                    merged["display_name"] = mod["display_name"]
                return merged
        except Exception:
            pass
        return dict(prof_tel)

    def start(self) -> None:
        if self.is_running:
            return
        if not self.telemetry_dir or not os.path.exists(self.telemetry_dir):
            logger.warning("Telemetry directory does not exist for '%s': %s", self.game_id, self.telemetry_dir)
            return
        self.is_running = True
        self._thread = threading.Thread(
            target=self._tail_loop,
            daemon=True,
            name=f"DeclarativeTelemetry_{self.game_id}",
        )
        self._thread.start()
        logger.info("Started DeclarativeFileTelemetryAdapter watcher for '%s'.", self.game_id)

    def stop(self) -> None:
        self.is_running = False
        th = self._thread
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=0.5)
        if self._file_handle and (th is None or not th.is_alive()):
            try:
                self._file_handle.close()
            except Exception:
                pass
            self._file_handle = None

    def is_active(self) -> bool:
        with self._lock:
            return bool(self.state.get("active"))

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.state)

    def get_state_flags(self) -> Dict[str, bool]:
        with self._lock:
            return dict(self.flags)

    def get_location(self) -> Dict[str, Any]:
        loc_map = self._tel_cfg.get("location_fields") if isinstance(self._tel_cfg.get("location_fields"), dict) else {}
        sys_key = str(loc_map.get("system") or "star_system")
        stn_key = str(loc_map.get("station") or "station")
        body_key = str(loc_map.get("body") or "body")
        pos_key = str(loc_map.get("star_pos") or "star_pos")
        with self._lock:
            sys_val = self.state.get(sys_key) or self.state.get("zone") or self.state.get("region")
            return {
                "system": sys_val if sys_val and sys_val != "Unknown" else None,
                "station": self.state.get(stn_key),
                "body": self.state.get(body_key),
                "star_pos": self.state.get(pos_key),
            }

    def _get_latest_journal_path(self) -> Optional[str]:
        if not self.telemetry_dir or not self._journal_glob or not os.path.exists(self.telemetry_dir):
            return None
        files = glob.glob(os.path.join(self.telemetry_dir, self._journal_glob))
        if not files:
            return None
        return max(files, key=os.path.getmtime)

    def _tail_loop(self) -> None:
        last_status_poll = 0.0
        try:
            while self.is_running:
                try:
                    now = time.time()
                    if now - last_status_poll >= self._poll_interval:
                        self._poll_status_file()
                        last_status_poll = now

                    if not self.is_running:
                        break

                    if self._journal_glob:
                        latest = self._get_latest_journal_path()
                        if not latest:
                            time.sleep(0.25)
                            continue

                        if latest != self._current_file:
                            if self._file_handle:
                                try:
                                    self._file_handle.close()
                                except Exception:
                                    pass
                            self._current_file = latest
                            self._file_handle = open(self._current_file, "r", encoding="utf-8", errors="replace")
                            self._rehydrate_initial_state()

                        if not self.is_running or self._file_handle is None:
                            break

                        line = self._file_handle.readline()
                        if line:
                            self._parse_event_line(line.strip())
                        else:
                            time.sleep(0.2)
                    else:
                        time.sleep(self._poll_interval)
                except Exception as exc:
                    if not self.is_running:
                        break
                    logger.error("Error in declarative telemetry loop for '%s': %s", self.game_id, exc)
                    time.sleep(0.5)
        finally:
            if self._file_handle:
                try:
                    self._file_handle.close()
                except Exception:
                    pass
                self._file_handle = None

    def _poll_status_file(self) -> None:
        if not self.status_file_path or not os.path.exists(self.status_file_path):
            self._poll_companion_files()
            return
        try:
            with open(self.status_file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read().strip()
                if not content:
                    return
                data = json.loads(content)

            bitmasks = self._tel_cfg.get("bitmask_mappings") or {}
            flag_to_state = self._tel_cfg.get("flag_to_state") or []
            status_fields = self._tel_cfg.get("status_fields") or {}

            with self._lock:
                if isinstance(bitmasks, dict):
                    for field_name, flag_map in bitmasks.items():
                        if field_name not in data or not isinstance(flag_map, dict):
                            continue
                        try:
                            raw_bits = int(data.get(field_name) or 0)
                        except (TypeError, ValueError):
                            continue
                        for flag_name, mask in flag_map.items():
                            self.flags[str(flag_name)] = bool(raw_bits & int(mask))

                for fname in flag_to_state:
                    if fname in self.flags:
                        self.state[fname] = self.flags[fname]

                if isinstance(status_fields, dict):
                    for state_key, spec in status_fields.items():
                        if isinstance(spec, str):
                            val = _extract_path(data, spec)
                            if val is not None:
                                self.state[state_key] = val
                        elif isinstance(spec, dict):
                            val = _extract_path(data, spec.get("path"))
                            if val is None:
                                continue
                            if isinstance(val, list) and spec.get("list_scale") is not None:
                                scale = float(spec["list_scale"])
                                val = [float(x) * scale for x in val]
                            elif spec.get("scale") is not None and isinstance(val, (int, float)):
                                val = float(val) * float(spec["scale"])
                            self.state[state_key] = val

                self.state["active"] = True
                self.state["last_updated"] = time.time()

            self._poll_companion_files()
        except Exception:
            pass

    def _poll_companion_files(self) -> None:
        if not self.telemetry_dir:
            return
        extractors = self._tel_cfg.get("companion_extractors")
        if not isinstance(extractors, list) or not extractors:
            return

        for ext_cfg in extractors:
            if not isinstance(ext_cfg, dict):
                continue
            fname = str(ext_cfg.get("file") or "").strip()
            if not fname:
                continue
            cpath = os.path.join(self.telemetry_dir, fname)
            if not os.path.isfile(cpath):
                continue
            try:
                with open(cpath, "r", encoding="utf-8", errors="replace") as f:
                    raw = f.read().strip()
                if not raw:
                    continue
                cdata = json.loads(raw)
                if not isinstance(cdata, dict):
                    continue
                fields = ext_cfg.get("fields") or {}
                if not isinstance(fields, dict):
                    continue
                with self._lock:
                    for state_key, fspec in fields.items():
                        if isinstance(fspec, str):
                            val = _extract_path(cdata, fspec)
                            if val is not None:
                                self.state[state_key] = val
                        elif isinstance(fspec, dict):
                            if "list_len_minus_1" in fspec:
                                arr = _extract_path(cdata, fspec["list_len_minus_1"])
                                if isinstance(arr, list) and arr:
                                    self.state[state_key] = max(0, len(arr) - 1)
                            elif "list_map" in fspec:
                                arr = _extract_path(cdata, fspec["list_map"])
                                if isinstance(arr, list):
                                    limit = int(fspec.get("limit") or 15)
                                    sub_fields = fspec.get("fields") or {}
                                    mapped = []
                                    for item in arr[:limit]:
                                        if not isinstance(item, dict):
                                            continue
                                        entry = {}
                                        for out_k, sub_path in sub_fields.items():
                                            entry[out_k] = _extract_path(item, sub_path)
                                        mapped.append(entry)
                                    self.state[state_key] = mapped
                            elif "path" in fspec:
                                val = _extract_path(cdata, fspec["path"])
                                if val is not None:
                                    cast = str(fspec.get("cast") or "").lower()
                                    if cast == "int":
                                        val = int(val)
                                    elif cast == "float":
                                        val = float(val)
                                    elif cast == "str":
                                        val = str(val)
                                    self.state[state_key] = val
            except Exception:
                pass

    def _rehydrate_initial_state(self) -> None:
        if not self._file_handle:
            return
        try:
            self._file_handle.seek(0)
            lines = self._file_handle.readlines()
            for ln in lines[-self._rehydrate_lines:]:
                self._parse_event_line(ln.strip(), is_initial_sync=True)
            self._file_handle.seek(0, os.SEEK_END)
        except Exception as exc:
            logger.warning("Declarative telemetry rehydration failed for '%s': %s", self.game_id, exc)

    def _apply_derived_rules(self, derived_rules: List[Dict[str, Any]]) -> None:
        for rule in derived_rules:
            if not isinstance(rule, dict):
                continue
            src_field = str(rule.get("source_state") or "").strip()
            target_field = str(rule.get("target_state") or "").strip()
            if not src_field or not target_field:
                continue
            raw_val = str(self.state.get(src_field) or "").strip().lower()
            matched_out = rule.get("default")
            for case in (rule.get("cases") or []):
                if not isinstance(case, dict):
                    continue
                exact = [str(x).lower() for x in (case.get("in") or [])]
                substrs = [str(x).lower() for x in (case.get("contains_any") or [])]
                if (raw_val and raw_val in exact) or (raw_val and any(tok in raw_val for tok in substrs)):
                    matched_out = case.get("value")
                    break
            self.state[target_field] = matched_out
            if rule.get("bool_equals_state") and isinstance(rule["bool_equals_state"], dict):
                for b_key, expected in rule["bool_equals_state"].items():
                    self.state[b_key] = (matched_out == expected)

    def _parse_event_line(self, line: str, is_initial_sync: bool = False) -> None:
        if not line:
            return
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(ev, dict):
            return

        event_type = ev.get(self._event_field)
        handlers = self._tel_cfg.get("event_handlers") or []
        milestones = self._tel_cfg.get("milestone_rules") or []

        with self._lock:
            self.state["last_event"] = event_type
            self.state["last_updated"] = time.time()
            self.state["active"] = True

            for handler in handlers:
                if not isinstance(handler, dict):
                    continue
                target_events = handler.get("events") or []
                if event_type not in target_events:
                    continue

                # 1. Fixed constant state updates
                for k, v in (handler.get("constants") or {}).items():
                    self.state[k] = v

                # 2. Clear specified state keys (reset to None)
                for k in (handler.get("clear_state") or []):
                    self.state[k] = None

                # 3. Extract fields from JSON event into state
                for state_key, fspec in (handler.get("set_state") or {}).items():
                    if isinstance(fspec, dict):
                        val = _extract_path(ev, fspec.get("path"))
                        if val is None and "default" in fspec:
                            val = fspec["default"]
                        if val is not None or fspec.get("allow_none", False):
                            if fspec.get("require_len") is not None:
                                if not (isinstance(val, list) and len(val) == int(fspec["require_len"])):
                                    continue
                            self.state[state_key] = val
                    else:
                        val = _extract_path(ev, fspec)
                        if val is not None:
                            self.state[state_key] = val

                # 4. Extract fields into boolean flags
                for flag_key, fspec in (handler.get("set_flags") or {}).items():
                    if isinstance(fspec, bool):
                        self.flags[flag_key] = fspec
                    else:
                        val = _extract_path(ev, fspec)
                        if val is not None:
                            self.flags[flag_key] = bool(val)

                # 5. Evaluate derived classification rules (e.g. pad size)
                if isinstance(handler.get("derived_rules"), list):
                    self._apply_derived_rules(handler["derived_rules"])

            state_snapshot = dict(self.state)

        # Emit declarative milestones only during live play
        if not is_initial_sync and self.on_milestone and callable(self.on_milestone) and milestones:
            loc_info = self.get_location()
            loc_sys = loc_info.get("system") or state_snapshot.get("star_system") or "Unknown"
            loc_stn = loc_info.get("station")
            curr_loc = f"{loc_sys}, {loc_stn}" if loc_stn else str(loc_sys)

            for mrule in milestones:
                if not isinstance(mrule, dict):
                    continue
                if event_type not in (mrule.get("events") or []):
                    continue
                ctx = _SafeFormatDict({**state_snapshot, **ev})
                for alias_key, path_spec in (mrule.get("fields") or {}).items():
                    val = _extract_path(ev, path_spec)
                    if val is not None:
                        ctx[alias_key] = val
                req_field = mrule.get("require_field")
                if req_field and not ctx.get(req_field):
                    continue
                template = str(mrule.get("template") or "").strip()
                if not template:
                    continue
                try:
                    summary = template.format_map(ctx).strip()
                except Exception:
                    continue
                if summary:
                    cat = str(mrule.get("category") or "intel")
                    self.on_milestone(cat, summary, curr_loc, line)

    def get_summary_prompt_context(self) -> str:
        with self._lock:
            if not self.state.get("active"):
                return f"Active Game: {self._display_name} (Waiting for live telemetry link...)"

            st = self.state
            fl = self.flags
            lines = [f"[LIVE GAME TELEMETRY: {self._display_name}]"]

            # Location & status
            loc_sys = st.get("star_system") or st.get("zone") or st.get("region")
            loc_body = st.get("body")
            if st.get("docked") and st.get("station"):
                status_desc = f"Docked at {st['station']}"
            elif fl.get("landed") and loc_body:
                status_desc = f"Landed on {loc_body}"
            elif st.get("supercruise"):
                status_desc = "in Supercruise"
            elif st.get("status_text"):
                status_desc = str(st["status_text"])
            else:
                status_desc = "Active"

            if loc_sys:
                near_str = f" (Near: {loc_body})" if loc_body and loc_body != "Unknown" else ""
                lines.append(f"- Location: {loc_sys}{near_str} | Status: {status_desc}")

            # System control / faction / allegiance / powerplay (if present in telemetry)
            ctrl_parts = []
            if st.get("controlling_power"):
                pstate = f" ({st['powerplay_state']})" if st.get("powerplay_state") else ""
                ctrl_parts.append(f"Powerplay Control: {st['controlling_power']}{pstate}")
            elif isinstance(st.get("powers"), list) and st["powers"]:
                pstate = f" ({st['powerplay_state']})" if st.get("powerplay_state") else ""
                ctrl_parts.append(f"Powerplay Presence: {', '.join(str(p) for p in st['powers'][:3])}{pstate}")
            if st.get("system_faction"):
                ctrl_parts.append(f"Controlling Faction: {st['system_faction']}")
            if st.get("system_allegiance"):
                ctrl_parts.append(f"Allegiance: {st['system_allegiance']}")
            if st.get("system_security"):
                ctrl_parts.append(f"Security: {st['system_security']}")
            if ctrl_parts:
                lines.append("- System Control: " + " | ".join(ctrl_parts))

            # Vehicle / character status
            if st.get("ship_name") or st.get("ship_type"):
                hull_val = st.get("hull_health")
                hull_str = f" | Hull: {int(float(hull_val) * 100)}%" if isinstance(hull_val, (int, float)) else ""
                shields_str = (
                    f" | Shields: {'ONLINE' if st.get('shields_up', True) else 'OFFLINE'}"
                    if "shields_up" in st
                    else ""
                )
                lines.append(f"- Vessel: {st.get('ship_name', 'Unknown')} ({st.get('ship_type', 'Unknown')}){hull_str}{shields_str}")

            # Compact active toggle flags summary (from declared summary_flags or key flags)
            summary_flag_labels = (self._tel_cfg.get("summary_config") or {}).get("flag_labels") or {}
            if isinstance(summary_flag_labels, dict) and summary_flag_labels:
                flag_parts = []
                for fkey, label_pair in summary_flag_labels.items():
                    if isinstance(label_pair, dict):
                        lbl = label_pair.get("label", fkey)
                        val_txt = label_pair.get("on", "ON") if fl.get(fkey) else label_pair.get("off", "OFF")
                        flag_parts.append(f"{lbl}: {val_txt}")
                if flag_parts:
                    lines.append("- Systems: " + " | ".join(flag_parts))

            if st.get("cargo_count") is not None or st.get("pips") is not None:
                extra_parts = []
                if st.get("cargo_count") is not None:
                    extra_parts.append(f"Cargo: {st['cargo_count']} items")
                if st.get("pips") is not None:
                    extra_parts.append(f"Pips: {st['pips']}")
                if extra_parts:
                    lines.append("- " + " | ".join(extra_parts))

            if st.get("route_destination"):
                lines.append(f"- Plotted Route: {st['route_destination']} ({st.get('route_jumps', 0)} jumps)")

            if st.get("last_event"):
                lines.append(f"- Last In-Game Event: {st['last_event']}")

            return "\n".join(lines)
