"""
Aether Desktop - Universal Vision-Action Game Agent & HUD Discovery Engine
Game-agnostic closed-loop observation engine that dynamically reads HUD legends,
evaluates viewports, executes physical primitives (pan, zoom, click), and verifies goals.
"""

import io
import re
import time
import json
from typing import Optional, Tuple, Dict, Any
import mss
from PIL import Image, ImageGrab

from core.screen_stream import ensure_thread_desktop
from tools.os_controls import (
    pulse_key,
    move_cursor_to_point,
    click_mouse,
    drag_viewport,
    zoom_viewport,
    send_directinput_key,
    move_mouse_absolute,
    click_mouse_button,
    drag_mouse_relative,
    send_gamepad_button
)


class UniversalGameNavigator:
    """
    Game-agnostic agent that autonomously inspects UI legends,
    evaluates viewports, executes physical primitives, and verifies outcomes.
    """

    def __init__(
        self,
        client=None,
        model_endpoint: str = "gemini-4-hydrogen",
        target_process: str = "",
        map_controls: Optional[Dict[str, Any]] = None,
        game_name: str = "",
        keybinds: Optional[Dict[str, Any]] = None,
        telemetry_adapter=None,
    ):
        from core.model_discovery import resolve_model_profile
        self.client = client
        self.model_endpoint = resolve_model_profile(model_endpoint, default_model="gemini-4-hydrogen")[0]
        self.target_process = target_process
        self.map_controls = map_controls or {}
        self.game_name = (game_name or "").strip()
        self.keybinds = keybinds or {}
        self.telemetry_adapter = telemetry_adapter
        self._last_capture_origin: Tuple[int, int] = (0, 0)

    def _resolve_target_monitor(self, sct) -> Dict[str, int]:
        """
        Resolves the monitor dictionary containing the active game window (if `target_process`
        is open on a secondary monitor), falling back cleanly to the primary monitor.
        """
        monitors = getattr(sct, "monitors", None) or []
        if len(monitors) <= 1:
            return monitors[0] if monitors else {"left": 0, "top": 0, "width": 1920, "height": 1080}

        default_mon = monitors[1]
        proc = (self.target_process or "").strip()
        if not proc:
            return default_mon

        try:
            import win32gui
            from tools.os_controls import find_window_hwnd

            hwnd = find_window_hwnd(proc)
            if hwnd and win32gui.IsWindow(hwnd):
                l, t, r, b = win32gui.GetWindowRect(hwnd)
                if r > l and b > t:
                    cx = (l + r) // 2
                    cy = (t + b) // 2
                    for mon in monitors[1:]:
                        ml, mt = mon["left"], mon["top"]
                        if ml <= cx < ml + mon["width"] and mt <= cy < mt + mon["height"]:
                            return mon
        except Exception:
            pass
        return default_mon

    def _norm_to_screen_px(self, x_norm: float, y_norm: float, width: int, height: int) -> Tuple[int, int]:
        """Converts normalized 0-1000 viewport coordinates to absolute desktop physical coordinates."""
        ox, oy = self._last_capture_origin
        px = ox + int((float(x_norm) / 1000.0) * width)
        py = oy + int((float(y_norm) / 1000.0) * height)
        return px, py

    def _build_low_latency_json_config(self, fast_mode: bool = False):
        """Returns a low-latency JSON GenerateContentConfig (temperature=0.0, minimal/zero thinking)."""
        try:
            from google.genai import types

            cfg_kwargs: Dict[str, Any] = {
                "response_mime_type": "application/json",
                "temperature": 0.0,
            }
            if hasattr(types, "ThinkingConfig"):
                try:
                    if fast_mode:
                        cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
                    else:
                        cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="low")
                except Exception:
                    try:
                        cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="low")
                    except Exception:
                        pass
            return types.GenerateContentConfig(**cfg_kwargs)
        except Exception:
            return {"response_mime_type": "application/json", "temperature": 0.0}

    def _call_vision_json(self, img_bytes: bytes, prompt: str, fast_mode: bool = False) -> Dict[str, Any]:
        """Executes a low-latency structured-JSON multimodal call and parses the response."""
        try:
            from google.genai import types
            part = types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
            contents = [part, prompt]
        except Exception:
            contents = [
                {
                    "parts": [
                        {"inline_data": {"mime_type": "image/jpeg", "data": img_bytes}},
                        {"text": prompt},
                    ]
                }
            ]

        res = self.client.models.generate_content(
            model=self.model_endpoint,
            contents=contents,
            config=self._build_low_latency_json_config(fast_mode=fast_mode),
        )
        raw_text = res.text.strip() if res and hasattr(res, "text") and res.text else ""
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
            raw_text = re.sub(r"\s*```$", "", raw_text)
        return json.loads(raw_text) if raw_text else {}

    def _fetch_landmark_web_context(self, landmark_description: str) -> str:
        """
        Optionally fetches a concise 1-2 sentence regional bearing hint via Google Search
        so fog-of-war / unmarked landmarks (e.g. 'Legendary Bear') can be dead-reckoned accurately.
        """
        if not self.client or not self.game_name or not landmark_description:
            return ""
        try:
            from google.genai import types

            cfg_kwargs: Dict[str, Any] = {
                "tools": [types.Tool(google_search=types.GoogleSearch())],
                "temperature": 0.0,
            }
            if hasattr(types, "ThinkingConfig"):
                try:
                    cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="low")
                except Exception:
                    pass
            prompt = (
                f'In the video game "{self.game_name}", where on the world map is "{landmark_description}" located? '
                f"Reply in 1-2 short sentences naming the region/province, nearby major landmarks, and cardinal direction."
            )
            res = self.client.models.generate_content(
                model=self.model_endpoint,
                contents=prompt,
                config=types.GenerateContentConfig(**cfg_kwargs),
            )
            return (res.text or "").strip() if res and hasattr(res, "text") else ""
        except Exception:
            return ""

    def capture_viewport(self, max_dim: int = 1280) -> tuple[bytes, int, int]:
        """
        Captures the monitor containing the active game window (multi-monitor aware),
        downscales the JPEG to `<= max_dim` for low-latency vision grounding, and returns
        `(jpeg_bytes, viewport_width, viewport_height)` while recording `_last_capture_origin`.
        """
        ensure_thread_desktop()
        try:
            with mss.mss() as sct:
                monitor = self._resolve_target_monitor(sct)
                shot = sct.grab(monitor)
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                orig_w, orig_h = monitor["width"], monitor["height"]
                self._last_capture_origin = (int(monitor.get("left", 0)), int(monitor.get("top", 0)))
                if max_dim and max(img.size) > max_dim:
                    img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=80)
                return buf.getvalue(), orig_w, orig_h
        except Exception:
            try:
                img = ImageGrab.grab()
                w, h = img.size
                self._last_capture_origin = (0, 0)
                if max_dim and max(img.size) > max_dim:
                    img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=80)
                return buf.getvalue(), w, h
            except Exception:
                self._last_capture_origin = (0, 0)
                return b"", 1920, 1080

    def discover_hud_controls(self, img_bytes: bytes) -> Dict[str, Any]:
        """
        Reads visible UI hints, button prompts, and legends on the screen.
        Determines how to center, pan, zoom, and place waypoints dynamically.
        """
        prompt = """
        Examine this active game screen and read all visible UI prompts, legends, or footer tooltips.
        Identify the controls for:
        1. "waypoint": The mouse click or key used to place a pin or destination (e.g., "right_click", "left_click", or a key name).
        2. "pan": The mouse button used to pan/drag the map canvas (e.g. "right", "left", or "middle").
        3. "center_player": The key used to snap back to the player character or origin, if visible.
        4. "zoom": The method used to zoom ("mouse_wheel" or specific buttons).
        5. "close_map": Key or button to exit.

        Return strictly a JSON object:
        {
            "waypoint_button": "right" or "left" or null,
            "pan_button": "right" or "left" or null,
            "center_player_key": "r" or null,
            "zoom_method": "mouse_wheel",
            "detected_prompts": ["list of text prompts seen"]
        }
        """
        if not self.client:
            return {"waypoint_button": "right", "pan_button": "right", "center_player_key": None, "zoom_method": "mouse_wheel"}

        try:
            parsed = self._call_vision_json(img_bytes, prompt)
            if "pan_button" not in parsed or not parsed["pan_button"]:
                parsed["pan_button"] = "right"
            return parsed
        except Exception as e:
            print(f"[WARN] [NAV_DISCOVERY] HUD read error: {e}")
            return {"waypoint_button": "right", "pan_button": "right", "center_player_key": None, "zoom_method": "mouse_wheel"}

    def evaluate_viewport_step(
        self,
        img_bytes: bytes,
        target_description: str,
        web_context: str = ""
    ) -> Dict[str, Any]:
        """
        Observes the screen and reasons about the next best physical action.
        Supports dead-reckoning projection into fog-of-war / uncharted regions.
        """
        prompt = f"""
        You are an autonomous game copilot navigating an in-game interface.
        Goal: Locate and place a marker on "{target_description}".
        
        Additional Background/Intel:
        {web_context or "None provided."}

        Look at the current screen capture:
        1. If the target landmark/POI is visible, return its normalized coordinates (0-1000) and action "CLICK_TARGET".
        2. If the target landmark is inside an UNCHARTED or FOG-OF-WAR area where the specific icon is hidden:
           - Look for the parent regional label (e.g. province name), border, or nearest visible landmark.
           - Estimate the projected point inside that territory (dead-reckoning).
           - Return action "CLICK_TARGET" with the estimated coordinates and note "DEAD_RECKONING" in analysis.
        3. If the viewport is zoomed in too far to see the territory, recommend action "ZOOM_OUT".
        4. If you see the general territory or a regional clue in a specific direction, recommend action "PAN" with direction (north, south, east, west) and distance.
        5. If you have arrived at the centered target region, recommend action "ZOOM_IN" to get precision.
        6. If a marker was just placed, evaluate whether it accurately landed on or near the target. If accurate, return action "VERIFIED_DONE".

        Return ONLY a JSON object:
        {{
            "analysis": "Brief 1-sentence assessment of current view (note if dead-reckoning was used)",
            "action": "CLICK_TARGET" | "ZOOM_OUT" | "ZOOM_IN" | "PAN" | "CENTER_ORIGIN" | "VERIFIED_DONE",
            "point": [y_normalized, x_normalized] or null,
            "pan_direction": "north" | "south" | "east" | "west" | null,
            "magnitude": integer (e.g. 3-6 for zoom notches, 200-400 for pan pixels)
        }}
        """
        if not self.client:
            return {"action": "VERIFIED_DONE"}

        try:
            return self._call_vision_json(img_bytes, prompt)
        except Exception as e:
            print(f"[ERROR] [NAV_EVAL] Vision evaluation failed: {e}")
            return {"action": "VERIFIED_DONE"}

    def run_vision_navigation_loop(
        self,
        target_description: str,
        web_context: str = "",
        max_iterations: int = 6
    ) -> Dict[str, Any]:
        """
        Adaptive observe-reason-act-verify loop.
        Learns UI controls on the fly and adjusts until the goal is verified.
        """
        print(f"[INFO] [NAV_LOOP] Starting autonomous vision navigation for: '{target_description}'")

        # 0. Enrich unmarked landmarks with a quick regional web hint if none provided
        effective_web_context = (web_context or "").strip()
        if not effective_web_context and self.game_name:
            effective_web_context = self._fetch_landmark_web_context(target_description)

        # 1. Capture initial frame and discover HUD controls
        frame_bytes, width, height = self.capture_viewport()
        hud_info = self.discover_hud_controls(frame_bytes)
        print(f"[INFO] [NAV_LOOP] Discovered HUD Controls: {hud_info}")

        ctrl = getattr(self, "map_controls", {}) or {}
        waypoint_cfg = ctrl.get("marker_method")
        if waypoint_cfg == "left_click":
            waypoint_btn = "left"
        elif waypoint_cfg == "right_click":
            waypoint_btn = "right"
        else:
            waypoint_btn = hud_info.get("waypoint_button") or "right"

        pan_cfg = ctrl.get("pan_method")
        if pan_cfg == "left_drag":
            pan_btn = "left"
        elif pan_cfg == "right_drag":
            pan_btn = "right"
        else:
            pan_btn = hud_info.get("pan_button") or "left"

        center_key = ctrl.get("center_player_key") or hud_info.get("center_player_key")

        # Anchor origin if a key was spotted
        if center_key:
            pulse_key(center_key)
            time.sleep(0.3)

        # 2. Iterative Exploration Loop
        for iteration in range(1, max_iterations + 1):
            frame_bytes, width, height = self.capture_viewport()
            decision = self.evaluate_viewport_step(frame_bytes, target_description, effective_web_context)
            action = decision.get("action")
            print(f"[LOOP #{iteration}] Assessment: {decision.get('analysis')} | Action: {action}")

            if action == "VERIFIED_DONE":
                return {"status": "success", "iterations": iteration, "target": target_description}

            elif action == "ZOOM_OUT":
                notches = decision.get("magnitude", 5)
                zoom_viewport(-abs(notches))
                time.sleep(0.4)

            elif action == "ZOOM_IN":
                notches = decision.get("magnitude", 4)
                zoom_viewport(abs(notches))
                time.sleep(0.4)

            elif action == "PAN":
                direction = decision.get("pan_direction", "north")
                dist = decision.get("magnitude", 300)
                if ctrl.get("preferred_input") == "gamepad":
                    from tools.os_controls import send_gamepad_stick
                    stick_map = {
                        "north": (0.0, 1.0),
                        "south": (0.0, -1.0),
                        "east": (1.0, 0.0),
                        "west": (-1.0, 0.0),
                    }
                    sx, sy = stick_map.get(direction.lower(), (0.0, 0.0))
                    if sx != 0.0 or sy != 0.0:
                        send_gamepad_stick("right", sx, sy, duration_sec=0.35)
                        time.sleep(0.4)
                    else:
                        drag_viewport(0, 0, button=pan_btn)
                else:
                    delta_map = {
                        "north": (0, dist),
                        "south": (0, -dist),
                        "east": (-dist, 0),
                        "west": (dist, 0)
                    }
                    dx, dy = delta_map.get(direction.lower(), (0, 0))
                    drag_viewport(dx, dy, button=pan_btn)
                    time.sleep(0.4)

            elif action == "CENTER_ORIGIN":
                if center_key:
                    pulse_key(center_key)
                time.sleep(0.3)

            elif action == "CLICK_TARGET":
                pt = decision.get("point")
                if pt:
                    y_norm, x_norm = pt
                    target_px, target_py = self._norm_to_screen_px(x_norm, y_norm, width, height)
                    move_cursor_to_point(target_px, target_py, screen_width=width, screen_height=height)
                    time.sleep(0.1)
                    click_mouse(waypoint_btn)
                    print(f"[INFO] [NAV_LOOP] Placed waypoint at ({target_px}, {target_py}) using {waypoint_btn} button.")
                    time.sleep(0.4)  # UI settle time before next verification pass

        return {"status": "partial", "message": "Max iterations reached without verified completion."}

    def spot_and_guide_map_location(self, landmark_description: str) -> Dict[str, Any]:
        """
        Co-Pilot Spotter mode: Observes the in-game world map via computer vision,
        identifies target landmark position or regional bearings, and returns actionable
        spoken guidance for the player without artificial cursor manipulation.
        """
        img_bytes, width, height = self.capture_viewport()
        is_map = self.is_map_screen(img_bytes)
        if not is_map:
            return {
                "status": "map_not_open",
                "found": False,
                "spoken_guidance": f"Open your world map and let me know, and I'll spot {landmark_description} for you."
            }

        if not self.client:
            return {
                "status": "no_vision_client",
                "found": False,
                "spoken_guidance": f"I see your map, but vision analysis is unavailable. Look for {landmark_description} on your regional markers."
            }

        prompt = f"""
        You are an expert in-game co-pilot and tactical spotter looking at the player's active world map screenshot.
        Target Destination or Landmark: "{landmark_description}"

        Analyze the current visible map screen:
        1. Is the target destination, quest marker, or landmark visibly present on this screen view?
        2. If visible, describe where it is located on the screen relative to the player character or central landmarks (e.g. "top-right corner", "due east of the river", "center screen near the fortress").
        3. If it is NOT visible, identify which general cardinal direction (north, south, east, west, northeast, etc.) the player should pan their map to reach that territory, based on visible region names, roads, or terrain features.
        4. Provide a clear, natural, spoken co-pilot sentence advising the player where to look or to place their waypoint marker.

        Return strictly a JSON object:
        {{
            "found_on_screen": true or false,
            "relative_location": "description of location or bearing",
            "spoken_guidance": "Concise in-character sentence advising player where to look or mark (e.g. 'I see it! Drop your marker just east of the ruined tower icon.' or 'Pan your map northeast toward Silver Wolf Mountain, right past the river.')"
        }}
        """
        try:
            data = self._call_vision_json(img_bytes, prompt)
            return {
                "status": "success",
                "found": data.get("found_on_screen", False),
                "relative_location": data.get("relative_location", ""),
                "spoken_guidance": data.get("spoken_guidance", f"Check your map markers around {landmark_description}.")
            }
        except Exception as e:
            return {
                "status": "error",
                "found": False,
                "error": str(e),
                "spoken_guidance": f"I'm scanning the map, but having trouble identifying {landmark_description}. Check your quest icons."
            }

    def _resolve_keyboard_bind(self, candidate_phrases: list[str], fallback_key: str = "") -> Tuple[str, list[str]]:
        """
        Resolves a keyboard key & modifiers from synced profile `keybinds` matching any
        candidate voice phrase, alias, or raw `action_id` (always preferring Keyboard binds).
        """
        kb = getattr(self, "keybinds", None) or {}
        wanted = [str(p).strip().lower() for p in candidate_phrases if str(p).strip()]
        for w in wanted:
            entry = kb.get(w)
            if isinstance(entry, dict):
                dev = str(entry.get("device") or "keyboard").lower()
                if dev == "keyboard" and entry.get("key"):
                    return str(entry["key"]).lower().strip(), list(entry.get("modifiers") or [])
        for phrase, entry in kb.items():
            if not isinstance(entry, dict) or not entry.get("key"):
                continue
            dev = str(entry.get("device") or "keyboard").lower()
            if dev != "keyboard":
                continue
            act_id = str(entry.get("action_id") or "").strip().lower()
            aliases = [str(a).strip().lower() for a in (entry.get("aliases") or [])]
            if phrase.lower().strip() in wanted or act_id in wanted or any(a in wanted for a in aliases):
                return str(entry["key"]).lower().strip(), list(entry.get("modifiers") or [])
        return fallback_key, []

    def locate_ui_element_point(self, element_hint: str) -> Optional[Tuple[int, int]]:
        """Locates a UI control (e.g. search bar, plot route button) on the current viewport via vision."""
        if not self.client or not element_hint:
            return None
        img_bytes, width, height = self.capture_viewport()
        if not img_bytes:
            return None
        prompt = f"""
Analyze this in-game map/navigation interface screenshot.
Locate the exact center of the UI element described as: "{element_hint}".
Return ONLY a JSON object:
{{"found": true, "point": [y_center_normalized, x_center_normalized]}}
Values must be normalized between 0 and 1000.
If the element is not visible on screen, return {{"found": false}}.
"""
        try:
            data = self._call_vision_json(img_bytes, prompt)
            if data.get("found") and isinstance(data.get("point"), list) and len(data["point"]) == 2:
                y_norm, x_norm = data["point"]
                return self._norm_to_screen_px(x_norm, y_norm, width, height)
        except Exception as e:
            print(f"[DEBUG] [GAME_NAV] UI element grounding failed for '{element_hint}': {e}")
        return None

    def search_and_route_destination(
        self,
        destination_name: str,
        map_key: str = "m",
        search_focus_key: str = "",
        plot_key: str = "space",
        target_process: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Declarative Keyboard & Mouse Search-Bar Navigation for games with text-searchable maps
        (e.g. Elite Dangerous Galaxy Map, StarMap, etc.).

        Reads optional `map_controls["search_routing"]` recipe from the game's `.aethermodule.json`:
        - Dynamically resolves map-open keyboard key from synced keybinds (never assumes 'm' if bound otherwise)
        - Copies destination to clipboard as instant backup and pastes via Ctrl+V (or DirectInput typing)
        - Focuses search bar via configured hotkey or vision-grounded mouse click
        - Submits search and triggers route plotting via vision-grounded click/hold or configured key
        - Optionally verifies plotted route via live telemetry (e.g. NavRoute.json `route_destination`)
        """
        from tools.os_controls import send_directinput_combo, send_directinput_string

        dest_clean = str(destination_name or "").strip()
        if not dest_clean:
            return {"status": "error", "message": "Destination name cannot be empty."}

        ctrl = getattr(self, "map_controls", {}) or {}
        routing_cfg = ctrl.get("search_routing") if isinstance(ctrl.get("search_routing"), dict) else {}

        # 0. Copy destination to clipboard immediately (fast paste + reliable manual backup)
        clipboard_copied = False
        try:
            from tools.clipboard import set_clipboard_text
            clipboard_copied = bool(set_clipboard_text(dest_clean))
        except Exception:
            clipboard_copied = False

        proc = target_process or getattr(self, "target_process", "")
        if proc:
            try:
                from tools.os_controls import focus_window
                focus_window(proc)
                time.sleep(0.15)
            except Exception:
                pass

        # 1. Resolve map-open keyboard bind dynamically from synced binds
        map_candidates = []
        if routing_cfg.get("map_open_action"):
            map_candidates.append(str(routing_cfg["map_open_action"]))
        if isinstance(routing_cfg.get("map_open_actions"), list):
            map_candidates.extend(str(x) for x in routing_cfg["map_open_actions"])
        if not map_candidates:
            map_candidates = ["open galaxy map", "galaxy map open", "open map"]

        fallback_map_key = str(routing_cfg.get("map_key") or map_key or "m")
        resolved_map_key, resolved_map_mods = self._resolve_keyboard_bind(map_candidates, fallback_key=fallback_map_key)
        open_wait = float(routing_cfg.get("open_wait_sec") or 0.9)

        # Open map if not already visible
        initial_frame, _, _ = self.capture_viewport()
        if not self.is_map_screen(initial_frame):
            if resolved_map_key:
                if resolved_map_mods:
                    send_directinput_combo(resolved_map_mods, resolved_map_key, hold_duration=0.12)
                else:
                    send_directinput_key(resolved_map_key, duration_sec=0.12)
                time.sleep(open_wait)

        # 2. Focus search bar (explicit key, vision-located mouse click, or already focused)
        eff_focus_key = str(routing_cfg.get("search_focus_key") or search_focus_key or "").strip()
        search_hint = str(routing_cfg.get("search_box_visual_hint") or "").strip()
        focused_via_vision = False

        if search_hint and self.client:
            pt = self.locate_ui_element_point(search_hint)
            if pt:
                move_mouse_absolute(pt[0], pt[1])
                time.sleep(0.1)
                click_mouse_button("left", hold_duration=0.06)
                time.sleep(0.2)
                focused_via_vision = True

        if not focused_via_vision and eff_focus_key:
            send_directinput_key(eff_focus_key, duration_sec=0.1)
            time.sleep(0.3)

        # 3. Enter destination (Clipboard Ctrl+A + Ctrl+V preferred for speed & exact punctuation, else DirectInput typing)
        input_method = str(routing_cfg.get("input_method") or ("clipboard_paste" if routing_cfg else "directinput")).lower()
        if input_method == "clipboard_paste" and clipboard_copied:
            send_directinput_combo(["ctrl"], "a", hold_duration=0.05)
            time.sleep(0.06)
            send_directinput_combo(["ctrl"], "v", hold_duration=0.06)
            time.sleep(0.25)
        else:
            send_directinput_string(dest_clean, delay_sec=0.04)
            time.sleep(0.2)

        submit_key = str(routing_cfg["submit_key"] if "submit_key" in routing_cfg else "enter").strip()
        if submit_key:
            send_directinput_key(submit_key, duration_sec=0.1)
        select_wait = float(routing_cfg.get("select_result_wait_sec") or 0.9)
        time.sleep(select_wait)

        # Optional declarative post-input navigation keys (e.g. [{"key": "down", "delay_ms": 400}, {"key": "enter", "delay_ms": 1100}])
        post_keys = routing_cfg.get("post_input_keys")
        if isinstance(post_keys, list) and post_keys:
            for pk in post_keys:
                if isinstance(pk, dict) and pk.get("key"):
                    send_directinput_key(str(pk["key"]).strip(), duration_sec=0.1)
                    time.sleep(max(0.05, float(pk.get("delay_ms") or 400) / 1000.0))
                elif isinstance(pk, str) and pk.strip():
                    send_directinput_key(pk.strip(), duration_sec=0.1)
                    time.sleep(0.4)
        elif routing_cfg.get("confirm_dropdown_enter"):
            send_directinput_key("enter", duration_sec=0.1)
            time.sleep(0.6)

        # 4. Trigger Plot Route (declarative pre-plot cursor nudge + key hold and/or vision click-hold)
        plot_hint = str(routing_cfg.get("plot_visual_hint") or "").strip()
        plot_hold_sec = float(routing_cfg.get("plot_hold_sec") or 0.15)
        eff_plot_key = str(routing_cfg.get("plot_key") if "plot_key" in routing_cfg else plot_key or "").strip()
        prefer_key_first = bool(routing_cfg.get("prefer_plot_key_first", False))
        pre_plot_move_center = bool(routing_cfg.get("pre_plot_move_center", False))
        pre_plot_nudge = routing_cfg.get("pre_plot_mouse_nudge")

        def _apply_pre_plot_cursor_nudge():
            from tools.os_controls import nudge_mouse_relative
            if pre_plot_move_center:
                try:
                    _, vw, vh = self.capture_viewport()
                    cx, cy = self._norm_to_screen_px(500, 500, vw, vh)
                    move_mouse_absolute(cx, cy)
                    time.sleep(0.08)
                except Exception:
                    pass
            if isinstance(pre_plot_nudge, (list, tuple)) and len(pre_plot_nudge) == 2:
                nudge_mouse_relative(int(pre_plot_nudge[0]), int(pre_plot_nudge[1]))
                time.sleep(0.14)
            elif pre_plot_nudge:
                nudge_mouse_relative(12, 8)
                time.sleep(0.14)

        def _try_plot_via_vision() -> bool:
            if not (plot_hint and self.client):
                return False
            from tools.os_controls import nudge_mouse_relative
            plot_pt = self.locate_ui_element_point(plot_hint)
            if not plot_pt:
                return False
            move_mouse_absolute(plot_pt[0], plot_pt[1])
            nudge_mouse_relative(1, 0)
            time.sleep(0.16)
            click_mouse_button("left", hold_duration=max(0.08, plot_hold_sec))
            time.sleep(0.4)
            return True

        def _try_plot_via_key() -> bool:
            if not eff_plot_key:
                return False
            _apply_pre_plot_cursor_nudge()
            send_directinput_key(eff_plot_key, duration_sec=max(0.15, plot_hold_sec))
            time.sleep(0.4)
            return True

        def _check_route_verified(timeout_sec: float):
            verify_field_local = str(routing_cfg.get("verify_telemetry_field") or "").strip()
            adapter_local = getattr(self, "telemetry_adapter", None)
            if not (verify_field_local and adapter_local is not None and hasattr(adapter_local, "get_state")):
                return None, None
            deadline_local = time.time() + max(0.2, timeout_sec)
            while time.time() < deadline_local:
                try:
                    st = adapter_local.get_state() or {}
                    val = str(st.get(verify_field_local) or "").strip()
                    if val and val.lower() == dest_clean.lower():
                        return val, st.get("route_jumps")
                except Exception:
                    break
                time.sleep(0.2)
            return None, None

        verify_field = str(routing_cfg.get("verify_telemetry_field") or "").strip()
        adapter = getattr(self, "telemetry_adapter", None)
        verification_expected = bool(verify_field and adapter is not None and hasattr(adapter, "get_state"))
        verify_timeout = float(routing_cfg.get("verify_timeout_sec") or 2.5)
        verified_route = None
        route_jumps = None

        if prefer_key_first and eff_plot_key:
            _try_plot_via_key()
            if verification_expected:
                verified_route, route_jumps = _check_route_verified(min(1.6, verify_timeout))
                if not verified_route and plot_hint and self.client:
                    if _try_plot_via_vision():
                        verified_route, route_jumps = _check_route_verified(verify_timeout)
        else:
            plotted_via_vision = _try_plot_via_vision()
            if plotted_via_vision and verification_expected:
                verified_route, route_jumps = _check_route_verified(min(1.6, verify_timeout))
                if not verified_route and eff_plot_key:
                    _try_plot_via_key()
                    verified_route, route_jumps = _check_route_verified(verify_timeout)
            elif not plotted_via_vision:
                _try_plot_via_key()
                if verification_expected:
                    verified_route, route_jumps = _check_route_verified(verify_timeout)

        if verified_route:
            status = "success"
            jump_note = f" ({route_jumps} jumps)" if route_jumps is not None else ""
            msg = f"Route plotted and confirmed to '{verified_route}'{jump_note}."
        elif verification_expected:
            status = "partial"
            msg = (
                f"Entered '{dest_clean}' in the map search bar and attempted to plot, "
                f"but live telemetry did not confirm the plotted route yet."
            )
            if clipboard_copied:
                msg += f" ('{dest_clean}' is on your clipboard.)"
        else:
            status = "success"
            msg = f"Entered destination '{dest_clean}' into navigation search and plotted route."
            if clipboard_copied and routing_cfg:
                msg += f" ('{dest_clean}' is also on your clipboard.)"

        res: Dict[str, Any] = {
            "status": status,
            "destination": dest_clean,
            "clipboard_copied": clipboard_copied,
            "verified": bool(verified_route),
            "message": msg,
        }
        if route_jumps is not None:
            res["route_jumps"] = route_jumps
        return res

    def complete_ui_task(
        self,
        goal: str,
        max_steps: int = 6,
        preferred_input: str = "auto",
        target_process: Optional[str] = None,
        learned_recipe: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Universal Closed-Loop Vision UI/Menu Navigator (100% game-agnostic).

        Features:
        - Fast Self-Learning Replay: If a previously verified `learned_recipe` exists for `goal`,
          replays its action steps directly (~0.8s) and performs a 1-shot Vision verification.
          If verification fails (e.g. cursor started in a different position), seamlessly falls back
          to the adaptive step-by-step Vision loop.
        - Strict Action-Button Verification: Never claims a task is "already done" while the target
          button/icon is still visibly enabled, lit, or displays a non-zero cost (even 10 CR).
        - Automatic Panel Cleanup: Closes any temporary side panel/overlay opened during the task
          via `cleanup_keys` so the player's view returns cleanly to gameplay.
        """
        from tools.os_controls import focus_window, nudge_mouse_relative

        clean_goal = str(goal or "").strip()
        if not clean_goal:
            return {"status": "error", "message": "UI task goal cannot be empty."}
        if not self.client:
            return {"status": "error", "message": "Vision client unavailable for closed-loop UI navigation."}

        proc = target_process or getattr(self, "target_process", "")
        if proc:
            try:
                focus_window(proc)
                time.sleep(0.1)
            except Exception:
                pass

        def _run_cleanup_keys(ckeys: Any) -> List[str]:
            executed_cleanup: List[str] = []
            if isinstance(ckeys, list) and ckeys:
                clean_ckeys = [str(k).strip().lower() for k in ckeys if str(k).strip()]
                for idx, ck in enumerate(clean_ckeys[:3]):
                    send_directinput_key(ck, duration_sec=0.08)
                    executed_cleanup.append(ck)
                    if idx < len(clean_ckeys[:3]) - 1:
                        time.sleep(0.12)
                if executed_cleanup:
                    time.sleep(0.15)
            return executed_cleanup

        def _execute_recorded_action(act_spec: Dict[str, Any], vw: int, vh: int) -> None:
            atype = str(act_spec.get("action") or "").upper()
            if atype == "KEYS":
                keys_list = act_spec.get("keys") or []
                hold_s = max(0.05, min(1.5, float(act_spec.get("hold_ms") or 80) / 1000.0))
                int_s = max(0.05, min(0.6, float(act_spec.get("interval_ms") or 130) / 1000.0))
                for idx, kname in enumerate(keys_list):
                    send_directinput_key(str(kname).strip().lower(), duration_sec=hold_s)
                    if idx < len(keys_list) - 1:
                        time.sleep(int_s)
            elif atype == "CLICK":
                pt = act_spec.get("point")
                if isinstance(pt, (list, tuple)) and len(pt) == 2:
                    px, py = self._norm_to_screen_px(float(pt[1]), float(pt[0]), vw, vh)
                    btn = str(act_spec.get("button") or "left").lower().strip()
                    hold_s = max(0.05, min(1.5, float(act_spec.get("hold_ms") or 90) / 1000.0))
                    move_mouse_absolute(px, py)
                    nudge_mouse_relative(1, 0)
                    time.sleep(0.16)
                    click_mouse_button(btn, hold_duration=hold_s)
            wait_s = max(0.15, min(1.0, float(act_spec.get("wait_ms") or 300) / 1000.0))
            time.sleep(wait_s)

        history: List[str] = []
        recorded_actions: List[Dict[str, Any]] = []

        # 0. Fast-Path Self-Learning Replay + 1-Shot Visual Verification
        if isinstance(learned_recipe, dict) and isinstance(learned_recipe.get("actions"), list) and learned_recipe["actions"]:
            _, vw, vh = self.capture_viewport()
            for r_act in learned_recipe["actions"]:
                if isinstance(r_act, dict):
                    _execute_recorded_action(r_act, vw, vh)
            verify_frame, _, _ = self.capture_viewport()
            replay_verify_prompt = f"""
Inspect this live game screenshot right after replaying a saved UI sequence for the goal: "{clean_goal}".
CRITICAL RULES:
- Do NOT say completed=true if the target action button/icon is still visibly ENABLED, LIT, or shows ANY non-zero cost (even a tiny cost like 10 CR, or even if a resource bar looks almost full).
- Only return completed=true if the target button is now visibly DISABLED / GREYED OUT / 0 cost, OR a clear confirmation banner/state change shows the action just succeeded.
- If a temporary side panel/menu is still open on screen that should be closed to return to gameplay, list the key(s) to close it in "cleanup_keys" (otherwise []).

Return ONLY JSON:
{{"completed": true or false, "observation": "Brief 1-sentence confirmation of what just succeeded or what is still needed", "cleanup_keys": []}}
"""
            try:
                replay_check = self._call_vision_json(verify_frame, replay_verify_prompt, fast_mode=True) or {}
            except TypeError:
                replay_check = self._call_vision_json(verify_frame, replay_verify_prompt) or {}
            if bool(replay_check.get("completed")):
                ckeys = replay_check.get("cleanup_keys") or learned_recipe.get("cleanup_keys") or []
                ran_cleanup = _run_cleanup_keys(ckeys)
                obs = str(replay_check.get("observation") or f"Completed '{clean_goal}'.").strip()
                return {
                    "status": "verified_done",
                    "verified": True,
                    "just_executed": True,
                    "replayed_recipe": True,
                    "goal": clean_goal,
                    "steps_executed": len(learned_recipe["actions"]),
                    "cleanup_keys": ran_cleanup,
                    "observation": obs,
                    "message": (
                        f"Completed '{clean_goal}' just now via learned fast replay: {obs} "
                        f"(Confirm naturally that it just succeeded — do NOT say it was 'already' done.)"
                    ),
                }
            history.append(
                f"Fast replay did not confirm completion ({replay_check.get('observation') or 'adapting from current screen'})"
            )

        capped_steps = max(1, min(10, int(max_steps or 6)))
        pref = str(preferred_input or "auto").lower().strip()
        input_hint = (
            "Prefer KEYBOARD menu navigation (KEYS) relative to the currently highlighted item whenever a highlight cursor is visible; use CLICK only if keyboard navigation is unclear."
            if pref == "keyboard"
            else (
                "Prefer direct mouse CLICK on the visible button/icon."
                if pref == "mouse"
                else "Choose whichever is cleaner and more reliable on this screen: KEYBOARD navigation ('KEYS') relative to the visibly highlighted item/tab (great for 3D cockpit/console menus where highlight cursors remember position), OR direct mouse 'CLICK' on an unambiguous button."
            )
        )

        for step_num in range(1, capped_steps + 1):
            frame_bytes, width, height = self.capture_viewport()
            prior_summary = "\n".join(f"  - Step {i + 1}: {h}" for i, h in enumerate(history)) or "  (none yet)"
            has_acted = len(recorded_actions) > 0
            verify_rule = (
                'Check if the action we JUST executed visibly succeeded (e.g., confirmation banner/message visible, target button turned disabled/greyed out/0 cost, or target screen opened).'
                if has_acted
                else 'Check if the GOAL is genuinely already complete with NOTHING left to click/activate.'
            )
            prompt = f"""
You are an autonomous visual UI/menu agent operating inside the game "{self.game_name or 'Active Game'}".
GOAL: "{clean_goal}"

Previous steps already executed in this attempt:
{prior_summary}

Carefully inspect the live screenshot:
1. Identify which menu item, icon, row, or tab is CURRENTLY HIGHLIGHTED / SELECTED (if any).
2. {verify_rule}
   STRICT VERIFICATION GUARD:
   - NEVER return "VERIFIED_DONE" if the target action button/icon (e.g. Refuel, Repair, Restock, Buy, Request, Confirm) is STILL VISIBLY ENABLED, LIT, or shows ANY non-zero cost (even a tiny cost like 10 CR, or even if a fuel/ammo/health bar looks almost full!). You MUST activate/press that button first!
   - Only return "VERIFIED_DONE" when the target button itself is visibly DISABLED / GREYED OUT / 0 cost, OR a clear confirmation banner/state change shows the action succeeded.
   - When returning "VERIFIED_DONE": if earlier steps opened a temporary side panel/overlay to perform this task (and the player should return to the main cockpit/gameplay view), set "cleanup_keys" to the key(s) that close that panel (e.g. ["1"] or ["escape"]); otherwise set "cleanup_keys": [].
3. Check if the GOAL is IMPOSSIBLE from this screen and no obvious key opens the needed menu. If so, return action "CANNOT_COMPLETE" with a clear reason.
4. Otherwise, decide the BEST NEXT ACTION to advance toward the goal:
   - {input_hint}
   - For "KEYS": list the exact ordered keys to press (e.g. ["space"] if the target icon/button is ALREADY highlighted, ["a", "space"] if one icon to the left, ["w", "w"] to move up 2 rows, ["1", "e", "e"] to open a side panel and tab right twice). You may batch 1-4 clear sequential keys in one step to reduce latency.
   - For "CLICK": return normalized [y, x] coordinates (0-1000 scale, center of target button/icon) and "button" ("left" or "right").

Return ONLY valid JSON:
{{
  "highlighted_now": "What is currently highlighted/focused on screen (or 'none')",
  "observation": "Brief 1-sentence assessment of current UI state",
  "action": "KEYS" | "CLICK" | "VERIFIED_DONE" | "CANNOT_COMPLETE",
  "keys": ["w", "space"],
  "hold_ms": 80,
  "interval_ms": 130,
  "point": [y_normalized, x_normalized],
  "button": "left",
  "wait_ms": 380,
  "cleanup_keys": [],
  "reason": "Only needed for VERIFIED_DONE or CANNOT_COMPLETE"
}}
"""
            try:
                decision = self._call_vision_json(frame_bytes, prompt, fast_mode=True)
            except TypeError:
                decision = self._call_vision_json(frame_bytes, prompt)
            if not isinstance(decision, dict) or not decision:
                return {
                    "status": "incomplete",
                    "goal": clean_goal,
                    "steps_executed": len(recorded_actions),
                    "history": history,
                    "message": f"Vision evaluation failed on step {step_num} while attempting '{clean_goal}'.",
                }

            action = str(decision.get("action") or "").upper().strip()
            obs = str(decision.get("observation") or "").strip()
            highlighted = str(decision.get("highlighted_now") or "").strip()

            if action == "VERIFIED_DONE":
                ran_cleanup = _run_cleanup_keys(decision.get("cleanup_keys"))
                raw_reason = str(decision.get("reason") or obs or f"Completed '{clean_goal}'.").strip()
                if recorded_actions:
                    msg = (
                        f"Completed '{clean_goal}' just now ({len(recorded_actions)} step(s)): {raw_reason} "
                        f"(Confirm naturally that it just succeeded — do NOT say it was 'already' done.)"
                    )
                    learned = {
                        "actions": recorded_actions,
                        "cleanup_keys": ran_cleanup,
                    }
                    return {
                        "status": "verified_done",
                        "verified": True,
                        "just_executed": True,
                        "already_done": False,
                        "goal": clean_goal,
                        "steps_executed": len(recorded_actions),
                        "cleanup_keys": ran_cleanup,
                        "learned_recipe": learned,
                        "history": history,
                        "observation": obs,
                        "message": msg,
                    }
                return {
                    "status": "verified_done",
                    "verified": True,
                    "just_executed": False,
                    "already_done": True,
                    "goal": clean_goal,
                    "steps_executed": 0,
                    "cleanup_keys": ran_cleanup,
                    "history": history,
                    "observation": obs,
                    "message": raw_reason,
                }

            if action == "CANNOT_COMPLETE":
                reason = str(decision.get("reason") or obs or f"Cannot complete '{clean_goal}' from current screen.").strip()
                return {
                    "status": "cannot_complete",
                    "verified": False,
                    "goal": clean_goal,
                    "steps_executed": len(recorded_actions),
                    "history": history,
                    "observation": obs,
                    "message": reason,
                }

            wait_ms = max(180, min(1400, int(decision.get("wait_ms") or 380)))
            wait_sec = wait_ms / 1000.0

            if action == "KEYS":
                keys = decision.get("keys")
                if not isinstance(keys, list) or not keys:
                    history.append(f"Empty KEYS decision ({obs})")
                    continue
                hold_ms = max(50, min(1500, int(decision.get("hold_ms") or 80)))
                interval_ms = max(50, min(600, int(decision.get("interval_ms") or 130)))
                clean_keys = [str(k).strip().lower() for k in keys if str(k).strip()]
                for idx, key_name in enumerate(clean_keys):
                    send_directinput_key(key_name, duration_sec=hold_ms / 1000.0)
                    if idx < len(clean_keys) - 1:
                        time.sleep(interval_ms / 1000.0)
                recorded_actions.append({
                    "action": "KEYS",
                    "keys": clean_keys,
                    "hold_ms": hold_ms,
                    "interval_ms": interval_ms,
                    "wait_ms": wait_ms,
                })
                history.append(f"Highlighted='{highlighted}' -> KEYS {clean_keys} ({obs})")
                time.sleep(wait_sec)

            elif action == "CLICK":
                pt = decision.get("point")
                if not isinstance(pt, (list, tuple)) or len(pt) != 2:
                    history.append(f"Invalid CLICK coordinates ({obs})")
                    continue
                y_norm, x_norm = float(pt[0]), float(pt[1])
                px, py = self._norm_to_screen_px(x_norm, y_norm, width, height)
                btn = str(decision.get("button") or "left").lower().strip()
                hold_ms = max(50, min(1500, int(decision.get("hold_ms") or 90)))
                move_mouse_absolute(px, py)
                nudge_mouse_relative(1, 0)
                time.sleep(0.16)
                click_mouse_button(btn, hold_duration=hold_ms / 1000.0)
                recorded_actions.append({
                    "action": "CLICK",
                    "point": [y_norm, x_norm],
                    "button": btn,
                    "hold_ms": hold_ms,
                    "wait_ms": wait_ms,
                })
                history.append(f"Highlighted='{highlighted}' -> CLICK({btn}) at ({px}, {py}) ({obs})")
                time.sleep(wait_sec)
            else:
                history.append(f"Unknown action '{action}' ({obs})")

        # Final visual verification pass after the last executed step
        final_frame, _, _ = self.capture_viewport()
        verify_prompt = f"""
Inspect this live game screenshot to verify whether the action we JUST executed for "{clean_goal}" succeeded.
CRITICAL: Do NOT return completed=true if the target action button is still visibly enabled/lit or shows a non-zero cost (even 10 CR).
If a temporary side panel/menu should now be closed to return to gameplay, include the close key(s) in "cleanup_keys" (otherwise []).
Return ONLY JSON:
{{"completed": true or false, "observation": "Brief 1-sentence confirmation or what is still missing", "cleanup_keys": []}}
"""
        try:
            final_check = self._call_vision_json(final_frame, verify_prompt, fast_mode=True) or {}
        except TypeError:
            final_check = self._call_vision_json(final_frame, verify_prompt) or {}
        if bool(final_check.get("completed")):
            ran_cleanup = _run_cleanup_keys(final_check.get("cleanup_keys"))
            obs = str(final_check.get("observation") or f"Completed '{clean_goal}'.").strip()
            learned = {"actions": recorded_actions, "cleanup_keys": ran_cleanup} if recorded_actions else None
            out = {
                "status": "verified_done",
                "verified": True,
                "just_executed": bool(recorded_actions),
                "already_done": not bool(recorded_actions),
                "goal": clean_goal,
                "steps_executed": len(recorded_actions),
                "cleanup_keys": ran_cleanup,
                "history": history,
                "observation": obs,
                "message": (
                    f"Completed '{clean_goal}' just now: {obs} "
                    f"(Confirm naturally that it just succeeded — do NOT say it was 'already' done.)"
                    if recorded_actions
                    else obs
                ),
            }
            if learned:
                out["learned_recipe"] = learned
            return out

        return {
            "status": "incomplete",
            "verified": False,
            "goal": clean_goal,
            "steps_executed": len(recorded_actions),
            "history": history,
            "observation": str(final_check.get("observation") or ""),
            "message": (
                f"Executed {len(recorded_actions)} adaptive UI steps for '{clean_goal}', "
                f"but could not visually confirm completion: {final_check.get('observation') or 'no visual confirmation'}."
            ),
        }

    def route_to_map_bookmark(
        self,
        bookmark_number: int,
        category: str = "",
        target_process: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Universal Declarative Map Bookmark Navigator (100% game-agnostic).

        Reads `map_controls["bookmarks"]` from the active game module:
        - Defaults `category` to `default_category` (e.g. 'favorites') when omitted.
        - Opens the map if not already visible, navigates to the Bookmarks panel and target category,
          locates item #`bookmark_number` via Vision, plots the route via click-and-hold, and
          automatically falls back to `search_and_route_destination(extracted_system)` if a parent
          routable system/region name is printed beneath the bookmark entry.
        """
        from tools.os_controls import focus_window, send_directinput_combo, nudge_mouse_relative

        try:
            idx_num = max(1, int(bookmark_number))
        except (TypeError, ValueError):
            return {"status": "error", "message": f"Invalid bookmark number: {bookmark_number!r}."}

        if not self.client:
            return {"status": "error", "message": "Vision client unavailable for bookmark navigation."}

        ctrl = getattr(self, "map_controls", {}) or {}
        bm_cfg = ctrl.get("bookmarks") if isinstance(ctrl.get("bookmarks"), dict) else {}
        routing_cfg = ctrl.get("search_routing") if isinstance(ctrl.get("search_routing"), dict) else {}

        default_cat = str(bm_cfg.get("default_category") or "favorites").strip()
        allowed_cats = [str(c).strip() for c in (bm_cfg.get("categories") or []) if str(c).strip()]
        raw_cat = str(category or "").strip().lower().replace(" ", "_").replace("-", "_")
        if not raw_cat:
            resolved_cat = default_cat
        else:
            # Fuzzy match singular/plural category names (e.g. 'system' -> 'systems', 'favourite' -> 'favorites')
            resolved_cat = raw_cat
            for c in allowed_cats:
                cl = c.lower()
                if cl == raw_cat or cl.rstrip("s") == raw_cat.rstrip("s") or raw_cat in cl:
                    resolved_cat = c
                    break
            if raw_cat in ("favourite", "favourites", "fav", "favs", "favorite"):
                resolved_cat = default_cat

        pretty_cat = resolved_cat.replace("_", " ").title()
        plot_hold_sec = float(bm_cfg.get("plot_hold_sec") or routing_cfg.get("plot_hold_sec") or 1.2)

        proc = target_process or getattr(self, "target_process", "")
        if proc:
            try:
                focus_window(proc)
                time.sleep(0.12)
            except Exception:
                pass

        # 1. Ensure the map screen is open
        map_candidates = []
        if routing_cfg.get("map_open_action"):
            map_candidates.append(str(routing_cfg["map_open_action"]))
        if isinstance(routing_cfg.get("map_open_actions"), list):
            map_candidates.extend(str(x) for x in routing_cfg["map_open_actions"])
        if not map_candidates:
            map_candidates = ["open galaxy map", "galaxy map open", "open map"]
        fallback_map_key = str(routing_cfg.get("map_key") or "m")
        resolved_map_key, resolved_map_mods = self._resolve_keyboard_bind(map_candidates, fallback_key=fallback_map_key)
        open_wait = float(routing_cfg.get("open_wait_sec") or 1.2)

        init_frame, _, _ = self.capture_viewport()
        if not self.is_map_screen(init_frame):
            if resolved_map_key:
                if resolved_map_mods:
                    send_directinput_combo(resolved_map_mods, resolved_map_key, hold_duration=0.12)
                else:
                    send_directinput_key(resolved_map_key, duration_sec=0.12)
                time.sleep(open_wait)

        # 2. Ensure Bookmarks panel & target category tab are open (up to 3 quick vision steps)
        open_hint = str(bm_cfg.get("open_bookmarks_visual_hint") or "Bookmarks icon on the map sidebar").strip()
        target_entry: Optional[Dict[str, Any]] = None

        for _ in range(3):
            frame, vw, vh = self.capture_viewport()
            inspect_prompt = f"""
You are navigating the Bookmarks panel inside "{self.game_name or 'Active Game'}"'s map.
TARGET: Category "{pretty_cat}", Bookmark #{idx_num} (1-indexed from top of the list).
Available categories in order (if shown as vertical sidebar icons): {allowed_cats or [pretty_cat]}.

Inspect the live screenshot:
1. If the Bookmarks panel is NOT open yet, return state "NEED_OPEN_BOOKMARKS" and normalized [y, x] (0-1000) of the "{open_hint}".
2. If the Bookmarks panel IS open, check its category header (e.g. "FAVOURITES", "SYSTEMS", "BODIES", "STATIONS", etc.).
   - If the open category does NOT match "{pretty_cat}", return state "NEED_SWITCH_CATEGORY" and normalized [y, x] (0-1000) of the "{pretty_cat}" category icon/tab.
3. If the "{pretty_cat}" bookmark list IS visible:
   - Count down from the top (1, 2, 3...) to find item #{idx_num}.
   - Return state "FOUND_BOOKMARK" with:
     - "bookmark_title": the title text of item #{idx_num}
     - "system_name": the parent star system / routable region name printed beneath/inside item #{idx_num} (if visible, else "")
     - "point": [y_normalized, x_normalized] center of item #{idx_num}
   - If there are fewer than {idx_num} items visible in "{pretty_cat}", return state "NOT_FOUND".

Return ONLY JSON:
{{
  "state": "NEED_OPEN_BOOKMARKS" | "NEED_SWITCH_CATEGORY" | "FOUND_BOOKMARK" | "NOT_FOUND",
  "point": [y_normalized, x_normalized],
  "bookmark_title": "",
  "system_name": "",
  "observation": "1-sentence observation"
}}
"""
            try:
                info = self._call_vision_json(frame, inspect_prompt, fast_mode=True) or {}
            except TypeError:
                info = self._call_vision_json(frame, inspect_prompt) or {}

            st_code = str(info.get("state") or "").upper().strip()
            pt = info.get("point")

            if st_code in ("NEED_OPEN_BOOKMARKS", "NEED_SWITCH_CATEGORY") and isinstance(pt, (list, tuple)) and len(pt) == 2:
                px, py = self._norm_to_screen_px(float(pt[1]), float(pt[0]), vw, vh)
                move_mouse_absolute(px, py)
                nudge_mouse_relative(1, 0)
                time.sleep(0.16)
                click_mouse_button("left", hold_duration=0.08)
                time.sleep(0.45)
                continue

            if st_code == "FOUND_BOOKMARK":
                target_entry = info
                break

            return {
                "status": "not_found",
                "category": pretty_cat,
                "bookmark_number": idx_num,
                "message": f"Could not find bookmark #{idx_num} under '{pretty_cat}': {info.get('observation') or 'not visible'}.",
            }

        if not target_entry:
            return {
                "status": "not_found",
                "category": pretty_cat,
                "bookmark_number": idx_num,
                "message": f"Could not open '{pretty_cat}' bookmarks or locate item #{idx_num}.",
            }

        bm_title = str(target_entry.get("bookmark_title") or f"{pretty_cat} #{idx_num}").strip()
        bm_system = str(target_entry.get("system_name") or "").strip()
        pt = target_entry.get("point")

        # 3. Click-and-hold bookmark item to plot route directly
        if isinstance(pt, (list, tuple)) and len(pt) == 2:
            _, vw, vh = self.capture_viewport()
            px, py = self._norm_to_screen_px(float(pt[1]), float(pt[0]), vw, vh)
            move_mouse_absolute(px, py)
            nudge_mouse_relative(1, 0)
            time.sleep(0.16)
            click_mouse_button("left", hold_duration=max(0.15, plot_hold_sec))
            time.sleep(0.5)

        # 4. Verify via live telemetry if configured; if not locked in and we read the parent system name,
        #    automatically fall back to search_and_route_destination(bm_system)!
        verify_field = str(routing_cfg.get("verify_telemetry_field") or "").strip()
        adapter = getattr(self, "telemetry_adapter", None)
        verified_route = None
        route_jumps = None
        if verify_field and adapter is not None and hasattr(adapter, "get_state"):
            deadline = time.time() + 1.8
            while time.time() < deadline:
                try:
                    st = adapter.get_state() or {}
                    val = str(st.get(verify_field) or "").strip()
                    if val and (
                        (bm_system and val.lower() == bm_system.lower())
                        or val.lower() in bm_title.lower()
                        or bm_title.lower() in val.lower()
                    ):
                        verified_route = val
                        route_jumps = st.get("route_jumps")
                        break
                except Exception:
                    break
                time.sleep(0.2)

        if verified_route:
            jump_note = f" ({route_jumps} jumps)" if route_jumps is not None else ""
            return {
                "status": "success",
                "verified": True,
                "category": pretty_cat,
                "bookmark_number": idx_num,
                "bookmark_title": bm_title,
                "destination": verified_route,
                "route_jumps": route_jumps,
                "message": f"Plotted route via {pretty_cat} bookmark #{idx_num} ('{bm_title}') to '{verified_route}'{jump_note}.",
            }

        if bm_system:
            fallback_res = self.search_and_route_destination(destination_name=bm_system, target_process=proc)
            fallback_res["bookmark_title"] = bm_title
            fallback_res["category"] = pretty_cat
            fallback_res["bookmark_number"] = idx_num
            return fallback_res

        return {
            "status": "partial",
            "verified": False,
            "category": pretty_cat,
            "bookmark_number": idx_num,
            "bookmark_title": bm_title,
            "message": f"Selected {pretty_cat} bookmark #{idx_num} ('{bm_title}') and attempted to plot, but live telemetry has not confirmed the route yet.",
        }


class GameNavigator(UniversalGameNavigator):
    """
    Backwards-compatible interface extending UniversalGameNavigator.
    Supports existing direct-method callers and test suites while sharing universal primitives.
    """

    def __init__(
        self,
        client=None,
        model_endpoint: str = "gemini-4-hydrogen",
        target_process: str = "",
        map_controls: Optional[Dict[str, Any]] = None,
        game_name: str = "",
        keybinds: Optional[Dict[str, Any]] = None,
        telemetry_adapter=None,
    ):
        super().__init__(
            client=client,
            model_endpoint=model_endpoint,
            target_process=target_process,
            map_controls=map_controls,
            game_name=game_name,
            keybinds=keybinds,
            telemetry_adapter=telemetry_adapter,
        )

    def capture_screen(self) -> Tuple[bytes, int, int]:
        """Captures the primary monitor and returns JPEG bytes and resolution (width, height)."""
        return self.capture_viewport()

    def pan_map(self, direction: str, distance: int = 350, button: Optional[str] = None):
        """Pans the map canvas toward a cardinal direction using relative mouse dragging or gamepad stick."""
        ctrl = getattr(self, "map_controls", {}) or {}
        preferred_input = ctrl.get("preferred_input", "mouse_keyboard")
        if preferred_input == "gamepad":
            from tools.os_controls import send_gamepad_stick
            stick_map = {
                "north": (0.0, 1.0),
                "south": (0.0, -1.0),
                "east": (1.0, 0.0),
                "west": (-1.0, 0.0),
                "northeast": (0.7, 0.7),
                "northwest": (-0.7, 0.7),
                "southeast": (0.7, -0.7),
                "southwest": (-0.7, -0.7),
            }
            sx, sy = stick_map.get(direction.lower().strip(), (0.0, 0.0))
            if sx != 0.0 or sy != 0.0:
                sent = send_gamepad_stick("right", sx, sy, duration_sec=0.35)
                if sent:
                    time.sleep(0.2)
                    return

        actual_button = button
        if not actual_button:
            pan_method = ctrl.get("pan_method")
            if pan_method == "right_drag":
                actual_button = "right"
            elif pan_method == "left_drag":
                actual_button = "left"
            else:
                actual_button = "left" if ctrl else "right"

        delta_map = {
            "north": (0, distance),
            "south": (0, -distance),
            "east": (-distance, 0),
            "west": (distance, 0),
            "northeast": (-int(distance * 0.7), int(distance * 0.7)),
            "northwest": (int(distance * 0.7), int(distance * 0.7)),
            "southeast": (-int(distance * 0.7), -int(distance * 0.7)),
            "southwest": (int(distance * 0.7), -int(distance * 0.7)),
        }
        dx, dy = delta_map.get(direction.lower().strip(), (0, 0))
        if dx != 0 or dy != 0:
            drag_mouse_relative(dx, dy, button=actual_button, steps=14)
            time.sleep(0.3)

    def locate_landmark_point(self, landmark_description: str, web_context: str = "") -> Optional[Tuple[int, int]]:
        """Uses visual grounding or regional dead-reckoning to find landmark coordinates."""
        if not self.client:
            print("[WARN] [GAME_NAV] No Gemini client provided for visual landmark grounding.")
            return None

        img_bytes, width, height = self.capture_screen()
        intel_block = f"\nRegional Intel / Known Location Hint:\n{web_context}\n" if web_context else ""
        prompt = f"""
Analyze this in-game world map screenshot. Locate the visual landmark, territory, or icon corresponding to: "{landmark_description}".{intel_block}
If the exact landmark or vendor icon is locked behind Fog-of-War or uncharted terrain:
- Identify the parent territory label (e.g. city or region name) or nearest visible boundary.
- Estimate the projected point inside that territory (dead-reckoning).

Return ONLY a JSON object:
{{"found": true, "point": [y_center_normalized, x_center_normalized]}}
Values must be normalized between 0 and 1000.
If neither the landmark nor its containing region/anchor can be found, return {{"found": false}}.
"""
        try:
            data = self._call_vision_json(img_bytes, prompt)
            if data.get("found") and "point" in data and len(data["point"]) == 2:
                y_norm, x_norm = data["point"]
                return self._norm_to_screen_px(x_norm, y_norm, width, height)
        except Exception as e:
            print(f"[ERROR] [GAME_NAV] Vision grounding failed: {e}")
        return None

    def verify_marker_placement(self, target_landmark: str) -> dict:
        """Captures a post-click screenshot to verify destination marker placement."""
        img_bytes, width, height = self.capture_screen()
        if not self.client or not img_bytes:
            return {"accurate": False, "marker_visible": False, "correction_needed": False, "correct_point": None}

        prompt = f"""
        Inspect this in-game map screenshot after a waypoint click was issued.
        Target Landmark: "{target_landmark}"

        Check whether an active destination beacon, waypoint pin, glowing beacon, or navigation path line is visibly present on or near the target landmark.
        Return strictly a JSON object:
        {{
            "marker_visible": true or false,
            "accurate": true or false,
            "correction_needed": true or false,
            "correct_point": [y_center_normalized, x_center_normalized] or null
        }}
        - If NO waypoint pin or navigation beacon is visible on the map, set "marker_visible": false and "accurate": false.
        - If a waypoint pin is clearly visible on or near the landmark, set "marker_visible": true and "accurate": true.
        - If a pin was placed far from the landmark, set "marker_visible": true, "accurate": false, "correction_needed": true, and provide the correct point.
        """
        try:
            return self._call_vision_json(img_bytes, prompt)
        except Exception as e:
            print(f"[WARN] [GAME_NAV] Marker verification parse error: {e}")
            return {"accurate": False, "marker_visible": False, "correction_needed": False, "correct_point": None}

    def check_is_map_explicit(self, img_bytes: bytes) -> Optional[bool]:
        """
        Determines if screen is confirmed map interface.
        Returns True if map, False if confirmed NOT map, or None if indeterminate.
        """
        if not self.client or not img_bytes:
            return None
        try:
            prompt = (
                "Inspect this screenshot from a video game. Is this currently an in-game world map, "
                "territory map, or navigation map interface? "
                "Return strictly JSON: {\"is_map\": true} or {\"is_map\": false}"
            )
            data = self._call_vision_json(img_bytes, prompt)
            if "is_map" in data:
                return bool(data["is_map"])
            return None
        except Exception as e:
            print(f"[DEBUG] [GAME_NAV] Map screen explicit check: {e}")
            return None

    def is_map_screen(self, img_bytes: bytes) -> bool:
        """Determines if the captured screen is already displaying an in-game world map."""
        res = self.check_is_map_explicit(img_bytes)
        return bool(res) if res is not None else False

    def pan_and_place_marker_verified(
        self,
        landmark_description: str,
        general_direction: Optional[str] = None,
        click_button: Optional[str] = None,
        pan_button: Optional[str] = None,
        use_gamepad: Optional[bool] = None,
        target_process: Optional[str] = None
    ) -> Dict[str, Any]:
        """Executes map opening (keyboard default), panning, visual localization, click, and verification."""
        ctrl = getattr(self, "map_controls", {}) or {}
        if click_button is None:
            marker_method = ctrl.get("marker_method")
            click_button = "left" if marker_method == "left_click" else "right"
        if pan_button is None:
            pan_method = ctrl.get("pan_method")
            if pan_method == "left_drag":
                pan_button = "left"
            elif pan_method == "right_drag":
                pan_button = "right"
            else:
                pan_button = "left" if ctrl else "right"
        if use_gamepad is None:
            use_gamepad = ctrl.get("preferred_input") == "gamepad"
        # 0. Ensure game window has foreground focus so DirectInput / SendInput registers
        proc = target_process or getattr(self, "target_process", "")
        if proc:
            try:
                from tools.os_controls import focus_window
                focus_window(proc)
                time.sleep(0.15)
            except Exception as e:
                print(f"[DEBUG] [GAME_NAV] Focus window attempt: {e}")

        # 1. Open the in-game map if not already displayed
        initial_frame, width, height = self.capture_screen()
        already_map = self.is_map_screen(initial_frame)
        if not already_map:
            opened = False
            if use_gamepad:
                opened = send_gamepad_button("view", duration_sec=0.1)
            if not opened:
                send_directinput_key("m", duration_sec=0.1)
            time.sleep(0.6)

            # Post-open map screen validation
            check_frame, _, _ = self.capture_screen()
            is_map_result = self.check_is_map_explicit(check_frame)
            if is_map_result is False:
                # If explicit false, re-focus window and retry keypress once
                if proc:
                    try:
                        from tools.os_controls import focus_window
                        focus_window(proc)
                        time.sleep(0.15)
                    except Exception:
                        pass
                send_directinput_key("m", duration_sec=0.15)
                time.sleep(0.6)
                check_frame2, _, _ = self.capture_screen()
                if self.check_is_map_explicit(check_frame2) is False:
                    return {
                        "status": "map_not_open",
                        "success": False,
                        "landmark": landmark_description,
                        "message": (
                            "Attempted to open the map with 'm', but the map screen was not detected. "
                            "Please ensure the game window is focused and 'm' is bound to the map (or open the map manually)."
                        )
                    }
        else:
            print("[INFO] [GAME_NAV] Map screen is already open. Proceeding directly to navigation.")

        # 2. Panning towards target direction if requested
        if general_direction:
            self.pan_map(general_direction, distance=400, button=pan_button)

        # 3. Locate landmark on viewport (enrich with regional web hint on miss for fog-of-war dead-reckoning)
        coords = self.locate_landmark_point(landmark_description)
        web_hint = ""
        if not coords and self.game_name:
            web_hint = self._fetch_landmark_web_context(landmark_description)
            if web_hint:
                coords = self.locate_landmark_point(landmark_description, web_context=web_hint)
        if not coords and not general_direction:
            for test_dir in ["north", "south", "east", "west"]:
                self.pan_map(test_dir, distance=300, button=pan_button)
                coords = (
                    self.locate_landmark_point(landmark_description, web_context=web_hint)
                    if web_hint
                    else self.locate_landmark_point(landmark_description)
                )
                if coords:
                    break

        if not coords:
            return {
                "status": "not_found",
                "success": False,
                "message": f"Could not find '{landmark_description}' on the visible map viewport. Try panning toward the region or zooming out."
            }

        # 4. Click target coordinates (or gamepad button if in controller mode)
        px, py = coords
        move_mouse_absolute(px, py)
        time.sleep(0.15)
        if use_gamepad:
            send_gamepad_button("a", duration_sec=0.1)
        else:
            click_mouse_button(click_button, hold_duration=0.08)

        # 5. Closed-Loop Verification & Auto-Correction
        time.sleep(0.4)
        verification = self.verify_marker_placement(landmark_description)

        marker_seen = (
            verification.get("marker_visible", False)
            or verification.get("accurate", False)
            or verification.get("correction_needed", False)
        )
        if not marker_seen:
            # First attempt may not have registered. Try alternate input
            if use_gamepad:
                print(f"[INFO] [GAME_NAV] Marker not confirmed with Gamepad 'A'. Retrying with Gamepad 'X' at ({px}, {py})...")
                send_gamepad_button("x", duration_sec=0.1)
            else:
                alt_btn = "left" if click_button == "right" else "right"
                print(f"[INFO] [GAME_NAV] Marker not confirmed with {click_button} click. Retrying with {alt_btn} click at ({px}, {py})...")
                move_mouse_absolute(px, py)
                time.sleep(0.1)
                click_mouse_button(alt_btn, hold_duration=0.1)
            time.sleep(0.4)
            verification = self.verify_marker_placement(landmark_description)
            marker_seen = (
                verification.get("marker_visible", False)
                or verification.get("accurate", False)
                or verification.get("correction_needed", False)
            )

        if not marker_seen:
            print(f"[WARN] [GAME_NAV] Waypoint marker was not detected after attempts at ({px}, {py}).")
            return {
                "status": "marker_unverified",
                "success": False,
                "placed_at": [px, py],
                "landmark": landmark_description,
                "message": f"Issued waypoint placement at coordinates ({px}, {py}) for '{landmark_description}', but no active waypoint marker was detected on the map. The game may require a specific key or button to set waypoints."
            }

        # Auto-correction if marker landed far from landmark
        if verification.get("correction_needed") and verification.get("correct_point"):
            correct_pt = verification["correct_point"]
            if isinstance(correct_pt, (list, tuple)) and len(correct_pt) == 2:
                _, width, height = self.capture_screen()
                c_y_norm, c_x_norm = correct_pt
                corr_px, corr_py = self._norm_to_screen_px(c_x_norm, c_y_norm, width, height)

                move_mouse_absolute(corr_px, corr_py)
                time.sleep(0.15)
                if use_gamepad:
                    send_gamepad_button("a", duration_sec=0.1)
                else:
                    click_mouse_button(click_button, hold_duration=0.08)
                time.sleep(0.3)

                return {
                    "status": "success",
                    "success": True,
                    "placed_at": [corr_px, corr_py],
                    "initial_point": [px, py],
                    "landmark": landmark_description,
                    "verified": True,
                    "auto_corrected": True,
                    "message": f"Waypoint placed and auto-corrected to ({corr_px}, {corr_py}) for '{landmark_description}'."
                }

        return {
            "status": "success",
            "success": True,
            "placed_at": [px, py],
            "landmark": landmark_description,
            "verified": True,
            "auto_corrected": False,
            "message": f"Waypoint successfully placed and verified at ({px}, {py}) for '{landmark_description}'."
        }

    def pan_and_place_marker(
        self,
        landmark_description: str,
        general_direction: Optional[str] = None,
        click_button: Optional[str] = None,
        use_gamepad: Optional[bool] = None,
        target_process: Optional[str] = None
    ) -> Dict[str, Any]:
        """Wrapper maintaining backwards-compatibility, executing verified placement."""
        return self.pan_and_place_marker_verified(
            landmark_description=landmark_description,
            general_direction=general_direction,
            click_button=click_button,
            use_gamepad=use_gamepad,
            target_process=target_process
        )
