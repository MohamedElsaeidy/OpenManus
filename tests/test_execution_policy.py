from app.agent.execution_policy import ExecutionPolicy


def test_execution_profiles_scale_all_budget_dimensions():
    fast = ExecutionPolicy.for_mode("fast")
    balanced = ExecutionPolicy.for_mode("balanced")
    deep = ExecutionPolicy.for_mode("deep")

    assert fast.slice_steps < balanced.slice_steps < deep.slice_steps
    assert fast.token_budget < balanced.token_budget < deep.token_budget
    assert (
        fast.max_wall_time_seconds
        < balanced.max_wall_time_seconds
        < deep.max_wall_time_seconds
    )
    assert fast.max_tool_calls < balanced.max_tool_calls < deep.max_tool_calls
    assert fast.max_continuations < balanced.max_continuations < deep.max_continuations


def test_unknown_execution_mode_falls_back_to_balanced():
    assert ExecutionPolicy.for_mode("unknown").mode == "balanced"


def test_local_policy_keeps_token_telemetry_without_enforcing_limit():
    policy = ExecutionPolicy.for_mode("balanced").without_token_limit()

    assert policy.token_budget == 320_000
    assert policy.enforce_token_budget is False
    assert policy.public_summary()["token_budget_enforced"] is False
