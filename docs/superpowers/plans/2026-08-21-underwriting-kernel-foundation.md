# Underwriting Trusted Kernel Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the isolated append-only identity, mandate, historical-basis, four-ledger, version, replay, and answerability kernel required before CATL research data or valuation logic can enter the product.

**Architecture:** The kernel lives under `app.underwriting` and exposes `/api/underwriting/v1`; it shares database/session/error infrastructure but imports no legacy research domain. Pure domain enums and policies sit above an append-only SQLAlchemy repository. A service validates dual time, successor chains, content hashes, and answerability before routes serialize frozen snapshots.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL append-only triggers, SQLite unit tests, pytest.

---

## File map

- Create `backend/app/underwriting/__init__.py`: bounded-context marker only.
- Create `backend/app/underwriting/domain/__init__.py`: public pure-domain exports.
- Create `backend/app/underwriting/domain/types.py`: controlled enums and immutable inputs.
- Create `backend/app/underwriting/domain/answerability.py`: pure answerability and action-boundary policy.
- Create `backend/app/underwriting/persistence/__init__.py`: persistence exports.
- Create `backend/app/underwriting/persistence/models.py`: seven append-only kernel tables.
- Create `backend/app/underwriting/persistence/repository.py`: append and historical read operations.
- Create `backend/app/underwriting/services/__init__.py`: service exports.
- Create `backend/app/underwriting/services/kernel.py`: validation, canonical hashing, successor checks, and snapshot assembly.
- Create `backend/app/underwriting/api/__init__.py`: API exports.
- Create `backend/app/underwriting/api/schemas.py`: strict request/response DTOs.
- Create `backend/app/underwriting/api/router.py`: `/api/underwriting/v1` routes.
- Create `backend/alembic/versions/0060_underwriting_kernel.py`: kernel tables, indexes, constraints, and PostgreSQL append-only triggers.
- Modify `backend/app/models/__init__.py`: import Underwriting models for metadata discovery.
- Modify `backend/app/models/ledger.py`: add the seven table names to `IMMUTABLE_TABLES`.
- Modify `backend/app/main.py`: include the independent Underwriting router.
- Create `backend/tests/underwriting/__init__.py`.
- Create `backend/tests/underwriting/test_isolation_contract.py`.
- Create `backend/tests/underwriting/test_domain_types.py`.
- Create `backend/tests/underwriting/test_answerability.py`.
- Create `backend/tests/underwriting/test_kernel_persistence.py`.
- Create `backend/tests/underwriting/test_historical_replay.py`.
- Create `backend/tests/underwriting/test_kernel_api.py`.
- Create `backend/tests/underwriting/test_kernel_postgres.py`.

## Kernel table contract

| Table | Responsibility | Natural family key |
|---|---|---|
| `uw_research_objects` | Industry / Company / Security identity | `kind + external_key` |
| `uw_object_relations` | Company↔Industry and Security↔Company links | `parent_id + child_id + relation_type` |
| `uw_mandate_versions` | Immutable investor authorization versions | `mandate_key + version` |
| `uw_historical_bases` | Frozen cutoff, price time, and source-manifest hash | `id` |
| `uw_ledger_entries` | Reality / Belief / Decision / Calibration entries | `object_id + ledger_kind + family_key + version` |
| `uw_research_versions` | Named IndustryState/Earnings/Forecast/Valuation/Underwriting version envelope | `object_id + version_kind + sequence` |
| `uw_answerability_evaluations` | Frozen gate result and blocker resolution | `object_id + basis_id + version` |

All seven tables reject UPDATE and DELETE. `supersedes_id` is nullable only for version 1 and must point to the effective prior row in the same family.

### Task 1: Establish the bounded-context isolation contract

**Files:**
- Create: `backend/app/underwriting/__init__.py`
- Create: `backend/app/underwriting/domain/__init__.py`
- Create: `backend/app/underwriting/persistence/__init__.py`
- Create: `backend/app/underwriting/services/__init__.py`
- Create: `backend/app/underwriting/api/__init__.py`
- Create: `backend/tests/underwriting/__init__.py`
- Create: `backend/tests/underwriting/test_isolation_contract.py`

- [ ] **Step 1: Write the failing package and import-isolation test**

```python
from pathlib import Path


UNDERWRITING_ROOT = Path(__file__).parents[2] / "app" / "underwriting"
FORBIDDEN_IMPORTS = (
    "app.domain.event_research",
    "app.models.event_research",
    "app.queries.event_research",
    "app.services.auto_research",
    "app.services.automatic_research",
    "app.services.fund_disclosure_sync",
)
FORBIDDEN_PUBLIC_TERMS = (
    "ResearchCase",
    "EventResearch",
    "ThemeRole",
    "FundDisclosure",
    "AutomaticResearch",
)


def test_underwriting_is_a_separate_bounded_context() -> None:
    assert UNDERWRITING_ROOT.is_dir()
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in UNDERWRITING_ROOT.rglob("*.py")
    )
    assert not any(name in sources for name in FORBIDDEN_IMPORTS)
    assert not any(name in sources for name in FORBIDDEN_PUBLIC_TERMS)
```

- [ ] **Step 2: Run the isolation test and verify it fails**

Run: `cd backend && pytest -q tests/underwriting/test_isolation_contract.py`

Expected: FAIL because `app/underwriting` does not exist.

- [ ] **Step 3: Create the empty package boundaries**

Each `__init__.py` contains only a bounded-context docstring. The root file is exactly:

```python
"""Independent dynamic investment-underwriting bounded context."""
```

- [ ] **Step 4: Run the isolation test and verify it passes**

Run: `cd backend && pytest -q tests/underwriting/test_isolation_contract.py`

Expected: `1 passed`.

- [ ] **Step 5: Commit the isolation boundary**

```bash
git add backend/app/underwriting backend/tests/underwriting
git commit -m "feat: establish underwriting bounded context"
```

### Task 2: Define controlled domain types

**Files:**
- Create: `backend/app/underwriting/domain/types.py`
- Modify: `backend/app/underwriting/domain/__init__.py`
- Create: `backend/tests/underwriting/test_domain_types.py`

- [ ] **Step 1: Write failing enum and immutable-input tests**

```python
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.underwriting.domain.types import (
    AnswerabilityState,
    BlockerCode,
    EligibleAction,
    InvestmentMandateInput,
    LedgerKind,
    ResearchObjectKind,
)


def test_controlled_values_are_exact() -> None:
    assert {item.value for item in ResearchObjectKind} == {"industry", "company", "security"}
    assert {item.value for item in LedgerKind} == {"reality", "belief", "decision", "calibration"}
    assert {item.value for item in AnswerabilityState} == {
        "answerable", "partially_answerable", "not_answerable"
    }
    assert {item.value for item in EligibleAction} == {
        "observe", "wait_for_validation", "eligible_for_probe_entry",
        "eligible_for_staged_entry", "do_not_enter",
    }
    assert len(BlockerCode) == 7


def test_mandate_input_is_immutable() -> None:
    value = InvestmentMandateInput(
        mandate_key="personal-cny-5y",
        horizon_years=5,
        base_currency="CNY",
        required_return=Decimal("0.12"),
        permanent_loss_limit=Decimal("0.25"),
        comparison_set=("CSI300", "CGB10Y"),
    )
    with pytest.raises(FrozenInstanceError):
        value.horizon_years = 3
```

- [ ] **Step 2: Run the domain tests and verify import failure**

Run: `cd backend && pytest -q tests/underwriting/test_domain_types.py`

Expected: FAIL with `ModuleNotFoundError: app.underwriting.domain.types`.

- [ ] **Step 3: Implement the exact controlled types**

