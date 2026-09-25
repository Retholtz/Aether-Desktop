import asyncio
import unittest
from tools.screen_vision import capture_screen_image
from tools.dispatcher import (
    inspect_screen_context,
    INSPECT_SCREEN_CONTEXT_DECLARATION,
    get_all_tool_declarations,
    ToolDispatcher,
)
from core.engine import AetherEngine


class TestScreenVision(unittest.TestCase):
    def test_capture_screen_active_window(self):
        img_bytes, desc = capture_screen_image(target="active_window")
        self.assertIsInstance(img_bytes, bytes)
        self.assertGreater(len(img_bytes), 1000)  # > 1KB
        self.assertLess(len(img_bytes), 400 * 1024)  # < 400KB to guarantee low latency

    def test_capture_screen_full(self):
        img_bytes, desc = capture_screen_image(target="full_screen")
        self.assertIsInstance(img_bytes, bytes)
        self.assertGreater(len(img_bytes), 1000)
        self.assertIn("screen", desc.lower())

    def test_inspect_screen_context_tool_declaration(self):
        all_decls = get_all_tool_declarations()
        names = [d["name"] for d in all_decls]
        self.assertIn("inspect_screen_context", names)
        self.assertEqual(INSPECT_SCREEN_CONTEXT_DECLARATION["name"], "inspect_screen_context")
        self.assertEqual(
            INSPECT_SCREEN_CONTEXT_DECLARATION["parameters"]["properties"]["target"]["enum"],
            ["active_window", "full_screen"]
        )

    def test_dispatcher_and_multimodal_injection(self):
        dispatcher = ToolDispatcher()
        result = asyncio.run(dispatcher.dispatch("inspect_screen_context", {"target": "active_window"}))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["mime_type"], "image/jpeg")
        self.assertIn("image_bytes", result)
        self.assertIsInstance(result["image_bytes"], bytes)

        engine = AetherEngine()
        mm_turn = engine.handle_tool_call_result("inspect_screen_context", result)
        self.assertEqual(mm_turn["role"], "tool")
        self.assertEqual(len(mm_turn["parts"]), 2)
        self.assertEqual(mm_turn["parts"][0]["function_response"]["name"], "inspect_screen_context")
        self.assertEqual(mm_turn["parts"][1]["inline_data"]["mime_type"], "image/jpeg")
        self.assertEqual(mm_turn["parts"][1]["inline_data"]["data"], result["image_bytes"])


if __name__ == '__main__':
    unittest.main()
