"""Offline, exact-card PRD reconciliation regression tests (never a live board)."""

import argparse
import json

import pytest

from hermes_cli import kanban as cli
from hermes_cli import kanban_db as db


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "isolated-hermes"))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    db.create_board("reconciliation-test")
    db.init_db(board="reconciliation-test")
    with db.connect(board="reconciliation-test") as conn:
        target = db.create_task(conn, title="builder", initial_status="blocked")
        unrelated = db.create_task(conn, title="validator", initial_status="blocked")
        # Reproduce existing todo cards; creation intentionally only accepts running/blocked.
        conn.execute("UPDATE tasks SET status = 'todo' WHERE id IN (?, ?)", (target, unrelated))
    return target, unrelated


def _invoke(argv):
    parser = argparse.ArgumentParser()
    cli.build_parser(parser.add_subparsers())
    return cli.kanban_command(parser.parse_args(["kanban", *argv]))


def _args(task_id, *, board: str | None = "reconciliation-test", status="todo"):
    args = ["--board", board] if board else []
    return [*args, "reconcile-prd", task_id, "--expected-status", status,
            "--expected-run-count", "0", "--prd-id", "prd_test",
            "--prd-path", "/tmp/test-prd.md", "--actor", "reviewer",
            "--reason", "approved offline test", "--json"]


def _events(conn, task_id):
    return conn.execute("SELECT id, kind, payload FROM task_events WHERE task_id = ? "
                        "AND kind = 'prd_metadata_reconciled'", (task_id,)).fetchall()


