"""Closeout-owned artifact publication gate for Kanban terminal parents.

This module is intentionally generic and default-off.  It gives downstream
GBAutomation installations a deterministic, fail-closed handoff point without
baking Netlify/Supabase credentials or repo-specific implementation details into
Hermes core.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse


class CloseoutArtifactGateError(RuntimeError):
    """Raised when the closeout publication gate refuses terminal completion."""

    def __init__(self, reason: str, receipt: Optional[dict[str, Any]] = None):
        self.reason = reason
        self.receipt = receipt or {"ok": False, "reason": reason}
        super().__init__(reason)


def config_enabled(config: dict[str, Any]) -> bool:
    gate = (config.get("kanban") or {}).get("closeout_artifacts") or {}
    return bool(gate.get("enabled", False))


def load_config() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config as _load_config

        return _load_config() or {}
    except Exception:
        return {}


def gate_settings(config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    cfg = config if isinstance(config, dict) else load_config()
    return dict(((cfg.get("kanban") or {}).get("closeout_artifacts") or {}))


def _artifact_key(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("url", "public_url", "href", "path", "file", "file_path"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return f"{key}:{value.strip()}"
        return json.dumps(item, sort_keys=True, default=str)
    return str(item)


def _clean_artifacts(items: Any) -> list[Any]:
    if isinstance(items, (str, dict)):
        seq = [items]
    elif isinstance(items, (list, tuple)):
        seq = list(items)
    else:
        return []
    out: list[Any] = []
    seen: set[str] = set()
    for item in seq:
        if isinstance(item, str):
            item = item.strip()
            if not item:
                continue
        elif not isinstance(item, dict):
            continue
        key = _artifact_key(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def build_manifest(
    *,
    task: Any,
    metadata: Optional[dict[str, Any]],
    board: Optional[str] = None,
) -> dict[str, Any]:
    """Return a deterministic parent closeout artifact manifest.

    Volatile fields such as ``generated_at`` are added after the digest is
    computed so identical task/artifact inputs keep the same ``manifest_hash``
    across retries.
    """

    md = metadata if isinstance(metadata, dict) else {}
    artifacts = _clean_artifacts(md.get("artifacts"))
    producers = [str(x) for x in (md.get("producer_task_ids") or []) if x]
    descendants = [str(x) for x in (md.get("descendant_task_ids") or []) if x]
    body = {
        "schema": "hermes.kanban.closeout_artifact_manifest.v1",
        "task_id": str(getattr(task, "id", "") or ""),
        "board": board,
        "title": str(getattr(task, "title", "") or ""),
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "producer_task_ids": sorted(dict.fromkeys(producers)),
        "descendant_task_ids": sorted(dict.fromkeys(descendants)),
        "version": 1,
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    manifest = dict(body)
    manifest["manifest_hash"] = digest
    manifest["generated_at"] = int(time.time())
    return manifest


def write_manifest(manifest: dict[str, Any], *, task: Any) -> Path:
    workspace = getattr(task, "workspace_path", None)
    if workspace:
        root = Path(str(workspace)).expanduser()
    else:
        root = Path(os.environ.get("HERMES_KANBAN_WORKSPACE", ".")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "kanban-closeout-artifact-manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _valid_verified_html_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme == "https" and bool(parsed.netloc) and not value.strip().startswith("file:")


def _valid_html_content_type(value: Any) -> bool:
    if value is None:
        return True
    return isinstance(value, str) and "text/html" in value.lower()


def normalize_receipt(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        receipt = dict(raw)
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            receipt = dict(parsed) if isinstance(parsed, dict) else {"ok": False, "raw": raw}
        except json.JSONDecodeError:
            receipt = {"ok": False, "raw": raw}
    else:
        receipt = {"ok": False, "reason": "empty publisher receipt"}

    url = (
        receipt.get("verified_html_url")
        or receipt.get("active_html_url")
        or receipt.get("public_url")
        or receipt.get("url")
    )
    content_type = receipt.get("content_type") or receipt.get("mime")
    ok = bool(receipt.get("ok", True))
    if not ok:
        raise CloseoutArtifactGateError(str(receipt.get("reason") or "publisher returned ok=false"), receipt)
    if not _valid_verified_html_url(url):
        receipt["ok"] = False
        receipt.setdefault("reason", "publisher did not return a verified HTTPS HTML URL")
        raise CloseoutArtifactGateError(str(receipt["reason"]), receipt)
    if not _valid_html_content_type(content_type):
        receipt["ok"] = False
        receipt.setdefault("reason", f"publisher returned non-HTML content_type={content_type!r}")
        raise CloseoutArtifactGateError(str(receipt["reason"]), receipt)

    receipt["ok"] = True
    receipt["verified_html_url"] = str(url).strip()
    if content_type:
        receipt["content_type"] = str(content_type)
    return receipt


def should_gate(task: Any, metadata: Optional[dict[str, Any]], settings: dict[str, Any]) -> bool:
    if not settings.get("enabled", False):
        return False
    md = metadata if isinstance(metadata, dict) else {}
    # Gate only parent/closeout completions with dependency artifact evidence by
    # default.  Operators may force the gate for tests/backfills with
    # require_for_all_completions.
    if settings.get("require_for_all_completions", False):
        return True
    title = str(getattr(task, "title", "") or "").lower()
    has_rollup = bool(md.get("producer_task_ids") or md.get("descendant_task_ids"))
    looks_closeout = any(token in title for token in ("closeout", "parent", "bundle", "artifact"))
    return bool(has_rollup and looks_closeout)


def run_gate(
    *,
    task: Any,
    metadata: Optional[dict[str, Any]],
    board: Optional[str] = None,
    settings: Optional[dict[str, Any]] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Publish/verify closeout artifacts and return ``(metadata, receipt)``.

    The publisher is an external command configured at
    ``kanban.closeout_artifacts.publisher_command``.  It receives the manifest
    path as argv[1] plus HERMES_KANBAN_CLOSEOUT_MANIFEST in env, and must print a
    JSON receipt containing a verified HTTPS HTML URL.  No command means
    fail-closed when the gate is enabled.
    """

    opts = dict(settings or gate_settings())
    manifest = build_manifest(task=task, metadata=metadata, board=board)
    manifest_path = write_manifest(manifest, task=task)
    command = str(opts.get("publisher_command") or "").strip()
    if not command:
        receipt = {
            "ok": False,
            "reason": "closeout artifact gate enabled but publisher_command is empty",
            "manifest_path": str(manifest_path),
            "manifest_hash": manifest["manifest_hash"],
        }
        raise CloseoutArtifactGateError(receipt["reason"], receipt)

    timeout = int(opts.get("publisher_timeout_seconds") or 300)
    env = os.environ.copy()
    env["HERMES_KANBAN_CLOSEOUT_MANIFEST"] = str(manifest_path)
    env["HERMES_KANBAN_CLOSEOUT_TASK_ID"] = str(getattr(task, "id", "") or "")
    proc = subprocess.run(
        [command, str(manifest_path)],
        capture_output=True,
        text=True,
        timeout=max(1, timeout),
        env=env,
    )
    if proc.returncode != 0:
        receipt = {
            "ok": False,
            "reason": f"publisher exited {proc.returncode}",
            "stderr": (proc.stderr or "")[-2000:],
            "manifest_path": str(manifest_path),
            "manifest_hash": manifest["manifest_hash"],
        }
        raise CloseoutArtifactGateError(receipt["reason"], receipt)
    receipt = normalize_receipt(proc.stdout)
    receipt["manifest_path"] = str(manifest_path)
    receipt["manifest_hash"] = manifest["manifest_hash"]
    merged = dict(metadata) if isinstance(metadata, dict) else {}
    merged["closeout_artifact_manifest"] = str(manifest_path)
    merged["closeout_artifact_manifest_hash"] = manifest["manifest_hash"]
    merged["closeout_artifact_publication"] = receipt
    merged["verified_html_url"] = receipt["verified_html_url"]
    return merged, receipt
