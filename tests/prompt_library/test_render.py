# tests/prompt_library/test_render.py
"""Unit tests for render_profile (T7, T8, T15, T16)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from hermes_agent.prompt_library._version import PROMPT_LIBRARY_SCHEMA
from hermes_agent.prompt_library.warnings import FOSSILIZATION_WARNING

# ---------------------------------------------------------------------------
# Shared fixture: minimal valid manifest written to a tmp library root
# ---------------------------------------------------------------------------


def _write_manifest(library_root: Path, profile: str, extra: dict | None = None) -> None:
    profiles = library_root / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": PROMPT_LIBRARY_SCHEMA,
        "profile": profile,
        "tenant": "greg",
        "render": {"prompt": profile},
        "targets": {
            "soul": {
                "path": str(library_root / "SOUL.md"),
                "mode": "replace",
            }
        },
    }
    if extra:
        manifest.update(extra)
    (profiles / f"{profile}.yaml").write_text(yaml.dump(manifest), encoding="utf-8")


def _canned_envelope() -> dict:
    """Return a canned cn render JSON envelope for tests."""
    fixture = Path(__file__).parent / "fixtures" / "cn_render_gelby_default.json"
    return json.loads(fixture.read_text())


def _mock_cn_render_patch():
    """Return a patch context that mocks cn_render + cn_version at the render module level."""
    return patch.multiple(
        "hermes_agent.prompt_library.render",
        cn_render=MagicMock(return_value=_canned_envelope()),
        cn_version=MagicMock(return_value="0.2.6"),
        cn_show=MagicMock(return_value={"tags": ["tenant:greg"]}),
    )


# ---------------------------------------------------------------------------
# T7: render_profile NEVER writes to live targets (dry-run-only invariant)
# ---------------------------------------------------------------------------


def test_render_profile_is_dry_run_only(tmp_path):
    """T7: render_profile must NOT touch any file under fake live-SOUL path."""
    library_root = tmp_path / "prompt-library"
    _write_manifest(library_root, "gelby-default")

    # Create a "live" SOUL.md — render must NOT modify it
    live_soul = library_root / "SOUL.md"
    live_soul.write_text("ORIGINAL CONTENT", encoding="utf-8")
    original_mtime = live_soul.stat().st_mtime

    with _mock_cn_render_patch():
        from hermes_agent.prompt_library.render import render_profile

        result = render_profile("gelby-default", library_root=library_root)

    # Live SOUL.md must be unmodified
    assert live_soul.read_text() == "ORIGINAL CONTENT"
    assert live_soul.stat().st_mtime == original_mtime
    # Result is marked as dry-run
    assert result["dry_run"] is True


# ---------------------------------------------------------------------------
# T8: render result always contains FOSSILIZATION_WARNING
# ---------------------------------------------------------------------------


def test_render_result_contains_fossilization_warning(tmp_path):
    """T8: result['fossilization_warning'] == FOSSILIZATION_WARNING constant."""
    library_root = tmp_path / "prompt-library"
    _write_manifest(library_root, "gelby-default")

    with _mock_cn_render_patch():
        from hermes_agent.prompt_library.render import render_profile

        result = render_profile("gelby-default", library_root=library_root)

    assert result["fossilization_warning"] == FOSSILIZATION_WARNING


# ---------------------------------------------------------------------------
# T15: params substitution applied to section bodies
# ---------------------------------------------------------------------------


def test_render_substitutes_params(tmp_path):
    """T15: {{ profile_display_name }} replaced in section bodies."""
    library_root = tmp_path / "prompt-library"
    _write_manifest(
        library_root,
        "gelby-default",
        extra={"params": {"profile_display_name": "Gelby"}},
    )

    # Inject a section with a param placeholder
    canned = _canned_envelope()
    canned["sections"][0]["body"] = "Hello {{ profile_display_name }}!"

    with patch.multiple(
        "hermes_agent.prompt_library.render",
        cn_render=MagicMock(return_value=canned),
        cn_version=MagicMock(return_value="0.2.6"),
        cn_show=MagicMock(return_value={"tags": ["tenant:greg"]}),
    ):
        from hermes_agent.prompt_library.render import render_profile

        result = render_profile("gelby-default", library_root=library_root)

    soul_content = result["targets"]["soul"]["content"]
    assert "Hello Gelby!" in soul_content
    assert "{{ profile_display_name }}" not in soul_content


# ---------------------------------------------------------------------------
# T16: section_routes honored
# ---------------------------------------------------------------------------


def test_render_routes_sections_to_targets(tmp_path):
    """T16: section_routes directs specific sections to specific targets."""
    library_root = tmp_path / "prompt-library"

    # Create a dummy config.yaml for the config_addendum target
    config_file = library_root / "config.yaml"
    library_root.mkdir(parents=True, exist_ok=True)
    config_file.write_text("key: value\n", encoding="utf-8")

    _write_manifest(
        library_root,
        "gelby-default",
        extra={
            "targets": {
                "soul": {
                    "path": str(library_root / "SOUL.md"),
                    "mode": "replace",
                },
                "config_addendum": {
                    "path": str(config_file),
                    "key": "system_prompt_addendum",
                    "mode": "replace",
                },
            },
            "section_routes": [
                {"section": "identity", "target": "config_addendum"},
                {"section": "mobile-digest", "target": "soul"},
                {"section": "deep-work-exception", "target": "soul"},
            ],
            # Override V15 by making profile NOT gelby-default
            "profile": "test-route-profile",
        },
    )
    # rewrite as test-route-profile
    profiles = library_root / "profiles"
    (profiles / "gelby-default.yaml").unlink(missing_ok=True)
    manifest = {
        "schema": PROMPT_LIBRARY_SCHEMA,
        "profile": "test-route-profile",
        "tenant": "greg",
        "render": {"prompt": "test-route-profile"},
        "targets": {
            "soul": {
                "path": str(library_root / "SOUL.md"),
                "mode": "replace",
            },
            "config_addendum": {
                "path": str(config_file),
                "key": "system_prompt_addendum",
                "mode": "replace",
            },
        },
        "section_routes": [
            {"section": "identity", "target": "config_addendum"},
            {"section": "mobile-digest", "target": "soul"},
            {"section": "deep-work-exception", "target": "soul"},
        ],
    }
    yaml_text = yaml.dump(manifest)
    (profiles / "test-route-profile.yaml").write_text(yaml_text, encoding="utf-8")

    canned = _canned_envelope()
    canned["name"] = "test-route-profile"

    with patch.multiple(
        "hermes_agent.prompt_library.render",
        cn_render=MagicMock(return_value=canned),
        cn_version=MagicMock(return_value="0.2.6"),
        cn_show=MagicMock(return_value={"tags": ["tenant:greg"]}),
    ), patch(
        "hermes_agent.prompt_library.render.check_profile_exists",
        return_value=True,
    ):
        from hermes_agent.prompt_library.render import render_profile

        result = render_profile("test-route-profile", library_root=library_root)

    soul_content = result["targets"]["soul"]["content"]
    cfg_content = result["targets"]["config_addendum"]["content"]

    # identity should be in config_addendum, not soul
    assert "Telegram Reply Style" in soul_content
    assert "Nous Research" in cfg_content or "intelligent AI" in cfg_content


def test_render_returns_sections_with_sha256_and_body_chars(tmp_path):
    """Sections in result have name, sha256, body_chars."""
    library_root = tmp_path / "prompt-library"
    _write_manifest(library_root, "gelby-default")

    with _mock_cn_render_patch():
        from hermes_agent.prompt_library.render import render_profile

        result = render_profile("gelby-default", library_root=library_root)

    for sec in result["sections"]:
        assert "name" in sec
        assert "sha256" in sec
        assert "body_chars" in sec
        assert isinstance(sec["body_chars"], int)
