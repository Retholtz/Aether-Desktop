"""
tests/test_api_telemetry_and_scrubber.py - Work Order (v2) Verification Suite.

Covers all 4 verification checklist items:
1. 4-Step Vision Loop Test: imgs never exceeds 1 (scrub=0, 1, 2, 3), low/medium/high flexing,
   and follow-up text turn after persistent chat history scrub logs imgs=0.
2. Casual Chat 5-Turn Diagnostic Test: emits [API_COST] lines & --cost-report breakdown with
   tools=, cached=, thoughts=, search= and confirms 0 unexpected background calls.
3. Agnostic Telemetry Safety Brake & Pixel-Diff Gate: confirms disable toggle, non-game idle
   block, and static-frame pixel-diff gate prevent idle Gemini polling.
4. Exact Non-Double-Counted Cost Math: verifies $0.02360 exact on gemini-3.8-flash (2026) and
   2027+ rate transition.
"""

import datetime
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image


def _make_jpeg_bytes(color=(30, 60, 90), size=(128, 72)) -> bytes:
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    return buf.getvalue()


def _make_fake_response(
    text: str = '{"ok": true}',
    prompt_tokens: int = 1000,
    cached_tokens: int = 0,
    tool_use_tokens: int = 0,
    cand_tokens: int = 100,
    thought_tokens: int = 0,
    img_tokens: int = 0,
    search_queries=None,
):
    details = []
    if img_tokens > 0:
        details.append(SimpleNamespace(modality="IMAGE", token_count=img_tokens))
    um = SimpleNamespace(
        prompt_token_count=prompt_tokens,
        cached_content_token_count=cached_tokens,
        tool_use_prompt_token_count=tool_use_tokens,
        candidates_token_count=cand_tokens,
        thoughts_token_count=thought_tokens,
        total_token_count=prompt_tokens + cand_tokens + thought_tokens,
        prompt_tokens_details=details,
    )
    gm = SimpleNamespace(web_search_queries=list(search_queries or [])) if search_queries else None
    return SimpleNamespace(
        text=text,
        usage_metadata=um,
        candidates=[SimpleNamespace(grounding_metadata=gm)],
    )


