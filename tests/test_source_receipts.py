"""Inbound source receipts — 2026-07-19 telegram_source capture repair.

Proves (per the repair contract):
1. a Telegram update with chat.id + message_id produces a structured receipt
   with both values intact;
2. the receipt reaches the dispatch/card-creation boundary (latest_for_chat);
3. a Telegram-source card body with a missing/placeholder message id is
   REJECTED at the create gate;
4. a real source renders exactly as ``telegram_source: <chat_id>/<message_id>``;
5. non-Telegram messages remain functional (receipt persists, formatter
   refuses, gate ignores).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gateway.source_receipts import (
    InboundSourceReceipt,
    format_telegram_source,
    latest_for_chat,
    validate_telegram_source_lines,
)


@dataclass
class _FakePlatform:
    value: str


@dataclass
class _FakeSource:
    platform: object
    chat_id: str
    user_id: str = "111"
    user_name: str = "Jason"
    thread_id: str = ""


@dataclass
class _FakeEvent:
    message_id: str
    platform_update_id: int = 900001
    reply_to_message_id: str = ""
    timestamp: datetime = None


def _tg_pair(chat="-5242718733", mid="4471"):
    src = _FakeSource(platform=_FakePlatform("telegram"), chat_id=chat)
    ev = _FakeEvent(message_id=mid,
                    timestamp=datetime(2026, 7, 19, 19, 58, 34, tzinfo=timezone.utc))
    return ev, src


def test_receipt_preserves_chat_and_message_id(tmp_path):
    ev, src = _tg_pair()
    r = InboundSourceReceipt.from_event(ev, src, "sess_abc")
    assert r.platform == "telegram"
    assert r.chat_id == "-5242718733"
    assert r.message_id == "4471"
    assert r.update_id == 900001
    assert r.session_id == "sess_abc"
    assert r.timestamp == "2026-07-19T19:58:34+00:00"
    out = r.persist(base_dir=tmp_path)
    # audit JSONL row
    row = json.loads((tmp_path / "inbound-source-receipts.jsonl")
                     .read_text(encoding="utf-8").strip())
    assert row["chat_id"] == "-5242718733" and row["message_id"] == "4471"
    # per-chat latest file (the surface agents read at card-create time)
    assert out.name == "chat_-5242718733.json"


def test_receipt_reaches_dispatch_boundary(tmp_path):
    ev, src = _tg_pair(mid="4471")
    InboundSourceReceipt.from_event(ev, src, "s1").persist(base_dir=tmp_path)
    ev2, src2 = _tg_pair(mid="4480")
    InboundSourceReceipt.from_event(ev2, src2, "s1").persist(base_dir=tmp_path)
    latest = latest_for_chat("-5242718733", base_dir=tmp_path)
    assert latest is not None and latest.message_id == "4480"
    # exact canonical render
    assert format_telegram_source(latest) == "telegram_source: -5242718733/4480"


def test_formatter_refuses_missing_or_placeholder_ids():
    base = dict(platform="telegram", chat_id="-5242718733")
    for bad in (None, "", "0", "unknown", "sess_123", "12ab"):
        with pytest.raises(ValueError):
            format_telegram_source(InboundSourceReceipt(message_id=bad, **base))
    with pytest.raises(ValueError):
        format_telegram_source(InboundSourceReceipt(
            platform="discord", chat_id="-1", message_id="5"))


def test_create_gate_rejects_lying_stamps_allows_real_and_absent():
    # missing stamp: allowed (sheet/cron lanes)
    assert validate_telegram_source_lines("plain card body") is None
    assert validate_telegram_source_lines("") is None
    # real stamp: allowed
    assert validate_telegram_source_lines(
        "context\ntelegram_source: -5242718733/4471\nmore") is None
    # placeholder / fabricated shapes: rejected
    for bad in ("telegram_source: -5242718733/0",
                "telegram_source: -5242718733/unknown",
                "telegram_source: -5242718733/",
                "telegram_source: -5242718733"):
        err = validate_telegram_source_lines(f"body\n{bad}\n")
        assert err is not None, bad


def test_non_telegram_receipts_unaffected(tmp_path):
    src = _FakeSource(platform=_FakePlatform("discord"), chat_id="chan-9")
    ev = _FakeEvent(message_id="55")
    r = InboundSourceReceipt.from_event(ev, src, "s2")
    assert r.platform == "discord"
    r.persist(base_dir=tmp_path)  # persists fine
    with pytest.raises(ValueError):
        format_telegram_source(r)  # but never renders a telegram stamp


def test_chatless_receipt_still_audited(tmp_path):
    src = _FakeSource(platform=_FakePlatform("telegram"), chat_id="")
    ev = _FakeEvent(message_id="")
    out = InboundSourceReceipt.from_event(ev, src, None).persist(base_dir=tmp_path)
    assert out.name == "inbound-source-receipts.jsonl"
