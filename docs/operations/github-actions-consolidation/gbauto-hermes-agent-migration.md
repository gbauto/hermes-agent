# gbauto/hermes-agent GitHub Actions consolidation receipt

Task: `t_f27c6955`
Repo: `gbauto/hermes-agent`
Branch: `gha/t_11beed68-gbauto-hermes-agent`
PR: https://github.com/gbauto/hermes-agent/pull/18
Final head: `915abc4f6e36e1e25d170401d16de2860777ae74`
Closeout class: `committed_unmerged_draft_clean`

## Repository boundary proof

- `origin`: `https://github.com/gbauto/hermes-agent.git`
- `origin` default branch: `main`
- `origin` visibility: public
- `upstream`: `https://github.com/NousResearch/hermes-agent.git`
- Official upstream mutation: none. All work is on the `gbauto/hermes-agent` branch above.
- No repository settings, branch protection, secrets, schedules, release destinations, or upstream refs were changed.

## Scope and decision

The first migration attempt changed the plain runtime workflows to call the private `gbauto/gbautomation` reusable catalog. GitHub Actions could not resolve that private catalog from this public fork, so the final branch keeps these workflows public-safe and local.

Changed plain CI/lint/test/docs/uv/contributor surfaces:

- `.github/workflows/tests.yml`
- `.github/workflows/lint.yml`
- `.github/workflows/docs-site-checks.yml`
- `.github/workflows/uv-lockfile-check.yml`
- `.github/workflows/contributor-check.yml`

Preserved in place and out of migration scope:

- `.github/workflows/supply-chain-audit.yml` — local PR-commenting security logic requires `pull-requests: write`, PR base/head SHAs, and local `gh pr comment` behavior.
- `.github/workflows/osv-scanner.yml` — external OSV reusable scanner already purpose-specific.
- `.github/workflows/nix.yml` and `.github/workflows/nix-lockfile-fix.yml` — Nix-specific setup/comment/fix behavior.
- `.github/workflows/history-check.yml` — history/common-ancestor guard.
- `.github/workflows/docker-publish.yml` — multi-arch Docker publish and smoke behavior.
- `.github/workflows/deploy-site.yml` and `.github/workflows/skills-index.yml` — Pages/site deployment and generated skills index publish behavior.
- `.github/workflows/upload_to_pypi.yml` — PyPI release/signing workflow.

## Before/after workflow surface

Workflow file count remains 14. The changed workflows preserve their trigger surfaces and visible job labels; no schedules were added or removed.

### `.github/workflows/tests.yml`

Before:

- Triggers: `push` to `main` and `pull_request` to `main`, with docs/markdown path ignores.
- Jobs/check labels: `test`, `e2e`.
- Behavior: checkout, install ripgrep, setup uv/Python 3.11, install `[all,dev]`, run `scripts/run_tests_parallel.py` and `tests/e2e/` with API keys blanked.

After:

- Triggers: unchanged.
- Jobs/check labels: `test`, `e2e` preserved.
- Behavior: public-safe local gate with explicit checkout, Python/uv setup, install, test, and e2e commands.

### `.github/workflows/lint.yml`

Before:

- Triggers: `push` to `main` and `pull_request` to `main`, with markdown/docs/website path ignores.
- Jobs/check labels: `ruff + ty diff`, `ruff enforcement (blocking)`, `Windows footguns (blocking)`.
- Behavior: advisory `lint-diff` posts PR comments; blocking ruff and Windows footgun checks run locally.

After:

- Triggers: unchanged.
- Jobs/check labels: `ruff + ty diff`, `ruff enforcement (blocking)`, and `Windows footguns (blocking)` preserved.
- Behavior: remains local/public-safe; untrusted PR ref interpolation was moved through environment variables before shell use.
- Closeout tweak: the blocking ruff command remains visible in the workflow for auditability.

### `.github/workflows/docs-site-checks.yml`

Before:

- Triggers: `pull_request` on `website/**` and workflow file changes; `workflow_dispatch`.
- Jobs/check labels: `docs-site-checks`.
- Behavior: setup Node 20/Python 3.11, `npm ci`, install `ascii-guard`/`pyyaml`, regenerate skill docs/catalogs, lint diagrams, build Docusaurus.

