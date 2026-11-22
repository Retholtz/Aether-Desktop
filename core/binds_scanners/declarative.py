"""
core/binds_scanners/declarative.py - Universal Declarative Keybinding Scanner.

Parses game keybind files (XML primary/secondary, XML attribute-based, INI/CFG, JSON)
purely from a declarative `binds_scanner` spec in `.aethermodule.json`.

Design principles:
- Zero arbitrary allowlist cap: imports ALL bound actions (1 or 1,000+) by default.
- Controller-agnostic: supports Keyboard, Mouse, GamePad/XInput, and arbitrary controllers/HOTAS.
- Normalizes game-specific key tokens (e.g. Key_Equals, Key_Numpad_0, GamePad_FaceDown)
  into standard Aether scancode/mouse/gamepad tokens.
"""

import glob
import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from core.logger import get_logger

logger = get_logger("DeclarativeBindsScanner")

# Universal key token normalization (strips common game prefixes & maps names to SCANCODE_MAP)
KEY_TOKEN_MAP: Dict[str, str] = {
    "equals": "=",
    "equal": "=",
    "plus": "=",
    "minus": "-",
    "hyphen": "-",
    "underscore": "-",
    "semicolon": ";",
    "apostrophe": "'",
    "quote": "'",
    "grave": "`",
    "tilde": "`",
    "backtick": "`",
    "backslash": "\\",
    "slash": "/",
    "forwardslash": "/",
    "comma": ",",
    "period": ".",
    "dot": ".",
    "leftbracket": "[",
    "rightbracket": "]",
    "lbracket": "[",
    "rbracket": "]",
    "uparrow": "up",
    "downarrow": "down",
    "leftarrow": "left",
    "rightarrow": "right",
    "up_arrow": "up",
    "down_arrow": "down",
    "left_arrow": "left",
    "right_arrow": "right",
    "pageup": "pageup",
    "pagedown": "pagedown",
    "page_up": "pageup",
    "page_down": "pagedown",
    "leftshift": "leftshift",
    "rightshift": "rightshift",
    "lshift": "leftshift",
    "rshift": "rightshift",
    "leftcontrol": "leftctrl",
    "rightcontrol": "rightctrl",
    "leftctrl": "leftctrl",
    "rightctrl": "rightctrl",
    "lctrl": "leftctrl",
    "rctrl": "rightctrl",
    "lcontrol": "leftctrl",
    "rcontrol": "rightctrl",
    "leftalt": "leftalt",
    "rightalt": "rightalt",
    "lalt": "leftalt",
    "ralt": "rightalt",
    "spacebar": "space",
    "return": "enter",
    "esc": "escape",
    "del": "delete",
    "ins": "insert",
    "caps": "capslock",
    "capital": "capslock",
    "numpad_0": "numpad_0",
    "numpad_1": "numpad_1",
    "numpad_2": "numpad_2",
    "numpad_3": "numpad_3",
    "numpad_4": "numpad_4",
    "numpad_5": "numpad_5",
    "numpad_6": "numpad_6",
    "numpad_7": "numpad_7",
    "numpad_8": "numpad_8",
    "numpad_9": "numpad_9",
    "numpad0": "numpad_0",
    "numpad1": "numpad_1",
    "numpad2": "numpad_2",
    "numpad3": "numpad_3",
    "numpad4": "numpad_4",
    "numpad5": "numpad_5",
    "numpad6": "numpad_6",
    "numpad7": "numpad_7",
    "numpad8": "numpad_8",
    "numpad9": "numpad_9",
    "numpad_add": "numpad_add",
    "numpad_subtract": "numpad_subtract",
    "numpad_multiply": "numpad_multiply",
    "numpad_divide": "numpad_divide",
    "numpad_decimal": "numpad_decimal",
    "numpad_enter": "numpad_enter",
    "add": "numpad_add",
    "subtract": "numpad_subtract",
    "multiply": "numpad_multiply",
    "divide": "numpad_divide",
    "decimal": "numpad_decimal",
}

MOUSE_TOKEN_MAP: Dict[str, str] = {
    "mouse_1": "mouse_left",
    "mouse_2": "mouse_right",
    "mouse_3": "mouse_middle",
    "mouse1": "mouse_left",
    "mouse2": "mouse_right",
    "mouse3": "mouse_middle",
    "lmb": "mouse_left",
    "rmb": "mouse_right",
    "mmb": "mouse_middle",
    "mouse_left": "mouse_left",
    "mouse_right": "mouse_right",
    "mouse_middle": "mouse_middle",
    "mouse_wheelup": "scroll_up",
    "mouse_wheeldown": "scroll_down",
    "mwheelup": "scroll_up",
    "mwheeldown": "scroll_down",
}

