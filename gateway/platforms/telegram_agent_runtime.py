"""Telegram Bot API 10.x agent-surface event envelopes and routing seam.

This module is intentionally network-free and PTB-free. The Telegram adapter
feeds raw update dicts (``telegram.Update.to_dict()`` output, which includes
``api_kwargs`` entries for fields PTB 22.6 does not model) into
:func:`normalize_update` and receives a typed event envelope plus a routing
decision from :func:`route_event`.

Envelope shapes are name-compatible with the pre-existing monorepo layer
(``gbautomation/resources/lib/telegram_agent_runtime.py``): the class names
``TelegramAgentEventKind`` / ``TelegramAgentEvent`` / ``TelegramAgentRoute`` /
``TelegramAgentFeatureFlags`` and the functions ``normalize_update`` /
``route_event`` keep the same signatures and field names so fixtures and
smoke tooling written against that layer keep working here.

Differences from the monorepo layer (deliberate, per the 2026-07-02 gateway
discovery receipt):

* ``TelegramAgentFeatureFlags.from_mapping`` accepts ONLY the fully-qualified
  flag spellings (``telegram_guest_mode_enabled`` etc.). The short aliases
  were dropped because the short key ``guest_mode`` is already an unrelated
  Telegram adapter option (mention-gate bypass for non-allowlisted groups —
  ``gateway/config.py`` bridges it to ``TELEGRAM_GUEST_MODE`` and
  ``TelegramAdapter._telegram_guest_mode`` consumes it). Reading short keys
  here would let that legacy option silently enable the Bot API 10.0 guest
  surface.
* Adds :func:`sanitize_update_excerpt` — the only shape of raw-update data
  that may be persisted or logged (never tokens, never chat rosters).
* Adds :class:`TelegramBotToBotGuard` — max-depth + message-id dedupe loop
  prevention enforced at the envelope layer before routing (plan 3.1).
"""

from __future__ import annotations

import html
import os
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Mapping

__all__ = [
    "TelegramAgentEventKind",
    "TelegramAgentFeatureFlags",
    "TelegramAgentEvent",
    "TelegramAgentRoute",
    "TelegramBotToBotGuard",
    "normalize_update",
    "route_event",
    "render_plain_text_fallback",
    "sanitize_update_excerpt",
    "DEFAULT_BOT_TO_BOT_MAX_DEPTH",
    "DEFAULT_BOT_TO_BOT_DEDUPE_CACHE_SIZE",
    "DEFAULT_BOT_TO_BOT_CHAIN_WINDOW_SECONDS",
    "CLASSIC_UPDATE_KEYS",
]


class TelegramAgentEventKind(str, Enum):
    MESSAGE = "message"
    VOICE_NOTE = "voice_note"
    GUEST_QUERY = "guest_query"
    BOT_TO_BOT = "bot_to_bot"
    RICH_MESSAGE = "rich_message"
    MANAGED_BOT_EVENT = "managed_bot_event"
    JOIN_REQUEST_QUERY = "join_request_query"
    UNKNOWN = "unknown"


# Top-level update keys that the classic (pre-10.x) gateway pipeline already
# owns via its registered PTB handlers. Updates whose keys are a subset of
# these are never the agent-runtime seam's business.
CLASSIC_UPDATE_KEYS: frozenset[str] = frozenset(
    {
        "update_id",
        "message",
        "edited_message",
        "channel_post",
        "edited_channel_post",
        "callback_query",
    }
)


