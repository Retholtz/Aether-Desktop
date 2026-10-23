"""
Aether Desktop - User Memory Tools
Exposes functions for Gemini to query, set, or remove personalized user facts.
"""

import threading
from typing import Dict, List, Optional, Any
from core.user_memory import UserMemory, get_user_memory, _MEMORY_SINGLETON, _MEMORY_INIT_LOCK

# Default user memory singleton reference
_default_memory = _MEMORY_SINGLETON



def remember_user_fact(
    category: str,
    key: str,
    value: str,
    data_type: str = "string",
    memory: Optional[UserMemory] = None
) -> Dict[str, Any]:
    """Inserts or updates a user fact into user_facts."""
    mem = memory or get_user_memory()
    return mem.remember_fact(category=category, key=key, value=value, data_type=data_type)


def forget_user_fact(
    category: str,
    key: str,
    memory: Optional[UserMemory] = None
) -> Dict[str, Any]:
    """Removes the specified key from user_facts."""
    mem = memory or get_user_memory()
    return mem.forget_fact(category=category, key=key)


def get_user_profile(
    category: Optional[str] = None,
    memory: Optional[UserMemory] = None
) -> Dict[str, Any]:
    """Returns active facts so the model can inspect context on demand."""
    mem = memory or get_user_memory()
    facts = mem.get_facts(category=category)
    return {
        "status": "success",
        "category_filter": category or "all",
        "count": len(facts),
        "facts": facts
    }


def query_user_memory(
    search_term: str,
    category: Optional[str] = None,
    memory: Optional[UserMemory] = None
) -> Dict[str, Any]:
    """Queries user memory dynamically for keywords, family facts, dates, preferences, or setups."""
    mem = memory or get_user_memory()
    facts = mem.query_facts(search_term=search_term, category=category, limit=5)
    if not facts:
        return {
            "status": "not_found_in_user_profile",
            "search_term": search_term,
            "category_filter": category or "all",
            "count": 0,
            "facts": [],
            "results": [],
            "directive": f"No stored personal facts match '{search_term}'. If the user is asking about an acquaintance, public figure, or external topic, immediately execute google_search."
        }
    return {
        "status": "success",
        "search_term": search_term,
        "category_filter": category or "all",
        "count": len(facts),
        "facts": facts,
        "results": facts
    }


def teach_word_pronunciation(
    term: str,
    phonetic_guide: str,
    category: str = "name",
    memory: Optional[UserMemory] = None
) -> Dict[str, Any]:
    """
    Teaches the assistant how to pronounce and transcribe an atypical word, surname, or technical jargon.
    Persists the term and its phonetic syllable breakdown into the custom lexicon dictionary.
    """
    mem = memory or get_user_memory()
    return mem.add_dictionary_term(term=term, phonetic_guide=phonetic_guide, category=category)