```python
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class ResearchObjectKind(StrEnum):
    INDUSTRY = "industry"
    COMPANY = "company"
    SECURITY = "security"


class LedgerKind(StrEnum):
    REALITY = "reality"
    BELIEF = "belief"
    DECISION = "decision"
    CALIBRATION = "calibration"


class AnswerabilityState(StrEnum):
    ANSWERABLE = "answerable"
    PARTIALLY_ANSWERABLE = "partially_answerable"
    NOT_ANSWERABLE = "not_answerable"


class EligibleAction(StrEnum):
    OBSERVE = "observe"
    WAIT_FOR_VALIDATION = "wait_for_validation"
    ELIGIBLE_FOR_PROBE_ENTRY = "eligible_for_probe_entry"
    ELIGIBLE_FOR_STAGED_ENTRY = "eligible_for_staged_entry"
    DO_NOT_ENTER = "do_not_enter"


class BlockerCode(StrEnum):
    MISSING_KEY_BASELINE = "missing_key_baseline"
    UNRESOLVED_SOURCE_CONFLICT = "unresolved_source_conflict"
    MECHANISM_UNIDENTIFIED = "mechanism_unidentified"
    FINANCIAL_MODEL_NOT_CLOSED = "financial_model_not_closed"
    EXPECTATION_SURFACE_UNIDENTIFIABLE = "expectation_surface_unidentifiable"
    SOURCE_UNAVAILABLE = "source_unavailable"
    FUTURE_INFORMATION_LEAKAGE = "future_information_leakage"


@dataclass(frozen=True, slots=True)
class InvestmentMandateInput:
    mandate_key: str
    horizon_years: int
    base_currency: str
    required_return: Decimal
    permanent_loss_limit: Decimal
    comparison_set: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HistoricalBasisInput:
    cutoff: datetime
    price_as_of: datetime | None
    source_manifest_hash: str


@dataclass(frozen=True, slots=True)
class LedgerEntryInput:
    ledger_kind: LedgerKind
    family_key: str
    entry_type: str
    payload: dict[str, Any]
    effective_at: datetime
    available_at: datetime
    source_boundary: str
```

Export all types from `domain/__init__.py` with explicit imports and `__all__`.

- [ ] **Step 4: Run tests and verify controlled values pass**

Run: `cd backend && pytest -q tests/underwriting/test_domain_types.py tests/underwriting/test_isolation_contract.py`

Expected: `3 passed`.

- [ ] **Step 5: Commit domain vocabulary**

```bash
git add backend/app/underwriting/domain backend/tests/underwriting
git commit -m "feat: define underwriting domain vocabulary"
```

### Task 3: Implement the pure AnswerabilityGate

**Files:**
- Create: `backend/app/underwriting/domain/answerability.py`
- Modify: `backend/app/underwriting/domain/__init__.py`
- Create: `backend/tests/underwriting/test_answerability.py`

- [ ] **Step 1: Write failing gate tests**

```python
import pytest

from app.underwriting.domain.answerability import (
    AnswerabilityInput,
    AnswerabilityResult,
    evaluate_answerability,
    enforce_action_boundary,
)
from app.underwriting.domain.types import (
    AnswerabilityState,
    BlockerCode,
    EligibleAction,
)


def test_no_blockers_is_answerable() -> None:
    result = evaluate_answerability(AnswerabilityInput((), (), True))
    assert result == AnswerabilityResult(AnswerabilityState.ANSWERABLE, (), True)


def test_research_debt_without_hard_blocker_is_partial() -> None:
    result = evaluate_answerability(
        AnswerabilityInput((), ("single_source_unit_cost",), True)
    )
    assert result.state is AnswerabilityState.PARTIALLY_ANSWERABLE


def test_any_hard_blocker_fails_closed() -> None:
    result = evaluate_answerability(
        AnswerabilityInput((BlockerCode.FINANCIAL_MODEL_NOT_CLOSED,), (), True)
    )
    assert result.state is AnswerabilityState.NOT_ANSWERABLE


def test_resolvable_failure_waits_and_unresolvable_failure_rejects() -> None:
    resolvable = AnswerabilityResult(
        AnswerabilityState.NOT_ANSWERABLE,
        (BlockerCode.MISSING_KEY_BASELINE,),
        True,
    )
    terminal = AnswerabilityResult(
        AnswerabilityState.NOT_ANSWERABLE,
        (BlockerCode.SOURCE_UNAVAILABLE,),
        False,
    )
    assert enforce_action_boundary(resolvable, EligibleAction.ELIGIBLE_FOR_PROBE_ENTRY) is EligibleAction.WAIT_FOR_VALIDATION
    assert enforce_action_boundary(terminal, EligibleAction.OBSERVE) is EligibleAction.DO_NOT_ENTER


@pytest.mark.parametrize(
    "requested",
    [EligibleAction.ELIGIBLE_FOR_PROBE_ENTRY, EligibleAction.ELIGIBLE_FOR_STAGED_ENTRY],
)
def test_partial_state_cannot_stage_entry(requested: EligibleAction) -> None:
    result = AnswerabilityResult(
        AnswerabilityState.PARTIALLY_ANSWERABLE, (), True
    )
    assert enforce_action_boundary(result, requested) is EligibleAction.WAIT_FOR_VALIDATION
```

- [ ] **Step 2: Run the gate tests and verify import failure**

Run: `cd backend && pytest -q tests/underwriting/test_answerability.py`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the fail-closed policy**

```python
from dataclasses import dataclass

from app.underwriting.domain.types import (
    AnswerabilityState,
    BlockerCode,
    EligibleAction,
)


ENTRY_ACTIONS = frozenset({
    EligibleAction.ELIGIBLE_FOR_PROBE_ENTRY,
    EligibleAction.ELIGIBLE_FOR_STAGED_ENTRY,
})


@dataclass(frozen=True, slots=True)
class AnswerabilityInput:
    hard_blockers: tuple[BlockerCode, ...]
    research_debt_keys: tuple[str, ...]
    resolvable_within_mandate: bool


@dataclass(frozen=True, slots=True)
class AnswerabilityResult:
    state: AnswerabilityState
    blockers: tuple[BlockerCode, ...]
    resolvable_within_mandate: bool


def evaluate_answerability(value: AnswerabilityInput) -> AnswerabilityResult:
    blockers = tuple(dict.fromkeys(value.hard_blockers))
    if blockers:
        state = AnswerabilityState.NOT_ANSWERABLE
    elif value.research_debt_keys:
        state = AnswerabilityState.PARTIALLY_ANSWERABLE
    else:
        state = AnswerabilityState.ANSWERABLE
    return AnswerabilityResult(state, blockers, value.resolvable_within_mandate)


def enforce_action_boundary(
    result: AnswerabilityResult,
    requested: EligibleAction,
) -> EligibleAction:
    if result.state is AnswerabilityState.NOT_ANSWERABLE:
        return (
            EligibleAction.WAIT_FOR_VALIDATION
            if result.resolvable_within_mandate
            else EligibleAction.DO_NOT_ENTER
        )
    if result.state is AnswerabilityState.PARTIALLY_ANSWERABLE and requested in ENTRY_ACTIONS:
        return EligibleAction.WAIT_FOR_VALIDATION
    return requested
```

- [ ] **Step 4: Run all pure-domain tests**

Run: `cd backend && pytest -q tests/underwriting/test_domain_types.py tests/underwriting/test_answerability.py`

Expected: `8 passed`.

- [ ] **Step 5: Commit answerability policy**

```bash
git add backend/app/underwriting/domain backend/tests/underwriting
git commit -m "feat: add fail-closed answerability gate"
```

### Task 4: Add append-only kernel tables and migration

