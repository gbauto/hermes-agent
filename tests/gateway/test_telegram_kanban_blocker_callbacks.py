import asyncio
from types import SimpleNamespace

from gateway.platforms.telegram import TelegramAdapter
from hermes_cli import kanban_db as kb


class FakeQuery:
    def __init__(self, data, user_id="111"):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id, first_name="Tester")
        self.message = SimpleNamespace(chat_id="123", message_thread_id=None, chat=SimpleNamespace(type="private"))
        self.answers = []
        self.edits = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)

    async def edit_message_text(self, text, **kwargs):
        self.edits.append({"text": text, **kwargs})


def _adapter(authorized=True):
    adapter = TelegramAdapter.__new__(TelegramAdapter)
    adapter._is_callback_user_authorized = lambda *a, **kw: authorized
    adapter.format_message = lambda text: text
    return adapter


def test_kanban_blocker_callback_rejects_unauthorized_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "acl.db"))
    kb.init_db()
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="blocked task", assignee="worker")
        assert kb.block_task(conn, tid, reason="human needed")
    finally:
        conn.close()

    query = FakeQuery(f"kbb:u:default:{tid}", user_id="999")
    asyncio.run(_adapter(authorized=False)._handle_kanban_blocker_callback(query, query.data))

    conn = kb.connect()
    try:
        assert kb.get_task(conn, tid).status == "blocked"
    finally:
        conn.close()
    assert query.answers == ["⛔ You are not authorized to manage Kanban blockers."]


def test_kanban_blocker_callback_unblocks_authorized_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "unblock.db"))
    kb.init_db()
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="blocked task", assignee="worker")
        assert kb.block_task(conn, tid, reason="human needed")
    finally:
        conn.close()

    query = FakeQuery(f"kbb:u:default:{tid}")
    asyncio.run(_adapter(authorized=True)._handle_kanban_blocker_callback(query, query.data))

    conn = kb.connect()
    try:
        assert kb.get_task(conn, tid).status == "ready"
    finally:
        conn.close()
    assert query.answers == ["✅ Kanban task unblocked."]
    assert "unblocked" in query.edits[0]["text"].lower()
