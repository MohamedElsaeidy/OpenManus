"""Fan-out / join orchestration.

The lead agent — the same agent that would have answered directly — splits a
request into independent worker briefs, the workers run in parallel under a
reduced authority, the lead reviews every result, and the lead writes the
delivery. Nothing else in the product changes: orchestration is a strategy for
one turn, not a second agent runtime.

Three rules keep this from becoming a liability:

1. *Degrade to direct.* If decomposition fails, or the work does not actually
   split, the turn falls back to a normal single-agent run. Orchestration must
   never make a request worse than not using it.
2. *Least authority.* A worker gets an explicit tool allowlist and its own
   workspace subdirectory. It cannot orchestrate, so the fan-out is one level
   deep by construction.
3. *The lead owns the result.* Worker output is evidence, not truth. The lead
   reviews each result and can reject it; only accepted work reaches the
   synthesis.

Fan-out only pays off when subtasks are genuinely independent — a lead that
splits sequential work just pays the token multiplier for nothing. The
decomposition prompt says so, and `MAX_WORKERS` caps the damage when the model
ignores it.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from app.logger import logger


# Fan-out costs roughly one agent run per worker. Past a handful the lead
# spends more effort reviewing than the split saves.
MAX_WORKERS = 5
MAX_CONCURRENCY = 3
WORKER_STEP_BUDGET = 10

# Tools a worker may never hold, whatever the lead asks for. Termination is
# driven by the worker loop itself, and nothing below may spawn more agents.
WORKER_TOOL_DENYLIST = frozenset({"ask_human", "wait_for_user_input", "planning"})

# What a worker is allowed to ask for, keyed by the kind of job it was given.
# Anything outside the union is denied regardless of what the lead requests.
WORKER_TOOL_PROFILES: dict[str, frozenset] = {
    "research": frozenset({"web_search", "browser_use", "read_files", "memory_recall"}),
    "code": frozenset(
        {
            "read_files",
            "glob",
            "grep",
            "codebase_overview",
            "line_edit",
            "apply_patch_editor",
            "python_execute",
            "bash",
        }
    ),
    "write": frozenset(
        {"read_files", "glob", "grep", "line_edit", "apply_patch_editor"}
    ),
    "analysis": frozenset({"read_files", "glob", "grep", "python_execute", "bash"}),
}

DECOMPOSE_SYSTEM = """You plan parallel work for a team of worker agents.

Split the request into INDEPENDENT subtasks that can run at the same time
without waiting for each other. Independence is the only reason to split:
if step B needs step A's output, they belong in ONE subtask, not two.

Return between 0 and {max_workers} workers. Return an EMPTY list when the
request is small, conversational, or inherently sequential — one capable agent
handles that better than a team.

Each worker gets a fresh agent with no memory of this conversation, so its
`brief` must be self-contained: state the goal, the inputs, and exactly what to
produce. Name the file it should write under its own directory.

Reply with ONLY a JSON object, no prose and no code fence:
{{"reasoning": "<one sentence on why this split is independent, or why you returned none>",
  "workers": [{{"id": "kebab-case-id",
               "title": "<short label>",
               "kind": "research|code|write|analysis",
               "brief": "<self-contained instructions>",
               "deliverable": "<relative path the worker must write>"}}]}}"""

REVIEW_SYSTEM = """You are reviewing one worker's result against its brief.

Accept only if the deliverable answers the brief. Reject vague summaries,
unfinished work, or claims with no evidence behind them. A worker saying it
succeeded is not evidence.

Reply with ONLY a JSON object, no prose and no code fence:
{"verdict": "accept|reject", "reason": "<one sentence>", "guidance": "<if reject, what to do differently>"}"""


class WorkerBrief(BaseModel):
    """One unit of independent work, as the lead specified it."""

    id: str
    title: str
    kind: str = "research"
    brief: str
    deliverable: str = ""

    def scope_dir(self) -> str:
        """Worker-private directory, relative to the workspace root."""
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", self.id).strip("-").lower() or "worker"
        return f".agents/{safe}"

    def allowed_tools(self) -> frozenset:
        profile = WORKER_TOOL_PROFILES.get(
            self.kind.lower(), WORKER_TOOL_PROFILES["research"]
        )
        return profile - WORKER_TOOL_DENYLIST


class Decomposition(BaseModel):
    reasoning: str = ""
    workers: list[WorkerBrief] = Field(default_factory=list)


class WorkerOutcome(BaseModel):
    brief: WorkerBrief
    status: str = "completed"
    output: str = ""
    verdict: str = "accept"
    review_reason: str = ""


def _extract_json(text: str) -> Optional[dict]:
    """Pull a JSON object out of a model reply that may be fenced or chatty."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    # Fall back to the outermost braces, which survives a leading sentence.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


