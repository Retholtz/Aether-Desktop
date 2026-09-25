import os
import shutil
import unittest
import tempfile
import json
from core.telemetry_db import init_telemetry_db, record_script_execution
from tools.skill_library import promote_cached_script, CATALOG_PATH, LIBRARY_DIR


class TestScriptAutoPromotion(unittest.TestCase):
    def setUp(self):
        init_telemetry_db()
        self.temp_dir = tempfile.mkdtemp()
        self.dummy_script = os.path.join(self.temp_dir, "test_clean_task.py")
        with open(self.dummy_script, "w", encoding="utf-8") as f:
            f.write("# Clean task\nprint('Execution OK')\n")
        self._created_library_files = []

    def test_frequency_auto_promotion_threshold(self):
        intent = "test_clean_automation"

        # Run 1
        res1 = record_script_execution(self.dummy_script, intent, success=True, returncode=0, duration_ms=120)
        self.assertFalse(res1["should_promote"])
        self.assertEqual(res1["clean_runs"], 1)

        # Run 2
        res2 = record_script_execution(self.dummy_script, intent, success=True, returncode=0, duration_ms=115)
        self.assertFalse(res2["should_promote"])
        self.assertEqual(res2["clean_runs"], 2)

        # Run 3: Meets threshold
        res3 = record_script_execution(self.dummy_script, intent, success=True, returncode=0, duration_ms=118)
        self.assertTrue(res3["should_promote"])
        self.assertEqual(res3["clean_runs"], 3)

        # Test Promotion Execution
        promoted = promote_cached_script(self.dummy_script, intent, res3["script_hash"])
        self.assertTrue(promoted)

        # Confirm Catalog entry
        with open(CATALOG_PATH, "r", encoding="utf-8") as f:
            catalog = json.load(f)
        matches = [s for s in catalog.get("skills", []) if s.get("intent") == intent]
        self.assertGreaterEqual(len(matches), 1)
        for m in matches:
            if m.get("path"):
                self._created_library_files.append(m["path"])

    def test_failure_resets_consecutive_clean_runs(self):
        intent = "test_reset_on_error"
        script_file = os.path.join(self.temp_dir, "test_reset_script.py")
        with open(script_file, "w", encoding="utf-8") as f:
            f.write("# Reset test\nprint('Step 1')\n")

        r1 = record_script_execution(script_file, intent, success=True, returncode=0, duration_ms=80)
        self.assertEqual(r1["clean_runs"], 1)

        r2 = record_script_execution(script_file, intent, success=True, returncode=0, duration_ms=85)
        self.assertEqual(r2["clean_runs"], 2)

        # Failed run resets consecutive_clean_runs to 0
        r_fail = record_script_execution(script_file, intent, success=False, returncode=1, duration_ms=50)
        self.assertEqual(r_fail["clean_runs"], 0)
        self.assertFalse(r_fail["should_promote"])

        # Next clean run starts at 1 again
        r_next = record_script_execution(script_file, intent, success=True, returncode=0, duration_ms=90)
        self.assertEqual(r_next["clean_runs"], 1)
        self.assertFalse(r_next["should_promote"])

    def test_ast_revalidation_blocks_unsafe_promotion(self):
        unsafe_script = os.path.join(self.temp_dir, "unsafe_task.py")
        with open(unsafe_script, "w", encoding="utf-8") as f:
            f.write("import os\nos.system('echo unsafe')\n")

        promoted = promote_cached_script(unsafe_script, "unsafe_intent", "deadbeef12345678")
        self.assertFalse(promoted)

    def test_execute_cached_script_end_to_end(self):
        from tools.script_runner import execute_cached_script

        cache_dir = os.path.join("scripts", "cache")
        os.makedirs(cache_dir, exist_ok=True)
        cached_script = os.path.join(cache_dir, "test_e2e_cached_task.py")
        self._created_library_files.append(cached_script)

        with open(cached_script, "w", encoding="utf-8") as f:
            f.write("# End-to-end cached script\nprint('E2E_CACHE_OK')\n")

        intent = "test_e2e_cache_intent"
        for _ in range(3):
            rc, out, err = execute_cached_script(cached_script, intent_label=intent, timeout_seconds=10)
            self.assertEqual(rc, 0)
            self.assertIn("E2E_CACHE_OK", out)

        with open(CATALOG_PATH, "r", encoding="utf-8") as f:
            catalog = json.load(f)
        matches = [s for s in catalog.get("skills", []) if s.get("intent") == intent]
        self.assertGreaterEqual(len(matches), 1)
        for m in matches:
            if m.get("path"):
                self._created_library_files.append(m["path"])

    def tearDown(self):
        if os.path.exists(self.dummy_script):
            os.remove(self.dummy_script)
        for fpath in getattr(self, "_created_library_files", []):
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except OSError:
                    pass
        # Clean up test intents from CATALOG_PATH and scripts/library/skills_catalog.json
        test_intents = {"test_clean_automation", "test_e2e_cache_intent", "test_reset_on_error", "unsafe_intent"}
        if os.path.exists(CATALOG_PATH):
            try:
                with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                    cat = json.load(f)
                if "skills" in cat:
                    cat["skills"] = [s for s in cat["skills"] if s.get("intent") not in test_intents]
                with open(CATALOG_PATH, "w", encoding="utf-8") as f:
                    json.dump(cat, f, indent=2)
            except Exception:
                pass
        lib_cat_path = os.path.join(LIBRARY_DIR, "skills_catalog.json")
        if os.path.exists(lib_cat_path):
            try:
                with open(lib_cat_path, "r", encoding="utf-8") as f:
                    lcat = json.load(f)
                keys_to_remove = [
                    k for k, v in lcat.items()
                    if isinstance(v, dict) and v.get("intent") in test_intents
                ]
                for k in keys_to_remove:
                    lcat.pop(k, None)
                with open(lib_cat_path, "w", encoding="utf-8") as f:
                    json.dump(lcat, f, indent=2)
            except Exception:
                pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
