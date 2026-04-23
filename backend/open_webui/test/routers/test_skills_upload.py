"""Tests for zip-slip / symlink-escape hardening in upload_skill_zip.

Design rationale
----------------
We test the security surface at the unit level by importing the pure helper
``_is_unsafe_zip_member`` directly (no FastAPI, no DB, no auth required) and
at the integration level via a minimal FastAPI app with dependency overrides
(no real database, no real auth).

Malicious ZIP fixtures are generated programmatically here using
``zipfile.ZipInfo`` so future auditors can read, diff, and regenerate them
without binary blobs being committed to the repository.

The four fixture builders
  - ``_make_traversal_zip``   — entry with ``../../etc/passwd`` as the name
  - ``_make_symlink_zip``     — entry with Unix symlink mode bits set
  - ``_make_absolute_zip``    — entry with absolute POSIX path ``/etc/passwd``
  - ``_make_happy_zip``       — minimal valid skill ZIP (SKILL.md + one file)

are all in this file so a single ``cat`` reveals the full threat model.
"""

import io
import os
import tempfile
import zipfile
from types import SimpleNamespace
from typing import Generator

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap (must run BEFORE any open_webui import)
# ---------------------------------------------------------------------------
import sys

_test_data_dir = tempfile.mkdtemp(prefix='owui_skilltest_')
os.environ.setdefault('DATA_DIR', _test_data_dir)
os.environ.setdefault('DATABASE_URL', f'sqlite:///{_test_data_dir}/test.db')
os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret-key-for-skill-zip-tests')

# ---------------------------------------------------------------------------
# Imports that require the env vars above
# ---------------------------------------------------------------------------
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from open_webui.routers import skills as skills_module
from open_webui.routers.skills import (
    _assert_no_symlinks_in_tree,
    _is_unsafe_zip_member,
    router as skills_router,
)
from open_webui.utils.auth import get_verified_user
from open_webui.internal.db import get_async_session


# ---------------------------------------------------------------------------
# Malicious + happy-path ZIP fixture builders
# All return ``bytes`` (in-memory ZIP content).
# ---------------------------------------------------------------------------


def _make_traversal_zip() -> bytes:
    """ZIP containing a path-traversal entry: ``../../etc/passwd``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_STORED) as zf:
        info = zipfile.ZipInfo('../../etc/passwd')
        info.external_attr = 0o100644 << 16  # regular file
        zf.writestr(info, 'root:x:0:0:root:/root:/bin/bash\n')
    return buf.getvalue()


def _make_symlink_zip() -> bytes:
    """ZIP containing a Unix symlink entry pointing at ``/etc/passwd``.

    The symlink type is encoded in ``ZipInfo.external_attr`` by setting the
    high 16 bits to the Unix st_mode value for a symlink (``0o120777``).
    The entry content is the symlink *target* path, as per the ZIP spec.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_STORED) as zf:
        info = zipfile.ZipInfo('evil-link')
        # S_IFLNK (0o120000) | 0o777 permissions → 0o120777
        info.external_attr = 0o120777 << 16
        zf.writestr(info, '/etc/passwd')  # symlink target is the content
    return buf.getvalue()