async def _decompose(lead: Any, prompt: str) -> Decomposition:
    """Ask the lead how — and whether — to split the request."""
    reply = await lead.llm.ask(
        messages=[{"role": "user", "content": prompt}],
        system_msgs=[
            {
                "role": "system",
                "content": DECOMPOSE_SYSTEM.format(max_workers=MAX_WORKERS),
            }
        ],
        stream=False,
    )
    payload = _extract_json(reply)
    if payload is None:
        logger.warning("Orchestration: decomposition was not JSON; running direct.")
        return Decomposition()
    try:
        decomposition = Decomposition.model_validate(payload)
    except ValidationError as exc:
        logger.warning(f"Orchestration: decomposition failed validation: {exc}")
        return Decomposition()

    # De-duplicate ids so two workers cannot share a scope directory.
    seen: set[str] = set()
    unique: list[WorkerBrief] = []
    for worker in decomposition.workers:
        if worker.id in seen or not worker.brief.strip():
            continue
        seen.add(worker.id)
        unique.append(worker)
    decomposition.workers = unique[:MAX_WORKERS]
    return decomposition


def _worker_prompt(brief: WorkerBrief) -> str:
    scope = brief.scope_dir()
    deliverable = brief.deliverable or "findings.md"
    return (
        f"{brief.brief}\n\n"
        f"--- Working agreement ---\n"
        f"You are one worker on a larger task. Other workers are handling other "
        f"parts in parallel; you cannot see or talk to them.\n"
        f"Write every file you produce under `{scope}/`. "
        f"Your deliverable is `{scope}/{deliverable}`.\n"
        f"Do not modify files outside `{scope}/`.\n"
        f"When you are done, state in one paragraph what you produced and where "
        f"it is. Be concrete: a reviewer will check the file against this brief."
    )


async def _run_worker(
    brief: WorkerBrief,
    emitter: Any,
    workspace_root: str,
    disabled_tools: set,
    semaphore: asyncio.Semaphore,
) -> WorkerOutcome:
    from app.agent.manus import Manus
    from app.tool import ToolCollection  # noqa: F401  (kept for tool-name discovery)

    async with semaphore:
        if emitter.is_interrupted():
            return WorkerOutcome(brief=brief, status="cancelled", output="")

        emitter.emit(
            "worker_started",
            {
                "worker_id": brief.id,
                "title": brief.title,
                "kind": brief.kind,
                "brief": brief.brief,
                "scope": brief.scope_dir(),
            },
        )

        try:
            worker = await Manus.create(
                workspace_root=workspace_root,
                disabled_tools=disabled_tools,
                max_steps=WORKER_STEP_BUDGET,
            )
            output = await worker.run(emitter, _worker_prompt(brief))
            status = "completed"
        except Exception as exc:  # a failed worker must not sink the whole run
            logger.error(f"Orchestration: worker {brief.id} failed: {exc}")
            output = f"This worker failed: {exc}"
            status = "failed"

        emitter.emit(
            "worker_completed",
            {
                "worker_id": brief.id,
                "title": brief.title,
                "status": status,
                "summary": _summarize(output),
            },
        )
        return WorkerOutcome(brief=brief, status=status, output=output)


