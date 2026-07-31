from types import SimpleNamespace
from unittest.mock import AsyncMock

from cron.scheduler import _send_media_via_adapter
from gateway.config import Platform
from gateway.platforms.base import SendResult


def test_cron_live_adapter_filters_telegram_source_artifacts(monkeypatch, tmp_path):
    json_path = tmp_path / "data.json"
    json_path.write_text('{"raw": true}')
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nbody")

    adapter = SimpleNamespace(
        platform=Platform.TELEGRAM,
        send_document=AsyncMock(return_value=SendResult(success=True, message_id="1")),
        send_image_file=AsyncMock(return_value=SendResult(success=True, message_id="2")),
        send_video=AsyncMock(return_value=SendResult(success=True, message_id="3")),
        send_voice=AsyncMock(return_value=SendResult(success=True, message_id="4")),
    )

    def fake_schedule(coro, loop):
        coro.close()
        return SimpleNamespace(result=lambda timeout=None: SendResult(success=True, message_id="ok"))

    monkeypatch.setattr("agent.async_utils.safe_schedule_threadsafe", fake_schedule)

    _send_media_via_adapter(
        adapter,
        chat_id="123",
        media_files=[(str(json_path), False), (str(pdf_path), False)],
        metadata=None,
        loop=SimpleNamespace(),
        job={"id": "job1"},
        platform=Platform.TELEGRAM,
    )

    assert adapter.send_document.call_count == 1
    assert adapter.send_document.call_args.kwargs["file_path"] == str(pdf_path)
