import ast
import asyncio
import hashlib
import inspect
import json
import multiprocessing
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
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


def _run_handoff(monkeypatch, tmp_path, body, payloads, user_id="user-1"):
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
            user=SimpleNamespace(id=user_id),
        )
    )
    return result, calls


def _attachment_files(tmp_path):
    return [
        path
        for path in tmp_path.rglob("*")
        if path.is_file()
        and path.name
        not in {".owui-handoff-manifest.json", ".owui-handoff-manifest.lock"}
    ]


def _merge_manifest_entry_in_process(
    handoff_dir,
    index,
    start_barrier,
):
    from open_webui.skiprag import hermes_handoff as process_handoff

    process_handoff.HANDOFF_DIR = handoff_dir
    process_handoff.HANDOFF_SIGNING_KEY = "test-secret"
    original_read = process_handoff._read_manifest_entries

    def slow_read(user_id, chat_id):
        entries = original_read(user_id, chat_id)
        time.sleep(0.1)
        return entries

    process_handoff._read_manifest_entries = slow_read
    chat_root = Path(handoff_dir) / "user" / "user-1" / "chat" / "chat-1"
    path = chat_root / "message" / f"msg-{index}" / f"report-{index}.pdf"
    start_barrier.wait(timeout=30)
    process_handoff._merge_manifest_entries(
        "user-1",
        "chat-1",
        [
            {
                "original": str(path),
                "file_id": f"file-{index}",
                "sha256": hashlib.sha256(str(index).encode()).hexdigest(),
            }
        ],
    )


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

    written_files = _attachment_files(tmp_path)
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
    assert len(_attachment_files(tmp_path)) == 1


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
    assert not _attachment_files(tmp_path)
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

    written_files = _attachment_files(tmp_path)
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
    written_files = _attachment_files(tmp_path)
    assert calls == [("file-1", "first.pdf"), ("file-2", "second.pdf")]
    assert len(written_files) == 2
    assert content.count("<file ") == 2
    assert 'file_id="file-1"' in content
    assert 'file_id="file-2"' in content


def test_three_upload_turns_accumulate_50_and_followup_reinjects_without_scan(
    monkeypatch,
    tmp_path,
):
    all_files = []
    resolved_ids = []
    for turn, count in enumerate((17, 17, 16), start=1):
        start = len(all_files)
        current_files = [
            {
                "type": "file",
                "id": f"file-{index:03d}",
                "name": f"report-{index:03d}.pdf",
            }
            for index in range(start, start + count)
        ]
        all_files.extend(current_files)
        body = {
            "chat_id": "chat-1",
            "metadata": {
                "chat_id": "chat-1",
                "user_message": {"id": f"msg-{turn}", "files": current_files},
                "files": list(all_files),
            },
            "messages": [
                {
                    "role": "user",
                    "id": f"msg-{turn}",
                    "content": f"audit upload turn {turn}",
                }
            ],
        }
        payloads = {
            item["id"]: f"%PDF payload {item['id']}".encode()
            for item in current_files
        }
        result, calls = _run_handoff(monkeypatch, tmp_path, body, payloads)
        resolved_ids.extend(file_id for file_id, _name in calls)
        assert result["messages"][-1]["content"].count("<file ") == len(all_files)

    manifest_path = (
        tmp_path
        / "handoff"
        / "user"
        / "user-1"
        / "chat"
        / "chat-1"
        / ".owui-handoff-manifest.json"
    )
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == 1
    assert manifest["scope"] == {"user_id": "user-1", "chat_id": "chat-1"}
    assert [entry["file_id"] for entry in manifest["entries"]] == [
        f"file-{index:03d}" for index in range(50)
    ]
    assert all(entry["sig"] for entry in manifest["entries"])
    assert resolved_ids == [f"file-{index:03d}" for index in range(50)]

    followup_body = {
        "chat_id": "chat-1",
        "metadata": {
            "chat_id": "chat-1",
            "user_message": {"id": "msg-4", "content": "continue the audit"},
            "files": [],
        },
        "messages": [{"role": "user", "id": "msg-4", "content": "continue the audit"}],
    }
    manifest_before = manifest_path.read_bytes()

    def fail_directory_scan(*args, **kwargs):
        raise AssertionError("follow-up must read the fixed chat manifest without scanning")

    monkeypatch.setattr(Path, "rglob", fail_directory_scan)
    monkeypatch.setattr(Path, "glob", fail_directory_scan)
    monkeypatch.setattr(Path, "iterdir", fail_directory_scan)
    monkeypatch.setattr(os, "walk", fail_directory_scan)
    followup, followup_calls = _run_handoff(
        monkeypatch,
        tmp_path,
        followup_body,
        {},
    )

    assert followup_calls == []
    assert manifest_path.read_bytes() == manifest_before
    content = followup["messages"][-1]["content"]
    assert content.count("<files>") == 1
    assert content.count("<file ") == 50
    for index in range(50):
        assert f'file_id="file-{index:03d}"' in content


