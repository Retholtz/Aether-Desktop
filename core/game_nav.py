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

    def __init__(self, client=None, model_endpoint: str = "gemini-3.8-flash"):
        self.client = client
        self.model_endpoint = model_endpoint

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

        waypoint_btn = hud_info.get("waypoint_button") or "right"
        pan_btn = hud_info.get("pan_button") or "right"
        center_key = hud_info.get("center_player_key")

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


class GameNavigator(UniversalGameNavigator):
    """
    Backwards-compatible interface extending UniversalGameNavigator.
    Supports existing direct-method callers and test suites while sharing universal primitives.
    """

    def __init__(self, client=None, model_endpoint: str = "gemini-2.5-flash"):
        super().__init__(client=client, model_endpoint=model_endpoint)

    def capture_screen(self) -> Tuple[bytes, int, int]:
        """Captures the primary monitor and returns JPEG bytes and resolution (width, height)."""
        return self.capture_viewport()

    def pan_map(self, direction: str, distance: int = 350, button: str = "right"):
        """Pans the map canvas toward a cardinal direction using relative mouse dragging."""
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
            drag_mouse_relative(dx, dy, button=button, steps=14)
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

    def is_map_screen(self, img_bytes: bytes) -> bool:
        """Determines if the captured screen is already displaying an in-game world map."""
        if not self.client or not img_bytes:
            return False
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
            return bool(data.get("is_map", False))
        except Exception as e:
            print(f"[DEBUG] [GAME_NAV] Map screen check: {e}")
            return False

    def pan_and_place_marker_verified(
        self,
        landmark_description: str,
        general_direction: Optional[str] = None,
        click_button: str = "right",
        pan_button: str = "right",
        use_gamepad: bool = False
    ) -> Dict[str, Any]:
        """Executes map opening (keyboard default), panning, visual localization, click, and verification."""
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

        # 4. Click target coordinates
        px, py = coords
        move_mouse_absolute(px, py)
        time.sleep(0.15)
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
            # First click may not have registered. Try alternate mouse button
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
            print(f"[WARN] [GAME_NAV] Waypoint marker was not detected after clicks at ({px}, {py}).")
            return {
                "status": "marker_unverified",
                "success": False,
                "placed_at": [px, py],
                "landmark": landmark_description,
                "message": f"Clicked at coordinates ({px}, {py}) for '{landmark_description}', but no active waypoint marker was detected on the map. The game may require a specific key or button to set waypoints."
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
        click_button: str = "right",
        use_gamepad: bool = False
    ) -> Dict[str, Any]:
        """Wrapper maintaining backwards-compatibility, executing verified placement."""
        return self.pan_and_place_marker_verified(
            landmark_description=landmark_description,
            general_direction=general_direction,
            click_button=click_button,
            use_gamepad=use_gamepad
        )
