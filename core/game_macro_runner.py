"""
core/game_macro_runner.py - State-Conditioned & Composite Macro Execution Engine.

Executes atomic keybinds with precondition checking (idempotency) and multi-step UI sequences.
Enforces window safety and supports emergency aborting.
"""

import time
import threading
from typing import Dict, Any, Optional, List

from core.logger import get_logger
from core.telemetry.base import BaseTelemetryAdapter
import tools.os_controls as os_controls

logger = get_logger("MacroRunner")


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
        # Normalize expression
        # Replace 'state.foo' with lookup
        invert = expr.startswith("!")
        if invert:
            expr = expr[1:].strip()

        if expr.startswith("state."):
            flag_name = expr[len("state."):].strip()
            # Handle '==' comparison
            if "==" in flag_name:
                parts = flag_name.split("==")
                k = parts[0].strip()
                v = parts[1].strip().lower() in ("true", "1")
                res = state_flags.get(k) == v
            elif "!=" in flag_name:
                parts = flag_name.split("!=")
                k = parts[0].strip()
                v = parts[1].strip().lower() in ("true", "1")
                res = state_flags.get(k) != v
            else:
                res = bool(state_flags.get(flag_name, False))

            return not res if invert else res

        return True
    except Exception as e:
        logger.warning(f"Error evaluating condition '{condition_str}': {e}")
        return True


class MacroRunner:
    """Manages state-aware keybind triggering and composite sequence execution."""

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
        telemetry_adapter: Optional[BaseTelemetryAdapter] = None
    ) -> Dict[str, Any]:
        """
        Executes a spoken macro or action phrase against profile keybinds/sequences.
        Evaluates preconditions using telemetry state flags when available.
        """
        clean_phrase = phrase.lower().strip()
        state_flags = telemetry_adapter.get_state_flags() if telemetry_adapter else {}

        # 1. Check for Composite Sequence match
        sequences = profile.get("sequences", {})
        if clean_phrase in sequences:
            seq_def = sequences[clean_phrase]
            return self._run_sequence(clean_phrase, seq_def, profile, state_flags)

        # 2. Check for Atomic Keybind/Action match
        actions = profile.get("actions", {})
        keybinds = profile.get("keybinds", {})
        action_def = actions.get(clean_phrase) or keybinds.get(clean_phrase)

        if not action_def:
            # Check for partial matches or aliases
            for name, item in {**actions, **keybinds}.items():
                aliases = item.get("aliases", []) if isinstance(item, dict) else []
                if clean_phrase == name or clean_phrase in [a.lower() for a in aliases]:
                    action_def = item
                    break

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

    def _run_sequence(
        self,
        name: str,
        seq_def: Any,
        profile: Dict[str, Any],
        state_flags: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Runs a multi-step macro sequence with timing and abort safety."""
        # Precondition check on sequence itself
        if isinstance(seq_def, dict):
            condition = seq_def.get("condition")
            if condition and not evaluate_condition(condition, state_flags):
                skip_ack = seq_def.get("condition_skip_ack") or f"Sequence '{name}' skipped (condition not met)."
                return {"status": "condition_skipped", "action": name, "ack": skip_ack}
            steps = seq_def.get("steps", [])
            ack = seq_def.get("ack", f"Executing sequence {name}.")
        elif isinstance(seq_def, list):
            steps = seq_def
            ack = f"Executing sequence {name}."
        else:
            return {"status": "error", "message": "Invalid sequence format."}

        self._abort_event.clear()

        # Run steps synchronously or launch in background if multi-step
        def step_worker():
            for step in steps:
                if self._abort_event.is_set():
                    logger.info(f"Sequence '{name}' aborted by user.")
                    break

                action_type = step.get("action", "press")
                delay_ms = step.get("delay_ms", 100)

                if action_type == "press":
                    k = step.get("key")
                    mods = step.get("modifiers", [])
                    if k:
                        if mods:
                            os_controls.send_directinput_combo(mods, k)
                        else:
                            os_controls.send_directinput_key(k)
                elif action_type == "type":
                    txt = step.get("text", "")
                    if txt:
                        os_controls.send_directinput_string(txt)
                elif action_type == "click":
                    btn = step.get("button", "left")
                    os_controls.click_mouse(button=btn)

                time.sleep(delay_ms / 1000.0)

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
