"""
scripts/audit_pillar1.py - Automated Static Codebase Inspection for Pillar 1
"""
import os
import re
from pathlib import Path

REPO_ROOT = Path(".").resolve()

AUDIT_TARGETS = {
    "engine": ["core/engine.py", "core/campaign_engine.py", "core/dm_engine.py"],
    "websocket": ["api/ws_gateway.py", "core/ws.py", "core/gui_bridge.py", "server.py", "app.py"],
    "rulesets": [
        "core/rulesets/base_ruleset.py",
        "core/rulesets/dnd5e_ruleset.py",
        "core/rulesets/pf2e_ruleset.py",
        "core/rulesets/coc7e_ruleset.py",
        "core/rulesets/cyberpunk_ruleset.py",
        "core/rulesets/__init__.py"
    ],
    "multiverse": ["core/multiverse/", "core/warping/", "core/translation/"]
}

def check_file(rel_path):
    p = REPO_ROOT / rel_path
    return p.exists(), p

print("=" * 70)
print("AETHER PROJECT: PILLAR 1 STATIC CODE AUDIT")
print("=" * 70)

# 1. Audit Engine & Model Isolation
print("\n[1] MODEL ORCHESTRATION & ENGINE DECOUPLING")
engine_found = False
for f in AUDIT_TARGETS["engine"]:
    exists, full_p = check_file(f)
    if exists:
        engine_found = True
        content = full_p.read_text(encoding="utf-8", errors="ignore")
        flash_calls = len(re.findall(r'flash', content, re.IGNORECASE))
        argon_calls = len(re.findall(r'argon', content, re.IGNORECASE))
        print(f"  FOUND: {f} -> Flash references: {flash_calls} | Argon references: {argon_calls}")
        
        # Check for route-level Argon usage
        lines = content.splitlines()
        for idx, line in enumerate(lines):
            if "argon" in line.lower() and any(k in line.lower() for k in ["turn", "ws", "stream", "combat", "route"]):
                print(f"    [!] Potential Coupling: Line {idx+1}: {line.strip()[:60]}")
if not engine_found:
    print("  [MISSING] No core engine files located matching known patterns.")

# 2. Audit WebSocket Transport
print("\n[2] STREAMING WEBSOCKET PROTOCOL")
ws_found = False
for f in AUDIT_TARGETS["websocket"]:
    exists, full_p = check_file(f)
    if exists:
        content = full_p.read_text(encoding="utf-8", errors="ignore")
        ws_keywords = ["websocket", "ws.receive", "send_json", "emit", "stream"]
        matches = [kw for kw in ws_keywords if kw in content.lower()]
        if matches:
            ws_found = True
            print(f"  FOUND: {f} -> Keywords matched: {matches}")
if not ws_found:
    print("  [MISSING] No active WebSocket gateway or streaming router detected.")

# 3. Audit Rulesets
print("\n[3] POLYMORPHIC RULESET CORE")
for f in AUDIT_TARGETS["rulesets"]:
    exists, full_p = check_file(f)
    if exists:
        content = full_p.read_text(encoding="utf-8", errors="ignore")
        classes = re.findall(r'class\s+([A-Za-z0-9_]+)', content)
        print(f"  FOUND: {f} -> Declared Classes: {classes}")
    else:
        print(f"  [MISSING] {f}")

# 4. Audit Multiverse & Scarcity
print("\n[4] MULTIVERSE WARPING & RESOURCE SCARCITY")
mv_found = False
for path_str in AUDIT_TARGETS["multiverse"]:
    exists, full_p = check_file(path_str)
    if exists:
        mv_found = True
        files = list(full_p.glob("*.py"))
        print(f"  FOUND DIRECTORY: {path_str} -> Files: {[f.name for f in files]}")
if not mv_found:
    print("  [MISSING] No dedicated Multiverse Warping directory or modules located.")

print("\n" + "=" * 70)
print("AUDIT SCRIPT COMPLETE")
print("=" * 70)
