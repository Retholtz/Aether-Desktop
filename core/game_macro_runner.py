"""
core/game_macro_runner.py - State-Conditioned & Composite Visual/Keystroke Macro Execution Engine.

Executes atomic keybinds with precondition checking (idempotency) and multi-step sequences
supporting:
- `press`: key + modifiers, optional `hold_ms`, `repeat`, `interval_ms`
- `trigger_bind`: resolves & triggers a synced profile keybind by phrase/action_id
- `vision_click`: captures screen, locates a UI element via Gemini Vision, and clicks/holds it
- `click`: mouse button click (optional x, y)
- `type` / `paste`: DirectInput typing or clipboard paste (Ctrl+V)
- `wait`: timed delay
"""

import difflib
import time
import threading
from typing import Dict, Any, Optional, List, Tuple

from core.logger import get_logger
from core.telemetry.base import BaseTelemetryAdapter
import tools.os_controls as os_controls

logger = get_logger("MacroRunner")

VALID_STEP_ACTIONS = {"press", "trigger_bind", "vision_click", "click", "type", "paste", "wait"}


def normalize_macro_step(step: Any) -> Optional[Dict[str, Any]]:
    """
    Normalizes both canonical step dicts (`{"action": "wait", "delay_ms": 500}`)
    and common LLM shorthand dicts (`{"wait": 500}`, `{"trigger_bind": {"phrase": "left panel"}}`,
    `{"press": "space"}`, `{"vision_click": "Auto Launch"}`).
    """
    if not isinstance(step, dict) or not step:
        return None
    if step.get("action"):
        out = dict(step)
        out["action"] = str(out["action"]).lower().strip()
        return out

    # Shorthand form: single known action key (e.g. {"wait": 500} or {"trigger_bind": {"phrase": "..."}})
    for act in VALID_STEP_ACTIONS:
        if act in step:
            val = step[act]
            out = {k: v for k, v in step.items() if k != act}
            out["action"] = act
            if isinstance(val, dict):
                out.update(val)
            elif act == "wait" and isinstance(val, (int, float)):
                out["delay_ms"] = val
            elif act == "press" and isinstance(val, str):
                out["key"] = val
            elif act == "trigger_bind" and isinstance(val, str):
                out["phrase"] = val
            elif act == "vision_click" and isinstance(val, str):
                out["target"] = val
            elif act in ("type", "paste") and isinstance(val, str):
                out["text"] = val
            elif act == "click" and isinstance(val, str):
                out["button"] = val
            return out

    # Infer action from characteristic keys if "action" was omitted
    if "target" in step or "element" in step:
        return {"action": "vision_click", **step}
    if "phrase" in step or "bind" in step:
        return {"action": "trigger_bind", **step}
    if "key" in step:
        return {"action": "press", **step}
    if "text" in step:
        return {"action": "type", **step}
    if "delay_ms" in step and len(step) == 1:
        return {"action": "wait", **step}
    return None


