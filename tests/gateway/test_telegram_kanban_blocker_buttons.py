"""Tests for Telegram Kanban blocker digests and opaque buttons."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

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
from hermes_cli import kanban_db as _kb


def _make_adapter(extra=None):
    config = PlatformConfig(enabled=True, token="test-token", extra=extra or {})
    adapter = TelegramAdapter(config)
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


def _make_query(data="kb:open:abc:sig"):
    query = AsyncMock()
    query.data = data
    query.message = MagicMock()
    query.message.chat_id = 12345
    query.message.text = "blocked"
    query.message.chat = MagicMock()
    query.message.chat.type = "private"
    query.message.message_thread_id = None
    query.from_user = MagicMock()
    query.from_user.id = "777"
    query.from_user.first_name = "Tester"
    return query


@pytest.fixture
def isolated_kanban_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path / "kanban-home"))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    _kb._INITIALIZED_PATHS.clear()
    yield tmp_path
    _kb._INITIALIZED_PATHS.clear()


def _blocked_task(board="gbautomation"):
    with _kb.connect_closing(board=board) as conn:
        return _kb.create_task(
            conn,
            title="Blocked task",
            assignee="coder",
            created_by="test",
            initial_status="blocked",
            board=board,
        )


def _callbacks(meta):
    return [button["callback_data"] for row in meta["telegram_inline_keyboard"] for button in row]


def test_blocker_digest_is_mobile_sized_and_labeled():
    reason = " ".join(["blocked"] * 220)
    msg = _kanban_blocked_digest("t_abc123", "A very long task title " * 20, "coder", reason)
    assert len(msg.split()) <= 150
    assert msg.startswith("🚧 Blocker: t_abc123")
    assert "Owner: @coder" in msg
    assert "Next: tap a button below." in msg


def test_blocker_keyboard_uses_signed_opaque_callbacks_only(isolated_kanban_home):
    task_id = _blocked_task()
    meta = _kanban_blocker_keyboard_metadata("gbautomation", task_id, chat_id="12345")
    callbacks = _callbacks(meta)

    assert len(callbacks) == 3
    assert all(callback.startswith("kb:") for callback in callbacks)
    assert all(len(callback) <= 64 for callback in callbacks)
    assert all("gbautomation" not in callback for callback in callbacks)
    assert all(task_id not in callback for callback in callbacks)
    assert all("review-required" not in callback for callback in callbacks)
    assert [callback.split(":", 3)[1] for callback in callbacks] == ["ub", "ack", "open"]


@pytest.mark.asyncio
async def test_send_metadata_inline_keyboard_passes_reply_markup(isolated_kanban_home):
    task_id = _blocked_task()
    adapter = _make_adapter()
    mock_msg = MagicMock()
    mock_msg.message_id = 42
    adapter._bot.send_message = AsyncMock(return_value=mock_msg)

    result = await adapter.send(
        "12345",
        "🚧 Blocker: t_abc123\nNext: tap a button below.",
        metadata=_kanban_blocker_keyboard_metadata("gbautomation", task_id),
    )

    assert result.success is True
    kwargs = adapter._bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == 12345
    assert "reply_markup" in kwargs


@pytest.mark.asyncio
async def test_kanban_open_board_callback_answers_with_cli_hint(isolated_kanban_home):
    task_id = _blocked_task()
    open_payload = _callbacks(_kanban_blocker_keyboard_metadata("gbautomation", task_id))[2]
    adapter = _make_adapter()
    query = _make_query(open_payload)
    adapter._is_callback_user_authorized = lambda *a, **k: True

    await adapter._handle_kanban_proposal_callback(
        query,
        open_payload,
        query_chat_id=12345,
        query_chat_type="private",
        query_thread_id=None,
        query_user_name="Tester",
    )

    query.answer.assert_awaited_once()
    kwargs = query.answer.call_args.kwargs
    assert kwargs["show_alert"] is True
    assert f"hermes kanban --board gbautomation show {task_id}" in kwargs["text"]


@pytest.mark.asyncio
async def test_kanban_promote_callback_uses_signed_callback_and_kanban_api(isolated_kanban_home):
    task_id = _blocked_task()
    promote_payload = _callbacks(_kanban_blocker_keyboard_metadata("gbautomation", task_id))[0]
    adapter = _make_adapter()
    query = _make_query(promote_payload)
    adapter._is_callback_user_authorized = lambda *a, **k: True

    await adapter._handle_kanban_proposal_callback(
        query,
        promote_payload,
        query_chat_id=12345,
        query_chat_type="private",
        query_thread_id=None,
        query_user_name="Tester",
    )

    query.answer.assert_awaited_once_with(text=f"✅ Promoted {task_id} to ready")
    query.edit_message_text.assert_awaited_once()
    assert query.edit_message_text.call_args.kwargs["reply_markup"] is None
    with _kb.connect_closing(board="gbautomation") as conn:
        task = _kb.get_task(conn, task_id)
        assert task is not None
        assert task.status == "ready"


@pytest.mark.asyncio
async def test_kanban_callback_bad_signature_fails_closed(isolated_kanban_home):
    task_id = _blocked_task()
    promote_payload = _callbacks(_kanban_blocker_keyboard_metadata("gbautomation", task_id))[0]
    bad_payload = promote_payload.rsplit(":", 1)[0] + ":bad"
    adapter = _make_adapter()
    query = _make_query(bad_payload)
    adapter._is_callback_user_authorized = lambda *a, **k: True

    await adapter._handle_kanban_proposal_callback(
        query,
        bad_payload,
        query_chat_id=12345,
        query_chat_type="private",
        query_thread_id=None,
        query_user_name="Tester",
    )

    query.answer.assert_awaited_once_with(text="Invalid or expired Kanban action.")
    with _kb.connect_closing(board="gbautomation") as conn:
        task = _kb.get_task(conn, task_id)
        assert task is not None
        assert task.status == "blocked"


@pytest.mark.asyncio
async def test_kanban_callback_unauthorized_fails_before_db_lookup(monkeypatch):
    adapter = _make_adapter()
    query = _make_query("kb:ub:abc:sig")
    adapter._is_callback_user_authorized = lambda *a, **k: False
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("DB callback lookup should not run for unauthorized users")

    monkeypatch.setattr(adapter, "_resolve_kanban_callback_action", fail_if_called)

    await adapter._handle_kanban_proposal_callback(
        query,
        "kb:ub:abc:sig",
        query_chat_id=12345,
        query_chat_type="private",
        query_thread_id=None,
        query_user_name="Tester",
    )

    assert called is False
    query.answer.assert_awaited_once_with(text="⛔ You are not authorized to act on this Kanban task.")


def test_no_openclaw_runtime_path_in_telegram_adapter():
    source = Path("gateway/platforms/telegram.py").read_text()
    assert "/Users/greg/.openclaw" not in source
