"""Tests for open_webui.utils.builtin_pipes.ensure_builtin_pipes().

Covers 4 scenarios:
  (a) No users exist  → returns early, no Functions calls
  (b) First user exists, pipe not in DB  → insert_new_function + activate
  (c) Pipe exists with matching hash  → no DB write
  (d) Pipe exists with different hash  → update_function_by_id

All mocks go through pytest monkeypatch so they are automatically restored.
No shared mutable state between tests.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers — fake domain objects
# ---------------------------------------------------------------------------

_FAKE_USER = SimpleNamespace(id='user-001')
_FAKE_CONTENT = 'print("hermes")\n'

# SHA-256 of _FAKE_CONTENT, first 16 chars — mirrors builtin_pipes logic
import hashlib

_FAKE_HASH = hashlib.sha256(_FAKE_CONTENT.encode()).hexdigest()[:16]
_DIFFERENT_HASH = 'deadbeef12345678'


def _fake_existing(content_hash: str):
    """Return a fake FunctionModel with the given content hash in meta."""
    return SimpleNamespace(
        id='hermes_agent',
        meta=SimpleNamespace(
            model_dump=lambda: {
                'manifest': {'content_hash': content_hash, 'builtin': True}
            }
        ),
    )


# ---------------------------------------------------------------------------
# Fixture: patch the pipe source path so no real file I/O occurs
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_pipe_source(monkeypatch):
    """Replace _BUILTIN_PIPES[0].path with a fake Path that returns _FAKE_CONTENT."""
    import open_webui.utils.builtin_pipes as bp_mod

    fake_path = MagicMock()
    fake_path.is_file.return_value = True
    fake_path.read_text.return_value = _FAKE_CONTENT

    original_pipes = bp_mod._BUILTIN_PIPES
    patched = [
        {**original_pipes[0], 'path': fake_path},
    ]
    monkeypatch.setattr(bp_mod, '_BUILTIN_PIPES', patched)
    return fake_path


# ---------------------------------------------------------------------------
# Fixture: patch Users and Functions on their modules
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_users(monkeypatch):
    """Return a namespace of mock callables for Users methods."""
    from open_webui.models import users as users_mod

    mocks = {
        'get_super_admin_user': MagicMock(return_value=None),
        'get_first_user': MagicMock(return_value=None),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(users_mod.Users, name, staticmethod(mock))
    return mocks


@pytest.fixture()
def mock_functions(monkeypatch):
    """Return a namespace of mock callables for Functions methods."""
    from open_webui.models import functions as fn_mod

    mocks = {
        'get_function_by_id': MagicMock(return_value=None),
        'insert_new_function': MagicMock(return_value=SimpleNamespace(id='hermes_agent')),
        'update_function_by_id': MagicMock(return_value=None),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(fn_mod.Functions, name, staticmethod(mock))
    return mocks


# ---------------------------------------------------------------------------
# Scenario (a): no users → returns early, no Functions calls
# ---------------------------------------------------------------------------

def test_no_users_returns_early(mock_users, mock_functions):
    """When both get_super_admin_user and get_first_user return None,
    ensure_builtin_pipes must return without touching Functions at all."""
    mock_users['get_super_admin_user'].return_value = None
    mock_users['get_first_user'].return_value = None

    from open_webui.utils.builtin_pipes import ensure_builtin_pipes

    ensure_builtin_pipes()

    mock_functions['get_function_by_id'].assert_not_called()
    mock_functions['insert_new_function'].assert_not_called()
    mock_functions['update_function_by_id'].assert_not_called()


# ---------------------------------------------------------------------------
# Scenario (b): first user exists, pipe not in DB → insert + activate
# ---------------------------------------------------------------------------

def test_new_pipe_is_inserted_and_activated(mock_users, mock_functions):
    """When no super-admin exists but a first user does, and the pipe is absent
    from the DB, ensure_builtin_pipes must call insert_new_function then
    update_function_by_id({is_active: True})."""
    mock_users['get_super_admin_user'].return_value = None
    mock_users['get_first_user'].return_value = _FAKE_USER
    mock_functions['get_function_by_id'].return_value = None  # pipe absent

    from open_webui.utils.builtin_pipes import ensure_builtin_pipes

    ensure_builtin_pipes()

    # insert called once with correct positional args
    assert mock_functions['insert_new_function'].call_count == 1
    call_args = mock_functions['insert_new_function'].call_args
    pos = call_args[0]
    assert pos[0] == 'user-001'  # admin.id
    assert pos[1] == 'pipe'  # type

    form = pos[2]  # FunctionForm
    assert form.id == 'hermes_agent'
    assert form.name == 'Hermes Agent'
    assert form.content == _FAKE_CONTENT
    # content_hash embedded in manifest
    assert form.meta.manifest['content_hash'] == _FAKE_HASH

    # activate call: update_function_by_id('hermes_agent', {'is_active': True})
    activate_calls = [
        c for c in mock_functions['update_function_by_id'].call_args_list
        if c[0][1] == {'is_active': True}
    ]
    assert len(activate_calls) == 1, (
        'Expected exactly one update_function_by_id call with {is_active: True}'
    )


# ---------------------------------------------------------------------------
# Scenario (c): pipe exists with matching hash → no DB write
# ---------------------------------------------------------------------------

def test_matching_hash_skips_db_write(mock_users, mock_functions):
    """When the pipe exists in DB with the same content hash as the file,
    ensure_builtin_pipes must not call insert or update."""
    mock_users['get_super_admin_user'].return_value = _FAKE_USER
    mock_functions['get_function_by_id'].return_value = _fake_existing(_FAKE_HASH)

    from open_webui.utils.builtin_pipes import ensure_builtin_pipes

    ensure_builtin_pipes()

    mock_functions['insert_new_function'].assert_not_called()
    mock_functions['update_function_by_id'].assert_not_called()


# ---------------------------------------------------------------------------
# Scenario (d): pipe exists with different hash → update_function_by_id
# ---------------------------------------------------------------------------

def test_changed_hash_triggers_update(mock_users, mock_functions):
    """When the pipe exists in DB but its stored hash differs from the file,
    ensure_builtin_pipes must call update_function_by_id with new content."""
    mock_users['get_super_admin_user'].return_value = _FAKE_USER
    mock_functions['get_function_by_id'].return_value = _fake_existing(_DIFFERENT_HASH)

    from open_webui.utils.builtin_pipes import ensure_builtin_pipes

    ensure_builtin_pipes()

    mock_functions['insert_new_function'].assert_not_called()

    assert mock_functions['update_function_by_id'].call_count == 1
    call_args = mock_functions['update_function_by_id'].call_args
    pipe_id = call_args[0][0]
    updated = call_args[0][1]

    assert pipe_id == 'hermes_agent'
    assert updated['content'] == _FAKE_CONTENT
    assert updated['meta']['manifest']['content_hash'] == _FAKE_HASH
    assert updated['meta']['manifest']['builtin'] is True
