"""Disposable-board negative controls for selected dispatch and cleanup receipts."""
import argparse
import importlib
import shutil

import pytest


@pytest.fixture
def board(tmp_path, monkeypatch):
    for key in ("HERMES_KANBAN_HOME", "HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD",
                "HERMES_KANBAN_WORKSPACES_ROOT"):
        monkeypatch.delenv(key, raising=False)
    home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    kb = importlib.import_module("hermes_cli.kanban_db")
    resolved = kb.kanban_db_path().resolve()
    assert resolved.is_relative_to(home.resolve()), resolved
    assert kb.workspaces_root().resolve().is_relative_to(home.resolve())
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


def test_delete_and_failure_receipt_fail_then_dispatch_recovers(board, monkeypatch, caplog):
    kb, _ = board
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="two failures")
        workspace = kb.resolve_workspace(kb.get_task(conn, tid))
        kb.set_workspace_path(conn, tid, workspace)
        (workspace / "partial.txt").write_text("data")
        original_delete, original_append = shutil.rmtree, kb._append_event

        def fail_delete(path, *args, **kwargs):
            if path == workspace:
                (workspace / "partial.txt").unlink()
                raise OSError("simulated partial delete")
            return original_delete(path, *args, **kwargs)

        def fail_receipt(conn, task_id, kind, *args, **kwargs):
            if task_id == tid and kind == "scratch_cleanup_failed":
                raise OSError("simulated receipt failure")
            return original_append(conn, task_id, kind, *args, **kwargs)

        with monkeypatch.context() as m:
            m.setattr(shutil, "rmtree", fail_delete)
            m.setattr(kb, "_append_event", fail_receipt)
            assert kb.complete_task(conn, tid)
        assert kb.get_task(conn, tid).status == "done"
        assert workspace.exists()
        assert [e.kind for e in kb.list_events(conn, tid) if e.kind.startswith("scratch_cleanup_")] == ["scratch_cleanup_started"]
        assert "Scratch workspace cleanup could not finish" in caplog.text

    with kb.connect() as conn:
        kb.dispatch_once(conn, task_id=tid, dry_run=True)
        assert workspace.exists()
        kb.dispatch_once(conn, task_id=tid)
        assert workspace.exists()
        kb.dispatch_once(conn, dry_run=True)
        assert workspace.exists()
        kb.dispatch_once(conn)
        assert not workspace.exists()
        assert [e.kind for e in kb.list_events(conn, tid) if e.kind.startswith("scratch_cleanup_")] == [
            "scratch_cleanup_started", "scratch_cleanup_started", "scratch_cleanup_succeeded",
        ]
        assert not kb.retry_failed_scratch_cleanup(conn, tid)


def test_intent_write_failure_leaves_scratch_intact(board, monkeypatch, caplog):
    kb, _ = board
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="intent failure")
        workspace = kb.resolve_workspace(kb.get_task(conn, tid))
        kb.set_workspace_path(conn, tid, workspace)
        (workspace / "keep.txt").write_text("data")
        original = kb._append_event

        def fail_intent(conn, task_id, kind, *args, **kwargs):
            if task_id == tid and kind == "scratch_cleanup_started":
                raise OSError("simulated intent failure")
            return original(conn, task_id, kind, *args, **kwargs)

        with monkeypatch.context() as m:
            m.setattr(kb, "_append_event", fail_intent)
            assert kb.complete_task(conn, tid)
        assert workspace.exists()
        assert kb.get_task(conn, tid).status == "done"
        assert not kb.retry_failed_scratch_cleanup(conn, tid)
        assert "Scratch workspace cleanup could not finish" in caplog.text
        kb.dispatch_once(conn, dry_run=True)
        assert workspace.exists()
        kb.dispatch_once(conn)
        assert not workspace.exists()
        assert [e.kind for e in kb.list_events(conn, tid) if e.kind.startswith("scratch_cleanup_")] == [
            "scratch_cleanup_started", "scratch_cleanup_succeeded",
        ]


def test_success_receipt_failure_after_delete_recovers_missing_path(board, monkeypatch):
    kb, _ = board
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="missing success receipt")
        workspace = kb.resolve_workspace(kb.get_task(conn, tid))
        kb.set_workspace_path(conn, tid, workspace)
        (workspace / "data").write_text("payload")
        original = kb._append_event

        def fail_success(conn, task_id, kind, *args, **kwargs):
            if task_id == tid and kind == "scratch_cleanup_succeeded":
                raise OSError("simulated success receipt failure")
            return original(conn, task_id, kind, *args, **kwargs)

        with monkeypatch.context() as m:
            m.setattr(kb, "_append_event", fail_success)
            assert kb.complete_task(conn, tid)
        assert not workspace.exists()
        assert kb.retry_failed_scratch_cleanup(conn, tid)
        assert not workspace.exists()
        assert [e.kind for e in kb.list_events(conn, tid) if e.kind.startswith("scratch_cleanup_")] == [
            "scratch_cleanup_started", "scratch_cleanup_started", "scratch_cleanup_succeeded",
        ]


