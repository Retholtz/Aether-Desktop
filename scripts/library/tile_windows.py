"""
Aether Skill: tile_windows
Arranges two target application windows side-by-side (50% left, 50% right)
on Windows 11 using native Win32/DWM window positioning with post-move verification.
"""

import json
import os
import sys

from tools.os_controls import tile_windows


def main():
    if sys.platform != "win32":
        print("Error: tile_windows requires Windows Win32 API.")
        sys.exit(1)

    args_str = os.environ.get("SKILL_ARGS", "")
    if not args_str and len(sys.argv) > 1:
        args_str = sys.argv[1]

    try:
        args = json.loads(args_str) if args_str else {}
    except Exception:
        args = {}

    left_app = str(args.get("left_app") or args.get("app1") or "").strip()
    right_app = str(args.get("right_app") or args.get("app2") or "").strip()
    monitor = args.get("monitor", None)
    if monitor is not None:
        try:
            monitor = int(monitor)
        except (ValueError, TypeError):
            monitor = None

    if not left_app or not right_app:
        print("Error: left_app and right_app arguments are required.")
        sys.exit(1)

    res = tile_windows(left_app=left_app, right_app=right_app, monitor=monitor)
    if res.get("status") != "success":
        print(f"Error: {res.get('message', 'Failed to split windows.')}")
        sys.exit(1)

    print(res.get("message", f"Split '{left_app}' on the left and '{right_app}' on the right successfully."))


if __name__ == "__main__":
    main()
