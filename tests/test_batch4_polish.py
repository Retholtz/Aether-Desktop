import unittest
from tools.os_controls import OSControls
from tools.dispatcher import ToolDispatcher

class TestBatch4Polish(unittest.TestCase):
    def setUp(self):
        self.os_controls = OSControls()
        self.dispatcher = ToolDispatcher()

    def test_tool_declarations_no_duplicates(self):
        declarations = self.dispatcher.get_core_declarations()
        names = [d["name"] for d in declarations]
        
        # Verify deduplication
        self.assertIn("list_available_skills", names)
        self.assertNotIn("list_saved_skills", names)
        
        self.assertIn("register_monitoring_task", names)
        self.assertNotIn("register_background_monitor", names)

    def test_clipboard_restoration(self):
        # Seed test clipboard
        seed_text = "PersistedUserSecret_123"
        self.os_controls._set_clipboard_safe(seed_text)

        # Trigger simulated fast typing
        large_text = "A" * 30
        res = self.os_controls.type_text(large_text)
        self.assertEqual(res["status"], "success")

        # Verify original clipboard was restored
        _, current_clip, _ = self.os_controls._get_clipboard_safe()
        self.assertEqual(current_clip, seed_text)

if __name__ == "__main__":
    unittest.main()