def normalize_and_validate_macro_steps(
    raw_steps: Any,
    profile: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Normalizes a list of macro steps and validates `trigger_bind` references against `profile["keybinds"]`.
    Returns `(normalized_steps, errors)`.
    """
    if not isinstance(raw_steps, list) or not raw_steps:
        return [], ["Macro must include at least one step."]

    normalized: List[Dict[str, Any]] = []
    errors: List[str] = []
    available_binds = list((profile.get("keybinds") or {}).keys()) if isinstance(profile, dict) else []

    for idx, raw_step in enumerate(raw_steps, 1):
        step = normalize_macro_step(raw_step)
        if not step or step.get("action") not in VALID_STEP_ACTIONS:
            errors.append(
                f"Step #{idx} has invalid format ({raw_step!r}). Use one of: {', '.join(sorted(VALID_STEP_ACTIONS))}."
            )
            continue
        act = step["action"]
        if act == "trigger_bind" and isinstance(profile, dict) and available_binds:
            b_phrase = str(step.get("phrase") or step.get("bind") or "").strip()
            if not b_phrase or _resolve_bind_entry(b_phrase, profile) is None:
                suggestions = difflib.get_close_matches(b_phrase.lower(), available_binds, n=4, cutoff=0.35)
                hint = f" Closest synced binds: {suggestions}" if suggestions else ""
                errors.append(f"Step #{idx} references unknown keybind '{b_phrase}'.{hint}")
                continue
        elif act == "press" and not str(step.get("key") or "").strip():
            errors.append(f"Step #{idx} ('press') is missing a 'key' value.")
            continue
        elif act == "vision_click" and not str(step.get("target") or step.get("element") or "").strip():
            errors.append(f"Step #{idx} ('vision_click') is missing a 'target' UI description.")
            continue
        normalized.append(step)

    return normalized, errors


def evaluate_condition(condition_str: str, state_flags: Dict[str, Any]) -> bool:
    """
    Evaluates a simple precondition expression against state flags.
    Examples:
        "!state.landing_gear_down" -> True if landing_gear_down is False
        "state.docked" -> True if docked is True
        "state.shields_up == false" -> True if shields_up is False
    """
    if not condition_str or not isinstance(condition_str, str):
        return True

    expr = condition_str.strip()
    try:
        invert = expr.startswith("!")
        if invert:
            expr = expr[1:].strip()

        if expr.startswith("state."):
            flag_name = expr[len("state."):].strip()
            if "==" in flag_name:
                parts = flag_name.split("==", 1)
                k = parts[0].strip()
                v = parts[1].strip().lower() in ("true", "1")
                res = bool(state_flags.get(k, False)) == v
            elif "!=" in flag_name:
                parts = flag_name.split("!=", 1)
                k = parts[0].strip()
                v = parts[1].strip().lower() in ("true", "1")
                res = bool(state_flags.get(k, False)) != v
            else:
                res = bool(state_flags.get(flag_name, False))

            return not res if invert else res

        return True
    except Exception as e:
        logger.warning(f"Error evaluating condition '{condition_str}': {e}")
        return True


def _resolve_bind_entry(phrase: str, profile: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    clean = str(phrase or "").lower().strip()
    if not clean:
        return None
    actions = profile.get("actions", {})
    keybinds = profile.get("keybinds", {})
    entry = actions.get(clean) or keybinds.get(clean)
    if entry:
        return entry
    for name, item in {**actions, **keybinds}.items():
        if not isinstance(item, dict):
            continue
        aliases = [str(a).lower().strip() for a in (item.get("aliases") or [])]
        act_id = str(item.get("action_id") or "").lower().strip()
        if clean == str(name).lower().strip() or clean in aliases or (act_id and clean == act_id):
            return item
    return None


class MacroRunner:
    """Manages state-aware keybind triggering and composite visual/keystroke sequence execution."""

    def __init__(self):
        self._abort_event = threading.Event()
        self._active_sequence_thread: Optional[threading.Thread] = None

    def abort_running_sequence(self) -> None:
        """Signals any running macro sequence to abort immediately."""
        self._abort_event.set()
        logger.info("Macro sequence abort signal set.")

    def execute_action(
        self,
        phrase: str,
        profile: Dict[str, Any],
        telemetry_adapter: Optional[BaseTelemetryAdapter] = None,
        vision_client: Any = None,
        model_endpoint: str = "gemini-4-hydrogen",
    ) -> Dict[str, Any]:
        """
        Executes a spoken macro or action phrase against profile keybinds/sequences.
        Evaluates preconditions using telemetry state flags when available.
        """
        clean_phrase = str(phrase or "").lower().strip().strip(".,!?;:\"'")
        state_flags = telemetry_adapter.get_state_flags() if telemetry_adapter else {}

        # Merge module default sequences with user profile sequences (profile wins; respect disabled defaults)
        disabled_seqs = {str(x).lower().strip() for x in (profile.get("disabled_module_sequences") or [])}
        merged_sequences: Dict[str, Any] = {}
        try:
            from core.game_modules import get_module_for_game
            mod = get_module_for_game(profile.get("module_id") or profile.get("display_name") or "", profile=profile)
            if isinstance(mod, dict) and isinstance(mod.get("sequences"), dict):
                for k, v in mod["sequences"].items():
                    if str(k).lower().strip() not in disabled_seqs:
                        merged_sequences[k] = v
        except Exception:
            pass
        if isinstance(profile.get("sequences"), dict):
            merged_sequences.update(profile["sequences"])

        # 1. Check for Composite Sequence match (case-insensitive names & aliases)
        seq_def = None
        seq_matched_name = clean_phrase
        for seq_name, item in merged_sequences.items():
            sname_low = str(seq_name).lower().strip()
            aliases = [str(a).lower().strip() for a in (item.get("aliases") or [])] if isinstance(item, dict) else []
            if sname_low == clean_phrase or clean_phrase in aliases:
                seq_def = item
                seq_matched_name = sname_low
                break
        if seq_def is not None:
            return self._run_sequence(
                seq_matched_name,
                seq_def,
                profile,
                state_flags,
                vision_client=vision_client,
                model_endpoint=model_endpoint,
            )

        # 2. Check for Atomic Keybind/Action match (case-insensitive names & aliases)
        action_def = _resolve_bind_entry(clean_phrase, profile)
        if not action_def:
            return {"status": "not_found", "message": f"No action or sequence configured for '{phrase}'."}

        # 3. Check Precondition
        condition = action_def.get("condition")
        if condition:
            is_satisfied = evaluate_condition(condition, state_flags)
            if not is_satisfied:
                skip_ack = action_def.get("condition_skip_ack") or f"Action '{phrase}' skipped (already in target state)."
                logger.info(f"Precondition '{condition}' not met for '{phrase}'. Skipping.")
                return {
                    "status": "condition_skipped",
                    "action": phrase,
                    "condition": condition,
                    "ack": skip_ack,
                    "message": skip_ack
                }

        # 4. Execute Action (Mouse or Keyboard)
        key = str(action_def.get("key", "")).lower().strip()
        modifiers = action_def.get("modifiers", [])
        ack = action_def.get("ack") or action_def.get("description") or f"Executed {phrase}"
        game_title = profile.get("display_name", "")

        mouse_map = {
            "mouse_left": "left",
            "left_click": "left",
            "mouse_right": "right",
            "right_click": "right",
            "mouse_middle": "middle",
            "middle_click": "middle",
        }
        if key in mouse_map:
            os_controls.click_mouse(button=mouse_map[key])
            return {
                "status": "executed",
                "game": game_title,
                "action": action_def.get("description", phrase),
                "type": "mouse",
                "device": "mouse",
                "ack": ack,
                "message": f"Successfully triggered '{phrase}'."
            }

        # Gamepad bypass to allow GameManager gamepad logic
        if key.startswith("gamepad_") or key.startswith("pad_") or key.startswith("btn_") or key in ("lt", "rt", "left_trigger", "right_trigger", "wheel_up", "wheel_down", "scroll_up", "scroll_down"):
            return {"status": "unhandled_device", "key": key, "action": phrase}

        success = False
        if key:
            if modifiers:
                success = os_controls.send_directinput_combo(modifiers, key)
            else:
                success = os_controls.send_directinput_key(key)

        return {
            "status": "executed" if success else "failed",
            "game": game_title,
            "action": action_def.get("description", phrase),
            "key": key,
            "modifiers": modifiers,
            "ack": ack,
            "type": "keyboard",
            "device": "keyboard",
            "message": f"Successfully triggered '{phrase}'." if success else f"Failed sending input for '{phrase}'."
        }

    def _execute_single_step(
        self,
        step: Dict[str, Any],
        profile: Dict[str, Any],
        vision_client: Any = None,
        model_endpoint: str = "gemini-4-hydrogen",
    ) -> Optional[Dict[str, Any]]:
        """Executes a single macro step. Returns an error dict if a guarded step fails & aborts."""
        norm = normalize_macro_step(step)
        if norm is None:
            return None
        step = norm
        action_type = str(step.get("action") or "press").lower().strip()
        delay_ms = float(step.get("delay_ms") if step.get("delay_ms") is not None else 100)

        if action_type == "press":
            k = str(step.get("key") or "").lower().strip()
            mods = step.get("modifiers") or []
            hold_sec = max(0.02, float(step.get("hold_ms") or 80) / 1000.0)
            repeat = max(1, min(50, int(step.get("repeat") or 1)))
            interval_sec = max(0.0, float(step.get("interval_ms") if step.get("interval_ms") is not None else 50) / 1000.0)
            if k:
                for idx in range(repeat):
                    if self._abort_event.is_set():
                        break
                    if mods:
                        os_controls.send_directinput_combo(mods, k, hold_duration=hold_sec)
                    else:
                        os_controls.send_directinput_key(k, duration_sec=hold_sec)
                    if idx < repeat - 1 and interval_sec > 0:
                        if self._abort_event.wait(interval_sec):
                            break

        elif action_type == "trigger_bind":
            bind_phrase = str(step.get("phrase") or step.get("bind") or "").strip()
            repeat = max(1, min(50, int(step.get("repeat") or 1)))
            interval_sec = max(0.0, float(step.get("interval_ms") if step.get("interval_ms") is not None else 50) / 1000.0)
            entry = _resolve_bind_entry(bind_phrase, profile)
            if not entry:
                if step.get("abort_if_not_found"):
                    return {"status": "failed", "message": f"Referenced bind '{bind_phrase}' not found in profile."}
            else:
                k = str(entry.get("key") or "").lower().strip()
                mods = entry.get("modifiers") or []
                for idx in range(repeat):
                    if self._abort_event.is_set():
                        break
                    if k:
                        if mods:
                            os_controls.send_directinput_combo(mods, k)
                        else:
                            os_controls.send_directinput_key(k)
                    if idx < repeat - 1 and interval_sec > 0:
                        if self._abort_event.wait(interval_sec):
                            break

        elif action_type == "vision_click":
            target = str(step.get("target") or step.get("element") or "").strip()
            btn = str(step.get("button") or "left").lower().strip()
            hold_sec = max(0.04, float(step.get("hold_ms") or 80) / 1000.0)
            abort_missing = bool(step.get("abort_if_not_found", True))
            if not target:
                if abort_missing:
                    return {"status": "failed", "message": "vision_click step missing 'target' description."}
            elif not vision_client:
                if abort_missing:
                    return {"status": "failed", "message": f"Vision client unavailable to locate '{target}' on screen."}
            else:
                from core.game_nav import UniversalGameNavigator
                proc = profile.get("process_name") or profile.get("display_name") or ""
                if proc:
                    try:
                        os_controls.focus_window(proc)
                        time.sleep(0.12)
                    except Exception:
                        pass
                nav = UniversalGameNavigator(
                    client=vision_client,
                    model_endpoint=model_endpoint,
                    target_process=proc,
                    game_name=profile.get("display_name", ""),
                )
                pt = nav.locate_ui_element_point(target)
                if not pt:
                    if abort_missing:
                        return {
                            "status": "element_not_found",
                            "message": f"Could not find UI element '{target}' on screen.",
                        }
                else:
                    os_controls.move_mouse_absolute(pt[0], pt[1])
                    time.sleep(0.1)
                    os_controls.click_mouse_button(btn, hold_duration=hold_sec)

        elif action_type == "type":
            txt = str(step.get("text") or "")
            if txt:
                os_controls.send_directinput_string(txt)

        elif action_type == "paste":
            txt = str(step.get("text") or "")
            if txt:
                try:
                    from tools.clipboard import set_clipboard_text
                    if set_clipboard_text(txt):
                        os_controls.send_directinput_combo(["ctrl"], "v", hold_duration=0.06)
                    else:
                        os_controls.send_directinput_string(txt)
                except Exception:
                    os_controls.send_directinput_string(txt)

        elif action_type == "click":
            btn = str(step.get("button") or "left").lower().strip()
            if step.get("x") is not None and step.get("y") is not None:
                os_controls.move_mouse_absolute(int(step["x"]), int(step["y"]))
                time.sleep(0.08)
            hold_sec = max(0.04, float(step.get("hold_ms") or 80) / 1000.0)
            os_controls.click_mouse_button(btn, hold_duration=hold_sec)

        elif action_type == "wait":
            pass

        if delay_ms and delay_ms > 0:
            self._abort_event.wait(delay_ms / 1000.0)
        return None

    def _run_sequence(
        self,
        name: str,
        seq_def: Any,
        profile: Dict[str, Any],
        state_flags: Dict[str, Any],
        vision_client: Any = None,
        model_endpoint: str = "gemini-4-hydrogen",
    ) -> Dict[str, Any]:
        """Runs a multi-step macro sequence with timing, vision verification, and abort safety."""
        if isinstance(seq_def, dict):
            condition = seq_def.get("condition")
            if condition and not evaluate_condition(condition, state_flags):
                skip_ack = seq_def.get("condition_skip_ack") or f"Sequence '{name}' skipped (condition not met)."
                return {"status": "condition_skipped", "action": name, "ack": skip_ack, "message": skip_ack}
            steps = seq_def.get("steps", [])
            ack = seq_def.get("ack", f"Executing sequence {name}.")
            explicit_sync = seq_def.get("sync")
        elif isinstance(seq_def, list):
            steps = seq_def
            ack = f"Executing sequence {name}."
            explicit_sync = None
        else:
            return {"status": "error", "message": "Invalid sequence format."}

        has_vision_step = any(
            isinstance(s, dict) and str(s.get("action") or "").lower().strip() == "vision_click"
            for s in steps
        )
        run_sync = bool(explicit_sync) if explicit_sync is not None else has_vision_step

        # Abort and cleanly join any prior running sequence so keypresses never interleave
        prev_thread = self._active_sequence_thread
        if prev_thread is not None and prev_thread.is_alive():
            self._abort_event.set()
            prev_thread.join(timeout=0.5)

        self._abort_event.clear()

        if run_sync:
            for step in steps:
                if self._abort_event.is_set():
                    return {"status": "aborted", "action": name, "message": f"Sequence '{name}' aborted."}
                if not isinstance(step, dict):
                    continue
                err = self._execute_single_step(
                    step, profile, vision_client=vision_client, model_endpoint=model_endpoint
                )
                if err is not None:
                    return {
                        "status": err.get("status", "failed"),
                        "action": name,
                        "message": err.get("message", f"Sequence '{name}' failed."),
                    }
            return {
                "status": "executed",
                "action": name,
                "step_count": len(steps),
                "ack": ack,
                "message": f"Sequence '{name}' completed ({len(steps)} steps).",
            }

        def step_worker():
            for step in steps:
                if self._abort_event.is_set():
                    logger.info(f"Sequence '{name}' aborted by user.")
                    break
                if not isinstance(step, dict):
                    continue
                err = self._execute_single_step(
                    step, profile, vision_client=vision_client, model_endpoint=model_endpoint
                )
                if err is not None:
                    logger.warning(f"Sequence '{name}' stopped on step error: {err.get('message')}")
                    break

        thread = threading.Thread(target=step_worker, daemon=True, name=f"SeqRunner_{name}")
        thread.start()
        self._active_sequence_thread = thread

        return {
            "status": "executed",
            "action": name,
            "step_count": len(steps),
            "ack": ack,
            "message": f"Sequence '{name}' started ({len(steps)} steps)."
        }
