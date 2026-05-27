import builtins
import io
import importlib
import sys
from email.message import EmailMessage

import pytest

import open_webui.skiprag as skiprag_pkg
from open_webui.skiprag import email as email_module
from open_webui.skiprag.email import eml_to_markdown

try:
    from PIL import Image

    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


def _eml_with_attachments(attachments):
    msg = EmailMessage()
    msg["From"] = "Alice <alice@example.com>"
    msg["To"] = "Bob <bob@example.com>"
    msg["Subject"] = "Attachment test"
    msg.set_content("Plain body.")

    for filename, payload, maintype, subtype in attachments:
        msg.add_attachment(
            payload,
            maintype=maintype,
            subtype=subtype,
            filename=filename,
        )

    return msg.as_bytes()


def _fake_converter(file_bytes=None, filename="", request=None):
    return f"[Converted: {filename}]"


@pytest.mark.skipif(
    not _PIL_AVAILABLE,
    reason="PIL not installed",
)
def test_first_gif_frame_as_png_returns_valid_png():
    gif_buf = io.BytesIO()
    img = Image.new("RGB", (1, 1), (255, 0, 0))
    img.save(gif_buf, format="GIF")

    png_bytes = email_module._first_gif_frame_as_png(gif_buf.getvalue())

    assert png_bytes[:4] == b"\x89PNG"


def test_eml_to_markdown_extracts_metadata_and_plain_text_body():
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Cc: Carol <carol@example.com>\r\n"
        b"Subject: Re: Invoice question\r\n"
        b"Date: Tue, 26 May 2026 10:30:00 +0800\r\n"
        b"Message-ID: <abc@example.com>\r\n"
        b"In-Reply-To: <prev@example.com>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Hi Bob,\r\nPlease see the invoice attached.\r\n"
    )

    md = eml_to_markdown(raw, filename="sample.eml")

    assert "# Email: Re: Invoice question" in md
    assert "- From: Alice <alice@example.com>" in md
    assert "- To: Bob <bob@example.com>" in md
    assert "- Cc: Carol <carol@example.com>" in md
    assert "- Date: Tue, 26 May 2026 10:30:00 +0800" in md
    assert "- Message-ID: <abc@example.com>" in md
    assert "- In-Reply-To: <prev@example.com>" in md
    assert "## Body" in md
    assert "Hi Bob," in md
    assert "Please see the invoice attached." in md


def test_eml_to_markdown_sanitizes_header_control_characters():
    raw = (
        b"From: Mallory <mallory@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: Safe\x01 subject with control\x02 chars\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Hello.\r\n"
    )

    md = eml_to_markdown(raw, filename="control.eml")

    assert "\x01" not in md
    assert "\x02" not in md
    assert "# Email: Safe subject with control chars" in md


def test_eml_to_markdown_strips_bidi_chars_from_subject():
    raw = (
        b"From: Mallory <mallory@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: Quarterly\xe2\x80\xaeexe.\xe2\x80\xacpdf\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Hello.\r\n"
    )

    md = eml_to_markdown(raw, filename="subject-bidi.eml")

    assert "\u202e" not in md
    assert "\u202c" not in md
    assert "# Email: Quarterlyexe.pdf" in md


def test_eml_to_markdown_strips_bidi_chars_from_body():
    raw = (
        b"From: Mallory <mallory@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: Body Bidi\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Open report\xe2\x80\xaeexe.\xe2\x80\xacpdf now.\r\n"
    )

    md = eml_to_markdown(raw, filename="body-bidi.eml")

    assert "\u202e" not in md
    assert "\u202c" not in md
    assert "Open reportexe.pdf now." in md


