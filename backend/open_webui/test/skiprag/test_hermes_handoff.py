import ast
import asyncio
import hashlib
import inspect
from types import SimpleNamespace

import pytest

from open_webui.skiprag import hermes_handoff
from open_webui.utils.handoff_filename import (
    MAX_ATTACHMENT_BASENAME_LENGTH,
    build_attachment_basename,
    corrected_attachment_candidate,
    legacy_attachment_candidate,
)


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        ("個資同意書.pdf", "file.pdf"),
        ("個人情報同意書.pdf", "file.pdf"),
        ("개인정보동의서.pdf", "file.pdf"),
        ("😀😀.pdf", "file.pdf"),
        ("Report個資.pdf", "Report.pdf"),
        ("archive.tar.gz", "archive.tar.gz"),
        ("SCAN.PDF", "SCAN.PDF"),
        ("README", "README"),
        (".env", "file.env"),
        ("report.invalid-suffix!", "report"),
        ("report.abcdefghijklmnopq", "report"),
        ("report.pdf ", "report.pdf"),
        ("個資同意書.pdf ", "file.pdf"),
        ("final report.pdf ", "final_report.pdf"),
        ("../private/contract.pdf", "contract.pdf"),
        (r"C:\private\report.PDF", "report.PDF"),
        ("bad\x00line\nname.pdf", "bad_line_name.pdf"),
        ("x" * 120 + ".pdf", "x" * 79 + ".pdf"),
    ],
)
def test_corrected_attachment_candidate_vectors(raw_name, expected):
    assert corrected_attachment_candidate(raw_name, index=0) == expected


@pytest.mark.parametrize("stem_length", [95, 96, 120])
@pytest.mark.parametrize("index", [0, 999, 1000])
def test_corrected_basename_retains_suffix_with_actual_prefix_budget(
    stem_length,
    index,
):
    nonce = "deadbeef"
    prefix = f"{index:03d}-{nonce}-"
    basename = build_attachment_basename(
        "a" * stem_length + ".pdf",
        index=index,
        nonce=nonce,
    )

    assert basename.startswith(prefix)
    assert basename.endswith(".pdf")
    assert len(basename) == MAX_ATTACHMENT_BASENAME_LENGTH
    assert len(basename) <= MAX_ATTACHMENT_BASENAME_LENGTH
    assert basename == (
        prefix
        + "a" * (MAX_ATTACHMENT_BASENAME_LENGTH - len(prefix) - len(".pdf"))
        + ".pdf"
    )


def test_legacy_candidate_reproduces_old_whole_basename_sanitizer():
    assert legacy_attachment_candidate("個資同意書.pdf") == "pdf"
    assert legacy_attachment_candidate("😀😀.pdf") == "pdf"
    assert legacy_attachment_candidate("x" * 120 + ".pdf") == "x" * 96
    assert legacy_attachment_candidate("../report.pdf") == "report.pdf"
    assert (
        legacy_attachment_candidate(r"C:\private\report.pdf")
        == "C_private_report.pdf"
    )
    assert legacy_attachment_candidate(".env") == "env"


def test_safe_segment_identity_contract_is_byte_and_behavior_unchanged():
    source = inspect.getsource(hermes_handoff._safe_segment)
    assert (
        hashlib.sha256(source.encode()).hexdigest()
        == "5582250b4c28d82071a3f5970906d167373fb81c7ac4ba8c9a1835e744cd8c4e"
    )
    assert hermes_handoff._safe_segment("../secret", "fallback") == "secret"
    assert hermes_handoff._safe_segment(r"user\name", "fallback") == "user_name"
    assert hermes_handoff._safe_segment("個資", "fallback") == "fallback"
    assert hermes_handoff._safe_segment("x" * 120, "fallback") == "x" * 96