def test_manifest_preserves_all_entries_without_silent_eviction(
    monkeypatch,
    tmp_path,
):
    first_files = [
        {
            "type": "file",
            "id": f"file-{index:03d}",
            "name": f"report-{index:03d}.pdf",
        }
        for index in range(50)
    ]
    first_body = {
        "chat_id": "chat-1",
        "metadata": {
            "chat_id": "chat-1",
            "user_message": {"id": "msg-1", "files": first_files},
            "files": list(first_files),
        },
        "messages": [{"role": "user", "id": "msg-1", "content": "first batch"}],
    }
    _run_handoff(
        monkeypatch,
        tmp_path,
        first_body,
        {item["id"]: item["id"].encode() for item in first_files},
    )
    manifest_path = (
        tmp_path
        / "handoff"
        / "user"
        / "user-1"
        / "chat"
        / "chat-1"
        / ".owui-handoff-manifest.json"
    )
    first_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    original_paths = [entry["original"] for entry in first_manifest["entries"][:5]]

    newest_files = [
        {
            "type": "file",
            "id": f"file-{index:03d}",
            "name": f"report-{index:03d}.pdf",
        }
        for index in range(50, 55)
    ]
    second_body = {
        "chat_id": "chat-1",
        "metadata": {
            "chat_id": "chat-1",
            "user_message": {"id": "msg-2", "files": newest_files},
            "files": first_files + newest_files,
        },
        "messages": [{"role": "user", "id": "msg-2", "content": "new batch"}],
    }
    result, calls = _run_handoff(
        monkeypatch,
        tmp_path,
        second_body,
        {item["id"]: item["id"].encode() for item in newest_files},
    )

    final_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [entry["file_id"] for entry in final_manifest["entries"]] == [
        f"file-{index:03d}" for index in range(55)
    ]
    assert calls == [(item["id"], item["name"]) for item in newest_files]
    content = result["messages"][-1]["content"]
    assert content.count("<file ") == 55
    for index in range(55):
        assert f'file_id="file-{index:03d}"' in content
    assert all(Path(path).is_file() for path in original_paths)


def test_concurrent_thread_manifest_updates_do_not_lose_entries(monkeypatch, tmp_path):
    handoff_dir = tmp_path / "handoff"
    monkeypatch.setattr(hermes_handoff, "HANDOFF_DIR", str(handoff_dir))
    monkeypatch.setattr(hermes_handoff, "HANDOFF_SIGNING_KEY", "test-secret")
    original_read = hermes_handoff._read_manifest_entries

    def slow_read(user_id, chat_id):
        entries = original_read(user_id, chat_id)
        time.sleep(0.01)
        return entries

    monkeypatch.setattr(hermes_handoff, "_read_manifest_entries", slow_read)
    chat_root = handoff_dir / "user" / "user-1" / "chat" / "chat-1"

    def add_entry(index):
        path = chat_root / "message" / f"msg-{index}" / f"report-{index}.pdf"
        hermes_handoff._merge_manifest_entries(
            "user-1",
            "chat-1",
            [
                {
                    "original": str(path),
                    "file_id": f"file-{index}",
                    "sha256": hashlib.sha256(str(index).encode()).hexdigest(),
                }
            ],
        )

    with ThreadPoolExecutor(max_workers=20) as executor:
        list(executor.map(add_entry, range(20)))

    manifest = json.loads(
        (chat_root / ".owui-handoff-manifest.json").read_text(encoding="utf-8")
    )
    assert {entry["file_id"] for entry in manifest["entries"]} == {
        f"file-{index}" for index in range(20)
    }


