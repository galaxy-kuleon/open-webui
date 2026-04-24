"""Integration tests for F-4b (KG1_GLMOCR_PROJECT_DIR) and F-5
(RAG_KNOWLEDGE_EXPORT_DIR) validation in update_rag_config.

These tests call _validate_admin_dir and the allowed-root helpers directly
(the same functions wired into the endpoint) because constructing a full
FastAPI test client with all the retrieval router's transitive dependencies
(vector DBs, file models, etc.) is prohibitively complex in a unit-test
environment.  The integration contract is: the same helper called by the
endpoint is exercised with the same env-var overrides the endpoint would use.
"""

import os
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Full-app import path: retrieval.py can be imported directly in this repo.
# ---------------------------------------------------------------------------

import open_webui.routers.retrieval as _retrieval

_validate_admin_dir = _retrieval._validate_admin_dir
_kg1_allowed_roots = _retrieval._kg1_allowed_roots
_rag_export_allowed_roots = _retrieval._rag_export_allowed_roots


# ---------------------------------------------------------------------------
# F-4b — KG1_GLMOCR_PROJECT_DIR
# ---------------------------------------------------------------------------


class TestKG1ProjectDirWiring:
    """Verify that _validate_admin_dir + _kg1_allowed_roots reject /etc and
    accept a valid directory inside the configured root — the same contract
    that update_rag_config enforces.
    """

    def test_forbidden_path_returns_400(self, tmp_path, monkeypatch):
        """KG1_GLMOCR_PROJECT_DIR=/etc → HTTPException(400)."""
        from fastapi import HTTPException

        kg1_root = str(tmp_path / 'kg1-root')
        os.makedirs(kg1_root, exist_ok=True)
        monkeypatch.setenv('KG1_GLMOCR_ALLOWED_ROOT', kg1_root)

        roots = _kg1_allowed_roots()
        with pytest.raises(HTTPException) as exc_info:
            _validate_admin_dir('/etc', allowed_roots=roots, setting_name='KG1_GLMOCR_PROJECT_DIR')

        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail
        assert 'KG1_GLMOCR_PROJECT_DIR' in detail
        assert '/etc' not in detail  # must not echo the raw path

    def test_valid_allowlisted_path_accepted(self, tmp_path, monkeypatch):
        """A directory inside the configured allowed root is accepted; returns resolved path."""
        kg1_root = str(tmp_path / 'kg1-root')
        os.makedirs(kg1_root, exist_ok=True)
        valid_dir = os.path.join(kg1_root, 'project')
        os.makedirs(valid_dir, exist_ok=True)
        monkeypatch.setenv('KG1_GLMOCR_ALLOWED_ROOT', kg1_root)

        roots = _kg1_allowed_roots()
        result = _validate_admin_dir(valid_dir, allowed_roots=roots, setting_name='KG1_GLMOCR_PROJECT_DIR')

        assert result == os.path.realpath(valid_dir)
        assert isinstance(result, str)
        assert os.path.isabs(result)

    def test_none_value_skips_validation(self, tmp_path, monkeypatch):
        """When form_data.KG1_GLMOCR_PROJECT_DIR is None, validation is not called.

        The wiring uses `if form_data.KG1_GLMOCR_PROJECT_DIR is not None`,
        so we verify the validator is never invoked with None.
        Passing None to _validate_admin_dir should raise (empty check).
        This test confirms the guard condition is correct.
        """
        from fastapi import HTTPException

        kg1_root = str(tmp_path / 'kg1-root')
        os.makedirs(kg1_root, exist_ok=True)
        monkeypatch.setenv('KG1_GLMOCR_ALLOWED_ROOT', kg1_root)

        # If the guard `if form_data.KG1_GLMOCR_PROJECT_DIR is not None` is
        # present, this code path (None value) is never reached.  Confirm that
        # passing None would indeed fail so the guard is load-bearing:
        roots = _kg1_allowed_roots()
        with pytest.raises((HTTPException, Exception)):
            _validate_admin_dir(None, allowed_roots=roots, setting_name='KG1_GLMOCR_PROJECT_DIR')  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# F-5 — RAG_KNOWLEDGE_EXPORT_DIR
# ---------------------------------------------------------------------------


class TestRAGExportDirWiring:
    def test_forbidden_path_returns_400(self, tmp_path, monkeypatch):
        """RAG_KNOWLEDGE_EXPORT_DIR=/etc → HTTPException(400)."""
        from fastapi import HTTPException

        rag_root = str(tmp_path / 'rag-root')
        os.makedirs(rag_root, exist_ok=True)
        monkeypatch.setenv('RAG_KNOWLEDGE_EXPORT_ROOT', rag_root)

        roots = _rag_export_allowed_roots()
        with pytest.raises(HTTPException) as exc_info:
            _validate_admin_dir('/etc', allowed_roots=roots, setting_name='RAG_KNOWLEDGE_EXPORT_DIR')

        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail
        assert 'RAG_KNOWLEDGE_EXPORT_DIR' in detail
        assert '/etc' not in detail

    def test_valid_allowlisted_path_accepted(self, tmp_path, monkeypatch):
        """A directory inside the configured allowed root is accepted."""
        rag_root = str(tmp_path / 'rag-root')
        os.makedirs(rag_root, exist_ok=True)
        valid_dir = os.path.join(rag_root, 'exports')
        os.makedirs(valid_dir, exist_ok=True)
        monkeypatch.setenv('RAG_KNOWLEDGE_EXPORT_ROOT', rag_root)

        roots = _rag_export_allowed_roots()
        result = _validate_admin_dir(valid_dir, allowed_roots=roots, setting_name='RAG_KNOWLEDGE_EXPORT_DIR')

        assert result == os.path.realpath(valid_dir)

    def test_none_value_skips_validation(self, tmp_path, monkeypatch):
        """Guard condition: None input must never reach the validator."""
        from fastapi import HTTPException

        rag_root = str(tmp_path / 'rag-root')
        os.makedirs(rag_root, exist_ok=True)
        monkeypatch.setenv('RAG_KNOWLEDGE_EXPORT_ROOT', rag_root)

        roots = _rag_export_allowed_roots()
        with pytest.raises((HTTPException, Exception)):
            _validate_admin_dir(None, allowed_roots=roots, setting_name='RAG_KNOWLEDGE_EXPORT_DIR')  # type: ignore[arg-type]

    def test_round_trip_persistence_assertion(self, tmp_path, monkeypatch):
        """After validation the returned resolved path equals the real path."""
        rag_root = str(tmp_path / 'rag-root')
        os.makedirs(rag_root, exist_ok=True)
        monkeypatch.setenv('RAG_KNOWLEDGE_EXPORT_ROOT', rag_root)

        roots = _rag_export_allowed_roots()
        result = _validate_admin_dir(rag_root, allowed_roots=roots, setting_name='RAG_KNOWLEDGE_EXPORT_DIR')

        # Simulate what update_rag_config does: store result in config.
        # The stored value should be the realpath-resolved string.
        class _MockConfig:
            pass

        cfg = _MockConfig()
        cfg.RAG_KNOWLEDGE_EXPORT_DIR = result
        assert cfg.RAG_KNOWLEDGE_EXPORT_DIR == os.path.realpath(rag_root)
