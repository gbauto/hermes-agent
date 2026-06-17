# Telegram Kanban blocker buttons rollout notes

Date: 2026-06-12
Task: t_f73f83aa
Repo: /Users/greg/repos/hermes-agent

## What changed

- Blocked Kanban notifications delivered to Telegram now use a short mobile digest.
- Telegram blocked-card notifications include inline buttons:
  - ✅ Promote: resolves a signed short token, then calls the canonical Kanban DB unblock path.
  - ⏭ Keep blocked: resolves a signed short token and dismisses the keyboard without changing task state.
  - Open board: resolves a signed short token and shows the matching `hermes kanban --board <board> show <task>` command.
- Telegram send metadata now supports `telegram_inline_keyboard` and passes it as `reply_markup` only on the first chunk of a message.
- Callback payloads are signed short tokens: `kb:<action>:<sid>:<sig>`.
- Callback payloads do not embed board slug, task id, task title, body, block reason, assignee, paths, comments, raw metadata, or secrets.
- Callback lookup state is persisted in the Kanban DB `kanban_callback_actions` table so gateway restarts do not strand buttons.
- Promote uses `hermes_cli.kanban_db.unblock_task()` through `connect_closing(board=...)` instead of shelling out to a hardcoded Hermes binary path.

## Active runtime impact

The canonical durable fork is `/Users/greg/repos/hermes-agent`, but the machine's active Hermes runtime was previously observed under `/Users/greg/.openclaw/workspace/hermes-agent`.

This patch changes only the canonical fork. It will not affect the live gateway until the runtime/LaunchAgents are pointed at this checkout or this branch is deployed into the active runtime deliberately.

Before switching the live gateway:

1. Run the focused gateway tests from `/Users/greg/repos/hermes-agent`:
   `python -m pytest tests/gateway/test_telegram_kanban_blocker_buttons.py tests/gateway/test_telegram_approval_buttons.py tests/gateway/test_telegram_clarify_buttons.py tests/gateway/test_telegram_slash_confirm.py tests/gateway/test_telegram_format.py -q -o addopts=`
2. Confirm no hardcoded OpenClaw path remains in gateway framework code:
   search for `/Users/greg/.openclaw` under `gateway/`.
3. Restart the target gateway profile only after confirming its `hermes` executable points at the deployed checkout.
4. Smoke-test with one blocked Kanban card subscribed from Telegram.

## Rollback

- Code rollback: revert the changes to:
  - `hermes_cli/kanban_db.py` (`kanban_callback_actions` schema/helpers)
  - `gateway/kanban_watchers.py`
  - `gateway/run.py`
  - `gateway/platforms/telegram.py`
  - `tests/gateway/test_telegram_kanban_blocker_buttons.py`
- Runtime rollback: repoint LaunchAgent/program arguments to the previously working Hermes checkout, then restart the affected gateway profile.
- Data rollback is not required. Existing rows in `kanban_callback_actions` expire naturally and are inert without the Telegram handler. The promote button writes task state only through the existing Kanban `unblock_task()` API, which appends the normal `unblocked` event and preserves board audit history.
