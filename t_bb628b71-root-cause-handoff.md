# Kanban notifier artifact contamination — research handoff

Task: t_bb628b71
Scope: read-only source-backed trace in /Users/greg/repos/hermes-agent-worktrees/t_36c095b2-kanban-artifact-notifications.
Fixture DB: /Users/greg/.hermes/kanban/boards/gbautomation/kanban.db.

## Trace

1. Worker tool input enters `tools/kanban_tools.py:_handle_complete` at lines 392-478.
   - `artifacts` is accepted as string/list at lines 406 and 420-431.
   - The list is merged into `metadata["artifacts"]` at lines 432-458.
   - The tool calls `hermes_cli.kanban_db.complete_task(..., summary=..., metadata=...)` at lines 473-478.

2. DB completion event is created in `hermes_cli/kanban_db.py:complete_task` at lines 2790-2896.
   - Task status/result/run handoff is persisted at lines 2815-2854.
   - The completed-event `summary` is a first-line 400-character preview at lines 2866-2875.
   - `metadata["artifacts"]` is promoted into `completed_payload["artifacts"]` at lines 2878-2891.
   - The terminal `completed` event is appended at lines 2892-2896.

3. Subscription notifier collection is in `gateway/run.py:_kanban_notifier_watcher` at lines 4565-4652.
   - It enumerates boards and notify subscriptions at lines 4565-4610.
   - It claims unseen terminal events via `claim_unseen_events_for_sub` at lines 4625-4632.
   - It loads the task row at line 4635.

4. Text notification and upload trigger are in `gateway/run.py` lines 4689-4774.
   - Completed text uses event payload summary first, then `task.result` fallback at lines 4689-4708.
   - After the text send succeeds, completed events call `_deliver_kanban_artifacts` at lines 4752-4769.

5. Artifact upload policy currently implemented in `gateway/run.py:_deliver_kanban_artifacts` at lines 4892-4994.
   - Docstring says sources scanned in priority order: event payload artifacts, event payload summary, task.result at lines 4908-4912.
   - Explicit event artifacts are added first at lines 4933-4940.
   - Summary text is parsed for local paths at lines 4941-4947.
   - Legacy `task.result` text is parsed for local paths at lines 4948-4953.
   - Existing files are deduped by absolute expanded path only at lines 4922-4931.
   - Images batch through `send_multiple_images`; other files go through `send_video` or `send_document` at lines 4963-4994.

6. Bare local-path extraction comes from `gateway/platforms/base.py:extract_local_files` at lines 2180-2267.
   - It matches absolute/tilde paths ending in a broad extension list including `.md`, `.json`, `.html`, etc. at lines 2204-2229.
   - It ignores paths inside code spans at lines 2232-2245.
   - It validates `os.path.isfile` and dedupes by expanded path at lines 2247-2259.

7. Telegram renderer/upload endpoint is `gateway/platforms/telegram.py`.
   - `send_multiple_images` handles local file:// image albums at lines 3464-3560.
   - `send_document` opens and uploads any existing local file at lines 3700-3738.
   - There is no task ownership check in the Telegram upload path; the notifier must pass the right candidate list before renderer dispatch.

8. Passive/dashboard subscription routing in this branch is `plugins/kanban/dashboard/plugin_api.py` lines 1480-1604.
   - It creates the same notify-sub rows as CLI/gateway routes, not a separate artifact-discovery path.
   - The referenced `gateway/kanban_notification_routes.py:artifact_links_from_payload` is not present in this target worktree; no file by that name exists here.

## Root cause

The contaminating behavior is in `gateway/run.py:_deliver_kanban_artifacts`: even when a completion event already has an explicit `payload["artifacts"]` list, the notifier continues scanning `payload["summary"]` and legacy `task.result` for any existing local paths. Summary/result fields often contain context paths, stale references, previous report paths, or paths mentioned as rejected/unrelated evidence. Because `_add` only checks existence and dedupes exact path strings, those reference paths can be uploaded as native attachments alongside the true deliverables.

This is a policy bug, not an event-payload bug: `tools/kanban_tools.py` and `hermes_cli/kanban_db.py` correctly carry explicit artifacts into the completed-event payload. The upload layer violates the implied precedence by treating explicit artifacts as additive rather than authoritative.

A secondary blast-radius concern is missing explicit artifacts: `_add` silently skips absent files. If all explicit paths are missing, the current code still falls through to summary/result path extraction and may upload unrelated existing files, masking the missing deliverables.

## Current tests and gap

Scoped tests still pass: `python3 -m pytest tests/gateway/test_kanban_notifier.py tests/hermes_cli/test_kanban_notify.py -q` returned `18 passed in 0.42s`.

Existing artifact tests at `tests/hermes_cli/test_kanban_notify.py` lines 485-571 verify happy-path explicit artifact uploads and lines 574-640 verify missing explicit artifact paths are skipped. They do not assert that summary/result extraction is disabled when explicit artifacts exist, and they do not cover mixed explicit-artifact plus stale-summary/reference-path contamination.

## Fixture payloads inspected

### t_98bee624

Completed event: id 111909, run 18003.
Payload before fix:

