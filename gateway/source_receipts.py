"""Typed inbound source receipts — preserve the platform envelope the session
transcript strips.

2026-07-19 repair (jid5274 estate): chat-originated kanban cards must carry a
REAL ``telegram_source: <chat_id>/<message_id>`` stamp (it drives threaded
completion/blocker notifications back to the originating request). The
Telegram adapter puts ``message_id``/``update_id``/``reply_to_message_id`` on
``MessageEvent``, but nothing persisted them: the gateway INFO line logs
chat + text only, and the session transcript reduces the turn to
``{"role": "user", "content": ...}``. Agents were left with no truthful
surface to read the message id from — and fabricating one is forbidden.

This module is that surface:

- ``InboundSourceReceipt`` — typed envelope (platform, chat_id, message_id,
  user_id, update_id, reply_to_message_id, thread_id, session_id, timestamp).
- ``persist()`` appends every receipt to ``state/inbound-source-receipts.jsonl``
  (audit trail) and atomically rewrites
  ``state/telegram-sources/chat_<chat_id>.json`` with the latest ~20 receipts
  for that chat — the file an agent reads at card-creation time.
- ``format_telegram_source()`` renders the canonical stamp and REFUSES
  missing/zero/non-numeric message ids (never ``unknown``, never fabricated).
- ``validate_telegram_source_lines()`` — dispatch-boundary gate: reject card
  bodies whose ``telegram_source:`` stamp is malformed.

Message content is intentionally NOT persisted here — receipts carry routing
metadata only, never text.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

RECEIPTS_FILENAME = "inbound-source-receipts.jsonl"
PER_CHAT_DIRNAME = "telegram-sources"
PER_CHAT_KEEP = 20

_CHAT_SAFE_RE = re.compile(r"[^0-9A-Za-z_-]")
# A telegram_source line an agent placed in a card body. Group 2 is whatever
# follows the slash — validated separately so `/0`, `/unknown`, and missing
# ids produce a precise error instead of silently passing.
_SOURCE_LINE_RE = re.compile(
    r"^\s*telegram_source\s*[:=]\s*(-?\d{6,})\s*/\s*(\S*)\s*$", re.MULTILINE)
_SOURCE_ANY_RE = re.compile(r"^\s*telegram_source\s*[:=]", re.MULTILINE)


def _state_dir(base_dir: Optional[Path] = None) -> Path:
    if base_dir is not None:
        return Path(base_dir)
    home = os.environ.get("HERMES_HOME") or "~/.hermes"
    return Path(home).expanduser() / "state"


def _chat_file(chat_id: str, base_dir: Optional[Path] = None) -> Path:
    safe = _CHAT_SAFE_RE.sub("_", str(chat_id)) or "unknown-chat"
    return _state_dir(base_dir) / PER_CHAT_DIRNAME / f"chat_{safe}.json"


@dataclass(frozen=True)
class InboundSourceReceipt:
    platform: str
    chat_id: Optional[str] = None
    message_id: Optional[str] = None
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    update_id: Optional[int] = None
    reply_to_message_id: Optional[str] = None
    thread_id: Optional[str] = None
    session_id: Optional[str] = None
    timestamp: Optional[str] = None

    @classmethod
    def from_event(cls, event: Any, source: Any,
                   session_id: Optional[str] = None) -> "InboundSourceReceipt":
        """Build a receipt from a MessageEvent + SessionSource pair.

        Truthful by construction: fields absent on the event stay ``None``;
        nothing is guessed or substituted."""
        platform = getattr(source, "platform", None)
        platform_name = getattr(platform, "value", None) or str(platform or "unknown")
        ts = getattr(event, "timestamp", None)
        if isinstance(ts, datetime):
            ts_str = ts.astimezone(timezone.utc).isoformat(timespec="seconds") \
                if ts.tzinfo else ts.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
        else:
            ts_str = None
        return cls(
            platform=platform_name,
            chat_id=str(getattr(source, "chat_id", None) or "") or None,
            message_id=str(getattr(event, "message_id", None) or "") or None,
            user_id=str(getattr(source, "user_id", None) or "") or None,
            user_name=str(getattr(source, "user_name", None) or "") or None,
            update_id=getattr(event, "platform_update_id", None),
            reply_to_message_id=str(getattr(event, "reply_to_message_id", None) or "") or None,
            thread_id=str(getattr(source, "thread_id", None) or "") or None,
            session_id=session_id,
            timestamp=ts_str,
        )

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def persist(self, base_dir: Optional[Path] = None) -> Path:
        """Append to the audit JSONL and refresh the per-chat latest file.

        Returns the per-chat file path (or the JSONL path for chat-less
        receipts). Best-effort callers should wrap in try/except — this
        raises on I/O failure so tests can assert real persistence."""
        state = _state_dir(base_dir)
        state.mkdir(parents=True, exist_ok=True)
        row = self.to_dict()
        with (state / RECEIPTS_FILENAME).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if not self.chat_id:
            return state / RECEIPTS_FILENAME
        chat_path = _chat_file(self.chat_id, base_dir)
        chat_path.parent.mkdir(parents=True, exist_ok=True)
        existing: list[dict] = []
        try:
            data = json.loads(chat_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                existing = list(data.get("receipts") or [])
        except (OSError, json.JSONDecodeError):
            existing = []
        existing.append(row)
        payload = {
            "chat_id": self.chat_id,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "receipts": existing[-PER_CHAT_KEEP:],
        }
        # Atomic replace so a concurrent reader never sees a torn file.
        fd, tmp = tempfile.mkstemp(dir=str(chat_path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            os.replace(tmp, chat_path)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return chat_path


def latest_for_chat(chat_id: str, base_dir: Optional[Path] = None) -> Optional[InboundSourceReceipt]:
    """Newest receipt for a chat, or None."""
    try:
        data = json.loads(_chat_file(chat_id, base_dir).read_text(encoding="utf-8"))
        receipts = data.get("receipts") or []
        if not receipts:
            return None
        return InboundSourceReceipt(**receipts[-1])
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def format_telegram_source(receipt: InboundSourceReceipt) -> str:
    """Render the canonical card stamp: ``telegram_source: <chat>/<msg>``.

    Refuses to fabricate: raises ValueError when chat_id or a positive
    numeric message_id is missing."""
    if receipt.platform != "telegram":
        raise ValueError(f"not a telegram receipt (platform={receipt.platform!r})")
    if not receipt.chat_id:
        raise ValueError("receipt has no chat_id")
    mid = str(receipt.message_id or "")
    if not mid.isdigit() or int(mid) <= 0:
        raise ValueError(
            f"receipt has no usable message_id ({receipt.message_id!r}) — "
            "never fabricate one; repair capture instead")
    return f"telegram_source: {receipt.chat_id}/{int(mid)}"


def validate_telegram_source_lines(body: str) -> Optional[str]:
    """Dispatch-boundary gate for card bodies.

    Returns an error string when the body contains a telegram_source stamp
    that is malformed (missing message id, zero, `unknown`, non-numeric) —
    or None when the body has no stamp or a valid one. Cards without a stamp
    are allowed (sheet/cron lanes); cards with a LYING stamp are not."""
    if not body or not _SOURCE_ANY_RE.search(body):
        return None
    matches = _SOURCE_LINE_RE.findall(body)
    if not matches:
        return ("telegram_source line is malformed — expected "
                "'telegram_source: <chat_id>/<message_id>' with a numeric id")
    for _chat, mid in matches:
        if not mid.isdigit() or int(mid) <= 0:
            return (f"telegram_source message id {mid!r} is not a real Telegram "
                    "message id — do not use 0/unknown/placeholders; read the "
                    "receipt from state/telegram-sources/ instead")
    return None
