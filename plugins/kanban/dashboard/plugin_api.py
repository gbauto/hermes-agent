"""Kanban dashboard plugin — backend API routes.

Mounted at /api/plugins/kanban/ by the dashboard plugin system.

This layer is intentionally thin: every handler is a small wrapper around
``hermes_cli.kanban_db`` or a direct SQL query. Writes use the same code
paths the CLI and gateway ``/kanban`` command use, so the three surfaces
cannot drift.

Live updates arrive via the ``/events`` WebSocket, which tails the
append-only ``task_events`` table on a short poll interval (WAL mode lets
reads run alongside the dispatcher's IMMEDIATE write transactions).

Security note
-------------
Plugin HTTP routes go through the dashboard's session-token auth middleware
(``web_server.auth_middleware``) just like core API routes — every
``/api/plugins/...`` request must present the session bearer token (or the
session cookie set when you load the dashboard HTML). The token is the
random per-process ``_SESSION_TOKEN`` printed at startup; the dashboard's
own pages inject it via ``window.__HERMES_SESSION_TOKEN__`` so logged-in
browsers don't have to handle it manually.

For the ``/events`` WebSocket we still require the session token as a
``?token=`` query parameter (browsers cannot set the ``Authorization``
header on an upgrade request), matching the established pattern used by
the in-browser PTY bridge in ``hermes_cli/web_server.py``.

This means ``hermes dashboard --host 0.0.0.0`` is safe to run on a LAN:
plugin routes are no longer an unauthenticated exception. The auth still
isn't multi-user — anyone who can read the printed URL+token gets full
dashboard access — but they can't ride along just because they can reach
the port.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect, status as http_status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from hermes_cli import kanban_db
from hermes_cli import kanban_diagnostics as kd
from plugins.kanban.dashboard import inbox_store

log = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth helper — WebSocket only (HTTP routes live behind the dashboard's
# existing plugin-bypass; this is documented above).
# ---------------------------------------------------------------------------

def _ws_upgrade_authorized(ws: "WebSocket") -> bool:
    """Authorize a WebSocket upgrade by delegating to the dashboard's canonical
    WS auth gate (``hermes_cli.web_server._ws_auth_ok``).

    Delegating (rather than re-implementing a ``_SESSION_TOKEN``-only check)
    means this endpoint transparently accepts whatever the core gate accepts
    in each mode:

      * loopback / ``--insecure``: legacy ``?token=<_SESSION_TOKEN>``
      * gated OAuth: single-use ``?ticket=`` (the browser SDK's
        ``buildWsUrl`` mints one per connect)
      * server-internal: the process-lifetime ``?internal=`` credential

    The previous bespoke check only understood ``_SESSION_TOKEN``, so the
    kanban live-events WS was rejected on every OAuth-gated deployment even
    though the rest of the dashboard worked. Routing through the shared gate
    also means this can never drift from core auth again.

    Imported lazily so the plugin still loads in test contexts where the
    dashboard ``web_server`` module isn't importable (e.g. the bare-FastAPI
    test harness); there we accept so the tail loop stays testable, matching
    the prior behaviour.
    """
    try:
        from hermes_cli import web_server as _ws
    except Exception:
        # No dashboard context (tests). Accept so the tail loop is still
        # testable; in production the dashboard module always imports
        # cleanly because it's the caller.
        return True
    return bool(_ws._ws_auth_ok(ws))


def _resolve_board(board: Optional[str]) -> Optional[str]:
    """Validate and normalise a board slug from a query param.

    Raises :class:`HTTPException` 400 on malformed slugs so the browser
    sees a clean error instead of a 500. Returns the normalised slug,
    or ``None`` when the caller omitted the param (which then falls
    through to the active board inside ``kb.connect()``).
    """
    if board is None or board == "":
        return None
    try:
        normed = kanban_db._normalize_board_slug(board)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if normed and normed != kanban_db.DEFAULT_BOARD and not kanban_db.board_exists(normed):
        raise HTTPException(
            status_code=404,
            detail=f"board {normed!r} does not exist",
        )
    return normed


def _conn(board: Optional[str] = None):
    """Open a kanban_db connection, creating the schema on first use.

    Every handler that mutates the DB goes through this so the plugin
    self-heals on a fresh install (no user-visible "no such table"
    error if somebody hits POST /tasks before GET /board).
    ``init_db`` is idempotent.

    ``board`` is the query-param slug (already normalised by
    :func:`_resolve_board`). When ``None`` the active board is used
    via the resolution chain (env var → ``current`` file → ``default``).
    """
    try:
        kanban_db.init_db(board=board)
    except Exception as exc:
        log.warning("kanban init_db failed: %s", exc)
    return kanban_db.connect(board=board)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

# Columns shown by the dashboard, in left-to-right order. "archived" is
# available via a filter toggle rather than a visible column.
#
# Keep this in sync with kanban_db.VALID_STATUSES.  In particular,
# ``scheduled`` is a first-class waiting column used for time-based follow-ups;
# if it is omitted here, the board-level fallback below mis-buckets scheduled
# tasks into ``todo`` and makes the dashboard look like the Scheduled column
# disappeared.
BOARD_COLUMNS: list[str] = [
    "triage", "todo", "scheduled", "ready", "running", "blocked", "review", "done",
]


_CARD_SUMMARY_PREVIEW_CHARS = 200
_CARD_BODY_PREVIEW_CHARS = 320
_DASHBOARD_CARD_LIMIT_DEFAULT = 100
_DASHBOARD_CARD_LIMIT_MAX = 100
_PRD_HINT_RE = re.compile(
    r"(?:\bprd\b|product[-_ ]requirements?|second-brain/(?:inbox/)?plans/|"
    r"(?:^|/)(?:prd|prds)/)",
    re.IGNORECASE,
)


def _task_dict(
    task: kanban_db.Task,
    *,
    latest_summary: Optional[str] = None,
) -> dict[str, Any]:
    d = asdict(task)
    # Add derived age metrics so the UI can colour stale cards without
    # computing deltas client-side.
    try:
        d["age"] = kanban_db.task_age(task)
    except Exception:
        d["age"] = {"created_age_seconds": None, "started_age_seconds": None, "time_to_complete_seconds": None}
    # Surface the latest non-null run summary so dashboards don't show
    # blank cards/drawers for tasks where the worker handed off via
    # ``task_runs.summary`` (the kanban-worker pattern) instead of
    # ``tasks.result``. ``None`` when no run has produced a summary yet.
    d["latest_summary"] = latest_summary
    # Keep body short on list endpoints; full body comes from /tasks/:id.
    return d


def _coerce_timestamp(value: Any) -> int:
    """Normalize legacy integer, numeric-text, and SQLite datetime values."""
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    raw = str(value).strip()
    if not raw:
        return 0
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except (TypeError, ValueError, OverflowError):
        return 0


def _sql_timestamp_expr(column: str) -> str:
    """SQLite epoch projection for mixed legacy timestamp storage."""
    text_value = f"TRIM(CAST({column} AS TEXT))"
    return (
        "CASE "
        f"WHEN {column} IS NULL THEN 0 "
        f"WHEN TYPEOF({column}) IN ('integer', 'real') "
        f"THEN CAST({column} AS INTEGER) "
        f"WHEN {text_value} != '' "
        f"AND {text_value} NOT GLOB '*[^0-9.]*' "
        f"THEN CAST({column} AS INTEGER) "
        f"ELSE COALESCE(unixepoch({column}), 0) END"
    )


def _task_activity_at(
    task: kanban_db.Task,
    *,
    latest_event_at: Optional[int] = None,
) -> int:
    """Best available task activity timestamp, including persisted updates."""
    return max(
        _coerce_timestamp(task.created_at),
        _coerce_timestamp(task.started_at),
        _coerce_timestamp(task.completed_at),
        _coerce_timestamp(task.last_heartbeat_at),
        _coerce_timestamp(latest_event_at),
    )


def _repo_label(project_id: Any, workspace_path: Any) -> Optional[str]:
    """Derive a compact repository facet from task-native workspace fields."""
    project = str(project_id or "").strip()
    if project:
        return project
    raw_path = str(workspace_path or "").strip().replace("\\", "/").rstrip("/")
    if not raw_path:
        return None
    name = raw_path.rsplit("/", 1)[-1]
    if not name or re.fullmatch(r"t_[0-9a-f]+", name, re.IGNORECASE):
        return None
    return name


def _task_has_prd(task: kanban_db.Task) -> bool:
    values = (
        task.title,
        task.body,
        task.result,
        task.workflow_template_id,
    )
    return any(_PRD_HINT_RE.search(str(value or "")) for value in values)


def _compact_task_dict(
    task: kanban_db.Task,
    *,
    latest_summary: Optional[str] = None,
    latest_event_at: Optional[int] = None,
) -> dict[str, Any]:
    """Card-list projection; the task drawer remains the full-data endpoint."""
    d = _task_dict(task, latest_summary=latest_summary)
    d["body"] = _excerpt(task.body, _CARD_BODY_PREVIEW_CHARS)
    d["result"] = _excerpt(task.result, _CARD_SUMMARY_PREVIEW_CHARS)
    d["last_failure_error"] = _excerpt(
        task.last_failure_error,
        _CARD_SUMMARY_PREVIEW_CHARS,
    )
    d["activity_at"] = _task_activity_at(
        task,
        latest_event_at=latest_event_at,
    )
    d["repo"] = _repo_label(task.project_id, task.workspace_path)
    d["has_prd"] = _task_has_prd(task)
    return d


def _compact_board_where(
    *,
    tenant: Optional[str],
    assignee: Optional[str],
    include_archived: bool,
    query: Optional[str],
    parent_only: bool,
    repo: Optional[str],
    prd_only: bool,
) -> tuple[str, list[Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if not include_archived:
        clauses.append("t.status != 'archived'")
    if tenant:
        clauses.append("t.tenant = ?")
        params.append(tenant)
    if assignee:
        clauses.append("t.assignee = ?")
        params.append(assignee)
    if parent_only:
        clauses.append(
            "EXISTS (SELECT 1 FROM task_links parent_link "
            "WHERE parent_link.parent_id = t.id)"
        )
    if repo:
        normalized_repo = str(repo).strip().lower()
        clauses.append(
            "(LOWER(COALESCE(t.project_id, '')) = ? "
            "OR LOWER(REPLACE(COALESCE(t.workspace_path, ''), '\\', '/')) = ? "
            "OR LOWER(REPLACE(COALESCE(t.workspace_path, ''), '\\', '/')) "
            "LIKE ?)"
        )
        params.extend(
            [
                normalized_repo,
                normalized_repo,
                f"%/{normalized_repo}",
            ]
        )
    if prd_only:
        clauses.append(
            "("
            "LOWER(COALESCE(t.title, '')) LIKE '%prd%' "
            "OR LOWER(COALESCE(t.body, '')) LIKE '%prd%' "
            "OR LOWER(COALESCE(t.body, '')) LIKE '%second-brain/plans/%' "
            "OR LOWER(COALESCE(t.body, '')) LIKE '%second-brain/inbox/plans/%' "
            "OR LOWER(COALESCE(t.result, '')) LIKE '%prd%' "
            "OR LOWER(COALESCE(t.workflow_template_id, '')) LIKE '%prd%'"
            ")"
        )
    q = str(query or "").strip().lower()
    if q:
        clauses.append(
            "LOWER("
            "COALESCE(t.id, '') || ' ' || "
            "COALESCE(t.title, '') || ' ' || "
            "COALESCE(t.body, '') || ' ' || "
            "COALESCE(t.result, '') || ' ' || "
            "COALESCE(t.assignee, '') || ' ' || "
            "COALESCE(t.tenant, '') || ' ' || "
            "COALESCE(t.project_id, '') || ' ' || "
            "COALESCE(t.workspace_path, '')"
            ") LIKE ?"
        )
        params.append(f"%{q}%")
    return " AND ".join(clauses), params


def _compact_board_tasks(
    conn: sqlite3.Connection,
    *,
    tenant: Optional[str],
    assignee: Optional[str],
    include_archived: bool,
    query: Optional[str],
    parent_only: bool,
    repo: Optional[str],
    prd_only: bool,
    limit_per_column: int,
    sort: str,
) -> tuple[list[kanban_db.Task], dict[str, int]]:
    """Return exact filtered totals plus a bounded card window per status.

    Separate status queries let SQLite use ``idx_tasks_status`` and stop after
    each column's limit. A window function over the entire tasks table looked
    elegant but still materialized and ranked all 100k+ rows before discarding
    almost all of them.
    """
    where_sql, params = _compact_board_where(
        tenant=tenant,
        assignee=assignee,
        include_archived=include_archived,
        query=query,
        parent_only=parent_only,
        repo=repo,
        prd_only=prd_only,
    )
    if sort == "recent":
        activity_parts = [
            _sql_timestamp_expr("t.created_at"),
            _sql_timestamp_expr("t.started_at"),
            _sql_timestamp_expr("t.completed_at"),
            _sql_timestamp_expr("t.last_heartbeat_at"),
            (
                "COALESCE((SELECT MAX("
                + _sql_timestamp_expr("activity_event.created_at")
                + ") FROM task_events activity_event "
                "WHERE activity_event.task_id = t.id), 0)"
            ),
        ]
        order_sql = (
            f"MAX({', '.join(activity_parts)}) DESC, "
            "t.priority DESC, t.id DESC"
        )
    else:
        order_sql = "t.priority DESC, t.created_at ASC, t.id ASC"
    total_rows = conn.execute(
        f"SELECT t.status, COUNT(*) AS n FROM tasks t "
        f"WHERE {where_sql} GROUP BY t.status",
        params,
    ).fetchall()
    totals = {
        str(row["status"]): int(row["n"])
        for row in total_rows
    }
    statuses = list(BOARD_COLUMNS)
    if include_archived:
        statuses.append("archived")
    rows: list[sqlite3.Row] = []
    for status_name in statuses:
        if totals.get(status_name, 0) <= 0:
            continue
        rows.extend(
            conn.execute(
                f"SELECT t.* FROM tasks t "
                f"WHERE {where_sql} AND t.status = ? "
                f"ORDER BY {order_sql} LIMIT ?",
                [*params, status_name, int(limit_per_column)],
            ).fetchall()
        )
    return [kanban_db.Task.from_row(row) for row in rows], totals


def _board_repositories(conn: sqlite3.Connection) -> list[str]:
    repos: set[str] = set()
    for row in conn.execute(
        "SELECT DISTINCT project_id, workspace_path FROM tasks "
        "WHERE status != 'archived' "
        "AND (project_id IS NOT NULL OR workspace_path IS NOT NULL)"
    ).fetchall():
        label = _repo_label(row["project_id"], row["workspace_path"])
        if label:
            repos.add(label)
    return sorted(repos, key=str.lower)


def _event_dict(event: kanban_db.Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "task_id": event.task_id,
        "kind": event.kind,
        "payload": event.payload,
        "created_at": event.created_at,
        "run_id": event.run_id,
    }


def _comment_dict(c: kanban_db.Comment) -> dict[str, Any]:
    return {
        "id": c.id,
        "task_id": c.task_id,
        "author": c.author,
        "body": c.body,
        "created_at": c.created_at,
    }


def _attachment_dict(a: kanban_db.Attachment) -> dict[str, Any]:
    """Serialise an Attachment for the drawer. ``stored_path`` is the
    absolute on-disk path workers read; the UI uses ``id`` for download."""
    return {
        "id": a.id,
        "task_id": a.task_id,
        "filename": a.filename,
        "content_type": a.content_type,
        "size": a.size,
        "uploaded_by": a.uploaded_by,
        "stored_path": a.stored_path,
        "created_at": a.created_at,
    }


def _run_dict(r: kanban_db.Run) -> dict[str, Any]:
    """Serialise a Run for the drawer's Run history section."""
    return {
        "id": r.id,
        "task_id": r.task_id,
        "profile": r.profile,
        "step_key": r.step_key,
        "status": r.status,
        "claim_lock": r.claim_lock,
        "claim_expires": r.claim_expires,
        "worker_pid": r.worker_pid,
        "max_runtime_seconds": r.max_runtime_seconds,
        "last_heartbeat_at": r.last_heartbeat_at,
        "started_at": r.started_at,
        "ended_at": r.ended_at,
        "outcome": r.outcome,
        "summary": r.summary,
        "metadata": r.metadata,
        "error": r.error,
    }


