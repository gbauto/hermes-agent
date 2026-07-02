"""Bot API 10.x agent-runtime event router tests (Phase 2.3 fixture gate).

Covers, against representative Bot API 10.x update JSON fixtures
(``tests/gateway/fixtures/telegram_bot_api_10_updates.json``):

* flags-off (the default) ⇒ every 10.x update is ignored safely — no crash,
  no envelope emitted;
* flags-on ⇒ the correct typed envelope reaches the routing seam;
* unknown update fields are logged (key names only) and ignored;
* bot-to-bot loop guards trip on message-id dedupe and max depth;
* managed-bot token material never appears in any persisted record;
* ``allowed_updates`` widening is opt-in and byte-identical to
  ``Update.ALL_TYPES`` when unset;
* the legacy short ``guest_mode`` key (mention-gate bypass) does NOT enable
  the Bot API 10.0 guest surface (naming-collision hazard).
"""

import asyncio
import copy
import json
import logging
import os
from collections import deque
from pathlib import Path

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.telegram_agent_runtime import (
    DEFAULT_BOT_TO_BOT_DEDUPE_CACHE_SIZE,
    DEFAULT_BOT_TO_BOT_MAX_DEPTH,
    TelegramAgentEventKind,
    TelegramAgentFeatureFlags,
    TelegramBotToBotGuard,
    normalize_update,
    route_event,
    sanitize_update_excerpt,
)

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "telegram_bot_api_10_updates.json").read_text(
        encoding="utf-8"
    )
)

ALL_FLAG_KEYS = (
    "telegram_guest_mode_enabled",
    "telegram_bot_to_bot_enabled",
    "telegram_rich_messages_enabled",
    "telegram_managed_bots_enabled",
    "telegram_guardian_enabled",
)


class _FakeUpdate:
    """Stands in for telegram.Update — the adapter only calls to_dict()."""

    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return copy.deepcopy(self._payload)


def _make_adapter(extra=None, guard=None):
    from gateway.platforms.telegram import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(enabled=True, token="***", extra=dict(extra or {}))
    adapter._agent_runtime_flags = TelegramAgentFeatureFlags.from_extra_and_env(
        adapter.config.extra
    )
    adapter._bot_to_bot_guard = guard or TelegramBotToBotGuard()
    adapter._agent_runtime_events = deque(maxlen=64)
    adapter._agent_runtime_router = None
    return adapter


def _run(adapter, payload):
    asyncio.run(adapter._handle_agent_runtime_update(_FakeUpdate(payload)))


# ── Flags off (default): ignored safely ─────────────────────────────────────


def test_flags_default_off_ignores_all_10x_fixtures():
    adapter = _make_adapter()
    assert not adapter._agent_runtime_flags.any_agent_surface_enabled
    for name, payload in FIXTURES.items():
        if name.startswith("_"):
            continue
        _run(adapter, payload)  # must not raise
    assert list(adapter._agent_runtime_events) == []


def test_legacy_guest_mode_key_does_not_enable_guest_surface():
    """The short `guest_mode` key is the mention-gate bypass, not 10.0 guests."""
    adapter = _make_adapter(extra={"guest_mode": True})
    assert adapter._agent_runtime_flags.guest_mode is False
    _run(adapter, FIXTURES["guest_query"])
    assert list(adapter._agent_runtime_events) == []


def test_feature_flags_reject_short_aliases():
    flags = TelegramAgentFeatureFlags.from_mapping(
        {
            "guest_mode": True,
            "bot_to_bot": True,
            "rich_messages": True,
            "managed_bots": True,
            "join_request_guardian": True,
        }
    )
    assert not flags.any_agent_surface_enabled

    flags = TelegramAgentFeatureFlags.from_mapping(
        {key: True for key in ALL_FLAG_KEYS}
    )
    assert flags.guest_mode and flags.bot_to_bot and flags.rich_messages
    assert flags.managed_bots and flags.join_request_guardian


def test_flags_default_false_from_env(monkeypatch):
    for _, env_name in TelegramAgentFeatureFlags.ENV_SOURCES:
        monkeypatch.delenv(env_name, raising=False)
    flags = TelegramAgentFeatureFlags.from_extra_and_env({})
    assert not flags.any_agent_surface_enabled
    monkeypatch.setenv("TELEGRAM_GUARDIAN_ENABLED", "true")
    flags = TelegramAgentFeatureFlags.from_extra_and_env({})
    assert flags.join_request_guardian is True
    assert not flags.guest_mode


# ── Flags on: correct envelope reaches the routing seam ─────────────────────


