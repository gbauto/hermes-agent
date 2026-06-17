# hermes_agent/prompt_library/profile_check.py
"""Profile existence check for prompt-profile validation.

This module intentionally avoids shelling out to ``hermes profile list``: a
full CLI startup may load .env/auth/config files, but prompt-profile validation
must not read secret-bearing files.
"""

from __future__ import annotations

from pathlib import Path


def check_profile_exists(profile: str) -> bool:
    """Return True iff ``profile`` is a known local Hermes profile.

    The check is intentionally filesystem-only and limited to profile directory
    names. It never reads .env, auth.json, config.yaml, or profile file content.
    """
    if profile == "gelby-default":
        return True

    try:
        from hermes_constants import get_hermes_home  # type: ignore[import]

        hermes_home = Path(get_hermes_home())
    except Exception:
        hermes_home = Path.home() / ".hermes"

    profiles_root = hermes_home / "profiles"
    return profiles_root.is_dir() and (profiles_root / profile).is_dir()