GAMEPAD_TOKEN_MAP: Dict[str, str] = {
    "gamepad_facedown": "gamepad_a",
    "gamepad_faceright": "gamepad_b",
    "gamepad_faceleft": "gamepad_x",
    "gamepad_faceup": "gamepad_y",
    "gamepad_lbumper": "gamepad_lb",
    "gamepad_rbumper": "gamepad_rb",
    "gamepad_lthumb": "gamepad_ls",
    "gamepad_rthumb": "gamepad_rs",
    "gamepad_dpaddown": "gamepad_dpad_down",
    "gamepad_dpadup": "gamepad_dpad_up",
    "gamepad_dpadleft": "gamepad_dpad_left",
    "gamepad_dpadright": "gamepad_dpad_right",
    "gamepad_back": "gamepad_view",
    "gamepad_start": "gamepad_menu",
    "pos_joy_zaxis": "lt",
    "neg_joy_zaxis": "rt",
    "pos_joy_rzaxis": "rt",
    "neg_joy_rzaxis": "lt",
}


def humanize_action_id(action_id: str) -> str:
    """Converts CamelCase or snake_case XML/INI action IDs into natural lowercase voice phrases."""
    s = str(action_id or "").strip()
    if not s:
        return ""
    # Split acronyms & CamelCase boundaries (e.g. "ChargeECM" -> "Charge ECM", "FSDJump" -> "FSD Jump")
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"[_\-\.]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.lower()


def normalize_key_token(raw_key: str, device: str = "Keyboard", key_prefix: str = "Key_") -> Tuple[str, str]:
    """
    Normalizes a raw key/button string and device into `(normalized_key, device_kind)`.
    `device_kind` is one of: 'keyboard', 'mouse', 'gamepad', 'controller'.
    """
    raw = str(raw_key or "").strip()
    if not raw:
        return "", "none"

    dev_low = str(device or "").strip().lower()
    raw_low = raw.lower()

    # Mouse
    if dev_low == "mouse" or raw_low.startswith("mouse_") or raw_low in MOUSE_TOKEN_MAP:
        mapped = MOUSE_TOKEN_MAP.get(raw_low)
        if mapped:
            return mapped, "mouse"
        return raw_low, "mouse"

    # Keyboard
    if dev_low == "keyboard" or (key_prefix and raw.startswith(key_prefix)):
        stripped = raw[len(key_prefix):] if (key_prefix and raw.startswith(key_prefix)) else raw
        low = stripped.lower().strip()
        return KEY_TOKEN_MAP.get(low, low), "keyboard"

    # Gamepad (controller-agnostic: any controller reporting GamePad_* or standard XInput names)
    if raw_low in GAMEPAD_TOKEN_MAP:
        return GAMEPAD_TOKEN_MAP[raw_low], "gamepad"
    if raw_low.startswith("gamepad_") or raw_low.startswith("pad_") or "gamepad" in dev_low or "xbox" in dev_low:
        return GAMEPAD_TOKEN_MAP.get(raw_low, raw_low), "gamepad"

    # Arbitrary HOTAS / Joystick / Flight Stick / Steering Wheel / Custom HID
    return raw_low, "controller"


def _classify_device_slot(device: str, raw_key: str) -> str:
    dev = str(device or "").strip()
    if not dev or dev == "{NoDevice}" or not str(raw_key or "").strip():
        return "none"
    dev_low = dev.lower()
    key_low = str(raw_key or "").strip().lower()
    if dev_low == "keyboard":
        return "Keyboard"
    if dev_low == "mouse":
        return "Mouse"
    if key_low.startswith("gamepad_") or key_low in GAMEPAD_TOKEN_MAP or "gamepad" in dev_low or "xbox" in dev_low:
        return "GamePad"
    return "Controller"