@dataclass(frozen=True)
class TelegramAgentFeatureFlags:
    """Feature gates for Telegram Bot API 10.x agent surfaces.

    Defaults are conservative: classic messages remain enabled, every new
    agent surface is opt-in (default off) per profile.
    """

    guest_mode: bool = False
    bot_to_bot: bool = False
    rich_messages: bool = False
    managed_bots: bool = False
    join_request_guardian: bool = False
    allow_classic_messages: bool = True

    @property
    def any_agent_surface_enabled(self) -> bool:
        return (
            self.guest_mode
            or self.bot_to_bot
            or self.rich_messages
            or self.managed_bots
            or self.join_request_guardian
        )

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "TelegramAgentFeatureFlags":
        """Build flags from a profile-config mapping.

        ⚠️ Naming-collision hazard (discovery receipt §4): ONLY the
        fully-qualified spellings are read. The short key ``guest_mode`` is
        already occupied by the legacy mention-gate bypass
        (``TELEGRAM_GUEST_MODE`` / ``TelegramAdapter._telegram_guest_mode``)
        and MUST NOT gate the Bot API 10.0 guest surface. Do not add short
        aliases (``guest_mode``, ``bot_to_bot``, ...) here.
        """
        if not values:
            return cls()

        def enabled(name: str, *, default: bool = False) -> bool:
            if name in values:
                return _as_bool(values[name])
            return default

        return cls(
            guest_mode=enabled("telegram_guest_mode_enabled"),
            bot_to_bot=enabled("telegram_bot_to_bot_enabled"),
            rich_messages=enabled("telegram_rich_messages_enabled"),
            managed_bots=enabled("telegram_managed_bots_enabled"),
            join_request_guardian=enabled("telegram_guardian_enabled"),
            allow_classic_messages=enabled("allow_classic_messages", default=True),
        )

    # Fully-qualified profile key → env var bridged by gateway/config.py.
    # ClassVar so the dataclass machinery does not treat it as a field.
    ENV_SOURCES: ClassVar[tuple[tuple[str, str], ...]] = (
        ("telegram_guest_mode_enabled", "TELEGRAM_GUEST_MODE_ENABLED"),
        ("telegram_bot_to_bot_enabled", "TELEGRAM_BOT_TO_BOT_ENABLED"),
        ("telegram_rich_messages_enabled", "TELEGRAM_RICH_MESSAGES_ENABLED"),
        ("telegram_managed_bots_enabled", "TELEGRAM_MANAGED_BOTS_ENABLED"),
        ("telegram_guardian_enabled", "TELEGRAM_GUARDIAN_ENABLED"),
    )

    @classmethod
    def from_extra_and_env(cls, extra: Mapping[str, Any] | None) -> "TelegramAgentFeatureFlags":
        """Resolve flags from platform ``extra`` config first, env second.

        Matches the adapter-wide precedence used for every other
        ``telegram:`` profile key (config value wins over env var default).
        """
        mapping: dict[str, Any] = {}
        extra = extra or {}
        for key, env_name in cls.ENV_SOURCES:
            value = extra.get(key)
            if value is None:
                value = os.getenv(env_name)
            if value is not None:
                mapping[key] = value
        return cls.from_mapping(mapping)


@dataclass(frozen=True)
class TelegramAgentEvent:
    kind: TelegramAgentEventKind
    update_id: int | None
    message_id: int | None = None
    chat_id: str | None = None
    chat_type: str | None = None
    from_user_id: str | None = None
    from_is_bot: bool = False
    text: str = ""
    reply_to_message_id: int | None = None
    guest_query_id: str | None = None
    guest_caller_user_id: str | None = None
    guest_caller_chat_id: str | None = None
    managed_bot_user_id: str | None = None
    join_request_query_id: str | None = None
    callback_query_id: str | None = None
    has_rich_message: bool = False
    has_voice_note: bool = False
    voice_file_ids: tuple[str, ...] = field(default_factory=tuple)
    raw_keys: tuple[str, ...] = field(default_factory=tuple)

    @property
    def dedupe_key(self) -> str:
        parts = [
            self.kind.value,
            str(self.update_id or ""),
            str(self.chat_id or ""),
            str(
                self.message_id
                or self.guest_query_id
                or self.join_request_query_id
                or self.callback_query_id
                or ""
            ),
        ]
        return ":".join(parts)


@dataclass(frozen=True)
class TelegramAgentRoute:
    event: TelegramAgentEvent
    allowed: bool
    action: str
    reason: str
    should_create_receipt: bool = False
    should_dispatch_kanban: bool = False


# ── Loop-prevention primitives (plan 3.1, gateway part) ─────────────────────
#
# Defaults are constants so tests and operators can reason about them;
# both are config-overridable via env (bridged from the profile YAML by
# gateway/config.py) or constructor args.

DEFAULT_BOT_TO_BOT_MAX_DEPTH = 4
DEFAULT_BOT_TO_BOT_DEDUPE_CACHE_SIZE = 512
DEFAULT_BOT_TO_BOT_CHAIN_WINDOW_SECONDS = 900.0