def test_concurrent_process_manifest_updates_do_not_lose_entries(tmp_path):
    handoff_dir = tmp_path / "handoff"
    context = multiprocessing.get_context("spawn")
    start_barrier = context.Barrier(9)
    processes = [
        context.Process(
            target=_merge_manifest_entry_in_process,
            args=(str(handoff_dir), index, start_barrier),
        )
        for index in range(8)
    ]

    try:
        for process in processes:
            process.start()
        start_barrier.wait(timeout=30)
        for process in processes:
            process.join(timeout=15)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    manifest_path = (
        handoff_dir
        / "user"
        / "user-1"
        / "chat"
        / "chat-1"
        / ".owui-handoff-manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert {entry["file_id"] for entry in manifest["entries"]} == {
        f"file-{index}" for index in range(8)
    }


def test_manifest_byte_overflow_rolls_back_and_marks_turn_skipped(
    monkeypatch,
    tmp_path,
):
    first_body = _body_with([
        {"type": "file", "id": "file-1", "name": "first.pdf"},
    ])
    _run_handoff(monkeypatch, tmp_path, first_body, {"file-1": b"first"})
    chat_root = tmp_path / "handoff" / "user" / "user-1" / "chat" / "chat-1"
    manifest_path = chat_root / ".owui-handoff-manifest.json"
    manifest_before = manifest_path.read_bytes()
    prior_attachments = _attachment_files(tmp_path)
    assert len(prior_attachments) == 1
    monkeypatch.setattr(
        hermes_handoff,
        "HANDOFF_MANIFEST_MAX_BYTES",
        len(manifest_before),
    )

    second_body = {
        "chat_id": "chat-1",
        "metadata": {
            "chat_id": "chat-1",
            "user_message": {
                "id": "msg-2",
                "files": [
                    {"type": "file", "id": "file-2", "name": "second.pdf"},
                ],
            },
            "files": [
                {"type": "file", "id": "file-2", "name": "second.pdf"},
            ],
        },
        "messages": [{"role": "user", "id": "msg-2", "content": "second"}],
    }
    result, _calls = _run_handoff(
        monkeypatch,
        tmp_path,
        second_body,
        {"file-2": b"second"},
    )

    assert manifest_path.read_bytes() == manifest_before
    assert _attachment_files(tmp_path) == prior_attachments
    content = result["messages"][-1]["content"]
    assert 'file_id="file-1"' in content
    assert "file-2" not in content
    assert '<materials_note skipped="1">' in content


def test_manifest_lock_timeout_rolls_back_and_marks_turn_skipped(
    monkeypatch,
    tmp_path,
):
    body = _body_with([
        {"type": "file", "id": "file-1", "name": "report.pdf"},
    ])

    def timeout(*_args):
        raise hermes_handoff.FileLockTimeout("manifest lock")

    monkeypatch.setattr(hermes_handoff, "_merge_manifest_entries", timeout)
    result, _calls = _run_handoff(
        monkeypatch,
        tmp_path,
        body,
        {"file-1": b"payload"},
    )

    content = result["messages"][-1]["content"]
    assert "<files>" not in content
    assert '<materials_note skipped="1">' in content
    assert _attachment_files(tmp_path) == []


def test_manifest_merge_runs_off_event_loop_thread(monkeypatch, tmp_path):
    import threading

    body = _body_with([
        {"type": "file", "id": "file-1", "name": "report.pdf"},
    ])
    event_loop_thread = threading.get_ident()

    def observe_thread(_user_id, _chat_id, entries):
        assert threading.get_ident() != event_loop_thread
        return entries

    monkeypatch.setattr(
        hermes_handoff,
        "_merge_manifest_entries",
        observe_thread,
    )
    result, _calls = _run_handoff(
        monkeypatch,
        tmp_path,
        body,
        {"file-1": b"payload"},
    )

    assert 'file_id="file-1"' in result["messages"][-1]["content"]


