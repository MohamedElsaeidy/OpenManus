"""OpenHands-compatible skill discovery for OpenManus.

Skills are Markdown files with optional YAML frontmatter. Repo or workspace
owners can place them under `.openhands/skills` or `.openhands/microagents`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml


@dataclass
class Skill:
    name: str
    path: str
    body: str
    type: str = "knowledge"
    version: str = "1.0"
    agent: str = "Manus"
    triggers: list[str] | None = None

    def matches(self, text: str) -> bool:
        haystack = text.lower()
        return any(trigger.lower() in haystack for trigger in self.triggers or [])

    def summary(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "type": self.type,
            "version": self.version,
            "agent": self.agent,
            "triggers": self.triggers or [],
        }


def _parse_skill(path: Path) -> Skill | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None

    metadata: dict = {}
    body = raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            try:
                metadata = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                metadata = {}
            body = parts[2].strip()

    name = str(metadata.get("name") or path.stem).strip()
    triggers = metadata.get("triggers") or []
    if isinstance(triggers, str):
        triggers = [triggers]
    return Skill(
        name=name,
        path=str(path),
        body=body.strip(),
        type=str(metadata.get("type") or "knowledge"),
        version=str(metadata.get("version") or "1.0"),
        agent=str(metadata.get("agent") or "Manus"),
        triggers=[str(item) for item in triggers],
    )


def skill_roots(workspace_root: str | Path | None = None) -> list[Path]:
    roots = [
        Path.cwd() / ".openhands" / "skills",
        Path.cwd() / ".openhands" / "microagents",
        Path.cwd() / "skills",
        Path.cwd() / "vendor" / "everything-claude-code" / "skills",
        Path.cwd() / "vendor" / "everything-claude-code" / "agents-skills",
        Path.cwd() / "vendor" / "everything-claude-code" / "agents",
    ]
    if workspace_root:
        workspace = Path(workspace_root)
        roots.extend(
            [
                workspace / ".openhands" / "skills",
                workspace / ".openhands" / "microagents",
                workspace / "skills",
                workspace / "vendor" / "everything-claude-code" / "skills",
                workspace / "vendor" / "everything-claude-code" / "agents-skills",
                workspace / "vendor" / "everything-claude-code" / "agents",
            ]
        )
    return roots


def load_skills(workspace_root: str | Path | None = None) -> list[Skill]:
    seen: set[Path] = set()
    skills: list[Skill] = []
    for root in skill_roots(workspace_root):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            skill = _parse_skill(path)
            if skill is not None:
                skills.append(skill)
    return skills


def select_skills(
    prompt: str, workspace_root: str | Path | None = None, limit: int = 6
) -> list[Skill]:
    skills = load_skills(workspace_root)
    always = [skill for skill in skills if skill.type == "repo" or not skill.triggers]
    matched = [skill for skill in skills if skill.triggers and skill.matches(prompt)]
    selected: list[Skill] = []
    for skill in [*always, *matched]:
        if skill.name in {item.name for item in selected}:
            continue
        selected.append(skill)
        if len(selected) >= limit:
            break
    return selected


def format_skill_context(skills: Iterable[Skill]) -> str:
    chunks = []
    for skill in skills:
        chunks.append(f"Skill: {skill.name}\n{skill.body}")
    if not chunks:
        return ""
    return (
        "Relevant OpenHands-style skills loaded for this conversation:\n\n"
        + "\n\n---\n\n".join(chunks)
    )
