---
type: runtime-reconciliation-report
task_id: t_b0fb3868
created: 2026-06-11
status: report-only
runtime_root: /Users/greg/.openclaw/workspace/hermes-agent
target_runtime_root: /Users/greg/repos/hermes-agent
---

# Hermes active runtime reconciliation

## Executive result

The active Hermes runtime is still `/Users/greg/.openclaw/workspace/hermes-agent`, not the target canonical fork checkout `/Users/greg/repos/hermes-agent`.

Recommendation: keep `/Users/greg/repos/hermes-agent` as the target runtime root, but do not flip launchd profiles or gateway workers yet. The runtime checkout contains dirty live patches and a committed prompt-library branch; the canonical checkout also has newer/different work. Treat the migration as file-by-file ports onto the canonical fork, not as a directory copy.

No launchd plist, runtime process, or runtime code was changed during this card. This report is the only file intentionally written.

## Evidence commands run

```text
which hermes
hermes --version
ps -axo pid,command | grep -E 'hermes.*gateway|kanban' | grep -v grep
git -C /Users/greg/repos/hermes-agent status --short --branch
git -C /Users/greg/repos/hermes-agent rev-parse HEAD
git -C /Users/greg/repos/hermes-agent remote -v  # redacted below
git -C /Users/greg/.openclaw/workspace/hermes-agent status --short --branch
git -C /Users/greg/.openclaw/workspace/hermes-agent rev-parse HEAD
git -C /Users/greg/.openclaw/workspace/hermes-agent remote -v  # redacted below
git -C /Users/greg/.openclaw/workspace/hermes-agent diff --stat
git -C /Users/greg/.openclaw/workspace/hermes-agent diff --cached --stat
git diff --no-index --stat /Users/greg/repos/hermes-agent/<path> /Users/greg/.openclaw/workspace/hermes-agent/<path>
```

## Runtime identity

### Active executable

```text
which hermes: /Users/greg/.openclaw/workspace/hermes-agent/venv/bin/hermes
hermes --version: Hermes Agent v0.16.0 (2026.6.5) · upstream 021ed691 · local 96d222e5 (+1 carried commit)
Project: /Users/greg/.openclaw/workspace/hermes-agent
Python: 3.11.15
OpenAI SDK: 2.24.0
Update available: 193 commits behind — run 'hermes update'
```

Observed live process sample confirms multiple gateways/workers are still executing from `.openclaw`, including this Kanban worker:

```text
/Users/greg/.openclaw/workspace/hermes-agent/venv/bin/python ... hermes -p tac-builder ... work kanban task t_b0fb3868
/Users/greg/.openclaw/workspace/hermes-agent/venv/bin/python -m hermes_cli.main --profile jason-va gateway run --replace
/Users/greg/.openclaw/workspace/hermes-agent/venv/bin/python ... --profile tac-lead gateway run --replace
/Users/greg/.openclaw/workspace/hermes-agent/venv/bin/python -m hermes_cli.main --profile dbforge gateway run --replace
```

There are also several Homebrew-Python `python -m hermes_cli.main gateway run --replace` processes, so rollout remains profile-by-profile.

## Repository SHAs and branches

### Target canonical checkout

Path: `/Users/greg/repos/hermes-agent`

```text
branch: feat/telegram-kanban-blocker-buttons
HEAD: 49ddf47e679cfebada6889162f2d38da54de9845
short: 49ddf47e6 feat(telegram): add kanban blocker action buttons
status: dirty only by untracked docs/plans files before this report
```

Redacted remotes:

```text
origin   https://[REDACTED]@github.com/gbauto/hermes-agent.git (fetch)
origin   https://[REDACTED]@github.com/gbauto/hermes-agent.git (push)
upstream https://[REDACTED]@github.com/NousResearch/hermes-agent.git (fetch)
upstream https://[REDACTED]@github.com/NousResearch/hermes-agent.git (push)
```

Pre-existing untracked files observed before this report:

```text
?? docs/plans/2026-06-11-prompt-library-adapter-validation-gates.md
?? docs/plans/2026-06-11-telegram-kanban-blocker-action-buttons.md
```