def _summarize(text: str, limit: int = 600) -> str:
    collapsed = " ".join(str(text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


async def _review(lead: Any, outcome: WorkerOutcome, emitter: Any) -> WorkerOutcome:
    """Lead accepts or rejects one worker's result before it can be used."""
    if outcome.status != "completed":
        outcome.verdict = "reject"
        outcome.review_reason = "The worker did not finish."
    else:
        reply = await lead.llm.ask(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Brief:\n{outcome.brief.brief}\n\n"
                        f"Expected deliverable: {outcome.brief.scope_dir()}/"
                        f"{outcome.brief.deliverable or 'findings.md'}\n\n"
                        f"Worker's report:\n{_summarize(outcome.output, 4000)}"
                    ),
                }
            ],
            system_msgs=[{"role": "system", "content": REVIEW_SYSTEM}],
            stream=False,
        )
        payload = _extract_json(reply) or {}
        # An unparseable review must not silently discard real work.
        outcome.verdict = "reject" if payload.get("verdict") == "reject" else "accept"
        outcome.review_reason = str(payload.get("reason") or "")

    emitter.emit(
        "worker_reviewed",
        {
            "worker_id": outcome.brief.id,
            "title": outcome.brief.title,
            "verdict": outcome.verdict,
            "reason": outcome.review_reason,
        },
    )
    return outcome


def _synthesis_prompt(prompt: str, outcomes: list[WorkerOutcome]) -> str:
    accepted = [o for o in outcomes if o.verdict == "accept"]
    rejected = [o for o in outcomes if o.verdict != "accept"]

    sections = [
        "Your team has finished. Write the final answer to the original request.",
        f"\nOriginal request:\n{prompt}\n",
        "\n--- Accepted worker results ---",
    ]
    for outcome in accepted:
        sections.append(
            f"\n[{outcome.brief.title}] (files under {outcome.brief.scope_dir()}/)\n"
            f"{_summarize(outcome.output, 2500)}"
        )
    if rejected:
        sections.append("\n--- Rejected or failed, do not rely on these ---")
        for outcome in rejected:
            sections.append(f"\n[{outcome.brief.title}] {outcome.review_reason}")
    sections.append(
        "\n\nRead the workers' files before you rely on them — their summaries "
        "are claims, not proof. Produce the deliverable the user asked for, and "
        "say plainly what is missing if a worker failed."
    )
    return "\n".join(sections)


async def run_orchestrated(
    lead: Any,
    emitter: Any,
    prompt: str,
    workspace_root: str,
    disabled_tools: Optional[set] = None,
) -> str:
    """Run one turn as lead + parallel workers, or fall back to a direct run."""
    disabled_tools = set(disabled_tools or ())

    decomposition = await _decompose(lead, prompt)

    # Splitting into one worker is strictly worse than answering directly: same
    # work, plus a hand-off and a review.
    if len(decomposition.workers) < 2:
        emitter.emit(
            "orchestration_skipped",
            {"reason": decomposition.reasoning or "The request did not split."},
        )
        return await lead.run(emitter, prompt)

    emitter.emit(
        "orchestration_planned",
        {
            "reasoning": decomposition.reasoning,
            "workers": [
                {
                    "worker_id": worker.id,
                    "title": worker.title,
                    "kind": worker.kind,
                    "brief": worker.brief,
                    "scope": worker.scope_dir(),
                }
                for worker in decomposition.workers
            ],
        },
    )

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    outcomes = await asyncio.gather(
        *(
            _run_worker(
                brief=worker,
                emitter=emitter,
                workspace_root=workspace_root,
                # Least authority: everything outside this worker's profile is
                # off, on top of whatever the workspace already disables.
                disabled_tools=disabled_tools | _denied_tools(worker),
                semaphore=semaphore,
            )
            for worker in decomposition.workers
        )
    )

    reviewed = [await _review(lead, outcome, emitter) for outcome in outcomes]

    emitter.emit(
        "orchestration_joined",
        {
            "accepted": sum(1 for o in reviewed if o.verdict == "accept"),
            "rejected": sum(1 for o in reviewed if o.verdict != "accept"),
            "total": len(reviewed),
        },
    )

    return await lead.run(emitter, _synthesis_prompt(prompt, reviewed))


def _denied_tools(brief: WorkerBrief) -> set:
    """Every known tool the worker is not explicitly allowed to hold."""
    from server.api.deps import AVAILABLE_TOOLS

    allowed = brief.allowed_tools()
    known = {str(tool["name"]) for tool in AVAILABLE_TOOLS}
    # terminate is how a worker ends its own loop, so it always stays.
    return {name for name in known if name not in allowed and name != "terminate"}
