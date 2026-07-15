from server.api import _agent_event_to_progress


def _names(event_type: str, data: dict) -> list[str]:
    return [
        item["name"]
        for item in _agent_event_to_progress({"type": event_type, "data": data})
    ]


def test_tool_result_does_not_close_multi_tool_step():
    assert _names(
        "tool_result",
        {"tool": "read_files", "tool_call_id": "call-1", "result": "ok"},
    ) == [
        "agent:lifecycle:step:act:tool:execute:complete",
        "agent:lifecycle:step:act:tool:complete",
    ]


def test_explicit_step_completion_closes_step_once():
    assert _names(
        "agent:lifecycle:step:complete",
        {"step": 2, "outcome": "acted"},
    ) == [
        "agent:lifecycle:step:act:complete",
        "agent:lifecycle:step:complete",
    ]


def test_internal_finish_preserves_final_response_without_completing_stream():
    progress = _agent_event_to_progress(
        {
            "type": "finish_signal",
            "data": {
                "message": "Finished cleanly.",
                "status": "success",
                "reason": "",
            },
        }
    )
    assert _names("finish_signal", {"message": "Finished cleanly."}) == [
        "agent:lifecycle:state:change"
    ]
    assert progress[0]["content"]["final_response"] == "Finished cleanly."
