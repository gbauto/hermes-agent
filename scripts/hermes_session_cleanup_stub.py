#!/usr/bin/env python3
"""Write a facts-only second-brain reference stub for a completed Hermes session.

This script is intended for Hermes' existing ``on_session_end`` shell-hook bridge.
It consumes the hook JSON payload from stdin, looks up session facts in state.db,
and writes a small sanitized Markdown pointer. It does not parse transcript JSONL
or export raw messages/tool arguments/system prompts.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    from hermes_constants import get_hermes_home
except Exception:  # pragma: no cover - CLI fallback outside repo imports
    def get_hermes_home() -> Path:  # type: ignore[no-redef]
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()

SCHEMA_VERSION = "hermes-session-reference.v1"
RECEIPT_SCHEMA_VERSION = "hermes-session-cleanup-receipt.v1"
MAX_PREVIEW_CHARS = 240
SECRET_PATTERNS = [
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|oauth|password|passwd|authorization)\b\s*[:=]\s*[^\s,;]+"), r"\1=[REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "[REDACTED]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"), "[REDACTED]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"), "[REDACTED]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer [REDACTED]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
]
FORBIDDEN_TERMS = ("system_prompt", ".env", "oauth", "api_key", "secret", "password", "token")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-db", type=Path, default=get_hermes_home() / "state.db")
    parser.add_argument("--second-brain-root", type=Path, default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--min-messages", type=int, default=4)
    parser.add_argument("--min-tool-calls", type=int, default=1)
    parser.add_argument("--min-duration-seconds", type=int, default=120)
    parser.add_argument("--include-incomplete", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print receipt JSON")
    return parser.parse_args(argv)


def load_hook_payload(stdin_text: str) -> dict[str, Any]:
    text = (stdin_text or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"stdin was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("stdin JSON payload must be an object")
    return data


def iso_from_timestamp(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        return dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return None


def date_from_timestamp(value: Any) -> str:
    iso = iso_from_timestamp(value)
    if iso:
        return iso[:10]
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def safe_scalar(value: Any) -> str:
    if value is None:
        return "null"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def sanitize_text(value: Any, *, max_chars: int = MAX_PREVIEW_CHARS) -> tuple[str, list[str]]:
    text = "" if value is None else str(value)
    redactions: list[str] = []
    text = re.sub(r"\s+", " ", text).strip()
    for pattern, repl in SECRET_PATTERNS:
        new_text = pattern.sub(repl, text)
        if new_text != text:
            redactions.append("secret_or_pii_pattern")
            text = new_text
    for term in FORBIDDEN_TERMS:
        if term.lower() in text.lower():
            redactions.append(f"forbidden_term:{term}")
            text = re.sub(re.escape(term), "[REDACTED]", text, flags=re.IGNORECASE)
    if len(text) > max_chars:
        redactions.append("truncated_preview")
        text = text[: max_chars - 1].rstrip() + "…"
    return text or "untitled", sorted(set(redactions))


def connect_readonly(state_db: Path) -> sqlite3.Connection:
    path = state_db.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(str(path))
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.DatabaseError:
        return set()


def select_columns(available: set[str], wanted: Iterable[str]) -> str:
    selected = [name for name in wanted if name in available]
    if not selected:
        selected = ["id"]
    return ", ".join(selected)


def fetch_session_facts(state_db: Path, session_id: str) -> dict[str, Any] | None:
    if not session_id:
        return None
    with connect_readonly(state_db) as conn:
        session_cols = table_columns(conn, "sessions")
        message_cols = table_columns(conn, "messages")
        if "id" not in session_cols:
            raise sqlite3.DatabaseError("sessions table with id column is required")
        wanted_session_cols = [
            "id", "source", "model", "started_at", "ended_at", "end_reason",
            "message_count", "tool_call_count", "input_tokens", "output_tokens",
            "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
            "estimated_cost_usd", "actual_cost_usd", "cost_status", "title",
        ]
        row = conn.execute(
            f"SELECT {select_columns(session_cols, wanted_session_cols)} FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        facts = dict(row)
        if "message_count" not in facts and "session_id" in message_cols:
            facts["message_count"] = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
        if "tool_call_count" not in facts and "session_id" in message_cols:
            tool_count = 0
            if "tool_calls" in message_cols:
                tool_count += conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ? AND COALESCE(tool_calls, '') != ''",
                    (session_id,),
                ).fetchone()[0]
            if "tool_name" in message_cols:
                tool_count += conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ? AND COALESCE(tool_name, '') != ''",
                    (session_id,),
                ).fetchone()[0]
            facts["tool_call_count"] = tool_count
        preview = None
        if {"session_id", "role", "content"}.issubset(message_cols):
            preview_row = conn.execute(
                "SELECT content FROM messages WHERE session_id = ? AND role = 'user' AND COALESCE(content, '') != '' ORDER BY timestamp ASC, id ASC LIMIT 1",
                (session_id,),
            ).fetchone()
            if preview_row:
                preview = preview_row[0]
        facts["preview"] = preview
        return facts


def is_substantial(facts: dict[str, Any], thresholds: argparse.Namespace) -> tuple[bool, str]:
    if not thresholds.include_incomplete and facts.get("ended_at") in (None, ""):
        return False, "incomplete_session"
    started_raw = facts.get("started_at")
    ended_raw = facts.get("ended_at")
    try:
        duration = None if ended_raw is None else float(ended_raw) - float(started_raw or 0)
    except (TypeError, ValueError):
        duration = None
    message_count = int(facts.get("message_count") or 0)
    tool_call_count = int(facts.get("tool_call_count") or 0)
    if message_count >= thresholds.min_messages:
        return True, "message_threshold"
    if tool_call_count >= thresholds.min_tool_calls:
        return True, "tool_call_threshold"
    if duration is not None and duration >= thresholds.min_duration_seconds:
        return True, "duration_threshold"
    return False, "below_thresholds"


def resolve_second_brain_root(raw: Path | None, cwd: str | None = None) -> Path:
    if raw is not None:
        return raw.expanduser().resolve()
    env_root = os.environ.get("HERMES_SECOND_BRAIN_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    if cwd:
        candidate = Path(cwd).expanduser() / "second-brain"
        if candidate.exists():
            return candidate.resolve()
    local_candidate = Path.cwd() / "second-brain"
    if local_candidate.exists():
        return local_candidate.resolve()
    return (get_hermes_home() / "second-brain").expanduser().resolve()


def resolve_profile(profile_arg: str | None, payload: dict[str, Any]) -> str:
    extra_obj = payload.get("extra")
    extra = extra_obj if isinstance(extra_obj, dict) else {}
    raw = profile_arg or extra.get("profile") or os.environ.get("HERMES_PROFILE") or "default"
    profile = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(raw)).strip(".-")
    return profile or "default"


def stub_path(second_brain_root: Path, profile: str, facts: dict[str, Any]) -> Path:
    session_id = str(facts.get("id") or "session")
    short_id = re.sub(r"[^A-Za-z0-9]+", "", session_id)[-8:] or "session"
    date_part = date_from_timestamp(facts.get("started_at"))
    return second_brain_root / "intelligence" / "hermes-sessions" / profile / f"{date_part}-{short_id}.md"


def render_stub(facts: dict[str, Any], profile: str, state_db: Path) -> tuple[str, list[str]]:
    title, title_redactions = sanitize_text(facts.get("title"), max_chars=120)
    preview, preview_redactions = sanitize_text(facts.get("preview"), max_chars=MAX_PREVIEW_CHARS)
    started = iso_from_timestamp(facts.get("started_at"))
    ended = iso_from_timestamp(facts.get("ended_at"))
    duration = "unknown"
    if facts.get("started_at") is not None and facts.get("ended_at") is not None:
        try:
            duration = str(max(0, int(float(facts["ended_at"]) - float(facts["started_at"]))))
        except (TypeError, ValueError):
            duration = "unknown"
    session_id = str(facts.get("id") or "")
    short_id = re.sub(r"[^A-Za-z0-9]+", "", session_id)[-8:] or "session"
    source = facts.get("source") or "unknown"
    content = f"""---
