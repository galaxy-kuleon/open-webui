import pytest

from open_webui.skiprag import convert as convert_module


def _fail_wrong_branch(*args, **kwargs):
    raise AssertionError(".eml conversion must not use docling or fallback loader")


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
