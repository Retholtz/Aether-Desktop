import os
import unittest
from unittest.mock import patch

from security.ast_gatekeeper import validate_python_code
from security.whitelist import WhitelistValidator
from tools.skill_library import SkillLibrary
from security.crypto import protect_secret, unprotect_secret
from tools.os_controls import focus_window


class TestBatch2Security(unittest.TestCase):
    def test_ast_blocks_dangerous_calls(self):
        code_eval = "eval('2 + 2')"
        valid, violations = validate_python_code(code_eval)
        self.assertFalse(valid)
        self.assertTrue(any("eval" in v for v in violations))

        code_import = "__import__('os').system('dir')"
        valid, violations = validate_python_code(code_import)
        self.assertFalse(valid)

    def test_ast_allows_standard_automation(self):
        safe_code = """
import os
import json
print(os.path.exists('.'))
"""
        valid, violations = validate_python_code(safe_code)
        self.assertTrue(valid, f"Unexpected violations: {violations}")

    def test_path_traversal_blocked(self):
        lib = SkillLibrary(library_dir=os.path.abspath("scripts/library"))
        with self.assertRaises(PermissionError):
            lib.get_skill_code("../../security/crypto.py")

    def test_taskkill_sanitization(self):
        valid, msg = WhitelistValidator.terminate_process("notepad.exe")
        # Should pass validation check (regex matches)
        self.assertTrue(valid)

        malicious_input = "notepad.exe\" /T || echo pwned"
        valid, msg = WhitelistValidator.terminate_process(malicious_input)
        self.assertFalse(valid)
        self.assertIn("Invalid process name format", msg)

    def test_launch_uri_protocol_guard(self):
        valid, msg = WhitelistValidator.launch_uri("cmd.exe")
        self.assertFalse(valid)

        with patch("os.startfile", create=True) as mock_startfile:
            valid, msg = WhitelistValidator.launch_uri("[https://google.com](https://google.com)")
            self.assertTrue(valid)
            mock_startfile.assert_called_once_with("https://google.com")

    def test_crypto_dpapi_runtime_guards(self):
        secret = "super_secret_credential_value"
        ciphertext = protect_secret(secret)
        self.assertTrue(ciphertext.startswith("dpapi:"))
        plaintext = unprotect_secret(ciphertext)
        self.assertEqual(plaintext, secret)

        with self.assertRaises(ValueError):
            unprotect_secret("missing_dpapi_prefix_raw_string")

    def test_focus_window_whitelist_enforcement(self):
        res = focus_window("definitely_unauthorized_app_batch2.exe", auto_launch=True)
        self.assertEqual(res.get("status"), "error")
        self.assertIn("whitelist", res.get("message", "").lower())


if __name__ == "__main__":
    unittest.main()
