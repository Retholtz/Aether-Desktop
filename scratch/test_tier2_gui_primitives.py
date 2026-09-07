"""
Test Tier 2: Low-Level GUI Primitives
Tests 64-bit SendInput struct alignment, coordinate translation, typing, key combos, and scrolling.
"""

import ctypes
import os
import sys

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.gui_primitives import (
    GuiPrimitivesController,
    INPUT,
    MOUSEINPUT,
    KEYBDINPUT,
)

def test_tier2():
    print("=" * 60)
    print("       TEST TIER 2: LOW-LEVEL GUI PRIMITIVES")
    print("=" * 60)

    # 1. Verify 64-bit Win32 SendInput struct alignment
    print("[1] Verifying 64-bit Win32 SendInput struct sizes...")
    input_size = ctypes.sizeof(INPUT)
    print(f"ctypes.sizeof(INPUT) = {input_size} bytes (Required on Win64: 40 bytes)")
    assert input_size == 40, f"Expected INPUT size 40, got {input_size}"
    print("[PASS] Struct alignment strictly matches Win64 ABI (40 bytes).")

    # 2. Test GuiPrimitivesController coordinate translation
    print("\n[2] Testing multi-monitor coordinate translation...")
    controller = GuiPrimitivesController()
    
    # Translate center coordinates
    x, y, mon = controller.translate_coords(500, 500, monitor="auto", normalized=True)
    print(f"Normalized (500, 500) translated to ({x}, {y}) on Monitor {mon}")
    assert isinstance(x, int) and isinstance(y, int)

    # 3. Test press_key shortcut
    print("\n[3] Testing press_key shortcut combo...")
    res_key = controller.press_key("ctrl+shift+escape")
    assert res_key["status"] == "success", f"press_key failed: {res_key}"
    print(f"[SUCCESS] {res_key['message']}")

    # 4. Test scroll
    print("\n[4] Testing mouse wheel scroll...")
    res_scroll = controller.scroll(amount=2, direction="down")
    assert res_scroll["status"] == "success", f"scroll failed: {res_scroll}"
    print(f"[SUCCESS] {res_scroll['message']}")

    # 5. Test type_text without enter
    print("\n[5] Testing type_text primitive...")
    res_type = controller.type_text("Aether Test", press_enter=False)
    assert res_type["status"] == "success", f"type_text failed: {res_type}"
    assert res_type["typed_chars"] == len("Aether Test")
    print(f"[SUCCESS] {res_type['message']}")

    print("\n[PASS] All Tier 2 Low-Level GUI Primitives tests passed successfully!")

if __name__ == "__main__":
    test_tier2()

