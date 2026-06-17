# hermes_agent/prompt_library/README.md
# Prompt Library Adapter — Module Developer Doc

This module implements a thin Hermes-native adapter on top of Jaymin West's
[Canopy](https://github.com/jayminwest/canopy) prompt-composition CLI.

## Module layout

```
hermes_agent/prompt_library/
├── __init__.py          # Public surface re-exports (see below)
├── _version.py          # PROMPT_LIBRARY_SCHEMA, CANOPY_CLI_PIN, ADAPTER_VERSION
├── paths.py             # Path helpers (library root, receipts, backups, staging)
├── manifest.py          # load_manifest + validate_manifest (V1-V15)
├── canopy.py            # cn subprocess wrapper (render, show, validate)
├── render.py            # render_profile orchestrator
├── apply.py             # apply_render + backup_target + patch_config_addendum
├── receipt.py           # write_receipt + receipt schema
├── profile_check.py     # check_profile_exists
├── warnings.py          # FOSSILIZATION_WARNING constant
└── errors.py            # Exception hierarchy
```

## Public surface

```python
from hermes_agent.prompt_library import (
    render_profile,
    apply_render,
    backup_target,
    patch_config_addendum,
    write_receipt,
    validate_manifest,
    load_manifest,
    check_profile_exists,
    FOSSILIZATION_WARNING,
    # Errors:
    PromptLibraryError,
    ManifestValidationError,
    CanopyMissingError,
    CanopyCliError,
    BackupFailedError,
    TargetMissingError,
    CrossTenantInheritError,
    FossilizationAcknowledgmentRequiredError,
)
```

## CLI surface

```
hermes prompts render <profile>  [--staging-dir PATH] [--no-staging] [--json] [--library-root PATH]
hermes prompts apply <profile>   --i-understand-fossilization [--json] [--library-root PATH]
hermes prompts list
hermes prompts receipts <profile> [--limit N]
hermes prompts doctor            [--seed-step-1]
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0    | Success |
| 1    | Generic error |
| 2    | Manifest validation error |
| 3    | cn binary missing |
| 4    | cn CLI error |
| 5    | Cross-tenant inheritance violation |
| 6    | --i-understand-fossilization flag missing |
| 7    | Backup failed |
| 8    | Target write failed |

## Key design constraints

- `render_profile` is ALWAYS a dry run. No live files touched.
- `apply_render` requires `acknowledged_fossilization=True`.
- `patch_config_addendum` uses ruamel.yaml round-trip with a pre-edit
  integrity check. If the file cannot round-trip, CanopyCliError is raised.
- `backup_target` runs BEFORE any write in `apply_render`.
- FOSSILIZATION_WARNING is printed on every render and apply and included
  in every receipt.
