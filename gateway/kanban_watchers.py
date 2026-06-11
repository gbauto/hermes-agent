"""Small Kanban gateway notification helpers.

The notifier currently lives in :mod:`gateway.run`; these helpers are kept in a
separate module so Telegram-specific tests can exercise the digest/keyboard
shape without importing the gateway runner god-file.
"""

from __future__ import annotations

import re
from typing import Any, Optional


def _trim_words(text: str, max_words: int) -> str:
    words = str(text or "").strip().split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]).rstrip(".,;:") + "…"


def _kanban_blocked_digest(task_id: str, title: str, assignee: Optional[str], reason: str) -> str:
    """Return a Telegram-mobile blocker digest under ~150 words."""
    clean_title = _trim_words(title, 14)
    clean_reason = _trim_words(reason or "Worker requested review", 58)
    owner = f"@{assignee}" if assignee else "unassigned"
    msg = (
        f"🚧 Blocker: {task_id}\n"
        f"Task: {clean_title}\n"
        f"Owner: {owner}\n"
        f"Issue: {clean_reason}\n"
        "Next: tap a button below."
    )
    # Last-resort hard cap. Keeps pathological titles/reasons from becoming
    # unreadable Telegram walls while preserving the action line.
    words = msg.split()
    if len(words) > 150:
        msg = " ".join(words[:147]).rstrip(".,;:") + "…"
    return msg


def _kanban_blocker_keyboard_metadata(board: str, task_id: str) -> dict[str, Any]:
    """Telegram inline-keyboard metadata for actionable blocked cards."""
    safe_board = re.sub(r"[^A-Za-z0-9_.-]", "", board or "default") or "default"
    safe_task = task_id if re.fullmatch(r"t_[A-Za-z0-9]+", task_id or "") else ""
    if not safe_task:
        return {}
    return {
        "telegram_inline_keyboard": [
            [
                {"text": "✅ Promote", "callback_data": f"kbp:p:{safe_board}:{safe_task}"},
                {"text": "⏭ Keep blocked", "callback_data": f"kbp:s:{safe_board}:{safe_task}"},
            ],
            [
                {"text": "Open board", "callback_data": f"kbp:o:{safe_board}:{safe_task}"},
            ],
        ]
    }
