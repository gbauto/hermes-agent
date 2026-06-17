# Prompt-library adapter validation gates

Task: t_de844862
Generated: 2026-06-11T21:29:55Z
Updated: 2026-06-11T22:00:00Z
Parent report: /Users/greg/repos/gbautomation/second-brain/intelligence/integration-experts/t_0809baf9-prompt-composition-canopy-expert.md
Reference adapter: /Users/greg/.openclaw/workspace/hermes-agent at feat/prompt-library-canopy-adapter, commit 96d222e52
Canonical repo checked: /Users/greg/repos/hermes-agent

## Verdict

Do not merge a Canopy-backed prompt-library adapter unless these gates pass:

1. Adapter tests pass without requiring Canopy during Hermes session startup.
2. CLI smoke covers missing `cn` and `render --json` output shape.
3. Live apply requires the fossilization acknowledgement flag.
4. Manifest validation rejects malformed manifests and failed YAML parse.
5. Cross-tenant inheritance is enforced with both negative and positive tests.
6. Live apply writes backups before targets and writes receipts after apply.
7. `config.yaml.system_prompt_addendum` mutation fails closed on lossy YAML round trips.
8. `agent/prompt_builder.py` contains no runtime Canopy or `cn` integration.
9. Any `cn emit --check` or equivalent runs only inside the prompt-library project state, never inside session startup prompt assembly.

Current status: the reference adapter passed its existing 39 tests, and the canonical implementation for `t_de844862` now includes a 48-test prompt-library/CLI suite, including temp-home acceptance coverage. The canonical suite covers dry-run staging/receipt behavior, fossilization acknowledgement, config-addendum patch preservation, secret-file non-read behavior, CLI render dry-run behavior, `doctor` missing-Canopy exit behavior, and render-level cross-tenant inheritance fail-closed checks.

Current environment note (2026-06-16): `cn` is not installed on this Mac (`which cn` empty, `cn --version` command-not-found). Live Canopy source/CLI behavior remains explicitly deferred to an environment with `@os-eco/canopy-cli@0.2.6` installed; canonical tests validate the subprocess boundary and CLI contracts with a fake `cn` and missing-`cn` paths.

## Exact commands run

From `/Users/greg/.openclaw/workspace/hermes-agent`:

```bash
python -m pytest tests/prompt_library tests/hermes_cli/test_prompts_command.py -q
```

Result:

```text
.......................................                                  [100%]
39 passed in 0.49s
```

From `/Users/greg/repos/hermes-agent`:

```bash
python - <<'PY'
import ast
from pathlib import Path
for root in [Path('/Users/greg/repos/hermes-agent'), Path('/Users/greg/.openclaw/workspace/hermes-agent')]:
    p = root / 'agent' / 'prompt_builder.py'
    tree = ast.parse(p.read_text(encoding='utf-8'))
    imports=[]; calls=[]; cn_literals=[]
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, 'module', None)
            names = [a.name for a in node.names]
            if mod == 'subprocess' or 'subprocess' in names or (mod and ('canopy' in mod or 'prompt_library' in mod)):
                imports.append((node.lineno, mod, names))
        if isinstance(node, ast.Call):
            f = node.func
            name = ''
            if isinstance(f, ast.Attribute): name = f.attr
            elif isinstance(f, ast.Name): name = f.id
            if name in {'run','Popen','call','check_call','check_output'}:
                calls.append((node.lineno, name))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and ('cn ' in node.value or 'canopy' in node.value):
            cn_literals.append((node.lineno, node.value[:80]))
    ok = not imports and not cn_literals and not calls
    print(f'{p}: {"PASS" if ok else "FAIL"} imports={imports} subprocess_like_calls={calls} cn_canopy_literals={cn_literals}')
PY
```

Result:

```text
/Users/greg/repos/hermes-agent/agent/prompt_builder.py: PASS imports=[] subprocess_like_calls=[] cn_canopy_literals=[]
/Users/greg/.openclaw/workspace/hermes-agent/agent/prompt_builder.py: PASS imports=[] subprocess_like_calls=[] cn_canopy_literals=[]
```

From `/Users/greg/repos/hermes-agent`:

