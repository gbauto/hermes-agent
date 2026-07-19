# jason estate runtime local-mods snapshot — 2026-07-19

Uncommitted drift captured from the LIVE runtime at
`/Users/jason-agent/.openclaw/workspace/hermes-agent` (upstream
NousResearch/hermes-agent main @ dae94fa65 + local mods, secret-scanned):

- `tools_config.py` — estate tool configuration (large divergence vs fork)
- `toolsets.py` — 6-line estate addition vs upstream
- `kanban-orchestrator-SKILL.md` — Carlos's evolved orchestrator contract
  (~312-line rework; the operative dispatch doctrine on the estate)

Snapshotted verbatim (NOT merged into the fork tree) because the estate base
and the fork have diverged independently — reconcile deliberately, never by
wholesale copy. A future `hermes update` on the estate must re-apply these
plus the source-receipts repair from this branch.
