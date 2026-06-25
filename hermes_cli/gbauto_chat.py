"""Supabase-backed chat reads/sends for the dashboard /chat tab.

All DB access goes through the ``gbauto-supabase`` CLI (server-side
credentials), so real-client chat is NEVER exposed to the browser via the
public anon key. The dashboard's session-token middleware gates the HTTP
endpoints; callers pass a tenant from a fixed allowlist and never raw SQL.

This is the secure alternative to anon RLS expansion: only authenticated
dashboard users (who hold the session token) can read/write client chat, and
the credentials stay on the server.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any, Optional

# Clients selectable in the dashboard tenant switcher. Only smoke-client has a
# Hermes bridge today, so other tenants' user rows persist but get no assistant
# reply until a per-client bridge runs.
ALLOWED_TENANTS = {"smoke-client", "gbautomation", "jid5274", "ecom"}
MAX_LEN = 4000
MAX_LIMIT = 200


def _sql_literal(value: str) -> str:
    # Mirrors gbauto_supabase_logs._sql_literal — escape quotes and % (the CLI
    # passes SQL through a format context that treats % specially).
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


def load_messages(tenant: str, *, since: Optional[str] = None, limit: Any = 100) -> dict[str, Any]:
    """Read chat history for a tenant, oldest-first. Optional incremental `since`."""
    if tenant not in ALLOWED_TENANTS:
        raise ValueError(f"tenant not allowed: {tenant}")
    n = _clamp(limit, 100, 1, MAX_LIMIT)
    clauses = [f"tenant = {_sql_literal(tenant)}"]
    if since:
        clauses.append(f"created_at > {_sql_literal(since)}")
    sql = (
        "select id, role, content, created_at, tenant, session_id "
        "from chat_messages where " + " and ".join(clauses) +
        f" order by created_at asc limit {n}"
    )
    return {"ok": True, "rows": _run_cli(sql)}


def _run_insert(table: str, row: dict[str, Any], *, timeout: int = 45) -> Optional[dict[str, Any]]:
    # The `query` subcommand is read-only (writes roll back); `insert` is the
    # real write path. It takes a JSON file with the row object.
    binary = shutil.which("gbauto-supabase")
    if not binary:
        raise RuntimeError("gbauto-supabase CLI is not on PATH")
    fd, path = tempfile.mkstemp(suffix=".json", prefix="gbchat_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(row, handle)
        proc = subprocess.run(
            [binary, "--json", "insert", table, path],
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
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


def send_message(tenant: str, session_id: str, text: str) -> dict[str, Any]:
    """Insert a user chat row server-side (bypasses anon RLS via real creds)."""
    if tenant not in ALLOWED_TENANTS:
        raise ValueError(f"tenant not allowed: {tenant}")
    body = (text or "").strip()
    if not body:
        raise ValueError("empty message")
    if len(body) > MAX_LEN:
        raise ValueError("message too long")
    sid = ((session_id or "dashboard-web").strip() or "dashboard-web")[:128]
    row = _run_insert(
        "chat_messages",
        {"tenant": tenant, "session_id": sid, "role": "user", "content": body},
    )
    return {"ok": True, "row": row}