def _pick_binds_file(binds_dir: str, file_glob: str, preset_pointer_file: Optional[str] = None) -> Optional[str]:
    """Resolves the active keybinds file using an optional preset pointer file, else newest matching file."""
    candidates = glob.glob(os.path.join(binds_dir, file_glob or "*"))
    if not candidates:
        return None

    if preset_pointer_file:
        pointer_path = os.path.join(binds_dir, preset_pointer_file)
        if os.path.isfile(pointer_path):
            try:
                with open(pointer_path, "r", encoding="utf-8", errors="ignore") as f:
                    preset_names = [line.strip() for line in f if line.strip()]
                # Prefer a custom preset present in binds_dir (check in reverse/unique order or match prefix)
                for pname in preset_names:
                    matched = [
                        c for c in candidates
                        if os.path.basename(c).lower().startswith(pname.lower() + ".")
                        or os.path.splitext(os.path.basename(c))[0].lower() == pname.lower()
                    ]
                    if matched:
                        return max(matched, key=os.path.getmtime)
            except Exception as e:
                logger.debug("Preset pointer read skipped (%s): %s", pointer_path, e)

    return max(candidates, key=os.path.getmtime)


def _matches_any_pattern(text: str, compiled_patterns: List[re.Pattern]) -> bool:
    for pat in compiled_patterns:
        if pat.search(text):
            return True
    return False


def _scan_xml_primary_secondary(
    binds_dir: str,
    spec: Dict[str, Any],
) -> Dict[str, dict]:
    """
    Generic XML scanner for schemas where each child element of `<Root>` is an action ID
    containing `<Primary Device="..." Key="...">` and/or `<Secondary ...>` (or `<Binding ...>`)
    with optional `<Modifier Device="..." Key="..."/>` children.
    """
    file_glob = str(spec.get("file_glob") or "*.binds")
    preset_pointer = spec.get("preset_pointer_file")
    key_prefix = str(spec.get("key_prefix") if spec.get("key_prefix") is not None else "Key_")
    device_priority = list(spec.get("device_priority") or ["Keyboard", "Mouse", "GamePad", "Controller"])
    auto_import_all = bool(spec.get("auto_import_all", True))
    actions_spec: Dict[str, Any] = spec.get("actions") if isinstance(spec.get("actions"), dict) else {}
    raw_excludes = spec.get("exclude_patterns") or []
    compiled_excludes = []
    for pat in raw_excludes:
        try:
            compiled_excludes.append(re.compile(str(pat), re.IGNORECASE))
        except re.error:
            pass

    target_file = _pick_binds_file(binds_dir, file_glob, preset_pointer_file=preset_pointer)
    if not target_file:
        return {}

    priority_rank = {d.lower(): idx for idx, d in enumerate(device_priority)}
    discovered: Dict[str, dict] = {}

    try:
        tree = ET.parse(target_file)
        root = tree.getroot()
    except Exception as e:
        logger.warning("Failed parsing XML binds file %s: %s", target_file, e)
        return {}

    for node in root:
        action_id = node.tag
        curated = actions_spec.get(action_id)

        if curated is None and not auto_import_all:
            continue
        if curated is None and compiled_excludes and _matches_any_pattern(action_id, compiled_excludes):
            continue

        # Collect candidate slots (<Primary>, <Secondary>, <Binding>)
        slots: List[Tuple[int, ET.Element, str]] = []
        for slot_tag in ("Primary", "Secondary", "Binding"):
            slot = node.find(slot_tag)
            if slot is None:
                continue
            dev = slot.get("Device", "")
            raw_k = slot.get("Key", "")
            dev_class = _classify_device_slot(dev, raw_k)
            if dev_class == "none":
                continue
            rank = priority_rank.get(dev_class.lower(), 999)
            if rank == 999 and dev_class.lower() not in priority_rank:
                # Only include devices listed in device_priority
                continue
            slots.append((rank, slot, dev_class))

        if not slots:
            continue

        # Pick highest-priority slot (e.g. Keyboard > Mouse > GamePad > Controller)
        slots.sort(key=lambda item: item[0])
        _, chosen_slot, chosen_dev_class = slots[0]

        raw_dev = chosen_slot.get("Device", "")
        raw_key = chosen_slot.get("Key", "")
        norm_key, device_kind = normalize_key_token(raw_key, device=raw_dev, key_prefix=key_prefix)
        if not norm_key:
            continue

        mods: List[str] = []
        for m in chosen_slot.findall("Modifier"):
            m_key, _ = normalize_key_token(
                m.get("Key", ""),
                device=m.get("Device", raw_dev),
                key_prefix=key_prefix,
            )
            if m_key:
                mods.append(m_key)

        if isinstance(curated, dict):
            phrase = str(curated.get("phrase") or humanize_action_id(action_id)).lower().strip()
            desc = str(curated.get("description") or phrase.title())
            condition = curated.get("condition")
            condition_skip_ack = curated.get("condition_skip_ack")
            aliases = [str(a).lower().strip() for a in (curated.get("aliases") or []) if str(a).strip()]
            extra_entries = curated.get("variants") if isinstance(curated.get("variants"), list) else []
        else:
            phrase = humanize_action_id(action_id)
            desc = phrase.title()
            condition = None
            condition_skip_ack = None
            aliases = []
            extra_entries = []

        if not phrase:
            continue

        entry: Dict[str, Any] = {
            "key": norm_key,
            "modifiers": mods,
            "description": desc,
            "source": "auto_detected",
            "action_id": action_id,
            "device": device_kind,
        }
        if raw_dev and device_kind == "controller":
            entry["raw_device"] = raw_dev
        if condition:
            entry["condition"] = condition
        if condition_skip_ack:
            entry["condition_skip_ack"] = condition_skip_ack
        if aliases:
            entry["aliases"] = aliases

        # Also record secondary keyboard/mouse/gamepad slot metadata if present
        if len(slots) > 1:
            alt_slot = slots[1][1]
            alt_k, alt_kind = normalize_key_token(
                alt_slot.get("Key", ""),
                device=alt_slot.get("Device", ""),
                key_prefix=key_prefix,
            )
            if alt_k:
                entry["secondary"] = {"key": alt_k, "device": alt_kind}

        discovered[phrase] = entry

        # Optional explicit alias entries (e.g. "lower landing gear", "retract landing gear")
        for var in extra_entries:
            if not isinstance(var, dict) or not var.get("phrase"):
                continue
            v_phrase = str(var["phrase"]).lower().strip()
            v_entry = dict(entry)
            v_entry["description"] = str(var.get("description") or desc)
            if "condition" in var:
                if var["condition"]:
                    v_entry["condition"] = var["condition"]
                else:
                    v_entry.pop("condition", None)
            if var.get("condition_skip_ack"):
                v_entry["condition_skip_ack"] = var["condition_skip_ack"]
            v_aliases = [str(a).lower().strip() for a in (var.get("aliases") or []) if str(a).strip()]
            if v_aliases:
                v_entry["aliases"] = v_aliases
            else:
                v_entry.pop("aliases", None)
            discovered[v_phrase] = v_entry

    logger.info("Declarative XML scanner extracted %d keybinds from %s.", len(discovered), os.path.basename(target_file))
    return discovered