def test_write_handoff_files_uses_corrected_name_nonce_and_original_bytes(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        hermes_handoff.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="deadbeefcafebabe"),
    )
    raw_bytes = b"\x00original payload\xff"

    written = hermes_handoff._write_handoff_files(
        tmp_path,
        r"C:\incoming\個資同意書.pdf ",
        raw_bytes,
        index=1000,
    )

    path = tmp_path / "1000-deadbeef-file.pdf"
    assert written == str(path)
    assert path.read_bytes() == raw_bytes
    assert len(path.name) <= MAX_ATTACHMENT_BASENAME_LENGTH


def _run_handoff(monkeypatch, tmp_path, body, payloads):
    monkeypatch.setattr(hermes_handoff, "HANDOFF_DIR", str(tmp_path / "handoff"))
    monkeypatch.setattr(hermes_handoff, "HANDOFF_SIGNING_KEY", "test-secret")
    calls = []

    async def fake_resolve_raw_bytes(file_item, filename):
        calls.append((file_item.get("id") or file_item.get("file_id"), filename))
        return payloads[file_item.get("id") or file_item.get("file_id")]

    monkeypatch.setattr(hermes_handoff, "_resolve_raw_bytes", fake_resolve_raw_bytes)

    result = asyncio.run(
        hermes_handoff.run_hermes_handoff(
            request=None,
            body=body,
            extra_params={},
            user=SimpleNamespace(id="user-1"),
        )
    )
    return result, calls


def test_path_b_imports_no_asyncio_or_markdown_converter():
    tree = ast.parse(inspect.getsource(hermes_handoff))

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert "asyncio" not in imported_modules
    assert "open_webui.skiprag.convert" not in imported_modules
    assert not hasattr(hermes_handoff, "convert_to_markdown")


def test_run_hermes_handoff_writes_only_original_file(monkeypatch, tmp_path):
    body = {
        "chat_id": "chat-1",
        "metadata": {
            "files": [
                {"type": "file", "id": "file-1", "name": "report.pdf"},
                {"type": "collection", "id": "keep-me"},
            ],
        },
        "messages": [
            {"role": "user", "id": "msg-1", "content": "please inspect this"},
        ],
    }

    result, calls = _run_handoff(
        monkeypatch,
        tmp_path,
        body,
        {"file-1": b"%PDF fake payload"},
    )

    written_files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert len(written_files) == 1
    assert written_files[0].read_bytes() == b"%PDF fake payload"
    assert not list(tmp_path.rglob("*.md"))
    assert calls == [("file-1", "report.pdf")]

    assert result["metadata"]["files"] == [{"type": "collection", "id": "keep-me"}]
    content = result["messages"][0]["content"]
    assert "<files>" in content
    assert 'original="' in content
    assert 'sig="' in content
    assert 'markdown="' not in content
    assert 'name="' not in content
    assert 'user="' not in content
    assert 'chat="' not in content
    assert 'file_id="file-1"' in content
    assert 'sha256="14def7dcd11c3d983d7b22888e45854250e6abb6f0b76270f5bb01e0f58f2bcf"' in content


def test_legacy_fallback_when_no_user_message(monkeypatch, tmp_path):
    legacy_body = {
        "chat_id": "chat-1",
        "metadata": {
            "chat_id": "chat-1",
            "files": [
                {"type": "file", "id": "file-1", "name": "report.pdf"},
                {"type": "collection", "id": "keep-me"},
            ],
        },
        "messages": [
            {"role": "user", "id": "msg-1", "content": "direct caller upload"},
        ],
    }

    result, calls = _run_handoff(
        monkeypatch,
        tmp_path,
        legacy_body,
        {"file-1": b"%PDF fake payload"},
    )

    content = result["messages"][0]["content"]
    assert calls == [("file-1", "report.pdf")]
    assert content.count("<files>") == 1
    assert content.count("<file ") == 1
    assert 'file_id="file-1"' in content
    assert result["metadata"]["files"] == [{"type": "collection", "id": "keep-me"}]

    followup_body = {
        "chat_id": "chat-1",
        "metadata": {
            "chat_id": "chat-1",
            "user_message": {"id": "msg-2", "content": "follow-up only"},
            "files": [
                {"type": "file", "id": "file-1", "name": "report.pdf"},
                {"type": "collection", "id": "keep-me"},
            ],
        },
        "messages": [
            {"role": "user", "id": "msg-2", "content": "follow-up only"},
        ],
    }

    result, calls = _run_handoff(
        monkeypatch,
        tmp_path,
        followup_body,
        {"file-1": b"%PDF fake payload"},
    )

    assert calls == []
    assert "<files>" not in result["messages"][0]["content"]
    assert result["metadata"]["files"] == [{"type": "collection", "id": "keep-me"}]


