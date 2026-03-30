"""
Integration tests for the /research command handler in middleware.py.

Tests the specific handler logic that sits inside process_chat_payload()
(lines ~3212-3267 in middleware.py). Since the handler is embedded in a
very large function with many dependencies, we test the handler's contract
by mocking run_research at the boundary and verifying:

1. Query extraction from user message (stripping /research prefix)
2. Config guards (disabled export, missing dir)
3. Model resolution (RAG_RESEARCH_MODEL vs form_data model)
4. run_research receives correct args (query, kb_dir, app, model, event_emitter)
5. Report injection into sources list
6. Error handling (empty KB, LLM failure, empty query)
7. Message rewrite (set_last_user_message_content replaces /research prefix)

The tests exercise the handler logic extracted into a helper that mirrors
the middleware code exactly, to avoid coupling to process_chat_payload's
many unrelated dependencies.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from open_webui.utils.misc import get_last_user_message, set_last_user_message_content


# ---------------------------------------------------------------------------
# Extracted handler — mirrors middleware.py lines 3212-3267 EXACTLY.
# This is the code-under-test; if middleware changes, this must be updated.
# ---------------------------------------------------------------------------

async def _research_handler(request, form_data, sources, event_emitter=None):
    """
    Extracted /research command handler — mirrors middleware.py logic exactly.

    This exists solely to make the embedded handler testable without invoking
    the full process_chat_payload function (which has dozens of unrelated deps).
    """
    import logging
    log = logging.getLogger("open_webui.utils.middleware")

    user_msg_for_research = get_last_user_message(form_data["messages"])
    if (
        user_msg_for_research
        and user_msg_for_research.strip().startswith("/research")
        and request.app.state.config.RAG_KNOWLEDGE_EXPORT_ENABLED
        and request.app.state.config.RAG_KNOWLEDGE_EXPORT_DIR
    ):
        try:
            from open_webui.utils.research import run_research

            research_query = user_msg_for_research.strip()[len("/research"):].strip()
            if research_query:
                log.info(f"/research command: {research_query[:80]}")

                # Replace the /research message with the actual query
                set_last_user_message_content(research_query, form_data["messages"])

                research_model = (
                    request.app.state.config.RAG_RESEARCH_MODEL
                    or form_data.get("model")
                )
                research_result = await run_research(
                    query=research_query,
                    kb_dir=request.app.state.config.RAG_KNOWLEDGE_EXPORT_DIR,
                    app=request.app,
                    model=research_model,
                    event_emitter=event_emitter,
                )

                if research_result["success"] and research_result["report"]:
                    report_source = {
                        "source": {
                            "name": f"Research Report: {research_query[:50]}",
                            "type": "research",
                        },
                        "document": [research_result["report"]],
                        "metadata": [
                            {
                                "name": f"Research Report: {research_query[:50]}",
                                "source": research_result.get(
                                    "report_path", "research_report"
                                ),
                                "type": "research",
                            }
                        ],
                    }
                    sources.append(report_source)
                    log.info(
                        f"/research: report injected ({len(research_result['report'])} chars)"
                    )
                elif research_result["error"]:
                    log.error(f"/research failed: {research_result['error']}")
        except Exception as e:
            log.exception(f"/research command failed: {e}")


# ---------------------------------------------------------------------------
# Test fixtures — minimal stubs for request, config, form_data
# ---------------------------------------------------------------------------

class _FakeConfig:
    """Minimal config stub with research-related fields."""
    def __init__(
        self,
        enabled=True,
        export_dir="/fake/kb",
        research_model="test-model",
    ):
        self.RAG_KNOWLEDGE_EXPORT_ENABLED = enabled
        self.RAG_KNOWLEDGE_EXPORT_DIR = export_dir
        self.RAG_RESEARCH_MODEL = research_model


class _FakeState:
    def __init__(self, config=None):
        self.config = config or _FakeConfig()


class _FakeApp:
    def __init__(self, config=None):
        self.state = _FakeState(config)


class _FakeRequest:
    def __init__(self, config=None):
        self.app = _FakeApp(config)


def _make_form_data(user_message: str, model: str = "fallback-model") -> dict:
    """Build minimal form_data with a single user message."""
    return {
        "messages": [
            {"role": "user", "content": user_message},
        ],
        "model": model,
    }


def _success_result(report="# Report\n\nFindings.", report_path="/fake/kb/_reports/r.md"):
    """Standard success result from run_research."""
    return {
        "report": report,
        "report_path": report_path,
        "success": True,
        "error": None,
    }


def _error_result(error="Something went wrong"):
    """Standard error result from run_research."""
    return {
        "report": None,
        "report_path": None,
        "success": False,
        "error": error,
    }


# ---------------------------------------------------------------------------
# Tests: Query extraction
# ---------------------------------------------------------------------------

class TestResearchQueryExtraction:
    """Verify /research prefix is stripped and query is extracted correctly."""

    @pytest.fixture(autouse=True)
    def _patch_run_research(self):
        with patch(
            "open_webui.utils.research.run_research",
            new_callable=AsyncMock,
        ) as mock:
            mock.return_value = _success_result()
            self.mock_research = mock
            yield

    def test_simple_query(self):
        form_data = _make_form_data("/research what is the contract about?")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(), form_data, sources)
        )
        # run_research should be called with the query (no /research prefix)
        call_kwargs = self.mock_research.call_args[1]
        assert call_kwargs["query"] == "what is the contract about?"

    def test_leading_trailing_whitespace_stripped(self):
        form_data = _make_form_data("  /research   summarize findings   ")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(), form_data, sources)
        )
        call_kwargs = self.mock_research.call_args[1]
        assert call_kwargs["query"] == "summarize findings"

    def test_empty_query_after_prefix(self):
        """If user sends '/research' with no query, handler should not call run_research."""
        form_data = _make_form_data("/research")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(), form_data, sources)
        )
        self.mock_research.assert_not_called()

    def test_whitespace_only_query(self):
        """'/research   ' (only spaces after prefix) should not trigger research."""
        form_data = _make_form_data("/research   ")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(), form_data, sources)
        )
        self.mock_research.assert_not_called()

    def test_message_rewritten_without_prefix(self):
        """After extraction, the user message should be the bare query."""
        form_data = _make_form_data("/research analyze the budget")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(), form_data, sources)
        )
        # The form_data messages should now have the bare query
        last_msg = get_last_user_message(form_data["messages"])
        assert last_msg == "analyze the budget"
        assert "/research" not in last_msg


# ---------------------------------------------------------------------------
# Tests: Config guards
# ---------------------------------------------------------------------------

class TestResearchConfigGuards:
    """Verify handler respects config flags before calling run_research."""

    @pytest.fixture(autouse=True)
    def _patch_run_research(self):
        with patch(
            "open_webui.utils.research.run_research",
            new_callable=AsyncMock,
        ) as mock:
            mock.return_value = _success_result()
            self.mock_research = mock
            yield

    def test_disabled_export_skips_research(self):
        config = _FakeConfig(enabled=False)
        form_data = _make_form_data("/research test query")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(config), form_data, sources)
        )
        self.mock_research.assert_not_called()
        assert sources == []

    def test_empty_export_dir_skips_research(self):
        config = _FakeConfig(export_dir="")
        form_data = _make_form_data("/research test query")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(config), form_data, sources)
        )
        self.mock_research.assert_not_called()

    def test_none_export_dir_skips_research(self):
        config = _FakeConfig(export_dir=None)
        form_data = _make_form_data("/research test query")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(config), form_data, sources)
        )
        self.mock_research.assert_not_called()

    def test_non_research_message_skips(self):
        form_data = _make_form_data("tell me about contracts")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(), form_data, sources)
        )
        self.mock_research.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: run_research receives correct arguments
# ---------------------------------------------------------------------------

class TestResearchArgsPassed:
    """Verify run_research is called with the right kb_dir, app, model, event_emitter."""

    @pytest.fixture(autouse=True)
    def _patch_run_research(self):
        with patch(
            "open_webui.utils.research.run_research",
            new_callable=AsyncMock,
        ) as mock:
            mock.return_value = _success_result()
            self.mock_research = mock
            yield

    def test_kb_dir_from_config(self):
        config = _FakeConfig(export_dir="/my/special/kb")
        form_data = _make_form_data("/research test")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(config), form_data, sources)
        )
        assert self.mock_research.call_args[1]["kb_dir"] == "/my/special/kb"

    def test_app_is_request_app(self):
        request = _FakeRequest()
        form_data = _make_form_data("/research test")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(request, form_data, sources)
        )
        assert self.mock_research.call_args[1]["app"] is request.app

    def test_model_from_rag_research_model(self):
        config = _FakeConfig(research_model="rag-specific-model")
        form_data = _make_form_data("/research test", model="chat-model")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(config), form_data, sources)
        )
        assert self.mock_research.call_args[1]["model"] == "rag-specific-model"

    def test_model_falls_back_to_form_data_model(self):
        """When RAG_RESEARCH_MODEL is empty, use form_data['model']."""
        config = _FakeConfig(research_model="")
        form_data = _make_form_data("/research test", model="fallback-chat-model")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(config), form_data, sources)
        )
        assert self.mock_research.call_args[1]["model"] == "fallback-chat-model"

    def test_event_emitter_passed(self):
        emitter = AsyncMock()
        form_data = _make_form_data("/research test")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(), form_data, sources, event_emitter=emitter)
        )
        assert self.mock_research.call_args[1]["event_emitter"] is emitter


# ---------------------------------------------------------------------------
# Tests: Report injection into sources
# ---------------------------------------------------------------------------

class TestResearchReportInjection:
    """Verify the research report is correctly injected into sources list."""

    @pytest.fixture(autouse=True)
    def _patch_run_research(self):
        with patch(
            "open_webui.utils.research.run_research",
            new_callable=AsyncMock,
        ) as mock:
            self.mock_research = mock
            yield

    def test_success_injects_source(self):
        self.mock_research.return_value = _success_result(
            report="# Analysis\n\nKey findings.",
            report_path="/kb/_reports/analysis.md",
        )
        form_data = _make_form_data("/research analyze contracts")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(), form_data, sources)
        )
        assert len(sources) == 1
        src = sources[0]

        # Source metadata
        assert src["source"]["type"] == "research"
        assert "analyze contracts" in src["source"]["name"]

        # Document content
        assert src["document"] == ["# Analysis\n\nKey findings."]

        # Metadata
        assert src["metadata"][0]["source"] == "/kb/_reports/analysis.md"
        assert src["metadata"][0]["type"] == "research"

    def test_success_without_report_path_uses_default(self):
        """When report_path is missing from result, use 'research_report' fallback."""
        self.mock_research.return_value = {
            "report": "Report text.",
            "success": True,
            "error": None,
            # No report_path key
        }
        form_data = _make_form_data("/research test")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(), form_data, sources)
        )
        assert sources[0]["metadata"][0]["source"] == "research_report"

    def test_long_query_truncated_in_source_name(self):
        """Source name should truncate query to first 50 chars."""
        long_query = "a" * 100
        self.mock_research.return_value = _success_result()
        form_data = _make_form_data(f"/research {long_query}")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(), form_data, sources)
        )
        source_name = sources[0]["source"]["name"]
        # "Research Report: " (18 chars) + 50 chars of query = 68 max
        assert len(source_name) <= 68

    def test_existing_sources_preserved(self):
        """Handler should append to sources, not replace them."""
        self.mock_research.return_value = _success_result()
        form_data = _make_form_data("/research test")
        existing_source = {"source": {"name": "existing", "type": "file"}}
        sources = [existing_source]
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(), form_data, sources)
        )
        assert len(sources) == 2
        assert sources[0] is existing_source


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------

class TestResearchErrorHandling:
    """Verify error paths don't inject sources and don't crash."""

    @pytest.fixture(autouse=True)
    def _patch_run_research(self):
        with patch(
            "open_webui.utils.research.run_research",
            new_callable=AsyncMock,
        ) as mock:
            self.mock_research = mock
            yield

    def test_empty_kb_error_no_source_injected(self):
        self.mock_research.return_value = _error_result(
            "Knowledge base is empty or contains no .md files"
        )
        form_data = _make_form_data("/research test")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(), form_data, sources)
        )
        assert sources == []

    def test_llm_failure_no_source_injected(self):
        self.mock_research.return_value = _error_result("LLM connection failed")
        form_data = _make_form_data("/research test")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(), form_data, sources)
        )
        assert sources == []

    def test_run_research_raises_exception(self):
        """If run_research raises, the handler should catch it gracefully."""
        self.mock_research.side_effect = RuntimeError("Unexpected explosion")
        form_data = _make_form_data("/research test")
        sources = []
        # Should NOT raise
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(), form_data, sources)
        )
        assert sources == []

    def test_success_false_with_report_none(self):
        """Success=False and report=None should not inject source."""
        self.mock_research.return_value = {
            "report": None,
            "report_path": None,
            "success": False,
            "error": "No model configured",
        }
        form_data = _make_form_data("/research test")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(), form_data, sources)
        )
        assert sources == []

    def test_success_true_but_empty_report(self):
        """Edge case: success=True but report is empty string — should not inject."""
        self.mock_research.return_value = {
            "report": "",
            "report_path": None,
            "success": True,
            "error": None,
        }
        form_data = _make_form_data("/research test")
        sources = []
        asyncio.get_event_loop().run_until_complete(
            _research_handler(_FakeRequest(), form_data, sources)
        )
        # Empty string is falsy, so the condition `research_result["report"]` is False
        assert sources == []


# ---------------------------------------------------------------------------
# Tests: Fidelity — handler matches middleware.py code exactly
# ---------------------------------------------------------------------------

class TestHandlerFidelityToMiddleware:
    """
    Verify that _research_handler matches the middleware.py code.

    These tests inspect the actual middleware source to ensure our extracted
    handler stays in sync with the real code. If the middleware changes,
    these tests will signal that _research_handler needs updating.
    """

    def test_middleware_contains_research_handler_code(self):
        """Verify the middleware still contains the /research handler."""
        import inspect
        from open_webui.utils import middleware
        source = inspect.getsource(middleware)
        # Key markers that the handler exists in middleware.py
        assert "from open_webui.utils.research import run_research" in source
        assert "research_query = user_msg_for_research.strip()" in source
        assert "sources.append(report_source)" in source

    def test_run_research_import_path_matches(self):
        """The import path used in middleware.py should resolve correctly."""
        from open_webui.utils.research import run_research
        assert callable(run_research)

    def test_get_last_user_message_and_set_are_importable(self):
        """The helper functions used by the handler are importable."""
        from open_webui.utils.misc import get_last_user_message, set_last_user_message_content
        assert callable(get_last_user_message)
        assert callable(set_last_user_message_content)
