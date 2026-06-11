---
name: kanban-orchestrator
description: Decomposition playbook + anti-temptation rules for an orchestrator profile routing work through Kanban. The "don't do the work yourself" rule and the basic lifecycle are auto-injected into every kanban worker's system prompt; this skill is the deeper playbook when you're specifically playing the orchestrator role.
version: 3.0.0
platforms: [linux, macos, windows]
environments: [kanban]
metadata:
  hermes:
    tags: [kanban, multi-agent, orchestration, routing]
    related_skills: [kanban-worker]
---

# Kanban Orchestrator — Decomposition Playbook

> The **core worker lifecycle** (including the `kanban_create` fan-out pattern and the "decompose, don't execute" rule) is auto-injected into every kanban process via the `KANBAN_GUIDANCE` system-prompt block. This skill is the deeper playbook when you're an orchestrator profile whose whole job is routing.

## Profiles are user-configured — not a fixed roster

Hermes setups vary widely. Some users run a single profile that does everything; some run a small fleet (`docker-worker`, `cron-worker`); some run a curated specialist team they've named themselves. There is **no default specialist roster** — the orchestrator skill does not know what profiles exist on this machine.

Before fanning out, you must ground the decomposition in the profiles that actually exist. The dispatcher silently fails to spawn unknown assignee names — it doesn't autocorrect, doesn't suggest, doesn't fall back. So a card assigned to `researcher` on a setup that only has `docker-worker` just sits in `ready` forever.

**Step 0: discover available profiles before planning.**

Use one of these:

- `hermes profile list` — prints the table of profiles configured on this machine. Run it through your terminal tool if you have one; otherwise ask the user.
- `kanban_list(assignee="<some-name>")` — sanity-check a single name. Returns an empty list (rather than an error) for an unknown assignee, so this only confirms a name you're already considering.
- **Just ask the user.** "What profiles do you have set up?" is a fine first turn when the goal needs more than one specialist.

Cache the result in your working memory for the rest of the conversation. Re-asking every turn wastes a tool call.

## Board lifecycle rule — new board per PRD

For TAC-style project execution, **create a fresh board for each distinct PRD / plan / sprint / work package** and pair it with **one git worktree-backed PRD branch by default**. Do not reuse a generic team board such as `tac-team` as the execution board, and do not use shared `dir:` workspaces for durable TAC work.

Rule of thumb: **one PRD = one Kanban board = one worktree branch**. Multiple cards on that board may share the same PRD worktree/branch when they are cooperating on one deliverable. Create separate task branches only when parallel file edits would collide, when two implementation lanes need independent review, or when the user explicitly asks for branch-per-task isolation.

Why: each board is a SQLite database and operational audit log. Reusing one board across unrelated PRDs mixes task graphs, stale blocked cards, crash-loop diagnostics, and recovery state. If the board corrupts, every unrelated PRD on that shared board is affected. A per-PRD board limits blast radius and keeps the audit trail clean. A PRD-level worktree provides file isolation and a single reviewable integration branch without creating unnecessary merge overhead for every small card.

Default pattern:

```bash
hermes kanban boards create <prd-slug-yyyymmdd> \
  --name "<Human Plan Name>" \
  --description "Per-PRD board for <goal>." \
  --icon "🌿" \
  --color '#2F855A' \
  --default-workdir /path/to/repo

hermes kanban --board <prd-slug-yyyymmdd> create "<task>" \
  --workspace worktree:/path/to/repo \
  --branch kanban/<prd-slug-yyyymmdd> \
  ...

hermes kanban --board <prd-slug-yyyymmdd> dispatch --max <N>
```

Acceptable exceptions:
- Tenant/operator boards that are explicitly long-lived control planes.
- Tracking-only boards that point at existing execution tasks and do not run workers.
- Tiny throwaway smoke-test boards that are deleted/archived immediately.

If you accidentally created work on a reusable/shared board, stop dispatch, reclaim/block the old tasks, create a fresh per-PRD board, recreate the task graph there, and report the correction.

## When to use the board (vs. just doing the work)

Create Kanban tasks when any of these are true:

1. **Multiple specialists are needed.** Research + analysis + writing is three profiles.
2. **The work should survive a crash or restart.** Long-running, recurring, or important.
3. **The user might want to interject.** Human-in-the-loop at any step.
4. **Multiple subtasks can run in parallel.** Fan-out for speed.
5. **Review / iteration is expected.** A reviewer profile loops on drafter output.
6. **The audit trail matters.** Board rows persist in SQLite forever.

If *none* of those apply — it's a small one-shot reasoning task — use `delegate_task` instead or answer the user directly.

## The anti-temptation rules

Your job description says "route, don't execute." The rules that enforce that:

- **Do not execute the work yourself.** Your restricted toolset usually doesn't even include terminal/file/code/web for implementation. If you find yourself "just fixing this quickly" — stop and create a task for the right specialist.
- **For any concrete task, create a Kanban task and assign it.** Every single time.
- **Split multi-lane requests before creating cards.** A user prompt can contain several independent workstreams. Extract those lanes first, then create one card per lane instead of bundling unrelated work into a single implementer card.
- **Run independent lanes in parallel.** If two cards do not need each other's output, leave them unlinked so the dispatcher can fan them out. Link only true data dependencies.
- **Never create dependent work as independent ready cards.** If a card must wait for another card, pass `parents=[...]` in the original `kanban_create` call. Do not create it first and link it later, and do not rely on prose like "wait for T1" inside the body.
- **If no specialist fits the available profiles, ask the user which profile to create or which existing profile to use.** Do not invent profile names; the dispatcher will silently drop unknown assignees.
- **Decompose, route, and summarize — that's the whole job.**

## Decomposition playbook

### Step 1 — Understand the goal

Ask clarifying questions if the goal is ambiguous. Cheap to ask; expensive to spawn the wrong fleet.

### Step 2 — Sketch the task graph

Before creating anything, draft the graph out loud (in your response to the user). Treat every concrete workstream as a candidate card:

