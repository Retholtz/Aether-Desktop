"""
core/game_intel.py - Universal Declarative REST Intel Engine.

Executes structured HTTPS GET/POST queries declared in any `.aethermodule.json`
`intel_endpoints` block, with:
- SSRF protection (HTTPS only, blocks loopback/private/link-local targets)
- Optional cached `value_resolver` canonicalization (e.g. "Liquid Oxygen" -> "Liquid oxygen")
- Safe template interpolation for `{query}`, `{canonical_query}`, `{location}`, `{telemetry.*}`
- Flexible dotted & list-filter field extraction (e.g. `market[commodity~={canonical_query}].buy_price`)
"""

from concurrent.futures import ThreadPoolExecutor
import html
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from core.logger import get_logger

logger = get_logger("DeclarativeGameIntel")

# In-memory cache for value_resolver vocabularies: url -> (fetched_ts, list_of_values)
_VALUE_RESOLVER_CACHE: Dict[str, Tuple[float, List[str]]] = {}
_VALUE_CACHE_TTL_SEC = 12 * 3600  # 12 hours

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_\.]+)\}")
_LIST_FILTER_RE = re.compile(r"^([a-zA-Z0-9_]+)\[([a-zA-Z0-9_]+)\s*(~=|==)\s*(.+)\]$")


def is_safe_public_https_url(url: str) -> bool:
    """Validates that an intel endpoint URL uses HTTPS and does not target private/loopback IPs."""
    raw = str(url or "").strip()
    if not raw:
        return False
    try:
        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme != "https":
            return False
        host = (parsed.hostname or "").strip().lower()
        if not host or host in ("localhost", "127.0.0.1", "::1"):
            return False
        # Block literal IP addresses in private/reserved/link-local ranges
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        except ValueError:
            pass
        return True
    except Exception:
        return False


def _is_resolved_host_safe(url: str) -> bool:
    """DNS-level check preventing private/link-local SSRF via hostname resolution."""
    if not is_safe_public_https_url(url):
        return False
    try:
        host = urllib.parse.urlparse(url).hostname
        if not host:
            return False
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        for info in infos:
            ip_str = info[4][0]
            ip = ipaddress.ip_address(ip_str)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except Exception:
        # If DNS resolution fails, let the actual HTTP call report the network error cleanly
        return True


def _get_nested_path(obj: Any, dotted_path: str, ctx: Optional[Dict[str, Any]] = None) -> Any:
    """
    Resolves dotted paths AND simple list-filter expressions like:
      - `system_name`
      - `market[commodity~={canonical_query}].buy_price`
      - `market[commodity~={canonical_query}]`
    """
    if not dotted_path:
        return obj
    cur = obj
    for part in str(dotted_path).split("."):
        if cur is None:
            return None
        m = _LIST_FILTER_RE.match(part.strip())
        if m:
            list_key, item_field, op, raw_target = m.groups()
            if not isinstance(cur, dict) or list_key not in cur:
                return None
            arr = cur.get(list_key)
            if not isinstance(arr, list):
                return None
            target = _interpolate_str(raw_target.strip().strip("'\""), ctx or {})
            target_low = str(target).strip().lower()
            matched = None
            for elem in arr:
                if not isinstance(elem, dict):
                    continue
                val = str(elem.get(item_field) or "").strip()
                if op == "==" and val == str(target):
                    matched = elem
                    break
                if op == "~=" and val.lower() == target_low:
                    matched = elem
                    break
            if matched is None and op == "~=":
                for elem in arr:
                    if isinstance(elem, dict) and target_low and target_low in str(elem.get(item_field) or "").lower():
                        matched = elem
                        break
            cur = matched
        else:
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, list):
                try:
                    cur = cur[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
    return cur


def _lookup_context_var(var_name: str, ctx: Dict[str, Any]) -> Any:
    if var_name in ctx:
        return ctx[var_name]
    if "." in var_name:
        return _get_nested_path(ctx, var_name)
    return None


def _interpolate_str(template: str, ctx: Dict[str, Any]) -> str:
    def _repl(match: re.Match) -> str:
        val = _lookup_context_var(match.group(1), ctx)
        return "" if val is None else str(val)
    return _PLACEHOLDER_RE.sub(_repl, template)


def _render_template(node: Any, ctx: Dict[str, Any]) -> Any:
    """
    Recursively renders a JSON request template with `{var}` interpolation.
    Supports optional conditional objects:
      `{"$if": "telemetry.needs_large_pad", "$value": {"value": true}}`
    which are omitted from parent dicts if the condition variable is falsy.
    """
    if isinstance(node, str):
        # Full-string placeholder preserves native JSON types (bool/int/float/list)
        m = re.fullmatch(r"\{([a-zA-Z0-9_\.]+)\}", node.strip())
        if m:
            val = _lookup_context_var(m.group(1), ctx)
            return val
        return _interpolate_str(node, ctx)

    if isinstance(node, list):
        out_list = []
        for item in node:
            rendered = _render_template(item, ctx)
            if rendered is not _OMIT_SENTINEL:
                out_list.append(rendered)
        return out_list

    if isinstance(node, dict):
        if "$if" in node:
            cond_val = _lookup_context_var(str(node["$if"]), ctx)
            if not cond_val:
                return _OMIT_SENTINEL
            return _render_template(node.get("$value"), ctx)

        out_dict = {}
        for k, v in node.items():
            rendered = _render_template(v, ctx)
            if rendered is not _OMIT_SENTINEL:
                out_dict[k] = rendered
        return out_dict

    return node


_OMIT_SENTINEL = object()


def _http_json(url: str, method: str = "GET", payload: Optional[Any] = None, timeout_sec: float = 10.0, headers: Optional[Dict[str, str]] = None) -> Any:
    if not _is_resolved_host_safe(url):
        raise ValueError(f"Blocked unsafe or non-HTTPS intel URL: {url}")

    req_headers = {
        "Accept": "application/json",
        "User-Agent": "AetherDesktop/GameIntel",
    }
    if isinstance(headers, dict):
        for hk, hv in headers.items():
            req_headers[str(hk)] = str(hv)

    data_bytes = None
    if payload is not None and method.upper() in ("POST", "PUT", "PATCH"):
        data_bytes = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=data_bytes, headers=req_headers, method=method.upper())
    with urllib.request.urlopen(req, timeout=max(1.0, min(20.0, float(timeout_sec)))) as resp:
        raw = resp.read(8_000_000).decode("utf-8", errors="replace").strip()
        return json.loads(raw) if raw else {}