```bash
python - <<'PY'
from pathlib import Path
checks = {
    'missing cn': ['cn_missing', 'cn_path_returns_none'],
    'render --json shape': ['render_json_mode_shape'],
    'apply fossilization ack': ['requires_acknowledged_fossilization', 'without_ack_flag'],
    'manifest validation': ['validate_manifest_rejects', 'validate_manifest_accepts'],
    'cross-tenant inheritance': ['cross_tenant', 'inherit'],
    'backups': ['backup_target'],
    'receipts': ['receipt'],
    'YAML round-trip failure': ['roundtrip', 'lossy_pre_edit'],
}
root = Path('/Users/greg/.openclaw/workspace/hermes-agent')
files = list((root / 'tests' / 'prompt_library').glob('test_*.py')) + [root / 'tests' / 'hermes_cli' / 'test_prompts_command.py']
texts = {str(p.relative_to(root)): p.read_text(encoding='utf-8') for p in files}
for gate, needles in checks.items():
    matches = [name for name, text in texts.items() if any(n in text for n in needles)]
    status = 'PASS' if matches else 'GAP'
    print(f'{status}: {gate}: {", ".join(matches) if matches else "no matching tests"}')
PY
```

Result:

```text
PASS: missing cn: tests/prompt_library/test_canopy.py, tests/hermes_cli/test_prompts_command.py
PASS: render --json shape: tests/hermes_cli/test_prompts_command.py
PASS: apply fossilization ack: tests/prompt_library/test_apply.py, tests/hermes_cli/test_prompts_command.py
PASS: manifest validation: tests/prompt_library/test_manifest.py
PASS: cross-tenant inheritance: tests/prompt_library/test_manifest.py
PASS: backups: tests/prompt_library/test_apply.py
PASS: receipts: tests/prompt_library/test_apply.py
PASS: YAML round-trip failure: tests/prompt_library/test_apply.py
```

Manual review note: the simple coverage scan marks cross-tenant as present because `test_manifest.py` contains `test_validate_manifest_accepts_cross_tenant_with_allow_list`. That is only structural coverage. It does not prove render-time inheritance rejection.

## Coverage assessment

### Missing `cn`

Covered.

Evidence:

- `tests/prompt_library/test_canopy.py::test_cn_missing_raises_canopy_missing_error`
- `tests/prompt_library/test_canopy.py::test_cn_missing_in_render_raises_canopy_missing_error`
- `tests/prompt_library/test_canopy.py::test_cn_path_returns_none_when_missing`
- `tests/hermes_cli/test_prompts_command.py::test_prompts_render_exits_3_when_cn_missing`

Gate:

```bash
python -m pytest tests/prompt_library/test_canopy.py tests/hermes_cli/test_prompts_command.py -q
```

### `render --json` shape

Covered.

Evidence:

- `tests/hermes_cli/test_prompts_command.py::test_prompts_render_json_mode_shape`
- `tests/prompt_library/test_render.py::test_render_returns_sections_with_sha256_and_body_chars`

Required JSON shape:

- `profile`
- `tenant`
- `manifest_path`
- `canopy_cli_version`
- `resolved_from`
- `sections`
- `params_sha256`
- `targets`
- `fossilization_warning`
- `dry_run`

Gate:

```bash
python -m pytest tests/prompt_library/test_render.py tests/hermes_cli/test_prompts_command.py -q
```

### Apply fossilization acknowledgement

Covered.

Evidence:

- `tests/prompt_library/test_apply.py::test_apply_requires_acknowledged_fossilization`
- `tests/hermes_cli/test_prompts_command.py::test_prompts_apply_without_ack_flag_exits_6`
- `tests/prompt_library/test_apply.py::test_apply_writes_receipt_with_fossilization_warning`

Gate:

```bash
python -m pytest tests/prompt_library/test_apply.py tests/hermes_cli/test_prompts_command.py -q
```

### Manifest validation

Covered for schema, tenant, targets, mode, gelby-default config target, bad YAML, missing file, and multi-error collection.

Evidence:

- `tests/prompt_library/test_manifest.py`

Gate:

```bash
python -m pytest tests/prompt_library/test_manifest.py -q
```

### Cross-tenant inheritance

Covered in the canonical acceptance suite.

Evidence:

- `tests/prompt_library/test_prompt_profile_acceptance.py::test_render_rejects_cross_tenant_inheritance_without_allow_list`
- `tests/prompt_library/test_prompt_profile_acceptance.py::test_render_accepts_cross_tenant_inheritance_with_allow_list`
- `tests/prompt_library/test_prompt_profile_acceptance.py::test_render_fails_closed_when_inherited_prompt_tenant_cannot_be_verified`

