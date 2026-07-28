"""Durable daily-inbox storage for Kanban decisions and swipe cards.

The dashboard keeps inbox state under ``HERMES_HOME/inbox/inbox.db`` so it is
profile-aware, local to the operator estate, and independent of any one Kanban
board.  Board-squash operations store both a recoverable board archive and a
JSON snapshot receipt here.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from hermes_constants import get_hermes_home


ITEM_TYPES = {"decision", "quick_form", "dynamic_form", "swipe"}
ITEM_STATUSES = {"pending", "answered", "archived", "snoozed"}
RESPONSE_ACTIONS = {
    "answer",
    "archive",
    "go",
    "note",
    "snooze_until_tomorrow",
}


def inbox_db_path() -> Path:
    """Return the active profile's inbox SQLite path."""

    return get_hermes_home() / "inbox" / "inbox.db"


def _json(value: Any, fallback: Any) -> str:
    if value is None:
        value = fallback
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    """Open and initialize the inbox database."""

    db_path = Path(path) if path is not None else inbox_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS inbox_items (
            id TEXT PRIMARY KEY,
            item_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            title TEXT NOT NULL,
            prompt TEXT NOT NULL,
            detail TEXT,
            choices_json TEXT NOT NULL DEFAULT '[]',
            form_schema_json TEXT NOT NULL DEFAULT '{}',
            source_type TEXT,
            source_ref TEXT,
            source_board TEXT,
            source_task_id TEXT,
            source_snapshot_json TEXT NOT NULL DEFAULT '{}',
            priority INTEGER NOT NULL DEFAULT 0,
            assignee TEXT,
            recipient TEXT NOT NULL DEFAULT 'greg',
            due_at INTEGER,
            snoozed_until INTEGER,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            answered_at INTEGER,
            response_action TEXT,
            response_json TEXT NOT NULL DEFAULT '{}',
            note TEXT,
            UNIQUE(source_type, source_ref, source_board, source_task_id)
        );

        CREATE INDEX IF NOT EXISTS idx_inbox_items_daily
            ON inbox_items(status, snoozed_until, priority DESC, created_at);
        CREATE INDEX IF NOT EXISTS idx_inbox_items_type
            ON inbox_items(item_type, status, created_at);
        CREATE INDEX IF NOT EXISTS idx_inbox_items_source
            ON inbox_items(source_type, source_board, source_task_id);

        CREATE TABLE IF NOT EXISTS inbox_item_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            action TEXT NOT NULL,
            actor TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            note TEXT,
            created_at INTEGER NOT NULL,
            FOREIGN KEY(item_id) REFERENCES inbox_items(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_inbox_item_events_item
            ON inbox_item_events(item_id, id);

        CREATE TABLE IF NOT EXISTS board_squash_events (
            id TEXT PRIMARY KEY,
            board_slug TEXT NOT NULL,
            board_name TEXT,
            status TEXT NOT NULL,
            archive_path TEXT,
            task_total INTEGER NOT NULL DEFAULT 0,
            outstanding_total INTEGER NOT NULL DEFAULT 0,
            decisions_created INTEGER NOT NULL DEFAULT 0,
            board_snapshot_json TEXT NOT NULL DEFAULT '{}',
            error TEXT,
            created_at INTEGER NOT NULL,
            completed_at INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_board_squash_events_board
            ON board_squash_events(board_slug, created_at DESC);
        """
    )
    conn.commit()
    return conn


def _validate_item(item: Mapping[str, Any]) -> None:
    item_type = str(item.get("item_type") or "decision")
    if item_type not in ITEM_TYPES:
        raise ValueError(
            f"unsupported inbox item_type {item_type!r}; "
            f"expected one of {sorted(ITEM_TYPES)}"
        )
    title = str(item.get("title") or "").strip()
    prompt = str(item.get("prompt") or "").strip()
    if not title:
        raise ValueError("inbox item title is required")
    if not prompt:
        raise ValueError("inbox item prompt is required")


def upsert_items(
    conn: sqlite3.Connection,
    items: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Insert or refresh source-backed inbox items without resetting answers.

    A recurring swipe report may contain the same Kanban task every run.  The
    source tuple is therefore idempotent: current content is refreshed while
    the operator's status, response, note, and event history are preserved.
    """

    now = int(time.time())
    created_ids: list[str] = []
    refreshed_ids: list[str] = []
    with conn:
        for raw in items:
            item = dict(raw)
            _validate_item(item)
            item_id = str(item.get("id") or _new_id("in"))
            source_tuple = (
                item.get("source_type"),
                item.get("source_ref"),
                item.get("source_board"),
                item.get("source_task_id"),
            )
            existing = None
            if all(value is not None for value in source_tuple):
                existing = conn.execute(
                    """
                    SELECT id FROM inbox_items
                    WHERE source_type = ? AND source_ref = ?
                      AND source_board = ? AND source_task_id = ?
                    """,
                    source_tuple,
                ).fetchone()

            values = (
                item_id,
                str(item.get("item_type") or "decision"),
                str(item.get("status") or "pending"),
                str(item["title"]).strip(),
                str(item["prompt"]).strip(),
                item.get("detail"),
                _json(item.get("choices"), []),
                _json(item.get("form_schema"), {}),
                item.get("source_type"),
                item.get("source_ref"),
                item.get("source_board"),
                item.get("source_task_id"),
                _json(item.get("source_snapshot"), {}),
                int(item.get("priority") or 0),
                item.get("assignee"),
                str(item.get("recipient") or "greg"),
                item.get("due_at"),
                item.get("snoozed_until"),
                int(item.get("created_at") or now),
                now,
            )
            conn.execute(
                """
                INSERT INTO inbox_items (
                    id, item_type, status, title, prompt, detail,
                    choices_json, form_schema_json,
                    source_type, source_ref, source_board, source_task_id,
                    source_snapshot_json, priority, assignee, recipient,
                    due_at, snoozed_until, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_type, source_ref, source_board, source_task_id)
                DO UPDATE SET
                    item_type = excluded.item_type,
                    title = excluded.title,
                    prompt = excluded.prompt,
                    detail = excluded.detail,
                    choices_json = excluded.choices_json,
                    form_schema_json = excluded.form_schema_json,
                    source_snapshot_json = excluded.source_snapshot_json,
                    priority = excluded.priority,
                    assignee = excluded.assignee,
                    recipient = excluded.recipient,
                    due_at = excluded.due_at,
                    updated_at = excluded.updated_at
                """,
                values,
            )
            if all(value is not None for value in source_tuple):
                row = conn.execute(
                    """
                    SELECT id FROM inbox_items
                    WHERE source_type = ? AND source_ref = ?
                      AND source_board = ? AND source_task_id = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    source_tuple,
                ).fetchone()
                resolved_id = row["id"] if row else item_id
            else:
                resolved_id = item_id
            if existing:
                refreshed_ids.append(resolved_id)
            else:
                created_ids.append(resolved_id)

    return {
        "created": len(created_ids),
        "created_ids": created_ids,
        "refreshed": len(refreshed_ids),
        "refreshed_ids": refreshed_ids,
    }


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["choices"] = _decode(data.pop("choices_json", None), [])
    data["form_schema"] = _decode(data.pop("form_schema_json", None), {})
    data["source_snapshot"] = _decode(
        data.pop("source_snapshot_json", None), {}
    )
    data["response"] = _decode(data.pop("response_json", None), {})
    return data


def _resurface_due_snoozes(conn: sqlite3.Connection, now: int) -> None:
    with conn:
        conn.execute(
            """
            UPDATE inbox_items
            SET status = 'pending', snoozed_until = NULL, updated_at = ?
            WHERE status = 'snoozed'
              AND snoozed_until IS NOT NULL
              AND snoozed_until <= ?
            """,
            (now, now),
        )


def list_items(
    conn: sqlite3.Connection,
    *,
    status: Optional[str] = "pending",
    item_type: Optional[str] = None,
    recipient: Optional[str] = None,
    limit: int = 250,
) -> list[dict[str, Any]]:
    now = int(time.time())
    _resurface_due_snoozes(conn, now)
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        if status not in ITEM_STATUSES and status != "all":
            raise ValueError(f"unsupported inbox status {status!r}")
        if status != "all":
            clauses.append("status = ?")
            params.append(status)
    if item_type:
        if item_type not in ITEM_TYPES:
            raise ValueError(f"unsupported inbox item_type {item_type!r}")
        clauses.append("item_type = ?")
        params.append(item_type)
    if recipient:
        clauses.append("recipient = ?")
        params.append(recipient)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(int(limit), 1000)))
    rows = conn.execute(
        f"""
        SELECT * FROM inbox_items
        {where}
        ORDER BY priority DESC, COALESCE(due_at, created_at) ASC, created_at ASC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_row_dict(row) for row in rows]


def get_item(conn: sqlite3.Connection, item_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM inbox_items WHERE id = ?", (item_id,)
    ).fetchone()
    return _row_dict(row) if row else None


def respond_item(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    action: str,
    response: Optional[Mapping[str, Any]] = None,
    note: Optional[str] = None,
    actor: str = "dashboard",
) -> dict[str, Any]:
    if action not in RESPONSE_ACTIONS:
        raise ValueError(
            f"unsupported inbox action {action!r}; "
            f"expected one of {sorted(RESPONSE_ACTIONS)}"
        )
    current = get_item(conn, item_id)
    if current is None:
        raise KeyError(item_id)

    now = int(time.time())
    next_status = current["status"]
    answered_at = current.get("answered_at")
    snoozed_until = current.get("snoozed_until")
    if action == "archive":
        next_status = "archived"
        answered_at = now
        snoozed_until = None
    elif action == "snooze_until_tomorrow":
        next_status = "snoozed"
        snoozed_until = now + 24 * 60 * 60
        answered_at = None
    elif action in {"answer", "go"}:
        next_status = "answered"
        answered_at = now
        snoozed_until = None

    clean_note = (note or "").strip() or None
    response_payload = dict(response or {})
    with conn:
        conn.execute(
            """
            UPDATE inbox_items
            SET status = ?, updated_at = ?, answered_at = ?,
                snoozed_until = ?, response_action = ?,
                response_json = ?, note = COALESCE(?, note)
            WHERE id = ?
            """,
            (
                next_status,
                now,
                answered_at,
                snoozed_until,
                action,
                _json(response_payload, {}),
                clean_note,
                item_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO inbox_item_events (
                item_id, action, actor, payload_json, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                action,
                actor,
                _json(response_payload, {}),
                clean_note,
                now,
            ),
        )
    updated = get_item(conn, item_id)
    assert updated is not None
    return updated


def create_squash_event(
    conn: sqlite3.Connection,
    *,
    board_slug: str,
    board_name: Optional[str],
    task_total: int,
    outstanding_total: int,
    board_snapshot: Mapping[str, Any],
) -> str:
    event_id = _new_id("sq")
    now = int(time.time())
    with conn:
        conn.execute(
            """
            INSERT INTO board_squash_events (
                id, board_slug, board_name, status, task_total,
                outstanding_total, board_snapshot_json, created_at
            ) VALUES (?, ?, ?, 'preparing', ?, ?, ?, ?)
            """,
            (
                event_id,
                board_slug,
                board_name,
                int(task_total),
                int(outstanding_total),
                _json(dict(board_snapshot), {}),
                now,
            ),
        )
    return event_id


def finish_squash_event(
    conn: sqlite3.Connection,
    event_id: str,
    *,
    status: str,
    decisions_created: int,
    archive_path: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    now = int(time.time())
    with conn:
        conn.execute(
            """
            UPDATE board_squash_events
            SET status = ?, decisions_created = ?, archive_path = ?,
                error = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                status,
                int(decisions_created),
                archive_path,
                error,
                now,
                event_id,
            ),
        )


def list_squash_events(
    conn: sqlite3.Connection, *, limit: int = 50
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM board_squash_events
        ORDER BY created_at DESC LIMIT ?
        """,
        (max(1, min(int(limit), 250)),),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        data["board_snapshot"] = _decode(
            data.pop("board_snapshot_json", None), {}
        )
        result.append(data)
    return result