def test_guest_flag_on_emits_guest_envelope():
    adapter = _make_adapter(extra={"telegram_guest_mode_enabled": True})
    _run(adapter, FIXTURES["guest_query"])
    assert len(adapter._agent_runtime_events) == 1
    record = adapter._agent_runtime_events[0]
    assert record["kind"] == "guest_query"
    assert record["action"] == "answer_guest_query"
    assert record["event"]["guest_query_id"] == "gq-77f1"
    assert record["event"]["guest_caller_user_id"] == "777001"
    assert record["event"]["guest_caller_chat_id"] == "-1002000000042"
    # Other surfaces stay gated with only the guest flag on.
    _run(adapter, FIXTURES["join_request_query"])
    _run(adapter, FIXTURES["managed_bot_event"])
    assert len(adapter._agent_runtime_events) == 1


def test_bot_to_bot_flag_on_emits_envelope_with_kanban_dispatch():
    adapter = _make_adapter(extra={"telegram_bot_to_bot_enabled": True})
    _run(adapter, FIXTURES["bot_to_bot"])
    assert len(adapter._agent_runtime_events) == 1
    record = adapter._agent_runtime_events[0]
    assert record["kind"] == "bot_to_bot"
    assert record["action"] == "handle_bot_to_bot"
    assert record["event"]["from_is_bot"] is True
    assert record["should_dispatch_kanban"] is True


def test_rich_message_callback_envelope():
    adapter = _make_adapter(extra={"telegram_rich_messages_enabled": True})
    _run(adapter, FIXTURES["rich_message_callback"])
    assert len(adapter._agent_runtime_events) == 1
    record = adapter._agent_runtime_events[0]
    assert record["kind"] == "rich_message"
    assert record["action"] == "handle_rich_message"
    assert record["event"]["callback_query_id"] == "cbq-31337"
    # Plain (classic) callback queries are silently left to the classic
    # CallbackQueryHandler — no envelope, no unknown-field log.
    _run(
        adapter,
        {
            "update_id": 910099,
            "callback_query": {"id": "cbq-classic", "data": "update_yes", "from": {"id": 1}},
        },
    )
    assert len(adapter._agent_runtime_events) == 1


def test_join_request_guardian_envelope():
    adapter = _make_adapter(extra={"telegram_guardian_enabled": True})
    _run(adapter, FIXTURES["join_request_query"])
    assert len(adapter._agent_runtime_events) == 1
    record = adapter._agent_runtime_events[0]
    assert record["kind"] == "join_request_query"
    assert record["action"] == "answer_join_request_query"
    assert record["event"]["join_request_query_id"] == "jrq-9"


def test_managed_bot_envelope_never_carries_token():
    adapter = _make_adapter(extra={"telegram_managed_bots_enabled": True})
    _run(adapter, FIXTURES["managed_bot_event"])
    assert len(adapter._agent_runtime_events) == 1
    record = adapter._agent_runtime_events[0]
    assert record["kind"] == "managed_bot_event"
    assert record["event"]["managed_bot_user_id"] == "888001"
    # The token VALUE must never appear anywhere in the persisted record
    # (the key name may appear in the excerpt's dropped_keys audit list).
    assert "CANARY-NEVER-PERSIST" not in json.dumps(record)


def test_sanitized_excerpt_drops_secrets_and_rosters():
    excerpt = sanitize_update_excerpt(FIXTURES["managed_bot_event"])
    dumped = json.dumps(excerpt)
    assert "CANARY-NEVER-PERSIST" not in dumped
    excerpt = sanitize_update_excerpt(FIXTURES["join_request_query"])
    dumped = json.dumps(excerpt)
    assert "SECRET-ROSTER-LINK" not in dumped
    # Roster-ish payloads are dropped even when nested under allowed containers.
    excerpt = sanitize_update_excerpt(
        {
            "update_id": 1,
            "message": {
                "message_id": 2,
                "chat": {"id": 3, "type": "group", "members": [{"id": 4}, {"id": 5}]},
                "text": "x" * 5000,
            },
        }
    )
    assert excerpt["message"]["chat"].get("members") is None
    assert "members" in excerpt["message"]["chat"].get("dropped_keys", [])
    assert len(excerpt["message"]["text"]) <= 256


# ── Unknown fields: logged and ignored ──────────────────────────────────────


def test_unknown_top_level_update_logged_and_ignored(caplog):
    adapter = _make_adapter(extra={"telegram_guest_mode_enabled": True})
    with caplog.at_level(logging.INFO, logger="gateway.platforms.telegram"):
        _run(adapter, FIXTURES["unknown_top_level"])
    assert list(adapter._agent_runtime_events) == []
    messages = [r.getMessage() for r in caplog.records]
    assert any("unknown fields" in m and "hypothetical_10_2_surface" in m for m in messages)
    # Key names only — payload values must never be logged.
    assert not any("mystery-1" in m for m in messages)


def test_classic_message_with_unknown_fields_left_to_classic_pipeline():
    adapter = _make_adapter(extra={"telegram_guest_mode_enabled": True})
    _run(adapter, FIXTURES["classic_message_with_unknown_fields"])
    assert list(adapter._agent_runtime_events) == []  # classic handlers own it


