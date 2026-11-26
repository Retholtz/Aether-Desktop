# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Aether Desktop (Core + Runtimes only).
Excludes tests, personal user data (data/, profile/, logs/), config.json, and .env.
"""

import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

datas = [
    ("ui/static", "ui/static"),
    ("ui/sounds/*.wav", "ui/sounds"),
    ("assets/icon.png", "assets"),
    ("assets/icon.ico", "assets"),
    ("scripts/skills_catalog.json", "scripts"),
    ("config.example.json", "."),
    (".env.example", "."),
]

# Bundle scripts/library excluding any __pycache__ directories and *.pyc bytecode files
if os.path.isdir("scripts/library"):
    for _root, _dirs, _files in os.walk("scripts/library"):
        _dirs[:] = [d for d in _dirs if d != "__pycache__"]
        _rel_dir = os.path.relpath(_root, ".").replace("\\", "/")
        for _fname in _files:
            if _fname.endswith((".pyc", ".pyo")):
                continue
            datas.append((os.path.join(_root, _fname), _rel_dir))

if os.path.isfile("models/wespeaker_en_voxceleb_CAM++.onnx"):
    datas.append(("models/wespeaker_en_voxceleb_CAM++.onnx", "models"))

if os.path.isdir("assets/game_modules"):
    datas.append(("assets/game_modules", "assets/game_modules"))

if os.path.isfile("assets/openxr/XR_APILAYER_AETHER_overlay.json"):
    datas.append(("assets/openxr/XR_APILAYER_AETHER_overlay.json", "assets/openxr"))

binaries = []
for bin_path, bin_dest in (
    ("assets/bin/PresentMon.exe", "assets/bin"),
    ("assets/openxr/aether_openxr_layer.dll", "assets/openxr"),
):
    if os.path.isfile(bin_path):
        binaries.append((bin_path, bin_dest))

hiddenimports = [
    "pystray._win32",
    "PIL",
    "PIL._tkinter_finder",
    "pycaw.pycaw",
    "comtypes.stream",
    "pynvml",
    "nvidia_ml_py",
    "bottle",
    "pypdf",
    "google.genai",
    "openpyxl",
    "et_xmlfile",
    "docx",
    "pptx",
    "matplotlib",
    "matplotlib.backends.backend_agg",
    "lxml",
    "bs4",
    "soupsieve",
    "win32api",
    "win32clipboard",
    "win32con",
    "win32gui",
    "win32com.client",
    "pythoncom",
    "numpy",
    "requests",
    "httpx",
    "aiohttp",
    "mss",
    "psutil",
    "tools.heavy_reasoning",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "winrt.windows.media.control",
    "winrt.windows.storage.streams",
    "winrt.windows.ui.notifications",
    "winrt.windows.ui.notifications.management",
    "winrt.windows.applicationmodel",
]

# Collect submodules for bundled document/HTML automation packages
for _auto_pkg in ("openpyxl", "docx", "bs4", "lxml"):
    try:
        hiddenimports += collect_submodules(_auto_pkg)
    except Exception:
        pass

# Collect full data + submodules for presentation/charting packages (pptx XML templates & matplotlib fonts/data)
for _pres_pkg in ("pptx", "matplotlib"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(_pres_pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception:
        pass

# Collect native/runtime assets for WebView2, Python.NET, ViGEm, sounddevice, sherpa-onnx, and OpenVR
for pkg in ("webview", "pythonnet", "clr_loader", "vgamepad", "sounddevice", "sherpa_onnx", "openvr"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception:
        pass

for mod in ("core", "ui", "api", "security", "tools"):
    hiddenimports += collect_submodules(mod)

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "_pytest", "WinTmp", "wintmp"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AetherDesktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # Set to True if you want a console window for stdout debugging
    uac_admin=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
    version="version_info.txt",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AetherDesktop",
)
