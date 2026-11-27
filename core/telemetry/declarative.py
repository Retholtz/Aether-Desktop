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
        self.on_project_update: Optional[Callable[[str, Dict[str, Any]], None]] = None
        self.on_poi_update: Optional[Callable[[str, Dict[str, Any]], None]] = None
        self._last_threshold_values: Dict[str, float] = {}
        self._last_route_destination: Optional[str] = None

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

    def _get_sorted_journal_paths(self) -> List[str]:
        if not self.telemetry_dir or not self._journal_glob or not os.path.exists(self.telemetry_dir):
            return []
        files = glob.glob(os.path.join(self.telemetry_dir, self._journal_glob))
        if not files:
            return []
        return sorted(files, key=os.path.getmtime)

    def _get_latest_journal_path(self) -> Optional[str]:
        paths = self._get_sorted_journal_paths()
        return paths[-1] if paths else None

    @staticmethod
    def _apply_poi_tracker_rule(ev: Dict[str, Any], ptracker: Dict[str, Any], tracked: Dict[str, Dict[str, Any]]) -> Optional[str]:
        """Applies one declarative poi_tracker rule to `tracked` in-place (100% game-agnostic). Returns resolved poi_id if updated."""
        match_eq = ptracker.get("match_equals")
        if isinstance(match_eq, dict):
            if any(str(_extract_path(ev, mk) or "").lower() != str(mv).lower() for mk, mv in match_eq.items()):
                return None
        match_in = ptracker.get("match_in")
        if isinstance(match_in, dict):
            for mk, allowed_vals in match_in.items():
                if not isinstance(allowed_vals, (list, tuple, set)):
                    continue
                actual = str(_extract_path(ev, mk) or "").strip().lower()
                allowed_low = {str(v).strip().lower() for v in allowed_vals}
                if actual not in allowed_low:
                    return None
        base_poi_id = str(ptracker.get("id") or "").strip()
        if not base_poi_id:
            return None

        eid_val = _extract_path(ev, ptracker.get("entity_id_field"))
        eid_str = str(eid_val).strip() if eid_val is not None and str(eid_val).strip() else ""
        poi_id = f"{base_poi_id}_{eid_str}" if (ptracker.get("key_by_entity_id") and eid_str) else base_poi_id
        existing = dict(tracked.get(poi_id) or {})

        # Optional entity-ID correlation (e.g. match MarketID == tracked CarrierID)
        match_eid_field = ptracker.get("match_entity_id_field")
        if match_eid_field:
            expected_eid = existing.get("entity_id")
            ev_eid = _extract_path(ev, match_eid_field)
            if not expected_eid or ev_eid is None or str(ev_eid) != str(expected_eid):
                return None

        # Optional callsign substring correlation (e.g. SignalName contains tracked Callsign)
        match_cs_field = ptracker.get("match_contains_callsign_field")
        if match_cs_field:
            expected_cs = str(existing.get("callsign") or "").strip()
            haystack = str(_extract_path(ev, match_cs_field) or "").strip()
            if not expected_cs or expected_cs.lower() not in haystack.lower():
                return None

        name_val = _extract_path(ev, ptracker.get("name_field"))
        callsign_val = _extract_path(ev, ptracker.get("callsign_field"))
        sys_val = _extract_path(ev, ptracker.get("system_field"))
        body_val = _extract_path(ev, ptracker.get("body_field"))

        extra_aliases: List[str] = []
        if eid_str:
            existing["entity_id"] = eid_str
        if callsign_val:
            existing["callsign"] = str(callsign_val).strip()
        if name_val:
            raw_name = str(name_val).strip()
            clean_name = raw_name
            if ptracker.get("strip_callsign_from_name") and existing.get("callsign"):
                cs = str(existing["callsign"]).strip()
                if cs and clean_name.lower().endswith(cs.lower()):
                    clean_name = clean_name[: -len(cs)].strip(" -–—")
            strip_prefixes = ptracker.get("strip_prefixes")
            if isinstance(strip_prefixes, list):
                for pref in strip_prefixes:
                    p_str = str(pref or "")
                    if p_str and clean_name.lower().startswith(p_str.lower()):
                        trimmed = clean_name[len(p_str):].strip()
                        if trimmed:
                            extra_aliases.append(raw_name)
                            clean_name = trimmed
                        break
            if clean_name:
                existing["name"] = clean_name
        if sys_val:
            existing["system"] = str(sys_val).strip()
        if body_val:
            existing["body"] = str(body_val).strip()
        if isinstance(ptracker.get("aliases"), list) or extra_aliases:
            new_al = [str(a).strip() for a in (ptracker.get("aliases") or []) if str(a).strip()] + extra_aliases
            existing["aliases"] = list(dict.fromkeys((existing.get("aliases") or []) + new_al))
        if existing.get("system") or existing.get("name") or existing.get("callsign") or existing.get("entity_id"):
            tracked[poi_id] = existing
            return poi_id
        return None

    @staticmethod
    def _apply_project_tracker_rule(
        ev: Dict[str, Any],
        proj_cfg: Dict[str, Any],
        projects: Dict[str, Dict[str, Any]],
        state_snapshot: Dict[str, Any],
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Applies one declarative `project_trackers` rule (resource-checklist snapshot or
        incremental contribution delivery) in-place (100% game-agnostic).
        Returns `(proj_id, project_dict)` when updated, else `None`.
        """
        ev_type = str(ev.get("event") or "")
        snap_events = proj_cfg.get("snapshot_events") or []
        contrib_events = proj_cfg.get("contribution_events") or []
        if ev_type not in snap_events and ev_type not in contrib_events:
            return None

        base_proj_id = str(proj_cfg.get("id") or "active_project").strip()
        if not base_proj_id:
            return None

        eid_val = _extract_path(ev, proj_cfg.get("entity_id_field"))
        eid_raw = str(eid_val).strip() if eid_val is not None and str(eid_val).strip() else ""
        proj_id = f"{base_proj_id}_{eid_raw}" if (proj_cfg.get("key_by_entity_id") and eid_raw) else base_proj_id
        existing = dict(projects.get(proj_id) or {})
        eid_str = eid_raw or str(existing.get("entity_id") or "")

        # Resolve human-readable project name & parent system from tracked_pois or current state
        proj_name = existing.get("name") or ""
        proj_sys = existing.get("system") or ""
        tracked_pois = state_snapshot.get("tracked_pois")
        if isinstance(tracked_pois, dict):
            for pinfo in tracked_pois.values():
                if not isinstance(pinfo, dict):
                    continue
                if eid_str and str(pinfo.get("entity_id") or "") == eid_str:
                    if pinfo.get("name"):
                        proj_name = str(pinfo["name"]).strip()
                    if pinfo.get("system"):
                        proj_sys = str(pinfo["system"]).strip()
                    break
        if not proj_name or (eid_str and eid_str != str(existing.get("entity_id") or "")):
            fb_name = state_snapshot.get(str(proj_cfg.get("name_state_fallback") or "station"))
            if fb_name and str(fb_name).strip() and str(fb_name).strip().lower() != "unknown":
                proj_name = str(fb_name).strip()
        if not proj_sys or (eid_str and eid_str != str(existing.get("entity_id") or "")):
            fb_sys = state_snapshot.get(str(proj_cfg.get("system_state_fallback") or "star_system"))
            if fb_sys and str(fb_sys).strip() and str(fb_sys).strip().lower() != "unknown":
                proj_sys = str(fb_sys).strip()

        def _extract_res_name(item: Dict[str, Any], name_fields: List[str]) -> str:
            for nf in name_fields:
                v = _extract_path(item, nf)
                if v is not None and str(v).strip():
                    s = str(v).strip()
                    if s.startswith("$") and s.endswith(";"):
                        s = s.strip("$_;").replace("_name", "").replace("_", " ").title()
                    return s
            return ""

        if ev_type in snap_events:
            res_list_raw = _extract_path(ev, proj_cfg.get("resources_field") or "ResourcesRequired")
            if not isinstance(res_list_raw, list):
                return None
            name_fields = proj_cfg.get("resource_name_fields") or ["Name_Localised", "Name"]
            req_field = str(proj_cfg.get("required_field") or "RequiredAmount")
            prov_field = str(proj_cfg.get("provided_field") or "ProvidedAmount")
            resources: List[Dict[str, Any]] = []
            total_req = 0
            total_prov = 0
            for r in res_list_raw:
                if not isinstance(r, dict):
                    continue
                rname = _extract_res_name(r, name_fields)
                if not rname:
                    continue
                try:
                    req_amt = max(0, int(r.get(req_field) or 0))
                    prov_amt = max(0, int(r.get(prov_field) or 0))
                except (TypeError, ValueError):
                    continue
                rem_amt = max(0, req_amt - prov_amt)
                total_req += req_amt
                total_prov += min(req_amt, prov_amt)
                resources.append({
                    "name": rname,
                    "required": req_amt,
                    "provided": prov_amt,
                    "remaining": rem_amt,
                })
            prog_raw = _extract_path(ev, proj_cfg.get("progress_field"))
            if isinstance(prog_raw, (int, float)):
                progress_pct = round(float(prog_raw) * (100.0 if float(prog_raw) <= 1.0 else 1.0), 1)
            elif total_req > 0:
                progress_pct = round((total_prov / float(total_req)) * 100.0, 1)
            else:
                progress_pct = 0.0
            complete = bool(_extract_path(ev, proj_cfg.get("complete_field")))
            failed = bool(_extract_path(ev, proj_cfg.get("failed_field")))
            remaining_total = sum(int(x["remaining"]) for x in resources)
            updated = {
                "id": proj_id,
                "label": str(proj_cfg.get("label") or "Active Project"),
                "entity_id": eid_str,
                "name": proj_name or existing.get("name") or str(proj_cfg.get("label") or proj_id),
                "system": proj_sys or existing.get("system") or "",
                "progress_pct": progress_pct,
                "complete": complete,
                "failed": failed,
                "remaining_total": remaining_total,
                "resources": resources,
                "sync_to_scratchpad": bool(proj_cfg.get("sync_to_scratchpad", False)),
                "updated_at": time.time(),
            }
            if existing.get("last_delivery"):
                updated["last_delivery"] = existing["last_delivery"]
            projects[proj_id] = updated
            return proj_id, updated

        # Incremental contribution / delivery event
        if ev_type in contrib_events:
            if not existing or not isinstance(existing.get("resources"), list):
                return None
            if eid_str and existing.get("entity_id") and eid_str != str(existing["entity_id"]):
                return None
            contribs_raw = _extract_path(ev, proj_cfg.get("contributions_field") or "Contributions")
            if not isinstance(contribs_raw, list) or not contribs_raw:
                return None
            cname_fields = proj_cfg.get("contribution_name_fields") or ["Name_Localised", "Name"]
            camt_field = str(proj_cfg.get("contribution_amount_field") or "Amount")
            res_copy = [dict(r) for r in existing["resources"]]
            delivered_summary: List[str] = []
            for c in contribs_raw:
                if not isinstance(c, dict):
                    continue
                cname = _extract_res_name(c, cname_fields)
                try:
                    camt = max(0, int(c.get(camt_field) or 0))
                except (TypeError, ValueError):
                    camt = 0
                if not cname or camt <= 0:
                    continue
                delivered_summary.append(f"{cname} x{camt}")
                cname_low = cname.lower()
                for r in res_copy:
                    if str(r.get("name") or "").lower() == cname_low:
                        r["provided"] = int(r.get("provided") or 0) + camt
                        r["remaining"] = max(0, int(r.get("required") or 0) - r["provided"])
                        break
            total_req = sum(int(r.get("required") or 0) for r in res_copy)
            total_prov = sum(min(int(r.get("required") or 0), int(r.get("provided") or 0)) for r in res_copy)
            existing["resources"] = res_copy
            existing["remaining_total"] = sum(int(r.get("remaining") or 0) for r in res_copy)
            if total_req > 0:
                existing["progress_pct"] = round((total_prov / float(total_req)) * 100.0, 1)
            if delivered_summary:
                existing["last_delivery"] = ", ".join(delivered_summary)
            existing["updated_at"] = time.time()
            projects[proj_id] = existing
            return proj_id, existing

        return None

    def scan_poi_history(self, max_files: int = 40) -> Dict[str, Dict[str, Any]]:
        """
        Walks up to `max_files` recent journal logs chronologically and extracts ONLY
        declarative `poi_trackers` events (fast scan to recover asset names/callsigns
        logged in earlier sessions as well as the latest system/location).
        """
        poi_trackers = self._tel_cfg.get("poi_trackers") or []
        if not isinstance(poi_trackers, list) or not poi_trackers:
            return {}
        target_events = set()
        for pt in poi_trackers:
            if isinstance(pt, dict):
                for ev_name in (pt.get("events") or []):
                    target_events.add(str(ev_name))
        if not target_events:
            return {}

        paths = self._get_sorted_journal_paths()
        if not paths:
            return {}
        recent_paths = paths[-max(1, int(max_files)):]
        found: Dict[str, Dict[str, Any]] = {}

        # Run 2 fast passes so entity_id/callsign discovered later in a file can still
        # correlate with earlier events (or vice versa) in chronological order.
        parsed_events: List[Dict[str, Any]] = []
        for jpath in recent_paths:
            try:
                with open(jpath, "r", encoding="utf-8", errors="replace") as f:
                    for raw_line in f:
                        line = raw_line.strip()
                        if not line or not line.startswith("{"):
                            continue
                        if not any(ev_n in line for ev_n in target_events):
                            continue
                        try:
                            ev = json.loads(line)
                        except Exception:
                            continue
                        if ev.get("event") in target_events:
                            parsed_events.append(ev)
            except Exception:
                continue

        for _ in range(2):
            for ev in parsed_events:
                ev_type = ev.get("event")
                for pt in poi_trackers:
                    if isinstance(pt, dict) and ev_type in (pt.get("events") or []):
                        self._apply_poi_tracker_rule(ev, pt, found)

        if found:
            with self._lock:
                tracked = self.state.setdefault("tracked_pois", {})
                for pid, info in found.items():
                    merged = dict(tracked.get(pid) or {})
                    if info.get("entity_id") and not merged.get("entity_id"):
                        merged["entity_id"] = info["entity_id"]
                    if info.get("name") and (not merged.get("name") or merged.get("name") == pid):
                        merged["name"] = info["name"]
                    if info.get("callsign") and not merged.get("callsign"):
                        merged["callsign"] = info["callsign"]
                    if info.get("system") and not merged.get("system"):
                        merged["system"] = info["system"]
                    if info.get("body") and not merged.get("body"):
                        merged["body"] = info["body"]
                    if info.get("aliases") and not merged.get("aliases"):
                        merged["aliases"] = info["aliases"]
                    tracked[pid] = merged
        return found

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
                                if spec.get("allow_none"):
                                    self.state[state_key] = spec.get("default", None)
                                continue
                            if isinstance(val, list) and spec.get("list_scale") is not None:
                                scale = float(spec["list_scale"])
                                val = [float(x) * scale for x in val]
                            elif spec.get("scale") is not None and isinstance(val, (int, float)):
                                val = float(val) * float(spec["scale"])
                            self.state[state_key] = val

                # Optional declarative computed percentage/ratios (e.g. fuel_pct = fuel_main / fuel_capacity * 100)
                computed_ratios = self._tel_cfg.get("computed_ratios") or {}
                if isinstance(computed_ratios, dict):
                    for out_key, rspec in computed_ratios.items():
                        if not isinstance(rspec, dict):
                            continue
                        num = self.state.get(str(rspec.get("numerator") or ""))
                        den = self.state.get(str(rspec.get("denominator") or ""))
                        try:
                            num_f = float(num)
                            den_f = float(den)
                            if den_f > 0:
                                scale = float(rspec.get("scale") if rspec.get("scale") is not None else 100.0)
                                self.state[out_key] = round((num_f / den_f) * scale, 1)
                        except (TypeError, ValueError):
                            pass

                self.state["active"] = True
                self.state["last_updated"] = time.time()

                # Check declarative numeric threshold_events (e.g. FuelLevel below 25%)
                triggered_thresholds: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
                threshold_rules = self._tel_cfg.get("threshold_events") or []
                if isinstance(threshold_rules, list) and threshold_rules:
                    for tr in threshold_rules:
                        if not isinstance(tr, dict):
                            continue
                        ev_name = str(tr.get("event") or "").strip()
                        sfield = str(tr.get("state_field") or "").strip()
                        if not ev_name or not sfield:
                            continue
                        raw_v = self.state.get(sfield)
                        if raw_v is None:
                            continue
                        try:
                            cur_v = float(raw_v)
                        except (TypeError, ValueError):
                            continue
                        below = tr.get("below")
                        above = tr.get("above")
                        in_zone = False
                        if below is not None and cur_v < float(below):
                            in_zone = True
                        if above is not None and cur_v > float(above):
                            in_zone = True
                        tkey = f"{ev_name}:{sfield}"
                        prev_v = self._last_threshold_values.get(tkey)
                        if in_zone and (prev_v is None or abs(cur_v - prev_v) >= 1.0):
                            self._last_threshold_values[tkey] = cur_v
                            triggered_thresholds.append(
                                (ev_name, {"event": ev_name, "field": sfield, "value": cur_v}, dict(self.state))
                            )
                        elif not in_zone and tkey in self._last_threshold_values:
                            self._last_threshold_values.pop(tkey, None)

            on_event_cb = getattr(self, "on_event", None)
            if callable(on_event_cb):
                for ev_name, raw_ev, st_snap in triggered_thresholds:
                    try:
                        on_event_cb(ev_name, raw_ev, st_snap)
                    except Exception:
                        pass

            self._poll_companion_files()
        except Exception:
            pass

    def refresh_companion_files(self) -> None:
        """Public on-demand refresh of companion JSON files (e.g., cargo/inventory/route) from disk."""
        self._poll_companion_files()

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
                    prev_route = self.state.get("route_destination")
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

                    # Build live cargo-hold lookup & compute `project_still_to_buy` (project remaining minus on-board cargo)
                    cargo_by_name: Dict[str, int] = {}
                    for cit in (self.state.get("cargo_items") or []):
                        if isinstance(cit, dict) and cit.get("name"):
                            ck = str(cit["name"]).strip().lower()
                            try:
                                cargo_by_name[ck] = cargo_by_name.get(ck, 0) + max(0, int(cit.get("count") or 0))
                            except (TypeError, ValueError):
                                pass

                    active_projs = self.state.get("active_projects")
                    needed_after_cargo: Dict[str, Dict[str, Any]] = {}
                    if isinstance(active_projs, dict) and active_projs:
                        raw_needed: Dict[str, Dict[str, Any]] = {}
                        for pinfo in active_projs.values():
                            if not isinstance(pinfo, dict) or pinfo.get("complete"):
                                continue
                            for r in (pinfo.get("resources") or []):
                                if not isinstance(r, dict):
                                    continue
                                rem = int(r.get("remaining") or 0)
                                rname = str(r.get("name") or "").strip()
                                if rem > 0 and rname:
                                    k = rname.lower()
                                    if k in raw_needed:
                                        raw_needed[k]["project_remaining"] += rem
                                    else:
                                        raw_needed[k] = {"name": rname, "project_remaining": rem}
                        still_list: List[Dict[str, Any]] = []
                        for k, info in raw_needed.items():
                            in_cargo = cargo_by_name.get(k, 0)
                            still_buy = max(0, int(info["project_remaining"]) - in_cargo)
                            if still_buy > 0:
                                entry = {
                                    "name": info["name"],
                                    "project_remaining": int(info["project_remaining"]),
                                    "in_cargo": in_cargo,
                                    "still_to_buy": still_buy,
                                }
                                still_list.append(entry)
                                needed_after_cargo[k] = {"name": info["name"], "needed": still_buy, "in_cargo": in_cargo}
                        still_list.sort(key=lambda x: -int(x["still_to_buy"]))
                        self.state["project_still_to_buy"] = still_list

                    new_route = self.state.get("route_destination")
                    route_changed_event = None
                    if new_route and str(new_route).strip() != str(self._last_route_destination or "").strip():
                        was_initialized = self._last_route_destination is not None
                        self._last_route_destination = str(new_route).strip()
                        if was_initialized and str(new_route).strip() != str(prev_route or "").strip():
                            route_changed_event = (
                                "RoutePlotted",
                                {
                                    "event": "RoutePlotted",
                                    "destination_system": str(new_route).strip(),
                                    "route_jumps": self.state.get("route_jumps", 0),
                                },
                                dict(self.state),
                            )

                    # Optional declarative local-market vs still-to-buy project cross-reference (0ms, 0 API calls!)
                    pm_cfg = ext_cfg.get("project_market_matcher")
                    if isinstance(pm_cfg, dict):
                        out_key = str(pm_cfg.get("output_state") or "station_project_matches")
                        cur_stn = str(self.state.get("station") or "").strip()
                        mkt_stn = str(_extract_path(cdata, pm_cfg.get("station_field") or "StationName") or "").strip()
                        mkt_sys = str(_extract_path(cdata, pm_cfg.get("system_field") or "StarSystem") or "").strip()
                        if not cur_stn or (mkt_stn and mkt_stn.lower() != cur_stn.lower()):
                            self.state.pop(out_key, None)
                        else:
                            raw_items = _extract_path(cdata, pm_cfg.get("items_field") or "Items") or []
                            if isinstance(raw_items, list):
                                name_fields = pm_cfg.get("name_fields") or ["Name_Localised", "Name"]
                                buy_f = str(pm_cfg.get("buy_price_field") or "BuyPrice")
                                stock_f = str(pm_cfg.get("stock_field") or "Stock")
                                matches: List[Dict[str, Any]] = []
                                for it in raw_items:
                                    if not isinstance(it, dict):
                                        continue
                                    iname = ""
                                    for nf in name_fields:
                                        v = _extract_path(it, nf)
                                        if v is not None and str(v).strip():
                                            iname = str(v).strip()
                                            break
                                    if not iname:
                                        continue
                                    ninfo = needed_after_cargo.get(iname.lower())
                                    if not ninfo:
                                        continue
                                    try:
                                        bp = int(it.get(buy_f) or 0)
                                        stk = int(it.get(stock_f) or 0)
                                    except (TypeError, ValueError):
                                        continue
                                    if bp > 0 and stk > 0:
                                        matches.append({
                                            "name": ninfo["name"],
                                            "needed": ninfo["needed"],
                                            "in_cargo": ninfo["in_cargo"],
                                            "stock": stk,
                                            "buy_price": bp,
                                            "covers_need": stk >= ninfo["needed"],
                                        })
                                matches.sort(key=lambda x: (-int(x["needed"]), x["buy_price"]))
                                self.state[out_key] = {
                                    "station": mkt_stn or cur_stn,
                                    "system": mkt_sys or str(self.state.get("star_system") or ""),
                                    "matched_count": len(matches),
                                    "total_needed": len(needed_after_cargo),
                                    "items": matches,
                                }
                if route_changed_event is not None:
                    on_event_cb = getattr(self, "on_event", None)
                    if callable(on_event_cb):
                        try:
                            on_event_cb(*route_changed_event)
                        except Exception:
                            pass
            except Exception:
                pass

    def _rehydrate_initial_state(self) -> None:
        if not self._file_handle:
            return
        try:
            self._file_handle.seek(0)
            lines = self._file_handle.readlines()
            for ln in lines:
                self._parse_event_line(ln.strip(), is_initial_sync=True)
            self._file_handle.seek(0, os.SEEK_END)

            # If the latest log file is a fresh menu/launcher stub without a location yet,
            # walk back up to 5 recent log files until a real session with location is found,
            # then replay the latest file on top so state is never stranded as "Unknown".
            loc_info = self.get_location()
            if not loc_info.get("system"):
                all_paths = self._get_sorted_journal_paths()
                prior_paths = [p for p in all_paths[:-1][-5:]]
                for prev_path in reversed(prior_paths):
                    try:
                        with open(prev_path, "r", encoding="utf-8", errors="replace") as pf:
                            for pln in pf:
                                self._parse_event_line(pln.strip(), is_initial_sync=True)
                        if self.get_location().get("system"):
                            break
                    except Exception:
                        continue
                if self.get_location().get("system"):
                    for ln in lines:
                        self._parse_event_line(ln.strip(), is_initial_sync=True)

            # Rehydrate recent project trackers (if not yet populated from the latest file) across up to 8 recent logs
            proj_trackers = self._tel_cfg.get("project_trackers") or []
            if isinstance(proj_trackers, list) and proj_trackers and not self.state.get("active_projects"):
                target_ev_names = set()
                for pt in proj_trackers:
                    if isinstance(pt, dict):
                        target_ev_names.update(str(x) for x in (pt.get("snapshot_events") or []))
                        target_ev_names.update(str(x) for x in (pt.get("contribution_events") or []))
                poi_trackers = self._tel_cfg.get("poi_trackers") or []
                for poit in poi_trackers:
                    if isinstance(poit, dict):
                        target_ev_names.update(str(x) for x in (poit.get("events") or []))
                if target_ev_names:
                    all_paths = self._get_sorted_journal_paths()
                    for prev_path in all_paths[-8:]:
                        try:
                            with open(prev_path, "r", encoding="utf-8", errors="replace") as pf:
                                for pln in pf:
                                    s = pln.strip()
                                    if not s or not s.startswith("{"):
                                        continue
                                    if not any(en in s for en in target_ev_names):
                                        continue
                                    try:
                                        pev = json.loads(s)
                                    except Exception:
                                        continue
                                    if not isinstance(pev, dict):
                                        continue
                                    ev_t = pev.get(self._event_field)
                                    with self._lock:
                                        tracked = self.state.setdefault("tracked_pois", {})
                                        for poit in poi_trackers:
                                            if isinstance(poit, dict) and ev_t in (poit.get("events") or []):
                                                self._apply_poi_tracker_rule(pev, poit, tracked)
                                        projs = self.state.setdefault("active_projects", {})
                                        for pt in proj_trackers:
                                            if isinstance(pt, dict):
                                                self._apply_project_tracker_rule(pev, pt, projs, self.state)
                        except Exception:
                            continue

            # Notify callbacks with rehydrated active projects & tracked POIs so notes/POIs stay synced on startup
            if callable(self.on_project_update):
                with self._lock:
                    projs_snap = {k: dict(v) for k, v in (self.state.get("active_projects") or {}).items() if isinstance(v, dict)}
                for pid, pdata in projs_snap.items():
                    try:
                        self.on_project_update(pid, pdata)
                    except Exception:
                        pass
            if callable(self.on_poi_update):
                with self._lock:
                    pois_snap = {k: dict(v) for k, v in (self.state.get("tracked_pois") or {}).items() if isinstance(v, dict)}
                for pid, pdata in pois_snap.items():
                    try:
                        self.on_poi_update(pid, pdata)
                    except Exception:
                        pass
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

            # 6. Evaluate declarative POI / asset trackers (e.g. mobile bases, home stations, carriers, construction sites)
            updated_pois: List[Tuple[str, Dict[str, Any]]] = []
            poi_trackers = self._tel_cfg.get("poi_trackers") or []
            if isinstance(poi_trackers, list) and poi_trackers:
                tracked = self.state.setdefault("tracked_pois", {})
                for ptracker in poi_trackers:
                    if not isinstance(ptracker, dict):
                        continue
                    if event_type not in (ptracker.get("events") or []):
                        continue
                    before_snap = {k: dict(v) for k, v in tracked.items() if isinstance(v, dict)}
                    resolved_pid = self._apply_poi_tracker_rule(ev, ptracker, tracked)
                    if resolved_pid and tracked.get(resolved_pid) != before_snap.get(resolved_pid):
                        if not is_initial_sync:
                            tracked[resolved_pid]["updated_at"] = time.time()
                            updated_pois.append((resolved_pid, dict(tracked[resolved_pid])))

            # 7. Evaluate declarative project / resource-checklist trackers (snapshots & deliveries)
            updated_projects: List[Tuple[str, Dict[str, Any]]] = []
            project_trackers = self._tel_cfg.get("project_trackers") or []
            if isinstance(project_trackers, list) and project_trackers:
                projects = self.state.setdefault("active_projects", {})
                for pcfg in project_trackers:
                    if not isinstance(pcfg, dict):
                        continue
                    before_projs = {k: dict(v) for k, v in projects.items() if isinstance(v, dict)}
                    res_p = self._apply_project_tracker_rule(ev, pcfg, projects, self.state)
                    if res_p is not None and not is_initial_sync:
                        pid_u, pdata_u = res_p
                        before_proj = before_projs.get(pid_u) or {}
                        if (
                            pdata_u.get("remaining_total") != before_proj.get("remaining_total")
                            or pdata_u.get("progress_pct") != before_proj.get("progress_pct")
                            or pdata_u.get("name") != before_proj.get("name")
                            or pdata_u.get("complete") != before_proj.get("complete")
                            or pdata_u.get("last_delivery") != before_proj.get("last_delivery")
                        ):
                            updated_projects.append((pid_u, dict(pdata_u)))

            state_snapshot = dict(self.state)

        # Dispatch live POI and Project tracker callbacks outside the lock
        if not is_initial_sync:
            if callable(self.on_poi_update):
                for pid_u, pdata_u in updated_pois:
                    try:
                        self.on_poi_update(pid_u, pdata_u)
                    except Exception:
                        pass
            if callable(self.on_project_update):
                for pid_u, pdata_u in updated_projects:
                    try:
                        self.on_project_update(pid_u, pdata_u)
                    except Exception:
                        pass

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

        # Notify live event subscribers (Event Triggers & Companion Chit-Chat)
        on_event_cb = getattr(self, "on_event", None)
        if not is_initial_sync and callable(on_event_cb):
            try:
                on_event_cb(str(event_type), ev, state_snapshot)
            except Exception as e:
                logger.debug("[DeclarativeTelemetry] on_event callback error: %s", e)

    def get_summary_prompt_context(self) -> str:
        with self._lock:
            if not self.state.get("active"):
                return f"Active Game: {self._display_name} (Waiting for live telemetry link...)"

            st = self.state
            fl = self.flags
            lines = [f"[LIVE GAME TELEMETRY: {self._display_name}]"]

            # Location & status (driven declaratively via summary_config.status_phases when provided)
            loc_sys = st.get("star_system") or st.get("zone") or st.get("region")
            loc_body = st.get("body")
            status_desc = ""
            phase_rules = (self._tel_cfg.get("summary_config") or {}).get("status_phases") or []
            if isinstance(phase_rules, list):
                fmt_ctx = _SafeFormatDict({**st, **fl})
                for pr in phase_rules:
                    if not isinstance(pr, dict):
                        continue
                    w_state = pr.get("when_state")
                    w_flag = pr.get("when_flag")
                    req_state = pr.get("require_state")
                    matched = True
                    if w_state is not None and not st.get(w_state):
                        matched = False
                    if w_flag is not None and not fl.get(w_flag):
                        matched = False
                    if req_state is not None and not st.get(req_state):
                        matched = False
                    if matched and pr.get("template"):
                        status_desc = str(pr["template"]).format_map(fmt_ctx).strip()
                        break
            if not status_desc:
                if st.get("docked") and st.get("station"):
                    status_desc = f"Docked at {st['station']}"
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
                jump_val = st.get("max_jump_range")
                jump_str = f" | Max Jump: {round(float(jump_val), 1)} LY" if isinstance(jump_val, (int, float)) and jump_val > 0 else ""
                lines.append(f"- Vessel: {st.get('ship_name', 'Unknown')} ({st.get('ship_type', 'Unknown')}){hull_str}{shields_str}{jump_str}")

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

            if st.get("cargo_count") is not None or st.get("cargo_items") is not None or st.get("pips") is not None:
                extra_parts = []
                if st.get("cargo_count") is not None or isinstance(st.get("cargo_items"), list):
                    c_count = int(st.get("cargo_count") or 0)
                    c_cap = st.get("cargo_capacity")
                    cap_str = f" / {int(c_cap)}" if isinstance(c_cap, (int, float)) and int(c_cap) > 0 else ""
                    c_items = st.get("cargo_items") if isinstance(st.get("cargo_items"), list) else []
                    if c_items:
                        manifest_parts = [
                            f"{it.get('name', 'Item')} x{it.get('count', 0)}"
                            for it in c_items[:12]
                            if isinstance(it, dict) and int(it.get("count") or 0) > 0
                        ]
                        overflow = max(0, len(c_items) - 12)
                        manifest_str = ", ".join(manifest_parts) + (f", +{overflow} more" if overflow else "")
                        extra_parts.append(f"Cargo: {c_count}{cap_str} ({manifest_str})")
                    else:
                        extra_parts.append(f"Cargo: {c_count}{cap_str} ({'Empty' if c_count == 0 else 'manifest pending'})")
                if st.get("pips") is not None:
                    extra_parts.append(f"Pips: {st['pips']}")
                if extra_parts:
                    lines.append("- " + " | ".join(extra_parts))

            if st.get("route_destination"):
                lines.append(f"- Plotted Route: {st['route_destination']} ({st.get('route_jumps', 0)} jumps)")

            tracked_pois = st.get("tracked_pois")
            if isinstance(tracked_pois, dict) and tracked_pois:
                poi_parts = []
                for pid, pinfo in tracked_pois.items():
                    if not isinstance(pinfo, dict):
                        continue
                    pname = pinfo.get("name") or pid
                    pcs = f" ({pinfo['callsign']})" if pinfo.get("callsign") else ""
                    psys = pinfo.get("system") or "Unknown"
                    poi_parts.append(f"{pname}{pcs} @ {psys}")
                if poi_parts:
                    lines.append("- Tracked POIs / Assets: " + " | ".join(poi_parts))

            active_projects = st.get("active_projects")
            if isinstance(active_projects, dict) and active_projects:
                for pinfo in active_projects.values():
                    if not isinstance(pinfo, dict):
                        continue
                    pname = pinfo.get("name") or pinfo.get("label") or "Project"
                    psys = f" @ {pinfo['system']}" if pinfo.get("system") else ""
                    pct = pinfo.get("progress_pct", 0.0)
                    if pinfo.get("complete"):
                        lines.append(f"- Active Project: {pname}{psys} — COMPLETE (100%)")
                    else:
                        rem_items = [
                            r for r in (pinfo.get("resources") or [])
                            if isinstance(r, dict) and int(r.get("remaining") or 0) > 0
                        ]
                        rem_items.sort(key=lambda r: int(r.get("remaining") or 0), reverse=True)
                        rem_str = ", ".join(f"{r['name']} ({r['remaining']})" for r in rem_items[:15])
                        if len(rem_items) > 15:
                            rem_str += f", +{len(rem_items) - 15} more"
                        lines.append(
                            f"- Active Project: {pname}{psys} — {pct}% complete | Remaining ({pinfo.get('remaining_total', 0)} total): {rem_str}"
                        )

            still_to_buy = st.get("project_still_to_buy")
            if isinstance(still_to_buy, list) and st.get("active_projects"):
                if still_to_buy:
                    stb_txt = ", ".join(
                        f"{it['name']} (buy {it['still_to_buy']}"
                        + (f"; {it['in_cargo']} already in cargo)" if it.get("in_cargo") else ")")
                        for it in still_to_buy[:12]
                    )
                    if len(still_to_buy) > 12:
                        stb_txt += f", +{len(still_to_buy) - 12} more"
                    lines.append(
                        f"- Project Still To Buy (EXCLUDING items already in cargo hold): {stb_txt}"
                    )
                else:
                    lines.append(
                        "- Project Still To Buy: 0 — ALL remaining project materials are ALREADY in your cargo hold! Ready to deliver."
                    )

            spm = st.get("station_project_matches")
            if isinstance(spm, dict) and st.get("docked"):
                m_items = spm.get("items") or []
                stn_lbl = spm.get("station") or st.get("station") or "Current Station"
                if m_items:
                    avail_txt = ", ".join(
                        f"{it['name']} (still need to buy {it['needed']} / stock {it['stock']:,} @ {it['buy_price']:,} CR)"
                        for it in m_items[:12]
                    )
                    lines.append(
                        f"- Unbought Project Items Available Here ({stn_lbl}, live local market): "
                        f"{spm.get('matched_count', len(m_items))}/{spm.get('total_needed', 0)} still-needed items — {avail_txt}"
                    )
                else:
                    lines.append(
                        f"- Unbought Project Items Available Here ({stn_lbl}, live local market): "
                        f"0/{spm.get('total_needed', 0)} still-needed items stocked here"
                    )

            if st.get("last_event"):
                lines.append(f"- Last In-Game Event: {st['last_event']}")

            return "\n".join(lines)
