"""Small Kanban gateway notification helpers.

Kept separate from ``gateway.run`` so platform adapters and tests can reuse the
mobile digest / button metadata contract without importing the full runner.
"""

from __future__ import annotations

from typing import Any, Optional


def _trim_words(text: str, max_words: int) -> str:
    words = str(text or "").strip().split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]).rstrip(".,;:") + "…"


def kanban_blocked_digest(
    task_id: str,
    title: str,
    assignee: Optional[str],
    reason: str,
) -> str:
    """Return a Telegram-mobile blocker digest under ~150 words."""
    clean_title = _trim_words(title, 14)
    clean_reason = _trim_words(reason or "Worker requested review", 58)
    owner = f"@{assignee}" if assignee else "unassigned"
    msg = (
        f"🚧 Blocker: {task_id}\n"
        "Status: blocked\n"
        f"Task: {clean_title}\n"
        f"Owner: {owner}\n"
        f"Issue: {clean_reason}\n"
        "Next: tap a button below."
    )
    words = msg.split()
    if len(words) > 150:
        msg = " ".join(words[:147]).rstrip(".,;:") + "…"
    return msg


def kanban_blocker_keyboard_metadata(
    board: str,
    task_id: str,
    *,
    chat_id: str = "",
    thread_id: str = "",
    event_id: Optional[int] = None,
) -> dict[str, Any]:
    """Telegram inline-keyboard metadata for actionable blocked cards.

    The Telegram payload carries only ``kb:<action>:<sid>:<sig>`` short tokens.
    The task id, board slug, and event routing data are persisted server-side in
    the Kanban DB callback table and are not embedded in callback_data.
    """
    from hermes_cli import kanban_db as _kb

    with _kb.connect_closing(board=board or "default") as conn:
        promote = _kb.create_callback_action(
            conn,
            task_id=task_id,
            board=board or "default",
            action="ub",
            platform="telegram",
            chat_id=chat_id,
            thread_id=thread_id,
            event_id=event_id,
        )
        keep_blocked = _kb.create_callback_action(
            conn,
            task_id=task_id,
            board=board or "default",
            action="ack",
            platform="telegram",
            chat_id=chat_id,
            thread_id=thread_id,
            event_id=event_id,
        )
        open_board = _kb.create_callback_action(
            conn,
            task_id=task_id,
            board=board or "default",
            action="open",
            platform="telegram",
            chat_id=chat_id,
            thread_id=thread_id,
            event_id=event_id,
        )
    return {
        "telegram_inline_keyboard": [
            [
                {"text": "✅ Promote", "callback_data": promote},
                {"text": "⏭ Keep blocked", "callback_data": keep_blocked},
            ],
            [
                {"text": "Open board", "callback_data": open_board},
            ],
        ]
    }


# Backwards-compatible names used by the tested patch source.
_kanban_blocked_digest = kanban_blocked_digest
_kanban_blocker_keyboard_metadata = kanban_blocker_keyboard_metadata
