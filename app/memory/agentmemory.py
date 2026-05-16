from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.config import config
from app.utils.logger import logger


@dataclass
class MemoryHit:
    title: str
    content: str
    score: float


class AgentMemoryClient:
    """Thin REST client for https://github.com/rohitg00/agentmemory."""

    def __init__(self) -> None:
        self.settings = config.agentmemory
        self.base_url = self.settings.base_url.rstrip("/")
        self.project = self.settings.project
        self.timeout = max(1, int(self.settings.timeout_seconds))

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled and self.base_url)

    def remember(
        self,
        *,
        conversation_id: str,
        content: str,
        title: str = "OpenManus memory",
        concepts: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        if not self.enabled or not content.strip():
            return False

        payload = {
            "project": self.project,
            "sessionId": conversation_id,
            "title": title[:180],
            "content": content[:12000],
            "concepts": concepts or [],
            "metadata": metadata or {},
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/agentmemory/remember",
                    json=payload,
                )
                response.raise_for_status()
                return True
        except Exception as exc:
            logger.warning(f"AgentMemory remember failed: {exc}")
            return False

    def search(
        self,
        *,
        conversation_id: str,
        query: str,
        limit: Optional[int] = None,
    ) -> list[MemoryHit]:
        if not self.enabled or not query.strip():
            return []

        payload = {
            "project": self.project,
            "sessionId": conversation_id,
            "query": query[:1000],
            "limit": int(limit or self.settings.top_k),
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/agentmemory/smart-search",
                    json=payload,
                )
                response.raise_for_status()
                body = response.json() if response.content else {}
        except Exception as exc:
            logger.warning(f"AgentMemory smart-search failed: {exc}")
            return []

        raw_results = body.get("results") or body.get("items") or []
        hits: list[MemoryHit] = []
        for item in raw_results[: payload["limit"]]:
            title = str(item.get("title") or item.get("name") or "Memory")
            content = str(item.get("content") or item.get("text") or "")
            try:
                score = float(item.get("score", 0.0))
            except Exception:
                score = 0.0
            if content.strip():
                hits.append(MemoryHit(title=title, content=content, score=score))
        return hits

    def format_context(
        self,
        *,
        conversation_id: str,
        query: str,
        limit: Optional[int] = None,
    ) -> str:
        hits = self.search(conversation_id=conversation_id, query=query, limit=limit)
        if not hits:
            return ""
        lines = [
            "AgentMemory recall context:",
            "- Relevant long-term memory snippets for this conversation:",
        ]
        for idx, hit in enumerate(hits, start=1):
            lines.append(
                f"  {idx}. {hit.title} (score={hit.score:.3f}): {hit.content[:700]}"
            )
        return "\n".join(lines)


agentmemory = AgentMemoryClient()
