"""F-4a acceptance tests for the bounded drain reader in kg1.py.

Tests the _read_bounded_line helper directly.  The full app environment
(fastapi, langchain, etc.) is available in this repo's venv, so we import
kg1.py directly without stubs.
"""

import io
import logging
import time

import pytest

from open_webui.retrieval.loaders import kg1  # noqa: E402
from open_webui.retrieval.loaders.kg1 import _DRAIN_LINE_CAP, _read_bounded_line  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KG1_LOGGER = 'open_webui.retrieval.loaders.kg1'


def _stream(data: bytes) -> io.BytesIO:
    """Wrap bytes in a BytesIO that mimics a binary pipe stream."""
    return io.BytesIO(data)


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestReadBoundedLineHappyPath:
    def test_short_line_returned_verbatim(self):
        """A line well within cap is returned unchanged."""
        line = _read_bounded_line(_stream(b'hello world\n'), identity='test')
        assert line == 'hello world'

    def test_short_line_without_trailing_newline(self):
        """EOF-terminated line (no \\n) is returned as-is."""
        line = _read_bounded_line(_stream(b'no newline'), identity='test')
        assert line == 'no newline'

    def test_eof_returns_none(self):
        """Empty stream yields None immediately."""
        result = _read_bounded_line(_stream(b''), identity='test')
        assert result is None

    def test_empty_line_newline_only(self):
        """A line containing only \\n should return an empty string."""
        result = _read_bounded_line(_stream(b'\n'), identity='test')
        assert result == ''

    def test_line_at_exactly_cap_boundary(self):
        """A line of exactly _DRAIN_LINE_CAP bytes + newline is NOT truncated."""
        data = b'B' * _DRAIN_LINE_CAP + b'\n'
        result = _read_bounded_line(_stream(data), identity='test')
        assert result is not None
        assert 'drain-truncated' not in result
        assert len(result) == _DRAIN_LINE_CAP

    def test_sequential_short_lines(self):
        """Multiple short lines are each returned correctly."""
        stream = _stream(b'line1\nline2\nline3\n')
        lines = []
        while True:
            line = _read_bounded_line(stream, identity='test')
            if line is None:
                break
            lines.append(line)
        assert lines == ['line1', 'line2', 'line3']

    def test_no_marker_for_short_line(self):
        """Marker string is absent from a short line."""
        result = _read_bounded_line(_stream(b'short\n'), identity='test')
        assert 'drain-truncated' not in (result or '')


# ---------------------------------------------------------------------------
# Truncation / overflow tests
# ---------------------------------------------------------------------------


class TestReadBoundedLineTruncation:
    def test_10mib_line_wall_clock_under_1s(self):
        """Spec acceptance: 10 MiB single-line input must complete in < 1 s."""
        big = b'A' * (10 * 1024 * 1024)
        stream = _stream(big + b'\n')
        t0 = time.monotonic()
        _read_bounded_line(stream, identity='test')
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f'Took {elapsed:.3f}s — exceeded 1-second wall-clock limit'

    def test_10mib_line_body_at_most_64kib(self):
        """Body (pre-marker) must be ≤ 64 KiB."""
        big = b'A' * (10 * 1024 * 1024)
        result = _read_bounded_line(_stream(big + b'\n'), identity='test')
        marker = '...[drain-truncated: '
        idx = result.find(marker)
        assert idx != -1, f'Marker not found; result prefix: {result[:200]!r}'
        body = result[:idx]
        assert len(body.encode()) <= _DRAIN_LINE_CAP, f'Body {len(body.encode())} bytes exceeds cap {_DRAIN_LINE_CAP}'

    def test_10mib_line_marker_correct_byte_count(self):
        """Marker must contain the exact omitted-byte count."""
        size = 10 * 1024 * 1024
        big = b'A' * size
        result = _read_bounded_line(_stream(big + b'\n'), identity='test')
        expected_omitted = size - _DRAIN_LINE_CAP
        marker = f'...[drain-truncated: {expected_omitted} bytes omitted]'
        assert marker in result, f'Expected marker {marker!r} not found in result suffix: {result[-200:]!r}'

    def test_10mib_line_warn_emitted_exactly_once(self, caplog):
        """A single WARN log is emitted for the truncated line."""
        big = b'A' * (10 * 1024 * 1024)
        with caplog.at_level(logging.WARNING, logger=_KG1_LOGGER):
            _read_bounded_line(_stream(big + b'\n'), identity='test-drain')
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warns) == 1, f'Expected 1 WARNING, got {len(warns)}: {warns}'

    def test_warn_contains_identity(self, caplog):
        """The WARN log must reference the caller's identity string."""
        big = b'X' * (10 * 1024 * 1024)
        with caplog.at_level(logging.WARNING, logger=_KG1_LOGGER):
            _read_bounded_line(_stream(big + b'\n'), identity='soffice:stderr')
        assert any('soffice:stderr' in r.getMessage() for r in caplog.records)

    def test_warn_not_emitted_for_short_line(self, caplog):
        """No WARN must be emitted for a line well within the cap."""
        with caplog.at_level(logging.WARNING, logger=_KG1_LOGGER):
            _read_bounded_line(_stream(b'short line\n'), identity='test')
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warns) == 0

    def test_truncation_does_not_raise(self):
        """Overflow path must never raise — not ValueError, not OSError, nothing."""
        big = b'Z' * (10 * 1024 * 1024)
        try:
            _read_bounded_line(_stream(big + b'\n'), identity='test')
        except Exception as exc:
            pytest.fail(f'_read_bounded_line raised unexpectedly: {exc!r}')

    def test_marker_string_format(self):
        """Marker must match the exact ASCII format specified."""
        big = b'M' * (10 * 1024 * 1024)
        result = _read_bounded_line(_stream(big + b'\n'), identity='test')
        assert result is not None
        assert '...[drain-truncated: ' in result
        assert 'bytes omitted]' in result