1. Choose or create the **fresh per-PRD board** for this work package. Prefer `prd-slug-yyyymmdd` over a reusable team board.
2. Extract the lanes from the request.
3. Map each lane to one of the profiles you discovered in Step 0. If a lane doesn't fit any existing profile, ask the user which to use or create.
4. Decide whether each lane is independent or gated by another lane.
5. Create independent lanes as parallel cards with no parent links.
6. Create synthesis/review/integration cards with parent links to the lanes they depend on. A child created with unfinished parents starts in `todo`; the dispatcher promotes it to `ready` only after every parent is done.

Examples of prompts that should fan out (using placeholder profile names — substitute whatever exists on the user's setup):

- "Build an app" → one card to a design-oriented profile for product/UI direction, one or two cards to engineering profiles for implementation, plus a later integration/review card if the user has a reviewer profile.
- "Fix blockers and check model variants" → one implementation card for the blocker fixes plus one discovery/research card for config/source verification. A final reviewer card can depend on both.
- "Research docs and implement" → a docs-research card can run in parallel with a codebase-discovery card; implementation waits only if it truly needs those findings.
- "Analyze this screenshot and find the related code" → one card to a vision-capable profile for the visual analysis while another searches the codebase.

Words like "also," "finally," or "and" do not automatically imply a dependency. They often mean "make sure this is covered before reporting back." Only link tasks when one card cannot start until another card's output exists.

Show the graph to the user before creating cards. Let them correct it — including which actual profile name should own each lane.

### Step 3 — Create tasks and link

Use the profile names from Step 0. The example below uses placeholders `<profile-A>`, `<profile-B>`, `<profile-C>` — replace them with what the user actually has.

```python
t1 = kanban_create(
    title="research: Postgres cost vs current",
    assignee="<profile-A>",  # whichever profile handles research on this setup
    body="Compare estimated infrastructure costs, migration costs, and ongoing ops costs over a 3-year window. Sources: AWS/GCP pricing, team time estimates, current Postgres bills from peers.",
    tenant=os.environ.get("HERMES_TENANT"),
)["task_id"]

t2 = kanban_create(
    title="research: Postgres performance vs current",
    assignee="<profile-A>",  # same profile, run in parallel
    body="Compare query latency, throughput, and scaling characteristics at our expected data volume (~500GB, 10k QPS peak). Sources: benchmark papers, public case studies, pgbench results if easy.",
)["task_id"]

t3 = kanban_create(
    title="synthesize migration recommendation",
    assignee="<profile-B>",  # whichever profile does synthesis/analysis
    body="Read the findings from T1 (cost) and T2 (performance). Produce a 1-page recommendation with explicit trade-offs and a go/no-go call.",
    parents=[t1, t2],
)["task_id"]

t4 = kanban_create(
    title="draft decision memo",
    assignee="<profile-C>",  # whichever profile drafts user-facing prose
    body="Turn the analyst's recommendation into a 2-page memo for the CTO. Match the tone of previous decision memos in the team's knowledge base.",
    parents=[t3],
)["task_id"]
```

`parents=[...]` gates promotion — children stay in `todo` until every parent reaches `done`, then auto-promote to `ready`. No manual coordination needed; the dispatcher and dependency engine handle it.

If the task graph has dependencies, create the parent cards first, capture their returned ids, and include those ids in the child card's `parents` list during the child `kanban_create` call. Avoid creating all cards in parallel and linking them afterward; that creates a window where the dispatcher can claim a child before its inputs exist.

### Step 4 — Complete your own task

If you were spawned as a task yourself (e.g. a planner profile was assigned `T0: "investigate Postgres migration"`), mark it done with a summary of what you created:

```python
kanban_complete(
    summary="decomposed into T1-T4: 2 research lanes in parallel, 1 synthesis on their outputs, 1 prose draft on the recommendation",
    metadata={
        "task_graph": {
            "T1": {"assignee": "<profile-A>", "parents": []},
            "T2": {"assignee": "<profile-A>", "parents": []},
            "T3": {"assignee": "<profile-B>", "parents": ["T1", "T2"]},
            "T4": {"assignee": "<profile-C>", "parents": ["T3"]},
        },
    },
)
```

### Step 5 — Report back to the user

Tell them what you created in plain prose, naming the actual profiles you used:

> I've queued 4 tasks:
> - **T1** (`<profile-A>`): cost comparison
> - **T2** (`<profile-A>`): performance comparison, in parallel with T1
> - **T3** (`<profile-B>`): synthesizes T1 + T2 into a recommendation
> - **T4** (`<profile-C>`): turns T3 into a CTO memo
>
> The dispatcher will pick up T1 and T2 now. T3 starts when both finish. You'll get a gateway ping when T4 completes. Use the dashboard or `hermes kanban tail <id>` to follow along.

## Common patterns

**Fan-out + fan-in (research → synthesize):** N research-style cards with no parents, one synthesis card with all of them as parents.

**Parallel implementation + validation:** one implementer card makes the change while one explorer/researcher card verifies config, docs, or source mapping. A reviewer card can depend on both. Do not make the implementer own unrelated verification just because the user mentioned both in one sentence.

**Pipeline with gates:** `planner → implementer → reviewer`. Each stage's `parents=[previous_task]`. Reviewer blocks or completes; if reviewer blocks, the operator unblocks with feedback and respawns.

**Same-profile queue:** N tasks, all assigned to the same profile, no dependencies between them. Dispatcher serializes — that profile processes them in priority order, accumulating experience in its own memory.

**Human-in-the-loop:** Any task can `kanban_block()` to wait for input. Dispatcher respawns after `/unblock`. The comment thread carries the full context.

**Telegram proposal and blocker cards must be actionable:** ready-zero / proposal cards sent to Greg over Telegram should include inline buttons, not just plain text commands. Include `✅ Promote`, `⏭ Skip`, and `Open board`. Use callback data shaped like `kbp:p:<board>:<task_id>` for promote and `kbp:s:<board>:<task_id>` for skip; the Telegram gateway handler should authorize the clicker, run `hermes kanban --board <board> promote <task_id>` for promote, and strip the keyboard after a one-shot decision. Keep the plain-text fallback lines (`promote <id>`, `skip <id>`) for clients that don't render buttons.

For blocked / review-required cards, use the same mobile-action pattern instead of dumping long worker handoffs. Keep the Telegram message under ~150 words with labels (`Blocker`, `Task`, `Owner`, `Issue`, `Next`) and buttons: `✅ Promote`, `⏭ Keep blocked`, `Open board`. Implementation notes and test checklist live in `references/actionable-telegram-blocker-notifications.md`.

**Live-run smoke test:** when the user wants to "see it work" or you suspect the dispatcher is wedged, run the 3-card researcher → coder → reviewer probe in `references/live-run-smoke-test-pattern.md`. Verifies daemon dispatch, dependency promotion, cross-profile `dir:` workspace handoff, and the audit-against-canonical pattern in ~4 minutes.

**Kanban board DB recovery + auth preflight:** if `hermes kanban` commands fail with `sqlite3.DatabaseError: database disk image is malformed`, repair the board DB with `.recover`, swap in a clean recovered DB only after `PRAGMA quick_check` returns `ok`, then verify `stats` and `ls` before dispatch. Before large dispatches, smoke-test Codex auth for likely worker profiles; if spawned workers crash with `HTTP 401 token_expired`, sync the known-good default OpenAI Codex credential into trusted worker profiles and re-smoke before dispatching again. See `references/kanban-db-recovery-and-auth-preflight.md`.

**Deterministic crash loop signatures:** named failure patterns (Codex auth, Bedrock IAM, OpenRouter 400, stale-heartbeat false alarm) with exact log strings and fix commands — see `references/deterministic-crash-loop-signatures.md`.

**Crash-loop badge already healed:** real confirmed evidence that a high crash count in the diagnostics panel does not mean the loop is still active — the last `runs` entry is the ground truth. Collected examples across multiple steward passes in `references/crash-loop-already-healed-evidence.md`. Also documents the **mass healed queue** pattern: when the briefing shows the *entire* blocked sample with uniform `failures=1, blocker: pid <N> not alive`, all those tasks likely self-blocked weeks ago and are awaiting human review — verify only the active crash-loop card with `runs`, not every sample card.

**Worker comment vs. steward escalation gap:** A stuck_in_blocked task can have a detailed worker `review-required` comment while having zero steward escalation (no `hermes kanban unblock` command surfaced for Greg). See `references/worker-comment-vs-steward-escalation.md` — includes detection pattern, comment template, CLI quoting pitfall (use single quotes), and confirmed 2026-06-09 examples.

**Forced-skill reassign crash pattern:** fast crash loop when a card's forced skills are missing in the target profile — see `references/forced-skill-reassign-crash-pattern.md` for the exact detection signals, five confirmed examples (t_df407158, t_1909be87, t_3d884b29 ×3), and safe recovery sequence. NEW: card may arrive already in `running` state (dispatched by a prior cron tick before the steward pass) — still apply the same path: `log` → crash signature → `ps -p <pid> -o stat` → zombie → `reclaim` → `block`. Recurring gap: `tac-director` + `check-langfuse-logs` has appeared in **5+ separate passes** — if seen again and ≥2 prior comments exist, add an explicit escalation comment naming the recurrence count, not just the fix. Steward `block` does NOT consume remaining retry budget — if `max-retries=2` and `consecutive_failures` resets after the block, the dispatcher may re-run the card 1–2 more times before it permanently re-blocks. When `runs` shows `blocked` at run N then `crashed` at N+1, N+2, this is the retry budget draining — not a new loop. If `gave_up` fires and `status: blocked`, no action needed. See Case 15 in `references/crash-loop-root-causes.md`.

**Objective-complete steward intervention:** when a steward cron sees a live worker that has satisfied the card but drifted into optional side effects (especially external comments or credential discovery), use `references/steward-objective-complete-intervention.md`: comment with objective evidence, complete the card, terminate any still-live old worker PID, then verify final running/stats state.

**Cross-user PRD package dispatch:** when Greg asks whether a tenant agent (e.g. Carlos under `jason-agent`) has access to PRDs that were emailed/generated by the default profile, do not rely on Gmail access alone. Stage the PRD markdown files under the tenant's Hermes home, create idempotent tenant-board Kanban cards pointing at those local staged files, dispatch with the tenant Hermes binary from a scrubbed env, and nudge the tenant chat with plain-text task IDs + guardrails. See `references/cross-user-prd-package-kanban-dispatch.md` for the full pattern and Carlos command shapes.

**Cross-user PRD package dispatch:** when PRDs are emailed/rendered under Greg but need to be worked by a tenant agent like Carlos, do not rely on Gmail as the worker source of truth. Stage the markdown into a tenant-owned path such as `/Users/jason-agent/.hermes/inbox/prds/<package>/`, create tenant Kanban cards from the tenant user's Hermes environment, and nudge via a no-agent script until all tracked tasks are done. If Greg asks for separate boards per PRD while active work already exists on the default board, create tracking-only boards/cards pointing back to the default-board task rather than duplicating execution. See `references/cross-user-prd-package-dispatch.md` for the full recipe and the `every 30m` cron schedule pitfall.

**Cross-agent Kanban environment audit response:** when a tenant agent sends Gelby/Greg an audit memo asking for Kanban environment decisions, treat it as both inbox routing and board control-plane work. Verify the memo landed; if pickup advanced its marker after a failed write, manually route and patch the pickup script so failed writes do not advance markers. Put decisions back onto the blocked cards as comments, unblock only the safe partial lane, run bounded dispatch, and verify child cards/parents. Do not install `hermes kanban daemon` if the current CLI says dispatch runs in the gateway; remove stale launchd services for intentionally deleted profiles instead. See `references/cross-agent-kanban-audit-response.md`.

**Prompt-library composition for orchestrator profiles:** when Kanban/TAC behavior is duplicated across `SOUL.md`, `SOUL_OVERRIDE.md`, `config.yaml.system_prompt_addendum`, and skills, treat it as a prompt composition problem. Keep this skill as the procedural source of truth, extract reusable prompt sections such as mobile style, TAC Director routing, scoped read-only behavior, and observability contracts, then render profile prompts from manifests. Prefer adapting Jaymin West's MIT-licensed Canopy model (`jayminwest/canopy`, CLI `cn`, package `@os-eco/canopy-cli`) over inventing a custom renderer: Canopy already has section composition, `extends`, `mixins`, versioned JSONL storage, schemas, and plain `.md` emits. The Hermes-specific work is the adapter layer: map Canopy emits into `SOUL.md` and `config.yaml:system_prompt_addendum`, take backups, write receipts, validate profile existence, and warn/restart for fossilized gateway sessions. See `references/prompt-library-composition.md` for the target directory shape, manifest schema, migration order, and gateway/session restart caveat.

**Repo-boundary dispatch for integration rewrites:** when Greg asks why a patch landed in a repo, asks how Hermes/GBAutomation/portal/templates/tenants fit together, or wants to rewrite the application around integration points, create a first-class repo-boundary expert lane before implementation. Framework behavior (Telegram adapter, gateway callbacks, Kanban watcher behavior, core Hermes config) belongs in the GB-owned Hermes fork, not the active runtime clone, a personal fork, or the GBAutomation product repo. Broad rewrites should fan out at least six experts (repo-boundary, Hermes fork, portal rewrite, template/TAC canon, prompt composition/Canopy, tenant integration, observability/control-plane), then gate synthesis on their reports. The synthesis deliverables must include an ASCII architecture diagram, repo ownership matrix, wrong-repo patch migration plan, and follow-up implementation cards. See `references/repo-boundary-dispatch-for-integration-rewrites.md`.

**Kanban board SQLite corruption:** if board commands fail with `database disk image is malformed`, stop before dispatching. Recover the board DB with SQLite `.recover`, swap only after `PRAGMA quick_check` returns `ok`, then verify with `stats` and `ls --status ready` before running bounded dispatch. See `references/kanban-board-sqlite-recovery.md`.

**Cron failure triage (sysadmin-watchdog auto-filed cards):** when a cron auto-files a Kanban card for 2 consecutive failures, follow `references/cron-failure-triage.md` before dispatching. Key steps: check `jobs.json` `last_status`/`last_error` first — if both are clean, the cron self-recovered and the card can be completed directly. Also documents `hermes cron` CLI gotchas (no `status <id>`, no `log` subcommand; sessions at `~/.hermes/sessions/session_cron_<id>_*.json`; `jobs.json` schema is `{"jobs": [...]}` not a flat list).

## Pitfalls

**Using shared `dir:` scratch workspaces for TAC-style or reviewable work.** For live TAC/Kanban runs, especially anything with implementation, review, or artifacts the user may want to inspect later, create cards with git worktree-backed workspaces rather than a shared temp dir. Use `--workspace worktree:/absolute/path/to/repo` plus a unique branch such as `--branch kanban/<board>/<task-slug>` (or the equivalent API fields) so each worker has durable git state, diffable output, and a clean audit trail. `dir:/tmp/...` is acceptable only for throwaway smoke demos where persistence and review do not matter; if the user is validating the pipeline itself, worktrees are part of the test.

**Worktree access blocker: `.git/config` or `.git/worktrees` not writable by the worker OS user.** Symptom: orchestrator falls back to scratch-copy workers or logs `unable to access '.git/config': Permission denied` when an ecom/TAC worker tries `git worktree add`. Fix the repo/worktree roots, not the task graph: keep ownership intact, but grant `staff` read/write/execute plus inherited ACL on the repo root, `.git`, and worktree root; add `safe.directory` for cross-owner users; then smoke-test `git worktree add/remove` as each OS user that runs workers. Confirmed 2026-06-11 for `/Users/ecom/repos/ecom`: `.git/config` was `greg:staff 0600`, blocking `ecom`; patch changed it to group-writable and verified `git worktree add/remove` as both `greg` and `ecom`. Log pattern: `/Users/greg/.hermes/logs/worktree-access-patch-<ts>.log`.

**Inventing profile names that don't exist.** The dispatcher silently fails to spawn unknown assignees — the card just sits in `ready` forever. Always assign to a profile from your Step 0 discovery; ask the user if you're unsure.

**Forcing `--skill` on cards can fail in target-profile context.** `hermes kanban create --skill <name>` is resolved by the spawned worker profile, not necessarily by the orchestrator profile that created the card. A skill that exists for the creator can still produce `Error: Unknown skill(s): ...` when the worker starts, causing a fast crash. For cross-profile TAC/Kanban graphs, prefer embedding "load relevant skills manually if available" in the card body, or first verify the target profile can see the skill. If you already created a graph with bad forced skills, reclaim/archive the failed graph and recreate clean cards without forced `--skill` flags rather than letting the dispatcher retry a deterministic bootstrap failure.

**Steward triage: forced-skill crash on reassigned card.** When a card was created with forced skills (visible in `hermes kanban show <id>` as `skills: skill-a, skill-b`) and was later reassigned to a different profile, the new profile may crash in <5s with `Error: Unknown skill(s): skill-a, skill-b`. Diagnosis: log tail shows the skill error; run history shows 3+ crashes in rapid succession all with `pid not alive`. This is **always deterministic** — do not retry with the same profile. Safe sequence:
1. `hermes kanban reclaim <id>` — resets claim (required if board still thinks it's running)
2. `hermes kanban block <id> "Deterministic skill bootstrap failure: <profile> missing forced skills <list>. Next owner: @ops — create missing profile or reassign to a profile where these skills are installed."`
3. Add comment naming the original assignee profile (which was likely not on-disk) and the concrete fix.
Do NOT reassign blindly to the first available on-disk profile without verifying that profile has the required skills. Check `hermes kanban show <id>` for the `skills:` line before reassigning.

**Bundling independent lanes into one card.** If the user asks for two independent outcomes, create two cards. Example: "fix blockers and check model variants" is not one fixer task; create a fixer/engineer card for the fixes and an explorer/researcher card for the variant check, then optionally gate review on both.

**Over-linking because of wording.** "Finally check X" may still be parallel with implementation if X is static config, docs, or source discovery. Link it after implementation only when the check depends on the implementation result.

**Forgetting dependency links.** If the task graph says `research -> implement -> review`, do not create all tasks as independent ready cards. Use parent links so implement/review cannot run before their inputs exist.

**Reassignment vs. new task.** If a reviewer blocks with "needs changes," create a NEW task linked from the reviewer's task — don't re-run the same task with a stern look. The new task is assigned to the original implementer profile.

**Argument order for links.** `kanban_link(parent_id=..., child_id=...)` — parent first. Mixing them up demotes the wrong task to `todo`.

**CLI: verify `--parent` actually attached before relying on it.** When creating cards via the `hermes kanban create` CLI (rather than the in-agent `kanban_create` tool), the `--parent <id>` flag is silently dropped if `<id>` expands to an empty string. Classic failure mode: piping `--json` output through a `python3 -c` extractor that uses the wrong key (the `--json` schema is not stable across versions and may not include `task_id` at the top level). The extractor KeyErrors, the shell var stays empty, `--parent ""` becomes a no-op, and all three cards land `ready` simultaneously — the dispatcher then races them and the dependency graph you thought you built doesn't exist. Two defenses: (a) capture task IDs by parsing `hermes kanban ls` output or reading the DB, not by trusting `--json` shape; (b) immediately after creation run `hermes kanban show <child_id>` and confirm `parents: [...]` is non-empty before walking away. If you find children `ready` when they should be `todo`, recover with `hermes kanban link <parent_id> <child_id>` — link auto-demotes the child to `todo` when its new parent isn't `done` yet.

**CLI: bulk `block --ids` can swallow the reason if ordered wrong.** `hermes kanban block` parses as `block task_id [reason ...] [--ids IDS ...]`, and `--ids` uses a greedy multi-value argument. If you put `--ids` before the reason, argparse treats the reason words as additional ids, prints `cannot block <word...>`, and blocks the intended ids with `reason: None` and no blocker comment. Safer patterns: (a) put the reason immediately after the primary task id, then `--ids <extra ids...>`; (b) block individually when comments matter; or (c) immediately verify with `hermes kanban show <task_id>` and add explicit `hermes kanban comment --author kanban-steward ...` comments for every blocked card. Steward cron runs must verify comments because the board policy requires a blocker and next owner on anything blocked.

**Don't pre-create the whole graph if the shape depends on intermediate findings.** If T3's structure depends on what T1 and T2 find, let T3 exist as a "synthesize findings" task whose own first step is to read parent handoffs and plan the rest. Orchestrators can spawn orchestrators.

**Tenant inheritance.** If `HERMES_TENANT` is set in your env, pass `tenant=os.environ.get("HERMES_TENANT")` on every `kanban_create` call so child tasks stay in the same namespace.

**Scratch/dir workspaces are not a valid TAC pipeline proof.** For tiny throwaway demos, `--workspace dir:/tmp/...` can show dependency promotion, but it does not prove the real TAC/Kanban build path because artifacts are not committed, branches are not isolated, and parallel workers can collide. When the user is reviewing the TAC pipeline, live build orchestration, or anything intended to be durable/auditable, create cards with a git worktree workspace: `--workspace worktree:/absolute/path/to/repo` plus a unique `--branch kanban/<board>/<task-slug>` per card/worker. Use a shared `dir:` only when the user explicitly asks for scratch-only output.

## Recovering stuck workers

### Cron steward crash-loop triage

When running as a scheduled Kanban steward from a pre-run briefing, act only on the listed board facts and keep the control-plane actions bounded:

1. **Inspect before acting.** For any crash loop or stale running task in the briefing, run diagnostics and inspect the specific task (`show`, `runs`, and `log`) before reclaiming or blocking. Do not just dispatch over the top of a stale claim.
2. **Stop infinite respawn loops.** If logs show a deterministic worker bootstrap/provider/config failure, block the task rather than reclaiming it back to `ready`. Include a Kanban comment with the exact blocker and next owner. Good blocker wording names the failing profile and the concrete error class (for example: Bedrock `AccessDeniedException` for `InvokeModelWithResponseStream`, OpenRouter HTTP 400 `No models provided`, missing profile model, broken provider IAM/config, or Codex `access_token` missing — `"Codex auth is missing access_token. Run hermes auth to re-authenticate."`). Treat alternating provider failures across successive respawns (for example empty OpenRouter model on some attempts and Bedrock IAM denial on others) as a single lane-level provider/profile configuration blocker, not as evidence that another retry may work. **Codex auth failures are always deterministic** — the worker exits in <5s on every run; check `hermes kanban log <id>` tail for this exact string before blocking. Next owner for Codex auth: `@ops`, fix with `hermes -p <profile> auth`.
3. **Dispatch after stabilizing.** Once zombies/crash loops are blocked or reclaimed, run a bounded `hermes kanban --board <board> dispatch --max N` pass. Do not leave ready work idle just because one worker lane was broken.
4. **Verify spawned tasks immediately.** After a dispatch pass, inspect `runs`, `log`, and/or `show` for any newly spawned task that appears in the dispatch result or rapidly returns to `ready/running` with fresh crash diagnostics. If logs show a deterministic bootstrap/provider/auth/workspace failure before work begins (for example missing Codex `access_token`, Anthropic/OpenRouter/Bedrock provider config failures, repeated 429 quota waits, or scratch workspace materialization/cwd failures that make worker tools fail before useful work starts), block the newly dispatched card with a concise root-blocker comment instead of allowing the next steward/dispatcher pass to spin it again. For workspace materialization failures, name the next owner as the Kanban dispatcher/workspace owner and specify the expected fix: mkdir the resolved scratch workspace before spawn or provide a safe cwd fallback, then unblock/retry.
5. **Handle dispatch no-ops explicitly.** If dispatch spawns nothing, check `hermes kanban --board <board> assignees` and the ready queue. Common causes are unassigned ready cards and non-spawnable terminal lanes (`ON DISK = no`). Route a small, concrete batch of high-priority ready cards to existing on-disk profiles, add a brief steward comment explaining the assignment, then run another bounded dispatch pass. If the routed profiles immediately expose the same deterministic auth/provider failure, block those cards and report the lane-level blocker rather than repeatedly rerouting.

   **Before reassigning a non-spawnable card:** always run `hermes kanban show <id>` and check the `skills:` line. If the card lists forced skills, only reassign to a profile that has those skills registered. Reassigning to any available on-disk profile without this check causes an immediate deterministic crash loop (fast failure, <5s per run). When no suitable on-disk profile exists, block with a comment naming the skill gap and next owner (@ops) rather than reassigning blind.
   - **Beware `--max` scan limits.** A low `dispatch --max N` can appear to no-op when higher-priority unassigned ready cards consume the scan before newly-assigned lower-priority cards are reached. After routing ready work, run `dispatch --dry-run --max 10` (or larger than the visible ready backlog) to see `skipped_unassigned`/`skipped_nonspawnable`, then run the real dispatch with a large enough `--max` to scan past the skipped cards while still bounding actual spawns.
   - **Assigned-but-not-spawned is not automatically failure.** Check `stats`, `assignees`, and `runs <task_id>` after dispatch. The task may still be `ready` because its assignee already has a running worker, because the dispatcher scan stopped early, or because the spawned run completed quickly and moved a different count.
6. **Get the full running picture first — not just the briefing's stale list.** The briefing only lists tasks flagged as *stale* (heartbeat older than threshold); it does not list all running tasks. Run `hermes kanban --board <board> ls --status running` early in the steward pass to see every running task. A task started minutes ago may be progressing fine but won't appear in the briefing's stale list at all — and a task the briefing calls stale may be fully alive when you check with `ps`. Always get the complete set of running tasks before deciding whether to reclaim anything.

7. **Differentiate stale heartbeat from dead process.** A briefing can label a running task stale because the heartbeat event is old even while the worker process is alive and logs show progress. Before reclaiming, inspect `show`, `runs`, `log`, and verify the current pid with `ps -p <pid> -o pid,etime,stat,command` when the briefing includes a pid. Reclaim/block only if the process is gone, the run is truly stuck, or logs show deterministic bootstrap/provider failure; leave live progressing workers alone and report that they were verified active.

    **Heartbeat age thresholds in practice:** a briefing flag of `heartbeat_age=1m` on a task that was only started ~1-2 minutes ago is almost always a false alarm — the worker simply hasn't emitted a heartbeat yet. The log trail (tool calls, file writes, exec output) is more reliable than the heartbeat event timestamp for assessing whether a young worker is progressing. Only escalate to reclaim when `heartbeat_age` substantially exceeds the task's typical iteration cycle AND `ps -p <pid>` shows the process is gone or zombie.

    **Key heuristic — heartbeat_age ≈ task elapsed time → false alarm:** If the briefing's `heartbeat_age` is approximately equal to the time since the task started (e.g. task started 3m ago, heartbeat_age=3m), this is the clearest false-alarm signal possible — the worker has never emitted a heartbeat, not that it stopped emitting one. Always verify with `ps -p <pid> -o pid,etime,stat,command`: if the process is alive and stat shows `S` or `R`, leave it alone. A first-heartbeat false alarm requires zero steward action.

    **Context-compression heartbeat lag:** A running worker that has compacted its context 2–3+ times will emit no heartbeat during the compaction LLM call (which can take 10–30s). The log tail signature is `⟳ compacting context…` / `🗜️ Compacting context — summarizing earlier conversation so I can continue...` / `⚠️ Session compressed 3 times`. This is a benign cause of `heartbeat_age` inflation. If `ps -p <pid>` shows the process alive and the log ends with a context-compression line, no action is needed. Confirmed: `t_8f01b6af` 2026-06-09 — PID alive at 6:30 elapsed, `heartbeat_age=6m`, log tail showed 3x context compression.

    **Session file modification time — critical for post-objective hang detection:** When `ps` shows alive and `lsof` shows ESTABLISHED TCP but heartbeat_age is hours, check the profile session file recency:
    ```bash
    ls -lt ~/.hermes/profiles/<profile>/sessions/ | head -3
    ```
    If the most recent session was written only minutes after the run started (not recently), the agent loop is frozen. Follow up with `hermes kanban log <id> | tail -10` — if it ends in a Python exception + `SystemExit`, check artifacts on disk. If all required outputs exist, use objective-complete intervention (complete card + SIGKILL). This is distinct from the prior-blocked epoch false alarm (below). See `references/deterministic-crash-loop-signatures.md` for the full disambiguation sub-case.

    **PID alive but task already done (completion race):** A running task can complete *between* when the briefing script runs and when the steward checks it. If `ps -p <pid>` shows the process alive but `hermes kanban show <id>` returns `status: done`, the task finished during the steward pass. No action needed — report it as "completed while checked." The PID may still show briefly alive as the process exits. Always call `show` to confirm status, not just `ps`. Confirmed: `t_c872e84f` 2026-06-09 — PID 87514 alive at ps-check, task was `done` by the time `show` ran (completed during steward pass after ~7 minutes elapsed).

    **Prior-blocked-state epoch false alarm (heartbeat_age >> elapsed):** When a task was previously blocked for hours before being re-dispatched, the briefing script may compute `heartbeat_age` from the last heartbeat event before the block — yielding values like `8h` even though the current run is only minutes old. Cross-reference: `hermes kanban show <id>` → find the most recent `[run NNNN] spawned` timestamp. If `now - spawned` is small but `heartbeat_age` is large, the briefing is reporting the prior block epoch. Confirm with `ps -p <pid> -o etime` (process elapsed time should match `now - spawned`, not `heartbeat_age`). Additional signal: `lsof -p <pid> | grep ESTABLISHED` — an active ESTABLISHED TCP connection to a relevant endpoint (AWS, website being processed) proves the worker is doing live network work. Leave it alone and report "run #N verified active, prior-block epoch heartbeat artifact." Confirmed example: `t_ae2083a5` 2026-06-09 — briefing `heartbeat_age=8h`, actual run age ~1.5h, process alive with active TCP to AWS. See `references/deterministic-crash-loop-signatures.md` for the full sub-case documentation.
8. **Re-check immediately before and after blocking/reclaiming.** Stale runs can crash or complete while the steward pass is inspecting them. If `reclaim` says `not running or unknown id`, immediately `show`/`runs` the card before deciding whether to block; it may already be `done`, `blocked`, or freshly re-claimed. After any `block` or `reclaim`, verify `show`, `runs`, and `ps -p <old_pid>[,<new_pid>]`: some CLI recovery paths can record a blocked run while an already-spawned process is still alive briefly. If the card is now blocked/done but a known stale worker process is still running for that exact task command, terminate that orphan process and report the cleanup separately from the Kanban state change.
9. **Dispatch only after the running queue is stable.** A task can complete during the steward pass; include that as a verified outcome rather than intervening. Once stale/zombie tasks are blocked/reclaimed and any orphan worker PIDs are gone, run a bounded dispatch pass and then verify final `stats` so `running`, `ready`, `blocked`, and `done` counts match the report. If `ps` still shows the just-completed worker as `Z` / `<defunct>`, treat it as a reaping artifact rather than an active orphan to kill; verify the card is `done` and report the count change instead of reclaiming or blocking.
10. **Close objective-complete workers that drift into optional side quests.** Sometimes a live worker has already satisfied the task objective (for example, verified a cron run succeeded and the historical alert is no longer firing) but then continues into optional external side effects such as Linear comments, credential searches, credential/API-key discovery, or unrelated follow-up cleanup. If the log contains enough evidence to satisfy the card, use `hermes kanban --board <board> complete <task_id> --summary ... --metadata ...` with a steward summary explaining the intervention, then terminate the old worker PID if it remains alive. This is not a reclaim: the card should move to `done`, and any dependent/root task may promote afterward. Run a bounded dispatch pass and verify the promoted task instead of letting the original worker spin forever. If a worker starts looking up secrets or embedding literal credentials in terminal commands after the board objective is already met, treat that as an immediate objective-complete intervention: complete the card from the verified evidence, stop the worker, and report that the intervention prevented unnecessary credential-handling side effects rather than continuing to watch the worker.
11. **Audit blocked crash-loop samples before redispatching.** A blocked card can carry diagnostics like `pid not alive` while the latest logs/comments show a deliberate `review-required`, external approval, architecture decision, or prerequisite blocker. For steward runs, do not blindly unblock/reclaim these back into a crash loop. Inspect `show` + log tail, then add or refresh a concise `kanban-steward` comment naming the real current blocker and next owner (reviewer, ops, DB admin, process-management owner) when the existing blocker is ambiguous.

    **Multi-pass race:** The steward cron runs on a short interval. If a crash loop is listed in the briefing, a prior steward pass that fired a few minutes earlier may have already handled it. Before acting, check the `runs` last entry AND the `show` event log for `commented` and `blocked` events from the same day. If they exist and the last run is `blocked`, the loop is handled — do not add a duplicate comment or re-block. A pass that finds everything already stable is a **correct outcome**, not a failure — report it explicitly (see `references/crash-loop-already-healed-evidence.md` Pass G for a confirmed example).

    **Duplicate-comment guard (critical):** When a task is already blocked and `show` reveals a `commented` event from the current day naming the same root cause, **skip the comment entirely** — do NOT add another comment saying the same thing. Multiple steward passes firing in quick succession can accumulate 3+ identical comments on a single task (confirmed: t_ae2083a5 got comments at 21:38, 21:45, and 21:52 all repeating "Codex auth missing access_token"). This is noise, not signal. The correct action when a prior steward already documented everything: report "already handled" in the steward summary output and take no board action at all. See `references/crash-loop-already-healed-evidence.md` Pass H for the confirmed example.

    **Fast-path duplicate guard (efficiency shortcut):** When `show` output for a blocked task already lists **2 or more comments with today's date**, skip the task entirely without reading comment content — you already have your answer. A task with ≥2 same-day steward comments is maximally documented; any further comment is noise. Only read the comment content when there is exactly 1 today-dated comment (to assess whether it covers the same root cause) or 0 today-dated comments (to assess whether any prior-day comment is sufficient). Confirmed 2026-06-09: t_9fe27ad8 had 8 same-day comments; t_c32fcc0b had 6 — both were correctly skipped without content inspection.

   **CRITICAL: Check today-comment count BEFORE calling `hermes kanban comment`** — do NOT batch-collect today counts for multiple tasks then loop and comment all of them. The count check must gate each individual comment call. Confirmed failure mode 2026-06-10: t_61a7fec7 already had 2 today-dated comments; the count check ran in a batch pass and showed `2` but the loop called `comment` anyway before verifying. Correct pattern: `count=$(hermes kanban show $id | grep -c "$(date +%Y-%m-%d)"); [ "$count" -lt 2 ] && hermes kanban comment $id '...'` — one conditional per task, inline.

**`hermes kanban create` TypeError: `create_task() got unexpected keyword argument 'goal_mode'`**

Version-drift bug: `kanban.py` CLI caller was updated to pass `goal_mode` and `goal_max_turns` kwargs but `kanban_db.py:create_task()` signature wasn't updated to match. Every `hermes kanban create` call fails with a traceback. Fix: add `goal_mode: bool = False` and `goal_max_turns: Optional[int] = None` to `create_task()` in `hermes_cli/kanban_db.py` around the `initial_status` parameter, then reinstall / restart. Confirmed fix path 2026-06-10. Signal: all `hermes kanban create` calls fail with the same `TypeError` regardless of arguments supplied.

**Comment-only steward pass does NOT stop a crash loop — you must also BLOCK.**

When a forced-skill crash loop (or any deterministic bootstrap failure) is identified, the correct
sequence is **reclaim → block → comment**, in that order. A comment without a subsequent block
leaves the task in `running` state (or lets it get reclaimed back to `ready`), allowing the
next dispatch tick to spawn it again.

**Confirmed failure mode (2026-06-10, t_1909be87):** A prior steward pass at 23:00 UTC
correctly identified the root cause (`check-langfuse-logs` skill missing from `tac-director`)
and added a detailed comment. But the task was not blocked — it was left in `running` state
with a dead PID. The dispatcher reclaimed and re-spawned it 3 more times (runs 2, 3, 4)
before the next steward pass actually reclaimed + blocked it.

**Correct sequence for forced-skill / deterministic bootstrap failures:**
```bash
# 1. Verify PID is dead
kill -0 <pid> 2>&1 && echo "PID_ALIVE" || echo "PID_DEAD"

# 2. Reclaim (resets running → ready; required if board thinks it's running)
hermes kanban --board <board> reclaim <id>

# 3. Block immediately (prevents re-dispatch before next cron tick)
hermes kanban --board <board> block <id> 'Deterministic failure: <reason>. Fix: <command>. Next owner: Greg.'

# 4. Add detailed comment (durable context)
hermes kanban --board <board> comment <id> 'Steward triage ...'
```

**Signal that a prior pass failed to block:** `hermes kanban runs <id>` shows run N as
`reclaimed` (not `blocked`) followed by run N+1 as `crashed` or `(running)`. The prior
pass reclaimed but never blocked — the dispatcher immediately re-queued it.

**Reclaim-before-block when last run is still `(running)` despite zombie PID.** When `hermes kanban runs <id>` shows the last entry as `(running)` but `ps -p <pid>` confirms `Z` (zombie/defunct) or the PID is not alive, the board still considers the task claimed. Calling `hermes kanban block <id>` on a task the board thinks is "running" may silently no-op or fail. Safe sequence: `hermes kanban reclaim <id>` first (resets the claim to `ready`), then `hermes kanban block <id> "reason"`. Confirmed pattern: crash-loop tasks where the dispatcher re-spawned a run that died before the steward arrived — the run appears `(running)` in `runs` output but the PID is already gone.

**`hermes kanban block` silent no-op on already-blocked tasks.** When a task is already blocked (including auto-blocked by a prior crash run), `hermes kanban block <id> "reason"` silently outputs `cannot block <task_id>` and exits 0 — no error, no exception. The steward must:
1. Always run `hermes kanban runs <id>` BEFORE attempting a block to check if the last run is already `blocked`.
2. If the task is already blocked but needs a diagnostic comment, use `hermes kanban comment <id> "..."` directly (comment always works regardless of task state).
3. Do not interpret `cannot block <task_id>` as a CLI error — it is a normal state signal. Record it as "already blocked, comment added" in the report.

    **Mass healed queue signal:** When the briefing shows the *entire* blocked sample with `failures=1, blocker: pid <N> not alive` (uniform low failure count, not escalating), this is a strong signal that all those tasks stopped crashing weeks ago and are simply awaiting human review. The `pid not alive` in the blocker field is what the *last* crashed run recorded before the worker self-blocked — it does not mean the task is actively crashing now. Still verify the flagged crash-loop card with `runs`, but if its last run is `blocked`, treat the whole queue as stable without individually checking every blocked sample. This pattern occurs regularly when the Kanban board has a large backlog of `review-required` cards accumulated from a prior sprint.

    **When the briefing blocked sample contains NO crash-loop card (only uniform failures=1 entries):** this is the strongest possible mass-healed signal. Do NOT pull `runs` on individual sample cards to verify — you already have the answer. Skip directly to confirming both running tasks are alive (with `ps -p <pid>`) and then report. Spot-checking review-required cards that the briefing itself doesn't flag as crash-looping wastes tool calls and adds latency to every steward pass.

    **Briefing `ready=N` can lag actual dispatch (race condition):** The briefing script captures board state at cron-trigger time, but by the time the steward pass runs its first tool call, the dispatcher may have already claimed and spawned those ready tasks. Always verify with `hermes kanban --board <board> ls --status ready` before dispatching — if the count is 0, the task is already running and no dispatch action is needed. Confirmed 2026-06-10: briefing showed `ready=1` (t_f140ec09 and t_549f427b in separate passes), but by steward-run time both were already `running` with live PIDs. Attempting dispatch on an empty ready queue is a no-op, but checking first avoids a spurious dispatch call.

    **Efficient batch PID check for all running tasks:** Instead of checking each running task's PID individually, fetch all running tasks at once and batch-verify:
    ```bash
    hermes kanban --board gbautomation ls --status running 2>&1
    # Capture PIDs from show/runs output, then:
    ps -p <pid1>,<pid2>,<pid3> -o pid,etime,stat,command 2>&1
    ```
    One `ps` call for all PIDs is faster than N separate `kill -0` checks and returns elapsed time + stat for each. If a PID is missing from the `ps` output entirely, it's dead. Confirmed 2026-06-10: 3 running tasks (PIDs 60087, 61312, 62528) all verified alive in a single `ps -p` call.

    **Crash-loop badge ≠ active crash loop — check `runs` last entry FIRST.** The diagnostics panel (and briefing script) count *historical* consecutive crashes; a card showing `crashed 8x` or `crashed 18x` may have already been self-healed by the worker's own `kanban_block(reason="review-required: ...")` call. Before any other action on a crash-badged blocked card, run `hermes kanban runs <id>` and look only at the *last* run's outcome:
    - Last outcome `blocked` → loop has already stopped; task is legitimately waiting for human review. Do not reclaim or re-block.
    - Last outcome `crashed` → loop is still active; inspect log for deterministic failure signature and block with a concrete reason.

    The pattern to confirm a stopped loop: `hermes kanban runs <id>` shows final entry as `blocked → review-required: ...`. A steward comment is only needed if the existing blocker text is ambiguous.

    Real-world examples of this pattern confirmed across multiple steward passes — see `references/crash-loop-already-healed-evidence.md`.
12. **Report changed state, not intentions.** Final steward reports should list blocked/reassigned/spawned task ids, root blockers recorded in comments, bounded dispatch results, orphan process cleanup if any, and verified current status counts. If nothing changed, say exactly why (for example: all ready cards are unassigned, assigned profiles are already busy, or assigned tasks are hidden behind higher-priority skipped cards in the dispatch scan).

When a worker profile keeps crashing, hallucinating, or getting blocked by its own mistakes (usually: wrong model, missing skill, broken credential), the kanban dashboard flags the task with a ⚠ badge and opens a **Recovery** section in the drawer. Three primary actions:

1. **Reclaim** (or `hermes kanban reclaim <task_id>`) — abort the running worker immediately and reset the task to `ready`. The existing claim TTL is ~15 min; this is the fast path out.
2. **Reassign** (or `hermes kanban reassign <task_id> <new-profile> --reclaim`) — switch the task to a different profile (one that exists on this setup) and let the dispatcher pick it up with a fresh worker.
3. **Change profile model** — the dashboard prints a copy-paste hint for `hermes -p <profile> model` since profile config lives on disk; edit it in a terminal, then Reclaim to retry with the new model.

Hallucination warnings appear on tasks where a worker's `kanban_complete(created_cards=[...])` claim included card ids that don't exist or weren't created by the worker's profile (the gate blocks the completion), or where the free-form summary references `t_<hex>` ids that don't resolve (advisory prose scan, non-blocking). Both produce audit events that persist even after recovery actions — the trail stays for debugging.
