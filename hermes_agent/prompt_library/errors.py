# hermes_agent/prompt_library/errors.py
"""Exception hierarchy for the Canopy-backed prompt library adapter."""

from __future__ import annotations


class PromptLibraryError(Exception):
    """Base class for all prompt library adapter errors."""


class ManifestValidationError(PromptLibraryError):
    """Raised when a profile manifest fails validation rules V1-V15.

    Attributes:
        errors: list of {"rule": str, "field": str, "message": str} dicts.
    """

    def __init__(self, errors: list[dict]) -> None:
        self.errors = errors
        messages = "; ".join(
            f"[{e.get('rule', '?')}] {e.get('message', 'validation error')}"
            for e in errors
        )
        super().__init__(f"Manifest validation failed: {messages}")


class CanopyMissingError(PromptLibraryError):
    """Raised when the 'cn' binary is not on PATH.

    Includes the install hint for CANOPY_CLI_PIN.
    """

    def __init__(self, install_hint: str | None = None) -> None:
        from hermes_agent.prompt_library._version import CANOPY_CLI_PIN

        self.install_hint = (
            install_hint
            or f"cn not found on PATH. Install: bun add -g @os-eco/canopy-cli@{CANOPY_CLI_PIN}"
        )
        super().__init__(self.install_hint)


class CanopyCliError(PromptLibraryError):
    """Raised when the 'cn' CLI exits non-zero or produces invalid output.

    Attributes:
        returncode: subprocess return code, or None if not applicable.
        stderr: captured stderr string.
    """

    def __init__(
        self,
        message: str,
        returncode: int | None = None,
        stderr: str = "",
    ) -> None:
        self.returncode = returncode
        self.stderr = stderr
        detail = f" (rc={returncode})" if returncode is not None else ""
        full = f"{message}{detail}"
        if stderr:
            full += f"\n  stderr: {stderr.strip()}"
        super().__init__(full)


class BackupFailedError(PromptLibraryError):
    """Raised when backup_target cannot snapshot a live file (disk full, permission)."""


class TargetMissingError(PromptLibraryError):
    """Raised when a required target file does not exist at apply time."""


class CrossTenantInheritError(PromptLibraryError):
    """Raised when the Canopy inheritance chain crosses a tenant boundary
    without an explicit allow-list entry in the manifest.

    Attributes:
        prompt: name of the Canopy prompt that triggered the violation.
        from_tenant: tenant tag found on the ancestor.
        profile_tenant: declared tenant in the manifest.
    """

    def __init__(
        self,
        prompt: str,
        from_tenant: str,
        profile_tenant: str,
    ) -> None:
        self.prompt = prompt
        self.from_tenant = from_tenant
        self.profile_tenant = profile_tenant
        super().__init__(
            f"Cross-tenant inheritance violation: prompt '{prompt}' has tenant "
            f"'{from_tenant}' but profile declares tenant '{profile_tenant}'. "
            f"Add an allow-list entry in manifest cross_tenant_inherit_allow."
        )


class FossilizationAcknowledgmentRequiredError(PromptLibraryError):
    """Raised when apply_render is called without acknowledged_fossilization=True.

    The caller must pass --i-understand-fossilization (CLI) or
    acknowledged_fossilization=True (programmatic API) to proceed.
    """

    def __init__(self) -> None:
        from hermes_agent.prompt_library.warnings import FOSSILIZATION_WARNING

        super().__init__(
            "Fossilization acknowledgment required. Rerun with "
            "--i-understand-fossilization flag.\n\n" + FOSSILIZATION_WARNING
        )
