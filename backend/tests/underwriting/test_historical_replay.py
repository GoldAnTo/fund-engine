from __future__ import annotations

from datetime import UTC, datetime, timedelta
import uuid

import pytest
from sqlalchemy.orm import Session

from app.models.ledger import ConflictError, ValidationError
from app.underwriting.domain import (
    HistoricalBasisInput,
    LedgerEntryInput,
    LedgerKind,
    ResearchObjectKind,
)
from app.underwriting.services.kernel import UnderwritingKernelService, canonical_hash


T1 = datetime(2026, 8, 20, 9, 30, tzinfo=UTC)
T2 = T1 + timedelta(days=1)


@pytest.fixture
def kernel(session: Session) -> UnderwritingKernelService:
    return UnderwritingKernelService(session, now=lambda: T2)


def _entry(
    family_key: str,
    available_at: datetime,
    *,
    effective_at: datetime | None = None,
) -> LedgerEntryInput:
    return LedgerEntryInput(
        LedgerKind.REALITY,
        family_key,
        "reported",
        {"family": family_key},
        effective_at or available_at,
        available_at,
        "public",
    )


def test_snapshot_replays_only_entries_available_at_historical_cutoff(
    kernel: UnderwritingKernelService,
    session: Session,
) -> None:
    research_object = kernel.add_object(
        ResearchObjectKind.COMPANY, "company:1", "Company"
    )
    t1_basis = kernel.add_basis(HistoricalBasisInput(T1, T1, "a" * 64))
    t2_basis = kernel.add_basis(HistoricalBasisInput(T2, T2, "b" * 64))
    visible = kernel.append_ledger_entry(
        research_object.id, t1_basis.id, _entry("revenue", T1), None
    )
    t1_before_successor = kernel.snapshot(research_object.id, t1_basis.id)
    successor = kernel.append_ledger_entry(
        research_object.id, t2_basis.id, _entry("revenue", T2), visible.id
    )
    late_known = kernel.append_ledger_entry(
        research_object.id,
        t2_basis.id,
        _entry("margin", T2, effective_at=T1 - timedelta(days=30)),
        None,
    )

    session.expire_all()
    t1_snapshot = kernel.snapshot(research_object.id, t1_basis.id)
    t2_snapshot = kernel.snapshot(research_object.id, t2_basis.id)

    assert t1_snapshot == t1_before_successor
    assert t1_snapshot.entries == (visible,)
    assert t2_snapshot.entries == (late_known, successor)
    assert t1_snapshot.snapshot_hash == canonical_hash(
        {
            "object_id": research_object.id,
            "basis_cutoff": T1,
            "source_manifest_hash": "a" * 64,
            "entries": [{"id": visible.id, "content_hash": visible.content_hash}],
        }
    )


def test_snapshot_rejects_unknown_object_or_basis(
    kernel: UnderwritingKernelService,
) -> None:
    research_object = kernel.add_object(
        ResearchObjectKind.COMPANY, "company:1", "Company"
    )
    basis = kernel.add_basis(HistoricalBasisInput(T1, T1, "a" * 64))

    with pytest.raises(ValidationError, match="^research object not found$"):
        kernel.snapshot(uuid.uuid4(), basis.id)
    with pytest.raises(ValidationError, match="^historical basis not found$"):
        kernel.snapshot(research_object.id, uuid.uuid4())


def test_research_version_uses_replay_hash_and_canonical_parent_ids(
    kernel: UnderwritingKernelService,
) -> None:
    research_object = kernel.add_object(
        ResearchObjectKind.COMPANY, "company:1", "Company"
    )
    basis = kernel.add_basis(HistoricalBasisInput(T2, T2, "a" * 64))
    entry = kernel.append_ledger_entry(
        research_object.id, basis.id, _entry("revenue", T1), None
    )

    preview = kernel.preview_research_version_hash(
        research_object.id, basis.id, "valuation", [str(entry.id), str(entry.id)]
    )
    first = kernel.publish_research_version(
        research_object.id,
        basis.id,
        "valuation",
        [str(entry.id), str(entry.id)],
        expected_parent_id=None,
    )
    second = kernel.publish_research_version(
        research_object.id,
        basis.id,
        "valuation",
        [str(entry.id)],
        expected_parent_id=first.id,
    )

    assert first.parent_ids == [str(entry.id)]
    assert first.content_hash == preview == second.content_hash
    assert (first.sequence, second.sequence, second.supersedes_id) == (
        1,
        2,
        first.id,
    )


def test_research_version_rejects_stale_parent(
    kernel: UnderwritingKernelService,
) -> None:
    research_object = kernel.add_object(
        ResearchObjectKind.COMPANY, "company:1", "Company"
    )
    empty_basis = kernel.add_basis(HistoricalBasisInput(T1, T1, "a" * 64))

    first = kernel.publish_research_version(
        research_object.id,
        empty_basis.id,
        "valuation",
        [],
        expected_parent_id=None,
    )
    with pytest.raises(
        ConflictError, match="^expected parent is not the effective family version$"
    ):
        kernel.publish_research_version(
            research_object.id,
            empty_basis.id,
            "valuation",
            [],
            expected_parent_id=None,
        )
    assert first.sequence == 1
