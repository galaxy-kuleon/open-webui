"""
E2E behavioral test for work_dir support across the full pipeline.

Tests the integrated flow: SkillMeta -> opencode utils -> builtin.py wiring.
Does NOT start actual opencode subprocesses — tests the data transformations
and contracts between the three modules.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 1. SkillMeta backward compatibility + work_dir field
# ---------------------------------------------------------------------------


class TestSkillMetaWorkDir:
    """Verify SkillMeta handles work_dir correctly, including old DB rows."""

    def test_work_dir_defaults_to_none(self):
        """Old DB rows with no work_dir key should deserialize with None."""
        from open_webui.models.skills import SkillMeta

        meta = SkillMeta(tags=["test"], type="agent_skill", disk_path="/some/path")
        assert meta.work_dir is None

    def test_work_dir_can_be_set(self):
        from open_webui.models.skills import SkillMeta

        meta = SkillMeta(
            tags=["test"],
            type="agent_skill",
            work_dir="/tmp/my-project",
        )
        assert meta.work_dir == "/tmp/my-project"

    def test_work_dir_from_dict_missing_key(self):
        """Simulate deserializing a DB row that predates the work_dir field."""
        from open_webui.models.skills import SkillMeta

        raw = {"tags": ["old"], "type": "agent_skill", "disk_path": "/old/path"}
        meta = SkillMeta(**raw)
        assert meta.work_dir is None
        assert meta.disk_path == "/old/path"

    def test_work_dir_from_dict_with_key(self):
        from open_webui.models.skills import SkillMeta

        raw = {
            "tags": [],
            "type": "agent_skill",
            "work_dir": "/projects/foo",
        }
        meta = SkillMeta(**raw)
        assert meta.work_dir == "/projects/foo"

    def test_work_dir_empty_string_is_falsy(self):
        """Empty string work_dir should be treated as not set."""
        from open_webui.models.skills import SkillMeta

        meta = SkillMeta(tags=[], type="agent_skill", work_dir="")
        # Empty string is falsy in Python, so dual-mode branching in builtin.py
        # will correctly fall through to sandbox mode.
        assert not meta.work_dir

    def test_model_dump_includes_work_dir(self):
        from open_webui.models.skills import SkillMeta

        meta = SkillMeta(type="agent_skill", work_dir="/tmp/proj")
        dumped = meta.model_dump()
        assert "work_dir" in dumped
        assert dumped["work_dir"] == "/tmp/proj"


# ---------------------------------------------------------------------------
# 2. collect_output_files_from_work_dir — real filesystem behavior
# ---------------------------------------------------------------------------


class TestCollectOutputFilesFromWorkDir:
    """Verify collect_output_files_from_work_dir with real files/dirs."""

    def test_no_output_dir_returns_empty(self):
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_workdir_") as td:
            # No output/ subdirectory
            result = collect_output_files_from_work_dir(td)
            assert result == []

    def test_empty_output_dir_returns_empty(self):
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_workdir_") as td:
            os.makedirs(os.path.join(td, "output"))
            result = collect_output_files_from_work_dir(td)
            assert result == []

    def test_collects_files_in_output(self):
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_workdir_") as td:
            out_dir = os.path.join(td, "output")
            os.makedirs(out_dir)

            # Create test files
            Path(out_dir, "result.txt").write_text("hello")
            Path(out_dir, "report.pdf").write_bytes(b"%PDF-fake")

            result = collect_output_files_from_work_dir(td)
            names = sorted(p.name for p in result)
            assert names == ["report.pdf", "result.txt"]

    def test_collects_nested_files(self):
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_workdir_") as td:
            nested = os.path.join(td, "output", "subdir")
            os.makedirs(nested)
            Path(nested, "deep.txt").write_text("deep content")

            result = collect_output_files_from_work_dir(td)
            assert len(result) == 1
            assert result[0].name == "deep.txt"

    def test_skips_hidden_files(self):
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_workdir_") as td:
            out_dir = os.path.join(td, "output")
            os.makedirs(out_dir)
            Path(out_dir, ".hidden").write_text("secret")
            Path(out_dir, "visible.txt").write_text("ok")

            result = collect_output_files_from_work_dir(td)
            names = [p.name for p in result]
            assert "visible.txt" in names
            assert ".hidden" not in names

    def test_skips_hidden_subdirectory_files(self):
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_workdir_") as td:
            hidden_dir = os.path.join(td, "output", ".git")
            os.makedirs(hidden_dir)
            Path(hidden_dir, "config").write_text("gitconfig")
            Path(os.path.join(td, "output"), "visible.txt").write_text("ok")

            result = collect_output_files_from_work_dir(td)
            names = [p.name for p in result]
            assert names == ["visible.txt"]

    def test_ignores_files_outside_output(self):
        """Files in work_dir root or other subdirs should NOT be collected."""
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_workdir_") as td:
            out_dir = os.path.join(td, "output")
            os.makedirs(out_dir)
            # File in work_dir root
            Path(td, "README.md").write_text("readme")
            # File in a sibling dir
            os.makedirs(os.path.join(td, "src"))
            Path(td, "src", "main.py").write_text("code")
            # File in output/
            Path(out_dir, "result.txt").write_text("result")

            result = collect_output_files_from_work_dir(td)
            names = [p.name for p in result]
            assert names == ["result.txt"]

    def test_symlink_escape_blocked(self):
        """Symlinks pointing outside output/ should be skipped."""
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_workdir_") as td:
            out_dir = os.path.join(td, "output")
            os.makedirs(out_dir)

            # Create a file outside output/
            outside_file = os.path.join(td, "secret.txt")
            Path(outside_file).write_text("sensitive data")

            # Create a symlink from output/ pointing outside
            link_path = os.path.join(out_dir, "escape_link")
            os.symlink(outside_file, link_path)

            # Also create a legitimate file
            Path(out_dir, "legit.txt").write_text("ok")

            result = collect_output_files_from_work_dir(td)
            names = [p.name for p in result]
            assert "legit.txt" in names
            assert "escape_link" not in names


# ---------------------------------------------------------------------------
# 3. sync_opencode_config_to_dir — idempotency + permission format
# ---------------------------------------------------------------------------


class TestSyncOpencodeConfigToDir:
    """Verify sync_opencode_config_to_dir is idempotent and uses correct permission."""

    def test_creates_config_in_target_dir(self):
        from open_webui.utils.opencode import sync_opencode_config_to_dir

        with tempfile.TemporaryDirectory(prefix="test_sync_") as td:
            sync_opencode_config_to_dir(td)
            config_path = Path(td) / "opencode.json"
            assert config_path.exists()

            config = json.loads(config_path.read_text())
            assert config["permission"] == "allow"
            assert "$schema" in config

    def test_idempotent_run_twice(self):
        """Running sync twice should produce identical output."""
        from open_webui.utils.opencode import sync_opencode_config_to_dir

        with tempfile.TemporaryDirectory(prefix="test_sync_") as td:
            sync_opencode_config_to_dir(td)
            first_content = (Path(td) / "opencode.json").read_text()

            sync_opencode_config_to_dir(td)
            second_content = (Path(td) / "opencode.json").read_text()

            assert first_content == second_content

    def test_preserves_existing_non_provider_fields(self):
        """Existing fields in project config that are not provider/permission should survive."""
        from open_webui.utils.opencode import sync_opencode_config_to_dir

        with tempfile.TemporaryDirectory(prefix="test_sync_") as td:
            # Pre-populate with custom field
            config_path = Path(td) / "opencode.json"
            config_path.write_text(json.dumps({
                "custom_field": "custom_value",
                "permission": "deny",  # should be overwritten to "allow"
            }))

            sync_opencode_config_to_dir(td)
            config = json.loads(config_path.read_text())

            assert config["permission"] == "allow"  # overwritten
            assert config["custom_field"] == "custom_value"  # preserved

    def test_permission_format_matches_generate(self):
        """Both config producers must use the same 'allow' string format."""
        from open_webui.utils.opencode import (
            generate_opencode_config,
            sync_opencode_config_to_dir,
        )

        # generate_opencode_config writes to ~/.config/opencode/opencode.json
        # We test that the permission value is the string "allow"
        config = generate_opencode_config(
            openai_api_base_urls=["http://localhost:11434"],
            openai_api_keys=["test-key"],
            ollama_base_urls=[],
        )
        assert config["permission"] == "allow"
        assert isinstance(config["permission"], str)

        # sync_opencode_config_to_dir writes permission: "allow"
        with tempfile.TemporaryDirectory(prefix="test_perm_") as td:
            sync_opencode_config_to_dir(td)
            project_config = json.loads((Path(td) / "opencode.json").read_text())
            assert project_config["permission"] == "allow"
            assert isinstance(project_config["permission"], str)

        # Both produce the exact same format
        assert config["permission"] == project_config["permission"]


# ---------------------------------------------------------------------------
# 4. generate_opencode_config — provider mapping
# ---------------------------------------------------------------------------


class TestGenerateOpencodeConfig:
    """Verify generate_opencode_config produces valid provider entries."""

    def test_openai_provider_mapping(self):
        from open_webui.utils.opencode import generate_opencode_config

        config = generate_opencode_config(
            openai_api_base_urls=["http://api.openai.com/v1"],
            openai_api_keys=["sk-test"],
            ollama_base_urls=[],
        )
        assert "openai" in config["provider"]
        p = config["provider"]["openai"]
        assert p["baseURL"] == "http://api.openai.com/v1"
        assert p["apiKey"] == "sk-test"

    def test_ollama_provider_adds_v1(self):
        from open_webui.utils.opencode import generate_opencode_config

        config = generate_opencode_config(
            openai_api_base_urls=[],
            openai_api_keys=[],
            ollama_base_urls=["http://localhost:11434"],
        )
        assert "ollama" in config["provider"]
        assert config["provider"]["ollama"]["baseURL"] == "http://localhost:11434/v1"

    def test_empty_urls_skipped(self):
        from open_webui.utils.opencode import generate_opencode_config

        config = generate_opencode_config(
            openai_api_base_urls=["", "http://valid.com/v1"],
            openai_api_keys=["", "key2"],
            ollama_base_urls=[""],
        )
        # First openai entry (empty URL) should be skipped
        assert "openai" not in config["provider"]  # empty, skipped
        assert "openai_1" in config["provider"]
        # Empty ollama should be skipped
        assert "ollama" not in config["provider"]

    def test_prefix_id_used_as_provider_id(self):
        from open_webui.utils.opencode import generate_opencode_config

        config = generate_opencode_config(
            openai_api_base_urls=["http://lmstudio.local/v1"],
            openai_api_keys=["lm-key"],
            ollama_base_urls=[],
            openai_api_configs={"0": {"prefix_id": "lmstudio"}},
        )
        assert "lmstudio" in config["provider"]
        assert "openai" not in config["provider"]


# ---------------------------------------------------------------------------
# 5. Dual-mode branching — contract verification
# ---------------------------------------------------------------------------


class TestDualModeBranching:
    """Verify the data contracts between builtin.py and opencode.py."""

    def test_meta_model_dump_has_work_dir_key(self):
        """builtin.py does meta.get('work_dir') — verify the key exists in dump."""
        from open_webui.models.skills import SkillMeta

        meta = SkillMeta(type="agent_skill", work_dir="/tmp/proj")
        dumped = meta.model_dump()
        assert "work_dir" in dumped
        assert dumped.get("work_dir") == "/tmp/proj"

    def test_meta_without_work_dir_falls_to_disk_path(self):
        """When work_dir is None, meta.get('work_dir') is falsy."""
        from open_webui.models.skills import SkillMeta

        meta = SkillMeta(type="agent_skill", disk_path="/skills/my-skill")
        dumped = meta.model_dump()
        assert not dumped.get("work_dir")  # None is falsy
        assert dumped.get("disk_path") == "/skills/my-skill"

    def test_collect_output_files_signature_matches_caller(self):
        """Verify function signatures accept the types that callers provide."""
        import inspect
        from open_webui.utils.opencode import (
            collect_output_files,
            collect_output_files_from_work_dir,
            sync_opencode_config_to_dir,
        )

        # collect_output_files(sandbox_dir: str, start_time: float)
        sig = inspect.signature(collect_output_files)
        params = list(sig.parameters.keys())
        assert params == ["sandbox_dir", "start_time"]

        # collect_output_files_from_work_dir(work_dir: str)
        sig = inspect.signature(collect_output_files_from_work_dir)
        params = list(sig.parameters.keys())
        assert params == ["work_dir"]

        # sync_opencode_config_to_dir(target_dir: str)
        sig = inspect.signature(sync_opencode_config_to_dir)
        params = list(sig.parameters.keys())
        assert params == ["target_dir"]

    def test_all_opencode_imports_resolve(self):
        """All functions imported in builtin.py's run_agent_skill must exist."""
        from open_webui.utils.opencode import (
            generate_opencode_config,
            sync_opencode_config_to_dir,
            setup_sandbox,
            run_opencode,
            collect_output_files,
            collect_output_files_from_work_dir,
            cleanup_sandbox,
            _get_user_semaphore,
        )
        # If any import fails, this test fails — verifying the import list matches
        assert callable(generate_opencode_config)
        assert callable(sync_opencode_config_to_dir)
        assert callable(setup_sandbox)
        assert callable(run_opencode)
        assert callable(collect_output_files)
        assert callable(collect_output_files_from_work_dir)
        assert callable(cleanup_sandbox)
        assert callable(_get_user_semaphore)