def test_initial_turn_current_file_triggers_one_handoff_block(monkeypatch, tmp_path):
    body = {
        "chat_id": "chat-1",
        "metadata": {
            "chat_id": "chat-1",
            "user_message": {
                "id": "msg-1",
                "files": [{"type": "file", "id": "file-1", "name": "report.pdf"}],
            },
            "files": [{"type": "file", "id": "file-1", "name": "report.pdf"}],
        },
        "messages": [
            {"role": "user", "id": "msg-1", "content": "please inspect this"},
        ],
    }

    result, calls = _run_handoff(
        monkeypatch,
        tmp_path,
        body,
        {"file-1": b"%PDF fake payload"},
    )

    content = result["messages"][0]["content"]
    assert calls == [("file-1", "report.pdf")]
    assert content.count("<files>") == 1
    assert content.count("<file ") == 1
    assert len([path for path in tmp_path.rglob("*") if path.is_file()]) == 1


def test_followup_turn_with_only_historical_chat_files_does_not_handoff(monkeypatch, tmp_path):
    body = {
        "chat_id": "chat-1",
        "metadata": {
            "chat_id": "chat-1",
            "user_message": {"id": "msg-2", "content": "where is it?"},
            "files": [
                {"type": "file", "id": "file-1", "name": "report.pdf"},
                {"type": "collection", "id": "keep-me"},
            ],
        },
        "messages": [
            {"role": "user", "id": "msg-1", "content": "initial"},
            {"role": "assistant", "id": "asst-1", "content": "done"},
            {"role": "user", "id": "msg-2", "content": "where is it?"},
        ],
    }

    result, calls = _run_handoff(
        monkeypatch,
        tmp_path,
        body,
        {"file-1": b"%PDF fake payload"},
    )

    assert calls == []
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]
    assert "<files>" not in result["messages"][-1]["content"]
    assert result["metadata"]["files"] == [{"type": "collection", "id": "keep-me"}]


def test_followup_turn_genuine_new_current_file_still_handoffs(monkeypatch, tmp_path):
    body = {
        "chat_id": "chat-1",
        "metadata": {
            "chat_id": "chat-1",
            "user_message": {
                "id": "msg-2",
                "files": [{"type": "file", "id": "new-file", "name": "new.pdf"}],
            },
            "files": [
                {"type": "file", "id": "old-file", "name": "old.pdf"},
                {"type": "file", "id": "new-file", "name": "new.pdf"},
                {"type": "collection", "id": "keep-me"},
            ],
        },
        "messages": [
            {"role": "user", "id": "msg-1", "content": "initial"},
            {"role": "assistant", "id": "asst-1", "content": "done"},
            {"role": "user", "id": "msg-2", "content": "new upload"},
        ],
    }

    result, calls = _run_handoff(
        monkeypatch,
        tmp_path,
        body,
        {
            "old-file": b"old bytes",
            "new-file": b"new bytes",
        },
    )

    written_files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert calls == [("new-file", "new.pdf")]
    assert len(written_files) == 1
    assert written_files[0].read_bytes() == b"new bytes"
    assert 'file_id="new-file"' in result["messages"][-1]["content"]
    assert "old-file" not in result["messages"][-1]["content"]
    assert result["metadata"]["files"] == [{"type": "collection", "id": "keep-me"}]