@pytest.mark.parametrize("has_intent", [False, True])
def test_archived_running_scratch_survives_unselected_tick_and_explicit_retry(board, has_intent):
    kb, _ = board
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="unfinished worker scratch")
        workspace = kb.resolve_workspace(kb.get_task(conn, tid))
        kb.set_workspace_path(conn, tid, workspace)
        unfinished = workspace / "unfinished.txt"
        unfinished.write_text("do not delete")
        assert kb.claim_task(conn, tid)
        assert kb.archive_task(conn, tid)
        assert kb.get_task(conn, tid).status == "archived"
        assert not any(e.kind == "completed" for e in kb.list_events(conn, tid))
        # An ordinary board-wide dispatch must not mistake archive for
        # completion, with or without a stale cleanup intent.
        if has_intent:
            with kb.write_txn(conn):
                kb._append_event(conn, tid, "scratch_cleanup_started", {"workspace_path": str(workspace)})
        assert not kb.retry_failed_scratch_cleanup(conn, tid)
        assert kb.dispatch_once(conn).spawned == []
        assert unfinished.read_text() == "do not delete"
        assert not any(e.kind == "scratch_cleanup_succeeded" for e in kb.list_events(conn, tid))


def test_archived_completed_legacy_missing_intent_recovers_on_tick(board, monkeypatch):
    kb, _ = board
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="completed legacy fallback")
        workspace = kb.resolve_workspace(kb.get_task(conn, tid))
        kb.set_workspace_path(conn, tid, workspace)
        (workspace / "disposable.txt").write_text("completed")
        original = kb._append_event

        def fail_intent(conn, task_id, kind, *args, **kwargs):
            if task_id == tid and kind == "scratch_cleanup_started":
                raise OSError("simulated legacy missing intent")
            return original(conn, task_id, kind, *args, **kwargs)

        with monkeypatch.context() as m:
            m.setattr(kb, "_append_event", fail_intent)
            assert kb.complete_task(conn, tid)
        assert workspace.exists()
        assert kb.archive_task(conn, tid)
        assert not any(e.kind.startswith("scratch_cleanup_") for e in kb.list_events(conn, tid))
        assert any(e.kind == "completed" for e in kb.list_events(conn, tid))
        kb.dispatch_once(conn, dry_run=True)
        assert workspace.exists()
        kb.dispatch_once(conn)
        assert not workspace.exists()
        assert [e.kind for e in kb.list_events(conn, tid) if e.kind.startswith("scratch_cleanup_")] == [
            "scratch_cleanup_started", "scratch_cleanup_succeeded",
        ]


def test_retry_batch_is_bounded_and_rotates(board, monkeypatch):
    kb, _ = board
    with kb.connect() as conn:
        ids = []
        for _ in range(3):
            tid = kb.create_task(conn, title="pending cleanup")
            workspace = kb.resolve_workspace(kb.get_task(conn, tid))
            kb.set_workspace_path(conn, tid, workspace)
            with kb.write_txn(conn):
                conn.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (tid,))
                kb._append_event(conn, tid, "scratch_cleanup_started", {"workspace_path": str(workspace)})
            ids.append(tid)
        seen = []
        monkeypatch.setattr(kb, "retry_failed_scratch_cleanup", lambda conn, tid: seen.append(tid) or True)
        monkeypatch.setattr(kb, "_scratch_retry_cursor", "")
        for _ in ids:
            kb._retry_pending_scratch_cleanup(conn, limit=1)
        assert set(seen) == set(ids)
        assert len(seen) == len(ids)


def test_retry_refuses_changed_or_unsafe_workspace_path(board):
    kb, home = board
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="unsafe retry")
        workspace = kb.resolve_workspace(kb.get_task(conn, tid))
        kb.set_workspace_path(conn, tid, workspace)
        (workspace / "keep.txt").write_text("data")
        outside = home / "unrelated"
        outside.mkdir()
        (outside / "keep.txt").write_text("safe")
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status = 'done', workspace_path = ? WHERE id = ?", (str(outside), tid))
            kb._append_event(conn, tid, "scratch_cleanup_started", {"workspace_path": str(workspace)})
        assert not kb.retry_failed_scratch_cleanup(conn, tid)
        kb.dispatch_once(conn)
        assert (outside / "keep.txt").read_text() == "safe"
        assert workspace.exists()
