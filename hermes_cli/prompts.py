# hermes_cli/prompts.py
"""hermes prompts — Canopy-backed prompt profile CLI.

Subcommands:
  render    Render a profile to staging (dry-run, no live writes).
  apply     Render + write to live profile targets.
  list      List available profile manifests.
  receipts  Show render receipts for a profile.
  doctor    Health check: cn version, library layout, manifest validation.
             --seed-step-1  Initialize ~/.hermes/prompt-library/ for step 1.

Exit codes:
  0  success
  1  generic error
  2  manifest validation error
  3  cn binary missing
  4  cn CLI error
  5  cross-tenant inheritance violation
  6  --i-understand-fossilization flag missing
  7  backup failed
  8  target write failed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Build the subcommand parser
# ---------------------------------------------------------------------------

def build_prompts_parser(subparsers) -> argparse.ArgumentParser:
    """Register ``hermes prompts`` and its subcommands."""
    prompts_parser = subparsers.add_parser(
        "prompts",
        help="Manage Canopy-backed prompt profiles",
        description=(
            "hermes prompts -- render and apply Canopy-backed prompt profiles.\n\n"
            "Dry-run is the default: `hermes prompts render <profile>` writes to staging\n"
            "only. To mutate live profile files, use `hermes prompts apply`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = prompts_parser.add_subparsers(dest="prompts_cmd", title="subcommands")

    # render
    render_p = sub.add_parser("render", help="Render a profile (dry-run)")
    render_p.add_argument("profile", help="Profile name (e.g. gelby-default)")
    render_p.add_argument(
        "--staging-dir",
        metavar="PATH",
        help="Override staging output dir (default: ~/.hermes/prompt-library/staging/<profile>/)",
    )
    render_p.add_argument(
        "--no-staging",
        action="store_true",
        help="Skip writing rendered files to staging (stdout summary only)",
    )
    render_p.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable summary to stdout",
    )
    render_p.add_argument(
        "--library-root",
        metavar="PATH",
        help="Override ~/.hermes/prompt-library/",
    )

    # apply
    apply_p = sub.add_parser("apply", help="Render + write to live profile targets")
    apply_p.add_argument("profile", help="Profile name (e.g. gelby-default)")
    apply_p.add_argument(
        "--i-understand-fossilization",
        action="store_true",
        dest="fossilization_ack",
        help="Required: acknowledge the fossilization warning before live writes.",
    )
    apply_p.add_argument(
        "--library-root",
        metavar="PATH",
        help="Override ~/.hermes/prompt-library/",
    )
    apply_p.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable summary to stdout",
    )

    # list
    sub.add_parser("list", help="List profile manifests")

    # receipts
    receipts_p = sub.add_parser("receipts", help="List render receipts for a profile")
    receipts_p.add_argument("profile", help="Profile name")
    receipts_p.add_argument(
        "--limit",
        type=int,
        default=10,
        metavar="N",
        help="Max receipts to show (default 10)",
    )

    # doctor
    doctor_p = sub.add_parser("doctor", help="Health check: cn, library layout, manifests")
    doctor_p.add_argument(
        "--seed-step-1",
        action="store_true",
        dest="seed_step1",
        help="Initialize ~/.hermes/prompt-library/ with step-1 seed data (gelby-default profile).",
    )

    return prompts_parser


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def _resolve_library_root(library_root_arg: Optional[str]) -> Path:
    from hermes_agent.prompt_library.paths import resolve_library_root

    if library_root_arg:
        return resolve_library_root(Path(library_root_arg))
    return resolve_library_root(None)


def cmd_render(args) -> int:
    """hermes prompts render <profile>"""
    from hermes_agent.prompt_library.errors import (
        CanopyCliError,
        CanopyMissingError,
        CrossTenantInheritError,
        ManifestValidationError,
    )
    from hermes_agent.prompt_library.render import render_profile
    from hermes_agent.prompt_library.warnings import print_fossilization_warning

    library_root = _resolve_library_root(getattr(args, "library_root", None))
    profile = args.profile

    # Determine staging dir
    if getattr(args, "no_staging", False):
        staging = False
    elif getattr(args, "staging_dir", None):
        staging = Path(args.staging_dir)
    else:
        from hermes_agent.prompt_library.paths import staging_dir as default_staging
        staging = default_staging(library_root, profile)

    try:
        result = render_profile(profile, library_root=library_root, staging_dir=staging)
    except ManifestValidationError as exc:
        print(f"ERROR: Manifest validation failed:", file=sys.stderr)
        for err in exc.errors:
            print(f"  [{err['rule']}] {err['field']}: {err['message']}", file=sys.stderr)
        return 2
    except CanopyMissingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    except CanopyCliError as exc:
        print(f"ERROR: Canopy CLI error: {exc}", file=sys.stderr)
        return 4
    except CrossTenantInheritError as exc:
        print(f"ERROR: Cross-tenant inheritance violation: {exc}", file=sys.stderr)
        return 5
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "json_output", False):
        print(json.dumps(result, indent=2))
        return 0

    # Human-readable output
    sections_str = ", ".join(
        f"{s['name']}:{s['sha256']}" for s in result["sections"]
    )
    print(f"Profile:  {result['profile']}  (tenant: {result['tenant']})")
    print(f"Manifest: {result['manifest_path']}")
    print(f"Canopy:   cn {result['canopy_cli_version']}")
    print(f"Resolved: {', '.join(result['resolved_from'])}")
    print(f"Sections: {len(result['sections'])} ({sections_str})")
    print("Targets:")
    for tname, tdata in result["targets"].items():
        content_len = len(tdata.get("content", "").encode("utf-8"))
        staged = "(staged)" if staging else ""
        print(f"  {tname} -> {tdata.get('path', '?')}  ({content_len} bytes rendered {staged})")

    if staging:
        print(f"Staging: {staging}")

    print()
    print("*** Dry-run only. No live files were modified.")
    print()
    print_fossilization_warning()

    return 0


def cmd_apply(args) -> int:
    """hermes prompts apply <profile>"""
    from hermes_agent.prompt_library.apply import apply_render
    from hermes_agent.prompt_library.errors import (
        BackupFailedError,
        CanopyCliError,
        CanopyMissingError,
        CrossTenantInheritError,
        FossilizationAcknowledgmentRequiredError,
        ManifestValidationError,
        TargetMissingError,
    )
    from hermes_agent.prompt_library.warnings import print_fossilization_warning

    library_root = _resolve_library_root(getattr(args, "library_root", None))
    profile = args.profile
    fossilization_ack = getattr(args, "fossilization_ack", False)

    try:
        result = apply_render(
            profile,
            library_root=library_root,
            acknowledged_fossilization=fossilization_ack,
        )
    except FossilizationAcknowledgmentRequiredError:
        print()
        print_fossilization_warning()
        print()
        print(
            "ERROR: Re-run with --i-understand-fossilization to proceed with live writes.",
            file=sys.stderr,
        )
        return 6
    except ManifestValidationError as exc:
        print("ERROR: Manifest validation failed:", file=sys.stderr)
        for err in exc.errors:
            print(f"  [{err['rule']}] {err['field']}: {err['message']}", file=sys.stderr)
        return 2
    except CanopyMissingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    except CanopyCliError as exc:
        print(f"ERROR: Canopy CLI error: {exc}", file=sys.stderr)
        return 4
    except CrossTenantInheritError as exc:
        print(f"ERROR: Cross-tenant inheritance violation: {exc}", file=sys.stderr)
        return 5
    except BackupFailedError as exc:
        print(f"ERROR: Backup failed: {exc}", file=sys.stderr)
        return 7
    except TargetMissingError as exc:
        print(f"ERROR: Target write failed: {exc}", file=sys.stderr)
        return 8
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "json_output", False):
        print(json.dumps(result, indent=2))
        return 0

    print(f"Profile:    {result['profile']}")
    print(f"Backup:     {result['backup_dir']}")
    print("Written:")
    for wt in result["written_targets"]:
        sha = wt.get("sha256", "?")[:12]
        print(f"  {wt.get('path', '?')}  sha256:{sha}")
    print(f"Receipt:    {result['receipt_path']}")
    print()
    print("*** APPLIED. Live files modified. ***")
    print()
    from hermes_agent.prompt_library.warnings import print_fossilization_warning
    print_fossilization_warning()

    return 0


def cmd_list(args) -> int:
    """hermes prompts list"""
    from hermes_agent.prompt_library.paths import profiles_dir, resolve_library_root

    library_root = resolve_library_root(None)
    profiles = profiles_dir(library_root)

    if not profiles.exists():
        print(f"No profiles directory found at {profiles}")
        return 0

    manifest_files = sorted(profiles.glob("*.yaml"))
    if not manifest_files:
        print("No profile manifests found.")
        return 0

    for mf in manifest_files:
        if mf.stem.startswith("_"):
            continue
        try:
            import yaml

            with open(mf, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            profile = data.get("profile", mf.stem)
            tenant = data.get("tenant", "?")
            render_prompt = (data.get("render") or {}).get("prompt", "?")
            targets = list((data.get("targets") or {}).keys())
            print(f"  {profile:20s}  tenant={tenant}  prompt={render_prompt}  targets={targets}")
        except Exception as exc:
            print(f"  {mf.stem:20s}  [error: {exc}]")

    return 0


def cmd_receipts(args) -> int:
    """hermes prompts receipts <profile>"""
    from hermes_agent.prompt_library.paths import receipts_dir, resolve_library_root

    library_root = resolve_library_root(None)
    profile = args.profile
    limit = getattr(args, "limit", 10)
    rdir = receipts_dir(library_root, profile)

    if not rdir.exists():
        print(f"No receipts found for profile '{profile}' at {rdir}")
        return 0

    receipt_files = sorted(rdir.glob("*.json"), reverse=True)[:limit]
    if not receipt_files:
        print(f"No receipts found for profile '{profile}'.")
        return 0

    for rf in receipt_files:
        try:
            with open(rf, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            dry_run = "(dry-run)" if data.get("dry_run") else "(applied)"
            rendered_at = data.get("rendered_at", "?")
            sections = len(data.get("sections", []))
            print(f"  {rf.name}  {dry_run}  {rendered_at}  sections={sections}")
        except Exception as exc:
            print(f"  {rf.name}  [error: {exc}]")

    return 0


def cmd_doctor(args) -> int:
    """hermes prompts doctor [--seed-step-1]"""
    import shutil

    from hermes_agent.prompt_library._version import CANOPY_CLI_PIN
    from hermes_agent.prompt_library.manifest import validate_manifest
    from hermes_agent.prompt_library.paths import (
        canopy_project_dir,
        profiles_dir,
        resolve_library_root,
    )

    library_root = resolve_library_root(None)
    ok = True

    # Check cn binary
    cn_bin = shutil.which("cn")
    if cn_bin:
        try:
            from hermes_agent.prompt_library.canopy import cn_version
            ver = cn_version()
            pin_ok = ver == CANOPY_CLI_PIN
            pin_msg = "(OK)" if pin_ok else f"(expected {CANOPY_CLI_PIN})"
            print(f"[OK] cn found at {cn_bin}, version {ver} {pin_msg}")
        except Exception as exc:
            print(f"[WARN] cn found but version check failed: {exc}")
    else:
        print(
            f"[FAIL] cn not found on PATH. "
            f"Install: bun add -g @os-eco/canopy-cli@{CANOPY_CLI_PIN}"
        )
        ok = False

    # Check library layout
    proj_dir = canopy_project_dir(library_root)
    if proj_dir.exists():
        prompts_jsonl = proj_dir / "prompts.jsonl"
        if prompts_jsonl.exists():
            print(f"[OK] .canopy/ exists with prompts.jsonl ({proj_dir})")
        else:
            print(f"[FAIL] .canopy/ exists but prompts.jsonl missing ({proj_dir})")
            ok = False
    else:
        print(f"[FAIL] .canopy/ directory missing at {proj_dir}")
        ok = False

    # Validate manifests
    pdir = profiles_dir(library_root)
    if pdir.exists():
        for mf in sorted(pdir.glob("*.yaml")):
            if mf.stem.startswith("_"):
                continue
            try:
                import yaml

                with open(mf, "r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
                validate_manifest(data)
                print(f"[OK] manifest {mf.name} validates")
            except Exception as exc:
                print(f"[FAIL] manifest {mf.name}: {exc}")
                ok = False
    else:
        print(f"[INFO] No profiles/ directory at {pdir}")

    # Seed step 1 if requested
    if getattr(args, "seed_step1", False):
        print()
        print("--- Seeding step-1 data ---")
        _seed_step1(library_root)

    if not ok:
        return 1
    return 0


def _seed_step1(library_root: Path) -> None:
    """Initialize ~/.hermes/prompt-library/ with step-1 seed data.

    Creates:
    - .canopy/config.yaml + prompts.jsonl (with gelby-default prompt)
    - profiles/gelby-default.yaml
    - profiles/_example.yaml
    - README.md
    - VERSION
    """
    import json
    from datetime import datetime, timezone

    from hermes_agent.prompt_library.paths import (
        canopy_project_dir,
        profiles_dir,
    )

    # Read live SOUL.md to extract the sections verbatim
    try:
        from hermes_constants import get_hermes_home  # type: ignore[import]
        soul_path = Path(get_hermes_home()) / "SOUL.md"
    except Exception:
        soul_path = Path.home() / ".hermes" / "SOUL.md"

    soul_content = ""
    if soul_path.exists():
        soul_content = soul_path.read_text(encoding="utf-8")

    # Parse sections from SOUL.md
    identity_body = ""
    mobile_digest_body = ""
    deep_work_body = ""
    lines = soul_content.splitlines(keepends=True)

    current_section = "identity"
    buf = []
    for line in lines:
        if line.startswith("# Telegram Reply Style"):
            identity_body = "".join(buf).strip()
            buf = [line]
            current_section = "mobile-digest"
        elif line.startswith("# Deep Work Exception"):
            mobile_digest_body = "".join(buf).strip()
            buf = [line]
            current_section = "deep-work-exception"
        else:
            buf.append(line)
    if current_section == "deep-work-exception":
        deep_work_body = "".join(buf).strip()
    elif current_section == "mobile-digest":
        mobile_digest_body = "".join(buf).strip()
    elif current_section == "identity":
        identity_body = "".join(buf).strip()

    # Ensure directories
    proj_dir = canopy_project_dir(library_root)
    proj_dir.mkdir(parents=True, exist_ok=True)
    profiles_dir(library_root).mkdir(parents=True, exist_ok=True)
    (library_root / "receipts").mkdir(parents=True, exist_ok=True)
    (library_root / "backups").mkdir(parents=True, exist_ok=True)
    (library_root / "staging").mkdir(parents=True, exist_ok=True)

    # .canopy/config.yaml
    canopy_config = proj_dir / "config.yaml"
    if not canopy_config.exists():
        canopy_config.write_text(
            "project: hermes-prompt-library\nversion: \"1\"\n", encoding="utf-8"
        )
        print(f"  Created {canopy_config}")
    else:
        print(f"  Skipped {canopy_config} (already exists)")

    # .canopy/.gitignore
    gitignore = proj_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*.lock\n", encoding="utf-8")

    # .canopy/schemas.jsonl (empty)
    schemas = proj_dir / "schemas.jsonl"
    if not schemas.exists():
        schemas.write_text("", encoding="utf-8")

    # .canopy/prompts.jsonl — seed gelby-default prompt record
    prompts_jsonl = proj_dir / "prompts.jsonl"
    now_iso = datetime.now(timezone.utc).isoformat()
    prompt_record = {
        "id": "hermes-prompt-library-gelby",
        "name": "gelby-default",
        "version": 1,
        "status": "active",
        "tags": ["tenant:greg", "target:soul", "profile:gelby-default"],
        "sections": [
            {"name": "identity", "body": identity_body, "required": True},
            {"name": "mobile-digest", "body": mobile_digest_body, "required": True},
            {"name": "deep-work-exception", "body": deep_work_body, "required": False},
        ],
        "createdAt": now_iso,
        "updatedAt": now_iso,
    }
    if not prompts_jsonl.exists():
        prompts_jsonl.write_text(
            json.dumps(prompt_record) + "\n", encoding="utf-8"
        )
        print(f"  Created {prompts_jsonl}")
    else:
        print(f"  Skipped {prompts_jsonl} (already exists — run cn update to modify)")

    # profiles/gelby-default.yaml
    gelby_manifest = profiles_dir(library_root) / "gelby-default.yaml"
    if not gelby_manifest.exists():
        gelby_manifest.write_text(
            "schema: hermes.prompt_profile.v1\n"
            "profile: gelby-default\n"
            "tenant: greg\n"
            "owner: greg\n"
            "render:\n"
            "  prompt: gelby-default\n"
            "targets:\n"
            "  soul:\n"
            "    path: ~/.hermes/SOUL.md\n"
            "    mode: replace\n"
            "notes: |\n"
            "  Step-1 profile: renders the global Hermes default SOUL.md (Nous boilerplate\n"
            "  + Telegram Reply Style + Deep Work Exception). dry-run-only by default.\n",
            encoding="utf-8",
        )
        print(f"  Created {gelby_manifest}")
    else:
        print(f"  Skipped {gelby_manifest} (already exists)")

    # profiles/_example.yaml
    example_manifest = profiles_dir(library_root) / "_example.yaml"
    if not example_manifest.exists():
        example_manifest.write_text(
            "# Example profile manifest — all fields documented.\n"
            "# Copy to <profile>.yaml and customize.\n"
            "\n"
            "schema: hermes.prompt_profile.v1\n"
            "profile: my-profile              # must match hermes profile list entry\n"
            "tenant: greg                     # tenant namespace (no cross-tenant inherit without allow-list)\n"
            "owner: greg                      # human owner (informational)\n"
            "\n"
            "render:\n"
            "  prompt: my-canopy-prompt       # cn render <prompt> --format json\n"
            "\n"
            "targets:\n"
            "  soul:\n"
            "    path: ~/.hermes/profiles/my-profile/SOUL.md\n"
            "    mode: replace\n"
            "  # config_addendum:             # optional\n"
            "  #   path: ~/.hermes/profiles/my-profile/config.yaml\n"
            "  #   key: system_prompt_addendum\n"
            "  #   mode: replace\n"
            "\n"
            "# Optional: route specific sections to specific targets\n"
            "# section_routes:\n"
            "#   - section: identity\n"
            "#     target: config_addendum\n"
            "#   - section: mobile-digest\n"
            "#     target: soul\n"
            "\n"
            "# Optional: params for {{ key }} substitution after cn render\n"
            "# params:\n"
            "#   profile_display_name: \"My Profile\"\n"
            "\n"
            "# Optional: allow-listed cross-tenant inheritance (R5 escape hatch)\n"
            "# cross_tenant_inherit_allow:\n"
            "#   - prompt: nous-shared/safety-block\n"
            "#     from_tenant: nous\n"
            "\n"
            "notes: |\n"
            "  Describe what this profile does.\n",
            encoding="utf-8",
        )
        print(f"  Created {example_manifest}")

    # README.md
    readme = library_root / "README.md"
    if not readme.exists():
        from hermes_agent.prompt_library._version import CANOPY_CLI_PIN
        from hermes_agent.prompt_library.warnings import FOSSILIZATION_WARNING
        readme.write_text(
            f"# Hermes Prompt Library\n\n"
            f"Canopy-backed composable prompt sections for Hermes profiles.\n\n"
            f"## Install Canopy CLI (pin: {CANOPY_CLI_PIN})\n\n"
            f"```\nbun add -g @os-eco/canopy-cli@{CANOPY_CLI_PIN}\n```\n\n"
            f"## Quickstart\n\n"
            f"```\nhermes prompts list\n"
            f"hermes prompts render gelby-default\n"
            f"# review ~/.hermes/prompt-library/staging/gelby-default/SOUL.md.rendered\n"
            f"hermes prompts apply gelby-default --i-understand-fossilization\n"
            f"```\n\n"
            f"## Directory layout\n\n"
            f"```\n"
            f"~/.hermes/prompt-library/\n"
            f"├── .canopy/          # Canopy project state (cn-managed)\n"
            f"├── profiles/         # Hermes profile manifests\n"
            f"├── receipts/         # Render receipts per profile\n"
            f"├── backups/          # Pre-overwrite backups\n"
            f"├── staging/          # Dry-run rendered files\n"
            f"├── README.md\n"
            f"└── VERSION\n"
            f"```\n\n"
            f"## Fossilization warning\n\n"
            f"{FOSSILIZATION_WARNING}\n\n"
            f"## Ops runbook\n\n"
            f"See: ~/.hermes/kanban/workspaces/ops/canopy-prompt-library-rollout.md\n",
            encoding="utf-8",
        )
        print(f"  Created {readme}")

    # VERSION
    version_file = library_root / "VERSION"
    if not version_file.exists():
        version_file.write_text("1\n", encoding="utf-8")
        print(f"  Created {version_file}")

    print("Step-1 seed complete.")


# ---------------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------------

def prompts_command(args) -> int:
    """Dispatch hermes prompts <subcommand>."""
    cmd = getattr(args, "prompts_cmd", None)
    if cmd == "render":
        return cmd_render(args)
    elif cmd == "apply":
        return cmd_apply(args)
    elif cmd == "list":
        return cmd_list(args)
    elif cmd == "receipts":
        return cmd_receipts(args)
    elif cmd == "doctor":
        return cmd_doctor(args)
    else:
        print("Usage: hermes prompts {render,apply,list,receipts,doctor} [options]")
        print("       hermes prompts --help")
        return 1