def _resolve_canonical_query(query: str, resolver_spec: Dict[str, Any], timeout_sec: float = 8.0) -> str:
    """
    Uses a module's declarative `value_resolver` endpoint (cached for 12h) to match
    user queries case-insensitively to an API's canonical item/service/module name.
    """
    if not isinstance(resolver_spec, dict) or not resolver_spec.get("url"):
        return query

    url = str(resolver_spec["url"]).strip()
    values_path = str(resolver_spec.get("values_path") or "values")
    now = time.time()

    vocab: Optional[List[str]] = None
    cached = _VALUE_RESOLVER_CACHE.get(url)
    if cached and (now - cached[0]) < _VALUE_CACHE_TTL_SEC:
        vocab = cached[1]
    else:
        try:
            data = _http_json(url, method="GET", timeout_sec=timeout_sec)
            extracted = _get_nested_path(data, values_path)
            if isinstance(extracted, dict):
                vocab = [str(k) for k in extracted.keys()]
            elif isinstance(extracted, list):
                vocab = [str(v) for v in extracted if v is not None]
            if vocab:
                _VALUE_RESOLVER_CACHE[url] = (now, vocab)
        except Exception as e:
            logger.debug("Value resolver fetch skipped (%s): %s", url, e)
            return query

    if not vocab:
        return query

    q_clean = str(query or "").strip()
    q_low = q_clean.lower()
    if not q_low:
        return query

    # 1. Case-insensitive exact match
    for v in vocab:
        if v.lower() == q_low:
            return v

    # 2. Exact match ignoring hyphens/punctuation/spaces
    q_alnum = re.sub(r"[^a-z0-9]+", "", q_low)
    for v in vocab:
        if re.sub(r"[^a-z0-9]+", "", v.lower()) == q_alnum:
            return v

    # 3. Substring match (prefer shortest canonical name containing all query words)
    words = [w for w in re.split(r"\s+", q_low) if w]
    candidates = [v for v in vocab if all(w in v.lower() for w in words)]
    if candidates:
        candidates.sort(key=len)
        return candidates[0]

    # 4. Fuzzy match (handles US/UK spelling like "Aluminum" -> "Aluminium" and voice typos)
    import difflib
    low_map = {v.lower(): v for v in vocab}
    close = difflib.get_close_matches(q_low, list(low_map.keys()), n=1, cutoff=0.78)
    if close:
        return low_map[close[0]]

    return query


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _clean_intel_field(val: Any, strip_html: bool = False, max_chars: int = 0) -> Any:
    """Optionally strips HTML tags/entities and truncates long feed bodies for concise voice summaries."""
    if not isinstance(val, str):
        return val
    text = val
    if strip_html:
        text = html.unescape(_HTML_TAG_RE.sub(" ", text))
    text = re.sub(r"\s+", " ", text).strip()
    if max_chars > 0 and len(text) > max_chars:
        cut = text[:max_chars].rsplit(" ", 1)[0].strip()
        text = (cut or text[:max_chars].strip()) + "..."
    return text