### Active runtime checkout

Path: `/Users/greg/.openclaw/workspace/hermes-agent`

```text
branch: feat/prompt-library-canopy-adapter
HEAD: 96d222e52f304f75b75089962ee2d61b10c29cb6
short: 96d222e52 feat(prompt-library): add Canopy-backed prompt profile adapter (step 1)
status: dirty, mixed staged and unstaged runtime changes
```

Redacted remotes:

```text
fork   https://[REDACTED]@github.com/gblack686/hermes-agent.git (fetch)
fork   https://[REDACTED]@github.com/gblack686/hermes-agent.git (push)
origin https://[REDACTED]@github.com/NousResearch/hermes-agent.git (fetch)
origin https://[REDACTED]@github.com/NousResearch/hermes-agent.git (push)
```

## Root comparison summary

The two roots do not share the inspected commit objects locally, so a normal `git diff <canonical>..<runtime>` was not available without fetching/importing refs. A filesystem comparison excluding heavy runtime/build directories (`.git`, `venv`, `.venv`, `node_modules`, caches, build/dist) found:

```text
canonical_files: 3612
runtime_files: 4958
same_files: 2007
different_common_files: 1429
only_canonical_files: 176
only_runtime_files: 1522
```

This is too much drift for a bulk copy or `rsync`-style promotion. The correct action is targeted migration by feature area.

## Active runtime dirty inventory

Runtime `git status --short --branch`:

```text
## feat/prompt-library-canopy-adapter
 M agent/agent_init.py
 M gateway/platforms/telegram.py
 M hermes_cli/config.py
MM hermes_cli/kanban_db.py
M  plugins/observability/langfuse/__init__.py
 M tests/hermes_cli/__init__.py
M  tests/hermes_cli/test_kanban_db.py
M  tests/plugins/test_langfuse_plugin.py
 M tests/tools/test_memory_tool.py
 M tests/tools/test_memory_tool_schema.py
 M tools/memory_tool.py
?? .hermes.md
?? hermes_cli/kanban_db.py.bak.codex-1781150991
```

Runtime unstaged diff stat:

```text
agent/agent_init.py                    |   6 +-
gateway/platforms/telegram.py          |  98 ++++++++++++++++++++
hermes_cli/config.py                   |   6 +-
hermes_cli/kanban_db.py                |  25 +++++
tests/hermes_cli/__init__.py           |   1 +
tests/tools/test_memory_tool.py        |  69 ++++++++++++--
tests/tools/test_memory_tool_schema.py |   2 +-
tools/memory_tool.py                   | 161 +++++++++++++++++++++++++++++----
8 files changed, 339 insertions(+), 29 deletions(-)
```

Runtime staged diff stat:

```text
hermes_cli/kanban_db.py                    | 1768 ++++------------------------
plugins/observability/langfuse/__init__.py |   90 +-
tests/hermes_cli/test_kanban_db.py         | 1448 +----------------------
tests/plugins/test_langfuse_plugin.py      |  130 +-
4 files changed, 387 insertions(+), 3049 deletions(-)
```

## Dirty file classification and migration targets