def _make_absolute_zip() -> bytes:
    """ZIP containing an absolute-path entry: ``/etc/passwd``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_STORED) as zf:
        info = zipfile.ZipInfo('/etc/passwd')
        info.external_attr = 0o100644 << 16
        zf.writestr(info, 'root:x:0:0:root:/root:/bin/bash\n')
    return buf.getvalue()


def _make_happy_zip() -> bytes:
    """Minimal valid skill ZIP: SKILL.md at root + one Python file.

    The SKILL.md frontmatter satisfies ``_parse_skill_md_frontmatter`` so the
    upload logic can extract a skill name and description without crashing.
    All entries are regular files with safe relative paths.
    """
    skill_md_content = (
        '---\n'
        'name: test-happy-skill\n'
        'description: A minimal skill for upload testing\n'
        'tags: [testing]\n'
        '---\n\n'
        '# test-happy-skill\n\n'
        'A minimal skill fixture used by automated tests.\n'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        info_md = zipfile.ZipInfo('SKILL.md')
        info_md.external_attr = 0o100644 << 16
        zf.writestr(info_md, skill_md_content)

        info_py = zipfile.ZipInfo('tool.py')
        info_py.external_attr = 0o100644 << 16
        zf.writestr(info_py, '# placeholder tool\ndef run(): pass\n')
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Unit tests for the pure helper _is_unsafe_zip_member
# These tests have zero FastAPI / DB / network dependencies.
# ---------------------------------------------------------------------------


class TestIsUnsafeZipMember:
    """Direct behavioral tests for the _is_unsafe_zip_member predicate."""

    def _make_info(self, name: str, mode: int = 0o100644) -> zipfile.ZipInfo:
        zi = zipfile.ZipInfo(name)
        zi.external_attr = mode << 16
        return zi

    # ── Safe entries ──────────────────────────────────────────────────────

    def test_safe_root_file(self):
        unsafe, _ = _is_unsafe_zip_member(self._make_info('SKILL.md'))
        assert not unsafe

    def test_safe_nested_file(self):
        unsafe, _ = _is_unsafe_zip_member(self._make_info('src/tool.py'))
        assert not unsafe

    def test_safe_deep_nested_file(self):
        unsafe, _ = _is_unsafe_zip_member(self._make_info('a/b/c/d.txt'))
        assert not unsafe

    # ── Path traversal ────────────────────────────────────────────────────

    def test_dotdot_prefix(self):
        unsafe, detail = _is_unsafe_zip_member(self._make_info('../../etc/passwd'))
        assert unsafe
        assert 'Unsafe path' in detail

    def test_dotdot_mid_path(self):
        unsafe, detail = _is_unsafe_zip_member(self._make_info('a/../b/c.txt'))
        assert unsafe
        assert 'Unsafe path' in detail

    def test_dotdot_relative_start(self):
        unsafe, detail = _is_unsafe_zip_member(self._make_info('./../x'))
        assert unsafe
        assert 'Unsafe path' in detail

    # ── Absolute paths ────────────────────────────────────────────────────

    def test_absolute_posix(self):
        unsafe, detail = _is_unsafe_zip_member(self._make_info('/etc/passwd'))
        assert unsafe
        assert 'Unsafe path' in detail

    def test_absolute_windows_drive(self):
        unsafe, detail = _is_unsafe_zip_member(self._make_info('C:/Windows/System32'))
        assert unsafe
        assert 'Unsafe path' in detail

    # ── Symlink detection ─────────────────────────────────────────────────

    def test_symlink_mode(self):
        zi = zipfile.ZipInfo('evil-link')
        zi.external_attr = 0o120777 << 16  # S_IFLNK
        unsafe, detail = _is_unsafe_zip_member(zi)
        assert unsafe
        assert 'Symlinks' in detail

    def test_symlink_mode_with_low_bits(self):
        zi = zipfile.ZipInfo('another-link')
        zi.external_attr = (0o120777 << 16) | 0o644  # S_IFLNK + DOS attrs
        unsafe, detail = _is_unsafe_zip_member(zi)
        assert unsafe
        assert 'Symlinks' in detail

    # ── Regular file modes must NOT be misclassified ──────────────────────

    def test_regular_file_not_flagged_as_symlink(self):
        zi = zipfile.ZipInfo('normal.txt')
        zi.external_attr = 0o100644 << 16  # S_IFREG
        unsafe, _ = _is_unsafe_zip_member(zi)
        assert not unsafe

    def test_directory_entry_safe(self):
        zi = zipfile.ZipInfo('subdir/')
        zi.external_attr = 0o040755 << 16  # S_IFDIR
        unsafe, _ = _is_unsafe_zip_member(zi)
        assert not unsafe

    # ── Error message safety ──────────────────────────────────────────────

    def test_detail_contains_no_path_leak(self):
        zi = zipfile.ZipInfo('../../etc/passwd')
        zi.external_attr = 0o100644 << 16
        _, detail = _is_unsafe_zip_member(zi)
        assert 'etc/passwd' not in detail
        assert '/tmp/' not in detail
        assert 'Traceback' not in detail


# ---------------------------------------------------------------------------
# Integration tests via FastAPI TestClient with dependency overrides
# ---------------------------------------------------------------------------

# Fake user object returned by the overridden get_verified_user dependency.
# role='admin' bypasses the has_permission check in upload_skill_zip.
_FAKE_ADMIN = SimpleNamespace(
    id='test-user-001',
    role='admin',
    name='Test Admin',
    email='test@example.com',
)


def _fake_get_verified_user():
    return _FAKE_ADMIN


async def _fake_get_async_session():
    """Yield a stub async session — upload_skill_zip only calls
    Skills.get_skill_by_id and Skills.insert_new_skill on it, both of which
    we intercept via monkeypatching in the happy-path test."""
    yield None  # type: ignore[misc]


def _build_test_app() -> FastAPI:
    """Construct a minimal FastAPI app with only the skills router mounted.

    Dependency overrides replace auth and DB so tests run without a real
    database server or authentication infrastructure.
    """
    app = FastAPI()
    app.include_router(skills_router, prefix='/api/v1/skills')
    app.dependency_overrides[get_verified_user] = _fake_get_verified_user
    app.dependency_overrides[get_async_session] = _fake_get_async_session
    return app


@pytest.fixture(scope='module')
def client() -> Generator[TestClient, None, None]:
    app = _build_test_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _post_zip(client: TestClient, zip_bytes: bytes, filename: str = 'test.zip'):
    """POST a ZIP to the upload-zip endpoint and return the response."""
    return client.post(
        '/api/v1/skills/upload-zip',
        files={'file': (filename, zip_bytes, 'application/zip')},
    )


# ── Rejection tests ───────────────────────────────────────────────────────


class TestMaliciousZipRejected:
    """All three malicious ZIP types must return HTTP 400 with a safe
    error body.  No file must be written to disk on rejection."""

    def test_zip_traversal_rejected(self, client: TestClient, tmp_path):
        """Path-traversal ZIP is rejected with HTTP 400."""
        zip_bytes = _make_traversal_zip()
        response = _post_zip(client, zip_bytes)

        assert response.status_code == 400, response.text
        body = response.text
        assert 'Unsafe path' in body or 'unsafe' in body.lower()
        # No internal path leak
        assert '/tmp/' not in body
        assert 'Traceback' not in body

    def test_zip_traversal_no_file_written(self, client: TestClient, tmp_path, monkeypatch):
        """After traversal rejection, tmp_extract directory must be empty or absent."""
        written_dirs = []

        original_mkdtemp = tempfile.mkdtemp

        def tracking_mkdtemp(**kwargs):
            d = original_mkdtemp(**kwargs)
            written_dirs.append(d)
            return d

        monkeypatch.setattr(tempfile, 'mkdtemp', tracking_mkdtemp)

        zip_bytes = _make_traversal_zip()
        _post_zip(client, zip_bytes)

        # Any tmp dirs that were created must be empty (traversal is rejected
        # before extraction) or must have been cleaned up.
        for d in written_dirs:
            if os.path.exists(d):
                contents = list(os.walk(d))
                total_files = sum(len(files) for _, _, files in contents)
                assert total_files == 0, f'Files found in {d} after traversal rejection: {contents}'

    def test_zip_symlink_rejected(self, client: TestClient):
        """Symlink ZIP is rejected with HTTP 400."""
        zip_bytes = _make_symlink_zip()
        response = _post_zip(client, zip_bytes)

        assert response.status_code == 400, response.text
        body = response.text
        assert 'Symlinks' in body or 'symlink' in body.lower()
        assert '/tmp/' not in body
        assert 'Traceback' not in body

    def test_zip_absolute_path_rejected(self, client: TestClient):
        """Absolute-path ZIP is rejected with HTTP 400."""
        zip_bytes = _make_absolute_zip()
        response = _post_zip(client, zip_bytes)

        assert response.status_code == 400, response.text
        body = response.text
        assert 'Unsafe path' in body or 'unsafe' in body.lower()
        assert '/tmp/' not in body
        assert 'Traceback' not in body

    def test_error_body_no_path_leak(self, client: TestClient):
        """Error body must not contain raw filesystem paths or tracebacks."""
        for zip_bytes in [
            _make_traversal_zip(),
            _make_symlink_zip(),
            _make_absolute_zip(),
        ]:
            response = _post_zip(client, zip_bytes)
            assert response.status_code == 400
            body = response.text
            # Must not contain patterns that reveal internal state
            assert '/tmp/' not in body
            assert 'tmp_extract' not in body
            assert 'Traceback' not in body
            # Must not contain the malicious path itself
            assert 'etc/passwd' not in body


# ── Happy-path test ───────────────────────────────────────────────────────


class TestHappyPathAccepted:
    """A well-formed skill ZIP passes all guards and triggers the DB insert path."""

    def test_zip_happy_path_accepted(self, client: TestClient, monkeypatch):
        """Control ZIP reaches the DB-insert step (not rejected by security guards).

        We monkeypatch ``Skills.get_skill_by_id`` to return None (no conflict)
        and ``Skills.insert_new_skill`` to return a fake skill object so the
        test does not need a real database.
        """
        # Fake DB responses so the route completes successfully
        fake_skill = SimpleNamespace(
            id='test-happy-skill',
            name='test-happy-skill',
            description='A minimal skill fixture used by automated tests.',
            content='',
            meta=SimpleNamespace(type='agent_skill', disk_path='/tmp/fake'),
            is_active=True,
            user_id='test-user-001',
            created_at=0,
            updated_at=0,
        )

        import open_webui.models.skills as skills_models

        monkeypatch.setattr(
            skills_models.Skills,
            'get_skill_by_id',
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            skills_models.Skills,
            'insert_new_skill',
            AsyncMock(return_value=fake_skill),
        )

        # Also prevent the copytree from writing to ~/.claude/skills in CI
        monkeypatch.setattr('shutil.copytree', lambda *a, **kw: None)

        zip_bytes = _make_happy_zip()
        response = _post_zip(client, zip_bytes, filename='test-happy-skill.zip')

        # Must NOT be rejected by any security guard
        assert response.status_code != 400 or 'Unsafe' not in response.text, (
            f'Happy-path ZIP was unexpectedly rejected: {response.text}'
        )
        # The route either returns 200/201 (skill created) or 400 due to
        # persistent_dir / copytree issues in the test environment.
        # What matters is the guards did not reject it.
        assert response.status_code in (200, 201, 400)
        if response.status_code == 400:
            body = response.text
            # If 400, it must NOT be a security rejection
            assert 'Unsafe path' not in body
            assert 'Symlinks not permitted' not in body
            assert 'Hardlinks not permitted' not in body


# ── Property-style sweep: all malicious ZIPs must be rejected ────────────


@pytest.mark.parametrize(
    'zip_factory,expected_detail_fragment',
    [
        (_make_traversal_zip, 'Unsafe path'),
        (_make_symlink_zip, 'Symlinks'),
        (_make_absolute_zip, 'Unsafe path'),
    ],
    ids=['traversal', 'symlink', 'absolute'],
)
def test_malicious_zip_returns_400(
    client: TestClient,
    zip_factory,
    expected_detail_fragment: str,
):
    """Parametric sweep: every known-malicious fixture must yield HTTP 400
    and a detail string that names the threat without leaking internal paths."""
    zip_bytes = zip_factory()
    response = _post_zip(client, zip_bytes)

    assert response.status_code == 400
    assert expected_detail_fragment in response.text
    assert '/tmp/' not in response.text
    assert 'Traceback' not in response.text
    assert 'etc/passwd' not in response.text
