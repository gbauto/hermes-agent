"""Fail-closed Telegram operator artifact allowlist.

This module is intentionally platform-adapter friendly: callers pass local
files, URLs, or text snippets and receive audit-safe decisions without leaking
raw internal paths in public reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Literal
from urllib.parse import urljoin, urlparse
import os
import re

DeliveryKind = Literal["text", "netlify_html_url", "image", "pdf", "drop"]
CandidateSource = Literal[
    "text",
    "media_tag",
    "local_path",
    "url",
    "kanban_artifact",
    "cron_media",
    "send_message",
]

POLICY_NAME = "telegram_operator_artifact_allowlist.v1"
MAX_TELEGRAM_OPERATOR_FILE_BYTES = 50 * 1024 * 1024

_IMAGE_MAGIC = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"RIFF": "image/webp",
}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_DENIED_EXTENSIONS = {
    ".md", ".markdown", ".json", ".txt", ".csv", ".tsv", ".yaml", ".yml",
    ".xml", ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods",
    ".odp", ".key", ".sqlite", ".db", ".parquet", ".py", ".js", ".ts",
    ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".sh",
    ".bash", ".zsh", ".toml", ".ini", ".lock",
}
_DENIED_MIME_PREFIXES = ("text/",)
_DENIED_MIME_TYPES = {
    "application/json", "application/xml", "application/zip", "application/x-zip-compressed",
    "text/csv", "text/plain", "text/markdown", "application/x-yaml", "application/yaml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_LOCAL_PATH_RE = re.compile(
    r"(?<![/:\w.])(?:~/|/)(?:[^\s`'\"<>]+/)*[^\s`'\"<>]+\.(?:"
    r"png|jpe?g|gif|webp|pdf|md|markdown|json|txt|csv|tsv|ya?ml|xml|zip|rar|7z|tar|gz|tgz|bz2|xz|"
    r"docx?|xlsx?|pptx?|sqlite|db|parquet|py|js|ts|tsx|jsx|go|rs|java|c|cpp|h|sh|toml|ini|lock"
    r")\b",
    re.IGNORECASE,
)
_MEDIA_TAG_RE = re.compile(r"[`\"']?MEDIA:\s*(?P<path>`[^`\n]+`|\"[^\"\n]+\"|'[^'\n]+'|(?:~/|/)\S+)[`\"']?", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s`'\"<>]+", re.IGNORECASE)
_SOURCE_FENCE_RE = re.compile(
    r"```\s*(?:json|ya?ml|xml|csv|toml|ini|python|py|javascript|js|typescript|ts|tsx|jsx|bash|sh|zsh|sql)\b.*?```",
    re.IGNORECASE | re.DOTALL,
)
_ARTIFACT_URL_DENY_HOST_SUFFIXES = (
    "drive.google.com",
    "docs.google.com",
    "dropbox.com",
    "box.com",
)
_ARTIFACT_URL_EXTENSIONS = _DENIED_EXTENSIONS | {".html", ".htm", ".pdf"}


@dataclass(frozen=True)
class ArtifactCandidate:
    source: CandidateSource
    platform: str
    path: str | None = None
    url: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    content_disposition: str | None = None
    size_bytes: int | None = None
    force_document: bool = False
    context: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    delivery_kind: DeliveryKind
    sanitized_text: str | None = None
    safe_url: str | None = None
    safe_path: str | None = None
    reason_code: str = ""
    public_reason: str = ""


def _deny(reason_code: str, public_reason: str = "Artifact omitted: not allowed for Telegram operator delivery.") -> PolicyDecision:
    return PolicyDecision(False, "drop", reason_code=reason_code, public_reason=public_reason)


def _allow(kind: DeliveryKind, *, path: str | None = None, url: str | None = None) -> PolicyDecision:
    return PolicyDecision(True, kind, safe_path=path, safe_url=url, reason_code="allowed", public_reason="allowed")


def _content_type(headers: dict | None) -> str:
    for key, value in (headers or {}).items():
        if key.lower() == "content-type":
            return str(value).split(";", 1)[0].strip().lower()
    return ""


def _content_disposition(headers: dict | None) -> str:
    for key, value in (headers or {}).items():
        if key.lower() == "content-disposition":
            return str(value or "")
    return ""


def _filename_from_disposition(disposition: str) -> str:
    match = re.search(r"filename\*?=(?:UTF-8''|\"?)([^\";]+)", disposition or "", re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _is_netlify_host(hostname: str) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host.endswith(".netlify.app") or host.endswith(".netlify.com")


def _is_known_document_host(hostname: str) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _ARTIFACT_URL_DENY_HOST_SUFFIXES)


def _should_redact_artifact_url(url: str) -> bool:
    parsed = urlparse(url.rstrip(".,);]"))
    hostname = parsed.hostname or ""
    ext = Path(parsed.path).suffix.lower()
    if _is_netlify_host(hostname) and ext in {"", ".html", ".htm"}:
        return False
    if _is_known_document_host(hostname):
        return True
    return ext in _ARTIFACT_URL_EXTENSIONS


def _sniff_local_mime(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return ""
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    for magic, mime in _IMAGE_MAGIC.items():
        if head.startswith(magic):
            return mime
    # Do not fall back to extension-derived mimetypes for local Telegram
    # operator artifacts. A disguised archive named report.pdf would otherwise
    # become application/pdf by suffix alone and reach send_document.
    return ""


def classify_local_file(candidate: ArtifactCandidate) -> PolicyDecision:
    if (candidate.platform or "").lower() != "telegram":
        return _deny("wrong_platform")
    if not candidate.path:
        return _deny("missing_path")
    path = os.path.expanduser(candidate.path)
    if not os.path.isfile(path):
        return _deny("missing_file", "Artifact omitted: file unavailable.")
    try:
        size = int(candidate.size_bytes if candidate.size_bytes is not None else os.path.getsize(path))
    except OSError:
        return _deny("missing_file", "Artifact omitted: file unavailable.")
    if size > MAX_TELEGRAM_OPERATOR_FILE_BYTES:
        return _deny("file_too_large", "Artifact omitted: file exceeds Telegram operator limit.")

    ext = (Path(candidate.filename or path).suffix or Path(path).suffix).lower()
    if ext in _DENIED_EXTENSIONS:
        return _deny("denied_extension")
    sniffed_mime = (_sniff_local_mime(path) or "").split(";", 1)[0].strip().lower()
    provided_mime = (candidate.mime_type or "").split(";", 1)[0].strip().lower()
    mime = provided_mime or sniffed_mime
    if ext in _IMAGE_EXTENSIONS:
        if mime.startswith("image/"):
            return _allow("image", path=path)
        return _deny("denied_mime")
    if ext == ".pdf":
        if sniffed_mime == "application/pdf":
            return _allow("pdf", path=path)
        return _deny("denied_mime")
    return _deny("denied_extension" if ext else "extensionless_local_file")


def sanitize_telegram_text(text: str) -> tuple[str, list[PolicyDecision]]:
    decisions: list[PolicyDecision] = []
    cleaned = str(text or "")

    def _replace_media(match: re.Match) -> str:
        decisions.append(_deny("denied_local_path"))
        return "Artifact omitted: publish/verification failed."

    cleaned = _MEDIA_TAG_RE.sub(_replace_media, cleaned)

    def _replace_source_fence(match: re.Match) -> str:
        decisions.append(_deny("denied_source_dump"))
        return "Artifact omitted: publish/verification failed."

    cleaned = _SOURCE_FENCE_RE.sub(_replace_source_fence, cleaned)

    def _replace_url(match: re.Match) -> str:
        url = match.group(0)
        if not _should_redact_artifact_url(url):
            return url
        decisions.append(_deny("denied_artifact_url"))
        return "Artifact omitted: publish/verification failed."

    cleaned = _URL_RE.sub(_replace_url, cleaned)

    def _replace_path(match: re.Match) -> str:
        decisions.append(_deny("denied_local_path"))
        return "Artifact omitted: publish/verification failed."

    cleaned = _LOCAL_PATH_RE.sub(_replace_path, cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, decisions


def _fetch_response(http_fetch: Callable, url: str, method: str) -> dict:
    response = http_fetch(url, method=method)
    if isinstance(response, dict):
        return response
    return {
        "status": getattr(response, "status", getattr(response, "status_code", 0)),
        "headers": dict(getattr(response, "headers", {}) or {}),
        "url": getattr(response, "url", url),
    }


def verify_public_artifact_url(url: str, http_fetch: Callable, *, max_redirects: int = 5) -> PolicyDecision:
    current = str(url or "").strip()
    parsed = urlparse(current)
    if parsed.scheme.lower() != "https":
        return _deny("non_https_url")

    for _hop in range(max_redirects + 1):
        response = _fetch_response(http_fetch, current, "HEAD")
        status = int(response.get("status") or 0)
        headers = response.get("headers") or {}
        if status in {301, 302, 303, 307, 308}:
            location = next((v for k, v in headers.items() if k.lower() == "location"), "")
            if not location:
                return _deny("redirect_without_location")
            current = urljoin(current, str(location))
            if urlparse(current).scheme.lower() != "https":
                return _deny("non_https_url")
            continue
        break
    else:
        return _deny("too_many_redirects")

    final_url = str(response.get("url") or current)
    if final_url == url and current != url:
        final_url = current
    final = urlparse(final_url)
    mime = _content_type(headers)
    disposition = _content_disposition(headers)
    disp_name = _filename_from_disposition(disposition)
    disp_lower = disposition.lower()
    final_ext = Path(final.path).suffix.lower()

    if mime == "application/pdf":
        if disp_name and Path(disp_name).suffix.lower() != ".pdf":
            return _deny("denied_disposition")
        return _allow("pdf", url=final_url)

    if _is_netlify_host(final.hostname or ""):
        if mime in {"text/html", "application/xhtml+xml"}:
            if "attachment" in disp_lower and disp_name and Path(disp_name).suffix.lower() in _DENIED_EXTENSIONS:
                return _deny("denied_disposition")
            return _allow("netlify_html_url", url=final_url)
        return _deny("denied_mime")

    if mime in _DENIED_MIME_TYPES or mime.startswith(_DENIED_MIME_PREFIXES) or final_ext in _DENIED_EXTENSIONS:
        return _deny("denied_host")
    return _deny("denied_host")


def filter_telegram_delivery(candidates: Iterable[ArtifactCandidate], text: str = "") -> tuple[str, list[PolicyDecision]]:
    cleaned, decisions = sanitize_telegram_text(text)
    for candidate in candidates:
        if (candidate.platform or "").lower() != "telegram":
            continue
        if candidate.url:
            maybe_fetch = candidate.context.get("http_fetch") if isinstance(candidate.context, dict) else None
            http_fetch = maybe_fetch if callable(maybe_fetch) else (lambda *_a, **_kw: {"status": 0, "headers": {}, "url": candidate.url})
            decision = verify_public_artifact_url(candidate.url, http_fetch)
        elif candidate.path:
            decision = classify_local_file(candidate)
        else:
            decision = _deny("empty_candidate")
        decisions.append(decision)
    return cleaned, decisions


def omitted_notice(decisions: Iterable[PolicyDecision]) -> str:
    if any(not d.allowed for d in decisions):
        return "Artifact omitted: publish/verification failed."
    return ""