# ---------------------------------------------------------------------------
# Drain byte-bleed regression tests (T2 retry)
# ---------------------------------------------------------------------------


class TestDrainByteBleedRegression:
    """Guard against the drain loop consuming bytes past the newline boundary.

    The original drain used stream.read(_DISCARD_CHUNK) which can consume bytes
    belonging to the next line when a chunk straddles a newline.  The fix uses
    stream.readline(_DISCARD_CHUNK) which stops at the first '\\n' (inclusive)
    or after limit bytes — whichever comes first.
    """

    def test_drain_preserves_following_line_after_truncation(self):
        """Line after an oversized line must be fully readable without byte loss.

        Arrange: oversized line (_DRAIN_LINE_CAP + 100 bytes) followed by
        'PREFIX:IMPORTANT\\n'.  If the drain bleeds bytes past the newline,
        the second _read_bounded_line call will return a truncated or
        wrong 'PREFIX:IMPORTANT' string.
        """
        stream = io.BytesIO(b'A' * (_DRAIN_LINE_CAP + 100) + b'\n' + b'PREFIX:IMPORTANT\n')

        r1 = _read_bounded_line(stream, identity='l1')
        r2 = _read_bounded_line(stream, identity='l2')

        assert r1 is not None
        assert '...[drain-truncated:' in r1  # line 1 truncated with marker
        assert r2 is not None
        assert r2.rstrip('\n') == 'PREFIX:IMPORTANT'  # line 2 fully preserved

    def test_drain_two_consecutive_oversized_lines(self):
        """Two back-to-back oversized lines are each independently truncated.

        If the drain bleeds bytes from line 1 into line 2's territory, line 2
        will either be missing its drain-truncated marker or contain 'X' chars
        (from line 1's overflow bleeding into line 2).
        """
        over1 = b'X' * (_DRAIN_LINE_CAP + 50) + b'\n'
        over2 = b'Y' * (_DRAIN_LINE_CAP + 75) + b'\n'
        stream = io.BytesIO(over1 + over2)

        r1 = _read_bounded_line(stream, identity='l1')
        r2 = _read_bounded_line(stream, identity='l2')
        r3 = _read_bounded_line(stream, identity='l3')  # EOF

        assert r1 is not None
        assert '...[drain-truncated:' in r1
        assert r2 is not None
        assert '...[drain-truncated:' in r2
        # No 'X' characters should appear in r2 — those belong to line 1's body/overflow
        body2 = r2[: r2.find('...[drain-truncated:')]
        assert 'X' not in body2, f'Byte bleed detected: line 1 Xs appear in line 2 body: {body2[:80]!r}'
        assert r3 is None  # EOF reached
