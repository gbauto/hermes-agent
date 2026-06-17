# hermes_agent/prompt_library/apply.py
"""Apply logic: backup_target, patch_config_addendum, apply_render.

Atomicity contract: if any step after backup fails, raise; backup is intact
for operator restore. We do NOT auto-rollback -- operator inspects backup_dir
and restores manually (documented in ops runbook).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TypedDict

from hermes_agent.prompt_library._version import ADAPTER_VERSION, CANOPY_CLI_PIN
from hermes_agent.prompt_library.errors import (
    BackupFailedError,
    CanopyCliError,
    FossilizationAcknowledgmentRequiredError,
    TargetMissingError,
)
from hermes_agent.prompt_library.paths import backups_dir, resolve_library_root
from hermes_agent.prompt_library.warnings import FOSSILIZATION_WARNING

try:
    from ruamel.yaml import YAML
    from ruamel.yaml.scalarstring import LiteralScalarString
except ImportError:  # pragma: no cover — ruamel.yaml is a required dep
    YAML = None  # type: ignore[assignment,misc]
    LiteralScalarString = None  # type: ignore[assignment,misc]


class BackupManifest(TypedDict):
    profile: str
    timestamp: str
    targets: list[dict]
    canopy_cli_version: str
    adapter_version: str


class ApplyResult(TypedDict):
    profile: str
    backup_dir: str
    written_targets: list[dict]
    receipt_path: str
    fossilization_warning: str
    dry_run: bool


def _sha256_file(path: Path) -> str:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except Exception:
        return "unknown"


def _ts_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def atomic_write_text(path: Path, content: str) -> None:
    """Write text to path atomically (tmp file + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    dir_ = str(path.parent)
    fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def backup_target(
    profile: str,
    targets: dict,
    *,
    library_root: Optional[Path] = None,
) -> Path:
    """Snapshot each live target to backups/<profile>/<ts>/ before any writes.

    Behavior:
    - For soul target: copy live SOUL.md to backups/<profile>/<ts>/SOUL.md.
    - For config_addendum: copy the ENTIRE config.yaml (not just the key).
    - Write BACKUP_MANIFEST.json with sha256 of each backed-up file.
    - If target path does not exist, record "missing": True (not a failure).
    - Returns backup_dir path (absolute).

    Raises BackupFailedError if write fails (disk full, permission denied).
    """
    root = resolve_library_root(library_root)
    ts = _ts_now()
    backup_dir = backups_dir(root, profile) / ts

    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupFailedError(f"Cannot create backup directory {backup_dir}: {exc}") from exc

    snapshot_entries = []

    # Backup soul target
    soul_t = targets.get("soul")
    if isinstance(soul_t, dict):
        soul_path_raw = soul_t.get("path", "")
        soul_path = Path(str(soul_path_raw)).expanduser().resolve()
        entry: dict = {"kind": "soul", "path": str(soul_path)}
        if soul_path.exists():
            dest = backup_dir / "SOUL.md"
            try:
                shutil.copy2(str(soul_path), str(dest))
            except OSError as exc:
                raise BackupFailedError(
                    f"Cannot backup {soul_path}: {exc}"
                ) from exc
            entry["sha256"] = _sha256_file(dest)
        else:
            entry["missing"] = True
        snapshot_entries.append(entry)

    # Backup config_addendum target (whole file)
    cfg_t = targets.get("config_addendum")
    if isinstance(cfg_t, dict):
        cfg_path_raw = cfg_t.get("path", "")
        cfg_path = Path(str(cfg_path_raw)).expanduser().resolve()
        entry = {"kind": "config_addendum", "path": str(cfg_path)}
        if cfg_path.exists():
            dest = backup_dir / "config.yaml"
            try:
                shutil.copy2(str(cfg_path), str(dest))
            except OSError as exc:
                raise BackupFailedError(
                    f"Cannot backup {cfg_path}: {exc}"
                ) from exc
            entry["sha256"] = _sha256_file(dest)
        else:
            entry["missing"] = True
        snapshot_entries.append(entry)

    # Write BACKUP_MANIFEST.json
    try:
        from hermes_agent.prompt_library import canopy as cn_mod

        try:
            cli_version = cn_mod.cn_version()
        except Exception:
            cli_version = "unknown"
    except Exception:
        cli_version = "unknown"

    backup_manifest: BackupManifest = {
        "profile": profile,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "targets": snapshot_entries,
        "canopy_cli_version": cli_version,
        "adapter_version": ADAPTER_VERSION,
    }

    manifest_path = backup_dir / "BACKUP_MANIFEST.json"
    try:
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(backup_manifest, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    except OSError as exc:
        raise BackupFailedError(f"Cannot write BACKUP_MANIFEST.json: {exc}") from exc

    return backup_dir.resolve()


def patch_config_addendum(
    path: Path,
    key: str,
    value: str,
) -> None:
    """Set config.yaml[key] = value using ruamel.yaml ROUND-TRIP.

    MANDATORY:
    - Use ruamel.yaml.YAML(typ="rt"); NOT yaml.safe_load/safe_dump.
    - Set yaml.preserve_quotes = True.
    - Set yaml.width = 4096 (avoid auto-line-wrapping long scalars).
    - Use LiteralScalarString for multi-line values (block scalars: |).
    - Round-trip integrity check: load->dump->load equality on a copy
      BEFORE writing; if pre-edit round-trip is lossy, raise CanopyCliError.
    - Atomic write via atomic_write_text (tmp + os.replace).

    Raises:
      TargetMissingError if path does not exist.
      CanopyCliError if pre-edit round-trip is lossy.
    """
    from io import StringIO

    if not path.exists():
        raise TargetMissingError(f"config_addendum target does not exist: {path}")

    with open(path, "r", encoding="utf-8") as fh:
        original_text = fh.read()

    yaml_inst = YAML(typ="rt")
    yaml_inst.preserve_quotes = True
    yaml_inst.width = 4096

    # Pre-edit round-trip integrity check
    try:
        data1 = yaml_inst.load(original_text)
    except Exception as exc:
        raise CanopyCliError(
            f"Cannot parse {path} with ruamel.yaml: {exc}"
        ) from exc

    # Dump and reload to check for lossiness
    buf1 = StringIO()
    yaml_inst.dump(data1, buf1)
    round_tripped_text = buf1.getvalue()

    try:
        data2 = yaml_inst.load(round_tripped_text)
    except Exception as exc:
        raise CanopyCliError(
            f"Pre-edit round-trip reparse failed for {path}: {exc}"
        ) from exc

    # Compare: dump both representations and compare
    buf2 = StringIO()
    yaml_inst.dump(data2, buf2)

    if buf1.getvalue() != buf2.getvalue():
        raise CanopyCliError(
            f"Pre-edit round-trip for {path} is lossy — "
            f"this file uses YAML features that ruamel.yaml cannot round-trip. "
            f"Operator must hand-edit."
        )

    # Set the key using LiteralScalarString for multiline values
    if "\n" in value:
        data1[key] = LiteralScalarString(value)
    else:
        data1[key] = value

    # Serialize and write atomically
    out_buf = StringIO()
    yaml_inst.dump(data1, out_buf)
    new_text = out_buf.getvalue()

    atomic_write_text(path, new_text)


def apply_render(
    profile: str,
    *,
    library_root: Optional[Path] = None,
    acknowledged_fossilization: bool = False,
) -> ApplyResult:
    """Render + back up live targets + overwrite live targets + write receipt.

    Steps:
      1. if not acknowledged_fossilization: raise FossilizationAcknowledgmentRequiredError
      2. r = render_profile(profile, library_root)  # full validation first
      3. backup_dir = backup_target(profile, r["targets"])
      4. for each target in r["targets"]:
           - soul: atomic write of content -> path
           - config_addendum: patch_config_addendum(path, key, content)
      5. receipt_path = write_receipt(profile, r, backup_dir, dry_run=False)
      6. return ApplyResult

    Raises:
      FossilizationAcknowledgmentRequiredError, BackupFailedError,
      TargetMissingError, plus any from render_profile.
    """
    from hermes_agent.prompt_library.receipt import write_receipt
    from hermes_agent.prompt_library.render import render_profile

    if not acknowledged_fossilization:
        raise FossilizationAcknowledgmentRequiredError()

    root = resolve_library_root(library_root)

    # Full render + validation first (dry-run, no side effects)
    r = render_profile(profile, library_root=root)

    # Backup live targets
    b_dir = backup_target(profile, r["targets"], library_root=root)

    # Write live targets
    written_targets = []

    soul_t = r["targets"].get("soul")
    if isinstance(soul_t, dict):
        soul_path = Path(soul_t["path"]).expanduser().resolve()
        content = soul_t.get("content", "")
        atomic_write_text(soul_path, content)
        written_targets.append(
            {
                "path": str(soul_path),
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "mode": "replace",
            }
        )

    cfg_t = r["targets"].get("config_addendum")
    if isinstance(cfg_t, dict):
        cfg_path = Path(cfg_t["path"]).expanduser().resolve()
        cfg_key = cfg_t["key"]
        content = cfg_t.get("content", "")
        patch_config_addendum(cfg_path, cfg_key, content)
        written_targets.append(
            {
                "path": str(cfg_path),
                "key": cfg_key,
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "mode": "replace",
            }
        )

    # Write receipt
    receipt_p = write_receipt(profile, r, b_dir, dry_run=False, library_root=root)

    return ApplyResult(
        profile=profile,
        backup_dir=str(b_dir),
        written_targets=written_targets,
        receipt_path=str(receipt_p),
        fossilization_warning=FOSSILIZATION_WARNING,
        dry_run=False,
    )