def _scan_xml_attr(binds_dir: str, spec: Dict[str, Any]) -> Dict[str, dict]:
    """
    Generic XML scanner for attribute-style keybind files (e.g. `<action name="..." keyboard="..."/>`
    or `<mapping name="..." key="..."/>`).
    """
    file_glob = str(spec.get("file_glob") or "*.xml")
    target_file = _pick_binds_file(binds_dir, file_glob, spec.get("preset_pointer_file"))
    if not target_file:
        return {}

    element_tag = str(spec.get("element_tag") or "action")
    name_attr = str(spec.get("name_attr") or "name")
    key_attr = str(spec.get("key_attr") or "keyboard")
    key_prefix = str(spec.get("key_prefix") or "")
    actions_spec: Dict[str, Any] = spec.get("actions") if isinstance(spec.get("actions"), dict) else {}
    auto_import_all = bool(spec.get("auto_import_all", True))

    discovered: Dict[str, dict] = {}
    try:
        root = ET.parse(target_file).getroot()
    except Exception as e:
        logger.warning("Failed parsing XML attr binds file %s: %s", target_file, e)
        return {}

    for elem in root.iter(element_tag):
        action_id = (elem.get(name_attr) or "").strip()
        raw_key = (elem.get(key_attr) or "").strip()
        if not action_id or not raw_key:
            continue
        curated = actions_spec.get(action_id)
        if curated is None and not auto_import_all:
            continue

        norm_key, dev_kind = normalize_key_token(raw_key, device="Keyboard", key_prefix=key_prefix)
        if not norm_key:
            continue

        phrase = (
            str(curated.get("phrase")).lower().strip()
            if isinstance(curated, dict) and curated.get("phrase")
            else humanize_action_id(action_id)
        )
        desc = (
            str(curated.get("description"))
            if isinstance(curated, dict) and curated.get("description")
            else phrase.title()
        )
        discovered[phrase] = {
            "key": norm_key,
            "modifiers": [],
            "description": desc,
            "source": "auto_detected",
            "action_id": action_id,
            "device": dev_kind,
        }
    return discovered