def _format_structured_summary(
    endpoint_name: str,
    query: str,
    canonical_query: str,
    location: str,
    items: List[Dict[str, Any]],
) -> str:
    """Formats extracted JSON result rows into a concise, spoken-ready intel brief."""
    q_label = f" for '{canonical_query}'" if str(canonical_query or "").strip() else ""
    loc_clean = str(location or "").strip()
    loc_label = f" near '{loc_clean}'" if loc_clean and loc_clean.lower() not in ("unknown", "current location") else ""
    if not items:
        return f"Live query ({endpoint_name}){q_label}{loc_label} returned 0 matching results."

    lines = [
        f"Live database results ({endpoint_name}){q_label}{loc_label} ({len(items)} matches):"
    ]
    for idx, row in enumerate(items, 1):
        parts = []
        for k, v in row.items():
            if v is None or v == "":
                continue
            if isinstance(v, float):
                parts.append(f"{k}: {round(v, 2)}")
            else:
                parts.append(f"{k}: {v}")
        lines.append(f"{idx}. " + " | ".join(parts))
    return "\n".join(lines)


def _parse_station_and_system(raw: str) -> Tuple[str, str]:
    """Parses 'Station Name [System Name]' or 'Station Name (System Name)' into (station, system)."""
    s = str(raw or "").strip()
    if not s:
        return "", ""
    m = re.match(r"^(.+?)\s*[\[\(]\s*([^\]\)]+?)\s*[\]\)]\s*$", s)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return s, ""