# Hallucination-warning event kinds — see complete_task() in kanban_db.py.
# completion_blocked_hallucination: kernel rejected created_cards with
#   phantom ids; task stays in prior state.
# suspected_hallucinated_references: prose scan found t_<hex> in summary
#   that doesn't resolve; completion succeeded, advisory only.
_WARNING_EVENT_KINDS = (
    "completion_blocked_hallucination",
    "suspected_hallucinated_references",
)


def _compute_task_diagnostics(
    conn: sqlite3.Connection,
    task_ids: Optional[list[str]] = None,
) -> dict[str, list[dict]]:
    """Run the diagnostic rule engine against every task (or a subset)
    and return ``{task_id: [diagnostic_dict, ...]}``.

    Tasks with no active diagnostics are omitted from the result.
    Uses ``hermes_cli.kanban_diagnostics`` — see that module for the
    rule definitions.
    """
    from hermes_cli import kanban_diagnostics as kd
    from hermes_cli.config import load_config

    diag_config = kd.config_from_runtime_config(load_config())

    # Build the candidate task list. We need each task's row + its
    # events + its runs. Doing N separate queries works but scales
    # poorly; do three aggregate queries instead.
    if task_ids is not None:
        if not task_ids:
            return {}
        placeholders = ",".join(["?"] * len(task_ids))
        rows = conn.execute(
            f"SELECT * FROM tasks WHERE id IN ({placeholders})",
            tuple(task_ids),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status != 'archived'",
        ).fetchall()

    if not rows:
        return {}

    # Index events + runs by task id. For very large boards this will
    # slurp a lot — acceptable on the dashboard's typical working set
    # (hundreds of tasks), but we can add pagination / filtering later
    # if profiling shows it's a hotspot.
    row_ids = [r["id"] for r in rows]
    placeholders = ",".join(["?"] * len(row_ids))
    events_by_task: dict[str, list] = {tid: [] for tid in row_ids}
    for ev_row in conn.execute(
        f"SELECT * FROM task_events WHERE task_id IN ({placeholders}) ORDER BY id",
        tuple(row_ids),
    ).fetchall():
        events_by_task.setdefault(ev_row["task_id"], []).append(ev_row)
    runs_by_task: dict[str, list] = {tid: [] for tid in row_ids}
    for run_row in conn.execute(
        f"SELECT * FROM task_runs WHERE task_id IN ({placeholders}) ORDER BY id",
        tuple(row_ids),
    ).fetchall():
        runs_by_task.setdefault(run_row["task_id"], []).append(run_row)

    out: dict[str, list[dict]] = {}
    for r in rows:
        tid = r["id"]
        diags = kd.compute_task_diagnostics(
            r,
            events_by_task.get(tid, []),
            runs_by_task.get(tid, []),
            config=diag_config,
        )
        if diags:
            out[tid] = [d.to_dict() for d in diags]
    return out


def _warnings_summary_from_diagnostics(
    diagnostics: list[dict],
) -> Optional[dict]:
    """Compact summary for cards: {count, highest_severity, kinds,
    latest_at}. Replaces the old hallucination-only ``warnings`` object
    — same shape additions plus ``highest_severity`` so the UI can color
    badges per diagnostic severity.

    Returns None when ``diagnostics`` is empty.
    """
    if not diagnostics:
        return None
    from hermes_cli.kanban_diagnostics import SEVERITY_ORDER

    kinds: dict[str, int] = {}
    latest = 0
    highest_idx = -1
    highest_sev: Optional[str] = None
    count = 0
    for d in diagnostics:
        kinds[d["kind"]] = kinds.get(d["kind"], 0) + d.get("count", 1)
        count += d.get("count", 1)
        la = d.get("last_seen_at") or 0
        if la > latest:
            latest = la
        sev = d.get("severity")
        if sev in SEVERITY_ORDER:
            idx = SEVERITY_ORDER.index(sev)
            if idx > highest_idx:
                highest_idx = idx
                highest_sev = sev
    return {
        "count": count,
        "kinds": kinds,
        "latest_at": latest,
        "highest_severity": highest_sev,
    }


def _links_for(conn: sqlite3.Connection, task_id: str) -> dict[str, list[str]]:
    """Return {'parents': [...], 'children': [...]} for a task."""
    parents = [
        r["parent_id"]
        for r in conn.execute(
            "SELECT parent_id FROM task_links WHERE child_id = ? ORDER BY parent_id",
            (task_id,),
        )
    ]
    children = [
        r["child_id"]
        for r in conn.execute(
            "SELECT child_id FROM task_links WHERE parent_id = ? ORDER BY child_id",
            (task_id,),
        )
    ]
    return {"parents": parents, "children": children}


# ---------------------------------------------------------------------------
# Sprint command-center helpers
# ---------------------------------------------------------------------------

_SPRINT_OPEN_STATUSES = {
    "triage", "todo", "scheduled", "ready", "running", "blocked", "review",
}
_SPRINT_ACTIVE_STATUSES = {"ready", "running", "review"}
_SPRINT_COMPLETE_STATUSES = {"done", "archived"}
_SPRINT_REFERENCE_RE = re.compile(
    r"(?P<path>(?:second-brain|artifacts|docs|receipts)/"
    r"[A-Za-z0-9_./-]+\.(?:md|html|json|png|svg|pdf))",
    re.IGNORECASE,
)


def _epoch(value: Any) -> int:
    """Return a defensive integer epoch for mixed SQLite values."""
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _excerpt(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _extract_reference_paths(*values: Any) -> list[dict[str, str]]:
    """Extract stable plan/evidence paths from task text.

    The sprint index intentionally points at durable artifacts already named by
    cards; it never scans arbitrary files or guesses paths.  That keeps the
    dashboard live and board-backed while still exposing the plan/evidence
    vocabulary used by Sprint Manager reports.
    """
    found: dict[str, dict[str, str]] = {}
    for value in values:
        text = str(value or "").replace("\\", "/")
        for match in _SPRINT_REFERENCE_RE.finditer(text):
            path = match.group("path").rstrip(".,;:)]}")
            lowered = path.lower()
            kind = (
                "plan"
                if lowered.startswith("second-brain/plans/")
                or lowered.startswith("second-brain/inbox/plans/")
                else "evidence"
            )
            found.setdefault(path, {"path": path, "kind": kind})
    return list(found.values())


def _reference_status(statuses: set[str]) -> str:
    if "blocked" in statuses:
        return "blocked"
    if "running" in statuses:
        return "running"
    if "review" in statuses:
        return "review"
    if "ready" in statuses:
        return "ready"
    if statuses and statuses.issubset(_SPRINT_COMPLETE_STATUSES):
        return "done"
    return "open"


def _task_sprint_dict(
    row: dict[str, Any],
    *,
    now: int,
    refs: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    created_at = _epoch(row.get("created_at"))
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "status": row.get("status"),
        "priority": int(row.get("priority") or 0),
        "assignee": row.get("assignee"),
        "tenant": row.get("tenant"),
        "project_id": row.get("project_id"),
        "block_kind": row.get("block_kind"),
        "created_at": created_at,
        "completed_at": _epoch(row.get("completed_at")) or None,
        "age_days": round(max(0, now - created_at) / 86400, 1) if created_at else None,
        "references": refs or [],
    }


def _build_sprint_snapshot(
    task_rows: list[dict[str, Any]],
    link_rows: list[dict[str, Any]],
    *,
    now: int,
    days: int,
) -> dict[str, Any]:
    """Build the live Sprint Manager projection from board-native rows."""
    start = now - days * 86400
    by_id = {str(row["id"]): row for row in task_rows}
    children: dict[str, list[str]] = {}
    child_ids: set[str] = set()
    for link in link_rows:
        parent_id = str(link.get("parent_id") or "")
        child_id = str(link.get("child_id") or "")
        if not parent_id or not child_id:
            continue
        children.setdefault(parent_id, []).append(child_id)
        child_ids.add(child_id)

    status_counts: dict[str, int] = {}
    references_by_task: dict[str, list[dict[str, str]]] = {}
    reference_index: dict[str, dict[str, Any]] = {}
    for row in task_rows:
        task_id = str(row["id"])
        status = str(row.get("status") or "todo")
        status_counts[status] = status_counts.get(status, 0) + 1
        refs = _extract_reference_paths(row.get("title"), row.get("body"), row.get("result"))
        references_by_task[task_id] = refs
        for ref in refs:
            entry = reference_index.setdefault(
                ref["path"],
                {
                    "path": ref["path"],
                    "kind": ref["kind"],
                    "mentions": 0,
                    "task_ids": [],
                    "task_titles": [],
                    "statuses": set(),
                    "latest_at": 0,
                },
            )
            entry["mentions"] += 1
            entry["task_ids"].append(task_id)
            entry["task_titles"].append(row.get("title") or task_id)
            entry["statuses"].add(status)
            entry["latest_at"] = max(entry["latest_at"], _epoch(row.get("created_at")))

    created_window = sum(1 for row in task_rows if _epoch(row.get("created_at")) >= start)
    completed_window = sum(
        1
        for row in task_rows
        if str(row.get("status") or "") in _SPRINT_COMPLETE_STATUSES
        and _epoch(row.get("completed_at")) >= start
    )
    open_count = sum(status_counts.get(status, 0) for status in _SPRINT_OPEN_STATUSES)
    active_count = sum(status_counts.get(status, 0) for status in _SPRINT_ACTIVE_STATUSES)
    blocked_count = status_counts.get("blocked", 0)

    def task_sort_key(row: dict[str, Any]) -> tuple[int, int]:
        return (-int(row.get("priority") or 0), -_epoch(row.get("created_at")))

    parent_candidates = [
        row
        for task_id, row in by_id.items()
        if task_id in children
        and task_id not in child_ids
        and (
            str(row.get("status") or "") in _SPRINT_OPEN_STATUSES
            or _epoch(row.get("completed_at")) >= start
        )
    ]
    parent_candidates.sort(key=task_sort_key)

    rock_rows = [
        row for row in parent_candidates
        if str(row.get("status") or "") in _SPRINT_OPEN_STATUSES
    ]
    if len(rock_rows) < 6:
        seen = {str(row["id"]) for row in rock_rows}
        recent_open = [
            row
            for row in task_rows
            if str(row["id"]) not in seen
            and str(row.get("status") or "") in _SPRINT_OPEN_STATUSES
            and (_epoch(row.get("created_at")) >= now - 14 * 86400 or int(row.get("priority") or 0) >= 100)
        ]
        recent_open.sort(key=task_sort_key)
        rock_rows.extend(recent_open[: 6 - len(rock_rows)])

    rocks: list[dict[str, Any]] = []
    for row in rock_rows[:6]:
        task_id = str(row["id"])
        child_rows = [by_id[cid] for cid in children.get(task_id, []) if cid in by_id]
        total = len(child_rows)
        done = sum(
            1 for child in child_rows
            if str(child.get("status") or "") in _SPRINT_COMPLETE_STATUSES
        )
        item = _task_sprint_dict(
            row, now=now, refs=references_by_task.get(task_id),
        )
        item["progress"] = {"done": done, "total": total}
        rocks.append(item)

    issues = []
    blocked_rows = [
        row for row in task_rows if str(row.get("status") or "") == "blocked"
    ]
    blocked_rows.sort(key=task_sort_key)
    for row in blocked_rows[:12]:
        item = _task_sprint_dict(
            row, now=now, refs=references_by_task.get(str(row["id"])),
        )
        item["failure_excerpt"] = _excerpt(row.get("last_failure_error"), 160)
        issues.append(item)

    workstreams: list[dict[str, Any]] = []
    for row in parent_candidates[:16]:
        task_id = str(row["id"])
        member_rows = [row] + [
            by_id[cid] for cid in children.get(task_id, []) if cid in by_id
        ]
        counts: dict[str, int] = {}
        for member in member_rows:
            status = str(member.get("status") or "todo")
            counts[status] = counts.get(status, 0) + 1
        tasks = [
            _task_sprint_dict(
                member,
                now=now,
                refs=references_by_task.get(str(member["id"])),
            )
            for member in sorted(member_rows, key=task_sort_key)
        ]
        workstreams.append({
            "id": task_id,
            "title": row.get("title"),
            "status": row.get("status"),
            "priority": int(row.get("priority") or 0),
            "assignee": row.get("assignee"),
            "tenant": row.get("tenant"),
            "counts": counts,
            "done": sum(counts.get(s, 0) for s in _SPRINT_COMPLETE_STATUSES),
            "total": len(member_rows),
            "tasks": tasks,
        })

    refs_out: list[dict[str, Any]] = []
    for entry in reference_index.values():
        statuses = set(entry.pop("statuses"))
        entry["status"] = _reference_status(statuses)
        entry["task_ids"] = entry["task_ids"][:8]
        entry["task_titles"] = entry["task_titles"][:8]
        refs_out.append(entry)
    refs_out.sort(
        key=lambda item: (
            0 if item["kind"] == "plan" else 1,
            -int(item["latest_at"]),
            -int(item["mentions"]),
            item["path"],
        )
    )

    return {
        "generated_at": now,
        "window": {"days": days, "start": start, "end": now},
        "status_counts": status_counts,
        "scorecard": {
            "created": created_window,
            "completed": completed_window,
            "flow_delta": completed_window - created_window,
            "open": open_count,
            "active": active_count,
            "blocked": blocked_count,
            "ready": status_counts.get("ready", 0),
            "running": status_counts.get("running", 0),
            "review": status_counts.get("review", 0),
        },
        "rocks": rocks,
        "issues": issues,
        "workstreams": workstreams,
        "references": refs_out[:80],
    }


@router.get("/sprint")
def get_sprint_snapshot(
    days: int = Query(7, ge=1, le=90),
    board: Optional[str] = Query(None, description="Kanban board slug (omit for current)"),
):
    """Return the live Sprint Manager projection for a board.

    This endpoint is read-only.  Rocks and workstreams come from the native
    parent/child graph; scorecard numbers and IDS blockers come from task
    state; plan/evidence entries are durable paths explicitly mentioned by
    cards.  No frozen report snapshot is embedded in the dashboard.
    """
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        task_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT id, title, body, assignee, status, priority, created_at, "
                "started_at, completed_at, tenant, result, last_failure_error, "
                "project_id, block_kind FROM tasks"
            ).fetchall()
        ]
        link_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT parent_id, child_id FROM task_links"
            ).fetchall()
        ]
        return _build_sprint_snapshot(
            task_rows,
            link_rows,
            now=int(time.time()),
            days=days,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /board
# ---------------------------------------------------------------------------

def _get_compact_board(
    *,
    board: Optional[str],
    tenant: Optional[str],
    assignee: Optional[str],
    include_archived: bool,
    query: Optional[str],
    parent_only: bool,
    repo: Optional[str],
    prd_only: bool,
    limit_per_column: int,
    sort: str,
    include_diagnostics: bool = True,
) -> dict[str, Any]:
    """Bounded dashboard card projection with lazy full details."""
    conn = _conn(board=board)
    try:
        tasks, column_totals = _compact_board_tasks(
            conn,
            tenant=tenant,
            assignee=assignee,
            include_archived=include_archived,
            query=query,
            parent_only=parent_only,
            repo=repo,
            prd_only=prd_only,
            limit_per_column=limit_per_column,
            sort=sort,
        )
        task_ids = [task.id for task in tasks]
        link_counts: dict[str, dict[str, int]] = {}
        comment_counts: dict[str, int] = {}
        progress: dict[str, dict[str, int]] = {}
        diagnostics_per_task: dict[str, list[dict]] = {}
        summary_map: dict[str, str] = {}
        activity_map: dict[str, int] = {}
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            activity_map = {
                row["task_id"]: _coerce_timestamp(row["activity_at"])
                for row in conn.execute(
                    f"SELECT task_id, "
                    f"MAX({_sql_timestamp_expr('created_at')}) AS activity_at "
                    f"FROM task_events WHERE task_id IN ({placeholders}) "
                    f"GROUP BY task_id",
                    task_ids,
                ).fetchall()
            }
            for row in conn.execute(
                f"SELECT parent_id, child_id FROM task_links "
                f"WHERE parent_id IN ({placeholders}) "
                f"OR child_id IN ({placeholders})",
                [*task_ids, *task_ids],
            ).fetchall():
                link_counts.setdefault(
                    row["parent_id"], {"parents": 0, "children": 0},
                )["children"] += 1
                link_counts.setdefault(
                    row["child_id"], {"parents": 0, "children": 0},
                )["parents"] += 1
            comment_counts = {
                row["task_id"]: int(row["n"])
                for row in conn.execute(
                    f"SELECT task_id, COUNT(*) AS n FROM task_comments "
                    f"WHERE task_id IN ({placeholders}) GROUP BY task_id",
                    task_ids,
                ).fetchall()
            }
            for row in conn.execute(
                f"SELECT l.parent_id AS pid, t.status AS cstatus "
                f"FROM task_links l JOIN tasks t ON t.id = l.child_id "
                f"WHERE l.parent_id IN ({placeholders})",
                task_ids,
            ).fetchall():
                item = progress.setdefault(
                    row["pid"], {"done": 0, "total": 0},
                )
                item["total"] += 1
                if row["cstatus"] == "done":
                    item["done"] += 1
            if include_diagnostics:
                diagnostics_per_task = _compute_task_diagnostics(
                    conn, task_ids=task_ids,
                )
            summary_map = kanban_db.latest_summaries(conn, task_ids)

        column_names = list(BOARD_COLUMNS)
        if include_archived:
            column_names.append("archived")
        columns: dict[str, list[dict[str, Any]]] = {
            name: [] for name in column_names
        }
        for task in tasks:
            full_summary = summary_map.get(task.id)
            preview = (
                full_summary[:_CARD_SUMMARY_PREVIEW_CHARS]
                if full_summary else None
            )
            item = _compact_task_dict(
                task,
                latest_summary=preview,
                latest_event_at=activity_map.get(task.id),
            )
            item["link_counts"] = link_counts.get(
                task.id, {"parents": 0, "children": 0},
            )
            item["is_parent"] = item["link_counts"]["children"] > 0
            item["comment_count"] = comment_counts.get(task.id, 0)
            item["progress"] = progress.get(task.id)
            diagnostics = diagnostics_per_task.get(task.id)
            if diagnostics:
                item["diagnostics"] = diagnostics
                item["warnings"] = _warnings_summary_from_diagnostics(
                    diagnostics,
                )
            column = task.status if task.status in columns else "todo"
            columns[column].append(item)

        tenants = [
            row["tenant"]
            for row in conn.execute(
                "SELECT DISTINCT tenant FROM tasks "
                "WHERE tenant IS NOT NULL ORDER BY tenant"
            )
        ]
        assignees = [
            row["assignee"]
            for row in conn.execute(
                "SELECT DISTINCT assignee FROM tasks "
                "WHERE assignee IS NOT NULL AND status != 'archived' "
                "ORDER BY assignee"
            )
        ]
        latest_event_id = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS m FROM task_events"
        ).fetchone()["m"]
        return {
            "compact": True,
            "columns": [
                {
                    "name": name,
                    "tasks": columns[name],
                    "total": int(column_totals.get(name, len(columns[name]))),
                    "limited": int(column_totals.get(name, 0)) > len(columns[name]),
                }
                for name in column_names
            ],
            "tenants": tenants,
            "assignees": assignees,
            "repositories": _board_repositories(conn),
            "latest_event_id": int(latest_event_id),
            "limit_per_column": int(limit_per_column),
            "now": int(time.time()),
        }
    finally:
        conn.close()


