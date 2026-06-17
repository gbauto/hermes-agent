# tests/prompt_library/test_smoke_gelby_default.py
"""Smoke tests for end-to-end gelby-default render (T_SMOKE_1, T_SMOKE_2).

These tests use a mocked cn_render (pre-recorded envelope from fixtures) so
they run deterministically in CI without the real cn binary installed.
The validator card will run the real cn end-to-end smoke separately.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from hermes_agent.prompt_library._version import PROMPT_LIBRARY_SCHEMA
from hermes_agent.prompt_library.warnings import FOSSILIZATION_WARNING

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _setup_tmp_library(tmp_path: Path) -> tuple[Path, Path]:
    """Set up a tmp library root with gelby-default manifest and a blank SOUL.md.

    Returns (library_root, soul_path).
    """
    library_root = tmp_path / "prompt-library"
    profiles = library_root / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)

    # Blank SOUL.md (target — must NOT be modified by render)
    soul_path = tmp_path / "SOUL.md"
    soul_path.write_text("", encoding="utf-8")

    manifest = {
        "schema": PROMPT_LIBRARY_SCHEMA,
        "profile": "gelby-default",
        "tenant": "greg",
        "render": {"prompt": "gelby-default"},
        "targets": {
            "soul": {
                "path": str(soul_path),
                "mode": "replace",
            }
        },
    }
    (profiles / "gelby-default.yaml").write_text(yaml.dump(manifest), encoding="utf-8")

    # Minimal .canopy/ dir
    canopy_dir = library_root / ".canopy"
    canopy_dir.mkdir(parents=True, exist_ok=True)
    (canopy_dir / "prompts.jsonl").write_text("", encoding="utf-8")
    (canopy_dir / "schemas.jsonl").write_text("", encoding="utf-8")
    (canopy_dir / "config.yaml").write_text(
        "project: hermes-prompt-library\nversion: '1'\n", encoding="utf-8"
    )

    return library_root, soul_path


def _mock_cn():
    envelope = json.loads((FIXTURES_DIR / "cn_render_gelby_default.json").read_text())
    return patch.multiple(
        "hermes_agent.prompt_library.render",
        cn_render=MagicMock(return_value=envelope),
        cn_version=MagicMock(return_value="0.2.6"),
        cn_show=MagicMock(return_value={"tags": ["tenant:greg"]}),
    )


# ---------------------------------------------------------------------------
# T_SMOKE_1: end-to-end render of gelby-default to staging
# ---------------------------------------------------------------------------


def test_smoke_gelby_default_renders_to_staging(tmp_path):
    """T_SMOKE_1: end-to-end render of gelby-default writes to staging, not live."""
    from hermes_agent.prompt_library.render import render_profile

    library_root, soul_path = _setup_tmp_library(tmp_path)
    staging = library_root / "staging"

    with _mock_cn():
        result = render_profile(
            "gelby-default",
            library_root=library_root,
            staging_dir=staging,
        )

    # 1. Live SOUL.md UNCHANGED (zero bytes, was empty)
    assert soul_path.read_text() == ""

    # 2. Staging file exists and contains "Telegram Reply Style"
    staged_file = staging / "gelby-default" / "SOUL.md.rendered"
    assert staged_file.exists(), f"Expected {staged_file} to exist"
    staged_content = staged_file.read_text(encoding="utf-8")
    assert "Telegram Reply Style" in staged_content

    # 3. fossilization_warning in result
    assert result["fossilization_warning"] == FOSSILIZATION_WARNING

    # 4. resolved_from contains gelby-default
    assert "gelby-default" in result["resolved_from"]

    # 5. dry_run is True
    assert result["dry_run"] is True


# ---------------------------------------------------------------------------
# T_SMOKE_2: double render is byte-equivalent (deterministic)
# ---------------------------------------------------------------------------


def test_smoke_gelby_default_double_render_is_deterministic(tmp_path):
    """T_SMOKE_2: Two successive renders produce identical staged output."""
    from hermes_agent.prompt_library.render import render_profile

    library_root, soul_path = _setup_tmp_library(tmp_path)
    staging1 = library_root / "staging1"
    staging2 = library_root / "staging2"

    with _mock_cn():
        result1 = render_profile(
            "gelby-default",
            library_root=library_root,
            staging_dir=staging1,
        )
        result2 = render_profile(
            "gelby-default",
            library_root=library_root,
            staging_dir=staging2,
        )

    # Staged outputs are byte-equivalent
    staged1 = (staging1 / "gelby-default" / "SOUL.md.rendered").read_bytes()
    staged2 = (staging2 / "gelby-default" / "SOUL.md.rendered").read_bytes()
    assert staged1 == staged2, "Double render is not byte-equivalent"

    # sections sha256 are identical
    assert result1["sections"] == result2["sections"]

    # params_sha256 identical
    assert result1["params_sha256"] == result2["params_sha256"]
