"""Conftest for pipes tests.

Sets environment variables needed for open_webui module imports
before any test modules are loaded. This prevents SQLite
"unable to open database file" errors from peewee migration init.
"""

import os
import tempfile

_test_data_dir = tempfile.mkdtemp(prefix='owui_pipes_test_')

os.environ.setdefault('DATA_DIR', _test_data_dir)
os.environ.setdefault('DATABASE_URL', f'sqlite:///{_test_data_dir}/test.db')
os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret-key-for-pipes-tests')
# Satisfy the mandatory hermes_api_key valve so Pipe() can be instantiated in tests.
# Individual valve-validation tests call Valves() directly and don't rely on this.
os.environ.setdefault('HERMES_API_KEY', 'test-hermes-key-for-pipes-tests')
