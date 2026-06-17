# hermes_agent/prompt_library/warnings.py
"""Fossilization warning constant and helpers for the prompt library adapter."""

from __future__ import annotations

FOSSILIZATION_WARNING = (
    "FOSSILIZATION WARNING: Changes to SOUL.md or config.yaml:system_prompt_addendum\n"
    "do NOT take effect in any currently active Telegram / gateway session. The system\n"
    "prompt is fossilized at session init. To pick up the new prompt you must either:\n"
    "  (a) restart the gateway (hermes gateway restart) and start a fresh session, OR\n"
    "  (b) quarantine / clear the active session mapping so the next message starts\n"
    "      a new session with the updated prompt.\n"
    "See ops runbook: ~/.hermes/kanban/workspaces/ops/canopy-prompt-library-rollout.md"
)


def print_fossilization_warning() -> None:
    """Print the fossilization warning to stdout, framed with asterisks."""
    lines = FOSSILIZATION_WARNING.splitlines()
    for line in lines:
        print(f"*** {line}")
