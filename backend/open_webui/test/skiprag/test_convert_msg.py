"""Path A `.msg` extraction.

Before this, `.msg` hit the unknown-extension branch in `_convert_uncached`,
spent ~2s on a docling round-trip that cannot read CFBF at all, and came back
`empty_conversion`. Live on 8083 that dropped 31 of a user's 50 uploaded files
without the model ever being told which ones.
"""

import struct

import pytest

from open_webui.skiprag import convert as convert_module
from open_webui.skiprag.msg import (
    MsgExtractionError,
    extract_msg_text_from_bytes,
)


def _fail_wrong_branch(*args, **kwargs):
    raise AssertionError(".msg conversion must not use docling or fallback loader")


def _msg_bytes(body_text, *, storage="fat"):
    """Build a minimal v3 CFBF file holding one Unicode MAPI body stream.

    Byte-identical construction to Hermes' `tests/tools/test_read_extract.py`
    fixture, so both copies of the reader are exercised by the same geometry.
    """
    free_sector = 0xFFFFFFFF
    end_of_chain = 0xFFFFFFFE
    fat_sector = 0xFFFFFFFD
    sector_size = 512

    header = bytearray(sector_size)
    header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<HHHH", header, 24, 0x003E, 3, 0xFFFE, 9)
    struct.pack_into("<H", header, 32, 6)
    first_mini_fat = 2 if storage == "mini" else end_of_chain
    mini_fat_count = 1 if storage == "mini" else 0
    struct.pack_into(
        "<IIIIIIIII",
        header,
        40,
        0,
        1,
        1,
        0,
        4096,
        first_mini_fat,
        mini_fat_count,
        end_of_chain,
        0,
    )
    struct.pack_into("<I", header, 76, 0)
    for offset in range(80, sector_size, 4):
        struct.pack_into("<I", header, offset, free_sector)

    fat = bytearray(b"\xff" * sector_size)
    if storage == "mini":
        fat_entries = [fat_sector, end_of_chain, end_of_chain, end_of_chain]
    else:
        fat_entries = [fat_sector, end_of_chain] + list(range(3, 10)) + [end_of_chain]
    for index, value in enumerate(fat_entries):
        struct.pack_into("<I", fat, index * 4, value)

    def directory_entry(name, entry_type, child, start_sector, stream_size):
        entry = bytearray(128)
        encoded_name = (name + "\0").encode("utf-16le")
        entry[: len(encoded_name)] = encoded_name
        struct.pack_into(
            "<HBBIII",
            entry,
            64,
            len(encoded_name),
            entry_type,
            1,
            free_sector,
            free_sector,
            child,
        )
        struct.pack_into("<I", entry, 116, start_sector)
        struct.pack_into("<Q", entry, 120, stream_size)
        return entry

    encoded_body = (body_text + "\r\n\0").encode("utf-16le")
    if storage == "mini":
        if len(encoded_body) <= 64 or len(encoded_body) > 128:
            raise ValueError("mini fixture body must span exactly two mini sectors")
        root_start, root_size = 3, 128
        body_start, body_size = 0, len(encoded_body)
    else:
        root_start, root_size = end_of_chain, 0
        body_start, body_size = 2, 4096

    directory = bytearray(sector_size)
    directory[:128] = directory_entry("Root Entry", 5, 1, root_start, root_size)
    directory[128:256] = directory_entry(
        "__substg1.0_1000001F",
        2,
        free_sector,
        body_start,
        body_size,
    )

    if storage == "mini":
        mini_fat = bytearray(b"\xff" * sector_size)
        struct.pack_into("<I", mini_fat, 0, 1)
        struct.pack_into("<I", mini_fat, 4, end_of_chain)
        mini_stream = bytearray(sector_size)
        mini_stream[: len(encoded_body)] = encoded_body
        sectors = [fat, directory, mini_fat, mini_stream]
    else:
        body = (encoded_body * ((4096 // len(encoded_body)) + 1))[:4096]
        sectors = [fat, directory] + [
            body[offset : offset + sector_size] for offset in range(0, 4096, sector_size)
        ]

    out = bytearray(header)
    for sector in sectors:
        out.extend(sector)
    return bytes(out)


@pytest.fixture(autouse=True)
def isolate_skiprag_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(convert_module, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(convert_module, "_docling_convert", _fail_wrong_branch)
    monkeypatch.setattr(convert_module, "_fallback_loader", _fail_wrong_branch)


def test_msg_is_extracted_without_docling():
    raw = _msg_bytes("Dear Sirs, please find the filing receipt attached.")
    md = convert_module.convert_to_markdown(raw, "sample-inbound-email.msg")
    assert "Dear Sirs, please find the filing receipt attached." in md


def test_msg_outcome_is_complete_not_empty_conversion():
    # The exact live symptom: outcome=unavailable / failure_kind=empty_conversion.
    raw = _msg_bytes("Body that docling could never read.")
    outcome = convert_module.convert_to_markdown_outcome(
        raw, "sample-outbound-email.msg"
    )
    assert outcome.is_complete
    assert outcome.failure_kind is None
    assert "Body that docling could never read." in outcome.markdown


def test_msg_mini_stream_storage_is_extracted():
    # Small bodies live in the mini-FAT, a genuinely different read path.
    body = "Short body living in the mini stream, ok."
    raw = _msg_bytes(body, storage="mini")
    md = convert_module.convert_to_markdown(raw, "short.msg")
    assert body in md


def test_msg_extension_matching_is_case_insensitive():
    raw = _msg_bytes("Uppercase extension body.")
    md = convert_module.convert_to_markdown(raw, "REPLY.MSG")
    assert "Uppercase extension body." in md


def test_non_cfbf_msg_raises_rather_than_returning_empty():
    with pytest.raises(MsgExtractionError):
        extract_msg_text_from_bytes(b"not a compound file" * 64)


def test_msg_body_with_only_whitespace_is_a_failure_not_a_success():
    # Mini storage stores an exact body_size, so this is a body that really
    # is nothing but whitespace rather than a padded regular-FAT sector.
    with pytest.raises(MsgExtractionError):
        extract_msg_text_from_bytes(_msg_bytes(" " * 40, storage="mini"))


def test_owui_and_hermes_msg_readers_have_not_diverged():
    """The two copies must stay byte-identical apart from the bytes entrypoint.

    OpenWebUI and Hermes ship as separate images with no shared import path,
    so `.msg` support exists twice on purpose. A silent divergence would mean
    the same file extracts on Path B and fails on Path A — exactly the class
    of two-pipeline drift that produced this bug.
    """
    from pathlib import Path

    owui_root = Path(__file__).resolve().parents[4]
    candidates = [
        # In the parent stack, hermes-agent is a sibling submodule.
        owui_root.parent / "hermes-agent" / "tools" / "msg_extract.py",
        # Split-worktree development layout.
        owui_root.parent / "kg-hermes-slop" / "tools" / "msg_extract.py",
    ]
    hermes = next((path for path in candidates if path.is_file()), None)
    if hermes is None:
        pytest.skip(
            "hermes checkout not present; tried "
            + ", ".join(str(path) for path in candidates)
        )

    ours = (owui_root / "backend" / "open_webui" / "skiprag" / "msg.py").read_text(
        encoding="utf-8"
    )
    theirs = hermes.read_text(encoding="utf-8")

    def core(text):
        # Compare the parser only: drop the module docstring and everything
        # from the public entrypoints down.
        body = text.split('"""', 2)[-1]
        for marker in ("def extract_msg_text_from_bytes", "def extract_msg_text"):
            if marker in body:
                return body.split(marker, 1)[0].strip()
        return body.strip()

    assert core(ours) == core(theirs)
