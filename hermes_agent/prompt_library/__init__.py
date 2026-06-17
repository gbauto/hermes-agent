# hermes_agent/prompt_library/__init__.py
"""Canopy-backed Hermes Prompt Library — public surface."""

from hermes_agent.prompt_library._version import (  # noqa: F401
    ADAPTER_VERSION,
    CANOPY_CLI_PIN,
    PROMPT_LIBRARY_SCHEMA,
)
from hermes_agent.prompt_library.apply import (  # noqa: F401
    apply_render,
    backup_target,
    patch_config_addendum,
)
from hermes_agent.prompt_library.errors import (  # noqa: F401
    BackupFailedError,
    CanopyCliError,
    CanopyMissingError,
    CrossTenantInheritError,
    FossilizationAcknowledgmentRequiredError,
    ManifestValidationError,
    PromptLibraryError,
    TargetMissingError,
)
from hermes_agent.prompt_library.manifest import (  # noqa: F401
    load_manifest,
    validate_manifest,
)
from hermes_agent.prompt_library.profile_check import check_profile_exists  # noqa: F401
from hermes_agent.prompt_library.receipt import write_receipt  # noqa: F401
from hermes_agent.prompt_library.render import render_profile  # noqa: F401
from hermes_agent.prompt_library.warnings import FOSSILIZATION_WARNING  # noqa: F401