def test_duplicate_current_turn_file_id_is_deduped_and_emits_stable_identity(monkeypatch, tmp_path):
    duplicate = {"type": "file", "id": "file-1", "name": "report.pdf"}
    body = {
        "chat_id": "chat-1",
        "metadata": {
            "chat_id": "chat-1",
            "user_message": {
                "id": "msg-1",
                "files": [duplicate, dict(duplicate)],
            },
            "files": [duplicate, dict(duplicate)],
        },
        "messages": [
            {"role": "user", "id": "msg-1", "content": "please inspect this"},
        ],
    }

    result, calls = _run_handoff(
        monkeypatch,
        tmp_path,
        body,
        {"file-1": b"%PDF fake payload"},
    )

    content = result["messages"][0]["content"]
    assert calls == [("file-1", "report.pdf")]
    assert content.count("<file ") == 1
    assert 'file_id="file-1"' in content
    assert 'sha256="14def7dcd11c3d983d7b22888e45854250e6abb6f0b76270f5bb01e0f58f2bcf"' in content


def test_same_content_different_file_ids_are_preserved(monkeypatch, tmp_path):
    body = {
        "chat_id": "chat-1",
        "metadata": {
            "chat_id": "chat-1",
            "user_message": {
                "id": "msg-1",
                "files": [
                    {"type": "file", "id": "file-1", "name": "first.pdf"},
                    {"type": "file", "id": "file-2", "name": "second.pdf"},
                ],
            },
            "files": [
                {"type": "file", "id": "file-1", "name": "first.pdf"},
                {"type": "file", "id": "file-2", "name": "second.pdf"},
            ],
        },
        "messages": [
            {"role": "user", "id": "msg-1", "content": "compare both files"},
        ],
    }

    result, calls = _run_handoff(
        monkeypatch,
        tmp_path,
        body,
        {
            "file-1": b"same bytes",
            "file-2": b"same bytes",
        },
    )

    content = result["messages"][0]["content"]
    written_files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert calls == [("file-1", "first.pdf"), ("file-2", "second.pdf")]
    assert len(written_files) == 2
    assert content.count("<file ") == 2
    assert 'file_id="file-1"' in content
    assert 'file_id="file-2"' in content


# ── #17 slice-4: counts-only <materials_note> for SKIPPED (not-delivered) current-turn uploads ──


def _body_with(files):
    """Initial-turn body whose current-turn (user_message) uploads are ``files``."""
    return {
        "chat_id": "chat-1",
        "metadata": {
            "chat_id": "chat-1",
            "user_message": {"id": "msg-1", "files": files},
            "files": list(files),
        },
        "messages": [{"role": "user", "id": "msg-1", "content": "here are my materials"}],
    }


def test_materials_note_injected_when_a_file_is_skipped(monkeypatch, tmp_path):
    # one delivered (real bytes) + one skipped (resolves to b'') → <files> for the delivered one
    # AND a counts-only <materials_note skipped="1"> for the dropped one.
    body = _body_with([
        {"type": "file", "id": "del-1", "name": "delivered.pdf"},
        {"type": "file", "id": "skip-1", "name": "dropped.pdf"},
    ])
    result, _calls = _run_handoff(monkeypatch, tmp_path, body, {"del-1": b"ok bytes", "skip-1": b""})
    content = result["messages"][0]["content"]
    assert content.count("<files>") == 1
    assert content.count("<file ") == 1
    assert 'file_id="del-1"' in content
    assert len([p for p in tmp_path.rglob("*") if p.is_file()]) == 1  # only the delivered file
    assert '<materials_note skipped="1">' in content
    assert "1 uploaded file(s) could not be read or delivered" in content
    assert content.count("<materials_note") == 1