Why this matters:

- `render_profile()` performs cross-tenant checks over `resolvedFrom` using `cn_show()`.
- The canonical implementation now raises on unverifiable ancestor tenant metadata instead of silently continuing.

Required gate before merge:

```bash
python -m pytest tests/prompt_library/test_prompt_profile_acceptance.py -q -k 'cross_tenant or inherited_prompt_tenant'
```

### Backups

Covered.

Evidence:

- `tests/prompt_library/test_apply.py::test_backup_target_copies_existing_soul_md_with_manifest`
- `tests/prompt_library/test_apply.py::test_backup_target_records_missing_target_without_failing`

Gate:

```bash
python -m pytest tests/prompt_library/test_apply.py -q -k backup
```

### Receipts

Covered.

Evidence:

- `tests/prompt_library/test_apply.py::test_apply_writes_receipt_with_fossilization_warning`

Gate:

```bash
python -m pytest tests/prompt_library/test_apply.py -q -k receipt
```

### YAML round-trip failure behavior

Covered.

Evidence:

- `tests/prompt_library/test_apply.py::test_patch_config_addendum_roundtrips_tac_director_yaml`
- `tests/prompt_library/test_apply.py::test_patch_config_addendum_rejects_lossy_pre_edit_roundtrip`

Gate:

```bash
python -m pytest tests/prompt_library/test_apply.py -q -k 'roundtrip or lossy'
```

### Runtime prompt builder boundary

Covered by static gate and manual source inspection.

No prompt-library, Canopy, `cn`, or subprocess execution integration is present in either canonical or reference `agent/prompt_builder.py` AST. A raw text search for `subprocess` is too noisy because the Kanban guidance string mentions long subprocesses. Use AST checks instead.

Required gate:

```bash
python - <<'PY'
import ast
from pathlib import Path
p = Path('agent/prompt_builder.py')
tree = ast.parse(p.read_text(encoding='utf-8'))
errors = []
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        mod = getattr(node, 'module', None)
        names = [a.name for a in node.names]
        if mod == 'subprocess' or 'subprocess' in names:
            errors.append((node.lineno, 'subprocess import'))
        if mod and ('canopy' in mod or 'prompt_library' in mod):
            errors.append((node.lineno, f'forbidden import {mod}'))
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if 'cn render' in node.value or 'cn emit' in node.value or 'canopy' in node.value:
            errors.append((node.lineno, 'forbidden runtime Canopy string'))
if errors:
    raise SystemExit(f'prompt_builder runtime boundary violation: {errors}')
print('prompt_builder runtime boundary: PASS')
PY
```

## Proposed CI smoke commands

Add these after the adapter is ported to canonical gbauto/hermes-agent:

```yaml
- name: Prompt-library adapter unit tests
  run: |
    source .venv/bin/activate
    python -m pytest tests/prompt_library tests/hermes_cli/test_prompts_command.py -q

- name: Prompt-builder runtime boundary check
  run: |
    source .venv/bin/activate
    python scripts/check_prompt_builder_no_canopy_runtime.py
```

Add a small script at `scripts/check_prompt_builder_no_canopy_runtime.py` using the AST gate above. This avoids false positives from prose strings while still blocking any import or command path that would make Hermes session startup depend on Canopy.

Optional smoke when a test fixture prompt-library project is available:

```bash
python -m pytest tests/prompt_library/test_smoke_gelby_default.py -q
```

If `cn emit --check` is adopted, run it only against the prompt-library project root:

```bash
cd "$HERMES_PROMPT_LIBRARY_ROOT"
cn emit --check
```

Do not call `cn emit --check` from `agent/prompt_builder.py`, `run_agent.py`, gateway session creation, or any startup prompt assembly path.

## Remaining validation notes before broad rollout

1. Add the AST runtime boundary script to CI if this subsystem graduates beyond the acceptance suite.
2. Keep adapter tests isolated under `tests/prompt_library` and any future `tests/hermes_cli/test_prompts_command.py` expansion.
3. Keep Canopy dependency optional at Hermes startup. Missing `cn` should only affect `hermes prompts ...`, not normal agent sessions.

## Merge blocker

Block any design or PR that shells out to `cn` from `agent/prompt_builder.py` during session startup. The only acceptable Canopy subprocess boundary is the explicit prompt-library adapter command path, such as `hermes prompts render`, `hermes prompts apply`, and prompt-library project CI checks.