```json
{
  "result_len": 0,
  "summary": "receipt_only: TAC Lead settled the V2 repo boundary and dispatched the isolated implementation graph. Primary package is /Users/greg/repos/gbautomation/second-brain/intelligence/architecture/vinyl-dj-v2/README.md; Vinyl Atlas stays in gbauto/vinyl-atlas, DJ Set Intelligence is routed to separate gbauto/dj-set-intelligence via TAC Ops.",
  "verified_cards": ["t_e10ad873", "t_1ee21a77", "t_f960d6d6", "t_435665c4", "t_6d8734e6", "t_debfe2ef", "t_e9edd363", "t_4f80d20f", "t_0ae1a9e1"],
  "artifacts": [
    "/Users/greg/repos/gbautomation/second-brain/intelligence/architecture/vinyl-dj-v2/README.md",
    "/Users/greg/repos/gbautomation/second-brain/intelligence/architecture/vinyl-dj-v2/music-domain-contracts.ts",
    "/Users/greg/repos/gbautomation/second-brain/intelligence/architecture/vinyl-dj-v2/music-domain-v2-migration-plan.sql",
    "/Users/greg/repos/gbautomation/second-brain/intelligence/architecture/vinyl-dj-v2/manifest.json"
  ]
}
```

Expected after-fix upload list:
- /Users/greg/repos/gbautomation/second-brain/intelligence/architecture/vinyl-dj-v2/README.md
- /Users/greg/repos/gbautomation/second-brain/intelligence/architecture/vinyl-dj-v2/music-domain-contracts.ts
- /Users/greg/repos/gbautomation/second-brain/intelligence/architecture/vinyl-dj-v2/music-domain-v2-migration-plan.sql
- /Users/greg/repos/gbautomation/second-brain/intelligence/architecture/vinyl-dj-v2/manifest.json

Reject:
- unrelated 2026-07-07 docs
- unrelated 2026-07-11 docs

Note: README.md is both explicit and mentioned in summary; dedupe currently prevents a duplicate here, but the fixture is still important because summary parsing must not be needed to include it.

### t_59ef1e8c

Completed event: id 115600, run 18024.
Payload before fix:

```json
{
  "result_len": 0,
  "summary": "Published the combined V2 closeout + Telegram board-aware bot branding report and verified anonymous HTTPS readback at https://files.catbox.moe/6m6vb3.html. The report corrects the t_98bee624 17/17 tree, excludes stale July 7/11 Telegram artifacts, verifies Vinyl PR #6 and DJ PR #1/#2 live, and recommends stable bot identity with board-aware name/description/commands rather than username churn.",
  "artifacts": [
    "/Users/greg/.hermes/kanban/boards/gbautomation/workspaces/t_59ef1e8c/v2-telegram-branding-report/v2-closeout-telegram-board-branding-t_59ef1e8c.html",
    "/Users/greg/.hermes/kanban/boards/gbautomation/workspaces/t_59ef1e8c/v2-telegram-branding-report/t_59ef1e8c-manifest.json",
    "/Users/greg/.hermes/kanban/boards/gbautomation/workspaces/t_59ef1e8c/v2-telegram-branding-report/public-dom-smoke.json"
  ]
}
```

Expected after-fix upload/link list:
- /Users/greg/.hermes/kanban/boards/gbautomation/workspaces/t_59ef1e8c/v2-telegram-branding-report/v2-closeout-telegram-board-branding-t_59ef1e8c.html
- /Users/greg/.hermes/kanban/boards/gbautomation/workspaces/t_59ef1e8c/v2-telegram-branding-report/t_59ef1e8c-manifest.json
- /Users/greg/.hermes/kanban/boards/gbautomation/workspaces/t_59ef1e8c/v2-telegram-branding-report/public-dom-smoke.json
- verified HTTPS URL: https://files.catbox.moe/6m6vb3.html

Reject:
- Gantt summary
- duplicate `index.html` entries

Note: on current disk, the three explicit local workspace paths are no longer present, so current uploader candidates are empty after `os.path.isfile` checks. That makes the missing-explicit-artifact fallback risk especially important: the corrected policy should not substitute stale summary/result files when explicit task-specific paths are unavailable.

## Authoritative artifact policy for builder

Implement a task-owned, explicit-first policy:

1. If `event_payload["artifacts"]` is a non-empty list, it is authoritative for native uploads. Do not scan `event_payload["summary"]` or `task.result` in that case.
2. Dedupe explicit artifacts after normalizing/expanding paths. Preserve first-seen order.
3. Upload only existing local files; record/log missing explicit files as skipped, but do not compensate by scanning prose.
4. Add optional task ownership checks before legacy fallback: for kanban workspace files, require the path to be under that task's workspace; for repo/client artifacts, prefer a task-specific manifest or explicit list.
5. Only when there is no explicit artifact list should legacy prose extraction run, and then only with all of these gates:
   - source text is the completed event summary and/or `task.result` for the same task,
   - files exist,
   - files pass ownership/age checks,
   - duplicates are removed by resolved path,
   - URL strings are not treated as local paths; verified public URLs should be carried as link text, not native uploads.
6. Passive route text/link extraction should read only explicit event payload artifacts and verified URL fields/metadata; it should not perform local filesystem discovery.

## Blast radius

Affected path is completion notifications for any kanban task with a notify subscription on any platform adapter, because the shared `GatewayRunner._deliver_kanban_artifacts` candidate list feeds Telegram, Discord, Slack, Signal, etc. Platform-specific upload functions merely upload what they are given. Normal live assistant responses still use `gateway/run.py:_deliver_media_from_response` lines 11145-11255 and should not be changed for this kanban-specific fix.

No secrets or chat IDs were included in this handoff. Fixture inspection selected task/event/run fields only and did not dump notify subscription rows.
