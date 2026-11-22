"""
core/game_extensions.py - Tier 2 Opt-In Shareable Python Game Extensions Loader.

Allows community developers to share optional `.py` extensions alongside pure-JSON
`.aethermodule.json` files when a game requires custom logic beyond declarative JSON
(e.g., async job-polling APIs, binary shared-memory/UDP telemetry, custom tools).

Security model:
- Pure JSON `.aethermodule.json` files remain 100% zero-code and auto-load safely.
- Any `.py` extension in `data/game_extensions/<module_id>/` is DISABLED by default
  until explicitly trusted by the user via `trust_game_extension(module_id)`.
- Trust records store the SHA-256 hash of the `.py` file; any modification to the
  file automatically invalidates trust until re-approved.
- Uses `importlib.util.spec_from_file_location` so community `.py` extensions work
  seamlessly both in source mode and inside PyInstaller builds.
"""

import hashlib
import importlib.util
import json
import os
from typing import Any, Callable, Dict, List, Optional

from core.logger import get_logger
from core.paths import app_path

logger = get_logger("GameExtensions")

TRUST_REGISTRY_FILENAME = "trusted_extensions.json"


class GameExtension:
    """
    Base class for shareable Tier 2 community Python game extensions.
    Subclasses may override any subset of hooks; unused hooks return None.
    """

    module_id: str = ""
    version: str = "1.0.0"
    author: str = ""
    description: str = ""

    def scan_binds(self, profile: Dict[str, Any]) -> Optional[Dict[str, dict]]:
        """Optional custom keybinds scanner. Return dict of phrase -> bind spec, or None to fall back."""
        return None

    def query_intel(
        self,
        query: str,
        category: str = "market",
        location: Optional[str] = None,
        telemetry: Optional[Dict[str, Any]] = None,
        profile: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Optional custom intel provider. Return result dict with 'status'/'intel', or None to fall back."""
        return None

    def create_telemetry_adapter(
        self,
        game_id: str,
        profile: Optional[Dict[str, Any]] = None,
        on_milestone: Optional[Callable[..., None]] = None,
    ) -> Optional[Any]:
        """Optional custom telemetry adapter instance (subclassing BaseTelemetryAdapter), or None."""
        return None

    def register_tools(self, dispatcher: Any, game_manager: Any, engine: Any = None) -> List[str]:
        """Optional extra tool registrations. Return list of registered tool names."""
        return []


def get_extensions_dir(base_dir: Optional[str] = None) -> str:
    path = base_dir or app_path("data", "game_extensions")
    os.makedirs(path, exist_ok=True)
    return path


def _compute_sha256(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _trust_registry_path(base_dir: Optional[str] = None) -> str:
    return os.path.join(get_extensions_dir(base_dir), TRUST_REGISTRY_FILENAME)


def _load_trust_registry(base_dir: Optional[str] = None) -> Dict[str, Any]:
    reg_path = _trust_registry_path(base_dir)
    if not os.path.isfile(reg_path):
        return {}
    try:
        with open(reg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_trust_registry(registry: Dict[str, Any], base_dir: Optional[str] = None) -> None:
    reg_path = _trust_registry_path(base_dir)
    os.makedirs(os.path.dirname(reg_path), exist_ok=True)
    tmp_path = f"{reg_path}.{os.getpid()}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)
    os.replace(tmp_path, reg_path)


def _normalize_module_id(module_id: str) -> str:
    return "".join(c if c.isalnum() or c == "_" else "_" for c in str(module_id or "").strip().lower()).strip("_")


def resolve_extension_script(
    module_id: str,
    script_name: str = "extension.py",
    base_dir: Optional[str] = None,
) -> Optional[str]:
    """
    Safely resolves a `.py` extension path inside `data/game_extensions/<module_id>/`.
    Prevents directory traversal outside the extension folder.
    """
    norm_id = _normalize_module_id(module_id)
    if not norm_id:
        return None
    safe_script = os.path.basename(str(script_name or "extension.py").strip())
    if not safe_script.endswith(".py"):
        return None

    ext_root = os.path.realpath(get_extensions_dir(base_dir))
    candidate = os.path.realpath(os.path.join(ext_root, norm_id, safe_script))
    if not candidate.startswith(ext_root + os.sep):
        return None
    if os.path.isfile(candidate):
        return candidate

    # Fallback: check plugin.py if default extension.py wasn't found
    if safe_script == "extension.py":
        alt = os.path.realpath(os.path.join(ext_root, norm_id, "plugin.py"))
        if alt.startswith(ext_root + os.sep) and os.path.isfile(alt):
            return alt
    return None


def list_game_extensions(base_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Discovers all installed `.py` game extensions and reports their SHA-256 trust status."""
    ext_root = get_extensions_dir(base_dir)
    registry = _load_trust_registry(base_dir)
    items: List[Dict[str, Any]] = []

    if not os.path.isdir(ext_root):
        return items

    for entry in sorted(os.listdir(ext_root)):
        subdir = os.path.join(ext_root, entry)
        if not os.path.isdir(subdir):
            continue
        script_path = resolve_extension_script(entry, base_dir=base_dir)
        if not script_path:
            continue
        sha = _compute_sha256(script_path)
        trust_rec = registry.get(entry) if isinstance(registry.get(entry), dict) else {}
        is_trusted = bool(trust_rec.get("trusted") and trust_rec.get("sha256") == sha)
        hash_changed = bool(trust_rec.get("trusted") and trust_rec.get("sha256") and trust_rec.get("sha256") != sha)
        items.append({
            "module_id": entry,
            "script_path": script_path,
            "sha256": sha,
            "trusted": is_trusted,
            "hash_changed": hash_changed,
        })
    return items


def trust_game_extension(
    module_id: str,
    script_name: str = "extension.py",
    base_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Explicitly trusts a `.py` extension by recording its current SHA-256 hash."""
    norm_id = _normalize_module_id(module_id)
    script_path = resolve_extension_script(norm_id, script_name=script_name, base_dir=base_dir)
    if not script_path:
        return {"success": False, "error": f"No Python extension found for '{module_id}'."}

    sha = _compute_sha256(script_path)
    registry = _load_trust_registry(base_dir)
    registry[norm_id] = {
        "trusted": True,
        "sha256": sha,
        "script": os.path.basename(script_path),
    }
    _save_trust_registry(registry, base_dir=base_dir)
    _LOADED_EXTENSIONS.pop((norm_id, script_path), None)
    return {"success": True, "module_id": norm_id, "sha256": sha, "trusted": True}


def revoke_game_extension(module_id: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Revokes trust for a `.py` extension and clears any cached instance."""
    norm_id = _normalize_module_id(module_id)
    registry = _load_trust_registry(base_dir)
    registry.pop(norm_id, None)
    _save_trust_registry(registry, base_dir=base_dir)
    for key in list(_LOADED_EXTENSIONS.keys()):
        if key[0] == norm_id:
            _LOADED_EXTENSIONS.pop(key, None)
    return {"success": True, "module_id": norm_id, "trusted": False}


# Cache: (module_id, script_path) -> (sha256, GameExtension instance)
_LOADED_EXTENSIONS: Dict[tuple, tuple] = {}


def load_trusted_extension(
    module_id: str,
    script_name: str = "extension.py",
    base_dir: Optional[str] = None,
) -> Optional[GameExtension]:
    """
    Loads and returns a `GameExtension` instance ONLY if the `.py` file exists
    AND its current SHA-256 matches the user's explicit trust registry entry.
    """
    norm_id = _normalize_module_id(module_id)
    if not norm_id:
        return None
    script_path = resolve_extension_script(norm_id, script_name=script_name, base_dir=base_dir)
    if not script_path:
        return None

    current_sha = _compute_sha256(script_path)
    registry = _load_trust_registry(base_dir)
    trust_rec = registry.get(norm_id)
    if not isinstance(trust_rec, dict) or not trust_rec.get("trusted"):
        return None
    if trust_rec.get("sha256") != current_sha:
        logger.warning(
            "Extension '%s' hash mismatch (file modified since approval); skipping load until re-trusted.",
            norm_id,
        )
        return None

    cache_key = (norm_id, script_path)
    cached = _LOADED_EXTENSIONS.get(cache_key)
    if cached and cached[0] == current_sha:
        return cached[1]

    try:
        spec = importlib.util.spec_from_file_location(f"aether_game_ext_{norm_id}", script_path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        ext_instance: Optional[GameExtension] = None
        if hasattr(mod, "create_extension") and callable(mod.create_extension):
            ext_instance = mod.create_extension()
        elif hasattr(mod, "EXTENSION") and isinstance(mod.EXTENSION, GameExtension):
            ext_instance = mod.EXTENSION
        else:
            for attr in vars(mod).values():
                if isinstance(attr, type) and issubclass(attr, GameExtension) and attr is not GameExtension:
                    ext_instance = attr()
                    break

        if ext_instance is not None:
            if not getattr(ext_instance, "module_id", ""):
                ext_instance.module_id = norm_id
            _LOADED_EXTENSIONS[cache_key] = (current_sha, ext_instance)
            logger.info("Loaded trusted Tier 2 game extension '%s' (%s).", norm_id, os.path.basename(script_path))
        return ext_instance
    except Exception as e:
        logger.warning("Failed loading trusted game extension '%s': %s", norm_id, e)
        return None