| Runtime path | Runtime-only finding | Classification | Follow-up migration target |
|---|---|---|---|
| `agent/agent_init.py` | Dirty live patch raises memory limits to 4400/2000 and wires `compact_target_ratio` / `auto_compact_threshold`. File also differs heavily from canonical because runtime lacks/has unrelated provider/runtime changes. | port, but only as part of memory compaction feature; do not copy whole file | `gbauto/hermes-agent` feature branch for memory compaction; target function `init_agent(... MemoryStore(...))`; tests with `tools/memory_tool.py` |
| `hermes_cli/config.py` | Dirty live patch changes default memory limits and adds compaction knobs. File also has broad runtime/canonical divergence. | port, targeted | `gbauto/hermes-agent` memory config migration; target `DEFAULT_CONFIG['memory']`; docs config update |
| `tools/memory_tool.py` | Adds memory compaction/archive API, larger defaults, drift protections, strict threat-pattern scanner use, and `compact` action. Canonical has different memory-tool baseline. | port after review | `gbauto/hermes-agent` memory compaction branch; target `tools/memory_tool.py`; tests `tests/tools/test_memory_tool.py`, `tests/tools/test_memory_tool_schema.py` |
| `tests/tools/test_memory_tool.py` | Adds compaction tests and many strict prompt-injection/threat-pattern tests. Canonical test file is much smaller/different. | port with memory feature, split if needed | Same memory compaction branch; keep threat-pattern tests if canonical has `tools/threat_patterns.py` equivalent |
| `tests/tools/test_memory_tool_schema.py` | Expects memory tool action enum to include `compact`. | port with memory feature | Same memory compaction branch |
| `gateway/platforms/telegram.py` | Dirty live patch adds ad-hoc `kbp:` Kanban proposal callback handler that shells out to `hermes kanban promote`, including a hardcoded `.openclaw` binary fallback. Canonical branch already has a safer action-button design note and newer Telegram code. | discard direct patch; redesign/port concept only | Use existing blocker/action-button contract plan in canonical docs. Target generic signed/opaque Kanban callback handling in `gateway/platforms/telegram.py` plus gateway/API surface. Do not port hardcoded binary fallback. |
| `hermes_cli/kanban_db.py` | Mixed staged/unstaged. Compared to canonical worktree, runtime has only 25 extra lines: unused `goal_mode`/`goal_max_turns` params on `create_task` and `reap_worker_zombies()`. The huge staged diff appears to make runtime HEAD converge with canonical for already-ported Kanban DB changes, but the working tree still carries extra lines. | split: keep/port zombie reaping if tests prove need; discard or complete goal-mode plumbing only with full schema/tool support | Target Kanban reliability branch for `reap_worker_zombies()` with tests. Target separate goal-mode card only if `Task`, schema, tools, CLI, dispatcher all support it. Do not leave unused params. |
| `tests/hermes_cli/__init__.py` | One comment line in an otherwise empty package marker. | discard | No migration target; do not port noise. |
| `plugins/observability/langfuse/__init__.py` | Staged runtime changes add Kanban join metadata/tags but also revert a canonical usage-dict regression fix. Current runtime file hash matches canonical worktree (`c7ffb1d41411`) after staged content, so it appears already present in target checkout. | keep in canonical if tests pass; no runtime-only port needed | Verify in observability card/smoke suite; target `plugins/observability/langfuse/__init__.py` and `tests/plugins/test_langfuse_plugin.py` only if later diffs reappear. |
| `tests/plugins/test_langfuse_plugin.py` | Runtime file hash matches canonical worktree (`c51eb0c8127a`) after staged content. | keep as already-port-equivalent | Same as Langfuse row. |
| `tests/hermes_cli/test_kanban_db.py` | Runtime file hash matches canonical worktree (`36bf2cc169bf`) after staged content. | keep as already-port-equivalent | No immediate migration target from this card. |
| `.hermes.md` | Untracked, 48,046 bytes / 1,429 lines. This session's project-context scanner blocked it for `html_comment_injection`, so it is not safe to promote blind. | discard/quarantine, do not port | If project context is still needed, create a new reviewed `AGENTS.md` or `.hermes.md` from scratch in canonical with injection-safe content. |
| `hermes_cli/kanban_db.py.bak.codex-1781150991` | Untracked backup file, 255,532 bytes / 6,373 lines. | discard | No migration target; leave out of canonical. |

## Committed active-runtime branch content

Runtime HEAD contains one carried prompt-library commit relative to its `origin/main`:

```text
96d222e52 feat(prompt-library): add Canopy-backed prompt profile adapter (step 1)
25 files changed, 3675 insertions(+), 1 deletion(-)
```

Files added/changed by that carried commit:

