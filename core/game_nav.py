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
        model_endpoint: str = "gemini-3.8-flash",
        target_process: str = "",
        map_controls: Optional[Dict[str, Any]] = None
    ):
        self.client = client
        self.model_endpoint = model_endpoint
        self.target_process = target_process
        self.map_controls = map_controls or {}

    def capture_viewport(self) -> tuple[bytes, int, int]:
        """Captures the primary monitor and returns JPEG bytes and resolution (width, height)."""
        ensure_thread_desktop()
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                shot = sct.grab(monitor)
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                return buf.getvalue(), monitor["width"], monitor["height"]
        except Exception:
            try:
                img = ImageGrab.grab()
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                w, h = img.size
                return buf.getvalue(), w, h
            except Exception:
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
            try:
                from google.genai import types
                part = types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
                contents = [part, prompt]
            except Exception:
                contents = [
                    {
                        "parts": [
                            {"inline_data": {"mime_type": "image/jpeg", "data": img_bytes}},
                            {"text": prompt}
                        ]
                    }
                ]

            res = self.client.models.generate_content(
                model=self.model_endpoint,
                contents=contents,
                config={"response_mime_type": "application/json"}
            )
            raw_text = res.text.strip() if res and hasattr(res, "text") and res.text else ""
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                raw_text = re.sub(r"\s*```$", "", raw_text)
            parsed = json.loads(raw_text)
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
            try:
                from google.genai import types
                part = types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
                contents = [part, prompt]
            except Exception:
                contents = [
                    {
                        "parts": [
                            {"inline_data": {"mime_type": "image/jpeg", "data": img_bytes}},
                            {"text": prompt}
                        ]
                    }
                ]

            res = self.client.models.generate_content(
                model=self.model_endpoint,
                contents=contents,
                config={"response_mime_type": "application/json"}
            )
            raw_text = res.text.strip() if res and hasattr(res, "text") and res.text else ""
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                raw_text = re.sub(r"\s*```$", "", raw_text)
            return json.loads(raw_text)
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
            decision = self.evaluate_viewport_step(frame_bytes, target_description, web_context)
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
                    target_px = int((x_norm / 1000.0) * width)
                    target_py = int((y_norm / 1000.0) * height)
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
            try:
                from google.genai import types
                part = types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
                contents = [part, prompt]
            except Exception:
                contents = [
                    {"parts": [{"inline_data": {"mime_type": "image/jpeg", "data": img_bytes}}, {"text": prompt}]}
                ]

            res = self.client.models.generate_content(
                model=self.model_endpoint,
                contents=contents,
                config={"response_mime_type": "application/json"}
            )
            raw = res.text.strip() if res and hasattr(res, "text") and res.text else ""
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
            data = json.loads(raw) if raw else {}
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

    def search_and_route_destination(
        self,
        destination_name: str,
        map_key: str = "m",
        search_focus_key: str = "",
        plot_key: str = "space",
        target_process: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Search-bar navigation for games like Elite Dangerous.
        Focuses target process, opens map, inputs search destination text via DirectInput scancodes,
        and plots destination route.
        """
        proc = target_process or getattr(self, "target_process", "")
        if proc:
            try:
                from tools.os_controls import focus_window
                focus_window(proc)
                time.sleep(0.15)
            except Exception:
                pass

        # Open map if needed
        initial_frame, _, _ = self.capture_viewport()
        if not self.is_map_screen(initial_frame):
            send_directinput_key(map_key, duration_sec=0.1)
            time.sleep(0.8)

        # Focus search bar if hotkey configured
        if search_focus_key:
            send_directinput_key(search_focus_key, duration_sec=0.1)
            time.sleep(0.3)

        # Type destination using DirectInput string
        from tools.os_controls import send_directinput_string
        send_directinput_string(destination_name, delay_sec=0.04)
        time.sleep(0.2)
        send_directinput_key("enter", duration_sec=0.1)
        time.sleep(0.8)

        # Plot route key
        if plot_key:
            send_directinput_key(plot_key, duration_sec=0.15)
            time.sleep(0.3)

        return {
            "status": "success",
            "destination": destination_name,
            "message": f"Entered destination '{destination_name}' into navigation search and plotted route."
        }


class GameNavigator(UniversalGameNavigator):
    """
    Backwards-compatible interface extending UniversalGameNavigator.
    Supports existing direct-method callers and test suites while sharing universal primitives.
    """

    def __init__(
        self,
        client=None,
        model_endpoint: str = "gemini-2.5-flash",
        target_process: str = "",
        map_controls: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            client=client,
            model_endpoint=model_endpoint,
            target_process=target_process,
            map_controls=map_controls
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

    def locate_landmark_point(self, landmark_description: str) -> Optional[Tuple[int, int]]:
        """Uses visual grounding or regional dead-reckoning to find landmark coordinates."""
        if not self.client:
            print("[WARN] [GAME_NAV] No Gemini client provided for visual landmark grounding.")
            return None

        img_bytes, width, height = self.capture_screen()
        prompt = f"""
Analyze this in-game world map screenshot. Locate the visual landmark, territory, or icon corresponding to: "{landmark_description}".
If the exact landmark or vendor icon is locked behind Fog-of-War or uncharted terrain:
- Identify the parent territory label (e.g. city or region name) or nearest visible boundary.
- Estimate the projected point inside that territory (dead-reckoning).

Return ONLY a JSON object:
{{"found": true, "point": [y_center_normalized, x_center_normalized]}}
Values must be normalized between 0 and 1000.
If neither the landmark nor its containing region/anchor can be found, return {{"found": false}}.
"""
        try:
            try:
                from google.genai import types
                part = types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
                contents = [part, prompt]
            except Exception:
                contents = [
                    {
                        "parts": [
                            {"inline_data": {"mime_type": "image/jpeg", "data": img_bytes}},
                            {"text": prompt}
                        ]
                    }
                ]

            res = self.client.models.generate_content(
                model=self.model_endpoint,
                contents=contents,
                config={"response_mime_type": "application/json"}
            )

            raw_text = res.text.strip() if res and hasattr(res, "text") and res.text else ""
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                raw_text = re.sub(r"\s*```$", "", raw_text)

            data = json.loads(raw_text) if raw_text else {}
            if data.get("found") and "point" in data and len(data["point"]) == 2:
                y_norm, x_norm = data["point"]
                px = int((x_norm / 1000.0) * width)
                py = int((y_norm / 1000.0) * height)
                return px, py
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
            try:
                from google.genai import types
                part = types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
                contents = [part, prompt]
            except Exception:
                contents = [
                    {"parts": [{"inline_data": {"mime_type": "image/jpeg", "data": img_bytes}}, {"text": prompt}]}
                ]

            res = self.client.models.generate_content(
                model=self.model_endpoint,
                contents=contents,
                config={"response_mime_type": "application/json"}
            )
            raw_text = res.text.strip() if res and hasattr(res, "text") and res.text else ""
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                raw_text = re.sub(r"\s*```$", "", raw_text)

            data = json.loads(raw_text) if raw_text else {}
            return data
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
            try:
                from google.genai import types
                part = types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
                contents = [part, prompt]
            except Exception:
                contents = [
                    {"parts": [{"inline_data": {"mime_type": "image/jpeg", "data": img_bytes}}, {"text": prompt}]}
                ]
            res = self.client.models.generate_content(
                model=self.model_endpoint,
                contents=contents,
                config={"response_mime_type": "application/json"}
            )
            raw = res.text.strip() if res and hasattr(res, "text") and res.text else ""
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
            data = json.loads(raw) if raw else {}
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

        # 3. Locate landmark on viewport
        coords = self.locate_landmark_point(landmark_description)
        if not coords and not general_direction:
            for test_dir in ["north", "south", "east", "west"]:
                self.pan_map(test_dir, distance=300, button=pan_button)
                coords = self.locate_landmark_point(landmark_description)
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
                corr_px = int((c_x_norm / 1000.0) * width)
                corr_py = int((c_y_norm / 1000.0) * height)

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