schema_version: {SCHEMA_VERSION}
profile: {safe_scalar(profile)}
session_id: {safe_scalar(session_id)}
source: {safe_scalar(source)}
started_at: {safe_scalar(started)}
ended_at: {safe_scalar(ended)}
message_count: {int(facts.get('message_count') or 0)}
tool_call_count: {int(facts.get('tool_call_count') or 0)}
model: {safe_scalar(facts.get('model'))}
state_db: {safe_scalar(str(state_db))}
generated_by: hermes_session_cleanup_stub.py
---

# Hermes session {short_id}

- Profile: {profile}
- Source: {source}
- Title: {title}
- Preview: {preview}
- Duration: {duration} seconds
- Tokens: input={int(facts.get('input_tokens') or 0)}, output={int(facts.get('output_tokens') or 0)}, cache_read={int(facts.get('cache_read_tokens') or 0)}, cache_write={int(facts.get('cache_write_tokens') or 0)}, reasoning={int(facts.get('reasoning_tokens') or 0)}
- Cost: estimated={facts.get('estimated_cost_usd') if facts.get('estimated_cost_usd') is not None else 'unknown'}, actual={facts.get('actual_cost_usd') if facts.get('actual_cost_usd') is not None else 'unknown'}, status={facts.get('cost_status') or 'unknown'}

