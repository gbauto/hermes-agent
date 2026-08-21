#!/usr/bin/env python3
"""Dry-run-first helper for wiring the Hermes session cleanup hook into profiles."""
from __future__ import annotations

import argparse
import json
import os
import shlex
from pathlib import Path
from typing import Any

try:
    from hermes_constants import get_hermes_home
except Exception:  # pragma: no cover
    def get_hermes_home() -> Path:  # type: ignore[no-redef]
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

SCHEMA_VERSION = "hermes-session-cleanup-config-receipt.v1"
DEFAULT_TIMEOUT = 30


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-home", type=Path, default=get_hermes_home())
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--apply", action="store_true", help="Write config files. Default is dry-run only.")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run; accepted for explicitness.")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def discover_profile_configs(hermes_home: Path) -> list[Path]:
    profiles_dir = hermes_home.expanduser() / "profiles"
    if not profiles_dir.exists():
        return []
    return sorted(p for p in profiles_dir.glob("*/config.yaml") if p.is_file())


def hook_command(repo_root: Path) -> str:
    script = repo_root.expanduser().resolve() / "scripts" / "hermes_session_cleanup_stub.py"
    return f"python3 {shlex.quote(str(script))} --json"


def load_config(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not text.strip():
        return {}
    if yaml is None:
        # Deliberately minimal fallback: preserve safety by refusing to apply.
        return {"__raw_text__": text}
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def dump_config(data: dict[str, Any]) -> str:
    if yaml is None:
        raise RuntimeError("PyYAML is required for --apply")
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def contains_conflict_markers(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    return any(marker in text for marker in ("<<<<<<<", "=======", ">>>>>>>"))


def desired_entry(command: str, timeout: int) -> dict[str, Any]:
    return {"command": command, "timeout": timeout}


def has_entry(entries: Any, command: str) -> bool:
    if not isinstance(entries, list):
        return False
    for item in entries:
        if isinstance(item, dict) and item.get("command") == command:
            return True
        if isinstance(item, str) and item == command:
            return True
    return False


def proposed_config(data: dict[str, Any], command: str, timeout: int) -> tuple[dict[str, Any], bool]:
    updated = dict(data)
    hooks = updated.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        updated["hooks"] = hooks
    entries = hooks.get("on_session_end")
    if not isinstance(entries, list):
        entries = []
        hooks["on_session_end"] = entries
    changed = False
    if not has_entry(entries, command):
        entries.append(desired_entry(command, timeout))
        changed = True
    if updated.get("hooks_auto_accept") is not True:
        updated["hooks_auto_accept"] = True
        changed = True
    return updated, changed


def plan_for_config(path: Path, command: str, timeout: int) -> dict[str, Any]:
    if contains_conflict_markers(path):
        return {
            "profile": path.parent.name,
            "config_path": str(path),
            "action": "skipped",
            "reason": "conflict_markers_present",
            "changed": False,
            "proposed_hooks": [desired_entry(command, timeout)],
        }
    data = load_config(path)
    if "__raw_text__" in data:
        return {
            "profile": path.parent.name,
            "config_path": str(path),
            "action": "skipped",
            "reason": "yaml_unavailable_for_nontrivial_config",
            "changed": False,
            "proposed_hooks": [desired_entry(command, timeout)],
        }
    proposed, changed = proposed_config(data, command, timeout)
    return {
        "profile": path.parent.name,
        "config_path": str(path),
        "action": "would_update" if changed else "unchanged",
        "reason": "hook_missing" if changed else "hook_already_present",
        "changed": changed,
        "proposed_hooks": proposed.get("hooks", {}).get("on_session_end", []),
        "proposed_hooks_auto_accept": proposed.get("hooks_auto_accept"),
        "_proposed_config": proposed,
    }


def apply_plan(plan: dict[str, Any]) -> None:
    path = Path(plan["config_path"])
    if contains_conflict_markers(path):
        plan["action"] = "skipped"
        plan["reason"] = "conflict_markers_present"
        return
    if not plan.get("changed"):
        return
    proposed = plan.pop("_proposed_config")
    path.write_text(dump_config(proposed), encoding="utf-8")
    plan["action"] = "updated"
    plan["reason"] = "applied"


def receipt(plans: list[dict[str, Any]], *, command: str, apply: bool, hermes_home: Path) -> dict[str, Any]:
    public_plans = []
    for plan in plans:
        item = {k: v for k, v in plan.items() if not k.startswith("_")}
        public_plans.append(item)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "apply" if apply else "dry_run",
        "hermes_home": str(hermes_home.expanduser().resolve()),
        "command": command,
        "profile_config_count": len(public_plans),
        "changed_count": sum(1 for p in public_plans if p.get("changed")),
        "plans": public_plans,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    apply = bool(args.apply and not args.dry_run)
    command = hook_command(args.repo_root)
    configs = discover_profile_configs(args.hermes_home)
    plans = [plan_for_config(path, command, args.timeout) for path in configs]
    if apply:
        for plan in plans:
            apply_plan(plan)
    out = receipt(plans, command=command, apply=apply, hermes_home=args.hermes_home)
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print(f"{out['mode']}: {out['changed_count']}/{out['profile_config_count']} profile configs need/update the hook")
        for plan in out["plans"]:
            print(f"- {plan['profile']}: {plan['action']} ({plan['config_path']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