class TelegramBotToBotGuard:
    """Max-depth + message-id dedupe guards for bot-to-bot events.

    Enforced at the envelope layer BEFORE routing, so no bot-originated
    event can recurse regardless of what downstream routing does. This is
    deliberately independent of the runner auth gate: bot-origin senders
    remain auth-denied by default there (``platform_allow_bots_map`` has no
    Telegram entry), and this guard must land before any such bypass does.

    * **Dedupe**: each event's ``dedupe_key`` may pass at most once
      (bounded FIFO cache, ``dedupe_cache_size`` entries).
    * **Max depth**: at most ``max_depth`` consecutive bot-to-bot events per
      chat within ``chain_window_seconds``. Any non-bot activity in the chat
      (reported via :meth:`note_non_bot_activity`) resets the chain, as does
      window expiry.
    """

    def __init__(
        self,
        max_depth: int | None = None,
        dedupe_cache_size: int | None = None,
        chain_window_seconds: float | None = None,
    ) -> None:
        self.max_depth = _positive_int(
            max_depth
            if max_depth is not None
            else os.getenv("TELEGRAM_BOT_TO_BOT_MAX_DEPTH"),
            DEFAULT_BOT_TO_BOT_MAX_DEPTH,
        )
        self.dedupe_cache_size = _positive_int(
            dedupe_cache_size
            if dedupe_cache_size is not None
            else os.getenv("TELEGRAM_BOT_TO_BOT_DEDUPE_CACHE_SIZE"),
            DEFAULT_BOT_TO_BOT_DEDUPE_CACHE_SIZE,
        )
        self.chain_window_seconds = float(
            chain_window_seconds
            if chain_window_seconds is not None
            else _positive_float(
                os.getenv("TELEGRAM_BOT_TO_BOT_CHAIN_WINDOW_SECONDS"),
                DEFAULT_BOT_TO_BOT_CHAIN_WINDOW_SECONDS,
            )
        )
        self._seen: OrderedDict[str, None] = OrderedDict()
        # chat_id -> (consecutive bot-to-bot count, last event monotonic ts)
        self._chains: dict[str, tuple[int, float]] = {}

    def check(self, event: TelegramAgentEvent, *, now: float | None = None) -> tuple[bool, str]:
        """Return ``(allowed, reason)`` for a bot-to-bot event."""
        now = time.monotonic() if now is None else now
        key = event.dedupe_key
        if key in self._seen:
            return False, "duplicate_message_id"
        self._seen[key] = None
        while len(self._seen) > self.dedupe_cache_size:
            self._seen.popitem(last=False)

        chat_id = str(event.chat_id or "")
        depth, last_ts = self._chains.get(chat_id, (0, now))
        if now - last_ts > self.chain_window_seconds:
            depth = 0
        depth += 1
        self._chains[chat_id] = (depth, now)
        if depth > self.max_depth:
            return False, f"max_depth_exceeded:{depth}>{self.max_depth}"
        return True, "ok"

    def note_non_bot_activity(self, chat_id: str | None) -> None:
        """Human (or non-bot) activity in a chat resets its bot-to-bot chain."""
        if chat_id is None:
            return
        self._chains.pop(str(chat_id), None)


