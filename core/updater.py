"""
Aether Desktop - GitHub Releases Auto-Updater
Checks GitHub Releases against the local APP_VERSION, downloads and stages
release .zip archives safely into %TEMP%, and launches a detached .bat
replacer script to swap the executable/_internal bundle and reboot the app.
"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlparse

import requests

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
TRUSTED_DOWNLOAD_HOSTS = (
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "codeload.github.com",
)


def extract_version_string(raw: str) -> str:
    """Extracts a clean dotted numeric version string (e.g. '0.82') from a tag or filename."""
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
    Parses a version string like '0.81', 'v0.82', 'Version 0.82', or 'Aether 0.82.zip'
    into a normalized integer tuple (trailing zeros trimmed so '0.81' == '0.81.0').
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


def select_release_asset(assets: list) -> Optional[Dict[str, Any]]:
    """
    Selects the best .zip release asset (e.g. 'Aether 0.82.zip' or 'Aether.0.82.zip').
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


def check_for_updates(current_version: str = APP_VERSION, timeout: float = 10.0) -> Dict[str, Any]:
    """
    Queries GitHub Releases (/releases/latest) and compares the release version
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
        resp = requests.get(GITHUB_RELEASES_API, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
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

    if not resp.ok:
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

    asset = select_release_asset(data.get("assets") or [])
    asset_name = str(asset.get("name") or "") if asset else ""
    download_url = str(asset.get("browser_download_url") or "") if asset else ""
    asset_size = int(asset.get("size") or 0) if asset else 0

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
        "message": (
            f"Update available: v{remote_ver}"
            if update_available
            else f"You are up to date (v{current_version})."
        ),
    }


def _safe_extract_zip(zip_path: str, extract_dir: str) -> None:
    """Extracts a zip archive into extract_dir while guarding against path traversal."""
    abs_extract_dir = os.path.abspath(extract_dir)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            member_path = os.path.abspath(os.path.join(abs_extract_dir, member.filename))
            if not (member_path == abs_extract_dir or member_path.startswith(abs_extract_dir + os.sep)):
                raise ValueError(f"Unsafe zip entry path detected: {member.filename}")
        zf.extractall(abs_extract_dir)


def _locate_payload_root(extract_dir: str, exe_name: str = EXE_NAME) -> Optional[str]:
    """
    Finds the folder containing AetherDesktop.exe inside the extracted archive.
    Supports both flat zips (AetherDesktop.exe at root) and single-folder zips
    (e.g. AetherDesktop/AetherDesktop.exe or 'Aether 0.82/AetherDesktop.exe').
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
) -> Dict[str, Any]:
    """
    Downloads the release .zip from GitHub into %TEMP%\\aether_desktop_update,
    extracts it, validates AetherDesktop.exe is present, and strips user config files.
    """
    if not is_trusted_github_url(download_url):
        return {
            "success": False,
            "error": "Refusing to download update from untrusted or non-HTTPS URL.",
        }

    stage_dir = os.path.join(tempfile.gettempdir(), "aether_desktop_update")
    zip_path = os.path.join(stage_dir, "update.zip")
    extract_dir = os.path.join(stage_dir, "extracted")

    try:
        if os.path.exists(stage_dir):
            shutil.rmtree(stage_dir, ignore_errors=True)
        os.makedirs(extract_dir, exist_ok=True)
    except Exception as exc:
        return {"success": False, "error": f"Failed to prepare staging directory: {exc}"}

    headers = {"User-Agent": f"Aether-Desktop/{APP_VERSION}"}

    try:
        if progress_cb:
            progress_cb(0, 0, "downloading")
        with requests.get(download_url, headers=headers, stream=True, timeout=timeout) as resp:
            if not resp.ok:
                return {"success": False, "error": f"Download failed with HTTP {resp.status_code}."}
            total_bytes = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(zip_path, "wb") as out_f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    out_f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        progress_cb(downloaded, total_bytes, "downloading")
    except Exception as exc:
        return {"success": False, "error": f"Download error: {exc}"}

    try:
        if progress_cb:
            progress_cb(100, 100, "extracting")
        _safe_extract_zip(zip_path, extract_dir)
    except Exception as exc:
        return {"success": False, "error": f"Failed to extract update archive: {exc}"}

    payload_dir = _locate_payload_root(extract_dir, EXE_NAME)
    if not payload_dir:
        return {
            "success": False,
            "error": f"Update archive did not contain {EXE_NAME}.",
        }

    # Never overwrite existing local user secrets/config if accidentally bundled
    for protected_name in ("config.json", ".env"):
        protected_path = os.path.join(payload_dir, protected_name)
        if os.path.isfile(protected_path):
            try:
                os.remove(protected_path)
            except OSError:
                pass

    return {
        "success": True,
        "stage_dir": stage_dir,
        "payload_dir": payload_dir,
        "exe_name": EXE_NAME,
    }