def test_eml_to_markdown_malformed_input_returns_non_empty_warning(monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("parser exploded")

    monkeypatch.setattr("open_webui.skiprag.email.BytesParser", boom)

    md = eml_to_markdown(b"not really an email", filename="broken.eml")

    assert md
    assert "# Email: broken.eml" in md
    assert "## Body" in md
    assert "[No body extracted]" in md
    assert "## Parse Warnings" in md
    assert "parser exploded" in md


def test_eml_to_markdown_converts_html_body_when_plain_text_missing():
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: HTML only\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<html><body><h1>Hello</h1><p>HTML body</p><script>bad()</script></body></html>"
    )

    md = eml_to_markdown(raw, filename="html.eml")

    assert "Hello" in md
    assert "HTML body" in md
    assert "bad()" not in md
    assert "<script>" not in md
    assert "HTML body converted to text because text/plain was missing." in md


def test_eml_to_markdown_prefers_plain_text_over_html_alternative():
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: Alternative\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/alternative; boundary=ALT\r\n"
        b"\r\n"
        b"--ALT\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Plain body wins.\r\n"
        b"--ALT\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<p>HTML body should not appear.</p>\r\n"
        b"--ALT--\r\n"
    )

    md = eml_to_markdown(raw, filename="alternative.eml")

    assert "Plain body wins." in md
    assert "HTML body should not appear." not in md
    assert "HTML body converted to text because text/plain was missing." not in md


def test_eml_to_markdown_lists_attachment_metadata_only():
    attachment = b"%PDF-1.4\nfake pdf bytes\n%%EOF\n"
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: Attachment\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/mixed; boundary=MIXED\r\n"
        b"\r\n"
        b"--MIXED\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Plain body.\r\n"
        b"--MIXED\r\n"
        b"Content-Type: application/pdf\r\n"
        b"Content-Disposition: attachment; filename=\"invoice.pdf\"\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"JVBERi0xLjQKZmFrZSBwZGYgYnl0ZXMKJSVFT0YK\r\n"
        b"--MIXED--\r\n"
    )

    md = eml_to_markdown(raw, filename="attachment.eml")

    assert "## Attachments" in md
    assert "### Attachment 1: invoice.pdf" in md
    assert "- Content-Type: application/pdf" in md
    assert f"- Size: {len(attachment)} bytes" in md
    assert "- Disposition: attachment" in md
    assert "- Status: extraction-failed" in md
    assert "- Extractor: skip-rag-convert" in md
    assert "attachment converter unavailable" in md


@pytest.mark.parametrize(
    ("filename", "unsafe_fragment"),
    [
        ("../../evil.pdf", "../../evil.pdf"),
        ("..\\\\..\\\\evil.pdf", "..\\..\\evil.pdf"),
    ],
)
def test_eml_to_markdown_sanitizes_attachment_path_filename(filename, unsafe_fragment):
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: Unsafe filename\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/mixed; boundary=MIXED\r\n"
        b"\r\n"
        b"--MIXED\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Plain body.\r\n"
        b"--MIXED\r\n"
        b"Content-Type: application/pdf\r\n"
        + f"Content-Disposition: attachment; filename=\"{filename}\"\r\n".encode()
        + b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"ZmlsZQ==\r\n"
        b"--MIXED--\r\n"
    )

    md = eml_to_markdown(raw, filename="unsafe.eml")

    assert "### Attachment 1: evil.pdf" in md
    assert "../evil.pdf" not in md
    assert unsafe_fragment not in md


def test_eml_to_markdown_strips_bidi_override_chars():
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: Bidi test\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/mixed; boundary=MIXED\r\n"
        b"\r\n"
        b"--MIXED\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Body.\r\n"
        b"--MIXED\r\n"
        b"Content-Type: application/pdf\r\n"
        b"Content-Disposition: attachment; filename=\"safe\xe2\x80\xaexe.\xe2\x80\xacpdf\"\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"ZmlsZQ==\r\n"
        b"--MIXED--\r\n"
    )

    md = eml_to_markdown(raw, filename="bidi.eml")

    assert "\u202e" not in md
    assert "\u202c" not in md
    assert "### Attachment 1: safexe.pdf" in md


