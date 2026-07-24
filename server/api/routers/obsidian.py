import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from server.api.deps import (
    WORKSPACE_ROOT,
    _now,
    _require_conversation,
    _require_user,
    registry,
)
from server.models import ConversationORM, ObsidianEdgeORM, ObsidianNoteORM
from server.obsidian_graph import desired_wikilink_edges
from server.vault_rag import answer_from_vault


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/conversations", tags=["obsidian"])


def _obsidian_graph_payload(session, conversation: ConversationORM) -> dict:
    notes = (
        session.query(ObsidianNoteORM)
        .filter(ObsidianNoteORM.conversation_id == conversation.conversation_id)
        .all()
    )
    edges = (
        session.query(ObsidianEdgeORM)
        .filter(ObsidianEdgeORM.conversation_id == conversation.conversation_id)
        .all()
    )
    node_payload = [
        {
            "id": str(note.note_id),
            "path": note.path,
            "title": note.title,
            "tags": note.tags or [],
            "updated_at": note.updated_at.isoformat() if note.updated_at else None,
        }
        for note in notes
    ]
    edge_payload = [
        {
            "id": str(edge.edge_id),
            "source": str(edge.source_note_id),
            "target": str(edge.target_note_id),
            "relation": edge.relation,
        }
        for edge in edges
    ]
    return {
        "nodes": node_payload,
        "edges": edge_payload,
        "node_count": len(node_payload),
        "edge_count": len(edge_payload),
    }


@router.post("/{conversation_id}/obsidian/import")
async def import_obsidian_context(request: Request, conversation_id: str):
    user = _require_user(request)
    body = await request.json()
    notes = body.get("notes") or []
    if not isinstance(notes, list) or not notes:
        raise HTTPException(status_code=400, detail="notes[] is required")

    with registry.SessionLocal() as session:
        conversation = _require_conversation(session, user.user_id, conversation_id)
        cid = conversation.conversation_id
        existing = (
            session.query(ObsidianNoteORM)
            .filter(ObsidianNoteORM.conversation_id == cid)
            .all()
        )
        by_path = {note.path: note for note in existing}

        upserted: list[ObsidianNoteORM] = []
        for raw in notes:
            if not isinstance(raw, dict):
                continue
            path = str(raw.get("path") or raw.get("title") or "").strip()
            title = str(raw.get("title") or Path(path).stem or "").strip()
            content = str(raw.get("content") or "")
            tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
            meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
            if not path or not title:
                continue
            note = by_path.get(path)
            if note is None:
                note = ObsidianNoteORM(
                    conversation_id=cid,
                    path=path,
                    title=title,
                    content=content,
                    tags=tags,
                    meta=meta,
                )
                session.add(note)
            else:
                note.title = title
                note.content = content
                note.tags = tags
                note.meta = meta
            upserted.append(note)
            by_path[path] = note

        session.flush()

        all_notes = (
            session.query(ObsidianNoteORM)
            .filter(ObsidianNoteORM.conversation_id == cid)
            .all()
        )
        upserted_ids = {note.note_id for note in upserted}
        desired_edges = desired_wikilink_edges(upserted, all_notes)

        existing_edge_rows = (
            session.query(ObsidianEdgeORM)
            .filter(
                ObsidianEdgeORM.conversation_id == cid,
                ObsidianEdgeORM.source_note_id.in_(upserted_ids),
            )
            .all()
        )
        existing_edges = {
            (e.source_note_id, e.target_note_id, e.relation): e
            for e in existing_edge_rows
        }

        for key, edge in existing_edges.items():
            if key not in desired_edges:
                session.delete(edge)

        edges_created = 0
        for src_id, tgt_id, relation in desired_edges:
            if (src_id, tgt_id, relation) not in existing_edges:
                session.add(
                    ObsidianEdgeORM(
                        conversation_id=cid,
                        source_note_id=src_id,
                        target_note_id=tgt_id,
                        relation=relation,
                    )
                )
                edges_created += 1

        conversation.updated_at = _now()
        session.commit()

        payload = _obsidian_graph_payload(session, conversation)
        return {
            "conversation_id": conversation_id,
            "imported_notes": len(upserted),
            "edges_created": edges_created,
            "graph": payload,
        }


@router.get("/{conversation_id}/obsidian/graph")
async def get_obsidian_graph(request: Request, conversation_id: str):
    user = _require_user(request)
    workspace_path = Path(WORKSPACE_ROOT) / "conversations" / str(conversation_id)
    try:
        from server.tasks import auto_sync_obsidian_notes

        auto_sync_obsidian_notes(conversation_id, workspace_path)
    except Exception as exc:
        logger.warning(f"Failed to auto-sync obsidian notes on graph query: {exc}")
    with registry.SessionLocal() as session:
        conversation = _require_conversation(session, user.user_id, conversation_id)
        return {
            "conversation_id": conversation_id,
            **_obsidian_graph_payload(session, conversation),
        }


@router.post("/{conversation_id}/obsidian/ask")
async def ask_obsidian_vault(request: Request, conversation_id: str):
    user = _require_user(request)
    body = await request.json()
    question = str(body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    if len(question) > 4000:
        raise HTTPException(status_code=400, detail="question is too long")
    try:
        max_hops = int(body.get("max_hops", 2))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="max_hops must be an integer")

    workspace_path = Path(WORKSPACE_ROOT) / "conversations" / str(conversation_id)
    try:
        from server.tasks import auto_sync_obsidian_notes

        auto_sync_obsidian_notes(conversation_id, workspace_path)
    except Exception as exc:
        logger.warning(f"Failed to auto-sync obsidian notes before vault query: {exc}")

    with registry.SessionLocal() as session:
        _require_conversation(session, user.user_id, conversation_id)
    try:
        return {
            "conversation_id": conversation_id,
            **await answer_from_vault(conversation_id, question, max_hops=max_hops),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Vault RAG query failed")
        raise HTTPException(status_code=502, detail=f"Vault query failed: {exc}")


@router.get("/{conversation_id}/obsidian/context")
async def get_obsidian_context(request: Request, conversation_id: str, limit: int = 8):
    user = _require_user(request)
    workspace_path = Path(WORKSPACE_ROOT) / "conversations" / str(conversation_id)
    try:
        from server.tasks import auto_sync_obsidian_notes

        auto_sync_obsidian_notes(conversation_id, workspace_path)
    except Exception as exc:
        logger.warning(f"Failed to auto-sync obsidian notes on context query: {exc}")
    with registry.SessionLocal() as session:
        conversation = _require_conversation(session, user.user_id, conversation_id)
        notes = (
            session.query(ObsidianNoteORM)
            .filter(ObsidianNoteORM.conversation_id == conversation.conversation_id)
            .order_by(ObsidianNoteORM.updated_at.desc())
            .limit(max(1, min(limit, 30)))
            .all()
        )
    items = []
    for note in notes:
        content = (note.content or "").strip()
        if len(content) > 900:
            content = content[:900] + "..."
        items.append(
            {
                "id": str(note.note_id),
                "path": note.path,
                "title": note.title,
                "tags": note.tags or [],
                "content": content,
            }
        )
    return {"conversation_id": conversation_id, "notes": items, "count": len(items)}