def test_normalize_merges_ptb_api_kwargs_shape():
    """PTB 22.6 stores unmodeled fields in api_kwargs; normalize folds them in."""
    payload = {"update_id": 910001, "api_kwargs": {"guest_message": FIXTURES["guest_query"]["guest_message"]}}
    event = normalize_update(payload)
    assert event.kind == TelegramAgentEventKind.GUEST_QUERY
    assert event.guest_query_id == "gq-77f1"


# ── Loop-prevention primitives (plan 3.1, gateway part) ─────────────────────


def test_bot_to_bot_dedupe_guard_trips_on_duplicate():
    adapter = _make_adapter(extra={"telegram_bot_to_bot_enabled": True})
    _run(adapter, FIXTURES["bot_to_bot"])
    _run(adapter, FIXTURES["bot_to_bot"])  # identical update re-delivered
    assert len(adapter._agent_runtime_events) == 1


def test_bot_to_bot_max_depth_guard_trips():
    guard = TelegramBotToBotGuard(max_depth=2, chain_window_seconds=3600)
    adapter = _make_adapter(extra={"telegram_bot_to_bot_enabled": True}, guard=guard)
    for i in range(4):
        payload = copy.deepcopy(FIXTURES["bot_to_bot"])
        payload["update_id"] += i
        payload["message"]["message_id"] += i
        _run(adapter, payload)
    # depth 1 and 2 pass, 3 and 4 are guarded off
    assert len(adapter._agent_runtime_events) == 2


def test_bot_to_bot_chain_resets_on_human_activity():
    guard = TelegramBotToBotGuard(max_depth=1, chain_window_seconds=3600)
    adapter = _make_adapter(extra={"telegram_bot_to_bot_enabled": True}, guard=guard)
    first = copy.deepcopy(FIXTURES["bot_to_bot"])
    _run(adapter, first)
    assert len(adapter._agent_runtime_events) == 1
    # A human message in the same chat resets the chain...
    human = {
        "update_id": 910050,
        "message": {
            "message_id": 900,
            "chat": first["message"]["chat"],
            "from": {"id": 777001, "is_bot": False},
            "text": "human speaking",
        },
    }
    _run(adapter, human)
    second = copy.deepcopy(first)
    second["update_id"] += 1
    second["message"]["message_id"] += 1
    _run(adapter, second)
    assert len(adapter._agent_runtime_events) == 2


def test_guard_defaults_and_env_override(monkeypatch):
    guard = TelegramBotToBotGuard()
    assert guard.max_depth == DEFAULT_BOT_TO_BOT_MAX_DEPTH
    assert guard.dedupe_cache_size == DEFAULT_BOT_TO_BOT_DEDUPE_CACHE_SIZE
    monkeypatch.setenv("TELEGRAM_BOT_TO_BOT_MAX_DEPTH", "7")
    monkeypatch.setenv("TELEGRAM_BOT_TO_BOT_DEDUPE_CACHE_SIZE", "9")
    guard = TelegramBotToBotGuard()
    assert guard.max_depth == 7
    assert guard.dedupe_cache_size == 9
    monkeypatch.setenv("TELEGRAM_BOT_TO_BOT_MAX_DEPTH", "not-a-number")
    guard = TelegramBotToBotGuard()
    assert guard.max_depth == DEFAULT_BOT_TO_BOT_MAX_DEPTH


# ── allowed_updates widening (opt-in; default byte-identical) ────────────────


def test_agent_allowed_updates_default_is_all_types(monkeypatch):
    import gateway.platforms.telegram as tg_mod

    monkeypatch.delenv("TELEGRAM_EXTRA_ALLOWED_UPDATES", raising=False)
    sentinel = ["message", "edited_message", "callback_query"]
    monkeypatch.setattr(tg_mod.Update, "ALL_TYPES", sentinel, raising=False)
    adapter = _make_adapter()
    result = adapter._agent_allowed_updates()
    assert result is sentinel  # byte-identical default: same object PTB got before


def test_agent_allowed_updates_widens_with_raw_strings(monkeypatch):
    import gateway.platforms.telegram as tg_mod

    monkeypatch.setattr(
        tg_mod.Update, "ALL_TYPES", ["message", "callback_query"], raising=False
    )
    adapter = _make_adapter(
        extra={"extra_allowed_updates": ["guest_message", "managed_bot", "message"]}
    )
    result = adapter._agent_allowed_updates()
    assert result == ["message", "callback_query", "guest_message", "managed_bot"]
    # env-var form (comma separated) works too
    monkeypatch.setenv("TELEGRAM_EXTRA_ALLOWED_UPDATES", "guest_message , ,managed_bot")
    adapter = _make_adapter()
    result = adapter._agent_allowed_updates()
    assert result == ["message", "callback_query", "guest_message", "managed_bot"]


