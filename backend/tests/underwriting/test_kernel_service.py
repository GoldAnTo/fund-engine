from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import re
import uuid

import pytest
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.underwriting.domain import (
    HistoricalBasisInput,
    InvestmentMandateInput,
    LedgerEntryInput,
    LedgerKind,
    ResearchObjectKind,
)
from app.underwriting.services.kernel import UnderwritingKernelService, canonical_hash


NOW = datetime(2026, 8, 21, 9, 30, tzinfo=UTC)


@pytest.fixture
def kernel(session: Session) -> UnderwritingKernelService:
    return UnderwritingKernelService(session, now=lambda: NOW)


@pytest.fixture
def basis_input() -> HistoricalBasisInput:
    return HistoricalBasisInput(NOW, NOW, "a" * 64)


def test_canonical_hash_is_stable_for_equivalent_dictionary_ordering() -> None:
    assert canonical_hash({"b": 2, "a": {"d": 4, "c": 3}}) == canonical_hash(
        {"a": {"c": 3, "d": 4}, "b": 2}
    )


def test_canonical_hash_changes_when_payload_or_timestamp_changes() -> None:
    value = {"payload": {"revenue": 1}, "at": NOW}

    assert canonical_hash(value) != canonical_hash(
        {"payload": {"revenue": 2}, "at": NOW}
    )
    assert canonical_hash(value) != canonical_hash(
        {"payload": {"revenue": 1}, "at": NOW + timedelta(seconds=1)}
    )


def test_adds_distinct_company_and_security_then_allows_their_relation(
    kernel: UnderwritingKernelService,
) -> None:
    company = kernel.add_object(
        ResearchObjectKind.COMPANY, " CN:300750:COMPANY ", " CATL "
    )
    security = kernel.add_object(
        ResearchObjectKind.SECURITY, " SZSE:300750 ", " CATL A Shares "
    )

    relation = kernel.link_objects(company.id, security.id, "company_has_security")

    assert (company.kind, company.external_key, company.canonical_name) == (
        "company",
        "CN:300750:COMPANY",
        "CATL",
    )
    assert (security.kind, relation.parent_id, relation.child_id) == (
        "security",
        company.id,
        security.id,
    )


@pytest.mark.parametrize(
    ("parent_kind", "child_kind", "relation_type", "message"),
    [
        (
            ResearchObjectKind.COMPANY,
            ResearchObjectKind.SECURITY,
            "industry_exposes_company",
            "industry_exposes_company requires industry parent and company child",
        ),
        (
            ResearchObjectKind.INDUSTRY,
            ResearchObjectKind.COMPANY,
            "unknown",
            "relation_type is invalid",
        ),
    ],
)
def test_link_objects_rejects_invalid_relation_or_object_kinds(
    kernel: UnderwritingKernelService,
    parent_kind: ResearchObjectKind,
    child_kind: ResearchObjectKind,
    relation_type: str,
    message: str,
) -> None:
    parent = kernel.add_object(parent_kind, f"parent:{parent_kind}", "Parent")
    child = kernel.add_object(child_kind, f"child:{child_kind}", "Child")

    with pytest.raises(ValidationError, match=f"^{re.escape(message)}$"):
        kernel.link_objects(parent.id, child.id, relation_type)


def test_link_objects_rejects_a_missing_object(kernel: UnderwritingKernelService) -> None:
    company = kernel.add_object(ResearchObjectKind.COMPANY, "company:1", "Company")

    with pytest.raises(ValidationError, match="^research object not found$"):
        kernel.link_objects(uuid.uuid4(), company.id, "industry_exposes_company")


@pytest.mark.parametrize(
    "basis,message",
    [
        (
            HistoricalBasisInput(NOW, NOW + timedelta(seconds=1), "a" * 64),
            "price_as_of must not be after cutoff",
        ),
        (HistoricalBasisInput(NOW, NOW, "not-a-hash"), "source_manifest_hash must be a sha256 hex digest"),
        (
            HistoricalBasisInput(NOW.replace(tzinfo=None), None, "a" * 64),
            "cutoff must be timezone-aware",
        ),
    ],
)
def test_add_basis_rejects_invalid_temporal_or_hash_values(
    kernel: UnderwritingKernelService,
    basis: HistoricalBasisInput,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=f"^{re.escape(message)}$"):
        kernel.add_basis(basis)