@router.get("/board")
def get_board(
    tenant: Optional[str] = Query(None, description="Filter to a single tenant"),
    assignee: Optional[str] = Query(None, description="Filter to one agent profile"),
    include_archived: bool = Query(False),
    board: Optional[str] = Query(None, description="Kanban board slug (omit for current)"),
    workflow_template_id: Optional[str] = Query(
        None, description="Restrict to tasks using this workflow template id",
    ),
    current_step_key: Optional[str] = Query(
        None, description="Restrict to tasks at this workflow step key",
    ),
    q: Optional[str] = Query(None, description="Server-side card search"),
    parent_only: bool = Query(False, description="Only tasks with linked children"),
    repo: Optional[str] = Query(None, description="Repository/project facet"),
    prd_only: bool = Query(False, description="Only PRD/plan-linked tasks"),
    compact: bool = Query(False, description="Return bounded card previews"),
    limit_per_column: int = Query(
        _DASHBOARD_CARD_LIMIT_DEFAULT,
        ge=1,
        le=_DASHBOARD_CARD_LIMIT_MAX,
    ),
    sort: str = Query("recent", pattern="^(priority|recent)$"),
):
    """Return the full board grouped by status column.

    ``_conn()`` auto-initializes ``kanban.db`` on first call so a fresh
    install doesn't surface a "failed to load" error on the plugin tab.

    ``board`` selects which board to read from. Omitting it falls
    through to the active board (``HERMES_KANBAN_BOARD`` env → on-disk
    ``current`` pointer → ``default``).
    """
    board = _resolve_board(board)
    if compact:
        return _get_compact_board(
            board=board,
            tenant=tenant,
            assignee=assignee,
            include_archived=include_archived,
            query=q,
            parent_only=parent_only,
            repo=repo,
            prd_only=prd_only,
            limit_per_column=limit_per_column,
            sort=sort,
        )
    conn = _conn(board=board)
    try:
        tasks = kanban_db.list_tasks(
            conn,
            assignee=assignee,
            tenant=tenant,
            include_archived=include_archived,
            workflow_template_id=workflow_template_id,
            current_step_key=current_step_key,
        )
        # Pre-fetch link counts per task (cheap: one query).
        link_counts: dict[str, dict[str, int]] = {}
        for row in conn.execute(
            "SELECT parent_id, child_id FROM task_links"
        ).fetchall():
            link_counts.setdefault(row["parent_id"], {"parents": 0, "children": 0})[
                "children"
            ] += 1
            link_counts.setdefault(row["child_id"], {"parents": 0, "children": 0})[
                "parents"
            ] += 1

        # Comment + event counts (both cheap aggregates).
        comment_counts: dict[str, int] = {
            r["task_id"]: r["n"]
            for r in conn.execute(
                "SELECT task_id, COUNT(*) AS n FROM task_comments GROUP BY task_id"
            )
        }

        # Progress rollup: for each parent, how many children are done / total.
        # One pass over task_links joined with child status — cheaper than
        # N per-task queries and the plugin uses it to render "N/M".
        progress: dict[str, dict[str, int]] = {}
        for row in conn.execute(
            "SELECT l.parent_id AS pid, t.status AS cstatus "
            "FROM task_links l JOIN tasks t ON t.id = l.child_id"
        ).fetchall():
            p = progress.setdefault(row["pid"], {"done": 0, "total": 0})
            p["total"] += 1
            if row["cstatus"] == "done":
                p["done"] += 1

        # Diagnostics rollup for this board — see kanban_diagnostics.
        # We get the full structured list per task AND a compact
        # summary for the card badge (so cards don't carry the detail
        # text; the drawer fetches that via /tasks/:id or /diagnostics).
        diagnostics_per_task = _compute_task_diagnostics(conn, task_ids=None)

        latest_event_id = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS m FROM task_events"
        ).fetchone()["m"]

        columns: dict[str, list[dict]] = {c: [] for c in BOARD_COLUMNS}
        if include_archived:
            columns["archived"] = []

        # Batch-fetch the latest non-null run summary per task in one
        # window-function query (avoids N+1 ``latest_summary`` calls
        # for boards with hundreds of tasks). Truncated to a card-size
        # preview here — the full text is available via /tasks/:id.
        summary_map = kanban_db.latest_summaries(conn, [t.id for t in tasks])

        for t in tasks:
            full = summary_map.get(t.id)
            preview = (
                full[:_CARD_SUMMARY_PREVIEW_CHARS] if full else None
            )
            d = _task_dict(t, latest_summary=preview)
            d["link_counts"] = link_counts.get(t.id, {"parents": 0, "children": 0})
            d["comment_count"] = comment_counts.get(t.id, 0)
            d["progress"] = progress.get(t.id)  # None when the task has no children
            diags = diagnostics_per_task.get(t.id)
            if diags:
                # Full list goes into the payload so the drawer can render
                # without a second round-trip. The board-level badge only
                # needs the summary.
                d["diagnostics"] = diags
                d["warnings"] = _warnings_summary_from_diagnostics(diags)
            col = t.status if t.status in columns else "todo"
            columns[col].append(d)

        # Stable per-column ordering already applied by list_tasks
        # (priority DESC, created_at ASC), keep as-is.

        # List of known tenants for the UI filter dropdown.
        tenants = [
            r["tenant"]
            for r in conn.execute(
                "SELECT DISTINCT tenant FROM tasks WHERE tenant IS NOT NULL ORDER BY tenant"
            )
        ]
        # List of distinct assignees for the lane-by-profile sub-grouping.
        assignees = [
            r["assignee"]
            for r in conn.execute(
                "SELECT DISTINCT assignee FROM tasks WHERE assignee IS NOT NULL "
                "AND status != 'archived' ORDER BY assignee"
            )
        ]

        return {
            "columns": [
                {"name": name, "tasks": columns[name]} for name in columns.keys()
            ],
            "tenants": tenants,
            "assignees": assignees,
            "latest_event_id": int(latest_event_id),
            "now": int(time.time()),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /tasks/:id
# ---------------------------------------------------------------------------

@router.get("/tasks/{task_id}")
def get_task(
    task_id: str,
    board: Optional[str] = Query(None),
    run_state_type: Optional[str] = Query(
        None, description="With run_state_name: filter runs by column 'status' or 'outcome'",
    ),
    run_state_name: Optional[str] = Query(
        None, description="With run_state_type: exact value for that run column",
    ),
):
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        if (run_state_type is None) ^ (run_state_name is None):
            raise HTTPException(
                status_code=400,
                detail="run_state_type and run_state_name must be passed together or omitted",
            )
        if run_state_type is not None and run_state_type not in ("status", "outcome"):
            raise HTTPException(
                status_code=400,
                detail="run_state_type must be 'status' or 'outcome'",
            )
        task = kanban_db.get_task(conn, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")
        # Drawer/detail view returns the FULL summary (no truncation) so
        # operators can read the complete worker handoff without making
        # a second round-trip. Cards on /board carry a 200-char preview.
        full_summary = kanban_db.latest_summary(conn, task_id)
        task_d = _task_dict(task, latest_summary=full_summary)
        # Attach diagnostics so the drawer's Diagnostics section can
        # render recovery actions without a second round-trip.
        diags = _compute_task_diagnostics(conn, task_ids=[task_id])
        diag_list = diags.get(task_id) or []
        if diag_list:
            task_d["diagnostics"] = diag_list
            task_d["warnings"] = _warnings_summary_from_diagnostics(diag_list)
        return {
            "task": task_d,
            "comments": [_comment_dict(c) for c in kanban_db.list_comments(conn, task_id)],
            "events": [_event_dict(e) for e in kanban_db.list_events(conn, task_id)],
            "attachments": [_attachment_dict(a) for a in kanban_db.list_attachments(conn, task_id)],
            "links": _links_for(conn, task_id),
            "runs": [
                _run_dict(r)
                for r in kanban_db.list_runs(
                    conn,
                    task_id,
                    state_type=run_state_type,
                    state_name=run_state_name,
                )
            ],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# POST /tasks
# ---------------------------------------------------------------------------

class CreateTaskBody(BaseModel):
    title: str
    body: Optional[str] = None
    assignee: Optional[str] = None
    tenant: Optional[str] = None
    priority: int = 0
    workspace_kind: str = "scratch"
    workspace_path: Optional[str] = None
    parents: list[str] = Field(default_factory=list)
    triage: bool = False
    idempotency_key: Optional[str] = None
    max_runtime_seconds: Optional[int] = None
    skills: Optional[list[str]] = None
    goal_mode: bool = False
    goal_max_turns: Optional[int] = None


@router.post("/tasks")
def create_task(payload: CreateTaskBody, board: Optional[str] = Query(None)):
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        task_id = kanban_db.create_task(
            conn,
            title=payload.title,
            body=payload.body,
            assignee=payload.assignee,
            created_by="dashboard",
            workspace_kind=payload.workspace_kind,
            workspace_path=payload.workspace_path,
            tenant=payload.tenant,
            priority=payload.priority,
            parents=payload.parents,
            triage=payload.triage,
            idempotency_key=payload.idempotency_key,
            max_runtime_seconds=payload.max_runtime_seconds,
            skills=payload.skills,
            goal_mode=payload.goal_mode,
            goal_max_turns=payload.goal_max_turns,
        )
        task = kanban_db.get_task(conn, task_id)
        body: dict[str, Any] = {"task": _task_dict(task) if task else None}
        # Surface a dispatcher-presence warning so the UI can show a
        # banner when a `ready` task would otherwise sit idle because no
        # gateway is running (or dispatch_in_gateway=false). Only emit
        # for ready+assigned tasks; triage/todo are expected to wait,
        # and unassigned tasks can't be dispatched regardless.
        if task and task.status == "ready" and task.assignee:
            try:
                from hermes_cli.kanban import _check_dispatcher_presence
                running, message = _check_dispatcher_presence()
                if not running and message:
                    body["warning"] = message
            except Exception:
                # Probe failure must never block the create itself.
                pass
        return body
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Attachments — upload / list / download / delete (#35338)
# ---------------------------------------------------------------------------

# Cap a single upload so a runaway request can't fill the disk. 25 MB
# comfortably covers PDFs, images, and source docs — the kanban use case.
_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


def _safe_attachment_name(raw: str) -> str:
    """Reduce a client-supplied filename to a safe basename.

    Strips any directory components (``os.path.basename`` on both
    separators) so a malicious ``../../etc/passwd`` or ``C:\\x`` collapses
    to its leaf. Rejects empty / dotfile-only names. The result is only
    ever joined under the per-task attachments dir, never used verbatim
    as a path from the client.
    """
    name = (raw or "").replace("\\", "/").split("/")[-1].strip()
    # Drop control chars and leading dots so we never write a dotfile or
    # a name with embedded NULs/newlines.
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '\x00').strip()
    name = name.lstrip(".").strip()
    if not name:
        raise HTTPException(status_code=400, detail="invalid attachment filename")
    return name[:200]


@router.get("/tasks/{task_id}/attachments")
def list_task_attachments(task_id: str, board: Optional[str] = Query(None)):
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        if kanban_db.get_task(conn, task_id) is None:
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")
        return {
            "attachments": [
                _attachment_dict(a) for a in kanban_db.list_attachments(conn, task_id)
            ]
        }
    finally:
        conn.close()


@router.post("/tasks/{task_id}/attachments")
async def upload_task_attachment(
    task_id: str,
    file: UploadFile = File(...),
    board: Optional[str] = Query(None),
    uploaded_by: Optional[str] = Form(None),
):
    """Store an uploaded file for a task and record its metadata.

    The blob lands under ``attachments_root(board)/<task_id>/`` with a
    sanitised, collision-resolved name. The worker reads it via the
    absolute path surfaced in ``build_worker_context``.
    """
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        if kanban_db.get_task(conn, task_id) is None:
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")

        safe_name = _safe_attachment_name(file.filename or "")

        # Stream to disk with a hard size cap so a huge upload can't fill
        # the disk. Read in chunks; abort + clean up if the cap is hit.
        dest_dir = kanban_db.task_attachments_dir(task_id, board=board)
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Resolve name collisions: foo.pdf → foo (1).pdf, foo (2).pdf, …
        stem, dot, ext = safe_name.partition(".")
        candidate = safe_name
        n = 1
        while (dest_dir / candidate).exists():
            candidate = f"{stem} ({n}){dot}{ext}"
            n += 1
        dest_path = dest_dir / candidate

        total = 0
        try:
            with open(dest_path, "wb") as out:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _MAX_ATTACHMENT_BYTES:
                        out.close()
                        dest_path.unlink(missing_ok=True)
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                f"attachment exceeds {_MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB limit"
                            ),
                        )
                    out.write(chunk)
        except HTTPException:
            raise
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"failed to store attachment: {exc}")

        att_id = kanban_db.add_attachment(
            conn,
            task_id,
            filename=candidate,
            stored_path=str(dest_path.resolve()),
            content_type=file.content_type,
            size=total,
            uploaded_by=(uploaded_by or "dashboard"),
        )
        att = kanban_db.get_attachment(conn, att_id)
        return {"attachment": _attachment_dict(att) if att else None}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


