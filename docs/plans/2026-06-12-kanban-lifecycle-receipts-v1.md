# Kanban lifecycle receipts v1

Status: implemented in `hermes_cli/kanban_db.py`.

## Purpose

Kanban SQLite remains the lifecycle source of truth. Supabase, portal, Telegram, and other mirrors can read a small normalized receipt from terminal `task_events` without depending on raw run metadata or private worker context.

## Receipt location

Receipts are embedded under `task_events.payload.receipt` for terminal lifecycle events. This keeps the SQLite schema additive-free and preserves existing event payload fields such as `summary`, `reason`, `artifacts`, and dispatcher diagnostics.

Covered terminal event kinds:

- `completed`
- `blocked`
- `crashed`
- `timed_out`
- `gave_up`

## Versioned shape

```json
{
  "schema_version": 1,
  "type": "kanban.lifecycle_receipt",
  "source": "hermes-kanban",
  "source_table": "task_events",
  "source_id": "15378",
  "task_id": "t_1d244f48",
  "run_id": 15378,
  "kind": "completed",
  "outcome": "completed",
  "status": "done",
  "created_at": 1781275199,
  "summary": "first-line handoff",
  "reason": "blocked reason when kind=blocked",
  "error": "bounded failure string when kind=crashed/timed_out/gave_up",
  "metadata": {"sanitized": true},
  "artifacts": ["/absolute/path/when safe"]
}
```

Optional keys are omitted when empty. `source_id` is the run id when available, otherwise the task id for synthetic/no-run events.

## Redaction and size policy

The receipt sanitizer is intentionally conservative and mirror-friendly:

- Secret-like metadata keys are replaced with `[REDACTED]`.
- Secret-like string values are replaced with `[REDACTED]`.
- Nested dictionaries and lists are sanitized recursively.
- Summary text is bounded to 400 characters.
- Reason text is bounded to 320 characters.
- Error and generic string fields are bounded to 500 characters.
- Receipt metadata does not include raw prompts, full transcripts, OAuth material, service-role keys, Telegram tokens, or decrypted secrets.

Existing run rows still keep their prior summary/metadata behavior for worker handoff. Consumers that need full local audit context should read Kanban SQLite directly, not the receipt mirror.

## Rollback notes

This change is schema-free. To roll back a consumer, ignore `payload.receipt` and continue using the existing top-level event payload fields. To roll back code emission, remove the `receipt=` arguments and helper functions from `hermes_cli/kanban_db.py`; old databases do not require migration because receipts live only in JSON event payloads.

## Validation

Focused unit coverage lives in `tests/hermes_cli/test_kanban_db.py`:

- completed receipt shape
- blocked/crashed/timed_out receipt shapes
- recursive redaction of secret-like keys and values

Existing gateway notifier tests continue to consume the legacy event payload fields and should pass unchanged.
