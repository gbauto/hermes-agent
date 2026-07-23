import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.run import GatewayRunner
from hermes_cli import kanban_db as kb


class RecordingAdapter:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, text, metadata=None):
        self.sent.append({"chat_id": chat_id, "text": text, "metadata": metadata or {}})


class DisconnectedAdapters(dict):
    """Expose a platform during collection, then simulate disconnect on get()."""

    def get(self, key, default=None):
        return None


async def _run_one_notifier_tick(monkeypatch, runner):
    real_sleep = asyncio.sleep

    async def fake_sleep(delay):
        if delay == 5:
            return None
        runner._running = False
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await runner._kanban_notifier_watcher(interval=1)


def _make_runner(adapter):
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._running = True
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._kanban_sub_fail_counts = {}
    return runner


def _create_completed_subscription(summary="done once"):
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="notify once", assignee="worker")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
        kb.complete_task(conn, tid, summary=summary)
        return tid
    finally:
        conn.close()


def _unseen_terminal_events(tid):
    conn = kb.connect()
    try:
        _, events = kb.unseen_events_for_sub(
            conn,
            task_id=tid,
            platform="telegram",
            chat_id="chat-1",
            kinds=["completed", "blocked", "gave_up", "crashed", "timed_out"],
        )
        return events
    finally:
        conn.close()


def test_kanban_notifier_dedupes_board_slugs_pointing_to_same_db(tmp_path, monkeypatch):
    db_path = tmp_path / "shared-kanban.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()
    kb.write_board_metadata("alias-a", name="Alias A")
    kb.write_board_metadata("alias-b", name="Alias B")

    tid = _create_completed_subscription()

    adapter = RecordingAdapter()
    runner = _make_runner(adapter)

    asyncio.run(_run_one_notifier_tick(monkeypatch, runner))

    assert len(adapter.sent) == 1
    assert "Kanban" in adapter.sent[0]["text"]
    assert tid in adapter.sent[0]["text"]


def test_kanban_notifier_claim_prevents_second_watcher_send(tmp_path, monkeypatch):
    db_path = tmp_path / "single-owner.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()

    tid = _create_completed_subscription()

    adapter1 = RecordingAdapter()
    adapter2 = RecordingAdapter()

    asyncio.run(_run_one_notifier_tick(monkeypatch, _make_runner(adapter1)))
    asyncio.run(_run_one_notifier_tick(monkeypatch, _make_runner(adapter2)))

    assert len(adapter1.sent) == 1
    assert adapter2.sent == []


def test_kanban_notifier_rewinds_claim_if_adapter_disconnects(tmp_path, monkeypatch):
    db_path = tmp_path / "adapter-disconnect.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()
    tid = _create_completed_subscription()

    runner = GatewayRunner.__new__(GatewayRunner)
    runner._running = True
    runner.adapters = DisconnectedAdapters({Platform.TELEGRAM: RecordingAdapter()})
    runner._kanban_sub_fail_counts = {}

    asyncio.run(_run_one_notifier_tick(monkeypatch, runner))

    assert [ev.kind for ev in _unseen_terminal_events(tid)] == ["completed"]


def test_kanban_db_path_is_test_isolated_from_real_home():
    hermes_home = Path(kb.kanban_home())
    production_db = Path.home() / ".hermes" / "kanban.db"
    assert kb.kanban_db_path().resolve() != production_db.resolve()

    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="x", assignee="worker")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
    finally:
        conn.close()

    assert kb.kanban_db_path().resolve().is_relative_to(hermes_home.resolve())
    assert kb.kanban_db_path().resolve() != production_db.resolve()


class FailingAdapter:
    """Adapter whose send() always raises, simulating a transient send error."""

    def __init__(self):
        self.attempts = 0

    async def send(self, chat_id, text, metadata=None):
        self.attempts += 1
        raise RuntimeError("simulated send failure")


def test_kanban_notifier_rewinds_claim_on_send_exception(tmp_path, monkeypatch):
    """A raising adapter rewinds the claim so the next tick can retry.

    This is the second rewind path (distinct from the adapter-disconnect path
    in test_kanban_notifier_rewinds_claim_if_adapter_disconnects). Here the
    adapter is connected and the send call actually fires; the claim must
    still rewind so the event isn't lost when send() raises mid-tick.
    """
    db_path = tmp_path / "send-failure.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()
    tid = _create_completed_subscription()

    adapter = FailingAdapter()
    runner = _make_runner(adapter)

    asyncio.run(_run_one_notifier_tick(monkeypatch, runner))

    # Send was attempted (so we exercised the failure path, not just the
    # disconnect path) and the claim was rewound — the unseen-events query
    # still returns the event for retry on the next tick.
    assert adapter.attempts >= 1, "send should have been attempted at least once"
    assert [ev.kind for ev in _unseen_terminal_events(tid)] == ["completed"]