## Next reference action
Use Hermes `/resume {session_id}` or session search to inspect the full conversation if needed.
"""
    redactions = sorted(set(title_redactions + preview_redactions))
    return content, redactions


def write_stub(path: Path, content: str, dry_run: bool) -> str:
    if dry_run:
        return "dry_run"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return "unchanged"
    action = "updated" if path.exists() else "written"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return action


def build_receipt(**kwargs: Any) -> dict[str, Any]:
    receipt = {"schema_version": RECEIPT_SCHEMA_VERSION, **kwargs}
    return receipt


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = load_hook_payload(sys.stdin.read())
        session_id = str(payload.get("session_id") or "")
        state_db = args.state_db.expanduser().resolve()
        if payload.get("hook_event_name") not in (None, "", "on_session_end"):
            receipt = build_receipt(session_id=session_id, profile=resolve_profile(args.profile, payload), substantial=False, stub_path=None, action="skipped", reason="not_on_session_end", source_state_db=str(state_db), facts_used=[], redactions_applied=[])
        else:
            facts = fetch_session_facts(state_db, session_id)
            profile = resolve_profile(args.profile, payload)
            if facts is None:
                receipt = build_receipt(session_id=session_id, profile=profile, substantial=False, stub_path=None, action="skipped", reason="session_not_found", source_state_db=str(state_db), facts_used=[], redactions_applied=[])
            else:
                substantial, reason = is_substantial(facts, args)
                root = resolve_second_brain_root(args.second_brain_root, payload.get("cwd"))
                path = stub_path(root, profile, facts)
                if not substantial:
                    receipt = build_receipt(session_id=session_id, profile=profile, substantial=False, stub_path=str(path), action="skipped", reason=reason, source_state_db=str(state_db), facts_used=sorted(facts.keys()), redactions_applied=[])
                else:
                    content, redactions = render_stub(facts, profile, state_db)
                    action = write_stub(path, content, args.dry_run)
                    receipt = build_receipt(session_id=session_id, profile=profile, substantial=True, stub_path=str(path), action=action, reason=reason, source_state_db=str(state_db), facts_used=sorted(k for k in facts.keys() if k != "system_prompt"), redactions_applied=redactions)
    except Exception as exc:
        receipt = build_receipt(session_id="", profile=args.profile or os.environ.get("HERMES_PROFILE") or "default", substantial=False, stub_path=None, action="error", reason=f"{type(exc).__name__}: {exc}", source_state_db=str(args.state_db), facts_used=[], redactions_applied=[])
    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    elif receipt.get("action") not in ("skipped", "unchanged"):
        print(f"{receipt['action']}: {receipt.get('stub_path') or receipt.get('reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
