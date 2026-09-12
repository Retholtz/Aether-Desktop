"""
Aether Desktop - Skill Library & Persistent Memory
Provides storage, dynamic retrieval, validation, and execution of reusable automation routines.
Saved skills are stored in scripts/library/ and indexed in skills_catalog.json,
allowing the agent to recall and execute them with zero latency and sync across devices via Git.
"""

import datetime
import difflib
import json
import os
import re
from typing import Dict, List, Optional

from security.ast_gatekeeper import validate_python_script
from tools.script_runner import ScriptRunner


class SkillLibrary:
    """Manages persistent repository of automation skills with dynamic fuzzy retrieval."""

    def __init__(self, workspace_root: Optional[str] = None):
        self.workspace_root = workspace_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.library_dir = os.path.join(self.workspace_root, "scripts", "library")
        os.makedirs(self.library_dir, exist_ok=True)
        self.catalog_file = os.path.join(self.library_dir, "skills_catalog.json")
        self.script_runner = ScriptRunner(self.workspace_root)
        self._ensure_catalog()

    def _ensure_catalog(self):
        """Initializes empty skills_catalog.json if missing."""
        if not os.path.exists(self.catalog_file):
            self._save_catalog({})

    def _load_catalog(self) -> Dict[str, dict]:
        """Loads the skills catalog from disk."""
        try:
            if os.path.exists(self.catalog_file):
                with open(self.catalog_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            print(f"[SKILL_LIBRARY] Warning: Failed to read {self.catalog_file}: {e}")
        return {}

    def _save_catalog(self, catalog: Dict[str, dict]):
        """Persists the skills catalog to disk."""
        try:
            with open(self.catalog_file, "w", encoding="utf-8") as f:
                json.dump(catalog, f, indent=2)
        except Exception as e:
            print(f"[SKILL_LIBRARY] Error saving catalog: {e}")

    def get_skills_manifest(self) -> List[dict]:
        """Returns structured list of available skills with metadata."""
        catalog = self._load_catalog()
        manifest = []
        for name, meta in catalog.items():
            script_path = os.path.join(self.library_dir, meta.get("file", f"{name}.py"))
            if os.path.exists(script_path):
                manifest.append({
                    "name": name,
                    "description": meta.get("description", ""),
                    "parameters": meta.get("parameters", {}),
                    "file": meta.get("file", f"{name}.py"),
                    "created_at": meta.get("created_at"),
                    "updated_at": meta.get("updated_at")
                })
        return manifest

    def find_relevant_skills(self, query: str, top_k: int = 5) -> List[dict]:
        """
        Lightweight local retrieval using token overlap and difflib fuzzy matching
        against skill names and descriptions to prevent context window bloat.
        """
        manifest = self.get_skills_manifest()
        if not manifest:
            return []

        q_clean = (query or "").strip().lower()
        if not q_clean:
            return manifest[:top_k]

        q_tokens = set(re.findall(r"\w+", q_clean))
        scored = []

        for skill in manifest:
            name = skill["name"].lower()
            desc = skill.get("description", "").lower()
            combined_text = f"{name} {desc}"
            skill_tokens = set(re.findall(r"\w+", combined_text))

            # 1. Token overlap score (Jaccard similarity)
            overlap = len(q_tokens & skill_tokens)
            token_score = (overlap / len(q_tokens)) if q_tokens else 0.0

            # 2. Substring & fuzzy ratio matching
            fuzzy_name = difflib.SequenceMatcher(None, q_clean, name).ratio()
            fuzzy_desc = difflib.SequenceMatcher(None, q_clean, desc).ratio() if len(q_clean) < 80 else 0.0
            contains_bonus = 0.4 if (name in q_clean or any(t in name for t in q_tokens if len(t) > 2)) else 0.0

            total_score = (token_score * 0.5) + (fuzzy_name * 0.3) + (fuzzy_desc * 0.1) + contains_bonus
            scored.append((total_score, skill))

        scored.sort(key=lambda x: x[0], reverse=True)
        if q_clean:
            filtered = [item[1] for item in scored if item[0] >= 0.15]
            return filtered[:top_k]
        return [item[1] for item in scored[:top_k]]

    def get_manifest_summary(
        self,
        query: Optional[str] = None,
        max_compact: int = 15,
        top_k: int = 5
    ) -> str:
        """
        Returns a clean plain-text summary of skills.
        If catalog <= max_compact (15), provides all skills.
        If catalog > max_compact, dynamically selects top_k most relevant skills and includes fallback tool instruction.
        """
        manifest = self.get_skills_manifest()
        if not manifest:
            return "No skills currently in library."

        total_count = len(manifest)
        if total_count <= max_compact:
            selected_skills = manifest
            header_notice = ""
        else:
            selected_skills = self.find_relevant_skills(query or "", top_k=top_k)
            header_notice = (
                f"\n(Surfacing top {len(selected_skills)} of {total_count} saved skills. "
                "To search and inspect additional skills, invoke `list_available_skills(query)`.)"
            )

        lines = []
        for s in selected_skills:
            params = s.get("parameters", {})
            param_desc = ", ".join(params.keys()) if isinstance(params, dict) else ""
            lines.append(f"- `{s['name']}({param_desc})`: {s.get('description', '')}")

        summary = "\n".join(lines)
        if header_notice:
            summary += header_notice
        return summary

    def find_matching_skill_name(self, intent_description: str, threshold: float = 0.60) -> Optional[str]:
        """
        Checks if an intent description closely matches an existing skill in the library.
        Returns the skill_name if matched, else None.
        """
        manifest = self.get_skills_manifest()
        if not manifest or not intent_description:
            return None

        clean_intent = intent_description.strip().lower()
        intent_tokens = set(re.findall(r"\w+", clean_intent))

        best_match = None
        best_score = 0.0

        for s in manifest:
            name = s["name"].lower()
            desc = s.get("description", "").lower()
            name_tokens = set(re.findall(r"\w+", name))
            desc_tokens = set(re.findall(r"\w+", desc))

            # Direct name in intent
            if name.replace("_", " ") in clean_intent or clean_intent in name.replace("_", " "):
                return s["name"]

            # Token overlap against description
            combined_tokens = name_tokens | desc_tokens
            overlap = len(intent_tokens & combined_tokens)
            token_score = overlap / max(1, len(intent_tokens))

            fuzzy_score = difflib.SequenceMatcher(None, clean_intent, desc).ratio()
            combined_score = max(token_score, fuzzy_score)

            if combined_score > best_score and combined_score >= threshold:
                best_score = combined_score
                best_match = s["name"]

        return best_match

    def update_skill_code(self, skill_name: str, script_code: str) -> dict:
        """Updates the source code and timestamp of an existing skill."""
        clean_name = re.sub(r"[^a-zA-Z0-9_]", "_", skill_name.strip().lower())
        catalog = self._load_catalog()

        if clean_name not in catalog:
            return {"status": "not_found", "error": f"Skill '{skill_name}' not found."}

        clean_code = (script_code or "").strip()
        if not clean_code:
            return {"status": "error", "error": "script_code cannot be empty."}

        is_safe, error_msg = validate_python_script(clean_code)
        if not is_safe:
            return {"status": "blocked", "error": f"AST Gatekeeper Rejected: {error_msg}"}

        filename = catalog[clean_name].get("file", f"{clean_name}.py")
        script_path = os.path.join(self.library_dir, filename)

        try:
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(clean_code)

            catalog[clean_name]["updated_at"] = datetime.datetime.now().isoformat()
            self._save_catalog(catalog)

            return {
                "status": "success",
                "skill_name": clean_name,
                "file": script_path,
                "message": f"Successfully updated skill '{clean_name}' with optimized code."
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def run_skill(self, skill_name: str, args: Optional[dict] = None) -> dict:
        """Executes a saved skill from the library with arguments."""
        clean_name = skill_name.lower().strip()
        catalog = self._load_catalog()

        if clean_name not in catalog:
            # Check case-insensitive / partial match
            matched = [k for k in catalog if k.lower() == clean_name or clean_name in k.lower()]
            if matched:
                clean_name = matched[0]
            else:
                available = list(catalog.keys())
                return {
                    "status": "not_found",
                    "error": f"Skill '{skill_name}' not found in library.",
                    "available_skills": available,
                    "message": f"Skill '{skill_name}' does not exist. Available skills: {available}"
                }

        meta = catalog[clean_name]
        filename = meta.get("file", f"{clean_name}.py")
        script_path = os.path.join(self.library_dir, filename)

        if not os.path.exists(script_path):
            return {
                "status": "error",
                "error": f"Script file '{filename}' missing from {self.library_dir}",
                "message": f"Skill file '{filename}' is missing from the repository."
            }

        desc = meta.get("description", f"Run skill {clean_name}")
        res = self.script_runner.execute_script_file(script_path, args=args, description=desc)
        res["skill_name"] = clean_name
        return res

    def save_skill(
        self,
        skill_name: str,
        description: str,
        script_code: str,
        parameters: Optional[dict] = None
    ) -> dict:
        """Saves a tested script into the permanent Skill Library."""
        clean_name = re.sub(r"[^a-zA-Z0-9_]", "_", skill_name.strip().lower())
        if not clean_name:
            return {"status": "error", "error": "Invalid skill_name."}

        clean_code = (script_code or "").strip()
        if not clean_code:
            return {"status": "error", "error": "script_code cannot be empty."}

        # Validate with AST gatekeeper
        is_safe, error_msg = validate_python_script(clean_code)
        if not is_safe:
            return {
                "status": "blocked",
                "error": f"AST Gatekeeper Rejected: {error_msg}",
                "message": f"Cannot save skill: {error_msg}"
            }

        filename = f"{clean_name}.py"
        script_path = os.path.join(self.library_dir, filename)

        try:
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(clean_code)

            catalog = self._load_catalog()
            now_iso = datetime.datetime.now().isoformat()
            catalog[clean_name] = {
                "description": description.strip() or f"Automation skill: {clean_name}",
                "parameters": parameters or {},
                "file": filename,
                "created_at": catalog.get(clean_name, {}).get("created_at", now_iso),
                "updated_at": now_iso
            }
            self._save_catalog(catalog)

            return {
                "status": "success",
                "skill_name": clean_name,
                "file": script_path,
                "message": f"Successfully saved skill '{clean_name}' to permanent library."
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "message": f"Failed to save skill: {e}"}