def test_all_skipped_injects_only_materials_note_no_files_block(monkeypatch, tmp_path):
    # ALL current-turn uploads skipped (entries empty, skipped>0) → ONLY the note, NO empty <files>.
    body = _body_with([
        {"type": "file", "id": "skip-1", "name": "a.pdf"},
        {"type": "file", "id": "skip-2", "name": "b.pdf"},
    ])
    result, _calls = _run_handoff(monkeypatch, tmp_path, body, {"skip-1": b"", "skip-2": b""})
    content = result["messages"][0]["content"]
    assert "<files>" not in content  # no empty files block
    assert '<materials_note skipped="2">' in content
    assert "2 uploaded file(s) could not be read or delivered" in content
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]  # nothing handed off
    assert result["metadata"]["files"] == []  # current-turn file items still stripped from RAG


def test_all_delivered_no_materials_note(monkeypatch, tmp_path):
    # every upload delivered → <files> only, NO note, no noise.
    body = _body_with([
        {"type": "file", "id": "del-1", "name": "a.pdf"},
        {"type": "file", "id": "del-2", "name": "b.pdf"},
    ])
    result, _calls = _run_handoff(monkeypatch, tmp_path, body, {"del-1": b"x", "del-2": b"y"})
    content = result["messages"][0]["content"]
    assert content.count("<file ") == 2
    assert "<materials_note" not in content
    assert "skipped=" not in content


def test_no_uploaded_files_byte_for_byte_unchanged(monkeypatch, tmp_path):
    # genuinely no current-turn uploads → early return, NO injection at all (no <files>, no note).
    body = {
        "chat_id": "chat-1",
        "metadata": {
            "chat_id": "chat-1",
            "user_message": {"id": "msg-2", "content": "follow-up only"},
            "files": [{"type": "file", "id": "old", "name": "old.pdf"}],
        },
        "messages": [
            {"role": "user", "id": "msg-1", "content": "initial"},
            {"role": "assistant", "id": "asst-1", "content": "done"},
            {"role": "user", "id": "msg-2", "content": "follow-up only"},
        ],
    }
    original = body["messages"][-1]["content"]
    result, calls = _run_handoff(monkeypatch, tmp_path, body, {"old": b"x"})
    assert calls == []
    last = result["messages"][-1]["content"]
    assert last == original  # byte-for-byte unchanged
    assert "<files>" not in last and "<materials_note" not in last


def test_materials_note_is_m4_counts_only_poison_skipped_name(monkeypatch, tmp_path):
    # M4: a skipped file with a poison name must contribute ONLY to the count — its raw name must
    # NEVER appear in the injection (or anywhere in the forwarded body/metadata).
    poison = "SECRET Client Contract.pdf"
    body = _body_with([
        {"type": "file", "id": "del-1", "name": "public.pdf"},
        {"type": "file", "id": "skip-1", "name": poison},
    ])
    result, _calls = _run_handoff(monkeypatch, tmp_path, body, {"del-1": b"ok", "skip-1": b""})
    content = result["messages"][0]["content"]
    assert '<materials_note skipped="1">' in content
    for frag in ("SECRET", "Client", "Contract", poison):
        assert frag not in content
    assert "SECRET" not in str(result["messages"])
    assert "SECRET" not in str(result["metadata"].get("files"))


def test_materials_note_is_transient_not_in_user_message_record(monkeypatch, tmp_path):
    # The note is injected into the FORWARDED messages only — NOT into metadata['user_message']
    # (the per-turn record), exactly like <files>. OWUI persists the original user message, not this.
    body = _body_with([
        {"type": "file", "id": "del-1", "name": "a.pdf"},
        {"type": "file", "id": "skip-1", "name": "b.pdf"},
    ])
    result, _calls = _run_handoff(monkeypatch, tmp_path, body, {"del-1": b"x", "skip-1": b""})
    assert "<materials_note" in result["messages"][0]["content"]
    assert "<materials_note" not in str(result["metadata"]["user_message"])
    assert "<files>" not in str(result["metadata"]["user_message"])