def test_followup_manifest_rejects_validly_signed_cross_user_entry(
    monkeypatch,
    tmp_path,
):
    handoff_dir = tmp_path / "handoff"
    monkeypatch.setattr(hermes_handoff, "HANDOFF_DIR", str(handoff_dir))
    monkeypatch.setattr(hermes_handoff, "HANDOFF_SIGNING_KEY", "test-secret")
    caller_chat_dir = (
        handoff_dir
        / "user"
        / "user-2"
        / "chat"
        / "chat-1"
    )
    caller_file = caller_chat_dir / "message" / "msg-1" / "report.pdf"
    caller_file.parent.mkdir(parents=True)
    safe_original = str(caller_file)
    caller_file.write_bytes(b"%PDF safe payload")
    safe_sha256 = hashlib.sha256(b"%PDF safe payload").hexdigest()
    safe_sig = hermes_handoff._sign_handoff_entry(
        "user-2",
        "chat-1",
        safe_original,
        file_id="file-1",
        sha256=safe_sha256,
    )
    victim_file = (
        handoff_dir
        / "user"
        / "victim-user"
        / "chat"
        / "chat-1"
        / "message"
        / "victim-msg"
        / "private.pdf"
    )
    victim_file.parent.mkdir(parents=True)
    victim_file.write_bytes(b"%PDF victim payload")
    victim_sha256 = hashlib.sha256(b"%PDF victim payload").hexdigest()
    victim_sig = hermes_handoff._sign_handoff_entry(
        "victim-user",
        "chat-1",
        str(victim_file),
        file_id="victim-file",
        sha256=victim_sha256,
    )
    manifest = {
        "version": 1,
        "scope": {"user_id": "user-2", "chat_id": "chat-1"},
        "entries": [
            {
                "original": safe_original,
                "file_id": "file-1",
                "sha256": safe_sha256,
                "sig": safe_sig,
            },
            {
                "original": str(victim_file),
                "file_id": "victim-file",
                "sha256": victim_sha256,
                "sig": victim_sig,
            },
        ],
    }
    caller_chat_dir.mkdir(parents=True, exist_ok=True)
    (caller_chat_dir / ".owui-handoff-manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    followup_body = {
        "chat_id": "chat-1",
        "metadata": {
            "chat_id": "chat-1",
            "user_message": {"id": "msg-2", "content": "continue"},
            "files": [],
        },
        "messages": [{"role": "user", "id": "msg-2", "content": "continue"}],
    }
    followup, calls = _run_handoff(
        monkeypatch,
        tmp_path,
        followup_body,
        {},
        user_id="user-2",
    )

    assert calls == []
    content = followup["messages"][-1]["content"]
    assert safe_original in content
    assert str(victim_file) not in content
    assert "victim-file" not in content


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
    assert len(_attachment_files(tmp_path)) == 1  # only the delivered file
    assert '<materials_note skipped="1">' in content
    assert "1 uploaded file(s) could not be read or delivered" in content
    assert content.count("<materials_note") == 1


def test_all_skipped_without_history_injects_only_materials_note(monkeypatch, tmp_path):
    skipped = [
        {"type": "file", "id": "skip-1", "name": "a.pdf"},
        {"type": "file", "id": "skip-2", "name": "b.pdf"},
    ]

    result, _calls = _run_handoff(
        monkeypatch,
        tmp_path,
        _body_with(skipped),
        {"skip-1": b"", "skip-2": b""},
    )

    content = result["messages"][0]["content"]
    assert "<files>" not in content
    assert "<file " not in content
    assert '<materials_note skipped="2">' in content
    assert _attachment_files(tmp_path) == []


def test_all_skipped_preserves_prior_manifest_and_adds_materials_note(
    monkeypatch,
    tmp_path,
    caplog,
):
    seed = _body_with([
        {'type': 'file', 'id': 'existing-1', 'name': 'existing.pdf'},
    ])
    _run_handoff(monkeypatch, tmp_path, seed, {'existing-1': b'existing bytes'})
    caplog.clear()
    caplog.set_level("INFO", logger=hermes_handoff.__name__)

    skipped = [
        {'type': 'file', 'id': 'skip-1', 'name': 'a.pdf'},
        {'type': 'file', 'id': 'skip-2', 'name': 'b.pdf'},
    ]
    body = {
        'chat_id': 'chat-1',
        'metadata': {
            'chat_id': 'chat-1',
            'user_message': {'id': 'msg-2', 'files': skipped},
            'files': skipped,
        },
        'messages': [{'role': 'user', 'id': 'msg-2', 'content': 'more materials'}],
    }
    result, _calls = _run_handoff(monkeypatch, tmp_path, body, {'skip-1': b'', 'skip-2': b''})
    content = result['messages'][0]['content']
    assert content.count('<files>') == 1
    assert content.count('<file ') == 1
    assert 'file_id="existing-1"' in content
    assert '<materials_note skipped="2">' in content
    assert '2 uploaded file(s) could not be read or delivered' in content
    assert len(_attachment_files(tmp_path)) == 1
    assert result['metadata']['files'] == []  # current-turn file items still stripped from RAG
    assert any(
        "Path B done" in record.message
        and "0 file(s) handed off" in record.message
        for record in caplog.records
    )


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


