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
