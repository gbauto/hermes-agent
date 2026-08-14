"""Regression tests for operator-visible Kanban dispatch diagnostics."""

from __future__ import annotations

import argparse
import json
from contextlib import nullcontext

from hermes_cli import kanban as kb_cli
from hermes_cli import kanban_db


def _diagnostic_result() -> kanban_db.DispatchResult:
    return kanban_db.DispatchResult(
        respawn_guarded=[
            ("t_auth", "blocker_auth"),
            ("t_recent", "recent_success"),
        ],
        rate_limited=["t_quota"],
        skipped_per_profile_capped=[("t_busy", "tac-builder", 2)],
        auto_assigned_default=[("t_unrouted", "tac-builder")],
    )


def _patch_dispatch(monkeypatch) -> None:
    monkeypatch.setattr(kanban_db, "connect", lambda: nullcontext(object()))
    monkeypatch.setattr(
        kanban_db,
        "dispatch_once",
        lambda conn, **kwargs: _diagnostic_result(),
    )


def test_cli_dispatch_json_includes_deferred_spawn_diagnostics(monkeypatch, capsys):
    _patch_dispatch(monkeypatch)

    args = argparse.Namespace(dry_run=True, max=None, failure_limit=2, json=True)
    assert kb_cli._cmd_dispatch(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["respawn_guarded"] == [
        {"task_id": "t_auth", "reason": "blocker_auth"},
        {"task_id": "t_recent", "reason": "recent_success"},
    ]
    assert payload["rate_limited"] == ["t_quota"]
    assert payload["skipped_per_profile_capped"] == [
        {"task_id": "t_busy", "assignee": "tac-builder", "current": 2}
    ]
    assert payload["auto_assigned_default"] == [
        {"task_id": "t_unrouted", "assignee": "tac-builder"}
    ]


def test_cli_dispatch_text_explains_each_deferred_bucket(monkeypatch, capsys):
    _patch_dispatch(monkeypatch)

    args = argparse.Namespace(dry_run=True, max=None, failure_limit=2, json=False)
    assert kb_cli._cmd_dispatch(args) == 0
    output = capsys.readouterr().out

    assert "Deferred (profile cap: tac-builder has 2 active): t_busy" in output
    assert "Deferred (respawn guard: blocker_auth): t_auth" in output
    assert "Deferred (provider rate-limited, cooldown pending): t_quota" in output
    assert "Auto-assigned default profile (tac-builder): t_unrouted" in output