def _scan_ini_or_json(binds_dir: str, spec: Dict[str, Any], fmt: str) -> Dict[str, dict]:
    """Generic INI/CFG or JSON keybind file scanner driven by module spec."""
    file_glob = str(spec.get("file_glob") or ("*.json" if fmt == "json" else "*.ini"))
    target_file = _pick_binds_file(binds_dir, file_glob, spec.get("preset_pointer_file"))
    if not target_file:
        return {}

    key_prefix = str(spec.get("key_prefix") or "")
    actions_spec: Dict[str, Any] = spec.get("actions") if isinstance(spec.get("actions"), dict) else {}
    auto_import_all = bool(spec.get("auto_import_all", True))
    discovered: Dict[str, dict] = {}

    if fmt == "json":
        try:
            with open(target_file, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
        except Exception:
            return {}
        root_key = spec.get("json_root_key")
        mapping = data.get(root_key, data) if (root_key and isinstance(data, dict)) else data
        if isinstance(mapping, dict):
            for act_id, raw_val in mapping.items():
                curated = actions_spec.get(act_id)
                if curated is None and not auto_import_all:
                    continue
                if isinstance(raw_val, dict):
                    raw_k = str(raw_val.get("key") or "")
                    mods = [str(m).lower() for m in (raw_val.get("modifiers") or [])]
                else:
                    raw_k = str(raw_val or "")
                    mods = []
                norm_k, dev_kind = normalize_key_token(raw_k, device="Keyboard", key_prefix=key_prefix)
                if not norm_k:
                    continue
                phrase = (
                    str(curated.get("phrase")).lower().strip()
                    if isinstance(curated, dict) and curated.get("phrase")
                    else humanize_action_id(act_id)
                )
                discovered[phrase] = {
                    "key": norm_k,
                    "modifiers": mods,
                    "description": (curated.get("description") if isinstance(curated, dict) else None) or phrase.title(),
                    "source": "auto_detected",
                    "action_id": act_id,
                    "device": dev_kind,
                }
        return discovered

    # INI / CFG
    try:
        with open(target_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith(("#", ";", "[")) or "=" not in s:
                    continue
                act_id, raw_k = [p.strip() for p in s.split("=", 1)]
                curated = actions_spec.get(act_id)
                if curated is None and not auto_import_all:
                    continue
                norm_k, dev_kind = normalize_key_token(raw_k, device="Keyboard", key_prefix=key_prefix)
                if not norm_k:
                    continue
                phrase = (
                    str(curated.get("phrase")).lower().strip()
                    if isinstance(curated, dict) and curated.get("phrase")
                    else humanize_action_id(act_id)
                )
                discovered[phrase] = {
                    "key": norm_k,
                    "modifiers": [],
                    "description": (curated.get("description") if isinstance(curated, dict) else None) or phrase.title(),
                    "source": "auto_detected",
                    "action_id": act_id,
                    "device": dev_kind,
                }
    except Exception:
        return {}
    return discovered


def scan_declarative_bindings(profile: Dict[str, Any], scanner_spec: Dict[str, Any]) -> Dict[str, dict]:
    """
    Executes a pure-JSON declarative keybinds scanner spec against the user's local binds directory.
    """
    if not isinstance(scanner_spec, dict) or not scanner_spec:
        return {}

    raw_dir = (
        (profile.get("bindings_dir") if isinstance(profile, dict) else None)
        or ((profile.get("binds_config") or {}).get("dir") if isinstance(profile, dict) else None)
        or scanner_spec.get("dir")
        or ""
    )
    if not raw_dir:
        return {}

    binds_dir = os.path.expandvars(str(raw_dir))
    if not os.path.exists(binds_dir):
        logger.debug("Binds directory does not exist: %s", binds_dir)
        return {}

    fmt = str(scanner_spec.get("format") or "xml_primary_secondary").strip().lower()
    if fmt == "xml_primary_secondary":
        return _scan_xml_primary_secondary(binds_dir, scanner_spec)
    if fmt == "xml_attr":
        return _scan_xml_attr(binds_dir, scanner_spec)
    if fmt in ("ini", "cfg", "json"):
        return _scan_ini_or_json(binds_dir, scanner_spec, fmt)

    logger.warning("Unsupported declarative binds_scanner format '%s'.", fmt)
    return {}
