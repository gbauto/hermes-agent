#!/usr/bin/env python3
"""Nightly artifact diff for the GBAutomation Hermes Documents (/artifacts) gallery.

Re-scans the GBAutomation workspace, regenerates the static artifact index, and
reports which artifacts are NEW since the previous run so the :9119 /artifacts
page can surface them (and a `hermes cron --no-agent` job can deliver a digest).

Mechanism
---------
  1. Read the previous ``index.json`` -> set of artifact ids + generatedAt.
     (Done BEFORE the rebuild, because build_index() wipes the public tree.)
  2. Rebuild the index via build_gbauto_documents_index.build_index() ->
     fresh ``index.json`` + copied artifact files.
  3. ``added``   = new ids - old ids ; ``removed`` = old ids - new ids.
  4. Stamp ``index.json`` with ``recentlyAdded`` (ids added now PLUS ids whose
     mtime is inside the freshness window) so the UI can badge them, and append
     a record to ``diff-history.json``.
  5. Print a concise digest to stdout. Silent (no output) on a no-op night, so a
     ``--no-agent`` cron job stays quiet unless something actually changed.

Run it directly (uses the same default paths as the build script)::

    python scripts/nightly_artifact_diff.py
    python scripts/nightly_artifact_diff.py --render-previews --fresh-days 2
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
# What the running dashboard actually serves (web_server catch-all -> WEB_DIST/<path>).
# vite copies web/public -> web_dist only at build time (emptyOutDir wipes it), so a
# nightly run must mirror its output here for new artifacts to show without a rebuild.
DEFAULT_SERVED_ROOT = REPO_ROOT / "hermes_cli" / "web_dist" / "gbauto-documents"


def _load_builder():
    """Import the sibling build script by path (its name is import-safe, but
    loading by path means this works regardless of sys.path / cwd)."""
    builder_path = SCRIPT_DIR / "build_gbauto_documents_index.py"
    spec = importlib.util.spec_from_file_location("build_gbauto_documents_index", builder_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load builder module at {builder_path}")
    module = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass introspection reads
    # sys.modules[cls.__module__].__dict__ during class creation.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _read_index(index_path: Path) -> Optional[dict[str, Any]]:
    if not index_path.exists():
        return None
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _read_history_runs(history_path: Path) -> list[dict[str, Any]]:
    existing = _read_index(history_path)
    if isinstance(existing, dict) and isinstance(existing.get("runs"), list):
        return list(existing["runs"])
    return []


def _mirror_to_served(public_root: Path, served_root: Path, refresh_ids: list[str], removed: list[str]) -> Optional[int]:
    """Mirror the freshly-built index + new/changed artifact files into the dir
    the dashboard actually serves (web_dist), so additions show on refresh
    without an SPA rebuild. Returns the count of artifact dirs copied, or None if
    web_dist doesn't exist yet (SPA never built — nothing to serve from)."""
    if not served_root.parent.exists():
        return None
    served_root.mkdir(parents=True, exist_ok=True)
    for name in ("index.json", "diff-history.json"):
        src = public_root / name
        if src.exists():
            shutil.copy2(src, served_root / name)

    files_src = public_root / "files"
    files_dst = served_root / "files"
    files_dst.mkdir(parents=True, exist_ok=True)
    copied = 0
    for artifact_id in refresh_ids:
        src_dir = files_src / artifact_id
        if not src_dir.exists():
            continue
        dst_dir = files_dst / artifact_id
        if dst_dir.exists():
            shutil.rmtree(dst_dir, ignore_errors=True)
        shutil.copytree(src_dir, dst_dir)
        copied += 1
    for artifact_id in removed:
        dst_dir = files_dst / artifact_id
        if dst_dir.exists():
            shutil.rmtree(dst_dir, ignore_errors=True)
    return copied