def test_notifier_redelivers_same_kind_on_dispatch_cycle(tmp_path, monkeypatch):
    """A retry cycle (crashed → reclaimed → crashed) notifies the user twice.

    Before #21398 the notifier auto-unsubscribed on any terminal event kind
    (gave_up / crashed / timed_out), so the second crash in a respawn cycle
    silently dropped — the subscription was already gone. This test pins the
    new contract: subscription survives non-final terminal events; the
    cursor handles dedup.

    Two crashes ten seconds apart on the same task — both should land on
    the adapter.
    """
    db_path = tmp_path / "redeliver-cycle.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()

    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="cycle test", assignee="worker")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
        # First crash — fired by the dispatcher when the worker PID dies.
        kb._append_event(conn, tid, kind="crashed")
    finally:
        conn.close()

    adapter = RecordingAdapter()
    runner = _make_runner(adapter)
    asyncio.run(_run_one_notifier_tick(monkeypatch, runner))

    # First crash delivered.
    assert len(adapter.sent) == 1
    assert "crashed" in adapter.sent[0]["text"].lower()

    # Subscription survives — the cursor advanced past event #1, but the
    # row is still there.
    conn = kb.connect()
    try:
        subs = kb.list_notify_subs(conn, tid)
        assert len(subs) == 1, (
            "Subscription must survive a crashed event so a respawn-cycle "
            "second crash also notifies the user (issue #21398)."
        )

        # Second crash — same task, same dispatcher (or a respawn). Append
        # another event to simulate the dispatcher firing crashed a second
        # time during retry.
        kb._append_event(conn, tid, kind="crashed")
    finally:
        conn.close()

    # New tick: the second event has a fresh id past the cursor advance,
    # so it gets claimed and delivered.
    runner = _make_runner(adapter)
    asyncio.run(_run_one_notifier_tick(monkeypatch, runner))

    assert len(adapter.sent) == 2, (
        f"Second crashed event should also notify; got {len(adapter.sent)} "
        f"deliveries (texts: {[d['text'] for d in adapter.sent]})"
    )
    assert "crashed" in adapter.sent[1]["text"].lower()


class ArtifactRecordingAdapter(RecordingAdapter):
    def __init__(self):
        super().__init__()
        self.documents = []
        self.images = []
        self.videos = []

    def extract_local_files(self, text):
        from gateway.platforms.base import BasePlatformAdapter
        return BasePlatformAdapter.extract_local_files(text)

    async def send_document(self, chat_id, file_path, metadata=None, **_kw):
        self.documents.append(file_path)

    async def send_multiple_images(self, chat_id, images, metadata=None, **_kw):
        self.images.extend(p for p, _caption in images)

    async def send_video(self, chat_id, video_path, metadata=None, **_kw):
        self.videos.append(video_path)


def _collector_runner():
    return GatewayRunner.__new__(GatewayRunner)


def _collector_task(tmp_path, *, created_at=None, tid="t_parent"):
    workspace = tmp_path / tid
    workspace.mkdir(exist_ok=True)
    return SimpleNamespace(
        id=tid,
        title="collector",
        result=None,
        created_at=int(created_at or time.time()) - 10,
        workspace_path=str(workspace),
    )


def test_artifact_collector_explicit_payload_is_authoritative(tmp_path):
    task = _collector_task(tmp_path)
    wanted = Path(task.workspace_path) / "report.html"
    wanted.write_text("<h1>ok</h1>")
    contaminant = tmp_path / "old-gantt-summary.html"
    contaminant.write_text("stale")
    task.result = f"legacy fallback should not upload {contaminant}"

    paths, links = _collector_runner()._collect_kanban_artifacts(
        adapter=ArtifactRecordingAdapter(),
        task=task,
        event_payload={
            "summary": f"done, but mentions stale {contaminant}",
            "artifacts": [str(wanted)],
        },
    )

    assert paths == [str(wanted.resolve())]
    assert links == []


