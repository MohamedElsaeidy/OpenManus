from app.agent.trust_ledger import TrustLedger, TrustLedgerEntry
from app.schema import VerificationVerdict
from server.models import TrustLedgerEntryORM


def _entry(agent_name: str, verified: bool) -> TrustLedgerEntry:
    return TrustLedgerEntry(
        agent_name=agent_name,
        verdict=VerificationVerdict(
            verified=verified,
            reason="passed" if verified else "rejected",
        ),
    )


def test_verify_chain_detects_tampering():
    ledger = TrustLedger()
    ledger.append(_entry("manus", True))
    ledger.append(_entry("manus", False))

    assert ledger.verify_chain() is True
    ledger._entries[0].agent_name = "altered"
    assert ledger.verify_chain() is False


def test_trust_score_moves_with_verification_outcomes():
    ledger = TrustLedger(trust_alpha=0.2, initial_trust=0.5)

    ledger.append(_entry("manus", False))
    rejected_score = ledger.trust_score("manus")
    ledger.append(_entry("manus", True))
    recovered_score = ledger.trust_score("manus")

    assert rejected_score < 0.5
    assert recovered_score > rejected_score


def test_database_uniqueness_is_scoped_to_conversation():
    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in TrustLedgerEntryORM.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert unique_constraints["uq_trust_ledger_conv_prev_hash"] == (
        "conversation_id",
        "prev_hash",
    )
    assert unique_constraints["uq_trust_ledger_conv_entry_hash"] == (
        "conversation_id",
        "entry_hash",
    )
    assert TrustLedgerEntryORM.entry_hash.property.columns[0].unique is not True
