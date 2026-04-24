"""Unit tests for _validate_admin_dir in routers/retrieval.py (F-4b, F-5).

Imports the helper directly by extracting it from the module's global namespace
after we stub out the heavy transitive dependencies.  All tests operate on
real temporary filesystem paths — no mocking of os.* calls.
"""

import os
import sys
import tempfile

import pytest

# Import the helpers from retrieval.py at collection time.
# The heavy stubs above must be set before this import runs.
import open_webui.routers.retrieval as _retrieval_mod  # noqa: E402

_validate_admin_dir = _retrieval_mod._validate_admin_dir
_kg1_allowed_roots = _retrieval_mod._kg1_allowed_roots
_rag_export_allowed_roots = _retrieval_mod._rag_export_allowed_roots


def _get_helper():
    """Return the three helpers (already imported above)."""
    return _validate_admin_dir, _kg1_allowed_roots, _rag_export_allowed_roots


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def allowed_root(tmp_path):
    """A real temporary directory that acts as the allowed root."""
    root = tmp_path / 'allowed'
    root.mkdir()
    return str(root)


@pytest.fixture()
def validate(allowed_root):
    """Partially-applied _validate_admin_dir bound to a single allowed root."""
    fn, _, _ = _get_helper()
    from functools import partial

    return partial(fn, allowed_roots=[allowed_root], setting_name='TEST_SETTING')


# ---------------------------------------------------------------------------
# Import smoke test
# ---------------------------------------------------------------------------


class TestImport:
    def test_functions_exist(self):
        fn, kg1_roots, rag_roots = _get_helper()
        assert callable(fn)
        assert callable(kg1_roots)
        assert callable(rag_roots)


# ---------------------------------------------------------------------------
# Input sanitisation — rules 1 and 2
# ---------------------------------------------------------------------------


class TestInputSanitisation:
    def test_empty_string_rejected(self, validate):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            validate('')
        assert exc_info.value.status_code == 400
        assert 'value is empty' in exc_info.value.detail

    def test_whitespace_only_rejected(self, validate):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            validate('   ')
        assert exc_info.value.status_code == 400
        assert 'value is empty' in exc_info.value.detail

    def test_null_byte_rejected(self, validate):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            validate('/tmp/foo\x00bar')
        assert exc_info.value.status_code == 400
        assert 'invalid path' in exc_info.value.detail

    def test_traversal_segment_rejected(self, validate):
        """A '..' component is caught before realpath, as defense-in-depth."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            validate('/tmp/ok/../evil')
        assert exc_info.value.status_code == 400
        assert 'invalid path' in exc_info.value.detail


# ---------------------------------------------------------------------------
# Containment check — rule 4
# ---------------------------------------------------------------------------


class TestContainmentCheck:
    def test_path_outside_allowed_root_rejected(self, validate):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            validate('/etc')
        assert exc_info.value.status_code == 400
        assert 'outside allowed roots' in exc_info.value.detail

    def test_prefix_near_miss_rejected(self, validate, allowed_root, tmp_path):
        """'/tmp/ok-evil' must NOT match allowed root '/tmp/ok'."""
        from fastapi import HTTPException

        # Build a directory whose name starts with the root name but continues.
        near_miss = allowed_root + '-evil'
        os.makedirs(near_miss, exist_ok=True)
        try:
            with pytest.raises(HTTPException) as exc_info:
                validate(near_miss)
            assert exc_info.value.status_code == 400
            assert 'outside allowed roots' in exc_info.value.detail
        finally:
            os.rmdir(near_miss)

    def test_symlink_escape_inside_allowed_root_rejected(self, validate, allowed_root, tmp_path):
        """A symlink inside the allowed root pointing outside is rejected post-realpath."""
        from fastapi import HTTPException

        # Create the symlink target outside the allowed root
        outside = tmp_path / 'outside'
        outside.mkdir()
        link = os.path.join(allowed_root, 'link')
        os.symlink(str(outside), link)
        try:
            with pytest.raises(HTTPException) as exc_info:
                validate(link)
            assert exc_info.value.status_code == 400
            assert 'outside allowed roots' in exc_info.value.detail
        finally:
            os.unlink(link)

    def test_multi_root_allowed(self, tmp_path):
        """When allowed_roots has multiple entries, any one match is sufficient."""
        fn, _, _ = _get_helper()
        root_a = tmp_path / 'a'
        root_b = tmp_path / 'b'
        root_a.mkdir()
        root_b.mkdir()
        # path inside root_b
        result = fn(str(root_b), allowed_roots=[str(root_a), str(root_b)], setting_name='MULTI')
        assert result == os.path.realpath(str(root_b))


# ---------------------------------------------------------------------------
# Existence and type checks — rules 5 and 6
# ---------------------------------------------------------------------------


class TestExistenceAndType:
    def test_nonexistent_path_inside_root_rejected(self, validate, allowed_root):
        from fastapi import HTTPException

        ghost = os.path.join(allowed_root, 'ghost_dir_that_does_not_exist')
        with pytest.raises(HTTPException) as exc_info:
            validate(ghost)
        assert exc_info.value.status_code == 400
        assert 'does not exist' in exc_info.value.detail

    def test_file_not_directory_rejected(self, validate, allowed_root):
        from fastapi import HTTPException

        f = os.path.join(allowed_root, 'regular_file')
        open(f, 'w').close()
        try:
            with pytest.raises(HTTPException) as exc_info:
                validate(f)
            assert exc_info.value.status_code == 400
            assert 'not a directory' in exc_info.value.detail
        finally:
            os.unlink(f)


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


class TestSuccessPath:
    def test_valid_allowed_path_returns_resolved_string(self, validate, allowed_root):
        result = validate(allowed_root)
        assert isinstance(result, str)
        assert result == os.path.realpath(allowed_root)

    def test_subdirectory_within_allowed_root_accepted(self, validate, allowed_root):
        sub = os.path.join(allowed_root, 'subdir')
        os.makedirs(sub, exist_ok=True)
        try:
            result = validate(sub)
            assert result == os.path.realpath(sub)
        finally:
            os.rmdir(sub)

    def test_returns_absolute_resolved_path(self, validate, allowed_root):
        result = validate(allowed_root)
        assert os.path.isabs(result)


# ---------------------------------------------------------------------------
# Safety: detail strings must not echo the raw value
# ---------------------------------------------------------------------------


class TestDetailSafety:
    @pytest.mark.parametrize(
        'bad_value,reason',
        [
            ('/etc', 'outside allowed roots'),
            ('', 'value is empty'),
            ('/tmp/foo\x00bar', 'invalid path'),
            ('/tmp/ok/../evil', 'invalid path'),
        ],
    )
    def test_detail_does_not_contain_raw_value(self, validate, bad_value, reason):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            validate(bad_value)
        detail = exc_info.value.detail
        if bad_value:
            assert bad_value not in detail, f'detail must not echo raw value. Got: {detail!r}'

    def test_nonexistent_detail_does_not_echo_path(self, validate, allowed_root):
        from fastapi import HTTPException

        ghost = os.path.join(allowed_root, 'ghost_secret_path_xyz')
        with pytest.raises(HTTPException) as exc_info:
            validate(ghost)
        assert ghost not in exc_info.value.detail
