"""
Aether Desktop - User Memory Tools
Exposes functions for Gemini to query, set, or remove personalized user facts.
"""

from typing import Dict, List, Optional, Any
from core.user_memory import UserMemory

# Default user memory singleton reference
_default_memory: Optional[UserMemory] = None


def get_user_memory() -> UserMemory:
    global _default_memory
    if _default_memory is None:
        _default_memory = UserMemory()
    return _default_memory


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
    results = mem.query_facts(search_term=search_term, category=category, limit=5)
    return {
        "status": "success",
        "search_term": search_term,
        "category_filter": category or "all",
        "count": len(results),
        "results": results
    }

