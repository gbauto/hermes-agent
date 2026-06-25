"""Cross-reference Hermes skills/profiles against the GBAutomation Canopy prompt system.

The Canopy source of truth lives in the sibling ``gbautomation`` repo:

- Profile manifests (``resources/skills/hermes-profile-templates/**/profiles/*.manifest.yaml``,
  schema ``hermes.prompt_profile.v1``) declare which profiles COMPOSE Canopy mixins via
  their ``inherits:`` list.
- The snippet inventory (``artifacts/canopy-snippet-index/canopy-snippets.json``) is the
  fleetwide prompt-fragment library, owned by the ``canopy`` skill and authored via
  ``tac-canopy-snippet``.

The dashboard uses this to earmark Canopy-connected rows on ``/api/skills`` and
``/api/profiles``. Resolution is best-effort and never raises: if the gbautomation repo
is not reachable, profiles report ``None`` and only the core Canopy skills are flagged.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

# Skills that ARE the Canopy system, independent of repo presence.
_CANOPY_CORE_SKILLS = {"canopy", "tac-canopy-snippet"}


def _gbauto_repo() -> Path | None:
    """Locate the gbautomation repo: explicit env override, else a sibling of this repo."""
    for env in ("GBAUTO_REPO", "GBAUTO_SECOND_BRAIN_PATH"):
        val = os.environ.get(env)
        if not val:
            continue
        p = Path(val)
        if p.name == "second-brain":  # env may point at the vault dir
            p = p.parent
        if (p / "resources" / "skills" / "canopy").exists():
            return p
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "gbautomation"
        if (cand / "resources" / "skills" / "canopy").exists():
            return cand
    return None


def _yaml_scalar(text: str, key: str) -> str | None:
    m = re.search(rf"^{re.escape(key)}:[ \t]*(.+?)[ \t]*$", text, re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip().strip("'\"") or None


def _yaml_list_block(text: str, key: str) -> list[str]:
    """Parse a simple block list:  key:\\n  - a\\n  - b  -> ['a','b']."""
    lines = text.splitlines()
    out: list[str] = []
    in_block = False
    for line in lines:
        if re.match(rf"^{re.escape(key)}:[ \t]*$", line):
            in_block = True
            continue
        if in_block:
            item = re.match(r"^[ \t]+-[ \t]*(.+?)[ \t]*$", line)
            if item:
                out.append(item.group(1).strip().strip("'\""))
                continue
            if line.strip() == "":
                continue
            break  # dedent / next key ends the block
    return out


@lru_cache(maxsize=1)
def _xref() -> dict[str, Any]:
    repo = _gbauto_repo()
    profiles: dict[str, list[str]] = {}
    skills: set[str] = set(_CANOPY_CORE_SKILLS)
    snippet_count = 0
    domains: set[str] = set()

    if repo is not None:
        tmpl = repo / "resources" / "skills" / "hermes-profile-templates"
        if tmpl.is_dir():
            for man in tmpl.rglob("*.manifest.yaml"):
                try:
                    body = man.read_text(encoding="utf-8")
                except OSError:
                    continue
                if "hermes.prompt_profile" not in body:
                    continue
                name = _yaml_scalar(body, "profile") or man.stem.replace(".manifest", "")
                profiles[name] = _yaml_list_block(body, "inherits")

        idx = repo / "artifacts" / "canopy-snippet-index" / "canopy-snippets.json"
        if idx.exists():
            try:
                data = json.loads(idx.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            snips = data.get("snippets", []) if isinstance(data, dict) else []
            snippet_count = len(snips)
            for s in snips:
                if not isinstance(s, dict):
                    continue
                if s.get("domain"):
                    domains.add(s["domain"])
                m = re.match(r"resources/skills/([^/]+)/", s.get("source_path", ""))
                if m:
                    skills.add(m.group(1))

    return {
        "profiles": profiles,
        "skills": skills,
        "snippet_count": snippet_count,
        "domains": sorted(d for d in domains if d),
        "repo": str(repo) if repo else None,
    }


def profile_canopy(name: str) -> list[str] | None:
    """Return a Canopy-composed profile's inherited mixins, or None if it is not Canopy."""
    try:
        return _xref()["profiles"].get(name)
    except Exception:
        return None


def skill_is_canopy(name: str) -> bool:
    """True if a skill is part of the Canopy system (owns/authors snippets, or is canopy)."""
    try:
        return name in _xref()["skills"]
    except Exception:
        return name in _CANOPY_CORE_SKILLS


def summary() -> dict[str, Any]:
    """Fleetwide Canopy summary (snippet count, domains, repo) for dashboard surfaces."""
    try:
        x = _xref()
        return {
            "snippet_count": x["snippet_count"],
            "domains": x["domains"],
            "canopy_profiles": sorted(x["profiles"].keys()),
            "repo": x["repo"],
        }
    except Exception:
        return {"snippet_count": 0, "domains": [], "canopy_profiles": [], "repo": None}
