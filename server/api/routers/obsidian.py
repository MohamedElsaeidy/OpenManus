import logging
import re
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


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/conversations", tags=["obsidian"])

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[^\]]*)\]\]")


def _extract_wikilinks(content: str) -> list[str]:
    if not content:
        return []
    out: list[str] = []
    for match in WIKILINK_RE.findall(content):
        normalized = str(match).strip()
        if normalized:
            out.append(normalized)
    return out


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

        # Rebuild note maps
        all_notes = (
            session.query(ObsidianNoteORM)
            .filter(ObsidianNoteORM.conversation_id == cid)
            .all()
        )
        all_by_path = {note.path: note for note in all_notes}
        all_by_path_stem: dict[str, list[ObsidianNoteORM]] = {}
        for note in all_notes:
            stem = note.path
            if stem.endswith(".md"):
                stem = stem[:-3]
            all_by_path_stem.setdefault(stem, []).append(note)
        all_by_basename: dict[str, list[ObsidianNoteORM]] = {}
        for note in all_notes:
            basename = Path(note.path).stem
            all_by_basename.setdefault(basename, []).append(note)
        all_by_title: dict[str, list[ObsidianNoteORM]] = {}
        for note in all_notes:
            all_by_title.setdefault(note.title, []).append(note)

        upserted_ids = {note.note_id for note in upserted}

        desired_edges = set()
        for note in upserted:
            for target_name in _extract_wikilinks(note.content):
                if not target_name:
                    continue
                target = None
                stem_matches = all_by_path_stem.get(target_name, [])
                if len(stem_matches) == 1:
                    target = stem_matches[0]
                elif target_name in all_by_path:
                    target = all_by_path[target_name]
                else:
                    base_matches = all_by_basename.get(target_name, [])
                    if len(base_matches) == 1:
                        target = base_matches[0]
                    else:
                        title_matches = all_by_title.get(target_name, [])
                        if len(title_matches) == 1:
                            target = title_matches[0]
                if target is not None and target.note_id != note.note_id:
                    desired_edges.add((note.note_id, target.note_id, "wikilink"))

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
