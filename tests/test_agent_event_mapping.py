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


def test_execution_slice_is_a_resumable_state_change():
    progress = _agent_event_to_progress(
        {
            "type": "execution_slice",
            "data": {
                "state": "continuing",
                "completed_slice": 1,
                "next_slice": 2,
                "mode": "balanced",
            },
        }
    )

    assert _names("execution_slice", {"state": "continuing"}) == [
        "agent:lifecycle:state:change"
    ]
    assert progress[0]["content"]["state"] == "continuing"
    assert progress[0]["content"]["next_slice"] == 2


def test_browser_event_preserves_backend_and_extraction_metadata():
    progress = _agent_event_to_progress(
        {
            "type": "browser_screenshot",
            "data": {
                "url": "https://example.test",
                "browser_backend": "cloakbrowser",
                "browser_fallback": False,
                "extraction_method": "dom_text_fallback",
                "extraction_fallback_reason": "model timeout after 120s",
            },
        }
    )

    assert progress[0]["name"] == ("agent:lifecycle:step:think:browser:browse:complete")
    assert progress[0]["content"]["browser_backend"] == "cloakbrowser"
    assert progress[0]["content"]["extraction_method"] == "dom_text_fallback"
