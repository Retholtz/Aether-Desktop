"""
Tests for Background Startup Automation & Task Monitor Registry.
Verifies registry persistence, AST safety enforcement, subprocess job execution,
proactive notification forwarding, tool dispatcher integration, and engine lifecycle.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from core.startup_runner import StartupJobRunner
from tools.os_controls import (
    register_background_monitor,
    register_monitoring_task,
    list_background_monitors,
    list_active_monitors,
)
from tools.dispatcher import (
    ToolDispatcher,
    get_all_tool_declarations,
)


class TestStartupJobRunner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.startup_dir = os.path.join(self.temp_dir, "scripts", "startup")
        self.registry_path = os.path.join(self.startup_dir, "registry.json")
        self.mock_engine = MagicMock()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_infrastructure_creation_and_empty_registry(self):
        runner = StartupJobRunner(
            engine=self.mock_engine,
            workspace_root=self.temp_dir,
            startup_dir=self.startup_dir,
            registry_path=self.registry_path
        )
        self.assertTrue(os.path.exists(self.startup_dir))
        self.assertTrue(os.path.exists(self.registry_path))

        data = runner.load_registry()
        self.assertEqual(data, {"monitors": []})

    def test_register_and_update_task(self):
        runner = StartupJobRunner(
            engine=self.mock_engine,
            workspace_root=self.temp_dir,
            startup_dir=self.startup_dir,
            registry_path=self.registry_path
        )

        success = runner.register_task(
            task_id="real_estate_zillow_monitor",
            description="Monitors 4-bed single family homes under $600k",
            script_name="monitor_real_estate.py",
            interval_minutes=240
        )
        self.assertTrue(success)

        data = runner.load_registry()
        monitors = data.get("monitors", [])
        self.assertEqual(len(monitors), 1)
        m = monitors[0]
        self.assertEqual(m["task_id"], "real_estate_zillow_monitor")
        self.assertEqual(m["interval_minutes"], 240)
        self.assertEqual(m["last_status"], "registered")
        self.assertTrue(m["enabled"])
        self.assertEqual(m["last_run_timestamp"], 0)

        # Update the task with new interval
        runner.register_task(
            task_id="real_estate_zillow_monitor",
            description="Updated description",
            script_name="monitor_real_estate.py",
            interval_minutes=120
        )
        data_updated = runner.load_registry()
        self.assertEqual(len(data_updated["monitors"]), 1)
        self.assertEqual(data_updated["monitors"][0]["interval_minutes"], 120)
        self.assertEqual(data_updated["monitors"][0]["description"], "Updated description")

    def test_execute_monitor_script_with_notification(self):
        runner = StartupJobRunner(
            engine=self.mock_engine,
            workspace_root=self.temp_dir,
            startup_dir=self.startup_dir,
            registry_path=self.registry_path
        )

        # Create a python script that prints a [NOTIFY] message
        script_file = os.path.join(self.startup_dir, "test_job.py")
        with open(script_file, "w", encoding="utf-8") as f:
            f.write(
                'print("Regular stdout")\n'
                'print("[NOTIFY] Found 2 new properties!")\n'
            )

        runner.register_task(
            task_id="test_job",
            description="Test notification job",
            script_name="test_job.py",
            interval_minutes=60
        )

        # Execute task
        executed = runner.run_task_now("test_job")
        self.assertTrue(executed)

        # Verify engine.post_proactive_event was called with the notification
        self.mock_engine.post_proactive_event.assert_called_once_with("[test_job] Found 2 new properties!")

        # Verify registry updated with status and timestamp
        reg = runner.load_registry()
        m = reg["monitors"][0]
        self.assertEqual(m["last_status"], "success")
        self.assertGreater(m["last_run_timestamp"], 0)

    def test_execute_missing_script(self):
        runner = StartupJobRunner(
            engine=self.mock_engine,
            workspace_root=self.temp_dir,
            startup_dir=self.startup_dir,
            registry_path=self.registry_path
        )

        runner.register_task(
            task_id="missing_job",
            description="Points to non-existent file",
            script_name="does_not_exist.py",
            interval_minutes=60
        )

        runner.run_task_now("missing_job")
        reg = runner.load_registry()
        m = reg["monitors"][0]
        self.assertEqual(m["last_status"], "missing_file")

    def test_execute_failing_script(self):
        runner = StartupJobRunner(
            engine=self.mock_engine,
            workspace_root=self.temp_dir,
            startup_dir=self.startup_dir,
            registry_path=self.registry_path
        )

        script_file = os.path.join(self.startup_dir, "failing_job.py")
        with open(script_file, "w", encoding="utf-8") as f:
            f.write('raise ValueError("Simulated network failure")\n')

        runner.register_task(
            task_id="failing_job",
            description="Failing job",
            script_name="failing_job.py",
            interval_minutes=60
        )

        runner.run_task_now("failing_job")
        reg = runner.load_registry()
        m = reg["monitors"][0]
        self.assertTrue(m["last_status"].startswith("error:"))

    def test_scheduler_lifecycle_start_and_stop(self):
        runner = StartupJobRunner(
            engine=self.mock_engine,
            workspace_root=self.temp_dir,
            startup_dir=self.startup_dir,
            registry_path=self.registry_path,
            check_interval_seconds=1
        )
        self.assertFalse(runner._running)
        runner.start()
        self.assertTrue(runner._running)
        self.assertIsNotNone(runner._thread)
        self.assertTrue(runner._thread.is_alive())

        # Stop scheduler cleanly
        runner.stop()
        self.assertFalse(runner._running)
        self.assertIsNone(runner._thread)


class TestStartupToolsAndSecurity(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_register_background_monitor_ast_safety_blocks(self):
        # Disallowed module import: paramiko
        dangerous_code = "import paramiko\nprint('connected')"
        res = register_background_monitor(
            task_id="danger_task",
            description="Unsafe paramiko script",
            python_code=dangerous_code,
            workspace_root=self.temp_dir
        )
        self.assertIn("AST Security Gatekeeper Rejected", res)

        # Disallowed call: os.system
        dangerous_call = "import os\nos.system('dir')"
        res2 = register_background_monitor(
            task_id="os_system_task",
            description="Unsafe os.system call",
            python_code=dangerous_call,
            workspace_root=self.temp_dir
        )
        self.assertIn("AST Security Gatekeeper Rejected", res2)

    def test_register_background_monitor_safe_script_and_list(self):
        safe_code = (
            "print('Checking real estate...')\n"
            "print('[NOTIFY] Price dropped to $580,000!')\n"
        )
        res = register_background_monitor(
            task_id="real_estate_tracker",
            description="Track 4-bed houses under $600k",
            python_code=safe_code,
            interval_minutes=240,
            workspace_root=self.temp_dir
        )
        self.assertIn("successfully saved and registered", res)

        # Verify listed via list_background_monitors
        monitors = list_background_monitors(workspace_root=self.temp_dir)
        self.assertEqual(len(monitors), 1)
        self.assertEqual(monitors[0]["task_id"], "real_estate_tracker")
        self.assertEqual(monitors[0]["interval_minutes"], 240)

        # Verify alias list_active_monitors works identically
        active = list_active_monitors(workspace_root=self.temp_dir)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["task_id"], "real_estate_tracker")

        # Verify alias register_monitoring_task updates
        res_alias = register_monitoring_task(
            task_id="real_estate_tracker",
            description="Updated via alias",
            python_code=safe_code,
            interval_minutes=120,
            workspace_root=self.temp_dir
        )
        self.assertIn("successfully saved and registered", res_alias)
        updated = list_background_monitors(workspace_root=self.temp_dir)
        self.assertEqual(updated[0]["interval_minutes"], 120)


class TestDispatcherToolIntegration(unittest.TestCase):
    def tearDown(self):
        from core.startup_runner import STARTUP_DIR, StartupJobRunner
        test_file = os.path.join(STARTUP_DIR, "dispatcher_test_monitor.py")
        if os.path.exists(test_file):
            try:
                os.remove(test_file)
            except OSError:
                pass
        runner = StartupJobRunner()
        reg = runner.load_registry()
        reg["monitors"] = [m for m in reg.get("monitors", []) if m.get("task_id") != "dispatcher_test_monitor"]
        runner.save_registry(reg)

    def test_tool_declarations_present(self):
        decls = get_all_tool_declarations()
        tool_names = [d["name"] for d in decls]
        self.assertIn("register_background_monitor", tool_names)
        self.assertIn("register_monitoring_task", tool_names)
        self.assertIn("list_background_monitors", tool_names)
        self.assertIn("list_active_monitors", tool_names)

    def test_dispatcher_routing(self):
        events = []
        dispatcher = ToolDispatcher(
            on_event=lambda et, d: events.append((et, d))
        )

        # Dispatch register_background_monitor
        reg_result = asyncio.run(dispatcher.dispatch("register_background_monitor", {
            "task_id": "dispatcher_test_monitor",
            "description": "Monitors API latency",
            "python_code": "print('[NOTIFY] Latency normal')",
            "interval_minutes": 60
        }))
        self.assertEqual(reg_result.get("status"), "success")
        self.assertIn("dispatcher_test_monitor", reg_result.get("message", ""))

        # Dispatch list_background_monitors
        list_result = asyncio.run(dispatcher.dispatch("list_background_monitors", {}))
        self.assertEqual(list_result.get("status"), "success")
        self.assertIsInstance(list_result.get("monitors"), list)
        task_ids = [m.get("task_id") for m in list_result.get("monitors", [])]
        self.assertIn("dispatcher_test_monitor", task_ids)

        # Dispatch alias list_active_monitors
        active_result = asyncio.run(dispatcher.dispatch("list_active_monitors", {}))
        self.assertEqual(active_result.get("status"), "success")
        self.assertIsInstance(active_result.get("monitors"), list)


class TestEngineLifecycleIntegration(unittest.TestCase):
    def test_engine_has_startup_runner_and_proactive_event(self):
        from core.engine import AetherEngine

        events = []
        config = {
            "api": {"agent_name": "AetherTest", "model_id": "gemini-3.8-flash"},
            "audio": {"vad_trailing_silence_ms": 1400}
        }
        engine = AetherEngine(
            config_getter=lambda: config,
            on_event=lambda et, d: events.append((et, d))
        )

        # 1. StartupJobRunner initialized on engine
        self.assertTrue(hasattr(engine, "startup_runner"))
        self.assertIsInstance(engine.startup_runner, StartupJobRunner)
        self.assertEqual(engine.startup_runner.engine, engine)

        # 2. post_proactive_event emits chat_event
        engine.post_proactive_event("[real_estate_zillow_monitor] New listing: 4 bed, $575,000")
        chat_events = [d for et, d in events if et == "chat_event"]
        self.assertTrue(len(chat_events) > 0)
        self.assertIn("🔔 [real_estate_zillow_monitor] New listing", chat_events[-1]["content"])

        # 3. Shutdown method exists and cleans up
        self.assertTrue(hasattr(engine, "shutdown"))
        engine.shutdown()
        self.assertFalse(engine.is_running)


if __name__ == "__main__":
    unittest.main()
