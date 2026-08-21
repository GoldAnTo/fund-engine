from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, delete, inspect, update
from sqlalchemy.orm import Session

from app.models import Base
from app.models.ledger import IMMUTABLE_TABLES, ImmutableLedgerError
from app.underwriting.persistence.models import UnderwritingResearchObject


UNDERWRITING_TABLES = frozenset(
    {
        "uw_research_objects",
        "uw_object_relations",
        "uw_mandate_versions",
        "uw_historical_bases",
        "uw_ledger_entries",
        "uw_research_versions",
        "uw_answerability_evaluations",
    }
)


def test_underwriting_kernel_tables_are_registered_with_metadata_and_immutable_guard():
    assert UNDERWRITING_TABLES <= set(Base.metadata.tables)
    assert UNDERWRITING_TABLES <= IMMUTABLE_TABLES


def test_underwriting_research_objects_reject_update_and_delete():
    engine = create_engine("sqlite://")
    table = UnderwritingResearchObject.__table__
    Base.metadata.create_all(engine, tables=[table])
    try:
        with Session(engine) as session:
            research_object = UnderwritingResearchObject(
                kind="company",
                external_key="acme-001",
                canonical_name="Acme",
                created_at=datetime.now(timezone.utc),
            )
            session.add(research_object)
            session.commit()

            with pytest.raises(ImmutableLedgerError):
                session.execute(
                    update(UnderwritingResearchObject)
                    .where(UnderwritingResearchObject.id == research_object.id)
                    .values(canonical_name="Acme Holdings")
                )
            with pytest.raises(ImmutableLedgerError):
                session.execute(
                    delete(UnderwritingResearchObject).where(
                        UnderwritingResearchObject.id == research_object.id
                    )
                )
    finally:
        Base.metadata.drop_all(engine, tables=[table])


def test_underwriting_kernel_metadata_can_create_and_drop_in_sqlite():
    engine = create_engine("sqlite://")
    tables = [Base.metadata.tables[name] for name in UNDERWRITING_TABLES]

    Base.metadata.create_all(engine, tables=tables)
    assert UNDERWRITING_TABLES <= set(inspect(engine).get_table_names())

    Base.metadata.drop_all(engine, tables=tables)
    assert not UNDERWRITING_TABLES & set(inspect(engine).get_table_names())
