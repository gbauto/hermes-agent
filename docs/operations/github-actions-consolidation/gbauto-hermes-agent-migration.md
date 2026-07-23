# gbauto/hermes-agent GitHub Actions consolidation receipt

Task: `t_f27c6955`
Repo: `gbauto/hermes-agent`
Branch: `gha/t_11beed68-gbauto-hermes-agent`
Catalog source: `gbauto/gbautomation/.github/workflows/reusable-pr-gate.yml@5bcb4f40e0dc65d5c5f838bbd16794bda185940d`
Catalog PR: https://github.com/gbauto/gbautomation/pull/691 (`mergeStateStatus: CLEAN` at migration time)

## Repository boundary proof

- `origin`: `https://github.com/gbauto/hermes-agent.git`
- `upstream`: `https://github.com/NousResearch/hermes-agent.git`
- `origin` default branch: `main`
- `upstream` default branch: `main`
- Official upstream mutation: none. All work is on the `gbauto/hermes-agent` worktree branch above.

## Scope

Migrated only plain CI/lint/test/docs/uv/contributor caller surfaces where the reusable PR gate could preserve trigger surfaces and visible job labels through `required_check_alias`.

Preserved in place:

- `.github/workflows/supply-chain-audit.yml` — local PR-commenting security logic requires `pull-requests: write`, PR base/head SHAs, and local `gh pr comment` behavior.
- `.github/workflows/osv-scanner.yml` — external OSV reusable scanner already purpose-specific.
- `.github/workflows/nix.yml` and `.github/workflows/nix-lockfile-fix.yml` — Nix-specific setup/comment/fix behavior.
- `.github/workflows/history-check.yml` — history/common-ancestor guard.
- `.github/workflows/docker-publish.yml` — multi-arch Docker publish and smoke behavior.
- `.github/workflows/deploy-site.yml` and `.github/workflows/skills-index.yml` — Pages/site deployment and generated skills index publish behavior.
- `.github/workflows/upload_to_pypi.yml` — PyPI release/signing workflow.

## Before/after trigger and job summary

### `.github/workflows/tests.yml`

Before:

- Triggers: `push` to `main` and `pull_request` to `main`, with docs/markdown path ignores.
- Jobs: `test`, `e2e`.
- Behavior: checkout, install ripgrep, setup uv/Python 3.11, install `[all,dev]`, run `scripts/run_tests_parallel.py` and `tests/e2e/` with API keys blanked.

After:

- Triggers: unchanged.
- Jobs/check labels: `test`, `e2e` preserved via caller job names and `required_check_alias`.
- Behavior: same explicit ripgrep install, uv install, venv creation, dependency install, and test commands supplied as caller inputs to reusable PR gate.

### `.github/workflows/lint.yml`

Before:

- Triggers: `push` to `main` and `pull_request` to `main`, with markdown/docs/website path ignores.
- Jobs: `lint-diff`, `ruff-blocking`, `windows-footguns`.
- Behavior: advisory `lint-diff` posts PR comments; blocking ruff and Windows footgun checks run locally.

After:

- Triggers: unchanged.
- Jobs/check labels: `lint-diff`, `ruff enforcement (blocking)`, and `Windows footguns (blocking)` preserved.
- Behavior: `lint-diff` remains local because it writes PR comments/artifacts; the two plain blocking checks call reusable PR gate with explicit commands.
- Additional actionlint hygiene: moved untrusted `github.head_ref` interpolation into `env` before use in the inline script.

### `.github/workflows/docs-site-checks.yml`

Before:

- Triggers: `pull_request` on `website/**` and workflow file changes; `workflow_dispatch`.
- Jobs: `docs-site-checks`.
- Behavior: setup Node 20/Python 3.11, `npm ci`, install `ascii-guard`/`pyyaml`, regenerate skill docs/catalogs, lint diagrams, build Docusaurus.

After:

- Triggers: unchanged.
- Jobs/check labels: `docs-site-checks` preserved.
- Behavior: same commands supplied to reusable PR gate; working directory remains `website` for npm cache/install, then commands `cd ..` for repo-root Python scripts.

### `.github/workflows/uv-lockfile-check.yml`

Before:

- Triggers: `push`/`pull_request` to `main` on `pyproject.toml`, `uv.lock`, or workflow file changes.
- Jobs: `check` with visible name `uv lock --check`.
- Behavior: setup uv and run `uv lock --check`; emits the existing explanatory failure summary.

After:

- Triggers: unchanged.
- Jobs/check labels: `check` / `uv lock --check` preserved.
- Behavior: reusable PR gate installs uv with pip and runs the same `uv lock --check` plus the existing failure summary.

### `.github/workflows/contributor-check.yml`

Before:

- Triggers: `pull_request` to `main` on Python files and workflow file changes.
- Jobs: `check-attribution`.
- Behavior: checkout full history, compare PR commit author emails against `scripts/release.py` `AUTHOR_MAP`.

After:

- Triggers: unchanged.
- Jobs/check labels: `check-attribution` preserved.
- Behavior: same git/AUTHOR_MAP command body supplied to reusable PR gate; catalog checkout uses `fetch-depth: 0`.

## Validation evidence

Local gates run before commit:

- `actionlint .github/workflows/*.yml`: pass.
- Python YAML parse across 14 workflow files: pass.
- `git diff --check`: pass.
- High-confidence secret scan over workflow diff: pass.
- Catalog SHA proof: `5bcb4f40e0dc65d5c5f838bbd16794bda185940d` exists as a commit in the gbautomation worktree.

Representative PR smoke:

- Pending until this branch is pushed and GitHub Actions runs on the PR. This receipt should be updated with PR URL, head SHA, and check results before closeout.

## Rollback

Revert the migration commit on `gha/t_11beed68-gbauto-hermes-agent` or close the PR. No branch protection settings, repository settings, secrets, schedules, or upstream `NousResearch/hermes-agent` refs were changed.
