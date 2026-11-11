"""
scripts/audit_pillar2.py - Automated Static Codebase Inspection for Pillar 2
"""
import os
import re
from pathlib import Path

REPO_ROOT = Path(".").resolve()

AUDIT_TARGETS = {
    "parsers": [
        "core/parsers/base_parser.py",
        "core/parsers/dnd5e_parser.py",
        "core/parsers/pf2e_parser.py",
        "core/parsers/coc7e_parser.py",
        "core/parsers/cyberpunk_red_parser.py",
        "core/parsers/spell_registry.py"
    ],
    "schema": [
        "core/schema/validator.py",
        "core/schema/character_schema.py",
        "core/schema/",
        "schema/"
    ],
    "spell_locks": [
        "core/rulesets/spell_locks.py",
        "core/spell_engine.py",
        "core/rulesets/spells.py"
    ],
    "vault": [
        "core/vault/",
        "core/character_vault_service.py",
        "core/vault_service.py",
        "core/graveyard.py"
    ]
}

def check_file(rel_path):
    p = REPO_ROOT / rel_path
    return p.exists(), p

print("=" * 70)
print("AETHER PROJECT: PILLAR 2 STATIC CODE AUDIT")
print("=" * 70)

# 1. Audit Parsers
print("\n[1] CHARACTER SHEET INGESTION & PARSERS")
for f in AUDIT_TARGETS["parsers"]:
    exists, full_p = check_file(f)
    if exists:
        content = full_p.read_text(encoding="utf-8", errors="ignore")
        classes = re.findall(r'class\s+([A-Za-z0-9_]+)', content)
        methods = re.findall(r'def\s+([A-Za-z0-9_]+)', content)
        print(f"  FOUND: {f}")
        print(f"    Classes: {classes}")
        print(f"    Key Functions: {methods[:6]}... (Total: {len(methods)})")
    else:
        print(f"  [MISSING] {f}")

# 2. Audit Schema & Validation
print("\n[2] SCHEMA DEFINITIONS & VALIDATION")
schema_found = False
for f in AUDIT_TARGETS["schema"]:
    exists, full_p = check_file(f)
    if exists:
        schema_found = True
        if full_p.is_dir():
            files = list(full_p.glob("*.py")) + list(full_p.glob("*.json"))
            print(f"  FOUND DIRECTORY: {f} -> Files: {[p.name for p in files]}")
        else:
            content = full_p.read_text(encoding="utf-8", errors="ignore")
            print(f"  FOUND FILE: {f} (Length: {len(content)} chars)")
if not schema_found:
    print("  [MISSING] Dedicated schema or validation modules not found.")

# 3. Audit Spellcasting Constraints & Locks
print("\n[3] CLASS & SPELLCASTING CONSTRAINTS")
spell_locks_found = False
for f in AUDIT_TARGETS["spell_locks"]:
    exists, full_p = check_file(f)
    if exists:
        spell_locks_found = True
        content = full_p.read_text(encoding="utf-8", errors="ignore")
        print(f"  FOUND: {f}")
        for kw in ["component", "dimension door", "slot_cost", "lock", "restriction"]:
            matches = len(re.findall(kw, content, re.IGNORECASE))
            print(f"    Keyword '{kw}': {matches} occurrences")
if not spell_locks_found:
    print("  [MISSING] No dedicated spell constraint or targeting lock modules found.")

# 4. Audit Character Vault & Graveyard Archival
print("\n[4] CHARACTER VAULT & GRAVEYARD DECOUPLING")
vault_found = False
for f in AUDIT_TARGETS["vault"]:
    exists, full_p = check_file(f)
    if exists:
        vault_found = True
        if full_p.is_dir():
            files = list(full_p.glob("*.py"))
            print(f"  FOUND DIRECTORY: {f} -> Files: {[p.name for p in files]}")
        else:
            content = full_p.read_text(encoding="utf-8", errors="ignore")
            methods = re.findall(r'def\s+([A-Za-z0-9_]+)', content)
            print(f"  FOUND: {f} -> Methods: {methods}")
            graveyard_matches = re.findall(r'graveyard|archive|death|kill', content, re.IGNORECASE)
            print(f"    Archival/Graveyard references: {len(graveyard_matches)}")
if not vault_found:
    print("  [MISSING] No vault management modules found.")

print("\n" + "=" * 70)
print("AUDIT SCRIPT COMPLETE")
print("=" * 70)
