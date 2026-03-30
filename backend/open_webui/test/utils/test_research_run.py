"""
Behavioral tests for run_research() — the refactored direct-LLM path.

Tests the async run_research() function by mocking _async_llm_completion
(the network boundary) while using real temporary KB directories.
This validates the full pipeline: KB loading -> prompt building -> LLM call ->
report saving -> result dict structure.

The LLM call is the only mock — everything else (KB loading, file I/O,
prompt construction) runs against real filesystems.
"""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from open_webui.utils.research import (
    RESEARCH_SYSTEM_PROMPT,
    _save_report,
    run_research,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_kb(tmp_path: Path, files: dict[str, str]) -> str:
    """Create a temporary KB directory with the given file structure."""
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    for rel_path, content in files.items():
        fpath = kb_root / rel_path
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")
    return str(kb_root)


class _FakeApp:
    """Minimal app stub with state.config for model resolution."""

    def __init__(self, model: str = ""):
        self.state = _FakeState(model)


class _FakeState:
    def __init__(self, model: str):
        self.config = _FakeConfig(model)


class _FakeConfig:
    def __init__(self, model: str):
        self.RAG_RESEARCH_MODEL = model


# ---------------------------------------------------------------------------
# run_research — error paths
# ---------------------------------------------------------------------------

class TestRunResearchErrors:
    @pytest.fixture(autouse=True)
    def _patch_llm(self):
        """Patch _async_llm_completion for all tests in this class."""
        with patch(
            "open_webui.utils.knowledge_export._async_llm_completion",
            new_callable=AsyncMock,
        ) as mock:
            self.mock_llm = mock
            yield

    def test_nonexistent_kb_dir(self):
        result = asyncio.get_event_loop().run_until_complete(
            run_research(
                query="test query",
                kb_dir="/nonexistent/path",
                app=_FakeApp("some-model"),
            )
        )
        assert result["success"] is False
        assert "not found" in result["error"]
        assert result["report"] is None
        assert result["report_path"] is None
        # LLM should not have been called
        self.mock_llm.assert_not_called()

    def test_empty_kb_dir(self, tmp_path):
        kb_dir = _make_kb(tmp_path, {})
        result = asyncio.get_event_loop().run_until_complete(
            run_research(
                query="test query",
                kb_dir=kb_dir,
                app=_FakeApp("some-model"),
            )
        )
        assert result["success"] is False
        assert "empty" in result["error"].lower() or "no .md" in result["error"].lower()
        self.mock_llm.assert_not_called()

    def test_no_model_configured(self, tmp_path):
        kb_dir = _make_kb(tmp_path, {"doc.md": "Some content here"})
        # App has no model, and no model passed explicitly
        result = asyncio.get_event_loop().run_until_complete(
            run_research(
                query="test query",
                kb_dir=kb_dir,
                app=_FakeApp(""),
                model=None,
            )
        )
        assert result["success"] is False
        assert "model" in result["error"].lower()
        self.mock_llm.assert_not_called()

    def test_llm_raises_exception(self, tmp_path):
        kb_dir = _make_kb(tmp_path, {"doc.md": "Some content here"})
        self.mock_llm.side_effect = RuntimeError("LLM connection failed")

        result = asyncio.get_event_loop().run_until_complete(
            run_research(
                query="test query",
                kb_dir=kb_dir,
                app=_FakeApp("test-model"),
                model="test-model",
            )
        )
        assert result["success"] is False
        assert "LLM connection failed" in result["error"]
        assert result["report"] is None

    def test_llm_returns_empty(self, tmp_path):
        kb_dir = _make_kb(tmp_path, {"doc.md": "Some content here"})
        self.mock_llm.return_value = ""

        result = asyncio.get_event_loop().run_until_complete(
            run_research(
                query="test query",
                kb_dir=kb_dir,
                app=_FakeApp("test-model"),
                model="test-model",
            )
        )
        assert result["success"] is False
        assert "empty" in result["error"].lower()

    def test_llm_returns_whitespace_only(self, tmp_path):
        kb_dir = _make_kb(tmp_path, {"doc.md": "Some content here"})
        self.mock_llm.return_value = "   \n  \n  "

        result = asyncio.get_event_loop().run_until_complete(
            run_research(
                query="test query",
                kb_dir=kb_dir,
                app=_FakeApp("test-model"),
                model="test-model",
            )
        )
        assert result["success"] is False


# ---------------------------------------------------------------------------
# run_research — success path
# ---------------------------------------------------------------------------

class TestRunResearchSuccess:
    @pytest.fixture(autouse=True)
    def _patch_llm(self):
        with patch(
            "open_webui.utils.knowledge_export._async_llm_completion",
            new_callable=AsyncMock,
        ) as mock:
            self.mock_llm = mock
            yield

    def test_success_basic(self, tmp_path):
        """Successful research: loads KB, calls LLM, saves report."""
        kb_dir = _make_kb(tmp_path, {
            "legal/contract.md": "Employment agreement content",
            "legal/contract.index.md": "Index: entities, dates",
        })
        self.mock_llm.return_value = "# Research Report\n\nFindings here."

        result = asyncio.get_event_loop().run_until_complete(
            run_research(
                query="summarize the contract",
                kb_dir=kb_dir,
                app=_FakeApp("test-model"),
                model="test-model",
            )
        )

        assert result["success"] is True
        assert result["error"] is None
        assert result["report"] == "# Research Report\n\nFindings here."
        assert result["report_path"] is not None
        # Report file should exist on disk
        assert os.path.isfile(result["report_path"])
        # Report file should contain the report content
        report_text = Path(result["report_path"]).read_text(encoding="utf-8")
        assert "Research Report" in report_text
        assert "Findings here." in report_text

    def test_llm_receives_kb_content_in_system_prompt(self, tmp_path):
        """The LLM should receive KB content in the system message."""
        kb_dir = _make_kb(tmp_path, {
            "doc.md": "Important document text about healthcare.",
        })
        self.mock_llm.return_value = "Report content."

        asyncio.get_event_loop().run_until_complete(
            run_research(
                query="what about healthcare?",
                kb_dir=kb_dir,
                app=_FakeApp("test-model"),
                model="test-model",
            )
        )

        # Verify _async_llm_completion was called with correct args
        self.mock_llm.assert_called_once()
        call_args = self.mock_llm.call_args
        app_arg, messages_arg, model_arg = call_args[0]

        assert model_arg == "test-model"
        assert len(messages_arg) == 2
        assert messages_arg[0]["role"] == "system"
        assert messages_arg[1]["role"] == "user"

        # System message should contain both the prompt and KB content
        system_content = messages_arg[0]["content"]
        assert RESEARCH_SYSTEM_PROMPT in system_content
        assert "Important document text about healthcare." in system_content
        assert "KNOWLEDGE BASE CONTENT" in system_content

        # User message should contain the query
        assert "what about healthcare?" in messages_arg[1]["content"]

    def test_model_resolved_from_config_when_not_provided(self, tmp_path):
        """When model=None, it should read from app.state.config."""
        kb_dir = _make_kb(tmp_path, {"doc.md": "Content"})
        self.mock_llm.return_value = "Report."

        asyncio.get_event_loop().run_until_complete(
            run_research(
                query="test",
                kb_dir=kb_dir,
                app=_FakeApp("config-model-id"),
                model=None,
            )
        )

        call_args = self.mock_llm.call_args
        _, _, model_arg = call_args[0]
        assert model_arg == "config-model-id"

    def test_explicit_model_overrides_config(self, tmp_path):
        """Explicit model param should be used even when config has a model."""
        kb_dir = _make_kb(tmp_path, {"doc.md": "Content"})
        self.mock_llm.return_value = "Report."

        asyncio.get_event_loop().run_until_complete(
            run_research(
                query="test",
                kb_dir=kb_dir,
                app=_FakeApp("config-model"),
                model="explicit-model",
            )
        )

        call_args = self.mock_llm.call_args
        _, _, model_arg = call_args[0]
        assert model_arg == "explicit-model"

    def test_report_saved_in_reports_subdir(self, tmp_path):
        """Report should be saved under kb_dir/_reports/."""
        kb_dir = _make_kb(tmp_path, {"doc.md": "Content"})
        self.mock_llm.return_value = "Report content."

        result = asyncio.get_event_loop().run_until_complete(
            run_research(
                query="test query",
                kb_dir=kb_dir,
                app=_FakeApp("test-model"),
                model="test-model",
            )
        )

        assert "_reports" in result["report_path"]
        assert result["report_path"].startswith(kb_dir)

    def test_report_has_frontmatter(self, tmp_path):
        """Saved report should have YAML frontmatter with query and timestamp."""
        kb_dir = _make_kb(tmp_path, {"doc.md": "Content"})
        self.mock_llm.return_value = "Report content."

        result = asyncio.get_event_loop().run_until_complete(
            run_research(
                query="my research query",
                kb_dir=kb_dir,
                app=_FakeApp("test-model"),
                model="test-model",
            )
        )

        report_text = Path(result["report_path"]).read_text(encoding="utf-8")
        assert "---" in report_text
        assert "type: research_report" in report_text
        assert 'query: "my research query"' in report_text
        assert "generated_at:" in report_text

    def test_token_budget_passed_to_loader(self, tmp_path):
        """Custom token_budget should limit the KB content loaded."""
        # Create content that exceeds a small budget
        large_content = "x" * 4000  # 1000 tokens
        kb_dir = _make_kb(tmp_path, {"doc.md": large_content})
        self.mock_llm.return_value = "Report."

        asyncio.get_event_loop().run_until_complete(
            run_research(
                query="test",
                kb_dir=kb_dir,
                app=_FakeApp("test-model"),
                model="test-model",
                token_budget=100,  # Only 100 tokens (~400 chars)
            )
        )

        # Verify the system prompt has truncated content
        system_content = self.mock_llm.call_args[0][1][0]["content"]
        # The KB content in the prompt should be much shorter than 4000 chars
        # (the original content) because of the 100 token budget
        kb_marker = "KNOWLEDGE BASE CONTENT"
        kb_start = system_content.index(kb_marker) + len(kb_marker)
        kb_portion = system_content[kb_start:]
        # 100 tokens = ~400 chars max for content
        assert len(kb_portion) < 1000  # generous upper bound

    def test_cjk_content_in_kb(self, tmp_path):
        """CJK content should be handled correctly through the full pipeline."""
        kb_dir = _make_kb(tmp_path, {
            "medical/評鑑文件.md": "這是一份評鑑文件的內容",
        })
        self.mock_llm.return_value = "# 研究報告\n\n分析結果。"

        result = asyncio.get_event_loop().run_until_complete(
            run_research(
                query="評鑑相關文件分析",
                kb_dir=kb_dir,
                app=_FakeApp("test-model"),
                model="test-model",
            )
        )

        assert result["success"] is True
        assert "研究報告" in result["report"]
        # Verify CJK content was passed to LLM
        system_content = self.mock_llm.call_args[0][1][0]["content"]
        assert "這是一份評鑑文件的內容" in system_content


# ---------------------------------------------------------------------------
# run_research — event emitter
# ---------------------------------------------------------------------------

class TestRunResearchEventEmitter:
    @pytest.fixture(autouse=True)
    def _patch_llm(self):
        with patch(
            "open_webui.utils.knowledge_export._async_llm_completion",
            new_callable=AsyncMock,
        ) as mock:
            self.mock_llm = mock
            yield

    def test_emitter_called_on_success(self, tmp_path):
        """Event emitter should receive start and completion events."""
        kb_dir = _make_kb(tmp_path, {"doc.md": "Content"})
        self.mock_llm.return_value = "Report."

        emitter = AsyncMock()
        asyncio.get_event_loop().run_until_complete(
            run_research(
                query="test",
                kb_dir=kb_dir,
                app=_FakeApp("test-model"),
                model="test-model",
                event_emitter=emitter,
            )
        )

        # Should be called at least twice: start and completion
        assert emitter.call_count >= 2
        # First call: researching (done=False)
        first_call = emitter.call_args_list[0][0][0]
        assert first_call["data"]["done"] is False
        # Last call: complete (done=True)
        last_call = emitter.call_args_list[-1][0][0]
        assert last_call["data"]["done"] is True

    def test_emitter_called_on_llm_failure(self, tmp_path):
        """Event emitter should receive failure event when LLM errors."""
        kb_dir = _make_kb(tmp_path, {"doc.md": "Content"})
        self.mock_llm.side_effect = RuntimeError("LLM broke")

        emitter = AsyncMock()
        asyncio.get_event_loop().run_until_complete(
            run_research(
                query="test",
                kb_dir=kb_dir,
                app=_FakeApp("test-model"),
                model="test-model",
                event_emitter=emitter,
            )
        )

        # Should have start + failure events
        assert emitter.call_count >= 2
        last_call = emitter.call_args_list[-1][0][0]
        assert last_call["data"]["done"] is True
        assert "failed" in last_call["data"]["description"].lower()

    def test_no_emitter_does_not_crash(self, tmp_path):
        """Passing event_emitter=None should not raise."""
        kb_dir = _make_kb(tmp_path, {"doc.md": "Content"})
        self.mock_llm.return_value = "Report."

        result = asyncio.get_event_loop().run_until_complete(
            run_research(
                query="test",
                kb_dir=kb_dir,
                app=_FakeApp("test-model"),
                model="test-model",
                event_emitter=None,
            )
        )
        assert result["success"] is True


# ---------------------------------------------------------------------------
# _save_report — preserved function
# ---------------------------------------------------------------------------

class TestSaveReport:
    def test_creates_reports_dir(self, tmp_path):
        kb_dir = str(tmp_path / "kb")
        os.makedirs(kb_dir)
        report_path = _save_report(kb_dir, "test query", "Report content")
        assert os.path.isfile(report_path)
        assert "_reports" in report_path

    def test_report_contains_content_and_frontmatter(self, tmp_path):
        kb_dir = str(tmp_path / "kb")
        os.makedirs(kb_dir)
        report_path = _save_report(kb_dir, "my query", "# My Report")
        content = Path(report_path).read_text(encoding="utf-8")
        assert "# My Report" in content
        assert 'query: "my query"' in content
        assert "type: research_report" in content

    def test_safe_filename_from_query(self, tmp_path):
        kb_dir = str(tmp_path / "kb")
        os.makedirs(kb_dir)
        report_path = _save_report(
            kb_dir, "what's the 合約 about?", "Report"
        )
        filename = os.path.basename(report_path)
        # Should not contain problematic chars
        assert "'" not in filename
        assert "?" not in filename
        assert filename.endswith(".md")


# ---------------------------------------------------------------------------
# Removed functions should not exist
# ---------------------------------------------------------------------------

class TestSubprocessCodeRemoved:
    """Verify that subprocess-related functions have been removed."""

    def test_no_run_subprocess(self):
        from open_webui.utils import research
        assert not hasattr(research, "_run_subprocess")

    def test_no_extract_report_from_output(self):
        from open_webui.utils import research
        assert not hasattr(research, "_extract_report_from_output")

    def test_no_active_processes(self):
        from open_webui.utils import research
        assert not hasattr(research, "_active_processes")

    def test_no_active_processes_lock(self):
        from open_webui.utils import research
        assert not hasattr(research, "_active_processes_lock")

    def test_no_kill_all_research_processes(self):
        """kill_all_research_processes was a no-op stub — now fully removed."""
        from open_webui.utils import research
        assert not hasattr(research, "kill_all_research_processes")

    def test_no_subprocess_import(self):
        """The research module should not import subprocess."""
        import open_webui.utils.research as research_mod
        import inspect
        source = inspect.getsource(research_mod)
        assert "import subprocess" not in source
        assert "import asyncio" not in source  # no longer needed