```text
hermes_agent/prompt_library/README.md
hermes_agent/prompt_library/__init__.py
hermes_agent/prompt_library/_version.py
hermes_agent/prompt_library/apply.py
hermes_agent/prompt_library/canopy.py
hermes_agent/prompt_library/errors.py
hermes_agent/prompt_library/manifest.py
hermes_agent/prompt_library/paths.py
hermes_agent/prompt_library/profile_check.py
hermes_agent/prompt_library/receipt.py
hermes_agent/prompt_library/render.py
hermes_agent/prompt_library/warnings.py
hermes_cli/commands.py
hermes_cli/main.py
hermes_cli/prompts.py
tests/hermes_cli/test_prompts_command.py
tests/prompt_library/__init__.py
tests/prompt_library/fixtures/cn_render_gelby_default.json
tests/prompt_library/fixtures/tac-director-config.yaml
tests/prompt_library/test_apply.py
tests/prompt_library/test_canopy.py
tests/prompt_library/test_invariants.py
tests/prompt_library/test_manifest.py
tests/prompt_library/test_render.py
tests/prompt_library/test_smoke_gelby_default.py
```

Classification: port, but only through the prompt schema/renderer implementation card (`t_de844862`). Do not merge the live branch wholesale. The prompt composition expert report explicitly requires validating Canopy CLI/source assumptions, adding schema validators, dry-run receipts, backups, fossilization acknowledgement, and tests in the canonical fork.

Migration target for this group: `/Users/greg/repos/hermes-agent` branch for `hermes.prompt_profile.v1` schema/renderer. Source prior art is the runtime `hermes_agent/prompt_library/*` and `hermes_cli/prompts.py`; target tests are `tests/prompt_library/*` and `tests/hermes_cli/test_prompts_command.py` after pruning runtime-only assumptions.

## Target runtime root

Target root remains:

```text
/Users/greg/repos/hermes-agent
```

Reasoning:

1. It is the `gbauto/hermes-agent` operational fork checkout specified by the integration synthesis.
2. It has `origin` on `gbauto/hermes-agent` and `upstream` on `NousResearch/hermes-agent`, both redacted above.
3. It already contains some changes equivalent to staged runtime files.
4. It is the right place for reviewable feature branches and upstream-sync work.

Do not update launchd plists or service commands until:

1. The targeted ports above are reviewed and tested in canonical.
2. Upstream sync card `t_a45b9af7` records before/after SHAs and test results.
3. Ops rollout card `t_8d57d414` chooses profile-by-profile binary path rollout and records smoke/rollback receipts.

## Follow-up migration queue

1. `t_de844862` — prompt profile schema and renderer
   - Port prompt-library prior art from runtime into canonical after validation.
   - Files: `hermes_agent/prompt_library/*`, `hermes_cli/prompts.py`, `hermes_cli/commands.py`, `hermes_cli/main.py`, prompt-library tests.

2. New recommended card — memory compaction/runtime memory budget
   - Port/review dirty runtime memory changes.
   - Files: `agent/agent_init.py`, `hermes_cli/config.py`, `tools/memory_tool.py`, `tests/tools/test_memory_tool.py`, `tests/tools/test_memory_tool_schema.py`.
   - Acceptance: temp-home tests for add/compact/archive, no data loss on drift, action schema updated, config defaults documented.

3. New recommended card — Kanban zombie reaping and goal-mode cleanup
   - Decide whether `reap_worker_zombies()` is still needed after canonical dispatcher changes.
   - Remove or fully implement `goal_mode`/`goal_max_turns`; unused `create_task` parameters are not enough.
   - Files: `hermes_cli/kanban_db.py`, `hermes_cli/kanban.py`, `tools/kanban_tools.py`, dispatcher tests as applicable.

4. Existing canonical Telegram blocker/action-button work
   - Do not port runtime `kbp:` shellout callback as-is.
   - Use signed/opaque callback contract and framework/API path instead.
   - Files: `gateway/platforms/telegram.py`, `gateway/run.py` or Kanban dashboard/API layer, related Telegram tests.

5. Ops cleanup after canonical cutover
   - Discard/quarantine `.hermes.md` and `*.bak.codex-*` from runtime.
   - Do not copy them into `/Users/greg/repos/hermes-agent`.

## Verification notes

- No `launchctl`, plist, or gateway restart command was run.
- No files under `/Users/greg/.openclaw/workspace/hermes-agent` were written by this card.
- Only this report was intentionally written under `/Users/greg/repos/hermes-agent/docs/plans/`.
- Remotes are redacted in this report.