def normalize_update(update: Mapping[str, Any]) -> TelegramAgentEvent:
    """Normalize a raw Telegram update dict into a stable event envelope.

    Accepts ``telegram.Update.to_dict()`` output — PTB 22.6 stores fields it
    does not model in ``api_kwargs`` (verified against PTB v22.6 source,
    ``TelegramObject._de_json``), and ``to_dict()`` merges those entries back
    into the top level, so Bot API 10.x payloads arrive here as plain keys.
    """
    update = _merge_api_kwargs(update)
    update_id = _optional_int(update.get("update_id"))
    raw_keys = tuple(sorted(str(key) for key in update.keys()))

    if isinstance(update.get("guest_message"), Mapping):
        message = update["guest_message"]
        return _event_from_message(
            TelegramAgentEventKind.GUEST_QUERY,
            update_id,
            message,
            raw_keys=raw_keys,
        )

    if isinstance(update.get("chat_join_request"), Mapping):
        join = update["chat_join_request"]
        user = join.get("from") if isinstance(join.get("from"), Mapping) else {}
        chat = join.get("chat") if isinstance(join.get("chat"), Mapping) else {}
        return TelegramAgentEvent(
            kind=TelegramAgentEventKind.JOIN_REQUEST_QUERY,
            update_id=update_id,
            chat_id=_str_or_none(chat.get("id")),
            chat_type=_str_or_none(chat.get("type")),
            from_user_id=_str_or_none(user.get("id")),
            from_is_bot=bool(user.get("is_bot")),
            join_request_query_id=_str_or_none(join.get("query_id")),
            raw_keys=raw_keys,
        )

    managed = _managed_bot_payload(update)
    if managed is not None:
        # Token fields are deliberately never carried into the envelope —
        # managed-bot token material is secret-store-only (plan §8).
        user_id = None
        bot = managed.get("bot") if isinstance(managed.get("bot"), Mapping) else {}
        if bot:
            user_id = _str_or_none(bot.get("id"))
        return TelegramAgentEvent(
            kind=TelegramAgentEventKind.MANAGED_BOT_EVENT,
            update_id=update_id,
            managed_bot_user_id=user_id,
            raw_keys=raw_keys,
        )

    callback = update.get("callback_query")
    if isinstance(callback, Mapping):
        cb_message = callback.get("message") if isinstance(callback.get("message"), Mapping) else {}
        has_rich = isinstance(cb_message.get("rich_message"), Mapping) or isinstance(
            callback.get("rich_message"), Mapping
        )
        if has_rich:
            event = _event_from_message(
                TelegramAgentEventKind.RICH_MESSAGE,
                update_id,
                cb_message,
                raw_keys=raw_keys,
            )
            return _with_callback_query_id(event, _str_or_none(callback.get("id")))
        # Plain callback queries belong to the classic CallbackQueryHandler.
        return TelegramAgentEvent(
            kind=TelegramAgentEventKind.UNKNOWN,
            update_id=update_id,
            callback_query_id=_str_or_none(callback.get("id")),
            raw_keys=raw_keys,
        )

    message = _message_payload(update)
    if message is not None:
        kind = TelegramAgentEventKind.MESSAGE
        has_rich = isinstance(message.get("rich_message"), Mapping)
        has_voice = bool(_voice_file_ids(message))
        sender = message.get("from") if isinstance(message.get("from"), Mapping) else {}
        if has_voice:
            kind = TelegramAgentEventKind.VOICE_NOTE
        elif has_rich:
            kind = TelegramAgentEventKind.RICH_MESSAGE
        elif bool(sender.get("is_bot")):
            kind = TelegramAgentEventKind.BOT_TO_BOT
        return _event_from_message(kind, update_id, message, raw_keys=raw_keys)

    return TelegramAgentEvent(kind=TelegramAgentEventKind.UNKNOWN, update_id=update_id, raw_keys=raw_keys)


def route_event(
    event: TelegramAgentEvent,
    flags: TelegramAgentFeatureFlags | Mapping[str, Any] | None = None,
) -> TelegramAgentRoute:
    """Return the gateway-level handling decision for a normalized event."""
    if not isinstance(flags, TelegramAgentFeatureFlags):
        flags = TelegramAgentFeatureFlags.from_mapping(flags)

    if event.kind == TelegramAgentEventKind.MESSAGE:
        return TelegramAgentRoute(
            event=event,
            allowed=flags.allow_classic_messages,
            action="handle_message" if flags.allow_classic_messages else "drop",
            reason="classic_message_enabled" if flags.allow_classic_messages else "classic_message_disabled",
        )
    if event.kind == TelegramAgentEventKind.VOICE_NOTE:
        return TelegramAgentRoute(
            event=event,
            allowed=flags.allow_classic_messages,
            action="transcribe_voice_note" if flags.allow_classic_messages else "drop",
            reason="voice_note_transcription_required"
            if flags.allow_classic_messages
            else "classic_message_disabled",
            should_create_receipt=flags.allow_classic_messages,
        )
    if event.kind == TelegramAgentEventKind.GUEST_QUERY:
        return TelegramAgentRoute(
            event=event,
            allowed=flags.guest_mode,
            action="answer_guest_query" if flags.guest_mode else "drop",
            reason="guest_mode_enabled" if flags.guest_mode else "guest_mode_disabled",
            should_create_receipt=flags.guest_mode,
        )
    if event.kind == TelegramAgentEventKind.BOT_TO_BOT:
        return TelegramAgentRoute(
            event=event,
            allowed=flags.bot_to_bot,
            action="handle_bot_to_bot" if flags.bot_to_bot else "drop",
            reason="bot_to_bot_enabled" if flags.bot_to_bot else "bot_to_bot_disabled",
            should_create_receipt=flags.bot_to_bot,
            should_dispatch_kanban=flags.bot_to_bot,
        )
    if event.kind == TelegramAgentEventKind.RICH_MESSAGE:
        return TelegramAgentRoute(
            event=event,
            allowed=flags.rich_messages,
            action="handle_rich_message" if flags.rich_messages else "plain_text_fallback",
            reason="rich_messages_enabled" if flags.rich_messages else "rich_messages_disabled_fallback",
            should_create_receipt=flags.rich_messages,
        )
    if event.kind == TelegramAgentEventKind.MANAGED_BOT_EVENT:
        return TelegramAgentRoute(
            event=event,
            allowed=flags.managed_bots,
            action="handle_managed_bot_event" if flags.managed_bots else "admin_gate",
            reason="managed_bots_enabled" if flags.managed_bots else "managed_bots_admin_gated",
            should_create_receipt=True,
        )
    if event.kind == TelegramAgentEventKind.JOIN_REQUEST_QUERY:
        return TelegramAgentRoute(
            event=event,
            allowed=flags.join_request_guardian,
            action="answer_join_request_query" if flags.join_request_guardian else "drop",
            reason="guardian_enabled" if flags.join_request_guardian else "guardian_disabled",
            should_create_receipt=flags.join_request_guardian,
        )
    return TelegramAgentRoute(event=event, allowed=False, action="drop", reason="unknown_update")