@router.get("/attachments/{attachment_id}")
def download_attachment(attachment_id: int, board: Optional[str] = Query(None)):
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        att = kanban_db.get_attachment(conn, attachment_id)
        if att is None:
            raise HTTPException(status_code=404, detail="attachment not found")
        # Confirm the blob still lives under the board's attachments root
        # before serving — defense in depth against a tampered DB row.
        root = kanban_db.attachments_root(board=board).resolve()
        try:
            stored = Path(att.stored_path).resolve()
            stored.relative_to(root)
        except (ValueError, OSError):
            raise HTTPException(status_code=404, detail="attachment file unavailable")
        if not stored.is_file():
            raise HTTPException(status_code=404, detail="attachment file missing on disk")
        return FileResponse(
            path=str(stored),
            filename=att.filename,
            media_type=att.content_type or "application/octet-stream",
        )
    finally:
        conn.close()


@router.delete("/attachments/{attachment_id}")
def remove_attachment(attachment_id: int, board: Optional[str] = Query(None)):
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        att = kanban_db.delete_attachment(conn, attachment_id)
        if att is None:
            raise HTTPException(status_code=404, detail="attachment not found")
        return {"ok": True, "id": attachment_id}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PATCH /tasks/:id  (status / assignee / priority / title / body)
# ---------------------------------------------------------------------------

class UpdateTaskBody(BaseModel):
    status: Optional[str] = None
    assignee: Optional[str] = None
    priority: Optional[int] = None
    title: Optional[str] = None
    body: Optional[str] = None
    result: Optional[str] = None
    block_reason: Optional[str] = None
    # Structured handoff fields — forwarded to complete_task when status
    # transitions to 'done'. Dashboard parity with ``hermes kanban
    # complete --summary ... --metadata ...``.
    summary: Optional[str] = None
    metadata: Optional[dict] = None


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, payload: UpdateTaskBody, board: Optional[str] = Query(None)):
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        task = kanban_db.get_task(conn, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")

        # --- assignee ----------------------------------------------------
        if payload.assignee is not None:
            try:
                ok = kanban_db.assign_task(
                    conn, task_id, payload.assignee or None,
                )
            except RuntimeError as e:
                raise HTTPException(status_code=409, detail=str(e))
            if not ok:
                raise HTTPException(status_code=404, detail="task not found")

        # --- status -------------------------------------------------------
        if payload.status is not None:
            s = payload.status
            ok = True
            if s == "done":
                ok = kanban_db.complete_task(
                    conn, task_id,
                    result=payload.result,
                    summary=payload.summary,
                    metadata=payload.metadata,
                )
            elif s == "blocked":
                ok = kanban_db.block_task(conn, task_id, reason=payload.block_reason)
            elif s == "scheduled":
                ok = kanban_db.schedule_task(conn, task_id, reason=payload.block_reason)
            elif s == "ready":
                # Re-open a blocked/scheduled task, or just an explicit status set.
                current = kanban_db.get_task(conn, task_id)
                if current and current.status in ("blocked", "scheduled"):
                    ok = kanban_db.unblock_task(conn, task_id)
                else:
                    # Direct status write for drag-drop (todo -> ready etc).
                    ok = _set_status_direct(conn, task_id, "ready")
            elif s == "archived":
                ok = kanban_db.archive_task(conn, task_id)
            elif s == "running":
                raise HTTPException(
                    status_code=400,
                    detail="Cannot set status to 'running' directly; use the dispatcher/claim path",
                )
            elif s in ("todo", "triage", "scheduled"):
                ok = _set_status_direct(conn, task_id, s)
            else:
                raise HTTPException(status_code=400, detail=f"unknown status: {s}")
            if not ok:
                # For ``ready``, name the blocking parent(s) so the dashboard
                # can render an actionable toast instead of a silent no-op.
                # See #26744.
                if s == "ready":
                    blockers = _parents_blocking_ready(conn, task_id)
                    if blockers:
                        names = ", ".join(
                            f"{p['title']!r} ({p['id']}, status={p['status']})"
                            for p in blockers
                        )
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"Cannot move to 'ready': blocked by parent(s) "
                                f"not done — {names}"
                            ),
                        )
                raise HTTPException(
                    status_code=409,
                    detail=f"status transition to {s!r} not valid from current state",
                )

        # --- priority -----------------------------------------------------
        if payload.priority is not None:
            with kanban_db.write_txn(conn):
                conn.execute(
                    "UPDATE tasks SET priority = ? WHERE id = ?",
                    (int(payload.priority), task_id),
                )
                conn.execute(
                    "INSERT INTO task_events (task_id, kind, payload, created_at) "
                    "VALUES (?, 'reprioritized', ?, ?)",
                    (task_id, json.dumps({"priority": int(payload.priority)}),
                     int(time.time())),
                )

        # --- title / body -------------------------------------------------
        if payload.title is not None or payload.body is not None:
            with kanban_db.write_txn(conn):
                sets, vals = [], []
                if payload.title is not None:
                    if not payload.title.strip():
                        raise HTTPException(status_code=400, detail="title cannot be empty")
                    sets.append("title = ?")
                    vals.append(payload.title.strip())
                if payload.body is not None:
                    sets.append("body = ?")
                    vals.append(payload.body)
                vals.append(task_id)
                conn.execute(
                    f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", vals,
                )
                conn.execute(
                    "INSERT INTO task_events (task_id, kind, payload, created_at) "
                    "VALUES (?, 'edited', NULL, ?)",
                    (task_id, int(time.time())),
                )

        updated = kanban_db.get_task(conn, task_id)
        return {"task": _task_dict(updated) if updated else None}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# DELETE /tasks/:id
# ---------------------------------------------------------------------------

