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
# 2b. Three-tier deliverable collection
# ---------------------------------------------------------------------------


class TestCollectOutputTieredDeliverables:
    """Verify the three-tier collection: manifest > final-output > fallback."""

    # -- Tier 1: .deliverables manifest --

    def test_tier1_manifest_returns_only_listed_files(self):
        """When .deliverables manifest exists, return ONLY the listed files."""
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_tier1_") as td:
            out_dir = os.path.join(td, "output")
            workspace = os.path.join(out_dir, "report-atd-workspace")
            os.makedirs(workspace)

            # Deliverable
            Path(workspace, "final-output.docx").write_bytes(b"PK-docx")
            # Intermediates
            dsl_dir = os.path.join(workspace, "dsl")
            os.makedirs(dsl_dir)
            Path(dsl_dir, "page-1.xml").write_text("<page/>")
            Path(workspace, "output.docx").write_bytes(b"PK-intermediate")
            ocr_dir = os.path.join(workspace, "ocr-output")
            os.makedirs(ocr_dir)
            Path(ocr_dir, "page-1.json").write_text('{"text":"hi"}')

            # Manifest — only lists the deliverable
            manifest = os.path.join(out_dir, ".deliverables")
            Path(manifest).write_text(
                "# Deliverables for this skill run\n"
                "report-atd-workspace/final-output.docx\n"
            )

            result = collect_output_files_from_work_dir(td)
            assert len(result) == 1
            assert result[0].name == "final-output.docx"

    def test_tier1_manifest_multiple_entries(self):
        """Manifest can list multiple deliverables."""
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_tier1m_") as td:
            out_dir = os.path.join(td, "output")
            os.makedirs(out_dir)

            Path(out_dir, "report.docx").write_bytes(b"PK-docx")
            Path(out_dir, "summary.pdf").write_bytes(b"%PDF-fake")
            Path(out_dir, "intermediate.xml").write_text("<xml/>")

            Path(out_dir, ".deliverables").write_text(
                "report.docx\nsummary.pdf\n"
            )

            result = collect_output_files_from_work_dir(td)
            names = sorted(p.name for p in result)
            assert names == ["report.docx", "summary.pdf"]

    def test_tier1_manifest_skips_missing_entries(self):
        """Manifest entries pointing to nonexistent files are skipped."""
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_tier1miss_") as td:
            out_dir = os.path.join(td, "output")
            os.makedirs(out_dir)
            Path(out_dir, "exists.docx").write_bytes(b"PK")

            Path(out_dir, ".deliverables").write_text(
                "exists.docx\nno-such-file.pdf\n"
            )

            result = collect_output_files_from_work_dir(td)
            assert len(result) == 1
            assert result[0].name == "exists.docx"

    def test_tier1_manifest_empty_falls_through(self):
        """An empty manifest (or all entries invalid) falls through to Tier 2/3."""
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_tier1empty_") as td:
            out_dir = os.path.join(td, "output")
            os.makedirs(out_dir)
            Path(out_dir, "data.csv").write_text("a,b\n1,2")

            # Manifest with only comments and blanks
            Path(out_dir, ".deliverables").write_text(
                "# nothing here\n\n"
            )

            result = collect_output_files_from_work_dir(td)
            names = [p.name for p in result]
            assert "data.csv" in names  # Fell through to Tier 3

    def test_tier1_manifest_ignores_comments_and_blanks(self):
        """Manifest parser skips comment lines and blank lines."""
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_tier1comments_") as td:
            out_dir = os.path.join(td, "output")
            os.makedirs(out_dir)
            Path(out_dir, "result.docx").write_bytes(b"PK")

            Path(out_dir, ".deliverables").write_text(
                "# comment\n\n  \nresult.docx\n# another comment\n"
            )

            result = collect_output_files_from_work_dir(td)
            assert len(result) == 1
            assert result[0].name == "result.docx"

    # -- Tier 2: final-output.* convention --

    def test_tier2_final_output_convention(self):
        """When no manifest but final-output.* exists, return only those."""
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_tier2_") as td:
            out_dir = os.path.join(td, "output")
            workspace = os.path.join(out_dir, "my-atd-workspace")
            os.makedirs(workspace)

            # Deliverable (final-output.docx)
            Path(workspace, "final-output.docx").write_bytes(b"PK-docx")
            # Intermediates
            Path(workspace, "output.docx").write_bytes(b"PK-intermediate")
            dsl = os.path.join(workspace, "dsl")
            os.makedirs(dsl)
            Path(dsl, "page-1.xml").write_text("<page/>")
            Path(dsl, "page-2.xml").write_text("<page/>")
            img_dir = os.path.join(workspace, "input-images")
            os.makedirs(img_dir)
            Path(img_dir, "page-1.png").write_bytes(b"\x89PNG")
            ocr = os.path.join(workspace, "ocr-output")
            os.makedirs(ocr)
            Path(ocr, "page-1.json").write_text("{}")

            result = collect_output_files_from_work_dir(td)
            assert len(result) == 1
            assert result[0].name == "final-output.docx"

    def test_tier2_final_output_multiple_formats(self):
        """Multiple final-output files (e.g. .docx and .pdf) are all returned."""
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_tier2multi_") as td:
            out_dir = os.path.join(td, "output")
            os.makedirs(out_dir)
            Path(out_dir, "final-output.docx").write_bytes(b"PK-docx")
            Path(out_dir, "final-output.pdf").write_bytes(b"%PDF")
            Path(out_dir, "intermediate.xml").write_text("<xml/>")

            result = collect_output_files_from_work_dir(td)
            names = sorted(p.name for p in result)
            assert names == ["final-output.docx", "final-output.pdf"]

    def test_tier2_final_output_nested_deep(self):
        """final-output.* is found even in deeply nested directories."""
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_tier2deep_") as td:
            deep = os.path.join(td, "output", "a", "b", "c")
            os.makedirs(deep)
            Path(deep, "final-output.xlsx").write_bytes(b"PK-xlsx")
            # Red herring at a sibling level
            Path(os.path.join(td, "output", "a"), "junk.tmp").write_text("tmp")

            result = collect_output_files_from_work_dir(td)
            assert len(result) == 1
            assert result[0].name == "final-output.xlsx"

    # -- Tier 3: Fallback (existing behavior) --

    def test_tier3_fallback_returns_everything(self):
        """When no manifest and no final-output, return all non-hidden files."""
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_tier3_") as td:
            out_dir = os.path.join(td, "output")
            os.makedirs(out_dir)
            Path(out_dir, "report.pdf").write_bytes(b"%PDF")
            Path(out_dir, "data.csv").write_text("x,y\n1,2")
            Path(out_dir, ".hidden").write_text("hidden")

            result = collect_output_files_from_work_dir(td)
            names = sorted(p.name for p in result)
            assert names == ["data.csv", "report.pdf"]

    # -- Tier precedence --

    def test_manifest_takes_precedence_over_final_output(self):
        """Tier 1 (manifest) beats Tier 2 (final-output) when both present."""
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_precedence_") as td:
            out_dir = os.path.join(td, "output")
            os.makedirs(out_dir)
            Path(out_dir, "final-output.docx").write_bytes(b"PK")
            Path(out_dir, "custom-report.pdf").write_bytes(b"%PDF")

            # Manifest lists the custom report, NOT final-output
            Path(out_dir, ".deliverables").write_text("custom-report.pdf\n")

            result = collect_output_files_from_work_dir(td)
            assert len(result) == 1
            assert result[0].name == "custom-report.pdf"

    def test_tier1_manifest_blocks_path_traversal(self):
        """Manifest entries with ../ path traversal are rejected as unsafe."""
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_traversal_") as td:
            out_dir = os.path.join(td, "output")
            os.makedirs(out_dir)

            # Place a file outside output/ that traversal would reach
            Path(td, "secret.txt").write_text("sensitive")

            # Place a legit file inside output/
            Path(out_dir, "safe.docx").write_bytes(b"PK")

            # Manifest tries to escape via path traversal
            Path(out_dir, ".deliverables").write_text(
                "../secret.txt\nsafe.docx\n"
            )

            result = collect_output_files_from_work_dir(td)
            names = [p.name for p in result]
            assert "safe.docx" in names
            assert "secret.txt" not in names

    # -- Tier 2: dedup across multiple workspaces --

    def test_tier2_dedup_two_workspaces_same_filename(self):
        """Two workspaces with final-output.docx: only the newest is returned."""
        import time

        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_dedup2_") as td:
            out_dir = os.path.join(td, "output")
            ws1 = os.path.join(out_dir, "report-atd-workspace")
            ws2 = os.path.join(out_dir, "report-atd-workspace-2")
            os.makedirs(ws1)
            os.makedirs(ws2)

            # Old workspace (stale)
            old_file = Path(ws1, "final-output.docx")
            old_file.write_bytes(b"PK-old-content")
            old_mtime = time.time() - 3600  # 1 hour ago
            os.utime(old_file, (old_mtime, old_mtime))

            # New workspace (current run)
            new_file = Path(ws2, "final-output.docx")
            new_file.write_bytes(b"PK-new-content")
            new_mtime = time.time()
            os.utime(new_file, (new_mtime, new_mtime))

            result = collect_output_files_from_work_dir(td)
            assert len(result) == 1
            assert result[0].name == "final-output.docx"
            assert result[0].read_bytes() == b"PK-new-content"

    def test_tier2_dedup_different_extensions_no_collision(self):
        """Two workspaces with different extensions: both are returned (no dedup)."""
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_dedup_ext_") as td:
            out_dir = os.path.join(td, "output")
            ws1 = os.path.join(out_dir, "ws-1")
            ws2 = os.path.join(out_dir, "ws-2")
            os.makedirs(ws1)
            os.makedirs(ws2)

            Path(ws1, "final-output.docx").write_bytes(b"PK-docx")
            Path(ws2, "final-output.pdf").write_bytes(b"%PDF")

            result = collect_output_files_from_work_dir(td)
            names = sorted(p.name for p in result)
            assert names == ["final-output.docx", "final-output.pdf"]

    def test_tier2_dedup_three_workspaces_same_file(self):
        """Three workspaces with same final-output.docx: only newest returned."""
        import time

        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_dedup3_") as td:
            out_dir = os.path.join(td, "output")
            base_time = time.time()

            for i, age_offset in enumerate([7200, 3600, 0]):
                ws = os.path.join(out_dir, f"workspace-{i + 1}")
                os.makedirs(ws)
                f = Path(ws, "final-output.docx")
                f.write_bytes(f"PK-content-{i + 1}".encode())
                mtime = base_time - age_offset
                os.utime(f, (mtime, mtime))

            result = collect_output_files_from_work_dir(td)
            assert len(result) == 1
            assert result[0].name == "final-output.docx"
            # workspace-3 has age_offset=0 (most recent)
            assert result[0].read_bytes() == b"PK-content-3"

    def test_tier2_dedup_survives_file_deleted_during_stat(self):
        """TOCTOU hardening: if a file vanishes between rglob and dedup stat, no crash."""
        import time
        from unittest.mock import patch

        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_toctou_") as td:
            out_dir = os.path.join(td, "output")
            ws1 = os.path.join(out_dir, "ws-1")
            ws2 = os.path.join(out_dir, "ws-2")
            os.makedirs(ws1)
            os.makedirs(ws2)

            old_file = Path(ws1, "final-output.docx")
            old_file.write_bytes(b"PK-old")
            old_mtime = time.time() - 3600
            os.utime(old_file, (old_mtime, old_mtime))

            new_file = Path(ws2, "final-output.docx")
            new_file.write_bytes(b"PK-new")

            # Patch Path.stat to raise OSError on the second call,
            # simulating the old file being deleted mid-comparison.
            original_stat = Path.stat
            call_count = 0

            def flaky_stat(self_, *args, **kwargs):
                nonlocal call_count
                call_count += 1
                # Let the first stat succeed (new_file or old_file depending on
                # rglob order), then blow up on the comparison stat to simulate
                # a TOCTOU race.
                if call_count == 2:
                    raise FileNotFoundError("TOCTOU race simulation")
                return original_stat(self_, *args, **kwargs)

            with patch.object(Path, "stat", flaky_stat):
                result = collect_output_files_from_work_dir(td)

            # Should still return at least one file (the one that didn't vanish)
            assert len(result) >= 1
            assert all(p.name == "final-output.docx" for p in result)

    def test_realistic_anything_to_docx_workspace(self):
        """
        End-to-end: simulates the exact anything-to-docx workspace structure.
        Without any manifest, Tier 2 should pick up only final-output.docx.
        """
        from open_webui.utils.opencode import collect_output_files_from_work_dir

        with tempfile.TemporaryDirectory(prefix="test_atd_") as td:
            out_dir = os.path.join(td, "output")
            ws = os.path.join(out_dir, "report-atd-workspace")

            # Build the exact workspace structure from the bug report
            for subdir in [
                "dsl", "dsl-vlm", "ocr-output", "input-images",
            ]:
                os.makedirs(os.path.join(ws, subdir))

            # Intermediates
            Path(ws, "output.docx").write_bytes(b"PK-untranslated")
            for i in range(1, 4):
                Path(ws, "dsl", f"page-{i}.xml").write_text(f"<page>{i}</page>")
                Path(ws, "dsl-vlm", f"page-{i}.xml").write_text(f"<page>{i}</page>")
                Path(ws, "input-images", f"page-{i}.png").write_bytes(b"\x89PNG")
                Path(ws, "ocr-output", f"page-{i}.json").write_text(f'{{"page":{i}}}')

            # THE deliverable
            Path(ws, "final-output.docx").write_bytes(b"PK-final-docx-content")

            result = collect_output_files_from_work_dir(td)

            # Tier 2 should return ONLY final-output.docx
            assert len(result) == 1
            assert result[0].name == "final-output.docx"
            # Verify it's the right one (not output.docx)
            assert result[0].read_bytes() == b"PK-final-docx-content"


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
            config_path.write_text(
                json.dumps(
                    {
                        "custom_field": "custom_value",
                        "permission": "deny",  # should be overwritten to "allow"
                    }
                )
            )

            sync_opencode_config_to_dir(td)
            config = json.loads(config_path.read_text())

            assert config["permission"] == "allow"  # overwritten
            assert config["custom_field"] == "custom_value"  # preserved

    def test_copies_model_fields_from_global_config(self, monkeypatch):
        from open_webui.utils.opencode import sync_opencode_config_to_dir

        with tempfile.TemporaryDirectory(prefix="test_home_") as home_dir:
            monkeypatch.setenv("HOME", home_dir)
            config_dir = Path(home_dir) / ".config" / "opencode"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "opencode.json").write_text(
                json.dumps(
                    {
                        "model": "lmstudio/qwen3.5-122b-a10b",
                        "small_model": "lmstudio/qwen3.5-9b",
                        "provider": {
                            "lmstudio": {
                                "options": {
                                    "baseURL": "http://127.0.0.1:1234/v1",
                                }
                            }
                        },
                        "agent": {
                            "general": {
                                "model": "lmstudio/qwen3.5-122b-a10b",
                            }
                        },
                    }
                )
            )

            with tempfile.TemporaryDirectory(prefix="test_sync_") as td:
                sync_opencode_config_to_dir(td)
                config = json.loads((Path(td) / "opencode.json").read_text())

                assert config["model"] == "lmstudio/qwen3.5-122b-a10b"
                assert config["small_model"] == "lmstudio/qwen3.5-9b"
                assert (
                    config["agent"]["general"]["model"] == "lmstudio/qwen3.5-122b-a10b"
                )

    def test_permission_format_matches_generate(self, monkeypatch):
        """Both config producers must use the same 'allow' string format."""
        from open_webui.utils.opencode import (
            generate_opencode_config,
            sync_opencode_config_to_dir,
        )

        with tempfile.TemporaryDirectory(prefix="test_home_") as home_dir:
            monkeypatch.setenv("HOME", home_dir)
            config_dir = Path(home_dir) / ".config" / "opencode"
            config_dir.mkdir(parents=True, exist_ok=True)
            # Pre-populate with permission: "allow" string format —
            # generate_opencode_config uses setdefault so it preserves this.
            (config_dir / "opencode.json").write_text(
                json.dumps({"permission": "allow"})
            )

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

    def test_openai_provider_mapping(self, monkeypatch):
        from open_webui.utils.opencode import generate_opencode_config

        with tempfile.TemporaryDirectory(prefix="test_home_") as home_dir:
            monkeypatch.setenv("HOME", home_dir)
            config_dir = Path(home_dir) / ".config" / "opencode"
            config_dir.mkdir(parents=True, exist_ok=True)
            # Pre-populate with an "openai" provider so generate_opencode_config
            # will update it (it only updates existing providers).
            (config_dir / "opencode.json").write_text(
                json.dumps({
                    "provider": {
                        "openai": {
                            "baseURL": "http://old.example.com/v1",
                            "apiKey": "old-key",
                        }
                    }
                })
            )

            config = generate_opencode_config(
                openai_api_base_urls=["http://api.openai.com/v1"],
                openai_api_keys=["sk-test"],
                ollama_base_urls=[],
            )
            assert "openai" in config["provider"]
            p = config["provider"]["openai"]
            assert p["baseURL"] == "http://api.openai.com/v1"
            assert p["apiKey"] == "sk-test"

    def test_ollama_provider_adds_v1(self, monkeypatch):
        from open_webui.utils.opencode import generate_opencode_config

        with tempfile.TemporaryDirectory(prefix="test_home_") as home_dir:
            monkeypatch.setenv("HOME", home_dir)
            config_dir = Path(home_dir) / ".config" / "opencode"
            config_dir.mkdir(parents=True, exist_ok=True)
            # Pre-populate with an "ollama" provider so it gets updated.
            (config_dir / "opencode.json").write_text(
                json.dumps({
                    "provider": {
                        "ollama": {
                            "baseURL": "http://old-ollama:11434/v1",
                        }
                    }
                })
            )

            config = generate_opencode_config(
                openai_api_base_urls=[],
                openai_api_keys=[],
                ollama_base_urls=["http://localhost:11434"],
            )
            assert "ollama" in config["provider"]
            assert config["provider"]["ollama"]["baseURL"] == "http://localhost:11434/v1"

    def test_empty_urls_skipped(self, monkeypatch):
        from open_webui.utils.opencode import generate_opencode_config

        with tempfile.TemporaryDirectory(prefix="test_home_") as home_dir:
            monkeypatch.setenv("HOME", home_dir)
            config_dir = Path(home_dir) / ".config" / "opencode"
            config_dir.mkdir(parents=True, exist_ok=True)
            # Pre-populate with "openai_1" provider (the one that should be updated)
            # but NOT "openai" or "ollama" (those should remain absent).
            (config_dir / "opencode.json").write_text(
                json.dumps({
                    "provider": {
                        "openai_1": {
                            "baseURL": "http://old.example.com/v1",
                            "apiKey": "old-key",
                        }
                    }
                })
            )

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

    def test_prefix_id_used_as_provider_id(self, monkeypatch):
        from open_webui.utils.opencode import generate_opencode_config

        with tempfile.TemporaryDirectory(prefix="test_home_") as home_dir:
            monkeypatch.setenv("HOME", home_dir)
            config_dir = Path(home_dir) / ".config" / "opencode"
            config_dir.mkdir(parents=True, exist_ok=True)
            # Pre-populate with "lmstudio" provider (the prefix_id) but NOT "openai".
            (config_dir / "opencode.json").write_text(
                json.dumps({
                    "provider": {
                        "lmstudio": {
                            "baseURL": "http://old-lmstudio.local/v1",
                            "apiKey": "old-key",
                        }
                    }
                })
            )

            config = generate_opencode_config(
                openai_api_base_urls=["http://lmstudio.local/v1"],
                openai_api_keys=["lm-key"],
                ollama_base_urls=[],
                openai_api_configs={"0": {"prefix_id": "lmstudio"}},
            )
            assert "lmstudio" in config["provider"]
            assert "openai" not in config["provider"]

    def test_normalizes_stale_top_level_model_from_agent_config(self, monkeypatch):
        from open_webui.utils.opencode import generate_opencode_config

        with tempfile.TemporaryDirectory(prefix="test_home_") as home_dir:
            monkeypatch.setenv("HOME", home_dir)
            config_dir = Path(home_dir) / ".config" / "opencode"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "opencode.json").write_text(
                json.dumps(
                    {
                        "model": "lmstudio/qwen3.5-27b",
                        "provider": {
                            "lmstudio": {
                                "options": {
                                    "baseURL": "http://old-host:1234/v1",
                                },
                                "models": {
                                    "qwen3.5-122b-a10b": {
                                        "name": "qwen3.5-122b-a10b",
                                    }
                                },
                            }
                        },
                        "agent": {
                            "general": {
                                "model": "lmstudio/qwen3.5-122b-a10b",
                            },
                            "plan": {
                                "model": "lmstudio/qwen3.5-122b-a10b",
                            },
                        },
                    }
                )
            )

            config = generate_opencode_config(
                openai_api_base_urls=["http://127.0.0.1:1234/v1"],
                openai_api_keys=["lmstudio"],
                ollama_base_urls=[],
                openai_api_configs={"0": {"prefix_id": "lmstudio"}},
            )

            assert config["model"] == "lmstudio/qwen3.5-122b-a10b"


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
