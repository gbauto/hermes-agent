# hermes_agent/prompt_library/canopy.py
"""Canopy CLI subprocess wrapper (internal — not exported from __init__.py).

All subprocess calls use subprocess.run(timeout=30), capture both streams,
and route stderr into CanopyCliError.message. Working directory is always
the Canopy project root (<library_root>/.canopy/../), never the caller's cwd.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from hermes_agent.prompt_library.errors import CanopyCliError, CanopyMissingError


def cn_path() -> Optional[str]:
    """Locate the cn binary via shutil.which('cn'). Returns None if missing."""
    return shutil.which("cn")


def cn_version() -> str:
    """`cn --version` parse. Raises CanopyMissingError if cn not found.
    Raises CanopyCliError on non-zero exit.
    """
    binary = cn_path()
    if binary is None:
        raise CanopyMissingError()

    result = subprocess.run(
        [binary, "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise CanopyCliError(
            "cn --version failed",
            returncode=result.returncode,
            stderr=result.stderr,
        )

    # Parse version string: typically "canopy/0.2.6 ..." or just "0.2.6"
    output = (result.stdout or result.stderr or "").strip()
    match = re.search(r"(\d+\.\d+\.\d+)", output)
    if match:
        return match.group(1)
    return output or "unknown"


def cn_render(
    prompt: str,
    *,
    project_dir: Path,
    format: str = "json",
) -> dict:
    """Run `cn render <prompt> --format json` inside project_dir.

    Returns parsed JSON envelope:
    {
        "success": bool,
        "name": str,
        "version": int,
        "sections": [{"name": str, "body": str}, ...],
        "resolvedFrom": [str, ...],
        "frontmatter": dict | None,
        "mulch": None | dict,
    }

    Raises CanopyMissingError if cn not on PATH.
    Raises CanopyCliError on non-zero exit or JSON parse failure.
    """
    binary = cn_path()
    if binary is None:
        raise CanopyMissingError()

    cmd = [binary, "render", prompt, "--format", format]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(project_dir),
    )
    if result.returncode != 0:
        raise CanopyCliError(
            f"cn render {prompt!r} failed",
            returncode=result.returncode,
            stderr=result.stderr,
        )

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CanopyCliError(
            f"cn render {prompt!r} returned non-JSON output: {result.stdout[:200]}",
        ) from exc

    return envelope


def cn_show(
    prompt: str,
    *,
    project_dir: Path,
) -> dict:
    """`cn show <prompt> --json` for tag/tenant introspection.

    Raises CanopyMissingError if cn not on PATH.
    Raises CanopyCliError on failure.
    """
    binary = cn_path()
    if binary is None:
        raise CanopyMissingError()

    result = subprocess.run(
        [binary, "show", prompt, "--json"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(project_dir),
    )
    if result.returncode != 0:
        raise CanopyCliError(
            f"cn show {prompt!r} failed",
            returncode=result.returncode,
            stderr=result.stderr,
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CanopyCliError(
            f"cn show {prompt!r} returned non-JSON: {result.stdout[:200]}",
        ) from exc


def cn_validate(
    prompt: str,
    *,
    project_dir: Path,
) -> dict:
    """`cn validate <prompt> --json`. Returns {valid, errors[], warnings[]}.

    Adapter calls this in render_profile step 4.5 (pre-route validation).
    Raises CanopyMissingError if cn not on PATH.
    Raises CanopyCliError on CLI failure.
    """
    binary = cn_path()
    if binary is None:
        raise CanopyMissingError()

    result = subprocess.run(
        [binary, "validate", prompt, "--json"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(project_dir),
    )
    # validate may exit non-zero on validation failure (not a CLI error per se)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        if result.returncode != 0:
            raise CanopyCliError(
                f"cn validate {prompt!r} failed",
                returncode=result.returncode,
                stderr=result.stderr,
            )
        return {"valid": False, "errors": [result.stdout], "warnings": []}
