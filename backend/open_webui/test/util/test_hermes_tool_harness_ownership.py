from open_webui.constants import TASKS
from open_webui.utils.hermes_bridge import model_uses_hermes_tool_harness
from open_webui.utils.middleware import (
    _bypass_openwebui_tool_harness,
    _foreground_first_background_tasks,
    _selected_tool_calls,
)


def test_hermes_model_owns_its_tool_harness():
    assert model_uses_hermes_tool_harness({"id": "hermes-agent"})
    assert not model_uses_hermes_tool_harness({"id": "origin-pro-t-01"})


def test_hermes_bypass_removes_outer_tools_servers_and_terminal():
    form_data = {"model": "hermes-agent", "tools": [{"type": "function"}]}
    metadata = {"tool_servers": [{"id": "socv2"}]}

    tool_ids, terminal_id, bypassed = _bypass_openwebui_tool_harness(
        form_data,
        metadata,
        {"id": "hermes-agent"},
        ["server:mcp:socv2"],
        "terminal-1",
    )

    assert bypassed is True
    assert tool_ids is None and terminal_id is None
    assert "tools" not in form_data
    assert metadata["tool_servers"] is None
    assert metadata["openwebui_tool_harness"] == "bypassed_hermes_owned"


def test_non_hermes_model_keeps_outer_tool_configuration():
    tools = [{"type": "function"}]
    form_data = {"model": "origin-pro-t-01", "tools": tools}
    metadata = {"tool_servers": [{"id": "socv2"}]}

    tool_ids, terminal_id, bypassed = _bypass_openwebui_tool_harness(
        form_data,
        metadata,
        {"id": "origin-pro-t-01"},
        ["server:mcp:socv2"],
        "terminal-1",
    )

    assert bypassed is False
    assert tool_ids == ["server:mcp:socv2"]
    assert terminal_id == "terminal-1"
    assert form_data["tools"] is tools


def test_agent_cosmetics_never_delay_terminal_truth():
    requested = {
        TASKS.FOLLOW_UP_GENERATION: True,
        TASKS.TITLE_GENERATION: True,
        TASKS.TAGS_GENERATION: True,
    }

    effective = _foreground_first_background_tasks(requested, "hermes-agent")

    assert effective == {
        TASKS.FOLLOW_UP_GENERATION: False,
        TASKS.TITLE_GENERATION: False,
        TASKS.TAGS_GENERATION: False,
    }
    assert all(requested.values()), "the caller-owned task request is not mutated"


def test_none_selector_is_no_tool_not_a_fake_invocation():
    assert _selected_tool_calls({"name": None, "parameters": {}}) == []
    assert _selected_tool_calls({"tool_calls": [{"name": None}, {"name": "search"}]}) == [
        {"name": "search"}
    ]
