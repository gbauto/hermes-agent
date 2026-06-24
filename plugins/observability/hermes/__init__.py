"""Hermes Observability emitter.

Posts real Hermes runtime hook events to the Pi-style observability server.
This is intentionally small and fail-open: observability must never block an
agent turn.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_seq_by_session: Dict[str, int] = {}
_started_sessions: set[str] = set()


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _config() -> tuple[str, str]:
    url = _env("HERMES_PI_OBSERVABILITY_URL") or _env("OBS_SERVER_URL")
    token = _env("HERMES_PI_OBSERVABILITY_TOKEN") or _env("OBS_AUTH_TOKEN")
    return url.rstrip("/"), token


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_seq(session_id: str) -> int:
    with _lock:
        seq = _seq_by_session.get(session_id, 0)
        _seq_by_session[session_id] = seq + 1
        return seq


def _short(value: Any, limit: int = 4000) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        text = str(value)
        return text[:limit] if isinstance(value, str) else value
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return text[:limit]


def _base_event(session_id: str, event_type: str, payload: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "ts": _now(),
        "type": event_type,
        "session_id": session_id or "unknown",
        "cwd": os.getcwd(),
        "pool": _env("HERMES_OBSERVABILITY_POOL") or _env("HERMES_PROFILE") or "smoke-client",
        "tags": [
            "runtime:hermes",
            f"tenant:{_env('HERMES_OBSERVABILITY_POOL') or _env('HERMES_PROFILE') or 'smoke-client'}",
            "surface:hermes-observability",
            "source:real-hermes-run",
        ],
        "provider": kwargs.get("provider") or _env("HERMES_INFERENCE_PROVIDER"),
        "model": kwargs.get("model") or _env("HERMES_INFERENCE_MODEL"),
        "payload": payload,
        "seq": _next_seq(session_id or "unknown"),
    }


def _post(event: Dict[str, Any]) -> None:
    url, token = _config()
    if not url or not token:
        return
    data = json.dumps(event, ensure_ascii=False, default=str).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/events",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.debug("Hermes observability emit failed: %s", exc)
    except Exception as exc:
        logger.debug("Hermes observability emit failed: %s", exc)


def _emit(session_id: str, event_type: str, payload: Dict[str, Any], **kwargs: Any) -> None:
    try:
        event = _base_event(session_id, event_type, payload, **kwargs)
        _post(event)
    except Exception as exc:
        logger.debug("Hermes observability event build failed: %s", exc)


def _ensure_started(session_id: str, user_message: str = "", **kwargs: Any) -> None:
    if not session_id:
        return
    with _lock:
        if session_id in _started_sessions:
            return
        _started_sessions.add(session_id)
    _emit(
        session_id,
        "session_start",
        {"reason": "real Hermes run", "pi_version": "0.1.0"},
        **kwargs,
    )
    _emit(
        session_id,
        "agent_start",
        {"prompt": _short(user_message, 1000), "session_id": session_id},
        **kwargs,
    )


def _on_pre_llm_call(
    session_id: str = "",
    user_message: str = "",
    model: str = "",
    provider: str = "",
    platform: str = "",
    **_: Any,
) -> None:
    _ensure_started(session_id, user_message, model=model, provider=provider)
    if user_message:
        _emit(
            session_id,
            "user_message",
            {"text": _short(user_message, 2000), "platform": platform},
            model=model,
            provider=provider,
        )


def _on_pre_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    session_id: str = "",
    tool_call_id: str = "",
    **_: Any,
) -> None:
    _emit(
        session_id,
        "tool_call",
        {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "args": args or {},
            "args_truncated": False,
        },
    )


def _on_post_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    session_id: str = "",
    tool_call_id: str = "",
    duration_ms: Optional[int] = None,
    **_: Any,
) -> None:
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    is_error = "error" in text[:500].lower()
    _emit(
        session_id,
        "tool_result",
        {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "content_text": _short(text, 2000),
            "content_truncated": len(text) > 2000,
            "is_error": is_error,
            "details_summary": {"duration_ms": duration_ms},
        },
    )


def _on_post_llm_call(
    session_id: str = "",
    user_message: str = "",
    assistant_response: str = "",
    model: str = "",
    provider: str = "",
    **_: Any,
) -> None:
    _ensure_started(session_id, user_message, model=model, provider=provider)
    _emit(
        session_id,
        "assistant_message",
        {
            "text": _short(assistant_response, 3000),
            "usage": {"total_tokens": 0, "cost_total": 0.0},
            "stop_reason": "stop",
        },
        model=model,
        provider=provider,
    )


def _on_session_finalize(session_id: str = "", **_: Any) -> None:
    if not session_id:
        return
    _emit(session_id, "session_shutdown", {"reason": "finalize"})


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("post_llm_call", _on_post_llm_call)
    ctx.register_hook("on_session_finalize", _on_session_finalize)
