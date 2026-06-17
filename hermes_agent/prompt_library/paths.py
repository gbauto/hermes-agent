# hermes_agent/prompt_library/paths.py
"""Path helpers for the Canopy-backed prompt library adapter.

All paths are resolved relative to the library root, which defaults to
~/.hermes/prompt-library/ (via get_hermes_home() if available, else
direct expansion).
"""

from __future__ import annotations

from pathlib import Path


def get_default_library_root() -> Path:
    """Return the default ~/.hermes/prompt-library/ path.

    Tries to use hermes_constants.get_hermes_home() so the path is
    profile-aware; falls back to ~/.hermes if the import fails.
    """
    try:
        from hermes_constants import get_hermes_home  # type: ignore[import]

        hermes_home = Path(get_hermes_home())
    except Exception:
        hermes_home = Path.home() / ".hermes"
    return hermes_home / "prompt-library"


def resolve_library_root(library_root: Path | None) -> Path:
    """Resolve a library_root arg; use the default if None."""
    if library_root is None:
        return get_default_library_root()
    return Path(library_root).expanduser().resolve()


def canopy_project_dir(library_root: Path) -> Path:
    """Return the Canopy project directory (.canopy/)."""
    return library_root / ".canopy"


def profiles_dir(library_root: Path) -> Path:
    """Return the profiles/ directory."""
    return library_root / "profiles"


def receipts_dir(library_root: Path, profile: str) -> Path:
    """Return the receipts/<profile>/ directory."""
    return library_root / "receipts" / profile


def backups_dir(library_root: Path, profile: str) -> Path:
    """Return the backups/<profile>/ directory."""
    return library_root / "backups" / profile


def staging_dir(library_root: Path, profile: str) -> Path:
    """Return the staging/<profile>/ directory."""
    return library_root / "staging" / profile


def manifest_path(library_root: Path, profile: str) -> Path:
    """Return the path to profiles/<profile>.yaml."""
    return profiles_dir(library_root) / f"{profile}.yaml"
