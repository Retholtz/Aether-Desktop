import os
from pathlib import Path
from typing import Optional

# Directories that contain deep trees and are not relevant for skill scripts
EXCLUDED_DIRS = {
    "appdata",
    "node_modules",
    "venv",
    ".venv",
    "env",
    ".env",
    ".git",
    ".svn",
    "__pycache__",
    "site-packages",
    "temp",
    "tmp",
    ".cache",
    "windows",
}


def find_and_read_file(
    target_filename: str,
    search_root: Optional[str] = None,
    max_depth: int = 5,
) -> Optional[str]:
    """Deterministically locates a target file using prioritized lookups and

    directory pruning to avoid filesystem crawl latency.
    """
    target_name_lower = target_filename.lower()

    # Priority 1: Current working directory and immediate workspace
    cwd = Path.cwd()
    immediate_candidate = cwd / target_filename
    if immediate_candidate.is_file():
        content = immediate_candidate.read_text(encoding="utf-8")
        print(f"Found: {immediate_candidate}")
        print(content)
        return content

    # Priority 2: Targeted walk starting from user root with aggressive pruning
    base_path = Path(search_root) if search_root else Path.home()
    if not base_path.exists():
        return None

    base_depth = len(base_path.parts)

    for current_root, dirs, files in os.walk(base_path):
        current_depth = len(Path(current_root).parts) - base_depth

        # In-place directory pruning to skip deep/irrelevant trees
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".")
            and d.lower() not in EXCLUDED_DIRS
            and current_depth < max_depth
        ]

        for file in files:
            if file.lower() == target_name_lower:
                file_path = Path(current_root) / file
                try:
                    content = file_path.read_text(encoding="utf-8")
                    print(f"Found: {file_path}")
                    print(content)
                    return content
                except OSError as err:
                    print(f"Failed to read {file_path}: {err}")
                    return None

    print(f"File '{target_filename}' not found.")
    return None


if __name__ == "__main__":
    target = os.getenv("TARGET_SKILL_FILE", "create_table_google_docs.py")
    search_dir = os.getenv("TARGET_SEARCH_ROOT", str(Path.home()))

    find_and_read_file(target_filename=target, search_root=search_dir)