@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, board: Optional[str] = Query(None)):
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        ok = kanban_db.delete_task(conn, task_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")
        return {"deleted": True, "task_id": task_id}
    finally:
        conn.close()


def _parents_blocking_ready(
    conn: sqlite3.Connection, task_id: str,
) -> list:
    """Return parent rows (``id``, ``title``, ``status``) that aren't ``done``
    and therefore prevent ``task_id`` from being promoted to ``ready``.

    Used to enrich the 409 response from :func:`update_task` so the
    dashboard can show an actionable toast (#26744) instead of a silent
    no-op.  Returns ``[]`` when nothing blocks the transition (e.g. no
    parents, or all parents already done).
    """
    rows = conn.execute(
        "SELECT t.id, t.title, t.status FROM tasks t "
        "JOIN task_links l ON l.parent_id = t.id "
        "WHERE l.child_id = ? AND t.status != 'done'",
        (task_id,),
    ).fetchall()
    return [
        {"id": r["id"], "title": r["title"], "status": r["status"]}
        for r in rows
    ]


def _set_status_direct(
    conn: sqlite3.Connection, task_id: str, new_status: str,
) -> bool:
    """Direct status write for drag-drop moves that aren't covered by the
    structured complete/block/unblock/archive verbs (e.g. todo<->ready,
    running<->ready). Appends a ``status`` event row for the live feed.

    When this transitions OFF ``running`` to anything other than the
    terminal verbs above (which own their own run closing), we close the
    active run with outcome='reclaimed' so attempt history isn't
    orphaned. ``running -> ready`` via drag-drop is the common case
    (user yanking a stuck worker back to the queue).
    """
    with kanban_db.write_txn(conn):
        # Snapshot current state so we know whether to close a run.
        prev = conn.execute(
            "SELECT status, current_run_id FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if prev is None:
            return False

        # Guard: don't allow promoting to 'ready' unless all parents are done.
        # Prevents the dispatcher from spawning a child whose upstream work
        # hasn't completed (e.g. T4 dispatched while T3 is still blocked).
        if new_status == "ready":
            parent_statuses = conn.execute(
                "SELECT t.status FROM tasks t "
                "JOIN task_links l ON l.parent_id = t.id "
                "WHERE l.child_id = ?",
                (task_id,),
            ).fetchall()
            if parent_statuses and not all(
                p["status"] == "done" for p in parent_statuses
            ):
                return False

        was_running = prev["status"] == "running"
        reopening_satisfied_parent = (
            prev["status"] in {"done", "archived"}
            and new_status not in {"done", "archived"}
        )

        cur = conn.execute(
            "UPDATE tasks SET status = ?, "
            "  claim_lock = CASE WHEN ? = 'running' THEN claim_lock ELSE NULL END, "
            "  claim_expires = CASE WHEN ? = 'running' THEN claim_expires ELSE NULL END, "
            "  worker_pid = CASE WHEN ? = 'running' THEN worker_pid ELSE NULL END "
            "WHERE id = ?",
            (new_status, new_status, new_status, new_status, task_id),
        )
        if cur.rowcount != 1:
            return False
        run_id = None
        if was_running and new_status != "running" and prev["current_run_id"]:
            run_id = kanban_db._end_run(
                conn, task_id,
                outcome="reclaimed", status="reclaimed",
                summary=f"status changed to {new_status} (dashboard/direct)",
            )
        conn.execute(
            "INSERT INTO task_events (task_id, run_id, kind, payload, created_at) "
            "VALUES (?, ?, 'status', ?, ?)",
            (task_id, run_id, json.dumps({"status": new_status}), int(time.time())),
        )
        if reopening_satisfied_parent:
            # A parent leaving done/archived invalidates any direct child that
            # was sitting in ready solely because that parent used to satisfy
            # the dependency gate. Demote those children immediately so the
            # dashboard does not keep advertising stale-ready work.
            for row in conn.execute(
                "SELECT child_id FROM task_links WHERE parent_id = ? ORDER BY child_id",
                (task_id,),
            ).fetchall():
                child_id = row["child_id"]
                demoted = conn.execute(
                    "UPDATE tasks SET status = 'todo' "
                    "WHERE id = ? AND status = 'ready'",
                    (child_id,),
                )
                if demoted.rowcount == 1:
                    conn.execute(
                        "INSERT INTO task_events (task_id, kind, payload, created_at) "
                        "VALUES (?, 'status', ?, ?)",
                        (
                            child_id,
                            json.dumps(
                                {
                                    "status": "todo",
                                    "reason": "parent_reopened",
                                    "parent": task_id,
                                }
                            ),
                            int(time.time()),
                        ),
                    )
    # If we re-opened something, children may have gone stale.
    if new_status in {"done", "ready"}:
        kanban_db.recompute_ready(conn)
    return True


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

class CommentBody(BaseModel):
    body: str
    author: Optional[str] = "dashboard"


@router.post("/tasks/{task_id}/comments")
def add_comment(task_id: str, payload: CommentBody, board: Optional[str] = Query(None)):
    if not payload.body.strip():
        raise HTTPException(status_code=400, detail="body is required")
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        if kanban_db.get_task(conn, task_id) is None:
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")
        kanban_db.add_comment(
            conn, task_id, author=payload.author or "dashboard", body=payload.body,
        )
        return {"ok": True}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

class LinkBody(BaseModel):
    parent_id: str
    child_id: str


@router.post("/links")
def add_link(payload: LinkBody, board: Optional[str] = Query(None)):
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        kanban_db.link_tasks(conn, payload.parent_id, payload.child_id)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


@router.delete("/links")
def delete_link(
    parent_id: str = Query(...),
    child_id: str = Query(...),
    board: Optional[str] = Query(None),
):
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        ok = kanban_db.unlink_tasks(conn, parent_id, child_id)
        return {"ok": bool(ok)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Bulk actions (multi-select on the board)
# ---------------------------------------------------------------------------

class BulkTaskBody(BaseModel):
    ids: list[str]
    status: Optional[str] = None
    assignee: Optional[str] = None  # "" or None = unassign
    priority: Optional[int] = None
    archive: bool = False
    result: Optional[str] = None
    summary: Optional[str] = None
    metadata: Optional[dict] = None
    reclaim_first: bool = False


@router.post("/tasks/bulk")
def bulk_update(payload: BulkTaskBody, board: Optional[str] = Query(None)):
    """Apply the same patch to every id in ``payload.ids``.

    This is an *independent* iteration — per-task failures don't abort
    siblings. Returns per-id outcome so the UI can surface partials.
    """
    ids = [i for i in (payload.ids or []) if i]
    if not ids:
        raise HTTPException(status_code=400, detail="ids is required")
    results: list[dict] = []
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        for tid in ids:
            entry: dict[str, Any] = {"id": tid, "ok": True}
            try:
                task = kanban_db.get_task(conn, tid)
                if task is None:
                    entry.update(ok=False, error="not found")
                    results.append(entry)
                    continue
                if payload.archive:
                    if not kanban_db.archive_task(conn, tid):
                        entry.update(ok=False, error="archive refused")
                if payload.status is not None and not payload.archive:
                    s = payload.status
                    if s == "done":
                        ok = kanban_db.complete_task(
                            conn, tid,
                            result=payload.result,
                            summary=payload.summary,
                            metadata=payload.metadata,
                        )
                    elif s == "blocked":
                        ok = kanban_db.block_task(conn, tid)
                    elif s == "ready":
                        cur = kanban_db.get_task(conn, tid)
                        if cur and cur.status in ("blocked", "scheduled"):
                            ok = kanban_db.unblock_task(conn, tid)
                        else:
                            ok = _set_status_direct(conn, tid, "ready")
                    elif s == "running":
                        entry.update(
                            ok=False,
                            error=(
                                "Cannot set status to 'running' directly; "
                                "use the dispatcher/claim path"
                            ),
                        )
                        results.append(entry)
                        continue
                    elif s == "scheduled":
                        ok = kanban_db.schedule_task(conn, tid)
                    elif s in {"todo", "triage"}:
                        ok = _set_status_direct(conn, tid, s)
                    else:
                        entry.update(ok=False, error=f"unknown status {s!r}")
                        results.append(entry)
                        continue
                    if not ok:
                        entry.update(ok=False, error=f"transition to {s!r} refused")
                if payload.assignee is not None:
                    try:
                        if payload.reclaim_first:
                            ok = kanban_db.reassign_task(
                                conn, tid, payload.assignee or None,
                                reclaim_first=True,
                            )
                        else:
                            ok = kanban_db.assign_task(
                                conn, tid, payload.assignee or None,
                            )
                        if not ok:
                            entry.update(ok=False, error="assign refused")
                    except RuntimeError as e:
                        entry.update(ok=False, error=str(e))
                if payload.priority is not None:
                    with kanban_db.write_txn(conn):
                        conn.execute(
                            "UPDATE tasks SET priority = ? WHERE id = ?",
                            (int(payload.priority), tid),
                        )
                        conn.execute(
                            "INSERT INTO task_events (task_id, kind, payload, created_at) "
                            "VALUES (?, 'reprioritized', ?, ?)",
                            (tid, json.dumps({"priority": int(payload.priority)}),
                             int(time.time())),
                        )
            except Exception as e:  # defensive — one bad id shouldn't kill the batch
                entry.update(ok=False, error=str(e))
            results.append(entry)
        return {"results": results}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Diagnostics — fleet-wide distress signals (hallucinations, crashes,
# spawn failures, stuck-blocked). See hermes_cli.kanban_diagnostics for
# the rule engine.
# ---------------------------------------------------------------------------

@router.get("/diagnostics")
def list_diagnostics(
    board: Optional[str] = Query(None, description="Kanban board slug (omit for current)"),
    severity: Optional[str] = Query(
        None,
        description="Filter by severity: warning|error|critical",
    ),
):
    """Return ``[{task_id, task_title, task_status, task_assignee,
    diagnostics: [...]}, ...]`` for every task on the board with at
    least one active diagnostic.

    Severity-filterable so the UI can render "just the critical ones"
    or the CLI can grep. Useful for the board-header attention strip
    AND for ``hermes kanban diagnostics`` which shells to this
    endpoint when the dashboard's running, or invokes the engine
    directly when it isn't.
    """
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        diags_by_task = _compute_task_diagnostics(conn, task_ids=None)
        if not diags_by_task:
            return {"diagnostics": [], "count": 0}

        # Narrow by severity if asked.
        if severity:
            filtered: dict[str, list[dict]] = {}
            for tid, dl in diags_by_task.items():
                keep = [d for d in dl if kd.severity_at_or_above(d.get("severity"), severity)]
                if keep:
                    filtered[tid] = keep
            diags_by_task = filtered
            if not diags_by_task:
                return {"diagnostics": [], "count": 0}

        # Pull the task rows we need in one query so we can include
        # titles/statuses without a per-task lookup.
        ids = list(diags_by_task.keys())
        placeholders = ",".join(["?"] * len(ids))
        rows = {
            r["id"]: r
            for r in conn.execute(
                f"SELECT id, title, status, assignee FROM tasks WHERE id IN ({placeholders})",
                tuple(ids),
            ).fetchall()
        }

        out = []
        for tid, dl in diags_by_task.items():
            r = rows.get(tid)
            out.append({
                "task_id": tid,
                "task_title": r["title"] if r else None,
                "task_status": r["status"] if r else None,
                "task_assignee": r["assignee"] if r else None,
                "diagnostics": dl,
            })
        # Sort: highest severity first, then most recent.
        from hermes_cli.kanban_diagnostics import SEVERITY_ORDER
        sev_idx = {s: i for i, s in enumerate(SEVERITY_ORDER)}
        def _sort_key(row):
            top = row["diagnostics"][0]
            return (
                -sev_idx.get(top.get("severity"), -1),
                -(top.get("last_seen_at") or 0),
            )
        out.sort(key=_sort_key)

        return {
            "diagnostics": out,
            "count": sum(len(d["diagnostics"]) for d in out),
        }
    finally:
        conn.close()



# ---------------------------------------------------------------------------
# Worker visibility — cross-task active-worker list and per-run inspection
# ---------------------------------------------------------------------------

try:
    import psutil as _psutil
except ImportError:
    _psutil = None  # type: ignore[assignment]


@router.get("/workers/active")
def list_active_workers(
    board: Optional[str] = Query(None, description="Kanban board slug (omit for current)"),
):
    """Return every currently-running worker on the board.

    A worker is a ``task_runs`` row whose ``ended_at`` is NULL and whose
    ``worker_pid`` is non-NULL, belonging to a task with ``status='running'``.

    Returns ``{workers: [...], count: N, checked_at: <epoch>}``.  Each
    worker entry carries enough context for the dashboard to link back to
    its task without a second round-trip.
    """
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        rows = conn.execute(
            """
            SELECT
                r.id          AS run_id,
                r.task_id,
                t.title       AS task_title,
                t.status      AS task_status,
                t.assignee    AS task_assignee,
                r.profile,
                r.worker_pid,
                r.started_at,
                r.claim_lock,
                r.claim_expires,
                r.last_heartbeat_at,
                r.max_runtime_seconds
            FROM task_runs r
            JOIN tasks t ON t.id = r.task_id
            WHERE r.ended_at IS NULL
              AND r.worker_pid IS NOT NULL
              AND t.status = 'running'
            ORDER BY r.started_at ASC
            """,
        ).fetchall()
        workers = [
            {
                "run_id": row["run_id"],
                "task_id": row["task_id"],
                "task_title": row["task_title"],
                "task_status": row["task_status"],
                "task_assignee": row["task_assignee"],
                "profile": row["profile"],
                "worker_pid": row["worker_pid"],
                "started_at": row["started_at"],
                "claim_lock": row["claim_lock"],
                "claim_expires": row["claim_expires"],
                "last_heartbeat_at": row["last_heartbeat_at"],
                "max_runtime_seconds": row["max_runtime_seconds"],
            }
            for row in rows
        ]
        return {"workers": workers, "count": len(workers), "checked_at": int(time.time())}
    finally:
        conn.close()


@router.get("/runs/{run_id}")
def get_run_endpoint(
    run_id: int,
    board: Optional[str] = Query(None, description="Kanban board slug (omit for current)"),
):
    """Direct lookup of a ``task_runs`` row by its integer id.

    Returns ``{run: {...}}`` using the same serialisation as the
    per-task run history embedded in ``GET /tasks/{task_id}``.
    404 when no such run exists.
    """
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        r = kanban_db.get_run(conn, run_id)
        if r is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        return {"run": _run_dict(r)}
    finally:
        conn.close()


@router.get("/runs/{run_id}/inspect")
def inspect_run_endpoint(
    run_id: int,
    board: Optional[str] = Query(None, description="Kanban board slug (omit for current)"),
):
    """Live PID stats for a run's worker process via psutil.

    If the run has already ended, or has no recorded ``worker_pid``,
    returns ``{alive: false}`` with a human-readable ``reason``.

    When the process is live, returns CPU, memory, thread count, fd count,
    status, create_time, and cmdline.  ``access_denied`` is set when the
    OS refuses inspection rather than raising a 500.

    psutil availability: if psutil is not installed the endpoint still
    works but ``alive`` is always returned as ``false`` with
    ``reason="psutil not available"``.
    """
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        r = kanban_db.get_run(conn, run_id)
        if r is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    finally:
        conn.close()

    if r.ended_at is not None:
        return {"run_id": run_id, "alive": False, "reason": "run already ended"}
    if r.worker_pid is None:
        return {"run_id": run_id, "alive": False, "reason": "no worker_pid recorded"}

    pid = r.worker_pid

    if _psutil is None:
        return {"run_id": run_id, "alive": False, "pid": pid, "reason": "psutil not available"}

    try:
        proc = _psutil.Process(pid)
        info = proc.as_dict(attrs=[
            "cpu_percent", "memory_info", "num_threads",
            "status", "create_time", "cmdline",
        ])
        # num_fds is POSIX-only; skip gracefully on Windows.
        try:
            num_fds = proc.num_fds()
        except AttributeError:
            num_fds = None
        mem = info.get("memory_info")
        return {
            "run_id": run_id,
            "alive": True,
            "pid": pid,
            "cpu_percent": info.get("cpu_percent"),
            "memory_rss_bytes": mem.rss if mem else None,
            "memory_vms_bytes": mem.vms if mem else None,
            "num_threads": info.get("num_threads"),
            "num_fds": num_fds,
            "status": info.get("status"),
            "create_time": info.get("create_time"),
            "cmdline": info.get("cmdline"),
        }
    except _psutil.NoSuchProcess:
        return {"run_id": run_id, "alive": False, "pid": pid, "reason": "process not found"}
    except _psutil.AccessDenied:
        return {"run_id": run_id, "alive": True, "pid": pid, "error": "access denied"}


class TerminateRunBody(BaseModel):
    reason: Optional[str] = None


@router.post("/runs/{run_id}/terminate")
def terminate_run_endpoint(
    run_id: int,
    payload: TerminateRunBody,
    board: Optional[str] = Query(None, description="Kanban board slug (omit for current)"),
):
    """Terminate the worker process backing an in-flight run.

    Resolves ``run_id`` to its parent ``task_id`` and routes through
    :func:`kanban_db.reclaim_task` so the SIGTERM->SIGKILL flow,
    run-outcome bookkeeping, and event-log append all match what the
    existing ``POST /tasks/{task_id}/reclaim`` endpoint does.

    Responses:
      * 200 ``{"ok": true, "run_id": ..., "task_id": ...}`` on success.
      * 404 when ``run_id`` is unknown.
      * 409 when the run has already ended, or the task is no longer in
        a claimable state.

    Closes the gap left by PR #28432, which shipped the read-only
    sibling endpoints (``/workers/active``, ``/runs/{run_id}``,
    ``/runs/{run_id}/inspect``) but no termination control surface.
    """
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        r = kanban_db.get_run(conn, run_id)
        if r is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        if r.ended_at is not None:
            raise HTTPException(
                status_code=409,
                detail=f"run {run_id} already ended",
            )
        ok = kanban_db.reclaim_task(conn, r.task_id, reason=payload.reason)
        if not ok:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"cannot terminate run {run_id}: task {r.task_id} is no "
                    "longer in a reclaimable state"
                ),
            )
        return {"ok": True, "run_id": run_id, "task_id": r.task_id}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Recovery actions — reclaim a running claim, reassign to a new profile
# ---------------------------------------------------------------------------

class ReclaimBody(BaseModel):
    reason: Optional[str] = None


@router.post("/tasks/{task_id}/reclaim")
def reclaim_task_endpoint(
    task_id: str,
    payload: ReclaimBody,
    board: Optional[str] = Query(None),
):
    """Release an active worker claim on a running task.

    Used by the dashboard recovery popover when an operator wants to
    abort a stuck worker (e.g. one that keeps hallucinating card ids)
    without waiting for the claim TTL. Maps 1:1 to
    ``hermes kanban reclaim <task_id> --reason ...``.
    """
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        ok = kanban_db.reclaim_task(conn, task_id, reason=payload.reason)
        if not ok:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"cannot reclaim {task_id}: not in a claimable state "
                    "(not running, or unknown id)"
                ),
            )
        return {"ok": True, "task_id": task_id}
    finally:
        conn.close()