def test_exact_card_atomic_receipt_and_independent_readback(board, capsys):
    target, unrelated = board
    with db.connect(board="reconciliation-test") as conn:
        before = conn.execute("SELECT * FROM tasks WHERE id = ?", (target,)).fetchone()
        before = dict(before)
    assert _invoke(_args(target)) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["board"] == "reconciliation-test"
    assert receipt["task_id"] == target
    assert receipt["status"] == "todo" and receipt["run_count"] == 0
    assert receipt["prd_metadata"] == {"prd_id": "prd_test", "prd_path": "/tmp/test-prd.md"}
    assert receipt["event_kind"] == "prd_metadata_reconciled"
    assert receipt["event_run_id"] is None
    assert receipt["event_payload"]["actor"] == "reviewer"
    assert receipt["event_payload"]["reason"] == "approved offline test"
    with db.connect(board="reconciliation-test") as conn:
        after = dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (target,)).fetchone())
        assert {k: v for k, v in after.items() if k != "prd_metadata"} == {
            k: v for k, v in before.items() if k != "prd_metadata"}
        assert db.get_task(conn, target).prd_metadata == receipt["prd_metadata"]
        assert db.get_task(conn, unrelated).prd_metadata is None
        assert conn.execute("SELECT COUNT(*) FROM task_runs WHERE task_id = ?", (target,)).fetchone()[0] == 0
        events = _events(conn, target)
        assert len(events) == 1 and events[0]["id"] == receipt["event_id"]
        assert json.loads(events[0]["payload"]) == receipt["event_payload"]
        assert not _events(conn, unrelated)
    assert _invoke(["--board", "reconciliation-test", "show", target, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["task"]["prd_metadata"] == receipt["prd_metadata"]


@pytest.mark.parametrize("condition", ["wrong_id", "wrong_status", "stamped", "run", "claim", "current_run", "done"])
def test_refuses_changed_or_unrelated_card_without_new_event(board, condition, capsys):
    target, unrelated = board
    if condition not in {"wrong_id", "wrong_status"}:
        with db.connect(board="reconciliation-test") as conn:
            if condition == "stamped":
                conn.execute("UPDATE tasks SET prd_metadata = ? WHERE id = ?", ('{"prd_id":"other"}', target))
            elif condition == "run":
                conn.execute("INSERT INTO task_runs (task_id, status, started_at) VALUES (?, 'done', 1)", (target,))
            elif condition in {"claim", "current_run", "done"}:
                column, value = {"claim": ("claim_lock", "lock"),
                                 "current_run": ("current_run_id", 123),
                                 "done": ("status", "done")}[condition]
                conn.execute(f"UPDATE tasks SET {column} = ? WHERE id = ?", (value, target))
    candidate = "t_nonexistent" if condition == "wrong_id" else target
    assert _invoke(_args(candidate, status="blocked" if condition == "wrong_status" else "todo")) == 1
    capsys.readouterr()
    with db.connect(board="reconciliation-test") as conn:
        assert not _events(conn, target)
        assert db.get_task(conn, unrelated).prd_metadata is None


def test_refuses_second_stamp_wrong_board_and_unsafe_routing(board, monkeypatch, capsys):
    target, unrelated = board
    db.create_board("other-test")
    db.init_db(board="other-test")
    # Even an identically named ID on another board must not be stamped there.
    with db.connect(board="other-test") as conn:
        other_id = db.create_task(conn, title="other board", initial_status="blocked")
        conn.execute("UPDATE tasks SET id = ?, status = 'todo' WHERE id = ?", (target, other_id))
    assert _invoke(_args(target, board="other-test")) == 0
    capsys.readouterr()
    with db.connect(board="reconciliation-test") as conn:
        task = db.get_task(conn, target)
        assert task is not None and task.prd_metadata is None
        assert not _events(conn, target)
    with db.connect(board="other-test") as conn:
        task = db.get_task(conn, target)
        assert task is not None and task.prd_metadata is not None
    assert _invoke(_args(target, board="typo-board")) == 1
    assert _invoke(_args(target, board=None)) == 2
    monkeypatch.setenv("HERMES_KANBAN_DB", "/tmp/forbidden-override.db")
    assert _invoke(_args(target)) == 2
    monkeypatch.delenv("HERMES_KANBAN_DB")
    assert _invoke(_args(target)) == 0
    capsys.readouterr()
    assert _invoke(_args(target)) == 1
    with db.connect(board="reconciliation-test") as conn:
        assert len(_events(conn, target)) == 1
        assert not _events(conn, unrelated)


@pytest.mark.parametrize("field,value", [("prd_id", " "), ("prd_path", "relative/path"),
                                         ("actor", ""), ("reason", " "),
                                         ("expected_run_count", 1), ("expected_status", "running")])
def test_db_api_validates_required_inputs(board, field, value):
    target, unrelated = board
    kwargs = dict(expected_status="todo", expected_run_count=0, prd_id="prd_test",
                  prd_path="/tmp/test.md", actor="reviewer", reason="audit")
    kwargs[field] = value
    with db.connect(board="reconciliation-test") as conn:
        with pytest.raises(ValueError):
            db.reconcile_existing_prd_metadata(conn, target, **kwargs)
        assert db.get_task(conn, target).prd_metadata is None
        assert db.get_task(conn, unrelated).prd_metadata is None
        assert not _events(conn, target)


def test_blocked_unrun_card_can_be_stamped(board, capsys):
    target, unrelated = board
    with db.connect(board="reconciliation-test") as conn:
        conn.execute("UPDATE tasks SET status = 'blocked' WHERE id = ?", (target,))
    assert _invoke(_args(target, status="blocked")) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "blocked"
    with db.connect(board="reconciliation-test") as conn:
        task = db.get_task(conn, target)
        assert task is not None
        assert task.status == "blocked" and task.prd_metadata == receipt["prd_metadata"]
        assert not _events(conn, unrelated)


def test_additive_migration_preserves_legacy_rows(board):
    target, _ = board
    with db.connect(board="reconciliation-test") as conn:
        # Simulate the old schema entirely inside the test home.
        conn.execute("ALTER TABLE tasks DROP COLUMN prd_metadata")
        db._migrate_add_optional_columns(conn)
        task = db.get_task(conn, target)
        assert task is not None and task.prd_metadata is None
        assert "prd_metadata" in {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
