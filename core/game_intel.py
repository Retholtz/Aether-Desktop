"""
core/game_intel.py - Universal Declarative REST Intel Engine.

Executes structured HTTPS GET/POST queries declared in any `.aethermodule.json`
`intel_endpoints` block, with:
- SSRF protection (HTTPS only, blocks loopback/private/link-local targets)
- Optional cached `value_resolver` canonicalization (e.g. "Liquid Oxygen" -> "Liquid oxygen")
- Safe template interpolation for `{query}`, `{canonical_query}`, `{location}`, `{telemetry.*}`
- Flexible dotted & list-filter field extraction (e.g. `market[commodity~={canonical_query}].buy_price`)
"""

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
        raw = resp.read(2_000_000).decode("utf-8", errors="replace").strip()
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

    return query


def _format_structured_summary(
    endpoint_name: str,
    query: str,
    canonical_query: str,
    location: str,
    items: List[Dict[str, Any]],
) -> str:
    """Formats extracted JSON result rows into a concise, COVAS-ready intel brief."""
    if not items:
        return (
            f"Live query ({endpoint_name}) for '{canonical_query}' near '{location}' "
            f"returned 0 matching results."
        )

    lines = [
        f"Live database results ({endpoint_name}) for '{canonical_query}' near '{location}' ({len(items)} matches):"
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


def query_declarative_intel(
    endpoint_spec: Dict[str, Any],
    query: str,
    location: str = "",
    telemetry_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Executes a single declarative REST intel endpoint from `.aethermodule.json`.
    Returns `{status, source, canonical_query, reference_location, results, intel}`.
    """
    if not isinstance(endpoint_spec, dict) or not endpoint_spec.get("url"):
        return {"status": "error", "error": "Invalid intel endpoint specification."}

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

    extracted_items: List[Dict[str, Any]] = []
    for item in raw_items[:max_results]:
        if field_map:
            row: Dict[str, Any] = {}
            for out_key, path_expr in field_map.items():
                val = _get_nested_path(item, str(path_expr), ctx=ctx)
                if val is not None:
                    row[out_key] = val
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
