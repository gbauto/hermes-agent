import asyncio
import json
import sys
from pathlib import Path

from gateway.config import Platform
from gateway.run import GatewayRunner
from hermes_cli import kanban_closeout_artifacts as gate
from hermes_cli import kanban_db as kb
from tests.gateway.test_kanban_notifier import RecordingAdapter, _run_one_notifier_tick


class TelegramRecordingAdapter(RecordingAdapter):
    platform = Platform.TELEGRAM


def test_closeout_artifact_gate_default_off_allows_completion(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "default-off.db"))
    kb.init_db()
    conn = kb.connect()
    try:
        tid = kb.create_task(
            conn,
            title="parent artifact bundle closeout",
            assignee="worker",
            workspace_kind="dir",
            workspace_path=str(tmp_path / "workspace"),
        )
        assert kb.complete_task(
            conn,
            tid,
            summary="done",
            metadata={"producer_task_ids": ["t_child"], "artifacts": ["https://example.com/report.html"]},
        ) is True
        assert kb.get_task(conn, tid).status == "done"
    finally:
        conn.close()


def test_closeout_artifact_gate_fail_closed_to_closeout_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "fail-closed.db"))
    kb.init_db()
    monkeypatch.setattr(
        gate,
        "gate_settings",
        lambda: {"enabled": True, "require_for_all_completions": True, "publisher_command": ""},
    )
    conn = kb.connect()
    try:
        workspace = tmp_path / "workspace"
        tid = kb.create_task(
            conn,
            title="parent artifact bundle closeout",
            assignee="worker",
            workspace_kind="dir",
            workspace_path=str(workspace),
        )
        assert kb.complete_task(conn, tid, summary="done", metadata={"producer_task_ids": ["t_child"]}) is False
        task = kb.get_task(conn, tid)
        assert task.status == "closeout_pending"
        manifest = workspace / "kanban-closeout-artifact-manifest.json"
        assert manifest.exists()
        payloads = [e.payload for e in kb.list_events(conn, tid) if e.kind == "closeout_pending"]
        assert payloads and "publisher_command is empty" in payloads[-1]["reason"]
    finally:
        conn.close()


def test_closeout_artifact_gate_success_promotes_verified_html_url(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "success.db"))
    kb.init_db()
    publisher = tmp_path / "publisher.py"
    publisher.write_text(
        "import json, sys\n"
        "print(json.dumps({\"ok\": True, \"verified_html_url\": \"https://gbautomation.xyz/apps/closeout/index.html\", \"content_type\": \"text/html; charset=utf-8\"}))\n"
    )
    monkeypatch.setattr(
        gate,
        "gate_settings",
        lambda: {
            "enabled": True,
            "require_for_all_completions": True,
            "publisher_command": sys.executable,
            "publisher_timeout_seconds": 30,
        },
    )

    # run_gate receives argv[1] as manifest path, so wrap Python with a small shim
    # command path rather than relying on shell parsing.
    shim = tmp_path / "publisher-shim"
    shim.write_text(f"#!/usr/bin/env bash\nexec {sys.executable} {publisher}\n")
    shim.chmod(0o755)
    monkeypatch.setattr(
        gate,
        "gate_settings",
        lambda: {
            "enabled": True,
            "require_for_all_completions": True,
            "publisher_command": str(shim),
            "publisher_timeout_seconds": 30,
        },
    )

    conn = kb.connect()
    try:
        tid = kb.create_task(
            conn,
            title="parent artifact bundle closeout",
            assignee="worker",
            workspace_kind="dir",
            workspace_path=str(tmp_path / "workspace"),
        )
        assert kb.complete_task(conn, tid, summary="done", metadata={"producer_task_ids": ["t_child"]}) is True
        task = kb.get_task(conn, tid)
        assert task.status == "done"
        events = [e for e in kb.list_events(conn, tid) if e.kind == "completed"]
        assert events[-1].payload["verified_html_url"] == "https://gbautomation.xyz/apps/closeout/index.html"
        run = conn.execute("SELECT metadata FROM task_runs WHERE task_id = ? ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
        metadata = json.loads(run["metadata"])
        assert metadata["closeout_artifact_publication"]["content_type"].startswith("text/html")
    finally:
        conn.close()


def test_kanban_completed_notification_links_title_to_verified_html(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "notifier-link.db"))
    kb.init_db()
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="Verified Bundle", assignee="worker")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
        kb.complete_task(
            conn,
            tid,
            summary="done",
            metadata={"verified_html_url": "https://gbautomation.xyz/apps/verified-bundle/index.html"},
        )
    finally:
        conn.close()

    adapter = TelegramRecordingAdapter()
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._running = True
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._kanban_sub_fail_counts = {}
    asyncio.run(_run_one_notifier_tick(monkeypatch, runner))

    assert len(adapter.sent) == 1
    text = adapter.sent[0]["text"]
    assert "[Verified Bundle](https://gbautomation.xyz/apps/verified-bundle/index.html)" in text
    assert "No task-specific deliverable recorded" not in text