def test_eml_to_markdown_sanitizes_attachment_markdown_link_filename():
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: Link filename\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/mixed; boundary=MIXED\r\n"
        b"\r\n"
        b"--MIXED\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Plain body.\r\n"
        b"--MIXED\r\n"
        b"Content-Type: application/pdf\r\n"
        b"Content-Disposition: attachment;\r\n"
        b" filename*=utf-8''%5Bevil%5D%28http%3A%2F%2Fbad.com%29.pdf\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"ZmlsZQ==\r\n"
        b"--MIXED--\r\n"
    )

    md = eml_to_markdown(raw, filename="link-filename.eml")
    attachment_heading = next(line for line in md.splitlines() if line.startswith("### Attachment 1:"))

    assert "[evil](http://bad.com).pdf" not in md
    assert "[" not in attachment_heading
    assert "](" not in attachment_heading
    assert ")" not in attachment_heading
    assert "evil" in attachment_heading
    assert "bad.com" in attachment_heading
    assert ".pdf" in attachment_heading


def test_eml_to_markdown_sanitizes_attachment_autolink_filename():
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: Autolink filename\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/mixed; boundary=MIXED\r\n"
        b"\r\n"
        b"--MIXED\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Plain body.\r\n"
        b"--MIXED\r\n"
        b"Content-Type: application/pdf\r\n"
        b"Content-Disposition: attachment;\r\n"
        b" filename*=utf-8''%3Chttp%3A%2F%2Fevil.example%2Fevil.pdf%3E\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"ZmlsZQ==\r\n"
        b"--MIXED--\r\n"
    )

    md = eml_to_markdown(raw, filename="autolink-filename.eml")
    attachment_heading = next(line for line in md.splitlines() if line.startswith("### Attachment 1:"))

    assert "<http://evil.example/evil.pdf>" not in md
    assert "<" not in attachment_heading
    assert ">" not in attachment_heading
    assert "evil.example" in attachment_heading
    assert "evil.pdf" in attachment_heading
    safe_name = email_module._safe_attachment_filename(
        "<http://evil.example/evil.pdf>",
        1,
    )
    assert "<" not in safe_name
    assert ">" not in safe_name


def test_eml_to_markdown_indexes_multiple_attachments():
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: Multiple attachments\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/mixed; boundary=MIXED\r\n"
        b"\r\n"
        b"--MIXED\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Plain body.\r\n"
        b"--MIXED\r\n"
        b"Content-Type: application/pdf\r\n"
        b"Content-Disposition: attachment; filename=\"one.pdf\"\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"b25l\r\n"
        b"--MIXED\r\n"
        b"Content-Type: text/csv\r\n"
        b"Content-Disposition: attachment; filename=\"two.csv\"\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"dHdv\r\n"
        b"--MIXED--\r\n"
    )

    md = eml_to_markdown(raw, filename="multiple-attachments.eml")

    assert "### Attachment 1: one.pdf" in md
    assert "### Attachment 2: two.csv" in md
    assert md.index("### Attachment 1: one.pdf") < md.index("### Attachment 2: two.csv")


def test_eml_to_markdown_uses_fallback_name_for_attachment_without_filename():
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: Nameless attachment\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/mixed; boundary=MIXED\r\n"
        b"\r\n"
        b"--MIXED\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Plain body.\r\n"
        b"--MIXED\r\n"
        b"Content-Type: application/octet-stream\r\n"
        b"Content-Disposition: attachment\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"ZmlsZQ==\r\n"
        b"--MIXED--\r\n"
    )

    md = eml_to_markdown(raw, filename="nameless-attachment.eml")

    assert "### Attachment 1: attachment-1.bin" in md


