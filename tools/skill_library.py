"""
Aether Desktop - Skill Library & Persistent Memory
Provides storage, discovery, validation, and execution of reusable automation routines.
Saved skills are stored in scripts/library/ and indexed in skills_catalog.json,
allowing the agent to recall and execute them with zero latency and sync across devices via Git.
"""

import json
import os
import re
from typing import Dict, List, Optional

from security.ast_gatekeeper import validate_python_script
from tools.script_runner import ScriptRunner


class SkillLibrary:
    """Manages persistent repository of automation skills."""

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
                    "file": meta.get("file", f"{name}.py")
                })
        return manifest

    def get_manifest_summary(self) -> str:
        """Returns a clean plain-text summary of available skills for system prompt injection."""
        manifest = self.get_skills_manifest()
        if not manifest:
            return "No skills currently in library."
        lines = []
        for s in manifest:
            params = s.get("parameters", {})
            param_desc = ", ".join(params.keys()) if isinstance(params, dict) else ""
            lines.append(f"- `{s['name']}({param_desc})`: {s.get('description', '')}")
        return "\n".join(lines)

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
            catalog[clean_name] = {
                "description": description.strip() or f"Automation skill: {clean_name}",
                "parameters": parameters or {},
                "file": filename
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

