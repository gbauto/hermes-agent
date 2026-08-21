---
title: Session Cleanup Hook
sidebar_position: 11
---

# Session Cleanup Hook

Hermes can write a small facts-only reference stub when a substantial session ends. The hook is designed for second-brain indexing, not for transcript export.

Generated stubs are written to:

```text
second-brain/intelligence/hermes-sessions/<profile>/<date>-<short_id>.md
```

The writer uses `state.db` session metadata and message previews. It does not re-parse JSONL transcripts, raw session snapshots, or terminal logs.

## What gets written

Each stub contains:

- schema version: `hermes-session-reference.v1`
- profile name
- session id
- source platform
- start/end timestamps
- message and tool-call counts
- model name
- short sanitized title and preview
- token and cost counters when `state.db` has them
- a reminder to use `/resume <session_id>` or session search for full inspection

## What is excluded

The stub intentionally excludes:

- system prompts
- raw transcripts
- tool arguments
- raw chat ids
- OAuth values
- API keys, passwords, and tokens
- `.env` contents
- private chain-of-thought or reasoning blobs

The preview is short, whitespace-normalized, and redacted for common secret patterns before writing.

## Hook script

The hook consumer lives at:

```text
scripts/hermes_session_cleanup_stub.py
```

It accepts the existing `on_session_end` shell-hook payload on stdin:

```json
{
  "hook_event_name": "on_session_end",
  "session_id": "sess_abc123",
  "cwd": "/path/to/current/workdir",
  "extra": {
    "completed": true,
    "interrupted": false,
    "model": "provider/model",
    "platform": "cli",
    "profile": "default"
  }
}
```

Run it manually with a fixture database:

```bash
python3 scripts/hermes_session_cleanup_stub.py \
  --state-db /path/to/state.db \
  --second-brain-root /path/to/second-brain \
  --profile default \
  --json < hook-payload.json
```

Useful options:

- `--state-db PATH`: defaults to `$HERMES_HOME/state.db`
- `--second-brain-root PATH`: defaults to `$HERMES_SECOND_BRAIN_ROOT`, then `<cwd>/second-brain` when present, then `$HERMES_HOME/second-brain`
- `--profile NAME`: overrides `$HERMES_PROFILE` or `extra.profile`
- `--min-messages N`: default `4`
- `--min-tool-calls N`: default `1`
- `--min-duration-seconds N`: default `120`
- `--include-incomplete`: allow interrupted/incomplete sessions
- `--dry-run`: emit a receipt without writing the stub
- `--json`: print a machine-readable receipt

The script exits successfully even when it skips a session. Check the JSON receipt fields `action`, `substantial`, and `reason`.

## Substantial-session filter

By default, a session is substantial if it is complete and any threshold matches:

- at least 4 messages
- at least 1 tool call
- at least 120 seconds duration

Skipped sessions return receipt reasons such as:

- `below_thresholds`
- `incomplete_session`
- `session_not_found`
- `not_on_session_end`

## Dry-run profile wiring

The config helper lives at:

```text
scripts/hermes_session_cleanup_config.py
```

It discovers profile config files under:

```text
$HERMES_HOME/profiles/*/config.yaml
```

Dry-run is the default:

```bash
python3 scripts/hermes_session_cleanup_config.py --json
```

The helper proposes this hook entry for each profile:

```yaml
hooks:
  on_session_end:
    - command: "<absolute-Hermes-Python> <repo>/scripts/hermes_session_cleanup_stub.py --json"
      timeout: 30
hooks_auto_accept: true
```

The helper persists the exact Python executable that ran it rather than a
bare `python3` command. This is required on native Windows Hermes installs,
which provide `python.exe` but may not provide a `python3` alias.

Live writes require an explicit flag:

```bash
python3 scripts/hermes_session_cleanup_config.py --apply --json
```

Use the JSON dry-run receipt for review before applying changes across many profiles. The helper refuses to apply a config file that contains merge-conflict markers.

## Operational guidance

- Run the config helper in dry-run mode first.
- Review the proposed profile count and command path.
- Apply only after operator approval.
- Keep live rollout separate from code review.
- Use the stub receipt as the hook smoke-test proof.

## Troubleshooting

- `session_not_found`: verify the hook is using the intended `HERMES_HOME/state.db`.
- `incomplete_session`: the session had no `ended_at`; use `--include-incomplete` only if interrupted sessions should be indexed.
- `below_thresholds`: lower the thresholds if short sessions should be referenced.
- `error: OperationalError`: the database may be locked or missing the expected tables.
