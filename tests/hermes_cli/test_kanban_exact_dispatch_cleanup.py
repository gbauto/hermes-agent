"""Disposable-board negative controls for selected dispatch and cleanup receipts."""
import argparse
import importlib
import shutil

import pytest


@pytest.fixture
def board(tmp_path, monkeypatch):
    for key in ("HERMES_KANBAN_HOME", "HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(key, raising=False)
    home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    kb = importlib.import_module("hermes_cli.kanban_db")
    resolved = kb.kanban_db_path().resolve()
    assert resolved.is_relative_to(home.resolve()), resolved
    kb.init_db()
    from hermes_cli import profiles
    monkeypatch.setattr(profiles, "profile_exists", lambda name: True)
    monkeypatch.setattr(kb, "_worker_profile_home", lambda name: str(home))
    return kb, home


@pytest.mark.parametrize("dry_run", [True, False])
def test_exact_dispatch_never_mutates_unrelated_cards(board, monkeypatch, dry_run):
    kb, home = board
    with kb.connect() as conn:
        target = kb.create_task(conn, title="selected", assignee="builder")
        peer = kb.create_task(conn, title="unrelated ready", assignee="builder")
        todo = kb.create_task(conn, title="unrelated todo", assignee="builder")
        conn.execute("UPDATE tasks SET status = 'todo' WHERE id = ?", (todo,))
        stale = kb.create_task(conn, title="unrelated stale", assignee="builder")
        conn.execute("UPDATE tasks SET status = 'running', claim_lock = 'old', claim_expires = 1 WHERE id = ?", (stale,))
        before = {tid: (dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()),
                        [dict(e) for e in conn.execute("SELECT * FROM task_events WHERE task_id = ?", (tid,))])
                  for tid in (peer, todo, stale)}
        spawned = []
        result = kb.dispatch_once(conn, task_id=target, dry_run=dry_run,
                                  spawn_fn=lambda task, workspace, **kw: spawned.append(task.id))
        assert [row[0] for row in result.spawned] == [target]
        assert spawned == ([] if dry_run else [target])
        assert result.promoted == 0 and result.reclaimed == 0
        after = {tid: (dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()),
                       [dict(e) for e in conn.execute("SELECT * FROM task_events WHERE task_id = ?", (tid,))])
                 for tid in before}
        assert before == after
        with pytest.raises(ValueError, match="unknown task ID"):
            kb.dispatch_once(conn, task_id="missing-id", dry_run=dry_run)
        assert before == {tid: (dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()),
                                [dict(e) for e in conn.execute("SELECT * FROM task_events WHERE task_id = ?", (tid,))])
                          for tid in before}


def test_selected_todo_only_promotes_selected_and_dry_run_is_read_only(board):
    kb, _ = board
    with kb.connect() as conn:
        target = kb.create_task(conn, title="target", assignee=None)
        other = kb.create_task(conn, title="other", assignee=None)
        conn.execute("UPDATE tasks SET status = 'todo' WHERE id IN (?, ?)", (target, other))
        before = [dict(e) for e in conn.execute("SELECT * FROM task_events")]
        assert kb.dispatch_once(conn, task_id=target, dry_run=True).promoted == 0
        assert [dict(e) for e in conn.execute("SELECT * FROM task_events")] == before
        assert kb.get_task(conn, target).status == "todo"
        assert kb.dispatch_once(conn, task_id=target).promoted == 1
        assert kb.get_task(conn, target).status == "ready"
        assert kb.get_task(conn, other).status == "todo"
        assert not any(e.kind == "promoted" for e in kb.list_events(conn, other))


def test_cli_exact_selector_passes_through(board, monkeypatch):
    kb, _ = board
    from hermes_cli import kanban
    parser = argparse.ArgumentParser()
    kanban.build_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["kanban", "dispatch", "--task-id", "missing", "--dry-run"])
    assert args.task_id == "missing"
    assert kanban.kanban_command(args) == 2


def test_failed_scratch_delete_has_durable_receipt_and_retry(board, monkeypatch):
    kb, _ = board
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="cleanup")
        workspace = kb.resolve_workspace(kb.get_task(conn, tid))
        kb.set_workspace_path(conn, tid, workspace)
        (workspace / "disposable.txt").write_text("data")
        original = shutil.rmtree
        def fail(path, *a, **kw):
            if path == workspace:
                raise OSError("simulated delete failure")
            return original(path, *a, **kw)
        with monkeypatch.context() as m:
            m.setattr(shutil, "rmtree", fail)
            assert kb.complete_task(conn, tid)
        assert workspace.exists()
        failures = [e for e in kb.list_events(conn, tid) if e.kind == "scratch_cleanup_failed"]
        assert failures and failures[-1].payload["workspace_path"] == str(workspace)
        assert kb.retry_failed_scratch_cleanup(conn, tid)
        assert not workspace.exists()
        assert any(e.kind == "scratch_cleanup_succeeded" for e in kb.list_events(conn, tid))
        assert not kb.retry_failed_scratch_cleanup(conn, tid)
