import asyncio
import json
from pathlib import Path

import pytest

from gateway.config import Platform
from gateway.run import GatewayRunner
from hermes_cli import kanban_db as kb


@pytest.fixture(autouse=True)
def _clear_kanban_board_url_env(monkeypatch):
    for name in (
        "HERMES_KANBAN_LIVE_BOARD_URL",
        "HERMES_KANBAN_BOARD_URL",
        "HERMES_DASHBOARD_URL",
    ):
        monkeypatch.delenv(name, raising=False)


class RecordingAdapter:
    def __init__(self):
        self.sent = []
        self.documents = []
        self.image_batches = []
        self.videos = []

    async def send(self, chat_id, text, metadata=None):
        self.sent.append({"chat_id": chat_id, "text": text, "metadata": metadata or {}})

    def extract_local_files(self, text):
        return [], text

    async def send_document(self, chat_id, file_path, metadata=None):
        self.documents.append({"chat_id": chat_id, "file_path": file_path, "metadata": metadata or {}})

    async def send_multiple_images(self, chat_id, images, metadata=None):
        self.image_batches.append({"chat_id": chat_id, "images": images, "metadata": metadata or {}})

    async def send_video(self, chat_id, video_path, metadata=None):
        self.videos.append({"chat_id": chat_id, "video_path": video_path, "metadata": metadata or {}})


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


def test_kanban_board_url_absent_without_verified_config():
    runner = GatewayRunner.__new__(GatewayRunner)

    assert runner._kanban_board_line("gbautomation", "t_a5aed461") == ""


def test_kanban_board_url_suppresses_netlify_preview_env(monkeypatch):
    runner = GatewayRunner.__new__(GatewayRunner)
    monkeypatch.setenv(
        "HERMES_KANBAN_LIVE_BOARD_URL",
        "https://6a4b4a94f98b4b17c22234f4--gbautoxyz.netlify.app",
    )

    assert runner._kanban_board_line("gbautomation", "t_a5aed461") == ""


def test_kanban_board_url_uses_verified_non_netlify_config(monkeypatch):
    runner = GatewayRunner.__new__(GatewayRunner)
    monkeypatch.setenv("HERMES_KANBAN_LIVE_BOARD_URL", "https://ops.example.test/kanban")

    assert (
        runner._kanban_board_line("gbautomation", "t_a5aed461")
        == "Board: https://ops.example.test/kanban#task=t_a5aed461"
    )


def test_kanban_board_url_fills_verified_template(monkeypatch):
    runner = GatewayRunner.__new__(GatewayRunner)
    monkeypatch.setenv(
        "HERMES_KANBAN_LIVE_BOARD_URL",
        "https://ops.example.test/boards/{board_slug}/tasks/{task_id}",
    )

    assert (
        runner._kanban_board_line("gb automation", "t_a5aed461")
        == "Board: https://ops.example.test/boards/gb%20automation/tasks/t_a5aed461"
    )


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



def test_blocked_notification_includes_actionable_telegram_options(tmp_path, monkeypatch):
    """Blocked events should carry a concise message plus Telegram buttons."""
    db_path = tmp_path / "blocked-actions.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()

    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="needs human input", assignee="builder")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
        assert kb.block_task(conn, tid, reason="pick the release owner")
    finally:
        conn.close()

    adapter = RecordingAdapter()
    runner = _make_runner(adapter)
    asyncio.run(_run_one_notifier_tick(monkeypatch, runner))

    assert len(adapter.sent) == 1
    sent = adapter.sent[0]
    assert tid in sent["text"]
    assert "needs human input" in sent["text"]
    lines = sent["text"].splitlines()
    assert lines[0].startswith("🚫 Blocked - needs human input")
    assert lines[1] == "Issue: pick the release owner."
    assert "Reason:" not in sent["text"]
    assert "blocked" not in lines[1].lower().replace("unblocked", "")
    assert lines[2] == "Unblock: add missing context, then promote"
    assert "  B) " not in lines[2]
    assert all("Board:" not in line for line in lines)
    assert f"default · {tid} · owner builder · source kanban-gateway" in sent["text"]
    assert "pick the release owner" in sent["text"]
    keyboard = sent["metadata"].get("telegram_inline_keyboard")
    labels = [button["text"] for row in keyboard for button in row]
    assert labels[:3] == ["✅ Unblock", "🚀 Promote", "⏸ Keep blocked"]
    assert any(button.get("callback_data", "").startswith("kbb:u:") for row in keyboard for button in row)
    assert any(button.get("callback_data", "").startswith("kbb:p:") for row in keyboard for button in row)
    assert any(button.get("callback_data", "").startswith("kbb:k:") for row in keyboard for button in row)


def test_blocked_notification_maps_known_blocker_to_ab_suggestions(tmp_path, monkeypatch):
    db_path = tmp_path / "blocked-auth.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()

    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="refresh oauth", assignee="builder")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
        assert kb.block_task(conn, tid, reason="auth token expired for profile")
    finally:
        conn.close()

    adapter = RecordingAdapter()
    asyncio.run(_run_one_notifier_tick(monkeypatch, _make_runner(adapter)))

    text = adapter.sent[0]["text"]
    assert "Issue: auth token expired for profile." in text
    assert "Unblock: A) Reauth the profile with a known-good credential.  B) Sync the approved token, then unblock." in text
    assert "Board:" not in text


