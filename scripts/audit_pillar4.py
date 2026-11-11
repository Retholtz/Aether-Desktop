"""
scripts/audit_pillar4.py - Automated Static Codebase Inspection for Pillar 4
"""
import os
import re
from pathlib import Path

REPO_ROOT = Path(".").resolve()

AUDIT_TARGETS = {
    "campaign": [
        "core/campaign/schema.py",
        "core/campaign/campaign_manager.py",
        "tools/campaign_tools.py",
        "core/campaign_engine.py"
    ],
    "scene_party": [
        "core/scene/scene_manager.py",
        "core/combat/combat_manager.py"
    ],
    "fail_forward": [
        "core/ttrpg_service.py",
        "core/dm_engine.py",
        "tools/ttrpg_tools.py"
    ]
}

def check_file(rel_path):
    p = REPO_ROOT / rel_path
    return p.exists(), p

print("=" * 70)
print("AETHER PROJECT: PILLAR 4 STATIC CODE AUDIT (CAMPAIGN & NARRATIVE)")
print("=" * 70)

# 1. Audit Campaign Schema & World State
print("\n[1] CAMPAIGN SCHEMA, LOCATION HIERARCHY & FACTIONS")
camp_found = False
for f in AUDIT_TARGETS["campaign"]:
    exists, full_p = check_file(f)
    if exists:
        camp_found = True
        content = full_p.read_text(encoding="utf-8", errors="ignore")
        classes = re.findall(r'class\s+([A-Za-z0-9_]+)', content)
        methods = re.findall(r'def\s+([A-Za-z0-9_]+)', content)
        print(f"  FOUND: {f}")
        print(f"    Classes: {classes}")
        print(f"    Methods: {methods[:6]}... (Total: {len(methods)})")
        for kw in ["parent_id", "connected_location", "disposition", "influence_score", "generate_prompt_context"]:
            matches = len(re.findall(kw, content, re.IGNORECASE))
            print(f"      Keyword '{kw}': {matches}")
if not camp_found:
    print("  [MISSING] No campaign engine modules located.")

# 2. Audit Split-Party & Multi-Scene Management
print("\n[2] ASYNCHRONOUS / SPLIT-PARTY ORCHESTRATION")
for f in AUDIT_TARGETS["scene_party"]:
    exists, full_p = check_file(f)
    if exists:
        content = full_p.read_text(encoding="utf-8", errors="ignore")
        print(f"  FOUND: {f}")
        for kw in ["split_party", "sub_scene", "party_roster", "isolated", "location_path"]:
            matches = len(re.findall(kw, content, re.IGNORECASE))
            print(f"    Keyword '{kw}': {matches}")

# 3. Audit Fail-Forward Narrative Logic
print("\n[3] FAIL-FORWARD & ALTERNATIVE RESOLUTION")
for f in AUDIT_TARGETS["fail_forward"]:
    exists, full_p = check_file(f)
    if exists:
        content = full_p.read_text(encoding="utf-8", errors="ignore")
        ff_matches = len(re.findall(r'fail_forward|complication|deescalat|surrender|bribe', content, re.IGNORECASE))
        print(f"  FOUND: {f} -> Fail-forward references: {ff_matches}")

print("\n" + "=" * 70)
print("AUDIT SCRIPT COMPLETE")
print("=" * 70)
