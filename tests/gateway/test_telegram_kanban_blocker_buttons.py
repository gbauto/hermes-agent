"""Tests for Telegram Kanban blocker digests and buttons."""

import sys
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return
    mod = MagicMock()
    mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    mod.constants.ChatType.PRIVATE = "private"
    mod.constants.ChatType.GROUP = "group"
    mod.constants.ChatType.SUPERGROUP = "supergroup"
    mod.constants.ChatType.CHANNEL = "channel"
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})
    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)
    sys.modules.setdefault("telegram.error", mod.error)


_ensure_telegram_mock()

from gateway.config import PlatformConfig
from gateway.kanban_watchers import _kanban_blocked_digest, _kanban_blocker_keyboard_metadata
from gateway.platforms.telegram import TelegramAdapter


def _make_adapter(extra=None):
    config = PlatformConfig(enabled=True, token="test-token", extra=extra or {})
    adapter = TelegramAdapter(config)
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


def test_blocker_digest_is_mobile_sized_and_labeled():
    reason = " ".join(["blocked"] * 220)
    msg = _kanban_blocked_digest("t_abc123", "A very long task title " * 20, "coder", reason)
    assert len(msg.split()) <= 150
    assert msg.startswith("🚧 Blocker: t_abc123")
    assert "Owner: @coder" in msg
    assert "Next: tap a button below." in msg


def test_blocker_keyboard_uses_existing_kanban_callbacks():
    meta = _kanban_blocker_keyboard_metadata("gbautomation", "t_abc123")
    keyboard = meta["telegram_inline_keyboard"]
    assert keyboard[0][0]["callback_data"] == "kbp:p:gbautomation:t_abc123"
    assert keyboard[0][1]["callback_data"] == "kbp:s:gbautomation:t_abc123"
    assert keyboard[1][0]["callback_data"] == "kbp:o:gbautomation:t_abc123"


def test_send_metadata_inline_keyboard_passes_reply_markup():
    asyncio.run(_send_metadata_inline_keyboard_passes_reply_markup())


async def _send_metadata_inline_keyboard_passes_reply_markup():
    adapter = _make_adapter()
    mock_msg = MagicMock()
    mock_msg.message_id = 42
    adapter._bot.send_message = AsyncMock(return_value=mock_msg)

    result = await adapter.send(
        "12345",
        "🚧 Blocker: t_abc123\nNext: tap a button below.",
        metadata=_kanban_blocker_keyboard_metadata("gbautomation", "t_abc123"),
    )

    assert result.success is True
    kwargs = adapter._bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == 12345
    assert "reply_markup" in kwargs


def test_kanban_open_board_callback_answers_with_cli_hint():
    asyncio.run(_kanban_open_board_callback_answers_with_cli_hint())


async def _kanban_open_board_callback_answers_with_cli_hint():
    adapter = _make_adapter()
    query = AsyncMock()
    query.data = "kbp:o:gbautomation:t_abc123"
    query.message = MagicMock()
    query.message.chat_id = 12345
    query.message.text = "blocked"
    query.message.chat = MagicMock()
    query.message.chat.type = "private"
    query.message.message_thread_id = None
    query.from_user = MagicMock()
    query.from_user.id = "777"
    query.from_user.first_name = "Tester"

    adapter._is_callback_user_authorized = lambda *a, **k: True

    await adapter._handle_kanban_proposal_callback(
        query,
        "kbp:o:gbautomation:t_abc123",
        query_chat_id=12345,
        query_chat_type="private",
        query_thread_id=None,
        query_user_name="Tester",
    )

    query.answer.assert_awaited_once()
    kwargs = query.answer.call_args.kwargs
    assert kwargs["show_alert"] is True
    assert "hermes kanban --board gbautomation show t_abc123" in kwargs["text"]
