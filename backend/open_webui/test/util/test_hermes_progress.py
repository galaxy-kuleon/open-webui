from open_webui.utils.hermes_progress import hermes_tool_status_event


def test_correlated_start_and_completion_preserve_elapsed_time():
    starts = {}

    running = hermes_tool_status_event(
        {"tool": "skill_view", "toolCallId": "call-1", "status": "running"},
        starts,
        now=100.0,
    )
    completed = hermes_tool_status_event(
        {"tool": "skill_view", "toolCallId": "call-1", "status": "completed"},
        starts,
        now=112.34,
    )

    assert running == {
        "type": "status",
        "data": {
            "action": "hermes_tool",
            "description": "Running skill_view",
            "done": False,
            "tool": "skill_view",
            "tool_call_id": "call-1",
            "started_at": 100.0,
        },
    }
    assert completed["data"]["description"] == "Completed skill_view"
    assert completed["data"]["elapsed_seconds"] == 12.3
    assert starts == {}


def test_unrelated_or_uncorrelated_provider_payload_is_ignored():
    starts = {}

    assert hermes_tool_status_event({"status": "running"}, starts, now=1) is None
    assert (
        hermes_tool_status_event(
            {"tool": "x", "toolCallId": "c", "status": "queued"}, starts, now=1
        )
        is None
    )
    assert starts == {}
