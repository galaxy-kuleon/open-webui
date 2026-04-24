"""Regression test for hermes_agent._resolve_file_paths.

Guards against the bug where Files.get_file_by_id was called without
``await``, making ``record`` a coroutine object rather than a FileModel,
which then raised ``AttributeError: 'coroutine' object has no attribute 'path'``.

RED (before fix): sync call returns a coroutine → AttributeError → empty string.
GREEN (after fix): async call returns the mock FileModel → path is used.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _make_fake_file_record(path: str, filename: str) -> SimpleNamespace:
    """Return a minimal file-record-like object with .path and .filename."""
    return SimpleNamespace(path=path, filename=filename)


@pytest.mark.asyncio
async def test_resolve_file_paths_returns_abs_path():
    """_resolve_file_paths must return a non-empty string containing the
    resolved absolute path when Files.get_file_by_id is properly awaited."""
    from open_webui.pipes.hermes_agent import Pipe

    pipe = Pipe()
    fake_record = _make_fake_file_record(
        path="uploads/abc123.pdf", filename="report.pdf"
    )

    with (
        patch(
            "open_webui.models.files.Files.get_file_by_id",
            new=AsyncMock(return_value=fake_record),
        ),
        patch(
            "open_webui.config.UPLOAD_DIR",
            "/fake/upload/root",
        ),
        patch(
            "os.path.exists",
            return_value=True,
        ),
    ):
        result = await pipe._resolve_file_paths([{"id": "abc123"}])

    assert result, "_resolve_file_paths must return a non-empty string when the file exists"
    assert "abc123.pdf" in result, (
        "Result must contain the file path component; got: " + repr(result)
    )
    assert "report.pdf" in result, (
        "Result must contain the filename; got: " + repr(result)
    )


@pytest.mark.asyncio
async def test_resolve_file_paths_empty_when_no_files():
    """_resolve_file_paths with an empty list must return an empty string."""
    from open_webui.pipes.hermes_agent import Pipe

    pipe = Pipe()
    result = await pipe._resolve_file_paths([])
    assert result == "", f"Expected empty string, got: {result!r}"


@pytest.mark.asyncio
async def test_resolve_file_paths_skips_missing_id():
    """File dicts without an 'id' key are silently skipped."""
    from open_webui.pipes.hermes_agent import Pipe

    pipe = Pipe()
    result = await pipe._resolve_file_paths([{"name": "no-id-here.pdf"}])
    assert result == "", f"Expected empty string for entry missing 'id', got: {result!r}"