# ── Routing seam ─────────────────────────────────────────────────────────────


def test_injected_router_receives_record_and_errors_are_contained():
    adapter = _make_adapter(extra={"telegram_guardian_enabled": True})
    received = []

    async def router(record):
        received.append(record)
        raise RuntimeError("router boom")  # must be contained

    adapter._agent_runtime_router = router
    _run(adapter, FIXTURES["join_request_query"])
    assert len(received) == 1
    assert received[0]["kind"] == "join_request_query"
    assert len(adapter._agent_runtime_events) == 1


def test_route_event_matrix_flags_off_conservative():
    for name, expected_kind in (
        ("guest_query", TelegramAgentEventKind.GUEST_QUERY),
        ("bot_to_bot", TelegramAgentEventKind.BOT_TO_BOT),
        ("rich_message_callback", TelegramAgentEventKind.RICH_MESSAGE),
        ("managed_bot_event", TelegramAgentEventKind.MANAGED_BOT_EVENT),
        ("join_request_query", TelegramAgentEventKind.JOIN_REQUEST_QUERY),
    ):
        event = normalize_update(FIXTURES[name])
        assert event.kind == expected_kind, name
        route = route_event(event, None)
        assert route.allowed is False, name


# ── PTB 22.6 de_json tolerance (proven in-repo when real PTB is present) ─────


def _real_ptb_available():
    try:
        import telegram  # noqa: F401

        return hasattr(telegram, "__file__") and hasattr(telegram.Update, "de_json")
    except Exception:
        return False


# ── Profile YAML → env/extra bridge (gateway/config.py) ─────────────────────


def test_config_bridges_fully_qualified_flags_and_extra_allowed_updates(tmp_path, monkeypatch):
    from gateway.config import load_gateway_config

    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "telegram:\n"
        "  telegram_guest_mode_enabled: true\n"
        "  telegram_guardian_enabled: true\n"
        "  extra_allowed_updates:\n"
        "    - guest_message\n"
        "    - managed_bot\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    for _, env_name in TelegramAgentFeatureFlags.ENV_SOURCES:
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.delenv("TELEGRAM_EXTRA_ALLOWED_UPDATES", raising=False)

    config = load_gateway_config()

    assert os.environ.get("TELEGRAM_GUEST_MODE_ENABLED") == "true"
    assert os.environ.get("TELEGRAM_GUARDIAN_ENABLED") == "true"
    assert os.environ.get("TELEGRAM_BOT_TO_BOT_ENABLED") is None
    assert os.environ.get("TELEGRAM_EXTRA_ALLOWED_UPDATES") == "guest_message,managed_bot"
    extra = config.platforms[Platform.TELEGRAM].extra
    assert extra["telegram_guest_mode_enabled"] is True
    assert extra["extra_allowed_updates"] == ["guest_message", "managed_bot"]
    # The unrelated legacy mention-gate bypass must NOT be flipped on.
    assert os.environ.get("TELEGRAM_GUEST_MODE") is None

    flags = TelegramAgentFeatureFlags.from_extra_and_env(extra)
    assert flags.guest_mode and flags.join_request_guardian
    assert not (flags.bot_to_bot or flags.rich_messages or flags.managed_bots)


def test_config_default_yaml_sets_no_agent_flag_env(tmp_path, monkeypatch):
    from gateway.config import load_gateway_config

    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("telegram:\n  guest_mode: true\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    for _, env_name in TelegramAgentFeatureFlags.ENV_SOURCES:
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.delenv("TELEGRAM_EXTRA_ALLOWED_UPDATES", raising=False)
    monkeypatch.delenv("TELEGRAM_GUEST_MODE", raising=False)

    load_gateway_config()

    # Legacy short key bridges only to its legacy env var...
    assert os.environ.get("TELEGRAM_GUEST_MODE") == "true"
    # ...and never to any Bot API 10.x agent-surface flag.
    for _, env_name in TelegramAgentFeatureFlags.ENV_SOURCES:
        assert os.environ.get(env_name) is None
    assert os.environ.get("TELEGRAM_EXTRA_ALLOWED_UPDATES") is None


@pytest.mark.skipif(not _real_ptb_available(), reason="real python-telegram-bot not installed")
def test_ptb_de_json_tolerates_unknown_10x_fields():
    """PTB 22.6 must sort unknown top-level update fields into api_kwargs."""
    from telegram import Update

    update = Update.de_json(copy.deepcopy(FIXTURES["unknown_top_level"]), None)
    assert update.update_id == 910006
    assert "hypothetical_10_2_surface" in dict(update.api_kwargs)
    # and to_dict() round-trips the unmodeled field for the normalizer
    assert "hypothetical_10_2_surface" in update.to_dict()
