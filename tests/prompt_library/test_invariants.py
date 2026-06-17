# tests/prompt_library/test_invariants.py
"""T_INV_1: Dry-run invariant — render_profile must NEVER write outside library root."""

from __future__ import annotations

import builtins
import json
import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from hermes_agent.prompt_library._version import PROMPT_LIBRARY_SCHEMA

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _setup_tmp_library(tmp_path: Path) -> tuple[Path, Path]:
    """Set up a minimal library root. Returns (library_root, live_soul_path)."""
    library_root = tmp_path / "prompt-library"
    profiles = library_root / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)

    # Simulated "live" SOUL.md at a different path (not under library_root)
    live_soul = tmp_path / "live" / "SOUL.md"
    live_soul.parent.mkdir(parents=True, exist_ok=True)
    live_soul.write_text("LIVE ORIGINAL", encoding="utf-8")

    manifest = {
        "schema": PROMPT_LIBRARY_SCHEMA,
        "profile": "gelby-default",
        "tenant": "greg",
        "render": {"prompt": "gelby-default"},
        "targets": {
            "soul": {
                "path": str(live_soul),
                "mode": "replace",
            }
        },
    }
    (profiles / "gelby-default.yaml").write_text(yaml.dump(manifest), encoding="utf-8")

    # .canopy dir
    canopy_dir = library_root / ".canopy"
    canopy_dir.mkdir(parents=True, exist_ok=True)
    (canopy_dir / "prompts.jsonl").write_text("", encoding="utf-8")

    return library_root, live_soul


def _mock_cn(library_root: Path):
    envelope = json.loads((FIXTURES_DIR / "cn_render_gelby_default.json").read_text())
    return patch.multiple(
        "hermes_agent.prompt_library.render",
        cn_render=MagicMock(return_value=envelope),
        cn_version=MagicMock(return_value="0.2.6"),
        cn_show=MagicMock(return_value={"tags": ["tenant:greg"]}),
    )


# ---------------------------------------------------------------------------
# T_INV_1: dry-run invariant
# ---------------------------------------------------------------------------


def test_dry_run_invariant_no_live_writes(tmp_path):
    """T_INV_1: render_profile must not write ANYWHERE outside the library_root.

    This test monkeypatches os.replace, shutil.copy*, and Path.write_text/write_bytes
    to fail if called against any path NOT under library_root.
    """
    library_root, live_soul = _setup_tmp_library(tmp_path)

    write_calls: list[str] = []

    # Track any write attempt outside library_root
    original_write_text = Path.write_text
    original_write_bytes = Path.write_bytes
    original_os_replace = os.replace
    original_shutil_copy2 = shutil.copy2
    original_shutil_copy = shutil.copy

    def guarded_write_text(self, *args, **kwargs):
        path = str(self.resolve()) if self.exists() else str(self)
        if not (
            path.startswith(str(library_root))
            or path.startswith(str(tmp_path / "prompt-library"))
        ):
            write_calls.append(f"write_text: {path}")
            raise AssertionError(
                f"T_INV_1 VIOLATION: write_text called on LIVE path {path!r}"
            )
        return original_write_text(self, *args, **kwargs)

    def guarded_write_bytes(self, *args, **kwargs):
        path = str(self)
        if not path.startswith(str(library_root)):
            write_calls.append(f"write_bytes: {path}")
            raise AssertionError(
                f"T_INV_1 VIOLATION: write_bytes called on LIVE path {path!r}"
            )
        return original_write_bytes(self, *args, **kwargs)

    def guarded_os_replace(src, dst):
        if not str(dst).startswith(str(library_root)):
            write_calls.append(f"os.replace: {dst}")
            raise AssertionError(
                f"T_INV_1 VIOLATION: os.replace to LIVE path {dst!r}"
            )
        return original_os_replace(src, dst)

    def guarded_copy2(src, dst):
        if not str(dst).startswith(str(library_root)):
            write_calls.append(f"shutil.copy2: {dst}")
            raise AssertionError(
                f"T_INV_1 VIOLATION: shutil.copy2 to LIVE path {dst!r}"
            )
        return original_shutil_copy2(src, dst)

    def guarded_copy(src, dst):
        if not str(dst).startswith(str(library_root)):
            write_calls.append(f"shutil.copy: {dst}")
            raise AssertionError(
                f"T_INV_1 VIOLATION: shutil.copy to LIVE path {dst!r}"
            )
        return original_shutil_copy(src, dst)

    with _mock_cn(library_root), patch.object(
        Path, "write_text", guarded_write_text
    ), patch.object(
        Path, "write_bytes", guarded_write_bytes
    ), patch(
        "os.replace", guarded_os_replace
    ), patch(
        "shutil.copy2", guarded_copy2
    ), patch(
        "shutil.copy", guarded_copy
    ):
        from hermes_agent.prompt_library.render import render_profile

        # Use a staging dir inside library_root (allowed)
        staging = library_root / "staging"
        result = render_profile(
            "gelby-default",
            library_root=library_root,
            staging_dir=staging,
        )

    # If we get here, no live writes happened
    assert write_calls == [], f"Live write violations detected: {write_calls}"

    # Confirm live soul was not touched
    assert live_soul.read_text() == "LIVE ORIGINAL"

    # Confirm staging was written (allowed inside library_root)
    staged = staging / "gelby-default" / "SOUL.md.rendered"
    assert staged.exists()