class SpecifyBody(BaseModel):
    """Optional author override. Nothing else is configurable from the
    dashboard — model + prompt come from ``auxiliary.triage_specifier``
    in config.yaml, same as the CLI."""

    author: Optional[str] = None


@router.post("/tasks/{task_id}/specify")
def specify_task_endpoint(
    task_id: str,
    payload: SpecifyBody,
    board: Optional[str] = Query(None),
):
    """Flesh out a triage-column task via the auxiliary LLM and promote
    it to ``todo``. Maps 1:1 to ``hermes kanban specify <task_id>``.

    Returns the outcome shape used by the CLI: ``{ok, task_id, reason,
    new_title}``. A non-OK outcome is NOT an HTTP error — the UI renders
    the reason inline (e.g. "no auxiliary client configured") so the
    operator knows what to fix, and retries without a page reload.

    This endpoint runs in FastAPI's threadpool (sync ``def``) because
    the underlying LLM call can take tens of seconds to minutes on
    reasoning models, which would block the event loop if we used
    ``async def`` without an explicit ``run_in_executor``.
    """
    board = _resolve_board(board)
    # Pin the board for the duration of this call so the specifier module
    # (which calls ``kb.connect()`` with no args) hits the right DB. Use a
    # context-local override rather than mutating the process-global
    # HERMES_KANBAN_BOARD env var — this endpoint runs in FastAPI's
    # threadpool, so two concurrent requests for different boards would
    # otherwise race on the shared env var and cross-write (issue #38323).
    with kanban_db.scoped_current_board(board or kanban_db.DEFAULT_BOARD):
        # Import lazily so a missing auxiliary client at import time
        # doesn't break plugin load.
        from hermes_cli import kanban_specify  # noqa: WPS433 (intentional)

        outcome = kanban_specify.specify_task(
            task_id,
            author=(payload.author or None),
        )

    return {
        "ok": bool(outcome.ok),
        "task_id": outcome.task_id,
        "reason": outcome.reason,
        "new_title": outcome.new_title,
    }


class ReassignBody(BaseModel):
    profile: Optional[str] = None  # "" or None = unassign
    reclaim_first: bool = False
    reason: Optional[str] = None


@router.post("/tasks/{task_id}/reassign")
def reassign_task_endpoint(
    task_id: str,
    payload: ReassignBody,
    board: Optional[str] = Query(None),
):
    """Reassign a task to a different profile, optionally reclaiming first.

    Used by the dashboard recovery popover when an operator wants to
    retry a task with a different worker profile (e.g. switch to a
    smarter model after the assigned profile keeps hallucinating).
    Maps 1:1 to ``hermes kanban reassign <task_id> <profile> [--reclaim]``.
    """
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        ok = kanban_db.reassign_task(
            conn, task_id,
            payload.profile or None,
            reclaim_first=bool(payload.reclaim_first),
            reason=payload.reason,
        )
        if not ok:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"cannot reassign {task_id}: unknown id, or still "
                    "running (pass reclaim_first=true to release the claim first)"
                ),
            )
        return {"ok": True, "task_id": task_id, "assignee": payload.profile or None}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Plugin config (read dashboard.kanban.* defaults from config.yaml)
# ---------------------------------------------------------------------------

@router.get("/config")
def get_config():
    """Return kanban dashboard preferences from ~/.hermes/config.yaml.

    Reads the ``dashboard.kanban`` section if present; defaults otherwise.
    Used by the UI to pre-select tenant filters, toggle markdown rendering,
    or set column-width preferences without a round-trip per page load.
    """
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
    except Exception:
        cfg = {}
    dash_cfg = (cfg.get("dashboard") or {})
    # dashboard.kanban may itself be a dict; fall back to {}.
    k_cfg = dash_cfg.get("kanban") or {}
    return {
        "default_tenant": k_cfg.get("default_tenant") or "",
        "lane_by_profile": bool(k_cfg.get("lane_by_profile", True)),
        "include_archived_by_default": bool(k_cfg.get("include_archived_by_default", False)),
        "render_markdown": bool(k_cfg.get("render_markdown", True)),
    }


# ---------------------------------------------------------------------------
# Home-channel subscriptions (per-task, per-platform toggles)
# ---------------------------------------------------------------------------
#
# Home channels are a first-class gateway concept — each configured platform
# can have exactly one (chat_id, thread_id, name) it considers "home". The
# dashboard surfaces these as per-task toggles so a user can opt a specific
# task into receiving terminal notifications (completed / blocked / gave_up)
# at their telegram/discord/slack home, without touching the CLI.
#
# The wire format mirrors kanban_db.add_notify_sub — (task_id, platform,
# chat_id, thread_id) — so toggle-on creates exactly the same row the
# `/kanban create` slash command would, and the existing gateway notifier
# watcher delivers events without any additional plumbing.


def _configured_home_channels() -> list[dict]:
    """Return every platform that has a home_channel set, fully hydrated.

    Reads the live GatewayConfig so env-var overlays (``TELEGRAM_HOME_CHANNEL``
    etc.) are honored alongside config.yaml. Returns platforms in a stable
    order and drops platforms without a home.
    """
    try:
        from gateway.config import load_gateway_config
    except Exception:
        return []
    try:
        gw_cfg = load_gateway_config()
    except Exception:
        return []
    result: list[dict] = []
    for platform, pcfg in gw_cfg.platforms.items():
        if not pcfg or not pcfg.home_channel:
            continue
        hc = pcfg.home_channel
        result.append({
            "platform": platform.value,
            "chat_id": hc.chat_id,
            "thread_id": hc.thread_id or "",
            "name": hc.name or "Home",
        })
    # Stable order for deterministic UI — platform name alphabetical.
    result.sort(key=lambda r: r["platform"])
    return result


def _active_profile_name() -> str:
    """Return the current Hermes profile name for notify-sub ownership."""
    try:
        from hermes_cli.profiles import get_active_profile_name
        return get_active_profile_name() or "default"
    except Exception:
        return "default"


def _home_sub_matches(sub: dict, home: dict) -> bool:
    """True if a notify_subs row corresponds to the given home channel."""
    return (
        sub.get("platform") == home["platform"]
        and str(sub.get("chat_id", "")) == str(home["chat_id"])
        and str(sub.get("thread_id") or "") == str(home["thread_id"] or "")
    )


@router.get("/home-channels")
def get_home_channels(
    task_id: Optional[str] = Query(None),
    board: Optional[str] = Query(None),
):
    """List every platform with a home channel, plus whether *task_id*
    (if given) is currently subscribed to that home.

    When ``task_id`` is omitted, every entry's ``subscribed`` is ``false``
    — useful for the "no task selected" state of the UI.
    """
    homes = _configured_home_channels()
    subscribed_homes: set[tuple[str, str, str]] = set()
    if task_id:
        board = _resolve_board(board)
        conn = _conn(board=board)
        try:
            subs = kanban_db.list_notify_subs(conn, task_id)
        finally:
            conn.close()
        for sub in subs:
            key = (
                str(sub.get("platform") or ""),
                str(sub.get("chat_id") or ""),
                str(sub.get("thread_id") or ""),
            )
            subscribed_homes.add(key)
    result = []
    for home in homes:
        key = (home["platform"], home["chat_id"], home["thread_id"])
        result.append({**home, "subscribed": key in subscribed_homes})
    return {"home_channels": result}