def search_past_sessions(query: str, limit: int = 4, db_path: Optional[str] = None) -> str:
    """
    Search historical session manifest cards for topics, actions, unresolved tasks, or entities.
    Use this when the user asks about past conversations, previous decisions, or earlier tasks.
    """
    import os
    from core.manifest_indexer import search_manifest_index, init_manifest_db
    init_manifest_db(db_path)
    results = search_manifest_index(query=query, limit=limit, db_path=db_path)

    # In normal runtime (db_path is None), also check data/dossiers/ (filename + full-text content)
    # and recent unindexed transcripts in data/chats/*.json
    dossier_matches = []
    raw_chat_matches = []
    if db_path is None and query and query.strip():
        q_raw = query.strip().lower()
        q_terms = [w.lower() for w in query.strip().split() if len(w) >= 3]
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        try:
            dossier_dir = os.path.join(base_dir, "data", "dossiers")
            if os.path.exists(dossier_dir):
                is_generic_doc_query = any(w in ("document", "dossier", "guide", "report", "recipe") for w in q_terms)
                for fname in sorted(os.listdir(dossier_dir), reverse=True):
                    if not fname.endswith(".md"):
                        continue
                    fpath = os.path.join(dossier_dir, fname)
                    rel_path = os.path.join("data", "dossiers", fname).replace("\\", "/")
                    fname_lower = fname.lower()
                    matched = is_generic_doc_query or (q_raw in fname_lower) or any(t in fname_lower for t in q_terms)
                    snippet_preview = ""
                    if not matched and q_terms:
                        try:
                            with open(fpath, "r", encoding="utf-8", errors="ignore") as df:
                                content_Sample = df.read(24000)
                            c_low = content_Sample.lower()
                            if q_raw in c_low or any(t in c_low for t in q_terms):
                                matched = True
                                hit_term = q_raw if q_raw in c_low else next((t for t in q_terms if t in c_low), "")
                                idx = c_low.find(hit_term) if hit_term else 0
                                start = max(0, idx - 80)
                                end = min(len(content_Sample), idx + 180)
                                snippet_preview = " | Excerpt: " + content_Sample[start:end].replace("\n", " ").strip()
                        except Exception:
                            pass
                    if matched:
                        dossier_matches.append(
                            f"- [SAVED DOSSIER ON DISK] `{rel_path}`{snippet_preview} (Available right now: read this file and use `from tools.clipboard import paste_markdown_to_target_app, copy_markdown_as_rich_html` via `execute_automation_script` instead of regenerating!)"
                        )
                        if len(dossier_matches) >= limit:
                            break
        except Exception:
            pass

        if not results and q_terms:
            try:
                import json
                chats_dir = os.path.join(base_dir, "data", "chats")
                if os.path.exists(chats_dir):
                    chat_files = [f for f in os.listdir(chats_dir) if f.endswith(".json")]
                    chat_files.sort(reverse=True)
                    for cfile in chat_files[:25]:
                        cpath = os.path.join(chats_dir, cfile)
                        try:
                            with open(cpath, "r", encoding="utf-8") as cf:
                                cdata = json.load(cf)
                            turns = cdata.get("turns", [])
                            matched_snippets = []
                            for t in turns:
                                txt = str(t.get("text", ""))
                                t_low = txt.lower()
                                if q_raw in t_low or any(term in t_low for term in q_terms):
                                    matched_snippets.append(f"{t.get('role', 'user')}: {txt[:160]}")
                                    if len(matched_snippets) >= 2:
                                        break
                            if matched_snippets:
                                sid = cdata.get("session_id", cfile[:-5])
                                raw_chat_matches.append(
                                    f"- [Transcript {sid}] " + " | ".join(matched_snippets)
                                )
                                if len(raw_chat_matches) >= limit:
                                    break
                        except Exception:
                            pass
            except Exception:
                pass

    if not results and not dossier_matches and not raw_chat_matches:
        return (
            f"No previous sessions matched query: '{query}'. "
            f"[Directive: No past sessions match '{query}'. If this inquiry concerns an external person, entity, or general topic, immediately execute google_search.]"
        )

    summaries = []
    if dossier_matches:
        summaries.extend(dossier_matches)
    if raw_chat_matches:
        summaries.extend(raw_chat_matches)
    for r in results:
        topics_str = ", ".join(r["topics"]) if r["topics"] else "None"
        actions_str = ", ".join(r["actions"]) if r["actions"] else "None"
        unresolved_str = ", ".join(r["unresolved"]) if r["unresolved"] else "None"
        entities_str = ", ".join(r["entities"]) if r["entities"] else "None"
        summaries.append(
            f"- [{r['date']}] Session {r['session_id']}:\n"
            f"  Topics: {topics_str}\n"
            f"  Actions: {actions_str}\n"
            f"  Unresolved: {unresolved_str}\n"
            f"  Entities: {entities_str}"
        )
    return "\n".join(summaries)