def render_plain_text_fallback(rich_message: Mapping[str, Any] | None) -> str:
    """Best-effort text fallback for Telegram rich-message payloads."""
    if not rich_message:
        return ""
    parts: list[str] = []
    _collect_rich_text(rich_message, parts)
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


# ── Sanitization ─────────────────────────────────────────────────────────────

# Substrings that mark a key as secret-bearing. Any matching key is dropped
# wherever it appears in the raw update.
_SECRET_KEY_MARKERS = ("token", "secret", "credential", "password", "api_key")

# Keys that carry chat rosters / membership PII — never persisted.
_ROSTER_KEYS = frozenset(
    {
        "members",
        "users",
        "chat_members",
        "member_list",
        "participants",
        "invite_link",
        "active_usernames",
    }
)

_EXCERPT_TEXT_LIMIT = 256


def sanitize_update_excerpt(update: Mapping[str, Any]) -> dict[str, Any]:
    """Return the only persistable shape of a raw Telegram update.

    Allowlist-based: scalar identity/routing fields survive, message text is
    truncated to ``_EXCERPT_TEXT_LIMIT`` characters, and anything that looks
    like a token/secret or a chat roster is dropped no matter how deeply it
    is nested. Everything not explicitly allowed is reduced to its key name
    in ``dropped_keys`` so operators can see what was ignored without the
    values ever being stored.
    """
    update = _merge_api_kwargs(update)
    allowed_scalar_keys = {
        "update_id",
        "message_id",
        "date",
        "id",
        "type",
        "is_bot",
        "username",
        "first_name",
        "query_id",
        "guest_query_id",
        "chat_id",
        "action",
        "status",
    }
    allowed_container_keys = {
        "message",
        "edited_message",
        "channel_post",
        "edited_channel_post",
        "guest_message",
        "chat_join_request",
        "callback_query",
        "managed_bot",
        "managed_bot_created",
        "managed_bot_updated",
        "rich_message",
        "chat",
        "from",
        "bot",
        "guest_bot_caller_user",
        "guest_bot_caller_chat",
        "reply_to_message",
        "message_origin",
        "forward_origin",
    }

    def _sanitize(value: Any, key: str | None) -> Any:
        if key is not None:
            lowered = key.lower()
            if any(marker in lowered for marker in _SECRET_KEY_MARKERS):
                return None
            if lowered in _ROSTER_KEYS:
                return None
        if isinstance(value, Mapping):
            out: dict[str, Any] = {}
            dropped: list[str] = []
            for k, v in value.items():
                k = str(k)
                lowered = k.lower()
                if any(marker in lowered for marker in _SECRET_KEY_MARKERS) or lowered in _ROSTER_KEYS:
                    dropped.append(k)
                    continue
                if lowered in allowed_container_keys:
                    child = _sanitize(v, None)
                    if child is not None:
                        out[k] = child
                elif lowered in allowed_scalar_keys and isinstance(v, (str, int, float, bool)):
                    out[k] = v
                elif lowered in {"text", "caption", "data"} and isinstance(v, str):
                    out[k] = v[:_EXCERPT_TEXT_LIMIT]
                else:
                    dropped.append(k)
            if dropped:
                out["dropped_keys"] = sorted(dropped)
            return out
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return None

    result = _sanitize(dict(update), None)
    return result if isinstance(result, dict) else {}