def build_updater_bat_script(
    old_pid: int,
    app_dir: str,
    payload_dir: str,
    stage_dir: str,
    exe_name: str = EXE_NAME,
) -> str:
    """
    Builds the Windows .bat script that waits for the running process to exit,
    deletes the old executable, swaps in the new build, reboots AetherDesktop.exe,
    and cleans up temporary update files.
    """
    log_path = os.path.join(tempfile.gettempdir(), "aether_update.log")
    return f"""@echo off
setlocal enabledelayedexpansion

set "OLD_PID={int(old_pid)}"
set "APP_DIR={app_dir}"
set "PAYLOAD_DIR={payload_dir}"
set "STAGE_DIR={stage_dir}"
set "EXE_NAME={exe_name}"
set "LOG_FILE={log_path}"

echo [%DATE% %TIME%] Starting Aether Desktop update > "%LOG_FILE%"
echo Waiting for PID %OLD_PID% to exit... >> "%LOG_FILE%"

set /a WAIT_COUNT=0
:wait_loop
tasklist /FI "PID eq %OLD_PID%" 2>NUL | find /I "%OLD_PID%" >NUL
if "%ERRORLEVEL%"=="0" (
    set /a WAIT_COUNT+=1
    if !WAIT_COUNT! GEQ 30 (
        echo PID %OLD_PID% still active after 30s; forcing termination... >> "%LOG_FILE%"
        taskkill /F /PID %OLD_PID% >NUL 2>&1
        goto wait_done
    )
    timeout /t 1 /nobreak >NUL
    goto wait_loop
)

:wait_done
rem Allow WebView2 child processes a moment to release file handles
timeout /t 2 /nobreak >NUL

rem Delete old executable with retries
set /a DEL_TRIES=0
:del_exe_loop
if exist "%APP_DIR%\\%EXE_NAME%" (
    del /F /Q "%APP_DIR%\\%EXE_NAME%" >> "%LOG_FILE%" 2>&1
    if exist "%APP_DIR%\\%EXE_NAME%" (
        set /a DEL_TRIES+=1
        if !DEL_TRIES! LSS 6 (
            timeout /t 1 /nobreak >NUL
            goto del_exe_loop
        )
        echo ERROR: Could not delete old %EXE_NAME%. >> "%LOG_FILE%"
        goto cleanup_and_launch
    )
)

rem Backup existing _internal bundle if replacing it
if exist "%PAYLOAD_DIR%\\_internal" (
    if exist "%APP_DIR%\\_internal_backup" rmdir /S /Q "%APP_DIR%\\_internal_backup" >> "%LOG_FILE%" 2>&1
    if exist "%APP_DIR%\\_internal" (
        move /Y "%APP_DIR%\\_internal" "%APP_DIR%\\_internal_backup" >> "%LOG_FILE%" 2>&1
    )
)

rem Copy new executable and updated bundle into APP_DIR (preserves config.json, .env, data, logs, profile, scripts)
robocopy "%PAYLOAD_DIR%" "%APP_DIR%" /E /IS /IT /R:5 /W:1 >> "%LOG_FILE%" 2>&1
set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 (
    echo ERROR: robocopy failed with exit code %RC%. Restoring backup... >> "%LOG_FILE%"
    if exist "%APP_DIR%\\_internal_backup" (
        if exist "%APP_DIR%\\_internal" rmdir /S /Q "%APP_DIR%\\_internal" >> "%LOG_FILE%" 2>&1
        move /Y "%APP_DIR%\\_internal_backup" "%APP_DIR%\\_internal" >> "%LOG_FILE%" 2>&1
    )
) else (
    echo Update files copied successfully (robocopy code %RC%). >> "%LOG_FILE%"
    if exist "%APP_DIR%\\_internal_backup" rmdir /S /Q "%APP_DIR%\\_internal_backup" >> "%LOG_FILE%" 2>&1
)

:cleanup_and_launch
if exist "%APP_DIR%\\%EXE_NAME%" (
    echo Launching updated %EXE_NAME%... >> "%LOG_FILE%"
    start "" /D "%APP_DIR%" "%APP_DIR%\\%EXE_NAME%"
) else (
    echo CRITICAL: %EXE_NAME% missing after update attempt! >> "%LOG_FILE%"
)

if exist "%STAGE_DIR%" rmdir /S /Q "%STAGE_DIR%" >> "%LOG_FILE%" 2>&1
(goto) 2>nul & del /F /Q "%~f0"
"""


def launch_updater_bat(
    payload_dir: str,
    stage_dir: str,
    exe_name: str = EXE_NAME,
) -> Dict[str, Any]:
    """
    Writes the update .bat script to %TEMP% and launches it detached so it can
    replace AetherDesktop.exe after this process exits.
    """
    if sys.platform != "win32":
        return {"success": False, "error": "Self-update restart is only supported on Windows."}

    if not is_frozen():
        return {
            "success": False,
            "dev_mode": True,
            "error": "Self-update is disabled when running from Python source. Pull the latest changes via Git or run the packaged executable.",
        }

    app_dir = get_app_dir()
    bat_path = os.path.join(tempfile.gettempdir(), "aether_apply_update.bat")
    script_content = build_updater_bat_script(
        old_pid=os.getpid(),
        app_dir=app_dir,
        payload_dir=payload_dir,
        stage_dir=stage_dir,
        exe_name=exe_name,
    )

    try:
        with open(bat_path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(script_content)
    except Exception as exc:
        return {"success": False, "error": f"Failed to write updater batch script: {exc}"}

    try:
        if is_app_dir_writable(app_dir):
            creation_flags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            )
            subprocess.Popen(
                ["cmd.exe", "/c", bat_path],
                cwd=tempfile.gettempdir(),
                creationflags=creation_flags,
                close_fds=True,
            )
        else:
            # App is likely in a protected directory like C:\Program Files; request UAC elevation
            ret = ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                "cmd.exe",
                f'/c "{bat_path}"',
                tempfile.gettempdir(),
                0,
            )
            if ret <= 32:
                return {
                    "success": False,
                    "error": f"Administrator permission is required to update files in {app_dir} (ShellExecute code {ret}).",
                }
        return {"success": True, "bat_path": bat_path}
    except Exception as exc:
        return {"success": False, "error": f"Failed to launch updater script: {exc}"}
