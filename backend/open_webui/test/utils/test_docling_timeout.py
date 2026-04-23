"""
Behavioral tests for docling.py — specifically the timeout kill path.

The critical bug: process.kill() only kills the top-level process, not its children.
When docling spawns subprocesses, those children hold pipes open and process.wait() hangs.

Fix: start_new_session=True + os.killpg() to kill entire process group on timeout.
"""

import asyncio
import os
import stat
import tempfile
import time
from pathlib import Path

import pytest

from open_webui.utils.docling import (
    DoclingTimeoutError,
    convert_to_markdown,
)


def _make_fake_docling_with_children(script_path: Path) -> None:
    """Create a shell script that simulates docling spawning child processes.

    The script:
    1. Spawns a background child (sleep infinity)
    2. The parent also sleeps (so it won't exit on its own within the timeout)

    This reproduces the exact bug: killing only the parent leaves the child alive,
    holding stdout/stderr pipes open, causing process.wait() to block forever.
    """
    script_path.write_text(
        "#!/bin/bash\n"
        "# Simulate docling spawning a child process\n"
        "sleep 3600 &\n"  # child — will survive parent kill if not in same group
        "sleep 3600\n"    # parent — blocks forever
    )
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)


def _make_fake_docling_fast_exit(script_path: Path) -> None:
    """Create a shell script that simulates docling completing normally.

    Produces a .md file in the --output directory and exits cleanly.
    """
    # The script parses --output from argv and creates a .md file
    script_path.write_text(
        '#!/bin/bash\n'
        '# Parse --output dir from args\n'
        'OUTPUT_DIR=""\n'
        'INPUT_FILE=""\n'
        'while [[ $# -gt 0 ]]; do\n'
        '    case "$1" in\n'
        '        --output) OUTPUT_DIR="$2"; shift 2;;\n'
        '        --*) shift 2;;\n'  # skip other flags
        '        *) INPUT_FILE="$1"; shift;;\n'
        '    esac\n'
        'done\n'
        '# Get basename without extension\n'
        'BASENAME=$(basename "$INPUT_FILE" | sed "s/\\.[^.]*$//")\n'
        'echo "# Converted document" > "$OUTPUT_DIR/$BASENAME.md"\n'
        'echo "Content here" >> "$OUTPUT_DIR/$BASENAME.md"\n'
    )
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def tmp_workspace(tmp_path: Path):
    """Create a workspace with a fake PDF file and fake docling binaries."""
    # Create a dummy .pdf file (just needs to exist for validation)
    fake_pdf = tmp_path / "test_doc.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    # Create the "hangs with children" binary
    hanging_binary = tmp_path / "docling_hanging"
    _make_fake_docling_with_children(hanging_binary)

    # Create the "fast exit" binary
    fast_binary = tmp_path / "docling_fast"
    _make_fake_docling_fast_exit(fast_binary)

    return {
        "pdf": fake_pdf,
        "hanging_binary": str(hanging_binary),
        "fast_binary": str(fast_binary),
    }


@pytest.mark.asyncio
async def test_timeout_kills_process_group_and_does_not_hang(tmp_workspace):
    """THE critical behavioral test: timeout must complete promptly, not hang.

    Before the fix (process.kill() only), this test would hang indefinitely
    because the child process holds the pipes open.

    After the fix (os.killpg + start_new_session), the entire process group
    is killed and process.wait() returns immediately.
    """
    start = time.monotonic()

    with pytest.raises(DoclingTimeoutError, match="timed out after 1s"):
        await convert_to_markdown(
            tmp_workspace["pdf"],
            timeout_seconds=1,
            docling_binary=tmp_workspace["hanging_binary"],
        )

    elapsed = time.monotonic() - start

    # The timeout is 1 second. If the kill path works correctly,
    # total elapsed should be ~1s (the timeout) + a tiny bit of overhead.
    # If it hangs (the old bug), it would block for 3600s.
    # We allow up to 5 seconds to account for slow CI, but the point is:
    # it must NOT hang indefinitely.
    assert elapsed < 5.0, (
        f"Timeout kill path took {elapsed:.1f}s — likely hanging on process.wait() "
        f"due to surviving child processes holding pipes open"
    )


@pytest.mark.asyncio
async def test_timeout_leaves_no_orphan_processes(tmp_workspace):
    """After timeout, no orphan processes from the docling binary should remain."""
    # Get count of our sleep processes before
    before_pids = _find_sleep_pids()

    with pytest.raises(DoclingTimeoutError):
        await convert_to_markdown(
            tmp_workspace["pdf"],
            timeout_seconds=1,
            docling_binary=tmp_workspace["hanging_binary"],
        )

    # Brief grace period for process cleanup
    await asyncio.sleep(0.5)

    after_pids = _find_sleep_pids()
    # The new pids that appeared during our test should be gone
    leaked = after_pids - before_pids
    assert len(leaked) == 0, (
        f"Orphan processes leaked after timeout kill: PIDs {leaked}"
    )


def _find_sleep_pids() -> set[int]:
    """Find PIDs of 'sleep 3600' processes (our test sentinel)."""
    import subprocess

    result = subprocess.run(
        ["pgrep", "-f", "sleep 3600"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return set()
    return {int(pid) for pid in result.stdout.strip().split("\n") if pid.strip()}


@pytest.mark.asyncio
async def test_normal_conversion_still_works(tmp_workspace):
    """The happy path (no timeout) must still produce correct results."""
    result = await convert_to_markdown(
        tmp_workspace["pdf"],
        timeout_seconds=10,
        docling_binary=tmp_workspace["fast_binary"],
    )

    assert "# Converted document" in result
    assert "Content here" in result
