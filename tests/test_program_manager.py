"""
Unit and integration tests for ProgramManager, WhitelistValidator, GuiBridge program RPCs,
and Dispatcher tool routing.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core.program_manager import ProgramManager
from security.whitelist import WhitelistValidator
from tools.dispatcher import ToolDispatcher


class TestProgramManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.programs_path = os.path.join(self.test_dir, "test_programs.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_default_initialization(self):
        pm = ProgramManager(programs_path=self.programs_path)
        data = pm.list_programs()
        self.assertIn("programs", data)
        self.assertIn("google_chrome", data["programs"])
        self.assertIn("notepad", data["programs"])
        self.assertIn("file_explorer", data["programs"])
        self.assertTrue(os.path.exists(self.programs_path))

    def test_add_update_delete_program(self):
        pm = ProgramManager(programs_path=self.programs_path)
        
        # Add Program with --elevate argument
        add_res = pm.add_program(
            name="Crimson Desert Test",
            path="D:\\SteamLibrary\\steamapps\\common\\Crimson Desert\\bin64\\CrimsonDesert.exe",
            arguments="--elevate --skip-intro",
            elevate=True,
            working_dir="D:\\SteamLibrary\\steamapps\\common\\Crimson Desert\\bin64"
        )
        self.assertTrue(add_res["success"])
        pid = add_res["program_id"]
        self.assertTrue(pid.startswith("crimson_desert_test"))
        self.assertEqual(add_res["program"]["elevate"], True)

        # Lookup by display name
        prog = pm.get_program("Crimson Desert Test")
        self.assertIsNotNone(prog)
        self.assertEqual(prog["id"], pid)

        # Lookup by process name
        prog_by_exe = pm.get_program("CrimsonDesert.exe")
        self.assertIsNotNone(prog_by_exe)

        # Whitelist check
        self.assertTrue(pm.is_whitelisted("Crimson Desert Test"))
        self.assertTrue(pm.is_whitelisted("CrimsonDesert.exe"))

        # Update program
        upd_res = pm.update_program(
            prog_id=pid,
            name="Crimson Desert Remastered",
            path="D:\\SteamLibrary\\steamapps\\common\\Crimson Desert\\bin64\\CrimsonDesert.exe",
            arguments="--fullscreen",
            elevate=False
        )
        self.assertTrue(upd_res["success"])
        self.assertEqual(upd_res["program"]["name"], "Crimson Desert Remastered")
        self.assertEqual(upd_res["program"]["elevate"], False)

        # Delete program
        del_res = pm.delete_program(pid)
        self.assertTrue(del_res["success"])
        self.assertIsNone(pm.get_program(pid))
        self.assertFalse(pm.is_whitelisted(pid))

    def test_steam_library_discovery(self):
        # find_steam_libraries returns list without crashing
        libs = ProgramManager.find_steam_libraries()
        self.assertIsInstance(libs, list)

    def test_resolve_program_path(self):
        # Test standard windows executables
        notepad_path = ProgramManager.resolve_program_path("notepad.exe")
        self.assertTrue(notepad_path is not None and notepad_path.lower().endswith("notepad.exe"))

        cmd_path = ProgramManager.resolve_program_path("cmd.exe")
        self.assertTrue(cmd_path is not None and cmd_path.lower().endswith("cmd.exe"))

    @patch("subprocess.Popen")
    def test_launch_standard_program(self, mock_popen):
        pm = ProgramManager(programs_path=self.programs_path)
        pm.add_program("Test Tool", path=r"C:\Windows\System32\notepad.exe", arguments="--test")
        
        res = pm.launch_program("Test Tool")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["elevated"], False)
        self.assertTrue(mock_popen.called)

    @patch("ctypes.windll.shell32.ShellExecuteW", return_value=42)
    def test_launch_elevated_program(self, mock_shellexecute):
        pm = ProgramManager(programs_path=self.programs_path)
        pm.add_program("Elevated App", path=r"C:\Windows\System32\notepad.exe", arguments="", elevate=True)

        res = pm.launch_program("Elevated App")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["elevated"], True)
        self.assertTrue(mock_shellexecute.called)


    def test_deduplication(self):
        pm = ProgramManager(programs_path=self.programs_path)
        
        # Add Crimson Desert once
        res1 = pm.add_program(
            name="Crimson Desert",
            path=r"D:\SteamLibrary\steamapps\common\Crimson Desert\bin64\CrimsonDesert.exe",
            arguments="--elevate"
        )
        pid1 = res1["program_id"]
        
        # Add Crimson Desert a second time
        res2 = pm.add_program(
            name="Crimson Desert",
            path=r"D:\SteamLibrary\steamapps\common\Crimson Desert\bin64\CrimsonDesert.exe",
            arguments="--fullscreen"
        )
        pid2 = res2["program_id"]
        
        # Must return the SAME program_id and update it, NOT create crimson_desert_2
        self.assertEqual(pid1, pid2)
        programs = pm.list_programs()["programs"]
        self.assertNotIn("crimson_desert_2", programs)
        self.assertEqual(programs[pid1]["arguments"], "--fullscreen")

    def test_file_explorer_window_detection(self):
        # File Explorer should not report running solely because explorer.exe shell exists
        pm = ProgramManager(programs_path=self.programs_path)
        with patch.object(ProgramManager, "is_file_explorer_open", return_value=False):
            self.assertFalse(pm.is_program_running("file_explorer"))
            status = pm.get_running_status_map()
            self.assertFalse(status.get("file_explorer", False))

        with patch.object(ProgramManager, "is_file_explorer_open", return_value=True):
            self.assertTrue(pm.is_program_running("file_explorer"))
            status = pm.get_running_status_map()
            self.assertTrue(status.get("file_explorer", False))


class TestDispatcherWhitelistIntegration(unittest.TestCase):
    def test_launch_crimson_desert_via_dispatcher(self):
        import asyncio
        # Verify dispatcher handles launch_application with Crimson Desert
        dispatcher = ToolDispatcher()
        
        # Test dispatching add_to_whitelist
        res_add = asyncio.run(dispatcher.dispatch(
            "add_to_whitelist",
            {"app_name": "Crimson Desert", "arguments": "--elevate"}
        ))
        self.assertIn(res_add.get("status"), ("success", "already_allowed"))

        # Test launch_application mock dispatch
        with patch.object(ProgramManager, "launch_program") as mock_launch:
            mock_launch.return_value = {
                "status": "success",
                "app_name": "Crimson Desert",
                "executable": r"D:\SteamLibrary\steamapps\common\Crimson Desert\bin64\CrimsonDesert.exe",
                "elevated": False
            }
            res_launch = asyncio.run(dispatcher.dispatch("launch_application", {"app_name": "Crimson Desert"}))
            self.assertEqual(res_launch["status"], "success")
            self.assertEqual(res_launch["app_name"], "Crimson Desert")



class TestGuiBridgeProgramMethods(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.programs_path = self.tmp.name
        self.pm = ProgramManager(programs_path=self.programs_path)

    def tearDown(self):
        if os.path.exists(self.programs_path):
            try:
                os.remove(self.programs_path)
            except Exception:
                pass

    def test_gui_bridge_parameter_names_safe_from_js_reserved_keywords(self):
        import inspect
        from unittest.mock import MagicMock
        from core.gui_bridge import GuiBridge

        bridge = GuiBridge(MagicMock())
        add_params = list(inspect.getfullargspec(bridge.add_program).args)[1:]
        update_params = list(inspect.getfullargspec(bridge.update_program).args)[1:]

        # 'arguments' keyword MUST NOT be a parameter name because pywebview's JS bridge
        # generates `new Function(params, ... Array.prototype.slice.call(arguments) ...)`
        # which shadows the JS arguments object if a parameter is named 'arguments'.
        self.assertNotIn("arguments", add_params)
        self.assertNotIn("arguments", update_params)
        self.assertIn("launch_args", add_params)
        self.assertIn("launch_args", update_params)

    def test_gui_bridge_add_and_update_program_positional(self):
        from unittest.mock import MagicMock
        from core.gui_bridge import GuiBridge

        bridge = GuiBridge(MagicMock())
        bridge.program_mgr = self.pm

        res = bridge.add_program("Crimson Desert", r"D:\Games\cd.exe", "--elevate", True, r"D:\Games")
        self.assertTrue(res.get("success"))
        pid = res.get("program_id")

        prog = self.pm.get_program(pid)
        self.assertIsNotNone(prog)
        self.assertEqual(prog["name"], "Crimson Desert")
        self.assertEqual(prog["path"], r"D:\Games\cd.exe")
        self.assertEqual(prog["arguments"], "--elevate")
        self.assertTrue(prog["elevate"])

        res_update = bridge.update_program(pid, "Crimson Desert Updated", r"D:\Games\cd2.exe", "--windowed", False, r"D:\Games")
        self.assertTrue(res_update.get("success"))
        prog_updated = self.pm.get_program(pid)
        self.assertEqual(prog_updated["name"], "Crimson Desert Updated")
        self.assertEqual(prog_updated["arguments"], "--windowed")
        self.assertFalse(prog_updated["elevate"])


if __name__ == "__main__":
    unittest.main()

