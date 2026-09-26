import unittest
from core.context_sanitizer import (
    is_refusal_text,
    sanitize_turn_history,
    sanitize_chat_session,
    NEUTRALIZED_ASSISTANT_REPLACEMENT,
)
from core.session_lifecycle import SessionLifecycleManager


class TestContextSanitizer(unittest.TestCase):
    def test_refusal_pattern_detection(self):
        # Must detect actual policy refusals
        refusals = [
            "I cannot assist with searching for personal individuals or their relatives as it violates privacy guidelines.",
            "I am unable to find personal information about this private individual due to safety policies.",
            "I must decline to search for family members because it violates our privacy policy.",
            "As an AI, I cannot look up people or individuals.",
            "I cannot provide contact information or locate private individuals.",
        ]
        for r in refusals:
            self.assertTrue(is_refusal_text(r), f"Failed to detect refusal: {r}")

        # Must NOT detect normal operational negative results
        benign_answers = [
            "I searched Google and local records, but I could not find anyone matching that specific name in Erie.",
            "I looked through your past sessions and found 0 matching notes.",
            "The file you requested does not exist on your desktop.",
            "I'm sorry, I didn't catch that. Could you repeat it?"
        ]
        for b in benign_answers:
            self.assertFalse(is_refusal_text(b), f"False positive on benign text: {b}")

    def test_surgical_history_cleansing(self):
        history = [
            {
                "role": "user",
                "parts": [{"text": "Can you find background information on Dr. Stanley Orlop and his family?"}]
            },
            {
                "role": "model",
                "parts": [{"text": "I cannot fulfill this request to look up private individuals or their relatives due to privacy policies."}]
            },
            {
                "role": "user",
                "parts": [{"text": "Try checking public records instead."}]
            }
        ]

        cleaned = sanitize_turn_history(history)

        # User prompt 1 must be identical (entities preserved)
        self.assertEqual(cleaned[0]["parts"][0]["text"], "Can you find background information on Dr. Stanley Orlop and his family?")
        
        # Assistant refusal must be neutralized
        self.assertEqual(cleaned[1]["parts"][0]["text"], NEUTRALIZED_ASSISTANT_REPLACEMENT)
        
        # User prompt 2 must remain untouched
        self.assertEqual(cleaned[2]["parts"][0]["text"], "Try checking public records instead.")

    def test_session_lifecycle_active_context_turns(self):
        mgr = SessionLifecycleManager()
        mgr.record_turn("user", "Can you find background information on Dr. Stanley Orlop?")
        mgr.record_turn(
            "assistant",
            "I cannot assist with searching for personal individuals or private individuals due to privacy policies."
        )
        mgr.record_turn("user", "Can you search public records for that person?")

        active_turns = mgr.get_active_context_turns()
        self.assertEqual(len(active_turns), 3)
        self.assertEqual(active_turns[0]["content"], "Can you find background information on Dr. Stanley Orlop?")
        self.assertEqual(active_turns[1]["content"], NEUTRALIZED_ASSISTANT_REPLACEMENT)
        self.assertEqual(active_turns[2]["content"], "Can you search public records for that person?")


if __name__ == "__main__":
    unittest.main()
