from types import SimpleNamespace

import pytest

from server.vault_rag import VaultRAG


@pytest.mark.asyncio
async def test_two_hop_retrieval_cites_distant_note():
    notes = [
        SimpleNamespace(
            note_id="seed",
            title="Architecture",
            path="architecture.md",
            content="The service design links to deployment planning.",
        ),
        SimpleNamespace(
            note_id="middle",
            title="Deployment",
            path="deployment.md",
            content="Deployment planning links to the regional runbook.",
        ),
        SimpleNamespace(
            note_id="distant",
            title="Regional Runbook",
            path="regional-runbook.md",
            content="The production region is Frankfurt.",
        ),
        SimpleNamespace(
            note_id="other",
            title="Unrelated",
            path="unrelated.md",
            content="Unrelated material.",
        ),
    ]
    edges = [
        SimpleNamespace(source_note_id="seed", target_note_id="middle"),
        SimpleNamespace(source_note_id="middle", target_note_id="distant"),
    ]

    def embed(text: str) -> list[float]:
        if "architecture" in text.lower() or "service design" in text.lower():
            return [1.0, 0.0]
        return [0.0, 1.0]

    async def answer(question: str, context: str) -> str:
        assert "Regional Runbook" in context
        return "The production region is Frankfurt [[Regional Runbook]]."

    rag = VaultRAG(
        graph_loader=lambda _: (notes, edges),
        embedder=embed,
        answerer=answer,
        top_k=1,
    )
    result = await rag.answer_from_vault(
        "conversation", "Explain the service design region", max_hops=2
    )

    assert result["hops_used"] == 2
    assert "distant" in result["cited_notes"]
    assert result["cited_notes"] != ["seed"]