# --- M1 / 2026-07-28: metadata['files'] is explicitly None -------------------
# Live 8083 raised `TypeError: 'NoneType' object is not iterable` at
# hermes_handoff.py:636 21 times between 2026-07-24 and 2026-07-28. The caller
# in utils/middleware.py catches it, logs
# "run_hermes_handoff raised unexpectedly ... no file context injected
# (embedding suppressed)" and forwards the turn with NO file context at all, so
# every previously uploaded file silently disappears mid-chat. `.get(key,
# default)` only substitutes the default when the key is ABSENT; an explicit
# null value flows straight through.


def test_none_metadata_files_followup_still_reinjects_manifest(monkeypatch, tmp_path):
    # Turn 1: a genuine upload, so the chat manifest is populated.
    upload_files = [{"type": "file", "id": "file-1", "name": "report.pdf"}]
    upload_body = {
        "chat_id": "chat-1",
        "metadata": {
            "chat_id": "chat-1",
            "user_message": {"id": "msg-1", "files": upload_files},
            "files": list(upload_files),
        },
        "messages": [{"role": "user", "id": "msg-1", "content": "audit this"}],
    }
    result, _calls = _run_handoff(
        monkeypatch, tmp_path, upload_body, {"file-1": b"%PDF payload"}
    )
    assert result["messages"][-1]["content"].count("<file ") == 1

    # Turn 2: OWUI sends metadata.files == None (not [] and not absent).
    followup_body = {
        "chat_id": "chat-1",
        "metadata": {
            "chat_id": "chat-1",
            "user_message": {"id": "msg-2", "content": "continue"},
            "files": None,
        },
        "messages": [{"role": "user", "id": "msg-2", "content": "continue"}],
    }
    followup, calls = _run_handoff(monkeypatch, tmp_path, followup_body, {})

    assert calls == []  # nothing re-uploaded
    content = followup["messages"][-1]["content"]
    assert content.count("<file ") == 1  # continuity preserved, not lost
    assert 'file_id="file-1"' in content
    assert followup["metadata"]["files"] == []


def test_none_metadata_files_with_current_turn_upload_still_hands_off(
    monkeypatch, tmp_path
):
    # metadata.files is None while the authoritative current-turn set lives on
    # user_message.files — the upload must still reach /handoff.
    current_files = [{"type": "file", "id": "file-1", "name": "report.pdf"}]
    body = {
        "chat_id": "chat-1",
        "metadata": {
            "chat_id": "chat-1",
            "user_message": {"id": "msg-1", "files": current_files},
            "files": None,
        },
        "messages": [{"role": "user", "id": "msg-1", "content": "audit this"}],
    }
    result, calls = _run_handoff(monkeypatch, tmp_path, body, {"file-1": b"%PDF x"})

    assert calls == [("file-1", "report.pdf")]
    assert result["messages"][-1]["content"].count("<file ") == 1
    assert result["metadata"]["files"] == []


def test_none_metadata_files_legacy_no_user_message_is_a_noop(monkeypatch, tmp_path):
    # Legacy callers have no user_message, so _current_turn_file_items falls
    # back to metadata.files. None must degrade to "no files", never crash.
    body = {
        "chat_id": "chat-1",
        "metadata": {"chat_id": "chat-1", "files": None},
        "messages": [{"role": "user", "id": "msg-1", "content": "plain question"}],
    }
    original = body["messages"][-1]["content"]
    result, calls = _run_handoff(monkeypatch, tmp_path, body, {})

    assert calls == []
    assert result["messages"][-1]["content"] == original
    assert result["metadata"]["files"] == []
