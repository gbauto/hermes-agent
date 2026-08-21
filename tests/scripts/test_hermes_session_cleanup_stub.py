from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import hermes_session_cleanup_stub as stub


def make_state_db(path: Path, *, message_count: int = 5, tool_call_count: int = 1, ended: bool = True, preview: str = "Build the thing") -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            model TEXT,
            started_at REAL NOT NULL,
            ended_at REAL,
            end_reason TEXT,
            message_count INTEGER DEFAULT 0,
            tool_call_count INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_write_tokens INTEGER DEFAULT 0,
            reasoning_tokens INTEGER DEFAULT 0,
            estimated_cost_usd REAL,
            actual_cost_usd REAL,
            cost_status TEXT,
            title TEXT,
            system_prompt TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_calls TEXT,
            tool_name TEXT,
            timestamp REAL NOT NULL
        );
        """
    )
    conn.execute(
        """
        INSERT INTO sessions (
            id, source, model, started_at, ended_at, end_reason, message_count,
            tool_call_count, input_tokens, output_tokens, cache_read_tokens,
            cache_write_tokens, reasoning_tokens, estimated_cost_usd,
            actual_cost_usd, cost_status, title, system_prompt
        ) VALUES (?, 'cli', 'provider/model', 1000, ?, 'completed', ?, ?, 10, 20, 3, 4, 5, 0.01, 0.02, 'final', ?, ?)
        """,
        ("session_abcdef123456", 1300 if ended else None, message_count, tool_call_count, "Useful session", "DO NOT EXPORT system prompt"),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'user', ?, 1001)",
        ("session_abcdef123456", preview),
    )
    conn.commit()
    conn.close()


def payload(session_id: str = "session_abcdef123456") -> str:
    return json.dumps(
        {
            "hook_event_name": "on_session_end",
            "session_id": session_id,
            "cwd": "/tmp/project",
            "extra": {"completed": True, "interrupted": False, "profile": "test-profile", "platform": "cli"},
        }
    )


def test_writes_idempotent_sanitized_stub(tmp_path, monkeypatch, capsys):
    db = tmp_path / "state.db"
    make_state_db(db, preview="Please use token=sk-abcdefghijklmnopqrstuvwxyz and keep .env safe")
    brain = tmp_path / "second-brain"
    monkeypatch.setattr(sys, "stdin", type("S", (), {"read": lambda self: payload()})())

    assert stub.main(["--state-db", str(db), "--second-brain-root", str(brain), "--json"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["action"] == "written"
    assert receipt["substantial"] is True
    stub_path = Path(receipt["stub_path"])
    assert stub_path.exists()
    text = stub_path.read_text()
    assert "schema_version: hermes-session-reference.v1" in text
    assert "system_prompt" not in text
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in text
    assert ".env" not in text
    assert "[REDACTED]" in text

    monkeypatch.setattr(sys, "stdin", type("S", (), {"read": lambda self: payload()})())
    assert stub.main(["--state-db", str(db), "--second-brain-root", str(brain), "--json"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["action"] == "unchanged"
    assert second["stub_path"] == str(stub_path)


def test_skips_below_threshold_session(tmp_path, monkeypatch, capsys):
    db = tmp_path / "state.db"
    make_state_db(db, message_count=1, tool_call_count=0)
    monkeypatch.setattr(sys, "stdin", type("S", (), {"read": lambda self: payload()})())

    stub.main(["--state-db", str(db), "--second-brain-root", str(tmp_path / "brain"), "--min-duration-seconds", "999", "--json"])
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["action"] == "skipped"
    assert receipt["reason"] == "below_thresholds"
    assert receipt["substantial"] is False


def test_skips_incomplete_by_default_and_allows_flag(tmp_path, monkeypatch, capsys):
    db = tmp_path / "state.db"
    make_state_db(db, ended=False, message_count=10)
    monkeypatch.setattr(sys, "stdin", type("S", (), {"read": lambda self: payload()})())
    stub.main(["--state-db", str(db), "--second-brain-root", str(tmp_path / "brain"), "--json"])
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["reason"] == "incomplete_session"

    monkeypatch.setattr(sys, "stdin", type("S", (), {"read": lambda self: payload()})())
    stub.main(["--state-db", str(db), "--second-brain-root", str(tmp_path / "brain"), "--include-incomplete", "--json"])
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["action"] == "written"


def test_missing_session_receipt(tmp_path, monkeypatch, capsys):
    db = tmp_path / "state.db"
    make_state_db(db)
    monkeypatch.setattr(sys, "stdin", type("S", (), {"read": lambda self: payload("missing")})())
    stub.main(["--state-db", str(db), "--second-brain-root", str(tmp_path / "brain"), "--json"])
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["action"] == "skipped"
    assert receipt["reason"] == "session_not_found"
