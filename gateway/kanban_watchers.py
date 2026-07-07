"""Kanban board watcher methods for GatewayRunner.

Extracted verbatim from ``gateway/run.py`` (god-file decomposition Phase 3).
These are the background-loop methods that subscribe to kanban boards, deliver
notifications/artifacts, and drive the multi-agent dispatcher. They use only
``self`` state, so they live on a mixin that ``GatewayRunner`` inherits — the
``self._kanban_*`` call sites resolve identically via the MRO, making this a
behavior-neutral move that lifts ~1,000 LOC out of run.py.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

# Match the logger run.py uses (logging.getLogger(__name__) where __name__ ==
# "gateway.run") so extracted log records keep their original logger name.
logger = logging.getLogger("gateway.run")

# No baked-in public Kanban URL.  A previous constant pointed at a Netlify
# deploy-preview/static report and caused blocked/review notifications to label
# stale HTML as the live board.  Only emit Board links from explicit, verified
# deployment configuration.
APPROVED_KANBAN_LIVE_BOARD_URL = ""


class GatewayKanbanWatchersMixin:
    """Kanban watcher / notifier / dispatcher loops for GatewayRunner."""

    async def _kanban_notifier_watcher(self, interval: float = 5.0) -> None:
        """Poll ``kanban_notify_subs`` and deliver terminal events to users.

        For each subscription row, fetches ``task_events`` newer than the
        stored cursor with kind in the terminal set (``completed``,
        ``blocked``, ``gave_up``, ``crashed``, ``timed_out``). Sends one
        message per new event to ``(platform, chat_id, thread_id)``,
        then advances the cursor. When a task reaches a terminal state
        (``completed`` / ``archived``), the subscription is removed.

        Runs in the gateway event loop; all SQLite work is pushed to a
        thread via ``asyncio.to_thread`` so the loop never blocks on the
        WAL lock. Failures in one tick don't stop subsequent ticks.

        **Multi-board:** iterates every board discovered on disk per
        tick. Subscriptions live inside each board's own DB and cannot
        cross boards, so delivery semantics are unchanged — this is
        purely a fan-out of the single-DB poll.
        """
        # Gate: only the dispatch-owning gateway opens kanban DBs for notifier polling.
        # Non-dispatch gateways have no subscriptions to deliver — all kanban state lives
        # in the dispatch owner's per-board DBs. This prevents N-gateway -shm contention.
        # TODO: gate per-board when per-board dispatcher_owner tracking lands.
        try:
            from hermes_cli.config import load_config as _load_config
        except Exception:
            logger.warning("kanban notifier: config loader unavailable; disabled")
            return
        env_override = os.environ.get("HERMES_KANBAN_DISPATCH_IN_GATEWAY", "").strip().lower()
        if env_override in {"0", "false", "no", "off"}:
            logger.info("kanban notifier: disabled via HERMES_KANBAN_DISPATCH_IN_GATEWAY env")
            return
        try:
            cfg = _load_config()
        except Exception as exc:
            logger.warning("kanban notifier: cannot load config (%s); disabled", exc)
            return
        kanban_cfg = cfg.get("kanban", {}) if isinstance(cfg, dict) else {}
        if not kanban_cfg.get("dispatch_in_gateway", True):
            logger.info(
                "kanban notifier: disabled via config kanban.dispatch_in_gateway=false"
            )
            return
        from gateway.config import Platform as _Platform
        try:
            from hermes_cli import kanban_db as _kb
        except Exception:
            logger.warning("kanban notifier: kanban_db not importable; notifier disabled")
            return

        TERMINAL_KINDS = ("completed", "blocked", "gave_up", "crashed", "timed_out")
        # Subscriptions are removed only when the task reaches a truly final
        # status (done / archived). We used to also unsub on any terminal
        # event kind (gave_up / crashed / timed_out / blocked), but that
        # silently dropped the user out of the loop whenever the dispatcher
        # respawned the task: a worker that crashes, gets reclaimed, runs
        # again, and crashes a second time would only notify on the first
        # crash because the subscription was deleted after the first event.
        # Same shape as the reblock-after-unblock cycle that PR #22941
        # fixed for `blocked`. Keeping the subscription alive until the
        # task is genuinely done lets the cursor (advanced atomically by
        # claim_unseen_events_for_sub) handle dedup, and any retry-loop
        # event reaches the user.
        # Per-subscription send-failure counter. Adapter.send raising
        # means the chat is dead (deleted, bot kicked, etc.) — after N
        # consecutive send failures the sub is dropped so we don't spin
        # against a dead chat every 5 seconds forever.
        MAX_SEND_FAILURES = 3
        sub_fail_counts: dict[tuple, int] = getattr(
            self, "_kanban_sub_fail_counts", {}
        )
        self._kanban_sub_fail_counts = sub_fail_counts
        def _notification_sources_allowed(raw_sources: Any, current_profile: str) -> set[str] | None:
            """Return allowed subscription-owner profiles for this gateway.

            ``None`` means wildcard/all profiles.  The public kanban-worker
            guidance documents ``notification_sources: ['*']`` for the
            gateway that should receive cross-profile Kanban terminal events;
            honor that config here so subscriptions created by worker profiles
            (tac-director, tac-ops, etc.) are not silently skipped by the
            default-profile gateway.
            """
            if raw_sources is None or raw_sources == "":
                return {current_profile}
            if isinstance(raw_sources, str):
                parts = [p.strip() for p in raw_sources.split(",")]
            elif isinstance(raw_sources, (list, tuple, set)):
                parts = [str(p).strip() for p in raw_sources]
            else:
                parts = [str(raw_sources).strip()]
            parts = [p for p in parts if p]
            if "*" in parts:
                return None
            return set(parts or [current_profile])

        notifier_profile = getattr(self, "_kanban_notifier_profile", None)
        if not notifier_profile:
            notifier_profile = self._active_profile_name()
            self._kanban_notifier_profile = notifier_profile
        allowed_notification_sources = _notification_sources_allowed(
            cfg.get("notification_sources") if isinstance(cfg, dict) else None,
            notifier_profile,
        )

        # Initial delay so the gateway can finish wiring adapters.
        await asyncio.sleep(5)

        while self._running:
            try:
                def _collect():
                    deliveries: list[dict] = []
                    active_platforms = {
                        getattr(platform, "value", str(platform)).lower()
                        for platform in self.adapters.keys()
                    }
                    if not active_platforms:
                        logger.debug("kanban notifier: no connected adapters; skipping tick")
                        return deliveries

                    # Enumerate every board on disk, but poll each resolved DB
                    # path once. Multiple slugs can point at the same DB when
                    # HERMES_KANBAN_DB pins the board path; without this guard
                    # one gateway could collect the same subscription/event
                    # more than once before advancing the cursor.
                    try:
                        boards = _kb.list_boards(include_archived=False)
                    except Exception:
                        boards = [_kb.read_board_metadata(_kb.DEFAULT_BOARD)]
                    seen_db_paths: set[str] = set()
                    for board_meta in boards:
                        slug = board_meta.get("slug") or _kb.DEFAULT_BOARD
                        db_path = board_meta.get("db_path")
                        try:
                            resolved_db_path = str(Path(db_path).expanduser().resolve()) if db_path else str(_kb.kanban_db_path(slug).resolve())
                        except Exception:
                            resolved_db_path = f"slug:{slug}"
                        if resolved_db_path in seen_db_paths:
                            logger.debug(
                                "kanban notifier: skipping duplicate board slug %s for DB %s",
                                slug, resolved_db_path,
                            )
                            continue
                        seen_db_paths.add(resolved_db_path)
                        try:
                            conn = _kb.connect(board=slug)
                        except Exception as exc:
                            logger.debug("kanban notifier: cannot open board %s: %s", slug, exc)
                            continue
                        try:
                            # `connect()` runs the schema + idempotent migration
                            # on first open per process, so an explicit
                            # `init_db()` here would be redundant. Worse:
                            # `init_db()` deliberately busts the per-process
                            # cache and re-runs the migration on a *second*
                            # connection, which races the first and used to
                            # log a benign but noisy `duplicate column name`
                            # traceback (and intermittent "database is locked"
                            # — issue #21378) on every gateway start against
                            # a legacy DB. `_add_column_if_missing` now
                            # tolerates that race, but we still skip the
                            # redundant call to avoid the wasted work.
                            subs = _kb.list_notify_subs(conn)
                            if not subs:
                                logger.debug("kanban notifier: board %s has no subscriptions", slug)
                            for sub in subs:
                                owner_profile = sub.get("notifier_profile") or None
                                if (
                                    owner_profile
                                    and allowed_notification_sources is not None
                                    and owner_profile not in allowed_notification_sources
                                ):
                                    logger.debug(
                                        "kanban notifier: subscription for %s owned by profile %s; current profile %s sources=%s skipping",
                                        sub.get("task_id"), owner_profile, notifier_profile,
                                        sorted(allowed_notification_sources),
                                    )
                                    continue
                                platform = (sub.get("platform") or "").lower()
                                if platform not in active_platforms:
                                    logger.debug(
                                        "kanban notifier: subscription for %s on %s skipped; adapter not connected",
                                        sub.get("task_id"), platform or "<missing>",
                                    )
                                    continue
                                old_cursor, cursor, events = _kb.claim_unseen_events_for_sub(
                                    conn,
                                    task_id=sub["task_id"],
                                    platform=sub["platform"],
                                    chat_id=sub["chat_id"],
                                    thread_id=sub.get("thread_id") or "",
                                    kinds=TERMINAL_KINDS,
                                )
                                if not events:
                                    continue
                                task = _kb.get_task(conn, sub["task_id"])
                                logger.debug(
                                    "kanban notifier: claimed %d event(s) for %s on board %s cursor %s→%s",
                                    len(events), sub["task_id"], slug, old_cursor, cursor,
                                )
                                deliveries.append({
                                    "sub": sub,
                                    "old_cursor": old_cursor,
                                    "cursor": cursor,
                                    "events": events,
                                    "task": task,
                                    "board": slug,
                                })
                        finally:
                            conn.close()
                    return deliveries

                deliveries = await asyncio.to_thread(_collect)
                for d in deliveries:
                    sub = d["sub"]
                    task = d["task"]
                    board_slug = d.get("board")
                    platform_str = (sub["platform"] or "").lower()
                    try:
                        plat = _Platform(platform_str)
                    except ValueError:
                        # Unknown platform string; skip and advance cursor so
                        # we don't replay forever.
                        await asyncio.to_thread(
                            self._kanban_advance, sub, d["cursor"], board_slug,
                        )
                        continue
                    adapter = self.adapters.get(plat)
                    if adapter is None:
                        logger.debug(
                            "kanban notifier: adapter %s disconnected before delivery for %s; rewinding claim",
                            platform_str, sub["task_id"],
                        )
                        await asyncio.to_thread(
                            self._kanban_rewind,
                            sub,
                            d["cursor"],
                            d.get("old_cursor", 0),
                            board_slug,
                        )
                        continue
                    title = (task.title if task else sub["task_id"])[:120]
                    for ev in d["events"]:
                        kind = ev.kind
                        # Identity prefix: attribute terminal pings to the
                        # worker that did the work. Makes fleets (where one
                        # chat subscribes to many tasks) legible at a glance.
                        who = (task.assignee if task and task.assignee else None)
                        tag = f"@{who} " if who else ""
                        if kind == "completed":
                            # Prefer the run's summary (the worker's
                            # intentional human-facing handoff, carried
                            # in the event payload), then fall back to
                            # task.result for legacy rows written before
                            # runs shipped. Auto-completed grouping records
                            # have no worker run; call that out instead of
                            # making the parent look like execution finished.
                            handoff = ""
                            payload_summary = None
                            payload_reason = ""
                            if ev.payload:
                                if ev.payload.get("summary"):
                                    payload_summary = str(ev.payload["summary"])
                                if ev.payload.get("reason"):
                                    payload_reason = str(ev.payload["reason"])
                            if payload_summary:
                                lines = payload_summary.strip().splitlines()
                                h = lines[0][:200] if lines else payload_summary[:200]
                                handoff = f"\nSummary: {h}"
                            elif payload_reason and "grouping record" in payload_reason.lower():
                                handoff = "\nSummary: grouping record, first child owns execution"
                            elif task and task.result:
                                lines = task.result.strip().splitlines()
                                r = lines[0][:160] if lines else task.result[:160]
                                handoff = f"\nSummary: {r}"
                            board_line = self._kanban_board_line(board_slug, sub["task_id"])
                            board_context = f"\n{board_line}" if board_line else ""
                            source = f"\n{board_slug or 'default'} · {sub['task_id']} · source kanban-gateway"
                            msg = (
                                f"✔ {tag}Kanban {sub['task_id']} done"
                                f" - {title}{handoff}{board_context}{source}"
                            )
                        elif kind == "blocked":
                            msg, blocked_metadata = self._format_kanban_blocked_notification(
                                sub=sub,
                                task=task,
                                event=ev,
                                board_slug=board_slug,
                                title=title,
                                tag=tag,
                            )
                        elif kind == "gave_up":
                            err = ""
                            if ev.payload and ev.payload.get("error"):
                                err = f"\n{str(ev.payload['error'])[:200]}"
                            board_line = self._kanban_board_line(board_slug, sub["task_id"])
                            board_context = f"\n{board_line}" if board_line else ""
                            msg = (
                                f"✖ {tag}Kanban {sub['task_id']} gave up "
                                f"after repeated spawn failures{err}{board_context}"
                            )
                        elif kind == "crashed":
                            board_line = self._kanban_board_line(board_slug, sub["task_id"])
                            board_context = f"\n{board_line}" if board_line else ""
                            msg = (
                                f"✖ {tag}Kanban {sub['task_id']} worker crashed "
                                f"(pid gone); dispatcher will retry{board_context}"
                            )
                        elif kind == "timed_out":
                            limit = 0
                            if ev.payload and ev.payload.get("limit_seconds"):
                                limit = int(ev.payload["limit_seconds"])
                            board_line = self._kanban_board_line(board_slug, sub["task_id"])
                            board_context = f"\n{board_line}" if board_line else ""
                            msg = (
                                f"⏱ {tag}Kanban {sub['task_id']} timed out "
                                f"(max_runtime={limit}s); will retry{board_context}"
                            )
                        else:
                            continue
                        metadata: dict[str, Any] = {}
                        if kind == "blocked" and "blocked_metadata" in locals():
                            metadata.update(blocked_metadata)
                            del blocked_metadata
                        if sub.get("thread_id"):
                            metadata["thread_id"] = sub["thread_id"]
                        sub_key = (
                            sub["task_id"], sub["platform"],
                            sub["chat_id"], sub.get("thread_id") or "",
                        )
                        try:
                            await adapter.send(
                                sub["chat_id"], msg, metadata=metadata,
                            )
                            logger.debug(
                                "kanban notifier: delivered %s event for %s to %s/%s on board %s",
                                kind, sub["task_id"], platform_str, sub["chat_id"], board_slug,
                            )
                            # After delivering the text notification, surface
                            # any artifact paths the worker referenced in
                            # ``kanban_complete(summary=..., artifacts=[...])``
                            # (or the legacy ``result`` field) as native
                            # uploads. ``extract_local_files`` finds bare
                            # absolute paths in the summary;
                            # ``send_document`` / ``send_image_file`` uploads
                            # them. Only fires on the ``completed`` event so
                            # we never spam attachments on retries.
                            if kind == "completed":
                                try:
                                    await self._deliver_kanban_artifacts(
                                        adapter=adapter,
                                        chat_id=sub["chat_id"],
                                        metadata=metadata,
                                        event_payload=getattr(ev, "payload", None),
                                        task=task,
                                    )
                                except Exception as art_exc:
                                    logger.debug(
                                        "kanban notifier: artifact delivery for %s failed: %s",
                                        sub["task_id"], art_exc,
                                    )
                            # Reset the failure counter on success.
                            sub_fail_counts.pop(sub_key, None)
                        except Exception as exc:
                            fails = sub_fail_counts.get(sub_key, 0) + 1
                            sub_fail_counts[sub_key] = fails
                            logger.warning(
                                "kanban notifier: send failed for %s on %s "
                                "(attempt %d/%d): %s",
                                sub["task_id"], platform_str, fails,
                                MAX_SEND_FAILURES, exc,
                            )
                            if fails >= MAX_SEND_FAILURES:
                                logger.warning(
                                    "kanban notifier: dropping subscription "
                                    "%s on %s after %d consecutive send failures",
                                    sub["task_id"], platform_str, fails,
                                )
                                await asyncio.to_thread(self._kanban_unsub, sub, board_slug)
                                sub_fail_counts.pop(sub_key, None)
                            else:
                                await asyncio.to_thread(
                                    self._kanban_rewind,
                                    sub,
                                    d["cursor"],
                                    d.get("old_cursor", 0),
                                    board_slug,
                                )
                            # Rewind the pre-send claim on transient failure so
                            # a later tick can retry. After too many failures,
                            # dropping the subscription is the terminal action.
                            break
                    else:
                        # All events delivered; advance cursor. The cursor
                        # is the dedup mechanism — it prevents re-delivery
                        # of the same event on subsequent ticks.
                        await asyncio.to_thread(
                            self._kanban_advance, sub, d["cursor"], board_slug,
                        )
                        # Unsubscribe only when the task has reached a truly
                        # final status (done / archived). For blocked /
                        # gave_up / crashed / timed_out the subscription is
                        # kept alive so the user gets notified again if the
                        # dispatcher respawns the task and it cycles into the
                        # same state. See the longer comment on TERMINAL_KINDS
                        # above for the failure mode this prevents.
                        task_terminal = task and task.status in {"done", "archived"}
                        if task_terminal:
                            await asyncio.to_thread(
                                self._kanban_unsub, sub, board_slug,
                            )
            except Exception as exc:
                logger.warning("kanban notifier tick failed: %s", exc)
            # Sleep with cancellation checks.
            for _ in range(int(max(1, interval))):
                if not self._running:
                    return
                await asyncio.sleep(1)


    def _kanban_board_url(self, board_slug: Optional[str], task_id: str) -> Optional[str]:
        """Return the configured live board/task URL for Telegram notices.

        Board notices are intentionally opt-in: deployments can set
        ``HERMES_KANBAN_LIVE_BOARD_URL``, ``HERMES_KANBAN_BOARD_URL``, or
        ``HERMES_DASHBOARD_URL`` after verifying that the target is a live board.
        If the URL template includes ``{task_id}`` or ``{board_slug}``, those
        placeholders are filled; otherwise use the live-board anchor contract:
        ``<board-url>#task=<task_id>``. Netlify preview/static report URLs are
        suppressed because they have repeatedly looked like a live board while
        serving stale report HTML.
        """
        from urllib.parse import quote, urlparse

        base = (
            os.environ.get("HERMES_KANBAN_LIVE_BOARD_URL")
            or os.environ.get("HERMES_KANBAN_BOARD_URL")
            or os.environ.get("HERMES_DASHBOARD_URL")
            or APPROVED_KANBAN_LIVE_BOARD_URL
        ).strip()
        if not base:
            return None
        try:
            host = (urlparse(base).hostname or "").lower()
        except Exception:
            host = ""
        if host.endswith(".netlify.app") or host == "netlify.app" or "gbautoxyz" in host:
            logger.warning(
                "kanban notifier: suppressing unverified/stale Board URL host %s",
                host or "<unparseable>",
            )
            return None
        board = quote((board_slug or "default"), safe="")
        task = quote(task_id, safe="")
        if "{task_id}" in base or "{board_slug}" in base:
            return base.format(board_slug=board, task_id=task)
        root = base.rstrip("/")
        separator = "&" if "#" in root and "?" in root.rsplit("#", 1)[-1] else "#"
        if "#" in root:
            return f"{root}{separator}task={task}"
        return f"{root}#task={task}"

    def _kanban_board_line(self, board_slug: Optional[str], task_id: str) -> str:
        """Return a Telegram-friendly Board line for every Kanban update."""
        url = self._kanban_board_url(board_slug, task_id)
        return f"Board: {url}" if url else ""

    def _kanban_blocked_issue(self, task, event, board_slug: Optional[str] = None) -> str:
        """Return a plain-English blocker/root cause for the Issue line."""
        run_or_log_issue = self._kanban_blocked_issue_from_run_or_log(task, board_slug)
        candidates: list[str] = []
        if getattr(event, "payload", None) and event.payload.get("reason"):
            candidates.append(str(event.payload["reason"]))
        for attr in ("last_failure_error", "result"):
            value = getattr(task, attr, None) if task is not None else None
            if value:
                candidates.append(str(value))
        for raw in candidates:
            first = raw.strip().splitlines()[0].strip()
            if not first:
                continue
            lowered = first.lower()
            if lowered in {"blocked", "task blocked", "status: blocked"}:
                continue
            if self._kanban_blocked_reason_is_meta(lowered):
                derived = self._derive_kanban_blocked_issue_from_task(task)
                if derived:
                    return derived
                if run_or_log_issue:
                    return run_or_log_issue
                return "Worker did not provide a concrete blocker; needs triage."
            if lowered.startswith("review-required:"):
                detail = first.split(":", 1)[1].strip()
                if detail:
                    return f"Human review required: {detail[:220].rstrip('.')}."
                return "Human review required before marking this task done."
            if "pid gone" in lowered or re.search(r"\bpid\s+\d+\s+not\s+alive\b", lowered) or "pid not alive" in lowered:
                if run_or_log_issue:
                    return run_or_log_issue
                return "Worker exited; inspect the latest run log before unblocking."
            return first[:240].rstrip(".") + "."
        if run_or_log_issue:
            return run_or_log_issue
        derived = self._derive_kanban_blocked_issue_from_task(task)
        if derived:
            return derived
        return "Worker did not provide a concrete blocker; needs triage."

    def _kanban_blocked_issue_from_run_or_log(self, task, board_slug: Optional[str]) -> str:
        """Best-effort root cause from latest run error or worker log tail."""
        if task is None:
            return ""
        task_id = getattr(task, "id", "") or ""
        texts: list[str] = []
        try:
            from hermes_cli import kanban_db as _kb

            conn = _kb.connect(board=board_slug)
            try:
                run = _kb.latest_run(conn, task_id)
                if run is not None:
                    for value in (run.error, run.summary):
                        if value:
                            texts.append(str(value))
            finally:
                conn.close()
            log_tail = _kb.read_worker_log(task_id, tail_bytes=6000, board=board_slug)
            if log_tail:
                texts.append(log_tail)
        except Exception:
            return ""
        for text in texts:
            issue = self._extract_kanban_root_cause_line(text)
            if issue:
                return issue
        return ""

    def _extract_kanban_root_cause_line(self, text: str) -> str:
        """Convert a run/log tail into a concise user-facing root cause."""
        if not text:
            return ""
        unknown_skill = re.search(r"Unknown skill\(s\):\s*([^\n\r]+)", text)
        if unknown_skill:
            missing = unknown_skill.group(1).strip().strip("`'.\"")
            return f"Profile cannot load required skill(s): {missing}."
        for raw in reversed(text.splitlines()):
            line = raw.strip()
            if not line:
                continue
            lowered = line.lower()
            if lowered.startswith(("error:", "traceback", "runtimeerror:", "valueerror:")):
                return line[:240].rstrip(".") + "."
        return ""

    def _kanban_blocked_reason_is_meta(self, lowered_reason: str) -> bool:
        """Detect reasons that describe the request, not the actual blocker."""
        meta_markers = (
            "greg clarified",
            "greg said",
            "user clarified",
            "human clarified",
            "ux request",
            "notification ux",
            "copy pattern",
            "current copy is confusing",
        )
        return any(marker in lowered_reason for marker in meta_markers)

    def _derive_kanban_blocked_issue_from_task(self, task) -> str:
        """Best-effort issue extraction from task body when block reason is meta."""
        body = str(getattr(task, "body", "") or "")
        lines = [line.strip().lstrip("-• ").strip() for line in body.splitlines()]
        for line in lines:
            if line.lower().startswith("issue:"):
                value = line.split(":", 1)[1].strip()
                if value:
                    return value[:240].rstrip(".") + "."
        for line in lines:
            if line.lower().startswith("missing "):
                return line[:240].rstrip(".") + "."
        for line in lines:
            lowered = line.lower()
            if "missing " in lowered and not lowered.startswith("problem:"):
                idx = lowered.find("missing ")
                return line[idx:][:240].rstrip(".") + "."
        return ""

    def _kanban_blocked_suggestions(self, issue: str) -> list[str]:
        """Map common blocker classes to concise unblock actions."""
        text = (issue or "").lower()
        if "logo" in text and ("asset" in text or "path" in text):
            return [
                "Provide the logo asset/path.",
                "Use existing GBAuto wordmark from theme assets.",
            ]
        if "review-required" in text or "review required" in text:
            return [
                "Approve/promote if the diff is acceptable.",
                "Create a fix card with requested changes.",
            ]
        if "permission" in text or "aws" in text or "secret" in text:
            return [
                "Grant the missing permission or secret access.",
                "Provide an approved token and unblock.",
            ]
        if "auth" in text or "token" in text or "oauth" in text or "credential" in text or "api key" in text:
            return [
                "Reauth the profile with a known-good credential.",
                "Sync the approved token, then unblock.",
            ]
        if "iteration budget" in text or "goal-turn" in text or "max turns" in text:
            return [
                "Split into a smaller follow-up task.",
                "Raise max turns for a bounded retry.",
            ]
        if "skill" in text and (
            "missing" in text
            or "not found" in text
            or "install" in text
            or "cannot load required" in text
            or "unknown skill" in text
        ):
            return [
                "Install the missing skill for this profile.",
                "Reassign to a profile that already has it.",
            ]
        if "worker exited" in text or "run log" in text or "pid" in text:
            return [
                "Inspect the latest run log, then unblock.",
                "Reassign or split if the crash repeats.",
            ]
        return []

    def _kanban_blocked_unblock_line(self, issue: str) -> str:
        suggestions = self._kanban_blocked_suggestions(issue)
        if not suggestions:
            return "Unblock: add missing context, then promote"
        options = [f"A) {suggestions[0]}"]
        if len(suggestions) > 1 and suggestions[1]:
            options.append(f"B) {suggestions[1]}")
        return "Unblock: " + "  ".join(options)

    def _format_kanban_blocked_notification(
        self,
        *,
        sub: dict,
        task,
        event,
        board_slug: Optional[str],
        title: str,
        tag: str,
    ) -> tuple[str, dict[str, Any]]:
        """Build the user-facing blocked notice and Telegram action metadata."""
        task_id = str(sub["task_id"])
        issue = self._kanban_blocked_issue(task, event, board_slug)
        owner = getattr(task, "assignee", None) or "unassigned"
        owner_text = str(owner)
        board = board_slug or "default"
        board_line = self._kanban_board_line(board_slug, task_id)
        lines = [
            f"🚫 Blocked - {title}",
            f"Issue: {issue}",
            self._kanban_blocked_unblock_line(issue),
        ]
        if board_line:
            lines.append(board_line)
        lines.append(f"{board} · {task_id} · owner {owner_text} · source kanban-gateway")
        keyboard = [
            [
                {"text": "✅ Unblock", "callback_data": f"kbb:u:{board}:{task_id}"},
                {"text": "🚀 Promote", "callback_data": f"kbb:p:{board}:{task_id}"},
            ],
            [
                {"text": "⏸ Keep blocked", "callback_data": f"kbb:k:{board}:{task_id}"},
            ],
        ]
        url = self._kanban_board_url(board_slug, task_id)
        if url:
            keyboard[-1].append({"text": "🔎 Open board", "url": url})
        else:
            keyboard[-1].append({"text": "🔎 Open board", "callback_data": f"kbb:o:{board}:{task_id}"})
        return "\n".join(lines), {
            "telegram_inline_keyboard": keyboard,
            "kanban_blocker_event": {
                "schema_version": "blocker_event.v1",
                "task_id": task_id,
                "board_slug": board,
                "owner": owner,
                "issue": issue,
                "event_id": getattr(event, "id", None),
            },
        }

    def _kanban_advance(
        self, sub: dict, cursor: int, board: Optional[str] = None,
    ) -> None:
        """Sync helper: advance a subscription's cursor. Runs in to_thread.

        ``board`` scopes the DB connection to the board that owns this
        subscription. Unsub cursors in one board can't touch another's.
        """
        from hermes_cli import kanban_db as _kb
        conn = _kb.connect(board=board)
        try:
            _kb.advance_notify_cursor(
                conn,
                task_id=sub["task_id"],
                platform=sub["platform"],
                chat_id=sub["chat_id"],
                thread_id=sub.get("thread_id") or "",
                new_cursor=cursor,
            )
        finally:
            conn.close()

    def _kanban_unsub(self, sub: dict, board: Optional[str] = None) -> None:
        from hermes_cli import kanban_db as _kb
        conn = _kb.connect(board=board)
        try:
            _kb.remove_notify_sub(
                conn,
                task_id=sub["task_id"],
                platform=sub["platform"],
                chat_id=sub["chat_id"],
                thread_id=sub.get("thread_id") or "",
            )
        finally:
            conn.close()

    def _kanban_rewind(
        self,
        sub: dict,
        claimed_cursor: int,
        old_cursor: int,
        board: Optional[str] = None,
    ) -> None:
        """Sync helper: undo a claimed notification cursor after send failure."""
        from hermes_cli import kanban_db as _kb
        conn = _kb.connect(board=board)
        try:
            _kb.rewind_notify_cursor(
                conn,
                task_id=sub["task_id"],
                platform=sub["platform"],
                chat_id=sub["chat_id"],
                thread_id=sub.get("thread_id") or "",
                claimed_cursor=claimed_cursor,
                old_cursor=old_cursor,
            )
        finally:
            conn.close()

    def _resolve_hosted_html_artifact_url(self, path: str) -> str:
        """Resolve a local HTML artifact to a hosted Netlify/GBAutomation URL.

        Kanban gateway notifications should not upload raw `.html` files to
        chat. This resolver keeps delivery side-effect-free: it uses an explicit
        JSONL registry if configured, the GBAutomation plan-render path shape,
        or a URL already embedded in the HTML. Actual publishing is handled by
        upstream artifact pipelines; when no hosted URL can be found, the caller
        skips the HTML unless an explicit debug/admin fallback is enabled.
        """
        import json as _json
        import re as _re

        def _is_url(value: str) -> bool:
            return value.startswith("https://") or value.startswith("http://")

        def _norm(value: str) -> str:
            return value.replace("\\", "/").rstrip("/")

        def _row_url(row: dict) -> str:
            # Prefer user-openable public URLs over Netlify's admin/drop viewer links.
            for key in ("public_url", "url", "deployUrl", "deploy_url"):
                value = row.get(key)
                if isinstance(value, str) and _is_url(value) and "app.netlify.com/drop" not in value:
                    return value
            urls = row.get("urls")
            if isinstance(urls, list):
                for value in urls:
                    if isinstance(value, str) and _is_url(value) and "app.netlify.com/drop" not in value:
                        return value
            value = row.get("deploy_url")
            if isinstance(value, str) and _is_url(value):
                return value
            return ""

        expanded = os.path.expanduser(str(path or ""))
        needle = _norm(expanded)
        basename = Path(expanded).name

        registry_env = os.environ.get("HERMES_KANBAN_HTML_ARTIFACT_REGISTRY", "")
        for raw_registry in [p for p in registry_env.split(os.pathsep) if p.strip()]:
            registry = Path(raw_registry).expanduser()
            if not registry.exists():
                continue
            try:
                rows = registry.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in rows:
                try:
                    row = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                values = [
                    _norm(str(row.get(key, "")))
                    for key in ("html_path", "source", "out_dir", "plan_path")
                ]
                if any(needle == v or needle.endswith(v) or v.endswith(needle) for v in values if v):
                    url = _row_url(row)
                    if url:
                        return url
                if basename and any(v.endswith("/" + basename) for v in values if v):
                    url = _row_url(row)
                    if url:
                        return url

        marker = "/artifacts/plan-renders/"
        if marker in needle:
            slug = needle.split(marker, 1)[1].split("/", 1)[0].strip()
            if slug:
                return f"https://gbautomation.xyz/prds/{slug}"

        try:
            html_text = Path(expanded).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        for match in _re.finditer(r"https?://[^\s'\"<>]+", html_text):
            url = match.group(0).rstrip(".,);]")
            if "netlify.app" in url or "gbautomation.xyz" in url:
                return url
        return ""

    async def _deliver_kanban_artifacts(
        self,
        *,
        adapter,
        chat_id: str,
        metadata: dict,
        event_payload: Optional[dict],
        task,
    ) -> None:
        """Deliver artifact files referenced by a completed kanban task.

        Workers passing ``kanban_complete(artifacts=[...])`` ship absolute
        file paths through the completion event. Non-HTML artifacts keep the
        native upload behavior. HTML artifacts are converted to hosted
        Netlify/GBAutomation links and sent as text, because raw HTML uploads in
        Telegram are noisy and unusable for normal users. Set
        ``HERMES_KANBAN_HTML_ARTIFACT_DEBUG_UPLOAD=1`` for an explicit
        debug/admin fallback upload when no hosted URL can be resolved.

        Sources scanned, in priority order:
          1. ``event_payload['artifacts']`` (explicit list — preferred)
          2. ``event_payload['summary']`` (truncated first line)
          3. ``task.result`` (legacy fallback)

        Files are deduplicated, missing files are silently skipped (the
        path may have been mentioned for reference only), and delivery
        errors are logged but do not break the notifier loop.
        """
        from pathlib import Path as _Path

        candidates: list[str] = []
        seen: set[str] = set()

        def _add(path: str) -> None:
            if not path:
                return
            expanded = os.path.expanduser(path)
            if expanded in seen:
                return
            if not os.path.isfile(expanded):
                return
            seen.add(expanded)
            candidates.append(expanded)

        # 1. Explicit artifacts list in payload.
        if isinstance(event_payload, dict):
            raw = event_payload.get("artifacts")
            if isinstance(raw, (list, tuple)):
                for item in raw:
                    if isinstance(item, str):
                        _add(item)

            # 2. Paths embedded in the payload summary.
            summary = event_payload.get("summary")
            if isinstance(summary, str) and summary:
                paths, _ = adapter.extract_local_files(summary)
                for p in paths:
                    _add(p)

        # 3. Legacy: paths embedded in task.result.
        if task is not None and getattr(task, "result", None):
            result_text = str(task.result)
            paths, _ = adapter.extract_local_files(result_text)
            for p in paths:
                _add(p)

        if not candidates:
            return

        from gateway.platforms.base import BasePlatformAdapter
        candidates = BasePlatformAdapter.filter_local_delivery_paths(candidates)
        if not candidates:
            return

        _IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
        _VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".3gp"}
        _HTML_EXTS = {".html", ".htm"}

        from urllib.parse import quote as _quote

        # HTML is a special user-facing path: send a hosted URL, not a raw
        # document upload, unless an operator explicitly opts into debug upload.
        html_paths = [p for p in candidates if _Path(p).suffix.lower() in _HTML_EXTS]
        upload_candidates: list[str] = []
        debug_html_upload = os.environ.get(
            "HERMES_KANBAN_HTML_ARTIFACT_DEBUG_UPLOAD", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        for path in html_paths:
            hosted_url = self._resolve_hosted_html_artifact_url(path)
            if hosted_url:
                try:
                    await adapter.send(
                        chat_id,
                        f"📎 HTML artifact: {hosted_url}",
                        metadata=metadata,
                    )
                except Exception as exc:
                    logger.warning(
                        "kanban notifier: hosted HTML artifact link (%s) failed: %s",
                        path, exc,
                    )
            elif debug_html_upload:
                upload_candidates.append(path)
            else:
                logger.info(
                    "kanban notifier: skipped raw HTML artifact without hosted URL: %s",
                    path,
                )

        non_html_candidates = [p for p in candidates if _Path(p).suffix.lower() not in _HTML_EXTS]
        upload_candidates.extend(non_html_candidates)

        # Partition images so they ride a single send_multiple_images call
        # on platforms that support batch image uploads (Signal/Slack RPCs).
        image_paths = [p for p in upload_candidates if _Path(p).suffix.lower() in _IMAGE_EXTS]
        other_paths = [p for p in upload_candidates if _Path(p).suffix.lower() not in _IMAGE_EXTS]

        if image_paths:
            try:
                batch = [(f"file://{_quote(p)}", "") for p in image_paths]
                await adapter.send_multiple_images(
                    chat_id=chat_id, images=batch, metadata=metadata,
                )
            except Exception as exc:
                logger.warning(
                    "kanban notifier: image batch upload failed: %s", exc,
                )

        for path in other_paths:
            ext = _Path(path).suffix.lower()
            try:
                if ext in _VIDEO_EXTS:
                    await adapter.send_video(
                        chat_id=chat_id, video_path=path, metadata=metadata,
                    )
                else:
                    await adapter.send_document(
                        chat_id=chat_id, file_path=path, metadata=metadata,
                    )
            except Exception as exc:
                logger.warning(
                    "kanban notifier: artifact upload (%s) failed: %s",
                    path, exc,
                )

    async def _kanban_dispatcher_watcher(self) -> None:
        """Embedded kanban dispatcher — one tick every `dispatch_interval_seconds`.

        Gated by `kanban.dispatch_in_gateway` in config.yaml (default True).
        When true, the gateway hosts the single dispatcher for this profile:
        no separate `hermes kanban daemon` process needed. When false, the
        loop exits immediately and an external daemon is expected.

        Each tick calls :func:`kanban_db.dispatch_once` inside
        ``asyncio.to_thread`` so the SQLite WAL lock never blocks the
        event loop. Failures in one tick don't stop subsequent ticks —
        same pattern as `_kanban_notifier_watcher`.

        Shutdown: the loop checks ``self._running`` between ticks; gateway
        stop() flips it to False and cancels pending tasks, and the
        in-flight ``to_thread`` returns on its own after the current
        ``dispatch_once`` call finishes (typically <1ms on an idle board).
        """
        # Read config once at boot. If the user flips the flag later, they
        # restart the gateway; same pattern as every other background
        # watcher here. Honours HERMES_KANBAN_DISPATCH_IN_GATEWAY env var
        # as an escape hatch (false-y value disables without editing YAML).
        try:
            from hermes_cli.config import load_config as _load_config
        except Exception:
            logger.warning("kanban dispatcher: config loader unavailable; disabled")
            return
        env_override = os.environ.get("HERMES_KANBAN_DISPATCH_IN_GATEWAY", "").strip().lower()
        if env_override in {"0", "false", "no", "off"}:
            logger.info("kanban dispatcher: disabled via HERMES_KANBAN_DISPATCH_IN_GATEWAY env")
            return

        try:
            cfg = _load_config()
        except Exception as exc:
            logger.warning("kanban dispatcher: cannot load config (%s); disabled", exc)
            return
        kanban_cfg = cfg.get("kanban", {}) if isinstance(cfg, dict) else {}
        if not kanban_cfg.get("dispatch_in_gateway", True):
            logger.info(
                "kanban dispatcher: disabled via config kanban.dispatch_in_gateway=false"
            )
            return

        try:
            from hermes_cli import kanban_db as _kb
        except Exception:
            logger.warning("kanban dispatcher: kanban_db not importable; dispatcher disabled")
            return

        try:
            interval = float(kanban_cfg.get("dispatch_interval_seconds", 60) or 60)
        except (ValueError, TypeError):
            logger.warning(
                "kanban dispatcher: invalid dispatch_interval_seconds=%r, using default 60",
                kanban_cfg.get("dispatch_interval_seconds"),
            )
            interval = 60.0
        interval = max(interval, 1.0)  # sanity floor — tighter than this is a footgun

        # Read max_spawn config to limit concurrent kanban tasks
        max_spawn = kanban_cfg.get("max_spawn", None)
        if max_spawn is not None:
            logger.info(f"kanban dispatcher: max_spawn={max_spawn}")

        # Cap the number of simultaneously running tasks so slow workers
        # (local LLMs, resource-constrained hosts) don't pile up and time
        # out. When set, the dispatcher skips spawning when the board
        # already has this many tasks in 'running' status.
        raw_max_in_progress = kanban_cfg.get("max_in_progress", None)
        max_in_progress = None
        if raw_max_in_progress is not None:
            try:
                max_in_progress = int(raw_max_in_progress)
            except (TypeError, ValueError):
                logger.warning(
                    "kanban dispatcher: invalid kanban.max_in_progress=%r; ignoring",
                    raw_max_in_progress,
                )
                max_in_progress = None
            else:
                if max_in_progress < 1:
                    logger.warning(
                        "kanban dispatcher: kanban.max_in_progress=%r is below 1; ignoring",
                        raw_max_in_progress,
                    )
                    max_in_progress = None
                else:
                    logger.info(f"kanban dispatcher: max_in_progress={max_in_progress}")

        raw_failure_limit = kanban_cfg.get("failure_limit", _kb.DEFAULT_FAILURE_LIMIT)
        try:
            failure_limit = int(raw_failure_limit)
        except (TypeError, ValueError):
            logger.warning(
                "kanban dispatcher: invalid kanban.failure_limit=%r; using default %d",
                raw_failure_limit,
                _kb.DEFAULT_FAILURE_LIMIT,
            )
            failure_limit = _kb.DEFAULT_FAILURE_LIMIT
        if failure_limit < 1:
            logger.warning(
                "kanban dispatcher: kanban.failure_limit=%r is below 1; using default %d",
                raw_failure_limit,
                _kb.DEFAULT_FAILURE_LIMIT,
            )
            failure_limit = _kb.DEFAULT_FAILURE_LIMIT

        # Read stale_timeout_seconds — 0 disables stale detection.
        raw_stale = kanban_cfg.get("dispatch_stale_timeout_seconds", 0)
        try:
            stale_timeout_seconds = int(raw_stale or 0)
        except (TypeError, ValueError):
            logger.warning(
                "kanban dispatcher: invalid kanban.dispatch_stale_timeout_seconds=%r; "
                "disabling stale detection",
                raw_stale,
            )
            stale_timeout_seconds = 0

        # Read kanban.default_assignee — fallback profile for tasks
        # created without an explicit assignee (e.g. via the dashboard).
        # When set, the dispatcher applies it to unassigned ready tasks
        # instead of skipping them indefinitely (#27145). Empty string
        # (the schema default) means "no fallback, keep skipping" —
        # backward-compatible with existing installs.
        default_assignee = (kanban_cfg.get("default_assignee") or "").strip() or None
        if default_assignee:
            logger.info(
                "kanban dispatcher: default_assignee=%r (unassigned ready tasks "
                "will route to this profile)",
                default_assignee,
            )

        # Read kanban.max_in_progress_per_profile — per-profile concurrency
        # cap (#21582). When set, no single profile gets more than N
        # workers running at once, even if the global max_in_progress
        # would allow it. Prevents one profile's local model / API quota
        # / browser pool from being overwhelmed by a fan-out.
        raw_per_profile = kanban_cfg.get("max_in_progress_per_profile", None)
        max_in_progress_per_profile = None
        if raw_per_profile is not None:
            try:
                max_in_progress_per_profile = int(raw_per_profile)
            except (TypeError, ValueError):
                logger.warning(
                    "kanban dispatcher: invalid kanban.max_in_progress_per_profile=%r; ignoring",
                    raw_per_profile,
                )
                max_in_progress_per_profile = None
            else:
                if max_in_progress_per_profile < 1:
                    logger.warning(
                        "kanban dispatcher: kanban.max_in_progress_per_profile=%r is below 1; ignoring",
                        raw_per_profile,
                    )
                    max_in_progress_per_profile = None
                else:
                    logger.info(
                        "kanban dispatcher: max_in_progress_per_profile=%d",
                        max_in_progress_per_profile,
                    )

        # Initial delay so the gateway finishes wiring adapters before the
        # dispatcher spawns workers (those workers may hit gateway notify
        # subscriptions etc.). Matches the notifier watcher's delay.
        await asyncio.sleep(5)

        # Health telemetry mirrored from `_cmd_daemon`: warn when ready
        # queue is non-empty but spawns are 0 for N consecutive ticks —
        # usually means broken PATH, missing venv, or credential loss.
        HEALTH_WINDOW = 6
        bad_ticks = 0
        last_warn_at = 0
        # Avoid hot-looping corrupt-looking board DBs, but do not suppress
        # same-fingerprint retries forever: transient WAL/open races can
        # surface as "database disk image is malformed" for one tick.
        CORRUPT_BOARD_RETRY_AFTER_SECONDS = 300
        disabled_corrupt_boards: dict[
            str, tuple[tuple[str, int | None, int | None], float]
        ] = {}

        def _board_db_fingerprint(slug: str) -> tuple[str, int | None, int | None]:
            path = _kb.kanban_db_path(slug)
            try:
                resolved = str(path.expanduser().resolve())
            except Exception:
                resolved = str(path)
            try:
                stat = path.stat()
            except OSError:
                return (resolved, None, None)
            return (resolved, stat.st_mtime_ns, stat.st_size)

        def _is_corrupt_board_db_error(exc: Exception) -> bool:
            corrupt_guard_error = getattr(_kb, "KanbanDbCorruptError", None)
            if corrupt_guard_error is not None and isinstance(exc, corrupt_guard_error):
                return True
            if not isinstance(exc, sqlite3.DatabaseError):
                return False
            msg = str(exc).lower()
            return (
                "file is not a database" in msg
                or "database disk image is malformed" in msg
            )

        def _tick_once_for_board(slug: str) -> "Optional[object]":
            """Run one dispatch_once for a specific board.

            Runs in a worker thread via `asyncio.to_thread`. `board=slug`
            is passed through `dispatch_once` so `resolve_workspace` and
            `_default_spawn` see the right paths. The per-board DB is
            opened explicitly so concurrent boards never share a
            connection handle or accidentally claim across each other.
            """
            conn = None
            fingerprint = _board_db_fingerprint(slug)
            disabled_entry = disabled_corrupt_boards.get(slug)
            if disabled_entry is not None:
                disabled_fingerprint, disabled_at = disabled_entry
                age = time.monotonic() - disabled_at
                if (
                    disabled_fingerprint == fingerprint
                    and age < CORRUPT_BOARD_RETRY_AFTER_SECONDS
                ):
                    return None
                if disabled_fingerprint == fingerprint:
                    logger.info(
                        "kanban dispatcher: board %s database fingerprint unchanged "
                        "after %.0fs quarantine; retrying dispatch",
                        slug,
                        age,
                    )
                else:
                    logger.info(
                        "kanban dispatcher: board %s database changed; retrying dispatch",
                        slug,
                    )
                disabled_corrupt_boards.pop(slug, None)
            try:
                conn = _kb.connect(board=slug)
                # `connect()` runs the schema + idempotent migration on
                # first open per process; the previous explicit
                # `init_db()` call here busted the per-process cache and
                # re-ran the migration on a second connection, racing
                # the first. See the matching comment in
                # `_kanban_notifier_watcher` and issue #21378.
                return _kb.dispatch_once(
                    conn,
                    board=slug,
                    max_spawn=max_spawn,
                    max_in_progress=max_in_progress,
                    failure_limit=failure_limit,
                    stale_timeout_seconds=stale_timeout_seconds,
                    default_assignee=default_assignee,
                    max_in_progress_per_profile=max_in_progress_per_profile,
                )
            except sqlite3.DatabaseError as exc:
                if _is_corrupt_board_db_error(exc):
                    disabled_corrupt_boards[slug] = (fingerprint, time.monotonic())
                    logger.error(
                        "kanban dispatcher: board %s database %s is not a valid "
                        "SQLite database; pausing dispatch for this board until "
                        "the file changes, the gateway restarts, or the "
                        "quarantine timer expires. Move or restore the file, "
                        "then run `hermes kanban init` if you need a fresh board.",
                        slug,
                        fingerprint[0],
                    )
                    return None
                logger.exception("kanban dispatcher: tick failed on board %s", slug)
                return None
            except Exception as exc:
                if _is_corrupt_board_db_error(exc):
                    disabled_corrupt_boards[slug] = (fingerprint, time.monotonic())
                    logger.error(
                        "kanban dispatcher: board %s database %s is not a valid "
                        "SQLite database; pausing dispatch for this board until "
                        "the file changes, the gateway restarts, or the "
                        "quarantine timer expires. Move or restore the file, "
                        "then run `hermes kanban init` if you need a fresh board.",
                        slug,
                        fingerprint[0],
                    )
                    return None
                logger.exception("kanban dispatcher: tick failed on board %s", slug)
                return None
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        def _tick_once() -> "list[tuple[str, Optional[object]]]":
            """Run one dispatch_once per board. Returns (slug, result) pairs.

            Enumerating boards on every tick keeps the dispatcher honest
            when users create a new board mid-run: no restart required,
            the next tick picks it up automatically.
            """
            try:
                boards = _kb.list_boards(include_archived=False)
            except Exception:
                boards = [_kb.read_board_metadata(_kb.DEFAULT_BOARD)]
            out: list[tuple[str, "Optional[object]"]] = []
            for b in boards:
                slug = b.get("slug") or _kb.DEFAULT_BOARD
                out.append((slug, _tick_once_for_board(slug)))
            return out

        def _ready_nonempty() -> bool:
            """Cheap probe: is there at least one ready+assigned+unclaimed
            task on ANY board whose assignee maps to a real Hermes profile
            (i.e. one the dispatcher would actually spawn for)?

            Tasks assigned to control-plane lanes (e.g. ``orion-cc``,
            ``orion-research``) are pulled by terminals via
            ``claim_task`` directly and never spawnable, so a queue full
            of those is "correctly idle", not "stuck". Filtering them out
            here keeps the stuck-warn fire only on real failures (broken
            PATH, missing venv, credential loss for a real Hermes profile).
            """
            try:
                boards = _kb.list_boards(include_archived=False)
            except Exception:
                boards = [_kb.read_board_metadata(_kb.DEFAULT_BOARD)]
            for b in boards:
                slug = b.get("slug") or _kb.DEFAULT_BOARD
                conn = None
                try:
                    conn = _kb.connect(board=slug)
                    if _kb.has_spawnable_ready(conn):
                        return True
                    if _kb.has_spawnable_review(conn):
                        return True
                except Exception:
                    continue
                finally:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
            return False

        # Auto-decompose: turn fresh triage tasks into ready workgraphs
        # before the dispatcher fans out workers. Gated by
        # ``kanban.auto_decompose`` (default True). Capped by
        # ``kanban.auto_decompose_per_tick`` (default 3) so a bulk-load
        # of triage tasks doesn't burst-spend the aux LLM in one tick;
        # remainder defers to subsequent ticks.
        auto_decompose_enabled = bool(kanban_cfg.get("auto_decompose", True))
        try:
            auto_decompose_per_tick = int(
                kanban_cfg.get("auto_decompose_per_tick", 3) or 3
            )
        except (TypeError, ValueError):
            auto_decompose_per_tick = 3
        if auto_decompose_per_tick < 1:
            auto_decompose_per_tick = 1

        def _auto_decompose_tick() -> int:
            """Run the auto-decomposer for up to N triage tasks across all
            boards. Returns the number of triage tasks that were
            successfully decomposed or specified this tick.
            """
            try:
                from hermes_cli import kanban_decompose as _decomp
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "kanban auto-decompose: import failed (%s); skipping", exc,
                )
                return 0
            try:
                boards = _kb.list_boards(include_archived=False)
            except Exception:
                boards = [_kb.read_board_metadata(_kb.DEFAULT_BOARD)]
            attempted = 0
            successes = 0
            for b in boards:
                slug = b.get("slug") or _kb.DEFAULT_BOARD
                if attempted >= auto_decompose_per_tick:
                    break
                # Pin this board for the duration of the call — same
                # pattern as the dashboard specify endpoint. The
                # decomposer module connects with no board kwarg and
                # relies on the env var.
                prev_env = os.environ.get("HERMES_KANBAN_BOARD")
                try:
                    os.environ["HERMES_KANBAN_BOARD"] = slug
                    try:
                        triage_ids = _decomp.list_triage_ids()
                    except Exception as exc:
                        logger.debug(
                            "kanban auto-decompose: list_triage_ids failed on board %s (%s)",
                            slug, exc,
                        )
                        triage_ids = []
                    for tid in triage_ids:
                        if attempted >= auto_decompose_per_tick:
                            break
                        attempted += 1
                        try:
                            outcome = _decomp.decompose_task(
                                tid, author="auto-decomposer",
                            )
                        except Exception:
                            logger.exception(
                                "kanban auto-decompose: decompose_task crashed on %s",
                                tid,
                            )
                            continue
                        if outcome.ok:
                            successes += 1
                            if outcome.fanout and outcome.child_ids:
                                logger.info(
                                    "kanban auto-decompose [%s]: %s → %d children",
                                    slug, tid, len(outcome.child_ids),
                                )
                            else:
                                logger.info(
                                    "kanban auto-decompose [%s]: %s → single task (no fanout)",
                                    slug, tid,
                                )
                        else:
                            # Common no-op reasons (no aux client configured) shouldn't
                            # spam logs every tick. Log at debug.
                            logger.debug(
                                "kanban auto-decompose [%s]: %s skipped: %s",
                                slug, tid, outcome.reason,
                            )
                finally:
                    if prev_env is None:
                        os.environ.pop("HERMES_KANBAN_BOARD", None)
                    else:
                        os.environ["HERMES_KANBAN_BOARD"] = prev_env
            return successes

        logger.info(
            "kanban dispatcher: embedded in gateway (interval=%.1fs)", interval
        )
        while self._running:
            try:
                # Reap zombie children before per-board work so a board DB
                # failure cannot block cleanup of unrelated workers.
                pids = await asyncio.to_thread(_kb.reap_worker_zombies)
                if pids:
                    logger.info(
                        "kanban dispatcher: reaped %d zombie worker(s), pids=%s",
                        len(pids),
                        pids,
                    )
            except Exception:
                logger.exception("kanban dispatcher: zombie reaper failed")

            try:
                if auto_decompose_enabled:
                    await asyncio.to_thread(_auto_decompose_tick)
                results = await asyncio.to_thread(_tick_once)
                any_spawned = False
                for slug, res in (results or []):
                    if res is not None and getattr(res, "spawned", None):
                        any_spawned = True
                        # Quiet by default — only log when something actually
                        # happened, so an idle gateway stays silent.
                        logger.info(
                            "kanban dispatcher [%s]: spawned=%d reclaimed=%d "
                            "crashed=%d timed_out=%d promoted=%d auto_blocked=%d",
                            slug,
                            len(res.spawned),
                            res.reclaimed,
                            len(res.crashed) if hasattr(res.crashed, "__len__") else 0,
                            len(res.timed_out) if hasattr(res.timed_out, "__len__") else 0,
                            res.promoted,
                            len(res.auto_blocked) if hasattr(res.auto_blocked, "__len__") else 0,
                        )
                # Health telemetry (aggregate across boards)
                ready_pending = await asyncio.to_thread(_ready_nonempty)
                if ready_pending and not any_spawned:
                    bad_ticks += 1
                else:
                    bad_ticks = 0
                if bad_ticks >= HEALTH_WINDOW:
                    now = int(time.time())
                    if now - last_warn_at >= 300:
                        logger.warning(
                            "kanban dispatcher stuck: ready queue non-empty for "
                            "%d consecutive ticks but 0 workers spawned. Check "
                            "profile health (venv, PATH, credentials) and "
                            "`hermes kanban list --status ready`.",
                            bad_ticks,
                        )
                        last_warn_at = now
            except asyncio.CancelledError:
                logger.debug("kanban dispatcher: cancelled")
                raise
            except Exception:
                logger.exception("kanban dispatcher: unexpected watcher error")

            # Sleep in 1s slices so shutdown is snappy — otherwise a stop()
            # waits up to `interval` seconds for the current sleep to finish.
            slept = 0.0
            while slept < interval and self._running:
                await asyncio.sleep(min(1.0, interval - slept))
                slept += 1.0