@pytest.mark.parametrize(
    ("filename", "maintype", "subtype"),
    [
        ("invoice.pdf", "application", "pdf"),
        (
            "proposal.docx",
            "application",
            "vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("notes.txt", "text", "plain"),
        ("readme.md", "text", "markdown"),
    ],
)
def test_eml_to_markdown_extracts_convertible_attachments(filename, maintype, subtype):
    raw = _eml_with_attachments(
        [
            (filename, b"attachment bytes", maintype, subtype),
        ]
    )

    md = eml_to_markdown(
        raw,
        filename="convertible.eml",
        attachment_converter=_fake_converter,
    )

    assert f"### Attachment 1: {filename}" in md
    assert "- Status: extracted" in md
    assert "- Extractor: skip-rag-convert" in md
    assert "#### Extracted Content" in md
    assert f"[Converted: {filename}]" in md


@pytest.mark.parametrize(
    ("filename", "subtype"),
    [
        ("screenshot.png", "png"),
        ("photo.jpg", "jpeg"),
        ("animation.gif", "gif"),
    ],
)
def test_eml_to_markdown_extracts_image_attachments(monkeypatch, filename, subtype):
    calls = []

    def fake_image_extractor(file_bytes, safe_filename):
        calls.append((file_bytes, safe_filename))
        return True, "Image description here"

    monkeypatch.setattr(
        email_module,
        "_extract_image_via_task_model",
        fake_image_extractor,
    )
    raw = _eml_with_attachments(
        [
            (filename, b"image bytes", "image", subtype),
        ]
    )

    md = eml_to_markdown(raw, filename="image.eml")

    assert f"### Attachment 1: {filename}" in md
    assert "- Status: extracted" in md
    assert "- Extractor: task-model-vision" in md
    assert "#### Image OCR / Description" in md
    assert "Image description here" in md
    assert calls == [(b"image bytes", filename)]


@pytest.mark.parametrize("filename", ["malware.exe", "archive.zip", "weird.xyz"])
def test_eml_to_markdown_marks_unsupported_attachments(filename):
    raw = _eml_with_attachments(
        [
            (filename, b"unsupported bytes", "application", "octet-stream"),
        ]
    )

    md = eml_to_markdown(
        raw,
        filename="unsupported.eml",
        attachment_converter=_fake_converter,
    )

    assert f"### Attachment 1: {filename}" in md
    assert "- Status: unsupported-type" in md
    assert "#### Extracted Content" not in md
    assert "[Converted:" not in md


def test_eml_to_markdown_skips_oversized_attachment(monkeypatch):
    monkeypatch.setenv("SKIP_RAG_EMAIL_MAX_ATTACHMENT_BYTES", "100")
    raw = _eml_with_attachments(
        [
            ("large.txt", b"x" * 101, "text", "plain"),
        ]
    )

    def fail_if_called(**kwargs):
        raise AssertionError("oversized attachment should not be converted")

    md = eml_to_markdown(
        raw,
        filename="oversized.eml",
        attachment_converter=fail_if_called,
    )

    assert "### Attachment 1: large.txt" in md
    assert "- Size: 101 bytes" in md
    assert "- Status: skipped-oversized" in md
    assert "exceeds 100 byte limit" in md


def test_eml_to_markdown_stops_at_max_attachment_count(monkeypatch):
    monkeypatch.setenv("SKIP_RAG_EMAIL_MAX_ATTACHMENTS", "2")
    raw = _eml_with_attachments(
        [
            ("one.txt", b"one", "text", "plain"),
            ("two.txt", b"two", "text", "plain"),
            ("three.txt", b"three", "text", "plain"),
            ("four.txt", b"four", "text", "plain"),
        ]
    )

    md = eml_to_markdown(
        raw,
        filename="max-count.eml",
        attachment_converter=_fake_converter,
    )

    assert "### Attachment 1: one.txt" in md
    assert "### Attachment 2: two.txt" in md
    assert "### Attachment 3: three.txt" not in md
    assert "### Attachment 4: four.txt" not in md
    assert "Skipped 2 remaining attachments: exceeded limit." in md


def test_eml_to_markdown_truncates_attachment_content(monkeypatch):
    monkeypatch.setenv("SKIP_RAG_EMAIL_MAX_ATTACHMENT_CHARS", "50")
    raw = _eml_with_attachments(
        [
            ("long.txt", b"long", "text", "plain"),
        ]
    )

    def long_converter(**kwargs):
        return "x" * 80

    md = eml_to_markdown(
        raw,
        filename="truncate.eml",
        attachment_converter=long_converter,
    )

    assert "- Status: extracted" in md
    assert ("x" * 50) in md
    assert ("x" * 51) not in md
    assert "attachment content exceeded 50 characters" in md


def test_eml_to_markdown_respects_total_attachment_character_limit(monkeypatch):
    monkeypatch.setenv("SKIP_RAG_EMAIL_MAX_TOTAL_ATTACHMENT_CHARS", "100")
    raw = _eml_with_attachments(
        [
            ("one.txt", b"one", "text", "plain"),
            ("two.txt", b"two", "text", "plain"),
            ("three.txt", b"three", "text", "plain"),
        ]
    )

    def eighty_chars_converter(file_bytes=None, filename="", request=None):
        return filename[:1] * 80

    md = eml_to_markdown(
        raw,
        filename="total-chars.eml",
        attachment_converter=eighty_chars_converter,
    )

    assert "### Attachment 1: one.txt" in md
    assert "### Attachment 2: two.txt" in md
    assert "### Attachment 3: three.txt" not in md
    assert ("o" * 80) in md
    assert ("t" * 20) in md
    assert ("t" * 21) not in md
    assert "total email attachment extraction limit exceeded 20 remaining characters" in md
    assert "Skipped 1 remaining attachments: exceeded limit." in md


def test_eml_to_markdown_respects_total_attachment_bytes_limit(monkeypatch):
    monkeypatch.setenv("SKIP_RAG_EMAIL_MAX_TOTAL_ATTACHMENT_BYTES", "200")
    raw = _eml_with_attachments(
        [
            ("one.txt", b"1" * 80, "text", "plain"),
            ("two.txt", b"2" * 80, "text", "plain"),
            ("three.txt", b"3" * 80, "text", "plain"),
        ]
    )

    md = eml_to_markdown(
        raw,
        filename="total-bytes.eml",
        attachment_converter=_fake_converter,
    )

    assert "### Attachment 1: one.txt" in md
    assert "### Attachment 2: two.txt" in md
    assert "### Attachment 3: three.txt" not in md
    assert "Skipped 1 remaining attachments: exceeded limit." in md


def test_eml_to_markdown_converter_failure_is_fail_closed():
    raw = _eml_with_attachments(
        [
            ("invoice.pdf", b"%PDF-1.4\nfake\n%%EOF", "application", "pdf"),
        ]
    )

    def boom_converter(**kwargs):
        raise RuntimeError("converter crashed")

    md = eml_to_markdown(
        raw,
        filename="converter-failure.eml",
        attachment_converter=boom_converter,
    )

    assert "# Email: Attachment test" in md
    assert "### Attachment 1: invoice.pdf" in md
    assert "- Status: extraction-failed" in md
    assert "- Extractor: skip-rag-convert" in md
    assert "attachment extraction failed: converter crashed" in md


def test_eml_to_markdown_image_extraction_failure_is_fail_closed(monkeypatch):
    def boom_post(*args, **kwargs):
        raise ConnectionError("model unavailable")

    monkeypatch.setattr(email_module.requests, "post", boom_post)
    raw = _eml_with_attachments(
        [
            ("screenshot.png", b"image bytes", "image", "png"),
        ]
    )

    md = eml_to_markdown(raw, filename="image-failure.eml")

    assert "# Email: Attachment test" in md
    assert "### Attachment 1: screenshot.png" in md
    assert "- Status: extraction-failed" in md
    assert "model unavailable" in md


def test_email_module_import_does_not_require_beautifulsoup(monkeypatch):
    original_import = builtins.__import__
    original_module = sys.modules.pop("open_webui.skiprag.email", None)
    original_package_attr = getattr(skiprag_pkg, "email", None)

    def fail_bs4_import(name, *args, **kwargs):
        if name == "bs4" or name.startswith("bs4."):
            raise ImportError("bs4 unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_bs4_import)

    try:
        imported = importlib.import_module("open_webui.skiprag.email")
        plain_md = imported.eml_to_markdown(
            b"Subject: Plain\r\n" b"Content-Type: text/plain; charset=utf-8\r\n" b"\r\n" b"Plain body.",
            filename="plain.eml",
        )
        html_md = imported.eml_to_markdown(
            b"Subject: HTML\r\n" b"Content-Type: text/html; charset=utf-8\r\n" b"\r\n" b"<p>HTML body.</p>",
            filename="html.eml",
        )
    finally:
        sys.modules.pop("open_webui.skiprag.email", None)
        if original_module is not None:
            sys.modules["open_webui.skiprag.email"] = original_module
        if original_package_attr is not None:
            setattr(skiprag_pkg, "email", original_package_attr)

    assert callable(imported.eml_to_markdown)
    assert "Plain body." in plain_md
    assert html_md
    assert "## Parse Warnings" in html_md
    assert "bs4 unavailable" in html_md


def test_eml_to_markdown_ignores_inline_image_without_filename():
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: Inline image\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/related; boundary=RELATED\r\n"
        b"\r\n"
        b"--RELATED\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<p>Hello</p><img src=\"cid:logo\">\r\n"
        b"--RELATED\r\n"
        b"Content-Type: image/png\r\n"
        b"Content-Disposition: inline\r\n"
        b"Content-ID: <logo>\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"iVBORw0KGgo=\r\n"
        b"--RELATED--\r\n"
    )

    md = eml_to_markdown(raw, filename="inline.eml")

    assert "Hello" in md
    assert "## Attachments" not in md
    assert "image/png" not in md


def test_eml_to_markdown_lists_inline_image_with_filename_as_attachment_metadata(monkeypatch):
    monkeypatch.setattr(
        email_module,
        "_extract_image_via_task_model",
        lambda fb, fn: (False, "Warning: task model image extraction failed: unavailable"),
    )

    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: Inline image filename\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/related; boundary=RELATED\r\n"
        b"\r\n"
        b"--RELATED\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<p>Hello</p><img src=\"cid:logo\">\r\n"
        b"--RELATED\r\n"
        b"Content-Type: image/png\r\n"
        b"Content-Disposition: inline; filename=\"logo.png\"\r\n"
        b"Content-ID: <logo>\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"iVBORw0KGgo=\r\n"
        b"--RELATED--\r\n"
    )

    md = eml_to_markdown(raw, filename="inline-filename.eml")

    assert "Hello" in md
    assert "## Attachments" in md
    assert "### Attachment 1: logo.png" in md
    assert "- Content-Type: image/png" in md
    assert "- Disposition: inline" in md
    assert "- Status: extraction-failed" in md
    assert "- Extractor: task-model-vision" in md
    assert "task model image extraction failed: unavailable" in md


def test_eml_to_markdown_attachment_decode_failure_is_metadata_only(monkeypatch):
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: Malformed attachment\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/mixed; boundary=MIXED\r\n"
        b"\r\n"
        b"--MIXED\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Plain body.\r\n"
        b"--MIXED\r\n"
        b"Content-Type: application/octet-stream\r\n"
        b"Content-Disposition: attachment; filename=\"broken.bin\"\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"broken payload\r\n"
        b"--MIXED--\r\n"
    )

    def boom(_part):
        raise ValueError("payload decode failed")

    monkeypatch.setattr("open_webui.skiprag.email._decoded_attachment_bytes", boom)

    md = eml_to_markdown(raw, filename="broken-attachment.eml")

    assert md
    assert "### Attachment 1: broken.bin" in md
    assert "- Size: 0 bytes" in md
    assert "- Status: extraction-failed" in md
    assert "## Parse Warnings" in md
    assert "payload decode failed" in md
