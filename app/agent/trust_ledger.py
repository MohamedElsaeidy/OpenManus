from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from app.schema import VerificationVerdict


class TrustLedgerEntry(BaseModel):
    agent_name: str
    verdict: VerificationVerdict
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    prev_hash: str = ""
    entry_hash: str = ""


class TrustLedger:
    """Tamper-evident verification history with per-agent EMA trust scores."""

    GENESIS_HASH = "0" * 64

    def __init__(
        self,
        *,
        conversation_id: Any = None,
        session_factory: Optional[Callable[[], Any]] = None,
        orm_model: Any = None,
        trust_alpha: float = 0.2,
        initial_trust: float = 0.5,
    ) -> None:
        if not 0 < trust_alpha <= 1:
            raise ValueError("trust_alpha must be in the interval (0, 1]")
        if not 0 <= initial_trust <= 1:
            raise ValueError("initial_trust must be in the interval [0, 1]")
        self._entries: list[TrustLedgerEntry] = []
        self._conversation_id = conversation_id
        self._session_factory = session_factory
        self._orm_model = orm_model
        self._trust_alpha = trust_alpha
        self._initial_trust = initial_trust
        if all((conversation_id is not None, session_factory, orm_model)):
            self._load()

    @property
    def entries(self) -> list[TrustLedgerEntry]:
        return list(self._entries)

    @staticmethod
    def _normalized_timestamp(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")

    @classmethod
    def _calculate_hash(cls, entry: TrustLedgerEntry) -> str:
        payload = {
            "agent_name": entry.agent_name,
            "verdict": entry.verdict.model_dump(mode="json"),
            "timestamp": cls._normalized_timestamp(entry.timestamp),
            "prev_hash": entry.prev_hash,
        }
        canonical = json.dumps(
            payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def append(self, entry: TrustLedgerEntry) -> TrustLedgerEntry:
        if not self.verify_chain():
            raise ValueError("Cannot append to a trust ledger with a broken hash chain")
        prev_hash = self._entries[-1].entry_hash if self._entries else self.GENESIS_HASH
        chained = entry.model_copy(update={"prev_hash": prev_hash, "entry_hash": ""})
        chained = chained.model_copy(
            update={"entry_hash": self._calculate_hash(chained)}
        )
        self._persist(chained)
        self._entries.append(chained)
        return chained

    def verify_chain(self) -> bool:
        expected_prev = self.GENESIS_HASH
        for entry in self._entries:
            if entry.prev_hash != expected_prev:
                return False
            if entry.entry_hash != self._calculate_hash(entry):
                return False
            expected_prev = entry.entry_hash
        return True

    def trust_score(self, agent_name: str) -> float:
        score = self._initial_trust
        for entry in self._entries:
            if entry.agent_name != agent_name or entry.verdict.verified is None:
                continue
            outcome = 1.0 if entry.verdict.verified else 0.0
            score = self._trust_alpha * outcome + (1 - self._trust_alpha) * score
        return score

    def _load(self) -> None:
        with self._session_factory() as session:
            rows = (
                session.query(self._orm_model)
                .filter(self._orm_model.conversation_id == self._conversation_id)
                .order_by(self._orm_model.entry_id.asc())
                .all()
            )
            self._entries = [
                TrustLedgerEntry(
                    agent_name=row.agent_name,
                    verdict=VerificationVerdict.model_validate(row.verdict),
                    timestamp=row.timestamp,
                    prev_hash=row.prev_hash,
                    entry_hash=row.entry_hash,
                )
                for row in rows
            ]

    def _persist(self, entry: TrustLedgerEntry) -> None:
        if not all(
            (
                self._conversation_id is not None,
                self._session_factory,
                self._orm_model,
            )
        ):
            return
        with self._session_factory() as session:
            session.add(
                self._orm_model(
                    conversation_id=self._conversation_id,
                    agent_name=entry.agent_name,
                    verdict=entry.verdict.model_dump(mode="json"),
                    timestamp=entry.timestamp,
                    prev_hash=entry.prev_hash,
                    entry_hash=entry.entry_hash,
                )
            )
            session.commit()
