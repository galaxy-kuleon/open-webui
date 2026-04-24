"""
Conftest for hermes tests.

Sets environment variables needed for open_webui module imports
before any test modules are loaded. This allows importing models
and utils without a running database server.
"""

import os
import tempfile

# Create a temporary directory for test data BEFORE any open_webui imports.
# This prevents SQLite "unable to open database file" errors that occur
# when the models module initializes its peewee migration history table.
_test_data_dir = tempfile.mkdtemp(prefix="owui_hermes_test_")

os.environ.setdefault("DATA_DIR", _test_data_dir)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_test_data_dir}/test.db")
os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret-key-for-hermes-tests")
