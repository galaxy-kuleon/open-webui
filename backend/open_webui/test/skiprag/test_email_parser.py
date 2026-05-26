import pytest

from open_webui.skiprag.email import eml_to_markdown


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
    assert "- Status: metadata-only" in md


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
    attachment_heading = next(
        line for line in md.splitlines() if line.startswith("### Attachment 1:")
    )

    assert "[evil](http://bad.com).pdf" not in md
    assert "[" not in attachment_heading
    assert "](" not in attachment_heading
    assert ")" not in attachment_heading
    assert "evil" in attachment_heading
    assert "bad.com" in attachment_heading
    assert ".pdf" in attachment_heading


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


def test_eml_to_markdown_lists_inline_image_with_filename_as_attachment_metadata():
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
    assert "- Status: metadata-only" in md


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

    monkeypatch.setattr("open_webui.skiprag.email._decoded_payload_size", boom)

    md = eml_to_markdown(raw, filename="broken-attachment.eml")

    assert md
    assert "### Attachment 1: broken.bin" in md
    assert "- Size: 0 bytes" in md
    assert "- Status: metadata-only" in md
    assert "## Parse Warnings" in md
    assert "payload decode failed" in md