After:

- Triggers: unchanged.
- Jobs/check labels: `docs-site-checks` preserved.
- Behavior: public-safe local gate with the same dependency install and documentation build command sequence.

### `.github/workflows/uv-lockfile-check.yml`

Before:

- Triggers: `push`/`pull_request` to `main` on `pyproject.toml`, `uv.lock`, or workflow file changes.
- Jobs/check labels: `check` / `uv lock --check`.
- Behavior: setup uv and run `uv lock --check`; emits the existing explanatory failure summary.

After:

- Triggers: unchanged.
- Jobs/check labels: `check` / `uv lock --check` preserved.
- Behavior: public-safe local gate runs `uv lock --check` and preserves the explanatory failure summary.

### `.github/workflows/contributor-check.yml`

Before:

- Triggers: `pull_request` to `main` on Python files and workflow file changes.
- Jobs/check labels: `check-attribution`.
- Behavior: checkout full history, compare PR commit author emails against `scripts/release.py` `AUTHOR_MAP`.

After:

- Triggers: unchanged.
- Jobs/check labels: `check-attribution` preserved.
- Behavior: public-safe local gate; full-history checkout preserved.
- Additional allowlist: `agent@gbautomation.com` automation commits are accepted.

## Validation evidence

Local gates run on final head `915abc4f6e36e1e25d170401d16de2860777ae74`:

- `actionlint .github/workflows/contributor-check.yml .github/workflows/docs-site-checks.yml .github/workflows/lint.yml .github/workflows/tests.yml .github/workflows/uv-lockfile-check.yml`: pass.
- Python `yaml.safe_load` across 14 workflow files: pass.
- `git diff --check origin/main...HEAD`: pass.
- High-confidence secret scan over `origin/main...HEAD`: pass, 0 hits.
- Repo/default proof: `gh repo view gbauto/hermes-agent` returns public repo, URL `https://github.com/gbauto/hermes-agent`, default branch `main`.

GitHub PR smoke on PR #18, final head `915abc4f6e36e1e25d170401d16de2860777ae74`:

- PR state: `OPEN`, draft: `true`, mergeability: `MERGEABLE`, merge state: `CLEAN`.
- `Contributor Attribution Check / check-attribution`: success, https://github.com/gbauto/hermes-agent/actions/runs/30059620270/job/89378433284
- `Docs Site Checks / docs-site-checks`: success, https://github.com/gbauto/hermes-agent/actions/runs/30059620289/job/89378433358
- `History Check / check-common-ancestor`: success, https://github.com/gbauto/hermes-agent/actions/runs/30059620255/job/89378433210
- `Lint (ruff + ty) / ruff + ty diff`: success, https://github.com/gbauto/hermes-agent/actions/runs/30059620286/job/89378433608
- `Lint (ruff + ty) / ruff enforcement (blocking)`: success, https://github.com/gbauto/hermes-agent/actions/runs/30059620286/job/89378433580
- `Lint (ruff + ty) / Windows footguns (blocking)`: success, https://github.com/gbauto/hermes-agent/actions/runs/30059620286/job/89378433609
- `Tests / test`: success, https://github.com/gbauto/hermes-agent/actions/runs/30059620290/job/89378433605
- `Tests / e2e`: success, https://github.com/gbauto/hermes-agent/actions/runs/30059620290/job/89378433479
- `uv.lock check / uv lock --check`: success, https://github.com/gbauto/hermes-agent/actions/runs/30059620282/job/89378433526
- `Nix / nix (ubuntu-latest)`: success, https://github.com/gbauto/hermes-agent/actions/runs/30059620250/job/89378433337
- `Nix / nix (macos-latest)`: success, https://github.com/gbauto/hermes-agent/actions/runs/30059620250/job/89378433359

## Draft/merge status

The PR is intentionally left as an open draft. The technical gates are green and the PR is `MERGEABLE/CLEAN`; repo-owner undraft/approval is the remaining merge gate.

## Rollback

Close PR #18 or revert branch commits through `915abc4f6e36e1e25d170401d16de2860777ae74`. No branch protection settings, repository settings, secrets, schedules, release/publish workflows, or upstream `NousResearch/hermes-agent` refs were changed.
