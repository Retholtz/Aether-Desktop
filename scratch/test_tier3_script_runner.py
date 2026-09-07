"""
Test Tier 3: Sandboxed Script Runner & AST Gatekeeper
Tests AST validation, module blocking, policy enforcement, and subprocess execution.
"""

import os
import sys

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from security.ast_gatekeeper import validate_python_script
from tools.script_runner import ScriptRunner

def test_tier3():
    print("=" * 60)
    print("       TEST TIER 3: SCRIPT RUNNER & AST GATEKEEPER")
    print("=" * 60)

    # 1. Test AST Gatekeeper with safe Python script
    print("[1] Testing AST validation of safe Office automation script...")
    safe_code = """
import math
import datetime
import win32com.client

data = [10, 20, 30, 40]
total = sum(data)
avg = total / len(data)
print(f"Calculated Total={total}, Avg={avg}")
"""
    is_safe, error = validate_python_script(safe_code)
    assert is_safe is True, f"Expected safe script to pass, got error: {error}"
    print("[PASS] Safe script passed AST validation.")

    # 2. Test AST Gatekeeper with prohibited os.system call
    print("\n[2] Testing AST rejection of prohibited 'os.system' call...")
    unsafe_code_os = """
import os
os.system("dir")
"""
    is_safe_os, error_os = validate_python_script(unsafe_code_os)
    assert is_safe_os is False, "Expected unsafe os.system script to be rejected!"
    print(f"[PASS] Successfully blocked unsafe script: '{error_os}'")

    # 3. Test AST Gatekeeper with prohibited shutil.rmtree call
    print("\n[3] Testing AST rejection of prohibited 'shutil.rmtree' call...")
    unsafe_code_rm = """
import shutil
shutil.rmtree("C:\\\\test")
"""
    is_safe_rm, error_rm = validate_python_script(unsafe_code_rm)
    assert is_safe_rm is False, "Expected unsafe rmtree script to be rejected!"
    print(f"[PASS] Successfully blocked unsafe script: '{error_rm}'")

    # 4. Test execution of safe script via ScriptRunner
    print("\n[4] Testing background subprocess execution of safe script...")
    runner = ScriptRunner()
    run_res = runner.execute_script(safe_code, script_type="python", description="Test Calculation")
    print("Execution result:", run_res)
    assert run_res["status"] == "success", f"Script failed: {run_res}"
    assert "Calculated Total=100" in run_res["stdout"], "Expected calculation output in stdout"
    print(f"[SUCCESS] Script output: {run_res['stdout']}")

    # 5. Test execution rejection of blocked script via ScriptRunner
    print("\n[5] Testing runner rejection of blocked script...")
    blocked_res = runner.execute_script(unsafe_code_os, script_type="python", description="Blocked test")
    print("Blocked result:", blocked_res)
    assert blocked_res["status"] == "blocked", f"Expected blocked status, got: {blocked_res}"
    print(f"[SUCCESS] {blocked_res['message']}")

    # 6. Test PowerShell script execution
    print("\n[6] Testing PowerShell script execution...")
    ps_code = 'Write-Output "PowerShell Automation Active: $(Get-Date -Format yyyy-MM-dd)"'
    ps_res = runner.execute_script(ps_code, script_type="powershell", description="PS Date test")
    print("PowerShell result:", ps_res)
    assert ps_res["status"] == "success", f"PS script failed: {ps_res}"
    assert "PowerShell Automation Active" in ps_res["stdout"]
    print(f"[SUCCESS] PS output: {ps_res['stdout']}")

    print("\n[PASS] All Tier 3 Script Runner & AST Gatekeeper tests passed successfully!")

if __name__ == "__main__":
    test_tier3()