@pytest.mark.parametrize(
    ("object_id", "basis_id", "entry", "message"),
    [
        (
            uuid.uuid4(),
            "basis",
            LedgerEntryInput(LedgerKind.REALITY, "revenue", "reported", {}, NOW, NOW, "public"),
            "research object not found",
        ),
        (
            "object",
            uuid.uuid4(),
            LedgerEntryInput(LedgerKind.REALITY, "revenue", "reported", {}, NOW, NOW, "public"),
            "historical basis not found",
        ),
        (
            "object",
            "basis",
            LedgerEntryInput(LedgerKind.REALITY, "revenue", "reported", {}, NOW, NOW + timedelta(seconds=1), "public"),
            "available_at must not be after basis cutoff",
        ),
        (
            "object",
            "basis",
            LedgerEntryInput(LedgerKind.REALITY, "revenue", "reported", {}, NOW.replace(tzinfo=None), NOW, "public"),
            "effective_at must be timezone-aware",
        ),
        (
            "object",
            "basis",
            LedgerEntryInput(LedgerKind.REALITY, "revenue", "reported", {}, NOW, NOW.replace(tzinfo=None), "public"),
            "available_at must be timezone-aware",
        ),
        (
            "object",
            "basis",
            LedgerEntryInput(LedgerKind.REALITY, "  ", "reported", {}, NOW, NOW, "public"),
            "family_key must not be empty",
        ),
        (
            "object",
            "basis",
            LedgerEntryInput(LedgerKind.REALITY, "revenue", "  ", {}, NOW, NOW, "public"),
            "entry_type must not be empty",
        ),
        (
            "object",
            "basis",
            LedgerEntryInput(LedgerKind.REALITY, "revenue", "reported", {}, NOW, NOW, "  "),
            "source_boundary must not be empty",
        ),
    ],
)
def test_append_ledger_entry_rejects_invalid_inputs(
    kernel: UnderwritingKernelService,
    basis_input: HistoricalBasisInput,
    object_id: uuid.UUID | str,
    basis_id: uuid.UUID | str,
    entry: LedgerEntryInput,
    message: str,
) -> None:
    object_row = kernel.add_object(ResearchObjectKind.COMPANY, "company:1", "Company")
    basis_row = kernel.add_basis(basis_input)

    with pytest.raises(ValidationError, match=f"^{re.escape(message)}$"):
        kernel.append_ledger_entry(
            object_row.id if object_id == "object" else object_id,
            basis_row.id if basis_id == "basis" else basis_id,
            entry,
            expected_parent_id=None,
        )


def test_append_ledger_entry_normalizes_fields_and_hashes_content(
    kernel: UnderwritingKernelService,
    basis_input: HistoricalBasisInput,
) -> None:
    object_row = kernel.add_object(ResearchObjectKind.COMPANY, "company:1", "Company")
    basis_row = kernel.add_basis(basis_input)
    entry = LedgerEntryInput(
        LedgerKind.REALITY,
        " revenue ",
        " reported ",
        {"b": 2, "a": 1},
        NOW,
        NOW,
        " public ",
    )

    row = kernel.append_ledger_entry(object_row.id, basis_row.id, entry, None)

    assert (row.family_key, row.entry_type, row.source_boundary, row.created_at) == (
        "revenue",
        "reported",
        "public",
        NOW,
    )
    assert row.content_hash == canonical_hash(
        {
            "ledger_kind": "reality",
            "family_key": "revenue",
            "entry_type": "reported",
            "payload": {"a": 1, "b": 2},
            "effective_at": NOW,
            "available_at": NOW,
            "source_boundary": "public",
        }
    )


@pytest.mark.parametrize(
    "mandate,message",
    [
        (
            InvestmentMandateInput("long", 2, "CNY", Decimal("0.1"), Decimal("0.2"), ("CSI300",)),
            "horizon_years must be one of 3, 4, 5",
        ),
        (
            InvestmentMandateInput("long", 5, "cny", Decimal("0.1"), Decimal("0.2"), ("CSI300",)),
            "base_currency must be a three-letter uppercase code",
        ),
        (
            InvestmentMandateInput("long", 5, "CNY", Decimal("1"), Decimal("0.2"), ("CSI300",)),
            "required_return must be in [0, 1)",
        ),
        (
            InvestmentMandateInput("long", 5, "CNY", Decimal("0.1"), Decimal("1.1"), ("CSI300",)),
            "permanent_loss_limit must be in [0, 1]",
        ),
        (
            InvestmentMandateInput("long", 5, "CNY", Decimal("0.1"), Decimal("0.2"), ()),
            "comparison_set must not be empty",
        ),
    ],
)
def test_append_mandate_rejects_invalid_values(
    kernel: UnderwritingKernelService,
    mandate: InvestmentMandateInput,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=f"^{re.escape(message)}$"):
        kernel.append_mandate(mandate, expected_parent_id=None)


def test_append_mandate_normalizes_values(kernel: UnderwritingKernelService) -> None:
    row = kernel.append_mandate(
        InvestmentMandateInput(
            " long-term ",
            5,
            " CNY ",
            Decimal("0.12"),
            Decimal("0.25"),
            (" CSI300 ", " SSE50 "),
        ),
        expected_parent_id=None,
    )

    assert (row.mandate_key, row.base_currency, row.comparison_set, row.created_at) == (
        "long-term",
        "CNY",
        ["CSI300", "SSE50"],
        NOW,
    )
