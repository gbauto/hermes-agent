"""Offline dispatch regressions: bounded reconciliation and review skill requirements."""

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    # Set all routing inputs before importing the board module. Never inherit
    # HERMES_KANBAN_HOME from a developer shell (it can point at a live board).
    for key in ("HERMES_KANBAN_HOME", "HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(key, raising=False)
    home = tmp_path / "hermes-test-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    kb = importlib.import_module("hermes_cli.kanban_db")
    assert kb.kanban_db_path().resolve().is_relative_to(home.resolve())
    kb.init_db()
    from hermes_cli import profiles
    monkeypatch.setattr(profiles, "profile_exists", lambda name: True)
    monkeypatch.setattr(kb, "_worker_profile_home", lambda name: str(home))
    return kb, home


def _review(conn, kb, skills):
    tid = kb.create_task(conn, title="review", assignee="reviewer", skills=skills)
    conn.execute("UPDATE tasks SET status = 'review' WHERE id = ?", (tid,))
    return tid


def _skill(home: Path, name: str):
    path = home / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: Test skill.\n---\n\n# {name}\n")


def test_dispatch_reconciles_deterministic_bounded_todo_slice_without_touching_other_states(sandbox, monkeypatch):
    kb, _ = sandbox
    seen = []
    original = kb.reconcile_task_state

    def tracked(conn, task_id, **kwargs):
        seen.append(task_id)
        return original(conn, task_id, **kwargs)

    monkeypatch.setattr(kb, "reconcile_task_state", tracked)
    with kb.connect() as conn:
        ids = []
        for index in range(130):
            tid = kb.create_task(conn, title=f"todo {index}", initial_status="blocked")
            conn.execute("UPDATE tasks SET status = 'todo', priority = 1, created_at = 1 WHERE id = ?", (tid,))
            ids.append(tid)
        blocked = kb.create_task(conn, title="human gate")
        kb.block_task(conn, blocked, reason="approval required")
        running = kb.create_task(conn, title="in flight")
        conn.execute("UPDATE tasks SET status = 'running' WHERE id = ?", (running,))
        locked = kb.create_task(conn, title="claimed todo", initial_status="blocked")
        conn.execute("UPDATE tasks SET status = 'todo', claim_lock = 'owner' WHERE id = ?", (locked,))
        result = kb.dispatch_once(conn, dry_run=True)
        assert seen == sorted(ids)[:128]
        assert result.promoted == 131  # 128 reconciled + 2 legacy todos + 1 locked legacy todo
        for tid in ids:
            assert kb.get_task(conn, tid).status == "ready"
        for tid in seen:
            assert [event.payload for event in kb.list_events(conn, tid) if event.kind == "promoted"][-1] == {
                "source": "task_reconciliation"
            }
        assert kb.get_task(conn, blocked).status == "blocked"
        assert kb.get_task(conn, running).status == "running"
        assert kb.get_task(conn, locked).claim_lock == "owner"
        assert not any(event.kind == "promoted" for event in kb.list_events(conn, blocked))
        assert kb.dispatch_once(conn, dry_run=True).promoted == 0
        assert len(seen) == 128


def test_dispatch_reconciliation_does_not_promote_missing_evidence_or_failed_block(sandbox):
    kb, home = sandbox
    with kb.connect() as conn:
        producer = kb.create_task(conn, title="producer")
        consumer = kb.create_task(conn, title="consumer", parents=[producer], assignee=None)
        artifact = home / "not-published.txt"
        assert kb.complete_task(conn, producer, metadata={"artifacts": [str(artifact)]})
        failed = kb.create_task(conn, title="failed")
        kb.block_task(conn, failed, reason="manual inspection required")
        conn.execute("UPDATE tasks SET consecutive_failures = 3 WHERE id = ?", (failed,))
        assert kb.dispatch_once(conn, dry_run=True).promoted == 0
        assert kb.get_task(conn, consumer).status == "todo"
        assert kb.get_task(conn, failed).status == "blocked"
        assert kb.get_task(conn, failed).consecutive_failures == 3
        artifact.write_text("receipt")
        assert kb.dispatch_once(conn, dry_run=True).promoted == 1
        assert kb.get_task(conn, consumer).status == "ready"
        assert kb.dispatch_once(conn, dry_run=True).promoted == 0


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("review_installed", [True, False])
def test_review_keeps_all_explicit_skills_and_optional_injection(sandbox, monkeypatch, dry_run, review_installed):
    kb, home = sandbox
    preflights = []
    original = kb._worker_skill_preflight

    def tracked(task, **kwargs):
        answer = original(task, **kwargs)
        preflights.append((list(task.skills), answer))
        return answer

    monkeypatch.setattr(kb, "_worker_skill_preflight", tracked)
    for skill in ("required-a", "required-b"):
        _skill(home, skill)
    if review_installed:
        _skill(home, "sdlc-review")
    with kb.connect() as conn:
        tid = _review(conn, kb, ["required-a", "required-b"])
        spawned = []

        def spawn(task, workspace, board=None):
            argv, payload = kb._worker_skill_preflight(task, profile_arg="reviewer", hermes_home=str(home))
            spawned.append((list(task.skills), argv, payload))
            return None

        result = kb.dispatch_once(conn, dry_run=dry_run, spawn_fn=spawn)
        assert [row[0] for row in result.spawned] == [tid]
        assert not result.readiness_failed
        assert preflights
        for skills, (argv, payload) in preflights:
            assert skills == ["required-a", "required-b", "sdlc-review"]
            assert argv == ["required-a", "required-b"] + (["sdlc-review"] if review_installed else [])
            assert payload["missing_required_skills"] == []
        if not dry_run:
            assert spawned.pop()[0] == ["required-a", "required-b", "sdlc-review"]
        assert kb.get_task(conn, tid).skills == ["required-a", "required-b"]


@pytest.mark.parametrize("dry_run", [True, False])
def test_review_missing_required_skill_blocks_even_if_injected_review_missing(sandbox, dry_run):
    kb, home = sandbox
    _skill(home, "required-a")
    with kb.connect() as conn:
        tid = _review(conn, kb, ["required-a", "required-b"])
        result = kb.dispatch_once(conn, dry_run=dry_run, spawn_fn=lambda *a, **k: pytest.fail("must not spawn"))
        assert result.spawned == []
        assert any(row[0] == tid and "required-b" in row[1] for row in result.readiness_failed)
        assert kb.get_task(conn, tid).skills == ["required-a", "required-b"]


@pytest.mark.parametrize("dry_run", [True, False])
def test_explicit_review_skill_is_required_and_not_duplicated(sandbox, dry_run):
    kb, home = sandbox
    _skill(home, "required-a")
    with kb.connect() as conn:
        tid = _review(conn, kb, ["sdlc-review", "required-a"])
        result = kb.dispatch_once(conn, dry_run=dry_run, spawn_fn=lambda *a, **k: pytest.fail("must not spawn"))
        assert result.spawned == []
        assert any(row[0] == tid and "sdlc-review" in row[1] for row in result.readiness_failed)
        assert kb.get_task(conn, tid).skills == ["sdlc-review", "required-a"]