def test_blocked_notification_derives_issue_from_meta_reason(tmp_path, monkeypatch):
    db_path = tmp_path / "blocked-meta.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()

    body = """Replace orange circle with GB logo in report manager templates.

Issue: Missing canonical GB logo asset/path for the report templates.
"""
    conn = kb.connect()
    try:
        tid = kb.create_task(
            conn,
            title="Replace orange circle with GB logo in report manager templates",
            assignee="tac-builder",
            body=body,
        )
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
        assert kb.block_task(
            conn,
            tid,
            reason="Greg clarified Kanban blocked notification UX: use Issue lines",
        )
    finally:
        conn.close()

    adapter = RecordingAdapter()
    asyncio.run(_run_one_notifier_tick(monkeypatch, _make_runner(adapter)))

    text = adapter.sent[0]["text"]
    assert text.splitlines()[:4] == [
        "🚫 Blocked - Replace orange circle with GB logo in report manager templates",
        "Issue: Missing canonical GB logo asset/path for the report templates.",
        "Unblock: A) Provide the logo asset/path.  B) Use existing GBAuto wordmark from theme assets.",
        f"default · {tid} · owner tac-builder · source kanban-gateway",
    ]
    assert "Reason: Greg clarified" not in text


def test_hosted_html_artifact_resolver_uses_registry(tmp_path, monkeypatch):
    html_path = tmp_path / "approved-plan-email.html"
    html_path.write_text("<html>private</html>", encoding="utf-8")
    registry = tmp_path / "netlify.jsonl"
    registry.write_text(json.dumps({
        "html_path": str(html_path),
        "public_url": "https://approved-plan-email--gbautoxyz.netlify.app",
    }) + "\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_KANBAN_HTML_ARTIFACT_REGISTRY", str(registry))

    runner = GatewayRunner.__new__(GatewayRunner)

    assert runner._resolve_hosted_html_artifact_url(str(html_path)) == "https://approved-plan-email--gbautoxyz.netlify.app"


def test_hosted_html_artifact_resolver_prefers_public_url_over_netlify_drop(tmp_path, monkeypatch):
    html_path = tmp_path / "approved-plan-email.html"
    html_path.write_text("<html>private</html>", encoding="utf-8")
    registry = tmp_path / "netlify.jsonl"
    registry.write_text(json.dumps({
        "html_path": str(html_path),
        "deploy_url": "https://app.netlify.com/drop/private#drop_token=secret",
        "urls": [
            "https://app.netlify.com/drop/private#drop_token=secret",
            "https://approved-plan-email--gbautoxyz.netlify.app",
        ],
    }) + "\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_KANBAN_HTML_ARTIFACT_REGISTRY", str(registry))

    runner = GatewayRunner.__new__(GatewayRunner)

    assert runner._resolve_hosted_html_artifact_url(str(html_path)) == "https://approved-plan-email--gbautoxyz.netlify.app"


def test_deliver_kanban_html_artifact_sends_hosted_link_not_document(tmp_path, monkeypatch):
    html_path = tmp_path / "approved-plan-email.html"
    html_path.write_text("<html>private</html>", encoding="utf-8")
    registry = tmp_path / "netlify.jsonl"
    registry.write_text(json.dumps({
        "html_path": str(html_path),
        "public_url": "https://approved-plan-email--gbautoxyz.netlify.app",
    }) + "\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_KANBAN_HTML_ARTIFACT_REGISTRY", str(registry))
    monkeypatch.delenv("HERMES_KANBAN_HTML_ARTIFACT_DEBUG_UPLOAD", raising=False)

    runner = GatewayRunner.__new__(GatewayRunner)
    adapter = RecordingAdapter()
    asyncio.run(runner._deliver_kanban_artifacts(
        adapter=adapter,
        chat_id="chat-1",
        metadata={},
        event_payload={"artifacts": [str(html_path)]},
        task=None,
    ))

    assert adapter.sent == [{
        "chat_id": "chat-1",
        "text": "📎 HTML artifact: https://approved-plan-email--gbautoxyz.netlify.app",
        "metadata": {},
    }]
    assert adapter.documents == []


def test_deliver_kanban_unhosted_html_skips_raw_upload(tmp_path, monkeypatch):
    html_path = tmp_path / "unhosted.html"
    html_path.write_text("<html>no link</html>", encoding="utf-8")
    monkeypatch.delenv("HERMES_KANBAN_HTML_ARTIFACT_REGISTRY", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_HTML_ARTIFACT_DEBUG_UPLOAD", raising=False)

    runner = GatewayRunner.__new__(GatewayRunner)
    adapter = RecordingAdapter()
    asyncio.run(runner._deliver_kanban_artifacts(
        adapter=adapter,
        chat_id="chat-1",
        metadata={},
        event_payload={"artifacts": [str(html_path)]},
        task=None,
    ))

    assert adapter.sent == []
    assert adapter.documents == []


def test_deliver_kanban_html_debug_fallback_uploads_when_explicit(tmp_path, monkeypatch):
    html_path = tmp_path / "debug.html"
    html_path.write_text("<html>no link</html>", encoding="utf-8")
    monkeypatch.delenv("HERMES_KANBAN_HTML_ARTIFACT_REGISTRY", raising=False)
    monkeypatch.setenv("HERMES_KANBAN_HTML_ARTIFACT_DEBUG_UPLOAD", "1")

    runner = GatewayRunner.__new__(GatewayRunner)
    adapter = RecordingAdapter()
    asyncio.run(runner._deliver_kanban_artifacts(
        adapter=adapter,
        chat_id="chat-1",
        metadata={},
        event_payload={"artifacts": [str(html_path)]},
        task=None,
    ))

    assert adapter.sent == []
    assert adapter.documents == [{"chat_id": "chat-1", "file_path": str(html_path), "metadata": {}}]
