"""
core/binds_scanners/elite_xml.py - Elite Dangerous XML Keybindings Scanner.

Parses custom .binds XML files from Elite Dangerous to automatically extract
active keyboard bindings into standard Aether action mappings.
"""

import os
import glob
import xml.etree.ElementTree as ET
from typing import Dict, Any
from core.logger import get_logger

logger = get_logger("EliteBindsScanner")

DEFAULT_BINDS_DIR = r"%LOCALAPPDATA%\Frontier Developments\Elite Dangerous\Options\Bindings"

TARGET_ACTIONS = {
    "DeployHeatSink": ("deploy heat sink", "Deploy Heat Sink", None),
    "ToggleFlightAssist": ("toggle flight assist", "Toggle Flight Assist", None),
    "ChargeECM": ("charge ecm", "Charge ECM", None),
    "IncreaseEnginesPower": ("divert power to engines", "Power to Engines", None),
    "IncreaseWeaponsPower": ("divert power to weapons", "Power to Weapons", None),
    "IncreaseSystemsPower": ("divert power to systems", "Power to Systems", None),
    "ResetPowerDistribution": ("balance power", "Balance Power", None),
    "ToggleCargoScoop": ("toggle cargo scoop", "Toggle Cargo Scoop", None),
    "LandingGearToggle": ("deploy landing gear", "Toggle Landing Gear", "!state.landing_gear_down"),
    "DeployHardpointToggle": ("deploy hardpoints", "Toggle Hardpoints", "!state.hardpoints_deployed"),
    "FireChaffLauncher": ("fire chaff", "Fire Chaff", None),
    "SelectTarget": ("target ahead", "Target Ahead", None),
    "CycleNextTarget": ("next target", "Target Next", None),
    "SelectHighestThreat": ("target highest threat", "Target Highest Threat", None),
    "HyperSuperCombination": ("frame shift drive", "Toggle Frame Shift Drive", None),
}


def scan_elite_bindings(profile: Dict[str, Any]) -> Dict[str, dict]:
    """Scans and extracts active keybinds from Elite Dangerous XML binds files."""
    raw_dir = (
        profile.get("bindings_dir")
        or (profile.get("binds_config") or {}).get("dir")
        or DEFAULT_BINDS_DIR
    )
    binds_dir = os.path.expandvars(raw_dir)
    if not os.path.exists(binds_dir):
        return {}

    bind_files = glob.glob(os.path.join(binds_dir, "*.binds"))
    if not bind_files:
        return {}

    # Pick the most recently modified binds file
    latest_file = max(bind_files, key=os.path.getmtime)
    discovered = {}

    try:
        tree = ET.parse(latest_file)
        root = tree.getroot()

        for xml_elem, (phrase, desc, condition) in TARGET_ACTIONS.items():
            node = root.find(xml_elem)
            if node is not None:
                slot = node.find("Primary")
                if slot is None or slot.get("Device") != "Keyboard":
                    slot = node.find("Secondary")

                if slot is not None and slot.get("Device") == "Keyboard":
                    raw_key = slot.get("Key", "").replace("Key_", "").lower()
                    mods = [m.get("Key", "").replace("Key_", "").lower() for m in slot.findall("Modifier")]
                    if raw_key:
                        item = {
                            "key": raw_key,
                            "modifiers": mods,
                            "description": desc,
                            "source": "auto_detected"
                        }
                        if condition:
                            item["condition"] = condition
                        discovered[phrase] = item

                        # Add common aliases (e.g. "lower landing gear" for "deploy landing gear")
                        if phrase == "deploy landing gear":
                            discovered["lower landing gear"] = dict(item, aliases=["deploy landing gear"])
                        elif phrase == "deploy hardpoints":
                            discovered["draw weapons"] = dict(item, aliases=["deploy hardpoints"])

        logger.info(f"Scanned {len(discovered)} active keybinds from {os.path.basename(latest_file)}.")
        return discovered
    except Exception as e:
        logger.warning(f"Failed parsing Elite Dangerous binds: {e}")
        return {}

