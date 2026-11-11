"""
scripts/audit_pillar3.py - Automated Static Codebase Inspection for Pillar 3
"""
import os
import re
from pathlib import Path

REPO_ROOT = Path(".").resolve()

AUDIT_TARGETS = {
    "permanence": [
        "core/inventory/permanence.py",
        "core/inventory/item_manager.py",
        "core/inventory/",
        "core/ttrpg_service.py"
    ],
    "containment": [
        "core/scene/scene_manager.py",
        "core/combat/combat_manager.py"
    ],
    "encumbrance": [
        "core/parsers/dnd5e_parser.py",
        "core/rulesets/dnd5e_ruleset.py"
    ],
    "commerce": [
        "core/economy/commerce.py",
        "core/economy/merchant.py",
        "core/economy/",
        "tools/ttrpg_tools.py"
    ]
}

def check_file(rel_path):
    p = REPO_ROOT / rel_path
    return p.exists(), p

print("=" * 70)
print("AETHER PROJECT: PILLAR 3 STATIC CODE AUDIT (INVENTORY & ECONOMY)")
print("=" * 70)

# 1. Audit Object Permanence & Flags
print("\n[1] OBJECT PERMANENCE & KNOWLEDGE FLAGS")
perm_found = False
for f in AUDIT_TARGETS["permanence"]:
    exists, full_p = check_file(f)
    if exists:
        perm_found = True
        if full_p.is_dir():
            files = list(full_p.glob("*.py"))
            print(f"  FOUND DIRECTORY: {f} -> Files: {[p.name for p in files]}")
        else:
            content = full_p.read_text(encoding="utf-8", errors="ignore")
            known_matches = len(re.findall(r'\bknown\b', content, re.IGNORECASE))
            salvage_matches = len(re.findall(r'\bsalvageable\b', content, re.IGNORECASE))
            print(f"  FOUND FILE: {f}")
            print(f"    'known' references: {known_matches}")
            print(f"    'salvageable' references: {salvage_matches}")
if not perm_found:
    print("  [MISSING] No dedicated inventory permanence modules found.")

# 2. Audit Dynamic Containment & Loot Ingestion
print("\n[2] ENTITY CONTAINMENT & LOOT TABLES")
for f in AUDIT_TARGETS["containment"]:
    exists, full_p = check_file(f)
    if exists:
        content = full_p.read_text(encoding="utf-8", errors="ignore")
        loot_matches = len(re.findall(r'loot|container|corpse|drop', content, re.IGNORECASE))
        print(f"  FOUND FILE: {f} -> Loot/Container references: {loot_matches}")

# 3. Audit Encumbrance Calculations
print("\n[3] ENCUMBRANCE & CAPACITY")
for f in AUDIT_TARGETS["encumbrance"]:
    exists, full_p = check_file(f)
    if exists:
        content = full_p.read_text(encoding="utf-8", errors="ignore")
        enc_matches = len(re.findall(r'encumber|weight_carried|push_drag_lift', content, re.IGNORECASE))
        print(f"  FOUND FILE: {f} -> Encumbrance references: {enc_matches}")

# 4. Audit Commerce & Merchant Engine
print("\n[4] COMMERCE & MERCHANTS")
comm_found = False
for f in AUDIT_TARGETS["commerce"]:
    exists, full_p = check_file(f)
    if exists:
        comm_found = True
        if full_p.is_dir():
            files = list(full_p.glob("*.py"))
            print(f"  FOUND DIRECTORY: {f} -> Files: {[p.name for p in files]}")
        else:
            content = full_p.read_text(encoding="utf-8", errors="ignore")
            comm_matches = len(re.findall(r'merchant|shop|buy|sell|price|currency', content, re.IGNORECASE))
            print(f"  FOUND FILE: {f} -> Commerce references: {comm_matches}")
if not comm_found:
    print("  [MISSING] No dedicated commerce or merchant modules found.")

print("\n" + "=" * 70)
print("AUDIT SCRIPT COMPLETE")
print("=" * 70)
