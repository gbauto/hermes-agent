"""Regression tests for Kanban dispatcher worker skill readiness preflight."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with an empty kanban DB."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _write_skill(home: Path, rel: str, name: str | None = None) -> None:
    skill_dir = home / "skills" / rel
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_name = name or skill_dir.name
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {skill_name}\n"
        f"description: Test skill {skill_name}.\n"
        "---\n\n"
        f"# {skill_name}\n\nTest fixture skill.\n",
        encoding="utf-8",
    )


def test_missing_builtin_kanban_worker_is_omitted_not_fatal(
    kanban_home, all_assignees_spawnable
):
    """A profile without kanban-worker must not crash before task logic."""

    with kb.connect() as conn:
        task_id = kb.create_task(conn, title="plain worker", assignee="builder")
        res = kb.dispatch_once(conn, dry_run=True)

    assert [row[0] for row in res.spawned] == [task_id]
    assert res.readiness_failed == []
    with kb.connect() as conn:
        task = kb.get_task(conn, task_id)
        assert task is not None
        assert task.status == "ready"


def test_missing_forced_skill_fails_dry_run_before_spawn(
    kanban_home, all_assignees_spawnable
):
    """Dry-run must surface the same preload failure live spawn would hit."""

    with kb.connect() as conn:
        task_id = kb.create_task(
            conn,
            title="needs missing skill",
            assignee="builder",
            skills=["definitely-missing-skill"],
        )
        res = kb.dispatch_once(conn, dry_run=True)

    assert res.spawned == []
    assert res.readiness_failed
    assert res.readiness_failed[0][0] == task_id
    assert "missing required skill" in res.readiness_failed[0][1]
    with kb.connect() as conn:
        task = kb.get_task(conn, task_id)
        assert task is not None
        assert task.status == "ready"


def test_ambiguous_forced_skill_fails_readiness(
    kanban_home, all_assignees_spawnable
):
    """Duplicate bare skill names must be caught before worker startup."""

    _write_skill(kanban_home, "alpha/dupe-skill", name="dupe-skill")
    _write_skill(kanban_home, "beta/dupe-skill", name="dupe-skill")

    with kb.connect() as conn:
        task_id = kb.create_task(
            conn,
            title="ambiguous skill",
            assignee="builder",
            skills=["dupe-skill"],
        )
        res = kb.dispatch_once(conn, dry_run=True)

    assert res.spawned == []
    assert res.readiness_failed
    assert res.readiness_failed[0][0] == task_id
    assert "ambiguous skill" in res.readiness_failed[0][1]


def test_live_readiness_failure_blocks_without_retry_budget(
    kanban_home, all_assignees_spawnable
):
    """Host/profile skill readiness failures are blockers, not worker crashes."""

    called = []

    def fake_spawn(task, workspace):  # pragma: no cover - should not be reached
        called.append(task.id)
        return 123

    with kb.connect() as conn:
        task_id = kb.create_task(
            conn,
            title="missing forced skill live",
            assignee="builder",
            skills=["not-installed"],
        )
        res = kb.dispatch_once(conn, spawn_fn=fake_spawn)
        task = kb.get_task(conn, task_id)
        events = kb.list_events(conn, task_id)
        runs = kb.list_runs(conn, task_id, include_active=True)

    assert called == []
    assert res.spawned == []
    assert res.auto_blocked == []
    assert res.readiness_failed and res.readiness_failed[0][0] == task_id
    assert task is not None
    assert task.status == "blocked"
    assert task.claim_lock is None
    assert task.worker_pid is None
    assert task.consecutive_failures == 0
    assert any(event.kind == "host_readiness_failed" for event in events)
    readiness_event = next(event for event in events if event.kind == "host_readiness_failed")
    raw_payload = readiness_event.payload
    payload = raw_payload if isinstance(raw_payload, dict) else json.loads(raw_payload or "{}")
    assert payload["check"] == "profile_skill_import"
    assert payload["missing_required_skills"] == ["not-installed"]
    assert runs[-1].outcome == "blocked"
