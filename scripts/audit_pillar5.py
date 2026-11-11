"""
scripts/audit_pillar5.py - Automated Static Codebase Inspection for Pillar 5
"""
import os
import re
from pathlib import Path

REPO_ROOT = Path(".").resolve()

AUDIT_TARGETS = {
    "combat_death": [
        "core/combat/combat_manager.py",
        "core/combat/death_engine.py",
        "core/rulesets/dnd5e_ruleset.py",
        "core/ttrpg_service.py"
    ],
    "corpse_scene": [
        "core/scene/scene_manager.py",
        "core/scene/corpse_manager.py",
        "core/inventory/permanence.py"
    ],
    "temple_economy": [
        "core/economy/temple_services.py",
        "core/economy/commerce.py",
        "tools/ttrpg_tools.py"
    ],
    "campaign_schema": [
        "core/campaign/schema.py",
        "core/campaign/campaign_manager.py"
    ]
}

def check_file(rel_path):
    p = REPO_ROOT / rel_path
    return p.exists(), p

print("=" * 70)
print("AETHER PROJECT: PILLAR 5 STATIC CODE AUDIT")
print("=" * 70)

# 1. Audit Combat & Death State Machine
print("\n[1] 5E DEATH & DYING STATE MACHINE")
for f in AUDIT_TARGETS["combat_death"]:
    exists, full_p = check_file(f)
    if exists:
        content = full_p.read_text(encoding="utf-8", errors="ignore")
        print(f"  FOUND: {f}")
        for kw in ["death_save", "death save", "unconscious", "instant_death", "revivify", "raise dead"]:
            matches = len(re.findall(kw, content, re.IGNORECASE))
            print(f"    Keyword '{kw}': {matches} occurrences")
    else:
        print(f"  [MISSING] {f}")

# 2. Audit Corpse Management & Expiration Timers
print("\n[2] CORPSE CONTAINERS & EXPIRATION TIMERS")
for f in AUDIT_TARGETS["corpse_scene"]:
    exists, full_p = check_file(f)
    if exists:
        content = full_p.read_text(encoding="utf-8", errors="ignore")
        print(f"  FOUND: {f}")
        for kw in ["corpse", "decay", "gentle repose", "loot_table", "container"]:
            matches = len(re.findall(kw, content, re.IGNORECASE))
            print(f"    Keyword '{kw}': {matches} occurrences")
    else:
        print(f"  [MISSING] {f}")

# 3. Audit TPK Routing
print("\n[3] TOTAL PARTY KILL (TPK) ROUTING")
tpk_found = False
for f in AUDIT_TARGETS["combat_death"] + AUDIT_TARGETS["campaign_schema"]:
    exists, full_p = check_file(f)
    if exists:
        content = full_p.read_text(encoding="utf-8", errors="ignore")
        tpk_matches = len(re.findall(r'\btpk\b|total party kill|party_wipe', content, re.IGNORECASE))
        if tpk_matches > 0:
            tpk_found = True
            print(f"  FOUND in {f}: {tpk_matches} TPK references")
            for line in content.splitlines():
                if any(k in line.lower() for k in ["tpk", "total party kill", "party_wipe"]):
                    print(f"    -> {line.strip()[:70]}")
if not tpk_found:
    print("  [MISSING] No explicit TPK handling hooks detected.")

# 4. Audit Settlement Spell Tier Gating
print("\n[4] SETTLEMENT SPELL TIER GATING (max_spell_tier)")
tier_found = False
for f in AUDIT_TARGETS["campaign_schema"]:
    exists, full_p = check_file(f)
    if exists:
        content = full_p.read_text(encoding="utf-8", errors="ignore")
        if "max_spell_tier" in content:
            tier_found = True
            print(f"  FOUND in {f}: 'max_spell_tier' attribute defined!")
if not tier_found:
    print("  [MISSING] 'max_spell_tier' attribute not found in campaign location schemas.")

# 5. Audit Temple Services & Revival Penalty
print("\n[5] TEMPLE REVIVAL SERVICES & PENALTY TRACKING")
temple_found = False
for f in AUDIT_TARGETS["temple_economy"]:
    exists, full_p = check_file(f)
    if exists:
        content = full_p.read_text(encoding="utf-8", errors="ignore")
        matches = len(re.findall(r'reviv|resurrect|raise_dead|temple', content, re.IGNORECASE))
        if matches > 0:
            temple_found = True
            print(f"  FOUND in {f}: {matches} revival/temple references")
if not temple_found:
    print("  [MISSING] No dedicated temple revival services found.")

print("\n" + "=" * 70)
print("AUDIT SCRIPT COMPLETE")
print("=" * 70)
