"""Chat mode → execution budget resolution.

The composer's mode is a per-message decision and has to outrank the
workspace-wide default, without breaking older tasks that never sent one.
"""

import pytest

from app.agent.execution_policy import (
    execution_mode_for_chat_mode,
    is_orchestrated,
    normalize_chat_mode,
)
from server.tasks import resolve_execution_policy


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("instant", "instant"),
        ("  Thinking  ", "thinking"),
        ("ORCHESTRATED", "orchestrated"),
        ("balanced", "balanced"),
        ("nonsense", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_chat_mode(raw, expected):
    assert normalize_chat_mode(raw) == expected


@pytest.mark.parametrize(
    "mode, budget",
    [
        ("instant", "fast"),
        ("balanced", "balanced"),
        ("thinking", "deep"),
        # Orchestration fans out and the lead reviews everything, so it needs
        # the widest budget rather than the middle one.
        ("orchestrated", "deep"),
        ("nonsense", None),
        (None, None),
    ],
)
def test_execution_mode_for_chat_mode(mode, budget):
    assert execution_mode_for_chat_mode(mode) == budget


def test_only_orchestrated_selects_the_multi_agent_path():
    assert is_orchestrated("orchestrated")
    for mode in ("instant", "balanced", "thinking", "nonsense", None):
        assert not is_orchestrated(mode)


def test_chat_mode_outranks_the_workspace_default():
    connection = {"execution_mode": "deep"}

    policy, source = resolve_execution_policy(connection, "instant")

    assert policy.mode == "fast"
    assert source == "chat_mode:instant"


def test_without_a_chat_mode_the_workspace_default_still_applies():
    policy, source = resolve_execution_policy({"execution_mode": "fast"})

    assert policy.mode == "fast"
    assert source == "llm_connection"


def test_an_unknown_chat_mode_falls_back_instead_of_erroring():
    policy, source = resolve_execution_policy({"execution_mode": "deep"}, "wizardry")

    assert policy.mode == "deep"
    assert source == "llm_connection"


def test_legacy_max_steps_does_not_override_an_explicit_chat_mode():
    """max_steps is a pre-modes setting; a deliberate choice should win."""
    connection = {"max_steps": 3}

    policy, source = resolve_execution_policy(connection, "thinking")

    assert policy.mode == "deep"
    assert policy.slice_steps > 3
    assert source == "chat_mode:thinking"


def test_legacy_max_steps_still_applies_when_no_mode_is_chosen():
    policy, source = resolve_execution_policy({"max_steps": 3})

    assert policy.slice_steps == 3
    assert source == "legacy_max_steps"


def test_local_providers_keep_their_token_exemption_under_a_chat_mode():
    connection = {"api_type": "lmstudio"}

    policy, source = resolve_execution_policy(connection, "thinking")

    assert policy.enforce_token_budget is False
    assert source == "chat_mode:thinking_local_unmetered"


def test_wall_time_stays_inside_the_hard_task_timeout():
    """Deep mode's wall clock must not outlive the Celery hard timeout."""
    from server.tasks import TASK_HARD_TIMEOUT_SECONDS

    policy, _ = resolve_execution_policy({}, "thinking")

    assert policy.max_wall_time_seconds <= TASK_HARD_TIMEOUT_SECONDS - 30