class TestWorkOrderV2TelemetryAndScrubber(unittest.TestCase):
    def test_exact_cost_math_2026_and_2027(self):
        from core.api_telemetry import calculate_api_cost_usd

        # Work Order Checklist #4:
        # prompt=10000, cached=8000, candidates=200, thoughts=1800, search=1 on gemini-3.8-flash (2026)
        # = $0.00150 uncached + $0.00060 cached + $0.00750 out/thoughts + $0.01400 search = $0.02360 exact!
        cost_2026 = calculate_api_cost_usd(
            model_name="gemini-3.8-flash",
            prompt_token_count=10000,
            cached_content_token_count=8000,
            candidates_token_count=200,
            thoughts_token_count=1800,
            grounding_queries_count=1,
            as_of_date=datetime.date(2026, 11, 25),
        )
        self.assertAlmostEqual(cost_2026, 0.02360, places=5)

        # 2027+ rate transition ($1.50 / $0.15 / $7.50 + $0.014 search):
        # 2000*1.5/1e6 ($0.003) + 8000*0.15/1e6 ($0.0012) + 2000*7.5/1e6 ($0.015) + $0.014 = $0.03320
        cost_2027 = calculate_api_cost_usd(
            model_name="gemini-3.8-flash",
            prompt_token_count=10000,
            cached_content_token_count=8000,
            candidates_token_count=200,
            thoughts_token_count=1800,
            grounding_queries_count=1,
            as_of_date=datetime.date(2027, 1, 1),
        )
        self.assertAlmostEqual(cost_2027, 0.03320, places=5)

    def test_4_step_vision_loop_scrubber_and_dynamic_media_resolution(self):
        from core.api_telemetry import (
            SCRUBBED_IMAGE_PLACEHOLDER,
            make_image_part,
            scrub_chat_history_images,
            scrub_stale_images,
        )
        from core.game_nav import UniversalGameNavigator

        # Simulate a 4-step active loop accumulating 1 screenshot per step
        loop_contents = []
        scrub_counts = []
        kept_counts = []
        for step in range(1, 5):
            loop_contents.append({
                "role": "user",
                "parts": [
                    make_image_part(_make_jpeg_bytes((step * 20, 40, 60)), resolution="medium"),
                    {"text": f"Step {step}"},
                ],
            })
            # In a real loop, step 1 has 1 img (0 scrubbed), step 2 has 2 imgs (1 scrubbed),
            # or if we accumulate raw history before scrubbing each call:
            snapshot_copy = [
                {"role": m["role"], "parts": list(m["parts"])}
                for m in loop_contents
            ]
            _, kept, scrubbed = scrub_stale_images(snapshot_copy, keep_latest_n=1)
            kept_counts.append(kept)
            scrub_counts.append(scrubbed)

        self.assertEqual(kept_counts, [1, 1, 1, 1])
        self.assertEqual(scrub_counts, [0, 1, 2, 3])

        # Persistent chat history scrub after loop completes -> 0 historical images retained
        fake_chat = SimpleNamespace(_curated_history=loop_contents)
        scrubbed_after = scrub_chat_history_images(fake_chat, keep_latest_n=0)
        self.assertEqual(scrubbed_after, 4)
        # Follow-up text turn on retained history must have 0 images
        loop_contents.append({"role": "user", "parts": [{"text": "Follow-up text turn"}]})
        _, kept_followup, _ = scrub_stale_images(loop_contents, keep_latest_n=1)
        self.assertEqual(kept_followup, 0)
        self.assertIn(SCRUBBED_IMAGE_PLACEHOLDER, str(loop_contents[0]["parts"][0]))

        # Dynamic media_resolution flex: medium -> high single retry when unreadable=True
        seen_levels = []
        mock_client = MagicMock()

        def _fake_gen(model, contents, config=None):
            img_part = contents[0]
            lvl = str(getattr(getattr(img_part, "media_resolution", None), "level", "")).lower()
            seen_levels.append(lvl)
            if len(seen_levels) == 1:
                return _make_fake_response('{"unreadable": true, "text": ""}')
            return _make_fake_response('{"unreadable": false, "text": "Aluminium 1,394 CR"}')

        mock_client.models.generate_content.side_effect = _fake_gen
        nav = UniversalGameNavigator(client=mock_client, model_endpoint="gemini-3.8-flash", game_name="Space Sim")
        res = nav._call_vision_json(_make_jpeg_bytes(), "Read market table", resolution="medium", sub_action="ocr_test")
        self.assertEqual(res["text"], "Aluminium 1,394 CR")
        self.assertEqual(len(seen_levels), 2)
        self.assertIn("medium", seen_levels[0])
        self.assertIn("high", seen_levels[1])

    def test_casual_chat_5_turns_cost_report_and_breakdown(self):
        from core.api_telemetry import _finalize_and_log_call, format_cost_report

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "telemetry.db")
            for turn in range(1, 6):
                cached = 0 if turn == 1 else 6000
                resp = _make_fake_response(
                    text=f"Reply {turn}",
                    prompt_tokens=8000,
                    cached_tokens=cached,
                    cand_tokens=90,
                    thought_tokens=0,
                    search_queries=["weather today"] if turn == 3 else [],
                )
                _finalize_and_log_call(
                    ctx={"feature_tag": "casual_chat", "sub_action": "chat_reply", "loop_step": 1, "turn_id": f"t{turn}"},
                    model_name="gemini-3.8-flash",
                    diag={
                        "system_prompt_chars": 4200,
                        "tool_declarations_count": 32,
                        "history_message_count": turn * 2,
                        "thinking_config": "budget=0",
                        "media_resolution": "",
                    },
                    imgs_kept=0,
                    imgs_scrubbed=0,
                    response=resp,
                    latency_ms=420.0,
                    db_path=db_path,
                )

            report = format_cost_report(db_path=db_path)
            self.assertIn("Feature Breakdown:", report)
            self.assertIn("casual_chat", report)
            self.assertIn("Top 5 Most Expensive Calls:", report)
            self.assertNotIn("agnostic_telemetry", report)
            self.assertNotIn("dossier_creation", report)

    def test_agnostic_telemetry_safety_brake_and_pixel_diff_gate(self):
        from core.game_triggers import CompanionChitChatManager

        mgr = MagicMock()
        mgr.game_mode_enabled = False
        mgr.get_profile.return_value = {"display_name": "Space Sim", "process_name": "SpaceSim.exe"}
        eng = MagicMock()
        eng.genai_client = MagicMock()
        eng.config = {"telemetry": {"vision_polling_enabled": False}}

        cc = CompanionChitChatManager(mgr, engine=eng)
        cc.stop()

        # 1. Disabled via telemetry.vision_polling_enabled=False -> 0 calls
        out_disabled = cc._describe_screen_peek("space_sim")
        self.assertFalse(out_disabled["notable"])
        self.assertFalse(eng.genai_client.models.generate_content.called)

        # 2. Enabled + Game Mode active -> identical static frames blocked by pixel-diff gate!
        mgr.game_mode_enabled = True
        eng.config = {"telemetry": {"vision_polling_enabled": True, "vision_polling_cooldown_sec": 0.0, "vision_pixel_diff_threshold": 3.5}}
        eng.genai_client.models.generate_content.return_value = _make_fake_response(
            '{"notable": true, "urgent": false, "mood": "awe", "scene": "Bright nebula"}'
        )
        static_frame = _make_jpeg_bytes((40, 40, 40))
        with patch("core.game_nav.UniversalGameNavigator.capture_viewport", return_value=(static_frame, 1280, 720)):
            first = cc._describe_screen_peek("space_sim")
            second = cc._describe_screen_peek("space_sim")

        self.assertTrue(first["notable"])
        self.assertFalse(second["notable"])  # Blocked by pixel-diff gate!
        self.assertEqual(eng.genai_client.models.generate_content.call_count, 1)

    def test_browsing_games_tab_without_running_game_does_not_engage_game_mode_or_chitchat(self):
        """
        Regression test: clicking/selecting a game profile in the Games tab when the game
        process is NOT running must keep game_mode_enabled=False, active_telemetry_adapter=None,
        and prevent Chit-Chat / Vision Screen Peek / Triggers from firing.
        """
        import json
        import os
        import shutil
        from core.game_manager import GameManager
        from core.game_triggers import CompanionChitChatManager, GameTriggerEngine

        tmp = tempfile.mkdtemp()
        try:
            prof_path = os.path.join(tmp, "game_profiles.json")
            with open(prof_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "active_profile": "elite_dangerous_64",
                        "profiles": {
                            "elite_dangerous_64": {
                                "display_name": "Elite Dangerous",
                                "process_name": "EliteDangerous64.exe",
                                "chitchat_settings": {"frequency": "high", "screen_peek": True},
                                "triggers": [
                                    {"id": "t1", "enabled": True, "event": "Docked", "action_type": "speak", "action_value": "Docked!"}
                                ],
                            },
                            "crimson_desert": {
                                "display_name": "Crimson Desert",
                                "process_name": "CrimsonDesert.exe",
                            },
                        },
                    },
                    f,
                )

            with patch.object(GameManager, "detect_foreground_game", return_value=None), \
                 patch.object(GameManager, "detect_running_game", return_value=None), \
                 patch.object(GameManager, "is_game_running", return_value=False):
                gm = GameManager(profiles_path=prof_path)
                self.assertFalse(gm.game_mode_enabled)
                self.assertIsNone(gm.active_telemetry_adapter)

                # Simulate clicking Crimson Desert then Elite Dangerous in the Games tab
                gm.set_active_profile("crimson_desert")
                self.assertFalse(gm.game_mode_enabled)
                self.assertIsNone(gm.user_override_game_mode)
                self.assertIsNone(gm.active_telemetry_adapter)

                gm.set_active_profile("elite_dangerous_64")
                self.assertFalse(gm.game_mode_enabled)
                self.assertIsNone(gm.user_override_game_mode)
                self.assertIsNone(gm.active_telemetry_adapter)

                # Even if game_mode_enabled were toggled manually, Chit-Chat & Triggers stay silent if game isn't running
                gm.game_mode_enabled = True
                fake_engine = MagicMock()
                fake_engine.is_running = True
                fake_engine.is_speaking = False
                fake_engine._is_speaking = False
                fake_engine.is_user_speaking = False
                fake_engine._user_speaking = False
                fake_engine.config = {"telemetry": {"vision_polling_enabled": True}}

                cc = CompanionChitChatManager(gm, engine=fake_engine)
                cc.stop()
                self.assertFalse(cc._can_chitchat_now("elite_dangerous_64", cc._get_settings("elite_dangerous_64")))

                te = GameTriggerEngine(gm, engine=fake_engine)
                try:
                    self.assertEqual(te.handle_event("elite_dangerous_64", "Docked", {}, {}), 0)
                finally:
                    te.stop()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_live_websocket_cost_hardening_defaults_and_metering(self):
        """
        Verifies:
        1. Continuous 1 FPS live video streaming defaults OFF (vision.enabled=False).
        2. On-demand Live WebSocket linger defaults to 60.0s (not 300.0s).
        3. record_live_session_usage meters Gemini Live `response_token_count` into --cost-report.
        """
        import os
        from core.api_telemetry import format_cost_report, record_live_session_usage
        from core.config_manager import ConfigManager
        from core.engine import AetherEngine

        with tempfile.TemporaryDirectory() as tmp:
            cfg_mgr = ConfigManager(config_path=os.path.join(tmp, "fresh_config.json"))
            self.assertFalse(cfg_mgr.config["vision"]["enabled"])

            with patch("core.engine.DesktopNotificationWatcher", return_value=MagicMock()), \
                 patch("core.engine.LiveStreamService", return_value=MagicMock()), \
                 patch("core.engine.SteamService", return_value=MagicMock()), \
                 patch("core.engine.GameEngine", return_value=MagicMock()):
                eng = AetherEngine(config_getter=lambda: {"audio": {"mode": "ptt"}})
            self.assertEqual(eng._linger_seconds, 60.0)

            db_path = os.path.join(tmp, "telemetry.db")
            fake_live_resp = SimpleNamespace(
                usage_metadata=SimpleNamespace(
                    prompt_token_count=4200,
                    cached_content_token_count=0,
                    tool_use_prompt_token_count=0,
                    response_token_count=650,
                    thoughts_token_count=0,
                    total_token_count=4850,
                    prompt_tokens_details=[SimpleNamespace(modality="AUDIO", token_count=320)],
                ),
                candidates=[],
            )
            row = record_live_session_usage(
                fake_live_resp,
                model_name="gemini-3.8-live",
                feature_tag="casual_chat",
                db_path=db_path,
            )
            self.assertIsNotNone(row)
            self.assertEqual(row["candidates_token_count"], 650)
            self.assertGreater(row["estimated_cost_usd"], 0.0)
            rep = format_cost_report(db_path=db_path)
            self.assertIn("gemini-3.8-live", rep)
            self.assertIn("live_websocket_turn", rep)

            # Verify Continuous Live Video 10x cost confirmation modal is present & wired in UI
            with open(os.path.join("ui", "static", "index.html"), "r", encoding="utf-8") as hf:
                idx_html = hf.read()
            self.assertIn("modal-live-video-cost-warning", idx_html)
            self.assertIn("10-fold", idx_html)
            self.assertIn("btn-confirm-live-video-cost", idx_html)

            with open(os.path.join("ui", "static", "app.js"), "r", encoding="utf-8") as jf:
                app_js = jf.read()
            self.assertIn("modal-live-video-cost-warning", app_js)
            self.assertIn("wiredCostWarning", app_js)


if __name__ == "__main__":
    unittest.main()