**Files:**
- Create: `backend/app/underwriting/persistence/models.py`
- Modify: `backend/app/underwriting/persistence/__init__.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/ledger.py`
- Create: `backend/alembic/versions/0060_underwriting_kernel.py`
- Create: `backend/tests/underwriting/test_kernel_persistence.py`

- [ ] **Step 1: Write the failing metadata and immutability tests**

```python
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, update

from app.models.ledger import Base, ImmutableLedgerError
from app.underwriting.persistence.models import UnderwritingResearchObject


EXPECTED_TABLES = {
    "uw_research_objects",
    "uw_object_relations",
    "uw_mandate_versions",
    "uw_historical_bases",
    "uw_ledger_entries",
    "uw_research_versions",
    "uw_answerability_evaluations",
}


def test_kernel_tables_are_registered() -> None:
    assert EXPECTED_TABLES.issubset(Base.metadata.tables)


def test_kernel_identity_is_append_only(session) -> None:
    row = UnderwritingResearchObject(
        kind="company",
        external_key="CN:300750:COMPANY",
        canonical_name="宁德时代新能源科技股份有限公司",
        created_at=datetime(2026, 8, 21, tzinfo=UTC),
    )
    session.add(row)
    session.flush()
    with pytest.raises(ImmutableLedgerError):
        session.execute(
            update(UnderwritingResearchObject)
            .where(UnderwritingResearchObject.id == row.id)
            .values(canonical_name="changed")
        )
    with pytest.raises(ImmutableLedgerError):
        session.execute(
            delete(UnderwritingResearchObject)
            .where(UnderwritingResearchObject.id == row.id)
        )
```

- [ ] **Step 2: Run the persistence tests and verify import failure**

Run: `cd backend && pytest -q tests/underwriting/test_kernel_persistence.py`

Expected: FAIL because persistence models do not exist.

- [ ] **Step 3: Implement the seven SQLAlchemy models**

Use one file with the exact fields below; every timestamp is timezone-aware and every JSON field is non-null:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Integer, JSON, Numeric,
    String, Text, UniqueConstraint, Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base, _uuid