def _write_history(history_path: Path, prior_runs: list[dict[str, Any]], record: dict[str, Any], keep: int = 60) -> None:
    runs = (prior_runs + [record])[-keep:]
    history_path.write_text(
        json.dumps({"updatedAt": record["generatedAt"], "runs": runs}, indent=2),
        encoding="utf-8",
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    builder = _load_builder()

    public_root: Path = args.public_root.resolve()
    index_path = public_root / "index.json"
    history_path = public_root / "diff-history.json"

    # 1. Snapshot the prior index AND history BEFORE the rebuild wipes the
    #    public tree (build_index removes public_root wholesale).
    prior = _read_index(index_path)
    prior_history = _read_history_runs(history_path)
    prior_ids: set[str] = set()
    prior_generated: Optional[str] = None
    if isinstance(prior, dict):
        prior_ids = {a["id"] for a in prior.get("artifacts", []) if "id" in a}
        prior_generated = prior.get("generatedAt")
    baseline = prior is None

    # 2. Rebuild (writes index.json + gbautoDocuments.ts + copies files).
    roots = args.scan_roots or builder.DEFAULT_SCAN_ROOTS
    payload = builder.build_index(
        args.gbauto_root.resolve(),
        args.site_public_root.resolve(),
        public_root,
        args.src_index.resolve(),
        roots,
        args.max_items,
        args.render_previews,
        args.preview_count,
    )

    artifacts = payload["artifacts"]
    new_ids = {a["id"] for a in artifacts}

    # 3. Diff. On a true first run there is no prior index, so treat it as a
    #    baseline rather than flagging every artifact as "new".
    if baseline:
        added: list[str] = []
        removed: list[str] = []
    else:
        added = sorted(new_ids - prior_ids)
        removed = sorted(prior_ids - new_ids)

    # 4. recentlyAdded = ids added this run + anything modified within the
    #    freshness window (so the badge survives the public-tree wipe and a
    #    missed night), minus anything no longer present.
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.fresh_days)
    recently: set[str] = set(added)
    for artifact in artifacts:
        modified = _parse_iso(artifact.get("modifiedAt", ""))
        if modified and modified >= cutoff:
            recently.add(artifact["id"])
    recently &= new_ids

    generated_at = payload["generatedAt"]

    # 5. Stamp the freshly-written index.json so the UI can highlight new items.
    index_data = _read_index(index_path) or payload
    index_data["recentlyAdded"] = sorted(recently)
    index_data["previousGeneratedAt"] = prior_generated
    index_data["diff"] = {"added": added, "removed": removed, "baseline": baseline}
    index_path.write_text(json.dumps(index_data, indent=2), encoding="utf-8")

    # History sidecar (bounded) for trend/auditing. Carries forward the runs
    # snapshotted before the wipe so it accumulates across nights.
    _write_history(
        history_path,
        prior_history,
        {
            "generatedAt": generated_at,
            "previousGeneratedAt": prior_generated,
            "total": len(artifacts),
            "added": added,
            "removed": removed,
            "baseline": baseline,
        },
    )

    # Mirror into the served web_dist dir so additions show without an SPA
    # rebuild. Refresh both newly-added and recently-modified artifact files.
    mirrored: Optional[int] = None
    if not args.no_mirror:
        served_root: Path = args.served_root.resolve()
        if served_root != public_root:
            refresh_ids = sorted(set(added) | recently)
            mirrored = _mirror_to_served(public_root, served_root, refresh_ids, removed)

    title_by_id = {a["id"]: a for a in artifacts}
    return {
        "generatedAt": generated_at,
        "previousGeneratedAt": prior_generated,
        "total": len(artifacts),
        "added": added,
        "removed": removed,
        "recentlyAdded": sorted(recently),
        "baseline": baseline,
        "mirroredFiles": mirrored,
        "titles": title_by_id,
    }


def _format_digest(result: dict[str, Any]) -> str:
    added = result["added"]
    removed = result["removed"]
    titles = result["titles"]

    # No-op night: stay silent so the cron job delivers nothing.
    if result["baseline"]:
        return f"GBAutomation artifacts — baseline index built ({result['total']} artifacts)."
    if not added and not removed:
        return ""

    since = result.get("previousGeneratedAt") or "the previous index"
    lines = [f"GBAutomation artifacts — {len(added)} new since {since} ({result['total']} total):"]
    for artifact_id in added[:25]:
        meta = titles.get(artifact_id, {})
        title = meta.get("title", artifact_id)
        doc_type = meta.get("docType", "?")
        group = meta.get("group", "?")
        source = meta.get("sourcePath", "")
        lines.append(f"  • {title} ({doc_type} · {group}) — {source}")
    if len(added) > 25:
        lines.append(f"  …and {len(added) - 25} more.")
    if removed:
        lines.append(f"Removed {len(removed)} artifact(s) no longer on disk.")
    lines.append("View: http://localhost:9119/artifacts")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gbauto-root", type=Path, default=REPO_ROOT.parent / "gbautomation")
    parser.add_argument(
        "--site-public-root", type=Path, default=REPO_ROOT.parent / "gb-automation-landing" / "public"
    )
    parser.add_argument(
        "--public-root", type=Path, default=REPO_ROOT / "web" / "public" / "gbauto-documents"
    )
    parser.add_argument(
        "--src-index", type=Path, default=REPO_ROOT / "web" / "src" / "generated" / "gbautoDocuments.ts"
    )
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--render-previews", action="store_true")
    parser.add_argument("--preview-count", type=int, default=32)
    parser.add_argument("--scan-root", action="append", dest="scan_roots")
    parser.add_argument(
        "--fresh-days", type=int, default=2, help="Artifacts modified within N days stay flagged 'new'."
    )
    parser.add_argument(
        "--served-root",
        type=Path,
        default=DEFAULT_SERVED_ROOT,
        help="Dir the dashboard serves; index + new files are mirrored here (default: hermes_cli/web_dist/gbauto-documents).",
    )
    parser.add_argument("--no-mirror", action="store_true", help="Skip mirroring into web_dist.")
    parser.add_argument("--json", action="store_true", help="Emit the machine-readable diff to stderr.")
    args = parser.parse_args()

    try:
        result = run(args)
    except Exception as exc:  # noqa: BLE001 - cron surface; report and fail cleanly
        print(f"nightly_artifact_diff: {exc}", file=sys.stderr)
        return 1

    if args.json:
        machine = {k: v for k, v in result.items() if k != "titles"}
        print(json.dumps(machine, indent=2), file=sys.stderr)

    digest = _format_digest(result)
    if digest:
        print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
