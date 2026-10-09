"""
core/game_engine.py - Autonomous Gaming Automation Engine & Immersion Orchestrator
Encapsulates game profile state, fast-path voice macro dispatch, telemetry integration,
vision-based navigation, tool declaration fencing, and strict in-character immersion directives.
"""

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Set, Tuple, Callable

from core.logger import get_logger
from core.game_manager import GameManager, GAMES_PROFILES_PATH
from tools.game_tools import register_game_tools

logger = get_logger("GameEngine")


@dataclass
class GameInterceptionResult:
    """Outcome of evaluating an incoming voice prompt against game automation rules."""
    handled: bool = False
    action_type: str = ""           # "mode_toggle" | "fast_path_macro" | ""
    user_message: str = ""          # Chat bubble text
    tts_message: Optional[str] = None  # Spoken voice text
    requires_context_reset: bool = False
    skip_llm: bool = False
    details: Optional[Dict[str, Any]] = None


class GameEngine:
    """
    Subsystem engine orchestrating gaming profiles, telemetry, macros,
    and conversational immersion for Aether.
    """

    CORE_GAME_TOOL_NAMES: Set[str] = {
        "get_current_game_telemetry",
        "get_current_game_status",
        "trigger_game_action",
        "toggle_game_mode",
        "record_copilot_observation",
        "update_game_scratchpad",
        "pan_and_mark_map_location",
        "assist_game_navigation",
        "inspect_screen_context",
    }

    def __init__(
        self,
        engine=None,
        dispatcher=None,
        profiles_path: str = GAMES_PROFILES_PATH,
        on_notify: Optional[Callable[[str, Dict[str, Any]], None]] = None
    ):
        self.engine = engine
        self.dispatcher = dispatcher
        self.on_notify = on_notify
        self.manager = GameManager(profiles_path=profiles_path)
        self.manager.on_game_mode_changed = self._on_game_mode_changed

        if self.dispatcher:
            self.register_tools(self.dispatcher)

    # --- Backward-Compatibility Aliases ---

    @property
    def game_mgr(self) -> GameManager:
        """Alias for self.manager to support legacy callers."""
        return self.manager

    @property
    def data(self) -> Dict[str, Any]:
        """Provides direct access to underlying profiles data."""
        return self.manager.data

    # --- Subsystem Lifecycle ---

    def start(self, foreground_interval: float = 2.0):
        """Starts background watchers (foreground window poller, telemetry tailers)."""
        self.manager.start_foreground_watcher(interval=foreground_interval)
        self.manager._notify_mode_changed()
        logger.info("[GAME_ENGINE] Gaming subsystem initialized and watching foreground processes.")

    def stop(self):
        """Cleanly tears down background threads and watchers."""
        try:
            self.manager.close()
            logger.info("[GAME_ENGINE] Gaming subsystem stopped cleanly.")
        except Exception as e:
            logger.warning(f"[GAME_ENGINE] Error during shutdown: {e}")

    # --- Mode & State Queries ---

    @property
    def game_mode_enabled(self) -> bool:
        return self.manager.game_mode_enabled

    def is_game_mode_active(self) -> bool:
        return self.manager.is_game_mode_active()

    def set_game_mode(self, enabled: bool, user_explicit: bool = False) -> dict:
        return self.manager.set_game_mode(enabled, user_explicit=user_explicit)

    def handle_hotkey_toggle(self) -> Tuple[bool, str]:
        """Toggles game mode from global hotkey; returns (new_state, announcement_text)."""
        new_state = not self.manager.game_mode_enabled
        self.manager.set_game_mode(new_state, user_explicit=True)
        active_id = self.manager.data.get("active_profile", "")
        prof = self.manager.get_profile(active_id) if active_id else None
        game_title = prof.get("display_name", active_id) if prof else "Game"

        if new_state:
            msg = f"Game Mode active. Focused on {game_title}."
        else:
            msg = f"Switched to open conversation. {game_title} macros remain standing by." if active_id else "Switched to open conversation."
        return new_state, msg

    # --- Voice Pipeline Interception ---

    def intercept_voice_turn(self, raw_prompt: str, agent_name: str = "Aether") -> GameInterceptionResult:
        """
        Inspects incoming speech prior to LLM submission.
        1. Checks for voice commands toggling Game Mode.
        2. Evaluates fast-path DirectInput voice macros.
        """
        lower_prompt = (raw_prompt or "").strip().lower()
        clean_voice_cmd = re.sub(r'^[,\.\s]+|[,\.\s]+$', '', lower_prompt).strip()
        if clean_voice_cmd.startswith(agent_name.lower()):
            clean_voice_cmd = clean_voice_cmd[len(agent_name):].strip().lstrip(",. ")

        # 1. Mode Toggle: Disable Game Mode
        if (re.search(r'\b(disable|exit|turn\s+off|leave|deactivate|stop)\s+game\s+mode\b', clean_voice_cmd)
                or re.search(r'\bgame\s+mode\s+(off|disable)\b', clean_voice_cmd)):
            self.set_game_mode(False, user_explicit=True)
            active_id = self.manager.data.get("active_profile", "")
            prof = self.manager.get_profile(active_id) if active_id else None
            game_title = prof.get("display_name", active_id) if prof else "game"
            msg = f"Switched to open conversation. {game_title} macros remain standing by." if active_id else "Switched to open conversation."
            return GameInterceptionResult(
                handled=True,
                action_type="mode_toggle",
                user_message=msg,
                tts_message=msg,
                requires_context_reset=True,
                skip_llm=True
            )

        # 2. Mode Toggle: Enable Game Mode
        if (re.search(r'\b(enable|enter|turn\s+on|activate|start)\s+game\s+mode\b', clean_voice_cmd)
                or re.search(r'\bgame\s+mode\s+(on|enable)\b', clean_voice_cmd)):
            self.set_game_mode(True, user_explicit=True)
            active_id = self.manager.data.get("active_profile", "")
            prof = self.manager.get_profile(active_id) if active_id else None
            game_title = prof.get("display_name", active_id) if prof else "Game"
            msg = f"Game Mode active. Focused on {game_title}." if active_id else "Game Mode active."
            return GameInterceptionResult(
                handled=True,
                action_type="mode_toggle",
                user_message=msg,
                tts_message=msg,
                requires_context_reset=True,
                skip_llm=True
            )

        # 3. Fast-Path DirectInput Macro Dispatch
        active_game = self.manager.data.get("active_profile")
        if active_game:
            clean_macro = re.sub(r'^[,\.\s]+|[,\.\s]+$', '', lower_prompt).strip()
            if clean_macro.startswith(agent_name.lower()):
                clean_macro = clean_macro[len(agent_name):].strip().lstrip(",. ")

            prof = self.manager.get_profile(active_game) or {}
            profile_binds = prof.get("keybinds", {})
            if clean_macro in profile_binds:
                macro_res = self.manager.trigger_action(clean_macro)
                if macro_res.get("status") == "executed":
                    return GameInterceptionResult(
                        handled=True,
                        action_type="fast_path_macro",
                        user_message=f"⚡ [In-Game Macro] Executed: {macro_res.get('action')}",
                        tts_message=None,
                        requires_context_reset=False,
                        skip_llm=True,
                        details=macro_res
                    )

        return GameInterceptionResult(handled=False)

    async def trigger_action_async(self, phrase: str) -> dict:
        """Executes hardware scancodes in a background thread to prevent asyncio event loop stalls."""
        return await asyncio.to_thread(self.manager.trigger_action, phrase)

    async def intercept_voice_turn_async(self, raw_prompt: str, agent_name: str = "Aether") -> GameInterceptionResult:
        """Async-safe wrapper executing interception and macro dispatch off the main event loop thread."""
        return await asyncio.to_thread(self.intercept_voice_turn, raw_prompt, agent_name)

    # --- Immersion Prompt Generation ---

    def build_system_instruction(self, hotkey_display: str = "Ctrl+Shift+G") -> Optional[str]:
        """
        If Game Mode is active, returns the complete immersion system instruction
        including universe rules, out-of-universe deflection directives, and live telemetry context.
        Returns None if Game Mode is inactive.
        Handles unlisted, indie, or generic games gracefully with dynamic fallback.
        """
        if not self.is_game_mode_active():
            return None

        active_id = self.manager.data.get("active_profile", "")
        prof = self.manager.get_profile(active_id) if active_id else {}
        game_title = prof.get("display_name")

        if not game_title:
            # Fallback to executable process name or formatted ID
            pname = prof.get("process_name", "")
            if pname:
                game_title = os.path.splitext(pname)[0].replace("_", " ").title()
            else:
                game_title = active_id.replace("_", " ").title() if active_id else "Active Game"

        has_explicit_universe = bool(prof.get("universe_name") or prof.get("world_name"))
        if has_explicit_universe:
            universe_name = prof.get("universe_name") or prof.get("world_name")
            immersion_directive = (
                f"1. UNIVERSE IMMERSION: You are completely immersed in {universe_name}. Speak, think, and react solely in-character.\n"
                f"   Focus entirely on the player's immediate surroundings, quests, combat, exploration, inventory, and navigation in {game_title}."
            )
        else:
            universe_name = f"the game world of {game_title}"
            immersion_directive = (
                f"1. UNIVERSE IMMERSION & DYNAMIC GROUNDING: You are completely immersed in {universe_name}.\n"
                f"   Because explicit universe lore was not predefined, dynamically infer the universe, tone, setting, and mechanics\n"
                f"   from the active screen view, visible HUD elements, and player prompts. Speak, think, and react solely in-character as the player's dedicated co-pilot."
            )

        immersion_banner = (
            f"================================================================================\n"
            f"🚨 MAXIMUM OVERRIDE: IN-CHARACTER GAME MODE ENGAGED — {game_title.upper()} 🚨\n"
            f"================================================================================\n"
            f"You are operating in dedicated GAME MODE as the in-game co-pilot and companion in {universe_name} ({game_title}).\n"
            f"THIS INSTRUCTION STRICTLY OVERRIDES ALL DESKTOP ASSISTANT, CODING, AND REAL-WORLD CAPABILITIES.\n\n"
            f"STRICT IN-CHARACTER DIRECTIVES:\n"
            f"{immersion_directive}\n"
            f"2. ZERO TOLERANCE FOR OUT-OF-UNIVERSE DEVIATION:\n"
            f"   - You MUST NEVER break character to discuss out-of-game matters, real-world events, politics, science, personal tasks, Python code, software architecture, system bugs, or application development.\n"
            f"   - If the user asks about real-world topics or system bugs, stay in-character and deflect in-universe:\n"
            f"     'My focus is entirely on our journey in {game_title}. If you wish to discuss real-world matters or inspect code, please tell me to \"disable Game Mode\" (or press {hotkey_display}).'\n"
            f"3. IN-GAME ACTIONS:\n"
            f"   You can execute game actions via `trigger_game_action` and check telemetry via `get_current_game_telemetry` / `get_current_game_status`.\n"
            f"================================================================================\n"
        )
        game_context = self.manager.get_active_game_context()
        return f"{immersion_banner}\n\n{game_context}"

    # --- Tool Fencing & Registration ---

    def get_allowed_tool_names(self) -> Set[str]:
        """Returns allowed tool names for active game mode, including profile-specific extra tools."""
        tools = set(self.CORE_GAME_TOOL_NAMES)
        active_id = self.manager.data.get("active_profile", "")
        if active_id:
            prof = self.manager.get_profile(active_id) or {}
            extra_tools = prof.get("extra_tools", [])
            if isinstance(extra_tools, (list, set, tuple)):
                tools.update(extra_tools)
            if active_id == "elite_dangerous":
                tools.add("lookup_inara_market")
        return tools

    def filter_tools(self, declarations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filters a set of tool declarations to only allowed gaming tools."""
        allowed = self.get_allowed_tool_names()
        return [d for d in declarations if d.get("name") in allowed]

    def register_tools(self, dispatcher):
        """Registers all gaming tools on the given ToolDispatcher."""
        register_game_tools(dispatcher, self.manager, engine=self.engine)

    # --- Event Notification Hook ---

    def _on_game_mode_changed(self, enabled: bool, game_id: str, game_name: str, is_running: bool = False):
        if callable(self.on_notify):
            self.on_notify("game_mode_changed", {
                "enabled": enabled,
                "game_mode_enabled": enabled,
                "is_active": enabled,
                "is_running": is_running,
                "active_profile": game_id,
                "game_id": game_id,
                "display_name": game_name,
                "game_name": game_name
            })

