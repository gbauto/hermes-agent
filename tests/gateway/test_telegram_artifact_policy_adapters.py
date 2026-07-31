"""Outbound Telegram artifact allowlist adapter regressions."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gateway.config import PlatformConfig
from gateway.platforms.telegram import TelegramAdapter
from tools.send_message_tool import _send_telegram


class _DummyBot:
    def __init__(self):
        self.send_document = AsyncMock(return_value=SimpleNamespace(message_id=10))
        self.send_photo = AsyncMock(return_value=SimpleNamespace(message_id=11))
        self.send_message = AsyncMock(return_value=SimpleNamespace(message_id=12))


def test_telegram_adapter_send_document_denies_markdown_without_upload(tmp_path):
    path = tmp_path / "receipt.md"
    path.write_text("# internal receipt")
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="token"))
    adapter._bot = _DummyBot()

    result = asyncio.run(adapter.send_document("123", str(path)))

    assert not result.success
    assert "Artifact omitted" in str(result.error)
    adapter._bot.send_document.assert_not_called()


def test_telegram_adapter_send_document_allows_pdf(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.7\nbody")
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="token"))
    adapter._bot = _DummyBot()
    adapter._send_with_dm_topic_reply_anchor_retry = AsyncMock(return_value=SimpleNamespace(message_id=99))

    result = asyncio.run(adapter.send_document("123", str(path)))

    assert result.success
    assert result.message_id == "99"
    assert adapter._send_with_dm_topic_reply_anchor_retry.await_count == 1


def test_telegram_adapter_send_document_denies_disguised_pdf_without_upload(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"PK\x03\x04zip bytes in a pdf costume")
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="token"))
    adapter._bot = _DummyBot()
    adapter._send_with_dm_topic_reply_anchor_retry = AsyncMock(return_value=SimpleNamespace(message_id=99))

    result = asyncio.run(adapter.send_document("123", str(path)))

    assert not result.success
    assert "Artifact omitted" in str(result.error)
    adapter._send_with_dm_topic_reply_anchor_retry.assert_not_called()
    adapter._bot.send_document.assert_not_called()


def test_standalone_send_telegram_denies_json_media_without_document_upload(monkeypatch, tmp_path):
    path = tmp_path / "data.json"
    path.write_text('{"raw": true}')
    bot = _DummyBot()

    class BotFactory:
        def __new__(cls, token, **kwargs):
            return bot

    monkeypatch.setattr("telegram.Bot", BotFactory, raising=False)

    result = asyncio.run(_send_telegram("token", "123", f"Here MEDIA:{path}", media_files=[(str(path), False)]))

    assert result["success"] is True
    assert any("Artifact omitted" in warning for warning in result.get("warnings", []))
    bot.send_document.assert_not_called()
    bot.send_message.assert_awaited()
    sent_text = bot.send_message.call_args.kwargs["text"]
    assert str(path) not in sent_text
