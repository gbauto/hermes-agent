# tests/prompt_library/test_canopy.py
"""Unit tests for the canopy.py subprocess wrapper (T19, T20, T21)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes_agent.prompt_library.errors import CanopyCliError, CanopyMissingError


# ---------------------------------------------------------------------------
# T19: cn missing -> CanopyMissingError
# ---------------------------------------------------------------------------


def test_cn_missing_raises_canopy_missing_error():
    """T19: cn_version/cn_render raise CanopyMissingError when shutil.which returns None."""
    with patch("shutil.which", return_value=None):
        from hermes_agent.prompt_library import canopy

        with pytest.raises(CanopyMissingError) as exc_info:
            canopy.cn_version()

        assert "bun add" in str(exc_info.value)
        assert "canopy-cli" in str(exc_info.value)


def test_cn_missing_in_render_raises_canopy_missing_error(tmp_path):
    """T19b: cn_render raises CanopyMissingError when cn not on PATH."""
    with patch("shutil.which", return_value=None):
        from hermes_agent.prompt_library import canopy

        with pytest.raises(CanopyMissingError):
            canopy.cn_render("gelby-default", project_dir=tmp_path)


# ---------------------------------------------------------------------------
# T20: cn_render parses JSON envelope
# ---------------------------------------------------------------------------


def test_cn_render_parses_json_envelope(tmp_path):
    """T20: cn_render correctly parses the JSON output from cn."""
    fixture = Path(__file__).parent / "fixtures" / "cn_render_gelby_default.json"
    envelope = json.loads(fixture.read_text())
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps(envelope)
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result), patch(
        "shutil.which", return_value="/usr/local/bin/cn"
    ):
        from hermes_agent.prompt_library import canopy

        result = canopy.cn_render("gelby-default", project_dir=tmp_path)

    assert result["name"] == "gelby-default"
    assert len(result["sections"]) == 3
    section_names = [s["name"] for s in result["sections"]]
    assert "identity" in section_names
    assert "mobile-digest" in section_names
    assert "deep-work-exception" in section_names
    assert result["resolvedFrom"] == ["gelby-default"]


def test_cn_render_raises_on_nonzero_exit(tmp_path):
    """T20b: cn_render raises CanopyCliError on non-zero exit."""
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "some error"

    with patch("subprocess.run", return_value=mock_result), patch(
        "shutil.which", return_value="/usr/local/bin/cn"
    ):
        from hermes_agent.prompt_library import canopy

        with pytest.raises(CanopyCliError) as exc_info:
            canopy.cn_render("gelby-default", project_dir=tmp_path)
        assert exc_info.value.returncode == 1


def test_cn_render_raises_on_invalid_json(tmp_path):
    """T20c: cn_render raises CanopyCliError on non-JSON stdout."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "not json output"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result), patch(
        "shutil.which", return_value="/usr/local/bin/cn"
    ):
        from hermes_agent.prompt_library import canopy

        with pytest.raises(CanopyCliError):
            canopy.cn_render("gelby-default", project_dir=tmp_path)


# ---------------------------------------------------------------------------
# T21: cn_version parse
# ---------------------------------------------------------------------------


def test_cn_version_parse():
    """T21: cn_version correctly parses version number from cn --version output."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "canopy/0.2.6 linux/arm64"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result), patch(
        "shutil.which", return_value="/usr/local/bin/cn"
    ):
        from hermes_agent.prompt_library import canopy

        ver = canopy.cn_version()
    assert ver == "0.2.6"


def test_cn_version_parse_plain_version():
    """T21b: cn --version with plain '0.2.6' format."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "0.2.6"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result), patch(
        "shutil.which", return_value="/usr/local/bin/cn"
    ):
        from hermes_agent.prompt_library import canopy

        ver = canopy.cn_version()
    assert ver == "0.2.6"


def test_cn_path_returns_none_when_missing():
    """cn_path() returns None when cn is not installed."""
    with patch("shutil.which", return_value=None):
        from hermes_agent.prompt_library import canopy

        assert canopy.cn_path() is None
