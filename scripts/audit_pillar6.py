"""
scripts/audit_pillar6.py - Automated Static Codebase Inspection for Pillar 6
(Session Memory, Cold-Start Briefings & Lore Retrieval Audit)
"""
import os
import re
from pathlib import Path

REPO_ROOT = Path(".").resolve()

AUDIT_TARGETS = {
    "memory_logbook": [
        "core/memory/session_logger.py",
        "core/memory/logbook.py",
        "core/journal.py",
        "core/session_logger.py",
        "core/campaign/campaign_manager.py",
        "core/ttrpg_service.py",
        "core/session_lifecycle.py",
        "core/session_manager.py",
        "core/user_memory.py",
        "core/manifest_indexer.py"
    ],
    "cold_start": [
        "core/briefing.py",
        "core/recap.py",
        "core/cold_start.py",
        "core/proactive_engine.py",
        "core/engine.py"
    ],
    "lore_query": [
        "tools/lore_tools.py",
        "tools/memory_tools.py",
        "tools/campaign_tools.py",
        "tools/ttrpg_tools.py"
    ]
}

def check_file(rel_path):
    p = REPO_ROOT / rel_path
    return p.exists(), p

print("=" * 75)
print("AETHER PROJECT: PILLAR 6 STATIC CODE AUDIT (SESSION MEMORY & LORE)")
print("=" * 75)

# 1. Audit Session Logbook & Clue Persistence
print("\n[1] SESSION LOGBOOK, MILESTONES & CLUE PERSISTENCE")
log_found = False
for f in AUDIT_TARGETS["memory_logbook"]:
    exists, full_p = check_file(f)
    if exists:
        log_found = True
        content = full_p.read_text(encoding="utf-8", errors="ignore")
        classes = re.findall(r'class\s+([A-Za-z0-9_]+)', content)
        methods = re.findall(r'def\s+([A-Za-z0-9_]+)', content)
        print(f"  FOUND: {f}")
        if classes:
            print(f"    Classes: {classes}")
        # Search for key logbook routines
        relevant_methods = [m for m in methods if any(k in m.lower() for k in ("journal", "recap", "clue", "note", "log", "summary", "turn", "session"))]
        print(f"    Relevant Routines: {relevant_methods[:10]}")
        for kw in ["record_campaign_notes", "auto_summarize_session_to_journal", "ClueRecord", "latest_recap", "journal", "QuestStatus"]:
            matches = len(re.findall(kw, content))
            if matches > 0:
                print(f"      Keyword '{kw}': {matches}")
    else:
        # report only if primary expected file
        if f in ["core/memory/session_logger.py", "core/journal.py"]:
            print(f"  [MISSING]: {f}")

# 2. Audit Cold-Start Narrative Briefing Routine
print("\n[2] COLD-START NARRATIVE BRIEFING")
for f in AUDIT_TARGETS["cold_start"]:
    exists, full_p = check_file(f)
    if exists:
        content = full_p.read_text(encoding="utf-8", errors="ignore")
        print(f"  FOUND: {f}")
        methods = re.findall(r'def\s+([A-Za-z0-9_]+)', content)
        briefing_methods = [m for m in methods if any(k in m.lower() for k in ("briefing", "recap", "cold_start", "startup", "resume"))]
        print(f"    Briefing/Startup Methods: {briefing_methods}")
        for kw in ["cold_start", "startup_briefing", "check_proactive_briefing", "auto_briefing", "voice_briefing"]:
            matches = len(re.findall(kw, content, re.IGNORECASE))
            if matches > 0:
                print(f"      Keyword '{kw}': {matches}")
    else:
        print(f"  [MISSING]: {f}")

# 3. Audit On-Demand Lore & Retrieval Tools
print("\n[3] ON-DEMAND LORE & RETRIEVAL TOOLS")
for f in AUDIT_TARGETS["lore_query"]:
    exists, full_p = check_file(f)
    if exists:
        content = full_p.read_text(encoding="utf-8", errors="ignore")
        print(f"  FOUND: {f}")
        methods = re.findall(r'def\s+([A-Za-z0-9_]+)', content)
        lore_methods = [m for m in methods if any(k in m.lower() for k in ("lore", "clue", "query", "search", "journal", "notes", "read"))]
        print(f"    Lore/Query Functions: {lore_methods}")
        for kw in ["query_lore", "search_journal", "read_campaign_document", "get_campaign_notes", "hidden_secrets", "is_revealed"]:
            matches = len(re.findall(kw, content))
            if matches > 0:
                print(f"      Keyword '{kw}': {matches}")
    else:
        print(f"  [MISSING]: {f}")

print("\n" + "=" * 75)
print("AUDIT SCRIPT COMPLETE")
print("=" * 75)
