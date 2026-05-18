from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

from app.config import config
from app.utils.logger import logger


@dataclass
class MemoryHit:
    title: str
    content: str
    score: float


class AgentMemoryClient:
    """Robust, zero-dependency local SQLite-based AgentMemory implementation.
    Bypasses network, container dependency, and onboarding flows.
    """

    def __init__(self) -> None:
        self.settings = config.agentmemory
        self.enabled_in_config = self.settings.enabled
        self.project = self.settings.project
        # Store SQLite DB in the shared persistent workspace root
        self.db_path = "/app/workspace/agentmemory.db"
        self._init_db()

    @property
    def enabled(self) -> bool:
        return bool(self.enabled_in_config)

    def _get_conn(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        if not self.enabled:
            return
        try:
            with self._get_conn() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS memories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        conversation_id TEXT NOT NULL,
                        project TEXT NOT NULL,
                        title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        concepts TEXT,
                        metadata TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                # Try to create FTS5 virtual table for full-text search and BM25 ranking
                try:
                    conn.execute("""
                        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                            title, content, tokenize="unicode61"
                        )
                    """)
                    # Create triggers to sync FTS5 table
                    conn.execute("""
                        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                            INSERT INTO memories_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
                        END
                    """)
                    conn.execute("""
                        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                            INSERT INTO memories_fts(memories_fts, rowid, title, content) VALUES('delete', old.id, old.title, old.content);
                        END
                    """)
                    conn.execute("""
                        CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                            INSERT INTO memories_fts(memories_fts, rowid, title, content) VALUES('delete', old.id, old.title, old.content);
                            INSERT INTO memories_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
                        END
                    """)
                except Exception as fts_exc:
                    logger.warning(f"SQLite FTS5 not available, falling back to substring matching: {fts_exc}")
        except Exception as exc:
            logger.error(f"Failed to initialize local AgentMemory DB: {exc}")

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

        try:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO memories (conversation_id, project, title, content, concepts, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        self.project,
                        title,
                        content,
                        json.dumps(concepts or []),
                        json.dumps(metadata or {}),
                    )
                )
                conn.commit()
            return True
        except Exception as exc:
            logger.error(f"Local AgentMemory remember failed: {exc}")
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

        limit_val = int(limit or self.settings.top_k)
        hits: list[MemoryHit] = []

        try:
            with self._get_conn() as conn:
                # 1. Try FTS5 BM25 search
                try:
                    # Sanitize FTS5 query to avoid syntax errors on special chars
                    sanitized_words = [f'"{word}"' for word in query.split() if len(word) > 1]
                    if not sanitized_words:
                        sanitized_words = [f'"{query}"']
                    sanitized_query = " OR ".join(sanitized_words)

                    cursor = conn.execute(
                        """
                        SELECT m.title, m.content, bm25(memories_fts) as score
                        FROM memories m
                        JOIN memories_fts f ON m.id = f.rowid
                        WHERE m.conversation_id = ? AND m.project = ? AND memories_fts MATCH ?
                        ORDER BY score ASC
                        LIMIT ?
                        """,
                        (conversation_id, self.project, sanitized_query, limit_val)
                    )
                    rows = cursor.fetchall()
                    for row in rows:
                        # FTS5 bm25 returns negative values (lower is better), convert to positive score
                        score = abs(float(row['score']))
                        hits.append(MemoryHit(title=row['title'], content=row['content'], score=score))
                except Exception as fts_err:
                    # 2. Substring matching fallback
                    logger.debug(f"FTS5 search query failed, using fallback: {fts_err}")
                    words = [w.lower() for w in query.split() if len(w) > 1]
                    if not words:
                        words = [query.lower()]

                    cursor = conn.execute(
                        """
                        SELECT title, content
                        FROM memories
                        WHERE conversation_id = ? AND project = ?
                        """,
                        (conversation_id, self.project)
                    )
                    rows = cursor.fetchall()
                    
                    scored_rows = []
                    for row in rows:
                        title_lower = row['title'].lower()
                        content_lower = row['content'].lower()
                        score = 0.0
                        for word in words:
                            if word in title_lower:
                                score += 2.0
                            if word in content_lower:
                                score += 1.0
                        if score > 0:
                            scored_rows.append((row['title'], row['content'], score))
                    
                    # Sort descending by score
                    scored_rows.sort(key=lambda x: x[2], reverse=True)
                    for r_title, r_content, r_score in scored_rows[:limit_val]:
                        hits.append(MemoryHit(title=r_title, content=r_content, score=r_score))
        except Exception as exc:
            logger.error(f"Local AgentMemory search failed: {exc}")
            
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

