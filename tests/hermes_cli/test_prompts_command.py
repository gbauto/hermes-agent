# tests/hermes_cli/test_prompts_command.py
"""CLI integration tests for hermes prompts (T22, T23, T24)."""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from hermes_agent.prompt_library._version import PROMPT_LIBRARY_SCHEMA
from hermes_agent.prompt_library.errors import CanopyMissingError


def _write_minimal_manifest(library_root: Path, profile: str = "gelby-default") -> None:
    profiles = library_root / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    soul_path = library_root / "SOUL.md"
    soul_path.write_text("", encoding="utf-8")
    manifest = {
        "schema": PROMPT_LIBRARY_SCHEMA,
        "profile": profile,
        "tenant": "greg",
        "render": {"prompt": profile},
        "targets": {
            "soul": {
                "path": str(soul_path),
                "mode": "replace",
            }
        },
    }
    (profiles / f"{profile}.yaml").write_text(yaml.dump(manifest), encoding="utf-8")


def _invoke_prompts(argv: list[str]) -> tuple[int, str, str]:
    """Invoke the prompts CLI and return (exit_code, stdout, stderr)."""
    from hermes_cli.prompts import prompts_command

    old_argv = sys.argv[:]
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = StringIO()
    sys.stderr = StringIO()
    sys.argv = ["hermes", "prompts"] + argv

    try:
        # Build a namespace from argparse
        import argparse

        from hermes_cli.prompts import build_prompts_parser

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="_top")
        build_prompts_parser(subparsers)
        args = parser.parse_args(["prompts"] + argv)
        exit_code = prompts_command(args)
    finally:
        stdout_val = sys.stdout.getvalue()
        stderr_val = sys.stderr.getvalue()
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        sys.argv = old_argv

    return exit_code or 0, stdout_val, stderr_val


# ---------------------------------------------------------------------------
# T22: hermes prompts render exits 3 when cn is missing
# ---------------------------------------------------------------------------


def test_prompts_render_exits_3_when_cn_missing(tmp_path):
    """T22: render subcommand exits with code 3 when cn binary not on PATH."""
    library_root = tmp_path / "prompt-library"
    _write_minimal_manifest(library_root)

    with patch(
        "hermes_agent.prompt_library.canopy.cn_path", return_value=None
    ), patch(
        "hermes_agent.prompt_library.canopy.cn_version",
        side_effect=CanopyMissingError(),
    ):
        exit_code, stdout, stderr = _invoke_prompts(
            ["render", "gelby-default", "--library-root", str(library_root)]
        )

    assert exit_code == 3
    assert "cn not found" in stderr.lower() or "canopy" in stderr.lower()


# ---------------------------------------------------------------------------
# T23: hermes prompts apply without --i-understand-fossilization exits 6
# ---------------------------------------------------------------------------


def test_prompts_apply_without_ack_flag_exits_6(tmp_path):
    """T23: apply without --i-understand-fossilization exits code 6."""
    library_root = tmp_path / "prompt-library"
    _write_minimal_manifest(library_root)

    exit_code, stdout, stderr = _invoke_prompts(
        ["apply", "gelby-default", "--library-root", str(library_root)]
    )

    assert exit_code == 6
    # Fossilization warning should appear in stdout (not stderr)
    combined = stdout + stderr
    assert "FOSSILIZATION WARNING" in combined


# ---------------------------------------------------------------------------
# T24: hermes prompts render --json outputs machine-readable shape
# ---------------------------------------------------------------------------


def test_prompts_render_json_mode_shape(tmp_path):
    """T24: render --json emits valid JSON with expected keys."""
    import json as json_mod

    from hermes_agent.prompt_library.warnings import FOSSILIZATION_WARNING

    library_root = tmp_path / "prompt-library"
    _write_minimal_manifest(library_root)

    fixture_path = Path(__file__).parent.parent / "prompt_library" / "fixtures" / "cn_render_gelby_default.json"
    envelope = json_mod.loads(fixture_path.read_text())

    with patch.multiple(
        "hermes_agent.prompt_library.render",
        cn_render=MagicMock(return_value=envelope),
        cn_version=MagicMock(return_value="0.2.6"),
        cn_show=MagicMock(return_value={"tags": ["tenant:greg"]}),
    ):
        exit_code, stdout, stderr = _invoke_prompts(
            [
                "render",
                "gelby-default",
                "--library-root",
                str(library_root),
                "--no-staging",
                "--json",
            ]
        )

    assert exit_code == 0, f"Expected exit 0, got {exit_code}. stderr: {stderr}"
    data = json_mod.loads(stdout)

    # Required keys in RenderResult
    assert "profile" in data
    assert "tenant" in data
    assert "sections" in data
    assert "fossilization_warning" in data
    assert "dry_run" in data
    assert data["dry_run"] is True
    assert data["fossilization_warning"] == FOSSILIZATION_WARNING