# ---------------------------------------------------------------------------
# 6. Sandbox cleanup sentinel pattern
# ---------------------------------------------------------------------------


class TestSandboxCleanupSentinel:
    """Verify the sentinel cleanup pattern works correctly."""

    def test_cleanup_sandbox_removes_dir(self):
        from open_webui.utils.opencode import cleanup_sandbox

        with tempfile.TemporaryDirectory(prefix="test_cleanup_") as td:
            # Create a subdirectory that mimics a sandbox
            sandbox = os.path.join(td, "sandbox")
            os.makedirs(sandbox)
            Path(sandbox, "file.txt").write_text("content")

            assert os.path.isdir(sandbox)
            cleanup_sandbox(sandbox)
            assert not os.path.isdir(sandbox)

    def test_cleanup_sandbox_noop_for_missing_dir(self):
        """cleanup_sandbox should not raise if dir doesn't exist."""
        from open_webui.utils.opencode import cleanup_sandbox

        # Should not raise
        cleanup_sandbox("/tmp/nonexistent-sandbox-dir-" + "x" * 30)


# ---------------------------------------------------------------------------
# 7. Error return format consistency
# ---------------------------------------------------------------------------


class TestErrorReturnConsistency:
    """Verify error returns match json.dumps({'error': ...}) format."""

    def test_collect_from_nonexistent_work_dir(self):
        """collect_output_files_from_work_dir with nonexistent dir returns empty list."""
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        # Note: the function checks output_dir.is_dir() and returns []
        result = collect_output_files_from_work_dir("/nonexistent/path/work")
        assert result == []

    def test_sync_to_unwritable_dir_logs_warning(self):
        """sync_opencode_config_to_dir to a bad path should not raise."""
        from open_webui.utils.opencode import sync_opencode_config_to_dir

        # Using a path that likely doesn't exist — function should handle gracefully
        # It catches OSError and logs a warning
        try:
            sync_opencode_config_to_dir("/nonexistent/readonly/path")
        except Exception:
            pytest.fail("sync_opencode_config_to_dir should not raise on write failure")
