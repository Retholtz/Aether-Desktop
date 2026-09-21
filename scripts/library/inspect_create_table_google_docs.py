from pathlib import Path
from typing import Iterable, Optional


def locate_file(
    filename: str,
    search_dirs: Optional[Iterable[Path]] = None,
    max_depth: int = 4,
) -> Optional[Path]:
    """Deterministically locate a file across prioritized directory targets

    without full filesystem scans.
    """
    home = Path.home()
    if search_dirs is None:
        search_dirs = (
            home
            / "StudioProjects"
            / "Aether Desktop"
            / "Aether-Desktop"
            / "skills",
            home / "StudioProjects",
            Path.cwd(),
        )

    # Fast path: Check direct locations first
    for base in search_dirs:
        direct_target = base / filename if base.is_dir() else base
        if direct_target.is_file():
            return direct_target

    # Bounded search: limit recursion depth to prevent unbounded I/O lag
    for base in search_dirs:
        if not base.is_dir():
            continue
        base_depth = len(base.parts)
        for path in base.rglob(filename):
            if len(path.parts) - base_depth <= max_depth and path.is_file():
                return path

    return None


def inspect_skill(
    filename: str = "create_table_google_docs.py",
    preview_limit: int = 1000,
) -> None:
    target_path = locate_file(filename)
    if not target_path:
        raise FileNotFoundError(
            f"Unable to locate '{filename}' in target search paths."
        )

    with target_path.open("r", encoding="utf-8") as file_stream:
        content = file_stream.read(preview_limit)
        print(content)


if __name__ == "__main__":
    inspect_skill()