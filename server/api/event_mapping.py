def _agent_event_to_progress(event: dict) -> list[dict]:
    """
    Convert one internal agent event into one or more SSE progress messages
    that match the frontend's lifecycle type hierarchy.

    Internal types → frontend lifecycle names:
      step_start     → [agent:lifecycle:start (once)] + agent:lifecycle:step:start
                        + agent:lifecycle:step:think:start
      thought        → agent:lifecycle:step:think:tool:selected
                        + agent:lifecycle:step:think:complete
                        + agent:lifecycle:step:act:start
      tool_result    → agent:lifecycle:step:act:tool:execute:complete
                        + agent:lifecycle:step:act:tool:complete
      agent:lifecycle:step:complete → agent:lifecycle:step:act:complete
                                      + agent:lifecycle:step:complete
      finish_signal  → agent:lifecycle:complete
      final_response  → agent:lifecycle:complete
      terminated     → agent:lifecycle:terminated
      browser_screenshot → agent:lifecycle:step:think:browser:browse:complete
      token_count    → agent:lifecycle:step:think:token:count
      execution_slice / execution_budget → agent:lifecycle:state:change
      context_compressed → agent:lifecycle:step:think:context:compressed
      terminal_output → agent:lifecycle:step:act:tool:terminal:output
      workspace_file_updated → agent:lifecycle:step:act:tool:file:updated
      error          → agent:lifecycle:step:error
    """
    agent_type = event.get("type", "")
    data = event.get("data", {})

    def _msg(name: str, content=None) -> dict:
        return {"type": "progress", "name": name, "content": content or data}

    if agent_type == "step_start":
        step = data.get("step", 1)
        msgs = []
        if step == 1:
            msgs.append(_msg("agent:lifecycle:start", {"step": step}))
        msgs.append(_msg("agent:lifecycle:step:start", data))
        msgs.append(_msg("agent:lifecycle:step:think:start", data))
        return msgs

    if agent_type == "thought":
        tools = data.get("tools", [])
        tool_calls = data.get("tool_calls", [])
        msgs = [
            _msg(
                "agent:lifecycle:step:think:tool:selected",
                {
                    "tool": (
                        (tool_calls[0].get("function", {}) or {}).get("name")
                        if tool_calls
                        else (tools[0] if tools else None)
                    ),
                    "tool_calls": tool_calls,
                    "content": data.get("content", ""),
                },
            )
        ]
        msgs.append(_msg("agent:lifecycle:step:think:complete", data))
        if tools or tool_calls:
            msgs.append(_msg("agent:lifecycle:step:act:start", data))
            if tool_calls:
                for call in tool_calls:
                    fn = call.get("function", {}) or {}
                    call_id = call.get("id") or fn.get("name")
                    call_name = fn.get("name")
                    call_args = fn.get("arguments")
                    msgs.append(
                        _msg(
                            "agent:lifecycle:step:act:tool:start",
                            {
                                "id": call_id,
                                "name": call_name,
                            },
                        )
                    )
                    msgs.append(
                        _msg(
                            "agent:lifecycle:step:act:tool:execute:start",
                            {
                                "id": call_id,
                                "name": call_name,
                                "arguments": call_args,
                            },
                        )
                    )
            else:
                first_id = tools[0] if tools else None
                first_name = tools[0] if tools else None
                msgs.append(
                    _msg(
                        "agent:lifecycle:step:act:tool:start",
                        {
                            "id": first_id,
                            "name": first_name,
                        },
                    )
                )
                msgs.append(
                    _msg(
                        "agent:lifecycle:step:act:tool:execute:start",
                        {
                            "id": first_id,
                            "name": first_name,
                            "arguments": data.get("arguments"),
                        },
                    )
                )
        return msgs

    if agent_type == "tool_result":
        tool = data.get("tool", "")
        tool_call_id = data.get("tool_call_id") or tool
        msgs = [
            _msg(
                "agent:lifecycle:step:act:tool:execute:complete",
                {
                    "id": tool_call_id,
                    "name": tool,
                    "result": data.get("result", ""),
                },
            )
        ]
        msgs.append(
            _msg(
                "agent:lifecycle:step:act:tool:complete",
                {"id": tool_call_id, "name": tool},
            )
        )
        return msgs

    if agent_type == "agent:lifecycle:step:complete":
        msgs = []
        if data.get("outcome") == "acted":
            msgs.append(_msg("agent:lifecycle:step:act:complete", data))
        msgs.append(_msg("agent:lifecycle:step:complete", data))
        return msgs

    if agent_type == "step_result":
        # Secondary step completion — already handled via tool_result path; skip
        return []

    if agent_type == "finish_signal":
        if "workspace" in data:
            return [_msg("agent:lifecycle:complete", data)]
        return [
            _msg(
                "agent:lifecycle:state:change",
                {
                    "state": "finishing",
                    "final_response": data.get("message", ""),
                    "final_status": data.get("status", "success"),
                    "reason": data.get("reason", ""),
                    "direct_response": bool(data.get("direct_response")),
                },
            )
        ]

    if agent_type == "final_response":
        # Ignore since the assistant text is already in the 'thought' event,
        # and tasks.py will emit a final finish_signal anyway.
        return []

    if agent_type == "browser_screenshot":
        return [_msg("agent:lifecycle:step:think:browser:browse:complete", data)]

    if agent_type == "token_count":
        return [_msg("agent:lifecycle:step:think:token:count", data)]

    if agent_type in {
        "agent_configuration",
        "execution_policy",
        "execution_slice",
        "execution_budget",
    }:
        return [
            _msg(
                "agent:lifecycle:state:change",
                {**data, "state": data.get("state") or agent_type},
            )
        ]

    if agent_type == "context_compressed":
        return [_msg("agent:lifecycle:step:think:context:compressed", data)]

    if agent_type == "terminal_output":
        return [_msg("agent:lifecycle:step:act:tool:terminal:output", data)]

    if agent_type == "workspace_file_updated":
        return [_msg("agent:lifecycle:step:act:tool:file:updated", data)]

    if agent_type == "terminated":
        return [_msg("agent:lifecycle:terminated", data)]

    if agent_type == "agent_state":
        return [_msg("agent:lifecycle:state:change", data)]

    if agent_type == "stuck_detected":
        return [_msg("agent:lifecycle:state:change", data)]

    if agent_type == "warning":
        return [_msg("agent:lifecycle:state:change", {**data, "state": "warning"})]

    if agent_type == "error":
        if not data.get("fatal", True):
            return [
                _msg(
                    "agent:lifecycle:state:change",
                    {**data, "state": "warning"},
                )
            ]
        msgs = [_msg("agent:lifecycle:step:error", data)]
        if data.get("fatal", True):
            msgs.append(
                _msg(
                    "agent:lifecycle:terminated",
                    {
                        **data,
                        "reason": data.get("detail")
                        or data.get("message")
                        or "Task failed",
                        "status": "failure",
                    },
                )
            )
        return msgs

    # These native trace events duplicate the normalized thought/tool/step
    # messages above and otherwise produce double-prefixed lifecycle names.
    if agent_type.startswith("agent:lifecycle:"):
        return []

    # Catch-all: pass through as a generic lifecycle event.
    return [_msg(f"agent:lifecycle:{agent_type}", data)]
