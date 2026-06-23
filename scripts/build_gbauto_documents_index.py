#!/usr/bin/env python3
"""Build the Hermes Documents static artifact index from the gbautomation repo."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path


EXTENSIONS = {".html", ".pdf", ".png"}
DEFAULT_SCAN_ROOTS = ["."]
SKIP_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".pytest-tmp-youtube",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
}


class TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_title = False
        self._done = False
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title" and not self._done:
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title" and self._in_title:
            self._in_title = False
            self._done = True

    def handle_data(self, data: str) -> None:
        if self._in_title and not self._done:
            self.title_parts.append(data.strip())

    @property
    def title(self) -> str:
        return " ".join(part for part in self.title_parts if part).strip()


@dataclass(frozen=True)
class Artifact:
    id: str
    title: str
    description: str
    doc_type: str
    taxonomy: str
    group: str
    source_path: str
    public_path: str
    preview_path: str | None
    extension: str
    size_bytes: int
    source_file: str
    modified_at: str
    content_score: int
    formatting_score: int
    favorite: bool


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "artifact"


def title_from_html(path: Path) -> str | None:
    try:
      text = path.read_text(encoding="utf-8", errors="ignore")[:200_000]
    except OSError:
      return None
    parser = TitleParser()
    parser.feed(text)
    return parser.title or None


def pretty_title(path: Path) -> str:
    if path.suffix.lower() == ".html":
        html_title = title_from_html(path)
        if html_title:
            return normalize_title(html_title)
    stem = path.stem
    if stem.lower() == "index":
        stem = path.parent.name
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}-?", "", stem)
    stem = re.sub(r"[-_]+", " ", stem)
    return normalize_title(stem.strip().title() or path.name)


def normalize_title(value: str) -> str:
    clean = re.sub(r"\s+", " ", value).strip()
    if len(clean) <= 96:
        return clean
    return clean[:93].rstrip(" -:_") + "..."


def classify_doc_type(path: Path) -> str:
    parts = [part.lower() for part in path.parts]
    name = "/".join(parts)
    if "gb-automation-landing/public" in name or (parts and parts[0] == "page-views"):
        return "Page View"
    if path.suffix.lower() == ".pdf":
        return "PDF"
    if path.suffix.lower() == ".png":
        return "Visual"
    if "prd" in name:
        return "PRD"
    if "sprint" in name:
        return "Sprint"
    if "telemetry" in name:
        return "Telemetry"
    if "architecture" in name:
        return "Architecture"
    if "deployment" in name:
        return "Deployment"
    if "build-report" in name or "tac-build-report" in name:
        return "Build"
    if "index.html" in name:
        return "Index"
    return "HTML"


def classify_taxonomy(path: Path) -> str:
    parts = [part.lower() for part in path.parts]
    joined = "/".join(parts)
    if "gb-automation-landing/public" in joined or "page-views" in parts:
        return "Website"
    if "clients" in parts or "client" in joined or "jid5274" in joined or "fish" in joined or "sylvan" in joined:
        return "Client"
    if "telemetry" in joined or "observability" in joined:
        return "Telemetry"
    if "experiments" in parts or "hyperframe" in joined or "svg" in joined:
        return "Visual Review"
    if "plan-renders" in joined or "prds" in parts:
        return "Planning"
    if "sales" in joined or "marketing" in joined or "resume" in joined:
        return "Sales"
    return "Ops"


def group_name(path: Path) -> str:
    parts = list(path.parts)
    if "page-views" in parts:
        return "Hermes page views"
    if "gb-automation-landing" in parts:
        if "public" in parts:
            idx = parts.index("public")
            if len(parts) > idx + 1:
                return parts[idx + 1]
        return "gb-automation-landing"
    if "reports" in parts:
        idx = parts.index("reports")
        if len(parts) > idx + 2:
            return parts[idx + 2]
        if len(parts) > idx + 1:
            return parts[idx + 1]
    if "artifact-registry" in parts:
        return "Artifact registry"
    if "clients" in parts:
        idx = parts.index("clients")
        if len(parts) > idx + 1:
            return parts[idx + 1]
    if len(parts) > 1:
        return parts[-2]
    return "GBAutomation"


def description_for(path: Path, doc_type: str, taxonomy: str, size_bytes: int) -> str:
    size_kb = max(1, round(size_bytes / 1024))
    if doc_type == "Page View":
        return f"Website page HTML tracked as a frontend artifact. Source size {size_kb} KB."
    return f"{doc_type} artifact from {taxonomy.lower()} reports. Source size {size_kb} KB."


def score_from_path(path: Path, offset: int) -> int:
    digest = hashlib.sha1(str(path).encode("utf-8")).digest()
    return 7 + ((digest[offset] % 4))


def find_chrome() -> Path | None:
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def render_preview(chrome: Path, source: Path, target: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gbauto-preview-", ignore_cleanup_errors=True) as profile_dir:
        command = [
            str(chrome),
            "--headless",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-sync",
            "--hide-scrollbars",
            "--no-first-run",
            "--allow-file-access-from-files",
            f"--user-data-dir={profile_dir}",
            "--virtual-time-budget=3000",
            "--window-size=1440,1080",
            f"--screenshot={target}",
            source.resolve().as_uri(),
        ]
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            returncode = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                process.kill()
            return False
        except OSError:
            return False
    return returncode == 0 and target.exists()


def iter_artifact_paths(gbauto_root: Path, roots: list[str]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        base = gbauto_root / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
                continue
            rel_parts = set(path.relative_to(gbauto_root).parts)
            if rel_parts & SKIP_PARTS:
                continue
            found.append(path)
    return sorted(set(found), key=lambda item: str(item.relative_to(gbauto_root)).lower())


def iter_site_page_paths(site_public_root: Path) -> list[Path]:
    if not site_public_root.exists():
        return []
    found: list[Path] = []
    for path in site_public_root.rglob("*.html"):
        if not path.is_file():
            continue
        rel_parts = set(path.relative_to(site_public_root).parts)
        if rel_parts & SKIP_PARTS:
            continue
        found.append(path)
    return sorted(set(found), key=lambda item: str(item.relative_to(site_public_root)).lower())


def remove_generated_tree(path: Path) -> None:
    for attempt in range(4):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 3:
                raise
            time.sleep(1)


def add_artifact(artifacts: list[Artifact], path: Path, rel: Path, source_label: str, public_name: str | None = None) -> None:
    digest = hashlib.sha1(f"{source_label}:{rel}".encode("utf-8")).hexdigest()[:12]
    artifact_id = f"{slugify(path.stem)}-{digest}"
    stat = path.stat()
    doc_type = classify_doc_type(rel)
    taxonomy = classify_taxonomy(rel)
    public_filename = public_name or path.name
    artifacts.append(
        Artifact(
            id=artifact_id,
            title=pretty_title(path),
            description=description_for(rel, doc_type, taxonomy, stat.st_size),
            doc_type=doc_type,
            taxonomy=taxonomy,
            group=group_name(rel),
            source_path=str(rel).replace("\\", "/"),
            public_path=f"/gbauto-documents/files/{artifact_id}/{public_filename}",
            preview_path=None,
            extension=path.suffix.lower().lstrip("."),
            size_bytes=stat.st_size,
            source_file=str(path),
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            content_score=score_from_path(rel, 0),
            formatting_score=score_from_path(rel, 1),
            favorite=doc_type in {"Page View", "Sprint", "Build", "Telemetry"} and len(artifacts) < 18,
        )
    )


def write_virtual_page_view(page_views_root: Path, route: str, title: str, description: str) -> Path:
    page_views_root.mkdir(parents=True, exist_ok=True)
    filename = f"{slugify(route.strip('/') or 'home')}.html"
    target = page_views_root / filename
    target.write_text(
        "\n".join(
            [
                "<!doctype html>",
                '<html lang="en">',
                "<head>",
                '  <meta charset="utf-8" />',
                '  <meta name="viewport" content="width=device-width, initial-scale=1" />',
                f"  <title>{title}</title>",
                '  <style>body{margin:0;background:#f3f1e7;color:#191919;font-family:Inter,system-ui,sans-serif}.wrap{min-height:100vh;display:grid;place-items:center;padding:32px}.card{max-width:760px;border:1px solid #d6d4c8;border-radius:10px;background:rgba(255,255,255,.62);padding:28px;box-shadow:0 28px 90px -70px rgba(25,25,25,.8)}h1{font-family:Georgia,serif;font-style:italic;font-size:42px;line-height:1;margin:0 0 12px}.eyebrow{color:#d97757;font-size:11px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}.route{font-family:ui-monospace,Consolas,monospace;background:#e6e4d9;border-radius:999px;padding:6px 10px;display:inline-flex;margin-top:16px}</style>',
                "</head>",
                "<body>",
                '  <main class="wrap">',
                '    <section class="card">',
                '      <p class="eyebrow">Hermes page view</p>',
                f"      <h1>{title}</h1>",
                f"      <p>{description}</p>",
                f'      <a class="route" href="{route}">{route}</a>',
                "    </section>",
                "  </main>",
                "</body>",
                "</html>",
            ]
        ),
        encoding="utf-8",
    )
    return target


def build_index(
    gbauto_root: Path,
    site_public_root: Path,
    public_root: Path,
    src_index: Path,
    roots: list[str],
    max_items: int | None,
    render_previews: bool,
    preview_count: int,
) -> dict[str, object]:
    files_root = public_root / "files"
    if public_root.exists():
        remove_generated_tree(public_root)
    files_root.mkdir(parents=True, exist_ok=True)

    artifacts: list[Artifact] = []
    for path in iter_artifact_paths(gbauto_root, roots):
        rel = path.relative_to(gbauto_root)
        add_artifact(artifacts, path, rel, "gbautomation")

    if site_public_root.exists():
        site_root = site_public_root.parent
        for path in iter_site_page_paths(site_public_root):
            rel = Path("gb-automation-landing") / path.relative_to(site_root)
            add_artifact(artifacts, path, rel, "gb-automation-landing")

    page_views_root = public_root.parent / ".generated-page-views"
    for route, title, description in [
        ("/overview", "Operations Overview", "Native Hermes route converted from gbautomation.xyz/overview."),
        ("/repos", "Repos & Commits", "Native Hermes route converted from gbautomation.xyz/repos."),
    ]:
        path = write_virtual_page_view(page_views_root, route, title, description)
        rel = Path("page-views") / path.name
        add_artifact(artifacts, path, rel, "hermes-agent-page-view")

    artifacts.sort(
        key=lambda item: (
            item.doc_type != "Page View",
            item.taxonomy != "Client",
            item.doc_type not in {"Page View", "Sprint", "Build", "Telemetry", "Architecture", "PRD"},
            item.modified_at,
        )
    )
    if max_items:
        artifacts = artifacts[:max_items]

    chrome = find_chrome() if render_previews else None
    materialized: list[Artifact] = []
    for index, item in enumerate(artifacts):
        source = Path(item.source_file)
        target_dir = files_root / item.id
        target_dir.mkdir(parents=True, exist_ok=True)
        if source.suffix.lower() == ".html":
            shutil.copytree(
                source.parent,
                target_dir,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(*SKIP_PARTS),
            )
        else:
            shutil.copy2(source, target_dir / source.name)
        preview_path = item.public_path
        if source.suffix.lower() in {".html", ".pdf"} and chrome and index < preview_count:
            preview_target = target_dir / "preview.png"
            if render_preview(chrome, target_dir / source.name, preview_target):
                preview_path = f"/gbauto-documents/files/{item.id}/preview.png"
        materialized.append(
            Artifact(
                **{
                    **item.__dict__,
                    "preview_path": preview_path,
                }
            )
        )
    artifacts = materialized

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceRoot": str(gbauto_root),
        "count": len(artifacts),
        "artifacts": [
            {
                "id": item.id,
                "title": item.title,
                "description": item.description,
                "docType": item.doc_type,
                "taxonomy": item.taxonomy,
                "group": item.group,
                "sourcePath": item.source_path,
                "publicPath": item.public_path,
                "previewPath": item.preview_path,
                "extension": item.extension,
                "sizeBytes": item.size_bytes,
                "modifiedAt": item.modified_at,
                "contentScore": item.content_score,
                "formattingScore": item.formatting_score,
                "favorite": item.favorite,
            }
            for item in artifacts
        ],
    }
    (public_root / "index.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    src_index.parent.mkdir(parents=True, exist_ok=True)
    src_index.write_text(
        "/* Generated by scripts/build_gbauto_documents_index.py. Do not edit by hand. */\n"
        f"export const gbautoDocumentsGeneratedAt = {json.dumps(payload['generatedAt'])};\n"
        f"export const gbautoDocuments = {json.dumps(payload['artifacts'], indent=2)} as const;\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gbauto-root", type=Path, default=Path(__file__).resolve().parents[2] / "gbautomation")
    parser.add_argument("--site-public-root", type=Path, default=Path(__file__).resolve().parents[2] / "gb-automation-landing" / "public")
    parser.add_argument("--public-root", type=Path, default=Path(__file__).resolve().parents[1] / "web" / "public" / "gbauto-documents")
    parser.add_argument("--src-index", type=Path, default=Path(__file__).resolve().parents[1] / "web" / "src" / "generated" / "gbautoDocuments.ts")
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--render-previews", action="store_true")
    parser.add_argument("--preview-count", type=int, default=32)
    parser.add_argument("--scan-root", action="append", dest="scan_roots")
    args = parser.parse_args()

    roots = args.scan_roots or DEFAULT_SCAN_ROOTS
    payload = build_index(
        args.gbauto_root.resolve(),
        args.site_public_root.resolve(),
        args.public_root.resolve(),
        args.src_index.resolve(),
        roots,
        args.max_items,
        args.render_previews,
        args.preview_count,
    )
    print(f"Indexed {payload['count']} artifacts into {args.public_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
