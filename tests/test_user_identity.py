"""
Unit tests for organic user identity directives and multiple callsign support.
"""

import os
import shutil
import tempfile
import unittest

from core.user_memory import UserMemory
from core.proactive_engine import ProactiveEngine


class TestUserIdentity(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_user_profile.db")
        self.memory = UserMemory(db_path=self.db_path)

    def tearDown(self):
        self.memory.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_single_user_name(self):
        self.memory.set_user_name("Mike")
        self.assertEqual(self.memory.get_user_name(), "Mike")
        self.assertEqual(self.memory.get_user_names(), ["Mike"])
        self.assertEqual(self.memory.get_random_user_name(), "Mike")

        directive = self.memory.build_user_identity_directive()
        self.assertIn("USER IDENTITY & ADDRESS DIRECTIVE:", directive)
        self.assertIn("'Mike'", directive)
        self.assertIn("NATURAL & BALANCED FREQUENCY", directive)
        self.assertIn("Do NOT use the user's name in every single response", directive)
        self.assertIn("about 40% to 50%", directive)

    def test_multiple_semicolon_callsigns(self):
        self.memory.set_user_name("Michael; Mike; Buddy")
        self.assertEqual(self.memory.get_user_name(), "Michael; Mike; Buddy")
        names = self.memory.get_user_names()
        self.assertEqual(names, ["Michael", "Mike", "Buddy"])

        random_name = self.memory.get_random_user_name()
        self.assertIn(random_name, ["Michael", "Mike", "Buddy"])

        directive = self.memory.build_user_identity_directive()
        self.assertIn("CALLSIGN / NAME VARIATION", directive)
        self.assertIn("'Michael', 'Mike', 'Buddy'", directive)
        self.assertIn("randomly alternate between their preferred callsigns", directive)
        self.assertIn("Never use more than one callsign in a single response", directive)

    def test_whitespace_and_trailing_semicolons_normalization(self):
        self.memory.set_user_name("  Michael ;  Mike   ; Buddy ;  ")
        self.assertEqual(self.memory.get_user_name(), "Michael; Mike; Buddy")
        self.assertEqual(self.memory.get_user_names(), ["Michael", "Mike", "Buddy"])

    def test_empty_user_name(self):
        self.assertEqual(self.memory.get_user_names(), [])
        self.assertEqual(self.memory.get_random_user_name(default="User"), "User")
        self.assertEqual(self.memory.build_user_identity_directive(), "")

    def test_proactive_engine_name_resolution(self):
        engine = ProactiveEngine(memory=self.memory)
        # Test with semicolon string
        resolved = engine._resolve_single_user_name("Michael; Mike; Buddy")
        self.assertIn(resolved, ["Michael", "Mike", "Buddy"])

        # Test fallback greeting doesn't output raw semicolon string
        greeting = engine._generate_fallback_greeting([], user_name="Michael; Mike; Buddy")
        self.assertNotIn(";", greeting)
        self.assertTrue(any(n in greeting for n in ["Michael", "Mike", "Buddy"]))

    def test_dynamic_directive_replacement(self):
        # Simulate base_system_instruction replacement behavior
        self.memory.set_user_name("Dave")
        initial_dir = self.memory.build_user_identity_directive()
        start_marker = "<!-- USER IDENTITY DIRECTIVE START -->"
        end_marker = "<!-- USER IDENTITY DIRECTIVE END -->"

        base_inst = f"HEADER\n{start_marker}\n{initial_dir}{end_marker}\n\nFOOTER"
        self.assertIn("'Dave'", base_inst)

        # Update to multiple callsigns
        self.memory.set_user_name("David; Dave; Boss")
        new_dir = self.memory.build_user_identity_directive()

        # Execute replacement logic as implemented in AetherEngine
        prefix = base_inst.split(start_marker)[0]
        suffix = base_inst.split(end_marker)[1]
        updated_inst = f"{prefix}{start_marker}\n{new_dir}{end_marker}{suffix}"

        self.assertNotIn("'Dave'\n- When", updated_inst)
        self.assertIn("'David', 'Dave', 'Boss'", updated_inst)
        self.assertIn("HEADER\n", updated_inst)
        self.assertIn("\nFOOTER", updated_inst)

    def test_callsign_frequency_persistence(self):
        # Default should be 'often'
        self.assertEqual(self.memory.get_callsign_frequency(), "often")

        # Test valid frequencies
        for freq in ["never", "seldom", "often", "always"]:
            self.memory.set_callsign_frequency(freq)
            self.assertEqual(self.memory.get_callsign_frequency(), freq)

        # Test invalid frequency falls back to 'often'
        self.memory.set_callsign_frequency("invalid_value")
        self.assertEqual(self.memory.get_callsign_frequency(), "often")

    def test_frequency_directives(self):
        self.memory.set_user_name("Michael; Mike")

        # Never
        self.memory.set_callsign_frequency("never")
        d_never = self.memory.build_user_identity_directive()
        self.assertIn("NEVER ADDRESS BY NAME", d_never)
        self.assertIn("Do NOT address the user by their name or callsign", d_never)

        # Seldom
        self.memory.set_callsign_frequency("seldom")
        d_seldom = self.memory.build_user_identity_directive()
        self.assertIn("SELDOM", d_seldom)
        self.assertIn("15-25%", d_seldom)

        # Often
        self.memory.set_callsign_frequency("often")
        d_often = self.memory.build_user_identity_directive()
        self.assertIn("OFTEN", d_often)
        self.assertIn("40-50%", d_often)

        # Always
        self.memory.set_callsign_frequency("always")
        d_always = self.memory.build_user_identity_directive()
        self.assertIn("ALWAYS", d_always)
        self.assertIn("Always address the user by their name or callsign", d_always)


if __name__ == "__main__":
    unittest.main()

