from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import hermes_session_cleanup_config as config


def split_windows_command(line: str) -> list[str]:
    tokens = shlex.split(line, posix=False)
    return [
        token[1:-1]
        if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"')
        else token
        for token in tokens
    ]


def write_profile(home: Path, name: str, text: str = "model:\n  provider: openrouter\n") -> Path:
    path = home / "profiles" / name / "config.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_dry_run_discovers_profiles_and_does_not_write(tmp_path, capsys):
    home = tmp_path / "hermes"
    profile_a = write_profile(home, "alpha")
    profile_b = write_profile(home, "beta", "hooks:\n  on_session_end: []\n")
    original_a = profile_a.read_text()
    original_b = profile_b.read_text()

    config.main(["--hermes-home", str(home), "--repo-root", str(tmp_path / "repo"), "--dry-run", "--json"])
    receipt = json.loads(capsys.readouterr().out)

    assert receipt["schema_version"] == "hermes-session-cleanup-config-receipt.v1"
    assert receipt["mode"] == "dry_run"
    assert receipt["profile_config_count"] == 2
    assert receipt["changed_count"] == 2
    command_argv = split_windows_command(receipt["command"])
    assert command_argv[0] == str(Path(sys.executable).resolve())
    assert command_argv[1].endswith("hermes_session_cleanup_stub.py")
    assert command_argv[2] == "--json"
    assert profile_a.read_text() == original_a
    assert profile_b.read_text() == original_b


def test_apply_requires_explicit_apply_flag(tmp_path, capsys):
    home = tmp_path / "hermes"
    profile = write_profile(home, "alpha")

    config.main(["--hermes-home", str(home), "--repo-root", str(tmp_path / "repo"), "--json"])
    assert "hooks_auto_accept" not in profile.read_text()
    capsys.readouterr()

    config.main(["--hermes-home", str(home), "--repo-root", str(tmp_path / "repo"), "--apply", "--json"])
    receipt = json.loads(capsys.readouterr().out)
    text = profile.read_text()
    assert receipt["mode"] == "apply"
    assert receipt["plans"][0]["action"] == "updated"
    assert "hooks_auto_accept: true" in text
    assert "on_session_end" in text
    assert "hermes_session_cleanup_stub.py" in text
    assert "--json" in text


def test_existing_hook_is_unchanged(tmp_path, capsys):
    home = tmp_path / "hermes"
    repo = tmp_path / "repo"
    command = config.hook_command(repo)
    write_profile(home, "alpha", config.dump_config({
        "hooks": {"on_session_end": [{"command": command, "timeout": 30}]},
        "hooks_auto_accept": True,
    }))

    config.main(["--hermes-home", str(home), "--repo-root", str(repo), "--json"])
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["changed_count"] == 0
    assert receipt["plans"][0]["action"] == "unchanged"


def test_apply_refuses_conflict_markers(tmp_path, capsys):
    home = tmp_path / "hermes"
    write_profile(home, "alpha", "<<<<<<< ours\nmodel: {}\n=======\nmodel: []\n>>>>>>> theirs\n")

    config.main(["--hermes-home", str(home), "--repo-root", str(tmp_path / "repo"), "--apply", "--json"])
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["plans"][0]["action"] == "skipped"
    assert receipt["plans"][0]["reason"] == "conflict_markers_present"