class UnderwritingResearchObject(Base):
    __tablename__ = "uw_research_objects"
    __table_args__ = (
        CheckConstraint("kind IN ('industry','company','security')", name="ck_uw_object_kind"),
        UniqueConstraint("kind", "external_key", name="uq_uw_object_external_key"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    external_key: Mapped[str] = mapped_column(String(160), nullable=False)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingObjectRelation(Base):
    __tablename__ = "uw_object_relations"
    __table_args__ = (
        UniqueConstraint("parent_id", "child_id", "relation_type", name="uq_uw_object_relation"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    parent_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_research_objects.id"), nullable=False)
    child_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_research_objects.id"), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(48), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingMandateVersion(Base):
    __tablename__ = "uw_mandate_versions"
    __table_args__ = (
        UniqueConstraint("mandate_key", "version", name="uq_uw_mandate_version"),
        CheckConstraint("horizon_years BETWEEN 3 AND 5", name="ck_uw_mandate_horizon"),
        CheckConstraint("required_return >= 0 AND required_return < 1", name="ck_uw_required_return"),
        CheckConstraint("permanent_loss_limit >= 0 AND permanent_loss_limit <= 1", name="ck_uw_loss_limit"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    mandate_key: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    horizon_years: Mapped[int] = mapped_column(Integer, nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    required_return: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    permanent_loss_limit: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    comparison_set: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_mandate_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingHistoricalBasis(Base):
    __tablename__ = "uw_historical_bases"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingLedgerEntry(Base):
    __tablename__ = "uw_ledger_entries"
    __table_args__ = (
        CheckConstraint("ledger_kind IN ('reality','belief','decision','calibration')", name="ck_uw_ledger_kind"),
        UniqueConstraint("object_id", "ledger_kind", "family_key", "version", name="uq_uw_ledger_family_version"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    object_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_research_objects.id"), nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    ledger_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    family_key: Mapped[str] = mapped_column(String(160), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_boundary: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_ledger_entries.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingResearchVersion(Base):
    __tablename__ = "uw_research_versions"
    __table_args__ = (
        UniqueConstraint("object_id", "version_kind", "sequence", name="uq_uw_research_version_sequence"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    object_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_research_objects.id"), nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    version_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_research_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingAnswerabilityEvaluation(Base):
    __tablename__ = "uw_answerability_evaluations"
    __table_args__ = (
        CheckConstraint("state IN ('answerable','partially_answerable','not_answerable')", name="ck_uw_answerability_state"),
        CheckConstraint("allowed_action IN ('observe','wait_for_validation','eligible_for_probe_entry','eligible_for_staged_entry','do_not_enter')", name="ck_uw_answerability_action"),
        UniqueConstraint("object_id", "basis_id", "version", name="uq_uw_answerability_version"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    object_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_research_objects.id"), nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    blockers: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    research_debt_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    resolvable_within_mandate: Mapped[bool] = mapped_column(nullable=False)
    allowed_action: Mapped[str] = mapped_column(String(48), nullable=False)
    resolution_requirements: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_answerability_evaluations.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 4: Register metadata and application immutability**

Add `from app.underwriting.persistence import models as underwriting_models` to `app/models/__init__.py`, export it in `__all__`, and add these exact names to `IMMUTABLE_TABLES`:

```python
{
    "uw_research_objects",
    "uw_object_relations",
    "uw_mandate_versions",
    "uw_historical_bases",
    "uw_ledger_entries",
    "uw_research_versions",
    "uw_answerability_evaluations",
}
```

- [ ] **Step 5: Create Alembic revision 0060**

Set:

```python
revision = "0060"
down_revision = "0059"
```

`upgrade()` creates the seven tables with the same columns, foreign keys, checks, and unique constraints as the models. It creates indexes on `(object_id, ledger_kind, available_at)`, `(object_id, version_kind, sequence)`, and `(object_id, basis_id, version)`. For each table it installs the repository's standard PostgreSQL immutable trigger. `downgrade()` drops the seven triggers and tables in reverse foreign-key order.

- [ ] **Step 6: Run persistence tests and migration bootstrap tests**

Run: `cd backend && pytest -q tests/underwriting/test_kernel_persistence.py tests/test_sqlite_migration_bootstrap.py tests/test_schema_reconciliation_migration.py`

Expected: all selected tests PASS.

- [ ] **Step 7: Commit schema foundation**

```bash
git add backend/app/models backend/app/underwriting/persistence backend/alembic/versions/0060_underwriting_kernel.py backend/tests/underwriting/test_kernel_persistence.py
git commit -m "feat: add append-only underwriting kernel schema"
```

### Task 5: Implement canonical append and successor repository rules

**Files:**
- Create: `backend/app/underwriting/persistence/repository.py`
- Modify: `backend/app/underwriting/persistence/__init__.py`
- Modify: `backend/tests/underwriting/test_kernel_persistence.py`

- [ ] **Step 1: Add failing family-version and optimistic-successor tests**

```python
from datetime import UTC, datetime

import pytest

from app.underwriting.persistence.repository import (
    StaleParentError,
    UnderwritingRepository,
)


NOW = datetime(2026, 8, 21, tzinfo=UTC)


def test_append_assigns_monotonic_family_versions(session, kernel_company, kernel_basis) -> None:
    repo = UnderwritingRepository(session)
    first = repo.append_ledger_entry(
        object_id=kernel_company.id,
        basis_id=kernel_basis.id,
        ledger_kind="belief",
        family_key="catl:unit-margin",
        entry_type="claim",
        payload={"statement": "unit margin depends on pass-through lag"},
        effective_at=NOW,
        available_at=NOW,
        source_boundary="researcher synthesis",
        content_hash="a" * 64,
        expected_parent_id=None,
        created_at=NOW,
    )
    second = repo.append_ledger_entry(
        object_id=kernel_company.id,
        basis_id=kernel_basis.id,
        ledger_kind="belief",
        family_key="catl:unit-margin",
        entry_type="claim",
        payload={"statement": "pass-through lag narrowed"},
        effective_at=NOW,
        available_at=NOW,
        source_boundary="researcher synthesis",
        content_hash="b" * 64,
        expected_parent_id=first.id,
        created_at=NOW,
    )
    assert (first.version, second.version, second.supersedes_id) == (1, 2, first.id)


def test_append_rejects_a_stale_expected_parent(session, kernel_company, kernel_basis) -> None:
    repo = UnderwritingRepository(session)
    first = append_test_claim(repo, kernel_company.id, kernel_basis.id, None, "a")
    append_test_claim(repo, kernel_company.id, kernel_basis.id, first.id, "b")
    with pytest.raises(StaleParentError):
        append_test_claim(repo, kernel_company.id, kernel_basis.id, first.id, "c")
```

Add `kernel_company`, `kernel_security`, `kernel_industry`, `kernel_basis`, and `append_test_claim` fixtures to this test module. The Company has external key `CN:300750:COMPANY`; the Security has `SZSE:300750`; the Industry has `CN:POWER-BATTERY`.

- [ ] **Step 2: Run the successor tests and verify import failure**

Run: `cd backend && pytest -q tests/underwriting/test_kernel_persistence.py -k 'monotonic or stale'`

Expected: FAIL because the repository is missing.

- [ ] **Step 3: Implement repository exceptions and effective-row lookup**

```python
class StaleParentError(ValueError):
    pass


class UnderwritingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def effective_ledger_entry(
        self,
        object_id: uuid.UUID,
        ledger_kind: str,
        family_key: str,
    ) -> UnderwritingLedgerEntry | None:
        return self._session.scalar(
            select(UnderwritingLedgerEntry)
            .where(
                UnderwritingLedgerEntry.object_id == object_id,
                UnderwritingLedgerEntry.ledger_kind == ledger_kind,
                UnderwritingLedgerEntry.family_key == family_key,
            )
            .order_by(
                UnderwritingLedgerEntry.version.desc(),
                UnderwritingLedgerEntry.id.desc(),
            )
            .limit(1)
        )

    @staticmethod
    def _require_expected_parent(current_id: uuid.UUID | None, expected_id: uuid.UUID | None) -> None:
        if current_id != expected_id:
            raise StaleParentError("expected parent is not the effective family version")
```

- [ ] **Step 4: Implement atomic ledger append**

```python
    def append_ledger_entry(
        self,
        *,
        object_id: uuid.UUID,
        basis_id: uuid.UUID,
        ledger_kind: str,
        family_key: str,
        entry_type: str,
        payload: dict,
        effective_at: datetime,
        available_at: datetime,
        source_boundary: str,
        content_hash: str,
        expected_parent_id: uuid.UUID | None,
        created_at: datetime,
    ) -> UnderwritingLedgerEntry:
        current = self.effective_ledger_entry(object_id, ledger_kind, family_key)
        self._require_expected_parent(current.id if current else None, expected_parent_id)
        row = UnderwritingLedgerEntry(
            object_id=object_id,
            basis_id=basis_id,
            ledger_kind=ledger_kind,
            family_key=family_key,
            entry_type=entry_type,
            version=(current.version + 1) if current else 1,
            payload=payload,
            effective_at=effective_at,
            available_at=available_at,
            source_boundary=source_boundary,
            content_hash=content_hash,
            supersedes_id=current.id if current else None,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row
```

Add repository methods following the same effective-parent rule for mandate versions, research versions, and answerability evaluations. Add direct append methods for identities, relations, and historical bases. No repository method performs UPDATE or DELETE.

- [ ] **Step 5: Add same-family and same-object rejection tests**

```python
def test_parent_must_belong_to_the_same_family(session, kernel_company, kernel_basis) -> None:
    repo = UnderwritingRepository(session)
    belief = append_test_claim(repo, kernel_company.id, kernel_basis.id, None, "a")
    with pytest.raises(StaleParentError):
        repo.append_ledger_entry(
            object_id=kernel_company.id,
            basis_id=kernel_basis.id,
            ledger_kind="reality",
            family_key="catl:unit-margin",
            entry_type="observation",
            payload={"value": "1"},
            effective_at=NOW,
            available_at=NOW,
            source_boundary="fixture",
            content_hash="d" * 64,
            expected_parent_id=belief.id,
            created_at=NOW,
        )


def test_parent_must_belong_to_the_same_object(
    session, kernel_company, second_kernel_company, kernel_basis
) -> None:
    repo = UnderwritingRepository(session)
    first = append_test_claim(repo, kernel_company.id, kernel_basis.id, None, "a")
    with pytest.raises(StaleParentError):
        repo.append_ledger_entry(
            object_id=second_kernel_company.id,
            basis_id=kernel_basis.id,
            ledger_kind="belief",
            family_key="catl:unit-margin",
            entry_type="claim",
            payload={"statement": "other company"},
            effective_at=NOW,
            available_at=NOW,
            source_boundary="fixture",
            content_hash="e" * 64,
            expected_parent_id=first.id,
            created_at=NOW,
        )
```

- [ ] **Step 6: Run persistence tests**

Run: `cd backend && pytest -q tests/underwriting/test_kernel_persistence.py`

Expected: all tests in the module PASS.

- [ ] **Step 7: Commit repository rules**

```bash
git add backend/app/underwriting/persistence backend/tests/underwriting/test_kernel_persistence.py
git commit -m "feat: enforce underwriting successor chains"
```

### Task 6: Add service validation, canonical hashing, and identity separation

**Files:**
- Create: `backend/app/underwriting/services/kernel.py`
- Modify: `backend/app/underwriting/services/__init__.py`
- Create: `backend/tests/underwriting/test_kernel_service.py`

- [ ] **Step 1: Write failing service-boundary tests**

```python
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models.ledger import ValidationError
from app.underwriting.domain.types import (
    HistoricalBasisInput,
    InvestmentMandateInput,
    LedgerEntryInput,
    LedgerKind,
    ResearchObjectKind,
)
from app.underwriting.services.kernel import UnderwritingKernelService


T1 = datetime(2026, 6, 30, tzinfo=UTC)
T2 = datetime(2026, 8, 21, tzinfo=UTC)


def test_company_and_security_are_distinct_but_related(session) -> None:
    service = UnderwritingKernelService(session, now=lambda: T2)
    company = service.add_object(ResearchObjectKind.COMPANY, "CN:300750:COMPANY", "宁德时代")
    security = service.add_object(ResearchObjectKind.SECURITY, "SZSE:300750", "宁德时代 A股")
    relation = service.link_objects(company.id, security.id, "company_has_security")
    assert company.id != security.id
    assert (relation.parent_id, relation.child_id) == (company.id, security.id)


def test_basis_rejects_price_after_cutoff(session) -> None:
    service = UnderwritingKernelService(session, now=lambda: T2)
    with pytest.raises(ValidationError, match="price_as_of must not be after cutoff"):
        service.add_basis(HistoricalBasisInput(T1, T2, "a" * 64))


def test_ledger_rejects_future_information(session, kernel_company, kernel_basis) -> None:
    service = UnderwritingKernelService(session, now=lambda: T2)
    value = LedgerEntryInput(
        ledger_kind=LedgerKind.REALITY,
        family_key="catl:shipment",
        entry_type="observation",
        payload={"value": "100", "unit": "GWh"},
        effective_at=T1,
        available_at=T2,
        source_boundary="frozen annual report",
    )
    with pytest.raises(ValidationError, match="available_at exceeds basis cutoff"):
        service.append_ledger_entry(kernel_company.id, kernel_basis.id, value, None)


def test_mandate_rejects_undeclared_comparison_set(session) -> None:
    service = UnderwritingKernelService(session, now=lambda: T2)
    value = InvestmentMandateInput(
        "personal-cny-5y", 5, "CNY", Decimal("0.12"), Decimal("0.25"), ()
    )
    with pytest.raises(ValidationError, match="comparison_set must not be empty"):
        service.append_mandate(value, None)
```

- [ ] **Step 2: Run service tests and verify import failure**

Run: `cd backend && pytest -q tests/underwriting/test_kernel_service.py`

Expected: FAIL because the kernel service is missing.

- [ ] **Step 3: Implement canonical JSON hashing**

```python
import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal


def canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
```

- [ ] **Step 4: Implement validation helpers**

```python
def _require_text(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValidationError(f"{field} must not be empty")
    return normalized


def _validate_mandate(value: InvestmentMandateInput) -> None:
    if value.horizon_years not in {3, 4, 5}:
        raise ValidationError("horizon_years must be between 3 and 5")
    if len(value.base_currency) != 3 or value.base_currency != value.base_currency.upper():
        raise ValidationError("base_currency must be an uppercase ISO currency")
    if not Decimal("0") <= value.required_return < Decimal("1"):
        raise ValidationError("required_return must be in [0, 1)")
    if not Decimal("0") <= value.permanent_loss_limit <= Decimal("1"):
        raise ValidationError("permanent_loss_limit must be in [0, 1]")
    if not value.comparison_set:
        raise ValidationError("comparison_set must not be empty")
```

- [ ] **Step 5: Implement service commands**

`UnderwritingKernelService` accepts a SQLAlchemy `Session` and injectable `now`. Implement:

```python
class UnderwritingKernelService:
    def __init__(self, session: Session, now: Callable[[], datetime]) -> None:
        self._session = session
        self._repo = UnderwritingRepository(session)
        self._now = now

    def add_basis(self, value: HistoricalBasisInput) -> UnderwritingHistoricalBasis:
        if value.cutoff.tzinfo is None:
            raise ValidationError("cutoff must be timezone-aware")
        if value.price_as_of and value.price_as_of > value.cutoff:
            raise ValidationError("price_as_of must not be after cutoff")
        if len(value.source_manifest_hash) != 64:
            raise ValidationError("source_manifest_hash must be a sha256 hex digest")
        return self._repo.add_basis(value, created_at=self._now())

    def append_ledger_entry(
        self,
        object_id: uuid.UUID,
        basis_id: uuid.UUID,
        value: LedgerEntryInput,
        expected_parent_id: uuid.UUID | None,
    ) -> UnderwritingLedgerEntry:
        basis = self._repo.basis(basis_id)
        if basis is None:
            raise ValidationError("historical basis not found")
        if self._repo.object(object_id) is None:
            raise ValidationError("research object not found")
        if value.available_at > basis.cutoff:
            raise ValidationError("available_at exceeds basis cutoff")
        if value.effective_at.tzinfo is None or value.available_at.tzinfo is None:
            raise ValidationError("ledger times must be timezone-aware")
        payload_hash = canonical_hash({
            "ledger_kind": value.ledger_kind.value,
            "family_key": value.family_key,
            "entry_type": value.entry_type,
            "payload": value.payload,
            "effective_at": value.effective_at,
            "available_at": value.available_at,
            "source_boundary": value.source_boundary,
        })
        return self._repo.append_ledger_entry(
            object_id=object_id,
            basis_id=basis_id,
            ledger_kind=value.ledger_kind.value,
            family_key=_require_text(value.family_key, "family_key"),
            entry_type=_require_text(value.entry_type, "entry_type"),
            payload=dict(value.payload),
            effective_at=value.effective_at,
            available_at=value.available_at,
            source_boundary=_require_text(value.source_boundary, "source_boundary"),
            content_hash=payload_hash,
            expected_parent_id=expected_parent_id,
            created_at=self._now(),
        )
```

Implement `add_object`, `link_objects`, and `append_mandate` with the same normalization, existence, family-parent, and canonical-hash rules. `link_objects` allows only `industry_exposes_company` and `company_has_security`, and validates parent/child kinds.

- [ ] **Step 6: Run service and persistence tests**

Run: `cd backend && pytest -q tests/underwriting/test_kernel_service.py tests/underwriting/test_kernel_persistence.py`

Expected: all selected tests PASS.

- [ ] **Step 7: Commit the kernel service**

```bash
git add backend/app/underwriting/services backend/tests/underwriting/test_kernel_service.py
git commit -m "feat: validate underwriting kernel writes"
```

### Task 7: Implement historical replay and immutable research versions

**Files:**
- Modify: `backend/app/underwriting/persistence/repository.py`
- Modify: `backend/app/underwriting/services/kernel.py`
- Create: `backend/tests/underwriting/test_historical_replay.py`

- [ ] **Step 1: Write the failing future-leakage replay test**

```python
from datetime import UTC, datetime

from app.underwriting.domain.types import LedgerEntryInput, LedgerKind
from app.underwriting.services.kernel import UnderwritingKernelService


T1 = datetime(2025, 12, 31, 23, 59, tzinfo=UTC)
T2 = datetime(2026, 6, 30, 23, 59, tzinfo=UTC)


def test_t2_successor_does_not_change_t1_snapshot(session, kernel_company, basis_factory) -> None:
    service = UnderwritingKernelService(session, now=lambda: T2)
    basis_t1 = basis_factory(T1, "1" * 64)
    first = service.append_ledger_entry(
        kernel_company.id,
        basis_t1.id,
        LedgerEntryInput(
            LedgerKind.REALITY,
            "catl:shipment",
            "observation",
            {"value": "100", "unit": "GWh"},
            T1,
            T1,
            "annual report page 12",
        ),
        None,
    )
    snapshot_before = service.snapshot(kernel_company.id, basis_t1.id)

    basis_t2 = basis_factory(T2, "2" * 64)
    service.append_ledger_entry(
        kernel_company.id,
        basis_t2.id,
        LedgerEntryInput(
            LedgerKind.REALITY,
            "catl:shipment",
            "observation",
            {"value": "120", "unit": "GWh"},
            T2,
            T2,
            "interim report page 9",
        ),
        first.id,
    )

    snapshot_after = service.snapshot(kernel_company.id, basis_t1.id)
    assert snapshot_after == snapshot_before
    assert snapshot_after.entries[0].payload["value"] == "100"
```

- [ ] **Step 2: Write the failing research-version parent test**

```python
def test_research_version_freezes_basis_and_parent_hashes(session, kernel_company, kernel_basis) -> None:
    service = UnderwritingKernelService(session, now=lambda: kernel_basis.cutoff)
    version = service.publish_research_version(
        object_id=kernel_company.id,
        basis_id=kernel_basis.id,
        version_kind="earnings_engine",
        parent_ids=(),
        expected_parent_id=None,
    )
    assert version.sequence == 1
    assert len(version.content_hash) == 64
    assert version.parent_ids == []
```

- [ ] **Step 3: Run historical tests and verify missing methods**

Run: `cd backend && pytest -q tests/underwriting/test_historical_replay.py`

Expected: FAIL because `snapshot` and `publish_research_version` are missing.

- [ ] **Step 4: Implement cutoff-aware effective folding**

```python
    def effective_entries_at(
        self,
        object_id: uuid.UUID,
        cutoff: datetime,
    ) -> list[UnderwritingLedgerEntry]:
        rows = list(self._session.scalars(
            select(UnderwritingLedgerEntry)
            .where(
                UnderwritingLedgerEntry.object_id == object_id,
                UnderwritingLedgerEntry.available_at <= cutoff,
            )
            .order_by(
                UnderwritingLedgerEntry.ledger_kind,
                UnderwritingLedgerEntry.family_key,
                UnderwritingLedgerEntry.version,
            )
        ))
        effective: dict[tuple[str, str], UnderwritingLedgerEntry] = {}
        for row in rows:
            effective[(row.ledger_kind, row.family_key)] = row
        return sorted(
            effective.values(),
            key=lambda row: (row.ledger_kind, row.family_key),
        )
```

- [ ] **Step 5: Implement snapshot and research-version publication**

Define:

```python
@dataclass(frozen=True, slots=True)
class KernelSnapshot:
    object_id: uuid.UUID
    basis_id: uuid.UUID
    cutoff: datetime
    entries: tuple[UnderwritingLedgerEntry, ...]
    snapshot_hash: str
```

`snapshot()` loads the object and basis, calls `effective_entries_at`, and hashes only object ID, basis cutoff, source manifest hash, and the ordered entry IDs/content hashes. `publish_research_version()` hashes the snapshot plus `version_kind` and sorted `parent_ids`, then appends through the repository's optimistic successor method.

- [ ] **Step 6: Add correction and dual-time edge tests**

```python
def test_effective_time_does_not_bypass_available_time(
    session, kernel_company, basis_factory
) -> None:
    april = datetime(2026, 4, 30, tzinfo=UTC)
    may = datetime(2026, 5, 31, tzinfo=UTC)
    basis = basis_factory(april, "3" * 64)
    repo = UnderwritingRepository(session)
    repo.append_ledger_entry(
        object_id=kernel_company.id,
        basis_id=basis.id,
        ledger_kind="reality",
        family_key="catl:march-output",
        entry_type="observation",
        payload={"value": "10"},
        effective_at=datetime(2026, 3, 31, tzinfo=UTC),
        available_at=may,
        source_boundary="published in May",
        content_hash="f" * 64,
        expected_parent_id=None,
        created_at=may,
    )
    service = UnderwritingKernelService(session, now=lambda: may)
    assert service.snapshot(kernel_company.id, basis.id).entries == ()


def test_parent_order_does_not_change_research_version_hash(
    session, kernel_company, kernel_basis
) -> None:
    service = UnderwritingKernelService(session, now=lambda: kernel_basis.cutoff)
    left = service.preview_research_version_hash(
        kernel_company.id, kernel_basis.id, "valuation", ("b", "a")
    )
    right = service.preview_research_version_hash(
        kernel_company.id, kernel_basis.id, "valuation", ("a", "b")
    )
    assert left == right
```

Keep the T1/T2 test above as the correction replay check. Add a stale research-version parent assertion using the same `StaleParentError` contract as ledger families.

- [ ] **Step 7: Run historical and kernel tests**

Run: `cd backend && pytest -q tests/underwriting/test_historical_replay.py tests/underwriting/test_kernel_service.py tests/underwriting/test_kernel_persistence.py`

Expected: all selected tests PASS.

- [ ] **Step 8: Commit replay support**

```bash
git add backend/app/underwriting backend/tests/underwriting/test_historical_replay.py
git commit -m "feat: add underwriting historical replay"
```

### Task 8: Persist Answerability evaluations and action boundaries

**Files:**
- Modify: `backend/app/underwriting/services/kernel.py`
- Modify: `backend/app/underwriting/persistence/repository.py`
- Modify: `backend/tests/underwriting/test_answerability.py`

- [ ] **Step 1: Add failing persisted-gate tests**

```python
from app.underwriting.domain.types import BlockerCode, EligibleAction


def test_persisted_failure_cannot_claim_entry_eligibility(
    session, kernel_company, kernel_basis
) -> None:
    service = UnderwritingKernelService(session, now=lambda: kernel_basis.cutoff)
    row = service.record_answerability(
        object_id=kernel_company.id,
        basis_id=kernel_basis.id,
        hard_blockers=(BlockerCode.MISSING_KEY_BASELINE,),
        research_debt_keys=("missing_effective_capacity",),
        resolvable_within_mandate=True,
        requested_action=EligibleAction.ELIGIBLE_FOR_STAGED_ENTRY,
        resolution_requirements=("freeze effective-capacity source",),
        expected_parent_id=None,
    )
    assert row.state == "not_answerable"
    assert row.allowed_action == "wait_for_validation"


def test_unresolvable_failure_is_not_a_bearish_price_claim(
    session, kernel_company, kernel_basis
) -> None:
    service = UnderwritingKernelService(session, now=lambda: kernel_basis.cutoff)
    row = service.record_answerability(
        object_id=kernel_company.id,
        basis_id=kernel_basis.id,
        hard_blockers=(BlockerCode.SOURCE_UNAVAILABLE,),
        research_debt_keys=("licensed_source_unavailable",),
        resolvable_within_mandate=False,
        requested_action=EligibleAction.OBSERVE,
        resolution_requirements=("change authorized source policy",),
        expected_parent_id=None,
    )
    assert row.allowed_action == "do_not_enter"
    assert "price_state" not in row.research_debt_keys
```

- [ ] **Step 2: Run persisted gate tests and verify missing method**

Run: `cd backend && pytest -q tests/underwriting/test_answerability.py -k persisted`

Expected: FAIL because `record_answerability` is missing.

- [ ] **Step 3: Implement gate persistence**

`record_answerability()` must:

```python
result = evaluate_answerability(AnswerabilityInput(
    hard_blockers=hard_blockers,
    research_debt_keys=research_debt_keys,
    resolvable_within_mandate=resolvable_within_mandate,
))
allowed_action = enforce_action_boundary(result, requested_action)
```

It then appends an `UnderwritingAnswerabilityEvaluation` with monotonic version, exact blocker values, debt keys, action value, resolution requirements, and expected-parent enforcement. Reject empty resolution requirements for every `not_answerable` result.

- [ ] **Step 4: Add duplicate blocker and empty-resolution tests**

```python
def test_duplicate_blockers_are_canonicalized(
    session, kernel_company, kernel_basis
) -> None:
    service = UnderwritingKernelService(session, now=lambda: kernel_basis.cutoff)
    row = service.record_answerability(
        kernel_company.id,
        kernel_basis.id,
        (BlockerCode.MISSING_KEY_BASELINE, BlockerCode.MISSING_KEY_BASELINE),
        (),
        True,
        EligibleAction.OBSERVE,
        ("freeze baseline",),
        None,
    )
    assert row.blockers == ["missing_key_baseline"]


def test_failed_gate_requires_resolution_requirements(
    session, kernel_company, kernel_basis
) -> None:
    service = UnderwritingKernelService(session, now=lambda: kernel_basis.cutoff)
    with pytest.raises(ValidationError, match="resolution_requirements"):
        service.record_answerability(
            kernel_company.id,
            kernel_basis.id,
            (BlockerCode.MISSING_KEY_BASELINE,),
            (),
            True,
            EligibleAction.OBSERVE,
            (),
            None,
        )
```

- [ ] **Step 5: Run answerability, replay, and persistence tests**

Run: `cd backend && pytest -q tests/underwriting/test_answerability.py tests/underwriting/test_historical_replay.py tests/underwriting/test_kernel_persistence.py`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit persisted answerability**

```bash
git add backend/app/underwriting backend/tests/underwriting
git commit -m "feat: persist underwriting answerability decisions"
```

### Task 9: Expose the independent Underwriting API

**Files:**
- Create: `backend/app/underwriting/api/schemas.py`
- Create: `backend/app/underwriting/api/router.py`
- Modify: `backend/app/underwriting/api/__init__.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/underwriting/test_kernel_api.py`

- [ ] **Step 1: Write failing API identity and boundary tests**

```python
from app.db import get_db
from app.main import app


def test_create_company_and_security_are_separate(client, session) -> None:
    app.dependency_overrides[get_db] = lambda: session
    try:
        company = client.post("/api/underwriting/v1/objects", json={
            "kind": "company",
            "external_key": "CN:300750:COMPANY",
            "canonical_name": "宁德时代新能源科技股份有限公司",
        })
        security = client.post("/api/underwriting/v1/objects", json={
            "kind": "security",
            "external_key": "SZSE:300750",
            "canonical_name": "宁德时代 A股",
        })
    finally:
        app.dependency_overrides.clear()
    assert company.status_code == 201
    assert security.status_code == 201
    assert company.json()["id"] != security.json()["id"]
    assert company.json()["schema_version"] == "underwriting.v1"


def test_unknown_fields_fail_closed(client, session) -> None:
    app.dependency_overrides[get_db] = lambda: session
    try:
        response = client.post("/api/underwriting/v1/objects", json={
            "kind": "company",
            "external_key": "CN:300750:COMPANY",
            "canonical_name": "宁德时代",
            "ticker": "300750",
        })
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
```

- [ ] **Step 2: Write failing API answerability test**

```python
def test_not_answerable_api_never_returns_entry_action(client, session, api_kernel_ids) -> None:
    object_id, basis_id = api_kernel_ids
    response = client.post(
        f"/api/underwriting/v1/objects/{object_id}/answerability",
        json={
            "basis_id": str(basis_id),
            "hard_blockers": ["financial_model_not_closed"],
            "research_debt_keys": ["cash_bridge_missing"],
            "resolvable_within_mandate": True,
            "requested_action": "eligible_for_staged_entry",
            "resolution_requirements": ["close profit-to-free-cash-flow bridge"],
            "expected_parent_id": None,
        },
    )
    assert response.status_code == 201
    assert response.json()["state"] == "not_answerable"
    assert response.json()["allowed_action"] == "wait_for_validation"
```

- [ ] **Step 3: Run API tests and verify 404**

Run: `cd backend && pytest -q tests/underwriting/test_kernel_api.py`

Expected: FAIL because `/api/underwriting/v1` is not registered.

- [ ] **Step 4: Implement strict request and response schemas**

```python
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UnderwritingDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionedResponse(UnderwritingDTO):
    schema_version: Literal["underwriting.v1"] = "underwriting.v1"


class ResearchObjectCreate(UnderwritingDTO):
    kind: Literal["industry", "company", "security"]
    external_key: str = Field(min_length=1, max_length=160)
    canonical_name: str = Field(min_length=1)


class ResearchObjectResponse(VersionedResponse):
    id: UUID
    kind: str
    external_key: str
    canonical_name: str
    created_at: datetime


class BasisCreate(UnderwritingDTO):
    cutoff: datetime
    price_as_of: datetime | None
    source_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class MandateCreate(UnderwritingDTO):
    mandate_key: str = Field(min_length=1, max_length=120)
    horizon_years: int = Field(ge=3, le=5)
    base_currency: str = Field(pattern=r"^[A-Z]{3}$")
    required_return: Decimal = Field(ge=0, lt=1)
    permanent_loss_limit: Decimal = Field(ge=0, le=1)
    comparison_set: list[str] = Field(min_length=1)
    expected_parent_id: UUID | None


class LedgerEntryCreate(UnderwritingDTO):
    basis_id: UUID
    ledger_kind: Literal["reality", "belief", "decision", "calibration"]
    family_key: str = Field(min_length=1, max_length=160)
    entry_type: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any]
    effective_at: datetime
    available_at: datetime
    source_boundary: str = Field(min_length=1)
    expected_parent_id: UUID | None


class AnswerabilityCreate(UnderwritingDTO):
    basis_id: UUID
    hard_blockers: list[Literal[
        "missing_key_baseline", "unresolved_source_conflict",
        "mechanism_unidentified", "financial_model_not_closed",
        "expectation_surface_unidentifiable", "source_unavailable",
        "future_information_leakage",
    ]]
    research_debt_keys: list[str]
    resolvable_within_mandate: bool
    requested_action: Literal[
        "observe", "wait_for_validation", "eligible_for_probe_entry",
        "eligible_for_staged_entry", "do_not_enter",
    ]
    resolution_requirements: list[str]
    expected_parent_id: UUID | None


class BasisResponse(VersionedResponse):
    id: UUID
    cutoff: datetime
    price_as_of: datetime | None
    source_manifest_hash: str
    created_at: datetime


class MandateResponse(VersionedResponse):
    id: UUID
    mandate_key: str
    version: int
    horizon_years: int
    base_currency: str
    required_return: Decimal
    permanent_loss_limit: Decimal
    comparison_set: list[str]
    supersedes_id: UUID | None
    created_at: datetime


class LedgerEntryResponse(VersionedResponse):
    id: UUID
    ledger_kind: str
    family_key: str
    entry_type: str
    version: int
    payload: dict[str, Any]
    effective_at: datetime
    available_at: datetime
    source_boundary: str
    content_hash: str
    supersedes_id: UUID | None


class AnswerabilityResponse(VersionedResponse):
    id: UUID
    version: int
    state: str
    blockers: list[str]
    research_debt_keys: list[str]
    resolvable_within_mandate: bool
    allowed_action: str
    resolution_requirements: list[str]
    supersedes_id: UUID | None


class SnapshotResponse(VersionedResponse):
    object_id: UUID
    basis_id: UUID
    cutoff: datetime
    entries: list[LedgerEntryResponse]
    snapshot_hash: str
```

Snapshot entries expose type, family, payload, dual time, source boundary, version, content hash, and supersedes ID; routes construct these DTOs explicitly and never return ORM objects directly.

- [ ] **Step 5: Implement router and error translation**

```python
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import ConflictError, ValidationFailedError
from app.models.ledger import ValidationError
from app.underwriting.persistence.repository import StaleParentError


router = APIRouter(prefix="/api/underwriting/v1", tags=["underwriting-v1"])


def service(db: Session = Depends(get_db)) -> UnderwritingKernelService:
    return UnderwritingKernelService(db, now=lambda: datetime.now(UTC))


def translate_domain_error(exc: Exception) -> Exception:
    if isinstance(exc, StaleParentError):
        return ConflictError(str(exc))
    if isinstance(exc, ValidationError):
        return ValidationFailedError(str(exc))
    return exc
```

Implement these routes with `201` for writes and `200` for reads:

```text
POST /objects
POST /object-relations
POST /mandates
POST /historical-bases
POST /objects/{object_id}/ledger-entries
POST /objects/{object_id}/answerability
GET  /objects/{object_id}/snapshots/{basis_id}
```

Each write catches `ValidationError` and `StaleParentError`, raises the translated application error, calls `db.commit()` only after a successful service result, and serializes through a strict response DTO.

- [ ] **Step 6: Register only the independent router in `app/main.py`**

```python
from app.underwriting.api.router import router as underwriting_router

app.include_router(underwriting_router)
```

Do not include it in `app/api/v1/router.py`; the separate prefix and OpenAPI tag make the product boundary visible.

- [ ] **Step 7: Add full API lifecycle tests**

```python
def test_kernel_api_lifecycle(client, session) -> None:
    app.dependency_overrides[get_db] = lambda: session
    try:
        objects = [
            client.post("/api/underwriting/v1/objects", json=value).json()
            for value in (
                {"kind": "industry", "external_key": "CN:POWER-BATTERY", "canonical_name": "动力电池"},
                {"kind": "company", "external_key": "CN:300750:COMPANY", "canonical_name": "宁德时代"},
                {"kind": "security", "external_key": "SZSE:300750", "canonical_name": "宁德时代 A股"},
            )
        ]
        basis = client.post("/api/underwriting/v1/historical-bases", json={
            "cutoff": "2026-06-30T23:59:00Z",
            "price_as_of": "2026-06-30T07:00:00Z",
            "source_manifest_hash": "1" * 64,
        }).json()
        entry_ids = []
        for ledger_kind in ("reality", "belief", "decision", "calibration"):
            response = client.post(
                f"/api/underwriting/v1/objects/{objects[1]['id']}/ledger-entries",
                json={
                    "basis_id": basis["id"],
                    "ledger_kind": ledger_kind,
                    "family_key": f"catl:{ledger_kind}:fixture",
                    "entry_type": "fixture_contract",
                    "payload": {"ledger": ledger_kind},
                    "effective_at": "2026-06-30T00:00:00Z",
                    "available_at": "2026-06-30T12:00:00Z",
                    "source_boundary": "frozen contract fixture",
                    "expected_parent_id": None,
                },
            )
            assert response.status_code == 201
            entry_ids.append(response.json()["id"])
        snapshot = client.get(
            f"/api/underwriting/v1/objects/{objects[1]['id']}/snapshots/{basis['id']}"
        )
    finally:
        app.dependency_overrides.clear()
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert {entry["ledger_kind"] for entry in body["entries"]} == {
        "reality", "belief", "decision", "calibration"
    }
    assert all(entry["source_boundary"] for entry in body["entries"])
    assert len(body["snapshot_hash"]) == 64
```

Add one second request that supplies a stale `expected_parent_id` and assert `409` with error code `conflict`.

- [ ] **Step 8: Run API and kernel tests**

Run: `cd backend && pytest -q tests/underwriting/test_kernel_api.py tests/underwriting/test_kernel_service.py tests/underwriting/test_historical_replay.py`

Expected: all selected tests PASS.

- [ ] **Step 9: Commit the API boundary**

```bash
git add backend/app/main.py backend/app/underwriting/api backend/tests/underwriting/test_kernel_api.py
git commit -m "feat: expose underwriting kernel api"
```

### Task 10: Prove database immutability and complete the Wave 1 gate

**Files:**
- Create: `backend/tests/underwriting/test_kernel_postgres.py`
- Modify: `backend/scripts/dump_openapi.py`
- Modify: `frontend/openapi.json`
- Modify: `frontend/src/contracts/v1.ts`
- Modify: `prototype/investment-research-prototype/README.md`

- [ ] **Step 1: Write PostgreSQL trigger tests**

```python
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa


TABLES = {
    "uw_research_objects", "uw_object_relations", "uw_mandate_versions",
    "uw_historical_bases", "uw_ledger_entries", "uw_research_versions",
    "uw_answerability_evaluations",
}


def schema_url(database_url: str, schema: str) -> str:
    separator = "&" if "?" in database_url else "?"
    return f"{database_url}{separator}options=-csearch_path={schema}"


@pytest.mark.pg_only
def test_0060_installs_all_kernel_tables_and_immutable_triggers() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"underwriting_0060_{uuid.uuid4().hex}"
    migration_url = schema_url(database_url, schema)
    admin = sa.create_engine(database_url, future=True)
    isolated = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[2]
    object_id = uuid.uuid4()
    try:
        with admin.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        migrated = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0060"],
            cwd=backend,
            env={**os.environ, "DATABASE_URL": migration_url},
            text=True,
            capture_output=True,
            check=False,
        )
        assert migrated.returncode == 0, migrated.stderr
        with isolated.begin() as connection:
            assert TABLES <= set(sa.inspect(connection).get_table_names())
            triggered = set(connection.execute(sa.text("""
                SELECT c.relname
                FROM pg_trigger t
                JOIN pg_class c ON c.oid = t.tgrelid
                WHERE NOT t.tgisinternal AND c.relname LIKE 'uw_%'
            """)).scalars())
            assert triggered == TABLES
            connection.execute(sa.text("""
                INSERT INTO uw_research_objects
                    (id, kind, external_key, canonical_name, created_at)
                VALUES (:id, 'company', 'CN:300750:COMPANY', '宁德时代', CURRENT_TIMESTAMP)
            """), {"id": object_id})
        with pytest.raises(sa.exc.DBAPIError, match="append-only|immutable"):
            with isolated.begin() as connection:
                connection.execute(sa.text("""
                    UPDATE uw_research_objects
                    SET canonical_name = 'changed' WHERE id = :id
                """), {"id": object_id})
        with pytest.raises(sa.exc.DBAPIError, match="append-only|immutable"):
            with isolated.begin() as connection:
                connection.execute(sa.text(
                    "DELETE FROM uw_research_objects WHERE id = :id"
                ), {"id": object_id})
    finally:
        isolated.dispose()
        with admin.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
```

- [ ] **Step 2: Run SQLite gate tests**

Run:

```bash
cd backend
pytest -q \
  tests/underwriting/test_isolation_contract.py \
  tests/underwriting/test_domain_types.py \
  tests/underwriting/test_answerability.py \
  tests/underwriting/test_kernel_persistence.py \
  tests/underwriting/test_kernel_service.py \
  tests/underwriting/test_historical_replay.py \
  tests/underwriting/test_kernel_api.py
```

Expected: all Wave 1 tests PASS with no skip inside these modules.

- [ ] **Step 3: Run PostgreSQL gate when `TEST_DATABASE_URL` is configured**

Run: `cd backend && pytest -q tests/underwriting/test_kernel_postgres.py`

Expected with PostgreSQL: all parametrized trigger tests PASS. Expected without PostgreSQL: the module is explicitly skipped by the repository's `pg_only` convention and the checkpoint records that PostgreSQL verification remains required before Wave 2.

- [ ] **Step 4: Regenerate and verify the OpenAPI contract**

Run:

```bash
cd backend
python scripts/dump_openapi.py
cd ../frontend
npm run gen:contract
npm run typecheck
```

Expected: OpenAPI generation exits 0; TypeScript contract generation exits 0; typecheck exits 0.

- [ ] **Step 5: Run the complete backend regression suite**

Run: `cd backend && pytest -q`

Expected: all existing and new backend tests PASS; environment-dependent tests may only skip under their declared markers.

- [ ] **Step 6: Document the executable boundary**

Add a “Production implementation status” section to `prototype/investment-research-prototype/README.md` that states:

```text
The static prototype remains an interaction reference. The production-shaped
implementation begins at /api/underwriting/v1. Wave 1 contains identity,
mandate, historical basis, four-ledger, version, replay, and answerability
contracts only. It contains no CATL conclusion, valuation, recommendation,
position, or live-data claim.
```

- [ ] **Step 7: Run final diff and isolation checks**

Run:

```bash
git diff --check
cd backend
pytest -q tests/underwriting/test_isolation_contract.py
```

Expected: `git diff --check` emits no output and isolation reports `1 passed`.

- [ ] **Step 8: Commit the Wave 1 release gate**

```bash
git add backend frontend/openapi.json frontend/src/contracts/v1.ts prototype/investment-research-prototype/README.md
git commit -m "test: gate underwriting trusted kernel"
```

## Wave 1 completion record

Before starting CATL data work, record all of the following in the implementation handoff:

- migration revision and commit SHA;
- SQLite pass count and PostgreSQL pass/skip status;
- full backend pass count;
- OpenAPI and TypeScript contract generation result;
- one T1/T2 replay hash demonstration;
- one `not_answerable → wait_for_validation` demonstration;
- one stale-parent `409` demonstration;
- remaining design scope explicitly limited to Waves 2–5 in the program plan.