@router.post("/tasks/{task_id}/home-subscribe/{platform}")
def subscribe_home(task_id: str, platform: str, board: Optional[str] = Query(None)):
    """Subscribe *task_id* to notifications routed to *platform*'s home channel.

    Idempotent — re-subscribing is a no-op at the DB layer. 404 if the
    platform has no home channel configured. 404 if the task doesn't exist.
    """
    homes = _configured_home_channels()
    home = next((h for h in homes if h["platform"] == platform), None)
    if not home:
        raise HTTPException(
            status_code=404,
            detail=f"No home channel configured for platform {platform!r}. "
                   f"Set one from the messenger via /sethome, or configure "
                   f"gateway.platforms.{platform}.home_channel in config.yaml.",
        )
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        task = kanban_db.get_task(conn, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")
        kanban_db.add_notify_sub(
            conn,
            task_id=task_id,
            platform=platform,
            chat_id=home["chat_id"],
            thread_id=home["thread_id"] or None,
            notifier_profile=_active_profile_name(),
        )
        return {"ok": True, "task_id": task_id, "home_channel": home}
    finally:
        conn.close()


@router.delete("/tasks/{task_id}/home-subscribe/{platform}")
def unsubscribe_home(task_id: str, platform: str, board: Optional[str] = Query(None)):
    """Remove any notify subscription on *task_id* that matches *platform*'s home."""
    homes = _configured_home_channels()
    home = next((h for h in homes if h["platform"] == platform), None)
    if not home:
        raise HTTPException(
            status_code=404,
            detail=f"No home channel configured for platform {platform!r}.",
        )
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        kanban_db.remove_notify_sub(
            conn,
            task_id=task_id,
            platform=platform,
            chat_id=home["chat_id"],
            thread_id=home["thread_id"] or None,
        )
        return {"ok": True, "task_id": task_id, "home_channel": home}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Stats (per-profile / per-status counts + oldest-ready age)
# ---------------------------------------------------------------------------

@router.get("/stats")
def get_stats(board: Optional[str] = Query(None)):
    """Per-status + per-assignee counts + oldest-ready age.

    Designed for the dashboard HUD and for router profiles that need to
    answer "is this specialist overloaded?" without scanning the whole
    board themselves.
    """
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        return kanban_db.board_stats(conn)
    finally:
        conn.close()


@router.get("/assignees")
def get_assignees(board: Optional[str] = Query(None)):
    """Known profiles + per-profile task counts.

    Returns the union of ``~/.hermes/profiles/*`` on disk and every
    distinct assignee currently used on the board. The dashboard uses
    this to populate its assignee dropdown so a freshly-created profile
    appears in the picker before it's been given any task.
    """
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        return {"assignees": kanban_db.known_assignees(conn)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Worker log (read-only; file written by _default_spawn)
# ---------------------------------------------------------------------------

@router.get("/tasks/{task_id}/log")
def get_task_log(
    task_id: str,
    tail: Optional[int] = Query(None, ge=1, le=2_000_000),
    board: Optional[str] = Query(None),
):
    """Return the worker's stdout/stderr log.

    ``tail`` caps the response size (bytes) so the dashboard drawer
    doesn't paginate megabytes into the browser. Returns 404 if the task
    has never spawned. The on-disk log is rotated at 2 MiB per
    ``_rotate_worker_log`` — a single ``.log.1`` is kept, no further
    generations, so disk usage per task is bounded at ~4 MiB.
    """
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        task = kanban_db.get_task(conn, task_id)
    finally:
        conn.close()
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    content = kanban_db.read_worker_log(task_id, tail_bytes=tail, board=board)
    log_path = kanban_db.worker_log_path(task_id, board=board)
    size = log_path.stat().st_size if log_path.exists() else 0
    return {
        "task_id": task_id,
        "path": str(log_path),
        "exists": content is not None,
        "size_bytes": size,
        "content": content or "",
        # Truncated when the on-disk file was larger than the tail cap.
        "truncated": bool(tail and size > tail),
    }


# ---------------------------------------------------------------------------
# Dispatch nudge (optional quick-path so the UI doesn't wait 60 s)
# ---------------------------------------------------------------------------

@router.post("/dispatch")
def dispatch(
    dry_run: bool = Query(False),
    max_n: int = Query(8, alias="max"),
    board: Optional[str] = Query(None),
):
    board = _resolve_board(board)
    conn = _conn(board=board)
    try:
        result = kanban_db.dispatch_once(
            conn, dry_run=dry_run, max_spawn=max_n, board=board,
        )
        # DispatchResult is a dataclass.
        try:
            return asdict(result)
        except TypeError:
            return {"result": str(result)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Boards CRUD (multi-project support)
# ---------------------------------------------------------------------------

class CreateBoardBody(BaseModel):
    slug: str
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    switch: bool = False


class RenameBoardBody(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None


class InboxItemBody(BaseModel):
    item_type: str = "decision"
    title: str
    prompt: str
    detail: Optional[str] = None
    choices: list[Any] = Field(default_factory=list)
    form_schema: dict[str, Any] = Field(default_factory=dict)
    source_type: Optional[str] = None
    source_ref: Optional[str] = None
    source_board: Optional[str] = None
    source_task_id: Optional[str] = None
    source_snapshot: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    assignee: Optional[str] = None
    recipient: str = "greg"
    due_at: Optional[int] = None


class InboxResponseBody(BaseModel):
    action: str
    response: dict[str, Any] = Field(default_factory=dict)
    note: Optional[str] = None
    actor: str = "dashboard"


def _board_counts(slug: str) -> dict[str, int]:
    """Return ``{status: count}`` for a board. Safe on an empty DB."""
    try:
        path = kanban_db.kanban_db_path(board=slug)
        if not path.exists():
            return {}
        conn = kanban_db.connect(board=slug)
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"
            ).fetchall()
            return {r["status"]: int(r["n"]) for r in rows}
        finally:
            conn.close()
    except Exception:
        return {}


@router.get("/boards")
def list_boards(include_archived: bool = Query(False)):
    """Return every board on disk with task counts and the active slug."""
    boards = kanban_db.list_boards(include_archived=include_archived)
    current = kanban_db.get_current_board()
    for b in boards:
        b["is_current"] = (b["slug"] == current)
        b["counts"] = _board_counts(b["slug"])
        b["total"] = sum(b["counts"].values())
    return {"boards": boards, "current": current}


@router.get("/boards/all")
def get_all_boards(
    tenant: Optional[str] = Query(None),
    assignee: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    parent_only: bool = Query(False),
    repo: Optional[str] = Query(None),
    prd_only: bool = Query(False),
    compact: bool = Query(False),
    limit_per_column: int = Query(
        _DASHBOARD_CARD_LIMIT_DEFAULT,
        ge=1,
        le=_DASHBOARD_CARD_LIMIT_MAX,
    ),
    sort: str = Query("recent", pattern="^(priority|recent)$"),
):
    """Return a read-only aggregate of every active board.

    Task ids are only unique inside a board, so every aggregate card carries
    ``source_board`` and ``source_board_name``. The dashboard uses those fields
    when opening the task drawer against the original board.
    """

    boards = kanban_db.list_boards(include_archived=False)
    columns: dict[str, list[dict[str, Any]]] = {
        column: [] for column in BOARD_COLUMNS
    }
    tenants: set[str] = set()
    assignees: set[str] = set()
    repositories: set[str] = set()
    board_summaries: list[dict[str, Any]] = []
    column_totals: dict[str, int] = {column: 0 for column in BOARD_COLUMNS}

    for meta in boards:
        slug = str(meta["slug"])
        if compact:
            board_query = q
            normalized_query = str(q or "").strip().lower()
            if normalized_query and normalized_query in (
                f"{slug} {meta.get('name') or ''}".lower()
            ):
                board_query = None
            payload = _get_compact_board(
                board=slug,
                tenant=tenant,
                assignee=assignee,
                include_archived=False,
                query=board_query,
                parent_only=parent_only,
                repo=repo,
                prd_only=prd_only,
                limit_per_column=limit_per_column,
                sort=sort,
                include_diagnostics=False,
            )
        else:
            payload = get_board(
                tenant=tenant,
                assignee=None,
                include_archived=False,
                board=slug,
                workflow_template_id=None,
                current_step_key=None,
                compact=False,
            )
        counts: dict[str, int] = {}
        for column in payload["columns"]:
            name = column["name"]
            tasks = column["tasks"]
            total = int(column.get("total", len(tasks)))
            counts[name] = total
            column_totals[name] = column_totals.get(name, 0) + total
            for task in tasks:
                task["source_board"] = slug
                task["source_board_name"] = meta.get("name") or slug
                columns.setdefault(name, []).append(task)
        tenants.update(payload.get("tenants") or [])
        assignees.update(payload.get("assignees") or [])
        repositories.update(payload.get("repositories") or [])
        summary = dict(meta)
        summary["counts"] = counts
        summary["total"] = sum(counts.values())
        board_summaries.append(summary)

    for tasks in columns.values():
        if sort == "recent":
            tasks.sort(
                key=lambda task: (
                    -int(task.get("activity_at") or task.get("created_at") or 0),
                    -int(task.get("priority") or 0),
                    str(task.get("source_board") or ""),
                    str(task.get("id") or ""),
                )
            )
        else:
            tasks.sort(
                key=lambda task: (
                    -int(task.get("priority") or 0),
                    int(task.get("created_at") or 0),
                    str(task.get("source_board") or ""),
                    str(task.get("id") or ""),
                )
            )
        if compact:
            del tasks[limit_per_column:]

    return {
        "aggregate": True,
        "compact": compact,
        "columns": [
            {
                "name": name,
                "tasks": tasks,
                "total": int(column_totals.get(name, len(tasks))),
                "limited": int(column_totals.get(name, 0)) > len(tasks),
            }
            for name, tasks in columns.items()
        ],
        "tenants": sorted(tenants),
        "assignees": sorted(assignees),
        "repositories": sorted(repositories, key=str.lower),
        "boards": board_summaries,
        "latest_event_id": 0,
        "limit_per_column": int(limit_per_column) if compact else None,
        "now": int(time.time()),
    }


def _tag_sprint_task(
    item: dict[str, Any],
    *,
    board_slug: str,
    board_name: str,
) -> dict[str, Any]:
    tagged = dict(item)
    tagged["source_board"] = board_slug
    tagged["source_board_name"] = board_name
    return tagged


@router.get("/boards/all/sprint")
def get_all_boards_sprint(
    days: int = Query(7, ge=1, le=90),
):
    """Merge each active board's live Sprint Manager projection on demand."""
    now = int(time.time())
    start = now - days * 86400
    status_counts: dict[str, int] = {}
    scorecard = {
        "created": 0,
        "completed": 0,
        "flow_delta": 0,
        "open": 0,
        "active": 0,
        "blocked": 0,
        "ready": 0,
        "running": 0,
        "review": 0,
    }
    rocks: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    workstreams: list[dict[str, Any]] = []
    reference_index: dict[str, dict[str, Any]] = {}
    board_summaries: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for meta in kanban_db.list_boards(include_archived=False):
        slug = str(meta["slug"])
        name = str(meta.get("name") or slug)
        try:
            snapshot = get_sprint_snapshot(days=days, board=slug)
        except Exception as exc:
            log.warning("all-boards sprint skipped %s: %s", slug, exc)
            errors.append({"board": slug, "error": str(exc)})
            continue
        board_summaries.append(
            {
                "slug": slug,
                "name": name,
                "scorecard": snapshot.get("scorecard") or {},
            }
        )
        for status_name, count in (snapshot.get("status_counts") or {}).items():
            status_counts[status_name] = (
                status_counts.get(status_name, 0) + int(count or 0)
            )
        for metric in scorecard:
            scorecard[metric] += int(
                (snapshot.get("scorecard") or {}).get(metric) or 0
            )
        rocks.extend(
            _tag_sprint_task(item, board_slug=slug, board_name=name)
            for item in (snapshot.get("rocks") or [])
        )
        issues.extend(
            _tag_sprint_task(item, board_slug=slug, board_name=name)
            for item in (snapshot.get("issues") or [])
        )
        for stream in snapshot.get("workstreams") or []:
            tagged_stream = _tag_sprint_task(
                stream, board_slug=slug, board_name=name,
            )
            tagged_stream["tasks"] = [
                _tag_sprint_task(task, board_slug=slug, board_name=name)
                for task in (stream.get("tasks") or [])
            ]
            workstreams.append(tagged_stream)
        for ref in snapshot.get("references") or []:
            path = str(ref.get("path") or "")
            if not path:
                continue
            entry = reference_index.setdefault(
                path,
                {
                    "path": path,
                    "kind": ref.get("kind") or "evidence",
                    "mentions": 0,
                    "task_ids": [],
                    "task_titles": [],
                    "task_source_boards": [],
                    "statuses": set(),
                    "latest_at": 0,
                },
            )
            task_ids = list(ref.get("task_ids") or [])
            task_titles = list(ref.get("task_titles") or [])
            entry["mentions"] += int(ref.get("mentions") or len(task_ids))
            entry["task_ids"].extend(task_ids)
            entry["task_titles"].extend(task_titles)
            entry["task_source_boards"].extend([slug] * len(task_ids))
            entry["statuses"].add(str(ref.get("status") or "open"))
            entry["latest_at"] = max(
                int(entry["latest_at"]),
                int(ref.get("latest_at") or 0),
            )

    def sprint_sort_key(item: dict[str, Any]) -> tuple[int, int, str, str]:
        return (
            -int(item.get("priority") or 0),
            -int(item.get("created_at") or 0),
            str(item.get("source_board") or ""),
            str(item.get("id") or ""),
        )

    rocks.sort(key=sprint_sort_key)
    issues.sort(key=sprint_sort_key)
    workstreams.sort(key=sprint_sort_key)
    references: list[dict[str, Any]] = []
    for entry in reference_index.values():
        statuses = set(entry.pop("statuses"))
        entry["status"] = _reference_status(statuses)
        entry["task_ids"] = entry["task_ids"][:8]
        entry["task_titles"] = entry["task_titles"][:8]
        entry["task_source_boards"] = entry["task_source_boards"][:8]
        references.append(entry)
    references.sort(
        key=lambda item: (
            0 if item["kind"] == "plan" else 1,
            -int(item["latest_at"]),
            -int(item["mentions"]),
            item["path"],
        )
    )
    return {
        "aggregate": True,
        "generated_at": now,
        "window": {"days": days, "start": start, "end": now},
        "status_counts": status_counts,
        "scorecard": scorecard,
        "rocks": rocks[:24],
        "issues": issues[:24],
        "workstreams": workstreams[:64],
        "references": references[:120],
        "boards": board_summaries,
        "errors": errors,
    }


@router.post("/boards")
def create_board_endpoint(payload: CreateBoardBody):
    """Create a new board. Idempotent — ``slug`` collision returns existing."""
    try:
        meta = kanban_db.create_board(
            payload.slug,
            name=payload.name,
            description=payload.description,
            icon=payload.icon,
            color=payload.color,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if payload.switch:
        try:
            kanban_db.set_current_board(meta["slug"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return {"board": meta, "current": kanban_db.get_current_board()}


@router.patch("/boards/{slug}")
def rename_board(slug: str, payload: RenameBoardBody):
    """Update a board's display metadata (slug is immutable — create a new one to rename the directory)."""
    try:
        normed = kanban_db._normalize_board_slug(slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not normed or not kanban_db.board_exists(normed):
        raise HTTPException(status_code=404, detail=f"board {slug!r} does not exist")
    meta = kanban_db.write_board_metadata(
        normed,
        name=payload.name,
        description=payload.description,
        icon=payload.icon,
        color=payload.color,
    )
    return {"board": meta}


@router.delete("/boards/{slug}")
def delete_board(slug: str, delete: bool = Query(False, description="Hard-delete instead of archive")):
    """Archive (default) or hard-delete a board."""
    try:
        res = kanban_db.remove_board(slug, archive=not delete)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"result": res, "current": kanban_db.get_current_board()}


@router.post("/boards/{slug}/squash")
def squash_board_into_decisions(slug: str):
    """Snapshot and archive a board, promoting unresolved work to Inbox.

    The operation intentionally creates the durable Inbox decisions before
    moving the board directory. If the archive move fails, the event receipt
    is marked failed and no task data has been destroyed.
    """

    try:
        normed = kanban_db._normalize_board_slug(slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not normed:
        raise HTTPException(status_code=400, detail="board slug is required")
    if normed == kanban_db.DEFAULT_BOARD:
        raise HTTPException(
            status_code=400,
            detail="the default board cannot be squashed",
        )
    if not kanban_db.board_exists(normed):
        raise HTTPException(
            status_code=404,
            detail=f"board {normed!r} does not exist",
        )

    board_conn = kanban_db.connect(board=normed)
    try:
        meta = kanban_db.read_board_metadata(normed)
        tasks = kanban_db.list_tasks(board_conn, include_archived=True)
        task_rows = [asdict(task) for task in tasks]
        outstanding = [
            task
            for task in task_rows
            if task.get("status") not in {"done", "archived"}
        ]
        snapshot = {
            "schema_version": "hermes-board-squash.v1",
            "board": meta,
            "tasks": task_rows,
            "captured_at": int(time.time()),
        }
    finally:
        board_conn.close()

    inbox_conn = inbox_store.connect()
    event_id = inbox_store.create_squash_event(
        inbox_conn,
        board_slug=normed,
        board_name=meta.get("name"),
        task_total=len(task_rows),
        outstanding_total=len(outstanding),
        board_snapshot=snapshot,
    )
    decisions = [
        {
            "item_type": "decision",
            "title": task.get("title") or task["id"],
            "prompt": (
                f"What should happen next with this unresolved "
                f"{meta.get('name') or normed} item?"
            ),
            "detail": task.get("body") or task.get("result"),
            "choices": [
                {"value": "go", "label": "Move forward"},
                {"value": "delegate", "label": "Delegate"},
                {"value": "hold", "label": "Keep for later"},
                {"value": "archive", "label": "Archive"},
            ],
            "source_type": "board_squash",
            "source_ref": event_id,
            "source_board": normed,
            "source_task_id": task["id"],
            "source_snapshot": task,
            "priority": int(task.get("priority") or 0),
            "assignee": task.get("assignee"),
            "recipient": "greg",
        }
        for task in outstanding
    ]

    decisions_created = 0
    try:
        result = inbox_store.upsert_items(inbox_conn, decisions)
        decisions_created = int(result["created"])
        archive = kanban_db.remove_board(normed, archive=True)
        archive_path = archive.get("new_path")
        inbox_store.finish_squash_event(
            inbox_conn,
            event_id,
            status="completed",
            decisions_created=decisions_created,
            archive_path=archive_path,
        )
        return {
            "event_id": event_id,
            "board": normed,
            "task_total": len(task_rows),
            "outstanding_total": len(outstanding),
            "decisions_created": decisions_created,
            "archive": archive,
        }
    except Exception as exc:
        log.exception("Failed to squash Kanban board %s", normed)
        inbox_store.finish_squash_event(
            inbox_conn,
            event_id,
            status="failed",
            decisions_created=decisions_created,
            error=str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail=f"failed to squash board {normed!r}",
        )
    finally:
        inbox_conn.close()


@router.post("/boards/{slug}/switch")
def switch_board(slug: str):
    """Persist ``slug`` as the active board for subsequent CLI / slash calls.

    Dashboard users pick boards via a client-side ``localStorage`` — this
    endpoint is for ``/kanban boards switch`` parity so gateway slash
    commands and the CLI share the same current-board pointer.
    """
    try:
        normed = kanban_db._normalize_board_slug(slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not normed or not kanban_db.board_exists(normed):
        raise HTTPException(status_code=404, detail=f"board {slug!r} does not exist")
    kanban_db.set_current_board(normed)
    return {"current": normed}


# ---------------------------------------------------------------------------
# Daily Inbox
# ---------------------------------------------------------------------------

@router.get("/inbox")
def list_inbox_items(
    status: str = Query("pending"),
    item_type: Optional[str] = Query(None),
    recipient: Optional[str] = Query(None),
    limit: int = Query(250, ge=1, le=1000),
):
    conn = inbox_store.connect()
    try:
        try:
            items = inbox_store.list_items(
                conn,
                status=status,
                item_type=item_type,
                recipient=recipient,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        counts = {
            kind: len(
                inbox_store.list_items(
                    conn,
                    status="pending",
                    item_type=kind,
                    recipient=recipient,
                    limit=1000,
                )
            )
            for kind in sorted(inbox_store.ITEM_TYPES)
        }
        return {
            "items": items,
            "counts": counts,
            "pending_total": sum(counts.values()),
        }
    finally:
        conn.close()


@router.post("/inbox")
def create_inbox_item(payload: InboxItemBody):
    conn = inbox_store.connect()
    try:
        item = {
            "item_type": payload.item_type,
            "title": payload.title,
            "prompt": payload.prompt,
            "detail": payload.detail,
            "choices": payload.choices,
            "form_schema": payload.form_schema,
            "source_type": payload.source_type,
            "source_ref": payload.source_ref,
            "source_board": payload.source_board,
            "source_task_id": payload.source_task_id,
            "source_snapshot": payload.source_snapshot,
            "priority": payload.priority,
            "assignee": payload.assignee,
            "recipient": payload.recipient,
            "due_at": payload.due_at,
        }
        try:
            result = inbox_store.upsert_items(conn, [item])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        item_id = (result["created_ids"] or result["refreshed_ids"])[0]
        return {"item": inbox_store.get_item(conn, item_id)}
    finally:
        conn.close()


@router.post("/inbox/{item_id}/respond")
def respond_to_inbox_item(item_id: str, payload: InboxResponseBody):
    conn = inbox_store.connect()
    try:
        try:
            item = inbox_store.respond_item(
                conn,
                item_id,
                action=payload.action,
                response=payload.response,
                note=payload.note,
                actor=payload.actor,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="inbox item not found")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"item": item}
    finally:
        conn.close()


@router.post("/inbox/import/carlos-swipe")
def import_carlos_swipe_report():
    """Import the latest Carlos blocker report without resetting responses."""

    relative_report = (
        Path("artifacts")
        / "carlos-blocker-swipe-cards"
        / "latest"
        / "swipe-cards-data.json"
    )
    report_candidates = [
        inbox_store.inbox_db_path().parents[1] / relative_report,
        Path.home() / ".hermes" / relative_report,
    ]
    report_path = next(
        (candidate for candidate in report_candidates if candidate.is_file()),
        report_candidates[0],
    )
    if not report_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="the latest Carlos swipe-card report was not found",
        )
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"the latest Carlos swipe-card report is invalid: {exc}",
        )
    cards = report.get("cards")
    if not isinstance(cards, list):
        raise HTTPException(
            status_code=422,
            detail="the Carlos swipe-card report has no cards array",
        )
    report_id = str(report.get("report_id") or report_path.parent.name)
    items = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        board = str(card.get("board") or "")
        task_id = str(card.get("task_id") or card.get("card_id") or "")
        if not board or not task_id:
            continue
        items.append(
            {
                "item_type": "swipe",
                "title": str(card.get("title") or task_id),
                "prompt": str(
                    card.get("prompt")
                    or card.get("primary_action")
                    or "What should happen next?"
                ),
                "detail": card.get("reason") or card.get("body_excerpt"),
                "choices": [
                    {"value": "archive", "label": "Archive", "direction": "left"},
                    {
                        "value": "snooze_until_tomorrow",
                        "label": "Tomorrow",
                        "direction": "up",
                    },
                    {"value": "go", "label": "Go", "direction": "right"},
                ],
                "source_type": "carlos_swipe_report",
                "source_ref": "carlos-blocker-swipe-cards",
                "source_board": board,
                "source_task_id": task_id,
                "source_snapshot": card,
                "priority": int(card.get("priority") or 0),
                "assignee": card.get("assignee"),
                "recipient": "greg",
            }
        )
    conn = inbox_store.connect()
    try:
        result = inbox_store.upsert_items(conn, items)
        return {
            "report_id": report_id,
            "report_path": str(report_path),
            "cards_seen": len(cards),
            "items_valid": len(items),
            **result,
        }
    finally:
        conn.close()


@router.get("/inbox/squashes")
def list_board_squashes(limit: int = Query(50, ge=1, le=250)):
    conn = inbox_store.connect()
    try:
        return {"events": inbox_store.list_squash_events(conn, limit=limit)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# WebSocket: /events?since=<event_id>
# ---------------------------------------------------------------------------

# Poll interval for the event tail loop. SQLite WAL + 300 ms polling is
# the simplest and most robust approach; it adds a fraction of a percent
# of CPU and has no shared state to synchronize across workers.
_EVENT_POLL_SECONDS = 0.3


# ---------------------------------------------------------------------------
# Profile metadata & description editing (consumed by the kanban orchestrator)
# ---------------------------------------------------------------------------

class DescribeBody(BaseModel):
    description: Optional[str] = None  # explicit user-authored text


class DescribeAutoBody(BaseModel):
    overwrite: bool = False


@router.get("/profiles")
def list_profile_roster():
    """Return every installed profile with its description.

    Consumed by the dashboard's settings panel (orchestrator picker)
    and the profile-description editing UI. Profiles without a
    description still appear here — they're routable on name alone,
    just less precisely.
    """
    try:
        from hermes_cli import profiles as profiles_mod
        profiles = profiles_mod.list_profiles()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to list profiles: {exc}")
    return {
        "profiles": [
            {
                "name": p.name,
                "is_default": bool(p.is_default),
                "model": p.model or "",
                "provider": p.provider or "",
                "description": p.description or "",
                "description_auto": bool(p.description_auto),
                "skill_count": int(p.skill_count or 0),
            }
            for p in profiles
        ],
    }


@router.patch("/profiles/{profile_name}")
def update_profile_description(profile_name: str, payload: DescribeBody):
    """Set or clear the description of a profile.

    Empty string clears the description; non-empty stores it as a
    user-authored description (``description_auto: false``) so the
    auto-describer won't overwrite it on a sweep without
    ``--overwrite``.
    """
    try:
        from hermes_cli import profiles as profiles_mod
        canon = profiles_mod.normalize_profile_name(profile_name)
        if canon == "default":
            from hermes_constants import get_hermes_home  # type: ignore
            from pathlib import Path as _Path
            profile_dir = _Path(get_hermes_home())
        else:
            profile_dir = profiles_mod.get_profile_dir(canon)
        if not profile_dir.is_dir():
            raise HTTPException(status_code=404, detail=f"profile '{profile_name}' not found")
        text = (payload.description or "").strip()
        profiles_mod.write_profile_meta(
            profile_dir,
            description=text,
            description_auto=False,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to update profile: {exc}")
    return {"ok": True, "profile": canon, "description": text}


@router.post("/profiles/{profile_name}/describe-auto")
def auto_describe_profile(profile_name: str, payload: DescribeAutoBody):
    """Generate a description for the named profile via the auxiliary
    LLM (``auxiliary.profile_describer``). Persists with
    ``description_auto: true`` so the dashboard can surface a "review"
    badge.

    Maps 1:1 to ``hermes profile describe <name> --auto``. Non-OK
    outcomes are NOT HTTP errors — the UI renders the reason inline
    (e.g. "no auxiliary client configured") so the operator can fix
    config and retry without a page reload.
    """
    try:
        from hermes_cli import profile_describer  # noqa: WPS433 (intentional)
        outcome = profile_describer.describe_profile(
            profile_name,
            overwrite=bool(payload.overwrite),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"describer crashed: {exc}")
    return {
        "ok": bool(outcome.ok),
        "profile": outcome.profile_name,
        "reason": outcome.reason,
        "description": outcome.description,
    }


# ---------------------------------------------------------------------------
# Decompose endpoint (built-in decomposer fan-out)
# ---------------------------------------------------------------------------

class DecomposeBody(BaseModel):
    author: Optional[str] = None


@router.post("/tasks/{task_id}/decompose")
def decompose_task_endpoint(
    task_id: str,
    payload: DecomposeBody,
    board: Optional[str] = Query(None),
):
    """Fan a triage-column task out into a graph of child tasks via the
    auxiliary LLM, routed to specialist profiles by description. Maps
    1:1 to ``hermes kanban decompose <task_id>``.

    Returns the outcome shape used by the CLI: ``{ok, task_id, reason,
    fanout, child_ids, new_title}``. A non-OK outcome is NOT an HTTP
    error — the UI renders the reason inline.

    Runs in FastAPI's threadpool (sync ``def``) because the LLM call
    can take minutes on reasoning models.
    """
    board = _resolve_board(board)
    # Context-local board pin (see specify endpoint above): this sync
    # endpoint runs in FastAPI's threadpool, so mutating the process-global
    # HERMES_KANBAN_BOARD env var would let concurrent requests for
    # different boards race and cross-write (issue #38323).
    with kanban_db.scoped_current_board(board or kanban_db.DEFAULT_BOARD):
        from hermes_cli import kanban_decompose  # noqa: WPS433 (intentional)
        outcome = kanban_decompose.decompose_task(
            task_id,
            author=(payload.author or None),
        )

    return {
        "ok": bool(outcome.ok),
        "task_id": outcome.task_id,
        "reason": outcome.reason,
        "fanout": bool(outcome.fanout),
        "child_ids": outcome.child_ids or [],
        "new_title": outcome.new_title,
    }


# ---------------------------------------------------------------------------
# Orchestration settings (kanban.orchestrator_profile / default_assignee /
# auto_decompose) — surfaced to the dashboard's settings panel
# ---------------------------------------------------------------------------

class OrchestrationSettingsBody(BaseModel):
    orchestrator_profile: Optional[str] = None
    default_assignee: Optional[str] = None
    auto_decompose: Optional[bool] = None
    auto_promote_children: Optional[bool] = None


@router.get("/orchestration")
def get_orchestration_settings():
    """Return the current kanban orchestration knobs from config.yaml
    plus the resolved effective values (filling in fallbacks)."""
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
    except Exception:
        cfg = {}
    kanban_cfg = (cfg.get("kanban") or {}) if isinstance(cfg, dict) else {}
    explicit_orch = (kanban_cfg.get("orchestrator_profile") or "").strip()
    explicit_default = (kanban_cfg.get("default_assignee") or "").strip()
    auto_decompose = bool(kanban_cfg.get("auto_decompose", True))
    auto_promote_children = bool(kanban_cfg.get("auto_promote_children", True))

    # Resolve fallbacks the same way the decomposer does.
    resolved_orch = explicit_orch
    resolved_default = explicit_default
    try:
        from hermes_cli import profiles as profiles_mod
        active_default = profiles_mod.get_active_profile_name() or "default"
        if not resolved_orch or not profiles_mod.profile_exists(resolved_orch):
            resolved_orch = active_default
        if not resolved_default or not profiles_mod.profile_exists(resolved_default):
            resolved_default = active_default
    except Exception:
        active_default = "default"
        if not resolved_orch:
            resolved_orch = active_default
        if not resolved_default:
            resolved_default = active_default

    return {
        "orchestrator_profile": explicit_orch,
        "default_assignee": explicit_default,
        "auto_decompose": auto_decompose,
        "auto_promote_children": auto_promote_children,
        "resolved_orchestrator_profile": resolved_orch,
        "resolved_default_assignee": resolved_default,
        "active_profile": active_default,
    }


@router.put("/orchestration")
def set_orchestration_settings(payload: OrchestrationSettingsBody):
    """Update the kanban orchestration knobs in ~/.hermes/config.yaml.

    Each field is optional — only fields explicitly passed are
    written. ``orchestrator_profile`` / ``default_assignee`` accept
    empty strings to clear the override and fall back to the default
    profile.
    """
    try:
        from hermes_cli.config import load_config, save_config
        cfg = load_config() or {}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to load config: {exc}")

    kanban_section = cfg.setdefault("kanban", {})
    if not isinstance(kanban_section, dict):
        kanban_section = {}
        cfg["kanban"] = kanban_section

    # Validate any non-empty profile names exist before saving.
    try:
        from hermes_cli import profiles as profiles_mod
    except Exception:
        profiles_mod = None  # type: ignore

    if payload.orchestrator_profile is not None:
        name = (payload.orchestrator_profile or "").strip()
        if name and profiles_mod is not None:
            try:
                if not profiles_mod.profile_exists(name):
                    raise HTTPException(
                        status_code=400,
                        detail=f"profile '{name}' does not exist",
                    )
            except HTTPException:
                raise
            except Exception:
                pass  # fail open if the lookup itself errors
        kanban_section["orchestrator_profile"] = name

    if payload.default_assignee is not None:
        name = (payload.default_assignee or "").strip()
        if name and profiles_mod is not None:
            try:
                if not profiles_mod.profile_exists(name):
                    raise HTTPException(
                        status_code=400,
                        detail=f"profile '{name}' does not exist",
                    )
            except HTTPException:
                raise
            except Exception:
                pass
        kanban_section["default_assignee"] = name

    if payload.auto_decompose is not None:
        kanban_section["auto_decompose"] = bool(payload.auto_decompose)

    if payload.auto_promote_children is not None:
        kanban_section["auto_promote_children"] = bool(payload.auto_promote_children)

    try:
        save_config(cfg)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to save config: {exc}")

    # Echo back the resolved state (callers usually re-render from it).
    return get_orchestration_settings()


@router.websocket("/events")
async def stream_events(ws: WebSocket):
    # Authorize the upgrade via the dashboard's canonical WS gate so the
    # correct credential is accepted in every mode (loopback token / gated
    # single-use ticket / server-internal credential). Browsers can't set
    # Authorization on a WS upgrade, so the credential rides in the query
    # string — the browser SDK's buildWsUrl() assembles it.
    if not _ws_upgrade_authorized(ws):
        await ws.close(code=http_status.WS_1008_POLICY_VIOLATION)
        return
    await ws.accept()
    try:
        since_raw = ws.query_params.get("since", "0")
        try:
            cursor = int(since_raw)
        except ValueError:
            cursor = 0

        # Board selection — pinned at the WS handshake; re-subscribe to
        # switch boards. Changing boards mid-stream would require
        # reconciling two cursors, so the UI just opens a new WS on
        # board change.
        ws_board_raw = ws.query_params.get("board")
        try:
            ws_board = kanban_db._normalize_board_slug(ws_board_raw) if ws_board_raw else None
        except ValueError:
            ws_board = None

        def _fetch_new(cursor_val: int) -> tuple[int, list[dict]]:
            conn = kanban_db.connect(board=ws_board)
            try:
                rows = conn.execute(
                    "SELECT id, task_id, run_id, kind, payload, created_at "
                    "FROM task_events WHERE id > ? ORDER BY id ASC LIMIT 200",
                    (cursor_val,),
                ).fetchall()
                out: list[dict] = []
                new_cursor = cursor_val
                for r in rows:
                    try:
                        payload = json.loads(r["payload"]) if r["payload"] else None
                    except Exception:
                        payload = None
                    out.append({
                        "id": r["id"],
                        "task_id": r["task_id"],
                        "run_id": r["run_id"],
                        "kind": r["kind"],
                        "payload": payload,
                        "created_at": r["created_at"],
                    })
                    new_cursor = r["id"]
                return new_cursor, out
            finally:
                conn.close()

        while True:
            cursor, events = await asyncio.to_thread(_fetch_new, cursor)
            if events:
                await ws.send_json({"events": events, "cursor": cursor})
            await asyncio.sleep(_EVENT_POLL_SECONDS)
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        # Normal shutdown path: dashboard process exit (Ctrl-C) cancels the
        # websocket task while it is sleeping in the poll loop.
        # CancelledError is a BaseException in 3.8+ so the bare Exception
        # handler below would not catch it; without this clause Uvicorn
        # surfaces the cancellation as an application traceback. Quiet it.
        return
    except Exception as exc:  # defensive: never crash the dashboard worker
        log.warning("Kanban event stream error: %s", exc)
        try:
            await ws.close()
        except Exception:
            pass
