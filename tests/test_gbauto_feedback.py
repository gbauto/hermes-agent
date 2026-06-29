from __future__ import annotations

from hermes_cli import gbauto_feedback as feedback
from hermes_cli import kanban_db


def test_submit_feedback_inserts_ops_website_feedback(monkeypatch):
    calls = []

    def fake_insert(table, row, *, on_conflict=None, timeout=45):
        calls.append((table, row, on_conflict, timeout))
        return {**row, "feedback_id": "11111111-1111-4111-8111-111111111111"}

    monkeypatch.setattr(feedback, "_run_insert", fake_insert)

    result = feedback.submit_feedback(
        message="Fix the chat page receipt.",
        route="/chat",
        page_url="http://127.0.0.1:9119/chat",
        client_slug="smoke-client",
        metadata={"page_title": "Hermes Agent - Dashboard"},
    )

    assert result["ok"] is True
    assert calls[0][0] == "ops_website_feedback"
    row = calls[0][1]
    assert row["status"] == "new"
    assert row["feedback_type"] == "website_feedback"
    assert row["route"] == "/chat"
    assert row["client_slug"] == "smoke-client"
    assert row["board_slug"] == "gbautomation"
    assert row["message"] == "Fix the chat page receipt."
    assert row["metadata"]["source"] == "hermes_dashboard_command_layer"
    assert row["metadata"]["page_title"] == "Hermes Agent - Dashboard"


def test_promote_feedback_to_kanban_creates_idempotent_triage_task(monkeypatch):
    row = {
        "feedback_id": "22222222-2222-4222-8222-222222222222",
        "created_at": "2026-06-29T01:19:36+00:00",
        "status": "new",
        "page_url": "http://127.0.0.1:9119/chat",
        "route": "/chat",
        "client_slug": "smoke-client",
        "repo_slug": "gbautomation",
        "board_slug": "gbautomation",
        "profile": None,
        "skill_name": None,
        "obs_session_id": None,
        "task_id": None,
        "run_id": None,
        "langfuse_trace_id": None,
        "feedback_type": "website_feedback",
        "message": "Wire the command feedback panel to Supabase.",
        "reporter_email": None,
        "reporter_name": None,
        "user_agent": "pytest",
        "metadata": {"source": "test"},
    }
    upserts = []

    monkeypatch.setattr(
        feedback,
        "load_feedback",
        lambda **kwargs: {"ok": True, "rows": [row]},
    )

    def fake_insert(table, patched, *, on_conflict=None, timeout=45):
        upserts.append((table, patched, on_conflict))
        return patched

    monkeypatch.setattr(feedback, "_run_insert", fake_insert)

    first = feedback.promote_feedback_to_kanban(board_slug="gbautomation", limit=5)
    second = feedback.promote_feedback_to_kanban(board_slug="gbautomation", limit=5)

    assert first["ok"] is True
    assert first["count"] == 1
    assert first["results"][0]["status"] == feedback.PROMOTED_STATUS
    assert second["results"][0]["task_id"] == first["results"][0]["task_id"]

    assert upserts[-1][0] == "ops_website_feedback"
    assert upserts[-1][1]["status"] == feedback.PROMOTED_STATUS
    assert upserts[-1][1]["task_id"] == first["results"][0]["task_id"]
    assert upserts[-1][2] == "feedback_id"

    conn = kanban_db.connect(board="gbautomation")
    try:
        task = kanban_db.get_task(conn, first["results"][0]["task_id"])
    finally:
        conn.close()

    assert task is not None
    assert task.status == "triage"
    assert task.assignee == "tac-director"
    assert task.tenant == "smoke-client"
    assert task.idempotency_key == "feedback:22222222-2222-4222-8222-222222222222"
    assert "human_gate_required: true" in (task.body or "")
