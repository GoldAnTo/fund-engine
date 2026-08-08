"""Auditable China industry-index controls for report market studies."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.ledger import (
    ChinaIndustryIndex,
    ChinaIndustryIndexMembership,
    ChinaIndustryIndexSnapshot,
    Company,
)


NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def test_industry_index_membership_and_snapshot_are_ledger_facts(session):
    company = Company(
        id=uuid.uuid4(),
        code="600001",
        name="行业样本公司",
        type="listed",
        created_at=NOW,
    )
    index = ChinaIndustryIndex(
        id=uuid.uuid4(),
        code="801080.SI",
        name="申万电子",
        provider="申万",
        market="CN",
        source="provider://sw/industry-indexes",
        definition="申万一级电子行业指数",
        created_at=NOW,
    )
    session.add_all([company, index])
    session.flush()
    membership = ChinaIndustryIndexMembership(
        id=uuid.uuid4(),
        company_id=company.id,
        industry_index_id=index.id,
        source="provider://sw/constituents",
        definition="申万一级行业归属",
        available_at=datetime(2026, 8, 3, 7, tzinfo=timezone.utc),
        created_at=NOW,
    )
    snapshot = ChinaIndustryIndexSnapshot(
        id=uuid.uuid4(),
        industry_index_id=index.id,
        as_of_date=date(2026, 8, 4),
        metric_name="INDUSTRY_RETURN_1D",
        metric_value=Decimal("0.0125"),
        source="provider://sw/quotes",
        definition="发布后首个交易日行业指数收益率",
        available_at=datetime(2026, 8, 4, 15, tzinfo=timezone.utc),
        created_at=NOW,
    )
    session.add_all([membership, snapshot])
    session.commit()

    assert session.scalar(
        select(ChinaIndustryIndexSnapshot).where(
            ChinaIndustryIndexSnapshot.industry_index_id == index.id
        )
    ).metric_value == Decimal("0.0125")


def test_industry_index_snapshot_cannot_claim_visibility_before_its_trade_date(session):
    index = ChinaIndustryIndex(
        id=uuid.uuid4(),
        code="801080.SI",
        name="申万电子",
        provider="申万",
        market="CN",
        source="provider://sw/industry-indexes",
        definition="申万一级电子行业指数",
        created_at=NOW,
    )
    session.add(index)
    session.flush()
    session.add(
        ChinaIndustryIndexSnapshot(
            id=uuid.uuid4(),
            industry_index_id=index.id,
            as_of_date=date(2026, 8, 4),
            metric_name="INDUSTRY_RETURN_1D",
            metric_value=Decimal("0.01"),
            source="provider://sw/quotes",
            definition="行业收益",
            available_at=datetime(2026, 8, 3, 15, tzinfo=timezone.utc),
            created_at=NOW,
        )
    )

    with pytest.raises(ValueError, match="cannot predate"):
        session.flush()
