from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from app.llm import LLM
from app.memory.agentmemory import agentmemory
from app.schema import Message
from server.models import ObsidianEdgeORM, ObsidianNoteORM


CITATION_RE = re.compile(r"\[\[([^\]]+)\]\]")


@dataclass
class VaultVectorIndex:
    signature: str
    index: Any
    note_ids: list[str]
    notes_by_id: dict[str, Any]


GraphLoader = Callable[[str], tuple[list[Any], list[Any]]]
Embedder = Callable[[str], list[float]]
Answerer = Callable[[str, str], Awaitable[str] | str]


class VaultRAG:
    def __init__(
        self,
        *,
        graph_loader: Optional[GraphLoader] = None,
        embedder: Optional[Embedder] = None,
        answerer: Optional[Answerer] = None,
        top_k: int = 3,
    ) -> None:
        self._graph_loader = graph_loader or self._load_graph
        self._embedder = embedder or agentmemory._get_embedding
        self._answerer = answerer or self._answer_with_llm
        self._top_k = max(1, top_k)
        self._indexes: dict[str, VaultVectorIndex] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _load_graph(conversation_id: str) -> tuple[list[Any], list[Any]]:
        from server.api.deps import registry

        cid = uuid.UUID(str(conversation_id))
        with registry.SessionLocal() as session:
            notes = (
                session.query(ObsidianNoteORM)
                .filter(ObsidianNoteORM.conversation_id == cid)
                .order_by(ObsidianNoteORM.note_id.asc())
                .all()
            )
            edges = (
                session.query(ObsidianEdgeORM)
                .filter(ObsidianEdgeORM.conversation_id == cid)
                .all()
            )
        return notes, edges

    @staticmethod
    def _signature(notes: list[Any]) -> str:
        digest = hashlib.sha256()
        for note in notes:
            digest.update(str(note.note_id).encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(note.title or "").encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(note.content or "").encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()

    def embed_notes(
        self, conversation_id: str, notes: Optional[list[Any]] = None
    ) -> VaultVectorIndex:
        try:
            import faiss
            import numpy as np
        except ImportError as exc:
            raise RuntimeError(f"Vault RAG requires the configured FAISS stack: {exc}")

        if notes is None:
            notes, _ = self._graph_loader(conversation_id)
        if not notes:
            raise ValueError("The conversation vault has no notes to query")

        signature = self._signature(notes)
        with self._lock:
            cached = self._indexes.get(str(conversation_id))
            if cached is not None and cached.signature == signature:
                return cached

        vectors = []
        note_ids = []
        notes_by_id = {}
        dimension: Optional[int] = None
        for note in notes:
            vector = np.asarray(
                self._embedder(f"{note.title}\n{note.content or ''}"),
                dtype=np.float32,
            )
            if vector.ndim != 1 or vector.size == 0:
                raise ValueError(f"Invalid embedding for vault note '{note.title}'")
            if dimension is None:
                dimension = int(vector.size)
            elif vector.size != dimension:
                raise ValueError("Vault note embeddings have inconsistent dimensions")
            norm = float(np.linalg.norm(vector))
            if norm > 0:
                vector = vector / norm
            note_id = str(note.note_id)
            vectors.append(vector)
            note_ids.append(note_id)
            notes_by_id[note_id] = note

        matrix = np.vstack(vectors).astype(np.float32)
        index = faiss.IndexFlatIP(int(dimension or 0))
        index.add(matrix)
        built = VaultVectorIndex(
            signature=signature,
            index=index,
            note_ids=note_ids,
            notes_by_id=notes_by_id,
        )
        with self._lock:
            self._indexes[str(conversation_id)] = built
        return built

    def _retrieve(
        self, conversation_id: str, question: str, max_hops: int
    ) -> tuple[list[Any], int]:
        import numpy as np

        notes, edges = self._graph_loader(conversation_id)
        vector_index = self.embed_notes(conversation_id, notes)
        query_vector = np.asarray(self._embedder(question), dtype=np.float32)
        if query_vector.ndim != 1 or query_vector.size != vector_index.index.d:
            raise ValueError("Question embedding does not match the vault index")
        norm = float(np.linalg.norm(query_vector))
        if norm > 0:
            query_vector = query_vector / norm

        k = min(self._top_k, len(vector_index.note_ids))
        _, positions = vector_index.index.search(query_vector.reshape(1, -1), k)
        seed_ids = [
            vector_index.note_ids[int(position)]
            for position in positions[0]
            if 0 <= int(position) < len(vector_index.note_ids)
        ]

        outward: dict[str, list[str]] = {}
        for edge in edges:
            outward.setdefault(str(edge.source_note_id), []).append(
                str(edge.target_note_id)
            )

        queue = deque((note_id, 0) for note_id in seed_ids)
        depth_by_id = {note_id: 0 for note_id in seed_ids}
        ordered_ids = list(seed_ids)
        while queue:
            note_id, depth = queue.popleft()
            if depth >= max_hops:
                continue
            for target_id in outward.get(note_id, []):
                if (
                    target_id in depth_by_id
                    or target_id not in vector_index.notes_by_id
                ):
                    continue
                depth_by_id[target_id] = depth + 1
                ordered_ids.append(target_id)
                queue.append((target_id, depth + 1))

        collected = [vector_index.notes_by_id[note_id] for note_id in ordered_ids]
        hops_used = max(depth_by_id.values(), default=0)
        return collected, hops_used

    @staticmethod
    def _build_context(notes: list[Any]) -> str:
        parts = []
        used_chars = 0
        for note in notes:
            content = str(note.content or "").strip()[:5000]
            block = (
                f"NOTE id={note.note_id} title={note.title!r} path={note.path!r}\n"
                f"{content}"
            )
            if used_chars + len(block) > 30000:
                break
            parts.append(block)
            used_chars += len(block)
        return "\n\n---\n\n".join(parts)

    @staticmethod
    async def _answer_with_llm(question: str, context: str) -> str:
        return await LLM().ask(
            messages=[
                Message.user_message(f"QUESTION:\n{question}\n\nNOTES:\n{context}")
            ],
            system_msgs=[
                Message.system_message(
                    "Answer only from the supplied vault notes. Cite every factual claim "
                    "with the exact note title in double brackets, for example [[Design "
                    "Notes]]. If the notes do not support an answer, say so. Do not cite "
                    "a note that is not present in the supplied context."
                )
            ],
            stream=False,
            temperature=0.1,
        )

    @staticmethod
    def _validated_citations(answer: str, notes: list[Any]) -> list[str]:
        by_id = {str(note.note_id): str(note.note_id) for note in notes}
        title_ids: dict[str, list[str]] = {}
        for note in notes:
            title_ids.setdefault(str(note.title), []).append(str(note.note_id))

        cited = []
        for raw in CITATION_RE.findall(answer):
            key = raw.split("|", 1)[0].split("#", 1)[0].strip()
            note_id = by_id.get(key)
            if note_id is None and len(title_ids.get(key, [])) == 1:
                note_id = title_ids[key][0]
            if note_id is not None and note_id not in cited:
                cited.append(note_id)
        return cited

    async def answer_from_vault(
        self, conversation_id: str, question: str, max_hops: int = 2
    ) -> dict:
        question = str(question or "").strip()
        if not question:
            raise ValueError("question is required")
        if not 0 <= max_hops <= 4:
            raise ValueError("max_hops must be between 0 and 4")

        notes, hops_used = await asyncio.to_thread(
            self._retrieve, conversation_id, question, max_hops
        )
        context = self._build_context(notes)
        answer = self._answerer(question, context)
        if inspect.isawaitable(answer):
            answer = await answer
        answer_text = str(answer or "").strip()
        if not answer_text:
            raise ValueError("The vault answer model returned an empty response")
        return {
            "answer": answer_text,
            "cited_notes": self._validated_citations(answer_text, notes),
            "hops_used": hops_used,
        }


vault_rag = VaultRAG()


def embed_notes(conversation_id: str) -> VaultVectorIndex:
    return vault_rag.embed_notes(conversation_id)


async def answer_from_vault(
    conversation_id: str, question: str, max_hops: int = 2
) -> dict:
    return await vault_rag.answer_from_vault(conversation_id, question, max_hops)
