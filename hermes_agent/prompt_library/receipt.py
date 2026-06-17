# hermes_agent/prompt_library/receipt.py
"""Receipt writer for the Canopy-backed prompt library adapter.

Every render/apply writes a JSON receipt to:
  receipts/<profile>/<ts>.json

Filename format: f"{ts}.json" where ts = strftime("%Y-%m-%dT%H-%M-%SZ")
(colons sanitized to dashes — Windows-safe + URL-safe).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from hermes_agent.prompt_library._version import ADAPTER_VERSION
from hermes_agent.prompt_library.paths import receipts_dir, resolve_library_root
from hermes_agent.prompt_library.warnings import FOSSILIZATION_WARNING

if TYPE_CHECKING:
    from hermes_agent.prompt_library.render import RenderResult


def _hermes_version() -> str:
    """Get hermes-agent package version."""
    try:
        import importlib.metadata

        return importlib.metadata.version("hermes-agent")
    except Exception:
        return "unknown"


def _manifest_sha256(manifest_path_str: str) -> str:
    """Compute sha256 of the manifest file."""
    try:
        with open(manifest_path_str, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except Exception:
        return "unknown"


def _ts_now() -> str:
    """Return ISO 8601 UTC timestamp with colons sanitized to dashes."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")


def atomic_json_write(path: Path, data: dict) -> None:
    """Write data as JSON to path atomically (tmp file + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    dir_ = str(path.parent)
    fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def write_receipt(
    profile: str,
    render_result: "RenderResult",
    backup_dir: Optional[Path] = None,
    *,
    dry_run: bool,
    library_root: Optional[Path] = None,
) -> Path:
    """Write the receipt JSON to receipts/<profile>/<ts>.json. Returns absolute path.

    Receipt schema: hermes.prompt_library.receipt.v1
    """
    root = resolve_library_root(library_root)
    ts = _ts_now()
    receipt_path = receipts_dir(root, profile) / f"{ts}.json"

    # Build target_paths summary
    targets_raw = render_result.get("targets", {})
    target_paths: dict = {}
    if "soul" in targets_raw:
        target_paths["soul"] = targets_raw["soul"].get("path")
    else:
        target_paths["soul"] = None

    if "config_addendum" in targets_raw:
        cfg = targets_raw["config_addendum"]
        target_paths["config_addendum"] = {
            "path": cfg.get("path"),
            "key": cfg.get("key"),
        }
    else:
        target_paths["config_addendum"] = None

    receipt = {
        "schema": "hermes.prompt_library.receipt.v1",
        "profile": profile,
        "tenant": render_result.get("tenant", ""),
        "sections": render_result.get("sections", []),
        "params_sha256": render_result.get("params_sha256", ""),
        "manifest_path": render_result.get("manifest_path", ""),
        "manifest_sha256": _manifest_sha256(render_result.get("manifest_path", "")),
        "target_paths": target_paths,
        "rendered_at": datetime.now(timezone.utc).isoformat(),
        "hermes_version": _hermes_version(),
        "adapter_version": ADAPTER_VERSION,
        "canopy_cli_version": render_result.get("canopy_cli_version", "unknown"),
        "resolved_from": render_result.get("resolved_from", []),
        "dry_run": dry_run,
        "fossilization_warning": FOSSILIZATION_WARNING,
        "backup_dir": str(backup_dir) if backup_dir else None,
    }

    atomic_json_write(receipt_path, receipt)
    return receipt_path.resolve()
