# tests/prompt_library/test_manifest.py
"""Unit tests for manifest loading and validation (T1-T6, T11)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hermes_agent.prompt_library._version import PROMPT_LIBRARY_SCHEMA
from hermes_agent.prompt_library.errors import ManifestValidationError
from hermes_agent.prompt_library.manifest import load_manifest, validate_manifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_valid() -> dict:
    """Return a minimal valid gelby-default manifest dict."""
    return {
        "schema": PROMPT_LIBRARY_SCHEMA,
        "profile": "gelby-default",
        "tenant": "greg",
        "owner": "greg",
        "render": {"prompt": "gelby-default"},
        "targets": {
            "soul": {
                "path": "~/.hermes/SOUL.md",
                "mode": "replace",
            }
        },
    }


# ---------------------------------------------------------------------------
# T1: accept minimal valid manifest
# ---------------------------------------------------------------------------

def test_validate_manifest_accepts_minimal_valid():
    """T1: schema/profile/tenant/render.prompt/one target passes."""
    m = _minimal_valid()
    validate_manifest(m)  # should not raise


# ---------------------------------------------------------------------------
# T2: reject missing schema (V1)
# ---------------------------------------------------------------------------

def test_validate_manifest_rejects_missing_schema():
    """T2: V1 — schema must equal hermes.prompt_profile.v1."""
    m = _minimal_valid()
    m["schema"] = "wrong.schema.v99"
    with pytest.raises(ManifestValidationError) as exc_info:
        validate_manifest(m)
    errors = exc_info.value.errors
    rules = [e["rule"] for e in errors]
    assert "V1" in rules


def test_validate_manifest_rejects_absent_schema():
    """T2b: V1 — schema key missing entirely."""
    m = _minimal_valid()
    del m["schema"]
    with pytest.raises(ManifestValidationError) as exc_info:
        validate_manifest(m)
    rules = [e["rule"] for e in exc_info.value.errors]
    assert "V1" in rules


# ---------------------------------------------------------------------------
# T3: reject missing tenant (V3)
# ---------------------------------------------------------------------------

def test_validate_manifest_rejects_missing_tenant():
    """T3: V3 — tenant must be present and non-empty string."""
    m = _minimal_valid()
    del m["tenant"]
    with pytest.raises(ManifestValidationError) as exc_info:
        validate_manifest(m)
    rules = [e["rule"] for e in exc_info.value.errors]
    assert "V3" in rules


def test_validate_manifest_rejects_empty_tenant():
    """T3b: V3 — empty tenant rejected."""
    m = _minimal_valid()
    m["tenant"] = ""
    with pytest.raises(ManifestValidationError) as exc_info:
        validate_manifest(m)
    rules = [e["rule"] for e in exc_info.value.errors]
    assert "V3" in rules


# ---------------------------------------------------------------------------
# T4: reject no targets (V5)
# ---------------------------------------------------------------------------

def test_validate_manifest_rejects_no_targets():
    """T4: V5 — targets block must be present with at least one sub-target."""
    m = _minimal_valid()
    del m["targets"]
    with pytest.raises(ManifestValidationError) as exc_info:
        validate_manifest(m)
    rules = [e["rule"] for e in exc_info.value.errors]
    assert "V5" in rules


def test_validate_manifest_rejects_empty_targets():
    """T4b: V5 — empty targets dict rejected."""
    m = _minimal_valid()
    m["targets"] = {}
    with pytest.raises(ManifestValidationError) as exc_info:
        validate_manifest(m)
    rules = [e["rule"] for e in exc_info.value.errors]
    assert "V5" in rules


# ---------------------------------------------------------------------------
# T5: reject unknown mode (V9)
# ---------------------------------------------------------------------------

def test_validate_manifest_rejects_unknown_mode():
    """T5: V9 — mode must be 'replace'."""
    m = _minimal_valid()
    m["targets"]["soul"]["mode"] = "append"
    with pytest.raises(ManifestValidationError) as exc_info:
        validate_manifest(m)
    rules = [e["rule"] for e in exc_info.value.errors]
    assert "V9" in rules


# ---------------------------------------------------------------------------
# T6: reject gelby-default with config_addendum target (V15)
# ---------------------------------------------------------------------------

def test_validate_manifest_rejects_gelby_default_with_config_addendum():
    """T6: V15 — gelby-default profile must NOT have config_addendum target."""
    m = _minimal_valid()
    m["targets"]["config_addendum"] = {
        "path": "~/.hermes/profiles/some/config.yaml",
        "key": "system_prompt_addendum",
        "mode": "replace",
    }
    with pytest.raises(ManifestValidationError) as exc_info:
        validate_manifest(m)
    rules = [e["rule"] for e in exc_info.value.errors]
    assert "V15" in rules


# ---------------------------------------------------------------------------
# T11: reject cross-tenant without allow-list (V14)
# Note: V14 is enforced by render_profile (requires cn show); here we test
# the manifest structural rules only. We seed a fixture with a cross-tenant
# allow-list entry to verify V14 is wired at the manifest level.
# ---------------------------------------------------------------------------

def test_validate_manifest_accepts_cross_tenant_with_allow_list():
    """T11: cross_tenant_inherit_allow present -> manifest validates structurally."""
    m = _minimal_valid()
    m["cross_tenant_inherit_allow"] = [
        {"prompt": "nous-shared/safety-block", "from_tenant": "nous"}
    ]
    validate_manifest(m)  # should not raise


def test_validate_manifest_collects_multiple_errors():
    """V1 + V3 + V5 all reported at once (not first-failure-only)."""
    bad = {
        "schema": "wrong",
        "profile": "some-profile",
        # missing tenant
        "render": {"prompt": "x"},
        # missing targets
    }
    with pytest.raises(ManifestValidationError) as exc_info:
        validate_manifest(bad)
    rules = {e["rule"] for e in exc_info.value.errors}
    # V1 (schema), V3 (tenant), V5 (targets) should all be present
    assert "V1" in rules
    assert "V3" in rules
    assert "V5" in rules


def test_load_manifest_raises_on_missing_file(tmp_path):
    """load_manifest raises ManifestValidationError when manifest file absent."""
    with pytest.raises(ManifestValidationError) as exc_info:
        load_manifest("nonexistent-profile", library_root=tmp_path)
    assert exc_info.value.errors[0]["rule"] == "LOAD"


def test_load_manifest_raises_on_bad_yaml(tmp_path):
    """load_manifest raises ManifestValidationError on YAML parse failure."""
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "bad.yaml").write_text(": : : invalid yaml :::", encoding="utf-8")
    with pytest.raises(ManifestValidationError) as exc_info:
        load_manifest("bad", library_root=tmp_path)
    assert exc_info.value.errors[0]["rule"] == "LOAD"


def test_load_manifest_returns_dict(tmp_path):
    """load_manifest returns the parsed dict for a valid manifest."""
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    manifest_data = {
        "schema": PROMPT_LIBRARY_SCHEMA,
        "profile": "gelby-default",
        "tenant": "greg",
        "render": {"prompt": "gelby-default"},
        "targets": {"soul": {"path": "/tmp/SOUL.md", "mode": "replace"}},
    }
    (profiles / "gelby-default.yaml").write_text(
        yaml.dump(manifest_data), encoding="utf-8"
    )
    result = load_manifest("gelby-default", library_root=tmp_path)
    assert result["profile"] == "gelby-default"
    assert result["tenant"] == "greg"