def _query_declarative_trade_pair(
    endpoint_spec: Dict[str, Any],
    query: str,
    location: str = "",
    telemetry_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    100% game-agnostic declarative station/vendor-to-station profit comparison engine.
    Fetches live market tables for an Origin and Destination station in parallel and ranks
    profitable commodities (buy_price + supply at origin vs. sell_price + demand at destination).
    """
    timeout_sec = float(endpoint_spec.get("timeout_sec") or 12.0)
    endpoint_name = str(endpoint_spec.get("name") or endpoint_spec.get("url"))
    method = str(endpoint_spec.get("method") or "POST").upper().strip()
    url = str(endpoint_spec["url"])
    tel = dict(telemetry_state or {})

    raw_q = str(query or "").strip()
    origin_station = ""
    origin_system = ""
    dest_station = ""
    dest_system = ""

    # Support explicit "Origin to Destination" or "Origin -> Destination" syntax
    pair_match = re.split(r"\s+(?:->|=>|to)\s+", raw_q, maxsplit=1, flags=re.IGNORECASE)
    if len(pair_match) == 2 and pair_match[0].strip() and pair_match[1].strip():
        origin_station, origin_system = _parse_station_and_system(pair_match[0])
        dest_station, dest_system = _parse_station_and_system(pair_match[1])
    else:
        dest_station, dest_system = _parse_station_and_system(raw_q)

    # Default origin to live docked/current station & system from telemetry
    if not origin_station:
        origin_station = str(tel.get("station") or "").strip()
    if not origin_system:
        tel_sys = str(tel.get("star_system") or "").strip()
        if tel_sys and tel_sys.lower() != "unknown":
            origin_system = tel_sys
    # Use location parameter as destination system if caller passed destination system there
    loc_clean = str(location or "").strip()
    if not dest_system and loc_clean and loc_clean.lower() not in ("unknown", "current location"):
        if loc_clean.lower() != origin_system.lower() or not origin_system:
            dest_system = loc_clean

    if not origin_station or (not dest_station and not dest_system):
        return {
            "status": "error",
            "source": endpoint_name,
            "error": (
                "Origin station and destination station/system are required for trade comparison "
                f"(origin='{origin_station or 'unknown'}', destination='{dest_station or dest_system or 'unknown'}')."
            ),
        }

    results_path = str(endpoint_spec.get("results_path") or "results")
    market_path = str(endpoint_spec.get("market_list_path") or "market")
    name_f = str(endpoint_spec.get("item_name_field") or "commodity")
    buy_f = str(endpoint_spec.get("buy_price_field") or "buy_price")
    sell_f = str(endpoint_spec.get("sell_price_field") or "sell_price")
    supply_f = str(endpoint_spec.get("supply_field") or "supply")
    demand_f = str(endpoint_spec.get("demand_field") or "demand")
    headers = endpoint_spec.get("headers")

    def _fetch_station_record(stn_name: str, sys_name: str) -> Optional[Dict[str, Any]]:
        def _build_payload(include_sys: bool) -> Any:
            tmpl = endpoint_spec.get("station_body_template")
            ctx = {
                "station_name": stn_name,
                "system_name": sys_name if include_sys else "",
                "telemetry": tel,
            }
            if tmpl is not None:
                body = _render_template(tmpl, ctx)
                if isinstance(body, dict) and isinstance(body.get("filters"), dict):
                    sn_f = body["filters"].get("system_name")
                    if isinstance(sn_f, dict) and not sn_f.get("value"):
                        body["filters"].pop("system_name", None)
                    nm_f = body["filters"].get("name")
                    if isinstance(nm_f, dict) and not nm_f.get("value"):
                        body["filters"].pop("name", None)
                        body["filters"]["has_market"] = {"value": True}
                        if tel.get("needs_large_pad"):
                            body["filters"]["has_large_pad"] = {"value": True}
                        body["size"] = 8
                return body
            filters: Dict[str, Any] = {}
            if stn_name:
                filters["name"] = {"value": stn_name}
            else:
                filters["has_market"] = {"value": True}
                if tel.get("needs_large_pad"):
                    filters["has_large_pad"] = {"value": True}
            if include_sys and sys_name:
                filters["system_name"] = {"value": sys_name}
            return {"filters": filters, "size": 3 if stn_name else 8}

        for try_sys in ([True, False] if (sys_name and stn_name) else ([True] if sys_name else [False])):
            try:
                raw_resp = _http_json(
                    url,
                    method=method,
                    payload=_build_payload(try_sys),
                    timeout_sec=timeout_sec,
                    headers=headers,
                )
                rows = _get_nested_path(raw_resp, results_path) if results_path else raw_resp
                if isinstance(rows, dict):
                    rows = [rows]
                if isinstance(rows, list) and rows:
                    if stn_name:
                        for r in rows:
                            if isinstance(r, dict) and str(r.get("name") or "").strip().lower() == stn_name.lower():
                                return r
                    else:
                        # System-level lookup: prefer exact system_name match, exclude carriers, pick largest market
                        non_carriers = [
                            r for r in rows
                            if isinstance(r, dict) and "carrier" not in str(r.get("type") or "").lower()
                        ]
                        pool_rows = non_carriers or [r for r in rows if isinstance(r, dict)]
                        if sys_name:
                            exact_sys = [
                                r for r in pool_rows
                                if str(r.get("system_name") or "").strip().lower() == sys_name.lower()
                            ]
                            if exact_sys:
                                pool_rows = exact_sys
                        pool_rows.sort(key=lambda r: len(r.get(market_path) or []), reverse=True)
                        return pool_rows[0] if pool_rows else None
                    return rows[0] if isinstance(rows[0], dict) else None
            except Exception as exc:
                logger.warning("Declarative trade station fetch failed (%s): %s", stn_name or sys_name, exc)
        return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_orig = pool.submit(_fetch_station_record, origin_station, origin_system)
        f_dest = pool.submit(_fetch_station_record, dest_station, dest_system)
        orig_rec = f_orig.result()
        dest_rec = f_dest.result()

    if not orig_rec or not dest_rec:
        missing = []
        if not orig_rec:
            missing.append(f"origin '{origin_station}'")
        if not dest_rec:
            missing.append(f"destination '{dest_station}'")
        return {
            "status": "error",
            "source": endpoint_name,
            "error": f"Could not find live market data for {', '.join(missing)}.",
        }

    orig_market = _get_nested_path(orig_rec, market_path) or []
    dest_market = _get_nested_path(dest_rec, market_path) or []
    if not isinstance(orig_market, list) or not isinstance(dest_market, list):
        return {
            "status": "error",
            "source": endpoint_name,
            "error": "Station market tables were empty or unavailable.",
        }

    dest_by_name: Dict[str, Dict[str, Any]] = {}
    for item in dest_market:
        if isinstance(item, dict) and item.get(name_f):
            dest_by_name[str(item[name_f]).strip().lower()] = item

    # Estimate free cargo space from telemetry if available
    free_cargo = 0
    try:
        cap = int(tel.get("cargo_capacity") or 0)
        used = int(tel.get("cargo_count") or 0)
        if cap > 0:
            free_cargo = max(0, cap - used)
    except (TypeError, ValueError):
        free_cargo = 0

    overlaps: List[Dict[str, Any]] = []
    for item in orig_market:
        if not isinstance(item, dict) or not item.get(name_f):
            continue
        cname = str(item[name_f]).strip()
        try:
            buy_p = int(item.get(buy_f) or 0)
            supply = int(item.get(supply_f) or 0)
        except (TypeError, ValueError):
            continue
        if buy_p <= 0 or supply <= 0:
            continue
        d_item = dest_by_name.get(cname.lower())
        if not d_item:
            continue
        try:
            sell_p = int(d_item.get(sell_f) or 0)
            demand = int(d_item.get(demand_f) or 0)
        except (TypeError, ValueError):
            continue
        profit = sell_p - buy_p
        if profit <= 0:
            continue
        row: Dict[str, Any] = {
            "commodity": cname,
            "buy_price": buy_p,
            "supply": supply,
            "sell_price": sell_p,
            "demand": demand,
            "profit_per_unit": profit,
            "high_demand": demand >= 100,
        }
        if free_cargo > 0:
            haul_qty = min(free_cargo, supply)
            row["est_trip_profit"] = profit * haul_qty
            row["haul_qty"] = haul_qty
        overlaps.append(row)

    overlaps.sort(key=lambda r: (r["profit_per_unit"], r["demand"]), reverse=True)
    max_results = max(1, min(15, int(endpoint_spec.get("max_results") or 8)))

    # Ensure both top overall AND top high-demand items are surfaced (so demand=1 items don't crowd out real demand)
    top_overall = overlaps[:max_results]
    high_demand_only = [r for r in overlaps if r["high_demand"]][:4]
    selected: List[Dict[str, Any]] = list(top_overall)
    seen_names = {r["commodity"].lower() for r in selected}
    for hd in high_demand_only:
        if hd["commodity"].lower() not in seen_names:
            selected.append(hd)
            seen_names.add(hd["commodity"].lower())

    orig_label = f"{orig_rec.get('name', origin_station)} ({orig_rec.get('system_name', origin_system)})"
    dest_label = f"{dest_rec.get('name', dest_station)} ({dest_rec.get('system_name', dest_system)})"
    if not selected:
        summary = f"Live trade comparison ({endpoint_name}): No profitable commodities found from {orig_label} to {dest_label}."
    else:
        lines = [
            f"Live trade comparison ({endpoint_name}): Buy at {orig_label} -> Sell at {dest_label} ({len(overlaps)} profitable commodities):"
        ]
        for idx, r in enumerate(selected, 1):
            dem_tag = "" if r["high_demand"] else " [low demand]"
            trip_str = f" | est_trip_profit ({r['haul_qty']}t): +{r['est_trip_profit']:,}" if r.get("est_trip_profit") else ""
            lines.append(
                f"{idx}. {r['commodity']}: buy {r['buy_price']:,} (supply {r['supply']:,}) -> "
                f"sell {r['sell_price']:,} (demand {r['demand']:,}{dem_tag}) | profit: +{r['profit_per_unit']:,}/t{trip_str}"
            )
        summary = "\n".join(lines)

    return {
        "status": "success",
        "source": endpoint_name,
        "canonical_query": f"{orig_label} -> {dest_label}",
        "reference_location": dest_rec.get("system_name") or dest_system or origin_system,
        "count": len(selected),
        "results": selected,
        "intel": summary,
    }


def _query_declarative_shopping_list(
    endpoint_spec: Dict[str, Any],
    query: str,
    location: str = "",
    telemetry_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    100% game-agnostic multi-item / project shopping-list station finder.
    Fetches nearby stations/vendors with full market tables in a single API call, then scores
    and ranks them by how many of the player's needed items they stock (plus distance).
    """
    timeout_sec = float(endpoint_spec.get("timeout_sec") or 12.0)
    endpoint_name = str(endpoint_spec.get("name") or endpoint_spec.get("url"))
    method = str(endpoint_spec.get("method") or "POST").upper().strip()
    tel = dict(telemetry_state or {})

    # 1. Resolve target needed items: {canonical_lower: {"name": display_name, "needed": int}}
    raw_q = str(query or "").strip()
    generic_project_phrases = {
        "", "all", "project", "projects", "construction", "remaining", "shopping list",
        "resources", "materials", "needed", "active project", "my project", "our project",
        "construction project", "majority of resources", "project resources", "project materials",
    }
    needed_items: Dict[str, Dict[str, Any]] = {}
    project_label = ""

    use_active_projects = (
        raw_q.lower() in generic_project_phrases
        or ("project" in raw_q.lower() and "," not in raw_q)
        or ("construction" in raw_q.lower() and "," not in raw_q)
    )
    if use_active_projects:
        active_projs = tel.get("active_projects")
        if isinstance(active_projs, dict) and active_projs:
            # Prefer in-progress projects (newest first)
            ordered_p = sorted(
                [p for p in active_projs.values() if isinstance(p, dict) and not p.get("complete")],
                key=lambda p: float(p.get("updated_at") or 0.0),
                reverse=True,
            )
            if ordered_p:
                project_label = ", ".join(str(p.get("name") or "Project") for p in ordered_p[:3])
                # Subtract items already sitting in the player's cargo hold!
                cargo_by_name: Dict[str, int] = {}
                for cit in (tel.get("cargo_items") or []):
                    if isinstance(cit, dict) and cit.get("name"):
                        ck = str(cit["name"]).strip().lower()
                        try:
                            cargo_by_name[ck] = cargo_by_name.get(ck, 0) + max(0, int(cit.get("count") or 0))
                        except (TypeError, ValueError):
                            pass
                for p in ordered_p:
                    for r in (p.get("resources") or []):
                        if not isinstance(r, dict):
                            continue
                        rem = int(r.get("remaining") or 0)
                        rname = str(r.get("name") or "").strip()
                        if rem > 0 and rname:
                            key = rname.lower()
                            if key in needed_items:
                                needed_items[key]["needed"] += rem
                            else:
                                needed_items[key] = {"name": rname, "needed": rem}
                # Deduct on-board cargo quantities so we only search for unbought items
                for k in list(needed_items.keys()):
                    in_hold = cargo_by_name.get(k, 0)
                    if in_hold > 0:
                        left = needed_items[k]["needed"] - in_hold
                        if left <= 0:
                            needed_items.pop(k, None)
                        else:
                            needed_items[k]["needed"] = left
                if not needed_items and cargo_by_name:
                    return {
                        "status": "success",
                        "source": endpoint_name,
                        "canonical_query": f"project '{project_label}'",
                        "reference_location": str(location or tel.get("star_system") or ""),
                        "count": 0,
                        "results": [],
                        "intel": f"All remaining materials for project '{project_label}' are ALREADY in your cargo hold — ready to deliver!",
                    }
    if not needed_items and raw_q and raw_q.lower() not in generic_project_phrases:
        # Parse comma / semicolon / " and " separated list
        parts = [p.strip() for p in re.split(r"[,;]|\band\b", raw_q, flags=re.IGNORECASE) if p.strip()]
        for part in parts:
            key = part.lower()
            if key not in needed_items:
                needed_items[key] = {"name": part, "needed": 1}

    if not needed_items:
        return {
            "status": "error",
            "source": endpoint_name,
            "error": "No needed items found in active project tracker or query.",
        }

    # Optional canonicalization via value_resolver (e.g. "Aluminum" -> "Aluminium")
    if isinstance(endpoint_spec.get("value_resolver"), dict):
        remapped: Dict[str, Dict[str, Any]] = {}
        for item in needed_items.values():
            canon = _resolve_canonical_query(item["name"], endpoint_spec["value_resolver"], timeout_sec=timeout_sec)
            ckey = canon.lower()
            if ckey in remapped:
                remapped[ckey]["needed"] += item["needed"]
            else:
                remapped[ckey] = {"name": canon, "needed": item["needed"]}
        needed_items = remapped

    # 2. Resolve reference location
    effective_loc = str(location or "").strip()
    if not effective_loc or effective_loc.lower() in ("unknown", "current location"):
        tel_sys = str(tel.get("star_system") or "").strip()
        if tel_sys and tel_sys.lower() != "unknown":
            effective_loc = tel_sys
        else:
            effective_loc = str(endpoint_spec.get("default_location") or "").strip()

    ctx: Dict[str, Any] = {
        "query": raw_q,
        "location": effective_loc,
        "telemetry": tel,
    }
    url = _interpolate_str(str(endpoint_spec["url"]), ctx)
    payload = None
    if method in ("POST", "PUT", "PATCH") and endpoint_spec.get("body_template") is not None:
        payload = _render_template(endpoint_spec["body_template"], ctx)
        if isinstance(payload, dict) and payload.get("reference_system") in ("", None):
            payload.pop("reference_system", None)

    try:
        raw_resp = _http_json(
            url,
            method=method,
            payload=payload,
            timeout_sec=timeout_sec,
            headers=endpoint_spec.get("headers"),
        )
    except Exception as exc:
        logger.warning("Declarative shopping-list query failed (%s): %s", endpoint_name, exc)
        return {
            "status": "error",
            "source": endpoint_name,
            "reference_location": effective_loc,
            "error": str(exc),
        }

    results_path = str(endpoint_spec.get("results_path") or "results")
    stations = _get_nested_path(raw_resp, results_path, ctx=ctx) if results_path else raw_resp
    if isinstance(stations, dict):
        stations = [stations]
    elif not isinstance(stations, list):
        stations = []

    market_path = str(endpoint_spec.get("market_list_path") or "market")
    name_f = str(endpoint_spec.get("item_name_field") or "commodity")
    buy_f = str(endpoint_spec.get("buy_price_field") or "buy_price")
    supply_f = str(endpoint_spec.get("supply_field") or "supply")
    exclude_types = {str(t).strip().lower() for t in (endpoint_spec.get("exclude_station_types") or [])}

    scored: List[Dict[str, Any]] = []
    total_needed_kinds = len(needed_items)
    for stn in stations:
        if not isinstance(stn, dict):
            continue
        stype = str(stn.get("type") or "").strip()
        if stype and stype.lower() in exclude_types:
            continue
        mkt = _get_nested_path(stn, market_path) or []
        if not isinstance(mkt, list) or not mkt:
            continue
        matched_list: List[Dict[str, Any]] = []
        full_cover = 0
        for m_item in mkt:
            if not isinstance(m_item, dict) or not m_item.get(name_f):
                continue
            mkey = str(m_item[name_f]).strip().lower()
            need_info = needed_items.get(mkey)
            if not need_info:
                continue
            try:
                buy_p = int(m_item.get(buy_f) or 0)
                supply = int(m_item.get(supply_f) or 0)
            except (TypeError, ValueError):
                continue
            if buy_p <= 0 or supply <= 0:
                continue
            covers = supply >= need_info["needed"]
            if covers:
                full_cover += 1
            matched_list.append({
                "name": need_info["name"],
                "needed": need_info["needed"],
                "supply": supply,
                "buy_price": buy_p,
                "covers_need": covers,
            })
        if not matched_list:
            continue
        matched_list.sort(key=lambda x: (-int(x["needed"]), x["buy_price"]))
        dist_ly = float(stn.get("distance") or 0.0)
        dist_ls = stn.get("distance_to_arrival")
        scored.append({
            "station": stn.get("name") or "Unknown Station",
            "system": stn.get("system_name") or "Unknown System",
            "station_type": stype,
            "distance_ly": round(dist_ly, 2),
            "distance_ls": round(float(dist_ls), 1) if isinstance(dist_ls, (int, float)) else None,
            "large_pads": stn.get("large_pads"),
            "medium_pads": stn.get("medium_pads"),
            "matched_count": len(matched_list),
            "total_needed": total_needed_kinds,
            "full_cover_count": full_cover,
            "matched_items": matched_list,
        })

    scored.sort(key=lambda s: (-s["matched_count"], -s["full_cover_count"], s["distance_ly"]))
    max_results = max(1, min(10, int(endpoint_spec.get("max_results") or 5)))
    top_stations = scored[:max_results]

    scope_lbl = f"project '{project_label}'" if project_label else f"{total_needed_kinds} requested items"
    if not top_stations:
        summary = f"Multi-item station search ({endpoint_name}) for {scope_lbl} near '{effective_loc}' found 0 nearby stocking stations."
    else:
        lines = [
            f"Best one-stop stations ({endpoint_name}) for {scope_lbl} ({total_needed_kinds} needed items) near '{effective_loc}':"
        ]
        for idx, s in enumerate(top_stations, 1):
            ls_str = f", {s['distance_ls']} LS" if s.get("distance_ls") is not None else ""
            pad_str = f" | L-pads: {s['large_pads']}" if s.get("large_pads") is not None else ""
            items_txt = ", ".join(
                f"{it['name']} (need {it['needed']:,} / supply {it['supply']:,} @ {it['buy_price']:,} CR)"
                for it in s["matched_items"][:10]
            )
            if len(s["matched_items"]) > 10:
                items_txt += f", +{len(s['matched_items']) - 10} more"
            lines.append(
                f"{idx}. {s['station']} ({s['system']}) — {s['distance_ly']} LY{ls_str}{pad_str} | "
                f"Stocks {s['matched_count']}/{s['total_needed']} items: {items_txt}"
            )
        summary = "\n".join(lines)

    return {
        "status": "success",
        "source": endpoint_name,
        "canonical_query": scope_lbl,
        "reference_location": effective_loc,
        "count": len(top_stations),
        "results": top_stations,
        "intel": summary,
    }


def query_declarative_intel(
    endpoint_spec: Dict[str, Any],
    query: str,
    location: str = "",
    telemetry_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Executes a single declarative REST intel endpoint from `.aethermodule.json`.
    Supports standard search/feed endpoints, `mode: "trade_pair"` station-to-station profit comparison,
    and `mode: "shopping_list"` multi-commodity / project one-stop-shop station ranking.
    Returns `{status, source, canonical_query, reference_location, results, intel}`.
    """
    if not isinstance(endpoint_spec, dict) or not endpoint_spec.get("url"):
        return {"status": "error", "error": "Invalid intel endpoint specification."}

    ep_mode = str(endpoint_spec.get("mode") or "").strip().lower()
    if ep_mode == "trade_pair":
        return _query_declarative_trade_pair(
            endpoint_spec=endpoint_spec,
            query=query,
            location=location,
            telemetry_state=telemetry_state,
        )
    if ep_mode == "shopping_list":
        return _query_declarative_shopping_list(
            endpoint_spec=endpoint_spec,
            query=query,
            location=location,
            telemetry_state=telemetry_state,
        )

    timeout_sec = float(endpoint_spec.get("timeout_sec") or 10.0)
    endpoint_name = str(endpoint_spec.get("name") or endpoint_spec.get("url"))
    method = str(endpoint_spec.get("method") or "GET").upper().strip()

    # Optional canonicalization (e.g. "Liquid Oxygen" -> "Liquid oxygen")
    canonical_query = query.strip()
    if isinstance(endpoint_spec.get("value_resolver"), dict):
        canonical_query = _resolve_canonical_query(
            canonical_query, endpoint_spec["value_resolver"], timeout_sec=timeout_sec
        )

    # Resolve location fallback (use default_location from spec if live location unknown)
    tel = dict(telemetry_state or {})
    effective_loc = str(location or "").strip()
    if not effective_loc or effective_loc.lower() in ("unknown", "current location"):
        effective_loc = str(endpoint_spec.get("default_location") or "").strip()

    ctx: Dict[str, Any] = {
        "query": query.strip(),
        "canonical_query": canonical_query,
        "query_encoded": urllib.parse.quote(canonical_query),
        "location": effective_loc,
        "location_encoded": urllib.parse.quote(effective_loc),
        "telemetry": tel,
    }

    url = _interpolate_str(str(endpoint_spec["url"]), ctx)
    if isinstance(endpoint_spec.get("query_params"), dict):
        rendered_params = _render_template(endpoint_spec["query_params"], ctx)
        clean_params = {k: str(v) for k, v in rendered_params.items() if v is not None and v != ""}
        if clean_params:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{urllib.parse.urlencode(clean_params)}"

    payload = None
    if method in ("POST", "PUT", "PATCH") and endpoint_spec.get("body_template") is not None:
        payload = _render_template(endpoint_spec["body_template"], ctx)
        # Drop empty reference_system if location was unknown
        if isinstance(payload, dict) and payload.get("reference_system") in ("", None):
            payload.pop("reference_system", None)

    try:
        raw_resp = _http_json(
            url,
            method=method,
            payload=payload,
            timeout_sec=timeout_sec,
            headers=endpoint_spec.get("headers"),
        )
    except Exception as e:
        logger.warning("Declarative intel query failed (%s): %s", endpoint_name, e)
        return {
            "status": "error",
            "source": endpoint_name,
            "canonical_query": canonical_query,
            "reference_location": effective_loc or location,
            "error": str(e),
        }

    results_path = str(endpoint_spec.get("results_path") or "")
    raw_items = _get_nested_path(raw_resp, results_path, ctx=ctx) if results_path else raw_resp
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    elif not isinstance(raw_items, list):
        raw_items = []

    max_results = max(1, min(15, int(endpoint_spec.get("max_results") or 5)))
    field_map = endpoint_spec.get("result_fields") if isinstance(endpoint_spec.get("result_fields"), dict) else None
    strip_html = bool(endpoint_spec.get("strip_html", False))
    max_field_chars = max(0, int(endpoint_spec.get("max_field_chars") or 0))

    extracted_items: List[Dict[str, Any]] = []
    for item in raw_items[:max_results]:
        if field_map:
            row: Dict[str, Any] = {}
            for out_key, path_expr in field_map.items():
                val = _get_nested_path(item, str(path_expr), ctx=ctx)
                if val is not None:
                    row[out_key] = _clean_intel_field(val, strip_html=strip_html, max_chars=max_field_chars)
            extracted_items.append(row)
        elif isinstance(item, dict):
            extracted_items.append(item)
        else:
            extracted_items.append({"value": item})

    summary_text = _format_structured_summary(
        endpoint_name=endpoint_name,
        query=query,
        canonical_query=canonical_query,
        location=effective_loc or location or "Unknown",
        items=extracted_items,
    )

    return {
        "status": "success",
        "source": endpoint_name,
        "canonical_query": canonical_query,
        "reference_location": effective_loc or location,
        "count": len(extracted_items),
        "results": extracted_items,
        "intel": summary_text,
    }