def test_artifact_collector_manifest_urls_dedupe_and_rank(tmp_path):
    task = _collector_task(tmp_path)
    workspace = Path(task.workspace_path)
    report = workspace / "report.html"
    manifest = workspace / "manifest.json"
    evidence = workspace / "public-dom-smoke.json"
    dup_report = workspace / "nested" / "report.html"
    dup_report.parent.mkdir()
    for path in (report, evidence, dup_report):
        path.write_text(path.name)
    manifest.write_text(
        '{"outputs": ['
        f'{{"path": "{report}", "kind": "html_report"}},'
        f'{{"path": "{dup_report}", "kind": "duplicate"}},'
        f'{{"path": "{evidence}", "kind": "validation_evidence"}},'
        '{"url": "https://files.catbox.moe/6m6vb3.html", "kind": "public_url"}'
        ']}'
    )

    paths, links = _collector_runner()._collect_kanban_artifacts(
        adapter=ArtifactRecordingAdapter(),
        task=task,
        event_payload={"artifacts": [str(manifest), str(report)]},
    )

    assert paths == [str(report.resolve()), str(manifest.resolve()), str(evidence.resolve())]
    assert links == ["https://files.catbox.moe/6m6vb3.html"]
    assert str(dup_report.resolve()) not in paths


def test_artifact_collector_legacy_fallback_requires_workspace_and_age(tmp_path):
    created = int(time.time())
    task = _collector_task(tmp_path, created_at=created)
    workspace = Path(task.workspace_path)
    valid = workspace / "implementation-receipt.md"
    stale = workspace / "old-output.md"
    outside = tmp_path / "outside.md"
    for path in (valid, stale, outside):
        path.write_text(path.name)
    os_times = [created + 5, created - 100, created + 5]
    for path, mtime in zip((valid, stale, outside), os_times):
        path.touch()
        import os
        os.utime(path, (mtime, mtime))
    task.result = f"valid {valid} stale {stale} outside {outside}"

    paths, _links = _collector_runner()._collect_kanban_artifacts(
        adapter=ArtifactRecordingAdapter(),
        task=task,
        event_payload={},
    )

    assert paths == [str(valid.resolve())]


def test_notifier_upload_path_does_not_use_summary_when_explicit_present(tmp_path, monkeypatch):
    db_path = tmp_path / "explicit-authoritative.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    wanted = workspace / "manifest.json"
    wanted.write_text("{}")
    contaminant = tmp_path / "index.html"
    contaminant.write_text("stale")

    conn = kb.connect()
    try:
        tid = kb.create_task(
            conn,
            title="explicit artifacts",
            assignee="worker",
            workspace_kind="dir",
            workspace_path=str(workspace),
        )
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
        kb.complete_task(
            conn,
            tid,
            summary=f"real output in payload; unrelated duplicate index is {contaminant}",
            metadata={"artifacts": [str(wanted)]},
        )
    finally:
        conn.close()

    adapter = ArtifactRecordingAdapter()
    asyncio.run(_run_one_notifier_tick(monkeypatch, _make_runner(adapter)))

    assert adapter.documents == [str(wanted.resolve())]
    assert str(contaminant.resolve()) not in adapter.documents


def test_completed_notification_says_no_task_specific_deliverable(tmp_path, monkeypatch):
    db_path = tmp_path / "no-output.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="no artifact", assignee="worker")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
        kb.complete_task(conn, tid, summary="done without artifacts", metadata={"artifacts": [str(tmp_path / 'missing.html')]})
    finally:
        conn.close()

    adapter = ArtifactRecordingAdapter()
    asyncio.run(_run_one_notifier_tick(monkeypatch, _make_runner(adapter)))

    assert adapter.documents == []
    assert "No task-specific deliverable recorded" in adapter.sent[0]["text"]


def test_dry_run_fixture_has_before_after_without_chat_ids():
    fixture_path = Path(__file__).parents[1] / "fixtures" / "kanban_notifier_artifact_selection.json"
    text = fixture_path.read_text()
    assert "chat_id" not in text
    assert "thread_id" not in text
    assert "-100" not in text
    data = __import__("json").loads(text)
    assert set(data["fixtures"]) == {"t_98bee624", "t_59ef1e8c"}
    for fixture in data["fixtures"].values():
        assert fixture["before"]["explicit_artifacts"]
        assert fixture["after"]["selected_outputs"]
        assert fixture["after"]["rejected"]