# ── Internal helpers ─────────────────────────────────────────────────────────


def _merge_api_kwargs(update: Mapping[str, Any]) -> Mapping[str, Any]:
    """Fold a PTB ``api_kwargs`` mapping back into the top level, if present.

    ``telegram.Update.to_dict()`` already does this merge in PTB 22.6; this
    guard makes the normalizer robust to callers that pass the raw
    ``__dict__``-shaped payload instead.
    """
    api_kwargs = update.get("api_kwargs")
    if not isinstance(api_kwargs, Mapping):
        return update
    merged = {k: v for k, v in update.items() if k != "api_kwargs"}
    for key, value in api_kwargs.items():
        merged.setdefault(str(key), value)
    return merged


def _with_callback_query_id(event: TelegramAgentEvent, callback_query_id: str | None) -> TelegramAgentEvent:
    if callback_query_id is None:
        return event
    from dataclasses import replace

    return replace(event, callback_query_id=callback_query_id)


def _event_from_message(
    kind: TelegramAgentEventKind,
    update_id: int | None,
    message: Mapping[str, Any],
    *,
    raw_keys: tuple[str, ...],
) -> TelegramAgentEvent:
    chat = message.get("chat") if isinstance(message.get("chat"), Mapping) else {}
    sender = message.get("from") if isinstance(message.get("from"), Mapping) else {}
    reply = message.get("reply_to_message") if isinstance(message.get("reply_to_message"), Mapping) else {}
    guest_user = message.get("guest_bot_caller_user")
    guest_chat = message.get("guest_bot_caller_chat")
    voice_file_ids = _voice_file_ids(message)
    return TelegramAgentEvent(
        kind=kind,
        update_id=update_id,
        message_id=_optional_int(message.get("message_id")),
        chat_id=_str_or_none(chat.get("id")),
        chat_type=_str_or_none(chat.get("type")),
        from_user_id=_str_or_none(sender.get("id")),
        from_is_bot=bool(sender.get("is_bot")),
        text=_message_text(message),
        reply_to_message_id=_optional_int(reply.get("message_id")) if reply else None,
        guest_query_id=_str_or_none(message.get("guest_query_id")),
        guest_caller_user_id=_str_or_none(guest_user.get("id")) if isinstance(guest_user, Mapping) else None,
        guest_caller_chat_id=_str_or_none(guest_chat.get("id")) if isinstance(guest_chat, Mapping) else None,
        has_rich_message=isinstance(message.get("rich_message"), Mapping),
        has_voice_note=bool(voice_file_ids),
        voice_file_ids=voice_file_ids,
        raw_keys=raw_keys,
    )


def _message_payload(update: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
        value = update.get(key)
        if isinstance(value, Mapping):
            return value
    return None


def _managed_bot_payload(update: Mapping[str, Any]) -> Mapping[str, Any] | None:
    direct = update.get("managed_bot")
    if isinstance(direct, Mapping):
        return direct
    message = _message_payload(update)
    if not message:
        return None
    for key in ("managed_bot_created", "managed_bot_updated"):
        value = message.get(key)
        if isinstance(value, Mapping):
            return value
    return None


def _message_text(message: Mapping[str, Any]) -> str:
    for key in ("text", "caption"):
        value = message.get(key)
        if isinstance(value, str):
            return value
    if isinstance(message.get("rich_message"), Mapping):
        return render_plain_text_fallback(message["rich_message"])
    return ""


def _voice_file_ids(message: Mapping[str, Any]) -> tuple[str, ...]:
    file_ids: list[str] = []
    for key in ("voice", "audio"):
        value = message.get(key)
        if isinstance(value, Mapping):
            file_id = _str_or_none(value.get("file_id"))
            if file_id:
                file_ids.append(file_id)
    document = message.get("document")
    if isinstance(document, Mapping) and str(document.get("mime_type", "")).startswith("audio/"):
        file_id = _str_or_none(document.get("file_id"))
        if file_id:
            file_ids.append(file_id)
    return tuple(file_ids)


def _collect_rich_text(value: Any, parts: list[str]) -> None:
    if isinstance(value, Mapping):
        for key in ("text", "caption"):
            item = value.get(key)
            if isinstance(item, str):
                parts.append(item)
        for item in value.values():
            _collect_rich_text(item, parts)
    elif isinstance(value, list):
        for item in value:
            _collect_rich_text(item, parts)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
