"""
Unit tests for multi-workspace session isolation (General, Project, Campaign).
Verifies that conversation history, system prompt generation, and context invalidation
remain strictly separated across workspaces with zero bleed.
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, AsyncMock

from core.session_manager import SessionManager
from core.project_service import ProjectService
from core.gui_bridge import GuiBridge


class TestWorkspaceSessions(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.sessions_dir = os.path.join(self.temp_dir, "sessions")
        self.projects_dir = os.path.join(self.temp_dir, "projects")
        self.manifest_path = os.path.join(self.temp_dir, "projects.json")

        self.session_mgr = SessionManager(sessions_dir=self.sessions_dir)
        self.project_svc = ProjectService(base_dir=self.projects_dir, manifest_path=self.manifest_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_project_system_prompt_builder(self):
        res = self.project_svc.create_project(
            project_id="river_bend",
            name="River Bend Renovation",
            description="Kitchen and patio renovation project.",
            instructions="Focus on cherry butcher block counters and plumbing fixtures."
        )
        self.assertEqual(res["status"], "success")

        prompt = self.project_svc.build_project_system_prompt("river_bend")
        self.assertIn("[ACTIVE PROJECT WORKSPACE: River Bend Renovation (river_bend)]", prompt)
        self.assertIn("Kitchen and patio renovation project.", prompt)
        self.assertIn("Focus on cherry butcher block counters", prompt)

    def test_session_manager_workspace_isolation(self):
        # 1. Start in general workspace
        self.session_mgr.switch_session("general", "default", "Desktop Assistant")
        self.session_mgr.append_turn("user", "Hello general assistant")
        self.session_mgr.append_turn("model", "Hello! I am your desktop assistant.")
        self.assertEqual(len(self.session_mgr.history), 2)

        # 2. Switch to Project workspace
        proj_history = self.session_mgr.switch_session("project", "river_bend", "River Bend")
        self.assertEqual(len(proj_history), 0)  # Brand new session, no bleed!
        self.session_mgr.append_turn("user", "What are the specs for the butcher block?")
        self.session_mgr.append_turn("model", "The John Boos block is 4 inches thick.")
        self.assertEqual(len(self.session_mgr.history), 2)

        # 3. Switch to Campaign workspace
        camp_history = self.session_mgr.switch_session("campaign", "the_iron_reach", "The Iron Reach")
        self.assertEqual(len(camp_history), 0)  # Isolated!
        self.session_mgr.append_turn("user", "I cast Detect Magic on the stone altar.")
        self.session_mgr.append_turn("model", "A faint abjuration aura hums beneath the moss.")

        # 4. Switch back to general workspace - history is preserved!
        restored_general = self.session_mgr.switch_session("general", "default", "Desktop Assistant")
        self.assertEqual(len(restored_general), 2)
        self.assertEqual(restored_general[0]["parts"][0]["text"], "Hello general assistant")

        # 5. Switch back to project workspace - project history is preserved!
        restored_proj = self.session_mgr.switch_session("project", "river_bend", "River Bend")
        self.assertEqual(len(restored_proj), 2)
        self.assertEqual(restored_proj[0]["parts"][0]["text"], "What are the specs for the butcher block?")

    def test_gui_bridge_format_and_workspace_history(self):
        mock_engine = MagicMock()
        mock_engine.session_mgr = self.session_mgr
        mock_engine.active_workspace_type = "general"
        mock_engine.active_workspace_context_id = "default"

        bridge = GuiBridge(engine=mock_engine)

        # Test format history turns
        raw = [
            {"role": "user", "parts": [{"text": "Hello world"}], "timestamp": "2026-11-02T12:00:00Z"},
            {"role": "model", "parts": [{"text": "Hello! How can I help?"}]}
        ]
        formatted = bridge._format_history_turns(raw)
        self.assertEqual(len(formatted), 2)
        self.assertEqual(formatted[0]["type"], "user")
        self.assertEqual(formatted[0]["content"], "Hello world")
        self.assertEqual(formatted[1]["type"], "assistant")
        self.assertEqual(formatted[1]["content"], "Hello! How can I help?")

        # Populate a session file on disk for project
        self.session_mgr.switch_session("project", "test_proj")
        self.session_mgr.append_turn("user", "Project question")
        self.session_mgr.append_turn("model", "Project answer")

        res = bridge.get_workspace_history("project", "test_proj")
        self.assertEqual(res["status"], "success")
        self.assertEqual(len(res["history"]), 2)
        self.assertEqual(res["history"][0]["content"], "Project question")

        # Clear workspace history
        clear_res = bridge.clear_workspace_history("project", "test_proj")
        self.assertEqual(clear_res["status"], "success")
        post_clear = bridge.get_workspace_history("project", "test_proj")
        self.assertEqual(len(post_clear["history"]), 0)

    def test_auto_start_engine_on_typed_and_workspace_messages(self):
        mock_engine = MagicMock()
        mock_engine.is_running = False
        mock_engine.active_workspace_type = "general"
        mock_engine.active_workspace_context_id = "default"
        mock_engine.send_text = AsyncMock()

        bridge = GuiBridge(engine=mock_engine)

        # 1. Sending typed message when engine is stopped should auto-start engine
        res = bridge.send_text_message("Hello Aether, are you there?")
        self.assertTrue(res["success"])
        mock_engine.start.assert_called_once()

        # 2. Reset and test workspace message auto-starts engine
        mock_engine.start.reset_mock()
        mock_engine.is_running = False
        res_ws = bridge.send_workspace_message("project", "river_bend", "Can you review the butcher block?")
        self.assertTrue(res_ws["success"])
        mock_engine.start.assert_called_once()
        mock_engine.set_active_workspace.assert_called_with("project", "river_bend")

    def test_ttrpg_dice_mode_toggle(self):
        from core.ttrpg_service import TTRPGService
        test_manifest_path = os.path.join(self.temp_dir, "test_campaigns.json")
        svc = TTRPGService(data_dir=self.temp_dir, manifest_path=test_manifest_path)
        svc.manifest = {
            "active_campaign": "test_camp",
            "campaigns": {
                "test_camp": {
                    "title": "Test Campaign",
                    "system_preset": "heroic_d20",
                    "dice_mode": "digital"
                }
            }
        }
        svc.active_state = {}

        # Default is digital
        self.assertEqual(svc.get_dice_mode("test_camp"), "digital")
        prompt_dig = svc.build_gm_context_prompt()
        self.assertIn("DIGITAL DICE (AETHER ROLLS FOR PLAYERS)", prompt_dig)

        # Switch to physical
        set_res = svc.set_dice_mode("physical", "test_camp")
        self.assertEqual(set_res["status"], "success")
        self.assertEqual(svc.get_dice_mode("test_camp"), "physical")
        prompt_phys = svc.build_gm_context_prompt()
        self.assertIn("PHYSICAL TABLETOP DICE (PLAYERS ROLL AT THE TABLE)", prompt_phys)
        self.assertIn("DO NOT roll dice for the player using `roll_dice`", prompt_phys)

        # GuiBridge integration
        mock_engine = MagicMock()
        mock_engine.active_workspace_type = "campaign"
        bridge = GuiBridge(engine=mock_engine)
        bridge.ttrpg_service = svc
        b_res = bridge.set_ttrpg_dice_mode("physical", "test_camp")
        self.assertEqual(b_res["status"], "success")
        mock_engine.set_active_workspace.assert_called_with("campaign", "test_camp")

    def test_ttrpg_roleplay_chat_locking(self):
        """Verifies that sending messages to campaign workspace locks session to TTRPG and frames actions."""
        from core.ttrpg_service import TTRPGService
        test_manifest_path = os.path.join(self.temp_dir, "test_camp_manifest.json")
        svc = TTRPGService(data_dir=self.temp_dir, manifest_path=test_manifest_path)
        svc.manifest = {
            "active_campaign": "elf_hunt",
            "campaigns": {
                "elf_hunt": {
                    "title": "Elf Hunt",
                    "system_preset": "heroic_d20",
                    "difficulty": "helpful"
                }
            }
        }
        svc.active_state = {
            "latest_recap": "Joe found a locket and contemplates speaking to Chief Olaf.",
            "current_location": "Village Hall",
            "characters": {}
        }

        mock_engine = MagicMock()
        mock_engine.is_running = True
        mock_engine.active_workspace_type = "general"
        mock_engine.active_workspace_context_id = "default"
        mock_engine.send_text = AsyncMock()

        bridge = GuiBridge(engine=mock_engine)
        bridge.ttrpg_service = svc

        # Player sends action from Role Play tab chat box: "Speak to Olaf"
        res = bridge.send_workspace_message("campaign", "elf_hunt", "Speak to Olaf")
        self.assertTrue(res["success"])

        # Engine must be switched to campaign and TTRPG session marked active
        mock_engine.set_active_workspace.assert_called_with("campaign", "elf_hunt")
        mock_engine.set_ttrpg_session_active.assert_called_with(True)

        # Verify prompt delivered to model has GM directive and display_text is clean
        mock_engine.send_text.assert_called_once()
        sent_prompt = mock_engine.send_text.call_args[0][0]
        kwargs = mock_engine.send_text.call_args[1]
        self.assertIn("[TABLETOP ROLEPLAY ACTION — CAMPAIGN: Elf Hunt]", sent_prompt)
        self.assertIn("Player Action / Dialogue: Speak to Olaf", sent_prompt)
        self.assertIn("GAME MASTER DIRECTIVE", sent_prompt)
        self.assertEqual(kwargs.get("display_text"), "Speak to Olaf")

        # Verify history formatter strips framing for player view
        raw_history = [
            {"role": "user", "parts": [{"text": sent_prompt}], "timestamp": "2026-11-07T12:00:00Z"},
            {"role": "model", "parts": [{"text": "Chief Olaf looks up with a furrowed brow."}]}
        ]
        formatted = bridge._format_history_turns(raw_history)
        self.assertEqual(len(formatted), 2)
        self.assertEqual(formatted[0]["content"], "Speak to Olaf")
        self.assertEqual(formatted[1]["content"], "Chief Olaf looks up with a furrowed brow.")

    def test_start_or_resume_ttrpg_campaign_locking(self):
        """Verifies start_or_resume_ttrpg_campaign starts engine, activates TTRPG mode, and builds prompt."""
        from core.ttrpg_service import TTRPGService
        test_manifest_path = os.path.join(self.temp_dir, "test_resume_manifest.json")
        svc = TTRPGService(data_dir=self.temp_dir, manifest_path=test_manifest_path)
        svc.manifest = {
            "active_campaign": "elf_hunt",
            "campaigns": {
                "elf_hunt": {
                    "title": "Elf Hunt",
                    "system_preset": "heroic_d20",
                    "difficulty": "helpful",
                    "dice_mode": "digital"
                }
            }
        }
        svc.active_state = {
            "latest_recap": "Joe found a tarnished locket. They face the decision of reporting to Chief Olaf.",
            "current_location": "Investigation scene",
            "characters": {}
        }

        mock_engine = MagicMock()
        mock_engine.is_running = True
        mock_engine.active_workspace_type = "campaign"
        mock_engine.active_workspace_context_id = "elf_hunt"
        mock_engine.send_text = AsyncMock()

        bridge = GuiBridge(engine=mock_engine)
        bridge.ttrpg_service = svc

        res = bridge.start_or_resume_ttrpg_campaign()
        self.assertTrue(res["success"])
        self.assertEqual(res["session_id"], "campaign_elf_hunt")
        mock_engine.set_ttrpg_session_active.assert_called_with(True)

        sent_prompt = mock_engine.send_text.call_args[0][0]
        self.assertIn("Aether, please act as our Game Master and resume our campaign 'Elf Hunt'", sent_prompt)
        self.assertIn("at Investigation scene", sent_prompt)
        self.assertIn("Helpful / Beginner mode with digital dice", sent_prompt)


if __name__ == "__main__":
    unittest.main()
