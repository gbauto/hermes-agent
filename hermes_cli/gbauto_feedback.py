"""Supabase-backed dashboard feedback intake and Kanban promotion.

The dashboard command layer writes feedback into ``ops_website_feedback`` via
the ``gbauto-supabase`` CLI. Sprint-manager/TAC automation can then poll new
rows and promote them into Hermes Kanban triage cards with an idempotency key.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any, Optional

from hermes_cli.gbauto_chat import ALLOWED_TENANTS

FEEDBACK_TABLE = "ops_website_feedback"
MAX_MESSAGE_LEN = 5000
MAX_LIMIT = 200
DEFAULT_BOARD = "gbautomation"
DEFAULT_FEEDBACK_TYPE = "website_feedback"
PROMOTED_STATUS = "kanban_triaged"


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''").replace("%", "%%") + "'"


def _clamp(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _run_cli(sql: str, *, timeout: int = 45) -> list[dict[str, Any]]:
    binary = shutil.which("gbauto-supabase")
    if not binary:
        raise RuntimeError("gbauto-supabase CLI is not on PATH")
    proc = subprocess.run(
        [binary, "--json", "query", sql],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "gbauto-supabase query failed").strip()
        raise RuntimeError(message[:1200])
    data = json.loads(proc.stdout or "[]")
    if isinstance(data, dict) and data.get("ok") is False:
        raise RuntimeError(str(data.get("error") or "gbauto-supabase query failed"))
    if isinstance(data, dict) and isinstance(data.get("value"), list):
        data = data["value"]
    if not isinstance(data, list):
        raise RuntimeError("gbauto-supabase returned an unexpected response shape")
    return [row for row in data if isinstance(row, dict)]


def _run_insert(
    table: str,
    row: dict[str, Any],
    *,
    on_conflict: Optional[str] = None,
    timeout: int = 45,
) -> Optional[dict[str, Any]]:
    binary = shutil.which("gbauto-supabase")
    if not binary:
        raise RuntimeError("gbauto-supabase CLI is not on PATH")
    fd, path = tempfile.mkstemp(suffix=".json", prefix="gbfeedback_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(row, handle)
        cmd = [binary, "--json", "insert", table, path]
        if on_conflict:
            cmd.extend(["--on-conflict", on_conflict])
        proc = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=timeout,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "gbauto-supabase insert failed").strip()
        raise RuntimeError(message[:1200])
    out = (proc.stdout or "").strip()
    if not out:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and data.get("ok") is False:
        raise RuntimeError(str(data.get("error") or "gbauto-supabase insert failed"))
    if isinstance(data, dict) and isinstance(data.get("value"), dict):
        return data["value"]
    if isinstance(data, dict) and isinstance(data.get("inserted"), dict):
        return data["inserted"]
    if isinstance(data, dict) and isinstance(data.get("upserted"), dict):
        return data["upserted"]
    if isinstance(data, dict) and isinstance(data.get("row"), dict):
        return data["row"]
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


def _clean_optional(value: Any, *, max_len: int = 512) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text[:max_len] if text else None


def submit_feedback(
    *,
    message: str,
    route: Optional[str] = None,
    page_url: Optional[str] = None,
    client_slug: str = "gbautomation",
    board_slug: str = DEFAULT_BOARD,
    feedback_type: str = DEFAULT_FEEDBACK_TYPE,
    reporter_email: Optional[str] = None,
    reporter_name: Optional[str] = None,
    user_agent: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    body = (message or "").strip()
    if not body:
        raise ValueError("feedback message is required")
    if len(body) > MAX_MESSAGE_LEN:
        raise ValueError("feedback message is too long")
    if client_slug not in ALLOWED_TENANTS:
        raise ValueError(f"client_slug not allowed: {client_slug}")

    row = {
        "status": "new",
        "page_url": _clean_optional(page_url, max_len=2048),
        "route": _clean_optional(route, max_len=512),
        "client_slug": client_slug,
        "repo_slug": "gbautomation",
        "board_slug": _clean_optional(board_slug, max_len=128) or DEFAULT_BOARD,
        "feedback_type": _clean_optional(feedback_type, max_len=128) or DEFAULT_FEEDBACK_TYPE,
        "message": body,
        "reporter_email": _clean_optional(reporter_email, max_len=320),
        "reporter_name": _clean_optional(reporter_name, max_len=160),
        "user_agent": _clean_optional(user_agent, max_len=1000),
        "metadata": {
            "source": "hermes_dashboard_command_layer",
            **(metadata or {}),
        },
    }
    inserted = _run_insert(FEEDBACK_TABLE, row)
    return {"ok": True, "row": inserted or row}


def load_feedback(
    *,
    status: str = "new",
    limit: Any = 50,
    board_slug: Optional[str] = None,
    client_slug: Optional[str] = None,
) -> dict[str, Any]:
    n = _clamp(limit, 50, 1, MAX_LIMIT)
    clauses = []
    if status and status != "all":
        clauses.append(f"status = {_sql_literal(status)}")
    if board_slug:
        clauses.append(f"board_slug = {_sql_literal(board_slug)}")
    if client_slug:
        if client_slug not in ALLOWED_TENANTS:
            raise ValueError(f"client_slug not allowed: {client_slug}")
        clauses.append(f"client_slug = {_sql_literal(client_slug)}")
    where = (" where " + " and ".join(clauses)) if clauses else ""
    sql = (
        "select feedback_id, created_at, status, page_url, route, client_slug, "
        "repo_slug, board_slug, profile, skill_name, obs_session_id, task_id, "
        "run_id, langfuse_trace_id, feedback_type, message, reporter_email, "
        "reporter_name, user_agent, metadata "
        f"from {FEEDBACK_TABLE}{where} order by created_at asc limit {n}"
    )
    return {"ok": True, "rows": _run_cli(sql)}


def _task_title(row: dict[str, Any]) -> str:
    route = row.get("route") or row.get("page_url") or "dashboard"
    message = " ".join(str(row.get("message") or "").split())
    if len(message) > 72:
        message = message[:69].rstrip() + "..."
    return f"Feedback: {route} - {message or row.get('feedback_id')}"


def _task_body(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    surfaces = [v for v in (row.get("route"), row.get("page_url")) if v]
    surfaces_text = ", ".join(str(v) for v in surfaces) or "unknown"
    return "\n".join(
        [
            "Feedback intake triage card.",
            "",
            f"Feedback ID: {row.get('feedback_id')}",
            f"Created at: {row.get('created_at')}",
            f"Client: {row.get('client_slug') or 'unknown'}",
            f"Route: {row.get('route') or 'unknown'}",
            f"Page URL: {row.get('page_url') or 'unknown'}",
            f"Feedback type: {row.get('feedback_type') or DEFAULT_FEEDBACK_TYPE}",
            "",
            "Original feedback:",
            str(row.get("message") or "").strip(),
            "",
            "Triage contract:",
            "- severity: classify before implementation",
            "- complexity: classify before implementation",
            f"- surfaces: {surfaces_text}",
            "- blast_radius: identify affected dashboard/client surfaces",
            "- plan: reproduce, scope, propose, then request human approval before build dispatch",
            "- human_gate_required: true",
            "",
            "Metadata:",
            json.dumps(metadata, indent=2, sort_keys=True),
        ]
    )


def _upsert_feedback_status(row: dict[str, Any], *, status: str, task_id: str, board_slug: str) -> None:
    patched = dict(row)
    patched["status"] = status
    patched["task_id"] = task_id
    patched["board_slug"] = board_slug
    _run_insert(FEEDBACK_TABLE, patched, on_conflict="feedback_id")


def promote_feedback_to_kanban(
    *,
    board_slug: Optional[str] = None,
    status: str = "new",
    limit: Any = 25,
    assignee: Optional[str] = "tac-director",
    priority: int = 60,
) -> dict[str, Any]:
    """Poll feedback rows and promote them to Kanban triage cards.

    Returns one result per feedback row. The task idempotency key is
    ``feedback:<feedback_id>``, so rerunning the poller is safe even if the
    Supabase status upsert fails or lags.
    """
    from hermes_cli import kanban_db

    rows = load_feedback(status=status, limit=limit, board_slug=board_slug).get("rows", [])
    results: list[dict[str, Any]] = []
    for row in rows:
        feedback_id = str(row.get("feedback_id") or "").strip()
        if not feedback_id:
            results.append({"ok": False, "error": "feedback row missing feedback_id", "row": row})
            continue
        target_board = board_slug or _clean_optional(row.get("board_slug"), max_len=128) or DEFAULT_BOARD
        kanban_db.init_db(board=target_board)
        conn = kanban_db.connect(board=target_board)
        try:
            task_id = kanban_db.create_task(
                conn,
                title=_task_title(row),
                body=_task_body(row),
                assignee=assignee,
                created_by="feedback-poller",
                workspace_kind="scratch",
                tenant=_clean_optional(row.get("client_slug"), max_len=128),
                priority=int(priority),
                triage=True,
                idempotency_key=f"feedback:{feedback_id}",
                board=target_board,
            )
        finally:
            conn.close()
        _upsert_feedback_status(row, status=PROMOTED_STATUS, task_id=task_id, board_slug=target_board)
        results.append(
            {
                "ok": True,
                "feedback_id": feedback_id,
                "task_id": task_id,
                "board_slug": target_board,
                "status": PROMOTED_STATUS,
            }
        )
    return {"ok": True, "count": len(results), "results": results}
