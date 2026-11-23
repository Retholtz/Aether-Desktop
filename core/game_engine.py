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
        "query_game_intel",
        "record_copilot_observation",
        "update_game_scratchpad",
        "get_game_scratchpad",
        "search_copilot_log",
        "pan_and_mark_map_location",
        "assist_game_navigation",
        "spot_and_guide_map_landmark",
        "search_and_route_destination",
        "route_to_map_bookmark",
        "remember_game_poi",
        "pan_game_map",
        "place_map_marker",
        "center_map_on_player",
        "inspect_screen_context",
        "calibrate_hud_bars",
        "click_game_ui_element",
        "complete_game_ui_task",
        "run_game_key_sequence",
        "save_game_macro",
        "list_game_macros",
        "delete_game_macro",
        "launch_game_companion_app",
        "launch_application",
        "navigate_browser",
        "move_window_to_monitor",
        "focus_window",
        "tile_windows",
        "snap_window",
        "close_window",
        "close_application",
        "delegate_moderate_task",
        "delegate_heavy_task",
        "paste_to_document",
        "export_research_dossier",
        "get_steam_friends_activity",
        "get_steam_friend_games",
        "get_steam_owned_games",
        "get_steam_game_achievements",
        "get_steam_patch_notes",
        "trigger_instant_replay",
        "get_current_playing_media",
        "control_media_playback",
        "play_music",
        "get_hardware_telemetry",
        "roll_dice",
        "modify_character_vitals",
        "advance_combat_turn",
        "set_character_condition",
        "list_campaign_documents",
        "read_campaign_document",
        "upsert_ttrpg_character",
        "update_campaign_details",
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
        if self.engine is not None:
            self.manager.vision_client = getattr(self.engine, "genai_client", None) or getattr(self.engine, "client", None)

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

        # --- Fast-Path: Universal Media Controls ---
        # 1. Track Info Query
        if re.search(r'\b(what(\'s|\s+is)\s+(playing|this\s+song|this\s+track)|what\s+song(\s+is\s+this)?|current\s+song)\b', clean_voice_cmd):
            return GameInterceptionResult(
                handled=True,
                action_type="media_query",
                user_message="🎵 Querying current track info...",
                tts_message=None,
                requires_context_reset=False,
                skip_llm=True
            )

        # 2. Pause (anchor standalone 'pause' so sentences like 'pause the game' are not hijacked)
        if (
            re.search(r'\b(pause\s+(the\s+)?(music|track|song|media|playback))\b', clean_voice_cmd)
            or re.fullmatch(r'(?:please\s+)?pause(?:\s+please)?', clean_voice_cmd)
        ):
            return GameInterceptionResult(
                handled=True,
                action_type="media_pause",
                user_message="⏸️ [Media] Paused playback.",
                tts_message="Paused.",
                requires_context_reset=False,
                skip_llm=True
            )

        # 3. Resume / Play
        if re.search(r'\b(resume\s+(the\s+)?(music|track|song|media|playback)|play\s+music|unpause)\b', clean_voice_cmd):
            return GameInterceptionResult(
                handled=True,
                action_type="media_play",
                user_message="▶️ [Media] Resumed playback.",
                tts_message="Playing.",
                requires_context_reset=False,
                skip_llm=True
            )

        # 4. Next / Skip (anchor standalone 'skip' so sentences like "let's skip this quest" are not hijacked)
        if (
            re.search(r'\b(next\s+(song|track)|skip\s+(this\s+)?(song|track))\b', clean_voice_cmd)
            or re.fullmatch(r'(?:please\s+)?skip(?:\s+please)?', clean_voice_cmd)
        ):
            return GameInterceptionResult(
                handled=True,
                action_type="media_next",
                user_message="⏭️ [Media] Skipped track.",
                tts_message="Next track.",
                requires_context_reset=False,
                skip_llm=True
            )

        # 5. Previous
        if re.search(r'\b(previous\s+(song|track)|prev\s+(song|track)|go\s+back\s+a\s+track)\b', clean_voice_cmd):
            return GameInterceptionResult(
                handled=True,
                action_type="media_previous",
                user_message="⏮️ [Media] Previous track.",
                tts_message="Previous track.",
                requires_context_reset=False,
                skip_llm=True
            )

        # --- Fast-Path: Hardware Thermals & Framerate ---
        if re.search(
            r'\b('
            r'(how(\'s|\s+is|\s+are)|what(\'s|\s+is|\s+are))\s+(a\s+|an\s+|the\s+|my\s+|our\s+)?(temps?|thermals?|fps|framerate|frames?(\s+per\s+second)?|hardware|gpu|cpu)'
            r'|(the\s+)?(cpu|gpu|hardware|system)\s+(thermals?|temps?|temperatures?|sitting at|status|performance|load)'
            r'|check\s+(the\s+)?(fps|framerate|temps?|thermals?|hardware|gpu|cpu)'
            r'|hardware\s+status|fps\s+status'
            r')\b',
            clean_voice_cmd
        ):
            return GameInterceptionResult(
                handled=True,
                action_type="hardware_telemetry",
                user_message="⚡ Querying hardware thermals and performance...",
                tts_message=None,
                requires_context_reset=False,
                skip_llm=True
            )

        # --- Fast-Path: Instant Replay Capture ("Aether, clip that") ---
        if re.search(r'\b(clip\s+that|save\s+clip|capture\s+clip|record\s+that|save\s+replay|clip\s+this)\b', clean_voice_cmd):
            return GameInterceptionResult(
                handled=True,
                action_type="replay_capture",
                user_message="🎬 [Replay Capture] Triggering instant replay buffer...",
                tts_message="Captured.",
                requires_context_reset=False,
                skip_llm=True
            )

        # --- Fast-Path: Cryptographic Dice Rolls ("Aether, roll a d20 plus 5") ---
        dice_match = re.search(r'\broll\s+(?:a\s+|an\s+)?(\d*d\d+(?:\s*(?:[\+\-]|plus|minus)\s*\d+)?)\b', clean_voice_cmd)
        if dice_match:
            raw_formula = dice_match.group(1)
            formula = re.sub(r'\bplus\b', '+', raw_formula)
            formula = re.sub(r'\bminus\b', '-', formula).replace(" ", "")
            adv_mode = "advantage" if "advantage" in clean_voice_cmd else ("disadvantage" if "disadvantage" in clean_voice_cmd else "normal")
            return GameInterceptionResult(
                handled=True,
                action_type="ttrpg_roll_dice",
                user_message=f"🎲 Rolling {formula} ({adv_mode})...",
                tts_message=None,
                requires_context_reset=False,
                skip_llm=True,
                details={"formula": formula, "roll_type": adv_mode}
            )

        # --- Fast-Path: Initiative Turn Advancement ("Next turn" / "Advance initiative") ---
        # Gate ambiguous "next turn" / "who's up next" on active TTRPG combat when ttrpg_service is attached
        explicit_init_cmd = bool(re.search(r'\badvance\s+(initiative|combat)\b', clean_voice_cmd))
        turn_advance_cmd = explicit_init_cmd or bool(re.search(r'\b(next\s+turn|who(\'s|\s+is)\s+up\s+next)\b', clean_voice_cmd))
        if turn_advance_cmd:
            ttrpg_svc = getattr(self.engine, "ttrpg_service", None) if self.engine is not None else None
            combat_active = True
            if ttrpg_svc is not None:
                active_state = getattr(ttrpg_svc, "active_state", None)
                if active_state is None and hasattr(ttrpg_svc, "get_active_state"):
                    try:
                        active_state = ttrpg_svc.get_active_state()
                    except Exception:
                        active_state = None
                active_state = active_state or {}
                combat_active = bool(
                    (active_state.get("combat") or {}).get("active", False)
                    or active_state.get("initiative_order")
                )
            if explicit_init_cmd or combat_active:
                return GameInterceptionResult(
                    handled=True,
                    action_type="ttrpg_advance_turn",
                    user_message="⚔️ Advancing initiative turn...",
                    tts_message=None,
                    requires_context_reset=False,
                    skip_llm=True
                )

        # 1. Mode Toggle: Disable Game Mode (ignore questions like "why did you exit game mode" or "re-enable")
        if (
            not re.search(r'\b(why\s+did\s+(you|it|we)|re-?enable)\b', clean_voice_cmd)
            and (
                re.search(r'\b(disable|exit|turn\s+off|leave|deactivate|stop)\s+game\s+mode\b', clean_voice_cmd)
                or re.search(r'\bgame\s+mode\s+(off|disable)\b', clean_voice_cmd)
            )
        ):
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

        # 3. Guidance Level Voice Setting Command
        # Matches e.g. "set guidance level to walkthrough", "change guidance to companion", "guidance level tactical", "switch guidance to walkthrough"
        guidance_match = re.search(r'\b(?:set|switch|change)?\s*(?:the\s*)?guidance(?:\s*level)?\s*(?:to\s*)?(companion|tactical|walkthrough)\b', clean_voice_cmd)
        if guidance_match:
            new_level = guidance_match.group(1).lower().strip()
            active_id = self.manager.data.get("active_profile", "")
            if active_id:
                self.manager.set_guidance_level(active_id, new_level)
                prof = self.manager.get_profile(active_id) or {}
                game_title = prof.get("display_name", active_id)
                level_display = new_level.capitalize()
                msg = f"Guidance level set to {level_display} for {game_title}."
                return GameInterceptionResult(
                    handled=True,
                    action_type="guidance_level_change",
                    user_message=msg,
                    tts_message=msg,
                    requires_context_reset=True,
                    skip_llm=True,
                    details={"game_id": active_id, "guidance_level": new_level}
                )

        # 4. Map Navigation Mode Voice Setting Command
        # Matches e.g. "set map navigation to controller", "change map navigation to spotter", "switch map navigation mode to search bar", "map navigation mouse"
        nav_match = re.search(
            r'\b(?:set|switch|change)?\s*(?:the\s*)?(?:map\s*)?navigation(?:\s*mode)?\s*(?:to\s*)?'
            r'(controller|gamepad|spotter|co[-_ ]?pilot(?:\s*spotter)?|search\s*bar|mouse(?:\s*(?:and|&)?\s*keyboard)?)\b',
            clean_voice_cmd
        )
        if nav_match:
            raw_mode = nav_match.group(1).lower().strip()
            if "spotter" in raw_mode or "pilot" in raw_mode:
                canonical_mode = "co_pilot_spotter"
                display_mode = "Co-Pilot Guided Spotter (Voice & Vision)"
            elif "search" in raw_mode:
                canonical_mode = "search_bar"
                display_mode = "Search Bar & Binds"
            elif "mouse" in raw_mode:
                canonical_mode = "mouse_keyboard"
                display_mode = "Mouse & Keyboard"
            else:
                canonical_mode = "controller"
                display_mode = "Virtual Controller (ViGEmBus)"

            active_id = self.manager.data.get("active_profile", "")
            if active_id:
                self.manager.set_navigation_mode(active_id, canonical_mode)
                prof = self.manager.get_profile(active_id) or {}
                game_title = prof.get("display_name", active_id)
                msg = f"Map navigation mode set to {display_mode} for {game_title}."
                return GameInterceptionResult(
                    handled=True,
                    action_type="navigation_mode_change",
                    user_message=msg,
                    tts_message=msg,
                    requires_context_reset=True,
                    skip_llm=True,
                    details={"game_id": active_id, "navigation_mode": canonical_mode}
                )

        # 5. Fast-Path DirectInput Macro & Sequence Dispatch
        active_game = self.manager.data.get("active_profile")
        if active_game:
            # Anti-cheat safety guardrail: never inject synthetic keystrokes in spotter_only mode
            if hasattr(self.manager, "get_input_mode") and self.manager.get_input_mode(active_game) == "spotter_only":
                return GameInterceptionResult(handled=False)
            clean_macro = re.sub(r'^[,\.\s]+|[,\.\s]+$', '', lower_prompt).strip()
            if clean_macro.startswith(agent_name.lower()):
                clean_macro = clean_macro[len(agent_name):].strip().lstrip(",. ")

            macro_res = self.manager.trigger_action(clean_macro)
            status = macro_res.get("status")
            if status == "executed":
                ack_msg = macro_res.get("ack") or f"Executed: {macro_res.get('action')}"
                return GameInterceptionResult(
                    handled=True,
                    action_type="fast_path_macro",
                    user_message=f"⚡ [In-Game Macro] {ack_msg}",
                    tts_message=None,
                    requires_context_reset=False,
                    skip_llm=True,
                    details=macro_res
                )
            elif status == "condition_skipped":
                skip_msg = macro_res.get("ack") or f"Skipped: {macro_res.get('action')}"
                return GameInterceptionResult(
                    handled=True,
                    action_type="fast_path_macro",
                    user_message=f"⚡ [In-Game Macro] {skip_msg}",
                    tts_message=skip_msg,
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

        vibe_block = self.manager.build_copilot_personality_directive(prof)
        immersion_banner = (
            f"================================================================================\n"
            f"IN-CHARACTER GAME MODE ENGAGED — {game_title.upper()} ({universe_name})\n"
            f"================================================================================\n"
            f"You are the player's live in-game co-pilot and companion inside {universe_name} ({game_title}).\n"
            f"Leave desktop assistant habits behind — talk like a real companion riding shotgun.\n\n"
            f"{vibe_block}\n\n"
            f"GAME MODE DIRECTIVES:\n"
            f"{immersion_directive}\n"
            f"2. STAY IN-UNIVERSE — ZERO TOLERANCE FOR OUT-OF-UNIVERSE DEVIATION (GAMING RIG TELEMETRY ALLOWED):\n"
            f"   - Stay in-character and don't drift into unrelated real-world topics, politics, coding, or software troubleshooting.\n"
            f"   - GAMING RIG & PERFORMANCE EXCEPTION: Rig thermals (CPU/GPU temps), system load, VRAM, and framerate (FPS) ARE allowed in Game Mode. When asked, call `get_hardware_telemetry` immediately and report the metrics directly.\n"
            f"   - For unrelated real-world topics or code questions, deflect naturally in-character and let the player know they can say \"disable Game Mode\" (or press {hotkey_display}) to switch back.\n"
            f"3. IN-GAME ACTIONS, LIVE INTEL & TWO-TIER MEMORY:\n"
            f"   - Execute bound actions via `trigger_game_action` and check live state via `get_current_game_telemetry` / `get_current_game_status`.\n"
            f"   - LIVE DATABASE QUERIES vs. LIVE NEWS/LORE: Use `query_game_intel(query=..., category=...)` for structured live database lookups (commodities, market buy/sell stations, gear/outfitting, vendors, station/NPC services, routes). NEVER deflect by telling the player to 'look it up on a website or wiki' — YOU look it up and report exact results. For general game news, lore, story, or open discussion, answer directly on the Live WebSocket (using your built-in Google Search if current info is needed) with full natural conversational depth — do NOT artificially truncate to 2 lines.\n"
            f"   - COURSE PLOTTING, BOOKMARKS & POI RESOLUTION: When asked to plot a course to a numbered bookmark (e.g. 'Bookmark 2' or 'System Bookmark 3'), invoke `route_to_map_bookmark(bookmark_number=..., category=...)` (leave `category` blank to use the default/favorites category). When asked to navigate to a named NPC, vendor, character, mobile base, or POI (not a top-level map system/region), FIRST resolve its parent routable system/region (check live telemetry Tracked POIs / remembered POIs, then `query_game_intel`, or your game-scoped Google Search), and THEN call `search_and_route_destination` with the resolved routable system/region — NEVER paste a character's name into a top-level map search bar. Use `remember_game_poi` whenever the player asks you to save or update the location of a POI, home base, or mobile asset.\n"
            f"   - IN-GAME MENUS, STATEFUL CURSORS & UI BUTTONS: NEVER call `close_window` for in-game screens, maps, or menus — close them with `trigger_game_action` (e.g. `'close menu'`, `'escape'`, or the map toggle). For stateful in-game menus, station/vendor services, panels, or icons where the highlight cursor could start in different positions (e.g. Refuel, Repair, Request Docking, equipping gear), prefer `complete_game_ui_task(goal=...)` — it sees where the highlight cursor is right now, adapts step-by-step, and visually confirms completion. For a single obvious static button, you can also inspect with `inspect_screen_context` and use `click_game_ui_element`. NEVER claim an in-game action succeeded unless a tool actually executed and confirmed it in this turn.\n"
            f"   - AD-HOC KEY SEQUENCES & ON-THE-FLY MACRO CREATION: You HAVE full capability to both execute multi-key sequences immediately (`run_game_key_sequence`) AND save reusable custom macros (`save_game_macro`) — NEVER claim system limitations or say you cannot create macros. Both tools accept raw keyboard `press` steps (e.g. `{{\"action\": \"press\", \"key\": \"w\", \"repeat\": 3, \"interval_ms\": 150}}` followed by `{{\"action\": \"press\", \"key\": \"space\"}}`), synced keybinds (`trigger_bind`), visual UI clicks (`vision_click`), `type`, `paste`, and `wait` — even when no pre-existing named keybind exists for that action. When creating a macro involving unfamiliar on-screen menus/wheels, call `inspect_screen_context` first so you see exact labels, then call `save_game_macro` and concisely confirm the saved macro name and steps.\n"
            f"   - Live Working Memory: Your system prompt maintains active immediate objectives, location, and sub-tasks across 10-minute session rotations.\n"
            f"   - Deep Storage & Intel: Use `get_game_scratchpad` to view personal notes/quests, and `search_copilot_log` to query historical landmarks/intel.\n"
            f"   - Record discoveries with `record_copilot_observation` and update player notes with `update_game_scratchpad`.\n"
            f"4. AUXILIARY CONSOLES & COMPANION FLIGHT-DECK TOOLS:\n"
            f"   - You have direct access to auxiliary displays and companion utilities: `launch_application`, `navigate_browser`, `move_window_to_monitor`, `focus_window`, `close_window`.\n"
            f"   - Treat companion apps, tactical maps, database terminals, and market consoles as in-universe auxiliary flight-deck consoles, cockpit MFDs, or tactical datalinks.\n"
            f"   - If the player asks to open or move a companion tool or database to a secondary monitor (e.g., 'Open companion map in Chrome on Monitor 2'), do so immediately using `navigate_browser` or `launch_application` with the target `monitor` parameter, while keeping your voice and persona fully in-character.\n"
            f"   - Game Shield: You are strictly prevented from closing the active game itself, but you may manage auxiliary windows and companion consoles.\n"
            f"================================================================================\n"
        )
        game_context = self.manager.get_active_game_context(include_personality=False)
        return f"{immersion_banner}\n\n{game_context}"

    # --- Tool Fencing & Registration ---

    def get_allowed_tool_names(self) -> Set[str]:
        """Returns allowed tool names for active game mode, including profile-specific and trusted extension tools."""
        tools = set(self.CORE_GAME_TOOL_NAMES)
        tools.update(getattr(self, "_extension_tool_names", set()))
        active_id = self.manager.data.get("active_profile", "")
        if active_id:
            prof = self.manager.get_profile(active_id) or {}
            extra_tools = prof.get("extra_tools", [])
            if isinstance(extra_tools, (list, set, tuple)):
                tools.update(extra_tools)
        return tools

    def filter_tools(self, declarations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filters a set of tool declarations to only allowed gaming tools."""
        allowed = self.get_allowed_tool_names()
        return [d for d in declarations if d.get("name") in allowed]

    def register_tools(self, dispatcher):
        """Registers all gaming tools (and any explicitly trusted Tier 2 game extension tools)."""
        register_game_tools(dispatcher, self.manager, engine=self.engine)
        self._extension_tool_names = set()
        try:
            from core.game_extensions import list_game_extensions, load_trusted_extension
            for info in list_game_extensions():
                if not info.get("trusted"):
                    continue
                ext = load_trusted_extension(info["module_id"])
                if ext is not None:
                    added = ext.register_tools(dispatcher, self.manager, engine=self.engine)
                    if isinstance(added, (list, set, tuple)):
                        self._extension_tool_names.update(str(t) for t in added if t)
        except Exception as e:
            logger.debug("[GAME_ENGINE] Extension tool registration skipped: %s", e)

    # --- Event Notification Hook ---

    def _on_game_mode_changed(self, enabled: bool, game_id: str, game_name: str, is_running: bool = False):
        if callable(self.on_notify):
            prof = self.manager.get_profile(game_id) if game_id else None
            fps_mon = bool(prof.get("fps_monitoring", True)) if prof else True
            self.on_notify("game_mode_changed", {
                "enabled": enabled,
                "game_mode_enabled": enabled,
                "is_active": enabled,
                "is_running": is_running,
                "active_profile": game_id,
                "game_id": game_id,
                "display_name": game_name,
                "game_name": game_name,
                "fps_monitoring": fps_mon
            })

