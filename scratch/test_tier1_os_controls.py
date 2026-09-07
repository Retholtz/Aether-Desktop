"""
Test Tier 1: Win32 Native OS Window Management
Tests find_hwnd_by_query, maximize_window, minimize_window, restore_window, focus_window.
"""

import os
import sys
# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import win32gui

from tools.os_controls import (
    find_hwnd_by_query,
    maximize_window,
    minimize_window,
    restore_window,
    focus_window,
)

def test_tier1():
    print("=" * 60)
    print("       TEST TIER 1: WIN32 OS WINDOW MANAGEMENT")
    print("=" * 60)

    # Test against Chrome or VS Code
    target_app = "chrome" if find_hwnd_by_query("chrome") else "code"
    print(f"[1] Testing with target application '{target_app}'...")

    hwnd = find_hwnd_by_query(target_app)
    assert hwnd is not None, f"Failed to find HWND for '{target_app}'"
    title = win32gui.GetWindowText(hwnd)
    print(f"[SUCCESS] Found HWND: {hwnd} (0x{hwnd:X}), Title: {repr(title)}")

    # 2. Test focus_window
    print(f"\n[2] Testing focus_window('{target_app}')...")
    res_focus = focus_window(target_app)
    assert res_focus["status"] == "success", f"focus_window failed: {res_focus}"
    print(f"[SUCCESS] {res_focus['message']}")
    time.sleep(0.3)

    # 3. Test maximize_window
    print(f"\n[3] Testing maximize_window('{target_app}')...")
    res_max = maximize_window(target_app)
    assert res_max["status"] == "success", f"maximize_window failed: {res_max}"
    print(f"[SUCCESS] {res_max['message']}")
    time.sleep(0.3)

    # 4. Test restore_window
    print(f"\n[4] Testing restore_window('{target_app}')...")
    res_res = restore_window(target_app)
    assert res_res["status"] == "success", f"restore_window failed: {res_res}"
    print(f"[SUCCESS] {res_res['message']}")
    time.sleep(0.3)

    # 5. Test minimize_window
    print(f"\n[5] Testing minimize_window('{target_app}')...")
    res_min = minimize_window(target_app)
    assert res_min["status"] == "success", f"minimize_window failed: {res_min}"
    print(f"[SUCCESS] {res_min['message']}")
    time.sleep(0.3)

    # 6. Test re-maximizing from minimized state (verifying restore-then-maximize logic)
    print(f"\n[6] Testing maximize_window from minimized state...")
    res_max2 = maximize_window(target_app)
    assert res_max2["status"] == "success", f"maximize from minimized failed: {res_max2}"
    print(f"[SUCCESS] {res_max2['message']}")
    time.sleep(0.3)

    # 7. Restore back to normal state
    restore_window(target_app)

    print("\n[PASS] All Tier 1 Win32 OS Control tests passed successfully!")

if __name__ == "__main__":
    test_tier1()

