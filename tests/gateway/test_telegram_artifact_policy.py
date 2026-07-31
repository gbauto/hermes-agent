import os
from pathlib import Path

from gateway.platforms.artifact_policy import (
    ArtifactCandidate,
    classify_local_file,
    filter_telegram_delivery,
    sanitize_telegram_text,
    verify_public_artifact_url,
)


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_sanitize_telegram_text_removes_source_paths_and_media_tags(tmp_path):
    md = _write(tmp_path / "receipt.md", b"# internal")
    text = f"Done. MEDIA:{md}\nOpen {md} for details. Keep this concise."

    cleaned, decisions = sanitize_telegram_text(text)

    assert "Keep this concise" in cleaned
    assert str(md) not in cleaned
    assert "MEDIA:" not in cleaned
    assert any(d.reason_code == "denied_local_path" for d in decisions)


def test_sanitize_telegram_text_removes_source_dumps():
    cleaned, decisions = sanitize_telegram_text('Result:\n```json\n{"raw": true}\n```\nDone')

    assert '{"raw"' not in cleaned
    assert "Done" in cleaned
    assert any(d.reason_code == "denied_source_dump" for d in decisions)


def test_sanitize_telegram_text_removes_disallowed_artifact_urls():
    text = (
        "Report https://example.com/internal/receipt.md "
        "Data https://drive.google.com/file/d/abc/view "
        "Public https://project.netlify.app/report"
    )

    cleaned, decisions = sanitize_telegram_text(text)

    assert "receipt.md" not in cleaned
    assert "drive.google.com" not in cleaned
    assert "https://project.netlify.app/report" in cleaned
    assert any(d.reason_code == "denied_artifact_url" for d in decisions)


def test_classify_local_file_allows_images_and_pdf(tmp_path):
    image = _write(tmp_path / "chart.png", b"\x89PNG\r\n\x1a\nbytes")
    pdf = _write(tmp_path / "report.pdf", b"%PDF-1.7\nbytes")

    img_decision = classify_local_file(ArtifactCandidate(source="media_tag", platform="telegram", path=str(image)))
    pdf_decision = classify_local_file(ArtifactCandidate(source="media_tag", platform="telegram", path=str(pdf)))

    assert img_decision.allowed
    assert img_decision.delivery_kind == "image"
    assert pdf_decision.allowed
    assert pdf_decision.delivery_kind == "pdf"


def test_classify_local_file_denies_machine_artifacts_and_oversized_files(tmp_path):
    archive = _write(tmp_path / "bundle.zip", b"PK\x03\x04")
    huge_pdf = _write(tmp_path / "huge.pdf", b"%PDF-1.7\n")

    archive_decision = classify_local_file(ArtifactCandidate(source="media_tag", platform="telegram", path=str(archive)))
    huge_decision = classify_local_file(
        ArtifactCandidate(source="media_tag", platform="telegram", path=str(huge_pdf), size_bytes=51 * 1024 * 1024)
    )

    assert not archive_decision.allowed
    assert archive_decision.delivery_kind == "drop"
    assert archive_decision.reason_code == "denied_extension"
    assert not huge_decision.allowed
    assert huge_decision.reason_code == "file_too_large"


def test_filter_telegram_delivery_drops_local_source_but_allows_inline_image(tmp_path):
    image = _write(tmp_path / "ok.jpg", b"\xff\xd8\xff\xe0fake")
    data = _write(tmp_path / "data.json", b'{"secret": true}')

    text, decisions = filter_telegram_delivery(
        [
            ArtifactCandidate(source="media_tag", platform="telegram", path=str(image)),
            ArtifactCandidate(source="media_tag", platform="telegram", path=str(data)),
        ],
        text=f"Result paths: {data}",
    )

    assert str(data) not in text
    assert [d.delivery_kind for d in decisions if d.allowed] == ["image"]
    assert any((not d.allowed and d.reason_code == "denied_extension") for d in decisions)


def test_verify_public_artifact_url_allows_netlify_html_after_redirect():
    def fake_fetch(url, method="HEAD", **_kwargs):
        if url == "https://example.netlify.app/start":
            return {"status": 302, "headers": {"Location": "https://example.netlify.app/report"}, "url": url}
        return {"status": 200, "headers": {"Content-Type": "text/html; charset=utf-8"}, "url": url}

    decision = verify_public_artifact_url("https://example.netlify.app/start", fake_fetch)

    assert decision.allowed
    assert decision.delivery_kind == "netlify_html_url"
    assert decision.safe_url == "https://example.netlify.app/report"


def test_verify_public_artifact_url_denies_wrong_mime_extensionless_and_bad_redirect():
    def wrong_mime(url, method="HEAD", **_kwargs):
        return {"status": 200, "headers": {"Content-Type": "text/csv"}, "url": url}

    def bad_redirect(url, method="HEAD", **_kwargs):
        if "netlify" in url:
            return {"status": 302, "headers": {"Location": "https://files.example.com/report.html"}, "url": url}
        return {"status": 200, "headers": {"Content-Type": "text/html"}, "url": url}

    wrong = verify_public_artifact_url("https://site.netlify.app/download", wrong_mime)
    redirected = verify_public_artifact_url("https://site.netlify.app/report", bad_redirect)

    assert not wrong.allowed
    assert wrong.reason_code == "denied_mime"
    assert not redirected.allowed
    assert redirected.reason_code == "denied_host"


def test_verify_public_artifact_url_denies_disguised_pdf_disposition():
    def disguised(url, method="HEAD", **_kwargs):
        return {
            "status": 200,
            "headers": {
                "Content-Type": "application/pdf",
                "Content-Disposition": "attachment; filename=payload.zip",
            },
            "url": url,
        }

    decision = verify_public_artifact_url("https://files.example.com/report", disguised)

    assert not decision.allowed
    assert decision.reason_code == "denied_disposition"
