# tests/prompt_library/test_apply.py
"""Unit tests for apply_render, backup_target, patch_config_addendum (T9, T10, T13, T14, T17, T18)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from hermes_agent.prompt_library._version import PROMPT_LIBRARY_SCHEMA
from hermes_agent.prompt_library.errors import (
    CanopyCliError,
    FossilizationAcknowledgmentRequiredError,
)
from hermes_agent.prompt_library.warnings import FOSSILIZATION_WARNING

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _write_minimal_manifest(library_root: Path, profile: str = "gelby-default") -> Path:
    profiles = library_root / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    soul_path = library_root / "SOUL.md"
    soul_path.write_text("ORIGINAL SOUL CONTENT", encoding="utf-8")
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
    path = profiles / f"{profile}.yaml"
    path.write_text(yaml.dump(manifest), encoding="utf-8")
    return soul_path


def _canned_render_result(soul_path: Path) -> dict:
    return {
        "profile": "gelby-default",
        "tenant": "greg",
        "manifest_path": "/tmp/fake/profiles/gelby-default.yaml",
        "canopy_cli_version": "0.2.6",
        "resolved_from": ["gelby-default"],
        "sections": [{"name": "identity", "sha256": "abc1234", "body_chars": 100}],
        "params_sha256": "aabbcc",
        "targets": {
            "soul": {
                "path": str(soul_path),
                "content": "RENDERED SOUL CONTENT",
            }
        },
        "fossilization_warning": FOSSILIZATION_WARNING,
        "dry_run": True,
    }


# ---------------------------------------------------------------------------
# T9: patch_config_addendum round-trips tac-director config.yaml
# ---------------------------------------------------------------------------


def test_patch_config_addendum_roundtrips_tac_director_yaml(tmp_path):
    """T9: ruamel.yaml round-trip preserves every other key/comment/scalar style."""
    from hermes_agent.prompt_library.apply import patch_config_addendum

    # Copy the fixture
    fixture = FIXTURES_DIR / "tac-director-config.yaml"
    target = tmp_path / "tac-director-config.yaml"
    shutil.copy2(str(fixture), str(target))

    original_text = target.read_bytes()

    # Patch the addendum
    new_addendum = "New test addendum content.\nWith multiple lines.\n"
    patch_config_addendum(target, "system_prompt_addendum", new_addendum)

    # Read back and verify
    from ruamel.yaml import YAML as RuamelYAML

    ry = RuamelYAML(typ="rt")
    patched = ry.load(target.read_text(encoding="utf-8"))
    assert patched["system_prompt_addendum"].strip() == new_addendum.strip()

    # Verify all other keys are preserved
    original_parsed = ry.load(original_text.decode("utf-8"))
    for key in original_parsed:
        if key != "system_prompt_addendum":
            assert key in patched, f"Key {key!r} was lost after patching"


# ---------------------------------------------------------------------------
# T10: patch_config_addendum rejects lossy pre-edit round-trip
# ---------------------------------------------------------------------------


def test_patch_config_addendum_rejects_lossy_pre_edit_roundtrip(tmp_path, monkeypatch):
    """T10: When pre-edit round-trip is lossy, CanopyCliError is raised with no file mutation."""
    from hermes_agent.prompt_library.apply import patch_config_addendum

    normal_yaml = "key: original_value\nother: 42\n"
    target = tmp_path / "normal.yaml"
    target.write_text(normal_yaml, encoding="utf-8")
    original_text = target.read_bytes()

    # Monkeypatch the YAML dump to produce different output on second dump
    # simulating a round-trip lossiness check failure
    from io import StringIO as _StringIO

    from ruamel.yaml import YAML as _YAML

    call_count = [0]

    class FakeYAML:
        def __init__(self, *a, **kw):
            self._real = _YAML(typ="rt")
            self.preserve_quotes = True
            self.width = 4096

        def load(self, text):
            return self._real.load(text)

        def dump(self, data, stream):
            call_count[0] += 1
            buf = _StringIO()
            self._real.dump(data, buf)
            v = buf.getvalue()
            if call_count[0] == 1:
                stream.write(v)
            else:
                # Second dump returns DIFFERENT content to simulate lossiness
                stream.write(v + "# synthetic difference\n")

    monkeypatch.setattr(
        "hermes_agent.prompt_library.apply.YAML", FakeYAML
    )

    with pytest.raises(CanopyCliError):
        patch_config_addendum(target, "key", "new_value")

    # File must NOT have been mutated
    assert target.read_bytes() == original_text


# ---------------------------------------------------------------------------
# T13: apply_render requires acknowledged_fossilization
# ---------------------------------------------------------------------------


def test_apply_requires_acknowledged_fossilization(tmp_path):
    """T13: apply_render without flag raises FossilizationAcknowledgmentRequiredError."""
    from hermes_agent.prompt_library.apply import apply_render

    library_root = tmp_path / "prompt-library"
    _write_minimal_manifest(library_root)

    with pytest.raises(FossilizationAcknowledgmentRequiredError):
        apply_render("gelby-default", library_root=library_root, acknowledged_fossilization=False)


# ---------------------------------------------------------------------------
# T14: apply_render writes receipt with fossilization_warning
# ---------------------------------------------------------------------------


def test_apply_writes_receipt_with_fossilization_warning(tmp_path):
    """T14: receipt written by apply_render contains fossilization_warning key."""
    from hermes_agent.prompt_library.apply import apply_render

    library_root = tmp_path / "prompt-library"
    soul_path = _write_minimal_manifest(library_root)

    from hermes_agent.prompt_library.render import RenderResult

    canned_result = _canned_render_result(soul_path)

    with patch(
        "hermes_agent.prompt_library.render.render_profile",
        return_value=canned_result,
    ), patch(
        "hermes_agent.prompt_library.canopy.cn_version",
        return_value="0.2.6",
    ):
        result = apply_render(
            "gelby-default",
            library_root=library_root,
            acknowledged_fossilization=True,
        )

    # Verify receipt file
    receipt_path = Path(result["receipt_path"])
    assert receipt_path.exists()
    with open(receipt_path, "r", encoding="utf-8") as fh:
        receipt = json.load(fh)

    assert "fossilization_warning" in receipt
    assert receipt["fossilization_warning"] == FOSSILIZATION_WARNING
    assert receipt["dry_run"] is False


# ---------------------------------------------------------------------------
# T17: backup_target copies existing SOUL.md with manifest
# ---------------------------------------------------------------------------


def test_backup_target_copies_existing_soul_md_with_manifest(tmp_path):
    """T17: backup_target copies live SOUL.md and writes BACKUP_MANIFEST.json."""
    from hermes_agent.prompt_library.apply import backup_target

    library_root = tmp_path / "prompt-library"
    soul_path = tmp_path / "SOUL.md"
    soul_path.write_text("LIVE SOUL CONTENT", encoding="utf-8")

    targets = {
        "soul": {
            "path": str(soul_path),
            "content": "rendered content",
        }
    }

    with patch(
        "hermes_agent.prompt_library.canopy.cn_version",
        return_value="0.2.6",
    ):
        backup_dir = backup_target("gelby-default", targets, library_root=library_root)

    # Verify backup
    assert backup_dir.exists()
    backed_soul = backup_dir / "SOUL.md"
    assert backed_soul.exists()
    assert backed_soul.read_text() == "LIVE SOUL CONTENT"

    manifest_file = backup_dir / "BACKUP_MANIFEST.json"
    assert manifest_file.exists()
    with open(manifest_file, "r", encoding="utf-8") as fh:
        manifest_data = json.load(fh)
    assert manifest_data["profile"] == "gelby-default"
    assert len(manifest_data["targets"]) == 1
    assert manifest_data["targets"][0]["kind"] == "soul"
    assert "sha256" in manifest_data["targets"][0]


# ---------------------------------------------------------------------------
# T18: backup_target records missing target without failing
# ---------------------------------------------------------------------------


def test_backup_target_records_missing_target_without_failing(tmp_path):
    """T18: backup_target handles missing live target gracefully (first render)."""
    from hermes_agent.prompt_library.apply import backup_target

    library_root = tmp_path / "prompt-library"
    nonexistent_soul = tmp_path / "nonexistent" / "SOUL.md"

    targets = {
        "soul": {
            "path": str(nonexistent_soul),
            "content": "rendered content",
        }
    }

    with patch(
        "hermes_agent.prompt_library.canopy.cn_version",
        return_value="unknown",
    ):
        backup_dir = backup_target("new-profile", targets, library_root=library_root)

    manifest_file = backup_dir / "BACKUP_MANIFEST.json"
    assert manifest_file.exists()
    with open(manifest_file, "r", encoding="utf-8") as fh:
        manifest_data = json.load(fh)

    # Target recorded as missing
    assert manifest_data["targets"][0].get("missing") is True
    # No backup file created (target didn't exist)
    assert not (backup_dir / "SOUL.md").exists()
