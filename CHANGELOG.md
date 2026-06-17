# Changelog

## v2026.6.17-gbauto.1 - 2026-06-17

GBAuto fork release for the OpenClaw retirement migration. This release pins the hoisted nested OpenClaw `hermes-agent` work so client `hermes/config.yaml` files can reference a reviewable engine version.

Hoisted migration commits covered:

- `0b5b8fb92` fix(cron): format delivered job output as mobile digest
- `4f95bbf22` fix(cron): format delivered job output as mobile bullets
- `452219bd3` feat(telegram): handle Kanban proposal callbacks
- `9a51552ac` feat(observability): add Kanban join metadata to Langfuse traces
- `57dd4c352` feat(memory): auto-compact persistent memory near capacity
- `57db9b310` docs(agents): require TAC Lead review for architecture decisions
- `552a357c4` docs(kanban): require per-PRD boards and branches
- `96d222e52` feat(prompt-library): add Canopy-backed prompt profile adapter (step 1)
- `49dd776d8` Merge pull request #43041 from NousResearch/fix/fable-anthropic
- `d7886da08` add Fable 5 to model list for Anthropic provider

Notes:

- Upstream `NousResearch/hermes-agent` is not pushable from the Mac Mini credentials.
- GBAuto maintains `gbauto/hermes-agent` as the migration fork.
- This tag is intended as the initial `hermes_engine_version` pin for client Hermes configs.
