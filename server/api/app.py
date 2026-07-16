from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from server.api.deps import registry
from server.api.routers import (
    auth,
    models_llm,
    admin,
    tools_skills,
    conversations,
    obsidian,
    sandbox,
    tasks,
    workspace,
    health,
)

app = FastAPI(title="OpenManus Task API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _ensure_schema_updates() -> None:
    from server.models import Base

    Base.metadata.create_all(bind=registry.engine)
    with registry.engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS model VARCHAR")
        )
        connection.execute(
            text(
                "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS settings JSONB NOT NULL DEFAULT '{}'::jsonb"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_conversation_events_conversation_created "
                "ON conversation_events (conversation_id, created_at, event_id)"
            )
        )

        # Ensure Obsidian unique constraints exist
        note_constraint = connection.execute(
            text(
                "SELECT 1 FROM information_schema.table_constraints WHERE constraint_name='uq_obsidian_note_conv_path'"
            )
        ).fetchone()
        if not note_constraint:
            connection.execute(
                text(
                    "DELETE FROM obsidian_notes a USING obsidian_notes b "
                    "WHERE a.note_id < b.note_id AND a.conversation_id = b.conversation_id AND a.path = b.path"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE obsidian_notes ADD CONSTRAINT uq_obsidian_note_conv_path UNIQUE (conversation_id, path)"
                )
            )

        edge_constraint = connection.execute(
            text(
                "SELECT 1 FROM information_schema.table_constraints WHERE constraint_name='uq_obsidian_edge_conv_src_tgt_rel'"
            )
        ).fetchone()
        if not edge_constraint:
            connection.execute(
                text(
                    "DELETE FROM obsidian_edges a USING obsidian_edges b "
                    "WHERE a.edge_id < b.edge_id AND a.conversation_id = b.conversation_id "
                    "AND a.source_note_id = b.source_note_id AND a.target_note_id = b.target_note_id "
                    "AND a.relation = b.relation"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE obsidian_edges ADD CONSTRAINT uq_obsidian_edge_conv_src_tgt_rel "
                    "UNIQUE (conversation_id, source_note_id, target_note_id, relation)"
                )
            )

# Run schema migrations
_ensure_schema_updates()

# Register all routes via router packages
app.include_router(auth.router)
app.include_router(models_llm.router)
app.include_router(admin.router)
app.include_router(tools_skills.router)
app.include_router(conversations.router)
app.include_router(obsidian.router)
app.include_router(sandbox.router)
app.include_router(tasks.router)
app.include_router(workspace.router)
app.include_router(health.router)
