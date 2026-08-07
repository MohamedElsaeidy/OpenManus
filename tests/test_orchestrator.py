"""Tests for fan-out/join orchestration.

The orchestrator's job is to be safe when the model misbehaves: bad JSON, a
pointless split, a worker that dies, a review that comes back unparseable.
Those paths matter more than the happy one, because they are what stands
between a flaky decomposition and a broken turn.
"""

import asyncio
import json

import pytest

from app.agent.orchestrator import (
    MAX_WORKERS,
    WorkerBrief,
    _denied_tools,
    _extract_json,
    _summarize,
    run_orchestrated,
)


class FakeLLM:
    """Returns queued replies, in order, recording what it was asked."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def ask(self, messages, system_msgs=None, stream=True, **_):
        self.calls.append({"messages": messages, "system": system_msgs})
        return self.replies.pop(0) if self.replies else "{}"


class FakeAgent:
    """Stands in for Manus: records the prompts it was run with."""

    def __init__(self, replies=(), answer="direct answer"):
        self.llm = FakeLLM(replies)
        self.answer = answer
        self.runs = []

    async def run(self, emitter, prompt):
        self.runs.append(prompt)
        return self.answer


class FakeEmitter:
    def __init__(self):
        self.events = []
        self.interrupted = False

    def emit(self, event_type, data):
        self.events.append((event_type, data))

    def is_interrupted(self):
        return self.interrupted

    def types(self):
        return [event_type for event_type, _ in self.events]

    def payload(self, event_type):
        return next(data for kind, data in self.events if kind == event_type)


def decomposition_json(count):
    return json.dumps(
        {
            "reasoning": "independent",
            "workers": [
                {
                    "id": f"w{index}",
                    "title": f"Worker {index}",
                    "kind": "research",
                    "brief": f"Do part {index}",
                    "deliverable": "out.md",
                }
                for index in range(count)
            ],
        }
    )


@pytest.fixture
def patched_worker(monkeypatch):
    """Replace the real Manus worker with a recorder."""
    started = []

    class StubManus:
        @classmethod
        async def create(cls, **kwargs):
            started.append(kwargs)
            return cls()

        async def run(self, emitter, prompt):
            return f"worker output for: {prompt[:24]}"

    monkeypatch.setattr("app.agent.manus.Manus", StubManus)
    return started


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        'Sure, here you go:\n{"a": 1}',
        '```\n{"a": 1}\n```',
    ],
)
def test_extract_json_survives_chatty_and_fenced_replies(text):
    assert _extract_json(text) == {"a": 1}


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2, 3]"])
def test_extract_json_returns_none_when_there_is_no_object(text):
    assert _extract_json(text) is None


# ---------------------------------------------------------------------------
# Degrading to a direct run
# ---------------------------------------------------------------------------


def test_unparseable_decomposition_falls_back_to_a_direct_run():
    lead = FakeAgent(replies=["I think we should probably split this up somehow"])
    emitter = FakeEmitter()

    result = asyncio.run(run_orchestrated(lead, emitter, "do a thing", "/workspace"))

    assert result == "direct answer"
    assert lead.runs == ["do a thing"]
    assert "orchestration_skipped" in emitter.types()


def test_a_single_worker_is_not_worth_orchestrating():
    lead = FakeAgent(replies=[decomposition_json(1)])
    emitter = FakeEmitter()

    result = asyncio.run(run_orchestrated(lead, emitter, "do a thing", "/workspace"))

    assert result == "direct answer"
    assert lead.runs == ["do a thing"]
    assert "orchestration_planned" not in emitter.types()


def test_empty_worker_list_runs_directly():
    lead = FakeAgent(replies=['{"reasoning": "too small", "workers": []}'])
    emitter = FakeEmitter()

    asyncio.run(run_orchestrated(lead, emitter, "hello", "/workspace"))

    assert lead.runs == ["hello"]
    assert emitter.payload("orchestration_skipped")["reason"] == "too small"


# ---------------------------------------------------------------------------
# The fan-out path
# ---------------------------------------------------------------------------


def test_workers_run_and_the_lead_synthesises(patched_worker):
    lead = FakeAgent(
        replies=[
            decomposition_json(3),
            '{"verdict": "accept", "reason": "ok"}',
            '{"verdict": "accept", "reason": "ok"}',
            '{"verdict": "accept", "reason": "ok"}',
        ],
        answer="final delivery",
    )
    emitter = FakeEmitter()

    result = asyncio.run(run_orchestrated(lead, emitter, "big task", "/workspace"))

    assert result == "final delivery"
    assert len(patched_worker) == 3
    assert emitter.types().count("worker_started") == 3
    assert emitter.types().count("worker_completed") == 3
    assert emitter.types().count("worker_reviewed") == 3
    assert emitter.payload("orchestration_joined") == {
        "accepted": 3,
        "rejected": 0,
        "total": 3,
    }
    # The lead runs once, on the synthesis prompt carrying the worker results.
    assert len(lead.runs) == 1
    assert "big task" in lead.runs[0]
    assert "Accepted worker results" in lead.runs[0]


def test_rejected_work_is_quarantined_in_the_synthesis(patched_worker):
    lead = FakeAgent(
        replies=[
            decomposition_json(2),
            '{"verdict": "reject", "reason": "no evidence"}',
            '{"verdict": "accept", "reason": "ok"}',
        ]
    )
    emitter = FakeEmitter()

    asyncio.run(run_orchestrated(lead, emitter, "task", "/workspace"))

    synthesis = lead.runs[0]
    assert "do not rely on these" in synthesis
    assert "no evidence" in synthesis
    assert emitter.payload("orchestration_joined")["rejected"] == 1


def test_an_unparseable_review_does_not_discard_real_work(patched_worker):
    lead = FakeAgent(replies=[decomposition_json(2), "looks fine to me", "also fine"])
    emitter = FakeEmitter()

    asyncio.run(run_orchestrated(lead, emitter, "task", "/workspace"))

    assert emitter.payload("orchestration_joined")["accepted"] == 2


def test_a_failing_worker_does_not_sink_the_run(monkeypatch):
    class ExplodingManus:
        @classmethod
        async def create(cls, **kwargs):
            return cls()

        async def run(self, emitter, prompt):
            raise RuntimeError("sandbox died")

    monkeypatch.setattr("app.agent.manus.Manus", ExplodingManus)
    lead = FakeAgent(replies=[decomposition_json(2)], answer="partial delivery")
    emitter = FakeEmitter()

    result = asyncio.run(run_orchestrated(lead, emitter, "task", "/workspace"))

    assert result == "partial delivery"
    # A worker that raised is failed, and failure is rejected without asking
    # the model — so no review calls were needed.
    assert emitter.payload("orchestration_joined") == {
        "accepted": 0,
        "rejected": 2,
        "total": 2,
    }


def test_worker_count_is_capped(patched_worker):
    lead = FakeAgent(
        replies=[decomposition_json(12)] + ['{"verdict": "accept"}'] * MAX_WORKERS
    )
    emitter = FakeEmitter()

    asyncio.run(run_orchestrated(lead, emitter, "task", "/workspace"))

    assert len(patched_worker) == MAX_WORKERS


def test_duplicate_worker_ids_are_dropped(patched_worker):
    """Two workers sharing an id would share a scope directory."""
    payload = json.dumps(
        {
            "workers": [
                {"id": "same", "title": "A", "brief": "a", "kind": "research"},
                {"id": "same", "title": "B", "brief": "b", "kind": "research"},
                {"id": "other", "title": "C", "brief": "c", "kind": "research"},
            ]
        }
    )
    lead = FakeAgent(replies=[payload, '{"verdict": "accept"}', '{"verdict": "accept"}'])
    emitter = FakeEmitter()

    asyncio.run(run_orchestrated(lead, emitter, "task", "/workspace"))

    assert len(patched_worker) == 2


# ---------------------------------------------------------------------------
# Least authority
# ---------------------------------------------------------------------------


def test_each_worker_gets_its_own_scope_directory():
    assert WorkerBrief(id="find-refs", title="t", brief="b").scope_dir() == (
        ".agents/find-refs"
    )


@pytest.mark.parametrize(
    "raw_id, expected",
    [
        ("../escape", ".agents/escape"),
        ("../../etc/passwd", ".agents/etc-passwd"),
        ("A/B", ".agents/a-b"),
        ("", ".agents/worker"),
        ("...", ".agents/worker"),
    ],
)
def test_scope_directory_cannot_escape_the_agents_folder(raw_id, expected):
    scope = WorkerBrief(id=raw_id, title="t", brief="b").scope_dir()

    assert scope == expected
    assert ".." not in scope
    assert scope.startswith(".agents/")


def test_a_research_worker_cannot_run_shell_commands():
    brief = WorkerBrief(id="w", title="t", brief="b", kind="research")

    assert "bash" not in brief.allowed_tools()
    assert "bash" in _denied_tools(brief)
    assert "web_search" in brief.allowed_tools()


def test_no_worker_may_ask_the_human_or_plan():
    for kind in ("research", "code", "write", "analysis"):
        allowed = WorkerBrief(id="w", title="t", brief="b", kind=kind).allowed_tools()
        assert "ask_human" not in allowed
        assert "planning" not in allowed


def test_terminate_is_never_denied():
    """A worker needs terminate to end its own loop."""
    brief = WorkerBrief(id="w", title="t", brief="b", kind="research")

    assert "terminate" not in _denied_tools(brief)


def test_an_unknown_kind_falls_back_to_the_narrowest_profile():
    brief = WorkerBrief(id="w", title="t", brief="b", kind="wizardry")

    assert brief.allowed_tools() == WorkerBrief(
        id="w", title="t", brief="b", kind="research"
    ).allowed_tools()


def test_workspace_disabled_tools_are_carried_into_workers(patched_worker):
    lead = FakeAgent(replies=[decomposition_json(2), '{"verdict":"accept"}'] + ['{"verdict":"accept"}'])
    emitter = FakeEmitter()

    asyncio.run(
        run_orchestrated(
            lead, emitter, "task", "/workspace", disabled_tools={"browser_use"}
        )
    )

    for kwargs in patched_worker:
        assert "browser_use" in kwargs["disabled_tools"]


def test_worker_prompt_pins_the_worker_to_its_scope(patched_worker):
    lead = FakeAgent(replies=[decomposition_json(2)] + ['{"verdict":"accept"}'] * 2)
    emitter = FakeEmitter()

    asyncio.run(run_orchestrated(lead, emitter, "task", "/workspace"))

    scope = emitter.payload("orchestration_planned")["workers"][0]["scope"]
    assert scope == ".agents/w0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_summarize_collapses_whitespace_and_truncates():
    assert _summarize("a\n\n  b") == "a b"
    assert _summarize("x" * 100, limit=10).endswith("…")
    assert len(_summarize("x" * 100, limit=10)) == 10
