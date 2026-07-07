"""Tests for the extracted GatewayKanbanWatchersMixin (god-file Phase 3).

The kanban watcher loops were lifted out of gateway/run.py into a mixin that
GatewayRunner inherits. These tests confirm the mixin exposes the methods and
that GatewayRunner picks them up via the MRO (behavior-neutral relocation).
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from gateway.kanban_watchers import GatewayKanbanWatchersMixin

KANBAN_METHODS = [
    "_kanban_notifier_watcher",
    "_kanban_dispatcher_watcher",
    "_kanban_advance",
    "_kanban_unsub",
    "_kanban_rewind",
    "_deliver_kanban_artifacts",
    "_format_kanban_blocked_notification",
    "_kanban_board_url",
    "_kanban_board_line",
]


def test_mixin_defines_kanban_methods():
    for m in KANBAN_METHODS:
        assert hasattr(GatewayKanbanWatchersMixin, m), f"mixin missing {m}"


def test_gateway_runner_inherits_mixin():
    # Import here so a heavy gateway import only happens if the first test passed.
    from gateway.run import GatewayRunner

    assert issubclass(GatewayRunner, GatewayKanbanWatchersMixin)
    # Each kanban method resolves to the mixin's implementation via the MRO.
    for m in KANBAN_METHODS:
        owner = next(c for c in GatewayRunner.__mro__ if m in c.__dict__)
        assert owner is GatewayKanbanWatchersMixin, (
            f"{m} resolved to {owner.__name__}, expected the mixin"
        )


def test_watcher_loops_are_coroutines():
    # The two long-running watchers are async loops.
    assert inspect.iscoroutinefunction(GatewayKanbanWatchersMixin._kanban_notifier_watcher)
    assert inspect.iscoroutinefunction(GatewayKanbanWatchersMixin._kanban_dispatcher_watcher)


def test_blocked_notification_uses_issue_label_and_log_root_cause(monkeypatch):
    mixin = GatewayKanbanWatchersMixin()
    monkeypatch.setattr(
        mixin,
        "_kanban_blocked_issue_from_run_or_log",
        lambda task, board: "Profile cannot load required skill(s): kanban-worker.",
    )
    task = SimpleNamespace(id="t_bad", assignee="ecom", result="", last_failure_error="pid 71936 not alive")
    event = SimpleNamespace(id=9, payload={"reason": "pid 71936 not alive"})

    msg, metadata = mixin._format_kanban_blocked_notification(
        sub={"task_id": "t_bad"},
        task=task,
        event=event,
        board_slug="gbautomation",
        title="[ecom] intake and normalize target",
        tag="@ecom ",
    )

    assert "Issue: Profile cannot load required skill(s): kanban-worker." in msg
    assert "Blocker:" not in msg
    assert "Unblock: A) Install the missing skill for this profile." in msg
    assert "Board:" not in msg
    assert "gbautomation · t_bad · owner ecom · source kanban-gateway" in msg
    assert metadata["kanban_blocker_event"]["issue"] == "Profile cannot load required skill(s): kanban-worker."


def test_kanban_board_url_absent_without_verified_live_board(monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_LIVE_BOARD_URL", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD_URL", raising=False)
    monkeypatch.delenv("HERMES_DASHBOARD_URL", raising=False)
    mixin = GatewayKanbanWatchersMixin()

    assert mixin._kanban_board_url("gbautomation", "t_123") is None
    assert mixin._kanban_board_line("gbautomation", "t_123") == ""


def test_kanban_board_url_suppresses_stale_netlify_preview(monkeypatch):
    monkeypatch.setenv(
        "HERMES_KANBAN_LIVE_BOARD_URL",
        "https://6a4b4a94f98b4b17c22234f4--gbautoxyz.netlify.app",
    )
    mixin = GatewayKanbanWatchersMixin()

    assert mixin._kanban_board_url("gbautomation", "t_123") is None
    assert mixin._kanban_board_line("gbautomation", "t_123") == ""


def test_kanban_board_url_allows_explicit_template_override(monkeypatch):
    monkeypatch.setenv(
        "HERMES_KANBAN_LIVE_BOARD_URL",
        "https://example.test/boards/{board_slug}/tasks/{task_id}",
    )
    mixin = GatewayKanbanWatchersMixin()

    assert mixin._kanban_board_url("gb automation", "t 123") == "https://example.test/boards/gb%20automation/tasks/t%20123"


def test_log_root_cause_extracts_unknown_skill():
    mixin = GatewayKanbanWatchersMixin()

    assert (
        mixin._extract_kanban_root_cause_line("Traceback\nError: Unknown skill(s): kanban-worker\n")
        == "Profile cannot load required skill(s): kanban-worker."
    )
