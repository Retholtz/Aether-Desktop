"""
Aether Desktop - GitHub Releases Auto-Updater (Shell-Free)
Checks GitHub Releases against the local APP_VERSION, downloads and verifies
release .zip archives via httpx + SHA-256 into <app_dir>/_update_staging/,
extracts via Python's built-in zipfile module, performs an atomic Windows
.old rename swap, and relaunches directly without any shell wrapper.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from core.logger import get_logger
from core.paths import get_app_dir, is_frozen
from core.version import (
    APP_CHANNEL,
    APP_VERSION,
    GITHUB_RELEASES_API,
    GITHUB_RELEASES_PAGE,
    GITHUB_REPO,
)

logger = get_logger("Updater")

EXE_NAME = "AetherDesktop.exe"
INTERNAL_DIR_NAME = "_internal"
STAGING_DIR_NAME = "_update_staging"
PROTECTED_USER_FILES = frozenset({"config.json", ".env"})
TRUSTED_DOWNLOAD_HOSTS = (
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "codeload.github.com",
)


def extract_version_string(raw: str) -> str:
    """Extracts a clean dotted numeric version string (e.g. '0.83') from a tag or filename."""
    text = str(raw or "").strip()
    if not text:
        return ""
    match = re.search(r"(\d+(?:\.\d+)+)", text)
    if match:
        return match.group(1)
    single = re.search(r"(\d+)", text)
    return single.group(1) if single else ""


def parse_version(version_str: str) -> Tuple[int, ...]:
    """
    Parses a version string like '0.82', 'v0.83', 'Version 0.83', or 'Aether 0.83.zip'
    into a normalized integer tuple (trailing zeros trimmed so '0.83' == '0.83.0').
    """
    clean = extract_version_string(version_str)
    if not clean:
        return (0,)
    parts = [int(p) for p in clean.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def is_newer_version(remote_version: str, local_version: str = APP_VERSION) -> bool:
    """Returns True strictly when remote_version > local_version."""
    remote_tuple = parse_version(remote_version)
    local_tuple = parse_version(local_version)
    max_len = max(len(remote_tuple), len(local_tuple))
    r_padded = remote_tuple + (0,) * (max_len - len(remote_tuple))
    l_padded = local_tuple + (0,) * (max_len - len(local_tuple))
    return r_padded > l_padded


def is_trusted_github_url(url: str) -> bool:
    """Validates that a download URL uses HTTPS and points to a trusted GitHub host."""
    try:
        parsed = urlparse(str(url or "").strip())
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            return False
        host = parsed.hostname.lower()
        return any(host == trusted or host.endswith(f".{trusted}") for trusted in TRUSTED_DOWNLOAD_HOSTS)
    except Exception:
        return False


def is_app_dir_writable(app_dir: Optional[str] = None) -> bool:
    """Checks whether the current process has write permission in the application directory."""
    target_dir = app_dir or get_app_dir()
    if not target_dir or not os.path.isdir(target_dir):
        return False
    probe_path = os.path.join(target_dir, f".aether_write_probe_{os.getpid()}")
    try:
        with open(probe_path, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe_path)
        return True
    except Exception:
        try:
            if os.path.exists(probe_path):
                os.remove(probe_path)
        except Exception:
            pass
        return False


def _normalize_sha256(raw: str) -> str:
    """Normalizes an optional 'sha256:<hex>' digest string to lowercase 64-char hex."""
    text = str(raw or "").strip().lower()
    if text.startswith("sha256:"):
        text = text[7:].strip()
    return text if re.fullmatch(r"[0-9a-f]{64}", text) else ""


def select_release_asset(assets: list) -> Optional[Dict[str, Any]]:
    """
    Selects the best .zip release asset (e.g. 'Aether 0.83.zip' or 'Aether.0.83.zip').
    Prefers trusted HTTPS .zip files starting with 'aether', then falls back to any .zip asset.
    """
    if not isinstance(assets, list):
        return None

    zip_candidates = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if name.lower().endswith(".zip") and is_trusted_github_url(url):
            zip_candidates.append(asset)

    if not zip_candidates:
        return None

    for asset in zip_candidates:
        if str(asset.get("name") or "").strip().lower().startswith("aether"):
            return asset

    return zip_candidates[0]


def _extract_asset_sha256(asset: Optional[Dict[str, Any]], assets: list, timeout: float = 10.0) -> str:
    """
    Extracts an expected SHA-256 hex digest from a GitHub release asset's 'digest' field
    or from a companion .sha256 asset if present.
    """
    if isinstance(asset, dict):
        direct = _normalize_sha256(asset.get("digest") or "")
        if direct:
            return direct

    if not isinstance(assets, list) or not isinstance(asset, dict):
        return ""

    asset_name = str(asset.get("name") or "").strip().lower()
    for candidate in assets:
        if not isinstance(candidate, dict):
            continue
        cand_name = str(candidate.get("name") or "").strip().lower()
        cand_url = str(candidate.get("browser_download_url") or "")
        if not is_trusted_github_url(cand_url):
            continue
        if cand_name == f"{asset_name}.sha256" or cand_name in ("sha256sums.txt", "checksums.txt"):
            try:
                resp = httpx.get(cand_url, timeout=timeout, follow_redirects=True)
                if resp.status_code == 200:
                    for line in resp.text.splitlines():
                        parts = line.strip().split()
                        if not parts:
                            continue
                        digest = _normalize_sha256(parts[0])
                        if digest and (len(parts) == 1 or asset_name in line.lower()):
                            return digest
            except Exception:
                pass
    return ""


def check_for_updates(current_version: str = APP_VERSION, timeout: float = 10.0) -> Dict[str, Any]:
    """
    Queries GitHub Releases (/releases/latest) via httpx and compares the release version
    against current_version. Handles 404 (no releases yet) and rate limits gracefully.
    """
    headers = {
        "User-Agent": f"Aether-Desktop/{current_version}",
        "Accept": "application/vnd.github+json",
    }
    base_info: Dict[str, Any] = {
        "current_version": current_version,
        "channel": APP_CHANNEL,
        "repo": GITHUB_REPO,
        "releases_page": GITHUB_RELEASES_PAGE,
        "is_frozen": is_frozen(),
        "can_write_app_dir": is_app_dir_writable(),
    }

    try:
        resp = httpx.get(GITHUB_RELEASES_API, headers=headers, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as exc:
        logger.warning(f"[UPDATER] Network error checking for updates: {exc}")
        return {
            **base_info,
            "success": False,
            "update_available": False,
            "error": f"Could not reach GitHub: {exc}",
        }

    if resp.status_code == 404:
        return {
            **base_info,
            "success": True,
            "update_available": False,
            "no_releases": True,
            "latest_version": current_version,
            "message": "No releases have been published on GitHub yet.",
        }

    if resp.status_code in (403, 429):
        return {
            **base_info,
            "success": False,
            "update_available": False,
            "error": "GitHub API rate limit reached. Please try again later.",
        }

    if resp.status_code >= 400:
        return {
            **base_info,
            "success": False,
            "update_available": False,
            "error": f"GitHub returned HTTP {resp.status_code}.",
        }

    try:
        data = resp.json()
    except ValueError as exc:
        return {
            **base_info,
            "success": False,
            "update_available": False,
            "error": f"Invalid response from GitHub: {exc}",
        }

    tag_name = str(data.get("tag_name") or "").strip()
    release_name = str(data.get("name") or tag_name).strip()
    release_notes = str(data.get("body") or "").strip()
    release_url = str(data.get("html_url") or GITHUB_RELEASES_PAGE).strip()
    published_at = str(data.get("published_at") or "").strip()

    assets = data.get("assets") or []
    asset = select_release_asset(assets)
    asset_name = str(asset.get("name") or "") if asset else ""
    download_url = str(asset.get("browser_download_url") or "") if asset else ""
    asset_size = int(asset.get("size") or 0) if asset else 0
    expected_sha256 = _extract_asset_sha256(asset, assets, timeout=timeout) if asset else ""

    remote_ver = (
        extract_version_string(tag_name)
        or extract_version_string(release_name)
        or extract_version_string(asset_name)
    )
    if not remote_ver:
        return {
            **base_info,
            "success": False,
            "update_available": False,
            "error": f"Could not parse version number from release '{release_name or tag_name}'.",
        }

    update_available = is_newer_version(remote_ver, current_version)
    return {
        **base_info,
        "success": True,
        "update_available": update_available,
        "no_releases": False,
        "latest_version": remote_ver,
        "tag_name": tag_name,
        "release_name": release_name,
        "release_notes": release_notes,
        "release_url": release_url,
        "published_at": published_at,
        "asset_name": asset_name,
        "download_url": download_url,
        "asset_size": asset_size,
        "expected_sha256": expected_sha256,
        "message": (
            f"Update available: v{remote_ver}"
            if update_available
            else f"You are up to date (v{current_version})."
        ),
    }


def strip_mark_of_the_web(target_dir: str) -> int:
    """Removes NTFS Zone.Identifier alternate data streams from extracted binaries."""
    if os.name != "nt" or not target_dir or not os.path.exists(target_dir):
        return 0
    cleared = 0
    for root, _, files in os.walk(target_dir):
        for filename in files:
            ads_path = f"{os.path.join(root, filename)}:Zone.Identifier"
            try:
                if os.path.exists(ads_path):
                    os.remove(ads_path)
                    cleared += 1
            except OSError:
                pass
    return cleared


def _safe_extract_zip(zip_path: str, extract_dir: str) -> None:
    """Verifies archive integrity and extracts a zip archive while guarding against path traversal."""
    abs_extract_dir = os.path.abspath(extract_dir)
    with zipfile.ZipFile(zip_path, "r") as zf:
        bad_member = zf.testzip()
        if bad_member is not None:
            raise zipfile.BadZipFile(f"Corrupted zip entry detected: {bad_member}")
        for member in zf.infolist():
            member_path = os.path.abspath(os.path.join(abs_extract_dir, member.filename))
            if not (member_path == abs_extract_dir or member_path.startswith(abs_extract_dir + os.sep)):
                raise ValueError(f"Unsafe zip entry path detected: {member.filename}")
        zf.extractall(abs_extract_dir)


def _locate_payload_root(extract_dir: str, exe_name: str = EXE_NAME) -> Optional[str]:
    """
    Finds the folder containing AetherDesktop.exe inside the extracted archive.
    Supports both flat zips (AetherDesktop.exe at root) and single-folder zips
    (e.g. AetherDesktop/AetherDesktop.exe or 'Aether 0.83/AetherDesktop.exe').
    """
    if os.path.isfile(os.path.join(extract_dir, exe_name)):
        return extract_dir

    try:
        for entry in os.listdir(extract_dir):
            candidate = os.path.join(extract_dir, entry)
            if os.path.isdir(candidate) and os.path.isfile(os.path.join(candidate, exe_name)):
                return candidate
    except OSError:
        pass
    return None


def download_and_stage_update(
    download_url: str,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    timeout: float = 30.0,
    expected_sha256: str = "",
    app_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Downloads the release .zip from GitHub via httpx into <app_dir>/_update_staging/,
    verifies its SHA-256 checksum, extracts via zipfile, validates AetherDesktop.exe
    is present, and strips user config files.
    """
    if not is_trusted_github_url(download_url):
        return {
            "success": False,
            "error": "Refusing to download update from untrusted or non-HTTPS URL.",
        }

    target_app_dir = os.path.abspath(app_dir or get_app_dir())
    if not is_app_dir_writable(target_app_dir):
        return {
            "success": False,
            "error": f"Application directory is not writable: {target_app_dir}",
        }

    stage_dir = os.path.join(target_app_dir, STAGING_DIR_NAME)
    zip_path = os.path.join(stage_dir, "update.zip")
    extract_dir = os.path.join(stage_dir, "extracted")

    try:
        if os.path.exists(stage_dir):
            shutil.rmtree(stage_dir, ignore_errors=True)
        os.makedirs(extract_dir, exist_ok=True)
    except Exception as exc:
        return {"success": False, "error": f"Failed to prepare staging directory: {exc}"}

    headers = {"User-Agent": f"Aether-Desktop/{APP_VERSION}"}
    sha256_hasher = hashlib.sha256()

    try:
        if progress_cb:
            progress_cb(0, 0, "downloading")
        with httpx.stream(
            "GET",
            download_url,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        ) as resp:
            if resp.status_code >= 400:
                return {"success": False, "error": f"Download failed with HTTP {resp.status_code}."}
            total_bytes = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(zip_path, "wb") as out_f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    if not chunk:
                        continue
                    out_f.write(chunk)
                    sha256_hasher.update(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        progress_cb(downloaded, total_bytes, "downloading")
    except Exception as exc:
        return {"success": False, "error": f"Download error: {exc}"}

    actual_sha256 = sha256_hasher.hexdigest().lower()
    normalized_expected = _normalize_sha256(expected_sha256)
    if expected_sha256 and not normalized_expected:
        shutil.rmtree(stage_dir, ignore_errors=True)
        return {"success": False, "error": "Invalid expected SHA-256 checksum format."}
    if normalized_expected and actual_sha256 != normalized_expected:
        shutil.rmtree(stage_dir, ignore_errors=True)
        return {
            "success": False,
            "error": (
                f"SHA-256 checksum mismatch for update archive "
                f"(expected {normalized_expected}, got {actual_sha256})."
            ),
        }

    try:
        if progress_cb:
            progress_cb(100, 100, "extracting")
        _safe_extract_zip(zip_path, extract_dir)
        cleared_ads = strip_mark_of_the_web(stage_dir)
        if cleared_ads:
            logger.info(f"[UPDATER] Cleared {cleared_ads} Zone.Identifier stream(s) from staged update.")
    except Exception as exc:
        return {"success": False, "error": f"Failed to extract update archive: {exc}"}

    payload_dir = _locate_payload_root(extract_dir, EXE_NAME)
    if not payload_dir:
        return {
            "success": False,
            "error": f"Update archive did not contain {EXE_NAME}.",
        }

    # Never overwrite existing local user secrets/config if accidentally bundled
    for protected_name in PROTECTED_USER_FILES:
        protected_path = os.path.join(payload_dir, protected_name)
        if os.path.isfile(protected_path):
            try:
                os.remove(protected_path)
            except OSError:
                pass

    return {
        "success": True,
        "app_dir": target_app_dir,
        "stage_dir": stage_dir,
        "payload_dir": payload_dir,
        "exe_name": EXE_NAME,
        "sha256": actual_sha256,
    }


def cleanup_old_update_artifacts(app_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Sweeps and deletes any leftover *.old files (including inside _internal/),
    _internal.old directories, or _update_staging directories from a prior update
    on application startup.
    """
    target_dir = os.path.abspath(app_dir or get_app_dir())
    removed: List[str] = []
    if not os.path.isdir(target_dir):
        return {"removed": removed}

    try:
        for root, dirs, files in os.walk(target_dir, topdown=False):
            for fname in files:
                if fname.lower().endswith(".old"):
                    full_path = os.path.join(root, fname)
                    try:
                        os.remove(full_path)
                        removed.append(os.path.relpath(full_path, target_dir))
                    except OSError:
                        pass
            for dname in dirs:
                if dname.lower().endswith(".old") or dname == STAGING_DIR_NAME:
                    full_path = os.path.join(root, dname)
                    try:
                        shutil.rmtree(full_path, ignore_errors=True)
                        if not os.path.exists(full_path):
                            removed.append(os.path.relpath(full_path, target_dir))
                    except OSError:
                        pass
    except OSError:
        return {"removed": removed}

    if removed:
        logger.info(f"[UPDATER] Cleaned up prior update artifacts: {', '.join(removed[:20])}")
    return {"removed": removed}


def _merge_extra_payload_entries(payload_dir: str, app_dir: str, exe_name: str) -> None:
    """Moves any additional top-level payload entries (excluding exe, _internal, and user configs) into app_dir."""
    skip_names = {exe_name, INTERNAL_DIR_NAME, *PROTECTED_USER_FILES}
    for entry in os.listdir(payload_dir):
        if entry in skip_names:
            continue
        src = os.path.join(payload_dir, entry)
        dst = os.path.join(app_dir, entry)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            os.replace(src, dst)


def apply_staged_update_and_relaunch(
    payload_dir: str,
    stage_dir: Optional[str] = None,
    exe_name: str = EXE_NAME,
    app_dir: Optional[str] = None,
    relaunch: bool = True,
    allow_dev_mode: bool = False,
) -> Dict[str, Any]:
    """
    Performs an atomic pure-Python Windows rename swap:
      1. Renames running AetherDesktop.exe -> AetherDesktop.exe.old and _internal -> _internal.old
      2. Moves staged AetherDesktop.exe and _internal into app_dir
      3. Relaunches AetherDesktop.exe directly with shell=False (zero .bat/cmd/PowerShell)
    """
    if sys.platform != "win32" and not allow_dev_mode:
        return {"success": False, "error": "Self-update restart is only supported on Windows."}

    if not is_frozen() and not allow_dev_mode:
        return {
            "success": False,
            "dev_mode": True,
            "error": (
                "Self-update is disabled when running from Python source. "
                "Pull the latest changes via Git or run the packaged executable."
            ),
        }

    target_app_dir = os.path.abspath(app_dir or get_app_dir())
    staged_exe = os.path.join(payload_dir, exe_name)
    staged_internal = os.path.join(payload_dir, INTERNAL_DIR_NAME)

    if not os.path.isfile(staged_exe):
        return {"success": False, "error": f"Staged executable not found: {staged_exe}"}

    current_exe = os.path.join(target_app_dir, exe_name)
    old_exe = os.path.join(target_app_dir, f"{exe_name}.old")
    current_internal = os.path.join(target_app_dir, INTERNAL_DIR_NAME)
    old_internal = os.path.join(target_app_dir, f"{INTERNAL_DIR_NAME}.old")

    # Remove any stale .old artifacts before renaming
    for stale_path in (old_exe, old_internal):
        if os.path.exists(stale_path):
            try:
                if os.path.isdir(stale_path):
                    shutil.rmtree(stale_path, ignore_errors=True)
                else:
                    os.remove(stale_path)
            except OSError:
                pass

    renamed_exe = False
    swapped_internal_files: List[Tuple[str, str]] = []

    try:
        if os.path.exists(current_exe):
            os.replace(current_exe, old_exe)
            renamed_exe = True

        shutil.move(staged_exe, current_exe)

        # Swap _internal file-by-file so Windows never hits WinError 5 on renaming
        # a directory that has open DLL/WebView2 handles.
        if os.path.isdir(staged_internal):
            if not os.path.isdir(current_internal):
                shutil.move(staged_internal, current_internal)
            else:
                for root, _, files in os.walk(staged_internal):
                    rel_root = os.path.relpath(root, staged_internal)
                    dst_root = current_internal if rel_root == "." else os.path.join(current_internal, rel_root)
                    os.makedirs(dst_root, exist_ok=True)
                    for fname in files:
                        src_file = os.path.join(root, fname)
                        dst_file = os.path.join(dst_root, fname)
                        old_file = f"{dst_file}.old"
                        if os.path.exists(dst_file):
                            if os.path.exists(old_file):
                                try:
                                    os.remove(old_file)
                                except OSError:
                                    pass
                            os.replace(dst_file, old_file)
                            swapped_internal_files.append((dst_file, old_file))
                        shutil.move(src_file, dst_file)

        _merge_extra_payload_entries(payload_dir, target_app_dir, exe_name)
    except Exception as exc:
        # Roll back live files if anything failed during swap
        try:
            if renamed_exe and os.path.exists(old_exe):
                if os.path.exists(current_exe):
                    try:
                        os.remove(current_exe)
                    except OSError:
                        pass
                os.replace(old_exe, current_exe)
            for dst_file, old_file in reversed(swapped_internal_files):
                if os.path.exists(old_file):
                    if os.path.exists(dst_file):
                        try:
                            os.remove(dst_file)
                        except OSError:
                            pass
                    os.replace(old_file, dst_file)
        except Exception:
            pass
        return {"success": False, "error": f"Atomic update swap failed: {exc}"}

    if stage_dir and os.path.isdir(stage_dir):
        shutil.rmtree(stage_dir, ignore_errors=True)

    if relaunch:
        try:
            subprocess.Popen(
                [os.path.abspath(current_exe), "--post-update"],
                cwd=target_app_dir,
                shell=False,
                close_fds=True,
            )
        except Exception as exc:
            return {"success": False, "error": f"Update applied, but relaunch failed: {exc}"}

    return {
        "success": True,
        "exe_path": os.path.abspath(current_exe),
        "relaunched": relaunch,
    }
