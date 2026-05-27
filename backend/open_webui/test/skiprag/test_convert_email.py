from email.message import EmailMessage

import pytest

from open_webui.skiprag import convert as convert_module


def _fail_wrong_branch(*args, **kwargs):
    raise AssertionError(".eml conversion must not use docling or fallback loader")


def _eml_with_text_attachment():
    msg = EmailMessage()
    msg["From"] = "Alice <alice@example.com>"
    msg["To"] = "Bob <bob@example.com>"
    msg["Subject"] = "Cached attachment"
    msg.set_content("Plain body.")
    msg.add_attachment(
        b"attachment text",
        maintype="text",
        subtype="plain",
        filename="note.txt",
    )
    return msg.as_bytes()


@pytest.fixture(autouse=True)
def isolate_skiprag_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(convert_module, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(convert_module, "_docling_convert", _fail_wrong_branch)
    monkeypatch.setattr(convert_module, "_fallback_loader", _fail_wrong_branch)


def test_convert_to_markdown_routes_eml_to_email_parser(monkeypatch):
    calls = []

    def fake_eml_to_markdown(
        raw_bytes,
        filename,
        request=None,
        attachment_converter=None,
    ):
        calls.append((raw_bytes, filename, request, attachment_converter))
        return "# Email parser output\n"

    monkeypatch.setattr(
        "open_webui.skiprag.email.eml_to_markdown",
        fake_eml_to_markdown,
    )

    md = convert_module.convert_to_markdown(b"Subject: Hi\r\n\r\nBody", "sample.eml")

    assert md == "# Email parser output\n"
    assert len(calls) == 1
    assert calls[0][0] == b"Subject: Hi\r\n\r\nBody"
    assert calls[0][1] == "sample.eml"


def test_convert_to_markdown_passes_request_to_email_parser(monkeypatch):
    request = object()
    captured_request = None

    def fake_eml_to_markdown(
        raw_bytes,
        filename,
        request=None,
        attachment_converter=None,
    ):
        nonlocal captured_request
        captured_request = request
        return "# Email parser output\n"

    monkeypatch.setattr(
        "open_webui.skiprag.email.eml_to_markdown",
        fake_eml_to_markdown,
    )

    convert_module.convert_to_markdown(
        b"Subject: Hi\r\n\r\nBody",
        "sample.eml",
        request=request,
    )

    assert captured_request is request


def test_convert_to_markdown_passes_converter_entry_point_to_email_parser(monkeypatch):
    captured_converter = None

    def fake_eml_to_markdown(
        raw_bytes,
        filename,
        request=None,
        attachment_converter=None,
    ):
        nonlocal captured_converter
        captured_converter = attachment_converter
        return "# Email parser output\n"

    monkeypatch.setattr(
        "open_webui.skiprag.email.eml_to_markdown",
        fake_eml_to_markdown,
    )

    convert_module.convert_to_markdown(b"Subject: Hi\r\n\r\nBody", "sample.eml")

    assert callable(captured_converter)
    assert captured_converter is convert_module.convert_to_markdown


def test_convert_to_markdown_routes_uppercase_eml_to_email_parser(monkeypatch):
    calls = []

    def fake_eml_to_markdown(
        raw_bytes,
        filename,
        request=None,
        attachment_converter=None,
    ):
        calls.append(filename)
        return "# Uppercase email parser output\n"

    monkeypatch.setattr(
        "open_webui.skiprag.email.eml_to_markdown",
        fake_eml_to_markdown,
    )

    md = convert_module.convert_to_markdown(b"Subject: Hi\r\n\r\nBody", "SAMPLE.EML")

    assert md == "# Uppercase email parser output\n"
    assert calls == ["SAMPLE.EML"]


def test_convert_to_markdown_uses_cache_for_repeated_eml_bytes(monkeypatch):
    calls = []

    def fake_eml_to_markdown(
        raw_bytes,
        filename,
        request=None,
        attachment_converter=None,
    ):
        calls.append((raw_bytes, filename))
        return "# Cached email parser output\n"

    monkeypatch.setattr(
        "open_webui.skiprag.email.eml_to_markdown",
        fake_eml_to_markdown,
    )

    first = convert_module.convert_to_markdown(
        b"Subject: Hi\r\n\r\nBody",
        "sample.eml",
    )
    second = convert_module.convert_to_markdown(
        b"Subject: Hi\r\n\r\nBody",
        "sample.eml",
    )

    assert first == "# Cached email parser output\n"
    assert second == "# Cached email parser output\n"
    assert calls == [(b"Subject: Hi\r\n\r\nBody", "sample.eml")]


def test_eml_cache_key_uses_attach_v1_variant():
    key = convert_module._cache_key(b"test content", "eml")

    assert key.endswith(".eml.attach-v1.md")
    assert not key.endswith(".eml.md")


def test_non_eml_cache_key_unchanged():
    key = convert_module._cache_key(b"test content", "pdf")

    assert key.endswith(".pdf.md")
    assert "attach-v1" not in key


def test_eml_conversion_caches_with_attach_v1_suffix(monkeypatch, tmp_path):
    monkeypatch.setattr(convert_module, "CACHE_DIR", str(tmp_path))
    raw = _eml_with_text_attachment()

    first = convert_module.convert_to_markdown(raw, "cached.eml")
    cache_files = list(tmp_path.iterdir())

    assert "### Attachment 1: note.txt" in first
    assert "attachment text" in first
    assert len(cache_files) == 1
    assert cache_files[0].name.endswith(".eml.attach-v1.md")

    def fail_if_reparsed(*args, **kwargs):
        raise AssertionError("second conversion should hit the .eml attach-v1 cache")

    monkeypatch.setattr(
        "open_webui.skiprag.email.eml_to_markdown",
        fail_if_reparsed,
    )

    second = convert_module.convert_to_markdown(raw, "cached.eml")

    assert second == first
