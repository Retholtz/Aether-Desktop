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
    ("scripts/library", "scripts/library"),
    ("scripts/skills_catalog.json", "scripts"),
    ("config.example.json", "."),
    (".env.example", "."),
]

if os.path.isfile("models/wespeaker_en_voxceleb_CAM++.onnx"):
    datas.append(("models/wespeaker_en_voxceleb_CAM++.onnx", "models"))

binaries = []
for bin_path in (
    "assets/bin/PresentMon.exe",
):
    if os.path.isfile(bin_path):
        binaries.append((bin_path, "assets/bin"))

hiddenimports = [
    "pystray._win32",
    "PIL._tkinter_finder",
    "pycaw.pycaw",
    "comtypes.stream",
    "pynvml",
    "nvidia_ml_py",
    "bottle",
    "pypdf",
    "google.genai",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "winrt.windows.media.control",
    "winrt.windows.storage.streams",
    "winrt.windows.ui.notifications",
    "winrt.windows.ui.notifications.management",
    "winrt.windows.applicationmodel",
]

# Collect native/runtime assets for WebView2, Python.NET, ViGEm, sounddevice, and sherpa-onnx
for pkg in ("webview", "pythonnet", "clr_loader", "vgamepad", "sounddevice", "sherpa_onnx"):
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
