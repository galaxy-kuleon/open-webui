import ast
import asyncio
import inspect
from types import SimpleNamespace

from open_webui.skiprag import hermes_handoff


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
